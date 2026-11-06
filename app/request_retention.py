"""Explicit removal of unpaid requests; financial facts are never removed here.

Maintenance callers must select one company and use unit(True) for an apply.
Nothing in this module runs during startup or purges data automatically.
"""
import json

from fastapi import HTTPException
from sqlalchemy import func, select

from .bank_statement_models import BankStatementLine
from .db import (Account, Audit, CashFunding, CashFundingPaymentGroup,
                 CashFundingPaymentItem, Ledger, PaymentRequest, RequestDocument, Setting)
from .security import require_permission
from .services import log, request_journal


ID_HIGH_WATER_KEY = 'request_id_high_water'
MAX_REQUEST_ID = 9223372036854775807


def _sqlite_floor(s):
    """Global numbering survives deleting the last SQLite row in any company."""
    row=s.get(Setting,ID_HIGH_WATER_KEY)
    existing=s.scalar(select(func.max(PaymentRequest.id)).execution_options(company_unscoped=True)) or 0
    floor=max(int(row.value) if row else 0,existing)
    return row,floor


def next_request_id(s):
    """Called only inside the existing serialized write transaction."""
    if s.bind.dialect.name!='sqlite':return None  # PostgreSQL keeps its sequence.
    row,floor=_sqlite_floor(s)
    if floor>=MAX_REQUEST_ID:raise HTTPException(409,'Исчерпан диапазон номеров заявок.')
    if row is None:
        row=Setting(key=ID_HIGH_WATER_KEY);s.add(row)
    row.value=str(floor+1)
    return floor+1


def _preserve_request_ids(s):
    if s.bind.dialect.name=='sqlite':
        row,floor=_sqlite_floor(s)
        if row is None:
            row=Setting(key=ID_HIGH_WATER_KEY);s.add(row)
        row.value=str(floor)


def removal_blockers(s,r):
    """Conservative checks include preview links: bank review is never changed."""
    blockers=[]
    if s.scalar(select(Ledger.id).where(Ledger.request_id==r.id).limit(1).execution_options(company_unscoped=True)) is not None:
        blockers.append('По заявке есть денежная операция. Удаление не должно менять или разрывать денежные факты.')
    funding=s.scalar(select(CashFunding).where(CashFunding.request_id==r.id).execution_options(company_unscoped=True))
    if funding and (funding.company_id!=s.info.get('company_id') or funding.state!='unfunded' or funding.withdraw_date or funding.receive_date or
                    funding.transfer_ledger_id or funding.fee_ledger_id or funding.receipt_ledger_id):
        blockers.append('По заявке учтено снятие или получение наличных. Денежные операции и комиссия должны сохраняться.')
    groups=s.scalar(select(CashFundingPaymentItem.id).where(CashFundingPaymentItem.request_id==r.id).limit(1).execution_options(company_unscoped=True)) is not None
    if funding and s.scalar(select(CashFundingPaymentGroup.id).where(CashFundingPaymentGroup.funding_id==funding.id).limit(1).execution_options(company_unscoped=True)) is not None:
        groups=True
    if groups:blockers.append('Заявка связана с неизменяемой группой наличной оплаты.')
    if s.scalar(select(BankStatementLine.id).where(BankStatementLine.request_id==r.id).limit(1).execution_options(company_unscoped=True)) is not None:
        blockers.append('Заявка связана со строкой банковской выписки. Сначала разберите эту связь в выписке; денежные факты не удаляются.')
    return blockers


def permanently_delete_unpaid_request(s,user,r):
    """The HTTP action has already checked ownership, version and explanation."""
    if r.status not in ('draft','pending','approved','returned','cancelled'):
        raise HTTPException(409,'Полностью удалить можно только неоплаченную заявку.')
    account=s.get(Account,r.account_id)
    if account is None or s.info.get('company_id')!=account.company_id:
        raise HTTPException(404,'Запись не найдена в выбранной компании.')
    blockers=removal_blockers(s,r)
    if blockers:raise HTTPException(409,' '.join(blockers))
    rid=r.id
    _preserve_request_ids(s)
    # Flush dependants first: all FK constraints remain enabled.
    for doc in s.scalars(select(RequestDocument).where(RequestDocument.request_id==rid)):
        s.delete(doc)
    funding=s.scalar(select(CashFunding).where(CashFunding.request_id==rid))
    if funding is not None:s.delete(funding)
    s.flush()
    prior=s.info.get('audit_maintenance')
    try:
        # IDs are global. Old file events may have NULL/UNASSIGNED ownership;
        # Core DELETE avoids treating that legacy attribution as a company move.
        # The exact journal predicate removes only this authorized request's
        # business history. Other audit rows keep their normal immutable guard.
        s.info['audit_maintenance']=True
        s.execute(Audit.__table__.delete().where(request_journal(rid)))
    finally:
        if prior is None:s.info.pop('audit_maintenance',None)
        else:s.info['audit_maintenance']=prior
    s.delete(r);s.flush()
    # Keep who/when/company and a count; no request ID, contents, files or reason.
    log(s,user,'Полностью удалена неоплаченная заявка','request_retention',detail=json.dumps({'count':1}))
    return {'deleted':True,'permanent':True,'id':rid}


def purge_cancelled_requests(s,user,*,company_id,dry_run=True,expected_versions=None):
    """Review first; apply only the identical eligible ID/version snapshot.

    Return identifiers/counts/blockers, never request contents. The caller owns
    backup, transaction and commit. A selected company is mandatory even for
    maintenance; no HTTP or startup hook invokes this helper.
    """
    require_permission(user,'request_delete')
    if s.info.get('company_id')!=company_id:
        raise HTTPException(409,'Для очистки явно выберите одну компанию.')
    rows=list(s.scalars(select(PaymentRequest).join(Account,Account.id==PaymentRequest.account_id)
                       .where(Account.company_id==company_id,PaymentRequest.status=='cancelled')
                       .order_by(PaymentRequest.id)))
    report={'company_id':company_id,'dry_run':bool(dry_run),'eligible':[],'blocked':[]}
    for r in rows:
        reasons=removal_blockers(s,r)
        item={'id':r.id,'version':r.version}
        if reasons:
            report['blocked'].append({**item,'reasons':reasons})
        else:
            item['documents']=s.scalar(select(func.count(RequestDocument.id)).where(RequestDocument.request_id==r.id))
            item['history']=s.scalar(select(func.count(Audit.id)).where(request_journal(r.id)).execution_options(company_unscoped=True))
            report['eligible'].append(item)
    if dry_run:return report
    expected={item['id']:item['version'] for item in report['eligible']}
    if expected_versions is None or expected_versions!=expected:
        raise HTTPException(409,'Состав или версии закрытых заявок изменились. Повторите предварительную проверку.')
    for r in rows:
        if r.id in expected:permanently_delete_unpaid_request(s,user,r)
    report['deleted']=len(expected)
    return report
