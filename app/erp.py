"""Treasury import staging, protected documents, reports and scheduled delivery settings."""
import csv, io, json, hashlib, secrets, re, os, zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, quote
from typing import Literal
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from .db import *
from .security import session_user, PERMS, can_attach_document, payment_channels, perms_of, act_with_right, pay_right
from sqlalchemy.orm.attributes import flag_modified
from .services import (post_ledger, money, get, log, today, clear_approval, check_request_version,
                       DOCUMENT_KINDS, REQUIRED_DOCUMENTS)

router=APIRouter()
MAX_IMPORT=5*1024*1024
HEADERS=['date','kind','account_id','to_account_id','category_id','amount','counterparty','reference','note']
FRIENDLY=['Дата','Тип','Счёт','Счёт получателя','Статья','Сумма','Контрагент','Документ','Комментарий']

async def read_upload(request,limit=MAX_IMPORT):
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>limit:raise HTTPException(413,'Файл больше 5 МБ.')
    if not raw:raise HTTPException(422,'Пустой файл.')
    return bytes(raw)

def parse_rows(raw,name):
    try:
        if name.lower().endswith('.csv'):
            text=raw.decode('utf-8-sig')
            delimiter=';' if text.splitlines()[0].count(';')>text.splitlines()[0].count(',') else ','
            rows=list(csv.reader(io.StringIO(text),delimiter=delimiter))
        elif name.lower().endswith('.xlsx'):
            from openpyxl import load_workbook
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if sum(i.file_size for i in archive.infolist())>30*1024*1024:raise ValueError('Слишком большой распакованный XLSX.')
            book=load_workbook(io.BytesIO(raw),read_only=True,data_only=False,keep_links=False)
            try:
                sheet=book.active;rows=[]
                for row in sheet.iter_rows():
                    if len(rows)>1000:raise ValueError('Максимум 1000 строк за импорт.')
                    values=[]
                    for cell in row:
                        if cell.data_type=='f':raise ValueError('Формулы не разрешены в реестре импорта. Вставьте значения.')
                        v=cell.value
                        if isinstance(v,(datetime,date)):v=v.strftime('%Y-%m-%d')
                        values.append('' if v is None else str(v))
                    rows.append(values)
            finally:book.close()
        else:raise ValueError('Поддерживаются CSV UTF-8 и XLSX.')
        if not rows:raise ValueError('Пустая таблица.')
        headers=[str(h).strip() for h in rows[0]]
        headers=[dict(zip(FRIENDLY,HEADERS)).get(h,h) for h in headers]
        if len(headers)!=len(set(headers)):raise ValueError('Заголовки не должны повторяться.')
        if set(headers)!=set(HEADERS):raise ValueError('Нужны столбцы: '+', '.join(FRIENDLY))
        result=[]
        for line,row in enumerate(rows[1:],2):
            if not any(str(v).strip() for v in row):continue
            if len(row)!=len(headers):raise ValueError(f'Строка {line}: неверное число столбцов.')
            result.append((line,dict(zip(headers,row))))
        if not result:raise ValueError('Нет операций для импорта.')
        if len(result)>1000:raise ValueError('Максимум 1000 операций за импорт.')
        return result
    except HTTPException:raise
    except Exception as exc:
        raise HTTPException(422,str(exc) if isinstance(exc,ValueError) else 'Не удалось прочитать файл. Проверьте формат и кодировку.')

def preview(s,u,raw,name):
    from .main import LedgerIn
    rows=parse_rows(raw,name);valid=[];errors=[]
    # One rollback-only transaction checks the entire sequence, including duplicates.
    transaction=s.begin_nested()
    try:
        for line,data in rows:
            try:
                with s.begin_nested():
                    for key in ('account_id','category_id','to_account_id'):
                        value=str(data[key]).strip()
                        if not value:data[key]=None
                        elif value.isdigit():data[key]=int(value)
                        else:
                            cls=Category if key=='category_id' else Account
                            match=s.scalar(select(cls).where(cls.name==value))
                            if not match:raise ValueError('Не найдено название: '+value)
                            data[key]=match.id
                    data['kind']={'Поступление':'in','Выплата':'out','Расход':'out','Перевод':'transfer'}.get(str(data['kind']).strip(),data['kind'])
                    model=LedgerIn(**data)
                    post_ledger(s,u,model)
                    valid.append(model.model_dump(mode='json'))
            except Exception as exc:
                from sqlalchemy.exc import IntegrityError
                message='Повторный документ или нарушенная связь.' if isinstance(exc,IntegrityError) else getattr(exc,'detail',str(exc))
                errors.append({'line':line,'error':str(message)[:1000]})
    finally:transaction.rollback()
    digest=hashlib.sha256(raw).hexdigest()
    previous=s.scalar(select(ImportBatch).where(ImportBatch.digest==digest,ImportBatch.status=='committed').limit(1))
    if previous:raise HTTPException(409,f'Этот файл уже импортирован (пакет №{previous.id}). Повторная загрузка заблокирована.')
    batch=ImportBatch(user_id=u.id,filename=name[:220],digest=digest,payload=json.dumps(valid,ensure_ascii=False),status='invalid' if errors else 'preview')
    s.add(batch);s.flush();log(s,u,'Предпросмотр импорта','import',batch.id,f'{len(rows)} строк; ошибок: {len(errors)}')
    return {'id':batch.id,'rows':valid,'errors':errors,'count':len(rows),'can_commit':not errors}

@router.get('/api/import/template.csv')
def template(request:Request):
    with unit() as s:session_user(s,request,'import')
    return Response(('\ufeff'+';'.join(FRIENDLY)+'\r\n').encode('utf-8'),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="transactions-template.csv"'})

@router.post('/api/import/preview')
async def import_preview(request:Request):
    with unit() as s:u,_=session_user(s,request,'import');uid=u.id
    raw=await read_upload(request)
    name=Path(unquote(request.headers.get('X-Filename','transactions.csv'))).name
    with unit(True) as s:
        u,_=session_user(s,request,'import')
        return preview(s,u,raw,name)

@router.post('/api/import/{id}/commit')
def commit_import(id:int,request:Request):
    from .main import LedgerIn
    with unit(True) as s:
        u,_=session_user(s,request,'import');batch=get(s,ImportBatch,id)
        if batch.user_id!=u.id:raise HTTPException(403,'Импорт создан другим пользователем.')
        if batch.status!='preview':raise HTTPException(409,'Импорт уже выполнен либо содержит ошибки.')
        if s.scalar(select(ImportBatch.id).where(ImportBatch.digest==batch.digest,ImportBatch.status=='committed').limit(1)):
            raise HTTPException(409,'Этот файл уже импортирован. Повторная загрузка заблокирована.')
        count=0
        for data in json.loads(batch.payload):post_ledger(s,u,LedgerIn(**data));count+=1
        batch.status='committed';log(s,u,'Импорт операций подтверждён','import',id,str(count))
        return {'count':count}

@router.post('/api/import/google-preview')
def google_preview(request:Request):
    with unit() as s:u,_=session_user(s,request,'import');uid=u.id
    from .drive import download_model
    try:raw,name,source=download_model(file_id=os.getenv('GOOGLE_SHEETS_TRANSACTIONS_ID',''))
    except (ValueError,RuntimeError) as exc:raise HTTPException(422,str(exc))
    with unit(True) as s:
        u,_=session_user(s,request,'import')
        return preview(s,u,raw,name)

def readable_ledger(s,u,entry):
    """Плательщик без просмотра реестра читает документы только операций своего канала."""
    channels=payment_channels(perms_of(u))
    if 'view' not in perms_of(u) and channels and get(s,Account,entry.account_id).kind not in channels:
        raise HTTPException(404,'Запись не найдена.')
    return entry

def safe_filename(name,suffix=''):
    """Имя файла без управляющих символов; при обрезке расширение сохраняется."""
    name=''.join(ch for ch in name if ch.isprintable() and ch not in '\\/"').strip() or 'document'
    name=' '.join(name.split())
    if len(name)>220:
        stem,ext=(name[:-len(suffix)],suffix) if suffix and name.lower().endswith(suffix) else (name,'')
        name=stem[:220-len(ext)]+ext
    return name

@router.get('/api/documents')
def documents(request:Request,ledger_id:int):
    with unit() as s:
        u,_=session_user(s,request,'ledger');readable_ledger(s,u,get(s,Ledger,ledger_id))
        return [{'id':d.id,'filename':d.filename,'url':f'/api/documents/{d.id}'} for d in s.scalars(select(Document).where(Document.ledger_id==ledger_id))]

def writable_ledger(s,u,id):
    """Документ прикладывается к проводке выбранной компании.

    Право оплаты (pay_bank или pay_cash) не открывает посторонние операции на запись:
    исполнитель платежа прикладывает документ только к собственной оплате по заявке.
    """
    entry=get(s,Ledger,id)
    if can_attach_document(u,entry):return entry
    if not payment_channels(perms_of(u)):raise HTTPException(403,'У вашей роли нет прав на это действие.')
    raise HTTPException(403,'Документ прикладывается только к собственной оплате по утверждённой заявке.')

@router.post('/api/ledger/{id}/document')
async def add_document(id:int,request:Request):
    with unit() as s:u,_=session_user(s,request);uid=u.id;writable_ledger(s,u,id)
    raw=await read_upload(request)
    name=Path(unquote(request.headers.get('X-Filename','document'))).name
    suffix=Path(name).suffix.lower();name=safe_filename(name,suffix)
    allowed={'.pdf':('application/pdf',b'%PDF-'),'.png':('image/png',b'\x89PNG\r\n\x1a\n'),'.jpg':('image/jpeg',b'\xff\xd8\xff'),'.jpeg':('image/jpeg',b'\xff\xd8\xff')}
    if suffix not in allowed or not raw.startswith(allowed[suffix][1]):raise HTTPException(422,'Документ: PDF, PNG или JPEG, максимум 5 МБ.')
    with unit(True) as s:
        u,_=session_user(s,request);entry=writable_ledger(s,u,id)
        # Роль, давшая право приложить документ: запись операций или оплата своего канала (в том числе ВрИО).
        act_with_right(u,'write' if 'write' in perms_of(u) else pay_right(get(s,Account,entry.account_id)))
        d=Document(ledger_id=id,filename=name[:220],mime=allowed[suffix][0],storage_key=secrets.token_hex(24),sha256=hashlib.sha256(raw).hexdigest(),content=raw,created_by=uid)
        s.add(d);s.flush();log(s,u,'Добавлен документ','document',d.id,f'ledger={id}')
        return {'id':d.id,'url':f'/api/documents/{d.id}'}

@router.get('/api/documents/{id}')
def download_document(id:int,request:Request):
    with unit() as s:
        u,_=session_user(s,request,'ledger');d=get(s,Document,id);readable_ledger(s,u,get(s,Ledger,d.ledger_id))
        return Response(d.content,media_type=d.mime,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(d.filename),'Cache-Control':'no-store'})

OPEN_REQUEST_STATUSES=('draft','returned','pending','approved')
# Типы вложений заявки: сигнатура файла и для DOCX/XLSX — структура архива Office.
REQUEST_FILE_TYPES={'.pdf':('application/pdf',b'%PDF-'),'.png':('image/png',b'\x89PNG\r\n\x1a\n'),
                    '.jpg':('image/jpeg',b'\xff\xd8\xff'),'.jpeg':('image/jpeg',b'\xff\xd8\xff'),
                    '.docx':('application/vnd.openxmlformats-officedocument.wordprocessingml.document',b'PK\x03\x04'),
                    '.xlsx':('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',b'PK\x03\x04')}
REQUEST_FILE_ERROR='Документ заявки: PDF, PNG, JPEG, DOCX или XLSX, не больше 5 МБ.'

def request_file_type(raw,suffix):
    if suffix not in REQUEST_FILE_TYPES or not raw.startswith(REQUEST_FILE_TYPES[suffix][1]):raise HTTPException(422,REQUEST_FILE_ERROR)
    if suffix in ('.docx','.xlsx'):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names=archive.namelist()
                if sum(i.file_size for i in archive.infolist())>50*1024*1024:raise HTTPException(422,REQUEST_FILE_ERROR)
        except zipfile.BadZipFile:raise HTTPException(422,REQUEST_FILE_ERROR)
        folder='word/' if suffix=='.docx' else 'xl/'
        if '[Content_Types].xml' not in names or not any(n.startswith(folder) for n in names):raise HTTPException(422,REQUEST_FILE_ERROR)
    return REQUEST_FILE_TYPES[suffix][0]

def request_document_access(s,user,payment,write=False):
    """Права на документы заявки берутся из роли в выбранной компании (и ВрИО), а не из прежнего поля роли.

    Читает тот, кто видит заявку: автор, реестр (view), проверяющий бухгалтер, плательщик канала
    утверждённой заявки. Меняет автор или финансовый руководитель, пока заявка не оплачена и не закрыта."""
    from .requests_api import can_view_request
    if write:
        permissions=perms_of(user)
        own=payment.creator_id==user.id and 'request' in permissions
        if not (own or 'request_edit' in permissions):raise HTTPException(403,'Документы изменяет автор или финансовый руководитель.')
        act_with_right(user,'request' if own else 'request_edit')
        if payment.status=='rejected':raise HTTPException(409,'Отклонённая заявка хранится только для чтения.')
        if payment.status not in OPEN_REQUEST_STATUSES:raise HTTPException(409,'Документы оплаченной или закрытой заявки не меняются.')
        return
    if not can_view_request(s,user,payment):raise HTTPException(403,'Нет доступа к документам этой заявки.')

def request_version_of(request):
    raw=request.headers.get('X-Request-Version') or request.query_params.get('version')
    try:return int(raw) if raw else None
    except ValueError:raise HTTPException(422,'Некорректная версия заявки.')

def check_document_version(payment,version):
    """После отправки документы меняются только по актуальной версии заявки; в черновике версия
    проверяется, если клиент её передал."""
    if payment.status in ('pending','approved') or version is not None:check_request_version(payment,version)

def after_document_change(s,user,payment):
    """Любое изменение документов меняет версию заявки: тот, кто читал её раньше, при отправке или правке
    получит 409 и увидит новый состав файлов. После отправки снимаются проверка и согласования, заявка снова
    на согласовании; изменивший становится последним редактором и её не согласует (services.round_participants)."""
    reset=payment.status in ('pending','approved')
    if reset:
        clear_approval(payment);payment.status='pending'
    payment.last_editor_id=user.id
    flag_modified(payment,'decision_note')
    s.flush()
    return reset

def request_document_json(d):
    return {'id':d.id,'kind':d.kind,'label':DOCUMENT_KINDS[d.kind],'filename':d.filename,
            'version':d.version,'created_at':str(d.created_at),'url':f'/api/request-documents/{d.id}'}

@router.get('/api/requests/{id}/documents')
def request_documents(id:int,request:Request,history:bool=False):
    """Текущие документы заявки; с history=true — и прежние версии с автором."""
    with unit() as s:
        user,_=session_user(s,request);payment=get(s,PaymentRequest,id);request_document_access(s,user,payment)
        query=select(RequestDocument).where(RequestDocument.request_id==id)
        if not history:query=query.where(RequestDocument.active==True)
        names={u.id:u.name for u in s.scalars(select(User).execution_options(company_unscoped=True))} if history else {}
        rows=s.scalars(query.order_by(RequestDocument.kind,RequestDocument.version,RequestDocument.id))
        return [{**request_document_json(d),'active':d.active,**({'created_by':names.get(d.created_by,'')} if history else {})} for d in rows]

@router.post('/api/requests/{id}/documents')
async def add_request_document(id:int,kind:Literal['internal','contract','other'],request:Request):
    with unit() as s:
        user,_=session_user(s,request);payment=get(s,PaymentRequest,id);request_document_access(s,user,payment,True)
        check_document_version(payment,request_version_of(request));uid=user.id
    raw=await read_upload(request)
    name=Path(unquote(request.headers.get('X-Filename','document'))).name
    suffix=Path(name).suffix.lower();name=safe_filename(name,suffix);mime=request_file_type(raw,suffix)
    with unit(True) as s:
        user,_=session_user(s,request);payment=get(s,PaymentRequest,id);request_document_access(s,user,payment,True)
        check_document_version(payment,request_version_of(request))
        previous=list(s.scalars(select(RequestDocument).where(RequestDocument.request_id==id,RequestDocument.kind==kind).order_by(RequestDocument.version.desc())))
        sha=hashlib.sha256(raw).hexdigest()
        same=next((old for old in previous if old.active and old.sha256==sha),None)
        if same:
            # Повтор той же загрузки (например, после потерянного ответа) не создаёт вторую версию и не снимает согласования.
            return {**request_document_json(same),'request_version':payment.version,'status':payment.status,'approval_reset':False,'duplicate':True}
        # Прочие документы независимы друг от друга: у каждого версия 1. Обязательный документ один,
        # и каждая замена получает следующий номер версии.
        version=1 if kind=='other' else ((previous[0].version+1) if previous else 1)
        replaced=[old for old in previous if old.active] if kind!='other' else []
        # Обязательный документ один: новая версия заменяет прежнюю, прежняя остаётся в истории.
        for old in replaced:old.active=False
        doc=RequestDocument(request_id=id,kind=kind,filename=name[:220],mime=mime,storage_key=secrets.token_hex(24),
                            sha256=sha,content=raw,version=version,active=True,created_by=uid)
        s.add(doc);s.flush();reset=after_document_change(s,user,payment)
        log(s,user,'Новая версия документа заявки' if replaced else 'Добавлен документ заявки','request',payment.id,
            json.dumps({'document_id':doc.id,'kind':kind,'version':version,'filename':doc.filename,'size':len(raw),'sha256':doc.sha256,
                        'replaced':[old.id for old in replaced],'approval_reset':reset,'request_version':payment.version},ensure_ascii=False))
        return {**request_document_json(doc),'request_version':payment.version,'status':payment.status,'approval_reset':reset}

def remove_document(s,user,doc,reason):
    payment=get(s,PaymentRequest,doc.request_id)
    doc.active=False;reset=after_document_change(s,user,payment)
    log(s,user,'Убран документ заявки','request',payment.id,json.dumps({'document_id':doc.id,'kind':doc.kind,'filename':doc.filename,
        'approval_reset':reset,'reason':reason,'request_version':payment.version},ensure_ascii=False))
    return {'ok':True,'request_version':payment.version,'status':payment.status,'approval_reset':reset}

@router.delete('/api/request-documents/{id}')
def delete_request_document(id:int,request:Request):
    """Убрать файл из черновика или возвращённой заявки (форма заявки)."""
    with unit(True) as s:
        user,_=session_user(s,request);doc=s.get(RequestDocument,id)
        if not doc or not doc.active:raise HTTPException(404,'Документ не найден.')
        payment=get(s,PaymentRequest,doc.request_id);request_document_access(s,user,payment,True)
        if payment.status not in ('draft','returned'):raise HTTPException(409,'После отправки документ убирают с указанием причины в карточке заявки.')
        check_document_version(payment,request_version_of(request))
        return remove_document(s,user,doc,'')

class RemoveDocumentIn(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    version:int=Field(ge=1)
    reason:str=Field(min_length=10,max_length=1000)

@router.post('/api/request-documents/{id}/remove')
def remove_request_document(id:int,data:RemoveDocumentIn,request:Request):
    """После отправки убирается только прочий документ; обязательные заменяются новой версией.
    Согласование при этом начинается заново."""
    with unit(True) as s:
        user,_=session_user(s,request);doc=s.get(RequestDocument,id)
        if not doc:raise HTTPException(404,'Документ не найден.')
        payment=get(s,PaymentRequest,doc.request_id);request_document_access(s,user,payment,True)
        check_request_version(payment,data.version)
        if not doc.active:raise HTTPException(409,'Документ уже заменён или убран.')
        if doc.kind in REQUIRED_DOCUMENTS and payment.status in ('pending','approved'):
            raise HTTPException(409,'Обязательный документ не убирается: загрузите новую версию.')
        return remove_document(s,user,doc,data.reason)

@router.get('/api/request-documents/{id}')
def download_request_document(id:int,request:Request):
    with unit() as s:
        user,_=session_user(s,request);doc=s.get(RequestDocument,id)
        # Прежние версии остаются доступны тем, кто вправе читать документы заявки.
        if not doc:raise HTTPException(404,'Документ не найден.')
        payment=get(s,PaymentRequest,doc.request_id);request_document_access(s,user,payment)
        return Response(doc.content,media_type=doc.mime,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(doc.filename),'Cache-Control':'no-store'})

def report_data(s,year,currency):
    from .services import effective_cashflows
    aids={a.id for a in s.scalars(select(Account).where(Account.currency==currency))}
    rows=[];totals=[0]*12
    entries=list(s.scalars(effective_cashflows(s).where(Ledger.date>=date(year,1,1),Ledger.date<=date(year,12,31))))
    for c in s.scalars(select(Category).order_by(Category.id)):
        values=[0]*12
        for t in entries:
            if t.account_id in aids and t.category_id==c.id and t.kind!='transfer':values[t.date.month-1]+=t.amount if t.kind=='in' else -t.amount
        if any(values):rows.append([c.name]+[money(v) for v in values])
        totals=[a+b for a,b in zip(totals,values)]
    return rows, [money(v) for v in totals]

def render_report(s,year,currency,format,mode='actual',start_month=1,end_month=12,company_id=None,scenario='A',as_of=None):
    from .report_export import render
    return render(s,year,currency,format,mode,start_month,end_month,company_id,scenario,as_of)

@router.get('/api/export/report.{format}')
def export_report(format:Literal['xlsx','pdf'],request:Request,year:int=2026,currency:Literal['UZS','USD','EUR']='UZS',mode:Literal['actual','plan']='actual',start_month:int=1,end_month:int=12,company_id:int|None=None,scenario:Literal['A','B','V']='A',as_of:date|None=None):
    if not 2000<=year<=2100:raise HTTPException(422,'Некорректный год.')
    with unit() as s:
        session_user(s,request,'export')
        if not 1<=start_month<=end_month<=12:raise HTTPException(422,'Некорректные месяцы.')
        if as_of and (as_of.year!=year or as_of>today()):raise HTTPException(422,'Некорректная дата отчёта.')
        company_id=company_id or s.info['company_id']
        get(s,Company,company_id)
        raw=render_report(s,year,currency,format,mode,start_month,end_month,company_id,scenario,as_of)
    mime='application/pdf' if format=='pdf' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return Response(raw,media_type=mime,headers={'Content-Disposition':f'attachment; filename="cashflow-{year}-{currency}.{format}"'})

class ScheduleIn(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    recipient:str=Field(min_length=5,max_length=254)
    currency:Literal['UZS','USD','EUR']='UZS'
    hour:int=Field(default=9,ge=0,le=23)
    enabled:bool=False

@router.get('/api/report-schedules')
def schedules(request:Request):
    with unit() as s:
        session_user(s,request,'users')
        return {'smtp_configured':bool(os.getenv('SMTP_HOST') and os.getenv('SMTP_FROM')),'google_configured':bool(os.getenv('GOOGLE_SHEETS_TRANSACTIONS_ID') and os.getenv('GOOGLE_APPLICATION_CREDENTIALS')),'rows':[{'id':r.id,'recipient':r.recipient,'currency':r.currency,'hour':r.hour,'enabled':r.enabled,'last_sent':str(r.last_sent) if r.last_sent else None,'last_error':r.last_error} for r in s.scalars(select(ReportSchedule))]}

@router.post('/api/report-schedules')
def save_schedule(data:ScheduleIn,request:Request):
    if not re.fullmatch(r'[^\s@<>\r\n]+@[^\s@<>\r\n]+\.[^\s@<>\r\n]+',data.recipient):raise HTTPException(422,'Некорректный email.')
    if data.enabled and not (os.getenv('SMTP_HOST') and os.getenv('SMTP_FROM')):raise HTTPException(422,'Сначала настройте SMTP_HOST и SMTP_FROM на сервере.')
    with unit(True) as s:
        u,_=session_user(s,request,'users');r=ReportSchedule(**data.model_dump(),created_by=u.id)
        s.add(r);s.flush();log(s,u,'Создано расписание отчёта','schedule',r.id,data.recipient)
        return {'id':r.id}

@router.delete('/api/report-schedules/{id}')
def delete_schedule(id:int,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'users');r=get(s,ReportSchedule,id);s.delete(r);log(s,u,'Удалено расписание отчёта','schedule',id)
    return {'ok':True}

@router.put('/api/report-schedules/{id}')
def update_schedule(id:int,data:ScheduleIn,request:Request):
    if not re.fullmatch(r'[^\s@<>\r\n]+@[^\s@<>\r\n]+\.[^\s@<>\r\n]+',data.recipient):raise HTTPException(422,'Некорректный email.')
    if data.enabled and not (os.getenv('SMTP_HOST') and os.getenv('SMTP_FROM')):raise HTTPException(422,'Сначала настройте SMTP на сервере.')
    with unit(True) as s:
        u,_=session_user(s,request,'users');r=get(s,ReportSchedule,id)
        for key,value in data.model_dump().items():setattr(r,key,value)
        log(s,u,'Изменено расписание отчёта','schedule',id,data.recipient)
        return {'id':id}
