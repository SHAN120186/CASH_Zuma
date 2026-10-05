"""Regression API checks; run as a separate process with a temporary SQLite DB.

    python -m unittest tests.test_future_reservations

No working database or externally configured test database is used.
"""
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
TMP=tempfile.TemporaryDirectory(prefix='zuma-future-reservations-')
os.environ['DATA_DIR']=TMP.name
os.environ.pop('DATABASE_URL',None)
os.environ.pop('TEST_DATABASE_URL',None)
os.environ['ALLOWED_HOSTS']='testserver,localhost,127.0.0.1'
os.environ['COOKIE_SECURE']='0'
os.environ['PUBLIC_ORIGIN']=''

from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.db import (Base,engine,initialize,unit,Company,CompanyUser,User,Category,
                    Account,PaymentRequest,Receipt,Ledger,Audit,now)
from app.security import hash_password
from app.services import funds_state,budget_state
from app import clock

PASSWORD='TemporaryFutureReservationTests_847!'
HASH=hash_password(PASSWORD)
TODAY=date(2026,10,6)


def tearDownModule():
    clock.FROZEN=None
    engine.dispose()
    TMP.cleanup()


class FutureReservationTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(Path(engine.url.database).resolve(),Path(TMP.name)/'cashflow.sqlite3',
            'Run tests.test_future_reservations in a separate process; only its temporary SQLite DB may be reset.')
        clock.FROZEN=TODAY
        Base.metadata.drop_all(engine)
        initialize()
        self.ids={}
        with unit(True) as s:
            self.company=s.scalar(select(Company.id).where(Company.code=='UZGERMED'))
            c=s.scalar(select(Category).where(Category.company_id==self.company,Category.type=='outcome'))
            # The final stage is finance for an allowed regular expense.
            c.skip_allowed=True;c.director_policy='skip';self.category=c.id
            self.income=s.scalar(select(Category.id).where(Category.company_id==self.company,Category.type=='income'))
            for name,role in (('author','operator'),('checker','accountant'),('finance','finance'),('payer','accountant')):
                u=User(username=name,name=name,role=role,password_hash=HASH)
                s.add(u);s.flush();self.ids[name]=u.id
                s.add(CompanyUser(company_id=self.company,user_id=u.id,role=role))
        self.clients={};self.headers={}
        for name in self.ids:
            client=TestClient(app)
            self.clients[name]=client
            r=client.post('/api/login',json={'username':name,'password':PASSWORD})
            self.assertEqual(r.status_code,200,r.text)
            self.headers[name]={'X-CSRF-Token':r.json()['csrf'],'X-Company-ID':str(self.company)}
        self.account=self.new_account('Source bank')

    def tearDown(self):
        for client in self.clients.values():client.close()
        clock.FROZEN=None

    def post(self,name,path,data):
        return self.clients[name].post(path,json=data,headers=self.headers[name])

    def new_account(self,name,opening='1000',overdraft=False):
        r=self.post('author','/api/accounts',{'name':name,'kind':'bank','currency':'UZS',
            'opening':opening,'opening_date':'2026-10-01','allow_overdraft':overdraft})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()['id']

    def row(self,rid):
        r=self.clients['finance'].get(f'/api/requests/{rid}',headers=self.headers['finance'])
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def decision(self,name,rid,action):
        return self.post(name,f'/api/requests/{rid}/decision',{'action':action,
            'version':self.row(rid)['version'],'note':'Synthetic reservation regression reason'})

    def checked_request(self,amount='900',due='2026-10-20',account=None):
        r=self.post('author','/api/requests',{'company_id':self.company,'account_id':account or self.account,
            'channel':'bank','currency':'UZS','category_id':self.category,'amount':amount,
            'counterparty':'Synthetic supplier','date':due,'purpose':'Synthetic reservation regression','priority':'urgent'})
        self.assertEqual(r.status_code,200,r.text);rid=r.json()['id']
        for kind in ('internal','contract'):
            headers=dict(self.headers['author'])
            headers.update({'Content-Type':'application/octet-stream','X-Filename':kind+'.pdf',
                'X-Request-Version':str(self.row(rid)['version'])})
            r=self.clients['author'].post(f'/api/requests/{rid}/documents',params={'kind':kind},
                content=b'%PDF-1.4\n% synthetic regression document\n',headers=headers)
            self.assertEqual(r.status_code,200,r.text)
        for name,action in (('author','submit'),('checker','check')):
            r=self.decision(name,rid,action);self.assertEqual(r.status_code,200,r.text)
        return rid

    def approved_request(self,amount='900',due='2026-10-20',account=None):
        rid=self.checked_request(amount,due,account)
        r=self.decision('finance',rid,'approve')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['status'],'approved')
        return rid

    def legacy_approved(self,amount,due):
        # Model pre-existing outstanding approvals, including an already
        # overcommitted account, without asking the fixed API to create them.
        with unit(True) as s:
            r=PaymentRequest(creator_id=self.ids['author'],last_editor_id=self.ids['author'],
                account_id=self.account,category_id=self.category,counterparty='Synthetic supplier',
                purpose='Pre-existing synthetic approval',amount=int(amount)*100,
                due_date=date.fromisoformat(due),status='approved',priority='urgent',
                route_policy='skip',route_at=now(),checked_by=self.ids['checker'],checked_at=now(),
                finance_approved_by=self.ids['finance'],approved_at=now())
            s.add(r);s.flush();return r.id

    def ledger(self,kind,amount,account=None,target=None,day='2026-10-06',request=None,receipt=None):
        data={'account_id':account or self.account,'kind':kind,'amount':amount,'date':day,
            'category_id':None if kind=='transfer' else self.income if kind=='in' else self.category,
            'reference':'SYNTHETIC-'+str(self.ledger_count()),'note':'Synthetic reservation regression reason'}
        if target:data['to_account_id']=target
        if request:data.update(request_id=request,request_version=self.row(request)['version'])
        if receipt:data['receipt_id']=receipt
        return self.post('payer' if request else 'finance','/api/ledger',data)

    def ledger_count(self):
        with unit() as s:return s.scalar(select(func.count()).select_from(Ledger))

    def funds(self,day='2026-10-20',account=None):
        with unit() as s:return funds_state(s,s.get(Account,account or self.account),date.fromisoformat(day))

    def edit_opening(self,opening,account=None,overdraft=False,name='Source bank'):
        return self.post('finance',f'/api/accounts/{account or self.account}',{'name':name,
            'kind':'bank','currency':'UZS','opening':opening,'opening_date':'2026-10-01',
            'allow_overdraft':overdraft,'reason':'Synthetic accounting correction'})

    def reverse(self,lid):
        return self.post('finance',f'/api/ledger/{lid}/reverse',{'reason':'Synthetic accounting reversal'})

    def audit_count(self,action):
        with unit() as s:return s.scalar(select(func.count()).select_from(Audit).where(Audit.action==action))

    def test_earlier_approval_preserves_later_reservation_across_months(self):
        self.approved_request(due='2026-11-20')
        early=self.checked_request(due='2026-10-09')
        r=self.decision('finance',early,'approve')
        self.assertEqual(r.status_code,409,r.text)
        self.assertIn('2026-11-20',r.text)
        self.assertEqual(self.row(early)['status'],'pending')
        self.assertIsNone(self.row(early)['finance_approved_by'])
        self.assertEqual(self.funds('2026-11-20')['available'],10000)

    def test_later_approval_still_checks_earlier_reservations(self):
        self.approved_request(due='2026-10-09')
        later=self.checked_request()
        r=self.decision('finance',later,'approve')
        self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.row(later)['status'],'pending')

    def test_direct_expense_cannot_take_future_reservation(self):
        self.approved_request()
        r=self.ledger('out','200')
        self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.ledger_count(),0)
        self.assertEqual(self.funds()['available'],10000)

    def test_transfer_cannot_take_future_reservation(self):
        self.approved_request()
        target=self.new_account('Target bank','0')
        r=self.ledger('transfer','200',target=target)
        self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.ledger_count(),0)
        self.assertEqual(self.funds(account=target)['actual'],0)

    def test_early_payment_of_existing_approval_preserves_later_reservation(self):
        early=self.legacy_approved('900','2026-10-09')
        self.legacy_approved('900','2026-10-20')
        r=self.ledger('out','900',request=early)
        self.assertEqual(r.status_code,409,r.text)
        self.assertIn('2026-10-20',r.text)
        self.assertIn('финансовому директору',r.text)
        self.assertEqual(self.row(early)['status'],'approved')
        self.assertEqual(self.ledger_count(),0)

    def test_correct_early_payments_count_each_reservation_once(self):
        later=self.approved_request('400')
        early=self.approved_request('600','2026-10-09')
        r=self.ledger('out','600',request=early)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.row(early)['status'],'paid')
        self.assertEqual(self.funds()['actual'],40000)
        self.assertEqual(self.funds()['reserved'],40000)
        self.assertEqual(self.funds()['available'],0)
        with unit() as s:
            budget=budget_state(s,self.category,'2026-10','UZS')
            self.assertEqual((budget['spent'],budget['reserved'],budget['used']),(60000,40000,100000))
        r=self.ledger('out','400',request=later)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual((self.funds()['actual'],self.funds()['reserved']),(0,0))

    def test_return_releases_future_reservation(self):
        rid=self.approved_request()
        r=self.decision('finance',rid,'return')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.row(rid)['status'],'returned')
        r=self.ledger('out','200')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.funds()['available'],80000)

    def test_close_releases_future_reservation(self):
        rid=self.approved_request()
        r=self.decision('author',rid,'close')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.row(rid)['status'],'cancelled')
        r=self.ledger('out','200')
        self.assertEqual(r.status_code,200,r.text)

    def test_only_actual_receipt_can_cover_future_reservation(self):
        self.approved_request()
        r=self.post('finance','/api/receipts',{'account_id':self.account,'category_id':self.income,
            'amount':'500','date':'2026-10-08','counterparty':'Synthetic buyer'})
        self.assertEqual(r.status_code,200,r.text);receipt=r.json()['id']
        r=self.ledger('out','200')
        self.assertEqual(r.status_code,409,r.text)
        r=self.ledger('in','500',receipt=receipt)
        self.assertEqual(r.status_code,200,r.text)
        r=self.ledger('out','200')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.funds()['available'],40000)

    def test_backdated_expense_keeps_original_date_and_current_funds_checks(self):
        self.approved_request()
        r=self.ledger('in','300')
        self.assertEqual(r.status_code,200,r.text)
        r=self.ledger('out','200',day='2026-10-02')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.funds()['available'],20000)

    def test_backdated_expense_does_not_replay_past_reservation_dates(self):
        self.legacy_approved('900','2026-10-04')
        r=self.ledger('in','300')
        self.assertEqual(r.status_code,200,r.text)
        r=self.ledger('out','200',day='2026-10-02')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.funds()['available'],20000)

    def test_backdated_expense_cannot_use_money_before_it_arrived(self):
        account=self.new_account('Historical bank','100')
        r=self.ledger('in','900',account=account)
        self.assertEqual(r.status_code,200,r.text)
        r=self.ledger('out','200',account=account,day='2026-10-02')
        self.assertEqual(r.status_code,409,r.text)
        self.assertIn('2026-10-02',r.text)

    def test_other_accounts_reservations_do_not_block_an_expense(self):
        self.approved_request()
        other=self.new_account('Independent bank')
        r=self.ledger('out','900',account=other)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.funds(account=other)['available'],10000)
        self.assertEqual(self.funds()['reserved'],90000)

    def test_overdraft_setting_does_not_bypass_reservations(self):
        account=self.new_account('Overdraft bank',overdraft=True)
        self.approved_request(account=account)
        r=self.ledger('out','200',account=account)
        self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.funds(account=account)['available'],10000)

    def test_opening_correction_rolls_back_and_can_be_repeated_after_return(self):
        rid=self.approved_request();version=self.row(rid)['version']
        r=self.post('finance','/api/receipts',{'account_id':self.account,'category_id':self.income,
            'amount':'2000','date':'2026-10-08','counterparty':'Unconfirmed synthetic buyer'})
        self.assertEqual(r.status_code,200,r.text)
        r=self.edit_opening('100',name='Attempted correction')
        self.assertEqual(r.status_code,409,r.text)
        self.assertIn('финансовому директору',r.text)
        with unit() as s:
            account=s.get(Account,self.account)
            self.assertEqual((account.name,account.opening),('Source bank',100000))
        self.assertEqual(self.row(rid)['version'],version)
        self.assertEqual(self.audit_count('Изменён счёт / начальный остаток'),0)
        r=self.decision('finance',rid,'return');self.assertEqual(r.status_code,200,r.text)
        r=self.edit_opening('100')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual((self.funds()['actual'],self.funds()['reserved']),(10000,0))
        self.assertEqual(self.audit_count('Изменён счёт / начальный остаток'),1)

    def test_funded_opening_correction_does_not_subtract_the_reduction_twice(self):
        self.approved_request()
        r=self.edit_opening('920')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.funds()['available'],2000)

    def test_income_reversal_rolls_back_receipt_and_retries_after_return(self):
        r=self.post('finance','/api/receipts',{'account_id':self.account,'category_id':self.income,
            'amount':'500','date':'2026-10-08','counterparty':'Synthetic buyer'})
        self.assertEqual(r.status_code,200,r.text);receipt=r.json()['id']
        r=self.ledger('in','500',receipt=receipt)
        self.assertEqual(r.status_code,200,r.text);lid=r.json()['id']
        rid=self.approved_request('1400');version=self.row(rid)['version']
        r=self.reverse(lid)
        self.assertEqual(r.status_code,409,r.text)
        self.assertIn('финансовому директору',r.text)
        self.assertEqual(self.ledger_count(),1)
        self.assertEqual(self.row(rid)['version'],version)
        self.assertEqual(self.funds()['actual'],150000)
        self.assertEqual(self.audit_count('Сторно операции (исходная дата)'),0)
        self.assertEqual(self.audit_count('Отмена поступления'),0)
        with unit() as s:
            self.assertEqual(s.get(Receipt,receipt).status,'received')
            self.assertEqual(s.get(Ledger,lid).receipt_id,receipt)
        r=self.decision('finance',rid,'return');self.assertEqual(r.status_code,200,r.text)
        r=self.reverse(lid);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.funds()['actual'],100000)
        with unit() as s:self.assertEqual(s.get(Receipt,receipt).status,'expected')
        r=self.reverse(lid);self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.ledger_count(),2)

    def test_funded_income_reversal_does_not_debit_the_amount_twice(self):
        r=self.ledger('in','500');self.assertEqual(r.status_code,200,r.text);lid=r.json()['id']
        self.approved_request()
        r=self.reverse(lid)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.funds()['available'],10000)

    def test_transfer_reversal_protects_the_original_recipient_reservations(self):
        target=self.new_account('Transfer recipient','0')
        r=self.ledger('transfer','500',target=target)
        self.assertEqual(r.status_code,200,r.text);lid=r.json()['id']
        rid=self.approved_request('400',account=target);version=self.row(rid)['version']
        r=self.reverse(lid)
        self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.ledger_count(),1)
        self.assertEqual(self.row(rid)['version'],version)
        self.assertEqual((self.funds()['actual'],self.funds(account=target)['actual']),(50000,50000))
        self.assertEqual(self.audit_count('Сторно операции (исходная дата)'),0)
        r=self.decision('finance',rid,'return');self.assertEqual(r.status_code,200,r.text)
        r=self.reverse(lid);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual((self.funds()['actual'],self.funds(account=target)['actual']),(100000,0))
        r=self.reverse(lid);self.assertEqual(r.status_code,409,r.text)

    def test_funded_transfer_reversal_restores_each_account_once(self):
        target=self.new_account('Funded transfer recipient','900')
        r=self.ledger('transfer','500',target=target)
        self.assertEqual(r.status_code,200,r.text);lid=r.json()['id']
        self.approved_request('900',account=target)
        r=self.reverse(lid);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.funds()['actual'],100000)
        self.assertEqual((self.funds(account=target)['actual'],self.funds(account=target)['reserved']),(90000,90000))

    def test_expense_reversal_reopens_one_reservation_and_allows_repayment(self):
        early=self.approved_request('600','2026-10-09')
        self.approved_request('400')
        r=self.ledger('out','600',request=early)
        self.assertEqual(r.status_code,200,r.text);lid=r.json()['id']
        r=self.reverse(lid);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.row(early)['status'],'approved')
        self.assertEqual((self.funds()['actual'],self.funds()['reserved']),(100000,100000))
        with unit() as s:
            budget=budget_state(s,self.category,'2026-10','UZS')
            self.assertEqual((budget['spent'],budget['reserved']),(0,100000))
            self.assertIsNone(s.get(Ledger,lid).request_id)
        r=self.reverse(lid);self.assertEqual(r.status_code,409,r.text)
        r=self.ledger('out','600',request=early);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual((self.funds()['actual'],self.funds()['reserved']),(40000,40000))
        self.assertEqual(self.ledger_count(),3)

    def test_unreserved_overdraft_correction_keeps_existing_accounting_rules(self):
        account=self.new_account('Historical overdraft bank','100',overdraft=True)
        with unit(True) as s:
            s.add(Ledger(account_id=account,category_id=self.category,kind='out',amount=30000,
                date=date(2026,10,2),reference='LEGACY-OVERDRAFT',creator_id=self.ids['finance']))
        r=self.edit_opening('50',account=account,overdraft=True,name='Corrected overdraft bank')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['balance'],'-250.00')

    def test_foreign_company_money_cannot_cover_a_local_correction(self):
        with unit(True) as s:
            company=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
            account=Account(name='Other company bank',kind='bank',currency='UZS',company_id=company,
                opening=100000000,opening_date=date(2026,10,1),created_by=self.ids['author'])
            s.add(account);s.flush();foreign=account.id
        self.approved_request()
        r=self.edit_opening('100');self.assertEqual(r.status_code,409,r.text)
        r=self.edit_opening('0',account=foreign);self.assertEqual(r.status_code,404,r.text)
        with unit() as s:self.assertEqual(s.get(Account,foreign).opening,100000000)


if __name__=='__main__':unittest.main()
