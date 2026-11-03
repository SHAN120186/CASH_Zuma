"""Reconsider exact approved-request payments in an already confirmed statement.

This module never grants write or payment rights. Preview is read-only. The
caller must check the preview token and call the processor inside the shared
write transaction and the *whole-statement* reconciliation savepoint.
"""
import re
from collections import Counter
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select

from .bank_statement_models import BankStatementBatch, BankStatementLine
from .db import Ledger, PaymentRequest
from .requests_api import can_view_request
from .section_access import can_edit_section
from .security import perms_of
from .services import effective_cashflows, get, money, post_ledger


REQUEST_ID = re.compile(r'(?<![A-Za-z0-9])CF-0*([1-9][0-9]*)(?![A-Za-z0-9])', re.I)


def classify_reprocess_payments(s, batch, lines, account, user):
    """Find only uniquely matched still-approved requests; never mutate ORM rows."""
    if (batch.status != 'committed' or not batch.source_ready or batch.account_id != account.id
            or batch.company_id != account.company_id or account.archived or account.kind != 'bank'
            or account.currency != batch.currency or 'pay_bank' not in perms_of(user)
            or not can_edit_section(user, 'requests', 'ledger')):
        return {}
    sections = getattr(user, '_sections', None)
    if sections is not None and sections.get('requests') == 'hidden':
        return {}
    requests = list(s.scalars(select(PaymentRequest).where(PaymentRequest.account_id == account.id)))
    days = {line.date for line in lines if line.direction == 'out'}
    debits = [(entry.date, entry.amount) for entry in s.scalars(effective_cashflows(s).where(
        Ledger.account_id == account.id, Ledger.kind.in_(('out', 'transfer')), Ledger.date.in_(days)))] if days else []
    previous = list(s.scalars(select(BankStatementLine).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(
        BankStatementBatch.status == 'committed', BankStatementLine.account_id == account.id,
        BankStatementLine.batch_id != batch.id)))
    old_request_ids = {other.request_id for other in previous if other.request_id is not None}
    old_request_ids.update(line.request_id for line in lines if line.request_id is not None)
    all_lines = list(lines) + previous
    # All statuses, including hidden and already associated records, count
    # towards ambiguity, just as on the first statement import.
    pairs = {line.id: [req for req in requests if line.direction == 'out' and line.correspondent_inn
             and req.counterparty_inn == line.correspondent_inn and req.amount == line.amount
             and req.due_date == line.date] for line in all_lines}
    reverse = Counter(req.id for values in pairs.values() for req in values)
    fingerprints = Counter(line.fingerprint for line in all_lines)
    result = {}
    for line in lines:
        if (line.batch_id != batch.id or line.company_id != batch.company_id or
                line.account_id != account.id or line.direction != 'out' or
                line.status not in ('review', 'reviewed') or line.classification not in ('', 'unresolved') or
                line.uncertain or not line.include_in_daily or line.request_id is not None or line.ledger_id is not None or
                fingerprints[line.fingerprint] != 1 or (line.date, line.amount) in debits):
            continue
        choices = pairs[line.id]
        if len(choices) != 1 or reverse[choices[0].id] != 1:
            continue
        req = choices[0]
        bank_ids = {int(value) for value in REQUEST_ID.findall(line.purpose or '')}
        if (len(bank_ids) > 1 or bank_ids and req.id not in bank_ids or req.status != 'approved' or
                req.id in old_request_ids or not can_view_request(s, user, req)):
            continue
        result[line.id] = {'status': 'auto_pay_candidate', 'reason': '',
                           'request_id': req.id, 'request_version': req.version}
    return result


def process_reprocess_payments(s, batch, lines, account, user, approved):
    """Post only candidates shown in the signed preview; caller owns savepoint."""
    count = 0
    for line in sorted(lines, key=lambda row: (row.date, row.line, row.id)):
        shown = approved.get(line.id, {})
        if shown.get('status') != 'auto_pay_candidate':
            continue
        fresh = classify_reprocess_payments(s, batch, lines, account, user).get(line.id)
        if not fresh or any(fresh.get(key) != shown.get(key)
                            for key in ('status', 'request_id', 'request_version')):
            raise HTTPException(409, 'Заявка или выписка изменились. Проверьте их заново.')
        req = get(s, PaymentRequest, fresh['request_id'])
        op = SimpleNamespace(account_id=account.id, to_account_id=None, category_id=req.category_id,
            kind='out', amount=money(line.amount), date=line.date, counterparty=req.counterparty,
            reference='BANK-' + line.fingerprint, note='Банковская выписка, документ ' + line.document_number,
            request_id=req.id, request_version=fresh['request_version'], receipt_id=None)
        entry = post_ledger(s, user, op, bank_statement_fact=True)
        line.request_id = req.id; line.ledger_id = entry.id; line.status = 'auto_paid'; line.reason = ''
        s.flush()
        count += 1
    return count
