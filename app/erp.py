"""Treasury import staging, protected documents, reports and scheduled delivery settings."""
import csv, io, json, hashlib, secrets, re, os, zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote
from typing import Literal
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from .db import *
from .security import session_user
from .services import post_ledger, money, get, log, today

router=APIRouter()
MAX_IMPORT=5*1024*1024
HEADERS=['date','kind','account_id','to_account_id','category_id','amount','counterparty','reference','note']

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
        if len(headers)!=len(set(headers)):raise ValueError('Заголовки не должны повторяться.')
        if set(headers)!=set(HEADERS):raise ValueError('Нужны столбцы: '+', '.join(HEADERS))
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
                        data[key]=int(data[key]) if str(data[key]).strip() else None
                    model=LedgerIn(**data)
                    post_ledger(s,u,model)
                    valid.append(model.model_dump(mode='json'))
            except Exception as exc:
                from sqlalchemy.exc import IntegrityError
                message='Повторный документ или нарушенная связь.' if isinstance(exc,IntegrityError) else getattr(exc,'detail',str(exc))
                errors.append({'line':line,'error':str(message)[:1000]})
    finally:transaction.rollback()
    batch=ImportBatch(user_id=u.id,filename=name[:220],digest=hashlib.sha256(raw).hexdigest(),payload=json.dumps(valid,ensure_ascii=False),status='invalid' if errors else 'preview')
    s.add(batch);s.flush();log(s,u,'Предпросмотр импорта','import',batch.id,f'{len(rows)} строк; ошибок: {len(errors)}')
    return {'id':batch.id,'rows':valid,'errors':errors,'count':len(rows),'can_commit':not errors}

@router.get('/api/import/template.csv')
def template(request:Request):
    with unit() as s:session_user(s,request,'import')
    return Response(('\ufeff'+';'.join(HEADERS)+'\r\n').encode('utf-8'),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="transactions-template.csv"'})

@router.post('/api/import/preview')
async def import_preview(request:Request):
    with unit() as s:u,_=session_user(s,request,'import');uid=u.id
    raw=await read_upload(request)
    name=Path(unquote(request.headers.get('X-Filename','transactions.csv'))).name
    with unit(True) as s:return preview(s,get(s,User,uid),raw,name)

@router.post('/api/import/{id}/commit')
def commit_import(id:int,request:Request):
    from .main import LedgerIn
    with unit(True) as s:
        u,_=session_user(s,request,'import');batch=get(s,ImportBatch,id)
        if batch.user_id!=u.id and u.role!='admin':raise HTTPException(403,'Импорт создан другим пользователем.')
        if batch.status!='preview':raise HTTPException(409,'Импорт уже выполнен либо содержит ошибки.')
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
    with unit(True) as s:return preview(s,get(s,User,uid),raw,name)

@router.get('/api/documents')
def documents(request:Request,ledger_id:int):
    with unit() as s:
        session_user(s,request,'ledger');get(s,Ledger,ledger_id)
        return [{'id':d.id,'filename':d.filename,'url':f'/api/documents/{d.id}'} for d in s.scalars(select(Document).where(Document.ledger_id==ledger_id))]

@router.post('/api/ledger/{id}/document')
async def add_document(id:int,request:Request):
    with unit() as s:u,_=session_user(s,request,'write');uid=u.id;get(s,Ledger,id)
    raw=await read_upload(request)
    name=Path(unquote(request.headers.get('X-Filename','document'))).name
    suffix=Path(name).suffix.lower()
    allowed={'.pdf':('application/pdf',b'%PDF-'),'.png':('image/png',b'\x89PNG\r\n\x1a\n'),'.jpg':('image/jpeg',b'\xff\xd8\xff'),'.jpeg':('image/jpeg',b'\xff\xd8\xff')}
    if suffix not in allowed or not raw.startswith(allowed[suffix][1]):raise HTTPException(422,'Документ: PDF, PNG или JPEG, максимум 5 МБ.')
    with unit(True) as s:
        u=get(s,User,uid);get(s,Ledger,id)
        d=Document(ledger_id=id,filename=name[:220],mime=allowed[suffix][0],storage_key=secrets.token_hex(24),sha256=hashlib.sha256(raw).hexdigest(),content=raw,created_by=uid)
        s.add(d);s.flush();log(s,u,'Добавлен документ','document',d.id,f'ledger={id}')
        return {'id':d.id,'url':f'/api/documents/{d.id}'}

@router.get('/api/documents/{id}')
def download_document(id:int,request:Request):
    from urllib.parse import quote
    with unit() as s:
        session_user(s,request,'ledger');d=get(s,Document,id)
        return Response(d.content,media_type=d.mime,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(d.filename),'Cache-Control':'no-store'})

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

def render_report(s,year,currency,format):
    rows,totals=report_data(s,year,currency)
    title=f'Cash Flow {year} · {currency}'
    headers=['Статья','Янв','Фев','Мар','Апр','Май','Июн','Июл','Авг','Сен','Окт','Ноя','Дек']
    stream=io.BytesIO()
    if format=='xlsx':
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        wb=Workbook();ws=wb.active;ws.title='Cash Flow';ws.append([title]);ws.append(headers)
        for row in rows+[['Чистый поток']+totals]:
            # force text cell for user-controlled category names; avoid formula execution
            ws.append([row[0]]+[Decimal(v) for v in row[1:]])
            ws.cell(ws.max_row,1).data_type='s'
            for cell in ws[ws.max_row][1:]:cell.number_format='#,##0.00;[Red]-#,##0.00'
        for cell in ws[2]:cell.font=Font(color='FFFFFF',bold=True);cell.fill=PatternFill('solid',fgColor='153F45')
        ws.freeze_panes='B3';ws.column_dimensions['A'].width=40
        from openpyxl.utils import get_column_letter
        for i in range(2,14):ws.column_dimensions[get_column_letter(i)].width=19
        wb.save(stream)
    else:
        from reportlab.platypus import SimpleDocTemplate,Table,TableStyle,Paragraph,Spacer
        from reportlab.lib.pagesizes import A3,landscape
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from xml.sax.saxutils import escape
        font=next((p for p in [ROOT/'app'/'fonts'/'DejaVuSans.ttf',Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),Path('C:/Windows/Fonts/arial.ttf')] if p.exists()),None)
        if not font:raise HTTPException(503,'Шрифт PDF не установлен на сервере.')
        pdfmetrics.registerFont(TTFont('Zuma',str(font)))
        style=ParagraphStyle('zuma',fontName='Zuma',fontSize=7,leading=10)
        content=[[Paragraph(escape(str(v)),style) for v in r] for r in [headers]+rows+[['Чистый поток']+totals]]
        table=Table(content,colWidths=[155]+[77]*12,repeatRows=1)
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),'#e1f2ee'),('GRID',(0,0),(-1,-1),.3,'#d0dbdf'),('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),8)]))
        SimpleDocTemplate(stream,pagesize=landscape(A3),rightMargin=30,leftMargin=30).build([Paragraph(title,ParagraphStyle('title',fontName='Zuma',fontSize=18)),Spacer(1,18),table,Spacer(1,12),Paragraph('Фактические операции. Внутренние переводы и сторнированные операции исключены.',style)])
    return stream.getvalue()

@router.get('/api/export/report.{format}')
def export_report(format:Literal['xlsx','pdf'],request:Request,year:int=2026,currency:Literal['UZS','USD','EUR']='UZS'):
    if not 2000<=year<=2100:raise HTTPException(422,'Некорректный год.')
    with unit() as s:
        session_user(s,request,'export');raw=render_report(s,year,currency,format)
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
