"""Read-only explanation of every bank debit, independent of posting gates.

The bank row is the amount authority. This module never creates an operation,
changes a request, or turns a classification guess into a payment instruction.
"""
from collections import Counter, defaultdict
import re

from sqlalchemy import or_, select

from .bank_statement_fees import SWIFT_COMMISSION, exact_iabs_fee
from .bank_statement_models import BankStatementBatch, BankStatementLine
from .db import Account, Category, Ledger, PaymentRequest
from .requests_api import can_view_request
from .services import deleted_ledger_ids, effective_cashflows


REQUEST_NUMBER = re.compile(r'(?<![A-Za-z0-9])CF-0*([1-9][0-9]*)(?![A-Za-z0-9])', re.I)
EXPLICIT_BANK_FEE = re.compile(
    r'комисси\w*\s+(?:банка|банк\w*|за\s+(?:банков|расч[её]тно))|'
    r'банковск\w*\s+(?:комисси|услуг|обслуж)|'
    r'расч[её]тно[-\s]кассов\w*\s+обслуж|\bbank\s+(?:fee|charge)\b', re.I)
LOAN_INTEREST = re.compile(
    r'^\s*01014\s*(?:кредит\s*)?фоиз\w*(?:\s|$)|процент\w*.{0,25}(?:кредит|займ)|'
    r'(?:кредит|займ).{0,25}процент|\bloan\s+interest\b', re.I)


def _fee_evidence(batch, line):
    """Do not confuse a supplier's '100%' or 'Ком. услуги' with a bank fee."""
    own_inn = bool(batch.owner_inn and line.correspondent_inn == batch.owner_inn)
    if line.classification == 'bank_fee' and line.reviewed_by is not None:
        return 'bank_fee', 'reviewed_bank_fee'
    if own_inn and SWIFT_COMMISSION.search(line.purpose or ''):
        return 'bank_fee', 'explicit_swift_commission'
    if own_inn and LOAN_INTEREST.search(line.purpose or ''):
        return 'bank_interest', 'explicit_loan_interest'
    if own_inn and EXPLICIT_BANK_FEE.search(line.purpose or ''):
        return 'bank_fee', 'explicit_bank_fee'
    if exact_iabs_fee(batch, line) and (line.reviewed_by is None or line.classification == 'bank_fee'):
        return 'bank_fee', 'explicit_iabs_fee'
    return None


def expense_annotations(s, batch, lines, user):
    """SELECT-only metadata keyed by debit line ID; same-company data only.

    Unapproved and hidden requests participate in ambiguity checks. Only a
    visible, approved or actively paid unique request exposes its article.
    """
    debits = [line for line in lines if line.direction == 'out']
    if not debits:
        return {}
    account_id = batch.account_id
    days = {line.date for line in debits}
    amounts = {line.amount for line in debits}
    requests = list(s.scalars(select(PaymentRequest).where(
        PaymentRequest.account_id == account_id, PaymentRequest.amount.in_(amounts))))
    categories = {item.id: item for item in s.scalars(select(Category).where(
        Category.company_id == batch.company_id))}
    deleted = deleted_ledger_ids(s)
    ledger = [entry for entry in s.scalars(effective_cashflows(s).where(
        or_(Ledger.account_id == account_id, Ledger.to_account_id == account_id),
        Ledger.date.in_(days), Ledger.amount.in_(amounts))) if entry.id not in deleted]
    entries = {entry.id: entry for entry in ledger}
    actual = defaultdict(list)
    for entry in ledger:
        if entry.account_id == account_id and entry.kind == 'out' and entry.request_id is not None:
            actual[entry.request_id].append(entry)

    def strict(line, req):
        if not line.correspondent_inn or req.counterparty_inn != line.correspondent_inn or req.amount != line.amount:
            return False
        if req.status == 'paid':
            return len([entry for entry in actual[req.id]
                        if entry.amount == line.amount and entry.date == line.date]) == 1
        return req.due_date == line.date

    pairs = {line.id: [req for req in requests if strict(line, req)] for line in debits}
    reverse = Counter(req.id for matches in pairs.values() for req in matches)
    prior = list(s.scalars(select(BankStatementLine).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(
            BankStatementBatch.status == 'committed', BankStatementBatch.id != batch.id,
            BankStatementLine.account_id == account_id, BankStatementLine.direction == 'out',
            or_(BankStatementLine.date.in_(days), BankStatementLine.request_id.in_([r.id for r in requests])))))
    for line in prior:
        for req in requests:
            if strict(line, req) or line.request_id == req.id:
                reverse[req.id] += 1
    fingerprints = Counter(line.fingerprint for line in debits)
    prior_fingerprints = {line.fingerprint for line in prior}
    sections = getattr(user, '_sections', None)
    requests_visible = sections is None or sections.get('requests') != 'hidden'
    visible = {req.id for req in requests if requests_visible and can_view_request(s, user, req)}
    own_accounts = {account.id for account in s.scalars(select(Account).where(
        Account.company_id == batch.company_id, Account.currency == batch.currency, Account.kind == 'bank'))}
    bindings = defaultdict(set)
    for confirmed in s.scalars(select(BankStatementBatch).where(
        BankStatementBatch.company_id == batch.company_id, BankStatementBatch.currency == batch.currency,
        BankStatementBatch.status == 'committed', BankStatementBatch.source_ready.is_(True))):
        if confirmed.account_id in own_accounts:
            bindings[confirmed.account_number].add(confirmed.account_id)

    result = {}
    for line in debits:
        row = {'expense_kind': 'unresolved', 'expense_reason': None, 'expense_evidence': None,
               'expense_request_id': None, 'expense_request_status': None,
               'expense_category_id': None, 'expense_category_name': None, 'expense_category_code': None,
               'expense_existing_operation': False}
        result[line.id] = row
        entry = entries.get(line.ledger_id)
        entry_matches = bool(entry and entry.date == line.date and entry.amount == line.amount and
            entry.account_id == account_id and entry.kind in ('out', 'transfer'))
        row['expense_existing_operation'] = entry_matches
        if line.uncertain or fingerprints[line.fingerprint] > 1 or line.fingerprint in prior_fingerprints:
            row['expense_reason'] = 'duplicate_or_overlap'; continue
        if line.ledger_id is not None and not entry_matches:
            row['expense_reason'] = 'linked_operation_changed'; continue
        candidates = pairs[line.id]
        marker = {int(value) for value in REQUEST_NUMBER.findall(line.purpose or '')}
        broad_requests = [req for req in requests if req.amount == line.amount and
                          (not line.correspondent_inn or not req.counterparty_inn or
                           req.counterparty_inn == line.correspondent_inn)]
        if len(marker) > 1 or marker and (len(candidates) != 1 or candidates[0].id not in marker):
            row['expense_reason'] = 'request_reference_conflict'; continue
        if candidates:
            if len(candidates) != 1 or reverse[candidates[0].id] != 1:
                row['expense_reason'] = 'request_not_unique'; continue
            req = candidates[0]
            if req.status not in ('approved', 'paid'):
                row['expense_reason'] = 'request_not_approved'; continue
            if req.id not in visible:
                row['expense_reason'] = 'request_not_available'; continue
            category = categories.get(req.category_id)
            if category is None or category.type != 'outcome':
                row['expense_reason'] = 'request_category_unavailable'; continue
            row.update(expense_kind='request', expense_evidence='unique_request',
                expense_request_id=req.id, expense_request_status=req.status,
                expense_category_id=category.id, expense_category_name=category.name,
                expense_category_code=category.code)
            continue
        if broad_requests:
            row['expense_reason'] = 'possible_request_different_date'; continue
        evidence = _fee_evidence(batch, line)
        known_own = bindings.get(line.correspondent_account, set()) - {account_id}
        if len(known_own) > 1:
            row['expense_reason'] = 'own_account_not_unique'; continue
        transfer = bool(entry_matches and entry.kind == 'transfer') or len(known_own) == 1
        if evidence and transfer:
            row['expense_reason'] = 'classification_conflict'; continue
        if evidence:
            row.update(expense_kind=evidence[0], expense_evidence=evidence[1]); continue
        if transfer:
            row.update(expense_kind='internal_transfer', expense_evidence='confirmed_own_account'); continue
        if batch.owner_inn and line.correspondent_inn == batch.owner_inn:
            row['expense_reason'] = 'own_inn_does_not_prove_transfer'; continue
        if not line.correspondent_name or not line.correspondent_inn:
            row['expense_reason'] = 'missing_counterparty_details'; continue
        row.update(expense_kind='no_request', expense_evidence='bank_counterparty')
    return result


def annotate_expense_rows(s, batch, lines, rows, user):
    annotations = expense_annotations(s, batch, lines, user)
    for row in rows:
        row.update(annotations.get(row['id'], {}))
    return rows


def sanitize_request_fields(s, rows, user):
    """Apply current visibility also to old fields retained by legacy clients."""
    ids = {row.get('request_id') for row in rows if row.get('request_id') is not None}
    sections = getattr(user, '_sections', None)
    readable = sections is None or sections.get('requests') != 'hidden'
    visible = ({item.id for item in s.scalars(select(PaymentRequest).where(PaymentRequest.id.in_(ids)))
                if can_view_request(s, user, item)} if ids and readable else set())
    for row in rows:
        if row.get('request_id') is not None and row['request_id'] not in visible:
            row['request_id'] = None
            row.pop('request_version', None)
            # A category that came with a hidden request must not disclose its
            # article through a legacy field while the new metadata is safe.
            row.pop('category_id', None)
    return rows
