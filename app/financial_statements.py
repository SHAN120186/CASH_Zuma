"""Mapping-based review of PNL and balance; no posting or certification.

The workbook remains an immutable company-private source. Formula caches, missing
input periods and accounting classification issues remain distinguishable. Derived
amounts have provenance; this module never changes Excel or the cash ledger.
"""
from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import unicodedata
from collections import Counter, OrderedDict, defaultdict
from threading import RLock
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from urllib.parse import unquote
from zipfile import BadZipFile, ZipFile
from typing import Literal

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from fastapi import APIRouter, HTTPException, Query, Request
from openpyxl.formula.translate import Translator, TranslatorError
from openpyxl.formula import Tokenizer
from openpyxl.formula.tokenizer import TokenizerError
from openpyxl.utils.cell import column_index_from_string
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .clock import today
from .db import FinancialStatementSource, Setting, unit
from .financial_statement_calculations import CONFIRMED_REVENUE_BASIS
from .model_import import MAX_SIZE, col
from .security import perms_of, require_permission, session_user
from .services import log

router = APIRouter()
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
RID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
REPORTS = {'pnl': ('09_Statements', 'PNL · план / факт', 7, 24),
           'balance': ('09b_Balance', 'Баланс · план / факт', 7, 81)}
PERCENT_ROWS = {'pnl': {13, 24}, 'balance': {62, 63, 64, 67, 68, 69, 75}}
COEFFICIENT_ROWS = {70, 73, 74, 76}
REVIEW_VERSION = 'mapping-2026-10-11-v4'
_review_cache = OrderedDict()
_review_cache_lock = RLock()
REQUIREMENTS = [
    {'key': 'trial_balance', 'title': 'Полная ОСВ за каждый месяц',
     'fields': ['Компания, год, месяц, валюта и единица измерения', 'Счёт и субсчёт',
                'Начальное сальдо Дт / Кт', 'Обороты Дт / Кт', 'Конечное сальдо Дт / Кт'],
     'frequency': 'Ежемесячно после закрытия периода',
     'reason': 'Проверка полноты учёта, оборотов и перехода остатков; отсутствие месяца не равно нулю.'},
    {'key': 'journal', 'title': 'Журнал проводок и правила сопоставления счетов',
     'fields': ['Дата признания', 'Дебет, кредит, сумма', 'Первичный документ',
                'Контрагент, подразделение и статья', 'Закрытие счетов доходов и затрат',
                'Сопоставление счетов со строками PNL и баланса'],
     'frequency': 'Ежемесячно; правила — при изменении учётной политики',
     'reason': 'Нужен учёт по начислению. Выплата денег, остаток задолженности и расход периода различаются.'},
    {'key': 'sales', 'title': 'Продажи, возвраты и скидки',
     'fields': ['Отгрузка и дата признания выручки', 'Выручка без НДС', 'Возвраты и скидки',
                'Дебиторская задолженность и авансы покупателей'],
     'frequency': 'Ежемесячно',
     'reason': 'Проверка чистой выручки и исключение двойного вычитания возвратов.'},
    {'key': 'inventory', 'title': 'Запасы, производство и себестоимость',
     'fields': ['Материалы, НЗП и готовая продукция на начало и конец', 'Приход и списание',
                'Себестоимость реализованной продукции', 'Распределение производственных расходов'],
     'frequency': 'Ежемесячно',
     'reason': 'Закупка материалов не равна себестоимости продаж; нужны движение запасов и распределение затрат.'},
    {'key': 'payroll', 'title': 'Начисленная зарплата и социальные расходы',
     'fields': ['Начисления за период', 'Распределение между производством и OPEX',
                'Выплаты', 'Задолженность и социальные обязательства на конец'],
     'frequency': 'Ежемесячно',
     'reason': 'Конечная кредиторская задолженность не является расходом месяца; ранее распределённые затраты не вычитаются повторно.'},
    {'key': 'fixed_assets', 'title': 'Основные средства и амортизация',
     'fields': ['Первоначальная стоимость и накопленная амортизация', 'Ввод и выбытие',
                'Амортизация месяца', 'Распределение в себестоимость и OPEX'],
     'frequency': 'Ежемесячно',
     'reason': 'Амортизация периода отличается от изменения накопленного износа; нужно исключить двойной учёт.'},
    {'key': 'debt', 'title': 'Кредиты, проценты и прочие обязательства',
     'fields': ['Остаток основного долга', 'Краткосрочная и долгосрочная части',
                'Начисленные проценты', 'Привлечение и погашение', 'Договоры и графики'],
     'frequency': 'Ежемесячно',
     'reason': 'Основной долг входит в баланс, а не в прибыль; проценты показываются отдельно.'},
    {'key': 'taxes', 'title': 'Налоги и сверка расчётов',
     'fields': ['Начисления по видам налогов', 'Авансы и задолженность',
                'Расход по налогу на прибыль', 'Социальные обязательства отдельно'],
     'frequency': 'Ежемесячно и по срокам налогового закрытия',
     'reason': 'Налоговый расход, аванс и оплата не заменяют друг друга; социальные платежи нельзя учитывать дважды.'},
    {'key': 'capital', 'title': 'Капитал и закрытие финансового результата',
     'fields': ['Уставный и резервный капитал', 'Нераспределённая прибыль на начало',
                'Движение прибыли и дивиденды', 'Незакрытый финансовый результат',
                'Начальные сравнительные остатки'],
     'frequency': 'Ежемесячно; сравнительные данные — при запуске',
     'reason': 'Капитал подтверждается счетами и решениями владельцев, а не разностью активов и обязательств.'},
    {'key': 'plan', 'title': 'План и параметры расчёта',
     'fields': ['План продаж и цен', 'План производства и себестоимости', 'OPEX и штат',
                'CAPEX и амортизация', 'Кредиты, налоги и оборотный капитал',
                'Версия плана, год, валюта и масштаб сумм'],
     'frequency': 'Утверждение плана; новая версия при изменениях',
     'reason': 'План, факт и нормативы хранятся отдельно; пороги коэффициентов требуют согласованной методики.'},
    {'key': 'ifrs_adjustments', 'title': 'Учётная политика и реестр корректировок МСФО',
     'fields': ['Принятая основа представления', 'Корректировки по начислению и оценке',
                'Сверка национального учёта с отчётными статьями', 'Сравнительные данные и раскрытия',
                'Методика коэффициентов и утверждённые управленческие пороги'],
     'frequency': 'При запуске и при каждом закрытии отчётности',
     'reason': 'Порог из файла не является нормативом МСФО; без корректировок и раскрытий соответствие МСФО не подтверждено.'},
]


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def read_workbook(raw):
    """Read XML values and formulas without running macros, links or formulas."""
    if not raw or len(raw) > MAX_SIZE:
        raise ValueError('Нужен непустой XLSX не больше 20 МБ.')
    try:
        with ZipFile(io.BytesIO(raw)) as z:
            infos = z.infolist()
            if (len(infos) > 2500 or sum(x.file_size for x in infos) > 150 * 1024 * 1024
                    or len({x.filename for x in infos}) != len(infos)):
                raise ValueError('Слишком большой или некорректный распакованный XLSX.')
            def xml(path):
                return ET.fromstring(z.read(path))
            wb = xml('xl/workbook.xml')
            relationships = {r.attrib['Id']: r for r in xml('xl/_rels/workbook.xml.rels')}
            strings = []
            if 'xl/sharedStrings.xml' in z.namelist():
                strings = [''.join(t.text or '' for t in item.iter('{' + NS['m'] + '}t'))
                           for item in xml('xl/sharedStrings.xml')]
            properties = wb.find('m:workbookPr', NS)
            epoch = date(1904, 1, 1) if properties is not None and properties.attrib.get('date1904') in ('1', 'true') else date(1899, 12, 30)
            sheets = {}
            total_cells = 0
            for sheet in wb.findall('m:sheets/m:sheet', NS):
                rel = relationships[sheet.attrib[RID]]
                if rel.attrib.get('TargetMode') == 'External':
                    raise ValueError('Лист книги не может быть внешней ссылкой.')
                target = rel.attrib['Target']
                path = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
                if not path.startswith('xl/') or '..' in PurePosixPath(path).parts:
                    raise ValueError('Некорректный путь листа XLSX.')
                cells = {}
                shared = {}
                for item in xml(path).findall('.//m:sheetData/m:row/m:c', NS):
                    address = item.attrib['r'].upper()
                    if not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,6}', address) or address in cells:
                        raise ValueError('Некорректный адрес ячейки XLSX.')
                    v, f = item.find('m:v', NS), item.find('m:f', NS)
                    value = v.text if v is not None else None
                    typ = item.attrib.get('t', 'n')
                    if typ == 's' and value is not None:
                        value = strings[int(value)]
                    elif typ == 'inlineStr':
                        value = ''.join(t.text or '' for t in item.iter('{' + NS['m'] + '}t'))
                    cells[address] = {'value': value, 'type': typ, 'formula': f.text if f is not None else None,
                                      'has_formula': f is not None}
                    total_cells += 1
                    if total_cells > 500000:
                        raise ValueError('Слишком много ячеек в XLSX.')
                    if f is not None and f.attrib.get('t') == 'shared':
                        cells[address]['shared'] = f.attrib.get('si')
                        if f.text:
                            shared[f.attrib.get('si')] = (address, f.text)
                for address, item in cells.items():
                    if item.get('has_formula') and not item.get('formula') and item.get('shared') in shared:
                        origin, formula = shared[item['shared']]
                        try:
                            item['formula'] = Translator('=' + formula.lstrip('='), origin=origin).translate_formula(address).lstrip('=')
                        except (TranslatorError, ValueError, IndexError):
                            item['formula_unresolved'] = True
                sheets[sheet.attrib['name']] = cells
            if not all(sheet in sheets for sheet, *_ in REPORTS.values()):
                raise ValueError('В XLSX нужны оба листа: 09_Statements и 09b_Balance.')
            return sheets, epoch
    except (BadZipFile, KeyError, IndexError, ValueError, OverflowError, RuntimeError, ET.ParseError, DefusedXmlException) as e:
        if isinstance(e, ValueError) and str(e).startswith(('Нужен', 'Слишком', 'В XLSX', 'Некорректный', 'Лист книги')):
            raise
        raise ValueError('Не удалось прочитать XLSX. Нужна обычная, не зашифрованная книга Excel.') from e


def date_value(value, epoch):
    if value is None:
        return None
    text = str(value).strip()
    for fmt in ('%Y-%m', '%Y-%m-%d', '%Y/%m/%d', '%d.%m.%Y', '%d/%m/%Y', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    numeric = number(value)
    if numeric is not None and Decimal(10000) <= numeric <= Decimal(73413):
        try:
            return epoch + timedelta(days=int(numeric))
        except (OverflowError, ValueError):
            pass
    return None


def _rows_referenced(formula, column):
    normalized = (formula or '').upper().replace('$', '')
    refs = {int(r) for r in re.findall(r'(?<![A-Z0-9_])' + column + r'([1-9][0-9]*)(?![0-9])', normalized)}
    for first, last in re.findall(column + r'([0-9]+):' + column + r'([0-9]+)', normalized):
        refs.update(range(int(first), int(last) + 1))
    return refs


def report_dependencies(sheets, coverage):
    """Trace displayed PNL/balance inputs; never audit the cash-flow report.

    SUMPRODUCT category masks restrict monetary dependencies to matching rows.
    Empty lookup scaffolding and unrelated worksheets are not source defects.
    No Excel expression or external link is executed here.
    """
    scope, pending, range_cache = set(), [], {}
    for key, (sheet, _, first, last) in REPORTS.items():
        cells = sheets.get(sheet, {})
        for row in range(first, last + 1):
            label = cells.get('D' + str(row), {}).get('value')
            if key == 'balance' and row == 38 and not label and balance_check_row(sheets):
                label = 'Проверка баланса'
            if not label or number(label) is not None:
                continue
            for month in range(1, 13):
                pending.append((sheet, col(3 + 2 * month) + str(row)))
                if coverage[month - 1]['status'] == 'available':
                    pending.append((sheet, col(4 + 2 * month) + str(row)))
            if key == 'balance' and row >= 62:
                pending.append((sheet, 'A' + str(row)))
    def excluded(sheet, address):
        return sheet.upper().startswith('09_CASH_FLOW') or (sheet == '09_Statements' and int(re.search(r'\d+', address).group()) >= 26)
    while pending:
        sheet, address = pending.pop()
        if (sheet, address) in scope or excluded(sheet, address):
            continue
        scope.add((sheet, address))
        formula = sheets.get(sheet, {}).get(address, {}).get('formula') or ''
        if not formula:
            continue
        # Source category labels can safely narrow SUMPRODUCT ranges without
        # interpreting or calculating arbitrary spreadsheet formulas.
        selected = {}
        if 'SUMPRODUCT' in formula.upper():
            for target, column, first, last, label in re.findall(r"'([^']+)'!\$?([A-Z]+)\$?(\d+):\$?\2\$?(\d+)\s*=\s*\"([^\"]+)\"", formula, re.I):
                selected[target] = {r for r in range(int(first), int(last) + 1)
                                    if str(sheets.get(target, {}).get(column.upper() + str(r), {}).get('value') or '') == label}
        try:
            tokens = Tokenizer('=' + formula.lstrip('=')).items
        except (TokenizerError, ValueError, IndexError):
            continue
        for token in tokens:
            if token.type != 'OPERAND' or token.subtype != 'RANGE':
                continue
            reference = token.value.replace('$', '')
            target = sheet
            if '!' in reference:
                target, reference = reference.rsplit('!', 1)
                target = target.strip("'").replace("''", "'")
            if target not in sheets:  # named ranges / external sources remain provenance only
                continue
            match = re.fullmatch(r'([A-Z]{1,3})(\d*)(?::([A-Z]{1,3})(\d*))?', reference, re.I)
            if not match:
                continue
            c1, r1, c2, r2 = match.groups()
            left, right = column_index_from_string(c1), column_index_from_string(c2 or c1)
            top, bottom = int(r1 or 1), int(r2 or r1 or 1048576)
            cache_key = (target, reference)
            if cache_key not in range_cache:
                if ':' not in reference and r1:
                    range_cache[cache_key] = [(reference.upper(), int(r1))]
                else:
                    found = []
                    for candidate in sheets[target]:
                        col_name, row_text = re.fullmatch(r'([A-Z]+)(\d+)', candidate).groups()
                        row_number = int(row_text)
                        if left <= column_index_from_string(col_name) <= right and top <= row_number <= bottom:
                            found.append((candidate, row_number))
                    range_cache[cache_key] = found
            for candidate, row_number in range_cache[cache_key]:
                if target not in selected or row_number in selected[target]:
                    pending.append((target, candidate))
    return scope


def source_requirements(records, coverage, currency, unit_status, plan_source, calculation, issues):
    """Ask for unresolved policy/detail only, acknowledging amounts already loaded."""
    loaded = [c['period'] for c in coverage if c['status'] == 'available']
    missing = [c['period'] for c in coverage if c['status'] == 'missing']
    loaded_periods = set(loaded)
    accounts = {r['account'] for ym, rows in records.items() if f'{ym[0]}-{ym[1]:02}' in loaded_periods
                for r in rows if not r['invalid_amounts']}
    evidence = 'Mapping: ' + ', '.join(loaded) if loaded else None
    result = []
    for template in REQUIREMENTS:
        item = {**template, 'fields': list(template['fields']), 'evidence': [], 'missing_fields': []}
        key = item['key']
        if evidence:
            item['evidence'].append(evidence)
        if key == 'trial_balance':
            item['title'] = 'ОСВ: загруженные и ожидаемые месяцы'
            if not loaded:
                item['missing_fields'].append({'key': 'mapping_periods', 'title': 'ОСВ за закрытый месяц'})
            elif missing:
                item['missing_fields'].append({'key': 'pending_periods', 'title': 'Ещё не загружены: ' + ', '.join(missing)})
            if currency is None:
                item['missing_fields'].append({'key': 'currency', 'title': 'Валюта отчёта'})
            if unit_status == 'unknown':
                item['missing_fields'].append({'key': 'amount_unit', 'title': 'Масштаб сумм: единицы / тысячи / миллионы'})
        elif key == 'plan':
            item['evidence'] = ([f"{plan_source['sheet']}: {plan_source['cached_cell_count']} сохранённых числовых результатов; снимок {plan_source['as_of']}"]
                                if plan_source else [])
            if not plan_source:
                item['missing_fields'].append({'key': 'plan_source', 'title': 'Источник утверждённого плана'})
            item['missing_fields'].extend([
                {'key': 'plan_vat_basis', 'title': 'План продаж: цены и выручка с НДС или без НДС'},
                {'key': 'plan_marketing_classification', 'title': 'План: классификация процента маркетинга / продаж'},
                {'key': 'plan_cost_allocation', 'title': 'План: состав себестоимости, постоянных затрат и амортизации'},
            ])
        elif key == 'fixed_assets':
            item['missing_fields'].append({'key': 'depreciation_allocation',
                'title': 'Распределение амортизации в себестоимость и OPEX',
                'reason': 'Для EBITDA; уже учтённые расходы повторно не вычитаются.'})
        elif key == 'payroll':
            item['title'] = 'Расчёты с персоналом и социальные обязательства'
        elif key == 'journal' and any(i['code'] in ('mapping_rollforward', 'mapping_balance_residual') for i in issues):
            item['missing_fields'].append({'key': 'osv_residual_explanation', 'title': 'Проводки / округления для выявленных расхождений ОСВ'})
        elif key == 'ifrs_adjustments':
            item['evidence'] = []
            item['missing_fields'] = [{'key': 'ifrs_policy_register', 'title': 'Учётная политика и сверка / корректировки МСФО для заявления о соответствии'}]
        # Amounts in Mapping already cover these categories. Their separate
        # registers are needed only for a specific disputed classification.
        category = {'sales': ('90',), 'inventory': ('10', '20', '21', '25', '28', '29'),
                    'payroll': ('65', '67'), 'fixed_assets': ('01', '02'),
                    'debt': ('68', '78'), 'taxes': ('64', '65'), 'capital': ('83', '85', '87', '99')}.get(key)
        if category:
            # Another category's OSV cannot prove this one, nor can future or
            # invalid periods. Registered blank/zero amounts still are evidence.
            item['evidence'] = []
            present = sorted(a for a in accounts if a.startswith(category))
            if present:
                if evidence:
                    item['evidence'].append(evidence)
                item['evidence'].append('Счета Mapping: ' + ', '.join(present))
            else:
                titles = {
                    'sales': 'Счета продаж, возвратов и скидок в ОСВ (90xx)',
                    'inventory': 'Счета запасов в ОСВ (10xx, 20xx, 21xx, 25xx, 28xx, 29xx)',
                    'payroll': 'Счета расчётов с персоналом и социальных обязательств в ОСВ (6500, 6700)',
                    'fixed_assets': 'Счета основных средств и накопленной амортизации в ОСВ (0100, 0200)',
                    'debt': 'Счета кредитов в ОСВ (68xx, 78xx)',
                    'taxes': 'Счета налоговых и социальных расчётов в ОСВ (64xx, 65xx)',
                    'capital': 'Счета капитала и финансового результата в ОСВ (83xx, 85xx, 87xx, 99xx)',
                }
                item['missing_fields'].append({'key': key + '_mapping', 'title': titles[key]})
        item['status'] = 'needs_clarification' if item['missing_fields'] else 'found'
        result.append(item)
    # Engine-specific unresolved classifications must be named, not guessed.
    for field in calculation.get('required_fields', []):
        if not isinstance(field, dict) or not field.get('key'):
            continue
        aliases = {'VAT_price_basis': 'plan_vat_basis', 'marketing_cost_classification': 'plan_marketing_classification'}
        field = {**field, 'key': aliases.get(field['key'], field['key'])}
        owner = 'plan' if field['key'].startswith('plan_') else 'fixed_assets' if field['key'] == 'depreciation_allocation' else 'debt' if field['key'] == 'debt_maturity' else 'journal'
        target = next(r for r in result if r['key'] == owner)
        if field['key'] not in {f['key'] for f in target['missing_fields']}:
            target['missing_fields'].append(field)
            target['status'] = 'needs_clarification'
    return result


def balance_check_row(sheets):
    """Recognise the template's boolean control; never promote arbitrary helpers."""
    cells = sheets.get('09b_Balance', {})
    formula = (cells.get('F38', {}).get('formula') or '').replace('$', '').lstrip('=').upper()
    labels = ((19, r'^total assets|всего актив'), (35, r'^total liabilities|всего обязательств'),
              (37, r'^total l\+e|всего обязательств и капитала'))
    return bool(re.fullmatch(r'F19\s*=\s*F37', formula) and all(
        re.search(pattern, str(cells.get('D' + str(row), {}).get('value') or '').casefold()) for row, pattern in labels))


def build_review(raw, filename, as_of=None, revenue_policy=None):
    sheets, epoch = read_workbook(raw)
    as_of = as_of or today()
    issues = []
    issue_keys = set()
    def issue(code, severity, title, source, detail):
        if (code, source) not in issue_keys:
            issues.append({'code': code, 'severity': severity, 'title': title, 'source': source, 'detail': detail})
            issue_keys.add((code, source))
    def cell(sheet, address):
        return sheets.get(sheet, {}).get(address, {})
    def val(sheet, address):
        return cell(sheet, address).get('value')

    periods_by_report = {}
    declared_years = set()
    for key, (sheet, _, _, _) in REPORTS.items():
        periods = []
        for month in range(1, 13):
            pc, fc = col(3 + 2 * month), col(4 + 2 * month)
            d = date_value(val(sheet, pc + '4'), epoch)
            fd = date_value(val(sheet, fc + '4'), epoch)
            if d:
                declared_years.add(d.year)
            if not d or d.month != month or (fd and (fd.year, fd.month) != (d.year, d.month)):
                issue('report_period_invalid', 'error', 'Период заголовка требует уточнения', sheet + '!' + pc + '4',
                      'Для пары «План / Факт» нужна однозначная дата соответствующего месяца; год из имени файла не используется.')
                d = None
            periods.append({'month': month, 'period': d.strftime('%Y-%m') if d else None,
                            'plan_source': sheet + '!' + pc + '4', 'fact_source': sheet + '!' + fc + '4'})
        periods_by_report[key] = periods
    year = next(iter(declared_years)) if len(declared_years) == 1 else None
    if year is None:
        issue('report_year_ambiguous', 'error', 'Год отчёта не определён однозначно', '09_Statements!E4; 09b_Balance!E4',
              'Нужны согласованные месячные даты в обоих листах. Имя загруженного файла не подтверждает год.')

    currency = None
    currency_candidates = set()
    unit_label = 'Масштаб сумм не указан'
    unit_status = 'unknown'
    for sheet in ('01_Assumptions', '09_Statements', '09b_Balance'):
        for address, item in sheets.get(sheet, {}).items():
            if int(re.search(r'[0-9]+', address).group()) > 7:
                continue
            text = str(item.get('value') or '')
            if re.search(r'currency|валют|валюта', text, re.I):
                currency_candidates.update(re.findall(r'\b(?:UZS|USD|EUR)\b', text.upper()))
            if re.search(r'единиц[аы].*измер|unit.*(?:amount|measure)|масштаб', text, re.I):
                unit_label, unit_status = text[:220], 'declared'
    if len(currency_candidates) == 1:
        currency = next(iter(currency_candidates))
    else:
        issue('currency_missing', 'warning', 'Валюта требует подтверждения', '01_Assumptions!A5',
              'Укажите одну валюту отчёта; суммы разных валют не складываются.')
    if unit_status == 'unknown':
        issue('unit_missing', 'warning', 'Единица измерения требует подтверждения', '01_Assumptions!A5',
              'Уточните, указаны ли суммы в единицах, тысячах или миллионах валюты. Значения не масштабируются автоматически.')

    mapping = sheets.get('Mapping', {})
    expected_headers = ['year', 'month', 'period', 'account', 'account_name', 'opening_debit',
                        'opening_credit', 'turnover_debit', 'turnover_credit', 'closing_debit', 'closing_credit']
    headers_valid = all(str(val('Mapping', col(i + 1) + '1') or '').strip().lower() == name
                        for i, name in enumerate(expected_headers))
    records = defaultdict(list)
    invalid_periods = set()
    mapping_years = set()
    if not headers_valid:
        issue('mapping_structure', 'error', 'Не найдена полная структура исходной ОСВ', 'Mapping!A1:K1',
              'Ожидаются year, month, period, account, account_name, opening_debit/credit, turnover_debit/credit, closing_debit/credit.')
    else:
        row_numbers = sorted({int(re.search(r'[0-9]+', address).group()) for address in mapping if address.startswith('D')})
        for row in row_numbers:
            account = str(val('Mapping', 'D' + str(row)) or '').strip()
            if row == 1 or not account:
                continue
            y, m = number(val('Mapping', 'A' + str(row))), number(val('Mapping', 'B' + str(row)))
            period = date_value(val('Mapping', 'C' + str(row)), epoch)
            ym = (int(y), int(m)) if y is not None and m is not None and y == int(y) and m == int(m) and 2000 <= y <= 2100 and 1 <= m <= 12 else None
            if ym:
                mapping_years.add(ym[0])
            if ym is None or period is None or (period.year, period.month) != ym:
                if ym:
                    invalid_periods.add(ym)
                issue('mapping_period_conflict', 'error', 'Год, месяц и период ОСВ не совпадают', 'Mapping!A' + str(row) + ':D' + str(row),
                      'Строка не подтверждает факт: нужны согласованные year, month и period. Период без года не угадывается.')
                continue
            measures, bad = [], []
            for column in range(6, 12):
                address = col(column) + str(row)
                item = cell('Mapping', address)
                entered = item.get('value') not in (None, '') or item.get('has_formula')
                invalid = entered and (item.get('type') in ('e', 'b', 's', 'inlineStr', 'str')
                                      or '#REF!' in (item.get('formula') or '').upper()
                                      or number(item.get('value')) is None)
                measures.append(None if invalid else number(item.get('value')))
                if invalid:
                    bad.append(address)
            if bad:
                invalid_periods.add(ym)
                issue('mapping_amount_invalid', 'error', 'Числа ОСВ ошибочны', 'Mapping!' + ','.join(bad),
                      'Ошибка Excel, текст, логическое значение или отсутствующий результат формулы не считается нулевым оборотом или остатком. Пустые стороны корректной строки ОСВ допустимы.')
            records[ym].append({'account': account, 'row': row, 'measures': measures,
                                'account_name': str(val('Mapping', 'E' + str(row)) or '').strip(),
                                'blank_amounts': not bad and all(value is None for value in measures),
                                'invalid_amounts': bad,
                                'has_numeric': not bad and any(value is not None for value in measures)})
        duplicates = [(ym, account) for ym, rows in records.items()
                      for account, count in Counter(r['account'] for r in rows).items() if count > 1]
        for ym, account in duplicates:
            invalid_periods.add(ym)
            issue('mapping_duplicate', 'error', 'Повторяется счёт в одном периоде', f'Mapping · {ym[0]}-{ym[1]:02} · {account}',
                  'Нужно различить субсчета или удалить подтверждённый дубль; неоднозначные строки не подтверждают факт.')
        inconsistent = []
        for ym, rows in records.items():
            for record in rows:
                if record['invalid_amounts']:
                    continue
                # Blank debit/credit sides are conventional in OSV; this diagnostic
                # does not turn these zero assumptions into report values.
                vals = [value if value is not None else Decimal(0) for value in record['measures']]
                residual = vals[0] - vals[1] + vals[2] - vals[3] - vals[4] + vals[5]
                if abs(residual) > Decimal('.02'):
                    inconsistent.append(record['row'])
        if inconsistent:
            issue('mapping_rollforward', 'warning', 'Начальные остатки и обороты не сходятся с конечными',
                  'Mapping · строки ' + ', '.join(str(r) for r in inconsistent[:12]),
                  f'Нужна сверка ОСВ и корректировок. Строк с расхождением: {len(inconsistent)}; баланс не выравнивается искусственно.')
    if year is not None and mapping_years and year not in mapping_years:
        issue('source_year_mismatch', 'error', 'В ОСВ нет года выбранного отчёта', 'Mapping!A:C',
              'Факт фильтруется по году и месяцу отчёта; совпадение только номера месяца недостаточно.')

    coverage = []
    for month in range(1, 13):
        ym = (year, month)
        count = len(records.get(ym, [])) if year is not None else 0
        numeric_count = sum(row['has_numeric'] for row in records.get(ym, [])) if year is not None else 0
        future = year is not None and (year, month) > (as_of.year, as_of.month)
        status = 'invalid' if ym in invalid_periods or year is None else 'future' if future else 'available' if count else 'missing'
        coverage.append({'month': month, 'period': f'{year}-{month:02}' if year else None,
                         'record_count': count, 'numeric_record_count': numeric_count,
                         'blank_record_count': sum(r['blank_amounts'] for r in records.get(ym, [])),
                         'status': status,
                         'calculation_eligible': status == 'available' and all(
                             periods_by_report[k][month - 1]['period'] == f'{year}-{month:02}' for k in REPORTS)})

    reports = {}
    scope = report_dependencies(sheets, coverage)
    error_cells, no_cache, bad_refs, external = [], [], [], []
    plan_cached, plan_uncached = [], []
    for sheet, address in sorted(scope):
        item = cell(sheet, address)
        source = sheet + '!' + address
        formula = item.get('formula') or ''
        if item.get('type') == 'e':
            error_cells.append(source)
        if item.get('has_formula') and item.get('value') is None and item.get('type') != 'str':
            no_cache.append(source)
        if '#REF!' in formula.upper():
            bad_refs.append(source)
        if re.search(r'\[[0-9]+\]|IMPORTRANGE|DUMMYFUNCTION', formula, re.I):
            if sheet == '98_CashMacro_Import':
                if number(item.get('value')) is not None and item.get('type') not in ('e', 'b', 's', 'inlineStr', 'str') and '#REF!' not in formula.upper():
                    plan_cached.append(source)
                else:
                    plan_uncached.append(source)
            else:
                external.append(source)
    for code, title, cells in [('excel_errors', 'В книге есть ошибки Excel', error_cells),
                               ('formula_cache_missing', 'Есть формулы без сохранённого результата', no_cache),
                               ('broken_references', 'Формулы содержат повреждённые ссылки', bad_refs),
                               ('external_dependencies', 'Формулы зависят от внешних источников', external)]:
        if cells:
            issue(code, 'error', title, '; '.join(cells[:8]),
                  f'Затронуто ячеек: {len(cells)}. Формулы не пересчитываются, ссылки не заменяются и ошибки не подменяются нулями.')
    yearless = [sheet + '!' + address for sheet, address in sorted(scope)
                for item in [cell(sheet, address)]
                if 'SUMIFS' in (item.get('formula') or '').upper() and 'MAPPING!' in (item.get('formula') or '').upper()
                and re.search(r'Mapping!\$?B', item.get('formula') or '', re.I)
                and not re.search(r'Mapping!\$?(?:A|C)', item.get('formula') or '', re.I)]
    if yearless:
        issue('yearless_sumifs', 'error' if len(mapping_years) > 1 else 'warning', 'Исходные формулы выбирают месяц без года', '; '.join(yearless[:5]),
              'Новый расчёт Mapping фильтрует год и месяц. Исходная формула при добавлении другого года может смешать периоды; нужен фильтр по году или YYYY-MM.')
    if plan_uncached:
        issue('plan_import_cache_missing', 'error', 'В импорте плана нет сохранённых числовых результатов', '; '.join(plan_uncached[:8]),
              'Внешняя связь не выполняется сервером; нужен числовой результат в загруженном снимке.')
    plan_source = ({'status': 'cached_snapshot', 'sheet': '98_CashMacro_Import',
                    'cached_cell_count': len(plan_cached), 'sources': plan_cached,
                    'as_of': as_of.isoformat(), 'live': False} if plan_cached else None)

    def output_cell(sheet, address, month, factual):
        item = cell(sheet, address)
        n = number(item.get('value')) if item.get('type') not in ('e', 'b', 's', 'inlineStr', 'str') else None
        broken_reference = '#REF!' in (item.get('formula') or '').upper()
        status = 'error' if item.get('type') == 'e' or broken_reference else 'missing_cache' if item.get('has_formula') and item.get('value') is None else 'missing' if item.get('value') in (None, '') else 'invalid' if n is None else 'cached' if item.get('has_formula') else 'value'
        result = {'value': format(n, 'f') if n is not None else None,
                  'source': sheet + '!' + address, 'status': status}
        if item.get('formula'):
            result['formula'] = '=' + item['formula'].lstrip('=')
        if sheet == '09_Statements' and not factual and plan_source and (sheet, address) in scope:
            result['snapshot_as_of'] = as_of.isoformat()
        if broken_reference and result['value'] is not None:
            result['source_cache'] = result['value']
        if sheet == '09b_Balance' and address.endswith('38') and balance_check_row(sheets) and item.get('type') == 'b':
            result['source_cache'] = item.get('value')
            result['source_type'] = 'b'
        if status in ('error', 'invalid'):
            result['value'] = None
        covered = coverage[month - 1]
        report_key = 'pnl' if sheet == REPORTS['pnl'][0] else 'balance'
        expected_period = f'{year}-{month:02}' if year else None
        header_valid = expected_period is not None and periods_by_report[report_key][month - 1]['period'] == expected_period
        if not header_valid:
            if result['value'] is not None:
                result['source_cache'] = result['value']
            result['value'] = None
            if status not in ('error', 'missing_cache', 'invalid'):
                result['status'] = 'invalid'
        if factual and covered['status'] != 'available':
            if result['value'] is not None:
                result['source_cache'] = result['value']
            result['value'] = None
            # Keep a genuine Excel error visible even in an uncovered period.
            if status not in ('error', 'missing_cache', 'invalid'):
                result['status'] = 'future' if covered['status'] == 'future' else 'invalid' if covered['status'] == 'invalid' else 'missing'
        return result

    for key, (sheet, title, first, last) in REPORTS.items():
        rows = []
        for row in range(first, last + 1):
            label = str(val(sheet, 'D' + str(row)) or '').strip()
            if key == 'balance' and row == 38 and not label and balance_check_row(sheets):
                label = 'Проверка баланса'
            # Numeric/helper rows are not financial rows; their values are never promoted to headings.
            if not label or number(label) is not None:
                continue
            all_cells = [cell(sheet, col(c) + str(row)) for m in range(1, 13) for c in (3 + 2 * m, 4 + 2 * m)]
            kind = 'section' if not any(c.get('value') is not None or c.get('has_formula') for c in all_cells) else 'ratio' if row in PERCENT_ROWS[key] or key == 'balance' and row in ({65, 66} | COEFFICIENT_ROWS) else 'amount'
            measure = 'section' if kind == 'section' else 'percent' if row in PERCENT_ROWS[key] else 'days' if key == 'balance' and row in {65, 66} else 'coefficient' if kind == 'ratio' else 'amount'
            output_row = {'key': key + '_' + str(row), 'row': row, 'label': label, 'kind': kind, 'measure': measure,
                          'cells': [{'month': m,
                                     'plan': output_cell(sheet, col(3 + 2 * m) + str(row), m, False),
                                     'fact': output_cell(sheet, col(4 + 2 * m) + str(row), m, True)} for m in range(1, 13)]}
            if key == 'balance' and row >= 62:
                norm_cell = cell(sheet, 'A' + str(row))
                norm_value = norm_cell.get('value')
                if norm_value not in (None, '') or norm_cell.get('has_formula'):
                    output_row['norm'] = {'value': str(norm_value) if norm_value is not None and norm_cell.get('type') != 'e' else None,
                                          'source': sheet + '!A' + str(row),
                                          'status': 'error' if norm_cell.get('type') == 'e' else 'missing_cache' if norm_value is None else 'author_guideline'}
            rows.append(output_row)
        reports[key] = {'sheet': sheet, 'title': title, 'periods': periods_by_report[key], 'rows': rows}

    from .financial_statement_calculations import apply_mapping_reports
    calculation = apply_mapping_reports(reports, records, year, coverage, sheets, issue, revenue_policy)

    balance_sheet = REPORTS['balance'][0]
    pnl_sheet = REPORTS['pnl'][0]
    loaded_months = [c['month'] for c in coverage if c['status'] == 'available']
    for month in loaded_months:
        fc = col(4 + 2 * month)
        plug = cell(balance_sheet, fc + '36').get('formula') or ''
        if {19, 35} <= _rows_referenced(plug, fc) and '-' in plug:
            issue('equity_plug', 'error', 'Капитал рассчитан как выравнивающая разница', balance_sheet + '!' + fc + '36',
                  'Активы минус обязательства не подтверждают капитал. Нужна сверка счетов капитала, прибыли и незакрытого финансового результата.')
            break
    for month in loaded_months:
        fc = col(4 + 2 * month)
        liability = cell(balance_sheet, fc + '35').get('formula') or ''
        if liability and not {30, 31} <= _rows_referenced(liability, fc) and any(cell(balance_sheet, fc + str(r)) for r in (30, 31)):
            issue('loans_omitted', 'error', 'Итог обязательств исключает строки кредитов', balance_sheet + '!' + fc + '35',
                  'Исходная формула исключает краткосрочные и долгосрочные кредиты строк 30 и 31. В распознанном профиле исправленный итог рассчитывается отдельно по Mapping.')
            break
    for month in loaded_months:
        fc = col(4 + 2 * month)
        equity = cell(balance_sheet, fc + '36').get('formula') or ''
        if {32, 33, 34, 50, 51, 52} <= _rows_referenced(equity, fc):
            issue('capital_double_count', 'error', 'Итог капитала включает повторные подробные строки', balance_sheet + '!' + fc + '36',
                  'Детализация капитала сама по себе допустима; её повторное включение в один итог требует исправления.')
            break
    for month in loaded_months:
        fc = col(4 + 2 * month)
        social = cell(balance_sheet, fc + '25').get('formula') or ''
        total = cell(balance_sheet, fc + '35').get('formula') or ''
        if re.search(r'![A-Z]+\$?44\b', social.replace('$', ''), re.I) and {25, 46} <= _rows_referenced(total, fc):
            issue('social_double_count', 'error', 'Социальное обязательство может учитываться дважды', balance_sheet + '!' + fc + '25; ' + fc + '35; ' + fc + '46',
                  'Социальные платежи входят и в налоги, и в прочие обязательства. Нужно согласовать классификацию по счетам.')
            break
    for month in loaded_months:
        fc = col(4 + 2 * month)
        ebit = cell(pnl_sheet, fc + '18').get('formula') or ''
        if 16 in _rows_referenced(ebit, fc) and 17 not in _rows_referenced(ebit, fc):
            issue('ebit_classification', 'warning', 'Формула EBIT не использует строку амортизации', pnl_sheet + '!' + fc + '18',
                  'Нужно проверить журнал и распределение амортизации в себестоимость/OPEX. Если она уже включена, повторное вычитание исказит прибыль.')
            break
    for month in loaded_months:
        fc = col(4 + 2 * month)
        gross = cell(pnl_sheet, fc + '8').get('formula') or ''
        net = cell(pnl_sheet, fc + '7').get('formula') or ''
        if 9 in _rows_referenced(gross, fc) and 9 in _rows_referenced(net, fc) and '-' in gross and '-' in net:
            issue('returns_double_count', 'warning', 'Возвраты могут вычитаться дважды', pnl_sheet + '!' + fc + '8; ' + fc + '7',
                  'Возврат участвует и в исходной выручке, и в строке чистых продаж. Нужны проводки и определение выручки до/после возвратов.')
            break
    for month in loaded_months:
        fc = col(4 + 2 * month)
        roe = cell(balance_sheet, fc + '64').get('formula') or ''
        if re.search(r"09_Statements'?!\$?" + fc + r'\$?7\b', roe, re.I):
            issue('roe_methodology', 'warning', 'ROE использует выручку вместо чистой прибыли', balance_sheet + '!' + fc + '64',
                  'Нужно согласовать чистую прибыль периода, средний капитал и периодизацию. Авторский порог не оценивается по непроверенной формуле.')
            break
    for month in loaded_months:
        fc = col(4 + 2 * month)
        roa = cell(balance_sheet, fc + '63').get('formula') or ''
        if 37 in _rows_referenced(roa, fc):
            issue('roa_methodology', 'warning', 'ROA требует проверки базы и периода', balance_sheet + '!' + fc + '63',
                  'В знаменателе используется итог баланса на одну дату. Нужны средние активы и согласованный период прибыли; нормативы не применяются автоматически.')
            break
    fact_sheet = '20_Fact_Input | Fakt'
    for row in (12, 13):
        formula = cell(fact_sheet, 'D' + str(row)).get('formula') or ''
        if (fact_sheet, 'D' + str(row)) in scope and re.search(r'Mapping!\$?K', formula, re.I):
            issue('payroll_payable_as_expense', 'warning', 'Расходы зарплаты или социальных платежей взяты из остатка долга', fact_sheet + '!D' + str(row),
                  'Конечное кредитовое сальдо не равно начисленному расходу. Нужны начисления и распределение; нельзя вычитать уже включённые в себестоимость/OPEX суммы повторно.')
    inventory_formula = cell(balance_sheet, 'F13').get('formula') or ''
    inventory_refs = re.findall(r"'20_Fact_Input \| Fakt'!\$?([A-Z]+)\$?([0-9]+)", inventory_formula)
    if 1 in loaded_months and len(inventory_refs) != len(set(inventory_refs)):
        issue('inventory_duplicate', 'error', 'В запасах повторяется одна ссылка', balance_sheet + '!F13',
              'Повторный источник может завысить запасы; проверьте материалы, НЗП, полуфабрикаты и готовую продукцию по месяцам.')
    issue('source_review_only', 'info', 'Расчёт Mapping и проверка исходной модели', '09_Statements; 09b_Balance',
          'Расчётные значения отмечены отдельно от сохранённых значений Excel, с источниками и методикой. Данные не проведены в учёт; незагруженные месяцы не заменяются нулями.')
    issue('ifrs_adjustments_missing', 'warning', 'Соответствие МСФО пока не подтверждено', 'Учётная политика и реестр корректировок',
          'Нужны учётная политика, корректировки, сравнительные данные и раскрытия. Пороги коэффициентов в колонке A — авторские ориентиры, а не нормативы МСФО; оценка «норма / отклонение» не выполняется.')
    source_defects = {'equity_plug': ('balance', [36]), 'loans_omitted': ('balance', [35]),
                      'social_double_count': ('balance', [25, 35, 46]),
                      'capital_double_count': ('balance', [36]),
                      'roe_methodology': ('balance', [64]), 'roa_methodology': ('balance', [63]),
                      'inventory_duplicate': ('balance', [13]),
                      'returns_double_count': ('pnl', [7, 8]), 'ebit_classification': ('pnl', [18])}
    for item in issues:
        if item['code'] == 'yearless_sumifs' and calculation.get('status') == 'derived_provisional':
            # The immutable Excel formula is still defective, but the site no
            # longer evaluates its month-only lookup for Mapping fact amounts.
            item['stage'] = 'source_formula'
            item['resolved_in_calculation'] = True
            item['calculation_resolution'] = 'Mapping facts select exact year and month; unconfirmed classifications remain unavailable.'
        if item['code'] not in source_defects:
            continue
        key, row_numbers = source_defects[item['code']]
        match = re.search(r'!([A-Z]+)[0-9]+', item['source'])
        month = (column_index_from_string(match.group(1)) - 4) // 2 if match else None
        if month is None or not 1 <= month <= 12:
            continue
        affected = []
        for row in reports[key]['rows']:
            if row['row'] not in row_numbers:
                continue
            value = row['cells'][month - 1]['fact']
            affected.append({'report': key, 'row': row['row'], 'month': month, 'mode': 'fact',
                             'source': value['source'], 'status': value['status']})
        item['stage'] = 'source_formula'
        item['resolved_in_calculation'] = (len(affected) == len(row_numbers)
                                          and all(c['status'] == 'calculated' for c in affected))
        item['affected_cells'] = affected
    return {'filename': filename, 'year': year, 'currency': currency, 'unit': unit_label,
            'unit_status': unit_status, 'review_status': 'needs_review', 'reviewed_as_of': as_of.isoformat(),
            'review_version': REVIEW_VERSION, 'plan_source': plan_source, 'calculation': calculation,
            'standards': {'presentation_basis': 'IAS 1' if year == 2026 else 'Требует подтверждения',
                          'profile': 'IAS1_2026' if year == 2026 else 'requires_confirmation',
                          'compliance_status': 'not_assessed', 'norm_status': 'author_guideline'},
            'coverage': coverage, 'issues': issues,
            'requirements': source_requirements({ym: rs for ym, rs in records.items() if ym[0] == year},
                                                coverage, currency, unit_status, plan_source, calculation, issues),
            'reports': reports}


def _policy_key(source):
    # Settings are otherwise global, so every lookup must bind both company and
    # immutable source. The signed-in company is checked before source lookup.
    return f'financial_revenue:{source.company_id}:{source.id}'


def source_revenue_policy(s, source):
    default = {'basis': 'unconfirmed', 'status': 'unconfirmed', 'revision': 0,
               'source_sha256': source.sha256, 'confirmed_at': None, 'confirmed_by': None}
    saved = s.get(Setting, _policy_key(source))
    if saved is None:
        return default
    try:
        value = json.loads(saved.value)
        if (not isinstance(value, dict) or value.get('company_id') != source.company_id
                or value.get('source_id') != source.id or value.get('source_sha256') != source.sha256
                or type(value.get('revision')) is not int or value['revision'] < 1
                or value.get('basis') not in ('unconfirmed', CONFIRMED_REVENUE_BASIS)):
            return default
        return {key: value.get(key) for key in default} | {
            'status': 'confirmed' if value['basis'] == CONFIRMED_REVENUE_BASIS else 'unconfirmed'}
    except (ValueError, TypeError):
        return default


class RevenuePolicyIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    basis: Literal['unconfirmed', 'gross_excluding_vat_before_returns_discounts']
    expected_revision: int = Field(ge=0, strict=True)
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.post('/api/financial-statements/{source_id}/revenue-policy')
def confirm_revenue_policy(source_id: int, payload: RevenuePolicyIn, request: Request,
                           company_id: int | None = Query(default=None, ge=1)):
    with unit(True) as s:
        user, _ = session_user(s, request, 'export')
        require_permission(user, 'import')
        source = s.get(FinancialStatementSource, source_id)
        if source is None:
            raise HTTPException(404, 'Исходник не найден в выбранной компании.')
        if payload.source_sha256 != source.sha256:
            raise HTTPException(409, 'Выбран другой исходник. Обновите отчёт перед подтверждением.')
        previous = source_revenue_policy(s, source)
        if payload.expected_revision != previous['revision']:
            raise HTTPException(409, 'Правило выручки уже изменено. Обновите отчёт перед подтверждением.')
        if payload.basis == CONFIRMED_REVENUE_BASIS:
            review = current_source_review(source, previous)
            if review.get('calculation', {}).get('profile') is None:
                raise HTTPException(422, 'Формат ОСВ не распознан: правило выручки нельзя применить к этому исходнику.')
        if payload.basis == previous['basis']:
            return {'company_id': source.company_id, 'source_id': source.id, 'changed': False, 'policy': previous}
        policy = {'basis': payload.basis, 'status': 'confirmed' if payload.basis == CONFIRMED_REVENUE_BASIS else 'unconfirmed',
                  'revision': previous['revision'] + 1, 'source_sha256': source.sha256,
                  'confirmed_at': datetime.now(timezone.utc).isoformat(), 'confirmed_by': user.id}
        stored = {**policy, 'company_id': source.company_id, 'source_id': source.id}
        setting = s.get(Setting, _policy_key(source))
        if setting is None:
            s.add(Setting(key=_policy_key(source), value=json.dumps(stored, ensure_ascii=False)))
        else:
            setting.value = json.dumps(stored, ensure_ascii=False)
        log(s, user, 'Изменено правило выручки PNL', 'financial_statement_source', source.id,
            json.dumps({'previous': previous, 'current': policy, 'scope': 'immutable_source'}, ensure_ascii=False))
        return {'company_id': source.company_id, 'source_id': source.id, 'changed': True, 'policy': policy}


def current_source_review(source, revenue_policy=None):
    """Re-review immutable source bytes with current code and original cutoff.

    A bounded process cache stores JSON, never mutable report objects. Its key
    includes company, source and version; authorization always precedes lookup.
    The database content and original snapshot remain untouched.
    """
    original = json.loads(source.snapshot)
    original_date = original.get('reviewed_as_of') or source.created_at.date().isoformat()
    try:
        as_of = date.fromisoformat(original_date)
    except (TypeError, ValueError):
        as_of = source.created_at.date()
        original_date = as_of.isoformat()
    revenue_policy = revenue_policy or {'basis': 'unconfirmed', 'status': 'unconfirmed', 'revision': 0,
                                        'source_sha256': source.sha256, 'confirmed_at': None, 'confirmed_by': None}
    policy_hash = hashlib.sha256(json.dumps(revenue_policy, sort_keys=True).encode()).hexdigest()
    key = (source.company_id, source.id, source.sha256, REVIEW_VERSION, original_date, policy_hash)
    with _review_cache_lock:
        cached = _review_cache.get(key)
        if cached is not None:
            _review_cache.move_to_end(key)
    if cached is None:
        # Original snapshots never contain a later confirmation. Rebuilding on
        # a confirmed policy ensures stale immutable snapshots cannot bypass it.
        result = (original if original.get('review_version') == REVIEW_VERSION and revenue_policy['revision'] == 0
                  else build_review(source.content, source.filename, as_of, revenue_policy))
        result = {**result, 'review_version': REVIEW_VERSION,
                  'revenue_policy': revenue_policy, 'source_sha256': source.sha256,
                  'original_reviewed_as_of': original_date,
                  'original_review_timestamp': source.created_at.replace(tzinfo=timezone.utc).isoformat()}
        cached = json.dumps(result, ensure_ascii=False)
        with _review_cache_lock:
            _review_cache[key] = cached
            _review_cache.move_to_end(key)
            while len(_review_cache) > 4:
                _review_cache.popitem(last=False)
    return json.loads(cached)


@router.get('/api/financial-statements')
def financial_statements(request: Request, source_id: int | None = Query(default=None, ge=1),
                         company_id: int | None = Query(default=None, ge=1)):
    with unit() as s:
        user, _ = session_user(s, request, 'export')
        sources = list(s.scalars(select(FinancialStatementSource).order_by(FinancialStatementSource.id.desc())))
        source = s.get(FinancialStatementSource, source_id) if source_id is not None else (sources[0] if sources else None)
        if source_id is not None and source is None:
            raise HTTPException(404, 'Исходник не найден в выбранной компании.')
        result = None
        if source:
            result = current_source_review(source, source_revenue_policy(s, source))
            result['id'] = source.id
            result['company_id'] = source.company_id
        return {'sources': [{'id': row.id, 'filename': row.filename,
                             'created_at': row.created_at.replace(tzinfo=timezone.utc).isoformat()} for row in sources],
                'selected': source.id if source else None, 'source': result,
                'company_id': s.info['company_id'], 'can_upload': 'import' in perms_of(user),
                'can_confirm_revenue_basis': 'import' in perms_of(user),
                'review_version': REVIEW_VERSION,
                'requirements': result['requirements'] if result else REQUIREMENTS}


@router.get('/api/financial-statements/reconciliation')
def financial_reconciliation(request: Request, year: int = Query(ge=2000, le=2100),
                             start_month: int = Query(default=1, ge=1, le=12),
                             end_month: int = Query(default=12, ge=1, le=12),
                             source_id: int | None = Query(default=None, ge=1),
                             company_id: int | None = Query(default=None, ge=1)):
    from .financial_reconciliation import build_reconciliation
    if start_month > end_month:
        raise HTTPException(400, 'Начало периода должно быть не позже конца.')
    with unit() as s:
        session_user(s, request, 'export')
        source = s.get(FinancialStatementSource, source_id) if source_id is not None else s.scalar(
            select(FinancialStatementSource).order_by(FinancialStatementSource.id.desc()).limit(1))
        if source_id is not None and source is None:
            raise HTTPException(404, 'Исходник не найден в выбранной компании.')
        result = build_reconciliation(current_source_review(source, source_revenue_policy(s, source)) if source else None,
                                      year, start_month, end_month)
        if source is None:
            result['status'] = 'missing'
        return {**result, 'company_id': s.info['company_id'], 'source_id': source.id if source else None}


@router.post('/api/financial-statements/upload')
async def upload_financial_statements(request: Request, company_id: int | None = Query(default=None, ge=1)):
    # Authorize before reading/parsing, then again inside the serialized write transaction.
    with unit() as s:
        user, _ = session_user(s, request, 'export')
        require_permission(user, 'import')
    filename = unquote(request.headers.get('X-Filename', 'FinancialStatements.xlsx')).replace('\\', '/')
    filename = PurePosixPath(filename).name
    if (not filename.lower().endswith('.xlsx') or not filename or len(filename) > 220
            or any(unicodedata.category(c).startswith('C') for c in filename)):
        raise HTTPException(422, 'Нужен файл Excel (.xlsx) с корректным названием.')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_SIZE:
            raise HTTPException(413, 'Файл больше 20 МБ.')
    digest = hashlib.sha256(raw).hexdigest()
    with unit() as s:
        user, _ = session_user(s, request, 'export')
        require_permission(user, 'import')
        existing = s.scalar(select(FinancialStatementSource).where(FinancialStatementSource.sha256 == digest))
        if existing:
            return {'id': existing.id, 'created': False, 'company_id': s.info['company_id']}
    try:
        snapshot = build_review(bytes(raw), filename)
    except ValueError as e:
        raise HTTPException(422, str(e))
    with unit(True) as s:
        user, _ = session_user(s, request, 'export')
        require_permission(user, 'import')
        existing = s.scalar(select(FinancialStatementSource).where(FinancialStatementSource.sha256 == digest))
        if existing:
            return {'id': existing.id, 'created': False, 'company_id': s.info['company_id']}
        source = FinancialStatementSource(company_id=s.info['company_id'], created_by=user.id, filename=filename,
                                          sha256=digest, size=len(raw), snapshot=json.dumps(snapshot, ensure_ascii=False), content=bytes(raw))
        s.add(source)
        s.flush()
        log(s, user, 'Загружен исходник PNL и баланса', 'financial_statement_source', source.id,
            f'{filename}; сохранён для проверки, без проведения операций')
        return {'id': source.id, 'created': True, 'company_id': s.info['company_id']}
