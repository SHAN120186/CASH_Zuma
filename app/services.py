from __future__ import annotations
import json
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import select, func
from fastapi import HTTPException
from .company_scope import get_setting
from .security import PERMS, pay_right, perms_of, company_role, has_role, act_with_right
from .db import Account, Category, Budget, PaymentRequest, Receipt, Ledger, Audit, Setting, RequestDocument, now
from . import clock

def today():return clock.today()
def amount(value, allow_zero=False):
    try:
        n=Decimal(str(value))
        if not n.is_finite() or n<0 or (n==0 and not allow_zero) or n>Decimal('100000000000000') or n!=n.quantize(Decimal('.01')):raise ValueError()
        return int(n*100)
    except (ValueError, InvalidOperation, TypeError):raise HTTPException(422,'Сумма должна быть положительной, не более 100 трлн, с максимум двумя знаками после запятой.')
def money(n):return format(Decimal(n)/100,'.2f')

def effective_cashflows(s):
    """Обороты без исправленных операций и компенсирующих записей сторно.

    Остатки по-прежнему считаются по полному журналу проводок.
    """
    reversed_ids=select(Ledger.reversal_of).where(Ledger.reversal_of.is_not(None))
    return select(Ledger).where(Ledger.reversal_of.is_(None),Ledger.id.not_in(reversed_ids))
def get(s,cls,id):
    value=s.get(cls,id)
    if not value:raise HTTPException(404,'Запись не найдена.')
    from .company_scope import check_object
    check_object(s, value)
    return value

def log(s,user,action,entity,id='',detail=''):
    """Запись журнала: пользователь, роль в компании, заменяемый при ВрИО, IP и серверное время."""
    acted=getattr(user,'_acted',None) if user else None
    role=acted[0] if acted else (company_role(user) or (user.role if user else None)) if user else None
    s.add(Audit(user_id=user.id if user else None,action=action,entity=entity,entity_id=str(id),detail=detail,
                ip=getattr(user,'_ip',None) if user else None,forwarded_for=getattr(user,'_forwarded',None) if user else None,
                role=role,acting_for_id=acted[1] if acted else None))
def account_balance(s,a,upto=None):
    upto=upto or today()
    if upto<a.opening_date:return 0
    balance=a.opening
    for t in s.scalars(select(Ledger).where(Ledger.date<=upto)):
        if t.account_id==a.id:balance += t.amount if t.kind=='in' else -t.amount
        if t.to_account_id==a.id:balance += t.amount
    return balance

def day_start_balance(s,a,day):
    """Остаток на начало дня. Начальный остаток задан на начало дня ``opening_date``,
    поэтому счёт, открытый в этот день, начинает его с начального остатка, а счёт,
    открытый позже, в этот день равен нулю."""
    if day<a.opening_date:return 0
    return a.opening if day==a.opening_date else account_balance(s,a,day-timedelta(days=1))

def funds_state(s, account, as_of, exclude_request=None):
    """Actual account money less fully approved unpaid requests due by ``as_of``.

    The caller runs inside ``unit(write=True)`` for approval/payment paths.  That
    transaction serializes writers (Guard row on PostgreSQL, BEGIN IMMEDIATE on
    SQLite), so two approvals cannot reserve the same money concurrently.
    """
    if as_of < account.opening_date:
        raise HTTPException(422,'Дата раньше начала учёта выбранного счёта.')
    actual=account_balance(s,account,as_of)
    approved=s.scalars(select(PaymentRequest).where(
        PaymentRequest.account_id==account.id,
        PaymentRequest.status=='approved',
        PaymentRequest.due_date<=as_of,
    ))
    reserved=sum(r.amount for r in approved if r.id!=exclude_request)
    return {'actual':actual,'reserved':reserved,'available':actual-reserved,
            'account_id':account.id,'currency':account.currency,'as_of':str(as_of)}

def enforce_available_funds(s, account, as_of, required, exclude_request=None):
    state=funds_state(s,account,as_of,exclude_request)
    if state['available']<required:
        raise HTTPException(409,
            f'Недостаточно доступных средств на счёте «{account.name}» на {as_of}: '
            f'{money(state["available"])} {account.currency}. Требуется {money(required)} {account.currency}. '
            f'Фактический остаток {money(state["actual"])}; резерв утверждённых заявок {money(state["reserved"])}.')
    return state

def validate_running_balance(s,a):
    """Проверка на конец каждого дня, включая ввод задним числом."""
    if a.allow_overdraft and a.kind=='bank':return
    flows={}
    for t in s.scalars(select(Ledger).order_by(Ledger.date,Ledger.id)):
        if t.account_id==a.id:flows[t.date]=flows.get(t.date,0)+(t.amount if t.kind=='in' else -t.amount)
        if t.to_account_id==a.id:flows[t.date]=flows.get(t.date,0)+t.amount
    bal=a.opening
    for d,change in sorted(flows.items()):
        bal+=change
        if bal<0:raise HTTPException(409,f'Недостаточно средств на счёте «{a.name}» на {d}: {money(bal)} {a.currency}.')

def budget_state(s,category,month,currency,extra=0,exclude_request=None):
    b=s.scalar(select(Budget).where(Budget.category_id==category,Budget.month==month,Budget.currency==currency))
    accounts={a.id:a for a in s.scalars(select(Account))}
    spent=0
    for t in s.scalars(effective_cashflows(s).where(Ledger.category_id==category)):
        if str(t.date)[:7]!=month or accounts[t.account_id].currency!=currency:continue
        if t.kind=='out':spent+=t.amount
    reserved=sum(r.amount for r in s.scalars(select(PaymentRequest).where(PaymentRequest.category_id==category,PaymentRequest.status=='approved'))
                 if str(r.due_date)[:7]==month and accounts[r.account_id].currency==currency and r.id!=exclude_request)
    used=spent+reserved
    return {'id':b.id if b else None,'limit':b.amount if b else None,'mode':b.mode if b else 'soft',
            'spent':spent,'reserved':reserved,'used':used,'remaining':b.amount-used if b else None,
            'after':used+extra,'over':bool(b and used+extra>b.amount),'source':b.source if b else ''}

def enforce_budget(s,category,dt,currency,n,note,exclude=None):
    b=budget_state(s,category,str(dt)[:7],currency,n,exclude)
    if b['over']:
        if b['mode']=='hard':raise HTTPException(409,'Жёсткий лимит превышен. Сначала пересмотрите утверждённый бюджет.')
        if len(note.strip())<10:raise HTTPException(409,'Превышен мягкий лимит. Укажите обоснование не короче 10 символов.')
    return b

def budget_json(b):
    return {k:(money(v) if v is not None else None) if k in {'limit','spent','reserved','used','remaining','after'} else v for k,v in b.items()}

def check_request_version(r,version):
    if version is None:raise HTTPException(428,'Обновите список заявок: для действия нужна версия документа.')
    if r.version!=version:raise HTTPException(409,'Заявка уже изменена другим пользователем. Обновите список и проверьте изменения.')

THRESHOLD_FIELDS={'UZS':'director_threshold_uzs','USD':'director_threshold_usd','EUR':'director_threshold_eur'}
REQUIRED_DOCUMENTS=('indent','contract')
DOCUMENT_KINDS={'indent':'Внутренняя заявка / Индент','contract':'Договор / Счёт на оплату','other':'Прочие подтверждающие документы'}

def category_policy(c,currency):
    """Действующая политика статьи для валюты. Нет политики или порога валюты — директор всегда."""
    policy=c.director_policy or 'always'
    if policy=='skip' and not c.skip_allowed:policy='always'
    threshold=getattr(c,THRESHOLD_FIELDS[currency]) if policy=='threshold' else None
    if policy=='threshold' and threshold is None:policy='always'
    return policy,threshold

def snapshot_route(s,r,currency):
    """Снимок политики статьи при отправке: поздние правки справочника маршрут не меняют."""
    c=s.get(Category,r.category_id)
    r.route_policy,r.route_threshold=category_policy(c,currency)
    r.route_at=now()

def requires_director(r):
    """Нужен ли директор по снимку. Заявки без снимка (до 2.13) — директор обязателен."""
    if r.route_policy is None or r.route_policy=='always':return True
    if r.route_policy=='skip':return False
    return r.route_threshold is None or r.amount>r.route_threshold

def needs_director(s,r):return requires_director(r)

def approval_stage(r):
    if r.status!='pending':return None
    if r.checked_by is None:return 'check'
    if r.finance_approved_by is None:return 'finance'
    return 'director'

def current_documents(s,request_id):
    return list(s.scalars(select(RequestDocument).where(RequestDocument.request_id==request_id,RequestDocument.is_current.is_(True),
                                                        RequestDocument.removed.is_(False)).order_by(RequestDocument.id)))

def missing_documents(s,r):
    kinds={d.kind for d in current_documents(s,r.id)}
    return [k for k in REQUIRED_DOCUMENTS if k not in kinds]

def enforce_documents(s,r):
    missing=missing_documents(s,r)
    if missing:
        raise HTTPException(422,'Перед отправкой приложите: '+', '.join('«'+DOCUMENT_KINDS[k]+'»' for k in missing)+'. Черновик можно сохранить без файлов.')

def budget_card(s,category,month,currency,n=0,exclude_request=None):
    """Бюджет выбранной статьи на период: лимит, оплачено, резерв утверждённых заявок, доступно и остаток после заявки."""
    b=budget_state(s,category,month,currency,0,exclude_request)
    if b['limit'] is None:
        return {'period':month,'currency':currency,'budget_set':False,'status':'no_budget','limit':None,'used':money(b['spent']),
                'reserved':money(b['reserved']),'available':None,'after':None,'mode':b['mode']}
    available=b['limit']-b['used'];after=available-n
    return {'period':month,'currency':currency,'budget_set':True,'limit':money(b['limit']),'used':money(b['spent']),
            'reserved':money(b['reserved']),'available':money(available),'after':money(after),
            'status':'ok' if after>=0 else b['mode'],'mode':b['mode']}

def overrun(b):
    """Превышение мягкого бюджета после учёта суммы, в копейках/тийинах."""
    return max(0,b['after']-b['limit']) if b['limit'] is not None else 0

def clear_approval(r,keep_check=False):
    """Любой возврат или правка снимает утверждение целиком, включая проверку бухгалтера."""
    r.approved_by=None;r.finance_approved_by=None;r.approved_at=None;r.approved_overrun=None
    if not keep_check:r.checked_by=None;r.checked_at=None

def local_date(moment):
    return moment.replace(tzinfo=timezone.utc).astimezone(clock.LOCAL_TZ).date()

def participants(r):
    return {x for x in (r.creator_id,r.last_editor_id,r.checked_by,r.finance_approved_by,r.approved_by) if x}

# Действия, после которых сотрудник считается участником заявки навсегда, даже если
# возврат или замена файла сняли согласования текущего круга.
PARTICIPANT_ACTIONS=('Создана заявка','Изменена заявка','Действие по заявке: submit','Действие по заявке: check',
                     'Действие по заявке: approve','Действие по заявке: reschedule','Добавлен документ заявки',
                     'Новая версия документа заявки','Убран документ заявки')

def all_participants(s,r):
    """Автор, редакторы, проверявшие и согласующие заявки во всех кругах согласования."""
    past=s.scalars(select(Audit.user_id).where(Audit.entity=='request',Audit.entity_id==str(r.id),Audit.user_id.is_not(None),
                                              Audit.action.in_(PARTICIPANT_ACTIONS)))
    return participants(r)|set(past)

def route_complete(r):
    """Все нужные этапы пройдены: проверка (для заявок со снимком), финансовый директор и директор по снимку."""
    if r.finance_approved_by is None or (r.route_policy is not None and r.checked_by is None):return False
    return not requires_director(r) or (r.approved_by is not None and r.approved_by!=r.finance_approved_by)

def check_payer(s,u,req):
    """Кто может провести оплату заявки: отдельное право канала и не участник заявки."""
    account=get(s,Account,req.account_id)
    right=pay_right(account)
    if right not in perms_of(u):
        raise HTTPException(403,'Оплату кассовой заявки проводит кассир.' if account.kind=='cash' else 'Оплату банковской заявки проводит расчётный бухгалтер.')
    if req.checked_by is not None and u.id==req.checked_by:
        raise HTTPException(403,'Бухгалтер, проверявший реквизиты и комплектность заявки, не проводит её оплату: нужен другой бухгалтер.')
    if u.id in all_participants(s,req):
        raise HTTPException(403,'Автор, редакторы, проверявшие и согласующие заявки (в том числе в прежних кругах согласования) не проводят её оплату.')
    act_with_right(u,right)
    return account

RETURN_HINT='Оплата не проведена. Верните заявку финансовому директору с комментарием.'

def month_bounds(month):
    start=date.fromisoformat(month+'-01')
    return start,(start.replace(day=28)+timedelta(days=4)).replace(day=1)

def accepted_overrun(s,category,month,currency):
    """Мягкое превышение статьи за месяц, которое согласующие уже приняли при утверждении.

    Учитываются утверждённые заявки со сроком в этом месяце и оплаченные заявки,
    оплата которых действительно пришлась на этот месяц."""
    start,end=month_bounds(month)
    base=select(func.max(PaymentRequest.approved_overrun)).join(Account,Account.id==PaymentRequest.account_id).where(
        PaymentRequest.category_id==category,Account.currency==currency,PaymentRequest.approved_overrun.is_not(None),
        PaymentRequest.due_date>=start,PaymentRequest.due_date<end)
    approved=s.scalar(base.where(PaymentRequest.status=='approved')) or 0
    paid=s.scalar(base.join(Ledger,Ledger.request_id==PaymentRequest.id).where(
        PaymentRequest.status=='paid',Ledger.date>=start,Ledger.date<end)) or 0
    return max(approved,paid)

def enforce_payment_budget(s,req,account,paid_on):
    """Бюджет при оплате: плательщик не может обойти его комментарием.

    Допускается только мягкое превышение статьи, которое согласующие приняли
    при утверждении заявок этого месяца, и только если заявка сама утверждалась
    на месяц оплаты."""
    month=str(paid_on)[:7];same_month=month==str(req.due_date)[:7]
    b=budget_state(s,req.category_id,month,account.currency,req.amount,req.id)
    if not b['over']:return b
    allowed=accepted_overrun(s,req.category_id,month,account.currency) if b['mode']=='soft' and same_month else 0
    if b['mode']=='hard' or overrun(b)>allowed:
        detail=f'Бюджет статьи на {month} превышен на {money(overrun(b)-allowed)} {account.currency} сверх утверждённого.'
        if b['mode']=='hard':
            detail+=' Лимит жёсткий: сначала нужно пересмотреть бюджет статьи на этот месяц.'
        elif not same_month:
            detail+=(f' Срок заявки {req.due_date}: финансовому директору нужно перенести срок на месяц оплаты;'
                     ' после переноса заявку проверяет другой финансист или администратор и утверждает директор.')
        raise HTTPException(409,f'{detail} {RETURN_HINT}')
    return b

def amount_label(n):
    """Сумма для текста: 5 000 000 или 5 000 000,50."""
    whole,cents=money(n).split('.')
    return f'{int(whole):,}'.replace(',',' ')+('' if cents=='00' else ','+cents)

def route_json(r,currency):
    """Маршрут по снимку: участвует ли директор и почему."""
    required=requires_director(r)
    if r.route_policy is None:reason='Директор утверждает заявку.'
    elif r.route_policy=='always':reason='Политика статьи: директор утверждает любую сумму.'
    elif r.route_policy=='skip':reason='Политика статьи: после финансового директора заявка готова к исполнению.'
    elif required:reason=f'Сумма выше порога статьи {amount_label(r.route_threshold)} {currency}: нужен директор.'
    else:reason=f'Сумма не выше порога статьи {amount_label(r.route_threshold)} {currency}: директор не участвует.'
    return {'policy':r.route_policy,'threshold':money(r.route_threshold) if r.route_threshold is not None else None,
            'at':str(r.route_at) if r.route_at else None,'director_required':required,'reason':reason}

def request_json(s,r,accounts=None,categories=None,users=None):
    a=get(s,Account,r.account_id);c=get(s,Category,r.category_id)
    b=budget_state(s,c.id,str(r.due_date)[:7],a.currency,r.amount if r.status in ('pending','draft') else 0)
    docs=current_documents(s,r.id)
    return {'id':r.id,'number':f'CF-{r.id:05d}','creator_id':r.creator_id,'category_id':c.id,'category':c.name,
            'account_id':a.id,'account':a.name,'account_kind':a.kind,'channel':a.kind,'currency':a.currency,'company_id':a.company_id,
            'counterparty':r.counterparty,'amount':money(r.amount),'date':str(r.due_date),'status':r.status,'purpose':r.purpose,
            'version':r.version,'last_editor_id':r.last_editor_id,'priority':r.priority,
            'created_at':str(r.created_at),'checked_by':r.checked_by,'finance_approved_by':r.finance_approved_by,'approved_by':r.approved_by,
            'approved_on':str(local_date(r.approved_at)) if r.approved_at else None,
            'approval_stage':approval_stage(r),'route':route_json(r,a.currency),
            'documents':{k:sum(d.kind==k for d in docs) for k in DOCUMENT_KINDS},'missing_documents':missing_documents(s,r),
            'project':r.project,'decision_note':r.decision_note,'overdue':r.due_date<today() and r.status in ('pending','approved'),
            'budget':budget_json(b),'budget_card':budget_card(s,c.id,str(r.due_date)[:7],a.currency,0 if r.status=='paid' else r.amount,r.id)}

def post_ledger(s,u,data):
    a=get(s,Account,data.account_id)
    if a.archived:raise HTTPException(409,'Счёт в архиве. Сначала восстановите его.')
    if 'write' not in perms_of(u) and (data.kind!='out' or not data.request_id):
        raise HTTPException(403,'Плательщик фиксирует только оплату утверждённой заявки: поступления, переводы и расход без заявки недоступны.')
    req=None
    if data.request_id:
        # Право и разделение обязанностей проверяются до любых сведений о заявке.
        req=get(s,PaymentRequest,data.request_id)
        check_payer(s,u,req)
    if data.date>today():raise HTTPException(422,'Будущий платёж — это заявка или ожидаемое поступление, а не факт.')
    if data.date<a.opening_date:raise HTTPException(422,'Операция раньше даты начального остатка счёта.')
    n=amount(data.amount);kind=data.kind
    target=None
    if kind=='transfer':
        target=get(s,Account,data.to_account_id)
        if target.archived:raise HTTPException(409,'Счёт-получатель в архиве.')
        if target.company_id!=a.company_id:raise HTTPException(422,'Переводы между компаниями оформляются отдельными поступлением и выплатой.')
        if target.id==a.id or target.currency!=a.currency:raise HTTPException(422,'Для перевода нужны разные счета одной валюты. Конвертация в этой версии не поддерживается.')
        if data.date<target.opening_date:raise HTTPException(422,'Дата перевода раньше начального остатка счёта-получателя.')
    else:get(s,Category,data.category_id)
    rec=None
    if req:
        check_request_version(req,data.request_version)
        if req.status!='approved':raise HTTPException(409,'Оплатить можно только утверждённую заявку.')
        # Сумма, счёт (и его валюта), статья и получатель берутся из утверждённой заявки.
        if kind!='out' or data.to_account_id or data.receipt_id or req.account_id!=a.id or req.amount!=n or req.category_id!=data.category_id:
            raise HTTPException(409,'Оплата проводится ровно по утверждённой заявке: её счёт, валюта, статья и сумма целиком. Изменить их при оплате нельзя.')
        if data.counterparty and data.counterparty!=req.counterparty:
            raise HTTPException(409,'Получатель платежа берётся из утверждённой заявки и не может быть заменён.')
        # Маршрут проверяется по снимку политики статьи. Заявки без снимка (утверждённые до 2.13)
        # оплачиваются только с отдельным подтверждением директора, как раньше.
        if req.finance_approved_by is None or (req.route_policy is not None and req.checked_by is None):
            raise HTTPException(409,f'Заявка не прошла все этапы согласования. {RETURN_HINT}')
        if requires_director(req) and (req.approved_by is None or req.approved_by==req.finance_approved_by):
            raise HTTPException(409,f'Заявка утверждена без отдельного подтверждения директора. {RETURN_HINT} После проверки её утвердит директор.')
        earliest=local_date(req.approved_at or req.created_at)
        if data.date<earliest:raise HTTPException(422,f'Дата оплаты не может быть раньше утверждения заявки ({earliest}).')
    if data.receipt_id:
        rec=get(s,Receipt,data.receipt_id)
        if kind!='in' or rec.status!='expected' or rec.account_id!=a.id or rec.amount!=n or rec.category_id!=data.category_id:
            raise HTTPException(409,'Поступление уже закрыто или сумма/счёт не совпадают.')
    if kind=='out' and req:
        enforce_payment_budget(s,req,a,data.date)
        # Деньги проверяются на дату оплаты, на сегодня и на срок самой заявки:
        # ни дата задним числом, ни ранняя оплата не забирают резерв других
        # утверждённых заявок со сроком раньше.
        for as_of in sorted({data.date,today(),max(today(),req.due_date)}):
            try:enforce_available_funds(s,a,as_of,n,req.id)
            except HTTPException as e:raise HTTPException(409,f'{e.detail} {RETURN_HINT}')
    elif kind=='out':
        enforce_budget(s,data.category_id,data.date,a.currency,n,data.note)
        # Расход без заявки тоже не может забрать резерв заявок, срок которых наступил.
        for as_of in sorted({data.date,today()}):enforce_available_funds(s,a,as_of,n)
        if len(data.note.strip())<10:raise HTTPException(422,'Для расхода без заявки укажите основание не короче 10 символов.')
    elif kind=='transfer':
        # Перевод не уносит со счёта деньги, зарезервированные под утверждённые заявки.
        for as_of in sorted({data.date,today()}):enforce_available_funds(s,a,as_of,n)
    t=Ledger(account_id=a.id,to_account_id=target.id if target else None,category_id=data.category_id if kind!='transfer' else None,
             kind=kind,amount=n,date=data.date,counterparty=req.counterparty if req else data.counterparty,reference=data.reference.strip(),
             note=data.note,request_id=req.id if req else None,receipt_id=rec.id if rec else None,creator_id=u.id)
    s.add(t);s.flush()
    validate_running_balance(s,a)
    if target:validate_running_balance(s,target)
    if req:req.status='paid'
    if rec:rec.status='received'
    log(s,u,'Фактическая операция','ledger',t.id,f'{kind}: {money(n)} {a.currency}; документ: {data.reference}'+(f'; заявка CF-{req.id:05d}' if req else ''))
    if req:log(s,u,'Оплата заявки','request',req.id,f'операция {t.id}; {money(n)} {a.currency}; документ: {data.reference}')
    return t

def dashboard(s,currency,horizon=30):
    accounts=[a for a in s.scalars(select(Account)) if a.currency==currency]
    ids={a.id for a in accounts};start=today()
    account_balances={a.id:account_balance(s,a) for a in accounts}
    current=sum(account_balances.values())
    req=[r for r in s.scalars(select(PaymentRequest)) if r.account_id in ids]
    receipt=[r for r in s.scalars(select(Receipt)) if r.account_id in ids and r.status=='expected']
    approved=[r for r in req if r.status=='approved']
    pending=[r for r in req if r.status=='pending']
    reserve=get_setting(s,'reserve_'+currency);reserve=int(reserve.value) if reserve else 0
    days=[];bal=current;requested_bal=current;requested_accounts=account_balances.copy()
    for i in range(horizon):
        day=start+timedelta(days=i)
        ins=[r for r in receipt if max(start,r.due_date)==day]
        outs=[r for r in approved if max(start,r.due_date)==day]
        waiting=[r for r in pending if max(start,r.due_date)==day]
        inc=sum(r.amount for r in ins);out=sum(r.amount for r in outs)
        opening=bal;requested_opening=requested_bal
        all_out=out+sum(r.amount for r in waiting)
        bal+=inc-out;requested_bal+=inc-all_out
        for r in ins:account_balances[r.account_id]+=r.amount
        for r in outs:account_balances[r.account_id]-=r.amount
        for r in ins:requested_accounts[r.account_id]+=r.amount
        for r in outs+waiting:requested_accounts[r.account_id]-=r.amount
        shortfalls=[{'account_id':a.id,'account':a.name,'balance':money(account_balances[a.id]),
                     'allow_overdraft':a.allow_overdraft} for a in accounts if account_balances[a.id]<0]
        requested_shortfalls=[{'account_id':a.id,'account':a.name,'balance':money(requested_accounts[a.id])} for a in accounts if requested_accounts[a.id]<0]
        days.append({'date':str(day),'opening':money(opening),'incoming':money(inc),'outgoing':money(out),'balance':money(bal),
                     'requested_opening':money(requested_opening),'requested_outgoing':money(all_out),'requested_balance':money(requested_bal),
                     'requested_risk':requested_bal<reserve or bool(requested_shortfalls),'requested_account_shortfalls':requested_shortfalls,
                     'risk':bal<reserve or bool(shortfalls),'reserve_risk':bal<reserve,'account_shortfalls':shortfalls,
                     'events':[{'id':r.id,'kind':'in','name':r.counterparty,'amount':money(r.amount),'overdue':r.due_date<start} for r in ins]+
                     [{'id':r.id,'kind':'out','name':r.counterparty,'amount':money(r.amount),'overdue':r.due_date<start,'priority':r.priority} for r in outs],
                     'pending_events':[{'id':r.id,'kind':'pending','name':r.counterparty,'amount':money(r.amount),'overdue':r.due_date<start,'priority':r.priority} for r in waiting]})
    period=[t for t in s.scalars(effective_cashflows(s)) if t.account_id in ids and t.date.year==start.year and t.date.month==start.month and t.date<=start]
    pending=[r for r in req if r.status=='pending']
    return {'currency':currency,'as_of':str(start),'account_count':len(accounts),'balance':money(current) if accounts else None,
            'reserve':money(reserve),'pending_count':len(pending),'pending_amount':money(sum(r.amount for r in pending)),
            'incoming7':money(sum(r.amount for r in receipt if max(start,r.due_date)<start+timedelta(days=7))),
            'outgoing7':money(sum(r.amount for r in approved if max(start,r.due_date)<start+timedelta(days=7))),
            'fact_in':money(sum(t.amount for t in period if t.kind=='in')),'fact_out':money(sum(t.amount for t in period if t.kind=='out')),
            'overdue_count':sum(r.due_date<start for r in approved)+sum(r.due_date<start for r in receipt),
            'forecast':days,'forecast_has_events':bool(receipt or approved or pending),
            'scenario_note':'Сценарий «Все заявки» добавляет к утверждённым оплатам заявки на согласовании. Черновики, возвращённые, отменённые и отклонённые заявки исключены. Оба сценария включают ожидаемые поступления: они ещё не подтверждены банком. Только утверждённые заявки резервируют бюджет.',
            'note':'Прогноз на конец дня: текущие остатки + ожидаемые поступления − утверждённые неоплаченные заявки. Проверяется также нехватка на каждом счёте; переводы между счетами автоматически не предполагаются. Отрицательный остаток требует проверки даже при разрешённом овердрафте: его лимит не задан. Просрочка отнесена на сегодня. Порядок платежей внутри дня не учитывается. Модель и бюджеты повторно не добавляются.'}
