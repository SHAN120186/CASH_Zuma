"""Derived review of a recognised grouped OSV; never edits the source or ledger.

This is a versioned presentation profile, not an IFRS certification. A complete
account register, report/account labels and expected row positions must agree
before blank sides are interpreted as zero. Relocated, unsupported or incomplete
templates retain their source view: UI cards and KPI methods use fixed row keys.
"""
from __future__ import annotations

import calendar
import copy
import re
from collections import Counter
from decimal import Decimal, DecimalException, localcontext

PROFILE = 'uzbek_grouped_chart_finmodel_v1'
ZERO = Decimal(0)
MAX_DISPLAY = Decimal('1.7976931348623157e308')
ASSET_GROUPS = {
    8: ('5000', '5100', '5200', '5500', '5700'), 9: ('4000',),
    10: ('4800',), 11: ('4400',), 12: ('4500',),
    13: ('1000', '2000', '2100', '2500', '2800', '2900'),
    14: ('3100',), 15: ('4300',), 16: ('4200',), 17: ('5800',),
    18: ('0100', '0200'),
}
LIABILITY_GROUPS = {
    22: ('6000',), 23: ('6300',), 24: ('6200',), 25: ('6400',),
    30: ('6800',), 31: ('7800',), 42: ('6500',), 43: ('6600',),
    44: ('6700',), 45: ('6900',),
}
CAPITAL = ('8300', '8500', '8700', '9900')
BS_ACCOUNTS = set(sum(ASSET_GROUPS.values(), ()) + sum(LIABILITY_GROUPS.values(), ()) + CAPITAL)
# 5800 is optional only after every other balance account in this profile is
# registered. No absent account is silently zero in a thin/partial OSV.
REQUIRED_ACCOUNTS = BS_ACCOUNTS - {'5800'}
KNOWN_ACCOUNTS = BS_ACCOUNTS | {'9000', '9040', '9050', '9100', '9200', '9300', '9400', '9500', '9600', '9800'}
CHART_REGISTRATION = {24: '0100', 25: '0200', 40: '6000', 48: '6800',
                      49: '7800', 50: '8300', 51: '8500', 52: '8700'}
CORE_NAMES = {
    '0100': r'основн|fixed asset|ppe|asosiy', '0200': r'амортиз|depreciat|eskir',
    '6000': r'поставщик|supplier|yetkaz', '6800': r'краткоср|short.term|qisqa',
    '7800': r'долгоср|long.term|uzoq', '8300': r'уставн|share capital|ustav',
    '8500': r'резервн|reserve|zaxira', '8700': r'нераспредел|retained|taqsimlan',
    '9900': r'финансов.*результат|financial result|yaku.*natija',
}
LABELS = {
    8: r'^cash\b|денеж.*эквивал', 9: r'^accounts receivable|^дебиторск',
    10: r'^other receivable|прочая дебитор', 11: r'^tax advance|авансы по налог',
    12: r'^social advance|авансы по социаль', 13: r'^inventory total|запасы.*итого',
    14: r'^prepaid expense|расходы будущ', 15: r'^advances to suppliers|авансы поставщ',
    16: r'^advances to employees|авансы сотруд', 17: r'^deposits|депозит',
    18: r'^net ppe|основные средства.*нетто', 19: r'^total assets|всего актив',
    22: r'^accounts payable|^кредиторск', 23: r'^customer advance|авансы получ',
    24: r'^deferred revenue|отложенные доход', 25: r'^taxes payable|налоги к уплате',
    26: r'^debt\b|заемные средства|заёмные средства',
    30: r'short.term.*6800|краткоср.*6800', 31: r'long.term.*7800|долгоср.*7800',
    32: r'share capital.*8300|уставный капитал', 33: r'reserve capital.*8500|резервный капитал',
    34: r'retained earnings.*8700|нераспределенная прибыль',
    35: r'^total liabilities|всего обязательств', 36: r'^equity|^капитал',
    37: r'^total l\+e|всего обязательств и капитала', 38: r'balance ?check|проверка баланса',
    42: r'social insurance payable|страховые взносы.*6500',
    43: r'due to founders|задолженность перед учред', 44: r'payroll payable|заработная плата.*6700',
    45: r'other creditors|прочие кредитор', 46: r'total other payables|прочая кредитор.*итого',
    50: r'^share capital|уставный капитал', 51: r'^reserve capital|резервный капитал',
    52: r'^retained earnings opening|нераспределенная прибыль на начало',
    53: r'current year net income|чистая прибыль отчетного года',
    54: r'^retained earnings \(ending\)|нераспределенная прибыль.*конец',
    55: r'^equity \(from accounts\)|капитал.*данным счетов',
    56: r'^equity plug|плагин капитала',
    62: r'net margin|рентабельность продаж', 63: r'\broa\b|рентабельность активов',
    64: r'\broe\b|рентабельность капитала', 65: r'\bdso\b|оборачиваемость дз',
    66: r'inventory days|оборачиваемость запасов', 67: r'дз /|дебиторская задолженность /',
    68: r'дс /|денежные средства /', 69: r'амортизация /', 70: r'оборачиваемость ос',
    72: r'рабочий капитал|working capital', 73: r'коэффициент ликвидности|current ratio',
    74: r'коэффициент покрытия|quick ratio', 75: r'коэффициент автономии|equity ratio',
    76: r'соотношение заемных|debt.*equity',
    77: r'^дебиторская задолженность', 78: r'^денежные средства',
    79: r'^запасы$', 80: r'^основные средства$', 81: r'^кредиторская задолженность',
}
PNL_NAMES = {
    '9000': r'доход.*основн|операцион.*доход|operating.*income|revenue|tushum',
    '9040': r'возврат|return|qaytar', '9050': r'скидк|discount|chegirma',
    '9100': r'себестоим|cost of sales|cogs|tannarx',
    '9200': r'выбыт|реализац.*актив|disposal|sotil',
    '9300': r'проч.*доход|other.*income|boshqa.*daromad',
    '9400': r'общехозяй|расход|opex|operating.*expense|xarajat',
    '9500': r'доход.*финанс|financ.*income|moliyaviy.*daromad',
    '9600': r'расход.*финанс|financ.*expense|moliyaviy.*xarajat',
    '9800': r'налог.*прибыл|income tax|profit tax|foyda.*sol',
}
PNL_LABELS = {
    7: r'net sales|net revenue|выручка.*чист|чистая выручка|чистые продажи',
    8: r'^revenue\s*\(выручка\)$|^gross sales$|^gross revenue$|^выручка до',
    9: r'return|возврат.*9040', 10: r'discount|скидк.*9050',
    11: r'cogs|себестоимость', 12: r'^gross profit(?!.*%)|валов.*прибыл',
    13: r'^gross profit.*%|gross margin|валовая.*рентабель',
    14: r'other operating income|проч.*операц.*доход', 15: r'\bopex\b|оператив.*расход|операцион.*расход',
    16: r'\bebitda\b', 17: r'\bda\b|depreciation|амортизац',
    18: r'\bebit\b', 19: r'finance income|финанс.*доход',
    20: r'interest expense|finance expense|процентные расходы',
    21: r'\bebt\b|profit before tax|прибыль до налого',
    22: r'^tax\b|income tax|налог на прибыль',
    23: r'net profit|net income(?!.*%)|чистая прибыль',
    24: r'net income.*%|net margin|рентабельность чистой прибыли',
}
REQUIRED_FIELDS = [
    {'key': 'actual_revenue_basis', 'title': 'Выручка 9000, возвраты 9040 и скидки 9050', 'reason': 'Подтвердите: 9000 включает возвраты/скидки и НДС или отражает выручку до них? Нужен состав агрегата и правило однократного учета 9040/9050; исходные чистая выручка и прибыль сохранены только для сравнения.'},
    {'key': 'depreciation_allocation', 'title': 'Распределение амортизации для EBITDA', 'reason': 'Нужно распределение амортизации 0200 между COGS, OPEX и другими статьями; EBITDA не заменяется EBIT и амортизация не вычитается повторно.'},
    {'key': 'plan_vat_basis', 'title': 'НДС в плановых ценах', 'reason': 'Подтвердите включение НДС в плановые цены; выручка и НДС исходного плана сохранены без догадок.'},
    {'key': 'plan_marketing_classification', 'title': 'Процентные расходы маркетинга', 'reason': 'Подтвердите состав процентных расходов маркетинга и включение в FC/COGS; они не считаются кредитными процентами автоматически.'},
    {'key': 'plan_cost_allocation', 'title': 'Состав и распределение FC', 'reason': 'Подтвердите состав FC и распределение между операционными расходами, амортизацией и финансовыми расходами; исходный план сохранён.'},
    {'key': 'debt_maturity', 'title': 'Сроки погашения кредитов', 'reason': 'Для классификации обязательств использованы 6800/7800; текущую часть долгосрочного долга нужно подтвердить графиком.'},
    {'key': 'cash_restrictions', 'title': 'Ограничения денежных счетов', 'reason': '5500 включён в денежные активы; ограничения спецсчетов и эквивалентность депозитов требуют подтверждения.'},
]


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result.copy_abs() <= MAX_DISPLAY and len(result.as_tuple().digits) <= 1000 else None
    except (DecimalException, ValueError):
        return None


def _text(value):
    return format(value, 'f') if value is not None else None


def _cell(row, month):
    return next((c for c in row.get('cells', []) if c.get('month') == month), None)


def _read(row, month):
    cell = _cell(row, month) if row else None
    item = cell.get('fact', {}) if cell else {}
    return _number(item.get('value')) if item.get('status') in ('value', 'cached', 'calculated') else None


def _locate(report, row_number, pattern):
    # Locate semantic mismatches for the profile guard. A relocated match is
    # identified here but is rejected before calculations by _profile.
    rows = report.get('rows', [])
    candidates = [row for row in rows if re.search(pattern, str(row.get('label', '')).casefold())]
    preferred = next((r for r in candidates if r.get('row') == row_number), None)
    return preferred or (candidates[0] if len(candidates) == 1 else None)


def _profile(reports, sheets):
    balance = reports.get('balance', {})
    pnl = reports.get('pnl', {})
    rows = {n: _locate(balance, n, pattern) for n, pattern in LABELS.items()}
    if any(row is not None and row.get('row') != n for n, row in rows.items()):
        return None
    essential = set(ASSET_GROUPS) | set(LIABILITY_GROUPS) | {19, 35, 36, 37, 50, 51, 52, 53, 55}
    if any(rows[n] is None for n in essential):
        return None
    fact = sheets.get('20_Fact_Input | Fakt', {})
    if any(str(fact.get('A' + str(row), {}).get('value', '')).strip() != account
           for row, account in CHART_REGISTRATION.items()):
        return None
    p = {n: _locate(pnl, n, pattern) for n, pattern in PNL_LABELS.items()}
    if any(p[n] is None for n in (7, 11, 16, 18, 23)):
        return None
    if any(row is not None and row.get('row') != n for n, row in p.items()):
        return None
    return rows, p


class _OSV:
    def __init__(self, rows, year, month):
        self.year, self.month = year, month
        self.rows = {r['account']: {**r, 'measures': [_number(v) for v in r['measures']]} for r in rows}

    def amount(self, accounts, opening=False, credit=False):
        index = 0 if opening else 4
        total, provenance = ZERO, []
        for account in accounts:
            row = self.rows.get(account)
            if row is None:
                # Only optional5800 can reach here, after the complete-register
                # guard. Keep that convention inspectable instead of inventing a cell.
                provenance.append({'source': f'Mapping · {self.year}-{self.month:02}',
                                   'account': account, 'value': '0', 'assumption': 'absent_zero_in_complete_account_register'})
                continue
            dr, cr = row['measures'][index:index + 2]
            total += (cr or ZERO) - (dr or ZERO) if credit else (dr or ZERO) - (cr or ZERO)
            for offset, value in enumerate((dr, cr)):
                provenance.append({'source': f"Mapping!{'FGHIJK'[index + offset]}{row['row']}",
                                   'account': account, 'measure': ('opening_debit', 'opening_credit', 'turnover_debit', 'turnover_credit', 'closing_debit', 'closing_credit')[index + offset],
                                   'value': _text(value), 'blank_side_zero': value is None})
        return total, provenance

    def totals(self, opening=False):
        groups = {row: self.amount(accounts, opening) for row, accounts in ASSET_GROUPS.items()}
        liabilities = {row: self.amount(accounts, opening, True) for row, accounts in LIABILITY_GROUPS.items()}
        assets = sum((v[0] for v in groups.values()), ZERO)
        debt = sum((v[0] for v in liabilities.values()), ZERO)
        equity, eq_provenance = self.amount(CAPITAL, opening, True)
        return groups, liabilities, assets, debt, equity, eq_provenance

    def turnover(self, account, credit=False):
        """A named registered account side, never absent-account zero or ABS."""
        row = self.rows.get(account)
        if row is None:
            return None, [{'source': f'Mapping · {self.year}-{self.month:02}',
                           'account': account, 'missing_account': True}]
        index = 3 if credit else 2
        value = row['measures'][index]
        return value or ZERO, [{'source': f"Mapping!{'FGHIJK'[index]}{row['row']}",
                               'account': account,
                               'measure': 'turnover_credit' if credit else 'turnover_debit',
                               'value': _text(value), 'blank_side_zero': value is None}]


def _provenance(items):
    unique = {}
    for item in items:
        unique[(item.get('source'), item.get('account'), item.get('measure'))] = item
    return list(unique.values())


def _derived(original, value, provenance, method, month, year, reason=None):
    result = dict(original or {})
    if 'source_cache' not in result:
        result['source_cache'] = result.get('value')
    result['source_formula'] = result.get('source_formula', result.get('formula'))
    result['source_status'] = result.get('source_status', result.get('status'))
    result.pop('formula', None)
    finite = value is not None and value.is_finite() and abs(value) <= MAX_DISPLAY
    result.update(value=_text(value) if finite else None,
                  status='calculated' if finite else 'requires_allocation' if reason == 'depreciation_allocation_required' else 'unavailable',
                  provenance=_provenance(provenance), methodology=method,
                  calculation_month=month, period=f'{year}-{month:02}',
                  calendar_days=calendar.monthrange(year, month)[1])
    if reason or not finite:
        result['reason'] = reason or 'nonfinite_result'
    else:
        result.pop('reason', None)
    return result


def _set(row, month, year, value, provenance, method, opening=None, reason=None):
    if row is None:
        return
    pair = _cell(row, month)
    if pair is None:
        return
    original = pair.get('fact', {})
    result = _derived(original, value, provenance, method, month, year, reason)
    if opening:
        result['opening_balance'] = _derived({}, opening[0], opening[1],
                                            'Signed opening OSV balance for the same month', month, year)
        result['opening_balance']['source'] = f'Mapping · {year}-{month:02} · opening'
    pair['fact'] = result


def _guard(rows):
    codes = [r.get('account') for r in rows]
    if any(count > 1 for count in Counter(codes).values()):
        return 'duplicate_accounts'
    if not REQUIRED_ACCOUNTS <= set(codes):
        return 'incomplete_account_register'
    if set(codes) - KNOWN_ACCOUNTS:
        return 'unmapped_accounts'
    for row in rows:
        values = row.get('measures', [])
        if row.get('invalid_amounts') or len(values) != 6 or any(v is not None and _number(v) is None for v in values):
            return 'invalid_mapping_amounts'
        if all(v is None for v in values) and not row.get('blank_amounts'):
            return 'missing_mapping_amounts'
        if row['account'] in CORE_NAMES and not re.search(CORE_NAMES[row['account']], str(row.get('account_name', '')).casefold()):
            return 'account_chart_conflict'
        if row['account'] in PNL_NAMES and not re.search(PNL_NAMES[row['account']], str(row.get('account_name', '')).casefold()):
            return 'account_chart_conflict'
        if row['account'] not in BS_ACCOUNTS and any(v is not None and _number(v) != ZERO for v in values[:2] + values[4:]):
            return 'unclosed_profit_accounts'
    return None


def apply_mapping_reports(reports, records, year, coverage, sheets, issue):
    """Mutate only derived report cells; return a JSON-safe provenance summary."""
    metadata = {'status': 'source_only', 'profile': None, 'source_preserved': True,
                'plan_basis': 'immutable_source_cache', 'plan_classification_status': 'pending_vat_marketing_confirmation',
                'required_fields': REQUIRED_FIELDS, 'months': [], 'ratio_period_basis': 'monthly_unannualized',
                'actual_pnl_basis': 'mapping_named_account_turnovers',
                'actual_pnl_classification_status': 'classification_pending'}
    profile = _profile(reports, sheets)
    if profile is None or not isinstance(year, int) or not 2000 <= year <= 2100:
        metadata['reason'] = 'profile_unrecognized'
        return metadata
    balance_rows, pnl_rows = profile
    # A frozen source comparison is not an endorsement of its classifications.
    reports['pnl'].setdefault('source_actual', {
        'status': 'reported_source', 'classification_pending': True,
        'rows': [{'row': r['row'], 'label': r['label'],
                  'cells': [{'month': c['month'], 'fact': copy.deepcopy(c.get('fact', {}))}
                            for c in r.get('cells', [])]}
                 for r in reports['pnl'].get('rows', [])],
    })
    metadata['profile'] = PROFILE
    for covered in coverage:
        month = covered['month']
        if covered.get('status') != 'available' or covered.get('calculation_eligible') is not True:
            metadata['months'].append({'month': month, 'status': covered.get('status', 'missing')})
            continue
        source_rows = records.get((year, month), [])
        reason = _guard(source_rows)
        if reason:
            metadata['months'].append({'month': month, 'status': 'source_only', 'reason': reason})
            issue('mapping_calculation_blocked', 'warning', 'Для расчёта не подтверждён полный профиль ОСВ',
                  f'Mapping · {year}-{month:02}', reason + ': исходные значения сохранены, отсутствующие счета не превращены в нули.')
            continue
        try:
            with localcontext() as ctx:
                ctx.prec = 2048
                month_meta = _calculate_month(balance_rows, pnl_rows, _OSV(source_rows, year, month), issue)
                metadata['months'].append(month_meta)
        except DecimalException:
            metadata['months'].append({'month': month, 'status': 'source_only', 'reason': 'nonfinite_result'})
            issue('mapping_calculation_invalid', 'error', 'Расчёт выходит за допустимый диапазон', f'Mapping · {year}-{month:02}', 'Исходные значения сохранены.')
    if any(m.get('status') == 'calculated' for m in metadata['months']):
        metadata['status'] = 'derived_provisional'
    for report in reports.values():
        report['calculation'] = {k: v for k, v in metadata.items() if k != 'required_fields'}
    issue('plan_classification_pending', 'warning', 'План сохранён в исходном виде', '98_CashMacro_Import; 09_Statements',
          'Нужны база НДС и классификация процентных расходов маркетинга/FC. Плановые суммы не изменены и не выданы за подтверждённую прибыль МСФО.')
    return metadata


def _pnl_from_mapping(pnl, osv, issue):
    """Confirmed account-side flows; unresolved revenue is never a plug.

    Closed debit/credit turnover totals do not disclose transaction counterparts
    or whether 9000 includes 9040/9050/VAT. They cannot determine net sales,
    EBITDA allocation, asset-disposal profit or a missing income-tax account.
    9900 credit minus debit is a posted result, checked separately against its
    signed opening/closing movement; that result is not used to reverse-engineer
    revenue or to force agreement with the original workbook's rounded NI.
    """
    year, month = osv.year, osv.month
    ni_source = (_cell(pnl[23], month) or {}).get('fact', {})
    source_ni = (_number(ni_source.get('source_cache', ni_source.get('value')))
                 if ni_source.get('source_status', ni_source.get('status')) in ('value', 'cached', 'calculated') else None)
    flows = {}
    for row, account, credit, label in (
        (8, '9000', False, 'Оборот 9000: состав выручки требует подтверждения'),
        (9, '9040', False, 'Оборот возвратов 9040'),
        (10, '9050', False, 'Оборот скидок 9050'),
        (11, '9100', False, 'Себестоимость: дебетовый оборот 9100'),
        (14, '9300', False, 'Прочие операционные доходы: оборот 9300'),
        (15, '9400', False, 'Операционные расходы: дебетовый оборот 9400'),
        (17, '0200', True, 'Кредитовый оборот 0200: распределение не подтверждено'),
        (19, '9500', False, 'Финансовые доходы: оборот 9500'),
        (20, '9600', False, 'Финансовые расходы: оборот 9600'),
        (22, '9800', False, 'Налог на прибыль: оборот 9800, если счет загружен'),
    ):
        value, provenance = osv.turnover(account, credit)
        flows[row] = (value, provenance)
        _set(pnl.get(row), month, year, value, provenance,
             f"Registered account {account}: {'credit' if credit else 'debit'} turnover; no ABS, allocation or VAT inference",
             reason='absent_profit_account' if value is None else None)
        if pnl.get(row):
            pnl[row].setdefault('source_label', pnl[row]['label'])
            pnl[row]['fact_label'] = label
    revenue_provenance = [p for row in (8, 9, 10) for p in flows[row][1]]
    for row in (7, 12, 13, 18, 21, 24):
        _set(pnl.get(row), month, year, None, revenue_provenance,
             'Net revenue basis9000/9040/9050/VAT is unconfirmed; no return, discount or tax policy is guessed',
             reason='actual_revenue_classification_required')
    _set(pnl[16], month, year, None, flows[17][1] + flows[11][1] + flows[15][1],
         'EBITDA requires confirmed revenue and depreciation allocation in COGS/OPEX;0200 is not subtracted twice',
         reason='depreciation_allocation_required')
    debit, debit_p = osv.turnover('9900')
    credit, credit_p = osv.turnover('9900', True)
    closing, closing_p = osv.amount(('9900',), credit=True)
    opening, opening_p = osv.amount(('9900',), True, True)
    posted_result = credit - debit
    movement = closing - opening
    movement_difference = movement - posted_result
    reconciled = abs(movement_difference) <= Decimal(1)
    representable = _number(posted_result) is not None
    ni = posted_result if reconciled and representable else None
    ni_p = debit_p + credit_p + opening_p + closing_p
    _set(pnl[23], month, year, ni, ni_p,
         'Posted financial result: credit turnover9900 minus debit turnover9900, independently checked against signed balance movement (1 source-unit rounding tolerance); not an IFRS certification',
         reason=None if ni is not None else 'financial_result_rollforward_mismatch' if not reconciled else 'nonfinite_result')
    pnl[23].setdefault('source_label', pnl[23]['label'])
    pnl[23]['fact_label'] = 'Финансовый результат периода по 9900'
    difference = source_ni - posted_result if source_ni is not None else None
    source_status = 'matches_rounding' if difference is not None and abs(difference) <= Decimal(1) else 'unavailable' if difference is None else 'mismatch'
    source_check = {'status': source_status, 'source_value': _text(source_ni),
                    'derived_value': _text(ni), 'difference': _text(difference),
                    'comparison_basis': 'reported_source_NI_minus_posted_result_9900',
                    'rounding_tolerance': '1 source unit', 'provenance': ni_p}
    if source_status == 'mismatch':
        issue('net_income_account_mismatch', 'warning', 'Исходная чистая прибыль отличается от результата 9900',
              f'Mapping · {year}-{month:02} · 9900',
              'Исходный показатель сохранен для сравнения; разница исходника и оборотов 9900: ' + _text(difference) + '. Результат не подгоняется.')
    if not reconciled:
        issue('financial_result_rollforward_mismatch', 'warning', 'Результат 9900 не сходится с движением сальдо',
              f'Mapping · {year}-{month:02} · 9900',
              'Разница изменения сальдо и чистого оборота: ' + _text(movement_difference) + '; финансовый результат не выдан за подтвержденный.')
    issue('actual_revenue_classification_pending', 'warning', 'Нужно подтвердить состав выручки 9000',
          f'Mapping · {year}-{month:02} · 9000/9040/9050',
          'Подтвердите состав 9000 и однократное включение возвратов/скидок/НДС. Исходные NetRevenue/Gross/EBIT/PBT сохранены как reported_source; сайт их не объявляет подтвержденным расчетом.')
    disposal_debit, disposal_debit_p = osv.turnover('9200')
    disposal_credit, disposal_credit_p = osv.turnover('9200', True)
    return {'ni': ni, 'ni_provenance': ni_p, 'revenue': None,
            'revenue_provenance': revenue_provenance,
            'cogs': flows[11][0], 'cogs_provenance': flows[11][1],
            'net_income_check': source_check,
            'metadata': {'status': 'classification_pending', 'basis': 'mapping_named_account_turnovers',
                         'posted_financial_result': _text(ni), 'reported_source_net_income': _text(source_ni),
                         'result_account_movement': _text(movement),
                         'result_rollforward_difference': _text(movement_difference),
                         'result_rollforward_status': 'matches_rounding' if reconciled else 'mismatch',
                         'rounding_tolerance': '1 source unit',
                         'net_revenue_status': 'actual_revenue_classification_required',
                         'income_tax_status': 'registered_turnover' if flows[22][0] is not None else 'absent_profit_account',
                         'ebitda_status': 'depreciation_allocation_required',
                         'depreciation_status': 'credit_turnover_only_allocation_pending',
                         'finance_expense_status': 'general_finance_expense_not_interest_only',
                         'payroll_expense_status': 'payroll_payable_balance_is_not_expense',
                         'social_expense_status': 'social_payable_balance_is_not_expense',
                         'disposal_result_status': 'counterpart_classification_required' if '9200' in osv.rows else 'absent_profit_account',
                         'unclassified_account_turnovers': {'9200': {
                             'debit': _text(disposal_debit), 'credit': _text(disposal_credit),
                             'result': None, 'reason': 'counterpart_classification_required' if '9200' in osv.rows else 'absent_profit_account',
                             'provenance': disposal_debit_p + disposal_credit_p}},
                         'provenance': ni_p}}


def _calculate_month(rows, pnl, osv, issue):
    year, month = osv.year, osv.month
    closing, debts, assets, liabilities, equity, ep = osv.totals()
    opening, opening_debts, opening_assets, _, opening_equity, oep = osv.totals(True)
    ap = _provenance([p for _, ps in closing.values() for p in ps])
    oap = _provenance([p for _, ps in opening.values() for p in ps])
    lp = _provenance([p for _, ps in debts.values() for p in ps])
    pnl_result = _pnl_from_mapping(pnl, osv, issue)
    ni, revenue, cogs = (pnl_result[k] for k in ('ni', 'revenue', 'cogs'))
    np, rp, cp = (pnl_result[k] for k in ('ni_provenance', 'revenue_provenance', 'cogs_provenance'))
    for row, (value, provenance) in closing.items():
        _set(rows[row], month, year, value, provenance, 'Signed closing debit minus credit of mapped asset accounts', opening[row])
    for row, (value, provenance) in debts.items():
        _set(rows[row], month, year, value, provenance, 'Signed closing credit minus debit of mapped liability accounts', opening_debts[row])
    _set(rows[19], month, year, assets, ap, 'Sum of mapped assets including WIP2000 and special bank accounts5500', (opening_assets, oap))
    _set(rows[35], month, year, liabilities, lp, 'All mapped liabilities;6800/7800 included,6500 counted once')
    _set(rows[26], month, year, debts[30][0] + debts[31][0], debts[30][1] + debts[31][1],
         'Total loan detail6800+7800; liability total includes these accounts once')
    _set(rows[36], month, year, equity, ep, 'Independent signed account equity8300+8500+8700+9900', (opening_equity, oep))
    _set(rows[37], month, year, liabilities + equity, lp + ep, 'Independent liabilities plus account equity; no balancing plug')
    _set(rows[46], month, year, sum(debts[r][0] for r in (42, 43, 44, 45)),
         [p for r in (42, 43, 44, 45) for p in debts[r][1]], 'Other payables6500+6600+6700+6900, same month')
    for row, account in ((32, '8300'), (33, '8500'), (34, '8700'), (50, '8300'), (51, '8500'), (52, '8700'), (53, '9900')):
        value, provenance = osv.amount((account,), credit=True)
        _set(rows[row], month, year, value, provenance, 'Signed closing credit minus debit;9900 is cumulative account result')
    retained, retained_provenance = osv.amount(('8700', '9900'), credit=True)
    _set(rows[54], month, year, retained, retained_provenance, 'Closing retained earnings plus cumulative current-year result')
    _set(rows[55], month, year, equity, ep, 'Independent signed account equity8300+8500+8700+9900')
    residual = assets - liabilities - equity
    _set(rows[38], month, year, residual, ap + lp + ep, 'Independent assets minus liabilities minus account equity; source boolean check is retained only as cache')
    _set(rows[56], month, year, residual, ap + lp + ep, 'Unresolved assets minus liabilities minus independent account equity')
    for row, label in ((8, 'Денежные счета (5000, 5100, 5200, 5500, 5700)'),
                       (13, 'Запасы, включая НЗП (1000, 2000, 2100, 2500, 2800, 2900)'),
                       (36, 'Капитал по счетам (8300, 8500, 8700, 9900)'),
                       (52, 'Нераспределённая прибыль прошлых лет (8700)'),
                       (38, 'Проверка баланса'), (56, 'Остаточное расхождение баланса')):
        if rows[row]:
            rows[row].setdefault('source_label', rows[row]['label'])
            rows[row]['fact_label'] = label
    def ratio(row, numerator, denominator, provenance, method, positive_flow=False, missing_reason='ratio_base_missing'):
        reason = missing_reason if numerator is None or denominator is None else 'zero_denominator' if denominator == ZERO else 'nonpositive_flow' if positive_flow and denominator < ZERO else None
        # Monetary sums remain exact; non-terminating ratios need a bounded
        # precision rather than thousands of repeating digits in the API.
        with localcontext() as ratio_context:
            ratio_context.prec = 34
            value = None if reason else numerator / denominator
        _set(rows[row], month, year, value, provenance, method, reason=reason)
    avg_assets, avg_equity = (opening_assets + assets) / 2, (opening_equity + equity) / 2
    days = Decimal(calendar.monthrange(year, month)[1])
    ratio(62, ni, revenue, np + rp, 'Posted result9900 / confirmed net revenue; revenue classification pending', missing_reason='actual_revenue_classification_required')
    ratio(63, ni, avg_assets, np + ap + oap, 'Monthly NI / average opening and closing mapped assets; not annualised')
    ratio(64, ni, avg_equity, np + ep + oep, 'Monthly NI / average opening and closing independent equity; not annualised')
    ratio(65, (opening[9][0] + closing[9][0]) / 2 * days, revenue,
          opening[9][1] + closing[9][1] + rp, 'Average opening/closing receivables / monthly net sales × actual calendar days', True, 'actual_revenue_classification_required')
    ratio(66, (opening[13][0] + closing[13][0]) / 2 * days, cogs,
          opening[13][1] + closing[13][1] + cp, 'Average opening/closing inventories / monthly COGS × actual calendar days', True)
    ratio(67, closing[9][0], revenue, closing[9][1] + rp, 'Closing receivables / monthly net sales', missing_reason='actual_revenue_classification_required')
    ratio(68, closing[8][0], revenue, closing[8][1] + rp, 'Closing monetary assets / monthly net sales; restrictions unconfirmed', missing_reason='actual_revenue_classification_required')
    accum_da, dp = osv.amount(('0200',), credit=True)
    gross_ppe, pp = osv.amount(('0100',))
    ratio(69, accum_da, gross_ppe, dp + pp, 'Accumulated closing depreciation / gross closing PPE')
    ratio(70, revenue, (opening[18][0] + closing[18][0]) / 2,
          rp + opening[18][1] + closing[18][1], 'Monthly net sales / average opening and closing net PPE', missing_reason='actual_revenue_classification_required')
    current_assets = assets - closing[18][0]
    current_liabilities = liabilities - debts[31][0]
    _set(rows[72], month, year, current_assets - current_liabilities, ap + lp, 'Current assets minus current liabilities;7800 excluded,6800 included')
    ratio(73, current_assets, current_liabilities, ap + lp, 'Current assets / current liabilities by source account classification')
    ratio(74, current_assets - closing[13][0], current_liabilities, ap + lp, '(Current assets−inventory) / current liabilities')
    ratio(75, equity, assets, ep + ap, 'Independent account equity / mapped total assets')
    ratio(76, liabilities, equity, lp + ep, 'All mapped liabilities / independent account equity')
    for row, source in ((77, 9), (78, 8), (79, 13), (80, 18), (81, 22)):
        value, provenance = closing[source] if source in closing else debts[source]
        _set(rows[row], month, year, value, provenance, 'Same-month mapped balance detail')
    all_accounts = tuple(osv.rows)
    mapping_dr, mapping_p = osv.amount(all_accounts)
    if residual != ZERO:
        issue('mapping_balance_residual', 'warning', 'ОСВ содержит остаточное расхождение', f'Mapping · {year}-{month:02}',
              'Расхождение активов, обязательств и независимого капитала: ' + _text(residual) + '; баланс не выравнивается автоматически.')
    return {'month': month, 'status': 'calculated', 'balance_residual': _text(residual),
            'mapping_residual': _text(mapping_dr), 'source_imbalance': residual != ZERO,
            'net_income_check': pnl_result['net_income_check'], 'pnl': pnl_result['metadata'],
            'cash_restrictions': 'unconfirmed', 'debt_maturity': 'source_6800_7800_classification',
            'account_register': 'complete_profile', 'optional_absent_accounts': sorted(BS_ACCOUNTS - set(osv.rows))}
