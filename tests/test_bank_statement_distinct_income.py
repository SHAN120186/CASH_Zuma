"""Explicit repeated-document receipts, isolated synthetic records only."""
import unittest
from datetime import date, timedelta
from sqlalchemy import select, func

import tests.test_site as base
from app.main import app
from app.bank_statement_distinct_income import router
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.bank_statement_guard import set_statement_reconciliation, unreconciled_statements
from app.db import Account, Category, Company, Ledger, Receipt, Setting, User, engine, unit
from app.services import account_balance

if not any(getattr(route, 'path', '') == '/api/bank-statements/lines/{id}/distinct-income/options' for route in app.routes):
    app.include_router(router)


class DistinctIncomeTests(base.SiteTests):
    def prepared(self):
        with unit(True) as s:
            account = s.get(Account, self.acc)
            user = s.scalar(select(User).where(User.username == 'admin'))
            day = date.fromisoformat(self.date)
            batch = BankStatementBatch(company_id=self.company, account_id=self.acc, created_by=user.id,
                filename='synthetic-distinct.xls', sha256='a'*64, account_number='20200000000000000001',
                owner_name='Synthetic company', owner_inn='300000001', currency='UZS', period_from=day, period_to=day,
                opening=account.opening, closing=account.opening+30000, debit_total=0, credit_total=30000,
                balance_matches=True, declared_totals_match=True, source_ready=True, status='committed')
            s.add(batch); s.flush(); self.bid = batch.id
            self.ids = []
            for n, amount in ((1, 10000), (2, 20000)):
                line = BankStatementLine(company_id=self.company, batch_id=batch.id, account_id=self.acc, line=5+n,
                    date=day, direction='in', amount=amount, document_number='SYN-REPEATED-DOC', correspondent_mfo='00014',
                    correspondent_account='20200000000000000002', correspondent_name='Synthetic buyer', correspondent_inn='300000002',
                    purpose=f'Synthetic distinct contract {n}', fingerprint=str(n)*64, occurrence=1,
                    status='review', reason='Repeated bank document needs explicit evidence', include_in_daily=True, uncertain=False)
                s.add(line); s.flush(); self.ids.append(line.id)
            s.info['company_id'] = self.company
            set_statement_reconciliation(s, batch, False, user)
        return {'distinct_from_line_id': self.ids[1], 'reason': 'Synthetic bank evidence for two distinct contracts', 'attest_bank_fact': True}

    def url(self, action, line_id=None):
        return f'/api/bank-statements/lines/{line_id or self.ids[0]}/distinct-income/{action}'

    def signed(self, body, line_id=None):
        result = self.post(self.url('preview', line_id), body)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def apply(self, body, shown, line_id=None):
        return self.post(self.url('commit', line_id), {**body, 'token': shown['token'], 'confirm': True})

    def test_two_explicit_distinct_receipts_post_once_with_fresh_second_preview(self):
        body = self.prepared()
        with unit() as s:
            before = (s.scalar(select(func.count(Category.id))), s.scalar(select(func.count(Setting.key))))
        options = self.client.get(self.url('options'), headers=self.h)
        self.assertEqual(options.status_code, 200, options.text)
        self.assertEqual([item['line_id'] for item in options.json()['items']], [self.ids[1]])
        first = self.signed(body)
        second_body = {**body, 'distinct_from_line_id': self.ids[0]}
        stale_second = self.signed(second_body, self.ids[1])
        with unit() as s:
            self.assertEqual((s.scalar(select(func.count(Category.id))), s.scalar(select(func.count(Setting.key)))), before)
            self.assertIsNone(s.scalar(select(Ledger.id)))
        done = self.apply(body, first)
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(done.json()['status'], 'manual_distinct_income')
        self.assertEqual(self.apply(second_body, stale_second, self.ids[1]).status_code, 409)
        second = self.signed(second_body, self.ids[1])
        done_second = self.apply(second_body, second, self.ids[1])
        self.assertEqual(done_second.status_code, 200, done_second.text)
        self.assertEqual(self.apply(body, first).status_code, 409)
        with unit() as s:
            s.info['company_id'] = self.company
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 2)
            self.assertEqual(account_balance(s, s.get(Account, self.acc)), 100030000)
            self.assertEqual(unreconciled_statements(s, s.get(Account, self.acc)), [])

    def test_admin_attestation_and_changed_source_or_peer_are_required(self):
        body = self.prepared()
        finance = self.make_user('finance', 'synthetic_distinct_finance'); self.addCleanup(finance[0].close)
        self.assertEqual(finance[0].get(self.url('options'), headers=finance[1]).status_code, 403)
        self.assertEqual(self.post(self.url('preview'), {**body, 'attest_bank_fact': False}).status_code, 422)
        shown = self.signed(body)
        with unit(True) as s:
            s.get(BankStatementLine, self.ids[1]).purpose = 'Synthetic changed contract after preview'
        self.assertEqual(self.apply(body, shown).status_code, 409)
        with unit() as s:
            foreign = s.scalar(select(Company.id).where(Company.code == 'ZUMA'))
        self.assertEqual(self.client.get(self.url('options'), headers={**self.h, 'X-Company-ID': str(foreign)}).status_code, 404)
        with unit() as s:
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_same_fingerprint_and_cross_statement_overlap_cannot_be_attested_distinct(self):
        body = self.prepared()
        with unit(True) as s:
            peer = s.get(BankStatementLine, self.ids[1]); peer.fingerprint = '1'*64
        self.assertEqual(self.post(self.url('preview'), body).status_code, 409)
        with unit(True) as s:
            s.get(BankStatementLine, self.ids[1]).fingerprint = '2'*64
            source = s.get(BankStatementBatch, self.bid)
            other = BankStatementBatch(**{column.key: getattr(source, column.key) for column in source.__table__.columns if column.key != 'id'})
            other.sha256 = 'c'*64; s.add(other); s.flush()
            line = s.get(BankStatementLine, self.ids[0])
            copy = BankStatementLine(**{column.key: getattr(line, column.key) for column in line.__table__.columns if column.key != 'id'})
            copy.batch_id = other.id; copy.fingerprint = 'd'*64; s.add(copy)
        self.assertEqual(self.post(self.url('preview'), body).status_code, 409)

    def test_manual_journal_with_mistaken_nearby_date_or_reversal_blocks_reposting(self):
        body = self.prepared()
        with unit(True) as s:
            account = s.get(Account, self.acc)
            old = Ledger(account_id=self.acc, category_id=self.cat, kind='in', amount=10000,
                date=date.fromisoformat(self.date)-timedelta(days=1), counterparty='Synthetic buyer',
                reference='SYN-OTHER-DATE', note='Synthetic manual incoming with wrong date', creator_id=account.created_by)
            s.add(old); s.flush(); old_id = old.id
        self.assertEqual(self.post(self.url('preview'), body).status_code, 409)
        with unit(True) as s:
            old = s.get(Ledger, old_id); old.date = date.fromisoformat(self.date); old.reference = 'BANK-'+'1'*64
            s.add(Ledger(account_id=self.acc, category_id=self.cat, kind='out', amount=10000, date=old.date,
                counterparty=old.counterparty, reference='SYN-DISTINCT-REV', note='Synthetic reversal',
                creator_id=old.creator_id, reversal_of=old.id))
        self.assertEqual(self.post(self.url('preview'), body).status_code, 409)

    def test_nearby_received_or_cancelled_plan_never_leaves_a_false_forecast(self):
        body = self.prepared()
        with unit(True) as s:
            article = s.scalar(select(Category).where(Category.type == 'income', Category.company_id == self.company))
            account = s.get(Account, self.acc)
            plan = Receipt(account_id=self.acc, category_id=article.id, counterparty='Synthetic buyer',
                inn='300000002', amount=10000, due_date=date.fromisoformat(self.date)+timedelta(days=1),
                status='expected', creator_id=account.created_by, note='Synthetic nearby expected receipt')
            s.add(plan); s.flush(); plan_id = plan.id
        for status in ('expected', 'received', 'cancelled'):
            with unit(True) as s:
                s.get(Receipt, plan_id).status = status
            self.assertEqual(self.post(self.url('preview'), body).status_code, 409, status)
        with unit() as s:
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_exact_unique_expected_plan_is_received_through_normal_posting(self):
        body = self.prepared()
        with unit(True) as s:
            article = s.scalar(select(Category).where(Category.type == 'income', Category.company_id == self.company))
            account = s.get(Account, self.acc)
            plan = Receipt(account_id=self.acc, category_id=article.id, counterparty='Synthetic buyer',
                inn='300000002', amount=10000, due_date=date.fromisoformat(self.date), status='expected',
                creator_id=account.created_by, note='Synthetic exact expected receipt')
            s.add(plan); s.flush(); plan_id = plan.id
        done = self.apply(body, self.signed(body))
        self.assertEqual(done.status_code, 200, done.text)
        with unit() as s:
            self.assertEqual(s.get(Receipt, plan_id).status, 'received')
            self.assertEqual(s.get(Ledger, done.json()['ledger_id']).receipt_id, plan_id)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(DistinctIncomeTests(name) for name in DistinctIncomeTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
