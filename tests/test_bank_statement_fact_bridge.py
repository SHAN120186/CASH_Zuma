"""Stored synthetic statement -> confirmed ledger -> report and overview facts."""
import unittest
from datetime import date, timedelta

from sqlalchemy import select

from tests.test_bank_statement_resume import SavedStatementTests
from app.bank_statement_models import BankStatementBatch
from app.db import Account, Ledger, PaymentRequest, engine, unit
from app.services import account_balance


class StatementFactBridgeTests(SavedStatementTests):
    def facts(self):
        report = self.client.get('/api/report', headers=self.h, params={
            'year': int(self.date[:4]), 'currency': 'UZS', 'company_id': self.company})
        dashboard = self.client.get('/api/dashboard', headers=self.h, params={'currency': 'UZS'})
        group = self.client.get('/api/group-report', headers=self.h, params={
            'company_id': self.company, 'currency': 'UZS', 'month': self.date[:7], 'day': self.date})
        for result in (report, dashboard, group):
            self.assertEqual(result.status_code, 200, result.text)
        return report.json(), dashboard.json(), group.json()

    def test_reopened_confirmation_updates_reports_movement_and_fact_exactly_once(self):
        rid = self.approved()
        preview = self.payment_preview(self.statement(), self.cat)
        self.assertEqual(preview.status_code, 200, preview.text)
        bid = preview.json()['id']
        reopened = self.resume(bid, self.cat, *self.accountant)
        self.assertEqual(reopened.status_code, 200, reopened.text)
        report, dashboard, movement = self.facts()
        month = int(self.date[5:7]) - 1
        self.assertEqual(report['totals'][month], '0.00')
        self.assertEqual((dashboard['fact_in'], dashboard['fact_out']), ('0.00', '0.00'))
        self.assertEqual((movement['income_day'], movement['expense_day']), ('0.00', '0.00'))

        result = self.payment_confirmed(reopened.json(), self.cat)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['full_reconciliation'])
        report, dashboard, movement = self.facts()
        self.assertEqual(report['totals'][month], '-420.00')
        self.assertEqual((dashboard['fact_in'], dashboard['fact_out']), ('200.00', '620.00'))
        self.assertEqual((movement['income_day'], movement['expense_day'], movement['closing']),
                         ('200.00', '620.00', '999580.00'))
        self.assertEqual(self.saved().json()['items'], [])
        history = self.client.get(f'/api/bank-statements/history/{bid}', headers=self.h)
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual([r['operation_state'] for r in history.json()['rows']], ['active'] * 3)
        ledger = self.client.get('/api/ledger', headers=self.h, params={'currency': 'UZS'})
        self.assertEqual(ledger.status_code, 200, ledger.text)
        self.assertEqual(len(ledger.json()), 3)

        # Retries after a lost response, opening old drafts and reuploading the
        # same file must not count the bank total on top of the posted ledger.
        self.assertEqual(self.payment_confirmed(reopened.json(), self.cat).status_code, 409)
        self.assertEqual(self.resume(bid, self.cat, *self.accountant).status_code, 409)
        self.assertEqual(self.payment_preview(self.statement(), self.cat).status_code, 409)
        self.assertEqual(self.facts(), (report, dashboard, movement))
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(BankStatementBatch)))), 1)
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 3)
            self.assertEqual(s.get(PaymentRequest, rid).status, 'paid')
            self.assertEqual(account_balance(s, s.get(Account, self.acc)), 99958000)

    def test_historical_statement_updates_its_month_not_current_fact_cards(self):
        current = date.fromisoformat(self.date)
        historical = current.replace(day=1) - timedelta(days=1)
        with unit(True) as s:
            s.get(Account, self.acc).opening_date = historical - timedelta(days=1)
        old_day = historical.strftime('%d.%m.%Y').encode('ascii')
        raw = self.statement(False).replace(current.strftime('%d.%m.%Y').encode('ascii'), old_day)
        preview = self.preview(raw, self.cat)
        self.assertEqual(preview.status_code, 200, preview.text)
        reopened = self.resume(preview.json()['id'], self.cat)
        self.assertEqual(reopened.status_code, 200, reopened.text)
        confirmed = self.confirmed(reopened.json(), self.cat)
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertTrue(confirmed.json()['full_reconciliation'])
        report, dashboard, movement = self.facts()
        self.assertEqual(report['totals'][historical.month - 1], '180.00')
        self.assertEqual(report['totals'][current.month - 1], '0.00')
        self.assertEqual((dashboard['fact_in'], dashboard['fact_out'], dashboard['balance']),
                         ('0.00', '0.00', '1000180.00'))
        self.assertEqual((movement['income_day'], movement['expense_day'], movement['closing']),
                         ('0.00', '0.00', '1000180.00'))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(StatementFactBridgeTests(name) for name in StatementFactBridgeTests.__dict__
                              if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
