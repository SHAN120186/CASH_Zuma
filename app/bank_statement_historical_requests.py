"""Explicit holding-admin accounting of a closed request's historical bank fact.

This exception records an already executed bank movement. It never fabricates
approval, changes an approval date, or authorizes another bank payment.
"""
import hmac
import json
import re
from hashlib import sha256
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import inspect, or_, select

from .bank_statement_guard import accounted_line_ids, set_statement_reconciliation
from .bank_statement_incoming import opening_matches
from .bank_statement_models import BankStatementBatch, BankStatementLine
from .category_catalog import catalog
from .clock import today
from .db import Account, Audit, Category, Ledger, PaymentRequest, Receipt, now, unit
from .security import jwt_key, session_user
from .section_access import can_edit_section
from .services import account_balance, get, log, money, post_ledger


router = APIRouter()
HISTORICAL_ACTION = 'Учтён исторический факт оплаты закрытой заявки'
REQUEST_ID = re.compile(r'(?<![A-Za-z0-9])CF-0*([1-9][0-9]*)(?![A-Za-z0-9])', re.I)


class HistoricalPreview(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=1000)
    attest_bank_fact: bool = Field(strict=True)


class HistoricalCommit(HistoricalPreview):
    token: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirm: bool = Field(strict=True)


def _admin(s, request, *, requests=False):
    user, _ = session_user(s, request, 'write')
    if user.role != 'admin' or not can_edit_section(user, 'ledger') or (requests and not can_edit_section(user, 'requests')):
        raise HTTPException(403, 'Исключение банковской сверки подтверждает только администратор холдинга с правом изменения раздела.')
    return user


def _target(s, line_id, direction):
    line = get(s, BankStatementLine, line_id)
    batch = get(s, BankStatementBatch, line.batch_id)
    account = get(s, Account, line.account_id)
    if (batch.status != 'committed' or not batch.source_ready or account.archived or account.kind != 'bank'
            or line.company_id != batch.company_id or account.company_id != batch.company_id
            or line.account_id != batch.account_id or account.currency != batch.currency
            or line.direction != direction or line.status not in ('review', 'reviewed')
            or line.uncertain or not line.include_in_daily or line.ledger_id is not None or line.request_id is not None
            or line.date < account.opening_date or line.date > today() or line.amount <= 0
            or not line.document_number or not line.correspondent_inn or not line.correspondent_name):
        raise HTTPException(409, 'Строка недоступна для подтверждения отдельного банковского факта.')
    allowed = ('', 'unresolved') if direction == 'out' else ('', 'unresolved', 'receipt')
    if line.classification not in allowed:
        raise HTTPException(409, 'Ранее выбранное назначение операции требует отдельной проверки.')
    from .bank_statement_api import _validate_bank_binding
    _validate_bank_binding(s, account, batch.account_number)
    return line, batch, account


def _values(obj):
    return {field.key: str(getattr(obj, field.key)) for field in inspect(obj).mapper.column_attrs}


def _snapshot(s, line, batch, account):
    """Bind signatures to current account facts, source rows, plans and reversals."""
    batches = list(s.scalars(select(BankStatementBatch).where(BankStatementBatch.account_id == account.id,
        BankStatementBatch.status == 'committed').order_by(BankStatementBatch.id)))
    rows = list(s.scalars(select(BankStatementLine).where(BankStatementLine.batch_id.in_([b.id for b in batches]))
        .order_by(BankStatementLine.id)))
    ledger = list(s.scalars(select(Ledger).where(or_(Ledger.account_id == account.id,
        Ledger.to_account_id == account.id)).order_by(Ledger.id)))
    receipts = list(s.scalars(select(Receipt).where(Receipt.account_id == account.id).order_by(Receipt.id)))
    requests = list(s.scalars(select(PaymentRequest).where(PaymentRequest.account_id == account.id).order_by(PaymentRequest.id)))
    articles = [(c, retired) for c, retired in catalog(s, batch.company_id, include_retired=True)]
    return {'account': _values(account), 'batch': _values(batch), 'line': _values(line),
        'batches': [_values(b) for b in batches], 'rows': [_values(r) for r in rows],
        'ledger': [_values(e) for e in ledger], 'receipts': [_values(r) for r in receipts],
        'requests': [_values(r) for r in requests], 'categories': [(_values(c), retired) for c, retired in articles]}


def _token(user, kind, state, data):
    content = {'user': user.id, 'kind': kind, 'state': state, 'data': data.model_dump(exclude={'token', 'confirm'})}
    return hmac.new(jwt_key().encode(), json.dumps(content, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode(), sha256).hexdigest()


def _conservation(s, batch, account):
    rows = list(s.scalars(select(BankStatementLine).where(BankStatementLine.batch_id == batch.id)))
    accounted = accounted_line_ids(s, rows, account)
    pending_in = sum(row.amount for row in rows if row.id not in accounted and row.direction == 'in')
    pending_out = sum(row.amount for row in rows if row.id not in accounted and row.direction == 'out')
    if (not opening_matches(s, batch, account)
            or account_balance(s, account, batch.period_to) + pending_in - pending_out != batch.closing):
        raise HTTPException(409, 'Остатки и учтённая часть выписки изменились. Проверьте сверку заново.')
    return len(accounted) == len(rows)


def _exact_source_guard(s, line, batch, account):
    other = s.scalar(select(BankStatementLine.id).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(BankStatementBatch.status == 'committed',
        BankStatementLine.account_id == account.id, BankStatementLine.id != line.id,
        BankStatementLine.fingerprint == line.fingerprint).limit(1))
    if other:
        raise HTTPException(409, 'Точная банковская строка уже есть в подтверждённой выписке. Сначала разрешите повтор.')
    ledger = list(s.scalars(select(Ledger).where(or_(Ledger.account_id == account.id, Ledger.to_account_id == account.id))))
    if any(entry.account_id == account.id and (entry.reference in (line.document_number, 'BANK-' + line.fingerprint)
            or (entry.date == line.date and entry.amount == line.amount and entry.kind in (line.direction, 'transfer'))
            or (entry.kind == line.direction and entry.amount == line.amount
                and abs((entry.date-line.date).days) <= 7
                and entry.counterparty.strip().casefold() == line.correspondent_name.strip().casefold()))
            or entry.to_account_id == account.id and entry.kind == 'transfer' and entry.date == line.date and entry.amount == line.amount
            for entry in ledger):
        raise HTTPException(409, 'Похожая операция уже существует или была отменена. Сначала проверьте журнал.')


def _request(s, line, batch, account, request_id):
    req = get(s, PaymentRequest, request_id)
    if (req.status != 'cancelled' or req.account_id != account.id or req.amount != line.amount
            or not req.counterparty_inn or req.counterparty_inn != line.correspondent_inn):
        raise HTTPException(409, 'Нужна закрытая без оплаты заявка с тем же счётом, суммой и ИНН.')
    known_accounts = set(s.scalars(select(BankStatementBatch.account_number).where(
        BankStatementBatch.company_id == batch.company_id, BankStatementBatch.status == 'committed')))
    if (not batch.owner_inn or line.correspondent_inn == batch.owner_inn
            or line.correspondent_account in known_accounts):
        raise HTTPException(409, 'Собственный перевод нельзя учесть как оплату внешней закрытой заявки.')
    marker = {int(value) for value in REQUEST_ID.findall(line.purpose or '')}
    if marker and marker != {req.id}:
        raise HTTPException(409, 'Номер заявки в назначении не совпадает с выбранной заявкой.')
    if s.scalar(select(Ledger.id).where(Ledger.request_id == req.id).limit(1)):
        raise HTTPException(409, 'У заявки уже есть операция оплаты. Проверьте журнал.')
    article = next((c for c, retired in catalog(s, batch.company_id) if c.id == req.category_id and not retired and c.type == 'outcome'), None)
    if article is None:
        raise HTTPException(409, 'Статья закрытой заявки недоступна для учёта банковского факта.')
    _exact_source_guard(s, line, batch, account)
    _conservation(s, batch, account)
    return req, article


def historical_original_status(s, entry):
    """Return the audited closed status for reversal, without altering approval history."""
    if entry.request_id is None:
        return None
    account = s.get(Account, entry.account_id)
    if account is None or s.info.get('company_id') != account.company_id:
        return None
    records = list(s.scalars(select(Audit).where(Audit.entity == 'ledger', Audit.entity_id == str(entry.id),
        Audit.action == HISTORICAL_ACTION).order_by(Audit.id)))
    for record in records:
        try:
            data = json.loads(record.detail)
        except (ValueError, TypeError):
            continue
        if (data.get('ledger_id') == entry.id and data.get('request_id') == entry.request_id
                and data.get('previous_status') == 'cancelled'
                and data.get('company_id') == account.company_id):
            return 'cancelled'
    return None


@router.get('/api/bank-statements/lines/{id}/historical-request/options')
def historical_options(id: int, request: Request):
    with unit() as s:
        _admin(s, request, requests=True)
        line, batch, account = _target(s, id, 'out')
        items = []
        for req in s.scalars(select(PaymentRequest).where(PaymentRequest.account_id == account.id,
                PaymentRequest.status == 'cancelled', PaymentRequest.amount == line.amount,
                PaymentRequest.counterparty_inn == line.correspondent_inn).order_by(PaymentRequest.id)):
            try:
                _request(s, line, batch, account, req.id)
            except HTTPException:
                continue
            items.append({'request_id': req.id, 'number': f'CF-{req.id:05d}', 'counterparty': req.counterparty,
                'counterparty_inn': req.counterparty_inn, 'amount': money(req.amount), 'currency': account.currency,
                'date': str(req.due_date), 'status': req.status, 'version': req.version})
        return {'items': items, 'available': bool(items), 'reason': '' if items else 'Подходящая закрытая заявка не найдена или требует проверки.'}


def _historical_preview(s, user, line, batch, account, data):
    if not data.attest_bank_fact:
        raise HTTPException(422, 'Подтвердите фактическое исполнение операции банком.')
    req, article = _request(s, line, batch, account, data.request_id)
    state = _snapshot(s, line, batch, account)
    return req, {'line_id': line.id, 'request_id': req.id, 'amount': money(line.amount), 'currency': account.currency,
        'date': str(line.date), 'counterparty': req.counterparty, 'category_name': article.name,
        'previous_status': req.status, 'effect': 'historical_request_payment', 'token': _token(user, 'historical_request', state, data)}


@router.post('/api/bank-statements/lines/{id}/historical-request/preview')
def historical_preview(id: int, data: HistoricalPreview, request: Request):
    with unit() as s:
        user = _admin(s, request, requests=True)
        line, batch, account = _target(s, id, 'out')
        return _historical_preview(s, user, line, batch, account, data)[1]


@router.post('/api/bank-statements/lines/{id}/historical-request/commit')
def historical_commit(id: int, data: HistoricalCommit, request: Request):
    if not data.confirm:
        raise HTTPException(422, 'Подтвердите учёт исторического банковского факта.')
    with unit(True) as s:
        user = _admin(s, request, requests=True)
        line, batch, account = _target(s, id, 'out')
        req, shown = _historical_preview(s, user, line, batch, account, data)
        if not hmac.compare_digest(data.token, shown['token']):
            raise HTTPException(409, 'Данные изменились. Проверьте исключение заново.')
        before = _values(req)
        op = SimpleNamespace(account_id=account.id, to_account_id=None, category_id=req.category_id,
            kind='out', amount=money(line.amount), date=line.date, counterparty=req.counterparty,
            reference='BANK-' + line.fingerprint, note='Исторический банковский факт: ' + data.reason,
            request_id=None, request_version=None, receipt_id=None)
        entry = post_ledger(s, user, op, bank_statement_import=True)
        entry.request_id = req.id
        req.status = 'paid'
        line.ledger_id = entry.id; line.request_id = req.id; line.status = 'historical_request_paid'
        line.classification = 'request'; line.reason = ''; line.review_note = data.reason
        line.reviewed_by = user.id; line.reviewed_at = now()
        detail = {'ledger_id': entry.id, 'request_id': req.id, 'previous_status': before['status'],
            'request_before': before, 'line_id': line.id, 'batch_id': batch.id, 'company_id': batch.company_id,
            'reference': entry.reference, 'attest_bank_fact': True, 'reason': data.reason}
        log(s, user, HISTORICAL_ACTION, 'ledger', entry.id, json.dumps(detail, ensure_ascii=False))
        log(s, user, HISTORICAL_ACTION, 'request', req.id, json.dumps(detail, ensure_ascii=False))
        s.flush()
        complete = _conservation(s, batch, account)
        set_statement_reconciliation(s, batch, complete, user)
        return {'status': line.status, 'line_id': line.id, 'request_id': req.id, 'ledger_id': entry.id}
