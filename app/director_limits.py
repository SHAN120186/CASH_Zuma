"""Верхние лимиты согласования, отдельно от порогов участия директора.

Проверяются актуальные полномочия при окончательном согласовании, включая
заявки, отправленные до введения лимитов. Уже утверждённые оплаты не меняются.
"""
import re
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from fastapi import HTTPException
from .db import Category, Company, Setting, now

LIMIT_FIELDS = {cur: 'director_limit_' + cur.lower() for cur in ('UZS', 'USD', 'EUR')}
# Колонка «Лимит дир» таблицы владельца от 05.11.2026, в сотых долях UZS.
# «Без лимит» соответствует NULL. Пустые значения и формулы не выдумываются.
TABLE_LIMITS = {'12': 0, '65': 0, '70': 0, '78': 0, '98': 0, '17': 10_000_000_000}
NAME_CODES = {
    'Расходы на маркетинг (бонус)': '12',
    'Расчеты по предоставленным займам 4720 (Моддий ёрдам тарикасидаги туловлар)': '65',
    'Долгосрочные займы 7820': '70', 'Само учун карз': '78',
    'Девидент': '98', 'Дивиденды': '98',
    'Закуп основных средств': '17', 'Оборудование': '17',
}


def apply_default_director_limit(category):
    """Код 1С имеет приоритет, затем старый код в названии и точное имя."""
    if category.type != 'outcome' or category.director_limit_changed_at is not None:
        return
    code = (category.code or '').strip()
    if not code:
        # Тот же прежний формат, который поддерживает импорт бюджета.
        prefix = re.match(r'^(\d+)\s*[·|—]\s*', category.name)
        code = prefix.group(1) if prefix else NAME_CODES.get(category.name)
    if code in TABLE_LIMITS:
        category.director_limit_uzs = TABLE_LIMITS[code]
        category.director_limit_changed_at = now()


def migrate_director_limits(engine):
    with Session(engine) as s, s.begin():
        if engine.dialect.name != 'sqlite':
            s.execute(text('SELECT pg_advisory_xact_lock(7312801)'))
        if s.get(Setting, 'director_upper_limits_v1'):
            return
        companies = select(Company.id).where(Company.code != 'UNASSIGNED')
        for category in s.scalars(select(Category).where(Category.company_id.in_(companies))):
            apply_default_director_limit(category)
        s.add(Setting(key='director_upper_limits_v1', value='1'))


def limits_json(category):
    from .services import money
    return {cur: money(getattr(category, field)) if getattr(category, field) is not None else None
            for cur, field in LIMIT_FIELDS.items()}


def request_director_limit(s, request, currency, batch=None):
    """Лимит каждой статьи; при одной статье сумма и комиссия складываются."""
    from .services import get, money, amount_label
    from .cash_funding import funding_for
    funding = batch['fundings'].get(request.id) if batch is not None else funding_for(s, request.id)
    amounts = {request.category_id: request.amount}
    if funding and funding.fee_amount:
        amounts[funding.fee_category_id] = amounts.get(funding.fee_category_id, 0) + funding.fee_amount
    items = []
    reason = ''
    for category_id, value in amounts.items():
        category = batch['categories'][category_id] if batch is not None else get(s, Category, category_id)
        limit = getattr(category, LIMIT_FIELDS[currency])
        exceeded = limit is not None and value > limit
        items.append({'category_id': category_id, 'category': category.name, 'amount': money(value),
                      'limit': money(limit) if limit is not None else None, 'exceeded': exceeded})
        if exceeded and not reason:
            reason = (f'Лимит директора по статье «{category.name}» — {amount_label(limit)} {currency}; '
                      f'сумма — {amount_label(value)} {currency}. Окончательное согласование недоступно. '
                      'Измените сумму заявки или обратитесь к администратору холдинга для пересмотра лимита.')
    return {'currency': currency, 'items': items, 'exceeded': bool(reason), 'reason': reason}


def enforce_director_limit(s, request, currency):
    state = request_director_limit(s, request, currency)
    if state['exceeded']:
        raise HTTPException(409, state['reason'])
