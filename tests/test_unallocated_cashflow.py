"""Synthetic checks for the fact-only unallocated bank-income category."""
import json
import unittest
from datetime import date

from sqlalchemy import select

from tests.test_site import SiteTests
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.bank_statement_unallocated import ensure_category
from app.cash_report import export_rows, report
from app.db import Account, CashPlan, Category, Ledger, Setting, User, engine, unit
from app.services import account_balance


class UnallocatedCashFlowTests(SiteTests):
    def add_fact(self, s, account, *, amount=12345):
        category_id = ensure_category(s)
        user = s.scalar(select(User).where(User.username == 'admin').execution_options(company_unscoped=True))
        entry = Ledger(account_id=account.id,category_id=category_id,kind='in',amount=amount,
            date=date.fromisoformat(self.date),counterparty='Synthetic buyer',reference='SYNTHETIC-DOC',
            note='Synthetic statement receipt',creator_id=user.id)
        s.add(entry);s.flush()
        return category_id, entry, user

    def scoped(self, s):
        s.info['company_id'] = self.company
        account = s.get(Account,self.acc)
        account.opening_date = date(2026,1,1)
        s.flush()
        return account

    def read_report(self, s):
        return report(s,2026,'UZS',self.company,as_of=date.fromisoformat(self.date))

    def test_fact_is_a_separate_group_and_changes_the_balance_once(self):
        with unit(True) as s:
            account=self.scoped(s)
            unallocated,entry,_=self.add_fact(s,account)
            data=self.read_report(s)
            groups={g['activity']:g for g in data['groups']}
            self.assertEqual(groups['unclassified']['values'][8],'123.45')
            for name in ('operating','investing','financing'):
                self.assertEqual(groups[name]['values'][8],'0.00')
            self.assertEqual(data['totals'][8],'123.45')
            self.assertEqual(data['closing'][8],'1000123.45')
            self.assertEqual(account_balance(s,account),account.opening+12345)
            self.assertTrue(any(r[0]=='Не распределено' and r[1][8]=='123.45' for r in export_rows(data)))
            row=next(r for r in data['rows'] if r['category_id']==unallocated)
            self.assertEqual((row['activity'],row['kind'],row['values'][8]),('unclassified','in','123.45'))

    def test_complete_plan_remains_complete_in_ordered_and_grouped_reports(self):
        with unit(True) as s:
            account=self.scoped(s)
            before=self.read_report(s)
            baseline={r['key']:0 for r in before['rows']}
            first_income=next(r for r in before['rows'] if r['kind']=='in')
            first_out=next(r for r in before['rows'] if r['kind']=='out')
            baseline[first_income['key']]=10000
            baseline[first_out['key']]=-3000
            s.add(CashPlan(company_id=self.company,month=self.month,currency='UZS',scenario='A',
                           opening=50000,payload=json.dumps(baseline)))
            # Exercise ZUMA's catalogue-ordered presentation as well as underlying activity groups.
            ids=[r['category_id'] for r in before['rows']]
            s.add(Setting(key=f'company:{self.company}:category_catalog',value=json.dumps({'order':ids,'retired':[]})))
            s.flush()
            planned=self.read_report(s)
            self.assertEqual(planned['plan_totals'][8],'70.00')
            self.assertEqual(planned['plan_closing'][8],'570.00')
            expected_display={g['display_kind']:g['plan'][8] for g in planned['display_groups']}
            unallocated,_,_=self.add_fact(s,account)
            after=self.read_report(s)
            self.assertEqual(after['plan_totals'][8],planned['plan_totals'][8])
            self.assertEqual(after['plan_closing'][8],planned['plan_closing'][8])
            self.assertEqual({g['display_kind']:g['plan'][8] for g in after['display_groups']},expected_display)
            unknown=next(g for g in after['groups'] if g['activity']=='unclassified')
            self.assertEqual((unknown['values'][8],unknown['plan'][8]),('123.45',None))
            exported=export_rows(after)
            self.assertTrue(any(row[0]=='Не распределено' and row[1][8]=='123.45' for row in exported))
            self.assertEqual(next(row for row in exported if row[0]=='Чистое изменение денег')[2][8],'70.00')

    def test_assigning_article_moves_fact_without_changing_balance(self):
        with unit(True) as s:
            account=self.scoped(s)
            unallocated,entry,user=self.add_fact(s,account)
            income=s.scalar(select(Category).where(Category.company_id==self.company,
                Category.type=='income',Category.activity=='operating',Category.id!=unallocated))
            batch=BankStatementBatch(company_id=self.company,account_id=account.id,created_by=user.id,
                filename='synthetic.xls',sha256='b'*64,account_number='20200000000000000001',
                owner_name='Synthetic company',owner_inn='300000001',currency='UZS',
                period_from=entry.date,period_to=entry.date,opening=account.opening,
                closing=account.opening+12345,debit_total=0,credit_total=12345,
                balance_matches=True,declared_totals_match=True,source_ready=True,status='committed')
            s.add(batch);s.flush()
            line=BankStatementLine(company_id=self.company,batch_id=batch.id,account_id=account.id,
                line=6,date=entry.date,direction='in',amount=entry.amount,document_number='SYNTHETIC-DOC',
                correspondent_mfo='00014',correspondent_account='20200000000000000002',
                correspondent_name='Synthetic buyer',correspondent_inn='300000002',purpose='Test',
                fingerprint='f'*64,occurrence=1,status='auto_income_posted',include_in_daily=True,
                ledger_id=entry.id,classification='receipt')
            s.add(line);s.flush()
            line_id,article_id=line.id,income.id
            before=self.read_report(s)
            before_balance=account_balance(s,account)
        headers={**self.h,'X-Company-ID':str(self.company)}
        listing=self.client.get('/api/bank-statements/income-unallocated',headers=headers)
        self.assertEqual(listing.status_code,200,listing.text)
        record=next(item for item in listing.json()['items'] if item['id']==line_id)
        response=self.client.post(f'/api/bank-statements/lines/{line_id}/income-category',headers=headers,
            json={'category_id':article_id,'edit_token':record['edit_token'],'reason':'Проверена статья дохода'})
        self.assertEqual(response.status_code,200,response.text)
        with unit() as s:
            s.info['company_id']=self.company
            account=s.get(Account,self.acc)
            after=self.read_report(s)
            self.assertEqual(account_balance(s,account),before_balance)
            self.assertEqual(after['totals'][8],before['totals'][8])
            self.assertEqual(after['closing'][8],before['closing'][8])
            groups={g['activity']:g['values'][8] for g in after['groups']}
            self.assertEqual(groups['unclassified'],'0.00')
            self.assertEqual(groups['operating'],'123.45')
            self.assertEqual(next(r for r in after['rows'] if r['category_id']==article_id)['values'][8],'123.45')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(UnallocatedCashFlowTests(name) for name in UnallocatedCashFlowTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
