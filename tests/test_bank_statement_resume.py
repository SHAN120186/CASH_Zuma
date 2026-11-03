"""Saved bank drafts use stored synthetic rows and never post during reopening."""
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.test_bank_statement_auto_integration import AutoBankIntegrationTests
from app.bank_statement_api import DRAFT_REFRESH_ACTION
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.db import Account, Audit, Budget, Category, Company, CompanyUser, Ledger, PaymentRequest, Receipt, Setting, User, engine, unit
from app.main import app
from app.services import account_balance


class SavedStatementTests(AutoBankIntegrationTests):
    def resume(self, bid, category=None, client=None, headers=None):
        return (client or self.client).post(f'/api/bank-statements/{bid}/resume',
            json={'fee_category_id': category}, headers=headers or self.h)

    def saved(self, client=None, headers=None, **params):
        return (client or self.client).get('/api/bank-statements/previews',
            params=params, headers=headers or self.h)

    def finance_snapshot(self):
        with unit() as s:
            return {model.__tablename__: [tuple(getattr(row, col.key) for col in model.__table__.columns)
                    for row in s.scalars(select(model).order_by(*model.__mapper__.primary_key))]
                    for model in (Account, Ledger, PaymentRequest, Receipt, Budget, Category, Setting)}

    def test_saved_draft_survives_new_browser_and_resume_preserves_source_and_finances(self):
        rid = self.approved()
        cl, headers = self.accountant
        original = self.preview(self.statement(), self.cat, cl, headers).json()
        bid = original['id']
        before = self.finance_snapshot()
        with unit() as s:
            batch = s.get(BankStatementBatch, bid)
            identity = (batch.id, batch.sha256, batch.created_at, batch.created_by, batch.filename)
            row_ids = list(s.scalars(select(BankStatementLine.id).where(BankStatementLine.batch_id == bid)))
            original_audits = len(list(s.scalars(select(Audit).where(Audit.entity == 'bank_statement'))))
        with TestClient(app) as reloaded:
            reloaded.cookies.update(cl.cookies)
            with patch('app.bank_statement_api.parse_bank_statement', side_effect=AssertionError('No upload required')):
                with patch('app.bank_statement_api._classify', side_effect=AssertionError('GET cannot reclassify')):
                    listing = self.saved(reloaded, headers)
                    detail = reloaded.get(f'/api/bank-statements/{bid}', headers=headers)
                self.assertEqual(listing.status_code, 200, listing.text)
                self.assertEqual(detail.status_code, 200, detail.text)
                self.assertEqual(listing.json()['items'][0]['id'], bid)
                self.assertEqual(listing.json()['items'][0]['account']['currency'], 'UZS')
                self.assertEqual(listing.json()['items'][0]['debit_total'], '620.00')
                with unit() as s:
                    self.assertEqual(len(list(s.scalars(select(Audit).where(Audit.entity == 'bank_statement')))), original_audits)
                reopened = self.resume(bid, self.cat, reloaded, headers)
        self.assertEqual(reopened.status_code, 200, reopened.text)
        result = reopened.json()
        self.assertEqual(set(result), set(original))
        self.assertEqual(result['rows'], original['rows'])
        self.assertEqual(result['statement'], original['statement'])
        self.assertEqual((result['id'], result['status'], result['full_reconciliation']), (bid, 'preview', True))
        self.assertNotEqual(result['preview_token'], original['preview_token'])
        self.assertEqual(self.finance_snapshot(), before)
        with unit() as s:
            batch = s.get(BankStatementBatch, bid)
            self.assertEqual((batch.id, batch.sha256, batch.created_at, batch.created_by, batch.filename), identity)
            self.assertEqual(list(s.scalars(select(BankStatementLine.id).where(BankStatementLine.batch_id == bid))), row_ids)
            self.assertEqual(len(list(s.scalars(select(BankStatementBatch)))), 1)
            self.assertEqual(s.get(PaymentRequest, rid).status, 'approved')
            self.assertIsNotNone(s.scalar(select(Audit.id).where(Audit.action == DRAFT_REFRESH_ACTION)))

    def test_resume_rotates_token_and_old_or_missing_confirmation_cannot_write(self):
        preview = self.preview(self.statement(False), self.cat).json()
        first = self.resume(preview['id'], self.cat).json()
        second = self.resume(preview['id'], self.cat).json()
        self.assertEqual(len({p['preview_token'] for p in (preview, first, second)}), 3)
        before = self.finance_snapshot()
        for old in (preview, first):
            denied = self.confirmed(old, self.cat)
            self.assertEqual(denied.status_code, 409, denied.text)
        self.assertEqual(self.confirmed(second, token=False).status_code, 409)
        self.assertEqual(self.finance_snapshot(), before)
        done = self.confirmed(second, self.cat)
        self.assertEqual(done.status_code, 200, done.text)
        self.assertTrue(done.json()['full_reconciliation'])
        self.assertEqual(self.resume(second['id'], self.cat).status_code, 409)
        self.assertEqual(self.confirmed(second, self.cat).status_code, 409)
        self.assertEqual(self.saved().json()['items'], [])
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 2)
            self.assertEqual(account_balance(s, s.get(Account, self.acc)), 100018000)

    def test_list_ownership_admin_company_pagination_and_status_filter(self):
        cl, headers = self.accountant
        own = self.preview(self.statement(False), self.cat, cl, headers).json()['id']
        admin = self.preview(self.statement(False) + b' ', self.cat).json()['id']
        other, oh = self.make_user('accountant', 'synthetic_other_saved')
        try:
            self.assertEqual([p['id'] for p in self.saved(cl, headers).json()['items']], [own])
            self.assertEqual(self.saved(other, oh).json()['items'], [])
            self.assertEqual(self.resume(own, self.cat, other, oh).status_code, 403)
            self.assertEqual(self.resume(admin, self.cat, cl, headers).status_code, 403)
            opened = self.resume(own, self.cat)
            self.assertEqual(opened.status_code, 200, opened.text)
            # Admin can inspect another creator's draft, but its signature is personal.
            self.assertEqual(self.confirmed(opened.json(), self.cat, cl, headers).status_code, 409)
            first = self.saved(page_size=1).json()
            second = self.saved(page_size=1, page=2).json()
            self.assertTrue(first['has_more']);self.assertFalse(second['has_more'])
            self.assertEqual({first['items'][0]['id'], second['items'][0]['id']}, {own, admin})
            with unit() as s:foreign = s.scalar(select(Company.id).where(Company.code == 'ZUMA'))
            fh = {**self.h, 'X-Company-ID': str(foreign)}
            self.assertEqual(self.saved(headers=fh).json()['items'], [])
            self.assertEqual(self.resume(own, self.cat, headers=fh).status_code, 404)
            with unit(True) as s:s.get(BankStatementBatch, admin).status = 'deleted'
            self.assertEqual(self.resume(admin).status_code, 404)
            self.assertEqual([p['id'] for p in self.saved().json()['items']], [own])
            self.assertEqual(self.resume(999999).status_code, 404)
            self.assertEqual(self.saved(page=0).status_code, 422)
            self.assertEqual(self.saved(page_size=51).status_code, 422)
        finally:other.close()

    def test_permissions_csrf_validation_and_current_request_visibility(self):
        rid = self.approved()
        cl, headers = self.accountant
        preview = self.preview(self.statement(), self.cat, cl, headers).json()
        bid = preview['id']
        self.assertEqual(cl.post(f'/api/bank-statements/{bid}/resume', json={}).status_code, 403)
        self.assertEqual(cl.post(f'/api/bank-statements/{bid}/resume', headers=headers,
                                json={'fee_category_id': 0}).status_code, 422)
        self.assertEqual(cl.post(f'/api/bank-statements/{bid}/resume', headers=headers,
                                json={'confirm': True}).status_code, 422)
        self.assertEqual(self.saved(self.requester[0], self.requester[1]).status_code, 403)
        self.assertEqual(self.resume(bid, client=self.requester[0], headers=self.requester[1]).status_code, 403)
        with unit(True) as s:
            user = s.scalar(select(User).where(User.username == 'bank_synthetic_accountant'))
            uid = user.id
            s.merge(Setting(key=f'sections:{self.company}:{uid}',
                value=json.dumps({'version': 1, 'sections': {'requests': 'hidden'}})))
        before = self.finance_snapshot()
        hidden = self.resume(bid, self.cat, cl, headers)
        self.assertEqual(hidden.status_code, 200, hidden.text)
        self.assertTrue(all(row['request_id'] is None and row.get('request_version') is None
                            for row in hidden.json()['rows']))
        self.assertEqual(self.confirmed(preview, self.cat, cl, headers).status_code, 409)
        self.assertEqual(self.finance_snapshot(), before)
        with unit() as s:self.assertEqual(s.get(PaymentRequest, rid).status, 'approved')
        with unit(True) as s:
            s.delete(s.get(Setting, f'sections:{self.company}:{uid}'))
            s.get(CompanyUser, (self.company, uid)).role = 'employee'
        self.assertEqual(self.saved(cl, headers).status_code, 403)
        self.assertEqual(self.resume(bid, self.cat, cl, headers).status_code, 403)

    def test_stale_request_approval_version_rejected_after_resume(self):
        rid = self.approved()
        preview = self.preview(self.statement(), self.cat).json()
        current = self.resume(preview['id'], self.cat).json()
        with unit(True) as s:s.get(PaymentRequest, rid).version += 1
        before = self.finance_snapshot()
        denied = self.confirmed(current, self.cat)
        self.assertEqual(denied.status_code, 409, denied.text)
        self.assertEqual(self.finance_snapshot(), before)

    def test_archived_kind_currency_and_bank_binding_conflicts_do_not_modify_draft(self):
        preview = self.preview(self.statement(False), self.cat).json()
        bid = preview['id']
        for field, value, original in [('archived', True, False), ('kind', 'cash', 'bank'), ('currency', 'USD', 'UZS')]:
            with self.subTest(field=field):
                with unit(True) as s:setattr(s.get(Account, self.acc), field, value)
                before = self.finance_snapshot()
                response = self.resume(bid, self.cat)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(self.finance_snapshot(), before)
                with unit(True) as s:setattr(s.get(Account, self.acc), field, original)
        with unit(True) as s:
            original = s.get(BankStatementBatch, bid)
            values = {c.key: getattr(original, c.key) for c in BankStatementBatch.__table__.columns
                      if c.key not in ('id', 'created_at')}
            values.update(status='committed', sha256='d' * 64, account_number='20200000000000000009')
            s.add(BankStatementBatch(**values))
        before = self.finance_snapshot()
        conflict = self.resume(bid, self.cat)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertIn('связан', conflict.json()['detail'])
        self.assertEqual(self.finance_snapshot(), before)

    def test_unready_saved_source_opens_for_inspection_and_still_cannot_commit(self):
        preview = self.preview(self.statement(False), self.cat).json()
        with unit(True) as s:s.get(BankStatementBatch, preview['id']).source_ready = False
        before = self.finance_snapshot()
        current = self.resume(preview['id'], self.cat)
        self.assertEqual(current.status_code, 200, current.text)
        self.assertFalse(current.json()['statement']['ready'])
        self.assertFalse(current.json()['full_reconciliation'])
        self.assertEqual(self.confirmed(current.json(), self.cat).status_code, 409)
        self.assertEqual(self.finance_snapshot(), before)

    def test_repeat_upload_reuses_batch_rotates_token_and_different_creator_cannot_recommit(self):
        raw = self.statement(False)
        first = self.preview(raw, self.cat).json()
        repeated = self.preview(raw, self.cat).json()
        self.assertEqual(first['id'], repeated['id'])
        self.assertNotEqual(first['preview_token'], repeated['preview_token'])
        self.assertEqual(self.confirmed(first, self.cat).status_code, 409)
        cl, headers = self.accountant
        other = self.preview(raw, self.cat, cl, headers).json()
        current = self.resume(first['id'], self.cat).json()
        self.assertEqual(self.confirmed(current, self.cat).status_code, 200)
        self.assertEqual(self.resume(other['id'], self.cat, cl, headers).status_code, 409)
        self.assertEqual(self.confirmed(other, self.cat, cl, headers).status_code, 409)
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(BankStatementBatch)))), 2)
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 2)

    def test_overlapping_file_refresh_shows_ambiguity_and_never_duplicates_financial_fact(self):
        raw = self.statement(False)
        first = self.preview(raw, self.cat).json()
        overlapping = self.preview(raw + b' ', self.cat).json()
        self.assertEqual(self.confirmed(first, self.cat).status_code, 200)
        current = self.resume(overlapping['id'], self.cat)
        self.assertEqual(current.status_code, 200, current.text)
        self.assertFalse(current.json()['full_reconciliation'])
        self.assertTrue(all(row['uncertain'] for row in current.json()['rows']))
        self.assertEqual(self.confirmed(overlapping, self.cat).status_code, 409)
        confirmed = self.confirmed(current.json(), self.cat)
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertFalse(confirmed.json()['full_reconciliation'])
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 2)
            self.assertEqual(account_balance(s, s.get(Account, self.acc)), 100018000)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(SavedStatementTests(name) for name in SavedStatementTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
