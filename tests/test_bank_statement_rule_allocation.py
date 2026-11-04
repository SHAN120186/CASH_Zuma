"""Existing imported facts can be classified once without a second payment."""
import atexit
import unittest
from datetime import date

from sqlalchemy import select

from tests.test_bank_statement_auto_integration import AutoBankIntegrationTests
from app.bank_statement_models import BankStatementLine
from app.db import Account, Budget, Category, Company, Ledger, engine, unit
from app.services import account_balance, budget_state

atexit.register(engine.dispose)


class ExistingRuleAllocationTests(AutoBankIntegrationTests):
    def posted(self, raw=None):
        response = self.preview(raw or self.statement(False, external=True))
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        saved = self.confirmed(preview)
        self.assertEqual(saved.status_code, 200, saved.text)
        with unit(True) as s:
            category = Category(company_id=self.company, name='Synthetic configured income', type='income', activity='operating')
            s.add(category); s.flush(); self.income_category = category.id
        return preview['id']

    def rules(self, *, incoming=True, outgoing=True):
        loaded = self.client.get('/api/bank-statements/category-rules', headers=self.h)
        self.assertEqual(loaded.status_code, 200, loaded.text)
        items = []
        if incoming:
            items.append({'direction':'in', 'inn':'300000003', 'category_id':self.income_category})
        if outgoing:
            items.append({'direction':'out', 'inn':'300000005', 'purpose_contains':'Synthetic goods', 'category_id':self.cat})
        saved = self.client.put('/api/bank-statements/category-rules', headers=self.h,
            json={'revision':loaded.json()['revision'], 'items':items})
        self.assertEqual(saved.status_code, 200, saved.text)
        return saved.json()

    def review_rules(self, batch, client=None, headers=None):
        result = (client or self.client).post(f'/api/bank-statements/{batch}/category-rules/preview', headers=headers or self.h, json={})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def apply_rules(self, batch, preview, **kwargs):
        return self.client.post(f'/api/bank-statements/{batch}/category-rules/commit', headers=self.h,
            json={'token':preview['token'], 'confirm':True, **kwargs})

    def ledger_snapshot(self):
        with unit() as s:
            return [(e.id, e.account_id, e.to_account_id, e.kind, e.amount, str(e.date), e.reference,
                e.request_id, e.receipt_id, e.reversal_of) for e in s.scalars(select(Ledger).order_by(Ledger.id))]

    def test_existing_income_and_expense_change_only_categories_and_cashflow(self):
        batch = self.posted(); before = self.ledger_snapshot()
        with unit() as s:
            opening_balance = account_balance(s, s.get(Account,self.acc))
        self.rules(); preview = self.review_rules(batch)
        self.assertEqual(preview['ready_count'],2)
        self.assertEqual(self.ledger_snapshot(),before)
        result = self.apply_rules(batch,preview)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['allocated'],2)
        self.assertEqual(self.ledger_snapshot(),before)
        self.assertEqual(self.apply_rules(batch,preview).status_code,409)
        self.assertEqual(self.review_rules(batch)['ready_count'],0)
        with unit() as s:
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),opening_balance)
            selected = {line.document_number:s.get(Ledger,line.ledger_id).category_id for line in s.scalars(
                select(BankStatementLine).where(BankStatementLine.batch_id==batch))}
            self.assertEqual(selected['OTH001'],self.cat)
            self.assertEqual(selected['INC001'],self.income_category)
        report = self.client.get('/api/report',headers=self.h,params={'company_id':self.company,'year':int(self.date[:4]),'currency':'UZS'})
        self.assertEqual(report.status_code,200,report.text)
        month = int(self.date[5:7])-1
        values = {row['category_id']:row['values'][month] for row in report.json()['rows'] if row.get('category_id')}
        self.assertEqual(values[self.cat],'-75.00')
        self.assertEqual(values[self.income_category],'200.00')
        self.assertEqual(report.json()['totals'][month],'105.00')

    def test_rule_change_and_missing_confirmation_do_not_mutate_existing_entries(self):
        batch=self.posted(); self.rules(); preview=self.review_rules(batch); before=self.ledger_snapshot()
        self.assertEqual(self.apply_rules(batch,preview,confirm=False).status_code,422)
        self.rules(incoming=False)
        self.assertEqual(self.apply_rules(batch,preview).status_code,409)
        self.assertEqual(self.ledger_snapshot(),before)
        with unit() as s:
            self.assertNotEqual(s.scalar(select(Ledger.category_id).where(Ledger.kind=='out',Ledger.amount==7500)),self.cat)

    def test_hard_and_soft_budget_overflow_keep_original_unallocated_fact(self):
        batch=self.posted(); self.rules(incoming=False)
        with unit(True) as s:
            budget=Budget(category_id=self.cat,month=self.month,currency='UZS',amount=1000,mode='hard')
            s.add(budget);s.flush(); budget_id=budget.id
        before=self.ledger_snapshot()
        for mode in ('hard','soft'):
            with unit(True) as s:s.get(Budget,budget_id).mode=mode
            preview=self.review_rules(batch)
            self.assertEqual(preview['ready_count'],0)
            self.assertEqual(next(row for row in preview['rows'] if row['direction']=='out')['status'],'budget')
            self.assertEqual(self.apply_rules(batch,preview).status_code,409)
            self.assertEqual(self.ledger_snapshot(),before)
            with unit() as s:self.assertEqual(budget_state(s,self.cat,self.month,'UZS')['spent'],0)

    def test_cumulative_budget_assigns_first_row_and_keeps_later_row_unallocated(self):
        raw=self.statement(False,external=True).replace(b'75.00',b'40.00')
        day=date.fromisoformat(self.date).strftime('%d.%m.%Y')
        extra=(f'<tr><td>{day}</td><td>OTH002</td><td>00014</td><td>20200000000000000002</td>'
            '<td>Synthetic supplier</td><td>300000005</td><td>Synthetic goods purchase</td><td>35.00</td><td></td></tr>').encode('cp1251')
        raw=raw.replace('<tr><td>Итоговый оборот'.encode('cp1251'),extra+'<tr><td>Итоговый оборот'.encode('cp1251'))
        batch=self.posted(raw);self.rules(incoming=False)
        with unit(True) as s:s.add(Budget(category_id=self.cat,month=self.month,currency='UZS',amount=5000,mode='hard'))
        before=self.ledger_snapshot();preview=self.review_rules(batch)
        outgoing=[row for row in preview['rows'] if row['direction']=='out']
        self.assertEqual([(row['amount'],row['status']) for row in outgoing],[('40.00','ready'),('35.00','budget')])
        result=self.apply_rules(batch,preview);self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['allocated'],1);self.assertEqual(self.ledger_snapshot(),before)
        with unit() as s:self.assertEqual(budget_state(s,self.cat,self.month,'UZS')['spent'],4000)

    def test_budget_changed_after_preview_requires_new_confirmation(self):
        batch=self.posted();self.rules(incoming=False);preview=self.review_rules(batch)
        with unit(True) as s:s.add(Budget(category_id=self.cat,month=self.month,currency='UZS',amount=1000,mode='hard'))
        self.assertEqual(self.apply_rules(batch,preview).status_code,409)
        self.assertEqual(self.review_rules(batch)['ready_count'],0)

    def test_company_scope_and_payer_can_apply_only_saved_rules(self):
        batch=self.posted();self.rules()
        with unit() as s:other=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
        hidden=self.client.post(f'/api/bank-statements/{batch}/category-rules/preview',headers={**self.h,'X-Company-ID':str(other)},json={})
        self.assertEqual(hidden.status_code,404,hidden.text)
        preview=self.review_rules(batch,self.accountant[0],self.accountant[1])
        result=self.accountant[0].post(f'/api/bank-statements/{batch}/category-rules/commit',headers=self.accountant[1],json={'token':preview['token'],'confirm':True})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['allocated'],2)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ExistingRuleAllocationTests(name) for name in ExistingRuleAllocationTests.__dict__ if name.startswith('test_'))
