"""Derived review of a recognised grouped OSV; never edits the source or ledger.

This is a versioned presentation profile, not an IFRS certification. A complete
account register, report/account labels and expected row positions must agree
before blank sides are interpreted as zero. Relocated, unsupported or incomplete
templates retain their source view: UI cards and KPI methods use fixed row keys.
"""
from __future__ import annotations

import calendar
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
REQUIRED_FIELDS = [
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
        return result if result.is_finite() and abs(result) <= MAX_DISPLAY and len(result.as_tuple().digits) <= 1000 else None
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
    p = {7: _locate(pnl, 7, r'net sales|net revenue|выручка.*чист|чистая выручка|чистые продажи'),
         11: _locate(pnl, 11, r'cogs|себестоимость'),
         16: _locate(pnl, 16, r'\bebitda\b'), 17: _locate(pnl, 17, r'\bda\b|depreciation|амортизац'),
         18: _locate(pnl, 18, r'\bebit\b'), 23: _locate(pnl, 23, r'net profit|чистая прибыль')}
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
        if row['account'] not in BS_ACCOUNTS and any(v is not None and _number(v) != ZERO for v in values[:2] + values[4:]):
            return 'unclosed_profit_accounts'
    return None


def apply_mapping_reports(reports, records, year, coverage, sheets, issue):
    """Mutate only derived report cells; return a JSON-safe provenance summary."""
    metadata = {'status': 'source_only', 'profile': None, 'source_preserved': True,
                'plan_basis': 'immutable_source_cache', 'plan_classification_status': 'pending_vat_marketing_confirmation',
                'required_fields': REQUIRED_FIELDS, 'months': [], 'ratio_period_basis': 'monthly_unannualized'}
    profile = _profile(reports, sheets)
    if profile is None or not isinstance(year, int) or not 2000 <= year <= 2100:
        metadata['reason'] = 'profile_unrecognized'
        return metadata
    balance_rows, pnl_rows = profile
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


def _calculate_month(rows, pnl, osv, issue):
    year, month = osv.year, osv.month
    closing, debts, assets, liabilities, equity, ep = osv.totals()
    opening, opening_debts, opening_assets, _, opening_equity, oep = osv.totals(True)
    ap = _provenance([p for _, ps in closing.values() for p in ps])
    oap = _provenance([p for _, ps in opening.values() for p in ps])
    lp = _provenance([p for _, ps in debts.values() for p in ps])
    ni, revenue, cogs = _read(pnl[23], month), _read(pnl[7], month), _read(pnl[11], month)
    ni_source = (_cell(pnl[23], month) or {}).get('fact', {})
    rp = [{'source': (_cell(pnl[7], month) or {}).get('fact', {}).get('source'), 'value': _text(revenue), 'role': 'source_net_sales'}]
    np = [{'source': ni_source.get('source'), 'value': _text(ni), 'role': 'preserved_source_net_income'}]
    cp = [{'source': (_cell(pnl[11], month) or {}).get('fact', {}).get('source'), 'value': _text(cogs), 'role': 'source_cogs'}]
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
    result_close, result_p = osv.amount(('9900',), credit=True)
    result_open, result_op = osv.amount(('9900',), True, True)
    difference = ni - (result_close - result_open) if ni is not None else None
    ni_status = 'matches_rounding' if difference is not None and abs(difference) <= Decimal(1) else 'unavailable' if difference is None else 'mismatch'
    if difference is not None and ni_status == 'mismatch':
        issue('net_income_account_mismatch', 'warning', 'Чистая прибыль требует сверки с9900', f'Mapping · {year}-{month:02} ·9900',
              'Сохранённая чистая прибыль не изменена; разница с движением сальдо9900: ' + _text(difference))
    if _read(pnl[16], month) == _read(pnl[18], month) and (_read(pnl.get(17), month) != ZERO):
        _set(pnl[16], month, year, None, result_p + result_op, 'EBITDA needs the depreciation already included in COGS/OPEX', reason='depreciation_allocation_required')
        issue('ebitda_allocation_required', 'warning', 'Для EBITDA нужно распределение амортизации', '09_Statements · EBITDA',
              'Исходный EBITDA совпадает с EBIT. Чистая прибыль сохранена; амортизация не вычитается повторно.')
    def ratio(row, numerator, denominator, provenance, method, positive_flow=False):
        reason = 'ratio_base_missing' if numerator is None or denominator is None else 'zero_denominator' if denominator == ZERO else 'nonpositive_flow' if positive_flow and denominator < ZERO else None
        # Monetary sums remain exact; non-terminating ratios need a bounded
        # precision rather than thousands of repeating digits in the API.
        with localcontext() as ratio_context:
            ratio_context.prec = 34
            value = None if reason else numerator / denominator
        _set(rows[row], month, year, value, provenance, method, reason=reason)
    avg_assets, avg_equity = (opening_assets + assets) / 2, (opening_equity + equity) / 2
    days = Decimal(calendar.monthrange(year, month)[1])
    ratio(62, ni, revenue, np + rp, 'Source monthly net income / source monthly net sales')
    ratio(63, ni, avg_assets, np + ap + oap, 'Monthly NI / average opening and closing mapped assets; not annualised')
    ratio(64, ni, avg_equity, np + ep + oep, 'Monthly NI / average opening and closing independent equity; not annualised')
    ratio(65, (opening[9][0] + closing[9][0]) / 2 * days, revenue,
          opening[9][1] + closing[9][1] + rp, 'Average opening/closing receivables / monthly net sales × actual calendar days', True)
    ratio(66, (opening[13][0] + closing[13][0]) / 2 * days, cogs,
          opening[13][1] + closing[13][1] + cp, 'Average opening/closing inventories / monthly COGS × actual calendar days', True)
    ratio(67, closing[9][0], revenue, closing[9][1] + rp, 'Closing receivables / monthly net sales')
    ratio(68, closing[8][0], revenue, closing[8][1] + rp, 'Closing monetary assets / monthly net sales; restrictions unconfirmed')
    accum_da, dp = osv.amount(('0200',), credit=True)
    gross_ppe, pp = osv.amount(('0100',))
    ratio(69, accum_da, gross_ppe, dp + pp, 'Accumulated closing depreciation / gross closing PPE')
    ratio(70, revenue, (opening[18][0] + closing[18][0]) / 2,
          rp + opening[18][1] + closing[18][1], 'Monthly net sales / average opening and closing net PPE')
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
            'net_income_check': {'status': ni_status, 'difference': _text(difference), 'rounding_tolerance': '1 source unit', 'provenance': result_p + result_op},
            'cash_restrictions': 'unconfirmed', 'debt_maturity': 'source_6800_7800_classification',
            'account_register': 'complete_profile', 'optional_absent_accounts': sorted(BS_ACCOUNTS - set(osv.rows))}
