"""The budget page keeps exact monetary semantics with constant SQL work."""
from datetime import date
import unittest
from sqlalchemy import event,select
import tests.test_site as base
from app.db import Account,Budget,CashFunding,Category,Company,Ledger,PaymentRequest,User,engine,unit
from app.services import budget_state,budget_states,budget_json
from app.category_catalog import catalog


class BudgetReadPerformanceTests(base.SiteTests):
    def seed(self):
        with unit(True) as s:
            uid=s.scalar(select(User.id).where(User.username=='admin'))
            foreign=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
            cats=[]
            for i in range(50):
                c=Category(company_id=self.company,name=f'Synthetic budget {i}',code=f'B{i}')
                s.add(c);s.flush();cats.append(c.id)
            other=Category(company_id=foreign,name='Synthetic foreign budget');s.add(other);s.flush()
            accounts={}
            for code,cid in [('UZS',self.company),('USD',self.company),('EUR',self.company),('foreign',foreign)]:
                a=Account(company_id=cid,name=f'Synthetic {code}',kind='bank',currency='UZS' if code=='foreign' else code,
                          opening=1000000,opening_date=date(2026,1,1),created_by=uid)
                s.add(a);s.flush();accounts[code]=a.id
            def ledger(cat,code,n,ref,when=date(2026,9,16),kind='out',reversal=None):
                row=Ledger(account_id=accounts[code],category_id=cat,kind=kind,amount=n,date=when,
                           reference=ref,creator_id=uid,reversal_of=reversal)
                s.add(row);s.flush();return row.id
            ledger(cats[0],'UZS',101,'CURRENT')
            ledger(cats[0],'UZS',202,'FIRST',date(2026,9,1))
            ledger(cats[0],'UZS',303,'LAST',date(2026,9,30))
            ledger(cats[0],'UZS',900,'BEFORE',date(2026,8,31))
            ledger(cats[0],'UZS',900,'AFTER',date(2026,10,1))
            ledger(cats[0],'UZS',500,'INCOME',kind='in')
            ledger(cats[0],'UZS',500,'TRANSFER',kind='transfer')
            reversed_id=ledger(cats[0],'UZS',777,'REVERSED')
            ledger(cats[0],'UZS',777,'REVERSAL',kind='in',reversal=reversed_id)
            ledger(cats[0],'USD',123,'USD')
            ledger(cats[1],'EUR',456,'EUR')
            ledger(other.id,'foreign',999999,'FOREIGN')
            for code in ('UZS','USD','EUR'):
                s.add(Budget(category_id=cats[0],month='2026-09',currency=code,amount=1000,mode='hard',source='synthetic'))
            s.add(Budget(category_id=cats[1],month='2026-09',currency='UZS',amount=0,mode='soft',source='zero'))
            def request(cat,code,n,status='approved',when=date(2026,9,16),mode=None,state='unfunded'):
                r=PaymentRequest(account_id=accounts[code],category_id=cat,amount=n,due_date=when,
                    creator_id=uid,last_editor_id=uid,counterparty='Synthetic',purpose='Synthetic',status=status)
                s.add(r);s.flush()
                if mode:
                    s.add(CashFunding(company_id=self.company,request_id=r.id,mode=mode,bank_account_id=accounts[code],
                        cash_account_id=accounts[code],gross_amount=n+10,fee_percent='1',fee_amount=10,net_amount=n,
                        fee_category_id=cats[1],state=state,created_by=uid))
                return r
            request(cats[0],'UZS',400)
            request(cats[0],'UZS',800,mode='batch')
            request(cats[0],'UZS',200,mode='single')
            request(cats[1],'UZS',300,mode='single',state='received')
            request(cats[0],'UZS',10000,status='pending')
            request(cats[0],'UZS',10000,status='paid')
            request(cats[0],'UZS',10000,when=date(2026,10,1))
            request(cats[0],'USD',50)
            request(other.id,'foreign',999999)
        return cats,foreign,other.id

    def test_batch_matches_scalar_with_fees_funding_reversals_currency_and_month_boundaries(self):
        cats,_,_=self.seed()
        with unit() as s:
            s.info['company_id']=self.company
            for month in ('2026-08','2026-09','2026-10','2027-01'):
                for currency in ('UZS','USD','EUR'):
                    expected={c:budget_state(s,c,month,currency) for c in cats[:3]}
                    self.assertEqual(budget_states(s,cats[:3],month,currency),expected)
            values=budget_states(s,cats,'2026-09','UZS')
            self.assertEqual(values[cats[0]]['spent'],606)
            self.assertEqual(values[cats[0]]['reserved'],600)
            self.assertEqual(values[cats[1]]['reserved'],320)
            self.assertTrue(values[cats[0]]['over'])
            self.assertIsNone(values[cats[2]]['limit'])

    def test_four_queries_for_one_and_fifty_articles_instead_of_per_article_reads(self):
        cats,_,_=self.seed();counts=[];old=[]
        with unit() as s:
            s.info['company_id']=self.company
            for chosen in (cats[:1],cats):
                statements=[]
                def counter(*args):statements.append(args[2])
                event.listen(engine,'before_cursor_execute',counter)
                try:budget_states(s,chosen,self.month,'UZS')
                finally:event.remove(engine,'before_cursor_execute',counter)
                counts.append(len(statements));statements.clear()
                event.listen(engine,'before_cursor_execute',counter)
                try:
                    for c in chosen:budget_state(s,c,self.month,'UZS')
                finally:event.remove(engine,'before_cursor_execute',counter)
                old.append(len(statements))
        self.assertEqual(counts,[4,4]);self.assertEqual(old,[4,200])
        print('BUDGET_READ_QUERIES',{'old':old,'new':counts})

    def test_http_order_and_every_field_match_original_calculation(self):
        self.seed()
        with unit() as s:
            s.info['company_id']=self.company
            cats=[c.id for c,_ in catalog(s) if c.type=='outcome' and c.activity!='unclassified']
            expected={c:budget_json(budget_state(s,c,self.month,'UZS')) for c in cats}
        response=self.client.get('/api/budgets',params={'month':self.month,'currency':'UZS'},headers=self.h)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual([r['category_id'] for r in response.json()],cats)
        for row in response.json():
            self.assertEqual({k:row[k] for k in expected[row['category_id']]},expected[row['category_id']])

    def test_foreign_company_is_not_aggregated(self):
        cats,foreign,other=self.seed()
        with unit() as s:
            s.info['company_id']=foreign
            value=budget_states(s,[other],self.month,'UZS')[other]
            self.assertEqual(value,budget_state(s,other,self.month,'UZS'))
            self.assertEqual(value['spent'],999999)
            self.assertEqual(value['reserved'],999999)
        response=self.client.get('/api/budgets',headers={**self.h,'X-Company-ID':str(foreign)})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(set(cats).isdisjoint({r['category_id'] for r in response.json()}))

    def test_empty_batch_and_malformed_period(self):
        with unit() as s:self.assertEqual(budget_states(s,[],self.month,'UZS'),{})
        response=self.client.get('/api/budgets?month=invalid',headers=self.h)
        self.assertEqual(response.status_code,422)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(BudgetReadPerformanceTests(name) for name in BudgetReadPerformanceTests.__dict__ if name.startswith('test_'))


def tearDownModule():base.tearDownModule()
