"""Synthetic end-to-end accounting across all three bank exceptions."""
import unittest
from datetime import date, timedelta

from sqlalchemy import select

from tests import test_site as base
from app.bank_statement_guard import set_statement_reconciliation, unreconciled_statements, pending_bank_debit_hold
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.db import Account, Ledger, PaymentRequest, User, now, unit
from app.services import account_balance


def tearDownModule():
    base.engine.dispose()


class BankDisputeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.SiteTests('runTest')
        self.fixture.setUp()
        self.client, self.h = self.fixture.client, self.fixture.h
        self.acc, self.company = self.fixture.acc, self.fixture.company
        self.day = date.fromisoformat(self.fixture.date)
        with unit(True) as s:
            admin = s.scalar(select(User).where(User.username == 'admin'))
            source = s.get(Account, self.acc)
            destination = Account(company_id=self.company, name='Synthetic own bank destination',
                kind='bank', currency='UZS', opening=0, opening_date=self.day-timedelta(days=1), created_by=admin.id)
            s.add(destination)
            req = PaymentRequest(creator_id=admin.id, category_id=self.fixture.cat, account_id=self.acc,
                counterparty='Synthetic supplier', counterparty_inn='300000008', purpose='Synthetic supplies',
                amount=10000, due_date=self.day, status='cancelled')
            s.add(req); s.flush()
            self.dest, self.req = destination.id, req.id
            batch = BankStatementBatch(company_id=self.company, account_id=self.acc, created_by=admin.id,
                filename='synthetic-disputes.xls', sha256='a'*64, account_number='20000000000000000001',
                owner_name='Synthetic company', owner_inn='300000001', currency='UZS', period_from=self.day,
                period_to=self.day, opening=source.opening, closing=source.opening-21000,
                debit_total=30000, credit_total=9000, balance_matches=True, declared_totals_match=True,
                source_ready=True, status='committed', committed_at=now())
            s.add(batch); s.flush(); self.batch = batch.id
            specs = [
                ('out', 20000, 'MOVE-1', '300000001', '20000000000000000002', 'Synthetic own bank', 'Own transfer'),
                ('out', 10000, 'PAY-1', '300000008', '20000000000000000008', 'Synthetic supplier', 'Supplies'),
                ('in', 4000, 'RECEIPT-1', '300000009', '20000000000000000009', 'Synthetic customer', 'Contract alpha'),
                ('in', 5000, 'RECEIPT-1', '300000009', '20000000000000000009', 'Synthetic customer', 'Contract beta'),
            ]
            self.lines = []
            for idx, (direction, amount, document, inn, number, name, purpose) in enumerate(specs):
                line = BankStatementLine(company_id=self.company, batch_id=batch.id, account_id=self.acc,
                    line=idx+6, date=self.day, direction=direction, amount=amount, document_number=document,
                    correspondent_mfo='00014', correspondent_account=number, correspondent_name=name,
                    correspondent_inn=inn, purpose=purpose, fingerprint=str(idx+1)*64, occurrence=1,
                    status='review', reason='', include_in_daily=True, uncertain=False)
                s.add(line); s.flush(); self.lines.append(line.id)
            set_statement_reconciliation(s, batch, False, admin)

    def tearDown(self):
        self.fixture.tearDown()

    def post(self, path, body):
        return self.client.post(path, json=body, headers=self.h)

    def resolve(self, kind, line, **body):
        endpoint = f'/api/bank-statements/lines/{line}/{kind}'
        data = {'reason': 'Synthetic verified historical bank evidence', **body}
        preview = self.post(endpoint+'/preview', data)
        self.assertEqual(preview.status_code, 200, preview.text)
        token_key = 'preview_token' if kind == 'historical-transfer' else 'token'
        commit_data = {**data, token_key: preview.json()[token_key]}
        if kind == 'historical-transfer':
            commit_data.update(confirm_bank_fact=True, confirm_ownership=True)
        else:
            commit_data.update(confirm=True)
        done = self.post(endpoint+'/commit', commit_data)
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(self.post(endpoint+'/commit', commit_data).status_code, 409)
        return done.json()

    def resolve_all(self):
        self.resolve('distinct-income', self.lines[2], distinct_from_line_id=self.lines[3], attest_bank_fact=True)
        self.resolve('distinct-income', self.lines[3], distinct_from_line_id=self.lines[2], attest_bank_fact=True)
        self.resolve('historical-request', self.lines[1], request_id=self.req, attest_bank_fact=True)
        return self.resolve('historical-transfer', self.lines[0], to_account_id=self.dest)

    def test_all_exceptions_close_reconciliation_once_and_preserve_cashflow(self):
        self.resolve_all()
        history = self.client.get(f'/api/bank-statements/history/{self.batch}', headers=self.h)
        self.assertEqual(history.status_code, 200, history.text)
        result = history.json()
        self.assertTrue(result['full_reconciliation'], history.text)
        self.assertEqual(result['posting_summary']['pending']['count'], 0)
        self.assertEqual(result['posting_summary']['posted']['count'], 4)
        self.assertEqual({row['operation_state'] for row in result['rows']}, {'active'})
        with unit() as s:
            entries = list(s.scalars(select(Ledger)))
            self.assertEqual(len(entries), 4)
            self.assertEqual(len({entry.reference for entry in entries}), 4)
            self.assertEqual(account_balance(s, s.get(Account, self.acc)), 99979000)
            self.assertEqual(account_balance(s, s.get(Account, self.dest)), 20000)
            req = s.get(PaymentRequest, self.req)
            self.assertEqual(req.status, 'paid')
            self.assertIsNone(req.approved_at)
            self.assertIsNone(req.finance_approved_by)
            self.assertFalse(unreconciled_statements(s, s.get(Account, self.acc)))
        report = self.client.get('/api/report', headers=self.h, params={
            'company_id':self.company, 'currency':'UZS', 'year':self.day.year, 'as_of':str(self.day)})
        self.assertEqual(report.status_code, 200, report.text)
        self.assertEqual(report.json()['totals'][self.day.month-1], '-10.00')
        self.assertEqual(report.json()['closing'][self.day.month-1], '999990.00')
        dash = self.client.get('/api/dashboard', headers=self.h, params={'currency':'UZS'})
        self.assertEqual(dash.status_code, 200, dash.text)
        self.assertEqual((dash.json()['fact_in'],dash.json()['fact_out']),('90.00','100.00'))
        self.assertEqual(dash.json()['bank_pending_debits'], '0.00')

    def test_statement_delete_reverses_owned_transfer_and_restores_closed_request(self):
        self.resolve_all()
        preview = self.client.get(f'/api/bank-statements/{self.batch}/delete-preview', headers=self.h)
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(preview.json()['operation_count'],4)
        removed = self.post(f'/api/bank-statements/{self.batch}/delete', {
            'confirm':True,'reason':'Synthetic reversal of an imported bank fact','expected':preview.json()['delete_token']})
        self.assertEqual(removed.status_code,200,removed.text)
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest,self.req).status,'cancelled')
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),100000000)
            self.assertEqual(account_balance(s,s.get(Account,self.dest)),0)
            self.assertEqual(s.get(BankStatementBatch,self.batch).status,'deleted')

    def test_delete_protects_later_statement_on_transfer_destination(self):
        self.resolve_all()
        with unit(True) as s:
            admin=s.scalar(select(User).where(User.username=='admin'))
            batch=BankStatementBatch(company_id=self.company,account_id=self.dest,created_by=admin.id,
                filename='synthetic-dependent.xls',sha256='b'*64,account_number='20000000000000000002',
                owner_name='Synthetic company',owner_inn='300000001',currency='UZS',
                period_from=self.day+timedelta(days=1),period_to=self.day+timedelta(days=1),
                opening=20000,closing=20000,debit_total=0,credit_total=0,balance_matches=True,
                declared_totals_match=True,source_ready=True,status='committed',committed_at=now())
            s.add(batch)
        preview=self.client.get(f'/api/bank-statements/{self.batch}/delete-preview',headers=self.h)
        removed=self.post(f'/api/bank-statements/{self.batch}/delete',{
            'confirm':True,'reason':'Synthetic dependency protection','expected':preview.json()['delete_token']})
        self.assertEqual(removed.status_code,409,removed.text)
        with unit() as s:
            self.assertEqual(s.get(BankStatementBatch,self.batch).status,'committed')
            self.assertEqual(s.get(PaymentRequest,self.req).status,'paid')
            self.assertEqual(account_balance(s,s.get(Account,self.dest)),20000)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),4)

    def test_direct_transfer_reversal_restores_pending_hold_and_payment_guard(self):
        done=self.resolve_all()
        reversed_entry=self.post(f"/api/ledger/{done['ledger_id']}/reverse",{
            'reason':'Synthetic correction of a historical transfer'})
        self.assertEqual(reversed_entry.status_code,200,reversed_entry.text)
        with unit() as s:
            source=s.get(Account,self.acc)
            self.assertTrue(unreconciled_statements(s,source))
            self.assertEqual(pending_bank_debit_hold(s,source),20000)
        history=self.client.get(f'/api/bank-statements/history/{self.batch}',headers=self.h)
        self.assertFalse(history.json()['full_reconciliation'])
        self.assertEqual(history.json()['posting_summary']['pending']['count'],1)
        self.assertEqual(history.json()['posting_summary']['pending']['debit_total'],'200.00')

    def test_direct_historical_request_delete_restores_closed_status_and_bank_hold(self):
        self.resolve_all()
        with unit() as s:
            entry=s.scalar(select(Ledger).where(Ledger.request_id==self.req))
            entry_id=entry.id
        ledger=self.client.get('/api/ledger',headers=self.h)
        self.assertEqual(ledger.status_code,200,ledger.text)
        listing=ledger.json()
        records=listing.get('items',[]) if isinstance(listing,dict) else listing
        record=next(item for item in records if item['id']==entry_id)
        deleted=self.client.request('DELETE',f'/api/ledger/{entry_id}',headers=self.h,json={
            'expected':record['edit_token'],'reason':'Synthetic correction of a historical payment'})
        self.assertEqual(deleted.status_code,200,deleted.text)
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest,self.req).status,'cancelled')
            self.assertEqual(pending_bank_debit_hold(s,s.get(Account,self.acc)),10000)
            self.assertTrue(unreconciled_statements(s,s.get(Account,self.acc)))


if __name__ == '__main__':
    unittest.main()
