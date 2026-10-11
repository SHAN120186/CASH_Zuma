"""Synthetic grouped OSV contracts; no private company data or formula evaluation."""
import copy
import unittest
from decimal import Decimal

from app.financial_statement_calculations import (
    ASSET_GROUPS, BS_ACCOUNTS, CHART_REGISTRATION, CORE_NAMES,
    LABELS, apply_mapping_reports,
)

BALANCE_LABELS = {
    8: 'Cash', 9: 'Accounts receivable', 10: 'Other receivables',
    11: 'Tax advances', 12: 'Social advances', 13: 'Inventory total',
    14: 'Prepaid expenses', 15: 'Advances to suppliers', 16: 'Advances to employees',
    17: 'Deposits', 18: 'Net PPE', 19: 'Total Assets', 22: 'Accounts payable',
    23: 'Customer advances', 24: 'Deferred revenue', 25: 'Taxes payable', 26: 'Debt',
    30: 'Loans Short-term6800', 31: 'Loans Long-term7800',
    32: 'Share capital8300', 33: 'Reserve capital8500', 34: 'Retained earnings8700',
    35: 'Total Liabilities', 36: 'Equity (balancing)', 37: 'Total L+E',
    38: 'BalanceCheck', 42: 'Social insurance payable', 43: 'Due to founders',
    44: 'Payroll payable', 45: 'Other creditors', 46: 'Total other payables',
    50: 'Share capital', 51: 'Reserve capital', 52: 'Retained earnings opening',
    53: 'Current year net income', 54: 'Retained earnings (ending)',
    55: 'Equity (from accounts)', 56: 'Equity plug', 62: 'Net Margin',
    63: 'ROA', 64: 'ROE', 65: 'DSO', 66: 'Inventory Days',
    67: 'ДЗ / Чистые продажи', 68: 'ДС / Чистые продажи', 69: 'Амортизация / ОС',
    70: 'Оборачиваемость ОС', 72: 'Working capital', 73: 'Current ratio',
    74: 'Quick ratio', 75: 'Equity ratio', 76: 'Debt/Equity',
    77: 'Дебиторская задолженность', 78: 'Денежные средства', 79: 'Запасы',
    80: 'Основные средства', 81: 'Кредиторская задолженность',
}
NAMES = {'0100': 'Fixed assets', '0200': 'Depreciation', '6000': 'Suppliers',
         '6800': 'Short-term loans', '7800': 'Long-term loans', '8300': 'Share capital',
         '8500': 'Reserve capital', '8700': 'Retained earnings', '9900': 'Financial result'}


def fixture(months=(1,), year=2026):
    def row(n, label, sheet):
        return {'row': n, 'key': str(n), 'label': label, 'kind': 'amount', 'measure': 'amount',
                'cells': [{'month': m,
                           'plan': {'value': '9', 'status': 'cached', 'source': f'{sheet}!PLAN{m}_{n}', 'formula': '=original_plan'},
                           'fact': {'value': '999', 'status': 'cached', 'source': f'{sheet}!FACT{m}_{n}', 'formula': '=wrong_source'}} for m in range(1, 13)]}
    balance = {'rows': [row(n, label, 'Balance') for n, label in BALANCE_LABELS.items()]}
    pnl = {'rows': [row(n, label, 'PNL') for n, label in {7: 'Net Sales', 11: 'COGS', 16: 'EBITDA', 17: 'Depreciation', 18: 'EBIT', 23: 'Net Profit'}.items()]}
    for n, value in {7: '100', 11: '40', 16: '30', 17: '8', 18: '30', 23: '10'}.items():
        for item in next(r for r in pnl['rows'] if r['row'] == n)['cells']:
            item['fact']['value'] = value
    records = {}
    for m in months:
        rs = []
        for offset, code in enumerate(sorted(BS_ACCOUNTS)):
            rs.append({'account': code, 'account_name': NAMES.get(code, 'Account ' + code),
                       'row': 2 + offset, 'measures': [Decimal(0)] * 6,
                       'has_numeric': True, 'blank_amounts': False, 'invalid_amounts': []})
        records[(year, m)] = rs
        for code, before, after, credit in [
            ('0100', 1000, 1000, False), ('0200', 200, 220, True),
            ('5100', 100, 160, False), ('5500', 5, 7, False), ('4000', 80, 120, False),
            ('1000', 50, 70, False), ('2000', 20, 30, False), ('2500', 3, 4, False),
            ('6000', 100, 110, True), ('6500', 10, 15, True), ('6800', 20, 25, True),
            ('7800', 300, 280, True), ('8300', 500, 500, True), ('8500', 10, 10, True),
            ('8700', -50, -50, True), ('9900', 0, 10, True),
        ]:
            set_balance(records, year, m, code, before, after, credit)
    sheets = {'20_Fact_Input | Fakt': {'A' + str(n): {'value': code} for n, code in CHART_REGISTRATION.items()}}
    coverage = [{'month': m, 'status': 'available' if m in months else 'missing',
                 'calculation_eligible': m in months} for m in range(1, 13)]
    return {'pnl': pnl, 'balance': balance}, records, coverage, sheets


def set_balance(records, year, month, account, before, after, credit=False):
    row = next(r for r in records[(year, month)] if r['account'] == account)
    opening, closing = (1, 5) if credit else (0, 4)
    row['measures'][opening] = Decimal(str(before))
    row['measures'][closing] = Decimal(str(after))


def cell(reports, n, month=1, report='balance', side='fact'):
    row = next(r for r in reports[report]['rows'] if r['row'] == n)
    return next(c for c in row['cells'] if c['month'] == month)[side]


def calculate(reports, records, coverage, sheets, year=2026):
    issues = []
    metadata = apply_mapping_reports(reports, records, year, coverage, sheets,
                                     lambda *args: issues.append(args))
    return metadata, issues


class MappingCalculationTests(unittest.TestCase):
    def test_corrected_balance_independent_capital_provenance_and_source_preserved(self):
        reports, records, coverage, sheets = fixture()
        before = copy.deepcopy(reports)
        metadata, _ = calculate(reports, records, coverage, sheets)
        self.assertEqual(metadata['status'], 'derived_provisional')
        self.assertEqual(cell(reports, 8)['value'], '167')  # includes special bank accounts
        self.assertEqual(cell(reports, 13)['value'], '104')  # includes WIP2000 and overhead2500
        self.assertEqual(cell(reports, 35)['value'], '430')  #6800/7800 included,6500 once
        self.assertEqual(cell(reports, 26)['value'], '305')
        self.assertEqual(cell(reports, 36)['value'], '470')  #negative retained loss stays signed
        self.assertEqual(cell(reports, 53)['value'], '10')
        self.assertEqual(cell(reports, 55)['value'], '470')
        self.assertEqual(cell(reports, 38)['value'], '271')
        self.assertEqual(cell(reports, 56)['value'], '271')
        self.assertEqual(metadata['months'][0]['balance_residual'], metadata['months'][0]['mapping_residual'])
        self.assertEqual(cell(reports, 19)['source_cache'], '999')
        self.assertEqual(cell(reports, 19)['source_formula'], '=wrong_source')
        self.assertEqual(cell(reports, 19)['source_status'], 'cached')
        self.assertTrue(any(p['account'] == '2000' and p['source'].startswith('Mapping!J') for p in cell(reports, 13)['provenance']))
        for key in ('pnl', 'balance'):
            for old, new in zip(before[key]['rows'], reports[key]['rows']):
                self.assertEqual([c['plan'] for c in old['cells']], [c['plan'] for c in new['cells']])
        self.assertEqual(cell(reports, 23, report='pnl'), cell(before, 23, report='pnl'))

    def test_signed_net_balances_are_not_abs_or_one_sided(self):
        reports, records, coverage, sheets = fixture()
        r = next(r for r in records[(2026, 1)] if r['account'] == '4000')
        r['measures'][4:6] = [Decimal('30.25'), Decimal('40.75')]
        calculate(reports, records, coverage, sheets)
        self.assertEqual(cell(reports, 9)['value'], '-10.50')

    def test_openings_averages_actual_days_and_equity_not_revenue(self):
        reports, records, coverage, sheets = fixture()
        calculate(reports, records, coverage, sheets)
        self.assertEqual(cell(reports, 19)['opening_balance']['value'], '1058')
        self.assertEqual(cell(reports, 36)['opening_balance']['value'], '460')
        self.assertEqual(cell(reports, 9)['opening_balance']['value'], '80')
        expected_roa = Decimal(10) / ((Decimal(1058) + 1171) / 2)
        self.assertLess(abs(Decimal(cell(reports, 63)['value']) - expected_roa), Decimal('1e-28'))
        self.assertAlmostEqual(float(cell(reports, 64)['value']), 10 / 465)
        self.assertEqual(cell(reports, 65)['value'], '31')  #average AR100 / sales100 ×31days
        self.assertEqual(cell(reports, 65)['calendar_days'], 31)
        self.assertEqual(cell(reports, 69)['value'], '0.22')
        self.assertIsNone(cell(reports, 16, report='pnl')['value'])
        self.assertEqual(cell(reports, 16, report='pnl')['status'], 'requires_allocation')
        self.assertEqual(cell(reports, 16, report='pnl')['source_cache'], '30')

    def test_leap_month_same_period_and_no_previous_month_fallback(self):
        reports, records, coverage, sheets = fixture((2,), 2028)
        set_balance(records, 2028, 2, '3100', 11, 13)
        calculate(reports, records, coverage, sheets, 2028)
        self.assertEqual(cell(reports, 14, 2)['value'], '13')
        self.assertEqual(cell(reports, 65, 2)['calendar_days'], 29)
        self.assertEqual(cell(reports, 65, 2)['value'], '29')
        self.assertEqual(cell(reports, 19, 1)['value'], '999')  #source fixture stays unchanged when uncovered
        self.assertEqual(cell(reports, 19, 2)['opening_balance']['period'], '2028-02')

    def test_partial_duplicate_invalid_and_unknown_chart_do_not_fabricate(self):
        for mode in ('partial', 'duplicate', 'invalid', 'name', 'unknown', 'header'):
            with self.subTest(mode=mode):
                reports, records, coverage, sheets = fixture()
                if mode == 'partial':
                    records[(2026, 1)] = records[(2026, 1)][:1]
                elif mode == 'duplicate':
                    records[(2026, 1)].append(copy.deepcopy(records[(2026, 1)][0]))
                elif mode == 'invalid':
                    records[(2026, 1)][0]['invalid_amounts'] = ['J2']
                elif mode == 'name':
                    records[(2026, 1)][0]['account_name'] = 'Unrelated revenue'
                elif mode == 'unknown':
                    records[(2026, 1)].append({'account': '7777'})
                else:
                    coverage[0]['calculation_eligible'] = False
                metadata, _ = calculate(reports, records, coverage, sheets)
                self.assertEqual(metadata['status'], 'source_only')
                self.assertEqual(cell(reports, 19)['value'], '999')
                self.assertTrue(metadata['source_preserved'])

    def test_profile_requires_semantic_labels_and_registered_chart(self):
        for kind in ('label', 'registration'):
            reports, records, coverage, sheets = fixture()
            if kind == 'label':
                next(r for r in reports['balance']['rows'] if r['row'] == 8)['label'] = 'Unrelated amount'
            else:
                sheets['20_Fact_Input | Fakt']['A24']['value'] = '9999'
            metadata, _ = calculate(reports, records, coverage, sheets)
            self.assertEqual(metadata['reason'], 'profile_unrecognized')
            self.assertEqual(cell(reports, 19)['value'], '999')

    def test_moved_semantic_rows_require_original_layout(self):
        # Cash/Tax has unique semantic matches after a swap, so a permissive
        # lookup previously calculated the wrong fixed-row cards and KPI bases.
        # Cash/AR also remains rejected when duplicate detail labels are present.
        for swapped_row in (11, 9):
            with self.subTest(swapped_row=swapped_row):
                reports, records, coverage, sheets = fixture()
                cash = next(r for r in reports['balance']['rows'] if r['row'] == 8)
                other = next(r for r in reports['balance']['rows'] if r['row'] == swapped_row)
                cash['label'], other['label'] = other['label'], cash['label']
                before = copy.deepcopy(reports)
                metadata, _ = calculate(reports, records, coverage, sheets)
                self.assertEqual(metadata['status'], 'source_only')
                self.assertEqual(metadata['reason'], 'profile_unrecognized')
                self.assertEqual(reports, before)

    def test_explicit_unused_row_and_guarded_optional_account(self):
        reports, records, coverage, sheets = fixture()
        r = next(r for r in records[(2026, 1)] if r['account'] == '4500')
        r.update(measures=[None] * 6, blank_amounts=True, has_numeric=False)
        records[(2026, 1)] = [r for r in records[(2026, 1)] if r['account'] != '5800']
        metadata, _ = calculate(reports, records, coverage, sheets)
        self.assertEqual(cell(reports, 12)['value'], '0')
        self.assertEqual(cell(reports, 17)['value'], '0')
        self.assertEqual(metadata['months'][0]['optional_absent_accounts'], ['5800'])
        self.assertEqual(cell(reports, 17)['provenance'][0]['assumption'], 'absent_zero_in_complete_account_register')

    def test_missing_and_future_coverage_are_never_derived(self):
        reports, records, coverage, sheets = fixture((1, 2))
        coverage[0]['status'] = 'missing'
        coverage[1]['status'] = 'future'
        metadata, _ = calculate(reports, records, coverage, sheets)
        self.assertEqual(metadata['status'], 'source_only')
        self.assertEqual(cell(reports, 19, 1)['value'], '999')
        self.assertEqual(cell(reports, 19, 2)['value'], '999')

    def test_zero_denominators_and_negative_flow_are_unavailable(self):
        for value, expected in [('0', 'zero_denominator'), ('-10', 'nonpositive_flow')]:
            reports, records, coverage, sheets = fixture()
            cell(reports, 7, report='pnl')['value'] = value
            calculate(reports, records, coverage, sheets)
            self.assertIsNone(cell(reports, 65)['value'])
            self.assertEqual(cell(reports, 65)['reason'], expected)
        reports, records, coverage, sheets = fixture()
        for account in ('8300', '8500', '8700', '9900'):
            set_balance(records, 2026, 1, account, 0, 0, True)
        calculate(reports, records, coverage, sheets)
        self.assertIsNone(cell(reports, 64)['value'])
        self.assertEqual(cell(reports, 64)['reason'], 'zero_denominator')

    def test_ni_mismatch_is_diagnostic_and_source_remains_unchanged(self):
        reports, records, coverage, sheets = fixture()
        cell(reports, 23, report='pnl')['value'] = '-25.5'
        metadata, issues = calculate(reports, records, coverage, sheets)
        self.assertEqual(cell(reports, 23, report='pnl')['value'], '-25.5')
        self.assertEqual(metadata['months'][0]['net_income_check']['status'], 'mismatch')
        self.assertTrue(any(i[0] == 'net_income_account_mismatch' for i in issues))

    def test_decimal_precision_and_large_money_are_preserved(self):
        reports, records, coverage, sheets = fixture()
        set_balance(records, 2026, 1, '5100', 0, '9007199254740993.25')
        set_balance(records, 2026, 1, '5500', 0, '.1')
        set_balance(records, 2026, 1, '5000', 0, '.2')
        calculate(reports, records, coverage, sheets)
        self.assertEqual(cell(reports, 8)['value'], '9007199254740993.55')

    def test_different_month_values_cannot_copy_previous_month(self):
        reports, records, coverage, sheets = fixture((4, 6))
        set_balance(records, 2026, 4, '6500', 0, 3, True)
        set_balance(records, 2026, 6, '6500', 0, 7, True)
        set_balance(records, 2026, 6, '3100', 0, '120.25')
        calculate(reports, records, coverage, sheets)
        self.assertEqual(cell(reports, 46, 4)['value'], '3')
        self.assertEqual(cell(reports, 46, 6)['value'], '7')
        self.assertEqual(cell(reports, 14, 6)['value'], '120.25')
        self.assertTrue(all(p.get('account') != '6500' for p in cell(reports, 25, 4)['provenance']))

    def test_numeric_overflow_is_unavailable_not_infinite_or_zero(self):
        reports, records, coverage, sheets = fixture()
        set_balance(records, 2026, 1, '5100', 0, '1e308')
        set_balance(records, 2026, 1, '5500', 0, '1e308')
        calculate(reports, records, coverage, sheets)
        self.assertIsNone(cell(reports, 8)['value'])
        self.assertEqual(cell(reports, 8)['reason'], 'nonfinite_result')
        self.assertEqual(cell(reports, 8)['source_cache'], '999')


if __name__ == '__main__':
    unittest.main()
