"""Read-only period comparison, scope, source identity and evidence strength."""
from datetime import date, timedelta
import unittest
from unittest.mock import patch

from sqlalchemy import func, select

from tests.test_bank_statement_api import BankStatementTests
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.db import Account, Company, Ledger, PaymentRequest, User, engine, unit


class PeriodComparisonTests(BankStatementTests):
    def compare(self, source, confirm=True, account_id=None, currency='UZS', login=None, **headers):
        client, auth = login or (self.client, self.h)
        with patch('app.bank_period_comparison.parse_period_statement', return_value=source):
            return client.post('/api/bank-statements/compare-period',
                params={'account_id': account_id or self.acc, 'currency': currency, 'confirm_account': confirm},
                headers={**auth, 'X-Filename': 'Synthetic.xlsx', **headers}, content=b'synthetic')

    def statement(self, rows=None):
        return {**self.source(rows), 'owner_name': 'OOO UZGERMED TEST'}

    def counts(self):
        with unit() as s:
            return [s.scalar(select(func.count()).select_from(model))
                    for model in (BankStatementBatch, BankStatementLine, Ledger, PaymentRequest)]

    def test_first_time_confirmation_then_exact_payment_and_unmatched_mirror(self):
        rid = self.approved()
        done = self.ledger('600', 'out', reference='0071', request_id=rid)
        self.assertEqual(done.status_code, 200, done.text)
        source = self.statement([self.bank_row('0071')])
        counts = self.counts()
        first = self.compare(source, confirm=False)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()['requires_confirmation'])
        self.assertNotIn('site', first.json())
        exact = self.compare(source)
        self.assertEqual(exact.status_code, 200, exact.text)
        out = exact.json()
        self.assertEqual((out['rows'][0]['status'], out['rows'][0]['request_ids']), ('matched', [rid]))
        self.assertEqual(out['unmatched_payments'], [])
        self.assertEqual(out['site'], {'opening_balance': '1000000.00', 'closing_balance': '999400.00',
            'opening_difference': '-999000.00', 'closing_difference': '-999000.00'})
        missing = self.compare(self.statement([self.bank_row('071', inn='300000999')])).json()
        self.assertEqual(missing['rows'][0]['status'], 'bank_only')
        self.assertEqual([(r['request_id'], r['status']) for r in missing['unmatched_payments']], [(rid, 'app_only')])
        self.assertEqual(counts, self.counts())

    def test_placeholder_inn_cannot_confirm_payment(self):
        rid = self.approved()
        self.assertEqual(self.ledger('600', 'out', reference='00017', request_id=rid).status_code, 200)
        with unit(True) as s:
            s.get(PaymentRequest, rid).counterparty_inn = '000000000'
        result = self.compare(self.statement([self.bank_row('00017', inn='000000000')])).json()
        self.assertEqual(result['rows'][0]['status'], 'candidate')
        self.assertEqual(result['unmatched_payments'][0]['request_id'], rid)

    def test_repeated_document_wrong_cf_and_unpaid_do_not_match(self):
        rid = self.approved()
        self.assertEqual(self.compare(self.statement()).json()['rows'][0]['status'], 'candidate')
        self.assertEqual(self.ledger('600', 'out', reference='0001', request_id=rid).status_code, 200)
        pair = self.compare(self.statement([self.bank_row('0001'), self.bank_row('0001', line=7)])).json()
        self.assertEqual([r['status'] for r in pair['rows']], ['candidate', 'candidate'])
        self.assertEqual(pair['unmatched_payments'][0]['status'], 'candidate')
        mismatch = self.bank_row('0001')
        mismatch['purpose'] = f'Оплата по CF-{rid + 1:05d}'
        self.assertEqual(self.compare(self.statement([mismatch])).json()['rows'][0]['status'], 'candidate')
        mismatch['purpose'] = f'Оплата по СF {rid + 1:05d}'
        self.assertEqual(self.compare(self.statement([mismatch])).json()['rows'][0]['status'], 'candidate')
        mismatch['purpose'] = 'Банк'
        self.assertEqual(self.compare({**self.statement([mismatch]), 'ready': False}).json()['rows'][0]['status'], 'candidate')

    def test_date_outside_period_name_candidate_not_missing_for_period(self):
        rid = self.approved()
        self.assertEqual(self.ledger('600', 'out', reference='42', request_id=rid).status_code, 200)
        with unit(True) as s:
            req = s.get(PaymentRequest, rid)
            req.counterparty, req.counterparty_inn = 'ООО Synthetic Airport', ''
        day = str(date.fromisoformat(self.date) - timedelta(days=2))
        row = self.bank_row('99', date=day)
        row['correspondent_name'] = 'SYNTHETIC AIRPORT MCHJ'
        out = self.compare({**self.statement([row]), 'period_from': day, 'period_to': day}).json()
        self.assertEqual(out['rows'][0]['status'], 'candidate')
        detail = out['rows'][0]['request_details'][0]
        self.assertEqual((detail['request_id'], detail['date'], detail['document_number']), (rid, self.date, '42'))
        self.assertEqual(out['unmatched_payments'], [])

    def test_saved_statement_source_only_committed_and_active_links(self):
        rid = self.approved()
        self.assertEqual(self.ledger('600', 'out', reference='Internal', request_id=rid).status_code, 200)
        source = self.statement()
        preview = self.preview(source)
        self.assertEqual(preview.status_code, 200, preview.text)
        params = {'account_id': self.acc, 'currency': 'UZS'}
        base = '/api/bank-statements/period-sources/'
        self.assertEqual(self.client.get(base+'list', params=params).json()['items'], [])
        ident = preview.json()['id']
        self.assertEqual(self.client.get(base+str(ident), params=params).status_code, 404)
        commit = self.commit(ident)
        self.assertEqual(commit.status_code, 200, commit.text)
        self.assertEqual(self.client.get(base+'list', params=params).json()['items'][0]['id'], ident)
        result = self.client.get(base+str(ident), params=params)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual((result.json()['rows'][0]['status'], result.json()['binding']), ('matched', 'confirmed'))
        for key, value in [('account_number', '20200000000000000999'), ('owner_inn', '300009999')]:
            self.assertEqual(self.compare({**source, key: value}).status_code, 409)
        self.assertNotIn('requires_confirmation', self.compare(source, confirm=False).json())

    def test_unverified_source_disputed_stored_rows_and_already_claimed_fact_block_exact_match(self):
        rid = self.approved()
        self.assertEqual(self.ledger('600', 'out', reference='0021', request_id=rid).status_code, 200)
        source = self.statement([self.bank_row('0021')])
        batch = self.preview(source).json()['id']
        self.assertEqual(self.commit(batch).status_code, 200)
        self.assertEqual(self.compare(source).json()['rows'][0]['status'], 'matched')
        bad = {**source, 'ready':False, 'balance_matches':False, 'closing_balance':'123.00'}
        self.assertEqual(self.compare(bad).json()['rows'][0]['status'], 'candidate')
        competing = self.bank_row('0021')
        competing['correspondent_account'] = '20200000000000000901'
        self.assertEqual(self.compare(self.statement([competing])).json()['rows'][0]['status'], 'candidate')
        with unit(True) as s:
            line = s.scalar(select(BankStatementLine).where(BankStatementLine.batch_id == batch))
            original = line.ledger_id
            line.uncertain, line.request_id, line.ledger_id = True, None, None
            line.status = 'review'
        self.assertEqual(self.compare(source).json()['rows'][0]['status'], 'candidate')
        with unit(True) as s:
            line = s.scalar(select(BankStatementLine).where(BankStatementLine.batch_id == batch))
            line.uncertain, line.request_id, line.ledger_id, line.status = False, rid, original, 'paid_match'
            paid = s.get(Ledger, original)
            s.add(Ledger(account_id=paid.account_id, category_id=paid.category_id, kind='in',
                date=paid.date, amount=paid.amount, reference='REVERSE-TEST', reversal_of=paid.id,
                note='Synthetic reversal', creator_id=paid.creator_id))
        reversed_report = self.compare(source).json()
        self.assertEqual(reversed_report['rows'][0]['status'], 'candidate')
        self.assertEqual(reversed_report['unmatched_payments'], [])

    def test_wrong_company_role_hidden_section_and_currency(self):
        source = self.statement()
        self.assertEqual(self.compare({**source, 'owner_name': 'OOO ZUMA'}).status_code, 409)
        self.assertEqual(self.compare({**source, 'currency': 'USD'}).status_code, 409)
        result = self.post('/api/accounts', {'name': 'Dollar bank', 'kind':'bank', 'currency':'USD',
            'opening':'0.00', 'opening_date': self.date})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.compare(source, currency='USD', account_id=result.json()['id']).status_code, 409)
        with unit() as s:
            zuma = s.scalar(select(Company.id).where(Company.code == 'ZUMA'))
        self.assertEqual(self.compare(source, **{'X-Company-ID': str(zuma)}).status_code, 404)
        employee = self.make_user('employee', 'period_employee')
        try:
            self.assertEqual(self.compare(source, login=employee).status_code, 403)
        finally:
            employee[0].close()
        with unit() as s:
            uid = s.scalar(select(User.id).where(User.username == 'statement_payer'))
        def sections(version, mode):
            return self.client.put(f'/api/admin/users/{uid}/sections', headers=self.h,
                json={'company_id': self.company, 'version': version, 'sections': {'ledger':'view','requests':mode},
                      'reason':'Synthetic permission test for bank period'})
        setting = sections(0, 'view')
        self.assertEqual(setting.status_code, 200, setting.text)
        self.payer[0].close()
        self.payer = self.relogin('statement_payer')
        self.assertEqual(self.compare(source, login=self.payer).status_code, 200)
        self.assertEqual(sections(1, 'hidden').status_code, 200)
        self.payer[0].close()
        self.payer = self.relogin('statement_payer')
        self.assertEqual(self.compare(source, login=self.payer).status_code, 403)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(PeriodComparisonTests(name) for name in PeriodComparisonTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
