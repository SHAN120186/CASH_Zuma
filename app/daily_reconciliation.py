"""On-demand company/currency day report. This module never posts money.

The saved view is invalidated by the company's audit revision. Cache writes
are technical settings, not financial facts and do not produce audit events.
"""
import json
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError

from .db import (Account, Audit, CashFunding, CashFundingPaymentGroup, CashFundingPaymentItem,
                 Ledger, PaymentRequest, Setting, now, unit)
from .bank_statement_models import BankStatementBatch, BankStatementLine
from .bank_statement_guard import accounted_line_ids
from .security import session_user, company_role, HOLDING_ROLES
from .services import money, effective_cashflows

router = APIRouter()


def revision(s):
    return list(s.execute(select(func.count(Audit.id), func.max(Audit.id))).one())


def balances(s, accounts, day):
    """Aggregate raw signed journal legs, including reversal legs, in SQL."""
    ids = [a.id for a in accounts]
    if not ids:
        return {}
    source = {row[0]: row[1:] for row in s.execute(select(
        Ledger.account_id,
        func.sum(case((Ledger.kind == 'in', Ledger.amount), else_=-Ledger.amount)),
        func.sum(case(((Ledger.date == day) & (Ledger.kind == 'in'), Ledger.amount), else_=0)),
        func.sum(case(((Ledger.date == day) & (Ledger.kind != 'in'), Ledger.amount), else_=0)),
    ).where(Ledger.account_id.in_(ids), Ledger.date <= day).group_by(Ledger.account_id))}
    target = {row[0]: row[1:] for row in s.execute(select(
        Ledger.to_account_id, func.sum(Ledger.amount),
        func.sum(case((Ledger.date == day, Ledger.amount), else_=0)),
    ).where(Ledger.to_account_id.in_(ids), Ledger.date <= day).group_by(Ledger.to_account_id))}
    result = {}
    for a in accounts:
        if a.opening_date > day:
            result[a.id] = {'opening': 0, 'incoming': 0, 'outgoing': 0, 'closing': 0}
            continue
        net, incoming, outgoing = source.get(a.id, (0, 0, 0))
        added, incoming_transfer = target.get(a.id, (0, 0))
        closing = a.opening + net + added
        incoming += incoming_transfer
        result[a.id] = {'opening': closing-incoming+outgoing, 'incoming': incoming,
                        'outgoing': outgoing, 'closing': closing}
    return result


def build(s, day, currency):
    accounts = list(s.scalars(select(Account).where(Account.currency == currency)))
    by_account = {a.id: a for a in accounts}
    values = balances(s, accounts, day)
    batches = list(s.scalars(select(BankStatementBatch).where(
        BankStatementBatch.status == 'committed', BankStatementBatch.currency == currency,
        BankStatementBatch.period_from <= day, BankStatementBatch.period_to >= day)))
    batch_ids = [b.id for b in batches]
    lines = list(s.scalars(select(BankStatementLine).where(
        BankStatementLine.batch_id.in_(batch_ids), BankStatementLine.date == day))) if batch_ids else []
    banks, problems, statement_rows = [], [], []
    for a in accounts:
        if a.kind != 'bank':
            continue
        account_batches = [b for b in batches if b.account_id == a.id]
        account_lines = [line for line in lines if line.account_id == a.id]
        included = [line for line in account_lines if line.include_in_daily]
        backed = accounted_line_ids(s, included, a)
        pending = [line for line in included if line.id not in backed or line.uncertain]
        uncertain = any(line.uncertain for line in account_lines)
        # A multi-day statement does not prove an individual day's opening or
        # closing. Never invent it from the application's own ledger.
        exact = [b for b in account_batches if b.period_from == day and b.period_to == day
                 and b.source_ready and b.balance_matches and b.declared_totals_match is not False]
        anchors = {(b.opening, b.closing) for b in exact}
        bank_opening, bank_closing = next(iter(anchors)) if len(anchors) == 1 else (None, None)
        credit = sum(line.amount for line in included if line.direction == 'in')
        debit = sum(line.amount for line in included if line.direction == 'out')
        valid_totals = bank_opening is not None and bank_opening+credit-debit == bank_closing
        ledger = values[a.id]
        complete = bool(account_batches and valid_totals and not pending and not uncertain
                        and ledger['opening'] == bank_opening and ledger['closing'] == bank_closing)
        banks.append({'id': a.id, 'name': a.name, **{k: money(v) for k, v in ledger.items()},
            'available': bool(account_batches), 'bank_opening': money(bank_opening) if bank_opening is not None else None,
            'bank_closing': money(bank_closing) if bank_closing is not None else None,
            'credit': money(credit) if account_batches else None, 'debit': money(debit) if account_batches else None, 'statement_count': len(account_batches),
            'line_count': len(included), 'pending_count': len(pending), 'uncertain': uncertain,
            'difference': money(ledger['closing']-bank_closing) if bank_closing is not None else None,
            'reconciled': complete})
        for line in included:
            entry = {'id': line.id, 'batch_id': line.batch_id, 'account': a.name,
                'direction': line.direction, 'amount': money(line.amount), 'counterparty': line.correspondent_name,
                'purpose': line.purpose, 'reference': line.document_number, 'status': line.status,
                'request_id': line.request_id, 'accounted': line.id in backed and not line.uncertain,
                'reason': line.reason}
            statement_rows.append(entry)
            if not entry['accounted']:
                problems.append(entry)
    effective = list(s.scalars(effective_cashflows(s).where(Ledger.account_id.in_(by_account), Ledger.date == day))) if accounts else []
    fundings = list(s.scalars(select(CashFunding).where(CashFunding.bank_account_id.in_(by_account)))) if accounts else []
    batch_requests = {f.request_id for f in fundings if f.mode == 'batch'}
    planned = list(s.scalars(select(PaymentRequest).where(PaymentRequest.account_id.in_(by_account),
        PaymentRequest.due_date == day, PaymentRequest.status.notin_(['deleted', 'cancelled', 'rejected', 'draft'])))) if accounts else []
    planned = [r for r in planned if r.id not in batch_requests]
    planned_ids = {r.id for r in planned}
    payments = [e for e in effective if e.request_id and e.kind == 'out' and e.request_id not in batch_requests]
    paid_ids = {e.request_id for e in payments}
    planned_paid = [e for e in payments if e.request_id in planned_ids]
    all_ids = {v for f in fundings for v in (f.transfer_ledger_id, f.fee_ledger_id, f.receipt_ledger_id) if v}
    active_funding_legs = {e.id: e for e in s.scalars(effective_cashflows(s).where(Ledger.id.in_(all_ids), Ledger.date <= day))} if all_ids else {}
    funding_rows = []
    issued = dict(s.execute(select(CashFundingPaymentGroup.funding_id, func.sum(CashFundingPaymentItem.amount))
        .join(CashFundingPaymentItem, CashFundingPaymentItem.group_id == CashFundingPaymentGroup.id)
        .where(CashFundingPaymentGroup.funding_id.in_([f.id for f in fundings]),
               CashFundingPaymentGroup.date <= day)
        .group_by(CashFundingPaymentGroup.funding_id)).all()) if fundings else {}
    request_ids = {f.request_id for f in fundings}
    single_issued = {entry.request_id: entry.amount for entry in s.scalars(effective_cashflows(s).where(
        Ledger.request_id.in_([f.request_id for f in fundings if f.mode == 'single']),
        Ledger.kind == 'out', Ledger.date <= day))} if fundings else {}
    request_names = {r.id: r.counterparty for r in s.scalars(select(PaymentRequest).where(PaymentRequest.id.in_(request_ids)))} if request_ids else {}
    for f in fundings:
        withdrawn = f.transfer_ledger_id in active_funding_legs
        received = f.receipt_ledger_id in active_funding_legs
        if not withdrawn and not received:
            continue
        issued_amount = issued.get(f.id, 0) if f.mode == 'batch' else single_issued.get(f.request_id, 0)
        funding_rows.append({'request_id': f.request_id, 'source': f.source,
            'counterparty': f.intermediary_name if f.source == 'counterparty' else request_names.get(f.request_id, ''),
            'bank': by_account[f.bank_account_id].name, 'cash': by_account.get(f.cash_account_id).name if f.cash_account_id in by_account else '',
            'gross': money(f.gross_amount), 'fee': money(f.fee_amount), 'net': money(f.net_amount),
            'withdraw_date': str(f.withdraw_date), 'receive_date': str(f.receive_date) if received else None,
            'received': received, 'in_transit': money(f.net_amount if withdrawn and not received else 0),
            'issued': money(issued_amount),
            'remaining': money((f.net_amount if received else 0)-issued_amount)})
    fee_ids = {f.fee_ledger_id for f in fundings}
    partner_fee_ids = {f.fee_ledger_id for f in fundings if f.source == 'counterparty'}
    fee_lines = {line.ledger_id for line in lines if line.status in ('auto_fee_posted', 'fee_matched')}
    interest_lines = {line.ledger_id for line in lines if line.status in ('auto_interest_posted', 'interest_matched')}
    return {'date': str(day), 'currency': currency, 'generated_at': now().isoformat(),
        'basis': 'current_corrected_ledger', 'banks': banks, 'statement_rows': statement_rows, 'review': problems,
        'cash': [{'id': a.id, 'name': a.name, **{k: money(v) for k, v in values[a.id].items()}}
                 for a in accounts if a.kind == 'cash'],
        'fundings': funding_rows,
        'requests': {'planned_count': len(planned), 'planned_amount': money(sum(r.amount for r in planned)),
            'paid_count': len(paid_ids), 'paid_amount': money(sum(e.amount for e in payments)),
            'planned_paid_count': len({e.request_id for e in planned_paid}),
            'planned_paid_amount': money(sum(e.amount for e in planned_paid)),
            'other_date_paid_count': len(paid_ids-planned_ids),
            'remaining_count': len([r for r in planned if r.status != 'paid' and r.id not in paid_ids])},
        'bank_fees': money(sum(e.amount for e in effective if e.id in ((fee_lines | fee_ids)-partner_fee_ids))),
        'partner_fees': money(sum(e.amount for e in effective if e.id in partner_fee_ids)),
        'interest': money(sum(e.amount for e in effective if e.id in interest_lines))}


@router.get('/api/bank-statements/reconciliation-day')
def reconciliation_day(request: Request, date: date, currency: str = Query(pattern=r'^(UZS|USD|EUR)$')):
    with unit() as s:
        user, _ = session_user(s, request, ('view', 'pay_bank'))
        if user.role not in HOLDING_ROLES and company_role(user) not in ('finance', 'director', 'accountant'):
            raise HTTPException(403, 'Дневная сверка доступна финансовой службе и руководству.')
        company_id = s.info.get('company_id')
        if company_id is None:
            raise HTTPException(403, 'Выберите компанию.')
        stamp = revision(s)
        key = f'dailyrec:1:{company_id}:{date}:{currency}'
        cached = s.get(Setting, key)
        if cached:
            try:
                saved = json.loads(cached.value)
                if saved['revision'] == stamp:
                    return {**saved['payload'], 'cached': True}
            except (KeyError, TypeError, ValueError):
                pass
        payload = build(s, date, currency)
        if revision(s) != stamp:
            raise HTTPException(409, 'Данные изменились во время сверки. Обновите отчёт.')
        encoded = json.dumps({'revision': stamp, 'payload': payload}, ensure_ascii=False)
        if cached:
            cached.value = encoded
        else:
            try:
                with s.begin_nested():
                    s.add(Setting(key=key, value=encoded))
                    s.flush()
            except IntegrityError:
                pass  # Another reader saved this view; money was never touched.
        return {**payload, 'cached': False}
