"""Read-only bridges between calculated Mapping balances and profit.

These bridges retain source units and account provenance. A change in cash
balances is not a classified cash-flow statement, and a change in equity other
than profit is not automatically an owner distribution. Source caches, plans,
and the operational cash ledger are deliberately not calculation fallbacks.
"""
from __future__ import annotations

import calendar
import copy
from datetime import date
from decimal import Decimal, DecimalException, localcontext


PROFILE = 'uzbek_grouped_chart_finmodel_v1'
MAX_DISPLAY = Decimal('1.7976931348623157e308')
ZERO = Decimal(0)


def _number(value):
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    if len(str(value)) > 4096:
        return None
    try:
        result = Decimal(str(value))
        if (not result.is_finite() or result.copy_abs() > MAX_DISPLAY
                or len(result.as_tuple().digits) > 4096
                or result.as_tuple().exponent < -4096):
            return None
        return result
    except (DecimalException, ValueError):
        return None


def _text(value):
    return format(value, 'f') if value is not None else None


def _provenance(item, period):
    return [{**copy.deepcopy(p), 'period': period} for p in item.get('provenance', [])
            if isinstance(p, dict)]


def _read(item, period):
    if not isinstance(item, dict) or item.get('status') != 'calculated' or item.get('period') != period:
        return None
    provenance = item.get('provenance')
    if not isinstance(provenance, list) or not provenance:
        return None
    if any(not isinstance(p, dict) or not str(p.get('source', '')).startswith('Mapping') for p in provenance):
        return None
    return _number(item.get('value'))


def _facts(review, report, row_number, months, year):
    rows = review.get('reports', {}).get(report, {}).get('rows', [])
    matches = [r for r in rows if r.get('row') == row_number]
    if len(matches) != 1:
        return None
    pairs = matches[0].get('cells', [])
    facts = []
    for month in months:
        cells = [c for c in pairs if c.get('month') == month]
        if len(cells) != 1:
            return None
        fact = cells[0].get('fact', {})
        period = f'{year}-{month:02}'
        value = _read(fact, period)
        if value is None:
            return None
        facts.append((month, fact, value))
    return facts


def _continuity(facts, year):
    differences = []
    for previous, current in zip(facts, facts[1:]):
        opening = current[1]['opening_balance']
        value = _read(opening, f'{year}-{current[0]:02}') - previous[2]
        if value != ZERO:
            differences.append({
                'previous_period': f'{year}-{previous[0]:02}', 'period': f'{year}-{current[0]:02}',
                'difference': _text(value),
                'provenance': _provenance(previous[1], f'{year}-{previous[0]:02}')
                              + _provenance(opening, f'{year}-{current[0]:02}'),
            })
    return {'status': 'mismatch' if differences else 'matches', 'differences': differences}


def _balance_data(review, row, months, year):
    facts = _facts(review, 'balance', row, months, year)
    if facts is None:
        return None
    # Every monthly opening is required, including intermediate months: an
    # endpoint alone must not hide unsupported or disconnected monthly data.
    if any(_read(fact.get('opening_balance'), f'{year}-{month:02}') is None
           for month, fact, _ in facts):
        return None
    opening = facts[0][1]['opening_balance']
    return {'opening': _read(opening, f'{year}-{months[0]:02}'), 'closing': facts[-1][2],
            'opening_provenance': _provenance(opening, f'{year}-{months[0]:02}'),
            'closing_provenance': _provenance(facts[-1][1], f'{year}-{months[-1]:02}'),
            'continuity': _continuity(facts, year)}


def _income_rounding(facts, year):
    """Explain the result-account rounding already present in NI provenance."""
    months, provenance = [], []
    for month, fact, income in facts:
        period = f'{year}-{month:02}'
        measures = {}
        refs = [p for p in _provenance(fact, period) if p.get('account') == '9900']
        for measure in ('opening_debit', 'opening_credit', 'closing_debit', 'closing_credit'):
            candidates = [p for p in refs if p.get('measure') == measure]
            if len(candidates) != 1:
                return {'status': 'unavailable', 'difference': None}
            item = candidates[0]
            number = _number(item.get('value'))
            if number is None and item.get('value') is None and item.get('blank_side_zero') is True:
                number = ZERO
            if number is None:
                return {'status': 'unavailable', 'difference': None}
            measures[measure] = number
        movement = (measures['closing_credit'] - measures['closing_debit']
                    - measures['opening_credit'] + measures['opening_debit'])
        difference = movement - income
        months.append({'period': period, 'difference': _text(difference)})
        provenance.extend(refs)
    total = sum((Decimal(m['difference']) for m in months), ZERO)
    return {'title': 'Разница движения сальдо 9900 и результата периода',
            'status': 'available', 'difference': _text(total), 'months': months,
            'provenance': provenance,
            'methodology': 'Изменение сальдо 9900 минус рассчитанный результат периода; разница уже включена в прочее изменение капитала и отдельно не вычитается.'}


def _blank_bridges(status, reason):
    return {
        'profit_to_equity': {
            'title': 'Финансовый результат и изменение капитала', 'status': status, 'reason': reason,
            'net_income': None, 'opening_equity': None, 'closing_equity': None,
            'equity_change': None, 'other_equity_change': None, 'provenance': {},
            'other_change_classification': 'unclassified',
        },
        'cash_movement': {
            'title': 'Изменение денежных остатков по ОСВ', 'status': status, 'reason': reason,
            'opening_cash': None, 'net_change': None, 'closing_cash': None, 'provenance': {},
            'classification_status': 'unclassified',
        },
    }


def build_reconciliation(review, year, start_month, end_month):
    """Return source-unit bridges for a complete, closed monthly interval.

    The caller authorizes the company and source before passing its review.
    This helper neither reads another source nor persists financial data.
    """
    review = review if isinstance(review, dict) else {}
    source = {key: review.get(key) for key in
              ('review_version', 'reviewed_as_of', 'currency', 'unit', 'unit_status')}
    result = {
        'year': year, 'start_month': start_month, 'end_month': end_month,
        'source': source, 'status': 'unavailable', 'monetary_basis': 'source_units',
        'source_preserved': True, 'ledger_comparison_status': 'not_comparable',
        'coverage': [], 'bridges': _blank_bridges('unavailable', 'invalid_period'),
        'required_fields': [{
            'key': 'cash_scope', 'title': 'Состав денежных счетов для сопоставления',
            'status': 'needs_clarification',
            'reason': 'Подтвердите состав 5000/5100/5200/5500/5700, валютные оценки и ограничения спецсчетов. Счета ОСВ пока не сопоставлены со счетами денежных операций сайта.',
        }],
        'notes': [
            'Суммы приведены в исходных единицах Mapping. Масштаб отображения не меняет валюту или основание расчёта.',
            'Прочее изменение капитала не классифицировано как дивиденды или взносы собственников и может включать разницу округления результата 9900.',
            'Изменение денежных остатков не разделено на операционную, инвестиционную и финансовую деятельность; сравнение с Ledger не выполнено.',
        ],
    }
    if review.get('unit_status') not in ('declared', 'confirmed'):
        result['required_fields'].append({
            'key': 'amount_scale', 'title': 'Масштаб сумм Mapping', 'status': 'needs_clarification',
            'reason': 'Нужно подтвердить, записаны суммы в полных денежных единицах, тысячах или миллионах. Указание UZS само по себе не подтверждает коэффициент.',
        })
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (year, start_month, end_month)):
        return result
    if not 2000 <= year <= 2100 or not 1 <= start_month <= end_month <= 12:
        return result
    result['period'] = {'start': f'{year}-{start_month:02}', 'end': f'{year}-{end_month:02}'}
    calculation = review.get('calculation', {})
    if review.get('year') != year:
        result['bridges'] = _blank_bridges('unavailable', 'source_year_mismatch')
        return result
    if calculation.get('profile') != PROFILE or calculation.get('status') != 'derived_provisional':
        result['bridges'] = _blank_bridges('unavailable', calculation.get('reason') or 'calculation_unavailable')
        return result
    try:
        as_of = date.fromisoformat(review.get('reviewed_as_of', ''))
    except (TypeError, ValueError):
        result['bridges'] = _blank_bridges('unavailable', 'source_cutoff_missing')
        return result
    months = list(range(start_month, end_month + 1))
    coverage = review.get('coverage', [])
    for month in months:
        period = f'{year}-{month:02}'
        candidates = [c for c in coverage if c.get('month') == month and c.get('period') == period]
        covered = candidates[0] if len(candidates) == 1 else {}
        closed = date(year, month, calendar.monthrange(year, month)[1]) <= as_of
        ready = covered.get('status') == 'available' and covered.get('calculation_eligible') is True and closed
        reason = None if ready else 'month_not_closed' if not closed else 'mapping_month_missing'
        result['coverage'].append({'month': month, 'period': period,
                                   'status': 'available' if ready else covered.get('status', 'missing'),
                                   'ready': ready, 'reason': reason})
    if any(not c['ready'] for c in result['coverage']):
        result['status'] = 'missing'
        result['bridges'] = _blank_bridges('missing', 'incomplete_period')
        return result
    result['bridges'] = _blank_bridges('unavailable', 'calculated_mapping_cells_required')
    with localcontext() as context:
        # Bounded decimal inputs retain all fractional digits and integers above
        # JavaScript's safe range. No cents conversion or binary float summation.
        context.prec = 8192
        cash = _balance_data(review, 8, months, year)
        equity = _balance_data(review, 36, months, year)
        income = _facts(review, 'pnl', 23, months, year)
        if cash is not None:
            delta = cash['closing'] - cash['opening']
            if _number(delta) is not None:
                result['bridges']['cash_movement'].update(
                    status='ready', reason=None, opening_cash=_text(cash['opening']),
                    net_change=_text(delta), closing_cash=_text(cash['closing']),
                    continuity=cash['continuity'],
                    methodology='Конечное сальдо денежных счетов минус начальное сальдо выбранного периода; это изменение остатков, а не классифицированные денежные потоки.',
                    provenance={'opening_cash': cash['opening_provenance'], 'closing_cash': cash['closing_provenance'],
                                'net_change': cash['opening_provenance'] + cash['closing_provenance']},
                )
        if equity is not None and income is not None:
            net_income = sum((value for _, _, value in income), ZERO)
            delta = equity['closing'] - equity['opening']
            other = delta - net_income
            if all(_number(v) is not None for v in (net_income, delta, other)):
                income_provenance = [p for month, fact, _ in income
                                     for p in _provenance(fact, f'{year}-{month:02}')]
                equity_provenance = equity['opening_provenance'] + equity['closing_provenance']
                result['bridges']['profit_to_equity'].update(
                    status='ready', reason=None, net_income=_text(net_income),
                    opening_equity=_text(equity['opening']), closing_equity=_text(equity['closing']),
                    equity_change=_text(delta), other_equity_change=_text(other),
                    continuity=equity['continuity'],
                    rounding_check=_income_rounding(income, year),
                    methodology='Капитал на конец = капитал на начало + сумма рассчитанной месячной прибыли + прочее изменение капитала. Прочее изменение требует отдельной классификации и может включать разрывы начальных сальдо.',
                    provenance={'net_income': income_provenance,
                                'opening_equity': equity['opening_provenance'], 'closing_equity': equity['closing_provenance'],
                                'equity_change': equity_provenance,
                                'other_equity_change': equity_provenance + income_provenance},
                )
    statuses = [b['status'] for b in result['bridges'].values()]
    result['status'] = 'ready' if all(s == 'ready' for s in statuses) else 'unavailable'
    return result
