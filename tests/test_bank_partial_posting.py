"""Signed partial bank accounting and conservative pending debit holds (synthetic)."""
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from sqlalchemy import select

from tests.test_bank_statement_auto_integration import AutoBankIntegrationTests
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.bank_statement_guard import pending_bank_debit_hold, unreconciled_statements
from app.db import Account, Category, Company, Ledger, Setting, User, engine, unit
from app.services import account_balance, post_ledger


class PartialPostingTests(AutoBankIntegrationTests):
    def partial_statement(self, include_payment=False):
        raw = self.statement(include_payment)
        day = date.fromisoformat(self.date).strftime('%d.%m.%Y')
        unresolved = (f'<tr><td>{day}</td><td>PENDING-OWN</td><td>00014</td>'
            '<td>20200000000000000099</td><td>Synthetic own uncertain movement</td>'
            '<td>300000001</td><td>Synthetic own transfer needs evidence</td><td>50.00</td><td></td></tr>').encode('cp1251')
        closing, new_closing = (b'999580.00', b'999530.00') if include_payment else (b'1000180.00', b'1000130.00')
        debit, new_debit = ('620.00', '670.00') if include_payment else ('20.00', '70.00')
        return raw.replace(closing, new_closing).replace(
            f'<tr><td>Итоговый оборот</td><td>{debit}'.encode('cp1251'),
            unresolved + f'<tr><td>Итоговый оборот</td><td>{new_debit}'.encode('cp1251'))

    def partial_preview(self, raw=None):
        result = self.preview(raw or self.partial_statement(), self.cat)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def recheck(self, bid):
        result = self.client.post(f'/api/bank-statements/{bid}/reprocess/preview',
            json={'fee_category_id': self.cat}, headers=self.h)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def reprocess(self, bid, checked):
        return self.client.post(f'/api/bank-statements/{bid}/reprocess/commit', headers=self.h,
            json={'fee_category_id': self.cat, 'preview_token': checked['preview_token'], 'confirm': True})

    def assert_hold(self, expected):
        with unit() as s:
            s.info['company_id'] = self.company
            self.assertEqual(pending_bank_debit_hold(s, s.get(Account, self.acc)), expected)

    def test_verified_income_and_fee_post_while_own_transfer_waits_and_retries_are_idempotent(self):
        checked = self.partial_preview()
        summary = checked['posting_summary']
        self.assertFalse(checked['full_reconciliation'])
        self.assertTrue(summary['partial_allowed'])
        self.assertEqual(summary['ready'], {'count': 2, 'credit_total': '200.00', 'debit_total': '20.00'})
        self.assertEqual(summary['pending'], {'count': 1, 'credit_total': '0.00', 'debit_total': '50.00'})
        self.assertEqual((summary['projected_closing'], summary['conservative_closing']), ('1000180.00', '1000130.00'))
        self.assertEqual([r['posting_state'] for r in checked['rows']], ['ready', 'ready', 'pending'])
        self.assert_hold(0)  # A preview does not create a financial hold or operation.
        result = self.confirmed(checked, self.cat)
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        self.assertFalse(data['full_reconciliation']);self.assertTrue(data['partial_reconciliation'])
        self.assertEqual((data['auto_income'], data['auto_fee']), (1, 1))
        self.assertEqual(data['posting_summary']['posted'], summary['ready'])
        self.assert_hold(5000)
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 2)
            self.assertEqual(account_balance(s, s.get(Account, self.acc)), 100018000)
            pending = s.scalar(select(BankStatementLine).where(BankStatementLine.document_number == 'PENDING-OWN'))
            self.assertEqual((pending.status, pending.ledger_id), ('review', None))
        retry = self.reprocess(checked['id'], self.recheck(checked['id']))
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()['posted'], 0)
        self.assertFalse(retry.json()['full_reconciliation'])
        self.assert_hold(5000)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))), 2)

    def test_legacy_confirmation_does_not_broaden_and_signed_reprocess_posts_verified_subset(self):
        checked = self.partial_preview()
        legacy = self.confirmed(checked, token=False)
        self.assertEqual(legacy.status_code, 200, legacy.text)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))), 0)
        self.assert_hold(7000)
        recheck = self.recheck(checked['id'])
        self.assertTrue(recheck['posting_summary']['partial_allowed'])
        result = self.reprocess(checked['id'], recheck)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['posted'], 2)
        self.assertTrue(result.json()['partial_reconciliation'])
        self.assert_hold(5000)
        self.assertEqual(self.reprocess(checked['id'], recheck).status_code, 409)

    def test_resolving_last_debit_proves_bank_closing_and_releases_only_its_marker(self):
        checked = self.partial_preview()
        self.assertEqual(self.confirmed(checked, self.cat).status_code, 200)
        with unit() as s:
            line = s.scalar(select(BankStatementLine).where(BankStatementLine.document_number == 'PENDING-OWN'))
            lid = line.id
        reviewed = self.accountant[0].post(f'/api/bank-statements/lines/{lid}/review', headers=self.accountant[1],
            json={'classification': 'bank_fee', 'note': 'Synthetic accountant independently verified actual bank fee'})
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        recheck = self.recheck(checked['id'])
        self.assertTrue(recheck['full_reconciliation'])
        result = self.reprocess(checked['id'], recheck)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['full_reconciliation'])
        self.assertEqual(result.json()['posted'], 1)
        self.assert_hold(0)
        with unit() as s:
            s.info['company_id'] = self.company
            self.assertEqual(unreconciled_statements(s, s.get(Account, self.acc)), [])
            self.assertEqual(account_balance(s, s.get(Account, self.acc)), 100013000)
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 3)

    def test_late_budget_rejection_keeps_failed_fee_pending_without_rolling_back_verified_receipt(self):
        self.budget('10', mode='hard')
        checked = self.partial_preview()
        result = self.confirmed(checked, self.cat)
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        self.assertEqual((data['auto_income'], data['auto_fee']), (1, 0))
        self.assertEqual(data['posting_summary']['pending'], {'count': 2, 'credit_total': '0.00', 'debit_total': '70.00'})
        self.assertEqual(data['posting_summary']['posted']['credit_total'], '200.00')
        self.assert_hold(7000)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))), 1)

    def test_unexplained_ledger_difference_blocks_partial_and_a_late_difference_rolls_back_selected_subset(self):
        checked = self.partial_preview()
        original = post_ledger
        def with_unexpected_entry(s, user, data, **kwargs):
            entry = original(s, user, data, **kwargs)
            s.add(Ledger(account_id=self.acc, category_id=self.cat, kind='in', amount=1,
                date=date.fromisoformat(self.date), counterparty='Synthetic unexpected extra entry',
                reference='SYNTH-PARTIAL-MISMATCH', note='Synthetic rollback proof', creator_id=user.id))
            s.flush()
            return entry
        with patch('app.bank_statement_incoming.post_ledger', side_effect=with_unexpected_entry):
            result = self.confirmed(checked, self.cat)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse(result.json()['full_reconciliation'])
        self.assertEqual(result.json()['posting_summary']['posted']['count'], 0)
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 0)
            self.assertEqual(len(list(s.scalars(select(Category).where(Category.activity == 'unclassified')))), 0)
        self.assert_hold(7000)

    def test_existing_unexplained_ledger_difference_moves_all_candidates_to_pending(self):
        with unit(True) as s:
            user = s.scalar(select(User).where(User.username == 'admin'))
            s.add(Ledger(account_id=self.acc, category_id=self.cat, kind='in', amount=1,
                date=date.fromisoformat(self.date), counterparty='Synthetic prior unmatched movement',
                reference='SYNTH-PARTIAL-EXISTING', note='Synthetic accounting difference', creator_id=user.id))
        checked = self.partial_preview()
        self.assertFalse(checked['posting_summary']['partial_allowed'])
        self.assertFalse(checked['posting_summary']['closing_matches'])
        self.assertEqual(checked['posting_summary']['ready']['count'], 0)
        self.assertEqual(checked['posting_summary']['pending']['count'], 3)
        self.assertTrue(all(row['posting_state'] == 'pending' for row in checked['rows']))
        result = self.confirmed(checked, self.cat)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['posting_summary']['posted']['count'], 0)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))), 1)
        self.assert_hold(7000)

    def test_history_counts_only_active_created_rows_and_keeps_reversed_receipt_pending(self):
        checked = self.partial_preview()
        self.assertEqual(self.confirmed(checked, self.cat).status_code, 200)
        with unit(True) as s:
            entry = s.scalar(select(Ledger).where(Ledger.kind == 'in'))
            s.add(Ledger(account_id=self.acc, category_id=entry.category_id, kind='out', amount=entry.amount,
                date=entry.date, counterparty=entry.counterparty, reference='SYNTH-PARTIAL-REVERSE',
                note='Synthetic reversal to verify active history totals', creator_id=entry.creator_id, reversal_of=entry.id))
        history = self.client.get(f"/api/bank-statements/history/{checked['id']}", headers=self.h)
        self.assertEqual(history.status_code, 200, history.text)
        summary = history.json()['posting_summary']
        self.assertEqual(summary['scope'], 'history')
        self.assertEqual(summary['posted'], {'count': 1, 'credit_total': '0.00', 'debit_total': '20.00'})
        self.assertEqual(summary['linked'], summary['posted'])
        self.assertEqual(summary['pending'], {'count': 2, 'credit_total': '200.00', 'debit_total': '50.00'})
        self.assertFalse(history.json()['full_reconciliation'])

    def test_globally_blocked_exact_request_stays_reachable_for_later_signed_reprocess(self):
        rid = self.approved()
        with unit(True) as s:
            user = s.scalar(select(User).where(User.username == 'admin'))
            extra = Ledger(account_id=self.acc, category_id=self.cat, kind='in', amount=1,
                date=date.fromisoformat(self.date), counterparty='Synthetic prior unexplained entry',
                reference='SYNTH-PARTIAL-APPROVED-DIFF', note='Synthetic correction before recheck', creator_id=user.id)
            s.add(extra);s.flush();extra_id = extra.id
        checked = self.partial_preview(self.partial_statement(True))
        self.assertFalse(checked['posting_summary']['partial_allowed'])
        result = self.confirmed(checked, self.cat)
        self.assertEqual(result.status_code, 200, result.text)
        with unit(True) as s:
            payment = s.scalar(select(BankStatementLine).where(BankStatementLine.document_number == 'PAY001'))
            self.assertEqual((payment.status, payment.request_id, payment.ledger_id), ('review', None, None))
            s.delete(s.get(Ledger, extra_id))  # Remove only the synthetic setup discrepancy.
        rechecked = self.recheck(checked['id'])
        self.assertEqual(next(row for row in rechecked['rows'] if row['document_number'] == 'PAY001')['status'], 'auto_pay_candidate')
        result = self.reprocess(checked['id'], rechecked)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['posted'], 3)
        self.assert_hold(5000)

    def test_pending_hold_checks_dates_active_links_and_foreign_company(self):
        checked = self.partial_preview()
        self.assertEqual(self.confirmed(checked, self.cat).status_code, 200)
        day = date.fromisoformat(self.date)
        with unit(True) as s:
            pending = s.scalar(select(BankStatementLine).where(BankStatementLine.document_number == 'PENDING-OWN'))
            s.info['company_id'] = self.company
            account = s.get(Account, self.acc)
            self.assertEqual(pending_bank_debit_hold(s, account, day - timedelta(days=1)), 0)
            self.assertEqual(pending_bank_debit_hold(s, account, day), 5000)
            user = s.scalar(select(User).where(User.username == 'admin'))
            entry = Ledger(account_id=self.acc, category_id=self.cat, kind='out', amount=5000, date=day,
                counterparty='Synthetic accounted pending bank debit', reference='SYNTH-PARTIAL-TIE',
                note='Synthetic matching tie', creator_id=user.id)
            s.add(entry);s.flush();pending.ledger_id = entry.id;s.flush()
            self.assertEqual(pending_bank_debit_hold(s, account), 0)
            entry.amount = 4999;s.flush()
            self.assertEqual(pending_bank_debit_hold(s, account), 5000)
            s.info['company_id'] = s.scalar(select(Company.id).where(Company.code == 'ZUMA'))
            from fastapi import HTTPException
            with self.assertRaises(HTTPException):pending_bank_debit_hold(s, account)

    def test_transfer_tie_accounts_for_exact_source_side_but_duplicate_or_reversed_tie_stays_held(self):
        checked = self.partial_preview()
        self.assertEqual(self.confirmed(checked, self.cat).status_code, 200)
        with unit(True) as s:
            s.info['company_id'] = self.company
            account = s.get(Account, self.acc)
            pending = s.scalar(select(BankStatementLine).where(BankStatementLine.document_number == 'PENDING-OWN'))
            user = s.scalar(select(User).where(User.username == 'admin'))
            other = Account(company_id=self.company, name='Synthetic transfer destination', kind='bank', currency='UZS',
                opening=0, opening_date=date.fromisoformat(self.date), created_by=user.id)
            s.add(other);s.flush()
            entry = Ledger(account_id=self.acc, to_account_id=other.id, category_id=None, kind='transfer', amount=pending.amount,
                date=pending.date, counterparty='Synthetic own account transfer', reference='SYNTH-PARTIAL-TRANSFER',
                note='Synthetic matching transfer side', creator_id=user.id)
            s.add(entry);s.flush();pending.ledger_id = entry.id;s.flush()
            self.assertEqual(pending_bank_debit_hold(s, account), 0)
            values = {c.key: getattr(pending, c.key) for c in BankStatementLine.__table__.columns if c.key != 'id'}
            values.update(document_number='PENDING-OTHER', fingerprint='e' * 64, line=pending.line + 1)
            duplicate = BankStatementLine(**values);s.add(duplicate);s.flush()
            self.assertEqual(pending_bank_debit_hold(s, account), 10000)
            duplicate.ledger_id = None;s.flush()
            self.assertEqual(pending_bank_debit_hold(s, account), 5000)
            s.add(Ledger(account_id=other.id, to_account_id=self.acc, category_id=None, kind='transfer', amount=entry.amount,
                date=entry.date, counterparty='Synthetic reversal', reference='SYNTH-PARTIAL-TRANSFER-REV',
                note='Synthetic reversed matching transfer', creator_id=user.id, reversal_of=entry.id))
            s.flush()
            self.assertEqual(pending_bank_debit_hold(s, account), 10000)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(PartialPostingTests(name) for name in PartialPostingTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
