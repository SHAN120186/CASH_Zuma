"""Atomic issue of received cash against independently approved requests."""
import hmac
import json
from datetime import date
from hashlib import sha256
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, func
from sqlalchemy.orm.attributes import flag_modified

from .db import (Account, Budget, CashFundingPaymentGroup, CashFundingPaymentItem,
                 CashFunding, Ledger, PaymentRequest, unit)
from .cash_funding import funding_for, _require_active_legs
from .security import session_user, perms_of, act_with_right, jwt_key
from .section_access import can_edit_section
from .services import (get, money, log, amount, account_balance, check_payer,
                       check_request_version, route_complete, local_date, today,
                       enforce_payment_budget, post_ledger, request_batch, budget_state, accepted_overrun, overrun)

router=APIRouter()


class BatchItem(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:int=Field(strict=True,ge=1,le=9223372036854775807)
    version:int=Field(strict=True,ge=1,le=2147483647)


class BatchPreview(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    version:int=Field(strict=True,ge=1,le=2147483647)
    request_version:int=Field(strict=True,ge=1,le=2147483647)
    items:list[BatchItem]=Field(min_length=1,max_length=100)
    date:date
    reference:str=Field(min_length=2,max_length=120)
    note:str=Field(default='',max_length=1000)
    key:str=Field(pattern=r'^[A-Za-z0-9_-]{16,80}$')

    @model_validator(mode='after')
    def distinct(self):
        if len({item.id for item in self.items})!=len(self.items):
            raise ValueError('В группе нельзя выбрать одну заявку несколько раз.')
        return self


class BatchCommit(BatchPreview):
    token:str=Field(pattern=r'^[0-9a-f]{64}$')
    confirm:bool=Field(strict=True)


def _payload(data):
    return data.model_dump(mode='json',exclude={'token','confirm'})


def _digest(data):
    return sha256(json.dumps(_payload(data),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _source(s,user,id):
    from .requests_api import visible
    parent=visible(s,user,id)
    funding=funding_for(s,id)
    if funding is None or funding.mode!='batch':
        raise HTTPException(409,'Для групповой выдачи нужен отдельный документ получения наличных для группы.')
    cash=get(s,Account,funding.cash_account_id)
    if cash.id!=parent.account_id or cash.kind!='cash' or cash.company_id!=funding.company_id or parent.amount!=funding.net_amount:
        raise HTTPException(409,'Параметры документа получения наличных расходятся. Нужна проверка.')
    return parent,funding,cash


def _allowed(user,parent,funding,cash):
    if 'pay_cash' not in perms_of(user) or not can_edit_section(user,'requests','ledger'):
        raise HTTPException(403,'Выдачу наличных по выбранным заявкам подтверждает кассир.')
    if parent.status!='approved' or not route_complete(parent) or funding.state!='received':
        raise HTTPException(409,'Сначала согласуйте документ и подтвердите фактическое получение наличных в кассу.')
    if cash.archived:raise HTTPException(409,'Касса в архиве. Сначала восстановите счёт.')
    act_with_right(user,'pay_cash')


def _allocated(s,funding):
    return s.scalar(select(func.coalesce(func.sum(CashFundingPaymentGroup.total_amount),0)).where(
        CashFundingPaymentGroup.funding_id==funding.id)) or 0


def _meta(parent,funding,cash):
    return {'request_id':parent.id,'funding_id':funding.id,'company_id':funding.company_id,
            'cash_account_id':cash.id,'mode':'batch','currency':cash.currency,
            'version':funding.version,'request_version':parent.version}


def _group(s,group,items=None):
    if items is None:
        items=list(s.scalars(select(CashFundingPaymentItem).where(CashFundingPaymentItem.group_id==group.id).order_by(CashFundingPaymentItem.id)))
    return {'id':group.id,'key':group.key,'total_amount':money(group.total_amount),
            'remaining_amount':money(group.remaining_amount),'cash_balance':money(group.cash_balance),
            'date':str(group.date),'reference':group.reference,'note':group.note,
            'request_ids':[x.request_id for x in items],'ledger_ids':[x.ledger_id for x in items]}


def _result(s,parent,funding,cash,group):
    saved=_group(s,group)
    return {**_meta(parent,funding,cash),'version':group.funding_version,'request_version':group.request_version,
            'group':saved,'paid_request_ids':saved['request_ids'],
            'remaining_amount':money(group.remaining_amount),'cash_balance':money(group.cash_balance)}


def _ledger_reference(funding,data,request):
    return f'{data.reference[:100]}/CF-{request.id}/B-{sha256(data.key.encode()).hexdigest()[:16]}'


def _validate(s,user,parent,funding,cash,data):
    from .requests_api import visible
    _allowed(user,parent,funding,cash)
    check_request_version(parent,data.request_version)
    if funding.version!=data.version:raise HTTPException(409,'Получение наличных уже изменилось. Обновите список и проверку.')
    if data.date>today() or data.date<max(cash.opening_date,funding.receive_date):
        raise HTTPException(422,'Дата выдачи должна быть не раньше получения наличных и не позже сегодняшнего дня.')
    _require_active_legs(s,funding)
    receipt=get(s,Ledger,funding.receipt_ledger_id)
    from .services import deleted_ledger_ids
    if (receipt.kind!='transfer' or receipt.account_id!=funding.transit_account_id or receipt.to_account_id!=cash.id
        or receipt.amount!=funding.net_amount or receipt.date!=funding.receive_date or receipt.request_id or receipt.receipt_id
        or receipt.reversal_of or receipt.id in deleted_ledger_ids(s)
        or s.scalar(select(Ledger.id).where(Ledger.reversal_of==receipt.id))):
        raise HTTPException(409,'Получение наличных отменено или изменилось. Нужна проверка.')
    selected=[]
    for item in data.items:
        row=visible(s,user,item.id)
        if row.id==parent.id or row.account_id!=cash.id or funding_for(s,row.id) is not None:
            raise HTTPException(409,'Выберите обычные наличные заявки той же компании, кассы и валюты.')
        check_payer(s,user,row);check_request_version(row,item.version)
        if row.status!='approved' or not route_complete(row):
            raise HTTPException(409,'В группу входят только полностью согласованные неоплаченные заявки.')
        if data.date<local_date(row.approved_at or row.created_at):
            raise HTTPException(422,'Дата выдачи не может быть раньше утверждения каждой выбранной заявки.')
        if s.scalar(select(Ledger.id).where(Ledger.request_id==row.id)) or s.scalar(
                select(CashFundingPaymentItem.id).where(CashFundingPaymentItem.request_id==row.id)):
            raise HTTPException(409,'Одна из заявок уже оплачена или включена в проведённую группу. Повторная выдача запрещена.')
        if s.scalar(select(Ledger.id).where(Ledger.account_id==cash.id,Ledger.reference==_ledger_reference(funding,data,row))):
            raise HTTPException(409,'Документ выдачи уже есть в операциях. Обновите состояние группы.')
        enforce_payment_budget(s,row,cash,data.date)
        selected.append(row)
    total=sum(row.amount for row in selected)
    # Same-month approvals are already reserved. Payments moved to another
    # month add their combined amount, which an individual check cannot see.
    from collections import defaultdict
    categories=defaultdict(list)
    for row in selected:categories[row.category_id].append(row)
    month=str(data.date)[:7]
    for category_id,rows in categories.items():
        shifted=[row for row in rows if str(row.due_date)[:7]!=month]
        state=budget_state(s,category_id,month,cash.currency,sum(row.amount for row in shifted))
        allowed=accepted_overrun(s,category_id,month,cash.currency) if state['mode']=='soft' and not shifted else 0
        if state['over'] and (state['mode']=='hard' or overrun(state)>allowed):
            raise HTTPException(409,'Совокупная оплата выбранных заявок превышает бюджет месяца выплаты. Пересмотрите бюджет или согласованные сроки заявок.')
    remaining=funding.net_amount-_allocated(s,funding)
    if total>remaining:raise HTTPException(409,'Сумма выбранных заявок больше неиспользованной суммы получения наличных.')
    # A backdated issue must preserve every later daily balance, not only today.
    dates={data.date,today(),*s.scalars(select(Ledger.date).where(Ledger.date>=data.date,
            (Ledger.account_id==cash.id)|(Ledger.to_account_id==cash.id)))}
    if any(account_balance(s,cash,day)<total for day in dates):
        raise HTTPException(409,'На выбранную дату или после неё в кассе недостаточно фактических денег для всей группы.')
    return selected,total,remaining,account_balance(s,cash)


def _signature(s,user,parent,funding,cash,data):
    from .services import deleted_ledger_ids
    entries=[tuple(map(str,row)) for row in s.execute(select(Ledger.id,Ledger.account_id,Ledger.to_account_id,
        Ledger.amount,Ledger.date,Ledger.kind,Ledger.category_id,Ledger.reversal_of,Ledger.request_id).order_by(Ledger.id))]
    budgets=[tuple(map(str,row)) for row in s.execute(select(Budget.id,Budget.category_id,Budget.amount,Budget.month,Budget.currency,Budget.mode).order_by(Budget.id))]
    requests=[tuple(map(str,row)) for row in s.execute(select(PaymentRequest.id,PaymentRequest.version,
        PaymentRequest.status,PaymentRequest.amount).order_by(PaymentRequest.id))]
    payload={'user':user.id,**_meta(parent,funding,cash),'data':_payload(data),'entries':entries,'budgets':budgets,
             'requests':requests,'cash':(cash.opening,str(cash.opening_date),cash.archived),
             'deleted_ledger_ids':sorted(deleted_ledger_ids(s)),
             'cash_balance':account_balance(s,cash),'date_balance':account_balance(s,cash,data.date),
             'allocated':_allocated(s,funding)}
    return hmac.new(jwt_key().encode(),json.dumps(payload,sort_keys=True,separators=(',',':')).encode(),sha256).hexdigest()


@router.get('/api/requests/{id}/cash-funding/payments')
def read_payments(id:int,request:Request):
    from .requests_api import request_view
    with unit() as s:
        user,_=session_user(s,request);parent,funding,cash=_source(s,user,id)
        try:_allowed(user,parent,funding,cash)
        except HTTPException as error:can_pay=False;reason=error.detail
        else:can_pay=True;reason=''
        allocated=_allocated(s,funding)
        if can_pay and allocated>=funding.net_amount:
            can_pay=False;reason='Полученная наличная сумма уже полностью выдана по заявкам.'
        # Owning a request is not permission to inspect a whole cash desk or
        # payment groups belonging to other applicants.
        modes=getattr(user,'_sections',None)
        can_read=('view' in perms_of(user) or 'pay_cash' in perms_of(user)) and (modes is None or modes.get('ledger')!='hidden')
        rows=[];candidates=[]
        if can_pay:
            query=select(PaymentRequest).where(PaymentRequest.account_id==cash.id,PaymentRequest.status=='approved',
                PaymentRequest.id.not_in(select(CashFunding.request_id))).order_by(PaymentRequest.due_date,PaymentRequest.id).limit(500)
            rows=list(s.scalars(query))
            batch=request_batch(s,rows)
            paid_ids=set(s.scalars(select(Ledger.request_id).where(Ledger.request_id.in_([row.id for row in rows])))) if rows else set()
            for row in rows:
                value=request_view(s,user,row,batch=batch)
                if row.id not in paid_ids and 'pay' in value['actions']:candidates.append(value)
        groups=list(s.scalars(select(CashFundingPaymentGroup).where(CashFundingPaymentGroup.funding_id==funding.id).order_by(CashFundingPaymentGroup.id.desc()).limit(100))) if can_read else []
        groups.reverse()
        from collections import defaultdict
        group_items=defaultdict(list)
        if groups:
            for item in s.scalars(select(CashFundingPaymentItem).where(CashFundingPaymentItem.group_id.in_([group.id for group in groups])).order_by(CashFundingPaymentItem.id)):
                group_items[item.group_id].append(item)
        return {**_meta(parent,funding,cash),'received_amount':money(funding.net_amount if funding.state=='received' else 0),
                'allocated_amount':money(allocated) if can_read else None,
                'remaining_amount':money(funding.net_amount-allocated if funding.state=='received' else 0) if can_read else None,
                'cash_balance':money(account_balance(s,cash)) if can_read else None,'can_pay':can_pay,'blocked_reason':reason,
                'candidates':candidates,'groups':[_group(s,group,group_items[group.id]) for group in groups]}


@router.post('/api/requests/{id}/cash-funding/payments/preview')
def preview_payments(id:int,data:BatchPreview,request:Request):
    with unit() as s:
        user,_=session_user(s,request);parent,funding,cash=_source(s,user,id)
        if s.scalar(select(CashFundingPaymentGroup.id).where(CashFundingPaymentGroup.key==data.key)):
            raise HTTPException(409,'Группа с этим ключом уже проведена. Проверьте состояние вместо повторной оплаты.')
        selected,total,remaining,balance=_validate(s,user,parent,funding,cash,data)
        return {**_meta(parent,funding,cash),**_payload(data),'token':_signature(s,user,parent,funding,cash,data),
                'total_amount':money(total),'remaining_before':money(remaining),'cash_balance_before':money(balance),
                'remaining_amount':money(remaining-total),'cash_balance':money(balance-total)}


@router.post('/api/requests/{id}/cash-funding/payments/commit')
def commit_payments(id:int,data:BatchCommit,request:Request):
    if not data.confirm:raise HTTPException(422,'Подтвердите фактическую выдачу наличных по всем выбранным заявкам.')
    with unit(True) as s:
        user,_=session_user(s,request);parent,funding,cash=_source(s,user,id)
        # A retry uses the original immutable payload, not fresh revisions or a new selection.
        previous=s.scalar(select(CashFundingPaymentGroup).where(CashFundingPaymentGroup.key==data.key))
        if previous is not None:
            if previous.funding_id!=funding.id or previous.creator_id!=user.id or previous.payload_hash!=_digest(data):
                raise HTTPException(409,'Ключ проведённой группы не соответствует этому пользователю, документу или составу оплаты.')
            if 'pay_cash' not in perms_of(user) or not can_edit_section(user,'requests','ledger'):
                raise HTTPException(403,'Нет доступа к подтверждению выдачи наличных.')
            return _result(s,parent,funding,cash,previous)
        selected,total,remaining,balance=_validate(s,user,parent,funding,cash,data)
        if not hmac.compare_digest(data.token,_signature(s,user,parent,funding,cash,data)):
            raise HTTPException(409,'После проверки данные изменились. Обновите состав и подтвердите заново.')
        entries=[]
        for row in selected:
            ledger=post_ledger(s,user,SimpleNamespace(account_id=cash.id,to_account_id=None,category_id=row.category_id,
                kind='out',amount=money(row.amount),date=data.date,counterparty=row.counterparty,
                reference=_ledger_reference(funding,data,row),note=data.note,request_id=row.id,
                request_version=row.version,receipt_id=None))
            entries.append((row,ledger))
        flag_modified(funding,'state');flag_modified(parent,'decision_note');s.flush()
        group=CashFundingPaymentGroup(company_id=funding.company_id,funding_id=funding.id,key=data.key,
            payload_hash=_digest(data),total_amount=total,remaining_amount=remaining-total,
            cash_balance=account_balance(s,cash),funding_version=funding.version,request_version=parent.version,
            date=data.date,reference=data.reference,note=data.note,creator_id=user.id)
        s.add(group);s.flush()
        for row,ledger in entries:
            s.add(CashFundingPaymentItem(company_id=funding.company_id,group_id=group.id,
                request_id=row.id,ledger_id=ledger.id,amount=row.amount))
        log(s,user,'Выдача наличных по группе заявок','request',parent.id,json.dumps({
            'group_id':group.id,'request_ids':[row.id for row in selected],'ledger_ids':[entry.id for _,entry in entries],
            'total_amount':money(total),'remaining_amount':money(remaining-total),'reference':data.reference},ensure_ascii=False))
        s.flush()
        return _result(s,parent,funding,cash,group)
