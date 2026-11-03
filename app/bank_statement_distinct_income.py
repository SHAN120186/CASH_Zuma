"""Explicit proof that two repeated bank document numbers are distinct receipts.

Only the document-number ambiguity is resolved here. Exact source duplicates,
own transfers, plans, prior journals, reversals and the statement equation keep
their ordinary conservative checks. All writes use the regular ledger service.
"""
import hmac
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .bank_statement_historical_requests import (
    _admin, _conservation, _exact_source_guard, _snapshot, _target, _token,
)
from .bank_statement_guard import accounted_line_ids, set_statement_reconciliation
from .bank_statement_models import BankStatementBatch, BankStatementLine
from .bank_statement_unallocated import category_id, ensure_category, NAMES
from .category_catalog import catalog
from .db import Ledger, Receipt, now, unit
from .services import get, log, money, post_ledger


router = APIRouter()


class DistinctPreview(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    distinct_from_line_id: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=1000)
    attest_bank_fact: bool = Field(strict=True)


class DistinctCommit(DistinctPreview):
    token: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirm: bool = Field(strict=True)


def _peer(s, line, batch, account, peer_id):
    peer = get(s, BankStatementLine, peer_id)
    if (peer.id == line.id or peer.batch_id != batch.id or peer.company_id != batch.company_id
            or peer.account_id != account.id or peer.direction != 'in' or peer.date != line.date
            or peer.document_number != line.document_number or peer.correspondent_inn != line.correspondent_inn
            or peer.correspondent_account != line.correspondent_account
            or peer.fingerprint == line.fingerprint or peer.amount == line.amount or peer.amount <= 0
            or not peer.purpose.strip() or not line.purpose.strip() or peer.purpose.strip() == line.purpose.strip()
            or peer.uncertain or not peer.include_in_daily or peer.request_id is not None
            or peer.status not in ('review', 'reviewed', 'manual_distinct_income', 'auto_income_posted', 'income_matched')
            or peer.classification not in ('', 'unresolved', 'receipt')):
        raise HTTPException(409, 'Нужны две разные входящие строки одной выписки с одинаковым номером, датой и ИНН.')
    if peer.ledger_id is not None and peer.id not in accounted_line_ids(s, [peer], account):
        raise HTTPException(409, 'Связанная операция второй строки изменена или отменена.')
    if peer.ledger_id is None and peer.status not in ('review', 'reviewed'):
        raise HTTPException(409, 'Вторая строка не подтверждена действующей операцией.')
    return peer


def _decision(s, line, batch, account, peer_id):
    peer = _peer(s, line, batch, account, peer_id)
    if (not batch.owner_inn or line.correspondent_inn == batch.owner_inn
            or not line.correspondent_account):
        raise HTTPException(409, 'Собственный перевод или неполные реквизиты нельзя подтвердить как отдельное поступление.')
    own_numbers = set(s.scalars(select(BankStatementBatch.account_number).where(
        BankStatementBatch.company_id == batch.company_id, BankStatementBatch.status == 'committed')))
    if line.correspondent_account in own_numbers:
        raise HTTPException(409, 'Счёт корреспондента принадлежит компании. Проверьте собственный перевод.')
    similar = list(s.scalars(select(BankStatementLine).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(BankStatementBatch.status == 'committed',
        BankStatementLine.account_id == account.id, BankStatementLine.direction == 'in',
        BankStatementLine.date == line.date, BankStatementLine.document_number == line.document_number)))
    if {row.id for row in similar} != {line.id, peer.id}:
        raise HTTPException(409, 'Есть другие строки или пересекающиеся выписки с этим документом. Сначала проверьте повторы.')
    _exact_source_guard(s, line, batch, account)
    _conservation(s, batch, account)
    plans = list(s.scalars(select(Receipt).where(Receipt.account_id == account.id,
        Receipt.inn == line.correspondent_inn, Receipt.amount == line.amount)))
    exact = [plan for plan in plans if plan.due_date == line.date and plan.status in ('expected', 'received')]
    if len(exact) > 1 or exact and exact[0].status != 'expected':
        raise HTTPException(409, 'План поступления уже учтён или неоднозначен. Проверьте его отдельно.')
    plan = exact[0] if exact else None
    if plan is not None and s.scalar(select(Ledger.id).where(Ledger.receipt_id == plan.id).limit(1)):
        raise HTTPException(409, 'У плана поступления уже есть операция. Проверьте журнал.')
    if plan is None and any(p.status == 'expected' or abs((p.due_date-line.date).days) <= 7 for p in plans):
        raise HTTPException(409, 'Похожий план поступления имеет другую дату или статус. Проверьте его отдельно.')
    active = {c.id: c for c, retired in catalog(s, batch.company_id) if not retired and c.type == 'income'}
    selected = plan.category_id if plan else category_id(s)
    if selected is not None and selected not in active:
        raise HTTPException(409, 'Статья поступления недоступна для выбранной компании.')
    name = active[selected].name if selected is not None else NAMES['in']
    return peer, plan, selected, name


@router.get('/api/bank-statements/lines/{id}/distinct-income/options')
def distinct_options(id: int, request: Request):
    with unit() as s:
        _admin(s, request)
        line, batch, account = _target(s, id, 'in')
        items = []
        for peer in s.scalars(select(BankStatementLine).where(BankStatementLine.batch_id == batch.id,
                BankStatementLine.id != line.id, BankStatementLine.direction == 'in').order_by(BankStatementLine.id)):
            try:
                _decision(s, line, batch, account, peer.id)
            except HTTPException:
                continue
            items.append({'line_id': peer.id, 'document_number': peer.document_number, 'date': str(peer.date),
                'amount': money(peer.amount), 'currency': account.currency, 'counterparty': peer.correspondent_name,
                'purpose': peer.purpose, 'status': peer.status})
        return {'items': items, 'available': bool(items), 'reason': '' if items else 'Подходящая отдельная строка не найдена или требует проверки.'}


def _preview(s, user, line, batch, account, data):
    if not data.attest_bank_fact:
        raise HTTPException(422, 'Подтвердите, что банк исполнил обе строки как разные операции.')
    peer, plan, selected, name = _decision(s, line, batch, account, data.distinct_from_line_id)
    result = {'line_id': line.id, 'distinct_from_line_id': peer.id, 'amount': money(line.amount),
        'currency': account.currency, 'date': str(line.date), 'counterparty': line.correspondent_name,
        'category_name': name, 'effect': 'distinct_income', 'token': _token(user, 'distinct_income',
            _snapshot(s, line, batch, account), data)}
    return plan, selected, result


@router.post('/api/bank-statements/lines/{id}/distinct-income/preview')
def distinct_preview(id: int, data: DistinctPreview, request: Request):
    with unit() as s:
        user = _admin(s, request)
        line, batch, account = _target(s, id, 'in')
        return _preview(s, user, line, batch, account, data)[2]


@router.post('/api/bank-statements/lines/{id}/distinct-income/commit')
def distinct_commit(id: int, data: DistinctCommit, request: Request):
    if not data.confirm:
        raise HTTPException(422, 'Подтвердите учёт отдельного банковского поступления.')
    with unit(True) as s:
        user = _admin(s, request)
        line, batch, account = _target(s, id, 'in')
        plan, selected, shown = _preview(s, user, line, batch, account, data)
        if not hmac.compare_digest(data.token, shown['token']):
            raise HTTPException(409, 'Данные изменились. Проверьте исключение заново.')
        selected = selected if selected is not None else ensure_category(s)
        op = SimpleNamespace(account_id=account.id, to_account_id=None, category_id=selected,
            kind='in', amount=money(line.amount), date=line.date, counterparty=line.correspondent_name[:160],
            reference='BANK-' + line.fingerprint, note='Разные банковские поступления: ' + data.reason,
            request_id=None, request_version=None, receipt_id=plan.id if plan else None)
        entry = post_ledger(s, user, op, bank_statement_import=True)
        line.ledger_id = entry.id; line.status = 'manual_distinct_income'; line.classification = 'receipt'
        line.reason = ''; line.review_note = data.reason; line.reviewed_by = user.id; line.reviewed_at = now()
        s.flush()
        complete = _conservation(s, batch, account)
        set_statement_reconciliation(s, batch, complete, user)
        log(s, user, 'Подтверждены разные банковские поступления', 'bank_statement_line', line.id,
            'Вторая строка '+str(data.distinct_from_line_id)+'; операция '+str(entry.id)+'; '+data.reason)
        return {'status': line.status, 'line_id': line.id, 'ledger_id': entry.id}
