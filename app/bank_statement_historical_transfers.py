"""Atomically account for an already executed, explicitly verified own transfer.

This router cannot contact a bank, create an account, or convert a currency.
Only a holding administrator can confirm one stored pending debit at a time.
"""
import hmac
import json
from datetime import timedelta
from hashlib import sha256
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from .bank_statement_guard import accounted_line_ids, set_statement_reconciliation
from .bank_statement_models import BankStatementBatch, BankStatementLine
from .bank_statement_unallocated_out import POSSIBLE_FEE
from .clock import today
from .db import Account, Ledger, PaymentRequest, now, unit
from .security import jwt_key, session_user
from .section_access import can_edit_section
from .services import account_balance, day_start_balance, deleted_ledger_ids, effective_cashflows, enforce_reserved_funds, get, log, money, post_ledger

router = APIRouter()
PREFIX = '/api/bank-statements/lines/{id}/historical-transfer'
CREATED_STATUS = 'historical_transfer_posted'


class TransferPreview(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    to_account_id: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=1000)


class TransferConfirmation(TransferPreview):
    preview_token: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirm_bank_fact: bool = Field(strict=True)
    confirm_ownership: bool = Field(strict=True)


def _admin(s, request):
    user, _ = session_user(s, request, 'write')
    if user.role != 'admin' or not can_edit_section(user, 'ledger'):
        raise HTTPException(403, 'Оформить исторический перевод может только администратор холдинга.')
    return user


def _target(s, line_id):
    line = get(s, BankStatementLine, line_id)
    batch = get(s, BankStatementBatch, line.batch_id)
    account = get(s, Account, line.account_id)
    if (batch.status != 'committed' or not batch.source_ready or not batch.balance_matches
            or batch.declared_totals_match is not True or line.status not in ('review', 'reviewed')
            or line.uncertain or not line.include_in_daily or line.occurrence != 1
            or line.direction != 'out' or line.ledger_id is not None or line.request_id is not None
            or line.amount <= 0 or not batch.period_from <= line.date <= batch.period_to
            or line.date > today() or line.date < account.opening_date
            or line.company_id != batch.company_id or line.account_id != batch.account_id
            or account.company_id != batch.company_id or account.kind != 'bank' or account.archived
            or account.currency != batch.currency):
        raise HTTPException(409, 'Строка недоступна для оформления исторического перевода. Обновите сверку.')
    if (not batch.owner_inn or line.correspondent_inn != batch.owner_inn or not line.correspondent_account
            or line.correspondent_account == batch.account_number
            or line.classification not in ('', 'unresolved', 'internal_transfer')
            or POSSIBLE_FEE.search((line.purpose or '') + ' ' + (line.correspondent_name or ''))):
        raise HTTPException(409, 'Реквизиты или классификация не подтверждают собственный перевод.')
    bindings = list(s.scalars(select(BankStatementBatch).where(BankStatementBatch.status == 'committed',
        or_(BankStatementBatch.account_id == account.id, BankStatementBatch.account_number == batch.account_number))))
    if any(other.account_id != account.id or other.account_number != batch.account_number for other in bindings):
        raise HTTPException(409, 'Банковский счёт связан с другими реквизитами. Сначала проверьте соответствие.')
    return line, batch, account


def _destination(s, line, batch, source, account_id):
    account = get(s, Account, account_id)
    if (account.id == source.id or account.company_id != batch.company_id or account.kind != 'bank'
            or account.archived or account.currency != source.currency or account.opening_date > line.date):
        raise HTTPException(409, 'Выберите действующий собственный банковский счёт той же компании и валюты на дату перевода.')
    bindings = list(s.scalars(select(BankStatementBatch).where(BankStatementBatch.status == 'committed',
        BankStatementBatch.source_ready.is_(True), or_(BankStatementBatch.account_id == account.id,
            BankStatementBatch.account_number == line.correspondent_account))))
    if any(other.account_id != account.id or other.account_number != line.correspondent_account
            or other.currency != source.currency for other in bindings):
        raise HTTPException(409, 'Счёт получателя не соответствует подтверждённым банковским реквизитам строки.')
    # A prior explicitly confirmed transfer is also evidence of the mapping;
    # do not let a later unknown destination contradict that reviewed choice.
    pairs = list(s.execute(select(BankStatementLine, Ledger).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).join(Ledger, Ledger.id == BankStatementLine.ledger_id).where(
        BankStatementBatch.status == 'committed', BankStatementBatch.source_ready.is_(True),
        BankStatementLine.status.in_((CREATED_STATUS, 'manual_transfer_match')), Ledger.kind == 'transfer')))
    ids = {entry.id for _, entry in pairs}
    active = set(s.scalars(effective_cashflows(s).where(Ledger.id.in_(ids)).with_only_columns(Ledger.id))) if ids else set()
    deleted = deleted_ledger_ids(s)
    confirmed_mapping = False
    for old, entry in pairs:
        if entry.id not in active or entry.id in deleted or old.date != entry.date or old.amount != entry.amount or not old.correspondent_account:
            continue
        outgoing = old.direction == 'out' and old.account_id == entry.account_id
        incoming = old.direction == 'in' and old.account_id == entry.to_account_id
        if not outgoing and not incoming:
            continue
        other_id = entry.to_account_id if outgoing else entry.account_id
        if ((other_id == account.id and old.correspondent_account != line.correspondent_account)
                or (old.correspondent_account == line.correspondent_account and other_id != account.id)):
            raise HTTPException(409, 'Счёт получателя не соответствует подтверждённым банковским реквизитам строки.')
        confirmed_mapping = confirmed_mapping or (other_id == account.id and old.correspondent_account == line.correspondent_account)
    return account, bool(bindings or confirmed_mapping)


def _no_existing_movement(s, line, source, destination):
    other = s.scalar(select(BankStatementLine.id).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(BankStatementBatch.status == 'committed',
        BankStatementLine.id != line.id, BankStatementLine.account_id == source.id,
        BankStatementLine.fingerprint == line.fingerprint).limit(1))
    if other:
        raise HTTPException(409, 'Найден возможный повтор банковской строки. Сначала проверьте исходные операции.')
    references = {'BANK-' + line.fingerprint}
    if line.document_number:
        references.add(line.document_number)
    existing = s.scalar(select(Ledger.id).where(Ledger.account_id == source.id,
        or_(Ledger.reference.in_(references),
            (Ledger.kind == 'out') & (Ledger.date == line.date) & (Ledger.amount == line.amount),
            (Ledger.kind == 'transfer') & (Ledger.to_account_id == destination.id) & (Ledger.amount == line.amount)
            & (Ledger.date >= line.date - timedelta(days=7)) & (Ledger.date <= line.date + timedelta(days=7)))).limit(1))
    if existing:
        # Reversed/deleted movements are deliberately included. Recreating an
        # old bank fact after a correction requires a separate reviewed action.
        raise HTTPException(409, 'Есть похожая или отменённая операция. Проверьте уже оформленный перевод перед созданием новой записи.')


def _state(s, batch, account, *, delta=0, movement_date=None, remove_pending_id=None):
    lines = list(s.scalars(select(BankStatementLine).where(BankStatementLine.batch_id == batch.id)
        .order_by(BankStatementLine.line, BankStatementLine.id)))
    accounted = accounted_line_ids(s, lines, account)
    pending = [line for line in lines if line.id not in accounted and line.id != remove_pending_id]
    credit = sum(line.amount for line in pending if line.direction == 'in')
    debit = sum(line.amount for line in pending if line.direction == 'out')
    closing = account_balance(s, account, batch.period_to) + delta
    opening = day_start_balance(s, account, batch.period_from)
    if movement_date is not None and movement_date < batch.period_from:
        opening += delta
    totals_match = (sum(line.amount for line in lines if line.direction == 'in') == batch.credit_total
        and sum(line.amount for line in lines if line.direction == 'out') == batch.debit_total)
    valid = bool(batch.source_ready and batch.balance_matches and batch.declared_totals_match is True
        and totals_match and opening == batch.opening and closing + credit - debit == batch.closing)
    return {'bank_closing': batch.closing, 'ledger_closing': closing, 'pending_credit_total': credit,
        'pending_debit_total': debit, 'pending_count': len(pending), 'valid': valid,
        'full_reconciliation': bool(valid and not pending and closing == batch.closing),
        'source_rows': [_values(line) for line in lines]}


def _values(obj):
    return {column.key: str(getattr(obj, column.key)) for column in obj.__table__.columns}


def _public_reconciliation(before, after, destination_before, destination_after, amount):
    return {'bank_closing': money(after['bank_closing']), 'ledger_closing': money(before['ledger_closing']),
        'pending_credit_total': money(after['pending_credit_total']),
        'pending_debit_total': money(before['pending_debit_total']),
        'projected_closing': money(after['ledger_closing']),
        'projected_pending_debit_total': money(after['pending_debit_total']),
        'conservative_closing': money(after['ledger_closing'] - after['pending_debit_total']),
        'full_reconciliation': after['full_reconciliation'], 'pending_count': after['pending_count'],
        'destination_balance_before': money(destination_before), 'destination_balance_after': money(destination_after),
        'pending_debit_released': money(amount)}


def _context(s, line_id, account_id, user, reason):
    line, batch, source = _target(s, line_id)
    destination, confirmed = _destination(s, line, batch, source, account_id)
    _no_existing_movement(s, line, source, destination)
    before = _state(s, batch, source)
    after = _state(s, batch, source, delta=-line.amount, movement_date=line.date, remove_pending_id=line.id)
    if not before['valid'] or not after['valid']:
        raise HTTPException(409, 'Остатки и непроведённые строки выписки не согласуются. Сначала обновите сверку.')
    # Preserve all other confirmed source and destination statements. This
    # endpoint never guesses or automatically links an opposite bank row.
    affected = []
    others = list(s.scalars(select(BankStatementBatch).where(BankStatementBatch.status == 'committed',
        BankStatementBatch.source_ready.is_(True), BankStatementBatch.id != batch.id,
        BankStatementBatch.account_id.in_((source.id, destination.id)), BankStatementBatch.period_to >= line.date)
        .order_by(BankStatementBatch.id)))
    for other in others:
        account = source if other.account_id == source.id else destination
        delta = -line.amount if account.id == source.id else line.amount
        projected = _state(s, other, account, delta=delta, movement_date=line.date)
        if not projected['valid']:
            raise HTTPException(409, 'Перевод нарушит сверку другой подтверждённой выписки. Сначала проверьте связанную сторону.')
        affected.append({'batch': _values(other), 'state': projected})
    try:
        enforce_reserved_funds(s, source, line.date, line.amount, include_bank_hold=False)
    except HTTPException as exc:
        raise HTTPException(409, 'Исторический перевод не оформлен: недостаточно учтённых средств или покрытия утверждённых заявок. '
            'Сначала проверьте непроведённые поступления.') from exc
    account_ids = (source.id, destination.id)
    ledger = list(s.scalars(select(Ledger).where(or_(Ledger.account_id.in_(account_ids),
        Ledger.to_account_id.in_(account_ids))).order_by(Ledger.id)))
    reservations = list(s.scalars(select(PaymentRequest).where(PaymentRequest.account_id.in_(account_ids))
        .order_by(PaymentRequest.id)))
    contents = {'mode': 'historical_transfer', 'user': user.id, 'company': batch.company_id,
        'line': _values(line), 'batch': _values(batch), 'source': _values(source), 'destination': _values(destination),
        'confirmed': confirmed, 'reason': reason, 'before': before, 'after': after, 'affected': affected,
        'ledger': [_values(entry) for entry in ledger],
        'reservations': [{key: str(getattr(row, key)) for key in ('id', 'status', 'version', 'amount', 'due_date')}
                         for row in reservations], 'deleted_ledger_ids': sorted(deleted_ledger_ids(s))}
    token = hmac.new(jwt_key().encode(), json.dumps(contents, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode(), sha256).hexdigest()
    destination_before = account_balance(s, destination, line.date)
    payload = {'line_id': line.id, 'batch_id': batch.id,
        'source_account': {'id': source.id, 'name': source.name, 'currency': source.currency},
        'destination_account': {'id': destination.id, 'name': destination.name, 'currency': destination.currency},
        'operation': {'kind': 'transfer', 'date': str(line.date), 'amount': money(line.amount), 'currency': source.currency,
            'reference': 'BANK-' + line.fingerprint, 'correspondence_confirmed': confirmed},
        'reconciliation': _public_reconciliation(before, after, destination_before, destination_before + line.amount, line.amount),
        'preview_token': token}
    return line, batch, source, destination, others, payload


@router.get(PREFIX + '/options')
def transfer_options(id: int, request: Request):
    with unit() as s:
        _admin(s, request)
        line, batch, source = _target(s, id)
        accounts = []
        for candidate in s.scalars(select(Account).where(Account.id != source.id, Account.company_id == batch.company_id,
                Account.kind == 'bank', Account.archived.is_(False), Account.currency == source.currency,
                Account.opening_date <= line.date).order_by(Account.name, Account.id)):
            try:
                account, confirmed = _destination(s, line, batch, source, candidate.id)
            except HTTPException:
                continue
            accounts.append({'id': account.id, 'name': account.name, 'currency': account.currency,
                'correspondence_confirmed': confirmed})
        return {'line': {'id': line.id, 'batch_id': batch.id, 'date': str(line.date), 'document_number': line.document_number,
                    'amount': money(line.amount), 'currency': source.currency, 'correspondent_account': line.correspondent_account},
                'source_account': {'id': source.id, 'name': source.name, 'currency': source.currency}, 'accounts': accounts}


@router.post(PREFIX + '/preview')
def transfer_preview(id: int, data: TransferPreview, request: Request):
    with unit() as s:
        user = _admin(s, request)
        return _context(s, id, data.to_account_id, user, data.reason)[-1]


@router.post(PREFIX + '/commit')
def transfer_commit(id: int, data: TransferConfirmation, request: Request):
    if data.confirm_bank_fact is not True or data.confirm_ownership is not True:
        raise HTTPException(422, 'Подтвердите уже выполненный банковский перевод и принадлежность счёта получателя компании.')
    with unit(True) as s:
        user = _admin(s, request)
        line, batch, source, destination, others, shown = _context(s, id, data.to_account_id, user, data.reason)
        if not hmac.compare_digest(data.preview_token, shown['preview_token']):
            raise HTTPException(409, 'Данные исторического перевода изменились. Проверьте строку заново.')
        operation = SimpleNamespace(account_id=source.id, to_account_id=destination.id, category_id=None,
            kind='transfer', amount=money(line.amount), date=line.date, counterparty=destination.name,
            reference='BANK-' + line.fingerprint, note='Уже выполненный банковский перевод; выписка №' + str(batch.id)
                + ', документ ' + line.document_number + '. ' + data.reason,
            request_id=None, request_version=None, receipt_id=None)
        entry = post_ledger(s, user, operation, bank_statement_fact=True)
        line.ledger_id = entry.id; line.status = CREATED_STATUS; line.classification = 'internal_transfer'; line.reason = ''
        line.review_note = data.reason; line.reviewed_by = user.id; line.reviewed_at = now()
        s.flush()
        actual = _state(s, batch, source)
        if not actual['valid'] or any(not _state(s, other, source if other.account_id == source.id else destination)['valid'] for other in others):
            raise HTTPException(409, 'Перевод нарушает сверку банковских остатков. Изменения отменены.')
        set_statement_reconciliation(s, batch, actual['full_reconciliation'], user)
        log(s, user, 'Оформлен исторический банковский перевод', 'bank_statement_line', line.id,
            json.dumps({'ledger_id': entry.id, 'batch_id': batch.id, 'to_account_id': destination.id,
                'reason': data.reason, 'confirmed_bank_fact': True, 'confirmed_ownership': True}, ensure_ascii=False))
        destination_balance = account_balance(s, destination, line.date)
        return {'ok': True, 'line_id': line.id, 'ledger_id': entry.id, 'status': CREATED_STATUS,
            'full_reconciliation': actual['full_reconciliation'],
            'reconciliation': _public_reconciliation(actual, actual, destination_balance, destination_balance, line.amount)}
