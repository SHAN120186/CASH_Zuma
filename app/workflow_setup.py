"""Однократная настройка маршрута заявок: политика директора по статьям и календарь РУз.

Каждый шаг помечается записью в settings и не повторяется. Существующие заявки,
бюджеты и операции не меняются.
"""
from datetime import date
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .db import Category, Company, CalendarDay, Setting, now

# Первоначальные пороги для обычных статей (сотые доли валюты). EUR без порога — директор всегда.
DEFAULT_THRESHOLDS = {'UZS': 500_000_000, 'USD': 50_000, 'EUR': None}
# Утверждённый перечень регулярных статей, где директор может не участвовать.
SKIP_NAMES = ('Налоги', 'Коммунальные услуги')
# Праздники Республики Узбекистан с постоянной датой. Рамазан и Курбан хайит,
# а также переносы выходных администратор добавляет по постановлению.
FIXED_HOLIDAYS = [((1, 1), 'Новый год'), ((3, 8), 'Международный женский день'), ((3, 21), 'Навруз'),
                  ((5, 9), 'День памяти и почестей'), ((9, 1), 'День независимости'),
                  ((10, 1), 'День учителя и наставника'), ((12, 8), 'День Конституции')]
CALENDAR_YEARS = range(2026, 2031)


def migrate_workflow(engine):
    with Session(engine) as s, s.begin():
        if engine.dialect.name != 'sqlite':
            s.execute(text('SELECT pg_advisory_xact_lock(7312801)'))
        if not s.get(Setting, 'director_policy_v1'):
            for company in s.scalars(select(Company).where(Company.active.is_(True), Company.code != 'UNASSIGNED')):
                if not s.scalar(select(Category.id).where(Category.company_id == company.id, Category.name == 'Коммунальные услуги')):
                    s.add(Category(company_id=company.id, name='Коммунальные услуги', activity='operating', type='outcome', cost_group='fixed'))
            s.flush()
            for c in s.scalars(select(Category).where(Category.type == 'outcome', Category.director_policy.is_(None))):
                c.director_policy_changed_at = now()
                if c.name in SKIP_NAMES:
                    c.skip_allowed = True
                    c.director_policy = 'skip'
                else:
                    c.director_policy = 'threshold'
                    c.director_threshold_uzs = DEFAULT_THRESHOLDS['UZS']
                    c.director_threshold_usd = DEFAULT_THRESHOLDS['USD']
                    c.director_threshold_eur = DEFAULT_THRESHOLDS['EUR']
            s.add(Setting(key='director_policy_v1', value='1'))
        if not s.get(Setting, 'calendar_uz_v1'):
            for year in CALENDAR_YEARS:
                for (month, day), name in FIXED_HOLIDAYS:
                    d = date(year, month, day)
                    if not s.get(CalendarDay, d):
                        s.add(CalendarDay(day=d, kind='holiday', name=name))
            s.add(Setting(key='calendar_uz_v1', value='1'))
