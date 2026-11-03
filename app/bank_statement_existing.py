"""Link bank lines to already-recorded independent cash operations.

Matching is read-only.  Linking never creates a ledger row or changes a cash
amount.  The caller must re-run it under ``unit(True)`` following an explicit
statement confirmation, and must reconcile the statement's closing balance.
"""
from collections import Counter
import re

from fastapi import HTTPException
from sqlalchemy import select

from .bank_statement_fees import SWIFT_COMMISSION
from .bank_statement_models import BankStatementBatch, BankStatementLine
from .db import Ledger, Receipt
from .security import perms_of
from .section_access import can_edit_section
from .services import deleted_ledger_ids, log

REQUEST_NUMBER = re.compile(r'(?<![A-Za-z0-9])CF-0*[1-9][0-9]*(?![A-Za-z0-9])', re.I)


def _name(value):
    return ' '.join((value or '').casefold().split())


def _document_key(line):
    return (line.account_id, line.date, line.direction, line.document_number)


def classify_existing(s, batch, lines, account, user, opening_ok):
    """Return decisions keyed by line ID, without writing to the database.

    Only an exact unique document reference and counterparty, account, date,
    amount and direction can link a line to an existing independent operation.
    Prior links, cancelled operations, disputed duplicates and potential plans
    are never resolved by a guess.  All queries use the selected company.
    """
    if (batch.account_id != account.id or batch.company_id != account.company_id
            or account.archived or account.kind != 'bank' or account.currency != batch.currency):
        raise HTTPException(409, 'Счёт или валюта изменились. Проверьте выписку.')
    decisions = {}
    allowed = bool({'write', 'pay_bank'} & perms_of(user)) and can_edit_section(user, 'ledger')
    rows = [line for line in lines if line.direction in ('in', 'out') and
            line.status in ('review', 'reviewed', 'other_matched_candidate')]
    current_ids = {line.id for line in lines}
    days = {line.date for line in rows}
    if not days:
        return decisions
    previous = list(s.scalars(select(BankStatementLine).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(
            BankStatementBatch.status == 'committed', BankStatementLine.account_id == account.id,
            BankStatementLine.date.in_(days))))
    context = list(lines) + [line for line in previous if line.id not in current_ids]
    doc_counts = Counter(_document_key(line) for line in context if line.document_number)
    ledger = list(s.scalars(select(Ledger).where(Ledger.account_id == account.id,
        Ledger.kind.in_(('in','out')), Ledger.date.in_(days))))
    reversed_ids = set(s.scalars(select(Ledger.reversal_of).where(Ledger.reversal_of.is_not(None))))
    deleted_ids = deleted_ledger_ids(s)
    linked = {}
    for line in context:
        if line.ledger_id:
            linked.setdefault(line.ledger_id, set()).add(line.id)
    receipts = list(s.scalars(select(Receipt).where(Receipt.account_id == account.id,
        Receipt.status.in_(('expected','received')))))
    edges = {}
    for line in rows:
        result = {'status':'review', 'reason':'', 'ledger_id':None, 'category_id':None}
        decisions[line.id] = result
        if (not batch.source_ready or not opening_ok or line.uncertain or line.batch_id != batch.id
                or line.company_id != batch.company_id or line.account_id != account.id or not allowed):
            result['reason'] = 'Счёт, права или выписка требуют проверки.'; continue
        if line.classification not in ('', 'unresolved', 'other', 'receipt'):
            result['reason'] = 'Классификацию операции нужно проверить.'; continue
        if line.direction == 'in' and line.classification == 'other':
            result['reason'] = 'Ранее выбранное назначение поступления требует проверки.'; continue
        if line.direction == 'out' and line.classification == 'receipt':
            result['reason'] = 'Ранее выбранное назначение списания требует проверки.'; continue
        if line.request_id or REQUEST_NUMBER.search(line.purpose or '') or (
                line.direction == 'out' and SWIFT_COMMISSION.search(line.purpose or '')):
            result['reason'] = 'Операция относится к заявке или комиссии.'; continue
        if not batch.owner_inn or not line.correspondent_inn or line.correspondent_inn == batch.owner_inn:
            result['reason'] = 'Возможный собственный перевод требует проверки.'; continue
        if not line.document_number or not line.correspondent_name or doc_counts[_document_key(line)] != 1:
            result['reason'] = 'Номер документа повторяется или не заполнен.'; continue
        if line.direction == 'in' and any(r.inn and r.inn == line.correspondent_inn
                and r.amount == line.amount and (r.status == 'expected' or abs((r.due_date-line.date).days) <= 7)
                for r in receipts):
            result['reason'] = 'Возможна связь с планом поступления.'; continue
        refs = (line.document_number, 'BANK-' + line.fingerprint)
        possible = [entry for entry in ledger if entry.kind == line.direction and entry.date == line.date
                    and entry.reference in refs]
        # A document with different amount or name is ambiguous, not a new row.
        if len(possible) != 1:
            result['reason'] = 'Нет единственной операции с этим документом.'; continue
        entry = possible[0]
        if (entry.amount != line.amount or not _name(entry.counterparty)
                or _name(entry.counterparty) != _name(line.correspondent_name)
                or entry.request_id is not None or entry.receipt_id is not None
                or entry.reversal_of is not None or entry.id in reversed_ids or entry.id in deleted_ids
                or bool(linked.get(entry.id,set()) - {line.id})):
            result['reason'] = 'Операция отличается, отменена или уже связана.'; continue
        edges.setdefault(entry.id, []).append(line.id)
        result.update(status='other_matched_candidate', ledger_id=entry.id, category_id=entry.category_id)
    # Guard one existing Ledger against multiple competing rows even if those
    # rows use different representations of the reference.
    for entry_id, ids in edges.items():
        if len(ids) > 1:
            for line_id in ids:
                decisions[line_id].update(status='review', reason='Несколько строк подходят к одной операции.',
                                          ledger_id=None, category_id=None)
    return decisions


def process_existing(s, batch, lines, account, user, opening_ok, approved=None):
    """Link candidates, only; caller owns the write lock and savepoint."""
    decisions = classify_existing(s, batch, lines, account, user, opening_ok)
    counts = Counter()
    for line in lines:
        decision = decisions.get(line.id)
        if not decision or decision['status'] != 'other_matched_candidate':
            continue
        if approved is not None:
            from .bank_statement_incoming import approved_decision_matches
            if not approved_decision_matches(decision,approved.get(line.id),('other_matched_candidate',)):
                line.status='review';line.reason='Предпросмотр изменился: проверьте операцию заново.'
                counts['review'] += 1
                continue
        line.ledger_id = decision['ledger_id']
        line.status = 'other_matched'
        if not line.classification or line.classification == 'unresolved':
            line.classification = 'other' if line.direction == 'out' else 'receipt'
        line.reason = ''
        log(s, user, 'Связана выписка с ранее проведённой операцией', 'bank_statement_line',
            line.id, f"операция {line.ledger_id}")
        counts['other_matched'] += 1
    return dict(counts)
