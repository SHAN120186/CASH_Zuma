"""Inspectable input/output addresses for the supplied UZGERMED workbook.

This module only reads OOXML. It does not alter a workbook, invent missing
numbers, evaluate formulas, or substitute a generic financial model.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation
import math
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from defusedxml.common import DefusedXmlException
from .business_sources import _xlsx, SourceReadError


PROFILE_ID = 'uzgermed-36m-usd'
REQUIRED_SHEETS = frozenset({
    'Фин_план', 'Стоим_проекта', 'РКЛ', 'Производ. с учетом загрузки',
    'План производства', 'План_продаж', 'НДС', 'Раб_капит',
    'Приб_Убыт', 'Поток_нал', 'ВНД',
})

# Limits apply to this fixed 36-month template, not to general business plans.
# Only original scalar calculation drivers may be edited; calculated cells
# and unreferenced credit-condition captions are never editable inputs.
READ_ONLY_PARAMETERS = frozenset({('РКЛ', 'AA7'), ('РКЛ', 'AA8'), ('РКЛ', 'AA10')})
PARAMETERS = (
    ('Стоим_проекта', 'B38', 'Курс доллара для проекта', 'UZS/USD', 0.000001, 1_000_000_000, None,
     'Число сумов за 1 доллар. Это исходная ячейка именованного диапазона «курс». '
     'В ранее полученных кредитах сохранены отдельные исходные курсы.'),
    ('Стоим_проекта', 'D28', 'Новый кредит на оборотный капитал', 'USD', 0, 1_000_000_000_000, None,
     'Лимит нового банковского кредита из исходной стоимости проекта. '
     'Его итог D34 и график РКЛ рассчитываются формулами книги.'),
    ('РКЛ', 'AA6', 'Годовая ставка нового кредита', 'доля', 0, 1, None,
     'Введите долю: 0,12 означает 12%. В оригинале эта же ставка используется '
     'для дисконтирования NPV; изменение влияет на оба расчёта.'),
    ('РКЛ', 'AA7', 'Льготный период нового кредита (справочно)', 'месяцев', 0, 36, 1,
     'Справочное условие оригинала, только для чтения. Формулы не используют эту ячейку: '
     'сроки погашения закреплены в самом графике. Для изменения льготного периода '
     'обновите график в исходном Excel и загрузите исправленную книгу.'),
    ('РКЛ', 'AA8', 'Срок нового кредита (справочно)', 'месяцев', 1, 36, 1,
     'Справочное условие оригинала, только для чтения. Формулы не используют эту ячейку: '
     'горизонт и сроки платежей закреплены в исходной книге. Для изменения срока '
     'обновите график в Excel и загрузите исправленную книгу.'),
    ('РКЛ', 'AA10', 'Период использования кредитной линии (справочно)', 'месяцев', 1, 36, 1,
     'Справочное условие оригинала, только для чтения. Формулы не используют эту ячейку: '
     'выдачи и погашения заданы отдельно в графике. Для изменения периода использования '
     'обновите график в Excel и загрузите исправленную книгу.'),
    ('Производ. с учетом загрузки', 'B19', 'Загрузка производства в первом месяце', 'доля', 0, 1, None,
     'Введите долю: 0,85 означает 85%. Следующие месяцы рассчитываются исходными формулами.'),
    ('Производ. с учетом загрузки', 'Q19', 'Ежегодный прирост загрузки', 'доля', 0, 1, None,
     'Абсолютный годовой прирост: 0,01 означает 1 процентный пункт в год. '
     'Оригинальная формула добавляет это значение, делённое на 12, ежемесячно.'),
    ('НДС', 'F4', 'Ставка НДС в исходной модели', 'доля', 0, 1, None,
     'Введите долю: 0,12 означает 12%. Выручка с НДС и без НДС остаётся раздельной, как в книге.'),
    ('Раб_капит', 'B5', 'Период получения экспортной выручки', 'дней', 1, 365, 1,
     'Исходные дни покрытия экспортных продаж. В книге оборачиваемость рассчитывается как 360 / дни.'),
    ('Раб_капит', 'B6', 'Период получения местной выручки', 'дней', 1, 365, 1,
     'Исходные дни покрытия местных продаж. Нулевые поступления в соответствующей строке '
     'остаются нулевыми; количество дней само по себе не создаёт дебиторскую задолженность.'),
    ('Раб_капит', 'B7', 'Запасы и незавершённое производство', 'дней', 1, 365, 1,
     'Исходное количество дней покрытия ТМЗ. Ноль запрещён: формула книги делит 30 на это значение.'),
    ('Раб_капит', 'B10', 'Покрытие импортного сырья', 'дней', 1, 365, 1,
     'Исходное количество дней покрытия импортного сырья. Ноль запрещён из-за деления в книге.'),
)

METRICS = (
    ('Стоим_проекта', 'F34', 'Первоначальная стоимость проекта', 'USD'),
    ('Стоим_проекта', 'B34', 'Собственное участие в проекте', 'USD'),
    ('Стоим_проекта', 'D34', 'Новый банковский кредит', 'USD'),
    ('Стоим_проекта', 'E34', 'Остаток ранее полученных кредитов', 'USD'),
    ('ВНД', 'D6', 'NPV за три года', 'USD'),
    ('ВНД', 'E6', 'IRR за три года', '%'),
    ('План производства', 'E48', 'Выпуск при полной мощности, в месяц', 'упаковок'),
    ('План производства', 'F49', 'Продажи при полной мощности, в месяц', 'USD'),
    ('Приб_Убыт', 'N4', 'Выручка без НДС · 1-й год', 'USD'),
    ('Приб_Убыт', 'N27', 'Выручка без НДС · 2-й год', 'USD'),
    ('Приб_Убыт', 'N50', 'Выручка без НДС · 3-й год', 'USD'),
    ('Приб_Убыт', 'N18', 'Чистая прибыль · 1-й год', 'USD'),
    ('Приб_Убыт', 'N41', 'Чистая прибыль · 2-й год', 'USD'),
    ('Приб_Убыт', 'N64', 'Чистая прибыль · 3-й год', 'USD'),
    ('Поток_нал', 'O27', 'Поток после погашения кредитов · 1-й год', 'USD'),
    ('Поток_нал', 'N55', 'Поток после погашения кредитов · 2-й год', 'USD'),
    ('Поток_нал', 'N80', 'Поток после погашения кредитов · 3-й год', 'USD'),
    ('Производ. с учетом загрузки', 'N53', 'Продажи с НДС · 1-й год', 'USD'),
    ('Производ. с учетом загрузки', 'N92', 'Продажи с НДС · 2-й год', 'USD'),
    ('Производ. с учетом загрузки', 'N129', 'Продажи с НДС · 3-й год', 'USD'),
    ('Поток_нал', 'M83', 'Накопленный денежный поток · 36-й месяц', 'USD'),
)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        converted = float(number)
        if number != 0 and converted == 0:
            return None  # nonzero source underflow must not become a financial zero
        return converted if number.is_finite() and math.isfinite(converted) else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def _text(sheets, sheet, cell):
    value = sheets.get(sheet, {}).get(cell, {}).get('value')
    return value if isinstance(value, str) else ''


def _recognized(sheets):
    if not REQUIRED_SHEETS.issubset(sheets):
        return False
    company = ' '.join(_text(sheets, 'Стоим_проекта', 'B4').upper().split())
    return ('UZGERMED PHARM' in company and
            'СТОИМОСТЬ ПРОЕКТА' in _text(sheets, 'Стоим_проекта', 'A2').upper() and
            'ФИНАНСОВЫЙ ПЛАН' in _text(sheets, 'Фин_план', 'A2').upper() and
            all(token in _text(sheets, 'ВНД', 'A1').upper() for token in ('NPV', 'IRR')))


def describe_profile(raw, values=None):
    """Return native scalar controls and original saved outputs with provenance.

    ``values`` optionally accepts the recalculator's nested ``{sheet: {cell:
    scalar}}``. A missing/error fresh result stays unavailable; a stale cache
    is never used to fill it. Unreadable XLSX remains unsupported with a
    diagnostic, so the normal source-analysis path can explain the file error.
    """
    try:
        sheets, notes = _xlsx(raw)
    except (SourceReadError, BadZipFile, KeyError, ValueError, ParseError, DefusedXmlException):
        return {'supported': False, 'profile_id': PROFILE_ID, 'title': '', 'sheet_count': 0,
                'parameters': [], 'metrics': [], 'source_notes': [],
                'issues': [{'code': 'unreadable_workbook', 'reason': 'Не удалось прочитать структуру книги.'}]}
    profile = {'supported': _recognized(sheets), 'profile_id': PROFILE_ID,
               'title': '', 'sheet_count': len(sheets), 'parameters': [], 'metrics': [],
               'issues': [], 'source_notes': notes}
    if not profile['supported']:
        return profile
    profile.update(title='Бизнес-план UZGERMED PHARM · исходная модель 36 месяцев',
                   currency='USD', months=36)
    error_counts = Counter(str(c.get('value')) for cells in sheets.values() for c in cells.values()
                           if c.get('error'))
    profile['source_error_counts'] = dict(error_counts)
    profile['source_error_count'] = sum(error_counts.values())
    for sheet, address, label, unit, minimum, maximum, step, help_text in PARAMETERS:
        cell = sheets.get(sheet, {}).get(address, {})
        value = None if cell.get('formula') or cell.get('error') else _number(cell.get('numeric_text', cell.get('value')))
        parameter = {'key': f'{sheet}!{address}', 'sheet': sheet, 'cell': address,
                     'label': label, 'value': value, 'unit': unit, 'help': help_text,
                     'min': minimum, 'max': maximum, 'step': step,
                     'editable': value is not None and (sheet, address) not in READ_ONLY_PARAMETERS,
                     'original_text': cell.get('numeric_text')}
        if value is None:
            code = 'formula_parameter' if cell.get('formula') else 'missing_parameter'
            profile['issues'].append({'sheet': sheet, 'cell': address, 'code': code,
                'reason': 'В исходной ячейке нужна конечная числовая константа; формула или ошибка не заменяется числом.'})
        profile['parameters'].append(parameter)
    for sheet, address, label, unit in METRICS:
        cell = sheets.get(sheet, {}).get(address, {})
        original = None if cell.get('error') else _number(cell.get('numeric_text', cell.get('value')))
        metric = {'key': f'{sheet}!{address}', 'sheet': sheet, 'cell': address,
                  'label': label, 'original': original, 'unit': unit,
                  'original_text': cell.get('numeric_text'),
                  'original_source': 'excel_saved_cache' if cell.get('formula') else 'excel_constant',
                  'error': str(cell.get('value')) if cell.get('error') else None}
        if original is None:
            profile['issues'].append({'sheet': sheet, 'cell': address, 'code': 'original_metric_unavailable',
                'reason': 'Исходный результат отсутствует или содержит ошибку Excel; значение не заменяется нулём.'})
        if values is not None:
            fresh = values.get(sheet, {}).get(address)
            metric['calculated'] = _number(fresh)
            metric['calculated_error'] = None if metric['calculated'] is not None else str(fresh or 'Результат отсутствует')
            metric['calculated_source'] = 'recalculated_formula' if cell.get('formula') else 'excel_constant'
        profile['metrics'].append(metric)
    return profile
