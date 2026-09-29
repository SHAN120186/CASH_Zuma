from __future__ import annotations
import os, json, re, secrets, csv, io, logging
from pathlib import Path
from datetime import date, timedelta
import datetime as dt
from typing import Literal, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, delete, func, or_, and_
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm.exc import StaleDataError
from .db import *
from .security import *
from .services import *
from .company_scope import available_companies, setting_key, get_setting, active_delegations
from . import access
from .model_import import import_snapshot, MAX_SIZE
from .bot_api import drop_groups_of, drop_orphan_groups, drop_shadowed_groups, journal_path, revoke_telegram
from .workdays import enforce_lead_time, due_minimums
from .requests_api import request_view, can_view_request

SECURE=os.getenv('COOKIE_SECURE','0')=='1'
ALLOWED=[x.strip() for x in os.getenv('ALLOWED_HOSTS','127.0.0.1,localhost,testserver').split(',') if x.strip()]
PUBLIC_ORIGIN=os.getenv('PUBLIC_ORIGIN','').rstrip('/')

@asynccontextmanager
async def lifespan(app):
    initialize()
    # A stored Telegram group under a chat id fixed in BOT_REPORT_GROUPS must not outlive the entry.
    drop_shadowed_groups()
    drop_orphan_groups()
    yield
app=FastAPI(title='UZGERMED Treasury',version='2.14.0',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=ALLOWED)
app.mount('/static',StaticFiles(directory=ROOT/'app'/'static'),name='static')

@app.middleware('http')
async def safety(request,call_next):
    if request.method not in ('GET','HEAD','OPTIONS'):
        try:length=int(request.headers.get('content-length','0' if request.method=='DELETE' else '-1'))
        except ValueError:length=-1
        limit=MAX_SIZE if request.url.path=='/api/model/upload' else 5*1024*1024 if request.url.path in ('/api/import/preview','/api/plan-import/preview') or request.url.path.endswith('/document') or '/documents' in request.url.path else 65536
        if length<0 or length>limit:return JSONResponse({'detail':'Неверный размер запроса.'},status_code=413)
        origin=request.headers.get('origin')
        expected=PUBLIC_ORIGIN or str(request.base_url).rstrip('/')
        if origin and origin!=expected:return JSONResponse({'detail':'Запрос с другого сайта запрещён.'},status_code=403)
    response=await call_next(request)
    # Технический журнал пишется только для вошедших пользователей и бота с верным ключом:
    # анонимные 401/404 не занимают общую блокировку записи. Неудачные входы учитывает ограничение попыток.
    if request.url.path.startswith('/api') and (getattr(request.state,'user_id',None) or getattr(request.state,'bot',False)):
        try:
            with unit(True) as audit_session:
                audit_session.add(Audit(company_id=getattr(request.state,'company_id',None),user_id=getattr(request.state,'user_id',None),action='API '+request.method,entity='http',detail=journal_path(request.url.path)+'; status='+str(response.status_code),
                                        ip=client_ip(request),forwarded_for=(request.headers.get('X-Forwarded-For') or '')[:200] or None))
        except OperationalError:
            # Financial changes and their business audit are committed together.
            # A failed secondary HTTP log must not disguise a completed payment.
            logging.error('Could not persist HTTP audit: database unavailable')
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    if SECURE:response.headers['Strict-Transport-Security']='max-age=31536000'
    if request.url.path.startswith('/api') or request.url.path in ('/','/version.json'):response.headers['Cache-Control']='no-store'
    return response

@app.exception_handler(IntegrityError)
async def duplicate(request,exc):
    return JSONResponse({'detail':'Дубликат или связанная запись: такой документ/пользователь уже существует. Повторная оплата заблокирована.'},status_code=409)
@app.exception_handler(StaleDataError)
async def stale_document(request,exc):
    return JSONResponse({'detail':'Документ изменён другим пользователем. Обновите данные и повторно проверьте действие.'},status_code=409)
@app.exception_handler(OperationalError)
async def busy(request,exc):
    logging.error('Database operation failed: %s',type(exc).__name__)
    return JSONResponse({'detail':'База данных временно недоступна. Повторите запрос; не создавайте дубликат документа.'},status_code=503)
@app.get('/')
def index():return FileResponse(ROOT/'app'/'static'/'erp'/'index.html')
@app.get('/legacy')
def legacy():return FileResponse(ROOT/'app'/'static'/'index.html')
@app.get('/health')
def health():return {'status':'ok'}

@app.get('/ready')
def ready():
    with unit() as s:
        if s.get(Guard,1) is None:
            raise HTTPException(503,'База данных ещё не подготовлена.')
    return {'status':'ok','database':'ok'}

@app.get('/version.json')
def release_version():return FileResponse(ROOT/'release.json',media_type='application/json')

class Input(BaseModel):model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
class LoginIn(Input):
    username:str=Field(min_length=1,max_length=80)
    password:str=Field(min_length=1,max_length=128)
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=False)
class UserIn(Input):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=False)
    username:str=Field(pattern=r'^[a-zA-Z0-9_.-]{3,80}$')
    name:str=Field(min_length=2,max_length=160)
    password:str=Field(min_length=12,max_length=128)
    role:Literal['admin','founder','director','finance','accountant','employee','auditor','cashier','operator','investor','material_accountant','procurement']
class UserEdit(Input):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=False)
    role:Literal['admin','founder','director','finance','accountant','employee','auditor','cashier','operator','investor','material_accountant','procurement']
    active:bool
    password:str=Field(default='',max_length=128)
class PasswordIn(Input):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=False)
    old_password:str=Field(min_length=1,max_length=128)
    new_password:str=Field(min_length=12,max_length=128)
class AccountIn(Input):
    name:str=Field(min_length=2,max_length=160)
    kind:Literal['bank','cash']
    currency:Literal['UZS','USD','EUR']
    opening:str
    opening_date:date
    allow_overdraft:bool=False
    company_id:Optional[int]=None
class CategoryIn(Input):
    name:str=Field(min_length=2,max_length=160)
    activity:Literal['operating','investing','financing']='operating'
    type:Literal['income','outcome']='outcome'
class AccountEdit(AccountIn):
    reason:str=Field(min_length=10,max_length=1000)
class BudgetIn(Input):
    category_id:int
    month:str=Field(pattern=r'^20\d{2}-(0[1-9]|1[0-2])$')
    currency:Literal['UZS','USD','EUR']
    amount:Optional[str]=None
    mode:Literal['soft','hard']='soft'
    cost_group:Optional[Literal['fixed','variable','other']]=None
    source:str=Field(default='Введён вручную',max_length=240)
    reason:str=Field(min_length=10,max_length=1000)
class RequestIn(Input):
    # Обязательные поля формы: компания, канал и валюта сверяются со счётом списания.
    company_id:int
    account_id:int
    channel:Literal['bank','cash']
    currency:Literal['UZS','USD','EUR']
    category_id:int
    counterparty:str=Field(min_length=2,max_length=160)
    amount:str
    date:date
    purpose:str=Field(min_length=5,max_length=3000)
    project:str=Field(default='',max_length=120)
    priority:Literal['normal','high','urgent']
    status:Literal['draft','pending']='draft'
class RequestEdit(RequestIn):
    version:int=Field(ge=1)
    reason:str=Field(min_length=10,max_length=1000)
class DecisionIn(Input):
    # 'reject', 'cancel' и 'stop' остаются в схеме, чтобы старые клиенты получили понятный ответ.
    action:Literal['approve','check','submit','reschedule','return','return_finance','close','reject','cancel','stop']
    version:Optional[int]=Field(default=None,ge=1)
    note:str=Field(default='',max_length=2000)
    date:Optional[dt.date]=None
class ReceiptIn(Input):
    account_id:int
    category_id:int
    counterparty:str=Field(min_length=2,max_length=160)
    amount:str
    date:date
    note:str=Field(default='',max_length=2000)
class LedgerIn(Input):
    account_id:int
    to_account_id:Optional[int]=None
    category_id:Optional[int]=None
    kind:Literal['in','out','transfer']
    amount:str
    date:date
    counterparty:str=Field(default='',max_length=160)
    reference:str=Field(min_length=2,max_length=160)
    note:str=Field(default='',max_length=3000)
    request_id:Optional[int]=None
    request_version:Optional[int]=Field(default=None,ge=1)
    receipt_id:Optional[int]=None
class ReverseIn(Input):
    reason:str=Field(min_length=10,max_length=1000)
class ReserveIn(Input):
    currency:Literal['UZS','USD','EUR']
    amount:str

class ArchiveIn(Input):
    archived:bool
    reason:str=Field(min_length=10,max_length=1000)

class ResetPasswordIn(Input):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=False)
    password:str=Field(min_length=12,max_length=128)

@app.post('/api/login')
def login(body:LoginIn,request:Request,response:Response):
    error=None
    with unit(True) as s:
        username=body.username.strip().lower();keys=login_keys(request,username)
        if login_limited(s,keys):error=HTTPException(429,'Слишком много попыток. Повторите через 10 минут.')
        else:
            user=s.scalar(select(User).where(User.username==username,User.active==True).execution_options(company_unscoped=True))
            # Стоимость проверки одинакова и для отсутствующего или архивного пользователя.
            if not user:verify_password(body.password,DUMMY_HASH)
            if not user or not verify_password(body.password,user.password_hash):
                record_failure(s,keys);error=HTTPException(401,'Неверный логин или пароль.')
            else:
                s.execute(delete(LoginAttempt).where(LoginAttempt.key.in_([keys[0],keys[2]])))
                s.execute(delete(LoginSession).where(LoginSession.expires_at<now()))
                token=issue_token(user.id);csrf=secrets.token_urlsafe(32)
                if not user.password_hash.startswith('bcrypt_sha256$'):
                    # Старый хеш заменяется; пароль, не проходящий нынешние правила, нужно сменить.
                    try:user.password_hash=hash_password(body.password)
                    except ValueError:user.must_change_password=True
                access.scope_home(s,user)
                s.add(LoginSession(token_hash=digest(token),user_id=user.id,csrf=csrf,expires_at=now()+timedelta(hours=1)))
                user._ip=request.client.host if request.client else None
                user._forwarded=(request.headers.get('X-Forwarded-For') or '')[:200] or None
                log(s,user,'Вход в систему','user',user.id)
                result={'user':user_json(user),'csrf':csrf,'access_token':token,'token_type':'bearer','expires_in':3600}
    if error:raise error
    response.set_cookie('zuma_session',token,httponly=True,secure=SECURE,samesite='strict',max_age=3600,path='/')
    return result

@app.get('/api/me')
def me(request:Request):
    with unit() as s:
        user,session=session_user(s,request);access.scope_home(s,user)
        return {'user':user_json(user),'csrf':session.csrf,'today':str(today())}
@app.post('/api/logout')
def logout(request:Request,response:Response):
    with unit(True) as s:
        user,session=session_user(s,request);s.delete(session);log(s,user,'Выход','user',user.id)
    response.delete_cookie('zuma_session',path='/');return {'ok':True}
@app.post('/api/password')
def change_password(data:PasswordIn,request:Request):
    error=None
    with unit(True) as s:
        user,session=session_user(s,request);key=[digest('password-user:'+str(user.id))]
        # Подбор текущего пароля из открытого сеанса ограничен так же, как вход.
        if login_limited(s,key):raise HTTPException(429,'Слишком много попыток. Повторите через 10 минут.')
        if not verify_password(data.old_password,user.password_hash):record_failure(s,key);error=HTTPException(403,'Текущий пароль неверен.')
    if error:raise error
    with unit(True) as s:
        user,session=session_user(s,request)
        if verify_password(data.new_password,user.password_hash):raise HTTPException(422,'Новый пароль должен отличаться от текущего.')
        try:user.password_hash=hash_password(data.new_password)
        except ValueError as e:raise HTTPException(422,str(e))
        forced=user.must_change_password;user.must_change_password=False
        s.execute(delete(LoginSession).where(LoginSession.user_id==user.id));revoke_telegram(s,user.id)
        log(s,user,'Временный пароль заменён; сессии отозваны' if forced else 'Изменён пароль; сессии отозваны','user',user.id)
    return {'ok':True}

@app.get('/api/companies')
def company_choices(request:Request):
    with unit() as s:
        user,session=session_user(s,request);access.scope_home(s,user)
        return {'companies':[{'id':c.id,'code':c.code,'name':c.name} for c in available_companies(s,user)],'user':user_json(user),'csrf':session.csrf}

@app.get('/api/bootstrap')
def bootstrap(request:Request):
    with unit() as s:
        user,session=session_user(s,request)
        companies=[{'id':x.id,'code':x.code,'name':x.name} for x in available_companies(s,user)]
        a=[{'id':a.id,'name':a.name,'kind':a.kind,'currency':a.currency,'company_id':a.company_id,'opening_date':str(a.opening_date)} for a in s.scalars(select(Account).where(Account.archived==False).order_by(Account.name))]
        c=[{'id':c.id,'name':c.name,'activity':c.activity,'type':c.type} for c in s.scalars(select(Category).order_by(Category.id))]
        # Контрагенты собраны из операций и чужих заявок: только тем, кто видит реестр.
        cp=[{'name':x.name,'inn':x.inn} for x in s.scalars(select(Counterparty).order_by(Counterparty.name).limit(1000))] if {'view','ledger'}&perms_of(user) else []
        return {'accounts':a,'companies':companies,'categories':c,'counterparties':cp,'roles':ROLES,'today':str(today()),
                'due_minimums':due_minimums(s,today()),'user':user_json(user),'csrf':session.csrf}

def remember_counterparty(s,name):
    """Справочник пополняется автоматически при вводе документов."""
    name=(name or '').strip()
    if len(name)<2 or len(name)>160:return
    if not s.scalar(select(Counterparty.id).where(Counterparty.name==name)):s.add(Counterparty(name=name))

@app.get('/api/dashboard')
def get_dashboard(request:Request,currency:Literal['UZS','USD','EUR']='UZS',days:int=30):
    if days not in (7,30,90):raise HTTPException(422,'Период: 7, 30 или 90 дней.')
    with unit() as s:
        session_user(s,request,'view');return dashboard(s,currency,days)

@app.get('/api/accounts')
def accounts(request:Request,archived:bool=False):
    with unit() as s:
        session_user(s,request,'view')
        return [{'id':a.id,'name':a.name,'kind':a.kind,'currency':a.currency,'company_id':a.company_id,'opening':money(a.opening),
                 'balance':money(account_balance(s,a)),'opening_date':str(a.opening_date),'allow_overdraft':a.allow_overdraft,'archived':a.archived} for a in s.scalars(select(Account).where(Account.archived==archived).order_by(Account.id))]

@app.post('/api/accounts/{id}/archive')
def archive_account(id:int,data:ArchiveIn,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'users');a=get(s,Account,id)
        if 'write' not in perms_of(u):raise HTTPException(403,'Архивировать счёт может администратор с назначением «Директор», «Финансовый директор» или «Сотрудник / оператор» в этой компании.')
        if data.archived:
            if account_balance(s,a)!=0:raise HTTPException(409,'Сначала перенесите остаток: архивировать можно счёт с нулевым балансом.')
            if s.scalar(select(PaymentRequest.id).where(PaymentRequest.account_id==id,PaymentRequest.status.in_(['draft','pending','approved','returned'])).limit(1)) or s.scalar(select(Receipt.id).where(Receipt.account_id==id,Receipt.status=='expected').limit(1)):
                raise HTTPException(409,'Сначала закройте или перенесите незавершённые заявки и поступления.')
        a.archived=data.archived
        log(s,u,'Счёт в архиве' if data.archived else 'Счёт восстановлен','account',id,data.reason)
        return {'ok':True}

@app.post('/api/accounts')
def add_account(data:AccountIn,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'write')
        if data.opening_date>today():raise HTTPException(422,'Начальный остаток не может быть задан будущей датой.')
        if data.kind=='cash' and data.allow_overdraft:raise HTTPException(422,'Для кассы отрицательный остаток запрещён.')
        company_id=data.company_id or s.info['company_id']
        get(s,Company,company_id)
        a=Account(name=data.name,kind=data.kind,currency=data.currency,company_id=company_id,opening=amount(data.opening,True),opening_date=data.opening_date,allow_overdraft=data.allow_overdraft,created_by=u.id)
        s.add(a);s.flush();log(s,u,'Создан счёт / касса','account',a.id,f'{a.name}; {money(a.opening)} {a.currency}; начало дня {a.opening_date}')
        return {'id':a.id}
@app.post('/api/accounts/{id}')
def edit_account(id:int,data:AccountEdit,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'write');a=get(s,Account,id)
        if a.archived:raise HTTPException(409,'Сначала восстановите счёт из архива.')
        if data.opening_date>today():raise HTTPException(422,'Начальный остаток не может быть задан будущей датой.')
        if data.kind=='cash' and data.allow_overdraft:raise HTTPException(422,'Для кассы отрицательный остаток запрещён.')
        entries=list(s.scalars(select(Ledger).where((Ledger.account_id==id)|(Ledger.to_account_id==id))))
        requests=list(s.scalars(select(PaymentRequest).where(PaymentRequest.account_id==id)))
        receipts=list(s.scalars(select(Receipt).where(Receipt.account_id==id)))
        if data.currency!=a.currency and (entries or requests or receipts):
            raise HTTPException(409,'Валюту нельзя менять после создания операций, заявок или ожидаемых поступлений. Создайте отдельный счёт в нужной валюте.')
        if data.kind!=a.kind and (entries or requests or receipts):
            raise HTTPException(409,'Тип счёта (банк или касса) нельзя менять после создания операций, заявок или поступлений: от него зависит, кто проводит оплату.')
        dates=[t.date for t in entries]+[r.due_date for r in requests]+[r.due_date for r in receipts]
        if dates and data.opening_date>min(dates):raise HTTPException(409,'Дата начала учёта не может быть позже существующих операций, заявок или поступлений.')
        def snapshot():return {'name':a.name,'kind':a.kind,'currency':a.currency,'company_id':a.company_id,'opening':money(a.opening),'opening_date':str(a.opening_date),'allow_overdraft':a.allow_overdraft}
        before=snapshot()
        if data.company_id is not None:get(s,Company,data.company_id);a.company_id=data.company_id
        a.name=data.name;a.kind=data.kind;a.currency=data.currency;a.opening=amount(data.opening,True)
        a.opening_date=data.opening_date;a.allow_overdraft=data.allow_overdraft
        s.flush();validate_running_balance(s,a)
        if (a.kind,a.company_id)!=(before['kind'],before['company_id']):
            for r in requests:
                if r.status in ('pending','approved'):log(s,u,'Изменён счёт заявки','request',r.id,f'{before["kind"]} → {a.kind}')
        log(s,u,'Изменён счёт / начальный остаток','account',id,json.dumps({'before':before,'after':snapshot(),'reason':data.reason},ensure_ascii=False))
        return {'id':id,'balance':money(account_balance(s,a))}

@app.post('/api/categories')
def add_category(data:CategoryIn,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'catalog');c=Category(**data.model_dump());s.add(c);s.flush();log(s,u,'Создана статья','category',c.id,c.name);return {'id':c.id}

@app.put('/api/categories/{id}')
def edit_category(id:int,data:CategoryIn,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'catalog');c=get(s,Category,id)
        old={'name':c.name,'activity':c.activity,'type':c.type}
        c.name=data.name;c.activity=data.activity;c.type=data.type
        log(s,u,'Изменена статья','category',id,json.dumps({'before':old,'after':data.model_dump()},ensure_ascii=False))
        return {'id':id}

@app.get('/api/budgets')
def budgets(request:Request,month:str='',currency:Literal['UZS','USD','EUR']='UZS'):
    month=month or str(today())[:7]
    with unit() as s:
        session_user(s,request,'view')
        return [dict(category_id=c.id,category=c.name,cost_group=c.cost_group,month=month,currency=currency,**budget_json(budget_state(s,c.id,month,currency))) for c in s.scalars(select(Category).where(Category.type=='outcome').order_by(Category.id))]
@app.post('/api/budgets')
def save_budget(data:BudgetIn,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'budget');c=get(s,Category,data.category_id)
        if data.cost_group is not None and data.cost_group!=c.cost_group:
            log(s,u,'Изменена группа затрат','category',c.id,json.dumps({'before':c.cost_group,'after':data.cost_group,'reason':data.reason},ensure_ascii=False))
            c.cost_group=data.cost_group
        b=s.scalar(select(Budget).where(Budget.category_id==data.category_id,Budget.month==data.month,Budget.currency==data.currency))
        if data.amount is None:return {'id':b.id if b else None}
        old={'amount':money(b.amount),'mode':b.mode} if b else None
        if not b:b=Budget(category_id=data.category_id,month=data.month,currency=data.currency);s.add(b)
        b.amount=amount(data.amount,True);b.mode=data.mode;b.source=data.source;s.flush()
        log(s,u,'Изменён бюджет','budget',b.id,json.dumps({'before':old,'after':{'amount':money(b.amount),'mode':b.mode},'reason':data.reason},ensure_ascii=False))
        return {'id':b.id}
@app.post('/api/reserve')
def save_reserve(data:ReserveIn,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'budget');n=amount(data.amount,True);key=setting_key(s,'reserve_'+data.currency)
        row=s.get(Setting,key)
        if not row:row=Setting(key=key);s.add(row)
        row.value=str(n);log(s,u,'Изменён минимальный резерв','setting',key,money(n));return {'ok':True}

@app.get('/api/requests')
def requests_list(request:Request,date_from:Optional[date]=None,date_to:Optional[date]=None,currency:Optional[Literal['UZS','USD','EUR']]=None,q:str='',state:str='',page:int=Query(1,ge=1),page_size:int=Query(10,ge=1,le=100),paginated:bool=False):
    with unit() as s:
        u,_=session_user(s,request)
        perms=perms_of(u);channels=payment_channels(perms);checker='request_check' in perms
        if not ({'request','view'} & perms or channels or checker):raise HTTPException(403,'Недостаточно прав для просмотра заявок.')
        if date_from and date_to and date_from>date_to:raise HTTPException(422,'Начало периода позже окончания.')
        query=select(PaymentRequest).join(Account,Account.id==PaymentRequest.account_id)
        if 'view' not in perms:
            # Граница та же, что у карточки заявки: свои заявки, заявки к оплате своего канала
            # и очередь проверки расчётного бухгалтера — по правам, включая замещение.
            visible=[]
            if 'request' in perms:visible.append(PaymentRequest.creator_id==u.id)
            if channels:visible.append(and_(PaymentRequest.status.in_(['approved','paid']),Account.kind.in_(sorted(channels))))
            if checker:visible.append(or_(PaymentRequest.status=='pending',PaymentRequest.checked_by==u.id))
            query=query.where(or_(*visible))
        if currency:query=query.where(Account.currency==currency)
        if date_from:query=query.where(PaymentRequest.due_date>=date_from)
        if date_to:query=query.where(PaymentRequest.due_date<=date_to)
        if state:query=query.where(PaymentRequest.status==state)
        if q:query=query.where(or_(PaymentRequest.counterparty.ilike('%'+q+'%'),PaymentRequest.purpose.ilike('%'+q+'%'),Account.name.ilike('%'+q+'%')))
        total=s.scalar(select(func.count()).select_from(query.subquery()))
        query=query.order_by(PaymentRequest.due_date.desc(),PaymentRequest.id.desc())
        items=[request_view(s,u,r) for r in s.scalars(query.offset((page-1)*page_size).limit(page_size) if paginated else query.limit(500))]
        return {'items':items,'total':total,'page':page,'page_size':page_size} if paginated else items
@app.get('/api/budget-check')
def budget_check(request:Request,account_id:int,category_id:int,value:str=Query(alias='amount'),day:date=Query(alias='date')):
    """Бюджет выбранной статьи в форме заявки: лимит, оплачено, резерв, доступно и остаток после заявки.
    Остатки счетов и бюджеты других статей не раскрываются."""
    with unit() as s:
        session_user(s,request,'request');a=get(s,Account,account_id);c=get(s,Category,category_id)
        if c.type!='outcome':raise HTTPException(422,'Бюджет показывается для расходной статьи.')
        return budget_card(s,c.id,str(day)[:7],a.currency,amount(value))

def request_target(s,data):
    """Счёт определяет компанию, канал оплаты и валюту заявки: значения формы должны с ним совпадать."""
    if data.company_id!=s.info['company_id']:raise HTTPException(409,'Компания заявки не совпадает с выбранной. Обновите раздел.')
    a=get(s,Account,data.account_id);c=get(s,Category,data.category_id)
    if a.archived:raise HTTPException(409,'Счёт в архиве.')
    if a.kind!=data.channel:raise HTTPException(422,'Канал оплаты не совпадает со счётом: банковская заявка оплачивается с банковского счёта, кассовая — из кассы.')
    if a.currency!=data.currency:raise HTTPException(422,'Валюта заявки не совпадает с валютой счёта списания.')
    if c.type!='outcome':raise HTTPException(422,'Для заявки на оплату выберите расходную статью.')
    if data.date<a.opening_date:raise HTTPException(422,'Дата раньше начала учёта выбранного счёта.')
    enforce_lead_time(s,data.priority,data.date,today())
    return a,c

def request_state(r):
    return {'status':r.status,'stage':approval_stage(r),'date':str(r.due_date),'version':r.version,'checked_by':r.checked_by,
            'finance_approved_by':r.finance_approved_by,'approved_by':r.approved_by}

@app.post('/api/requests')
def add_request(data:RequestIn,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'request');a,c=request_target(s,data);n=amount(data.amount)
        if data.status=='pending':raise HTTPException(422,'Сначала сохраните черновик и приложите «Внутренняя заявка / Индент» и «Договор / Счёт на оплату», затем отправьте заявку.')
        r=PaymentRequest(creator_id=u.id,last_editor_id=u.id,category_id=c.id,account_id=a.id,counterparty=data.counterparty,amount=n,due_date=data.date,purpose=data.purpose,project=data.project,status='draft',priority=data.priority)
        remember_counterparty(s,data.counterparty)
        s.add(r);s.flush()
        log(s,u,'Создана заявка','request',r.id,json.dumps({'status':r.status,'version':r.version,'amount':money(n),'currency':a.currency,
            'category':c.name,'channel':a.kind,'date':str(r.due_date),'priority':r.priority},ensure_ascii=False))
        return request_view(s,u,r)
@app.put('/api/requests/{id}')
def edit_request(id:int,data:RequestEdit,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'request');r=get(s,PaymentRequest,id)
        if not can_view_request(s,u,r):raise HTTPException(404,'Запись не найдена.')
        if r.creator_id!=u.id and 'request_edit' not in perms_of(u):raise HTTPException(403,'Редактировать может автор или финансовый руководитель.')
        check_request_version(r,data.version)
        if r.status=='rejected':raise HTTPException(409,'Отклонённая заявка хранится только для чтения. Создайте новую заявку.')
        if r.status not in ('draft','pending','returned'):raise HTTPException(409,'Редактирование доступно до утверждения. Утверждённую заявку сначала верните на доработку.')
        if r.creator_id!=u.id:act_with_right(u,'request_edit')
        a,c=request_target(s,data);n=amount(data.amount)
        before=request_json(s,r)
        was_pending=r.status=='pending';route_changed=r.category_id!=c.id or get(s,Account,r.account_id).currency!=a.currency
        for key in ('account_id','category_id','counterparty','purpose','project','priority'):setattr(r,key,getattr(data,key))
        r.amount=n;r.due_date=data.date;clear_approval(r);r.last_editor_id=u.id;r.decision_note=data.reason;r.status=data.status
        s.flush()
        if data.status=='pending':
            enforce_documents(s,r);enforce_budget(s,r.category_id,r.due_date,a.currency,n,data.purpose,r.id)
            if not was_pending or route_changed:snapshot_route(s,r,a.currency)
            elif r.route_policy is not None:
                # Правка отправленной заявки не ослабляет маршрут: из сохранённого снимка и текущей
                # политики статьи действует более строгий. Заявка без снимка (до 2.13) остаётся с директором.
                kept=(r.route_policy,r.route_threshold,r.route_at);stored=requires_director(r)
                snapshot_route(s,r,a.currency)
                if stored or not requires_director(r):r.route_policy,r.route_threshold,r.route_at=kept
        remember_counterparty(s,data.counterparty);s.flush()
        log(s,u,'Изменена заявка','request',r.id,json.dumps({'before':before,'after':request_json(s,r),'reason':data.reason,'version':r.version},ensure_ascii=False))
        return request_view(s,u,r)

def ensure_new_participant(u,r):
    """Автор и редактор не согласуют свою заявку; один человек не проходит два этапа, в том числе по ВрИО."""
    if u.id in (r.creator_id,r.last_editor_id):raise HTTPException(403,'Автор и последний редактор не могут согласовать свою заявку, включая администратора. Нужен другой согласующий.')
    if u.id in participants(r):raise HTTPException(403,'Один сотрудник не проходит два этапа одной заявки: нужен другой согласующий.')

@app.post('/api/requests/{id}/decision')
def decide(id:int,data:DecisionIn,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request);r=get(s,PaymentRequest,id);a=get(s,Account,r.account_id)
        if data.action=='reject':raise HTTPException(422,'Отклонение больше не используется. Верните заявку на доработку с комментарием.')
        if data.action in ('cancel','stop'):raise HTTPException(422,'Такого действия нет: заявку возвращают на доработку или закрывают без оплаты с комментарием.')
        perms=perms_of(u)
        if data.action=='return_finance':
            # Плательщик канала, которому не хватило денег или бюджета при оплате.
            if not act_with_right(u,pay_right(a)):raise HTTPException(403,'Вернуть финансовому директору может плательщик этой заявки.')
        elif not ({'request','approve','request_check'} & perms):raise HTTPException(403,'Недостаточно прав.')
        if not can_view_request(s,u,r):raise HTTPException(404,'Запись не найдена.')
        # Без прав согласующего, проверяющего или финансового руководителя — только свои заявки.
        own_only=not ({'approve','request_edit','request_check'} & perms)
        if own_only and r.creator_id!=u.id and data.action!='return_finance':raise HTTPException(403,'Доступны только собственные заявки.')
        check_request_version(r,data.version)
        stage=approval_stage(r);before=request_state(r);note=data.note.strip()
        if data.action=='submit':
            if r.status not in ('draft','returned') or (r.creator_id!=u.id and 'request_edit' not in perms):raise HTTPException(403,'Отправить можно свой черновик или возвращённую заявку.')
            act_with_right(u,'request' if r.creator_id==u.id else 'request_edit')
            enforce_lead_time(s,r.priority,r.due_date,today());enforce_documents(s,r)
            enforce_budget(s,r.category_id,r.due_date,a.currency,r.amount,r.purpose,r.id)
            clear_approval(r);r.status='pending';snapshot_route(s,r,a.currency)
        elif data.action=='check':
            if 'request_check' not in perms:raise HTTPException(403,'Реквизиты и комплектность проверяет расчётный бухгалтер компании.')
            ensure_new_participant(u,r)
            if stage!='check':raise HTTPException(409,'Заявка не ожидает проверки расчётного бухгалтера.')
            if not act_as(u,'accountant'):raise HTTPException(403,'Реквизиты и комплектность проверяет расчётный бухгалтер компании.')
            enforce_documents(s,r)
            b=budget_state(s,r.category_id,str(r.due_date)[:7],a.currency,r.amount,r.id)
            if b['over'] and b['mode']=='hard':raise HTTPException(409,'Жёсткий лимит статьи превышен. Верните заявку на доработку или дождитесь пересмотра бюджета.')
            r.checked_by=u.id;r.checked_at=now()
        elif data.action=='approve':
            if 'approve' not in perms:raise HTTPException(403,'Недостаточно прав для согласования.')
            if u.id in (r.creator_id,r.last_editor_id):ensure_new_participant(u,r)
            if r.status!='pending':raise HTTPException(409,'Заявка уже обработана или не отправлена на согласование.')
            if stage=='check':raise HTTPException(409,'Сначала реквизиты и комплектность проверяет расчётный бухгалтер.')
            if not act_as(u,'finance' if stage=='finance' else 'director'):
                raise HTTPException(403,'Сначала требуется проверка финансового директора компании.' if stage=='finance' else 'Требуется отдельное подтверждение директора компании.')
            ensure_new_participant(u,r)
            b=enforce_budget(s,r.category_id,r.due_date,a.currency,r.amount,note,r.id)
            if stage=='finance':r.finance_approved_by=u.id
            else:r.approved_by=u.id
            # Директор участвует по снимку политики статьи; последний нужный этап завершает согласование.
            if stage=='director' or not requires_director(r):
                enforce_available_funds(s,a,r.due_date,r.amount,r.id)
                r.status='approved';r.approved_at=now();r.approved_overrun=overrun(b)
        elif data.action=='return':
            if not (('approve' in perms and act_with_right(u,'approve')) or act_as(u,'accountant')):raise HTTPException(403,'Недостаточно прав для возврата.')
            if r.status not in ('pending','approved'):raise HTTPException(409,'Вернуть можно заявку на согласовании или утверждённую заявку.')
            if 'approve' not in perms and stage!='check':raise HTTPException(403,'Расчётный бухгалтер возвращает заявку на этапе проверки реквизитов.')
            if len(note)<10:raise HTTPException(422,'Укажите причину возврата не короче 10 символов.')
            r.status='returned';clear_approval(r)
        elif data.action=='return_finance':
            if r.status!='approved':raise HTTPException(409,'Вернуть финансовому директору можно только утверждённую неоплаченную заявку.')
            if len(note)<10:raise HTTPException(422,'Укажите причину возврата не короче 10 символов: чего не хватает для оплаты.')
            r.status='pending';clear_approval(r,keep_check=True)
        elif data.action=='close':
            if not ((r.creator_id==u.id and 'request' in perms) or ('approve' in perms and not own_only)):raise HTTPException(403,'Закрыть без оплаты может автор заявки, финансовый директор или директор.')
            act_with_right(u,'request' if r.creator_id==u.id and 'request' in perms else 'approve')
            if r.status=='rejected':raise HTTPException(409,'Отклонённая заявка хранится только для чтения.')
            if r.status not in ('draft','pending','approved','returned'):raise HTTPException(409,'Оплаченную или уже закрытую заявку закрыть нельзя.')
            if len(note)<10:raise HTTPException(422,'Укажите причину закрытия не короче 10 символов.')
            r.status='cancelled';clear_approval(r)
        elif data.action=='reschedule':
            if 'request_edit' not in perms or r.status not in ('pending','approved'):raise HTTPException(403,'Перенос недоступен.')
            act_with_right(u,'request_edit')
            if not data.date or len(note)<10:raise HTTPException(422,'Нужны новая дата и причина не короче 10 символов.')
            if data.date<a.opening_date:raise HTTPException(422,'Дата раньше начала учёта счёта.')
            enforce_lead_time(s,r.priority,data.date,today())
            r.due_date=data.date;r.status='pending';clear_approval(r,keep_check=True);r.last_editor_id=u.id
        r.decision_note=data.note
        s.flush()
        detail={'before':before,'after':request_state(r),'reason':data.note}
        if data.action=='submit':detail['route']=route_json(r,a.currency)
        log(s,u,'Действие по заявке: '+data.action,'request',r.id,json.dumps(detail,ensure_ascii=False))
        return request_view(s,u,r)

@app.get('/api/receipts')
def get_receipts(request:Request):
    with unit() as s:
        session_user(s,request,'view')
        result=[]
        for r in s.scalars(select(Receipt).order_by(Receipt.id.desc()).limit(500)):
            a=get(s,Account,r.account_id)
            result.append({'id':r.id,'account_id':a.id,'account':a.name,'currency':a.currency,'category_id':r.category_id,
                'counterparty':r.counterparty,'amount':money(r.amount),'date':str(r.due_date),'note':r.note,'status':r.status})
        return result
@app.post('/api/receipts')
def add_receipt(data:ReceiptIn,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'schedule');a=get(s,Account,data.account_id);get(s,Category,data.category_id)
        if a.archived:raise HTTPException(409,'Счёт в архиве.')
        if data.date<a.opening_date:raise HTTPException(422,'Дата раньше начала учёта счёта.')
        r=Receipt(account_id=a.id,category_id=data.category_id,counterparty=data.counterparty,amount=amount(data.amount),due_date=data.date,note=data.note,creator_id=u.id)
        remember_counterparty(s,data.counterparty)
        s.add(r);s.flush();log(s,u,'План поступления','receipt',r.id,data.counterparty);return {'id':r.id}

@app.get('/api/ledger')
def ledger_list(request:Request,date_from:Optional[date]=None,date_to:Optional[date]=None,currency:Optional[Literal['UZS','USD','EUR']]=None,q:str='',page:int=Query(1,ge=1),page_size:int=Query(10,ge=1,le=100),paginated:bool=False):
    with unit() as s:
        u,_=session_user(s,request,'ledger');acc={a.id:a for a in s.scalars(select(Account))};cats={c.id:c.name for c in s.scalars(select(Category))}
        reversed_ids={t.reversal_of for t in s.scalars(select(Ledger).where(Ledger.reversal_of!=None))}
        if date_from and date_to and date_from>date_to:raise HTTPException(422,'Начало периода позже окончания.')
        query=select(Ledger).join(Account,Account.id==Ledger.account_id).outerjoin(Category,Category.id==Ledger.category_id)
        # Плательщик без просмотра реестра видит операции только своего канала: кассир — кассу, бухгалтер — банк.
        channels=payment_channels(perms_of(u))
        if 'view' not in perms_of(u) and channels:query=query.where(Account.kind.in_(sorted(channels)))
        if currency:query=query.where(Account.currency==currency)
        if date_from:query=query.where(Ledger.date>=date_from)
        if date_to:query=query.where(Ledger.date<=date_to)
        if q:query=query.where(or_(Ledger.counterparty.ilike('%'+q+'%'),Ledger.reference.ilike('%'+q+'%'),Ledger.note.ilike('%'+q+'%'),Account.name.ilike('%'+q+'%'),Category.name.ilike('%'+q+'%')))
        total=s.scalar(select(func.count()).select_from(query.subquery()))
        query=query.order_by(Ledger.date.desc(),Ledger.id.desc())
        items=[{'id':t.id,'date':str(t.date),'kind':t.kind,'account_id':t.account_id,'account':acc[t.account_id].name,
                 'to_account':acc[t.to_account_id].name if t.to_account_id else '', 'currency':acc[t.account_id].currency,
                 'category':cats.get(t.category_id,'Внутренний перевод'),'amount':money(t.amount),'counterparty':t.counterparty,
                 'reference':t.reference,'note':t.note,'request_id':t.request_id,'receipt_id':t.receipt_id,
                 'can_attach_document':can_attach_document(u,t),
                 'reversal_of':t.reversal_of,'reversed':t.id in reversed_ids} for t in s.scalars(query.offset((page-1)*page_size).limit(page_size) if paginated else query.limit(500))]
        return {'items':items,'total':total,'page':page,'page_size':page_size} if paginated else items
@app.post('/api/ledger')
def add_ledger(data:LedgerIn,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request)
        if not ('write' in perms_of(u) or payment_channels(perms_of(u))):raise HTTPException(403,'У вашей роли нет прав на это действие.')
        # Оплату заявки отмечает проверка плательщика; прочие операции идут по праву записи (в том числе ВрИО).
        if not data.request_id and 'write' in perms_of(u):act_with_right(u,'write')
        t=post_ledger(s,u,data);remember_counterparty(s,data.counterparty);return {'id':t.id}
@app.post('/api/ledger/{id}/reverse')
def reverse(id:int,data:ReverseIn,request:Request):
    with unit(True) as s:
        u,_=session_user(s,request,'write')
        own=company_role(u)
        if not (act_as(u,own) if own in ('finance','director') else (act_as(u,'finance') or act_as(u,'director'))):
            raise HTTPException(403,'Сторно выполняет финансовый директор или директор компании.')
        t=get(s,Ledger,id)
        if any(get(s,Account,x).archived for x in (t.account_id,t.to_account_id) if x):raise HTTPException(409,'Счёт в архиве. Сначала восстановите его.')
        if t.reversal_of or s.scalar(select(Ledger.id).where(Ledger.reversal_of==id)):raise HTTPException(409,'Сторнирование уже выполнено или это запись сторно.')
        inv=Ledger(account_id=t.to_account_id if t.kind=='transfer' else t.account_id,to_account_id=t.account_id if t.kind=='transfer' else None,
                   kind={'in':'out','out':'in','transfer':'transfer'}[t.kind],amount=t.amount,date=t.date,
                   category_id=t.category_id,counterparty=t.counterparty,reference='REV-'+str(t.id),note=data.reason,reversal_of=t.id,creator_id=u.id)
        s.add(inv)
        # Освобождаем уникальную связь оплаты, но сохраняем историю в журнале.
        if t.request_id:
            get(s,PaymentRequest,t.request_id).status='approved';log(s,u,'Отмена оплаты заявки','request',t.request_id,f'Сторно операции {id}');t.request_id=None
        if t.receipt_id:
            get(s,Receipt,t.receipt_id).status='expected';log(s,u,'Отмена поступления','receipt',t.receipt_id,f'Сторно операции {id}');t.receipt_id=None
        s.flush();validate_running_balance(s,get(s,Account,t.account_id))
        if t.to_account_id:validate_running_balance(s,get(s,Account,t.to_account_id))
        log(s,u,'Сторно операции (исходная дата)','ledger',id,data.reason)
        return {'id':inv.id}

@app.get('/api/report')
def operational_report(request:Request,year:int=2026,currency:Literal['UZS','USD','EUR']='UZS',company_id:int|None=None,scenario:Literal['A','B','V']='A',as_of:date|None=None):
    if not 2000<=year<=2100:raise HTTPException(422,'Некорректный год.')
    if as_of and (as_of.year!=year or as_of>today()):raise HTTPException(422,'Дата отчёта должна быть в выбранном году и не позже сегодня.')
    with unit() as s:
        session_user(s,request,'view')
        company_id=company_id or s.info['company_id']
        get(s,Company,company_id)
        from .cash_report import report
        return report(s,year,currency,company_id,scenario,as_of)

@app.get('/api/group-report')
def group_report(request:Request,company_id:int,currency:Literal['UZS','USD','EUR']='UZS',scenario:Literal['A','B','V']='A',month:str=Query(pattern=r'^20\d{2}-(0[1-9]|1[0-2])$'),day:date|None=None):
    """Read-only Telegram-ready preview. Sending is deliberately outside the product."""
    with unit() as s:
        session_user(s,request,'view');company=get(s,Company,company_id)
        y,m=map(int,month.split('-'));start=date(y,m,1);end=date(y+1,1,1) if m==12 else date(y,m+1,1);day=day or today()
        if not start<=day<end or day>today():raise HTTPException(422,'День должен входить в выбранный месяц и быть не позже сегодня.')
        accounts=list(s.scalars(select(Account).where(Account.company_id==company_id,Account.currency==currency)))
        aids={a.id for a in accounts}
        entries=[t for t in s.scalars(effective_cashflows(s).where(Ledger.date>=start,Ledger.date<=day)) if t.account_id in aids and t.kind!='transfer']
        plan=s.scalar(select(CashPlan).where(CashPlan.company_id==company_id,CashPlan.month==month,CashPlan.currency==currency,CashPlan.scenario==scenario))
        payload=json.loads(plan.payload) if plan else {}
        bank_ids={a.id for a in accounts if a.kind=='bank'}
        income_mtd=sum(t.amount for t in entries if t.kind=='in' and t.account_id in bank_ids)
        last_income=max((t.date for t in entries if t.kind=='in' and t.account_id in bank_ids),default=None)
        expense_plan=abs(sum(v for k,v in payload.items() if k.endswith(':out')))
        # Счёт, открытый позже дня отчёта, в этот день ещё не существует и даёт ноль, а не ошибку.
        opened=[a for a in accounts if a.opening_date<=day]
        opening=sum(day_start_balance(s,a,day) for a in opened)
        income_day=sum(t.amount for t in entries if t.kind=='in' and t.date==day)
        expense_day=sum(t.amount for t in entries if t.kind=='out' and t.date==day)
        closing=opening+income_day-expense_day
        reserved=sum(funds_state(s,a,day)['reserved'] for a in opened)
        available=closing-reserved
        if expense_plan:
            remaining=max(0,expense_plan-income_mtd);coverage=income_mtd*100/expense_plan
            plan_line=f'План расходов: {money(expense_plan)} {currency}; банковские поступления MTD: {money(income_mtd)}; до покрытия плана: {money(remaining)}; покрытие {coverage:.1f}%'
        else:plan_line=f'План расходов: данных нет; банковские поступления MTD: {money(income_mtd)} {currency}'
        last_line='нет' if last_income is None else last_income.strftime('%d.%m.%Y')
        day_label=day.strftime('%d.%m.%Y')
        text='\n'.join([f'{company.name} · {month} · сценарий {scenario}',plan_line,f'Последнее банковское поступление: {last_line}',
            f'{day_label}: начало дня {money(opening)}; приход {money(income_day)}; расход {money(expense_day)}; конец дня {money(closing)}; резерв {money(reserved)}; доступно {money(available)} {currency}'])
        return {'text':text,'company':company.name,'month':month,'day':str(day),'currency':currency,'scenario':scenario,
            'expense_plan':money(expense_plan),'bank_income_mtd':money(income_mtd),'last_bank_income':str(last_income) if last_income else None,
            'opening':money(opening),'income_day':money(income_day),'expense_day':money(expense_day),'closing':money(closing),'reserved':money(reserved),'available':money(available)}

@app.get('/api/export/ledger.csv')
def export_ledger(request:Request):
    with unit() as s:
        session_user(s,request,'export');out=io.StringIO();w=csv.writer(out,delimiter=';');w.writerow(['ID','Дата','Тип','Счёт','Валюта','Сумма','Контрагент','Документ','Комментарий'])
        def safe(x):
            v=str(x or '');return "'"+v if v[:1] in ('=','+','-','@','\t','\r') else v
        for t in s.scalars(select(Ledger).order_by(Ledger.date,Ledger.id)):
            a=s.get(Account,t.account_id)
            w.writerow([t.id,t.date,t.kind,safe(a.name),a.currency,money(t.amount),safe(t.counterparty),safe(t.reference),safe(t.note)])
        return Response(content=('\ufeff'+out.getvalue()).encode('utf-8'),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="cashflow_ledger.csv"'})

@app.get('/api/model')
def model(request:Request,version:int=0):
    with unit() as s:
        session_user(s,request,'view')
        versions=list(s.scalars(select(ModelVersion).order_by(ModelVersion.id.desc())))
        rec=s.get(ModelVersion,version) if version else (versions[0] if versions else None)
        return {'versions':[{'id':r.id,'name':r.filename,'imported_at':str(r.imported_at),'source':r.source,'sha256':r.sha256} for r in versions],
                'selected':rec.id if rec else None,'snapshot':json.loads(rec.snapshot) if rec else None,
                'drive_configured':bool(os.getenv('GOOGLE_APPLICATION_CREDENTIALS') and os.getenv('GOOGLE_DRIVE_FILE_ID'))}
@app.post('/api/model/upload')
async def model_upload(request:Request):
    with unit() as s:u,_=session_user(s,request,'import');uid=u.id
    filename=request.headers.get('X-Filename','FinModel.xlsx')
    from urllib.parse import unquote
    filename=Path(unquote(filename)).name
    if not filename.lower().endswith('.xlsx'):raise HTTPException(422,'Нужен файл .xlsx.')
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>MAX_SIZE:raise HTTPException(413,'Файл больше 20 МБ.')
    try:
        with unit(True) as s:
            session_user(s,request,'import')
            u,_=session_user(s,request,'import')
            rec,created=import_snapshot(s,bytes(raw),filename,user_id=uid,user=u)
            return {'id':rec.id,'created':created}
    except ValueError as e:raise HTTPException(422,str(e))
@app.post('/api/model/drive-sync')
def model_drive_sync(request:Request):
    with unit() as s:u,_=session_user(s,request,'import');uid=u.id
    try:
        from .drive import download_model
        raw,name,source=download_model()
        with unit(True) as s:
            u,_=session_user(s,request,'import')
            rec,created=import_snapshot(s,raw,name,source,uid,user=u);return {'id':rec.id,'created':created}
    except (ValueError,RuntimeError) as e:raise HTTPException(422,str(e))

@app.get('/api/users')
def users(request:Request):
    with unit() as s:
        session_user(s,request,'users')
        cid=s.info['company_id']
        members={m.user_id:m.role for m in s.scalars(select(CompanyUser).where(CompanyUser.company_id==cid))}
        rows=[]
        for u in s.scalars(select(User).where(User.active==True).order_by(User.id)):
            row=member_json(u,members.get(u.id))
            if u.id not in members and u.role not in HOLDING_ROLES:
                # В компании только по замещению: своей роли здесь нет.
                acting=[ROLES[d.role] for d in active_delegations(s,u.id,cid)]
                row.update(company_role=None,role_label='ВрИО · '+', '.join(acting) if acting else 'Без роли в компании',substitute=True)
            rows.append(row)
        return rows

class CompanyUserIn(Input):
    username:str=Field(min_length=3,max_length=80)
    # Роль в выбранной компании; для администратора холдинга это отдельное финансовое назначение.
    role:Optional[Literal['director','finance','accountant','employee','auditor','cashier','operator','investor','material_accountant','procurement']]=None

@app.post('/api/company-users')
def grant_company_access(data:CompanyUserIn,request:Request):
    with unit(True) as s:
        admin,_=session_user(s,request,'users')
        u=s.scalar(select(User).where(User.username==data.username.strip().lower()).execution_options(company_unscoped=True))
        if not u:raise HTTPException(404,'Логин не найден. Создайте нового пользователя.')
        if u.role=='founder':raise HTTPException(409,'Учредитель видит все компании только для чтения; назначение не требуется.')
        if u.id==admin.id and data.role:raise HTTPException(409,'Роль в компании себе не назначают: её выдаёт другой администратор.')
        if u.role=='admin' and not data.role:raise HTTPException(422,'Администратору холдинга в компании назначается роль, например «Финансовый директор».')
        if not data.role:raise HTTPException(422,'Выберите роль сотрудника в этой компании.')
        # Сотрудник относится к одной компании: назначение переносит доступ и отзывает прежний.
        access.assign(s,admin,u,s.info['company_id'],data.role,'Назначение по логину в выбранной компании',request)
        return {'ok':True}

@app.delete('/api/company-users/{id}')
def revoke_company_access(id:int,request:Request):
    with unit(True) as s:
        admin,_=session_user(s,request,'users');u=access.unscoped_user(s,id)
        # Администратор холдинга видит компанию и без назначения: снимается только его финансовая роль.
        access.unassign(s,admin,u,s.info['company_id'],'Отзыв доступа в выбранной компании',request)
        return {'ok':True}

@app.post('/api/users')
def add_user(data:UserIn,request:Request):
    with unit(True) as s:
        admin,_=session_user(s,request,'users')
        try:pw=hash_password(data.password)
        except ValueError as e:raise HTTPException(422,str(e))
        if s.scalar(select(User.id).where(User.username==data.username.lower()).execution_options(company_unscoped=True)):raise HTTPException(409,'Такой логин уже занят.')
        # Пароль, заданный администратором, временный: при первом входе его нужно сменить.
        u=User(username=data.username.lower(),name=data.name,password_hash=pw,role=data.role,role_id=s.scalar(select(Role.id).where(Role.name==data.role)),must_change_password=True);s.add(u);s.flush()
        # Пользователь холдинга видит компании по своей роли; сотруднику нужна роль в выбранной компании.
        if data.role not in HOLDING_ROLES:access.business_company(s,s.info['company_id']);s.add(CompanyUser(company_id=s.info['company_id'],user_id=u.id,role=data.role));s.flush()
        access.record(s,admin,'Создан пользователь',u,None,'Создание в выбранной компании',None,request)
        return member_json(u,None if data.role in HOLDING_ROLES else data.role)
@app.post('/api/users/{id}')
def edit_user(id:int,data:UserEdit,request:Request):
    with unit(True) as s:
        admin,_=session_user(s,request,'users');u=access.unscoped_user(s,id)
        if u.id==admin.id and (not data.active or data.role!='admin'):raise HTTPException(409,'Нельзя отключить себя или убрать собственные права администратора.')
        cid=s.info['company_id']
        # Прежняя форма «Управлять» работает по тем же правилам, что и центр администрирования.
        if not u.active:raise HTTPException(409,'Учётная запись в архиве: восстановите её в центре администрирования с новым назначением.')
        if not data.active:
            access.archive(s,admin,u,'Отключение через форму «Управлять»',request)
            return member_json(u,None)
        if data.role!=u.role and (data.role in HOLDING_ROLES or u.role in HOLDING_ROLES):
            # Смена статуса холдинга начинается с чистого листа: прежние роли в компаниях снимаются.
            access.set_holding(s,admin,u,data.role if data.role in HOLDING_ROLES else None,'Смена статуса через форму «Управлять»',cid,data.role,request)
        elif data.role not in HOLDING_ROLES:
            current=[m for m in access.valid_assignments(s,u)]
            if not (len(current)==1 and current[0].company_id==cid and current[0].role==data.role):
                # Сотрудник работает в одной компании: новое назначение отзывает прежние.
                access.assign(s,admin,u,cid,data.role,'Назначение через форму «Управлять»',request)
        if data.password:
            try:u.password_hash=hash_password(data.password)
            except ValueError as e:raise HTTPException(422,str(e))
            u.must_change_password=True
            access.revoke_links_and_sessions(s,u,admin)
            log(s,admin,'Сброшен пароль; сессии отозваны','user',id,'временный пароль задан администратором')
        m=s.get(CompanyUser,(cid,u.id))
        return member_json(u,m.role if m else None)

@app.post('/api/users/{id}/password')
def reset_user_password(id:int,data:ResetPasswordIn,request:Request):
    with unit(True) as s:
        admin,_=session_user(s,request,'users');u=access.unscoped_user(s,id)
        if not u.active:raise HTTPException(409,'Учётная запись в архиве: восстановите её с новым назначением.')
        try:u.password_hash=hash_password(data.password)
        except ValueError as e:raise HTTPException(422,str(e))
        u.must_change_password=True
        access.revoke_links_and_sessions(s,u,admin)
        log(s,admin,'Сброшен пароль; сессии отозваны','user',id)
        return {'ok':True}
@app.get('/api/audit')
def audit(request:Request):
    with unit() as s:
        u,_=session_user(s,request,'audit')
        # Имена всех сотрудников, включая архивных и переведённых в другую компанию.
        names={u.id:u.name for u in s.scalars(select(User).execution_options(company_unscoped=True))}
        return [{'id':a.id,'date':str(a.created_at)+' UTC','user':names.get(a.user_id,'Система'),'action':a.action,
                'entity':a.entity,'entity_id':a.entity_id,'detail':a.detail,'ip':a.ip,'forwarded_for':a.forwarded_for,
                'role':ROLES.get(a.role,a.role or ''),'acting_for':names.get(a.acting_for_id) if a.acting_for_id else None}
                for a in s.scalars(select(Audit).order_by(Audit.id.desc()).limit(200))]

from .erp import router as erp_router
app.include_router(erp_router)

from .review_api import router as review_router
app.include_router(review_router)

from .plan_import import router as plan_import_router
app.include_router(plan_import_router)

from .admin_api import router as admin_router
app.include_router(admin_router)

from .bot_api import router as bot_router
app.include_router(bot_router)

from .requests_api import router as requests_router
app.include_router(requests_router)
