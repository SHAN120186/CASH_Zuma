"""Company-scoped counterparties and validated Excel import; no ledger writes."""
import io, re, zipfile, json
from urllib.parse import unquote
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from .db import Counterparty, unit
from .security import session_user
from .services import get, log
from .erp import read_upload

router=APIRouter()

class CounterpartyIn(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name:str=Field(min_length=2,max_length=160)
    inn:str=Field(default='',max_length=20,pattern=r'^\d*$')
    note:str=Field(default='',max_length=240)

class ImportIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    rows:list[CounterpartyIn]=Field(min_length=1,max_length=1000)

def row(c):return {'id':c.id,'name':c.name,'inn':c.inn,'note':c.note}

def conflict(existing,data,exclude=None,allow_fill=False):
    if data.inn and any(c.id!=exclude and c.inn==data.inn and c.name.casefold()!=data.name.casefold() for c in existing):
        raise HTTPException(409,'Этот ИНН уже указан у другого контрагента. Проверьте название.')
    for c in existing:
        if c.id==exclude:continue
        if c.name.casefold()==data.name.casefold():
            if allow_fill and not (c.inn and data.inn and c.inn!=data.inn or c.note and data.note and c.note!=data.note):return c
            if (c.inn,c.note)==(data.inn,data.note):return c
            raise HTTPException(409,'Контрагент с таким названием уже есть. Измените его в справочнике.')
    return None

@router.get('/api/counterparties')
def listing(request:Request):
    with unit() as s:
        session_user(s,request,'counterparties')
        return [row(c) for c in s.scalars(select(Counterparty).order_by(Counterparty.name))]

@router.post('/api/counterparties')
def create(data:CounterpartyIn,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'counterparties')
        same=conflict(list(s.scalars(select(Counterparty))),data)
        if same:return row(same)
        c=Counterparty(**data.model_dump());s.add(c);s.flush()
        log(s,u,'Добавлен контрагент','counterparty',c.id,c.name)
        return row(c)

@router.put('/api/counterparties/{id}')
def edit(id:int,data:CounterpartyIn,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'counterparties');c=get(s,Counterparty,id)
        if conflict(list(s.scalars(select(Counterparty))),data,c.id):raise HTTPException(409,'Контрагент с таким названием уже есть. Измените его в справочнике.')
        before=row(c)
        for k,v in data.model_dump().items():setattr(c,k,v)
        s.flush();log(s,u,'Изменён контрагент','counterparty',c.id,json.dumps({'before':before,'after':row(c)},ensure_ascii=False))
        return row(c)

def excel_rows(raw):
    try:
        from openpyxl import load_workbook
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if sum(x.file_size for x in z.infolist())>30*1024*1024:raise ValueError('Слишком большой распакованный XLSX.')
        book=load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
        try:
            iterator=book.active.iter_rows()
            header=next(iterator)
            aliases={'контрагент':'name','наименование':'name','название':'name','name':'name','kontragent':'name','инн':'inn','inn':'inn','stir':'inn','примечание':'note','note':'note','izoh':'note'}
            keys=[aliases.get(str(c.value or '').strip().lower()) for c in header]
            active=[k for k in keys if k]
            if not {'name','inn'}<=set(active) or len(active)!=len(set(active)):
                raise ValueError('Нужны столбцы «Контрагент», «ИНН»; «Примечание» необязательно.')
            result=[]
            for line,cells in enumerate(iterator,2):
                if line>1001:raise ValueError('Максимум 1000 строк за импорт.')
                if not any(c.value is not None for c in cells):continue
                values={}
                for key,cell in zip(keys,cells):
                    if not key:continue
                    if cell.data_type=='f':raise ValueError(f'Строка {line}: формулы не разрешены. Вставьте значения.')
                    value=cell.value
                    if isinstance(value,(int,float)) and not isinstance(value,bool) and float(value).is_integer():
                        value=str(int(value))
                        if key=='inn' and re.fullmatch('0+',cell.number_format or ''):value=value.zfill(len(cell.number_format))
                    values[key]='' if value is None else str(value).strip()
                result.append((line,values))
            if not result:raise ValueError('В файле нет контрагентов.')
            return result
        finally:book.close()
    except HTTPException:raise
    except Exception as e:
        raise HTTPException(422,str(e) if isinstance(e,ValueError) else 'Не удалось прочитать Excel. Используйте XLSX.')

@router.post('/api/counterparties/import/preview')
async def preview(request:Request):
    with unit() as s:
        session_user(s,request,'counterparties')
    if not unquote(request.headers.get('X-Filename','')).lower().endswith('.xlsx'):
        raise HTTPException(422,'Не удалось прочитать Excel. Используйте XLSX.')
    source=excel_rows(await read_upload(request))
    with unit() as s:
        session_user(s,request,'counterparties');existing=list(s.scalars(select(Counterparty)))
        rows=[];errors=[];names=set();inns=set();skipped=0;updated=0
        for line,values in source:
            try:
                data=CounterpartyIn(**values)
                if data.name.casefold() in names or data.inn and data.inn in inns:
                    raise ValueError('Дубликат названия или ИНН в файле.')
                names.add(data.name.casefold())
                if data.inn:inns.add(data.inn)
                same=conflict(existing,data,allow_fill=True)
                if same:
                    if not same.inn and data.inn or not same.note and data.note:updated+=1
                    else:skipped+=1
                rows.append(data.model_dump())
            except HTTPException as e:errors.append({'line':line,'error':e.detail})
            except (ValueError,TypeError):errors.append({'line':line,'error':'Проверьте название, ИНН и примечание. ИНН содержит только цифры.'})
        return {'rows':rows,'errors':errors,'skipped':skipped,'updated':updated,'can_commit':bool(rows) and not errors}

@router.post('/api/counterparties/import/commit')
def commit(data:ImportIn,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'counterparties');existing=list(s.scalars(select(Counterparty)));added=0;skipped=0;updated=0
        for item in data.rows:
            same=conflict(existing,item,allow_fill=True)
            if same:
                before=row(same)
                if not same.inn:same.inn=item.inn
                if not same.note:same.note=item.note
                if row(same)!=before:
                    updated+=1
                    log(s,u,'Дополнены реквизиты контрагента','counterparty',same.id,json.dumps({'before':before,'after':row(same)},ensure_ascii=False))
                else:skipped+=1
                continue
            c=Counterparty(**item.model_dump());s.add(c);s.flush();existing.append(c);added+=1
        log(s,u,'Загружен справочник контрагентов','counterparty','import',json.dumps({'added':added,'updated':updated,'skipped':skipped}))
        return {'added':added,'updated':updated,'skipped':skipped}
