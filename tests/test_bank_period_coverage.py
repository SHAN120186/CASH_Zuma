from datetime import date, timedelta
import unittest

from sqlalchemy import select

from tests.test_bank_statement_api import BankStatementTests
from app.bank_statement_models import BankStatementBatch
from app.db import Company, Ledger, User, engine, unit


class BankPeriodCoverageTests(BankStatementTests):
    def coverage(self, start=None, end=None, *, login=None, headers=None, **params):
        client, auth = login or (self.client, self.h)
        return client.get('/api/bank-statements/uncovered-payments', headers={**auth, **(headers or {})},
            params={'account_id': self.acc, 'currency': 'UZS',
                    'date_from': start or self.date, 'date_to': end or self.date, **params})

    def saved(self, **overrides):
        with unit(True) as s:
            day = date.fromisoformat(self.date)
            fields = {'company_id': self.company, 'account_id': self.acc, 'created_by': s.scalar(
                select(User.id).where(User.username == 'admin')), 'filename':'synthetic.xls',
                'sha256':'a'*64, 'account_number':'20200000000000000001', 'owner_name':'Test', 'owner_inn':'123456789',
                'currency':'UZS', 'period_from':day, 'period_to':day, 'opening':100000, 'closing':100000,
                'debit_total':0, 'credit_total':0, 'balance_matches':True, 'declared_totals_match':True,
                'source_ready':True, 'status':'committed'}
            batch = BankStatementBatch(**{**fields, **overrides})
            s.add(batch)
            s.flush()
            return batch.id

    def paid(self, amount, reference):
        rid = self.approved(amount)
        response = self.ledger(amount, 'out', request_id=rid, reference=reference)
        self.assertEqual(response.status_code, 200, response.text)
        return rid

    def test_only_active_request_payments_on_uncovered_fact_dates(self):
        rid = self.paid('500', 'A-01')
        self.approved('200')
        self.assertEqual(self.ledger('10', 'out', reference='NOT-A-REQUEST').status_code, 200)
        previous = date.fromisoformat(self.date) - timedelta(days=1)
        self.saved(period_from=previous, period_to=previous)
        rows = self.coverage(previous, self.date).json()['rows']
        self.assertEqual([(row['request_id'], row['date'], row['document_number'], row['amount']) for row in rows],
                         [(rid, self.date, 'A-01', '500.00')])
        self.assertEqual(self.coverage(previous, previous).json()['rows'], [])
        with unit(True) as s:
            original = s.scalar(select(Ledger).where(Ledger.request_id == rid))
            s.add(Ledger(company_id=self.company, account_id=self.acc, kind='in', date=original.date,
                         amount=original.amount, reference='Reversal', creator_id=original.creator_id,
                         reversal_of=original.id))
        self.assertEqual(self.coverage().json()['rows'], [])

    def test_only_complete_confirmed_statement_covers_its_period(self):
        rid = self.paid('500', 'A-02')
        identity = self.saved()
        self.assertEqual(self.coverage().json()['rows'], [])
        for field, value, original in (
            ('status', 'preview', 'committed'), ('status', 'deleted', 'committed'),
            ('source_ready', False, True), ('balance_matches', False, True),
            ('declared_totals_match', None, True), ('closing', 100001, 100000),
            ('currency', 'USD', 'UZS'),
        ):
            with self.subTest(field=field, value=value):
                with unit(True) as s:
                    setattr(s.get(BankStatementBatch, identity), field, value)
                self.assertEqual([r['request_id'] for r in self.coverage().json()['rows']], [rid])
                with unit(True) as s:
                    setattr(s.get(BankStatementBatch, identity), field, original)
        self.saved(account_number='20200000000000000002')
        self.assertEqual(self.coverage().status_code, 409)

    def test_dates_company_role_and_currency(self):
        day = date.fromisoformat(self.date)
        self.assertEqual(self.coverage(day, day+timedelta(days=91)).status_code, 200)
        self.assertEqual(self.coverage(day, day+timedelta(days=92)).status_code, 422)
        self.assertEqual(self.coverage(day, day-timedelta(days=1)).status_code, 422)
        self.assertEqual(self.coverage(currency='USD').status_code, 422)
        with unit() as s:
            zuma = s.scalar(select(Company.id).where(Company.code == 'ZUMA'))
        self.assertEqual(self.coverage(headers={'X-Company-ID':str(zuma)}).status_code, 404)
        employee = self.make_user('employee', 'coverage_employee')
        try:
            self.assertEqual(self.coverage(login=employee).status_code, 403)
        finally:
            employee[0].close()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(BankPeriodCoverageTests(name) for name in BankPeriodCoverageTests.__dict__
                              if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
