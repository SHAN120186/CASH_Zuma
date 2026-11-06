"""Manual internal transfer reconciliation on isolated synthetic records."""
import unittest
from datetime import date, timedelta
from sqlalchemy import select

from tests.test_site import SiteTests
from app.bank_statement_transfers import router
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.bank_statement_guard import set_statement_reconciliation, unreconciled_statements
from app.db import Account, Audit, Company, Ledger, User, engine, now, unit
from app.main import app
from app.services import account_balance
from app.security import ADMIN_WORKFLOW_RIGHTS, perms_of

if not any(getattr(r, 'path', None) == '/api/bank-statements/lines/{id}/transfer-candidates' for r in app.routes):
    app.include_router(router)


class TransferLinkTests(SiteTests):
    def setUp(self):
        super().setUp()
        self.day=date.fromisoformat(self.date)
        with unit(True) as s:
            admin=s.scalar(select(User).where(User.username=='admin'))
            other=Account(company_id=self.company,name='Synthetic destination',kind='bank',currency='UZS',
                          opening=0,opening_date=self.day-timedelta(days=1),created_by=admin.id)
            s.add(other);s.flush();self.other=other.id
            entry=Ledger(account_id=self.acc,to_account_id=other.id,kind='transfer',amount=20000,date=self.day,
                         counterparty='',reference='SYN-TRANSFER',note='Synthetic transfer',creator_id=admin.id)
            s.add(entry);s.flush();self.entry=entry.id
            def batch(account,number,digest):
                return BankStatementBatch(company_id=self.company,account_id=account,created_by=admin.id,
                    filename='synthetic.xls',sha256=digest*64,account_number=number,owner_name='Synthetic Company',
                    owner_inn='300000001',currency='UZS',period_from=self.day,period_to=self.day,
                    opening=0,closing=0,debit_total=0,credit_total=0,balance_matches=True,
                    declared_totals_match=True,source_ready=True,status='committed',committed_at=now())
            debit=batch(self.acc,'20000000000000000001','a');credit=batch(other.id,'20000000000000000002','b')
            s.add_all([debit,credit]);s.flush();self.credit_batch=credit.id
            def line(batch_id,account,direction,other_number,line_no):
                return BankStatementLine(company_id=self.company,batch_id=batch_id,account_id=account,
                    line=line_no,date=self.day,direction=direction,amount=20000,document_number='SYN-1',
                    correspondent_mfo='00014',correspondent_account=other_number,correspondent_name='Synthetic',
                    correspondent_inn='300000001',purpose='Synthetic transfer',fingerprint=str(line_no)*64,
                    occurrence=1,status='review',reason='',include_in_daily=True,uncertain=False)
            out=line(debit.id,self.acc,'out',credit.account_number,6)
            incoming=line(credit.id,other.id,'in',debit.account_number,7)
            s.add_all([out,incoming]);s.flush();self.out=out.id;self.inc=incoming.id

    def candidates(self,line=None,client=None,headers=None):
        return (client or self.client).get(f'/api/bank-statements/lines/{line or self.out}/transfer-candidates',
                                            headers=headers or self.h)

    def link(self,item,line=None,client=None,headers=None,**overrides):
        data={'ledger_id':item['ledger_id'],'token':item['token'],
              'reason':'Проверена операция перевода','confirm':True};data.update(overrides)
        return (client or self.client).post(f'/api/bank-statements/lines/{line or self.out}/transfer-link',
                                             headers=headers or self.h,json=data)

    def test_link_both_opposite_sides_without_new_financial_operations(self):
        with unit() as s:
            admin=s.scalar(select(User).where(User.username=='admin'))
            self.assertTrue(ADMIN_WORKFLOW_RIGHTS.isdisjoint(perms_of(admin)))
            before=[account_balance(s,s.get(Account,i)) for i in (self.acc,self.other)]
        candidate=self.candidates();self.assertEqual(candidate.status_code,200,candidate.text)
        item=candidate.json()['items'][0];self.assertTrue(item['counterparty_account_confirmed'])
        self.assertEqual(item['to_account'],'Synthetic destination')
        done=self.link(item);self.assertEqual(done.status_code,200,done.text)
        opposite=self.candidates(self.inc);self.assertEqual(len(opposite.json()['items']),1)
        done2=self.link(opposite.json()['items'][0],self.inc);self.assertEqual(done2.status_code,200,done2.text)
        with unit() as s:
            self.assertEqual([account_balance(s,s.get(Account,i)) for i in (self.acc,self.other)],before)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),1)
            for line_id in (self.out,self.inc):
                line=s.get(BankStatementLine,line_id)
                self.assertEqual((line.status,line.ledger_id),('manual_transfer_match',self.entry))
            self.assertEqual(len(list(s.scalars(select(Audit).where(Audit.action=='Связан банковский перевод')))),2)
        self.assertEqual(self.link(item).status_code,409)

    def test_linked_transfer_allows_reconciliation_without_posting_again(self):
        with unit(True) as s:
            batch=s.get(BankStatementBatch,s.get(BankStatementLine,self.out).batch_id)
            account=s.get(Account,self.acc)
            batch.opening=account.opening;batch.closing=account.opening-20000
            batch.debit_total=20000;batch.credit_total=0
            inbound=s.get(BankStatementBatch,self.credit_batch)
            inbound.opening=0;inbound.closing=20000;inbound.credit_total=20000;inbound.debit_total=0
        item=self.candidates().json()['items'][0]
        result=self.link(item);self.assertEqual(result.status_code,200,result.text)
        in_item=self.candidates(self.inc).json()['items'][0]
        result=self.link(in_item,self.inc);self.assertEqual(result.status_code,200,result.text)
        with unit() as s:batch_id=s.get(BankStatementLine,self.out).batch_id
        p=self.client.post(f'/api/bank-statements/{batch_id}/reprocess/preview',headers=self.h,json={})
        self.assertEqual(p.status_code,200,p.text)
        self.assertTrue(p.json()['full_reconciliation'],p.text)
        applied=self.client.post(f'/api/bank-statements/{batch_id}/reprocess/commit',headers=self.h,
            json={'preview_token':p.json()['preview_token'],'confirm':True})
        self.assertEqual(applied.status_code,200,applied.text)
        self.assertTrue(applied.json()['full_reconciliation'],applied.text)
        p=self.client.post(f'/api/bank-statements/{self.credit_batch}/reprocess/preview',headers=self.h,json={})
        self.assertEqual(p.status_code,200,p.text);self.assertTrue(p.json()['full_reconciliation'],p.text)
        applied=self.client.post(f'/api/bank-statements/{self.credit_batch}/reprocess/commit',headers=self.h,
            json={'preview_token':p.json()['preview_token'],'confirm':True})
        self.assertEqual(applied.status_code,200,applied.text)
        self.assertTrue(applied.json()['full_reconciliation'],applied.text)
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))),1)
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),batch.closing)
            self.assertEqual(account_balance(s,s.get(Account,self.other)),20000)

    def test_linking_last_transfer_keeps_batch_reprocessable_until_guard_clears(self):
        with unit(True) as s:
            admin=s.scalar(select(User).where(User.username=='admin'))
            batch=s.get(BankStatementBatch,s.get(BankStatementLine,self.out).batch_id)
            account=s.get(Account,self.acc)
            batch.opening=account.opening;batch.closing=account.opening-20000
            batch.debit_total=20000;batch.credit_total=0;batch_id=batch.id
            older=BankStatementBatch(company_id=self.company,account_id=self.acc,created_by=admin.id,
                filename='synthetic-empty.xls',sha256='e'*64,account_number=batch.account_number,
                owner_name='Synthetic Company',owner_inn='300000001',currency='UZS',
                period_from=self.day-timedelta(days=1),period_to=self.day-timedelta(days=1),
                opening=account.opening,closing=account.opening,debit_total=0,credit_total=0,
                balance_matches=True,declared_totals_match=True,source_ready=True,status='committed',committed_at=now())
            s.add(older);s.flush();older_id=older.id
            set_statement_reconciliation(s,batch,False,admin)
            set_statement_reconciliation(s,older,False,admin)
        item=self.candidates().json()['items'][0]
        result=self.link(item);self.assertEqual(result.status_code,200,result.text)
        listing=self.client.get('/api/bank-statements/reprocess/batches',headers=self.h)
        self.assertEqual(listing.status_code,200,listing.text)
        self.assertIn(batch_id,[batch['id'] for batch in listing.json()['items']])
        p=self.client.post(f'/api/bank-statements/{batch_id}/reprocess/preview',headers=self.h,json={})
        self.assertEqual(p.status_code,200,p.text);self.assertTrue(p.json()['full_reconciliation'],p.text)
        done=self.client.post(f'/api/bank-statements/{batch_id}/reprocess/commit',headers=self.h,
            json={'preview_token':p.json()['preview_token'],'confirm':True})
        self.assertEqual(done.status_code,200,done.text);self.assertTrue(done.json()['full_reconciliation'])
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))),1)
            markers=unreconciled_statements(s,s.get(Account,self.acc))
            self.assertEqual(len(markers),1)
            self.assertTrue(markers[0].key.endswith(f':{older_id}'))
        listing=self.client.get('/api/bank-statements/reprocess/batches',headers=self.h)
        self.assertEqual(listing.status_code,200,listing.text)
        ids=[batch['id'] for batch in listing.json()['items']]
        self.assertNotIn(batch_id,ids);self.assertIn(older_id,ids)

    def test_existing_binding_must_match_and_unknown_binding_is_explained(self):
        with unit(True) as s:s.get(BankStatementLine,self.out).correspondent_account='20000000000000000099'
        self.assertEqual(self.candidates().json()['items'],[])
        with unit(True) as s:s.get(BankStatementBatch,self.credit_batch).status='preview'
        item=self.candidates().json()['items'][0]
        self.assertFalse(item['counterparty_account_confirmed'])
        self.assertEqual(self.link(item).status_code,200)

    def test_stale_preview_reversal_and_confirmation_validation(self):
        item=self.candidates().json()['items'][0]
        self.assertEqual(self.link(item,confirm=False).status_code,422)
        self.assertEqual(self.link(item,reason='short').status_code,422)
        self.assertEqual(self.link(item,token='é'*64).status_code,422)
        with unit(True) as s:s.get(Ledger,self.entry).note='Changed after preview'
        self.assertEqual(self.link(item).status_code,409)
        item=self.candidates().json()['items'][0]
        with unit(True) as s:
            old=s.get(Ledger,self.entry)
            s.add(Ledger(account_id=self.other,to_account_id=self.acc,kind='transfer',amount=old.amount,date=old.date,
                         reference='SYN-REVERSE',note='Synthetic reversal',counterparty='',
                         creator_id=old.creator_id,reversal_of=old.id))
        self.assertEqual(self.candidates().json()['items'],[])
        self.assertEqual(self.link(item).status_code,409)

    def test_same_side_cannot_be_linked_twice(self):
        with unit(True) as s:
            old=s.get(BankStatementLine,self.out)
            second=BankStatementLine(company_id=old.company_id,batch_id=old.batch_id,account_id=old.account_id,
                line=8,date=old.date,direction=old.direction,amount=old.amount,document_number='SYN-2',
                correspondent_mfo=old.correspondent_mfo,correspondent_account=old.correspondent_account,
                correspondent_name=old.correspondent_name,correspondent_inn=old.correspondent_inn,
                purpose=old.purpose,fingerprint='8'*64,occurrence=1,status='review',reason='',
                include_in_daily=True,uncertain=False)
            s.add(second);s.flush();second_id=second.id
        item=self.candidates().json()['items'][0]
        second_item=self.candidates(second_id).json()['items'][0]
        self.assertEqual(self.link(item).status_code,200)
        self.assertEqual(self.candidates(second_id).json()['items'],[])
        self.assertEqual(self.link(second_item,second_id).status_code,409)

    def test_company_permissions_and_accountant(self):
        item=self.candidates().json()['items'][0]
        finance,fh=self.make_user('finance','synthetic_transfer_finance')
        accountant,ah=self.make_user('accountant','synthetic_transfer_accountant')
        try:
            self.assertEqual(self.candidates(client=finance,headers=fh).status_code,403)
            self.assertEqual(self.link(item,client=finance,headers=fh).status_code,403)
            with unit() as s:foreign=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
            self.assertIn(self.candidates(headers={**self.h,'X-Company-ID':str(foreign)}).status_code,(403,404))
            result=self.candidates(client=accountant,headers=ah)
            self.assertEqual(result.status_code,200,result.text)
            result=self.link(result.json()['items'][0],client=accountant,headers=ah)
            self.assertEqual(result.status_code,200,result.text)
        finally:
            finance.close();accountant.close()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(TransferLinkTests(name) for name in TransferLinkTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
