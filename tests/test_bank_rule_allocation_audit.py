"""Adversarial lifecycle checks for category-only saved-bank allocation."""
import json
import unittest

from sqlalchemy import select

from tests.test_bank_statement_rule_allocation import ExistingRuleAllocationTests
from app.bank_statement_api import _ledger_edit_token
from app.bank_statement_models import BankStatementLine
from app.db import Ledger, Setting, User, unit


class RuleAllocationAuditTests(ExistingRuleAllocationTests):
    def source_entry(self, batch, document='OTH001'):
        with unit() as s:
            line = s.scalar(select(BankStatementLine).where(
                BankStatementLine.batch_id == batch,
                BankStatementLine.document_number == document))
            entry = s.get(Ledger, line.ledger_id)
            return entry.id, entry.category_id, _ledger_edit_token(entry)

    def test_journal_edit_invalidates_whole_allocation_before_any_category_changes(self):
        batch = self.posted(); self.rules(); preview = self.review_rules(batch)
        expense, original, _ = self.source_entry(batch)
        with unit(True) as s:
            s.get(Ledger, expense).note = 'Synthetic journal correction after allocation preview'
        before = self.ledger_snapshot()
        self.assertEqual(self.apply_rules(batch, preview).status_code, 409)
        self.assertEqual(self.ledger_snapshot(), before)
        with unit() as s:
            self.assertEqual(s.get(Ledger, expense).category_id, original)
            income = self.source_entry(batch, 'INC001')[0]
            self.assertNotEqual(s.get(Ledger, income).category_id, self.income_category)

    def test_reversed_fact_is_not_reclassified_and_other_partial_fact_can_be(self):
        batch = self.posted(); self.rules(); previous = self.review_rules(batch)
        expense, original, _ = self.source_entry(batch)
        reversed_result = self.post(f'/api/ledger/{expense}/reverse',
            {'reason':'Synthetic incorrect supplier payment reversed'})
        self.assertEqual(reversed_result.status_code, 200, reversed_result.text)
        before = self.ledger_snapshot()
        self.assertEqual(self.apply_rules(batch, previous).status_code, 409)
        fresh = self.review_rules(batch)
        self.assertNotIn(expense, [row['ledger_id'] for row in fresh['rows']])
        self.assertEqual(fresh['ready_count'], 1)
        applied = self.apply_rules(batch, fresh)
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()['allocated'], 1)
        self.assertEqual(self.ledger_snapshot(), before)
        with unit() as s:self.assertEqual(s.get(Ledger, expense).category_id, original)

    def test_deleted_fact_stays_excluded_and_old_confirmation_cannot_restore_it(self):
        batch = self.posted(); self.rules(); previous = self.review_rules(batch)
        expense, original, token = self.source_entry(batch)
        deleted = self.client.request('DELETE', f'/api/ledger/{expense}', headers=self.h,
            json={'expected':token, 'reason':'Synthetic duplicate supplier payment deleted'})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        before = self.ledger_snapshot()
        self.assertEqual(self.apply_rules(batch, previous).status_code, 409)
        fresh = self.review_rules(batch)
        self.assertNotIn(expense, [row['ledger_id'] for row in fresh['rows']])
        applied = self.apply_rules(batch, fresh)
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(self.ledger_snapshot(), before)
        with unit() as s:self.assertEqual(s.get(Ledger, expense).category_id, original)

    def test_revoked_ledger_edit_access_rejects_saved_payer_confirmation(self):
        batch = self.posted(); self.rules()
        payer, headers = self.accountant
        previous = self.review_rules(batch, payer, headers)
        before = self.ledger_snapshot()
        with unit(True) as s:
            user = s.scalar(select(User).where(User.username == 'bank_synthetic_accountant'))
            s.add(Setting(key=f'sections:{self.company}:{user.id}',
                value=json.dumps({'version':1, 'sections':{'ledger':'view'}})))
        result = payer.post(f'/api/bank-statements/{batch}/category-rules/commit', headers=headers,
            json={'token':previous['token'], 'confirm':True})
        self.assertEqual(result.status_code, 403, result.text)
        self.assertEqual(self.ledger_snapshot(), before)
        with unit() as s:
            self.assertNotEqual(s.get(Ledger, self.source_entry(batch)[0]).category_id, self.cat)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(RuleAllocationAuditTests(name)
        for name in RuleAllocationAuditTests.__dict__ if name.startswith('test_'))
