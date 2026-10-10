"""Company-private source review, using only synthetic workbooks and temporary DBs."""
import io
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as XML
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests.test_site as base
from openpyxl import Workbook
from sqlalchemy import func, select
from app.db import Account, Company, FinancialStatementSource, Ledger, Receipt, unit
from app.financial_statements import build_review, col

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
M = '{' + NS['m'] + '}'
HEADERS = ['year', 'month', 'period', 'account', 'account_name', 'opening_debit',
           'opening_credit', 'turnover_debit', 'turnover_credit', 'closing_debit', 'closing_credit']


def tearDownModule():
    base.tearDownModule()


def workbook(year=2026, months=(1,), configure=None, caches=None, shared=None):
    wb = Workbook()
    pnl = wb.active
    pnl.title = '09_Statements'
    balance = wb.create_sheet('09b_Balance')
    mapping = wb.create_sheet('Mapping')
    assumptions = wb.create_sheet('01_Assumptions')
    wb.create_sheet('20_Fact_Input | Fakt')
    assumptions['A5'] = 'Model currency: UZS'
    mapping.append(HEADERS)
    for month in months:
        mapping.append([year, month, f'{year}-{month:02}', '0100', 'Synthetic account', 0, 0, 0, 0, 0, 0])
    for sheet in (pnl, balance):
        for month in range(1, 13):
            pc, fc = col(3 + 2 * month), col(4 + 2 * month)
            sheet[pc + '4'] = date(year, month, 1)
            sheet[pc + '5'] = 'План'
            sheet[fc + '5'] = 'Факт'
        sheet['D7'] = 'Synthetic amount'
        for month in range(1, 13):
            sheet[col(3 + 2 * month) + '7'] = 0
            sheet[col(4 + 2 * month) + '7'] = 0
    if configure:
        configure(wb)
    stream = io.BytesIO()
    wb.save(stream)
    wb.close()
    raw = stream.getvalue()
    if not caches and not shared:
        return raw
    output = io.BytesIO()
    with ZipFile(io.BytesIO(raw)) as src, ZipFile(output, 'w') as dst:
        for entry in src.infolist():
            content = src.read(entry.filename)
            if entry.filename in (caches or {}) or entry.filename in (shared or {}):
                tree = XML.fromstring(content)
                for c in tree.findall('.//m:c', NS):
                    address = c.attrib['r']
                    if address in (caches or {}).get(entry.filename, {}):
                        cached = caches[entry.filename][address]
                        v = c.find('m:v', NS)
                        if v is None:
                            v = XML.SubElement(c, M + 'v')
                        if isinstance(cached, tuple):
                            c.set('t', cached[0]); v.text = cached[1]
                        else:
                            c.set('t', 'n'); v.text = str(cached)
                    if address in (shared or {}).get(entry.filename, {}):
                        si, expression, ref = shared[entry.filename][address]
                        f = c.find('m:f', NS)
                        if f is None:
                            f = XML.SubElement(c, M + 'f')
                        f.attrib = {'t': 'shared', 'si': str(si)}
                        if ref:
                            f.set('ref', ref)
                        f.text = expression
                content = XML.tostring(tree, encoding='utf-8', xml_declaration=True)
            dst.writestr(entry, content)
    return output.getvalue()


def first_cell(snapshot, key='pnl', month=1, mode='fact'):
    return snapshot['reports'][key]['rows'][0]['cells'][month - 1][mode]


class FinancialStatementParserTests(unittest.TestCase):
    def test_explicit_zero_and_missing_actual_period_are_different(self):
        def configure(wb):
            wb['09_Statements']['H7'] = '=SUM(Mapping!H:H)'
        raw = workbook(configure=configure, caches={'xl/worksheets/sheet1.xml': {'H7': 0}})
        review = build_review(raw, 'synthetic.xlsx', as_of=date(2026, 9, 16))
        self.assertEqual(first_cell(review)['value'], '0')
        self.assertEqual(first_cell(review)['status'], 'value')
        missing = first_cell(review, month=2)
        self.assertIsNone(missing['value'])
        self.assertEqual(missing['status'], 'missing')
        self.assertEqual(missing['source_cache'], '0')
        self.assertEqual(first_cell(review, month=2, mode='plan')['value'], '0')
        self.assertEqual(review['review_status'], 'needs_review')

    def test_header_year_not_filename_and_historical_vs_future(self):
        historical = build_review(workbook(year=2024), 'FinModel2026.xlsx', date(2026, 9, 16))
        self.assertEqual(historical['year'], 2024)
        self.assertEqual(first_cell(historical)['value'], '0')
        future = build_review(workbook(year=2027), 'FinModel2026.xlsx', date(2026, 9, 16))
        self.assertEqual(future['year'], 2027)
        self.assertEqual(first_cell(future)['status'], 'future')
        self.assertIsNone(first_cell(future)['value'])
        self.assertEqual(first_cell(future, mode='plan')['value'], '0')

    def test_other_year_or_conflicting_period_does_not_confirm_fact(self):
        def other_year(wb):
            wb['Mapping']['A2'] = 2025
            wb['Mapping']['C2'] = '2025-01'
        other = build_review(workbook(configure=other_year), 'test.xlsx', date(2026, 9, 16))
        self.assertIsNone(first_cell(other)['value'])
        self.assertIn('source_year_mismatch', {i['code'] for i in other['issues']})
        def conflict(wb):
            wb['Mapping']['C2'] = '2026-02'
        invalid = build_review(workbook(configure=conflict), 'test.xlsx', date(2026, 9, 16))
        self.assertEqual(first_cell(invalid)['status'], 'invalid')
        self.assertIn('mapping_period_conflict', {i['code'] for i in invalid['issues']})

    def test_mapping_period_supports_full_dates_and_rejects_month_only(self):
        for period in ('2026-01', '2026-01-31', '31.01.2026', date(2026, 1, 1)):
            with self.subTest(period=period):
                raw = workbook(configure=lambda w: setattr(w['Mapping']['C2'], 'value', period))
                result = build_review(raw, 'test.xlsx', date(2026, 9, 16))
                self.assertEqual(result['coverage'][0]['status'], 'available')
        raw = workbook(configure=lambda w: setattr(w['Mapping']['C2'], 'value', 'January'))
        self.assertEqual(build_review(raw, 'test.xlsx', date(2026, 9, 16))['coverage'][0]['status'], 'invalid')

    def test_formula_missing_cache_and_excel_errors_do_not_become_zero(self):
        def configure(wb):
            wb['09_Statements']['F7'] = '=1+1'
            wb['09b_Balance']['F7'] = '=#REF!'
        raw = workbook(configure=configure, caches={'xl/worksheets/sheet2.xml': {'F7': ('e', '#REF!')}})
        review = build_review(raw, 'test.xlsx', date(2026, 9, 16))
        self.assertEqual(first_cell(review)['status'], 'missing_cache')
        self.assertIsNone(first_cell(review)['value'])
        self.assertEqual(first_cell(review, 'balance')['status'], 'error')
        self.assertIsNone(first_cell(review, 'balance')['value'])
        self.assertIn('broken_references', {i['code'] for i in review['issues']})

    def test_iferror_masked_ref_cache_is_diagnostic_not_displayed_zero(self):
        def configure(wb):
            wb['09b_Balance']['D65'] = 'DSO source'
            wb['09b_Balance']['E65'] = '=IFERROR(30*#REF!/ABS(E7),0)'
            wb['09b_Balance']['F65'] = '=IFERROR(30*#REF!/ABS(F7),0)'
        raw = workbook(configure=configure,
            caches={'xl/worksheets/sheet2.xml': {'E65': 0, 'F65': 0}})
        review = build_review(raw, 'test.xlsx', date(2026, 9, 16))
        row = next(r for r in review['reports']['balance']['rows'] if r['row'] == 65)
        for mode in ('plan', 'fact'):
            with self.subTest(mode=mode):
                value = row['cells'][0][mode]
                self.assertEqual(value['status'], 'error')
                self.assertIsNone(value['value'])
                self.assertEqual(value['source_cache'], '0')
                self.assertIn('#REF!', value['formula'])
        self.assertIn('broken_references', {i['code'] for i in review['issues']})

    def test_ratio_units_and_full_balance_are_preserved(self):
        def configure(wb):
            for row in (13, 24):
                wb['09_Statements']['D' + str(row)] = 'Percent source'
                wb['09_Statements']['E' + str(row)] = .31
            for row in (62, 65, 66, 73, 81):
                wb['09b_Balance']['D' + str(row)] = 'Source row'
                wb['09b_Balance']['E' + str(row)] = 1.2
        review = build_review(workbook(configure=configure), 'test.xlsx', date(2026, 9, 16))
        pnl = {r['row']: r for r in review['reports']['pnl']['rows']}
        balance = {r['row']: r for r in review['reports']['balance']['rows']}
        self.assertEqual(pnl[13]['measure'], 'percent')
        self.assertEqual(pnl[13]['cells'][0]['plan']['value'], '0.31')
        self.assertEqual(balance[65]['measure'], 'days')
        self.assertEqual(balance[73]['measure'], 'coefficient')
        self.assertEqual(balance[81]['kind'], 'amount')

    def test_shared_formula_diagnostics_are_structural_and_do_not_recalculate(self):
        def configure(wb):
            b, p, f = wb['09b_Balance'], wb['09_Statements'], wb['20_Fact_Input | Fakt']
            for row in (19, 25, 30, 31, 35, 36, 42, 46):
                b['D' + str(row)] = 'Source row'
                b['E' + str(row)] = 0
                b['F' + str(row)] = 0
            b['E36'] = '=E19-E35'; b['F36'] = '=F19-F35'
            b['F35'] = '=SUM(F22:F26)+F46'
            b['F25'] = "='20_Fact_Input | Fakt'!D43+'20_Fact_Input | Fakt'!D44"
            b['F63'] = "=IFERROR('09_Statements'!F23/F37,0)"
            b['F64'] = "=IFERROR('09_Statements'!F7/F36,0)"
            p['D18'] = 'EBIT'; p['E18'] = '=E16-E17'; p['F18'] = '=F16'
            p['F7'] = '=F8-F9-F10'; p['F8'] = "='30_Plan_vs_Fact | PvF'!D7-F9"
            f['D12'] = '=SUMIFS(Mapping!$K:$K,Mapping!$D:$D,$Q12,Mapping!$B:$B,D$5)'
        raw = workbook(configure=configure,
            caches={'xl/worksheets/sheet2.xml': {'E36': 7, 'F36': 9}},
            shared={'xl/worksheets/sheet2.xml': {'E36': (0, 'E19-E35', 'E36:F36'), 'F36': (0, None, None)}})
        review = build_review(raw, 'test.xlsx', date(2026, 9, 16))
        codes = {i['code'] for i in review['issues']}
        self.assertTrue({'equity_plug', 'loans_omitted', 'social_double_count', 'ebit_classification',
                         'payroll_payable_as_expense', 'yearless_sumifs', 'roe_methodology',
                         'roa_methodology', 'returns_double_count'} <= codes, codes)
        row = next(r for r in review['reports']['balance']['rows'] if r['row'] == 36)
        self.assertEqual(row['cells'][0]['fact']['formula'], '=F19-F35')
        self.assertEqual(row['cells'][0]['fact']['value'], '9')

    def test_duplicate_mapping_account_is_ambiguous(self):
        def configure(wb):
            wb['Mapping'].append([2026, 1, '2026-01', '0100', 'Duplicate', 0, 0, 0, 0, 0, 0])
        review = build_review(workbook(configure=configure), 'test.xlsx', date(2026, 9, 16))
        self.assertEqual(first_cell(review)['status'], 'invalid')
        self.assertIn('mapping_duplicate', {i['code'] for i in review['issues']})

    def test_blank_account_only_does_not_confirm_actual_but_explicit_zero_does(self):
        def blank_only(wb):
            for column in range(6, 12):
                wb['Mapping'].cell(2, column, None).value = None
        review = build_review(workbook(configure=blank_only), 'test.xlsx', date(2026, 9, 16))
        self.assertEqual(review['coverage'][0]['record_count'], 1)
        self.assertEqual(review['coverage'][0]['numeric_record_count'], 0)
        self.assertEqual(review['coverage'][0]['status'], 'missing')
        self.assertIsNone(first_cell(review)['value'])
        def blank_and_numeric(wb):
            blank_only(wb)
            wb['Mapping'].append([2026, 1, '2026-01', '0200', 'Explicit zero', 0, 0, 0, 0, 0, 0])
        confirmed = build_review(workbook(configure=blank_and_numeric), 'test.xlsx', date(2026, 9, 16))
        self.assertEqual(confirmed['coverage'][0]['status'], 'available')
        self.assertEqual(confirmed['coverage'][0]['numeric_record_count'], 1)
        self.assertEqual(first_cell(confirmed)['value'], '0')

    def test_mapping_boolean_text_and_masked_ref_are_not_numeric_amounts(self):
        for invalid_amount in (True, '1', '=IFERROR(#REF!,0)'):
            with self.subTest(invalid_amount=invalid_amount):
                def configure(wb):
                    for column in range(6, 12):
                        wb['Mapping'].cell(2, column).value = None
                    wb['Mapping']['H2'] = invalid_amount
                caches = {'xl/worksheets/sheet3.xml': {'H2': 0}} if str(invalid_amount).startswith('=') else None
                review = build_review(workbook(configure=configure, caches=caches), 'test.xlsx', date(2026, 9, 16))
                self.assertEqual(review['coverage'][0]['numeric_record_count'], 0)
                self.assertEqual(review['coverage'][0]['status'], 'invalid')
                self.assertIsNone(first_cell(review)['value'])
                self.assertIn('mapping_amount_invalid', {i['code'] for i in review['issues']})

    def test_norms_are_original_author_guidelines_without_pass_fail(self):
        def configure(wb):
            wb['09b_Balance']['D62'] = 'Source margin'
            wb['09b_Balance']['A62'] = '>20%'
            wb['09b_Balance']['E62'] = .15
        result = build_review(workbook(configure=configure), 'test.xlsx', date(2026, 9, 16))
        row = next(r for r in result['reports']['balance']['rows'] if r['row'] == 62)
        self.assertEqual(row['norm'], {'value': '>20%', 'source': '09b_Balance!A62', 'status': 'author_guideline'})
        self.assertEqual(result['standards']['presentation_basis'], 'IAS 1')
        self.assertEqual(result['standards']['compliance_status'], 'not_assessed')
        self.assertNotIn('norm_pass', row)
        self.assertIn('ifrs_adjustments_missing', {i['code'] for i in result['issues']})


class FinancialStatementApiTests(unittest.TestCase):
    setUp = base.SiteTests.setUp
    tearDown = base.SiteTests.tearDown
    post = base.SiteTests.post
    ready = base.SiteTests.ready
    make_user = base.SiteTests.make_user

    def upload_source(self, content=None, client=None, headers=None, company=None):
        return (client or self.client).post('/api/financial-statements/upload',
            params={'company_id': company or self.company}, content=content or workbook(),
            headers={**(headers or self.h), 'X-Filename': 'synthetic.xlsx', 'Content-Type': 'application/octet-stream'})

    def test_empty_source_and_authorization(self):
        response = self.client.get('/api/financial-statements')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['sources'], [])
        self.assertIsNone(response.json()['source'])
        self.assertTrue(response.json()['requirements'])
        for role in ('sales', 'employee', 'accountant', 'cashier', 'material_accountant', 'procurement'):
            with self.subTest(role=role):
                client, headers = self.make_user(role, 'source_' + role)
                self.addCleanup(client.close)
                self.assertEqual(client.get('/api/financial-statements').status_code, 403)
                with patch('app.financial_statements.build_review') as parser:
                    self.assertEqual(self.upload_source(client=client, headers=headers).status_code, 403)
                    parser.assert_not_called()
        client, headers = self.make_user('auditor', 'source_viewer')
        self.addCleanup(client.close)
        self.assertEqual(client.get('/api/financial-statements').status_code, 200)
        self.assertEqual(self.upload_source(client=client, headers=headers).status_code, 403)

    def test_source_upload_deduplicates_per_company_and_keeps_ledger(self):
        with unit() as s:
            before = (s.scalar(select(func.count(Ledger.id))), s.scalar(select(func.count(Receipt.id))),
                      [(a.id, a.opening) for a in s.scalars(select(Account))])
            zuma = s.scalar(select(Company).where(Company.code == 'ZUMA')).id
        raw = workbook()
        first = self.upload_source(raw)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()['created'])
        repeated = self.upload_source(raw)
        self.assertEqual(repeated.json(), {'id': first.json()['id'], 'created': False, 'company_id': self.company})
        other = self.upload_source(raw, company=zuma)
        self.assertEqual(other.status_code, 200, other.text)
        self.assertTrue(other.json()['created'])
        self.assertNotEqual(other.json()['id'], first.json()['id'])
        result = self.client.get('/api/financial-statements', params={'company_id': self.company}).json()
        self.assertEqual([r['id'] for r in result['sources']], [first.json()['id']])
        self.assertEqual(result['source']['company_id'], self.company)
        self.assertNotIn('content', result['source'])
        with unit() as s:
            after = (s.scalar(select(func.count(Ledger.id))), s.scalar(select(func.count(Receipt.id))),
                     [(a.id, a.opening) for a in s.scalars(select(Account))])
            stored = s.get(FinancialStatementSource, first.json()['id'])
            self.assertEqual(stored.content, raw)
        self.assertEqual(before, after)

    def test_explicit_foreign_source_is_404_and_headers_must_match(self):
        with unit() as s:
            zuma = s.scalar(select(Company).where(Company.code == 'ZUMA')).id
        foreign = self.upload_source(company=zuma).json()['id']
        response = self.client.get('/api/financial-statements', params={'company_id': self.company, 'source_id': foreign})
        self.assertEqual(response.status_code, 404, response.text)
        mismatch = self.client.get('/api/financial-statements', params={'company_id': zuma},
                                   headers={'X-Company-ID': str(self.company)})
        self.assertEqual(mismatch.status_code, 409)

    def test_sources_are_immutable_and_have_no_download_route(self):
        source_id = self.upload_source().json()['id']
        with self.assertRaisesRegex(Exception, 'не изменяются'):
            with unit(True) as s:
                s.get(FinancialStatementSource, source_id).filename = 'rewritten.xlsx'
        with self.assertRaisesRegex(Exception, 'не изменяются'):
            with unit(True) as s:
                s.delete(s.get(FinancialStatementSource, source_id))
        self.assertEqual(self.client.get(f'/api/financial-statements/{source_id}/download').status_code, 404)
        self.assertEqual(self.client.delete(f'/api/financial-statements/{source_id}', headers=self.h).status_code, 404)

    def test_invalid_xlsx_filename_and_unauthorized_upload_are_not_saved(self):
        self.assertEqual(self.upload_source(b'not an Excel archive').status_code, 422)
        response = self.client.post('/api/financial-statements/upload', content=workbook(),
            headers={**self.h, 'X-Filename': 'bad.csv'})
        self.assertEqual(response.status_code, 422)
        without_csrf = self.client.post('/api/financial-statements/upload', content=workbook(),
                                       headers={'X-Filename': 'test.xlsx'})
        self.assertEqual(without_csrf.status_code, 403)
        self.assertEqual(self.client.get('/api/financial-statements').json()['sources'], [])


if __name__ == '__main__':
    unittest.main()
