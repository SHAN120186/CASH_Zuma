from __future__ import annotations
import json
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import select
from fastapi import HTTPException
from .db import Account, Category, Budget, PaymentRequest, Receipt, Ledger, Audit, Setting

def today():return datetime.now(timezone(timedelta(hours=5))).date()
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
    return value

def log(s,user,action,entity,id='',detail=''):
    s.add(Audit(user_id=user.id if user else None,action=action,entity=entity,entity_id=str(id),detail=detail))
def account_balance(s,a,upto=None):
    upto=upto or today();balance=a.opening
    for t in s.scalars(select(Ledger).where(Ledger.date<=upto)):
        if t.account_id==a.id:balance += t.amount if t.kind=='in' else -t.amount
        if t.to_account_id==a.id:balance += t.amount
    return balance

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

def needs_director(s,r):
    a=get(s,Account,r.account_id)
    threshold=s.get(Setting,'approval_limit_'+a.currency)
    return threshold is None or r.amount>int(threshold.value)

def request_json(s,r,accounts=None,categories=None,users=None):
    a=get(s,Account,r.account_id);c=get(s,Category,r.category_id)
    b=budget_state(s,c.id,str(r.due_date)[:7],a.currency,r.amount if r.status in ('pending','draft') else 0)
    return {'id':r.id,'number':f'CF-{r.id:05d}','creator_id':r.creator_id,'category_id':c.id,'category':c.name,
            'account_id':a.id,'account':a.name,'currency':a.currency,'counterparty':r.counterparty,
            'amount':money(r.amount),'date':str(r.due_date),'status':r.status,'purpose':r.purpose,
            'version':r.version,'last_editor_id':r.last_editor_id,'priority':r.priority,
            'created_at':str(r.created_at),'finance_approved_by':r.finance_approved_by,
            'approval_stage':('director' if r.finance_approved_by else 'finance') if r.status=='pending' else None,
            'project':r.project,'decision_note':r.decision_note,'overdue':r.due_date<today() and r.status in ('pending','approved'),
            'budget':budget_json(b)}

def post_ledger(s,u,data):
    a=get(s,Account,data.account_id)
    if a.archived:raise HTTPException(409,'Счёт в архиве. Сначала восстановите его.')
    if u.role=='cashier' and (data.kind!='out' or not data.request_id):raise HTTPException(403,'Кассир фиксирует оплату только утверждённой заявки.')
    if data.date>today():raise HTTPException(422,'Будущий платёж — это заявка или ожидаемое поступление, а не факт.')
    if data.date<a.opening_date:raise HTTPException(422,'Операция раньше даты начального остатка счёта.')
    n=amount(data.amount);kind=data.kind
    target=None
    if kind=='transfer':
        target=get(s,Account,data.to_account_id)
        if target.archived:raise HTTPException(409,'Счёт-получатель в архиве.')
        if target.id==a.id or target.currency!=a.currency:raise HTTPException(422,'Для перевода нужны разные счета одной валюты. Конвертация в этой версии не поддерживается.')
        if data.date<target.opening_date:raise HTTPException(422,'Дата перевода раньше начального остатка счёта-получателя.')
    else:get(s,Category,data.category_id)
    req=None;rec=None
    if data.request_id:
        req=get(s,PaymentRequest,data.request_id)
        check_request_version(req,data.request_version)
        if kind!='out' or req.status!='approved' or req.account_id!=a.id or req.amount!=n or req.category_id!=data.category_id:
            raise HTTPException(409,'Оплатить можно только утверждённую заявку, целиком, на её счёт и сумму.')
    if data.receipt_id:
        rec=get(s,Receipt,data.receipt_id)
        if kind!='in' or rec.status!='expected' or rec.account_id!=a.id or rec.amount!=n or rec.category_id!=data.category_id:
            raise HTTPException(409,'Поступление уже закрыто или сумма/счёт не совпадают.')
    if kind=='out':
        enforce_budget(s,data.category_id,data.date,a.currency,n,data.note,req.id if req else None)
        if not req and len(data.note.strip())<10:raise HTTPException(422,'Для расхода без заявки укажите основание не короче 10 символов.')
    t=Ledger(account_id=a.id,to_account_id=target.id if target else None,category_id=data.category_id if kind!='transfer' else None,
             kind=kind,amount=n,date=data.date,counterparty=data.counterparty,reference=data.reference.strip(),
             note=data.note,request_id=req.id if req else None,receipt_id=rec.id if rec else None,creator_id=u.id)
    s.add(t);s.flush()
    validate_running_balance(s,a)
    if target:validate_running_balance(s,target)
    if req:req.status='paid'
    if rec:rec.status='received'
    log(s,u,'Фактическая операция','ledger',t.id,f'{kind}: {money(n)} {a.currency}; документ: {data.reference}')
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
    reserve=s.get(Setting,'reserve_'+currency);reserve=int(reserve.value) if reserve else 0
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
