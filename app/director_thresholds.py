"""Порог участия директора в UZS по согласованной таблице владельца.

Суммы в сотых долях валюты. None: финдиректор без потолка, директор не нужен.
0: директор нужен для любой положительной суммы. Верхнего запрета нет.
"""
import json
import re
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .db import Audit, Category, Company, Setting, now

MIGRATION_KEY = 'director_participation_v2'


def normalized_name(name):
    name = re.sub(r'^\d+\s*[·|—]\s*', '', name)
    return ' '.join(name.casefold().replace('ё', 'е').split()).strip()


# Подтверждённые равнозначные названия; номер другой статьи сам по себе не используется.
ALIASES = {
    'Закупка основных средств': '17', 'Оборудование': '17',
    'Девидент': '98', 'Дивиденд': '98', 'Дивиденды': '98',
    'Закуп услуг по Лабораторным анализам': '18',
    'Микробиологические анализы': '19',
    'Реактивы и расходники для Лаборатории': '35',
    'Проценты банка %': '8', 'Қўшилган қиймат солиғи': '43',
}


def source_rule(category):
    title = normalized_name(category.name)
    for code, name, threshold in SOURCE_RULES:
        if title == normalized_name(name):
            return code, threshold
    for name, code in ALIASES.items():
        if title == normalized_name(name):
            return next((c, value) for c, _, value in SOURCE_RULES if c == code)
    if re.fullmatch(r'расчеты по предоставленным займам 4720(?:\s*\([^)]*\))?', title):
        return '65', 0
    return None


def apply_default_director_threshold(category, *, replace=False):
    if category.type != 'outcome':
        return False
    if not replace and (category.director_policy_changed_at is not None or category.director_policy_uzs is not None):
        return False
    rule = source_rule(category)
    if rule is None:
        return False
    _, threshold = rule
    category.director_policy_uzs = 'skip' if threshold is None else 'threshold'
    category.director_threshold_uzs = threshold
    if threshold is None:
        category.skip_allowed = True
    category.director_policy_changed_at = now()
    category.director_policy_changed_by = None
    return True


def policy_state(category):
    return {field: getattr(category, field) for field in (
        'director_policy', 'director_policy_uzs', 'director_policy_usd', 'director_policy_eur',
        'director_threshold_uzs', 'director_threshold_usd', 'director_threshold_eur',
        'skip_allowed', 'director_limit_uzs', 'director_limit_usd', 'director_limit_eur')}


def migrate_director_thresholds(engine):
    """Однократно меняет настройки статей; снимки заявок и финансовые факты не трогает."""
    with Session(engine) as s, s.begin():
        if engine.dialect.name != 'sqlite':
            s.execute(text('SELECT pg_advisory_xact_lock(7312801)'))
        if s.get(Setting, MIGRATION_KEY):
            return
        companies = select(Company.id).where(Company.code != 'UNASSIGNED')
        for category in s.scalars(select(Category).where(Category.company_id.in_(companies), Category.type == 'outcome')):
            before = policy_state(category)
            matched = apply_default_director_threshold(category, replace=True)
            for cur in ('uzs', 'usd', 'eur'):
                setattr(category, 'director_limit_' + cur, None)
            after = policy_state(category)
            if before != after:
                s.add(Audit(company_id=category.company_id, action='Настроен порог участия директора',
                            entity='category', entity_id=str(category.id),
                            detail=json.dumps({'before': before, 'after': after,
                                'source_article': source_rule(category)[0] if matched else None,
                                'reason': 'Поручение владельца: порог из лимита финдиректора, без верхнего запрета',
                                'units': 'minor_currency_units'}, ensure_ascii=False)))
        s.add(Setting(key=MIGRATION_KEY, value='1'))
# Только суммы столбца «Лимит фин.дир», не верхние лимиты из соседнего столбца.
SOURCE_RULES = (('18', 'Закуп услуг по Лабораторным анализам (QC)', 500000000),
 ('19', 'Микробиологические анализы (ML)', 500000000),
 ('35', 'Реактивы и расходники для Лаборатории (QA)', 500000000),
 ('28',
  'Прочие расходы для расстаможки (хранения, обработка груза и почты, въезд автомобиля….)',
  500000000),
 ('60', 'ЗАКУП местный', 500000000),
 ('61', 'ЗАКУП валютный хом-ашёлар', 500000000),
 ('55', 'Кредиторка местный қарзлар', 500000000),
 ('56', 'Кредиторка валютный қарзлар', 500000000),
 ('4', 'Расходники производства', 500000000),
 ('58', 'Азот ИТО', 2500000000),
 ('12', 'Расходы на маркетинг (бонус)', 10000000000),
 ('13', 'Клиентларга бериладиган бонуслар', 10000000000),
 ('59', 'Доставка продукции', None),
 ('27', 'Дорожные расходы сотрудников (отд. внут.продаж)+ ойлик+ойлик бонус', 500000000),
 ('57', 'Интернет ва телефон хизматлари', 500000000),
 ('53', 'Сув Коммунальные услуги', None),
 ('10', 'Расходы на аренда помещения и авто', 500000000),
 ('20',
  'Калибровка и Поверка оборудования Госстандарт (йилда 1 мартта) + Квалификация, валидация Хиндлар '
  '(йилда 2 мартта)',
  500000000),
 ('31', 'Командировочные расходы, отпуск Хиндлар йулкира, виза, прописка, лицензия', 500000000),
 ('32', 'Юридические услуги', None),
 ('14', 'Зарплата сотрудников', None),
 ('37', 'Налоги по ЗП', None),
 ('43', 'Налог на добавленную стоимость', None),
 ('5', 'Продукты питания, нон, сут - қатиқ', 500000000),
 ('52', 'Электр Коммунальные услуги', None),
 ('54', 'Газ Коммунальные услуги', None),
 ('9', 'Транспортные расходы (бензин+газ+дизел+бошкалар)', 500000000),
 ('15', 'Ҳўжалик эҳтиёжлари учун (тозалаш ва бошқа) AXO', 500000000),
 ('16', 'Запасные части для оборудования  ИТО', 500000000),
 ('29', 'Канцтовары', 500000000),
 ('33', 'За услуги для участия в биржевых торгах (брокеру)', 500000000),
 ('45', 'Прочие расходы (0,5%)', 500000000),
 ('8', 'Услуга банк (Проценты банка %)', None),
 ('34', 'Расходы на регистрацию/перерегистрация и аудит', 500000000),
 ('11', 'Расходы по ремонту и поддержанию ОС (факат бинони ремонти учун)', 500000000),
 ('67', 'Расходы по ремонту ускуналар учун ИТО', 500000000),
 ('64', 'УГП 2 млн кредит учун', 500000000),
 ('71', 'УГП карзи учун тулов', 500000000),
 ('65', 'Расчеты по предоставленным займам 4720 (Моддий ёрдам тарикасидаги туловлар)', 0),
 ('70', 'Долгосрочные займы 7820', 0),
 ('62', 'Кредит асосий қарз Зума', None),
 ('78', 'Само учун карз', 0),
 ('81', 'Дерекция учун тулов', 500000000),
 ('63', 'Кредит %  Зума', None),
 ('75', 'Workliy', 500000000),
 ('17', 'Закуп основных средств', 2000000000),
 ('98', 'Двидент', 500000000),
 ('99', 'Экспорт булими туловлари', 500000000),
 ('101', 'Курс фарки', None),
 ('88', 'Оборотка учун', None))
