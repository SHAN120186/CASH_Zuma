"""Synthetic read-only reconciliation: cash transfers are never extra expenses."""
from datetime import date
import unittest
from sqlalchemy import select, func, text
from tests import test_site as base
from tests.test_cash_funding_batch import CashFundingBatchTests
from app.db import Account, CashFunding, Ledger, User, unit, engine, initialize
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.services import log
from app.daily_reconciliation import build, balances
from app.services import account_balance

def tearDownModule():
    base.tearDownModule()

def load_tests(loader, tests, pattern):
    return unittest.TestSuite(DailyReconciliationTests(name)
        for name in DailyReconciliationTests.__dict__ if name.startswith('test_'))


class DailyReconciliationTests(base.SiteTests):
    fixture = CashFundingBatchTests.fixture
    cash_payer = CashFundingBatchTests.cash_payer
    unresolved_bank_review = CashFundingBatchTests.unresolved_bank_review
    cash_request = CashFundingBatchTests.cash_request
    funding_action = CashFundingBatchTests.funding_action

    def test_partner_withdrawal_and_receipt_keep_net_and_holdback_separate(self):
        request_id=self.cash_request(submit=False)
        plan={'mode':'batch','source':'counterparty','intermediary_name':'Synthetic partner',
              'bank_account_id':self.acc,'gross_amount':'1000000','fee_percent':'1','fee_category_id':self.fee_cat}
        response=self.client.put(f'/api/requests/{request_id}',headers=self.h,json={
            **self.request_body('990000',account=self.cash),'version':self.row(request_id)['version'],
            'reason':'Synthetic partner full lifecycle','cash_funding':plan})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.upload(request_id).status_code,200)
        self.assertEqual(self.post(f'/api/requests/{request_id}/decision',{'action':'submit'}).status_code,200)
        self.assertEqual(self.approve(request_id).status_code,200)
        self.funding_action(request_id,'withdraw',*self.stage_user('check'))
        self.funding_action(request_id,'receive',*self.cash_payer())
        with unit() as s:
            funding=s.scalar(select(CashFunding).where(CashFunding.request_id==request_id))
            self.assertEqual(s.get(Ledger,funding.fee_ledger_id).counterparty,'Synthetic partner')
            self.assertEqual(s.get(Ledger,funding.fee_ledger_id).amount,1000000)
            self.assertEqual(s.get(Ledger,funding.transfer_ledger_id).amount,99000000)
            self.assertEqual(account_balance(s,s.get(Account,self.cash)),99000000)
            self.assertIsNone(s.scalar(select(Ledger.id).where(Ledger.request_id==request_id)))
        body=self.report_day().json()
        self.assertEqual(body['partner_fees'],'10000.00')
        self.assertEqual(body['bank_fees'],'0.00')
        self.assertEqual(body['requests']['paid_count'],0)

    def test_old_schema_migration_preserves_existing_funding_and_amounts(self):
        self.fixture()
        with engine.begin() as conn:
            for field in ('source','intermediary_name','intermediary_inn'):
                conn.execute(text(f'ALTER TABLE request_cash_fundings DROP COLUMN {field}'))
        initialize()
        with unit() as s:
            funding=s.get(CashFunding,self.funding_id)
            self.assertEqual(funding.source,'bank')
            self.assertEqual(funding.intermediary_name,'')
            self.assertEqual(funding.net_amount,99000000)
            self.assertEqual(funding.state,'received')

    def test_intermediary_metadata_saved_and_old_client_cannot_erase_it(self):
        request_id=self.cash_request(submit=False)
        plan={'mode':'batch','source':'counterparty','intermediary_name':'Synthetic partner',
              'intermediary_inn':'123456789','bank_account_id':self.acc,'gross_amount':'1000000',
              'fee_percent':'1','fee_category_id':self.fee_cat}
        body={**self.request_body('990000',account=self.cash),'version':self.row(request_id)['version'],
              'reason':'Synthetic partner metadata edit','cash_funding':plan}
        response=self.client.put(f'/api/requests/{request_id}',json=body,headers=self.h)
        self.assertEqual(response.status_code,200,response.text)
        funding=response.json()['cash_funding']
        self.assertEqual(funding['source'],'counterparty')
        self.assertEqual(funding['intermediary_name'],'Synthetic partner')
        body['version']=response.json()['version']
        body['cash_funding']={k:v for k,v in plan.items() if k not in ('source','intermediary_name','intermediary_inn')}
        denied=self.client.put(f'/api/requests/{request_id}',json=body,headers=self.h)
        self.assertEqual(denied.status_code,409,denied.text)
        self.assertEqual(self.row(request_id)['cash_funding']['intermediary_name'],'Synthetic partner')

    def test_report_is_scoped_to_selected_company_and_currency(self):
        self.fixture()
        with unit() as s:
            from app.db import Company
            other=s.scalar(select(Company.id).where(Company.id!=self.company,Company.code!='UNASSIGNED'))
        headers={**self.h,'X-Company-Id':str(other)}
        response=self.client.get('/api/bank-statements/reconciliation-day',headers=headers,
                                 params={'date':self.date,'currency':'UZS'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['fundings'],[])
        response=self.client.get('/api/bank-statements/reconciliation-day',headers=self.h,
                                 params={'date':self.date,'currency':'USD'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['fundings'],[])

    def test_resolved_statement_refreshes_cache_and_duplicate_link_blocks_reconciliation(self):
        batch_id, line_id = self.unresolved_bank_review()
        first = self.report_day().json()
        self.assertEqual(len(first['review']), 1)
        self.assertTrue(self.report_day().json()['cached'])
        with unit(True) as s:
            s.info['company_id'] = self.company
            user = s.scalar(select(User).where(User.username == 'admin'))
            line = s.get(BankStatementLine, line_id)
            entry = Ledger(account_id=self.acc, category_id=self.cat, kind='out', amount=line.amount,
                           date=line.date, reference='SYNTHETIC-DAILY-POST', creator_id=user.id)
            s.add(entry); s.flush()
            line.ledger_id = entry.id; line.status = 'other_matched'
            log(s, user, 'Synthetic daily test change', 'ledger', entry.id, 'Synthetic fixture')
        updated = self.report_day().json()
        self.assertFalse(updated['cached'])
        self.assertTrue(updated['banks'][0]['reconciled'])
        self.assertEqual(updated['banks'][0]['difference'], '0.00')
        with unit(True) as s:
            s.info['company_id'] = self.company
            original = s.get(BankStatementLine, line_id)
            duplicate = BankStatementLine(**{column.name:getattr(original,column.name)
                for column in BankStatementLine.__table__.columns if column.name!='id'})
            duplicate.line = 2
            s.add(duplicate)
            log(s,s.scalar(select(User).where(User.username=='admin')),
                'Synthetic duplicate fixture','bank_statement',batch_id,'Synthetic fixture')
        duplicated = self.report_day().json()
        self.assertFalse(duplicated['banks'][0]['reconciled'])
        self.assertEqual(len(duplicated['review']), 2)

    def test_deleted_statement_is_not_reported_as_reconciled(self):
        batch_id, _ = self.unresolved_bank_review()
        self.report_day()
        with unit(True) as s:
            s.info['company_id'] = self.company
            s.get(BankStatementBatch,batch_id).status = 'deleted'
            log(s,s.scalar(select(User).where(User.username=='admin')),
                'Synthetic deletion fixture','bank_statement',batch_id,'Synthetic fixture')
        body=self.report_day().json()
        self.assertFalse(body['cached'])
        self.assertFalse(body['banks'][0]['available'])
        self.assertFalse(body['banks'][0]['reconciled'])
        self.assertEqual(body['review'],[])

    def report_day(self, client=None):
        cl, headers = client or (self.client, self.h)
        return cl.get('/api/bank-statements/reconciliation-day', headers=headers,
                      params={'date': self.date, 'currency': 'UZS'})

    def test_report_caches_without_posting_or_claiming_missing_statement(self):
        self.fixture()
        with unit() as s:
            before = s.scalar(select(func.count(Ledger.id)))
        first = self.report_day()
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertFalse(body['cached'])
        self.assertFalse(body['banks'][0]['reconciled'])
        self.assertIsNone(body['banks'][0]['bank_closing'])
        self.assertEqual(body['cash'][0]['closing'], '990000.00')
        self.assertEqual(body['requests']['planned_count'], 2)
        self.assertEqual(body['requests']['paid_count'], 0)
        self.assertEqual(body['fundings'][0]['remaining'], '990000.00')
        self.assertEqual(body['bank_fees'], '10000.00')
        self.assertTrue(self.report_day().json()['cached'])
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count(Ledger.id))), before)

    def test_partner_holdback_is_not_bank_commission(self):
        self.fixture(received=False)
        with unit(True) as s:
            funding = s.get(CashFunding, self.funding_id)
            funding.source = 'counterparty'
            funding.intermediary_name = 'Synthetic intermediary'
        body = self.report_day().json()
        self.assertEqual(body['partner_fees'], '10000.00')
        self.assertEqual(body['bank_fees'], '0.00')
        self.assertEqual(body['fundings'][0]['in_transit'], '990000.00')
        self.assertEqual(body['fundings'][0]['remaining'], '0.00')
        self.assertEqual(body['cash'][0]['closing'], '0.00')

    def test_aggregate_balances_match_existing_money_logic(self):
        self.fixture()
        with unit() as s:
            accounts = list(s.scalars(select(Account)))
            result = balances(s, accounts, date.fromisoformat(self.date))
            for account in accounts:
                self.assertEqual(result[account.id]['closing'], account_balance(s, account, date.fromisoformat(self.date)))

    def test_employee_cannot_read_company_reconciliation(self):
        employee = self.make_user('employee', 'daily_employee')
        try:
            self.assertEqual(self.report_day(employee).status_code, 403)
        finally:
            employee[0].close()
