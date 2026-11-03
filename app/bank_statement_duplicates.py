"""Manual, audited linking of a possible repeat to an earlier bank event.

No journal entry is created. Fingerprint alone is not sufficient: the user must
select an earlier confirmed row with an active matching ledger entry, preview
it and explicitly confirm with an explanation. Company scoping is mandatory.
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
LINKED = frozenset({'auto_paid', 'paid_match', 'manual_paid', 'manual_match',
                    'auto_income_posted', 'income_matched', 'auto_fee_posted', 'fee_matched',
                    'auto_interest_posted', 'interest_matched', 'other_matched', 'auto_outgoing_posted', 'manual_transfer_match'})


class DuplicateResolution(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    prior_line_id: int = Field(ge=1)
    token: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=10, max_length=1000)
    confirm: bool


def _target(s, line_id):
    line = get(s, BankStatementLine, line_id)
    batch = get(s, BankStatementBatch, line.batch_id)
    account = get(s, Account, line.account_id)
    if (batch.status != 'committed' or not batch.source_ready or line.status != 'ambiguous_duplicate'
            or not line.uncertain or line.ledger_id is not None or line.request_id is not None
            or line.company_id != batch.company_id or line.account_id != batch.account_id
            or account.id != batch.account_id or account.company_id != batch.company_id):
        raise HTTPException(409, 'Эта строка недоступна для разрешения повтора.')
    return line, batch, account


def _matches(s, target, batch, account):
    """Candidate is older, exact and backed by one active journal operation."""
    used = set(s.scalars(select(BankStatementLine.ledger_id).where(
        BankStatementLine.batch_id == batch.id, BankStatementLine.id != target.id,
        BankStatementLine.ledger_id.is_not(None))))
    pairs = s.execute(select(BankStatementLine, BankStatementBatch).join(
        BankStatementBatch, BankStatementBatch.id == BankStatementLine.batch_id).where(
        BankStatementBatch.status == 'committed', BankStatementBatch.source_ready.is_(True),
        BankStatementBatch.id != batch.id, BankStatementLine.account_id == account.id,
        BankStatementLine.fingerprint == target.fingerprint,
        BankStatementLine.status.in_(LINKED), BankStatementLine.ledger_id.is_not(None),
        BankStatementLine.include_in_daily.is_(True), BankStatementLine.uncertain.is_(False))
        .order_by(BankStatementBatch.committed_at, BankStatementLine.id))
    deleted = deleted_ledger_ids(s)
    for prior, prior_batch in pairs:
        if (prior.company_id != batch.company_id or prior_batch.company_id != batch.company_id
                or prior_batch.account_id != account.id or prior_batch.account_number != batch.account_number
                or prior_batch.currency != batch.currency or prior.ledger_id in used
                or not prior_batch.committed_at or not batch.committed_at):
            continue
        if ((prior_batch.committed_at, prior_batch.id) >= (batch.committed_at, batch.id)):
            continue
        if any(getattr(prior, field) != getattr(target, field) for field in (
                'document_number', 'correspondent_mfo', 'correspondent_account',
                'correspondent_name', 'correspondent_inn', 'purpose')):
            continue
        entry = s.get(Ledger, prior.ledger_id)
        if not entry or entry.id in deleted:
            continue
        active = s.scalar(effective_cashflows(s).where(Ledger.id == entry.id))
        side_matches = (entry.kind == 'transfer' and prior.status == 'manual_transfer_match' and (
            (target.direction == 'out' and entry.account_id == account.id) or
            (target.direction == 'in' and entry.to_account_id == account.id))) or (
            prior.status != 'manual_transfer_match' and entry.kind == target.direction and entry.account_id == account.id)
        if (not active or not side_matches
                or entry.amount != target.amount or entry.date != target.date
                or prior.date != target.date or prior.amount != target.amount
                or prior.direction != target.direction):
            continue
        yield prior, prior_batch, entry


def _token(user, target, batch, prior, prior_batch, entry):
    """Bind preview to actual rows, ledger metadata, company and acting user."""
    def values(obj, keys):
        return {key: str(getattr(obj, key)) for key in keys}
    payload = {'user': user.id, 'company': batch.company_id,
        'target': values(target, ('id', 'batch_id', 'account_id', 'date', 'direction', 'amount',
                                  'document_number', 'correspondent_mfo', 'correspondent_account',
                                  'correspondent_name', 'correspondent_inn', 'purpose',
                                  'fingerprint', 'status', 'uncertain', 'include_in_daily', 'ledger_id',
                                  'request_id', 'classification', 'review_note')),
        'batch': values(batch, ('id', 'account_id', 'account_number', 'currency', 'status', 'committed_at')),
        'prior': values(prior, ('id', 'batch_id', 'account_id', 'date', 'direction', 'amount',
                                'document_number', 'correspondent_mfo', 'correspondent_account',
                                'correspondent_name', 'correspondent_inn', 'purpose', 'fingerprint',
                                'status', 'uncertain', 'include_in_daily', 'ledger_id', 'classification')),
        'prior_batch': values(prior_batch, ('id', 'account_id', 'account_number', 'currency', 'status', 'committed_at')),
        'ledger': values(entry, ('id', 'account_id', 'to_account_id', 'kind', 'amount', 'date', 'category_id', 'counterparty',
                                 'reference', 'note', 'request_id', 'receipt_id', 'reversal_of'))}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hmac.new(jwt_key().encode(), encoded, sha256).hexdigest()


@router.get('/api/bank-statements/lines/{id}/duplicate-candidates')
def duplicate_candidates(id: int, request: Request):
    with unit() as s:
        user, _ = session_user(s, request, 'pay_bank')
        if not can_edit_section(user, 'ledger'):
            raise HTTPException(403, 'Для разрешения повтора нужен доступ к изменению операций.')
        target, batch, account = _target(s, id)
        items = []
        for prior, prior_batch, entry in _matches(s, target, batch, account):
            items.append({'line_id': prior.id, 'batch_id': prior_batch.id, 'date': str(prior.date),
                          'document_number': prior.document_number, 'account_name': account.name,
                          'amount': money(prior.amount), 'currency': account.currency, 'ledger_id': entry.id,
                          'token': _token(user, target, batch, prior, prior_batch, entry)})
        return {'items': items}


@router.post('/api/bank-statements/lines/{id}/duplicate-resolve')
def duplicate_resolve(id: int, data: DuplicateResolution, request: Request):
    if not data.confirm:
        raise HTTPException(422, 'Подтвердите связь повторной строки с операцией.')
    with unit(True) as s:
        user, _ = session_user(s, request, 'pay_bank')
        if not can_edit_section(user, 'ledger'):
            raise HTTPException(403, 'Для разрешения повтора нужен доступ к изменению операций.')
        target, batch, account = _target(s, id)
        matches = [item for item in _matches(s, target, batch, account) if item[0].id == data.prior_line_id]
        if len(matches) != 1:
            raise HTTPException(409, 'Предыдущая операция изменилась или уже связана с этой выпиской.')
        prior, prior_batch, entry = matches[0]
        if not hmac.compare_digest(data.token, _token(user, target, batch, prior, prior_batch, entry)):
            raise HTTPException(409, 'Данные изменились. Проверьте повтор заново.')
        target.ledger_id = entry.id
        target.status = 'duplicate'; target.uncertain = False; target.include_in_daily = False
        target.classification = prior.classification
        target.reason = ''; target.review_note = data.reason
        target.reviewed_by = user.id; target.reviewed_at = now()
        log(s, user, 'Разрешён повтор выписки', 'bank_statement_line', target.id,
            json.dumps({'source_line_id': prior.id, 'ledger_id': entry.id, 'reason': data.reason}, ensure_ascii=False))
        return {'status': 'duplicate', 'line_id': target.id, 'ledger_id': entry.id}
