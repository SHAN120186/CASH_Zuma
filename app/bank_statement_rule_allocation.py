"""Explicit, signed allocation of existing bank facts; never creates money."""
import hmac
import json
from hashlib import sha256

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .bank_statement_api import _ledger_edit_token
from .bank_statement_category_rules import load_rules, resolve_rule
from .bank_statement_guard import accounted_line_ids
from .bank_statement_models import BankStatementBatch, BankStatementLine
from .bank_statement_permissions import can_post_statement
from .bank_statement_unallocated import category_id as unallocated_id
from .db import Account, Ledger, unit
from .security import jwt_key, session_user
from .services import budget_state, effective_cashflows, log, money

router = APIRouter()
ELIGIBLE_STATUSES = {'auto_income_posted', 'auto_outgoing_posted', 'manual_distinct_income'}


class AllocationConfirm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirm: bool


def _source(s, request, batch_id):
    user, _ = session_user(s, request, ('pay_bank', 'write'))
    if not can_post_statement(user):
        raise HTTPException(403, 'Недостаточно прав для применения правил выписки.')
    batch = s.get(BankStatementBatch, batch_id)
    if not batch or batch.company_id != s.info.get('company_id'):
        raise HTTPException(404, 'Выписка не найдена.')
    account = s.get(Account, batch.account_id)
    if (batch.status != 'committed' or not batch.source_ready or not batch.balance_matches
            or batch.declared_totals_match is not True or not account or account.archived
            or account.kind != 'bank' or account.company_id != batch.company_id
            or account.currency != batch.currency):
        raise HTTPException(409, 'Сначала подтвердите корректную банковскую выписку действующего счёта.')
    return user, batch, account


def _review(s, user, batch, account):
    context = load_rules(s, batch.company_id)
    lines = list(s.scalars(select(BankStatementLine).where(
        BankStatementLine.batch_id == batch.id).order_by(BankStatementLine.date,
            BankStatementLine.line, BankStatementLine.id)))
    accounted = accounted_line_ids(s, lines, account)
    ids = {line.ledger_id for line in lines if line.ledger_id}
    entries = {entry.id: entry for entry in s.scalars(effective_cashflows(s).where(
        Ledger.id.in_(ids)))} if ids else {}
    unknown = {kind: unallocated_id(s, kind) for kind in ('in', 'out')}
    rows, snapshots, seen, provisional = [], [], set(), {}
    for line in lines:
        entry = entries.get(line.ledger_id)
        if (line.id not in accounted or line.status not in ELIGIBLE_STATUSES or not entry
                or entry.id in seen or entry.request_id is not None or entry.receipt_id is not None
                or entry.kind not in unknown or entry.kind != line.direction
                or entry.account_id != account.id or entry.amount != line.amount or entry.date != line.date
                or entry.reference != 'BANK-' + line.fingerprint
                or entry.category_id != unknown[entry.kind]
                or line.correspondent_inn and line.correspondent_inn == batch.owner_inn):
            continue
        seen.add(entry.id)
        decision = resolve_rule(context, line, account)
        status = decision['category_rule_status']
        row = {'line_id': line.id, 'ledger_id': entry.id, 'date': str(line.date),
            'document_number': line.document_number, 'counterparty': line.correspondent_name,
            'direction': line.direction, 'amount': money(line.amount),
            'from_category_id': entry.category_id, 'category_id': decision.get('category_id'),
            'category_name': decision.get('category_name', ''),
            'rule_ids': decision['category_rule_ids'], 'status': status, 'reason': ''}
        budget = None
        if status == 'matched':
            row['status'] = 'ready'
            if line.direction == 'out':
                key = (row['category_id'], str(line.date)[:7], account.currency)
                extra = provisional.get(key, 0) + line.amount
                budget = budget_state(s, key[0], key[1], key[2], extra)
                if budget['over']:
                    row['status'] = 'budget'
                    row['reason'] = 'Превышен бюджет: операция остаётся в «Не распределено».'
                else:
                    provisional[key] = extra
        elif status == 'conflict':
            row['reason'] = 'Совпали правила с разными статьями.'
        elif status == 'invalid':
            row['reason'] = 'Статья правила недоступна. Проверьте правила.'
        else:
            row['reason'] = 'Подходящее правило не найдено.'
        rows.append(row)
        snapshots.append({'line_id': line.id, 'fingerprint': line.fingerprint,
            'entry': _ledger_edit_token(entry), 'budget': budget, 'decision': row})
    catalog = [{'id': cid, 'name': category.name, 'type': category.type,
        'activity': category.activity, 'retired': retired}
        for cid, (category, retired) in sorted(context['categories'].items())]
    snapshot = {'user': user.id, 'company': batch.company_id, 'batch': batch.id,
        'source': batch.sha256, 'account': account.id, 'currency': account.currency,
        'rules_revision': context['revision'], 'catalog': catalog, 'rows': snapshots,
        'policy': 'only_existing_unallocated_no_new_ledger_budget_over_keeps_unallocated'}
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    token = hmac.new(jwt_key().encode(), encoded, sha256).hexdigest()
    return {'batch_id': batch.id, 'currency': account.currency,
        'revision': context['revision'], 'token': token, 'rows': rows,
        'ready_count': sum(row['status'] == 'ready' for row in rows),
        'remaining_count': sum(row['status'] != 'ready' for row in rows)}


@router.post('/api/bank-statements/{id}/category-rules/preview')
def preview_existing_allocation(id: int, request: Request):
    with unit() as s:
        user, batch, account = _source(s, request, id)
        return _review(s, user, batch, account)


@router.post('/api/bank-statements/{id}/category-rules/commit')
def apply_existing_allocation(id: int, data: AllocationConfirm, request: Request):
    with unit(True) as s:
        user, batch, account = _source(s, request, id)
        if not data.confirm:
            raise HTTPException(422, 'Подтвердите применение правил к существующим операциям.')
        review = _review(s, user, batch, account)
        if not hmac.compare_digest(data.token, review['token']):
            raise HTTPException(409, 'Данные или правила изменились. Проверьте распределение заново.')
        ready = [row for row in review['rows'] if row['status'] == 'ready']
        if not ready:
            raise HTTPException(409, 'Нет операций, готовых к распределению по правилам.')
        for row in ready:
            entry = s.get(Ledger, row['ledger_id'])
            entry.category_id = row['category_id']
            log(s, user, 'Назначена статья банковской операции по правилу', 'bank_statement_line', row['line_id'],
                json.dumps({'ledger_id': entry.id, 'before_category_id': row['from_category_id'],
                    'category_id': entry.category_id, 'rule_ids': row['rule_ids'],
                    'rule_revision': review['revision'], 'reason': 'Применение правил к сохранённой выписке'},
                    ensure_ascii=False))
        s.flush()
        return {'batch_id': batch.id, 'allocated': len(ready),
            'remaining_count': review['remaining_count']}
