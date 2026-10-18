"""Source-unit bridges; synthetic inputs, no databases or private workbooks."""
import copy
import unittest

from app.financial_reconciliation import PROFILE, build_reconciliation


def fixture(months=(1, 2), year=2026):
    def value(amount, period, account, side='closing_credit'):
        return {'status': 'calculated', 'value': str(amount), 'period': period,
                'source_cache': '987654321',
                'provenance': [{'source': f'Mapping!K{int(period[-2:]) + 2}',
                                'account': account, 'measure': side, 'value': str(amount)}]}

    cash, equity, income = [], [], []
    for month in months:
        period = f'{year}-{month:02}'
        cash_fact = value(100 + month * 5, period, '5100', 'closing_debit')
        cash_fact['opening_balance'] = value(100 + (month - 1) * 5, period, '5100', 'opening_debit')
        equity_fact = value(1000 + month * 20, period, '8300')
        equity_fact['opening_balance'] = value(1000 + (month - 1) * 20, period, '8300', 'opening_credit')
        cash.append({'month': month, 'fact': cash_fact})
        equity.append({'month': month, 'fact': equity_fact})
        income.append({'month': month, 'fact': value(10, period, '9900', 'turnover_credit')})
    return {
        'year': year, 'currency': 'UZS', 'unit': 'Масштаб сумм не указан', 'unit_status': 'unknown',
        'reviewed_as_of': f'{year}-12-31', 'review_version': 'synthetic',
        'calculation': {'profile': PROFILE, 'status': 'derived_provisional'},
        'coverage': [{'month': month, 'period': f'{year}-{month:02}', 'status': 'available',
                      'calculation_eligible': True} for month in months],
        'reports': {'balance': {'rows': [{'row': 8, 'cells': cash}, {'row': 36, 'cells': equity}]},
                    'pnl': {'rows': [{'row': 23, 'cells': income}]}},
    }


def fact(review, row, month=1, report='balance'):
    r = next(r for r in review['reports'][report]['rows'] if r['row'] == row)
    return next(c['fact'] for c in r['cells'] if c['month'] == month)


class FinancialReconciliationTests(unittest.TestCase):
    def test_profit_other_capital_and_cash_are_separate_with_provenance(self):
        review = fixture()
        before = copy.deepcopy(review)
        result = build_reconciliation(review, 2026, 1, 2)
        self.assertEqual(result['status'], 'ready')
        capital = result['bridges']['profit_to_equity']
        self.assertEqual((capital['net_income'], capital['equity_change'], capital['other_equity_change']), ('20', '40', '20'))
        self.assertEqual(capital['other_change_classification'], 'unclassified')
        self.assertEqual({p['period'] for p in capital['provenance']['net_income']}, {'2026-01', '2026-02'})
        cash = result['bridges']['cash_movement']
        self.assertEqual((cash['opening_cash'], cash['net_change'], cash['closing_cash']), ('100', '10', '110'))
        self.assertEqual(cash['classification_status'], 'unclassified')
        self.assertEqual(result['ledger_comparison_status'], 'not_comparable')
        self.assertEqual(review, before)
        capital['provenance']['net_income'][0]['value'] = 'changed'
        self.assertEqual(review, before)

    def test_zero_is_valid_but_cached_and_source_cache_are_not_fallbacks(self):
        review = fixture((1,))
        fact(review, 23, report='pnl')['value'] = '0'
        result = build_reconciliation(review, 2026, 1, 1)
        self.assertEqual(result['bridges']['profit_to_equity']['net_income'], '0')
        fact(review, 23, report='pnl').update(status='cached', value='400')
        result = build_reconciliation(review, 2026, 1, 1)
        self.assertEqual(result['bridges']['profit_to_equity']['status'], 'unavailable')
        self.assertIsNone(result['bridges']['profit_to_equity']['net_income'])
        self.assertEqual(result['bridges']['cash_movement']['status'], 'ready')
        fact(review, 8).update(status='unavailable', value=None)
        self.assertIsNone(build_reconciliation(review, 2026, 1, 1)['bridges']['cash_movement']['net_change'])

    def test_missing_interior_month_invalidates_both_bridges(self):
        review = fixture((1, 3))
        result = build_reconciliation(review, 2026, 1, 3)
        self.assertEqual(result['status'], 'missing')
        self.assertEqual([c['month'] for c in result['coverage'] if not c['ready']], [2])
        self.assertIsNone(result['bridges']['profit_to_equity']['net_income'])
        self.assertIsNone(result['bridges']['cash_movement']['closing_cash'])

    def test_no_previous_month_fallback_for_missing_end_month(self):
        review = fixture((7,))
        review['coverage'].append({'month': 8, 'period': '2026-08', 'status': 'missing', 'calculation_eligible': False})
        result = build_reconciliation(review, 2026, 7, 8)
        self.assertEqual(result['status'], 'missing')
        self.assertIsNone(result['bridges']['cash_movement']['closing_cash'])

    def test_current_partial_month_and_future_month_are_not_closed_months(self):
        review = fixture((9, 10, 11))
        review['reviewed_as_of'] = '2026-10-11'
        self.assertEqual(build_reconciliation(review, 2026, 9, 9)['status'], 'ready')
        for month in (10, 11):
            result = build_reconciliation(review, 2026, month, month)
            self.assertEqual(result['status'], 'missing')
            self.assertEqual(result['coverage'][0]['reason'], 'month_not_closed')

    def test_year_metadata_and_period_provenance_must_agree(self):
        review = fixture((1,))
        self.assertEqual(build_reconciliation(review, 2025, 1, 1)['bridges']['cash_movement']['reason'], 'source_year_mismatch')
        fact(review, 8)['period'] = '2025-01'
        self.assertEqual(build_reconciliation(review, 2026, 1, 1)['bridges']['cash_movement']['status'], 'unavailable')
        review = fixture((1,))
        review['coverage'][0]['period'] = '2025-01'
        self.assertEqual(build_reconciliation(review, 2026, 1, 1)['status'], 'missing')

    def test_unsupported_profile_missing_cutoff_and_nonmapping_input(self):
        review = fixture((1,))
        review['calculation']['profile'] = 'unrecognized'
        self.assertEqual(build_reconciliation(review, 2026, 1, 1)['status'], 'unavailable')
        review = fixture((1,))
        review.pop('reviewed_as_of')
        self.assertEqual(build_reconciliation(review, 2026, 1, 1)['bridges']['cash_movement']['reason'], 'source_cutoff_missing')
        review = fixture((1,))
        fact(review, 23, report='pnl')['provenance'][0]['source'] = '09_Statements!F23'
        self.assertIsNone(build_reconciliation(review, 2026, 1, 1)['bridges']['profit_to_equity']['net_income'])

    def test_missing_intermediate_opening_and_duplicate_month_are_not_silently_used(self):
        review = fixture()
        fact(review, 8, 2).pop('opening_balance')
        self.assertEqual(build_reconciliation(review, 2026, 1, 2)['bridges']['cash_movement']['status'], 'unavailable')
        review = fixture()
        review['coverage'].append(copy.deepcopy(review['coverage'][0]))
        self.assertEqual(build_reconciliation(review, 2026, 1, 2)['status'], 'missing')

    def test_discontinuous_openings_are_exposed_without_inventing_cash_activity(self):
        review = fixture()
        fact(review, 8, 2)['opening_balance']['value'] = '120'
        fact(review, 36, 2)['opening_balance']['value'] = '1027'
        result = build_reconciliation(review, 2026, 1, 2)
        cash = result['bridges']['cash_movement']
        self.assertEqual(cash['net_change'], '10')
        self.assertEqual(cash['continuity']['status'], 'mismatch')
        self.assertEqual(cash['continuity']['differences'][0]['difference'], '15')
        self.assertEqual(result['bridges']['profit_to_equity']['continuity']['differences'][0]['difference'], '7')

    def test_fractional_and_large_source_amounts_remain_exact_and_unscaled(self):
        review = fixture((1,))
        cash = fact(review, 8)
        cash['opening_balance']['value'] = '9007199254740993.123456'
        cash['value'] = '9007199254740993.123457'
        equity = fact(review, 36)
        equity['opening_balance']['value'] = '9007199254740993.123456'
        equity['value'] = '9007199254740993.423456'
        fact(review, 23, report='pnl')['value'] = '0.1'
        result = build_reconciliation(review, 2026, 1, 1)
        self.assertEqual(result['bridges']['cash_movement']['net_change'], '0.000001')
        self.assertEqual(result['bridges']['profit_to_equity']['other_equity_change'], '0.200000')
        self.assertEqual(result['monetary_basis'], 'source_units')
        self.assertEqual({f['key'] for f in result['required_fields']}, {'cash_scope', 'amount_scale'})
        review['unit_status'] = 'declared'
        self.assertEqual({f['key'] for f in build_reconciliation(review, 2026, 1, 1)['required_fields']}, {'cash_scope'})

    def test_result_rounding_is_disclosed_but_not_subtracted_twice(self):
        review = fixture((1,))
        income = fact(review, 23, report='pnl')
        income['provenance'].extend([
            {'source': 'Mapping!F43', 'account': '9900', 'measure': 'opening_debit', 'value': None, 'blank_side_zero': True},
            {'source': 'Mapping!G43', 'account': '9900', 'measure': 'opening_credit', 'value': '0'},
            {'source': 'Mapping!J43', 'account': '9900', 'measure': 'closing_debit', 'value': '0'},
            {'source': 'Mapping!K43', 'account': '9900', 'measure': 'closing_credit', 'value': '11'},
        ])
        capital = build_reconciliation(review, 2026, 1, 1)['bridges']['profit_to_equity']
        self.assertEqual(capital['other_equity_change'], '10')
        self.assertEqual(capital['rounding_check']['difference'], '1')
        self.assertEqual(capital['rounding_check']['months'], [{'period': '2026-01', 'difference': '1'}])
        income['provenance'][-1]['value'] = None
        income['provenance'][-1].pop('blank_side_zero', None)
        self.assertEqual(build_reconciliation(review, 2026, 1, 1)['bridges']['profit_to_equity']['rounding_check']['status'], 'unavailable')

    def test_invalid_values_are_not_zero_and_overflow_is_unavailable(self):
        for invalid in (None, True, 'NaN', 'Infinity', 'not an amount'):
            with self.subTest(value=invalid):
                review = fixture((1,))
                fact(review, 23, report='pnl')['value'] = invalid
                self.assertIsNone(build_reconciliation(review, 2026, 1, 1)['bridges']['profit_to_equity']['net_income'])
        review = fixture((1, 2))
        for month in (1, 2):
            fact(review, 23, month, 'pnl')['value'] = '1e308'
        self.assertIsNone(build_reconciliation(review, 2026, 1, 2)['bridges']['profit_to_equity']['net_income'])

    def test_invalid_ranges_do_not_select_a_different_period(self):
        for year, start, end in ((2026, 3, 1), (2026, 0, 1), (2026, 1, 13), (True, 1, 1), (2026, '1', 1)):
            with self.subTest(period=(year, start, end)):
                result = build_reconciliation(fixture(), year, start, end)
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(result['bridges']['cash_movement']['reason'], 'invalid_period')


if __name__ == '__main__':
    unittest.main()
