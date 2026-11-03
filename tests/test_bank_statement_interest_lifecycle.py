"""Interest follows ordinary statement ownership and duplicate resolution; synthetic only."""
import unittest
from datetime import date
from sqlalchemy import select

from tests.test_bank_statement_delete import StatementDeleteTests
from tests.test_bank_statement_charge_integration import BankChargeIntegrationTests
from tests.test_bank_statement_duplicates import DuplicateResolutionTests
from tests.test_bank_statement_unallocated_out import UnallocatedOutTests
from app.bank_statement_unallocated_out import classify_unallocated_out, process_unallocated_out
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.db import Account, Category, Ledger, engine, unit
from app.services import account_balance, deleted_ledger_ids


class InterestDeleteTests(StatementDeleteTests):
    charges_source = BankChargeIntegrationTests.charges_source

    def import_charges(self, raw=None):
        preview = self.preview(raw or self.charges_source())
        self.assertEqual(preview.status_code, 200, preview.text)
        posted = self.confirmed(preview.json())
        self.assertEqual(posted.status_code, 200, posted.text)
        self.assertTrue(posted.json()['full_reconciliation'])
        return preview.json()['id']

    def test_delete_reverses_owned_fees_and_interest_and_restores_balance(self):
        bid = self.import_charges()
        with unit() as s:
            interest_ids = {line.ledger_id for line in s.scalars(select(BankStatementLine).where(
                BankStatementLine.batch_id == bid, BankStatementLine.status == 'auto_interest_posted'))}
            self.assertEqual(len(interest_ids), 2)
        item = self.item(bid)
        self.assertEqual((item['operation_count'], item['linked_count']), (4, 0))
        deleted = self.remove(item)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()['removed_operations'], 4)
        with unit() as s:
            s.info['company_id'] = self.company
            self.assertEqual(s.get(BankStatementBatch, bid).status, 'deleted')
            self.assertTrue(interest_ids <= deleted_ledger_ids(s))
            reversals = list(s.scalars(select(Ledger).where(Ledger.reversal_of.in_(interest_ids))))
            self.assertEqual(len(reversals), 2)
            self.assertEqual(account_balance(s, s.get(Account, self.acc)), 100000000)

    def test_delete_cancels_created_interest_and_preserves_preexisting_manual_interest(self):
        with unit(True) as s:
            article = Category(company_id=self.company, code='63', name='Loan interest', type='outcome', activity='financing')
            s.add(article); s.flush()
            account = s.get(Account, self.acc)
            manual = Ledger(account_id=self.acc, category_id=article.id, kind='out', amount=3000,
                date=date.fromisoformat(self.date), counterparty='Synthetic bank',
                reference='SYN-INTEREST-1', note='Synthetic preexisting manual interest', creator_id=account.created_by)
            s.add(manual); s.flush(); manual_id = manual.id
        # A distinct second amount avoids the intentionally conservative
        # same-amount/manual-ledger ambiguity check for the second bank row.
        raw = self.charges_source()
        raw = b'<td>31.00</td>'.join(raw.rsplit(b'<td>30.00</td>', 1))
        raw = raw.replace(b'999900.00', b'999899.00').replace(b'<td>100.00</td>', b'<td>101.00</td>')
        bid = self.import_charges(raw)
        with unit() as s:
            rows = list(s.scalars(select(BankStatementLine).where(BankStatementLine.batch_id == bid)))
            self.assertEqual(sum(row.status == 'interest_matched' for row in rows), 1)
            self.assertEqual(sum(row.status == 'auto_interest_posted' for row in rows), 1)
        item = self.item(bid)
        self.assertEqual((item['operation_count'], item['linked_count']), (3, 1))
        result = self.remove(item)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['removed_operations'], 3)
        with unit() as s:
            s.info['company_id'] = self.company
            self.assertNotIn(manual_id, deleted_ledger_ids(s))
            self.assertIsNone(s.scalar(select(Ledger.id).where(Ledger.reversal_of == manual_id)))
            self.assertEqual(account_balance(s, s.get(Account, self.acc)), 99997000)

    def test_changed_created_interest_invalidates_the_delete_snapshot(self):
        bid = self.import_charges()
        item = self.item(bid)
        with unit(True) as s:
            line = s.scalar(select(BankStatementLine).where(BankStatementLine.batch_id == bid,
                BankStatementLine.status == 'auto_interest_posted'))
            s.get(Ledger, line.ledger_id).note = 'Synthetic changed interest after preview'
        result = self.remove(item)
        self.assertEqual(result.status_code, 409, result.text)
        with unit() as s:
            self.assertEqual(s.get(BankStatementBatch, bid).status, 'committed')
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 4)


class InterestDuplicateTests(DuplicateResolutionTests):
    def test_both_posted_and_existing_interest_are_exact_duplicate_candidates_without_new_ledger(self):
        for status in ('auto_interest_posted', 'interest_matched'):
            with self.subTest(status=status):
                with unit(True) as s:
                    entry = s.get(Ledger, self.entry_id); entry.kind = 'out'
                    for lid in (self.prior_id, self.target_id):
                        line = s.get(BankStatementLine, lid); line.direction = 'out'; line.classification = 'bank_interest'
                        batch = s.get(BankStatementBatch, line.batch_id)
                        batch.closing = batch.opening - entry.amount; batch.credit_total = 0; batch.debit_total = entry.amount
                    prior = s.get(BankStatementLine, self.prior_id); prior.status = status
                    target = s.get(BankStatementLine, self.target_id)
                    target.status = 'ambiguous_duplicate'; target.uncertain = True; target.ledger_id = None; target.include_in_daily = True
                candidates = self.candidates()
                self.assertEqual(candidates.status_code, 200, candidates.text)
                self.assertEqual(len(candidates.json()['items']), 1)
                resolved = self.resolve(candidates.json()['items'][0])
                self.assertEqual(resolved.status_code, 200, resolved.text)
                with unit() as s:
                    self.assertEqual(len(list(s.scalars(select(Ledger)))), 1)
                    self.assertEqual(s.get(BankStatementLine, self.target_id).ledger_id, self.entry_id)


class InterestPriorDebitTests(UnallocatedOutTests):
    def test_proven_prior_interest_is_distinct_from_a_new_document_but_reversal_still_blocks(self):
        with unit(True) as s:
            old, prior, account, user, category = self.prepare_out(s, status='committed')
            self.assertEqual(process_unallocated_out(s, old, prior, account, user, category.id)['auto_outgoing_posted'], 1)
            original = prior[0]
            batch = BankStatementBatch(company_id=self.company, account_id=account.id, created_by=user.id,
                filename='new-synthetic.xls', sha256='d'*64, account_number=old.account_number, owner_name=old.owner_name,
                owner_inn=old.owner_inn, currency='UZS', period_from=old.period_from, period_to=old.period_to,
                opening=old.opening, closing=old.closing, debit_total=original.amount, credit_total=0,
                balance_matches=True, declared_totals_match=True, source_ready=True, status='preview')
            s.add(batch); s.flush()
            line = BankStatementLine(company_id=self.company, batch_id=batch.id, account_id=account.id, line=6,
                date=original.date, direction='out', amount=original.amount, document_number='DISTINCT-DOC',
                correspondent_mfo=original.correspondent_mfo, correspondent_account=original.correspondent_account,
                correspondent_name=original.correspondent_name, correspondent_inn=original.correspondent_inn,
                purpose='Synthetic goods payment', fingerprint='e'*64, occurrence=1, status='review',
                include_in_daily=True, uncertain=False)
            s.add(line); s.flush()
            for status in ('auto_interest_posted', 'interest_matched'):
                original.status = status; original.classification = 'bank_interest'; s.flush()
                self.assertEqual(classify_unallocated_out(s, batch, [line], account, user, category.id)[line.id]['status'], 'outgoing_candidate')
            entry = s.get(Ledger, original.ledger_id)
            s.add(Ledger(account_id=account.id, category_id=category.id, kind='in', amount=entry.amount,
                date=entry.date, counterparty=entry.counterparty, reference='SYN-INTEREST-REV',
                note='Synthetic reversal of prior interest', reversal_of=entry.id, creator_id=user.id))
            s.flush()
            self.assertEqual(classify_unallocated_out(s, batch, [line], account, user, category.id)[line.id]['status'], 'review')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(cls(name) for cls in (InterestDeleteTests, InterestDuplicateTests, InterestPriorDebitTests)
                              for name in cls.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
