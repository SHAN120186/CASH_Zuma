"""Paid requests on days with no verified bank statement saved for the account."""
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from .bank_period_comparison import _binding, _context, currency_param
from .bank_statement_identity import check_statement_currency
from .bank_statement_models import BankStatementBatch
from .daily_reconciliation import revision
from .db import Ledger, PaymentRequest, unit
from .requests_api import can_view_request
from .services import deleted_ledger_ids, effective_cashflows, money

router = APIRouter(prefix='/api/bank-statements')


def _covered_dates(s, account, start, end):
    statements = s.scalars(select(BankStatementBatch).where(
        BankStatementBatch.account_id == account.id, BankStatementBatch.company_id == account.company_id,
        BankStatementBatch.currency == account.currency, BankStatementBatch.status == 'committed',
        BankStatementBatch.source_ready.is_(True), BankStatementBatch.balance_matches.is_(True),
        BankStatementBatch.declared_totals_match.is_(True),
        BankStatementBatch.period_from <= end, BankStatementBatch.period_to >= start))
    days = set()
    verified = set()
    for statement in statements:
        if statement.opening + statement.credit_total - statement.debit_total != statement.closing:
            continue
        identity = (statement.account_number, statement.owner_inn)
        if identity not in verified:
            metadata = {'account_number': statement.account_number, 'owner_inn': statement.owner_inn,
                        'owner_name': statement.owner_name, 'currency': statement.currency}
            # If the account's historical bindings conflict, showing any definitive
            # coverage would be misleading. Surface the same conflict as normal comparison.
            check_statement_currency(account, metadata)
            if _binding(s, account, metadata) != 'confirmed':
                continue
            verified.add(identity)
        first, last = max(start, statement.period_from), min(end, statement.period_to)
        days.update(first + timedelta(days=i) for i in range((last-first).days + 1))
    return days


@router.get('/uncovered-payments')
def uncovered_payments(request: Request, account_id: int, date_from: date, date_to: date,
                       currency: str = currency_param):
    with unit() as s:
        user, account = _context(s, request, account_id, currency)
        if not 0 <= (date_to-date_from).days < 92:
            raise HTTPException(422, 'Выберите период от 1 до 92 дней.')
        stamp = revision(s)
        covered = _covered_dates(s, account, date_from, date_to)
        query = effective_cashflows(s).add_columns(PaymentRequest).join(
            PaymentRequest, PaymentRequest.id == Ledger.request_id).where(
            Ledger.account_id == account.id, Ledger.kind == 'out', Ledger.date.between(date_from, date_to),
            PaymentRequest.account_id == account.id, PaymentRequest.status == 'paid')
        if covered:
            query = query.where(Ledger.date.not_in(covered))
        deleted = deleted_ledger_ids(s)
        rows = [{'request_id': req.id, 'date': str(entry.date), 'document_number': entry.reference,
                 'amount': money(entry.amount), 'counterparty': req.counterparty}
                for entry, req in s.execute(query.order_by(Ledger.date, Ledger.id))
                if entry.id not in deleted and can_view_request(s, user, req)]
        if revision(s) != stamp:
            raise HTTPException(409, 'Данные изменились во время сверки. Обновите список оплат.')
        return {'account': {'id': account.id, 'name': account.name, 'currency': account.currency},
                'date_from': date_from, 'date_to': date_to, 'rows': rows}
