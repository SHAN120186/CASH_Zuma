"""Explicit company rules for already-eligible external bank movements.

Rules choose an article; they never prove a payment, waive a duplicate check,
or change an existing ledger entry. Matching is deterministic and SELECT-only.
"""
import json
import re
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .category_catalog import catalog
from .db import Audit, Setting, unit
from .security import perms_of, session_user
from .section_access import can_edit_section
from .services import log

router = APIRouter()
RULE_BUDGET_POLICY = 'unallocated_on_budget_overrun'
RULE_METADATA = ('category_rule_ids', 'category_rule_status', 'category_rule_revision',
                 'category_rule_budget_policy', 'intended_category_id')


class CategoryRule(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: str = Field(default='', max_length=64, pattern=r'^[A-Za-z0-9_-]*$')
    direction: Literal['in', 'out']
    currency: Literal['UZS', 'USD', 'EUR'] | None = None
    inn: str = Field(default='', max_length=20, pattern=r'^[0-9]*$')
    counterparty: str = Field(default='', max_length=240)
    purpose_contains: str = Field(default='', max_length=300)
    contract_contains: str = Field(default='', max_length=200)
    category_id: int = Field(ge=1)
    category_name: str | None = Field(default=None, max_length=160)
    category_valid: bool | None = None
    enabled: bool = Field(default=True, strict=True)


class RulesUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str = Field(pattern=r'^[0-9a-f]{64}$')
    items: list[CategoryRule] = Field(max_length=200)


def normalized(value):
    return ' '.join((value or '').split()).casefold()


def _company(s, company_id=None):
    selected = s.info.get('company_id')
    company_id = company_id if company_id is not None else selected
    if company_id is None or selected is not None and selected != company_id:
        raise HTTPException(404, 'Компания правил банковской выписки не найдена.')
    return company_id


def setting_key(s, company_id=None):
    return f'company:{_company(s, company_id)}:bank_statement_category_rules'


def _canonical(items):
    return json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _revision(company_id, items, generation):
    return sha256(_canonical({'company_id': company_id, 'items': items, 'generation': generation}).encode()).hexdigest()


def _normalized_rule(rule, *, assign_id=False):
    item = rule.model_dump(exclude={'category_name', 'category_valid'})
    for field in ('counterparty', 'purpose_contains', 'contract_contains'):
        item[field] = ' '.join(item[field].split())
    if item['inn'] and len(item['inn']) < 5:
        raise ValueError('Для правила укажите ИНН не короче 5 цифр.')
    if item['counterparty'] and (len(item['counterparty']) < 2 or not any(c.isalnum() for c in item['counterparty'])):
        raise ValueError('Для правила укажите точное название контрагента.')
    for field in ('purpose_contains', 'contract_contains'):
        value = item[field]
        if value and len(value) < 5:
            raise ValueError('Фрагмент назначения или договора должен содержать не менее 5 символов.')
    meaningful_purpose = any(c.isalpha() for c in item['purpose_contains'])
    contract = item['contract_contains']
    meaningful_contract = (any(c.isalpha() for c in contract)
        or sum(c.isdigit() for c in contract) >= 3 and bool(re.search(r'[-/№]', contract)))
    if not (item['inn'] or item['counterparty'] or meaningful_purpose or meaningful_contract):
        raise ValueError('Укажите точный ИНН, название контрагента или содержательный фрагмент назначения либо договора.')
    if not item['id']:
        if not assign_id:
            raise ValueError('Missing stored rule identity')
        item['id'] = uuid4().hex
    return item


def load_rules(s, company_id=None):
    """Load rules and their current catalog once; no per-bank-line queries."""
    company_id = _company(s, company_id)
    row = s.get(Setting, setting_key(s, company_id))
    warning = ''
    items = []
    generation = ''
    try:
        config = json.loads(row.value) if row else {'version': 1, 'items': []}
        if not isinstance(config, dict) or config.get('version') != 1 or not isinstance(config.get('items'), list) or len(config['items']) > 200:
            raise ValueError('Invalid rules document')
        items = [_normalized_rule(CategoryRule.model_validate(item)) for item in config['items']]
        if len({item['id'] for item in items}) != len(items):
            raise ValueError('Duplicate stored rule identity')
        generation = config.get('generation', '')
        if not isinstance(generation, str) or generation and not re.fullmatch(r'[0-9a-f]{32}', generation):
            raise ValueError('Invalid rules generation')
        revision = _revision(company_id, items, generation)
    except (ValueError, TypeError, AttributeError):
        warning = 'Сохранённые правила статей повреждены. До исправления применяется «Не распределено».'
        revision = sha256((row.value if row else '').encode()).hexdigest()
        items = []
    categories = {category.id: (category, retired) for category, retired in catalog(s, company_id, include_retired=True)}
    return {'revision': revision, 'generation': generation, 'items': items, 'categories': categories,
        'warning': warning, 'company_id': company_id}


def _valid_category(context, category_id, direction):
    entry = context['categories'].get(category_id)
    return bool(entry and not entry[1] and entry[0].activity != 'unclassified' and entry[0].company_id == context['company_id']
        and entry[0].type == ('income' if direction == 'in' else 'outcome'))


def resolve_rule(context, line, account):
    """All supplied criteria must agree; conflicting articles stay unallocated."""
    purpose = normalized(line.purpose)
    counterparty = normalized(line.correspondent_name)
    matches = [rule for rule in context['items'] if rule['enabled'] and rule['direction'] == line.direction
        and (rule['currency'] is None or rule['currency'] == account.currency)
        and (not rule['inn'] or rule['inn'] == (line.correspondent_inn or '').strip())
        and (not rule['counterparty'] or normalized(rule['counterparty']) == counterparty)
        and (not rule['purpose_contains'] or normalized(rule['purpose_contains']) in purpose)
        and (not rule['contract_contains'] or normalized(rule['contract_contains']) in purpose)]
    result = {'category_rule_ids': sorted(rule['id'] for rule in matches),
        'category_rule_revision': context['revision'], 'category_rule_status': 'none'}
    if not matches:
        return result
    if any(not _valid_category(context, rule['category_id'], line.direction) for rule in matches):
        result['category_rule_status'] = 'invalid'
        return result
    categories = {rule['category_id'] for rule in matches}
    if len(categories) != 1:
        result['category_rule_status'] = 'conflict'
        return result
    category_id = next(iter(categories))
    category = context['categories'][category_id][0]
    result.update(category_rule_status='matched', category_id=category_id,
        category_name=category.name, category_source='bank_rule')
    if line.direction == 'out':
        result.update(category_rule_budget_policy=RULE_BUDGET_POLICY, intended_category_id=category_id)
    return result


def _public(context, user):
    items = []
    for item in context['items']:
        entry = context['categories'].get(item['category_id'])
        items.append({**item, 'category_name': entry[0].name if entry else '',
            'category_valid': _valid_category(context, item['category_id'], item['direction'])})
    return {'revision': context['revision'], 'can_manage': 'write' in perms_of(user) and can_edit_section(user, 'ledger'),
        'items': items, 'warning': context['warning']}


def annotate_budget_fallbacks(s, rows, entries):
    """Show immutable fallback evidence only while its active fact is unallocated."""
    from .bank_statement_unallocated import category_id
    unallocated = category_id(s, 'out')
    if unallocated is None:
        return
    targets = {row['id']: row for row in rows if row.get('ledger_id') in entries
        and entries[row['ledger_id']].category_id == unallocated and row.get('direction') == 'out'}
    if not targets:
        return
    audits = s.scalars(select(Audit).where(Audit.entity == 'bank_statement_line',
        Audit.action == 'Применено правило статьи банковской выписки',
        Audit.entity_id.in_([str(key) for key in targets])).order_by(Audit.id.desc()))
    seen = set()
    for audit in audits:
        try:
            line_id = int(audit.entity_id)
            if line_id in seen:
                continue
            seen.add(line_id)
            detail = json.loads(audit.detail)
        except (ValueError, TypeError):
            continue
        row = targets[line_id]
        if not isinstance(detail, dict) or detail.get('fallback') != 'budget_overrun' or detail.get('ledger_id') != row.get('ledger_id'):
            continue
        row.update(category_rule_status='budget_fallback', category_rule_ids=detail.get('rule_ids', []),
            category_rule_revision=detail.get('rule_revision'), intended_category_id=detail.get('intended_category_id'),
            intended_category_name=detail.get('intended_category_name', ''),
            category_rule_fallback_reason=detail.get('fallback_reason', ''))


@router.get('/api/bank-statements/category-rules')
def category_rules(request: Request):
    with unit() as s:
        user, _ = session_user(s, request, ('pay_bank', 'import', 'write'))
        return _public(load_rules(s), user)


@router.put('/api/bank-statements/category-rules')
def update_category_rules(data: RulesUpdate, request: Request):
    with unit(True) as s:
        user, _ = session_user(s, request, 'write')
        if not can_edit_section(user, 'ledger'):
            raise HTTPException(403, 'Для изменения правил нужен доступ к изменению операций.')
        context = load_rules(s)
        if not hmac_compare(data.revision, context['revision']):
            raise HTTPException(409, 'Правила статей изменились. Обновите список перед сохранением.')
        try:
            items = [_normalized_rule(item, assign_id=True) for item in data.items]
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if len({item['id'] for item in items}) != len(items):
            raise HTTPException(422, 'Идентификаторы правил не должны повторяться.')
        if any(not _valid_category(context, item['category_id'], item['direction']) for item in items):
            raise HTTPException(422, 'Выберите действующую статью нужного направления этой компании для каждого правила.')
        if items == context['items'] and not context['warning']:
            return _public(context, user)
        key = setting_key(s)
        row = s.get(Setting, key)
        generation = uuid4().hex
        value = _canonical({'version': 1, 'generation': generation, 'items': items})
        if row is None:
            s.add(Setting(key=key, value=value))
        else:
            row.value = value
        revision = _revision(context['company_id'], items, generation)
        log(s, user, 'Изменены правила статей банковской выписки', 'setting', key,
            _canonical({'before_revision': context['revision'], 'after_revision': revision,
                'before': context['items'], 'after': items}))
        s.flush()
        return _public({**context, 'items': items, 'generation': generation, 'revision': revision, 'warning': ''}, user)


def hmac_compare(first, second):
    from hmac import compare_digest
    return compare_digest(first, second)
