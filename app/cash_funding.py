"""Cash paid from a request after a separate bank withdrawal and cash receipt.

These commands record completed facts; they never instruct an external bank.
The serialized write unit, request/funding revisions, exact signed preview and
unique leg references guard against retries and concurrent duplicate facts.
"""
import hmac
import json
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select

from .db import Account, Audit, Budget, CashFunding, Category, Ledger, PaymentRequest, now, unit
from .security import act_with_right, jwt_key, perms_of, session_user
from .section_access import can_edit_section
from .services import (account_balance, all_participants, amount, enforce_budget, enforce_reserved_funds,
                       budget_state, get, local_date, log, money, overrun, route_complete, today, validate_running_balance)

router=APIRouter()


class CashFundingIn(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    bank_account_id:int=Field(ge=1)
    gross_amount:str
    fee_percent:str
    fee_category_id:int=Field(ge=1)


class FundingPreview(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    version:int=Field(ge=1)
    date:date
    reference:str=Field(min_length=1,max_length=120)
    note:str=Field(default='',max_length=1000)


class FundingCommit(FundingPreview):
    token:str=Field(pattern=r'^[0-9a-f]{64}$')
    confirm:bool=Field(strict=True)


def funding_for(s,request_id):
    cache=s.info.get('cash_funding_batch')
    if cache is not None and request_id in cache['ids']:return cache['values'].get(request_id)
    return s.scalar(select(CashFunding).where(CashFunding.request_id==request_id))


def calculate(gross_value,percent_value):
    gross=amount(gross_value)
    try:
        percent=Decimal(str(percent_value))
        if not percent.is_finite() or not 0<=percent<100 or percent!=percent.quantize(Decimal('.01')):
            raise ValueError()
    except (InvalidOperation,ValueError,TypeError):
        raise HTTPException(422,'Комиссия должна быть от 0 до 99,99%, не более двух знаков после запятой.')
    fee=int((Decimal(gross)*percent/100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    net=gross-fee
    if net<=0:raise HTTPException(422,'После комиссии должна остаться положительная сумма наличной заявки.')
    return gross,format(percent,'.2f'),fee,net


def validate_plan(s,request_account,request_amount,data):
    bank=get(s,Account,data.bank_account_id)
    category=get(s,Category,data.fee_category_id)
    if request_account.kind!='cash' or bank.kind!='bank':
        raise HTTPException(422,'Для наличных через банк выберите банковский счёт и кассу.')
    if bank.archived or request_account.archived:
        raise HTTPException(409,'Счёт в архиве. Выберите действующий банк и кассу.')
    if bank.company_id!=request_account.company_id or bank.currency!=request_account.currency:
        raise HTTPException(422,'Банк и касса должны принадлежать одной компании и иметь одну валюту.')
    from .category_catalog import catalog
    active={c.id for c,_ in catalog(s)}
    if category.type!='outcome' or category.activity=='unclassified' or category.id not in active:
        raise HTTPException(422,'Для комиссии выберите действующую расходную статью своей компании.')
    gross,percent,fee,net=calculate(data.gross_amount,data.fee_percent)
    if request_amount!=net:
        raise HTTPException(422,'Сумма наличной заявки должна равняться сумме списания из банка за вычетом комиссии.')
    return bank,category,gross,percent,fee,net


def save_plan(s,user,request,data,*,provided=True):
    current=funding_for(s,request.id)
    if not provided:
        if current:
            # An older client may edit text, but cannot silently erase or alter
            # the approved relationship or its derived cash amount.
            if request.account_id!=current.cash_account_id or request.amount!=current.net_amount:
                raise HTTPException(409,'В заявке есть получение наличных через банк. Обновите форму перед изменением суммы или кассы.')
        return
    if current and current.state!='unfunded':
        raise HTTPException(409,'Деньги по заявке уже сняты. Параметры наличного финансирования больше не меняются.')
    if data is None:
        if current:s.delete(current)
        return
    cash=get(s,Account,request.account_id)
    bank,category,gross,percent,fee,net=validate_plan(s,cash,request.amount,data)
    if current is None:
        current=CashFunding(company_id=cash.company_id,request_id=request.id,bank_account_id=bank.id,
                            cash_account_id=cash.id,created_by=user.id)
        s.add(current)
    current.bank_account_id=bank.id;current.cash_account_id=cash.id
    current.gross_amount=gross;current.fee_percent=percent;current.fee_amount=fee
    current.net_amount=net;current.fee_category_id=category.id
    # Touch the request too: stale approval/document actions cannot use a plan
    # changed under a funding-only revision.
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(request,'decision_note')
    s.flush()
    log(s,user,'Настроено получение наличных через банк','request',request.id,json.dumps({
        'bank_account_id':bank.id,'cash_account_id':cash.id,'gross_amount':money(gross),
        'fee_percent':percent,'fee_amount':money(fee),'net_amount':money(net),'fee_category_id':category.id},ensure_ascii=False))


def require_unfunded(s,request):
    value=funding_for(s,request.id)
    if value and value.state!='unfunded':
        raise HTTPException(409,'По заявке уже учтено снятие наличных. Нельзя менять, закрывать или удалять её отдельно от денежных фактов.')


def require_settled_cash_receipt(s,request):
    value=funding_for(s,request.id)
    if value and value.state=='in_transit':
        raise HTTPException(409,'Сначала подтвердите получение снятых денег в кассу. Закрытие или удаление заявки не отменяет снятие и комиссию.')


def fee_budget(s,request,note='',*,final=False,check_only=False):
    funding=funding_for(s,request.id)
    if funding is None or not funding.fee_amount:return None
    bank=get(s,Account,funding.bank_account_id)
    extra=funding.fee_amount+(request.amount if funding.fee_category_id==request.category_id else 0)
    if check_only:
        state=budget_state(s,funding.fee_category_id,str(request.due_date)[:7],bank.currency,extra,request.id)
        if state['over'] and state['mode']=='hard':
            raise HTTPException(409,'Жёсткий лимит статьи комиссии превышен. Сначала пересмотрите бюджет комиссии.')
    else:state=enforce_budget(s,funding.fee_category_id,request.due_date,bank.currency,extra,note,request.id)
    if final:
        funding.approved_fee_overrun=overrun(state)
        funding.approved_fee_month=str(request.due_date)[:7]
        if funding.fee_category_id==request.category_id:
            request.approved_overrun=max(request.approved_overrun or 0,funding.approved_fee_overrun)
    return state


def accepted_fee_overrun(s,funding,month,currency):
    value=s.scalar(select(func.max(CashFunding.approved_fee_overrun)).join(
        PaymentRequest,PaymentRequest.id==CashFunding.request_id).join(Account,Account.id==CashFunding.bank_account_id).where(
        CashFunding.fee_category_id==funding.fee_category_id,CashFunding.approved_fee_month==month,Account.currency==currency,
        or_(PaymentRequest.status=='approved',CashFunding.fee_ledger_id.is_not(None))))
    return value or 0


def protected_ledger_ids(s):
    return {id for funding in s.scalars(select(CashFunding)) for id in
            (funding.transfer_ledger_id,funding.fee_ledger_id,funding.receipt_ledger_id) if id is not None}


def require_independent_ledger(s,entry):
    if entry.id in protected_ledger_ids(s):
        raise HTTPException(409,'Операция связана с получением наличных через банк. Изменение или отдельное сторно нарушит связанную сверку.')


def require_regular_account(account):
    if account.kind=='transit':
        raise HTTPException(409,'Служебный счёт «Деньги в пути» используется только связанным получением наличных через банк.')


def _authorized(s,user,request,funding,action):
    right='pay_bank' if action=='withdraw' else 'pay_cash'
    if right not in perms_of(user) or not can_edit_section(user,'requests','ledger'):
        raise HTTPException(403,'Снятие из банка подтверждает расчётный бухгалтер, получение в кассу — кассир.')
    if request.status!='approved' or not route_complete(request):
        raise HTTPException(409,'Сначала заявка должна пройти все этапы согласования.')
    # The checking accountant may record the bank fact. Cash receiving is not
    # issuing a payment; its payer separation is checked on the final cash issue.
    if action=='withdraw' and user.id in all_participants(s,request,include_check=False):
        raise HTTPException(403,'Автор, редакторы и финансовые согласующие не подтверждают снятие по своей заявке.')
    if funding.cash_account_id!=request.account_id or funding.net_amount!=request.amount:
        raise HTTPException(409,'Параметры заявки и наличного финансирования расходятся. Нужна проверка.')
    expected='unfunded' if action=='withdraw' else 'in_transit'
    if funding.state!=expected:
        raise HTTPException(409,'Этот этап уже проведён или пока недоступен. Обновите заявку, не повторяйте денежный факт.')
    act_with_right(user,right)


def capabilities(s,user,request,funding):
    result={}
    for action in ('withdraw','receive'):
        try:_authorized(s,user,request,funding,action)
        except HTTPException:result['can_'+action]=False
        else:result['can_'+action]=True
    return result


def funding_json(s,funding,user=None,request=None,accounts=None,categories=None):
    if not funding:return None
    bank=(accounts or {}).get(funding.bank_account_id) or get(s,Account,funding.bank_account_id)
    fee=(categories or {}).get(funding.fee_category_id) or get(s,Category,funding.fee_category_id)
    result={key:getattr(funding,key) for key in ('id','version','bank_account_id','cash_account_id','fee_category_id','state',
        'transit_account_id','transfer_ledger_id','fee_ledger_id','receipt_ledger_id')}
    result.update({key:money(getattr(funding,key)) for key in ('gross_amount','fee_amount','net_amount')})
    result.update({'fee_percent':funding.fee_percent,'bank_account':bank.name,'bank_account_currency':bank.currency,
                   'fee_category_name':fee.name,'withdraw_date':str(funding.withdraw_date) if funding.withdraw_date else None,
                   'receive_date':str(funding.receive_date) if funding.receive_date else None,
                   'withdraw_reference':funding.withdraw_reference,'receive_reference':funding.receive_reference,
                   'can_withdraw':False,'can_receive':False})
    if user is not None and request is not None:result.update(capabilities(s,user,request,funding))
    return result


def _context(s,user,request_id,action,data):
    from .requests_api import visible
    request=visible(s,user,request_id)
    funding=funding_for(s,request_id)
    if funding is None:raise HTTPException(404,'В заявке не выбраны наличные через банк.')
    _authorized(s,user,request,funding,action)
    if data.version!=funding.version:raise HTTPException(409,'Наличное финансирование уже изменено. Обновите заявку.')
    bank=get(s,Account,funding.bank_account_id);cash=get(s,Account,funding.cash_account_id)
    if action=='withdraw':
        validate_plan(s,cash,request.amount,CashFundingIn(bank_account_id=bank.id,gross_amount=money(funding.gross_amount),
                      fee_percent=funding.fee_percent,fee_category_id=funding.fee_category_id))
    else:
        # No new fee or expense is created on receipt. Retiring the historical
        # fee article must not strand money already withdrawn from the bank.
        get(s,Category,funding.fee_category_id)
        gross,percent,fee,net=calculate(money(funding.gross_amount),funding.fee_percent)
        if (gross,fee,net)!=(funding.gross_amount,funding.fee_amount,funding.net_amount):
            raise HTTPException(409,'Параметры заявки и наличного финансирования расходятся. Нужна проверка.')
    if bank.archived or cash.archived:raise HTTPException(409,'Банк или касса в архиве. Сначала восстановите счёт.')
    if bank.kind!='bank' or cash.kind!='cash' or bank.currency!=cash.currency or bank.company_id!=cash.company_id:
        raise HTTPException(409,'Тип или валюта связанных счетов изменились. Нужна проверка.')
    minimum=local_date(request.approved_at or request.created_at)
    if data.date>today():raise HTTPException(422,'Подтверждайте только фактически выполненный этап, без будущей даты.')
    if data.date<max(bank.opening_date,cash.opening_date,minimum):
        raise HTTPException(422,'Дата не может быть раньше начала учёта счетов и утверждения заявки.')
    if action=='receive' and (not funding.withdraw_date or data.date<funding.withdraw_date):
        raise HTTPException(422,'Получение в кассу не может быть раньше списания из банка.')
    reference_field=CashFunding.withdraw_reference if action=='withdraw' else CashFunding.receive_reference
    account_field=CashFunding.bank_account_id if action=='withdraw' else CashFunding.cash_account_id
    date_field=CashFunding.withdraw_date if action=='withdraw' else CashFunding.receive_date
    other=s.scalar(select(CashFunding.id).where(CashFunding.id!=funding.id,account_field==(bank.id if action=='withdraw' else cash.id),
                    date_field==data.date,reference_field==data.reference))
    if other is not None:
        raise HTTPException(409,'Этот банковский или кассовый документ уже учтён по другой заявке. Повторный денежный факт заблокирован.')
    existing=select(Ledger.id).where(Ledger.date==data.date,Ledger.reference==data.reference)
    existing=existing.where(Ledger.account_id==bank.id,Ledger.kind.in_(('out','transfer'))) if action=='withdraw' else existing.where(
        or_(Ledger.to_account_id==cash.id, (Ledger.account_id==cash.id)&(Ledger.kind=='in')))
    if s.scalar(existing.limit(1)) is not None:
        raise HTTPException(409,'Этот банковский или кассовый документ уже есть в операциях. Сначала проверьте связь, повторное проведение запрещено.')
    if action=='withdraw':
        # Imported facts have generated BANK-* ledger references. Their source
        # bank document number is the identity the user enters here, so a
        # reference-only Ledger check cannot detect this already posted debit.
        # Never convert an external fact into a funding group implicitly.
        from .bank_statement_models import BankStatementBatch,BankStatementLine
        source=s.scalar(select(BankStatementLine.id).join(BankStatementBatch,
            BankStatementBatch.id==BankStatementLine.batch_id).where(
            BankStatementBatch.company_id==funding.company_id,BankStatementBatch.account_id==bank.id,
            BankStatementBatch.status=='committed',BankStatementBatch.source_ready.is_(True),
            BankStatementLine.company_id==funding.company_id,BankStatementLine.account_id==bank.id,
            BankStatementLine.direction=='out',BankStatementLine.date==data.date,
            BankStatementLine.document_number==data.reference).limit(1))
        if source is not None:
            raise HTTPException(409,'Этот банковский или кассовый документ уже есть в операциях. Сначала проверьте связь, повторное проведение запрещено.')
    return request,funding,bank,cash


def _checks(s,request,funding,bank,cash,action,data):
    if action=='withdraw':
        from .bank_statement_guard import require_reconciled_for_manual_payment
        require_reconciled_for_manual_payment(s,bank)
        for day in {data.date,today()}:
            if account_balance(s,bank,day)<funding.gross_amount:
                raise HTTPException(409,'На банковском счёте не хватает фактических денег на сумму снятия вместе с комиссией.')
        # Funding is tied to an approved request; other unpaid requests remain
        # forecasts just as with the ordinary final payment. Pending bank debits
        # are never ignored.
        from .bank_statement_guard import pending_bank_debit_hold
        for day in {data.date,today()}:
            if account_balance(s,bank,day)-pending_bank_debit_hold(s,bank,day)<funding.gross_amount:
                raise HTTPException(409,'Недостаточно средств с учётом банковских списаний на проверке.')
        if funding.fee_amount:
            state=budget_state(s,funding.fee_category_id,str(data.date)[:7],bank.currency,funding.fee_amount,request.id)
            if funding.fee_category_id==request.category_id and str(request.due_date)[:7]==str(data.date)[:7]:
                state=budget_state(s,funding.fee_category_id,str(data.date)[:7],bank.currency,funding.fee_amount+request.amount,request.id)
            allowed=funding.approved_fee_overrun if funding.approved_fee_month==str(data.date)[:7] else None
            if allowed is not None:allowed=max(allowed,accepted_fee_overrun(s,funding,str(data.date)[:7],bank.currency))
            if state['over'] and (state['mode']=='hard' or allowed is None or overrun(state)>allowed):
                raise HTTPException(409,'Комиссия превышает утверждённый бюджет. Комментарий бухгалтера не заменяет согласование финансового директора.')
        for leg in (funding.transfer_ledger_id,funding.fee_ledger_id):
            if leg is not None:raise HTTPException(409,'Снятие уже связано с операцией.')
    else:
        transit=get(s,Account,funding.transit_account_id)
        if transit.kind!='transit' or transit.archived:raise HTTPException(409,'Служебный счёт денег в пути недоступен.')
        if any(account_balance(s,transit,day)<funding.net_amount for day in {data.date,today()}):
            raise HTTPException(409,'Денег в пути недостаточно для подтверждения кассы.')
        _require_active_legs(s,funding)


def _require_active_legs(s,funding):
    expected=[(funding.transfer_ledger_id,{'account_id':funding.bank_account_id,'to_account_id':funding.transit_account_id,
        'kind':'transfer','amount':funding.net_amount,'date':funding.withdraw_date,'category_id':None})]
    if funding.fee_amount:
        expected.append((funding.fee_ledger_id,{'account_id':funding.bank_account_id,'to_account_id':None,
            'kind':'out','amount':funding.fee_amount,'date':funding.withdraw_date,'category_id':funding.fee_category_id}))
    elif funding.fee_ledger_id is not None:
        raise HTTPException(409,'Параметры заявки и наличного финансирования расходятся. Нужна проверка.')
    from .services import deleted_ledger_ids
    deleted=deleted_ledger_ids(s)
    for leg,fields in expected:
        if leg is None:raise HTTPException(409,'Параметры заявки и наличного финансирования расходятся. Нужна проверка.')
        entry=get(s,Ledger,leg)
        if any(getattr(entry,key)!=value for key,value in fields.items()) or entry.request_id or entry.receipt_id:
            raise HTTPException(409,'Параметры заявки и наличного финансирования расходятся. Нужна проверка.')
        if entry.reversal_of or s.scalar(select(Ledger.id).where(Ledger.reversal_of==leg)):
            raise HTTPException(409,'Снятие или комиссия отменены. Получение наличных заблокировано.')
        if leg in deleted:raise HTTPException(409,'Снятие или комиссия отменены. Получение наличных заблокировано.')


def _signature(s,user,request,funding,bank,cash,action,data):
    # All journal states relevant to funds, budget, approvals and bank holds are
    # bound to this user-specific preview. Reads never allocate a transit account.
    entries=[(x.id,x.account_id,x.to_account_id,x.kind,x.amount,str(x.date),x.category_id,x.reversal_of,x.reference)
             for x in s.scalars(select(Ledger).order_by(Ledger.id))]
    budgets=[(x.id,x.category_id,x.month,x.currency,x.amount,x.mode) for x in s.scalars(select(Budget).order_by(Budget.id))]
    accounts=[(x.id,x.name,x.kind,x.currency,x.opening,str(x.opening_date),x.archived) for x in s.scalars(select(Account).order_by(Account.id))]
    approvals=[(x.id,x.version,x.status,x.amount,str(x.due_date)) for x in s.scalars(select(PaymentRequest).order_by(PaymentRequest.id))]
    from .bank_statement_models import BankStatementBatch,BankStatementLine
    bankrows=[(x.id,x.status,x.ledger_id) for x in s.scalars(select(BankStatementLine).order_by(BankStatementLine.id))]
    batches=[(x.id,x.status) for x in s.scalars(select(BankStatementBatch).order_by(BankStatementBatch.id))]
    payload={'user':user.id,'company':funding.company_id,'action':action,'request':(request.id,request.version),
             'funding':funding_json(s,funding),'fee_acceptance':(funding.approved_fee_overrun,funding.approved_fee_month),
             'data':data.model_dump(mode='json',exclude={'token','confirm'}),
             'entries':entries,'budgets':budgets,'accounts':accounts,'approvals':approvals,'bankrows':bankrows,'batches':batches}
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return hmac.new(jwt_key().encode(),encoded,sha256).hexdigest()


def _preview(s,user,request_id,action,data):
    request,funding,bank,cash=_context(s,user,request_id,action,data)
    _checks(s,request,funding,bank,cash,action,data)
    return request,funding,bank,cash,{
        'token':_signature(s,user,request,funding,bank,cash,action,data),'action':action,'request_id':request.id,
        'version':funding.version,'state':funding.state,'date':str(data.date),'reference':data.reference,
        'currency':bank.currency,'gross_amount':money(funding.gross_amount),'fee_amount':money(funding.fee_amount),
        'net_amount':money(funding.net_amount),'bank_account':bank.name,'cash_account':cash.name,
        'fee_category_id':funding.fee_category_id,'fee_category_name':get(s,Category,funding.fee_category_id).name}


def _transit(s,user,bank,day):
    # A genuine internal monetary account makes every day balance, including a
    # gap between bank debit and physical cash receipt, reconcile without a
    # fabricated external income or early cash credit.
    name='Деньги в пути · '+bank.currency
    account=s.scalar(select(Account).where(Account.kind=='transit',Account.currency==bank.currency))
    if account is None:
        if s.scalar(select(Account.id).where(Account.name==name)):
            raise HTTPException(409,'Имя служебного счёта занято обычным счётом. Переименуйте обычный счёт.')
        account=Account(name=name,kind='transit',currency=bank.currency,company_id=bank.company_id,
                        opening=0,opening_date=date(2000,1,1),allow_overdraft=False,created_by=user.id)
        s.add(account);s.flush()
        log(s,user,'Создан служебный счёт денег в пути','account',account.id,bank.currency)
    if account.archived:raise HTTPException(409,'Служебный счёт денег в пути недоступен.')
    return account


@router.get('/api/requests/{id}/cash-funding')
def read_funding(id:int,request:Request):
    from .requests_api import visible
    with unit() as s:
        user,_=session_user(s,request);req=visible(s,user,id)
        return funding_json(s,funding_for(s,id),user,req)


@router.post('/api/requests/{id}/cash-funding/{action}/preview')
def preview_funding(id:int,action:str,data:FundingPreview,request:Request):
    if action not in ('withdraw','receive'):raise HTTPException(404,'Этап не найден.')
    with unit() as s:
        user,_=session_user(s,request)
        return _preview(s,user,id,action,data)[4]


@router.post('/api/requests/{id}/cash-funding/{action}/commit')
def commit_funding(id:int,action:str,data:FundingCommit,request:Request):
    if action not in ('withdraw','receive'):raise HTTPException(404,'Этап не найден.')
    if not data.confirm:raise HTTPException(422,'Подтвердите фактически выполненный этап.')
    from .requests_api import request_view
    with unit(True) as s:
        user,_=session_user(s,request)
        req,funding,bank,cash,preview=_preview(s,user,id,action,data)
        if not hmac.compare_digest(data.token,preview['token']):
            raise HTTPException(409,'После проверки данные изменились. Обновите предпросмотр и подтвердите заново.')
        prefix=f'CF-CASH-{funding.id}'
        if action=='withdraw':
            transit=_transit(s,user,bank,data.date)
            transfer=Ledger(account_id=bank.id,to_account_id=transit.id,kind='transfer',amount=funding.net_amount,date=data.date,
                            reference=prefix+'-WITHDRAW',counterparty=req.counterparty,note=data.note,creator_id=user.id)
            s.add(transfer);s.flush()
            funding.transit_account_id=transit.id;funding.transfer_ledger_id=transfer.id
            if funding.fee_amount:
                fee=Ledger(account_id=bank.id,category_id=funding.fee_category_id,kind='out',amount=funding.fee_amount,date=data.date,
                           reference=prefix+'-FEE',counterparty='Банковская комиссия',note=data.note,creator_id=user.id)
                s.add(fee);s.flush();funding.fee_ledger_id=fee.id
            funding.state='in_transit';funding.withdraw_date=data.date;funding.withdraw_reference=data.reference
            validate_running_balance(s,bank);validate_running_balance(s,transit)
        else:
            transit=get(s,Account,funding.transit_account_id)
            entry=Ledger(account_id=transit.id,to_account_id=cash.id,kind='transfer',amount=funding.net_amount,date=data.date,
                         reference=prefix+'-RECEIVE',counterparty=req.counterparty,note=data.note,creator_id=user.id)
            s.add(entry);s.flush();funding.receipt_ledger_id=entry.id
            funding.state='received';funding.receive_date=data.date;funding.receive_reference=data.reference
            validate_running_balance(s,transit);validate_running_balance(s,cash)
        # Changes the request's CAS revision without fabricating an editor or
        # another round of approval. The original cash issue uses its new CAS.
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(req,'decision_note')
        s.flush()
        log(s,user,'Учтено снятие наличных из банка' if action=='withdraw' else 'Наличные получены в кассу','request',req.id,
            json.dumps({'funding_id':funding.id,'state':funding.state,'date':str(data.date),'reference':data.reference,
                        'note':data.note,'gross_amount':money(funding.gross_amount),'net_amount':money(funding.net_amount),
                        'fee_amount':money(funding.fee_amount),'transfer_ledger_id':funding.transfer_ledger_id,
                        'fee_ledger_id':funding.fee_ledger_id,'receipt_ledger_id':funding.receipt_ledger_id},ensure_ascii=False))
        return request_view(s,user,req)
