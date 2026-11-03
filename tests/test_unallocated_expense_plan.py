"""Unclassified bank expenses never become planned articles."""
import io
import json
import unittest
from datetime import date

from openpyxl import Workbook
from sqlalchemy import select
from tests.test_site import SiteTests
from app.cash_report import expense_plan_summary, report
from app.db import Account, CashPlan, Category, Ledger, User, engine, unit


class UnallocatedExpensePlanTests(SiteTests):
    def test_fact_and_legacy_payload_do_not_change_complete_expense_plan(self):
        with unit(True) as s:
            s.info['company_id']=self.company
            account=s.get(Account,self.acc)
            existing=report(s,2026,'UZS',self.company,as_of=date.fromisoformat(self.date))
            payload={r['key']:0 for r in existing['rows']}
            outcome=next(r for r in existing['rows'] if r['kind']=='out')
            payload[outcome['key']]=-4321
            plan=CashPlan(company_id=self.company,month=self.month,currency='UZS',scenario='A',
                          opening=50000,payload=json.dumps(payload))
            s.add(plan);s.flush()
            before=expense_plan_summary(s,self.month,'UZS',self.company,as_of=date.fromisoformat(self.date))
            self.assertTrue(before['complete']);self.assertEqual(before['known'],4321)
            unallocated=Category(company_id=self.company,name='Synthetic pending classification',
                type='outcome',activity='unclassified',cost_group='other')
            s.add(unallocated);s.flush()
            after_category=expense_plan_summary(s,self.month,'UZS',self.company,as_of=date.fromisoformat(self.date))
            self.assertEqual(after_category,before)
            user=s.scalar(select(User).where(User.username=='admin').execution_options(company_unscoped=True))
            s.add(Ledger(account_id=account.id,category_id=unallocated.id,kind='out',amount=1234,
                date=date.fromisoformat(self.date),counterparty='Synthetic supplier',reference='SYNTHETIC',
                note='Synthetic bank statement',creator_id=user.id));s.flush()
            self.assertEqual(expense_plan_summary(s,self.month,'UZS',self.company,as_of=date.fromisoformat(self.date)),before)
            payload[f'{unallocated.id}:out']=-999999
            plan.payload=json.dumps(payload);s.flush()
            self.assertEqual(expense_plan_summary(s,self.month,'UZS',self.company,as_of=date.fromisoformat(self.date)),before)
            self.assertEqual(report(s,2026,'UZS',self.company,as_of=date.fromisoformat(self.date))['plan_totals'][8],'-43.21')

    def test_manual_and_import_plan_cannot_target_unclassified_article(self):
        with unit(True) as s:
            s.info['company_id']=self.company
            category=Category(company_id=self.company,name='Synthetic unclassified expense',
                type='outcome',activity='unclassified',cost_group='other')
            s.add(category);s.flush();category_id=category.id
        headers={**self.h,'X-Company-ID':str(self.company)}
        response=self.client.post('/api/cash-plan',headers=headers,json={'company_id':self.company,'month':'2026-01',
            'currency':'UZS','scenario':'A','version':0,'reason':'Synthetic test plan',
            'items':[{'category_id':category_id,'kind':'out','amount':'123'}]})
        self.assertEqual(response.status_code,422,response.text)
        wb=Workbook();ws=wb.active;ws.title='план на год';ws['H7']='Б';ws['I7']='А';ws['J7']='В'
        ws['A8']=date(2026,1,1);ws['E8']='Жами ҳаражатлар'
        ws['A9']=date(2026,1,1);ws['B9']=1;ws['C9']='61';ws['E9']='Synthetic unclassified expense';ws['I9']=100
        output=io.BytesIO();wb.save(output);wb.close()
        url=f'/api/plan-import/preview?company_id={self.company}&year=2026&month_from=1&month_to=1'
        preview=self.client.post(url,content=output.getvalue(),headers={**headers,'Content-Type':'application/octet-stream','X-Filename':'synthetic.xlsx'})
        self.assertEqual(preview.status_code,200,preview.text)
        data=preview.json();self.assertNotIn(category_id,[c['id'] for c in data['categories']])
        key=data['rows'][0]['source_key']
        for selection in (str(category_id),'new'):
            response=self.client.post(f"/api/plan-import/{data['id']}/commit",headers=headers,
                json={'mappings':{key:selection},'reason':'Synthetic plan import test'})
            self.assertEqual(response.status_code,422,response.text)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(UnallocatedExpensePlanTests(name) for name in UnallocatedExpensePlanTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
