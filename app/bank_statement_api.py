"""Bank statement preview, strict reconciliation and accountant review.

An uploaded statement is not proof of its authenticity; the authorized payer
must explicitly confirm the selected bank account and currency.  Nothing is
posted during preview. Every commit rechecks the statement, the currently
visible company and all candidate requests under the shared write guard.
"""
from collections import Counter
from datetime import date
from decimal import Decimal
from hashlib import sha256
import hmac
import json
import re
from types import SimpleNamespace
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from .bank_statement_models import BankStatementBatch, BankStatementLine
from .bank_statement_parser import MAX_BYTES, StatementParseError, parse_bank_statement
from .db import Account, Audit, Category, Ledger, PaymentRequest, Setting, now, unit
from .erp import read_upload
from .security import session_user, perms_of, company_role, HOLDING_ROLES, jwt_key
from .requests_api import can_view_request
from .section_access import can_edit_section
from .services import amount, account_balance, budget_json, budget_state, day_start_balance, deleted_ledger_ids, effective_cashflows, enforce_budget, get, log, money, post_ledger
from .bank_statement_incoming import opening_matches, classify_incoming, process_incoming
from .bank_statement_fees import classify_fee, post_fee, prepare_fee_context
from .bank_statement_existing import classify_existing, process_existing
from .bank_statement_unallocated_out import classify_unallocated_out, process_unallocated_out
from .bank_statement_unallocated import category_id as unallocated_category_id, ensure_category
from .category_catalog import catalog
from .bank_statement_guard import (accounted_line_ids, set_statement_reconciliation,
    unreconciled_batch_ids, CREATED_BANK_STATUSES, REQUEST_PAID_BANK_STATUSES)
from .bank_statement_interest import classify_interest, post_interest
from .bank_statement_reprocess_payments import classify_reprocess_payments, process_reprocess_payments
from .bank_statement_expenses import annotate_expense_rows, sanitize_request_fields
from .bank_statement_category_rules import annotate_budget_fallbacks, load_rules
from .bank_statement_cash_funding import (CANDIDATE as FUNDING_CANDIDATE, MATCHED as FUNDING_MATCHED,
    binding as funding_binding, accounted_funding_line_ids, classify_cash_funding, process_cash_funding)

router = APIRouter()
REQUEST_ID = re.compile(r'(?<![A-Za-z0-9])CF-0*([1-9][0-9]*)(?![A-Za-z0-9])', re.I)
DRAFT_REFRESH_ACTION = 'Обновлён предпросмотр банковской выписки'
NEW_POSTING_STATUSES = {'auto_pay_candidate', 'income_candidate', 'fee_candidate', 'interest_candidate', 'outgoing_candidate'}
LINK_POSTING_STATUSES = {'income_matched_candidate', 'fee_existing', 'interest_existing', 'other_matched_candidate', FUNDING_CANDIDATE}
FINAL_POSTING_STATUSES = {'paid_match', 'auto_paid', 'manual_paid', 'manual_match', 'income_matched',
    'auto_income_posted', 'fee_matched', 'auto_fee_posted', 'interest_matched', 'auto_interest_posted',
    'duplicate', 'other_matched', 'auto_outgoing_posted', 'manual_transfer_match',
    'historical_transfer_posted', 'historical_request_paid', 'manual_distinct_income', FUNDING_MATCHED}


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm_account: bool
    confirm_currency: bool
    fee_category_id: int | None = Field(default=None, ge=1)
    # Older open clients can still commit under the previous payment-only rules.
    preview_token: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


class ReprocessInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    fee_category_id: int | None = Field(default=None, ge=1)


class ReprocessConfirm(ReprocessInput):
    preview_token: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirm: bool


class AllocateIncome(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    category_id: int = Field(ge=1)
    edit_token: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=10, max_length=1000)


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    classification: str = Field(pattern=r'^(bank_fee|internal_transfer|receipt|other|unresolved)$')
    note: str = Field(default='', max_length=1000)


class MatchInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: int = Field(ge=1)
    request_version: int = Field(ge=1)
    confirm: bool
    note: str = Field(min_length=10, max_length=1000)


def _fingerprint(account_number, data):
    # A number alone is not a bank-transaction ID; include every stable field.
    parts = [account_number, data['date'], data['direction'], data['amount'],
             data['document_number'], data['correspondent_mfo'], data['correspondent_account'],
             data['correspondent_inn'], data['correspondent_name'], data['purpose']]
    return sha256(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def _minor(value):
    """Bank opening/closing balances can be negative; preserve exact cents."""
    result = int(Decimal(value) * 100)
    if not -(2**63) <= result < 2**63:
        raise HTTPException(422, 'Сумма выписки слишком велика для хранения.')
    return result


def _line_data(line):
    result = {'id': line.id, 'line': line.line, 'date': str(line.date), 'document_number': line.document_number,
            'correspondent_mfo': line.correspondent_mfo, 'correspondent_account': line.correspondent_account,
            'correspondent_name': line.correspondent_name, 'correspondent_inn': line.correspondent_inn,
            'purpose': line.purpose, 'direction': line.direction, 'amount': money(line.amount),
            'status': line.status, 'reason': line.reason, 'request_id': line.request_id,
            'ledger_id': line.ledger_id, 'classification': line.classification,
            'review_note': line.review_note, 'uncertain': line.uncertain}
    linked_funding = funding_binding(line)
    if linked_funding:
        result['cash_funding'] = linked_funding
        result['review_note'] = 'Снятие наличных связано с ранее проведёнными операциями; повторного списания нет.'
    return result


def _statement(batch):
    return {'account_number': batch.account_number, 'owner_name': batch.owner_name,
            'owner_inn': batch.owner_inn, 'period_from': str(batch.period_from),
            'period_to': str(batch.period_to), 'opening_balance': money(batch.opening),
            'closing_balance': money(batch.closing), 'debit_total': money(batch.debit_total),
            'credit_total': money(batch.credit_total), 'balance_matches': batch.balance_matches,
            'declared_totals_match': batch.declared_totals_match, 'currency': None,
            'ready': batch.source_ready}


def _payload(s, batch, user=None):
    account = get(s, Account, batch.account_id)
    lines = list(s.scalars(select(BankStatementLine).where(BankStatementLine.batch_id == batch.id)
                           .order_by(BankStatementLine.line, BankStatementLine.id)))
    rows = [_line_data(line) for line in lines]
    if user is not None:
        annotate_expense_rows(s, batch, lines, rows, user)
        sanitize_request_fields(s, rows, user)
    return {'id': batch.id, 'status': batch.status, 'filename': batch.filename,
            'account': {'id': account.id, 'name': account.name, 'currency': account.currency},
            'statement': _statement(batch), 'rows': rows}


def _requests(s, account_id):
    return {r.id: r for r in s.scalars(select(PaymentRequest).where(
        PaymentRequest.account_id == account_id))}


def _can_read_requests(user):
    sections = getattr(user, '_sections', None)
    return sections is None or sections.get('requests') != 'hidden'


def _classify(s, lines, account, previous, user):
    """Match unique requests using planned or proven actual payment dates.

    Approved requests still require the planned date. Paid requests can also
    match the date of their active ledger payment; they are only linked, never
    paid again. A request number in the purpose must agree when present.
    """
    counts = Counter(line.fingerprint for line in lines)
    # Include even hidden requests in uniqueness checks, then apply visibility
    # before exposing or linking a candidate. Otherwise hidden rows can cause a
    # false "unique" match for another user.
    requests = _requests(s, account.id)
    line_dates = {line.date for line in lines if line.direction == 'out'}
    ledger = (list(s.scalars(select(Ledger).where(Ledger.account_id == account.id,
                                                 Ledger.kind == 'out', Ledger.request_id.in_(requests),
                                                 Ledger.date.in_(line_dates), Ledger.reversal_of.is_(None))))
              if line_dates and requests else [])
    ledger_ids = [entry.id for entry in ledger]
    reversed_ids = (set(s.scalars(select(Ledger.reversal_of).where(Ledger.reversal_of.in_(ledger_ids))))
                    if ledger_ids else set())
    ledger = [entry for entry in ledger if entry.id not in reversed_ids]
    actual_dates = {}
    for entry in ledger:
        req = requests.get(entry.request_id)
        if req is not None and req.status == 'paid' and req.amount == entry.amount:
            actual_dates.setdefault(req.id, set()).add(entry.date)

    def matches_request(line, req):
        return (line.direction == 'out' and line.correspondent_inn
                and req.counterparty_inn == line.correspondent_inn
                and req.amount == line.amount
                and (req.due_date == line.date
                     or (req.status == 'paid' and line.date in actual_dates.get(req.id, set()))))
    old_request_ids = set(s.scalars(select(BankStatementLine.request_id).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(BankStatementBatch.status == 'committed',
        BankStatementLine.account_id == account.id, BankStatementLine.request_id.is_not(None),
        BankStatementLine.status.in_(REQUEST_PAID_BANK_STATUSES))))
    old_ledger_ids = set(s.scalars(select(BankStatementLine.ledger_id).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(BankStatementBatch.status == 'committed',
        BankStatementLine.account_id == account.id, BankStatementLine.ledger_id.is_not(None),
        BankStatementLine.status.in_(REQUEST_PAID_BANK_STATUSES))))
    # Count all plausible pairs before choosing any. Even an already-linked
    # request or a duplicate bank row makes an otherwise identical pair unsafe.
    pairs = {line.id: [r for r in requests.values() if matches_request(line, r)] for line in lines}
    reverse = Counter(r.id for matches in pairs.values() for r in matches)
    past_lines = s.scalars(select(BankStatementLine).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(BankStatementBatch.status == 'committed',
        BankStatementLine.account_id == account.id, BankStatementLine.direction == 'out'))
    for past in past_lines:
        for req in requests.values():
            if matches_request(past, req):
                reverse[req.id] += 1
    for line in lines:
        line.request_id = line.ledger_id = None
        # Preserve multiplicity. Identical records in overlapping files lack a
        # reliable bank ID, so count only the additional occurrences, and mark
        # *all* such cases uncertain for accountant review.
        prior = previous.get(line.fingerprint, 0)
        line.include_in_daily = line.occurrence > prior
        line.uncertain = bool(prior or counts[line.fingerprint] > 1)
        if prior or counts[line.fingerprint] > 1:
            line.status = 'ambiguous_duplicate'
            line.reason = 'Возможное совпадение одинаковых операций: необходима проверка бухгалтера.'
            continue
        if line.direction != 'out':
            line.status = 'review'
            line.reason = 'Поступление: требуется проверка и классификация.'
            continue
        bank_ids = {int(x) for x in REQUEST_ID.findall(line.purpose)}
        if len(bank_ids) > 1:
            line.status = 'review'; line.reason = 'В назначении указано несколько заявок.'; continue
        candidates = pairs[line.id]
        if len(candidates) != 1 or reverse[candidates[0].id] != 1:
            line.status = 'review'; line.reason = 'Нет взаимно однозначного совпадения счёта, суммы, ИНН и даты заявки.'; continue
        direct = candidates[0]
        if not _can_read_requests(user) or not can_view_request(s, user, direct):
            line.status = 'review'; line.reason = 'Нет взаимно однозначного совпадения счёта, суммы, ИНН и даты заявки.'; continue
        if bank_ids and direct.id not in bank_ids:
            line.status = 'review'; line.reason = 'Номер заявки в назначении не совпадает.'; continue
        if direct.id in old_request_ids:
            line.status = 'review'; line.reason = 'Заявка уже связана с другой строкой выписки.'; continue
        if direct.status not in ('approved', 'paid'):
            line.status = 'review'; line.reason = 'Заявка не утверждена для оплаты.'; continue
        if direct.status == 'approved':
            if 'pay_bank' not in perms_of(user) or not can_edit_section(user, 'requests', 'ledger'):
                line.status = 'review'; line.reason = 'Для оплаты нужен доступ к заявкам и операциям.'; continue
            line.status = 'auto_pay_candidate'; line.reason = ''; line.request_id = direct.id; continue
        matching = [entry for entry in ledger if entry.request_id == direct.id and entry.amount == line.amount
                    and entry.date == line.date and entry.id not in old_ledger_ids]
        if len(matching) == 1:
            line.status = 'paid_match'; line.reason = ''; line.request_id = direct.id; line.ledger_id = matching[0].id; continue
        line.status = 'review'; line.reason = 'Не найдена однозначная ранее проведённая оплата.'
    # A request and a ledger entry may each be linked to only one bank line.
    request_counts = Counter(line.request_id for line in lines if line.request_id is not None)
    ledger_counts = Counter(line.ledger_id for line in lines if line.ledger_id is not None)
    for line in lines:
        if ((line.request_id is not None and request_counts[line.request_id] > 1)
                or (line.ledger_id is not None and ledger_counts[line.ledger_id] > 1)):
            line.status = 'review'; line.reason = 'Несколько операций соответствуют одной заявке.'
            line.request_id = line.ledger_id = None


def _previous_fingerprints(s, batch):
    fingerprints = list(s.scalars(select(BankStatementLine.fingerprint).join(
        BankStatementBatch, BankStatementBatch.id == BankStatementLine.batch_id).where(
            BankStatementLine.account_id == batch.account_id, BankStatementBatch.status == 'committed',
            BankStatementLine.include_in_daily.is_(True), BankStatementBatch.id != batch.id)))
    return Counter(fingerprints)


def _validate_bank_binding(s, account, bank_number):
    """A confirmed bank number and site account remain a one-to-one binding."""
    for prior in s.scalars(select(BankStatementBatch).where(BankStatementBatch.status == 'committed')):
        if (prior.account_id == account.id and prior.account_number != bank_number
                or prior.account_number == bank_number and prior.account_id != account.id):
            raise HTTPException(409, 'Банковский счёт уже связан с другим счётом сайта. Проверьте соответствие.')


def _fee_category(s, category_id):
    if category_id is None:
        return None
    matches = [c for c, retired in catalog(s, include_retired=True)
               if c.id == category_id and c.type == 'outcome' and not retired]
    if len(matches) != 1:
        raise HTTPException(422, 'Выберите действующую расходную статью комиссии этой компании.')
    return matches[0]


def _fee_setting_key(s):
    return f"company:{s.info['company_id']}:bank_statement_fee_category"


def _remember_fee_category(s, category_id, user):
    if category_id is None or 'write' not in perms_of(user) or not can_edit_section(user, 'ledger'):
        return
    _fee_category(s, category_id)
    key = _fee_setting_key(s)
    setting = s.get(Setting, key)
    if not setting:
        setting = Setting(key=key, value=str(category_id))
        s.add(setting)
    else:
        setting.value = str(category_id)
    log(s, user, 'Настроена статья банковских комиссий', 'setting', key, str(category_id))


def _posting_summary(s, batch, lines, account, rows, *, posted_ids=(), scope='preview'):
    """Account for every source movement without making disputed cash spendable."""
    def empty():
        return {'count': 0, 'credit_total': 0, 'debit_total': 0}
    groups = {key: empty() for key in ('ready', 'posted', 'pending', 'linked')}
    by_id = {line.id: line for line in lines}
    accounted = accounted_line_ids(s, lines, account)
    states = {}
    for row in rows:
        line = by_id[row['id']]
        if row['status'] in NEW_POSTING_STATUSES:
            state = 'ready'
        elif row['status'] in LINK_POSTING_STATUSES or (row['status'] in FINAL_POSTING_STATUSES and line.id in accounted):
            state = 'linked'
        else:
            state = 'pending'
        states[line.id] = state
        group = groups[state]
        group['count'] += 1
        group['credit_total' if line.direction == 'in' else 'debit_total'] += line.amount
        if line.id in posted_ids and line.id in accounted:
            groups['posted']['count'] += 1
            groups['posted']['credit_total' if line.direction == 'in' else 'debit_total'] += line.amount
    ledger_closing = account_balance(s, account, batch.period_to)
    projected = ledger_closing + groups['ready']['credit_total'] - groups['ready']['debit_total']
    expected = batch.closing - groups['pending']['credit_total'] + groups['pending']['debit_total']
    closing_matches = projected == expected
    allowed = bool(batch.source_ready and opening_matches(s, batch, account) and closing_matches)
    if not allowed:
        for key in ('count', 'credit_total', 'debit_total'):
            groups['pending'][key] += groups['ready'][key]
        groups['ready'] = empty()
        states = {key: ('pending' if state == 'ready' else state) for key, state in states.items()}
        projected = ledger_closing
    for row in rows:
        row['posting_state'] = states[row['id']]
    return {'scope': scope, **{key: {'count': value['count'], 'credit_total': money(value['credit_total']),
                    'debit_total': money(value['debit_total'])} for key, value in groups.items()},
            'bank_closing': money(batch.closing), 'ledger_closing': money(ledger_closing),
            'projected_closing': money(projected),
            'conservative_closing': money(projected - groups['pending']['debit_total']),
            'closing_matches': closing_matches, 'partial_allowed': allowed,
            'blocking_reason': '' if closing_matches else 'Движения сайта не согласуются с учтённой частью банковской выписки.'}


def _evaluated(s, batch, lines, account, user, fee_category_id, *, reprocess=False):
    """Read-only view; never add a category or financial operation in preview."""
    _fee_category(s, fee_category_id)
    opening_ok = opening_matches(s, batch, account)
    rules = load_rules(s, batch.company_id)
    incoming = classify_incoming(s, batch, lines, account, user, unallocated=True, rule_context=rules)
    existing = classify_existing(s, batch, lines, account, user, opening_ok)
    cash_funding = classify_cash_funding(s, batch, lines, account, user, opening_ok)
    outgoing = classify_unallocated_out(s, batch, lines, account, user, unallocated=True, rule_context=rules)
    fee_context = prepare_fee_context(s, batch, lines, account, fee_category_id)
    interest_context = prepare_fee_context(s, batch, lines, account, None, charge_kind='interest')
    payment_candidates = classify_reprocess_payments(s, batch, lines, account, user) if reprocess else {}
    # Bind even initial exact matches to the approval version the operator saw.
    # The write transaction classifies and signs again; any intervening request
    # edit invalidates confirmation instead of silently paying the new version.
    request_ids = {line.request_id for line in lines
                   if line.status == 'auto_pay_candidate' and line.request_id is not None}
    request_versions = {req.id: req.version for req in s.scalars(
        select(PaymentRequest).where(PaymentRequest.id.in_(request_ids)))} if request_ids else {}
    rows = []
    for line in lines:
        row = _line_data(line)
        if line.request_id in request_versions and line.status == 'auto_pay_candidate':
            row['request_version'] = request_versions[line.request_id]
        decision = cash_funding.get(line.id) or payment_candidates.get(line.id) or incoming.get(line.id)
        if decision is None and line.direction == 'out':
            # Never replace an existing request match with a commission guess.
            if line.status in ('review', 'reviewed', 'fee_candidate', 'fee_existing'):
                decision = classify_fee(s, batch, line, account, fee_category_id, user, opening_ok, context=fee_context)
                if decision is None:
                    decision = classify_interest(s, batch, line, account, user, opening_ok, context=interest_context)
        if line.id not in cash_funding and (decision is None or decision.get('status') == 'review') and existing.get(line.id, {}).get('status') == 'other_matched_candidate':
            decision = existing[line.id]
        if line.id not in cash_funding and (decision is None or decision.get('status') == 'review') and outgoing.get(line.id, {}).get('status') == 'outgoing_candidate':
            decision = outgoing[line.id]
        elif decision is None and line.id in outgoing:
            decision = outgoing[line.id]
        if decision:
            row.update({key: value for key, value in decision.items()
                        if key in ('status', 'reason', 'category_id', 'category_source', 'category_name', 'category_code', 'receipt_id', 'ledger_id', 'attach_receipt',
                                   'request_id', 'request_version', 'category_rule_ids', 'category_rule_status',
                                   'category_rule_revision', 'category_rule_budget_policy', 'category_rule_budget_over', 'intended_category_id', 'cash_funding')})
        rows.append(row)
    site_opening = (money(day_start_balance(s, account, batch.period_from))
                    if batch.period_from >= account.opening_date else None)
    categories = [{'id': c.id, 'name': c.name, 'type': c.type, 'activity': c.activity, 'retired': retired}
                  for c, retired in catalog(s, include_retired=True)]
    category_snapshot = sha256(json.dumps(categories, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    # Previously linked operations can later be reversed or edited. Do not
    # assume that an old link still represents the bank movement.
    deleted_ids = deleted_ledger_ids(s)
    linked_ids = {line.ledger_id for line in lines if line.ledger_id is not None}
    active_entries = {entry.id: entry for entry in s.scalars(effective_cashflows(s).where(
        Ledger.id.in_(linked_ids)))} if linked_ids else {}
    valid_funding = accounted_funding_line_ids(s, lines, account)
    for line, row in zip(lines, rows):
        if line.status in FINAL_POSTING_STATUSES:
            if line.status == FUNDING_MATCHED:
                if line.id not in valid_funding:
                    row.update(status='review', reason='Связанное снятие наличных изменилось или было отменено.')
                continue
            entry = active_entries.get(line.ledger_id)
            same_side = bool(entry and ((entry.kind == line.direction and entry.account_id == account.id) or
                (entry.kind == 'transfer' and ((line.direction == 'out' and entry.account_id == account.id) or
                                               (line.direction == 'in' and entry.to_account_id == account.id)))))
            if (not entry or entry.id in deleted_ids or not same_side or entry.date != line.date or entry.amount != line.amount):
                row['status'] = 'review'; row['reason'] = 'Связанная операция изменилась или была отменена.'
            elif entry.request_id is None and entry.category_id in rules['categories']:
                row.update(category_id=entry.category_id, category_name=rules['categories'][entry.category_id][0].name,
                    category_source='existing')
    annotate_budget_fallbacks(s, rows, {key: entry for key, entry in active_entries.items() if key not in deleted_ids})
    summary = _posting_summary(s, batch, lines, account, rows)
    full = bool(opening_ok and batch.source_ready and summary['closing_matches'] and summary['pending']['count'] == 0)
    annotate_expense_rows(s, batch, lines, rows, user)
    sanitize_request_fields(s, rows, user)
    return {'batch_id': batch.id, 'fee_category_id': fee_category_id, 'opening_matches': opening_ok,
            'full_reconciliation': full, 'posting_summary': summary,
            'category_snapshot': category_snapshot, 'category_rules_revision': rules['revision'],
            'category_rules_warning': rules['warning'],
            'statement_opening': money(batch.opening), 'site_opening': site_opening, 'rows': rows}


def _draft_revision(s, batch):
    # The immutable refresh audit event doubles as the draft generation. This
    # rotates confirmations across reloads without touching source/finance data.
    return s.scalar(select(Audit.id).where(Audit.entity == 'bank_statement',
        Audit.entity_id == str(batch.id), Audit.action == DRAFT_REFRESH_ACTION)
        .order_by(Audit.id.desc()).limit(1))


def _preview_token(batch, account, user, evaluated, mode, *, revision=None):
    # The confirmation is tied to the exact reviewed results and user. No bank
    # data needs to be kept in a browser token; the write transaction recomputes.
    contents = {'mode': mode, 'batch': batch.id, 'account': account.id, 'currency': batch.currency,
                'user': user.id, 'sha256': batch.sha256, 'result': evaluated}
    if revision is not None:
        contents['draft_revision'] = revision
    payload = json.dumps(contents, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hmac.new(jwt_key().encode(), payload, sha256).hexdigest()


def _apply_incoming(s, batch, lines, account, user, evaluated):
    """Caller has the serialised write guard and has checked the preview token."""
    incoming_rows = [row for row in evaluated['rows'] if row['status'] == 'income_candidate'
                     and row.get('category_source') == 'unclassified']
    # Create the special category only if a receipt can actually be posted.
    unallocated_id = ensure_category(s) if incoming_rows else None
    approved = {row['id']: row for row in evaluated['rows']}
    return Counter(process_incoming(s, batch, lines, account, user, unallocated_id, approved=approved))


def _apply_fees(s, batch, lines, account, user, fee_category_id, evaluated):
    counts = Counter()
    approved = {row['id']: row for row in evaluated['rows'] if row['status'] in ('fee_candidate', 'fee_existing')}
    for line in lines:
        if line.direction != 'out' or line.id not in approved:
            continue
        verdict = classify_fee(s, batch, line, account, fee_category_id, user, evaluated['opening_matches'])
        if not _same_charge_decision(approved[line.id], verdict):
            line.status = 'review'; line.reason = 'Проверенная операция изменилась; проверьте выписку снова.'
            continue
        if verdict['status'] == 'fee_existing':
            line.status = 'fee_matched'; line.ledger_id = verdict['ledger_id']
            line.classification = 'bank_fee'; line.reason = ''
        elif verdict['status'] == 'fee_candidate':
            try:
                with s.begin_nested():
                    entry = post_fee(s, batch, line, account, fee_category_id, user, evaluated['opening_matches'],
                                     confirmed_decision=True)
                    line.status = 'auto_fee_posted'; line.ledger_id = entry.id
                    line.classification = 'bank_fee'; line.reason = ''
            except HTTPException as exc:
                line.status = 'review'; line.reason = str(exc.detail)[:160]
        elif line.status in ('review', 'reviewed'):
            line.status = 'review'; line.reason = verdict['reason'][:160]
        counts[line.status] += 1
    return counts


def _same_charge_decision(shown, fresh):
    if not fresh or any(shown.get(key) != fresh.get(key) for key in (
            'status', 'ledger_id', 'category_source', 'category_name', 'category_code')):
        return False
    return (shown.get('category_id') == fresh.get('category_id') or
        shown.get('category_id') is None and shown.get('category_source') in ('bank_fee_auto', 'bank_interest_auto'))


def _apply_interest(s, batch, lines, account, user, evaluated):
    approved = {row['id']: row for row in evaluated['rows'] if row['status'] in ('interest_candidate', 'interest_existing')}
    for line in lines:
        if line.id not in approved:
            continue
        fresh = classify_interest(s, batch, line, account, user, evaluated['opening_matches'])
        if not _same_charge_decision(approved[line.id], fresh):
            line.status = 'review'; line.reason = 'Проверенная операция изменилась; проверьте выписку снова.'
            continue
        try:
            with s.begin_nested():
                if fresh['status'] == 'interest_existing':
                    line.ledger_id = fresh['ledger_id']; line.status = 'interest_matched'
                else:
                    entry = post_interest(s, batch, line, account, user, evaluated['opening_matches'], confirmed_decision=True)
                    line.ledger_id = entry.id; line.status = 'auto_interest_posted'
                line.classification = 'bank_interest'; line.reason = ''
        except HTTPException as exc:
            line.status = 'review'; line.reason = str(exc.detail)[:160]


class _Unreconciled(Exception):
    pass


def _apply_reconciled(s, batch, lines, account, user, fee_category_id, evaluated, pay_requests):
    """Post only signed verified rows; pending source debits remain a cash hold.

    Partial accounting must prove L + pending credits - pending debits == bank
    closing. A failed equation rolls back the selected subset as one unit.
    """
    approved = {row['id']: row for row in evaluated['rows']}
    selected = {row['id'] for row in evaluated['rows'] if row.get('posting_state') in ('ready', 'linked')
                and row['status'] in NEW_POSTING_STATUSES | LINK_POSTING_STATUSES}
    if not evaluated['posting_summary']['partial_allowed']:
        for line in lines:
            if approved[line.id]['status'] in NEW_POSTING_STATUSES | LINK_POSTING_STATUSES:
                line.status = 'review'; line.request_id = line.ledger_id = None
                line.reason = (evaluated['posting_summary']['blocking_reason']
                    or 'Начальный остаток выписки не совпадает с остатком счёта сайта.')
        return False
    try:
        with s.begin_nested():
            process_cash_funding(s, batch, lines, account, user, evaluated['opening_matches'], approved)
            incoming = [line for line in lines if approved[line.id]['status'] in ('income_candidate', 'income_matched_candidate')
                        and line.id in selected]
            _apply_incoming(s, batch, incoming, account, user, evaluated)
            existing = [line for line in lines if approved[line.id]['status'] == 'other_matched_candidate' and line.id in selected]
            process_existing(s, batch, existing, account, user, evaluated['opening_matches'], approved=approved)
            for line in sorted(lines, key=lambda row: (row.date, row.line, row.id)):
                shown = approved[line.id]
                if line.id not in selected or shown['status'] != 'auto_pay_candidate':
                    continue
                try:
                    with s.begin_nested():
                        if not pay_requests:
                            process_reprocess_payments(s, batch, lines, account, user, {line.id: shown})
                        else:
                            req = get(s, PaymentRequest, shown['request_id'])
                            payment_op = SimpleNamespace(account_id=account.id, to_account_id=None,
                                category_id=req.category_id, kind='out', amount=money(line.amount), date=line.date,
                                counterparty=req.counterparty, reference='BANK-' + line.fingerprint,
                                note='Банковская выписка, документ ' + line.document_number,
                                request_id=req.id, request_version=shown['request_version'], receipt_id=None)
                            entry = post_ledger(s, user, payment_op, bank_statement_fact=True)
                            line.request_id = req.id; line.ledger_id = entry.id; line.status = 'auto_paid'; line.reason = ''
                except HTTPException as exc:
                    line.status = 'review'; line.reason = str(exc.detail)[:160]
                    line.request_id = line.ledger_id = None
            _apply_fees(s, batch, lines, account, user, fee_category_id, evaluated)
            _apply_interest(s, batch, lines, account, user, evaluated)
            outgoing = [line for line in lines if approved[line.id]['status'] == 'outgoing_candidate' and line.id in selected]
            outgoing_id = ensure_category(s, 'out') if any(approved[line.id].get('category_source') == 'unclassified_out' for line in outgoing) else None
            process_unallocated_out(s, batch, outgoing, account, user, outgoing_id, approved=approved)
            s.flush()
            final = _posting_summary(s, batch, lines, account, [_line_data(line) for line in lines], scope='result')
            if not final['closing_matches']:
                raise _Unreconciled()
            return bool(final['pending']['count'] == 0 and account_balance(s, account, batch.period_to) == batch.closing)
    except (HTTPException, _Unreconciled):
        for line in lines:
            if line.id in selected:
                line.status = 'review'; line.request_id = line.ledger_id = None
                line.reason = 'Движения сайта не согласуются с учтённой частью банковской выписки.'
        return False


def _refresh_preview(s, batch, account, user, fee_category_id):
    """Refresh only the existing draft; the caller holds the shared write guard."""
    lines = list(s.scalars(select(BankStatementLine).where(BankStatementLine.batch_id == batch.id)
                           .order_by(BankStatementLine.line, BankStatementLine.id)))
    if not lines:
        raise HTTPException(409, 'В сохранённой выписке нет строк. Проверьте исходный файл.')
    _classify(s, lines, account, _previous_fingerprints(s, batch), user)
    s.flush()
    evaluated = _evaluated(s, batch, lines, account, user, fee_category_id)
    log(s, user, DRAFT_REFRESH_ACTION, 'bank_statement', batch.id,
        json.dumps({'rows': len(lines), 'ready': batch.source_ready}, ensure_ascii=False))
    s.flush()
    return {**_payload(s, batch), **evaluated,
            'preview_token': _preview_token(batch, account, user, evaluated, 'import',
                                             revision=_draft_revision(s, batch))}


@router.get('/api/bank-statements/previews')
def saved_previews(request: Request, page: int = Query(default=1, ge=1),
                   page_size: int = Query(default=30, ge=1, le=50)):
    """Read-only saved drafts in the selected company; never classify on GET."""
    with unit() as s:
        user, _ = session_user(s, request, ('pay_bank', 'import'))
        query = select(BankStatementBatch, Account).join(
            Account, Account.id == BankStatementBatch.account_id).where(BankStatementBatch.status == 'preview')
        if user.role != 'admin':
            query = query.where(BankStatementBatch.created_by == user.id)
        pairs = list(s.execute(query.order_by(BankStatementBatch.created_at.desc(), BankStatementBatch.id.desc())
            .offset((page - 1) * page_size).limit(page_size + 1)))
        return {'page': page, 'has_more': len(pairs) > page_size,
                'items': [{'id': batch.id, 'status': batch.status, 'filename': batch.filename,
                    'account': {'id': account.id, 'name': account.name, 'currency': batch.currency},
                    'period_from': str(batch.period_from), 'period_to': str(batch.period_to),
                    'credit_total': money(batch.credit_total), 'debit_total': money(batch.debit_total),
                    'created_at': batch.created_at.isoformat()}
                    for batch, account in pairs[:page_size]]}


@router.post('/api/bank-statements/{id}/resume')
def resume_preview(id: int, data: ReprocessInput, request: Request):
    with unit(True) as s:
        user, _ = session_user(s, request, ('pay_bank', 'import'))
        batch = get(s, BankStatementBatch, id)
        if batch.status == 'deleted':
            raise HTTPException(404, 'Запись не найдена.')
        if batch.created_by != user.id and user.role != 'admin':
            raise HTTPException(403, 'Предпросмотр доступен загрузившему сотруднику или администратору.')
        if batch.status != 'preview':
            raise HTTPException(409, 'Эта выписка уже обработана. Откройте её в истории выписок.')
        account = get(s, Account, batch.account_id)
        if account.archived or account.kind != 'bank' or account.currency != batch.currency:
            raise HTTPException(409, 'Счёт или валюта изменились. Проверьте настройки банковского счёта.')
        _validate_bank_binding(s, account, batch.account_number)
        if s.scalar(select(BankStatementBatch.id).where(BankStatementBatch.account_id == account.id,
                BankStatementBatch.sha256 == batch.sha256, BankStatementBatch.status == 'committed')):
            raise HTTPException(409, 'Эта выписка уже обработана. Откройте её в истории выписок.')
        # Unready source totals stay visible for inspection. The commit route
        # keeps its existing mandatory source_ready check.
        return _refresh_preview(s, batch, account, user, data.fee_category_id)


@router.post('/api/bank-statements/preview')
async def preview(request: Request, account_id: int, currency: str = Query(pattern=r'^(UZS|USD|EUR)$'),
                  fee_category_id: int | None = Query(default=None, ge=1)):
    with unit() as s:
        session_user(s, request, ('pay_bank', 'import'))
        account = get(s, Account, account_id)
        if account.kind != 'bank' or account.archived:
            raise HTTPException(422, 'Выберите действующий банковский счёт.')
        if account.currency != currency:
            raise HTTPException(422, 'Выбранная валюта не совпадает с валютой счёта.')
    filename = unquote(request.headers.get('X-Filename', '')).replace('\\', '/').split('/')[-1].strip()
    if not filename or len(filename) > 220 or not filename.lower().endswith(('.xls', '.html', '.htm')):
        raise HTTPException(422, 'Выберите выписку банка в формате XLS или HTML.')
    raw = await read_upload(request, limit=MAX_BYTES, too_large='Файл больше 5 МБ.')
    try:
        source = parse_bank_statement(raw)
    except StatementParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    digest = sha256(raw).hexdigest()
    with unit(True) as s:
        user, _ = session_user(s, request, ('pay_bank', 'import'))
        account = get(s, Account, account_id)
        if account.kind != 'bank' or account.archived or account.currency != currency:
            raise HTTPException(409, 'Счёт изменился. Обновите страницу и проверьте выбранные данные.')
        _validate_bank_binding(s, account, source['account_number'])
        existing = s.scalar(select(BankStatementBatch).where(BankStatementBatch.account_id == account.id,
                                                             BankStatementBatch.sha256 == digest,
                                                             BankStatementBatch.status == 'committed'))
        if existing:
            raise HTTPException(409, 'Эта выписка уже загружена для выбранного счёта.')
        # Reusing the user's uncommitted preview avoids accumulating private
        # copies every time the operator changes a choice or tries again.
        prior = s.scalar(select(BankStatementBatch).where(BankStatementBatch.account_id == account.id,
            BankStatementBatch.sha256 == digest, BankStatementBatch.status == 'preview',
            BankStatementBatch.currency == currency, BankStatementBatch.created_by == user.id)
            .order_by(BankStatementBatch.id.desc()).limit(1))
        if prior:
            return _refresh_preview(s, prior, account, user, fee_category_id)
        batch = BankStatementBatch(company_id=s.info['company_id'], account_id=account.id, created_by=user.id,
            filename=filename, sha256=digest, account_number=source['account_number'], owner_name=source['owner_name'],
            owner_inn=source['owner_inn'], currency=currency, period_from=date.fromisoformat(source['period_from']),
            period_to=date.fromisoformat(source['period_to']), opening=_minor(source['opening_balance']),
            closing=_minor(source['closing_balance']), debit_total=_minor(source['debit_total']),
            credit_total=_minor(source['credit_total']), balance_matches=source['balance_matches'],
            declared_totals_match=source['declared_totals_match'], source_ready=source['ready'], status='preview')
        s.add(batch); s.flush()
        lines = []
        seen = Counter()
        for row in source['rows']:
            fp = _fingerprint(batch.account_number, row); seen[fp] += 1
            line = BankStatementLine(company_id=s.info['company_id'], batch_id=batch.id, account_id=account.id,
                line=row['line'], date=date.fromisoformat(row['date']), direction=row['direction'],
                amount=_minor(row['amount']), document_number=row['document_number'],
                correspondent_mfo=row['correspondent_mfo'], correspondent_account=row['correspondent_account'],
                correspondent_name=row['correspondent_name'], correspondent_inn=row['correspondent_inn'],
                purpose=row['purpose'], fingerprint=fp, occurrence=seen[fp])
            lines.append(line); s.add(line)
        s.flush()
        _classify(s, lines, account, _previous_fingerprints(s, batch), user); s.flush()
        evaluated = _evaluated(s, batch, lines, account, user, fee_category_id)
        log(s, user, 'Проверена банковская выписка', 'bank_statement', batch.id,
            json.dumps({'sha256': digest, 'rows': len(lines), 'ready': source['ready']}, ensure_ascii=False))
        return {**_payload(s, batch), **evaluated,
                'preview_token': _preview_token(batch, account, user, evaluated, 'import')}


@router.post('/api/bank-statements/{id}/commit')
def commit(id: int, data: Confirmation, request: Request):
    if data.confirm_account is not True or data.confirm_currency is not True:
        raise HTTPException(422, 'Подтвердите банковский счёт и валюту выписки.')
    with unit(True) as s:
        user, _ = session_user(s, request, ('pay_bank', 'import'))
        batch = get(s, BankStatementBatch, id)
        if batch.status != 'preview':
            raise HTTPException(409, 'Эта выписка уже обработана.')
        if batch.created_by != user.id and user.role != 'admin':
            raise HTTPException(403, 'Подтвердить выписку может загрузивший её сотрудник или администратор.')
        account = get(s, Account, batch.account_id)
        if account.archived or account.kind != 'bank' or account.currency != batch.currency:
            raise HTTPException(409, 'Счёт или валюта изменились. Проверьте настройки банковского счёта.')
        _validate_bank_binding(s, account, batch.account_number)
        if not batch.source_ready:
            raise HTTPException(409, 'Остатки или обороты выписки не подтверждены. Обработка заблокирована.')
        existing = s.scalar(select(BankStatementBatch.id).where(BankStatementBatch.account_id == account.id,
                    BankStatementBatch.sha256 == batch.sha256, BankStatementBatch.status == 'committed'))
        if existing:
            raise HTTPException(409, 'Эта выписка уже загружена для выбранного счёта.')
        lines = list(s.scalars(select(BankStatementLine).where(BankStatementLine.batch_id == batch.id)
                               .order_by(BankStatementLine.line, BankStatementLine.id)))
        _classify(s, lines, account, _previous_fingerprints(s, batch), user)
        counts = Counter()
        evaluated = None
        fully_reconciled = None
        revision = _draft_revision(s, batch)
        if revision is not None and data.preview_token is None:
            raise HTTPException(409, 'Сначала откройте сохранённую выписку и проверьте её снова.')
        if data.preview_token:
            evaluated = _evaluated(s, batch, lines, account, user, data.fee_category_id)
            expected = _preview_token(batch, account, user, evaluated, 'import', revision=revision)
            if not hmac.compare_digest(data.preview_token, expected):
                raise HTTPException(409, 'Данные изменились. Откройте сохранённую выписку и проверьте её снова.')
            fully_reconciled = _apply_reconciled(s, batch, lines, account, user, data.fee_category_id,
                                                  evaluated, pay_requests=True)
        elif data.fee_category_id is not None:
            raise HTTPException(409, 'Сначала проверьте выписку с выбранной статьёй комиссии.')
        for line in lines:
            if evaluated is None and line.status == 'auto_pay_candidate':
                try:
                    with s.begin_nested():
                        req = get(s, PaymentRequest, line.request_id)
                        payment_op = SimpleNamespace(account_id=account.id, to_account_id=None, category_id=req.category_id,
                            kind='out', amount=money(line.amount), date=line.date, counterparty=req.counterparty,
                            reference='BANK-' + line.fingerprint, note='Банковская выписка, документ ' + line.document_number,
                            request_id=req.id, request_version=req.version, receipt_id=None)
                        entry = post_ledger(s, user, payment_op, bank_statement_fact=True)
                        line.ledger_id = entry.id
                        line.status = 'auto_paid'
                except HTTPException as exc:
                    # A rejected payment rolls back its savepoint, not other rows.
                    line.status = 'review'; line.reason = str(exc.detail)[:160]
                    line.request_id = line.ledger_id = None
            counts[line.status] += 1
        if evaluated is not None:
            _remember_fee_category(s, data.fee_category_id, user)
        counts = Counter(line.status for line in lines)
        batch.status = 'committed'; batch.committed_at = now()
        # An older open client may omit the new token. It keeps its old
        # payment-only behaviour, but cannot mark an unreconciled account safe.
        if fully_reconciled is None:
            fully_reconciled = bool(all(line.status in FINAL_POSTING_STATUSES for line in lines) and
                                    account_balance(s, account, batch.period_to) == batch.closing)
        set_statement_reconciliation(s, batch, fully_reconciled, user)
        log(s, user, 'Обработана банковская выписка', 'bank_statement', batch.id,
            json.dumps({'sha256': batch.sha256, 'rows': len(lines), 'statuses': dict(counts)}, ensure_ascii=False))
        created = {line.id for line in lines if line.status in CREATED_BANK_STATUSES}
        summary = _posting_summary(s, batch, lines, account, [_line_data(line) for line in lines], posted_ids=created, scope='result')
        return {'id': batch.id, 'status': batch.status, 'auto_paid': sum(line.status=='auto_paid' for line in lines),
                'linked_paid': sum(line.status=='paid_match' for line in lines),
                'auto_income': sum(line.status=='auto_income_posted' for line in lines),
                'income_linked': sum(line.status=='income_matched' for line in lines),
                'auto_fee': sum(line.status=='auto_fee_posted' for line in lines),
                'fee_linked': sum(line.status=='fee_matched' for line in lines),
                'auto_interest': sum(line.status=='auto_interest_posted' for line in lines),
                'interest_linked': sum(line.status=='interest_matched' for line in lines),
                'auto_outgoing': sum(line.status=='auto_outgoing_posted' for line in lines),
                'other_linked': sum(line.status=='other_matched' for line in lines),
                'full_reconciliation': fully_reconciled, 'posting_summary': summary,
                'partial_reconciliation': bool(not fully_reconciled and (created or any(line.status in
                    ('income_matched', 'fee_matched', 'interest_matched', 'other_matched') for line in lines))),
                'review': sum(line.status in ('review','ambiguous_duplicate') or
                              (line.status == 'reviewed' and line.classification in ('receipt', 'bank_fee')) for line in lines),
                'duplicate': sum(1 for line in lines if not line.include_in_daily)}


@router.get('/api/bank-statements/reviews')
def reviews(request: Request):
    with unit() as s:
        session_user(s, request, ('ledger', 'pay_bank'))
        lines = s.execute(select(BankStatementLine, Account).join(BankStatementBatch,
            BankStatementBatch.id == BankStatementLine.batch_id).join(Account, Account.id == BankStatementLine.account_id).where(BankStatementBatch.status == 'committed',
            or_(BankStatementLine.status.in_(('review', 'ambiguous_duplicate')),
                (BankStatementLine.status == 'reviewed') & (BankStatementLine.classification == 'internal_transfer')))
            .order_by(BankStatementLine.date.desc(), BankStatementLine.id))
        result = []
        for line, account in lines:
            result.append({**_line_data(line), 'batch_id': line.batch_id, 'account_id': account.id,
                           'account_name': account.name, 'currency': account.currency})
        return {'items': result}


@router.get('/api/bank-statements/settings')
def statement_settings(request: Request):
    with unit() as s:
        session_user(s, request, ('ledger', 'pay_bank', 'import'))
        setting = s.get(Setting, _fee_setting_key(s))
        try:
            fee_id = int(setting.value) if setting else None
            _fee_category(s, fee_id)
        except (ValueError, HTTPException):
            fee_id = None
        return {'fee_category_id': fee_id}


@router.get('/api/bank-statements/reprocess/batches')
def reprocess_batches(request: Request):
    with unit() as s:
        session_user(s, request, ('write', 'pay_bank'))
        eligible = select(BankStatementLine.id).where(BankStatementLine.batch_id == BankStatementBatch.id,
            BankStatementLine.status.in_(('review', 'reviewed')), BankStatementLine.uncertain.is_(False)).exists()
        pending_batches = unreconciled_batch_ids(s)
        rows = s.execute(select(BankStatementBatch, Account).join(Account, Account.id == BankStatementBatch.account_id)
            .where(BankStatementBatch.status == 'committed', or_(eligible, BankStatementBatch.id.in_(pending_batches)))
            .order_by(BankStatementBatch.id.desc()))
        items = [{'id': batch.id, 'account_id': account.id, 'account_name': account.name,
                  'currency': batch.currency, 'period_from': str(batch.period_from),
                  'period_to': str(batch.period_to), 'filename': batch.filename} for batch, account in rows]
        return {'items': items}


def _reprocess_context(s, id):
    batch = get(s, BankStatementBatch, id)
    if batch.status != 'committed' or not batch.source_ready:
        raise HTTPException(409, 'Доступна только ранее подтверждённая выписка.')
    account = get(s, Account, batch.account_id)
    if account.archived or account.kind != 'bank' or account.currency != batch.currency:
        raise HTTPException(409, 'Счёт или валюта изменились. Проверьте выписку.')
    _validate_bank_binding(s, account, batch.account_number)
    lines = list(s.scalars(select(BankStatementLine).where(BankStatementLine.batch_id == batch.id)
                           .order_by(BankStatementLine.line, BankStatementLine.id)))
    return batch, account, lines


@router.post('/api/bank-statements/{id}/reprocess/preview')
def reprocess_preview(id: int, data: ReprocessInput, request: Request):
    with unit() as s:
        user, _ = session_user(s, request, ('write', 'pay_bank'))
        batch, account, lines = _reprocess_context(s, id)
        evaluated = _evaluated(s, batch, lines, account, user, data.fee_category_id, reprocess=True)
        return {**evaluated, 'preview_token': _preview_token(batch, account, user, evaluated, 'reprocess')}


@router.post('/api/bank-statements/{id}/reprocess/commit')
def reprocess_commit(id: int, data: ReprocessConfirm, request: Request):
    if not data.confirm:
        raise HTTPException(422, 'Подтвердите повторную обработку выписки.')
    with unit(True) as s:
        user, _ = session_user(s, request, ('write', 'pay_bank'))
        batch, account, lines = _reprocess_context(s, id)
        evaluated = _evaluated(s, batch, lines, account, user, data.fee_category_id, reprocess=True)
        expected = _preview_token(batch, account, user, evaluated, 'reprocess')
        if not hmac.compare_digest(data.preview_token, expected):
            raise HTTPException(409, 'Данные изменились. Проверьте выписку снова.')
        before = {line.id: (line.status, line.ledger_id) for line in lines}
        fully_reconciled = _apply_reconciled(s, batch, lines, account, user, data.fee_category_id,
                                            evaluated, pay_requests=False)
        set_statement_reconciliation(s, batch, fully_reconciled, user)
        counts = Counter(line.status for line in lines)
        _remember_fee_category(s, data.fee_category_id, user)
        log(s, user, 'Повторно проверена банковская выписка', 'bank_statement', batch.id,
            json.dumps({'statuses': dict(counts)}, ensure_ascii=False))
        changed = [line for line in lines if before[line.id] != (line.status, line.ledger_id)]
        created = {line.id for line in changed if line.status in CREATED_BANK_STATUSES}
        linked = sum(line.status in ('income_matched', 'fee_matched', 'interest_matched', 'other_matched', FUNDING_MATCHED) for line in changed)
        summary = _posting_summary(s, batch, lines, account, [_line_data(line) for line in lines], posted_ids=created, scope='result')
        return {'posted': len(created), 'linked': linked,
                'auto_interest': sum(line.status == 'auto_interest_posted' for line in changed),
                'interest_linked': sum(line.status == 'interest_matched' for line in changed),
                'full_reconciliation': fully_reconciled, 'posting_summary': summary,
                'partial_reconciliation': bool(not fully_reconciled and (created or linked)),
                'review': sum(line.status in ('review', 'ambiguous_duplicate') or
                              (line.status == 'reviewed' and line.classification in ('receipt', 'bank_fee')) for line in lines)}


def _ledger_edit_token(entry):
    # Same fields and encoding as the existing operations edit token.
    return sha256(json.dumps({k: str(getattr(entry, k)) for k in (
        'account_id', 'to_account_id', 'category_id', 'kind', 'amount', 'date', 'counterparty',
        'reference', 'note', 'request_id', 'receipt_id', 'reversal_of')}, sort_keys=True).encode()).hexdigest()


def _list_unallocated(s, kind):
    unallocated_id = unallocated_category_id(s, kind)
    if unallocated_id is None:
        return {'items': []}
    reversed_ids = select(Ledger.reversal_of).where(Ledger.reversal_of.is_not(None))
    rows = s.execute(select(BankStatementLine, Ledger, Account).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).join(Ledger,
        Ledger.id == BankStatementLine.ledger_id).join(Account, Account.id == BankStatementLine.account_id).where(
        BankStatementBatch.status == 'committed', BankStatementLine.direction == kind,
        Ledger.category_id == unallocated_id, Ledger.kind == kind, Ledger.reversal_of.is_(None),
        Ledger.id.not_in(reversed_ids), Ledger.id.not_in(deleted_ledger_ids(s))))
    return {'items': [{'id': line.id, 'date': str(line.date), 'amount': money(line.amount),
                       'currency': account.currency, 'account_name': account.name,
                       'correspondent_name': line.correspondent_name, 'correspondent_inn': line.correspondent_inn,
                       'document_number': line.document_number, 'category_id': entry.category_id,
                       'edit_token': _ledger_edit_token(entry)} for line, entry, account in rows]}


@router.get('/api/bank-statements/income-unallocated')
def unallocated_income(request: Request):
    with unit() as s:
        session_user(s, request, ('pay_bank', 'write'))
        return _list_unallocated(s, 'in')


@router.get('/api/bank-statements/outgoing-unallocated')
def unallocated_outgoing(request: Request):
    with unit() as s:
        session_user(s, request, ('pay_bank', 'write'))
        return _list_unallocated(s, 'out')


@router.post('/api/bank-statements/lines/{id}/income-category')
def allocate_income(id: int, data: AllocateIncome, request: Request):
    with unit(True) as s:
        user, _ = session_user(s, request, ('pay_bank', 'write'))
        if not can_edit_section(user, 'ledger'):
            raise HTTPException(403, 'Для назначения статьи нужен доступ к изменению операций.')
        line = get(s, BankStatementLine, id)
        batch = get(s, BankStatementBatch, line.batch_id)
        unallocated_id = unallocated_category_id(s)
        if batch.status != 'committed' or line.direction != 'in' or not line.ledger_id or unallocated_id is None:
            raise HTTPException(409, 'Поступление недоступно для распределения.')
        entry = get(s, Ledger, line.ledger_id)
        active = s.scalar(effective_cashflows(s).where(Ledger.id == entry.id))
        if (not active or entry.id in deleted_ledger_ids(s) or entry.kind != 'in' or entry.account_id != line.account_id or entry.receipt_id is not None or
                entry.category_id != unallocated_id or _ledger_edit_token(entry) != data.edit_token):
            raise HTTPException(409, 'Поступление изменилось. Обновите список.')
        choices = [c for c, retired in catalog(s, include_retired=True)
                   if not retired and c.id == data.category_id and c.type == 'income'
                   and c.activity != 'unclassified']
        if len(choices) != 1:
            raise HTTPException(422, 'Выберите действующую статью поступлений этой компании.')
        entry.category_id = choices[0].id
        log(s, user, 'Распределено банковское поступление', 'ledger', entry.id,
            json.dumps({'before_category': unallocated_id, 'after_category': entry.category_id,
                        'reason': data.reason, 'statement_line_id': line.id}, ensure_ascii=False))
        return {'ok': True}


@router.post('/api/bank-statements/lines/{id}/outgoing-category')
def allocate_outgoing(id: int, data: AllocateIncome, request: Request):
    with unit(True) as s:
        user, _ = session_user(s, request, ('pay_bank', 'write'))
        if not can_edit_section(user, 'ledger'):
            raise HTTPException(403, 'Для назначения статьи нужен доступ к изменению операций.')
        line = get(s, BankStatementLine, id)
        batch = get(s, BankStatementBatch, line.batch_id)
        unallocated_id = unallocated_category_id(s, 'out')
        if batch.status != 'committed' or line.direction != 'out' or not line.ledger_id or unallocated_id is None:
            raise HTTPException(409, 'Списание недоступно для распределения.')
        entry = get(s, Ledger, line.ledger_id)
        active = s.scalar(effective_cashflows(s).where(Ledger.id == entry.id))
        if (not active or entry.id in deleted_ledger_ids(s) or entry.kind != 'out' or entry.account_id != line.account_id or
                entry.request_id is not None or entry.receipt_id is not None or
                entry.category_id != unallocated_id or _ledger_edit_token(entry) != data.edit_token):
            raise HTTPException(409, 'Списание изменилось. Обновите список.')
        choices = [c for c, retired in catalog(s, include_retired=True)
                   if not retired and c.id == data.category_id and c.type == 'outcome'
                   and c.activity != 'unclassified']
        if len(choices) != 1:
            raise HTTPException(422, 'Выберите действующую расходную статью этой компании.')
        account = get(s, Account, line.account_id)
        # The bank movement is already real, but its final article must obey
        # the existing hard/soft budget controls before it is reclassified.
        budget = budget_state(s, choices[0].id, str(entry.date)[:7], account.currency, entry.amount)
        enforce_budget(s, choices[0].id, entry.date, account.currency, entry.amount, data.reason)
        entry.category_id = choices[0].id
        log(s, user, 'Распределено банковское списание', 'ledger', entry.id,
            json.dumps({'before_category': unallocated_id, 'after_category': entry.category_id,
                        'reason': data.reason, 'budget_over': budget['over'], 'statement_line_id': line.id}, ensure_ascii=False))
        return {'ok': True, 'budget': budget_json(budget)}


@router.post('/api/bank-statements/lines/{id}/review')
def review(id: int, data: ReviewInput, request: Request):
    with unit(True) as s:
        user, _ = session_user(s, request, 'pay_bank')
        line = get(s, BankStatementLine, id)
        batch = get(s, BankStatementBatch, line.batch_id)
        if batch.status != 'committed' or line.status not in ('review', 'ambiguous_duplicate'):
            raise HTTPException(409, 'Эта строка недоступна для проверки.')
        before = line.classification
        line.classification = data.classification
        line.review_note = data.note
        line.reviewed_by = user.id; line.reviewed_at = now()
        if data.classification != 'unresolved' and not line.uncertain:line.status = 'reviewed'
        log(s, user, 'Проверена строка выписки', 'bank_statement_line', line.id,
            json.dumps({'classification_before': before, 'classification': data.classification}, ensure_ascii=False))
        return _line_data(line)


def _review_line(s, id):
    line = get(s, BankStatementLine, id)
    batch = get(s, BankStatementBatch, line.batch_id)
    if batch.status != 'committed' or line.status != 'review' or line.direction != 'out' or line.uncertain:
        raise HTTPException(409, 'Эта строка недоступна для сопоставления.')
    return line


@router.get('/api/bank-statements/lines/{id}/candidates')
def candidates(id: int, request: Request):
    with unit() as s:
        user, _ = session_user(s, request, 'pay_bank')
        if not _can_read_requests(user) or not can_edit_section(user, 'requests', 'ledger'):
            raise HTTPException(403, 'Для сопоставления нужен доступ к заявкам и операциям.')
        line = _review_line(s, id)
        items = []
        for req in _requests(s, line.account_id).values():
            if (req.status in ('approved', 'paid') and line.correspondent_inn and req.counterparty_inn == line.correspondent_inn
                    and req.amount == line.amount and can_view_request(s, user, req)):
                items.append({'id': req.id, 'number': f'CF-{req.id:05d}', 'counterparty': req.counterparty,
                              'counterparty_inn': req.counterparty_inn, 'amount': money(req.amount),
                              'date': str(req.due_date), 'status': req.status, 'version': req.version})
        return {'items': items}


@router.post('/api/bank-statements/lines/{id}/match')
def match(id: int, data: MatchInput, request: Request):
    if not data.confirm:
        raise HTTPException(422, 'Подтвердите сопоставление.')
    with unit(True) as s:
        user, _ = session_user(s, request, 'pay_bank')
        if not _can_read_requests(user) or not can_edit_section(user, 'requests', 'ledger'):
            raise HTTPException(403, 'Для сопоставления нужен доступ к заявкам и операциям.')
        line = _review_line(s, id)
        req = get(s, PaymentRequest, data.request_id)
        if not can_view_request(s, user, req):
            raise HTTPException(404, 'Заявка не найдена.')
        if (req.version != data.request_version or req.status not in ('approved', 'paid')
                or req.account_id != line.account_id or req.amount != line.amount
                or not line.correspondent_inn or req.counterparty_inn != line.correspondent_inn):
            raise HTTPException(409, 'Заявка изменилась или её счёт, сумма либо ИНН не совпадают.')
        prior = s.scalar(select(BankStatementLine.id).join(BankStatementBatch,
            BankStatementBatch.id == BankStatementLine.batch_id).where(BankStatementBatch.status == 'committed',
            BankStatementLine.id != line.id, BankStatementLine.request_id == req.id,
            BankStatementLine.status.in_(REQUEST_PAID_BANK_STATUSES)))
        if prior:
            raise HTTPException(409, 'Заявка уже связана с другой строкой выписки.')
        if req.status == 'approved':
            op = SimpleNamespace(account_id=line.account_id, to_account_id=None, category_id=req.category_id,
                kind='out', amount=money(line.amount), date=line.date, counterparty=req.counterparty,
                reference='BANK-' + line.fingerprint, note='Банковская выписка: ' + data.note,
                request_id=req.id, request_version=req.version, receipt_id=None)
            entry = post_ledger(s, user, op, bank_statement_fact=True)
            line.status = 'manual_paid'
        else:
            entries = list(s.scalars(select(Ledger).where(Ledger.request_id == req.id, Ledger.kind == 'out',
                Ledger.account_id == line.account_id, Ledger.amount == line.amount, Ledger.date == line.date,
                Ledger.reversal_of.is_(None))))
            if len(entries) != 1 or s.scalar(select(Ledger.id).where(Ledger.reversal_of == entries[0].id)):
                raise HTTPException(409, 'Не найдена действующая операция оплаты за эту дату.')
            entry = entries[0]
            if s.scalar(select(BankStatementLine.id).join(BankStatementBatch,
                BankStatementBatch.id == BankStatementLine.batch_id).where(BankStatementBatch.status == 'committed',
                BankStatementLine.id != line.id, BankStatementLine.ledger_id == entry.id)):
                raise HTTPException(409, 'Операция уже связана с другой строкой выписки.')
            line.status = 'manual_match'
        line.request_id = req.id; line.ledger_id = entry.id; line.reason = ''
        line.review_note = data.note; line.reviewed_by = user.id; line.reviewed_at = now()
        log(s, user, 'Сопоставлена строка выписки', 'bank_statement_line', line.id,
            json.dumps({'request_id': req.id, 'ledger_id': entry.id, 'note': data.note}, ensure_ascii=False))
        return {'status': line.status, 'request_id': req.id, 'ledger_id': entry.id}


@router.get('/api/bank-statements/daily')
def daily(request: Request, date: date, currency: str = Query(pattern=r'^(UZS|USD|EUR)$')):
    with unit() as s:
        user, _ = session_user(s, request, ('view', 'ledger', 'pay_bank'))
        if user.role not in HOLDING_ROLES and company_role(user) == 'sales':
            raise HTTPException(403, 'Банковские обороты для этой роли недоступны.')
        batches = {batch.id: batch for batch in s.scalars(select(BankStatementBatch).where(
            BankStatementBatch.status == 'committed', BankStatementBatch.currency == currency,
            BankStatementBatch.period_from <= date, BankStatementBatch.period_to >= date))}
        if not batches:
            return {'date': str(date), 'currency': currency, 'credit_total': '0.00', 'debit_total': '0.00',
                    'accounts': [], 'source': 'bank_statements', 'available': False, 'uncertain': False}
        all_lines = list(s.scalars(select(BankStatementLine).where(BankStatementLine.batch_id.in_(list(batches)),
                    BankStatementLine.date == date)))
        lines = [line for line in all_lines if line.include_in_daily]
        uncertain = any(line.uncertain for line in all_lines)
        totals = {}
        for line in lines:
            account = get(s, Account, line.account_id)
            values = totals.setdefault(account.id, {'account_id': account.id, 'name': account.name,
                                                     'credit': 0, 'debit': 0})
            values['credit' if line.direction == 'in' else 'debit'] += line.amount
        return {'date': str(date), 'currency': currency,
                'credit_total': money(sum(v['credit'] for v in totals.values())),
                'debit_total': money(sum(v['debit'] for v in totals.values())),
                'accounts': [{'account_id': v['account_id'], 'name': v['name'],
                              'credit_total': money(v['credit']), 'debit_total': money(v['debit'])}
                             for v in totals.values()], 'source': 'bank_statements', 'available': True,
                'uncertain': uncertain}


@router.get('/api/bank-statements/history')
def statement_history(request: Request, page: int = Query(default=1, ge=1),
                      page_size: int = Query(default=30, ge=1, le=50)):
    """Read-only history: same statement access as the original detail route."""
    with unit() as s:
        session_user(s, request, ('pay_bank', 'import'))
        pairs = list(s.execute(select(BankStatementBatch, Account).join(
            Account, Account.id == BankStatementBatch.account_id).where(
            BankStatementBatch.status == 'committed').order_by(
            BankStatementBatch.period_to.desc(), BankStatementBatch.id.desc())
            .offset((page - 1) * page_size).limit(page_size + 1)))
        return {'page': page, 'has_more': len(pairs) > page_size,
                'items': [{'id': batch.id, 'filename': batch.filename,
                    'account': {'id': account.id, 'name': account.name, 'currency': account.currency},
                    'period_from': str(batch.period_from), 'period_to': str(batch.period_to),
                    'credit_total': money(batch.credit_total), 'debit_total': money(batch.debit_total),
                    'committed_at': batch.committed_at.isoformat() if batch.committed_at else None}
                    for batch, account in pairs[:page_size]]}


@router.get('/api/bank-statements/history/{id}')
def statement_history_detail(id: int, request: Request):
    """Current read-only result; no preview, deleted batch, token, or write."""
    with unit() as s:
        user, _ = session_user(s, request, ('pay_bank', 'import'))
        batch = get(s, BankStatementBatch, id)
        if batch.status != 'committed':
            raise HTTPException(404, 'Запись не найдена.')
        result = _payload(s, batch, user)
        rows = result['rows']
        ids = {row['ledger_id'] for row in rows if row['ledger_id'] is not None}
        entries = {entry.id: entry for entry in s.scalars(select(Ledger).where(Ledger.id.in_(ids)))} if ids else {}
        reversed_ids = set(s.scalars(select(Ledger.reversal_of).where(Ledger.reversal_of.in_(ids)))) if ids else set()
        deleted_ids = deleted_ledger_ids(s)
        request_ids = {row['request_id'] for row in rows if row['request_id'] is not None}
        requests = {item.id: item for item in s.scalars(select(PaymentRequest).where(PaymentRequest.id.in_(request_ids)))} if request_ids and _can_read_requests(user) else {}
        category_names = {category.id: category.name for category, _ in catalog(s, include_retired=True)}
        lines = list(s.scalars(select(BankStatementLine).where(BankStatementLine.batch_id == batch.id)
                               .order_by(BankStatementLine.line, BankStatementLine.id)))
        valid_funding = accounted_funding_line_ids(s, lines, get(s, Account, batch.account_id))
        for row in rows:
            entry = entries.get(row['ledger_id'])
            if row['ledger_id'] is None:
                state = 'unposted'
            elif row['status'] == FUNDING_MATCHED:
                state = 'active' if row['id'] in valid_funding else 'changed'
            elif not entry:
                state = 'missing'
            elif entry.id in deleted_ids or entry.id in reversed_ids or entry.reversal_of is not None:
                state = 'reversed'
            else:
                direction = row['direction']
                account_id = batch.account_id
                matches_side = ((direction == 'out' and entry.account_id == account_id and entry.kind in ('out', 'transfer')) or
                                (direction == 'in' and ((entry.kind == 'in' and entry.account_id == account_id) or
                                                       (entry.kind == 'transfer' and entry.to_account_id == account_id))))
                state = 'active' if matches_side and entry.amount == _minor(row['amount']) and str(entry.date) == row['date'] else 'changed'
            row['operation_state'] = state
            req = requests.get(row['request_id'])
            if not req or not can_view_request(s, user, req):
                row['request_id'] = None
            if state == 'active' and entry.category_id in category_names and (entry.request_id is None or row['request_id'] is not None):
                row.update(category_id=entry.category_id, category_name=category_names[entry.category_id], category_source='existing')
        annotate_budget_fallbacks(s, rows, {row['ledger_id']: entries[row['ledger_id']] for row in rows
            if row['operation_state'] == 'active'})
        lines = list(s.scalars(select(BankStatementLine).where(BankStatementLine.batch_id == batch.id)
                               .order_by(BankStatementLine.line, BankStatementLine.id)))
        created = {line.id for line in lines if line.status in CREATED_BANK_STATUSES}
        summary = _posting_summary(s, batch, lines, get(s, Account, batch.account_id), rows, posted_ids=created, scope='history')
        result['posting_summary'] = summary
        result['full_reconciliation'] = bool(summary['pending']['count'] == 0 and
            summary['ledger_closing'] == summary['bank_closing'] and summary['partial_allowed'])
        return result


@router.get('/api/bank-statements/{id}')
def details(id: int, request: Request):
    """Read actual state after an uncertain commit response, never replay writes."""
    with unit() as s:
        user, _ = session_user(s, request, ('pay_bank', 'import'))
        batch = get(s, BankStatementBatch, id)
        if batch.status == 'preview' and batch.created_by != user.id and user.role != 'admin':
            raise HTTPException(403, 'Предпросмотр доступен загрузившему сотруднику или администратору.')
        return _payload(s, batch, user)
