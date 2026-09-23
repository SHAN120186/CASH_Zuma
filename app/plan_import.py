"""Preview and explicitly commit annual Cash Flow expense plans.

This path never creates Ledger rows.  It reads only the detailed expense rows
from the workbook's ``план на год`` sheet and preserves existing plan values
where the source cell is blank.
"""
from __future__ import annotations
import hashlib, io, json, re, zipfile, posixpath
from defusedxml import ElementTree as ET
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import unquote
from typing import Literal
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from openpyxl.utils.datetime import from_excel
from .db import unit, User, Company, Category, CashPlan, PlanImportBatch
from .security import session_user
from .services import get, log
from .erp import read_upload

router=APIRouter()
SCENARIOS={'А':'A','A':'A','Б':'B','B':'B','В':'V','V':'V'}

def clean(value):return re.sub(r'\s+',' ',('' if value is None else str(value)).replace('\xa0',' ')).strip()
def norm(value):return clean(value).casefold()

def scenario_header(value):
    text=clean(value).upper()
    first=text[:1]
    return SCENARIOS.get(first)

def month_value(value, epoch):
    if isinstance(value,(datetime,date)):return value.year,value.month
    if isinstance(value,(int,float)):
        d=from_excel(value,epoch);return d.year,d.month
    text=clean(value)
    if re.fullmatch(r'20\d{2}',text):return int(text),None
    return None,None

def plan_amount(value, formula=False):
    text=clean(value)
    if not text or text in {'-','—'}:return None
    if isinstance(value,str) and value.startswith('#'):raise ValueError(f'ошибка Excel {value}')
    try:n=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):raise ValueError('значение не является числом')
    if not n.is_finite() or n<0:raise ValueError('план расхода должен быть неотрицательным числом')
    if n>Decimal('100000000000000'):raise ValueError('сумма больше 100 трлн')
    return int((n*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))

def parse_plan_workbook(raw,year,month_from,month_to):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(i.file_size for i in archive.infolist())>80*1024*1024:raise ValueError('Слишком большой распакованный XLSX.')
            ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            rel_key='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
            strings=[]
            if 'xl/sharedStrings.xml' in archive.namelist():
                for item in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si',ns):
                    strings.append(''.join(t.text or '' for t in item.iterfind('.//s:t',ns)))
            book=ET.fromstring(archive.read('xl/workbook.xml'))
            rels={r.attrib['Id']:r.attrib['Target'] for r in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
            sheet=next((x for x in book.findall('s:sheets/s:sheet',ns) if x.get('name')=='план на год'),None)
            if sheet is None:raise ValueError('Не найден лист «план на год».')
            target=rels[sheet.attrib[rel_key]];path=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
            root=ET.fromstring(archive.read(path));date1904=(book.find('s:workbookPr',ns) is not None and book.find('s:workbookPr',ns).get('date1904') in ('1','true'))
            epoch=datetime(1904,1,1) if date1904 else datetime(1899,12,30)
            cells={}
            for cell in root.findall('s:sheetData/s:row/s:c',ns):
                value_node=cell.find('s:v',ns);value=value_node.text if value_node is not None else None
                if cell.get('t')=='s' and value is not None:value=strings[int(value)]
                elif cell.get('t')=='inlineStr':value=''.join(t.text or '' for t in cell.iterfind('.//s:t',ns))
                elif value is not None:
                    try:value=float(value) if any(ch in value for ch in '.eE') else int(value)
                    except ValueError:pass
                cells[cell.get('r')]=(value,cell.find('s:f',ns) is not None)
            def cv(ref):return cells.get(ref,(None,False))
            columns={}
            for col in (8,9,10):
                letter=chr(64+col);scenario=scenario_header(cv(f'{letter}7')[0])
                if not scenario or scenario in columns:raise ValueError('В H7:J7 должны быть уникальные заголовки планов А, Б и В.')
                columns[scenario]=col
            if set(columns)!={'A','B','V'}:raise ValueError('Не удалось сопоставить все сценарии А, Б и В по заголовкам.')
            rows=[];issues=[];seen=set();expense_blocks={}
            max_row=max((int(re.search(r'\d+',ref).group()) for ref in cells),default=7)
            if max_row>50000:raise ValueError('Лист планов содержит больше 50 000 строк.')
            for row in range(8,max_row+1):
                y,m=month_value(cv(f'A{row}')[0],epoch)
                name=clean(cv(f'E{row}')[0]);name_key=norm(name)
                if y==year and m and 'жами ҳаражат' in name_key:
                    expense_blocks[(y,m)]=True;continue
                if y==year and m and 'жами тушум' in name_key:
                    expense_blocks[(y,m)]=False;continue
                # A code and a name identify a detailed expense. Some valid source rows
                # (for example «Курсовые разницы») intentionally leave sequence B blank.
                if y!=year or not m or not month_from<=m<=month_to or not expense_blocks.get((y,m),False):continue
                code=clean(cv(f'C{row}')[0])
                if not code:continue # uncoded salary breakdown rows are subdetails of the coded parent
                if not name:issues.append({'line':row,'error':'нет названия статьи'});continue
                source_key=code+'::'+norm(name);identity=(m,source_key)
                if identity in seen:issues.append({'line':row,'error':'повтор статьи в том же месяце'});continue
                seen.add(identity);amounts={}
                for scenario,col in columns.items():
                    letter=chr(64+col);cached,is_formula=cv(f'{letter}{row}')
                    try:
                        if is_formula and cached is None:raise ValueError('у формулы нет сохранённого числового результата')
                        amounts[scenario]=plan_amount(cached,is_formula)
                    except ValueError as exc:
                        issues.append({'line':row,'scenario':scenario,'error':str(exc)});amounts[scenario]=None
                rows.append({'line':row,'month':m,'source_key':source_key,'source_code':code,'source_name':name,'amounts':amounts})
            if not rows:raise ValueError('В выбранном периоде не найдены детальные строки расходов.')
            return rows,issues
    except HTTPException:raise
    except Exception as exc:raise HTTPException(422,str(exc) if isinstance(exc,ValueError) else 'Не удалось прочитать книгу планов.')

def preview(s,u,raw,name,company_id,year,month_from,month_to):
    get(s,Company,company_id)
    rows,issues=parse_plan_workbook(raw,year,month_from,month_to)
    categories=list(s.scalars(select(Category)))
    by_name={norm(c.name):c.id for c in categories}
    suggestions={r['source_key']:by_name.get(norm(r['source_name'])) for r in rows}
    existing_plans=list(s.scalars(select(CashPlan).where(
        CashPlan.company_id==company_id,CashPlan.currency=='UZS',CashPlan.month>=f'{year}-{month_from:02}',CashPlan.month<=f'{year}-{month_to:02}')))
    plans={(p.month,p.scenario):json.loads(p.payload) for p in existing_plans}
    versions={f'{p.month}:{p.scenario}':p.version for p in existing_plans}
    for row in rows:
        category_id=suggestions[row['source_key']];month=f'{year}-{row["month"]:02}';row['effects']={}
        for scenario,value in row['amounts'].items():
            if value is None:effect='keep'
            elif category_id is None:effect='new'
            else:
                payload=plans.get((month,scenario),{});key=f'{category_id}:out';next_value=-value
                effect='same' if payload.get(key)==next_value else 'change' if key in payload else 'new'
            row['effects'][scenario]=effect
    scope=f'{company_id}:{year}:{month_from}:{month_to}:'.encode()
    digest=hashlib.sha256(scope+raw).hexdigest()
    previous=s.scalar(select(PlanImportBatch).where(PlanImportBatch.digest==digest,PlanImportBatch.status=='committed').limit(1))
    if previous:raise HTTPException(409,f'Этот файл и период уже импортированы (пакет №{previous.id}).')
    batch=PlanImportBatch(user_id=u.id,company_id=company_id,filename=name[:220],digest=digest,year=year,
        month_from=month_from,month_to=month_to,payload=json.dumps({'rows':rows,'issues':issues,'versions':versions},ensure_ascii=False),status='preview')
    s.add(batch);s.flush();log(s,u,'Предпросмотр импорта планов','plan_import',batch.id,f'{len(rows)} строк; замечаний: {len(issues)}')
    return {'id':batch.id,'rows':rows,'issues':issues,'suggestions':suggestions,
        'categories':[{'id':c.id,'name':c.name} for c in categories],
        'months':sorted({r['month'] for r in rows}),'can_commit':not issues}

@router.post('/api/plan-import/preview')
async def plan_import_preview(request:Request,company_id:int,year:int=Query(ge=2000,le=2100),month_from:int=Query(ge=1,le=12),month_to:int=Query(ge=1,le=12)):
    if month_from>month_to:raise HTTPException(422,'Начальный месяц позже конечного.')
    with unit() as s:u,_=session_user(s,request,'import');uid=u.id
    raw=await read_upload(request)
    name=Path(unquote(request.headers.get('X-Filename','cash-flow-plan.xlsx'))).name
    if not name.lower().endswith('.xlsx'):raise HTTPException(422,'Для планов поддерживается только XLSX.')
    with unit(True) as s:
        u,_=session_user(s,request,'import')
        return preview(s,u,raw,name,company_id,year,month_from,month_to)

class CommitPlanImport(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    mappings:dict[str,str]=Field(max_length=500)
    reason:str=Field(min_length=10,max_length=1000)

@router.post('/api/plan-import/{id}/commit')
def commit_plan_import(id:int,data:CommitPlanImport,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'import');batch=get(s,PlanImportBatch,id)
        if batch.user_id!=u.id and u.role!='admin':raise HTTPException(403,'Импорт создан другим пользователем.')
        if batch.status!='preview':raise HTTPException(409,'Импорт уже выполнен или недоступен.')
        if s.scalar(select(PlanImportBatch.id).where(PlanImportBatch.digest==batch.digest,PlanImportBatch.status=='committed').limit(1)):
            raise HTTPException(409,'Этот файл и период уже импортированы. Обновите предпросмотр.')
        document=json.loads(batch.payload)
        if 'versions' not in document:raise HTTPException(409,'Предпросмотр устарел. Загрузите файл повторно.')
        if document['issues']:raise HTTPException(409,'Исправьте ошибки предпросмотра перед подтверждением.')
        rows=document['rows'];category_cache={};created=[]
        for row in rows:
            key=row['source_key'];choice=data.mappings.get(key)
            if not choice:raise HTTPException(422,f'Не выбрано сопоставление для «{row["source_name"]}».')
            if choice=='skip':category_cache[key]=None
            elif choice=='new':
                existing=s.scalar(select(Category).where(Category.name==row['source_name']))
                c=existing or Category(name=row['source_name'][:160],activity='operating',type='outcome',cost_group='other')
                if not existing:s.add(c);s.flush();created.append(c.id)
                category_cache[key]=c.id
            else:
                try:category_cache[key]=get(s,Category,int(choice)).id
                except (ValueError,TypeError):raise HTTPException(422,'Некорректное сопоставление статьи.')
        grouped={}
        for row in rows:
            category_id=category_cache[row['source_key']]
            if category_id is None:continue
            month=f'{batch.year}-{row["month"]:02}'
            for scenario,value in row['amounts'].items():
                if value is None:continue # blank preserves the existing plan value
                key=(month,scenario,category_id);grouped[key]=grouped.get(key,0)+value
        plan_objects={};updated=0
        for (month,scenario,category_id),value in grouped.items():
            scope=(month,scenario)
            if scope not in plan_objects:
                p=s.scalar(select(CashPlan).where(CashPlan.company_id==batch.company_id,CashPlan.month==month,CashPlan.currency=='UZS',CashPlan.scenario==scenario))
                if (p.version if p else 0)!=document['versions'].get(f'{month}:{scenario}',0):
                    raise HTTPException(409,'План изменился после предпросмотра. Загрузите файл повторно и проверьте новые суммы.')
                if not p:p=CashPlan(company_id=batch.company_id,month=month,currency='UZS',scenario=scenario,payload='{}',version=0);s.add(p);s.flush()
                plan_objects[scope]=p
            p=plan_objects[scope];payload=json.loads(p.payload);payload[f'{category_id}:out']=-value;p.payload=json.dumps(payload);updated+=1
        for p in plan_objects.values():p.version+=1
        batch.status='committed';log(s,u,'Импорт планов подтверждён','plan_import',id,json.dumps({'updated':updated,'created_categories':created,'reason':data.reason},ensure_ascii=False))
        return {'updated':updated,'created_categories':len(created),'plans':len(plan_objects)}
