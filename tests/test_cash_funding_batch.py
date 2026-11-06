"""Disposable cash desk: an internal funding envelope never duplicates expenses."""
import json
import os
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select, func, event, text, inspect

from tests.test_cash_funding import CashFundingTests
from tests import test_site as base
from app.db import (Account, Category, Company, CompanyUser, PaymentRequest, CashFunding,
                    CashFundingPaymentGroup, CashFundingPaymentItem, Ledger, User, Budget,
                    Setting, RequestDocument, initialize, engine, unit)
from app.services import account_balance, funds_state, budget_state, today, money, amount


class CashFundingBatchTests(CashFundingTests):
    def test_api_mode_draft_approvals_withdraw_receive_and_issue(self):
        parent=self.cash_request(submit=False)
        self.assertEqual(self.row(parent)['cash_funding']['mode'],'single')
        plan={'bank_account_id':self.acc,'gross_amount':'1000000','fee_percent':'1','fee_category_id':self.fee_cat,'mode':'batch'}
        edit={**self.request_body('990000',account=self.cash),'version':self.row(parent)['version'],
              'reason':'Synthetic explicit group funding mode','cash_funding':plan}
        response=self.client.put(f'/api/requests/{parent}',json=edit,headers=self.h)
        self.assertEqual(response.status_code,200,response.text)
        old_client={**edit,'version':response.json()['version'],'cash_funding':{k:v for k,v in plan.items() if k!='mode'}}
        denied=self.client.put(f'/api/requests/{parent}',json=old_client,headers=self.h)
        self.assertEqual(denied.status_code,409,denied.text)
        self.assertEqual(self.row(parent)['cash_funding']['mode'],'batch')
        self.budget('910000','hard')
        fee_budget=self.post('/api/budgets',{'category_id':self.fee_cat,'month':self.month,'currency':'UZS',
            'amount':'10000','mode':'hard','reason':'Synthetic commission budget','source':'synthetic'})
        self.assertEqual(fee_budget.status_code,200,fee_budget.text)
        self.children=[];self.parent=parent
        for value in ('500000','410000'):
            created=self.post('/api/requests',self.request_body(value,account=self.cash))
            self.assertEqual(created.status_code,200,created.text);id=created.json()['id'];self.children.append(id)
            self.assertEqual(self.upload(id).status_code,200)
            self.assertEqual(self.post(f'/api/requests/{id}/decision',{'action':'submit'}).status_code,200)
            approved=self.approve(id);self.assertEqual(approved.status_code,200,approved.text)
        self.assertEqual(self.upload(parent).status_code,200)
        submitted=self.post(f'/api/requests/{parent}/decision',{'action':'submit'})
        self.assertEqual(submitted.status_code,200,submitted.text)
        approved=self.approve(parent);self.assertEqual(approved.status_code,200,approved.text)
        with unit() as s:
            self.assertEqual(budget_state(s,self.cat,self.month,'UZS')['reserved'],91000000)
            self.assertEqual(budget_state(s,self.fee_cat,self.month,'UZS')['reserved'],1000000)
        dash=self.client.get('/api/dashboard',headers=self.h).json()
        self.assertEqual(dash['outgoing7'],'920000.00')
        self.funding_action(parent,'withdraw',*self.stage_user('check'))
        self.funding_action(parent,'receive',*self.cash_payer())
        self.pay()
        self.assertEqual(self.report()['totals'][8],'-920000.00')
        self.assertEqual(self.state()['remaining_amount'],'80000.00')

    def test_existing_funding_mode_migration_preserves_legacy_facts(self):
        self.fixture()
        with unit(True) as s:s.get(CashFunding,self.funding_id).mode='single'
        with engine.begin() as connection:connection.execute(text('ALTER TABLE request_cash_fundings DROP COLUMN mode'))
        initialize()
        self.assertIn('mode',{column['name'] for column in inspect(engine).get_columns('request_cash_fundings')})
        with unit() as s:
            funding=s.get(CashFunding,self.funding_id)
            self.assertEqual(funding.mode,'single');self.assertEqual(funding.net_amount,99000000)
            self.assertEqual(funding.state,'received');self.assertIsNotNone(funding.receipt_ledger_id)

    def fixture(self, amounts=('500000','410000'), received=True):
        """Seed only disposable facts; root may reuse this helper for local UI QA."""
        self.cash_payer()
        with unit(True) as s:
            author=s.scalar(select(User).where(User.username=='admin'))
            actors={}
            for role in ('accountant','finance'):
                actor=User(username='batch_'+role,name='Synthetic '+role,role=role,password_hash=base.HASH)
                s.add(actor);s.flush();s.add(CompanyUser(company_id=self.company,user_id=actor.id,role=role));actors[role]=actor.id
            cash=Account(company_id=self.company,name='Synthetic batch cash',kind='cash',currency='UZS',opening=0,
                         opening_date=date.fromisoformat(self.date)-timedelta(days=10),created_by=author.id)
            fee=Category(company_id=self.company,name='Synthetic batch fee',type='outcome',activity='operating',cost_group='other')
            s.add_all([cash,fee]);s.flush();self.cash=cash.id;self.fee_cat=fee.id
            def approved(value,name):
                row=PaymentRequest(creator_id=author.id,last_editor_id=author.id,category_id=self.cat,account_id=cash.id,
                    amount=amount(value),counterparty=name,purpose='Synthetic approved cash expense',
                    due_date=date.fromisoformat(self.date),status='approved',route_policy='skip',checked_by=actors['accountant'],
                    finance_approved_by=actors['finance'],approved_at=datetime.combine(date.fromisoformat(self.date),datetime.min.time()))
                s.add(row);s.flush();return row
            parent=approved('990000','Synthetic funding envelope')
            children=[approved(value,'Synthetic recipient '+str(i)) for i,value in enumerate(amounts)]
            transit=Account(company_id=self.company,name='Деньги в пути · UZS',kind='transit',currency='UZS',opening=0,
                opening_date=date(2000,1,1),created_by=author.id);s.add(transit);s.flush()
            legs=[Ledger(account_id=self.acc,to_account_id=transit.id,kind='transfer',amount=99000000,date=date.fromisoformat(self.date),
                         reference='SYNTH-WITHDRAW',creator_id=author.id),
                  Ledger(account_id=self.acc,category_id=fee.id,kind='out',amount=1000000,date=date.fromisoformat(self.date),
                         reference='SYNTH-FEE',creator_id=author.id)]
            if received:
                legs.append(Ledger(account_id=transit.id,to_account_id=cash.id,kind='transfer',amount=99000000,
                    date=date.fromisoformat(self.date),reference='SYNTH-RECEIVE',creator_id=author.id))
            s.add_all(legs);s.flush()
            funding=CashFunding(company_id=self.company,request_id=parent.id,mode='batch',bank_account_id=self.acc,
                cash_account_id=cash.id,transit_account_id=transit.id,gross_amount=100000000,fee_amount=1000000,
                fee_percent='1.00',net_amount=99000000,fee_category_id=fee.id,state='received' if received else 'in_transit',
                withdraw_date=date.fromisoformat(self.date),receive_date=date.fromisoformat(self.date) if received else None,
                transfer_ledger_id=legs[0].id,fee_ledger_id=legs[1].id,receipt_ledger_id=legs[2].id if received else None,
                withdraw_reference='Synthetic withdrawal',receive_reference='Synthetic receipt' if received else '',created_by=author.id)
            s.add(funding);s.flush()
            self.parent=parent.id;self.children=[row.id for row in children];self.funding_id=funding.id
        return {'parent':self.parent,'children':self.children,'cash':self.cash,'funding_id':self.funding_id}

    def state(self,client=None):
        cl,h=client or self.cash_payer()
        response=cl.get(f'/api/requests/{self.parent}/cash-funding/payments',headers=h)
        self.assertEqual(response.status_code,200,response.text);return response.json()

    def body(self,ids=None,key='synthetic-cash-batch-key-0001'):
        row=self.row(self.parent)
        return {'version':row['cash_funding']['version'],'request_version':row['version'],
                'items':[{'id':id,'version':self.row(id)['version']} for id in (self.children if ids is None else ids)],
                'key':key,'date':self.date,'reference':'Synthetic group issue','note':'Synthetic completed cash issue'}

    def preview(self,body=None,client=None):
        return self.post(f'/api/requests/{self.parent}/cash-funding/payments/preview',body or self.body(),*(client or self.cash_payer()))

    def pay(self,body=None):
        body=body or self.body();preview=self.preview(body)
        self.assertEqual(preview.status_code,200,preview.text)
        payload={**body,'token':preview.json()['token'],'confirm':True}
        response=self.post(f'/api/requests/{self.parent}/cash-funding/payments/commit',payload,*self.cash_payer())
        self.assertEqual(response.status_code,200,response.text)
        return response.json(),payload,preview.json()

    def test_full_issue_leftover_cashflow_and_retry(self):
        self.fixture();before=self.state();self.assertEqual(len(before['candidates']),2)
        self.assertEqual(before['remaining_amount'],'990000.00')
        result,payload,preview=self.pay()
        self.assertEqual(preview['total_amount'],'910000.00')
        self.assertEqual((preview['remaining_amount'],preview['cash_balance']),('80000.00','80000.00'))
        self.assertEqual(result['remaining_amount'],'80000.00')
        self.assertEqual(result['paid_request_ids'],self.children)
        self.assertTrue(all(self.row(id)['status']=='paid' for id in self.children))
        self.assertEqual(self.row(self.parent)['status'],'approved')
        self.assertNotIn('pay',self.row(self.parent)['actions'])
        repeated=self.post(f'/api/requests/{self.parent}/cash-funding/payments/commit',payload,*self.cash_payer())
        self.assertEqual(repeated.status_code,200,repeated.text);self.assertEqual(repeated.json(),result)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),5)
            self.assertIsNone(s.scalar(select(Ledger.id).where(Ledger.request_id==self.parent)))
            self.assertEqual(account_balance(s,s.get(Account,self.cash)),8000000)
            self.assertEqual(s.scalar(select(func.count()).select_from(CashFundingPaymentGroup)),1)
        self.assertEqual(self.report()['totals'][8],'-920000.00')
        dash=self.client.get('/api/dashboard',headers=self.h).json()
        self.assertEqual(dash['fact_out'],'920000.00');self.assertEqual(dash['outgoing7'],'0.00')
        self.assertEqual(self.state()['groups'][0]['request_ids'],self.children)

    def test_preview_has_no_writes_and_rollback_is_atomic(self):
        self.fixture();body=self.body();before=self.state()
        preview=self.preview(body);self.assertEqual(preview.status_code,200,preview.text)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),3)
            self.assertEqual(s.scalar(select(func.count()).select_from(CashFundingPaymentGroup)),0)
        import app.cash_funding_payments as module
        original=module.post_ledger;calls=[]
        def fail_second(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:raise HTTPException(409,'Synthetic second-row failure')
            return original(*args,**kwargs)
        with patch.object(module,'post_ledger',side_effect=fail_second):
            failure=self.post(f'/api/requests/{self.parent}/cash-funding/payments/commit',
                {**body,'token':preview.json()['token'],'confirm':True},*self.cash_payer())
        self.assertEqual(failure.status_code,409,failure.text)
        self.assertEqual(self.state(),before)
        self.assertTrue(all(self.row(id)['status']=='approved' for id in self.children))

    def test_source_cannot_be_paid_as_expense_or_used_before_receipt(self):
        self.fixture(received=False)
        self.assertFalse(self.state()['can_pay']);self.assertEqual(self.preview().status_code,409)
        bad=self.ledger('990000','out',reference='Unsafe direct parent payment',client=self.cash_payer(),
            account_id=self.cash,category_id=self.cat,request_id=self.parent)
        self.assertEqual(bad.status_code,409,bad.text)
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),2)

    def test_cap_cash_and_date_boundaries(self):
        self.fixture(amounts=('990000',))
        body=self.body();self.assertEqual(self.preview(body).status_code,200)
        with unit(True) as s:s.get(PaymentRequest,self.children[0]).amount+=1
        self.assertEqual(self.preview(self.body()).status_code,409)
        with unit(True) as s:s.get(PaymentRequest,self.children[0]).amount=99000000;s.get(Account,self.cash).opening=-1
        self.assertEqual(self.preview(self.body()).status_code,409)
        with unit(True) as s:s.get(Account,self.cash).opening=0
        for when in (str(today()+timedelta(days=1)),str(today()-timedelta(days=1))):
            self.assertEqual(self.preview({**self.body(),'date':when}).status_code,422)

    def test_stale_versions_budget_or_journal_reject_preview_token(self):
        self.fixture();body=self.body();preview=self.preview(body);self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(self.ledger('1','in',reference='Later cash income',account_id=self.cash).status_code,200)
        failure=self.post(f'/api/requests/{self.parent}/cash-funding/payments/commit',
            {**body,'token':preview.json()['token'],'confirm':True},*self.cash_payer())
        self.assertEqual(failure.status_code,409,failure.text)
        self.assertTrue(all(self.row(id)['status']=='approved' for id in self.children))
        with unit(True) as s:s.get(PaymentRequest,self.children[0]).decision_note='Changed after selection'
        self.assertEqual(self.preview(body).status_code,409)

    def test_aggregate_cross_month_budget_rejects_preview(self):
        self.fixture(amounts=('60','60'))
        with unit(True) as s:
            for id in self.children:s.get(PaymentRequest,id).due_date=date(2026,8,20)
            s.add(Budget(category_id=self.cat,month=self.month,currency='UZS',amount=10000,mode='hard',source='synthetic'))
        response=self.preview();self.assertEqual(response.status_code,409,response.text)
        self.assertIn('Совокупная',response.json()['detail'])
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),3)

    def test_roles_sections_company_and_private_cash_balance(self):
        self.fixture();worker=self.make_user('procurement','batch_owner')
        with unit(True) as s:
            owner=s.scalar(select(User).where(User.username=='batch_owner'));s.get(PaymentRequest,self.parent).creator_id=owner.id
        hidden=self.state(worker)
        self.assertIsNone(hidden['cash_balance']);self.assertIsNone(hidden['allocated_amount'])
        self.assertEqual(hidden['groups'],[]);self.assertEqual(hidden['candidates'],[])
        self.assertEqual(self.preview(client=worker).status_code,403)
        with unit() as s:other=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
        denied=self.client.get(f'/api/requests/{self.parent}/cash-funding/payments',headers={**self.h,'X-Company-ID':str(other)})
        self.assertEqual(denied.status_code,404,denied.text)
        with unit(True) as s:
            cashier=s.scalar(select(User).where(User.username=='cash_receiver'))
            s.add(Setting(key=f'sections:{self.company}:{cashier.id}',value=json.dumps({'version':1,'sections':{'ledger':'hidden'}})))
        self.assertFalse(self.state()['can_pay']);self.assertIsNone(self.state()['cash_balance'])

    def test_selection_sod_payload_and_reused_key(self):
        self.fixture();body=self.body()
        self.assertEqual(self.preview({**body,'items':[body['items'][0]]*2}).status_code,422)
        self.assertEqual(self.preview({**body,'items':body['items']*51}).status_code,422)
        self.assertEqual(self.preview({**body,'version':True}).status_code,422)
        with unit(True) as s:
            cashier=s.scalar(select(User).where(User.username=='cash_receiver'))
            s.get(PaymentRequest,self.children[1]).last_editor_id=cashier.id
        self.assertEqual(self.preview().status_code,403)
        self.assertEqual(len(self.state()['candidates']),1)
        result,payload,_=self.pay(self.body(ids=[self.children[0]]))
        forged={**payload,'items':[payload['items'][0],{'id':self.children[1],'version':self.row(self.children[1])['version']}]}
        self.assertEqual(self.post(f'/api/requests/{self.parent}/cash-funding/payments/commit',forged,*self.cash_payer()).status_code,409)

    def test_group_items_protected_and_soft_delete_does_not_refund_cap(self):
        self.fixture();result,_,_=self.pay();leg=result['group']['ledger_ids'][0]
        response=self.post(f'/api/ledger/{leg}/reverse',{'reason':'Synthetic unsafe group reversal'})
        self.assertEqual(response.status_code,409,response.text)
        deleted=self.post(f'/api/requests/{self.children[0]}/delete',{'version':self.row(self.children[0])['version'],
            'reason':'Synthetic request hidden with financial history preserved'})
        self.assertEqual(deleted.status_code,200,deleted.text)
        self.assertEqual(self.state()['remaining_amount'],'80000.00')
        with self.assertRaises(HTTPException):
            with unit(True) as s:
                item=s.scalar(select(CashFundingPaymentItem));item.amount=1;s.flush()

    def test_parent_not_reserved_and_group_forecast_keeps_only_fee(self):
        self.fixture(received=False)
        with unit() as s:
            state=funds_state(s,s.get(Account,self.cash),today())
            self.assertEqual(state['reserved'],91000000)
            state=budget_state(s,self.cat,self.month,'UZS')
            self.assertEqual(state['reserved'],91000000)
        dash=self.client.get('/api/dashboard',headers=self.h).json()
        self.assertEqual(dash['outgoing7'],'910000.00')
        ids={e['id'] for day in dash['forecast'] for e in day['events'] if e['kind']=='out'}
        self.assertNotIn(self.parent,ids)

    def test_received_parent_not_overdue_or_in_bot_expense_requests(self):
        self.fixture();yesterday=today()-timedelta(days=1)
        with unit(True) as s:
            for id in [self.parent,*self.children]:s.get(PaymentRequest,id).due_date=yesterday
        self.assertFalse(self.row(self.parent)['overdue']);self.assertTrue(self.row(self.children[0])['overdue'])
        group=-1001239876001;bot=('synthetic-batch-bot','synthetic-batch-secret-'+'x'*32)
        environment={'BOT_CLIENT_ID':bot[0],'BOT_CLIENT_SECRET':bot[1],'BOT_REPORT_GROUPS':f'{group}:UZGERMED'}
        with patch.dict(os.environ,environment):
            daily=self.client.get('/api/bot/v1/group-daily-summary',auth=bot,params={'chat_id':group,'report_date':self.date})
            self.assertEqual(daily.status_code,200,daily.text)
            statement=self.client.get('/api/bot/v1/group-statement-reconciliation',auth=bot,
                params={'chat_id':group,'report_date':str(yesterday)})
            self.assertEqual(statement.status_code,200,statement.text)
        source=f'CF-{self.parent:05d}';child=f'CF-{self.children[0]:05d}'
        for rows in (daily.json()['open_requests'],statement.json()['unpaid_requests']):
            numbers={row['number'] for row in rows};self.assertNotIn(source,numbers);self.assertIn(child,numbers)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(CashFundingBatchTests(name) for name in CashFundingBatchTests.__dict__ if name.startswith('test_'))


def tearDownModule():base.tearDownModule()
