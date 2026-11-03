"""Synthetic HTTP tests for audited overlapping-statement resolution."""
import unittest
from datetime import date, timedelta
from sqlalchemy import select

from tests.test_site import SiteTests
from app.bank_statement_duplicates import router
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.db import Account, Audit, Company, Ledger, User, engine, now, unit
from app.main import app
from app.services import account_balance

if not any(getattr(route, 'path', None) == '/api/bank-statements/lines/{id}/duplicate-candidates' for route in app.routes):
    app.include_router(router)


class DuplicateResolutionTests(SiteTests):
    def setUp(self):
        super().setUp()
        self.finance = self.make_user('finance', 'synthetic_finance')
        self.day = date.fromisoformat(self.date)
        with unit(True) as s:
            admin = s.scalar(select(User).where(User.username == 'admin'))
            self.ledger_row = Ledger(account_id=self.acc, category_id=self.cat, kind='in', amount=20000,
                date=self.day, counterparty='Synthetic buyer', reference='SYN-BANK-DOC',
                note='Synthetic bank record', creator_id=admin.id)
            s.add(self.ledger_row); s.flush(); ledger_id = self.ledger_row.id
            moment = now()
            def batch(digest, when):
                return BankStatementBatch(company_id=self.company, account_id=self.acc, created_by=admin.id,
                    filename='synthetic.xls', sha256=digest*64, account_number='20000000000000000001',
                    owner_name='Synthetic Company', owner_inn='300000001', currency='UZS', period_from=self.day,
                    period_to=self.day, opening=100000000, closing=100020000, debit_total=0, credit_total=20000,
                    balance_matches=True, declared_totals_match=True, source_ready=True, status='committed',
                    committed_at=when)
            old=batch('a',moment-timedelta(hours=1)); new=batch('b',moment)
            s.add_all([old,new]); s.flush()
            def line(batch_id, status, uncertain, ledger_id=None, line_number=6):
                return BankStatementLine(company_id=self.company, batch_id=batch_id, account_id=self.acc,
                    line=line_number, date=self.day, direction='in', amount=20000, document_number='SYN-1',
                    correspondent_mfo='00014', correspondent_account='20000000000000000002',
                    correspondent_name='Synthetic buyer', correspondent_inn='300000002', purpose='Synthetic incoming',
                    fingerprint='1'*64, occurrence=1, status=status, reason='', include_in_daily=True,
                    uncertain=uncertain, ledger_id=ledger_id, classification='receipt')
            prior=line(old.id,'auto_income_posted',False,ledger_id)
            target=line(new.id,'ambiguous_duplicate',True)
            s.add_all([prior,target]); s.flush()
            self.prior_id, self.target_id, self.batch_id, self.entry_id = prior.id, target.id, new.id, ledger_id

    def tearDown(self):
        self.finance[0].close()
        super().tearDown()

    def candidates(self, line_id=None, client=None, headers=None):
        return (client or self.client).get(f'/api/bank-statements/lines/{line_id or self.target_id}/duplicate-candidates',
                                           headers=headers or self.h)

    def resolve(self, item, line_id=None, client=None, headers=None, **overrides):
        body={'prior_line_id':item['line_id'],'token':item['token'],
              'reason':'Проверена выписка и операция','confirm':True}
        body.update(overrides)
        return (client or self.client).post(f'/api/bank-statements/lines/{line_id or self.target_id}/duplicate-resolve',
                                            json=body,headers=headers or self.h)

    def test_confirmation_links_without_second_ledger_and_corrects_daily(self):
        before=self.client.get('/api/bank-statements/daily',headers=self.h,params={'date':self.date,'currency':'UZS'})
        self.assertEqual(before.status_code,200,before.text)
        self.assertEqual(before.json()['credit_total'],'400.00'); self.assertTrue(before.json()['uncertain'])
        candidates=self.candidates(); self.assertEqual(candidates.status_code,200,candidates.text)
        self.assertEqual(len(candidates.json()['items']),1)
        item=candidates.json()['items'][0]
        self.assertEqual((item['line_id'],item['ledger_id']),(self.prior_id,self.entry_id))
        applied=self.resolve(item); self.assertEqual(applied.status_code,200,applied.text)
        self.assertEqual(applied.json()['status'],'duplicate')
        after=self.client.get('/api/bank-statements/daily',headers=self.h,params={'date':self.date,'currency':'UZS'})
        self.assertEqual(after.json()['credit_total'],'200.00'); self.assertFalse(after.json()['uncertain'])
        with unit() as s:
            target=s.get(BankStatementLine,self.target_id)
            self.assertEqual((target.ledger_id,target.include_in_daily,target.uncertain),(self.entry_id,False,False))
            self.assertEqual(len(list(s.scalars(select(Ledger)))),1)
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),100020000)
            self.assertTrue(s.scalar(select(Audit.id).where(Audit.action=='Разрешён повтор выписки')))
        self.assertEqual(self.resolve(item).status_code,409)

    def test_stale_token_and_reversal_never_resolve(self):
        item=self.candidates().json()['items'][0]
        with unit(True) as s:s.get(Ledger,self.entry_id).note='Changed synthetic note'
        self.assertEqual(self.resolve(item).status_code,409)
        fresh=self.candidates().json()['items'][0]
        with unit(True) as s:
            old=s.get(Ledger,self.entry_id)
            s.add(Ledger(account_id=self.acc,category_id=self.cat,kind='out',amount=old.amount,date=old.date,
                counterparty=old.counterparty,reference='SYN-REV',note='Synthetic reversal',
                creator_id=old.creator_id,reversal_of=old.id))
        self.assertEqual(self.candidates().json()['items'],[])
        self.assertEqual(self.resolve(fresh).status_code,409)
        with unit() as s:self.assertEqual(s.get(BankStatementLine,self.target_id).status,'ambiguous_duplicate')

    def test_changed_or_unlinked_ledger_cannot_be_a_candidate(self):
        candidate=self.candidates().json()['items'][0]
        with unit(True) as s:s.get(BankStatementLine,self.target_id).purpose='Changed synthetic purpose'
        self.assertEqual(self.resolve(candidate).status_code,409)
        with unit(True) as s:s.get(BankStatementLine,self.target_id).purpose='Synthetic incoming'
        with unit(True) as s:s.get(Ledger,self.entry_id).amount += 1
        self.assertEqual(self.candidates().json()['items'],[])
        with unit(True) as s:
            s.get(Ledger,self.entry_id).amount -= 1
            s.get(BankStatementLine,self.prior_id).ledger_id = None
        self.assertEqual(self.candidates().json()['items'],[])

    def test_accountant_can_review_and_resolve(self):
        accountant,headers=self.make_user('accountant','synthetic_bank_accountant')
        try:
            result=self.candidates(client=accountant,headers=headers)
            self.assertEqual(result.status_code,200,result.text)
            accepted=self.resolve(result.json()['items'][0],client=accountant,headers=headers)
            self.assertEqual(accepted.status_code,200,accepted.text)
        finally:
            accountant.close()

    def test_existing_independent_ledger_link_is_a_valid_original(self):
        with unit(True) as s:s.get(BankStatementLine,self.prior_id).status='other_matched'
        result=self.candidates(); self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(len(result.json()['items']),1)
        self.assertEqual(self.resolve(result.json()['items'][0]).status_code,200)

    def test_prior_transfer_matches_only_the_same_account_side(self):
        with unit(True) as s:
            admin=s.scalar(select(User).where(User.username=='admin'))
            other=Account(company_id=self.company,name='Synthetic transfer account',kind='bank',currency='UZS',
                          opening=0,opening_date=self.day,created_by=admin.id)
            s.add(other); s.flush()
            entry=s.get(Ledger,self.entry_id)
            entry.kind='transfer'; entry.to_account_id=self.acc; entry.account_id=other.id
            s.get(BankStatementLine,self.prior_id).status='manual_transfer_match'
        result=self.candidates(); self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(len(result.json()['items']),1)
        item=result.json()['items'][0]
        with unit(True) as s:
            entry=s.get(Ledger,self.entry_id); entry.to_account_id=other.id; entry.account_id=self.acc
        self.assertEqual(self.candidates().json()['items'],[])
        self.assertEqual(self.resolve(item).status_code,409)

    def test_explicit_confirmation_reason_and_token_validation(self):
        item=self.candidates().json()['items'][0]
        self.assertEqual(self.resolve(item,confirm=False).status_code,422)
        self.assertEqual(self.resolve(item,reason='short').status_code,422)
        self.assertEqual(self.resolve(item,token='é'*64).status_code,422)
        with unit() as s:self.assertEqual(s.get(BankStatementLine,self.target_id).status,'ambiguous_duplicate')

    def test_foreign_company_and_users_without_bank_payment_cannot_resolve(self):
        item=self.candidates().json()['items'][0]
        c,h=self.finance
        self.assertEqual(self.candidates(client=c,headers=h).status_code,403)
        self.assertEqual(self.resolve(item,client=c,headers=h).status_code,403)
        with unit() as s:foreign=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
        r=self.candidates(headers={**self.h,'X-Company-ID':str(foreign)})
        self.assertIn(r.status_code,(403,404),r.text)

    def test_one_previous_operation_cannot_resolve_two_rows_in_one_batch(self):
        item=self.candidates().json()['items'][0]
        with unit(True) as s:
            original=s.get(BankStatementLine,self.target_id)
            duplicate=BankStatementLine(company_id=self.company,batch_id=self.batch_id,account_id=self.acc,
                line=7,date=original.date,direction='in',amount=original.amount,document_number=original.document_number,
                correspondent_mfo=original.correspondent_mfo,correspondent_account=original.correspondent_account,
                correspondent_name=original.correspondent_name,correspondent_inn=original.correspondent_inn,
                purpose=original.purpose,fingerprint=original.fingerprint,occurrence=2,status='ambiguous_duplicate',
                reason='',include_in_daily=True,uncertain=True,classification='receipt')
            s.add(duplicate); s.flush(); second_id=duplicate.id
        self.assertEqual(len(self.candidates(second_id).json()['items']),1)
        self.assertEqual(self.resolve(item).status_code,200)
        self.assertEqual(self.candidates(second_id).json()['items'],[])
        self.assertEqual(self.resolve(item,line_id=second_id).status_code,409)

    def test_resolved_duplicate_allows_other_fee_without_reposting_income(self):
        with unit(True) as s:
            batch=s.get(BankStatementBatch,self.batch_id)
            batch.debit_total=2000; batch.closing=100018000
            fee=BankStatementLine(company_id=self.company,batch_id=self.batch_id,account_id=self.acc,
                line=7,date=self.day,direction='out',amount=2000,document_number='SYN-FEE',
                correspondent_mfo='00014',correspondent_account='20000000000000000003',
                correspondent_name='Synthetic fee',correspondent_inn=batch.owner_inn,
                purpose='00011 Погаш. ком. SWIFT - synthetic',fingerprint='2'*64,occurrence=1,
                status='review',reason='',include_in_daily=True,uncertain=False)
            s.add(fee)
        item=self.candidates().json()['items'][0]
        self.assertEqual(self.resolve(item).status_code,200)
        p=self.client.post(f'/api/bank-statements/{self.batch_id}/reprocess/preview',headers=self.h,
                           json={'fee_category_id':self.cat})
        self.assertEqual(p.status_code,200,p.text); self.assertTrue(p.json()['full_reconciliation'])
        result=self.client.post(f'/api/bank-statements/{self.batch_id}/reprocess/commit',headers=self.h,
            json={'fee_category_id':self.cat,'preview_token':p.json()['preview_token'],'confirm':True})
        self.assertEqual(result.status_code,200,result.text); self.assertTrue(result.json()['full_reconciliation'])
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))),2)
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),100018000)
            original=s.get(Ledger,self.entry_id)
            s.add(Ledger(account_id=self.acc,category_id=self.cat,kind='out',amount=original.amount,
                date=original.date,counterparty=original.counterparty,reference='SYN-REV2',note='Synthetic reversal',
                creator_id=original.creator_id,reversal_of=original.id))
        changed=self.client.post(f'/api/bank-statements/{self.batch_id}/reprocess/preview',headers=self.h,
                                 json={'fee_category_id':self.cat})
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertFalse(changed.json()['full_reconciliation'])
        self.assertEqual(next(row for row in changed.json()['rows'] if row['id']==self.target_id)['status'],'review')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(DuplicateResolutionTests(name) for name in DuplicateResolutionTests.__dict__
                              if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
