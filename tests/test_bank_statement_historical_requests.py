"""Historical closed-request exceptions use synthetic, isolated bank facts only."""
import unittest
from datetime import date
from sqlalchemy import select

import tests.test_site as base
from app.main import app
from app.bank_statement_historical_requests import router, HISTORICAL_ACTION
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.bank_statement_guard import set_statement_reconciliation
from app.db import Account, Audit, Company, Ledger, PaymentRequest, User, engine, unit
from app.services import account_balance

if not any(getattr(route, 'path', '') == '/api/bank-statements/lines/{id}/historical-request/options' for route in app.routes):
    app.include_router(router)


class HistoricalRequestTests(base.SiteTests):
    def prepared(self):
        created = self.post('/api/requests', {**self.request_body('100'), 'counterparty_inn': '300000002'})
        self.assertEqual(created.status_code, 200, created.text)
        self.rid = created.json()['id']
        closed = self.post(f'/api/requests/{self.rid}/decision', {'action': 'close', 'note': 'Synthetic closed without payment'})
        self.assertEqual(closed.status_code, 200, closed.text)
        with unit(True) as s:
            account = s.get(Account, self.acc)
            user = s.scalar(select(User).where(User.username == 'admin'))
            day = date.fromisoformat(self.date)
            batch = BankStatementBatch(company_id=self.company, account_id=self.acc, created_by=user.id,
                filename='synthetic-historical.xls', sha256='a'*64, account_number='20200000000000000001',
                owner_name='Synthetic company', owner_inn='300000001', currency='UZS', period_from=day, period_to=day,
                opening=account.opening, closing=account.opening-10000, debit_total=10000, credit_total=0,
                balance_matches=True, declared_totals_match=True, source_ready=True, status='committed')
            s.add(batch); s.flush()
            line = BankStatementLine(company_id=self.company, batch_id=batch.id, account_id=self.acc, line=6,
                date=day, direction='out', amount=10000, document_number='SYN-HISTORY', correspondent_mfo='00014',
                correspondent_account='20200000000000000002', correspondent_name='Supplier', correspondent_inn='300000002',
                purpose=f'Synthetic bank fact CF-{self.rid:05d}', fingerprint='b'*64, occurrence=1,
                status='review', reason='Closed request needs explicit bank fact', include_in_daily=True, uncertain=False)
            s.add(line); s.flush(); self.lid, self.bid = line.id, batch.id
            s.info['company_id'] = self.company
            set_statement_reconciliation(s, batch, False, user)
        return {'request_id': self.rid, 'reason': 'Synthetic bank evidence checked independently', 'attest_bank_fact': True}

    def url(self, action):
        return f'/api/bank-statements/lines/{self.lid}/historical-request/{action}'

    def preview_fact(self, body):
        result = self.post(self.url('preview'), body)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_fact_pays_closed_request_without_fabricated_approval_and_reversal_restores_closed(self):
        body = self.prepared()
        before = self.row(self.rid)
        options = self.client.get(self.url('options'), headers=self.h)
        self.assertEqual(options.status_code, 200, options.text)
        self.assertEqual(options.json()['items'][0]['request_id'], self.rid)
        shown = self.preview_fact(body)
        with unit() as s:
            self.assertIsNone(s.scalar(select(Ledger.id)))
            self.assertEqual(s.get(PaymentRequest, self.rid).status, 'cancelled')
        committed = self.post(self.url('commit'), {**body, 'token': shown['token'], 'confirm': True})
        self.assertEqual(committed.status_code, 200, committed.text)
        self.assertEqual(committed.json()['status'], 'historical_request_paid')
        after = self.row(self.rid)
        self.assertEqual(after['status'], 'paid')
        for key in ('approved_by', 'finance_approved_by', 'checked_by', 'date'):
            self.assertEqual(after[key], before[key])
        with unit() as s:
            entry = s.get(Ledger, committed.json()['ledger_id'])
            req = s.get(PaymentRequest, self.rid)
            self.assertEqual(entry.date, date.fromisoformat(self.date))
            self.assertIsNone(req.approved_at)
            self.assertIsNone(req.route_at)
            self.assertTrue(s.scalar(select(Audit.id).where(Audit.action == HISTORICAL_ACTION, Audit.entity == 'ledger')))
        self.assertEqual(self.post(self.url('commit'), {**body, 'token': shown['token'], 'confirm': True}).status_code, 409)
        # Linked-payment reference metadata may be corrected later. Its immutable
        # ledger/request/company identity still restores the actual old status.
        with unit(True) as s:
            s.get(Ledger, committed.json()['ledger_id']).reference = 'SYN-CORRECTED-HISTORICAL-REF'
        reversed_result = self.post(f"/api/ledger/{committed.json()['ledger_id']}/reverse", {'reason': 'Synthetic historical fact reversal'})
        self.assertEqual(reversed_result.status_code, 200, reversed_result.text)
        self.assertEqual(self.row(self.rid)['status'], 'cancelled')

    def test_statement_delete_restores_original_closed_state(self):
        body = self.prepared(); shown = self.preview_fact(body)
        committed = self.post(self.url('commit'), {**body, 'token': shown['token'], 'confirm': True})
        self.assertEqual(committed.status_code, 200, committed.text)
        preview = self.client.get(f'/api/bank-statements/{self.bid}/delete-preview', headers=self.h)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()['operation_count'], 1)
        removed = self.post(f'/api/bank-statements/{self.bid}/delete', {'confirm': True,
            'expected': preview.json()['delete_token'], 'reason': 'Synthetic statement exception reversal'})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(self.row(self.rid)['status'], 'cancelled')
        with unit() as s:
            self.assertEqual(account_balance(s, s.get(Account, self.acc)), 100000000)

    def test_admin_attestation_stale_and_company_guards(self):
        body = self.prepared()
        finance = self.make_user('finance', 'synthetic_exception_finance')
        self.addCleanup(finance[0].close)
        self.assertEqual(finance[0].get(self.url('options'), headers=finance[1]).status_code, 403)
        self.assertEqual(self.post(self.url('preview'), {**body, 'attest_bank_fact': False}).status_code, 422)
        shown = self.preview_fact(body)
        self.assertEqual(self.post(self.url('commit'), {**body, 'token': shown['token'], 'confirm': False}).status_code, 422)
        with unit(True) as s:
            s.get(PaymentRequest, self.rid).purpose = 'Synthetic correction after signed preview'
        self.assertEqual(self.post(self.url('commit'), {**body, 'token': shown['token'], 'confirm': True}).status_code, 409)
        with unit() as s:
            foreign = s.scalar(select(Company.id).where(Company.code == 'ZUMA'))
        self.assertEqual(self.client.get(self.url('options'), headers={**self.h, 'X-Company-ID': str(foreign)}).status_code, 404)
        with unit() as s:
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_exact_duplicate_and_existing_or_reversed_journal_block_exception(self):
        body = self.prepared()
        with unit(True) as s:
            line = s.get(BankStatementLine, self.lid)
            other = BankStatementLine(**{column.key: getattr(line, column.key) for column in line.__table__.columns if column.key != 'id'})
            other.line = 7; s.add(other); s.flush(); duplicate_id = other.id
        self.assertEqual(self.post(self.url('preview'), body).status_code, 409)
        with unit(True) as s:
            s.delete(s.get(BankStatementLine, duplicate_id))
            account = s.get(Account, self.acc)
            old = Ledger(account_id=self.acc, category_id=self.cat, kind='out', amount=10000,
                date=date.fromisoformat(self.date), counterparty='Supplier', reference='BANK-'+'b'*64,
                note='Synthetic old reversed bank fact', creator_id=account.created_by)
            s.add(old); s.flush()
            s.add(Ledger(account_id=self.acc, category_id=self.cat, kind='in', amount=10000,
                date=old.date, counterparty=old.counterparty, reference='SYN-HISTORY-REV', reversal_of=old.id,
                note='Synthetic reversal', creator_id=old.creator_id))
        self.assertEqual(self.post(self.url('preview'), body).status_code, 409)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(HistoricalRequestTests(name) for name in HistoricalRequestTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
