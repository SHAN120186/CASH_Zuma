"""Read-only bank history, role boundaries and live ledger status (synthetic data)."""
import json
import unittest
from sqlalchemy import select
from tests.test_bank_statement_auto_integration import AutoBankIntegrationTests
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.db import Account, Company, Ledger, PaymentRequest, Setting, User, engine, unit


class StatementHistoryTests(AutoBankIntegrationTests):
    def posted(self, payment=False):
        if payment:self.approved()
        preview=self.preview(self.statement(payment),self.cat)
        self.assertEqual(preview.status_code,200,preview.text)
        committed=self.confirmed(preview.json(),self.cat)
        self.assertEqual(committed.status_code,200,committed.text)
        self.assertTrue(committed.json()['full_reconciliation'])
        return preview.json()['id']

    def test_history_contains_only_confirmed_company_scoped_statements_and_is_read_only(self):
        preview=self.preview(self.statement(False),self.cat)
        self.assertEqual(preview.status_code,200,preview.text)
        empty=self.client.get('/api/bank-statements/history',headers=self.h)
        self.assertEqual(empty.status_code,200,empty.text);self.assertEqual(empty.json()['items'],[])
        self.assertEqual(self.client.get(f"/api/bank-statements/history/{preview.json()['id']}",headers=self.h).status_code,404)
        saved=self.confirmed(preview.json(),self.cat);self.assertEqual(saved.status_code,200,saved.text)
        before=self.client.get('/api/bank-statements/history',headers=self.h)
        self.assertEqual(before.status_code,200,before.text);item=before.json()['items'][0]
        self.assertEqual((item['id'],item['credit_total'],item['debit_total'],item['account']['currency']),
                         (preview.json()['id'],'200.00','20.00','UZS'))
        self.assertFalse(before.json()['has_more'])
        detail=self.client.get(f"/api/bank-statements/history/{item['id']}",headers=self.h)
        self.assertEqual(detail.status_code,200,detail.text)
        self.assertEqual([r['operation_state'] for r in detail.json()['rows']],['active','active'])
        with unit() as s:
            entries=list(s.scalars(select(Ledger)));self.assertEqual(len(entries),2)
            foreign=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
        h={**self.h,'X-Company-ID':str(foreign)}
        other=self.client.get('/api/bank-statements/history',headers=h)
        self.assertEqual(other.status_code,200,other.text);self.assertEqual(other.json()['items'],[])
        self.assertEqual(self.client.get(f"/api/bank-statements/history/{item['id']}",headers=h).status_code,404)
        with unit(True) as s:s.get(BankStatementBatch,item['id']).status='deleted'
        self.assertEqual(self.client.get(f"/api/bank-statements/history/{item['id']}",headers=self.h).status_code,404)
        self.assertEqual(self.client.get('/api/bank-statements/history',headers=self.h).json()['items'],[])
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))),2)

    def test_history_is_available_to_existing_statement_roles_only(self):
        bid=self.posted()
        accountant,ah=self.accountant
        self.assertEqual(accountant.get('/api/bank-statements/history',headers=ah).status_code,200)
        self.assertEqual(accountant.get(f'/api/bank-statements/history/{bid}',headers=ah).status_code,200)
        finance,fh=self.make_user('finance','synthetic_finance_history')
        try:
            self.assertEqual(finance.get('/api/bank-statements/history',headers=fh).status_code,200)
            self.assertEqual(finance.get(f'/api/bank-statements/history/{bid}',headers=fh).status_code,200)
        finally:finance.close()
        viewer,vh=self.make_user('material_accountant','synthetic_readonly_history')
        try:
            self.assertEqual(viewer.get('/api/bank-statements/history',headers=vh).status_code,403)
            self.assertEqual(viewer.get(f'/api/bank-statements/history/{bid}',headers=vh).status_code,403)
        finally:viewer.close()
        self.assertEqual(self.requester[0].get('/api/bank-statements/history',headers=self.requester[1]).status_code,403)
        self.assertEqual(self.client.get('/api/bank-statements/history?page=0',headers=self.h).status_code,422)
        self.assertEqual(self.client.get('/api/bank-statements/history?page_size=51',headers=self.h).status_code,422)

    def test_history_paginates_without_duplicate_batches(self):
        bid=self.posted()
        with unit(True) as s:
            original=s.get(BankStatementBatch,bid)
            for i in range(3):
                copy=BankStatementBatch(company_id=original.company_id,account_id=original.account_id,
                    created_by=original.created_by,filename=f'synthetic-{i}.xls',sha256=str(i)*64,
                    account_number=original.account_number,owner_name=original.owner_name,owner_inn=original.owner_inn,
                    currency=original.currency,period_from=original.period_from,period_to=original.period_to,
                    opening=original.opening,closing=original.closing,debit_total=original.debit_total,
                    credit_total=original.credit_total,balance_matches=True,declared_totals_match=True,
                    source_ready=True,status='committed')
                s.add(copy)
        first=self.client.get('/api/bank-statements/history?page_size=2&page=1',headers=self.h).json()
        second=self.client.get('/api/bank-statements/history?page_size=2&page=2',headers=self.h).json()
        self.assertTrue(first['has_more']);self.assertFalse(second['has_more'])
        self.assertEqual(len({row['id'] for row in first['items']+second['items']}),4)

    def test_nonposted_rows_and_reversed_or_changed_operations_are_not_active(self):
        preview=self.preview(self.statement(False),None)
        self.assertEqual(preview.status_code,200,preview.text)
        done=self.confirmed(preview.json(),None,token=False);self.assertEqual(done.status_code,200,done.text)
        bid=preview.json()['id']
        detail=self.client.get(f'/api/bank-statements/history/{bid}',headers=self.h)
        self.assertTrue(all(row['operation_state']=='unposted' for row in detail.json()['rows']))
        retry=self.client.post(f'/api/bank-statements/{bid}/reprocess/preview',headers=self.h,json={'fee_category_id':self.cat})
        self.assertEqual(retry.status_code,200,retry.text)
        commit=self.client.post(f'/api/bank-statements/{bid}/reprocess/commit',headers=self.h,
              json={'fee_category_id':self.cat,'preview_token':retry.json()['preview_token'],'confirm':True})
        self.assertEqual(commit.status_code,200,commit.text)
        with unit(True) as s:
            incoming=s.scalar(select(Ledger).where(Ledger.kind=='in'))
            outgoing=s.scalar(select(Ledger).where(Ledger.kind=='out'))
            incoming.amount+=1
            s.add(Ledger(account_id=outgoing.account_id,category_id=outgoing.category_id,kind='in',amount=outgoing.amount,
                        date=outgoing.date,counterparty=outgoing.counterparty,reference='SYNTH-HISTORY-REV',
                        note='Synthetic reversal for history state',reversal_of=outgoing.id,creator_id=outgoing.creator_id))
        detail=self.client.get(f'/api/bank-statements/history/{bid}',headers=self.h)
        self.assertEqual(detail.status_code,200,detail.text)
        self.assertEqual([r['operation_state'] for r in detail.json()['rows']],['changed','reversed'])

    def test_hidden_request_section_does_not_disclose_request_number(self):
        bid=self.posted(True)
        with unit(True) as s:
            user=s.scalar(select(User).where(User.username=='bank_synthetic_accountant'))
            s.merge(Setting(key=f'sections:{self.company}:{user.id}',
                            value=json.dumps({'version':1,'sections':{'requests':'hidden'}})))
        detail=self.accountant[0].get(f'/api/bank-statements/history/{bid}',headers=self.accountant[1])
        self.assertEqual(detail.status_code,200,detail.text)
        self.assertTrue(all(row['request_id'] is None for row in detail.json()['rows']))


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(StatementHistoryTests(name) for name in StatementHistoryTests.__dict__ if name.startswith('test_'))

def tearDownModule():
    engine.dispose()
