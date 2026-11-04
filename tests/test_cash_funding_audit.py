"""Independent synthetic accounting checks for bank-funded cash requests."""
from datetime import date
import unittest

from sqlalchemy import select

from tests.test_cash_funding import CashFundingTests
from tests import test_site as base
from app import clock
from app.db import Account, Company, Ledger, unit
from app.services import account_balance


class CashFundingAuditTests(CashFundingTests):
    def set_day(self, day):
        clock.FROZEN = day
        self.date = day.isoformat()
        # Sessions intentionally expire after an hour. Re-authenticate the
        # synthetic actors when advancing business time instead of weakening it.
        response = self.client.post('/api/login', json={'username': 'admin', 'password': base.PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        self.h = {'X-CSRF-Token': response.json()['csrf'], 'X-Company-ID': str(self.company)}
        for attribute, username in (('checker', 'checker'), ('approver', 'reviewer'),
                                    ('director', 'chief'), ('cash_user', 'cash_receiver')):
            if hasattr(self, attribute):
                getattr(self, attribute)[0].close()
                setattr(self, attribute, self.relogin(username, self.company))

    def make_user(self, role='employee', name='worker'):
        client, headers = super().make_user(role, name)
        return client, {**headers, 'X-Company-ID': str(self.company)}

    def actuals(self, day):
        response = self.client.get('/api/report', headers=self.h, params={
            'year': day.year, 'currency': 'UZS', 'company_id': self.company,
            'as_of': day.isoformat()})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def issue(self, rid):
        response = self.ledger('990000', 'out', reference='Audit actual cash issue',
                               client=self.cash_payer(), account_id=self.cash,
                               category_id=self.cat, request_id=rid)
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def test_withdrawal_and_cash_issue_in_different_months(self):
        rid = self.cash_request()
        self.set_day(date(2026, 9, 30))
        self.funding_action(rid, 'withdraw', *self.stage_user('check'))
        september = self.actuals(date(2026, 9, 30))
        self.assertEqual(september['totals'][8], '-10000.00')
        self.assertEqual(september['closing'][8], '990000.00')
        self.assertEqual(self.balances()['cash'], 0)
        self.assertEqual(self.balances()['transit'], 99000000)

        self.set_day(date(2026, 10, 1))
        self.funding_action(rid, 'receive', *self.cash_payer())
        self.assertEqual(self.actuals(date(2026, 10, 1))['totals'][9], '0.00')
        self.assertEqual(self.balances()['cash'], 99000000)
        self.set_day(date(2026, 10, 2))
        self.issue(rid)
        october = self.actuals(date(2026, 10, 2))
        self.assertEqual(october['totals'][8], '-10000.00')
        self.assertEqual(october['totals'][9], '-990000.00')
        self.assertEqual(october['closing'][8], '990000.00')
        self.assertEqual(october['closing'][9], '0.00')
        with unit() as session:
            entries = list(session.scalars(select(Ledger)))
            self.assertEqual(len(entries), 4)
            self.assertEqual(sum(row.amount for row in entries if row.kind == 'out'), 100000000)
            self.assertEqual(sum(account_balance(session, account) for account in session.scalars(select(Account))), 0)

    def test_half_kopeck_commission_rounds_once_and_net_conserves_money(self):
        rid = self.cash_request(gross='0.50', pct='1', net='0.49')
        funding = self.row(rid)['cash_funding']
        self.assertEqual(funding['fee_amount'], '0.01')
        self.assertEqual(funding['net_amount'], '0.49')
        self.funding_action(rid, 'withdraw', *self.stage_user('check'))
        self.funding_action(rid, 'receive', *self.cash_payer())
        response = self.ledger('0.49', 'out', reference='Audit rounded cash issue',
                               client=self.cash_payer(), account_id=self.cash,
                               category_id=self.cat, request_id=rid)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.report()['totals'][8], '-0.50')
        with unit() as session:
            entries = list(session.scalars(select(Ledger)))
            self.assertEqual(sum(row.amount for row in entries if row.kind == 'out'), 50)
            self.assertEqual(account_balance(session, session.get(Account, self.acc)), 99999950)

    def test_cash_already_available_does_not_bypass_funding_receipt(self):
        rid = self.cash_request()
        with unit(True) as session:
            session.get(Account, self.cash).opening = 200000000
        response = self.ledger('990000', 'out', reference='Audit premature cash issue',
                               client=self.cash_payer(), account_id=self.cash,
                               category_id=self.cat, request_id=rid)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.row(rid)['status'], 'approved')
        self.assertEqual(self.row(rid)['cash_funding']['state'], 'unfunded')
        with unit() as session:
            self.assertEqual(list(session.scalars(select(Ledger))), [])

    def test_complete_cycle_in_zuma_keeps_uzgermed_account_untouched(self):
        original_bank = self.acc
        with unit() as session:
            zuma = session.scalar(select(Company.id).where(Company.code == 'ZUMA'))
        self.h['X-Company-ID'] = str(zuma)
        self.company = zuma
        bootstrap = self.client.get('/api/bootstrap', headers=self.h)
        self.assertEqual(bootstrap.status_code, 200, bootstrap.text)
        self.cat = next(row['id'] for row in bootstrap.json()['categories'] if row['type'] == 'outcome')
        account = self.post('/api/accounts', {'name': 'Audit ZUMA bank', 'kind': 'bank',
                           'currency': 'UZS', 'opening': '1000000', 'opening_date': self.date})
        self.assertEqual(account.status_code, 200, account.text)
        self.acc = account.json()['id']
        rid = self.cash_request()
        self.funding_action(rid, 'withdraw', *self.stage_user('check'))
        self.funding_action(rid, 'receive', *self.cash_payer())
        self.issue(rid)
        self.assertEqual(self.row(rid)['status'], 'paid')
        self.assertEqual(self.report()['totals'][8], '-1000000.00')
        with unit() as session:
            self.assertEqual(account_balance(session, session.get(Account, original_bank)), 100000000)
            self.assertEqual(account_balance(session, session.get(Account, self.acc)), 0)
            entries = list(session.scalars(select(Ledger)))
            self.assertEqual(len(entries), 4)
            accounts = {row.id: row for row in session.scalars(select(Account))}
            self.assertTrue(all(accounts[row.account_id].company_id == zuma for row in entries))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(CashFundingAuditTests(name) for name in CashFundingAuditTests.__dict__
                              if name.startswith('test_'))


def tearDownModule():
    base.tearDownModule()
