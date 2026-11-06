"""Partial synthetic bank facts use one ledger and conservative available funds."""
import unittest
from datetime import date, timedelta

from sqlalchemy import select

from tests.test_bank_statement_auto_integration import AutoBankIntegrationTests
from app.bank_statement_models import BankStatementBatch
from app.db import Account, Ledger, PaymentRequest, engine, unit
from app.services import account_balance, funds_state


class PartialBankFactTests(AutoBankIntegrationTests):
    def partial_statement(self):
        rid = self.approved()
        # The last debit might be an internal transfer; leave it unclassified.
        raw = self.statement(external=True).replace(b'<td>300000005</td>', b'<td>300000001</td>')
        preview = self.payment_preview(raw, self.cat)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertFalse(preview.json()['full_reconciliation'])
        return rid, preview.json()

    def reports(self):
        params = {'company_id': self.company, 'currency': 'UZS'}
        paths = (
            ('report', {**params, 'year': int(self.date[:4])}),
            ('dashboard', {'currency': 'UZS'}),
            ('group-report', {**params, 'month': self.month, 'day': self.date}),
            ('accounts', {}),
        )
        result = []
        for path, query in paths:
            response = self.client.get('/api/' + path, params=query, headers=self.h)
            self.assertEqual(response.status_code, 200, response.text)
            result.append(response.json())
        return result

    def test_partial_confirm_updates_fact_and_holds_unposted_debit_without_double_count(self):
        rid, preview = self.partial_statement()
        committed = self.payment_confirmed(preview, self.cat)
        self.assertEqual(committed.status_code, 200, committed.text)
        self.assertFalse(committed.json()['full_reconciliation'])
        self.assertEqual((committed.json()['auto_income'], committed.json()['auto_paid'], committed.json()['auto_fee']), (1, 1, 1))
        report, dash, movement, accounts = self.reports()
        month = int(self.date[5:7]) - 1
        self.assertEqual(report['totals'][month], '-420.00')
        self.assertEqual((dash['fact_in'], dash['fact_out']), ('200.00', '620.00'))
        self.assertEqual((dash['balance'], dash['bank_pending_debits'], dash['spendable_balance']),
                         ('999580.00', '75.00', '999505.00'))
        self.assertEqual(dash['forecast'][0]['opening'], '999505.00')
        self.assertEqual((movement['income_day'], movement['expense_day'], movement['closing'],
                          movement['bank_pending_debits'], movement['available']),
                         ('200.00', '620.00', '999580.00', '75.00', '999505.00'))
        account = next(a for a in accounts if a['id'] == self.acc)
        self.assertEqual((account['balance'], account['bank_pending_debits'], account['spendable_balance'],
                          account['unreconciled_statements']), ('999580.00', '75.00', '999505.00', 1))
        with unit() as s:
            state = funds_state(s, s.get(Account, self.acc), date.fromisoformat(self.date))
            self.assertEqual((state['actual'], state['bank_pending_debits'], state['available']),
                             (99958000, 7500, 99950500))
            self.assertEqual(s.get(PaymentRequest, rid).status, 'paid')
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 3)

        self.assertEqual(self.payment_confirmed(preview, self.cat).status_code, 409)
        fresh = self.client.post(f"/api/bank-statements/{preview['id']}/reprocess/preview",
                                 json={'fee_category_id': self.cat}, headers=self.h)
        self.assertEqual(fresh.status_code, 200, fresh.text)
        retried = self.client.post(f"/api/bank-statements/{preview['id']}/reprocess/commit",
                                  json={'fee_category_id': self.cat, 'preview_token': fresh.json()['preview_token'], 'confirm': True}, headers=self.h)
        self.assertIn(retried.status_code, (200, 409), retried.text)
        self.assertEqual(self.reports(), [report, dash, movement, accounts])
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 3)
            self.assertEqual(len(list(s.scalars(select(BankStatementBatch)))), 1)

    def test_generic_expense_and_transfer_cannot_spend_unposted_debit_hold(self):
        _, preview = self.partial_statement()
        self.assertEqual(self.payment_confirmed(preview, self.cat).status_code, 200)
        self.budget('2000000', mode='hard')
        before = self.reports()
        expense = self.ledger('999550', 'out', reference='SYN-BLOCK-HELD')
        self.assertEqual(expense.status_code, 409, expense.text)
        self.assertIn('непроведённых списаний', expense.text)
        target = self.post('/api/accounts', {'name': 'Synthetic cash target', 'kind': 'cash', 'currency': 'UZS',
                            'opening': '0', 'opening_date': self.date})
        self.assertEqual(target.status_code, 200, target.text)
        transfer = self.ledger('999550', 'transfer', to_account_id=target.json()['id'], reference='SYN-BLOCK-TRANSFER')
        self.assertEqual(transfer.status_code, 409, transfer.text)
        self.assertIn('непроведённых списаний', transfer.text)
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 3)
            self.assertEqual(account_balance(s, s.get(Account, self.acc)), 99958000)
        self.assertEqual(self.reports()[0]['totals'], before[0]['totals'])

    def test_historical_partial_updates_its_month_and_keeps_current_money_hold(self):
        current = date.fromisoformat(self.date)
        historical = current.replace(day=1) - timedelta(days=1)
        with unit(True) as s:
            s.get(Account, self.acc).opening_date = historical - timedelta(days=1)
        # No request is needed for a recognized external expense; the other
        # outgoing line is intentionally an unresolved own-account movement.
        raw = self.statement(False, external=True).replace(b'<td>300000005</td>', b'<td>300000001</td>')
        raw = raw.replace(current.strftime('%d.%m.%Y').encode('ascii'), historical.strftime('%d.%m.%Y').encode('ascii'))
        preview = self.preview(raw, self.cat)
        self.assertEqual(preview.status_code, 200, preview.text)
        committed = self.confirmed(preview.json(), self.cat)
        self.assertEqual(committed.status_code, 200, committed.text)
        report, dash, movement, _ = self.reports()
        self.assertEqual(report['totals'][historical.month - 1], '180.00')
        self.assertEqual(report['totals'][current.month - 1], '0.00')
        self.assertEqual((dash['fact_in'], dash['fact_out'], dash['bank_pending_debits']), ('0.00', '0.00', '75.00'))
        self.assertEqual((movement['income_day'], movement['expense_day'], movement['available']),
                         ('0.00', '0.00', '1000105.00'))
        with unit() as s:
            before = funds_state(s, s.get(Account, self.acc), historical - timedelta(days=1))
            self.assertEqual(before['bank_pending_debits'], 0)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(PartialBankFactTests(name) for name in PartialBankFactTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
