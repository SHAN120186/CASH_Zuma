"""Persist the author's workflow role without revising completed approvals."""
import json
from sqlalchemy import select, text, or_
from sqlalchemy.orm import Session
from .db import Account, Audit, CashFunding, CompanyUser, Guard, Ledger, PaymentRequest, User


def author_snapshot(s,request):
    """Creation evidence wins over later assignments; admin is never finance."""
    creator=s.get(User,request.creator_id)
    if creator is None:return False,'missing_creator'
    # Old role='finance' alone cannot prove whether a current holding admin
    # acted as staff or merely had a company assignment. Do not invent that
    # exemption; already persisted snapshots survive later role changes.
    if creator.role in ('admin','founder'):return False,'holding_role'
    account=s.get(Account,request.account_id)
    if account is None:return False,'missing_account'
    for action in ('Создана заявка','Действие по заявке: submit'):
        entries=s.scalars(select(Audit).where(Audit.entity=='request',Audit.entity_id==str(request.id),
            Audit.company_id==account.company_id,Audit.user_id==request.creator_id,Audit.action==action).order_by(Audit.id))
        for entry in entries:
            if entry.role:return entry.role=='finance','creation_audit' if action=='Создана заявка' else 'submission_audit'
    member=s.get(CompanyUser,(account.company_id,request.creator_id)) if account else None
    return bool(member and member.role=='finance'),'company_membership'


def snapshot_finance_author(s,request):
    if request.finance_author is None:
        request.finance_author=author_snapshot(s,request)[0]
    return bool(request.finance_author)


def new_finance_author(user):
    from .security import company_role
    acted=getattr(user,'_acted',None)
    return user.role not in ('admin','founder') and (company_role(user)=='finance' or bool(acted and acted[0]=='finance'))


def migrate_finance_authors(engine):
    # Pending requests with only a bookkeeping check have no financial consent
    # yet. Completed and partly financially approved routes remain untouched.
    with Session(engine) as s,s.begin():
        if engine.dialect.name!='sqlite':
            s.execute(text('SELECT pg_advisory_xact_lock(7312801)'))
            s.execute(select(Guard).where(Guard.id==1).with_for_update())
        else:s.execute(text('BEGIN IMMEDIATE'))
        rows=s.scalars(select(PaymentRequest).where(PaymentRequest.finance_author.is_(None),
            PaymentRequest.status=='pending',PaymentRequest.finance_approved_by.is_(None),
            PaymentRequest.approved_by.is_(None),PaymentRequest.id.not_in(select(Ledger.request_id).where(Ledger.request_id.is_not(None))),
            PaymentRequest.id.not_in(select(CashFunding.request_id).where(or_(CashFunding.state!='unfunded',
                CashFunding.transfer_ledger_id.is_not(None),CashFunding.fee_ledger_id.is_not(None),CashFunding.receipt_ledger_id.is_not(None))))))
        for row in rows:
            flag,evidence=author_snapshot(s,row)
            row.finance_author=flag
            if not flag:continue
            account=s.get(Account,row.account_id)
            s.add(Audit(company_id=account.company_id if account else None,user_id=None,
                action='Уточнён маршрут заявки финансового директора',entity='request',entity_id=str(row.id),
                role=None,detail=json.dumps({'finance_author':True,'finance_required':False,
                    'director_required':True,'evidence':evidence,'preserved_checked_by':row.checked_by,
                    'route_policy':row.route_policy,'route_threshold':row.route_threshold},ensure_ascii=False)))
