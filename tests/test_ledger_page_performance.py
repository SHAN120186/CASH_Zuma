"""Bound page metadata reads without changing visibility or ledger flags."""
import json
import unittest
from collections import Counter
from datetime import date,timedelta
from sqlalchemy import event,insert,select
from sqlalchemy.orm import Session

import tests.test_site as base
from app.db import Account,Category,Company,Ledger,Setting,User,engine,unit
from app.company_scope import setting_key


class LedgerPageTests(base.SiteTests):
    def seed(self):
        day=date.fromisoformat(self.date)
        with unit(True) as s:
            user=s.scalar(select(User).where(User.username=='admin'))
            foreign=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
            cash=Account(company_id=self.company,name='Page cash target',kind='cash',currency='UZS',opening=0,opening_date=day,created_by=user.id)
            other=Account(company_id=foreign,name='Foreign page bank',kind='bank',currency='UZS',opening=0,opening_date=day,created_by=user.id)
            usd=Account(company_id=self.company,name='Page USD bank',kind='bank',currency='USD',opening=0,opening_date=day,created_by=user.id)
            s.add_all([cash,other,usd]);s.flush()
            def entry(reference,account=None,kind='out',target=None,when=day,reverse=None):
                row=Ledger(account_id=account or self.acc,to_account_id=target,category_id=None if kind=='transfer' else self.cat,
                    kind=kind,amount=100,date=when,reference=reference,creator_id=user.id,counterparty='Page supplier',note='Page note',reversal_of=reverse)
                s.add(row);s.flush();return row.id
            original=entry('PAGE-ORIGINAL')
            inverse=entry('PAGE-INVERSE',kind='in',when=day-timedelta(days=1),reverse=original)
            transfer=entry('PAGE-TRANSFER',kind='transfer',target=cash.id)
            expense=entry('PAGE-CASH',account=cash.id)
            foreign_row=entry('PAGE-FOREIGN',account=other.id)
            dollar=entry('PAGE-USD',account=usd.id)
            independent=entry('PAGE-INDEPENDENT')
            # A malformed foreign reversal cannot alter flags in this company.
            entry('PAGE-FOREIGN-REV',account=other.id,kind='in',reverse=independent)
            s.info['company_id']=self.company
            s.add(Setting(key=setting_key(s,'ledger_deletions'),value=json.dumps({'synthetic':{'ids':[inverse]}})))
        return dict(original=original,inverse=inverse,transfer=transfer,expense=expense,foreign=foreign_row,
                    dollar=dollar,independent=independent,cash=cash.id,foreign_company=foreign)

    def listing(self,query='',client=None,headers=None):
        response=(client or self.client).get('/api/ledger?'+query,headers=headers or self.h)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_page_search_transfer_currency_and_reversal_flags(self):
        ids=self.seed()
        all_rows=self.listing('currency=UZS')
        self.assertEqual([r['id'] for r in all_rows],[ids[k] for k in ('independent','expense','transfer','original')])
        rows={r['id']:r for r in all_rows}
        self.assertTrue(rows[ids['original']]['reversed'])
        self.assertFalse(rows[ids['original']]['can_reverse']);self.assertFalse(rows[ids['original']]['can_edit'])
        self.assertFalse(rows[ids['independent']]['reversed']);self.assertTrue(rows[ids['independent']]['can_edit'])
        self.assertEqual(rows[ids['transfer']]['to_account'],'Page cash target')
        self.assertEqual(rows[ids['transfer']]['category'],'Внутренний перевод')
        for page in (1,2,3):
            result=self.listing(f'currency=UZS&paginated=true&page_size=2&page={page}')
            self.assertEqual(result['total'],4);self.assertEqual(result['items'],all_rows[(page-1)*2:page*2])
        self.assertEqual([r['id'] for r in self.listing('currency=USD')],[ids['dollar']])
        self.assertEqual([r['id'] for r in self.listing('q=PAGE-TRANSFER')],[ids['transfer']])
        self.assertEqual([r['id'] for r in self.listing('q=Page%20cash%20target')],[ids['expense']])
        selected=self.listing(f'currency=UZS&date_from={self.date}&date_to={self.date}&q=PAGE-ORIGINAL')
        self.assertTrue(selected[0]['reversed']) # inverse is outside date/search and marked deleted
        history={r['id']:r for r in self.listing('include_deleted=true')}
        self.assertTrue(history[ids['inverse']]['deleted']);self.assertFalse(history[ids['inverse']]['can_edit'])

    def test_role_channel_and_company_boundaries(self):
        ids=self.seed()
        for role,expected in (('cashier',{'expense','transfer'}),('accountant',{'original','transfer','dollar','independent'})):
            client,headers=self.make_user(role,'page_'+role)
            try:
                rows=self.listing(client=client,headers=headers)
                self.assertEqual({r['id'] for r in rows},{ids[k] for k in expected})
                self.assertTrue(all(not r['can_edit'] and not r['can_delete'] for r in rows))
                self.assertEqual(client.get('/api/ledger?include_deleted=true',headers=headers).status_code,403)
                self.assertEqual(client.get('/api/ledger',headers={**headers,'X-Company-ID':str(ids['foreign_company'])}).status_code,403)
            finally:client.close()

    def test_ten_row_page_has_bounded_orm_loads_with_ten_thousand_rows(self):
        with unit(True) as s:
            uid=s.scalar(select(User.id).where(User.username=='admin'))
            for n in range(30):
                s.add(Account(company_id=self.company,name=f'Unused page account {n}',kind='cash',currency='UZS',opening=0,
                    opening_date=date(2026,1,1),created_by=uid))
                s.add(Category(company_id=self.company,name=f'Unused page category {n}',type='outcome',activity='operating'))
        measurements=[]
        for size in (10,10000):
            with unit(True) as s:
                start=0 if size==10 else 10
                normal=10 if size==10 else 5000
                rows=[dict(id=i+1,account_id=self.acc,category_id=self.cat,kind='in' if i>=normal else 'out',amount=100,
                    date=date(2026,9,16),reference=f'LOAD-{i}',creator_id=uid,reversal_of=i-normal+1 if i>=normal else None)
                    for i in range(start,size)]
                s.execute(insert(Ledger),rows)
            loaded=Counter();sql=[]
            def load(session,obj):loaded[type(obj).__name__]+=1
            def count(*args):sql.append(args[2])
            event.listen(Session,'loaded_as_persistent',load);event.listen(engine,'before_cursor_execute',count)
            try:result=self.listing('paginated=true&page_size=10')
            finally:event.remove(Session,'loaded_as_persistent',load);event.remove(engine,'before_cursor_execute',count)
            self.assertEqual(result['total'],size);self.assertEqual(len(result['items']),10)
            self.assertEqual(loaded['Ledger'],10);self.assertEqual(loaded['Account'],1);self.assertEqual(loaded['Category'],1)
            measurements.append((len(sql),dict(loaded)))
        self.assertEqual(measurements[0],measurements[1])
        print('LEDGER_PAGE_READ_COUNTS',measurements)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(LedgerPageTests(name) for name in LedgerPageTests.__dict__ if name.startswith('test_'))


def tearDownModule():base.tearDownModule()
