"""Explicitly link one bank-statement side to an existing internal transfer.

This module never creates or changes a financial ledger operation.  The user
must preview an eligible transfer, select it and confirm with an explanation;
all checks are repeated under the serialized write guard.
"""
import hmac
import json
from hashlib import sha256

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .bank_statement_models import BankStatementBatch, BankStatementLine
from .db import Account, Ledger, now, unit
from .section_access import can_edit_section
from .security import jwt_key, session_user
from .services import deleted_ledger_ids, effective_cashflows, get, log, money

router = APIRouter()


class TransferLink(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    ledger_id: int = Field(ge=1)
    token: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=10, max_length=1000)
    confirm: bool


def _target(s, line_id):
    line = get(s, BankStatementLine, line_id)
    batch = get(s, BankStatementBatch, line.batch_id)
    account = get(s, Account, line.account_id)
    if (batch.status != 'committed' or not batch.source_ready or line.status not in ('review', 'reviewed')
            or line.uncertain or line.direction not in ('in', 'out') or line.ledger_id is not None
            or line.request_id is not None or line.company_id != batch.company_id
            or line.account_id != batch.account_id or account.company_id != batch.company_id
            or account.id != batch.account_id or account.kind != 'bank' or account.currency != batch.currency):
        raise HTTPException(409, 'Эта строка недоступна для сверки перевода.')
    return line, batch, account


def _bindings(s, account_id):
    return set(s.scalars(select(BankStatementBatch.account_number).where(
        BankStatementBatch.account_id == account_id, BankStatementBatch.status == 'committed',
        BankStatementBatch.source_ready.is_(True))))


def _candidates(s, target, batch, account):
    used = set(s.scalars(select(BankStatementLine.ledger_id).join(
        BankStatementBatch, BankStatementBatch.id == BankStatementLine.batch_id).where(
        BankStatementBatch.status == 'committed', BankStatementLine.id != target.id,
        BankStatementLine.account_id == account.id, BankStatementLine.direction == target.direction,
        BankStatementLine.ledger_id.is_not(None))))
    deleted = deleted_ledger_ids(s)
    entries = s.scalars(select(Ledger).where(Ledger.kind == 'transfer', Ledger.date == target.date,
        Ledger.amount == target.amount, Ledger.reversal_of.is_(None),
        Ledger.account_id == account.id if target.direction == 'out' else Ledger.to_account_id == account.id))
    for entry in entries:
        if entry.id in used or entry.id in deleted or not entry.to_account_id or entry.to_account_id == entry.account_id:
            continue
        if not s.scalar(effective_cashflows(s).where(Ledger.id == entry.id)):
            continue
        other_id = entry.to_account_id if target.direction == 'out' else entry.account_id
        other = s.get(Account, other_id)
        if not other or other.company_id != batch.company_id or other.currency != account.currency:
            continue
        known = _bindings(s, other.id)
        # A known number must agree.  If no number has been bound to the other
        # account, the UI must say it is unconfirmed and the user decides.
        if known and target.correspondent_account not in known:
            continue
        confirmed = bool(known and target.correspondent_account in known)
        yield entry, other, confirmed


def _token(user, line, batch, account, entry, other, confirmed):
    def values(obj, fields):
        return {field: str(getattr(obj, field)) for field in fields}
    payload = {'user': user.id, 'company': batch.company_id, 'confirmed': confirmed,
        'line': values(line, ('id', 'batch_id', 'account_id', 'date', 'direction', 'amount', 'document_number',
                              'correspondent_mfo', 'correspondent_account', 'correspondent_name',
                              'correspondent_inn', 'purpose', 'fingerprint', 'status', 'uncertain',
                              'include_in_daily', 'ledger_id', 'request_id', 'classification', 'review_note')),
        'batch': values(batch, ('id', 'account_id', 'account_number', 'currency', 'status')),
        'account': values(account, ('id', 'name', 'kind', 'currency', 'archived')),
        'other': values(other, ('id', 'name', 'kind', 'currency', 'archived')),
        'ledger': values(entry, ('id', 'account_id', 'to_account_id', 'kind', 'amount', 'date',
                                 'counterparty', 'reference', 'note', 'reversal_of'))}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hmac.new(jwt_key().encode(), raw, sha256).hexdigest()


@router.get('/api/bank-statements/lines/{id}/transfer-candidates')
def transfer_candidates(id: int, request: Request):
    with unit() as s:
        user, _ = session_user(s, request, 'pay_bank')
        if not can_edit_section(user, 'ledger'):
            raise HTTPException(403, 'Для сверки перевода нужен доступ к изменению операций.')
        line, batch, account = _target(s, id)
        items = []
        for entry, other, confirmed in _candidates(s, line, batch, account):
            source = account if line.direction == 'out' else other
            destination = other if line.direction == 'out' else account
            items.append({'ledger_id': entry.id, 'date': str(entry.date), 'amount': money(entry.amount),
                'currency': account.currency, 'from_account': source.name, 'to_account': destination.name,
                'reference': entry.reference, 'counterparty_account_confirmed': confirmed,
                'token': _token(user, line, batch, account, entry, other, confirmed)})
        return {'items': items}


@router.post('/api/bank-statements/lines/{id}/transfer-link')
def transfer_link(id: int, data: TransferLink, request: Request):
    if not data.confirm:
        raise HTTPException(422, 'Подтвердите связь строки с переводом.')
    with unit(True) as s:
        user, _ = session_user(s, request, 'pay_bank')
        if not can_edit_section(user, 'ledger'):
            raise HTTPException(403, 'Для сверки перевода нужен доступ к изменению операций.')
        line, batch, account = _target(s, id)
        matches = [item for item in _candidates(s, line, batch, account) if item[0].id == data.ledger_id]
        if len(matches) != 1:
            raise HTTPException(409, 'Перевод изменился, отменён или уже связан с этой стороной счёта.')
        entry, other, confirmed = matches[0]
        if not hmac.compare_digest(data.token, _token(user, line, batch, account, entry, other, confirmed)):
            raise HTTPException(409, 'Данные изменились. Проверьте перевод заново.')
        line.ledger_id = entry.id; line.status = 'manual_transfer_match'; line.classification = 'internal_transfer'
        line.reason = ''; line.review_note = data.reason; line.reviewed_by = user.id; line.reviewed_at = now()
        log(s, user, 'Связан банковский перевод', 'bank_statement_line', line.id,
            json.dumps({'ledger_id': entry.id, 'other_account_id': other.id,
                        'account_confirmed': confirmed, 'reason': data.reason}, ensure_ascii=False))
        return {'status': line.status, 'line_id': line.id, 'ledger_id': entry.id}
