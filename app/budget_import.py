"""Monthly budget import: preview then atomic replacement of selected limits."""
import io, json, re, zipfile, posixpath, hashlib
from pathlib import Path
from urllib.parse import unquote
from decimal import Decimal
from typing import Literal
from defusedxml import ElementTree as ET
from fastapi import APIRouter, Request, HTTPException, Query
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from .db import unit, Category, Budget, BudgetImportBatch
from .security import session_user, require_permission, perms_of
from .services import get, log, money
from .section_access import can_edit_section
from .plan_import import clean, norm, plan_amount

router=APIRouter()
NS='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

def parse_budget(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if sum(i.file_size for i in z.infolist())>160*1024*1024:raise ValueError('Распакованный Excel больше 160 МБ.')
            strings=[]
            if 'xl/sharedStrings.xml' in z.namelist():
                if z.getinfo('xl/sharedStrings.xml').file_size>8*1024*1024:raise ValueError('Слишком много текста в Excel.')
                root=ET.fromstring(z.read('xl/sharedStrings.xml'))
                strings=[''.join(t.text or '' for t in si.iter(NS+'t')) for si in root]
            book=ET.fromstring(z.read('xl/workbook.xml'))
            sheets=book.find(NS+'sheets')
            if len(sheets)!=1:raise ValueError('Для бюджета оставьте в файле один лист.')
            rels={r.get('Id'):r.get('Target') for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
            target=rels[sheets[0].get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')]
            path=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
            if not path.startswith('xl/worksheets/'):raise ValueError('Некорректный лист Excel.')
            rows=[];issues=[];seen=set();cols=None;parent=None
            with z.open(path) as stream:
                for event,el in ET.iterparse(stream,events=('start','end')):
                    if event=='start' and el.tag==NS+'sheetData':parent=el
                    if event!='end' or el.tag!=NS+'row':continue
                    cells={}
                    for c in el:
                        v=c.find(NS+'v');value=v.text if v is not None else None
                        if c.get('t')=='s' and value is not None:value=strings[int(value)]
                        elif c.get('t')=='inlineStr':value=''.join(t.text or '' for t in c.iter(NS+'t'))
                        formula=c.find(NS+'f') is not None
                        if clean(value) or formula:cells[re.sub(r'\d','',c.get('r',''))]=(value,formula)
                    line=int(el.get('r','0'))
                    if parent is not None:parent.remove(el)
                    el.clear()
                    if not cells:continue
                    if cols is None:
                        cols=('H','I','J') if 'H' in cells and 'I' in cells else ('A','B','C')
                    code,cf=cells.get(cols[0],(None,False));name,nf=cells.get(cols[1],(None,False));value,vf=cells.get(cols[2],(None,False))
                    code,name=clean(code),clean(name)
                    if norm(code) in ('код','код статьи','code','kod','modda kodi'):continue
                    if not code and norm(name) in ('жами ҳаражатлар','итого','всего','jami xarajatlar'):continue
                    if not code and not name and not clean(value):
                        issues.append({'line':line,'error':'Данные вне столбцов кода, названия и суммы.'});continue
                    error=None
                    if not re.fullmatch(r'[\w.\-]{1,40}',code) or not name or len(name)>160 or cf or nf:error='Нужны код статьи и название до 160 символов без формул.'
                    elif code in seen:error='Код статьи повторяется в файле.'
                    seen.add(code)
                    amount=None
                    try:
                        if vf and not clean(value):raise ValueError('У формулы суммы нет сохранённого результата.')
                        text=clean(value).replace(' ','').replace(',','.')
                        amount=plan_amount(text)
                    except ValueError as exc:error=str(exc)
                    if error:issues.append({'line':line,'error':error})
                    rows.append({'line':line,'code':code,'name':name,'amount':amount})
                    if len(rows)>1000 or len(issues)>1000:raise ValueError('В бюджете допускается до 1000 статей.')
            if not rows:raise ValueError('Статьи бюджета не найдены. Нужны код, название и сумма.')
            return rows,issues
    except HTTPException:raise
    except Exception as exc:raise HTTPException(422,str(exc) if isinstance(exc,ValueError) else 'Не удалось прочитать Excel бюджета.')

def article_code(c):
    if c.code:return c.code
    m=re.match(r'^(\d+)\s*[·|—]\s*',c.name)
    return m.group(1) if m else None

def article_name(c):
    return re.sub(r'^'+re.escape(article_code(c) or '\0')+r'\s*[·|—]\s*','',c.name)

def snapshot(s,month,currency):
    cats=list(s.scalars(select(Category).order_by(Category.id)))
    budgets=list(s.scalars(select(Budget).where(Budget.month==month,Budget.currency==currency).order_by(Budget.id)))
    raw={'categories':[(c.id,c.name,c.code,c.type) for c in cats],'budgets':[(b.id,b.category_id,b.amount,b.mode,b.source) for b in budgets]}
    return hashlib.sha256(json.dumps(raw,ensure_ascii=False).encode()).hexdigest(),cats,budgets

@router.post('/api/budgets/import/preview')
async def preview(request:Request,month:str=Query(pattern=r'^20\d{2}-(0[1-9]|1[0-2])$'),currency:Literal['UZS','USD','EUR']='UZS',scale:Literal['1','1000','1000000']='1'):
    with unit() as s:session_user(s,request,'budget')
    name=Path(unquote(request.headers.get('X-Filename','budget.xlsx'))).name
    if not name.lower().endswith('.xlsx'):raise HTTPException(422,'Для бюджета нужен файл XLSX.')
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>15*1024*1024:raise HTTPException(413,'Excel бюджета больше 15 МБ.')
    rows,issues=await run_in_threadpool(parse_budget,raw)
    with unit(True) as s:
        u,_=session_user(s,request,'budget');version,cats,budgets=snapshot(s,month,currency)
        old={b.category_id:money(b.amount) for b in budgets}
        for r in rows:
            if r['amount'] is not None:
                r['amount']*=int(scale)
                if r['amount']>10**16:issues.append({'line':r['line'],'error':'Сумма больше 100 трлн.'})
            matches=[c for c in cats if article_code(c)==r['code']]
            if not matches:matches=[c for c in cats if norm(c.name)==norm(r['name']) or norm(re.sub(r'^\d+\s*[·|—]\s*','',c.name))==norm(r['name'])]
            r['suggestion']=str(matches[0].id) if len(matches)==1 and matches[0].type=='outcome' else ''
            r['income']=any(c.type=='income' for c in matches)
        batch=BudgetImportBatch(user_id=u.id,company_id=request.state.company_id,month=month,currency=currency,filename=name[:220],payload=json.dumps({'rows':rows,'issues':issues,'version':version},ensure_ascii=False))
        s.add(batch);s.flush()
        return {'id':batch.id,'rows':[{**r,'amount':None if r['amount'] is None else money(r['amount'])} for r in rows], 'issues':issues,'month':month,'currency':currency,'scale':int(scale),'can_create':'catalog' in perms_of(u) and can_edit_section(u,'categories'),'categories':[{'id':c.id,'code':article_code(c),'name':article_name(c),'old':old.get(c.id)} for c in cats if c.type=='outcome']}

class CommitBudget(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mappings:dict[str,str]=Field(max_length=1000)

@router.post('/api/budgets/import/{id}/commit')
def commit(id:int,data:CommitBudget,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'budget');batch=get(s,BudgetImportBatch,id)
        if batch.user_id!=u.id:raise HTTPException(403,'Предпросмотр принадлежит другому пользователю.')
        if batch.status!='preview':raise HTTPException(409,'Бюджет уже загружен.')
        doc=json.loads(batch.payload)
        if doc['issues']:raise HTTPException(422,'Исправьте ошибки файла перед загрузкой бюджета.')
        version,cats,budgets=snapshot(s,batch.month,batch.currency)
        if version!=doc['version']:raise HTTPException(409,'Бюджет или статьи изменились. Откройте предпросмотр заново.')
        by_id={c.id:c for c in cats};old={b.category_id:b for b in budgets};used=set();updates=[]
        for r in doc['rows']:
            if r['amount'] is None:continue
            choice=data.mappings.get(r['code'],'')
            if choice=='skip':continue
            if choice=='new':
                require_permission(u,'catalog')
                if not can_edit_section(u,'categories'):raise HTTPException(403,'Нет права создавать статьи в справочнике.')
                if any(c.name==r['name'] or article_code(c)==r['code'] for c in cats):raise HTTPException(409,'Статья с таким кодом или названием уже есть. Выберите её.')
                c=Category(company_id=batch.company_id,code=r['code'],name=r['name'],type='outcome',activity='operating',cost_group='other');s.add(c);s.flush();cats.append(c)
            else:
                try:c=by_id[int(choice)]
                except (ValueError,KeyError):raise HTTPException(422,'Выберите расходную статью или пропуск для каждой суммы.')
                if c.type!='outcome':raise HTTPException(422,'Поступление нельзя загрузить как расходный бюджет.')
                if article_code(c) and article_code(c)!=r['code']:raise HTTPException(409,'Код выбранной статьи отличается от Excel.')
                if any(x.id!=c.id and article_code(x)==r['code'] for x in cats):raise HTTPException(409,'Код статьи уже занят.')
                c.code=r['code']
            if c.id in used:raise HTTPException(422,'Несколько строк сопоставлены одной статье.')
            used.add(c.id);b=old.get(c.id);before=b.amount if b else None
            if b is None:b=Budget(category_id=c.id,month=batch.month,currency=batch.currency,mode='soft');s.add(b)
            b.amount=r['amount'];b.source=('Excel: '+batch.filename)[:240];s.flush()
            updates.append({'category_id':c.id,'code':c.code,'before':before,'after':b.amount})
        if not updates:raise HTTPException(422,'Нет выбранных сумм для загрузки.')
        batch.status='committed'
        log(s,u,'Импорт бюджета Excel','budget_import',batch.id,json.dumps({'month':batch.month,'currency':batch.currency,'changes':updates,'skipped_codes':[r['code'] for r in doc['rows'] if data.mappings.get(r['code'])=='skip'],'blank_codes':[r['code'] for r in doc['rows'] if r['amount'] is None]},ensure_ascii=False))
        return {'updated':len(updates)}
