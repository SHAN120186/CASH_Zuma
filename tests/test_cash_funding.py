"""Synthetic cash funding: actual dates, company boundaries and duplicate guards."""
import json
import os
import unittest
from datetime import timedelta
from sqlalchemy import select,func,inspect
from unittest.mock import patch

import tests.test_site as base
from app.db import Account,Budget,CashFunding,Category,Company,CompanyUser,Ledger,PaymentRequest,Setting,TelegramLink,User,unit,engine,initialize
from app.services import account_balance,today


class CashFundingTests(base.SiteTests):
    def cash_request(self,gross='1000000',pct='1',net='990000',submit=True):
        if not hasattr(self,'cash'):
            cash=self.post('/api/accounts',{'name':'Funding cash','kind':'cash','currency':'UZS','opening':'0','opening_date':self.date})
            self.assertEqual(cash.status_code,200,cash.text);self.cash=cash.json()['id']
        if not hasattr(self,'fee_cat'):
            fee=self.post('/api/categories',{'name':'Synthetic withdrawal commission','type':'outcome','activity':'operating'})
            self.assertEqual(fee.status_code,200,fee.text);self.fee_cat=fee.json()['id']
        body=self.request_body(net,account=self.cash)
        body['cash_funding']={'bank_account_id':self.acc,'gross_amount':gross,'fee_percent':pct,'fee_category_id':self.fee_cat}
        response=self.post('/api/requests',body)
        self.assertEqual(response.status_code,200,response.text)
        rid=response.json()['id']
        if submit:
            self.assertEqual(self.upload(rid).status_code,200)
            response=self.post(f'/api/requests/{rid}/decision',{'action':'submit'})
            self.assertEqual(response.status_code,200,response.text)
            response=self.approve(rid);self.assertEqual(response.status_code,200,response.text)
        return rid

    def funding_action(self,rid,action,client=None,headers=None,**extra):
        row=self.row(rid)
        payload={'version':row['cash_funding']['version'],'date':self.date,'reference':'Synthetic '+action,
                 'note':'Completed synthetic bank and cash fact',**extra}
        response=self.post(f'/api/requests/{rid}/cash-funding/{action}/preview',payload,client,headers)
        self.assertEqual(response.status_code,200,response.text)
        result=self.post(f'/api/requests/{rid}/cash-funding/{action}/commit',
                         {**payload,'token':response.json()['token'],'confirm':True},client,headers)
        self.assertEqual(result.status_code,200,result.text)
        return result,payload,response.json()['token']

    def cash_payer(self):
        if not hasattr(self,'cash_user'):self.cash_user=self.make_user('cashier','cash_receiver')
        return self.cash_user

    def funding_balances(self):
        with unit() as s:
            return {a.kind:account_balance(s,a) for a in s.scalars(select(Account))
                    if a.id in (self.acc,self.cash) or a.kind=='transit'}

    def report(self):
        return self.client.get('/api/report',headers=self.h,params={'year':today().year,'currency':'UZS','company_id':self.company,'as_of':self.date}).json()

    def test_full_cycle_cashflow_and_no_duplicate_facts(self):
        rid=self.cash_request()
        checker=self.stage_user('check')
        before=self.row(rid)
        self.assertEqual(before['cash_funding']['net_amount'],'990000.00')
        self.assertEqual(before['cash_funding']['fee_amount'],'10000.00')
        self.assertNotIn('pay',self.cash_payer()[0].get(f'/api/requests/{rid}',headers=self.cash_payer()[1]).json()['actions'])
        row,payload,token=self.funding_action(rid,'withdraw',*checker)
        self.assertEqual(row.json()['status'],'approved')
        self.assertEqual(self.funding_balances(),{'bank':0,'cash':0,'transit':99000000})
        report=self.report();self.assertEqual(report['totals'][8],'-10000.00')
        dash=self.client.get('/api/dashboard',headers=self.h).json()
        self.assertEqual(dash['fact_in'],'0.00');self.assertEqual(dash['fact_out'],'10000.00')
        self.assertEqual(dash['balance'],'990000.00');self.assertEqual(dash['in_transit_balance'],'990000.00')
        self.assertEqual(dash['spendable_balance'],'0.00')
        repeat=self.post(f'/api/requests/{rid}/cash-funding/withdraw/commit',{**payload,'token':token,'confirm':True},*checker)
        self.assertEqual(repeat.status_code,409,repeat.text)
        row,received,_=self.funding_action(rid,'receive',*self.cash_payer())
        self.assertEqual(row.json()['cash_funding']['state'],'received')
        self.assertEqual(self.funding_balances(),{'bank':0,'cash':99000000,'transit':0})
        self.assertEqual(self.report()['totals'][8],'-10000.00')
        r=self.ledger('990000','out',reference='Synthetic cash issue',client=self.cash_payer(),account_id=self.cash,
                      category_id=self.cat,request_id=rid)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.row(rid)['status'],'paid')
        self.assertEqual(self.report()['totals'][8],'-1000000.00')
        self.assertEqual(self.funding_balances(),{'bank':0,'cash':0,'transit':0})
        duplicate=self.ledger('990000','out',reference='Repeated cash issue',client=self.cash_payer(),account_id=self.cash,
                              category_id=self.cat,request_id=rid)
        self.assertEqual(duplicate.status_code,409,duplicate.text)
        with unit() as s:
            entries=list(s.scalars(select(Ledger)))
            self.assertEqual(len(entries),4)
            self.assertEqual(len({(e.account_id,e.reference) for e in entries}),4)
            self.assertEqual(sum(e.kind=='out' for e in entries),2)

    def test_zero_balance_allows_approval_but_blocks_withdrawal(self):
        with unit(True) as s:s.get(Account,self.acc).opening=0
        rid=self.cash_request()
        self.assertEqual(self.row(rid)['status'],'approved')
        body={'version':self.row(rid)['cash_funding']['version'],'date':self.date,'reference':'No funds'}
        response=self.post(f'/api/requests/{rid}/cash-funding/withdraw/preview',body,*self.stage_user('check'))
        self.assertEqual(response.status_code,409,response.text)
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),0)

    def test_validation_exact_net_currency_company_and_percent(self):
        rid=self.cash_request(submit=False)
        body=self.request_body('990000',account=self.cash)
        plan={'bank_account_id':self.acc,'gross_amount':'1000000','fee_percent':'1','fee_category_id':self.fee_cat}
        for percent in ('100','-1','NaN','1.001'):
            response=self.post('/api/requests',{**body,'cash_funding':{**plan,'fee_percent':percent}})
            self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.post('/api/requests',{**body,'amount':'1000000','cash_funding':plan}).status_code,422)
        usd=self.post('/api/accounts',{'name':'USD source','kind':'bank','currency':'USD','opening':'100','opening_date':self.date}).json()['id']
        self.assertEqual(self.post('/api/requests',{**body,'cash_funding':{**plan,'bank_account_id':usd}}).status_code,422)
        with unit() as s:
            other=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
        foreign=self.post('/api/accounts',{'name':'Foreign source','kind':'bank','currency':'UZS','opening':'1000000','opening_date':self.date},headers={**self.h,'X-Company-ID':str(other)}).json()['id']
        self.assertEqual(self.post('/api/requests',{**body,'cash_funding':{**plan,'bank_account_id':foreign}}).status_code,404)
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(CashFunding)),1)

    def test_cash_payment_requires_receipt_and_actual_dates(self):
        rid=self.cash_request()
        bad=self.ledger('990000','out',reference='Too early cash',client=self.cash_payer(),account_id=self.cash,
                        category_id=self.cat,request_id=rid)
        self.assertEqual(bad.status_code,409,bad.text)
        self.funding_action(rid,'withdraw',*self.stage_user('check'))
        # Cash stays empty while funds are in transit.
        self.assertEqual(self.funding_balances()['cash'],0)
        body={'version':self.row(rid)['cash_funding']['version'],'date':str(today()-timedelta(days=1)),'reference':'Earlier receipt'}
        response=self.post(f'/api/requests/{rid}/cash-funding/receive/preview',body,*self.cash_payer())
        self.assertEqual(response.status_code,422,response.text)
        body['date']=str(today()+timedelta(days=1))
        self.assertEqual(self.post(f'/api/requests/{rid}/cash-funding/receive/preview',body,*self.cash_payer()).status_code,422)

    def test_payer_rights_preview_stale_and_protected_legs(self):
        rid=self.cash_request()
        body={'version':self.row(rid)['cash_funding']['version'],'date':self.date,'reference':'Withdrawal document'}
        # Finance has write, but no bank-pay right; the author admin also fails separation.
        self.assertEqual(self.post(f'/api/requests/{rid}/cash-funding/withdraw/preview',body,*self.stage_user('finance')).status_code,403)
        self.assertEqual(self.post(f'/api/requests/{rid}/cash-funding/withdraw/preview',body).status_code,403)
        self.assertEqual(self.post(f'/api/requests/{rid}/cash-funding/withdraw/preview',body,*self.cash_payer()).status_code,403)
        checker=self.stage_user('check')
        preview=self.post(f'/api/requests/{rid}/cash-funding/withdraw/preview',body,*checker)
        self.assertEqual(preview.status_code,200,preview.text)
        # A changed balance after preview invalidates the signed state.
        self.assertEqual(self.ledger('1','in',reference='Changed state').status_code,200)
        stale=self.post(f'/api/requests/{rid}/cash-funding/withdraw/commit',{**body,'token':preview.json()['token'],'confirm':True},*checker)
        self.assertEqual(stale.status_code,409,stale.text)
        self.funding_action(rid,'withdraw',*checker)
        funding=self.row(rid)['cash_funding']
        for leg in ('transfer_ledger_id','fee_ledger_id'):
            response=self.post(f"/api/ledger/{funding[leg]}/reverse",{'reason':'Synthetic unsafe leg reversal'})
            self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(self.post(f'/api/requests/{rid}/delete',{'version':self.row(rid)['version'],'reason':'Do not orphan funds in transit'}).status_code,409)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'close','note':'Do not orphan funds in transit'}).status_code,409)

    def test_close_after_receipt_is_blocked_and_no_generic_transit_use(self):
        rid=self.cash_request()
        self.funding_action(rid,'withdraw',*self.stage_user('check'))
        self.funding_action(rid,'receive',*self.cash_payer())
        funding=self.row(rid)['cash_funding'];transit=funding['transit_account_id']
        response=self.post('/api/ledger',{'account_id':transit,'category_id':self.cat,'kind':'in','amount':'1','date':self.date,'reference':'Forbidden transit'})
        self.assertEqual(response.status_code,409,response.text)
        archive=self.post(f'/api/accounts/{transit}/archive',{'archived':True,'reason':'Must remain system controlled'})
        self.assertEqual(archive.status_code,409,archive.text)
        closed=self.post(f'/api/requests/{rid}/decision',{'action':'close','note':'Cash remains in company cash desk'})
        self.assertEqual(closed.status_code,409,closed.text)
        self.assertEqual(self.row(rid)['status'],'approved')
        self.assertEqual(self.row(rid)['cash_funding']['state'],'received')
        self.assertEqual(self.funding_balances(),{'bank':0,'cash':99000000,'transit':0})
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),3)

    def test_additive_table_initialization_preserves_existing_data(self):
        # Simulate a schema before funding existed. Its later group/item
        # tables cannot exist in that historical schema either; PostgreSQL
        # correctly protects their foreign key to the funding table.
        from app.db import CashFundingPaymentItem,CashFundingPaymentGroup
        CashFundingPaymentItem.__table__.drop(engine)
        CashFundingPaymentGroup.__table__.drop(engine)
        CashFunding.__table__.drop(engine)
        initialize()
        self.assertIn('request_cash_fundings',inspect(engine).get_table_names())
        with unit() as s:
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),100000000)
            self.assertEqual(s.scalar(select(func.count()).select_from(CashFunding)),0)

    def test_fee_budget_cannot_be_accepted_by_payer_note(self):
        with unit(True) as s:s.get(Account,self.acc).opening+=100000
        rid=self.cash_request()
        self.assertEqual(self.post('/api/budgets',{'category_id':self.fee_cat,'month':self.month,'currency':'UZS',
            'amount':'10000','mode':'soft','reason':'Synthetic approved commission budget'}).status_code,200)
        # A different actual fee consumes the budget after this request was
        # approved. A payer's long comment cannot accept the new overrun.
        self.assertEqual(self.ledger('1000','out',reference='Other actual fee',category_id=self.fee_cat).status_code,200)
        body={'version':self.row(rid)['cash_funding']['version'],'date':self.date,'reference':'Fee over budget',
              'note':'A long payer note must not approve overspend'}
        response=self.post(f'/api/requests/{rid}/cash-funding/withdraw/preview',body,*self.stage_user('check'))
        self.assertEqual(response.status_code,409,response.text)
        self.assertIn('бюджет',response.text)

    def test_same_article_cash_and_fee_accepts_total_once(self):
        rid=self.cash_request(submit=False)
        self.budget('995000','soft')
        payload={**self.request_body('990000',account=self.cash),'version':self.row(rid)['version'],
                 'reason':'Synthetic common article for fee and issue',
                 'cash_funding':{'bank_account_id':self.acc,'gross_amount':'1000000','fee_percent':'1','fee_category_id':self.cat}}
        changed=self.client.put(f'/api/requests/{rid}',json=payload,headers=self.h)
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(self.upload(rid).status_code,200)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'submit'}).status_code,200)
        approved=self.approve(rid);self.assertEqual(approved.status_code,200,approved.text)
        with unit() as s:
            req=s.get(PaymentRequest,rid);funding=s.scalar(select(CashFunding).where(CashFunding.request_id==rid))
            self.assertEqual(req.approved_overrun,500000)
            self.assertEqual(funding.approved_fee_overrun,500000)
        self.funding_action(rid,'withdraw',*self.stage_user('check'))
        self.funding_action(rid,'receive',*self.cash_payer())
        paid=self.ledger('990000','out',reference='Common article issue',client=self.cash_payer(),account_id=self.cash,
                         category_id=self.cat,request_id=rid)
        self.assertEqual(paid.status_code,200,paid.text)
        self.assertEqual(self.report()['totals'][8],'-1000000.00')

    def test_cross_month_shared_article_withdrawal_uses_only_current_month_commission_budget(self):
        from app.services import budget_state
        rid=self.cash_request(submit=False)
        october='2026-10'
        self.assertEqual(self.post('/api/budgets',{'category_id':self.cat,'month':october,'currency':'UZS',
            'amount':'1000000','mode':'hard','reason':'Synthetic October cash and planned fee budget'}).status_code,200)
        payload={**self.request_body('990000',account=self.cash,date='2026-10-02'),'version':self.row(rid)['version'],
                 'reason':'Synthetic October issue funded by September withdrawal',
                 'cash_funding':{'bank_account_id':self.acc,'gross_amount':'1000000','fee_percent':'1','fee_category_id':self.cat}}
        edited=self.client.put(f'/api/requests/{rid}',json=payload,headers=self.h)
        self.assertEqual(edited.status_code,200,edited.text)
        self.assertEqual(self.upload(rid).status_code,200)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'submit'}).status_code,200)
        approved=self.approve(rid);self.assertEqual(approved.status_code,200,approved.text)
        # The earlier month's hard limit must still cover its actual fee.
        self.budget('9999','hard')
        body={'version':self.row(rid)['cash_funding']['version'],'date':self.date,'reference':'September funding for October issue'}
        denied=self.post(f'/api/requests/{rid}/cash-funding/withdraw/preview',body,*self.stage_user('check'))
        self.assertEqual(denied.status_code,409,denied.text)
        self.budget('10000','hard')
        self.funding_action(rid,'withdraw',*self.stage_user('check'),reference=body['reference'])
        with unit() as s:
            september=budget_state(s,self.cat,self.month,'UZS')
            october_state=budget_state(s,self.cat,october,'UZS')
            self.assertEqual(september['spent'],1000000)
            self.assertEqual(september['reserved'],0)
            self.assertEqual(october_state['spent'],0)
            self.assertEqual(october_state['reserved'],99000000)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),2)
        self.assertEqual(self.report()['totals'][8],'-10000.00')
        self.assertEqual(self.row(rid)['amount'],'990000.00')
        self.assertEqual(self.funding_balances(),{'bank':0,'cash':0,'transit':99000000})

    def test_same_physical_bank_document_cannot_fund_two_requests(self):
        with unit(True) as s:s.get(Account,self.acc).opening=200000000
        first=self.cash_request();second=self.cash_request()
        self.funding_action(first,'withdraw',*self.stage_user('check'),reference='One physical bank withdrawal')
        body={'version':self.row(second)['cash_funding']['version'],'date':self.date,
              'reference':'One physical bank withdrawal'}
        duplicate=self.post(f'/api/requests/{second}/cash-funding/withdraw/preview',body,*self.stage_user('check'))
        self.assertEqual(duplicate.status_code,409,duplicate.text)
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),2)

    def test_commission_reserves_and_accepted_soft_overrun_cover_both_plans(self):
        with unit(True) as s:s.get(Account,self.acc).opening=200000000
        first=self.cash_request()
        self.assertEqual(self.post('/api/budgets',{'category_id':self.fee_cat,'month':self.month,'currency':'UZS',
            'amount':'15000','mode':'soft','reason':'Synthetic commission approval budget'}).status_code,200)
        second=self.cash_request()
        with unit() as s:
            from app.services import budget_state
            value=budget_state(s,self.fee_cat,self.month,'UZS')
            self.assertEqual(value['reserved'],2000000)
            second_funding=s.scalar(select(CashFunding).where(CashFunding.request_id==second))
            self.assertEqual(second_funding.approved_fee_overrun,500000)
        self.funding_action(first,'withdraw',*self.stage_user('check'),reference='First approved bank withdrawal')
        self.funding_action(second,'withdraw',*self.stage_user('check'),reference='Second approved bank withdrawal')
        with unit() as s:
            value=budget_state(s,self.fee_cat,self.month,'UZS')
            self.assertEqual(value['spent'],2000000);self.assertEqual(value['reserved'],0)

    def test_fee_director_policy_and_shared_article_gross_threshold(self):
        first=self.cash_request(pct='99',net='10000',submit=False)
        with unit(True) as s:
            cash_article=s.get(Category,self.cat);cash_article.director_policy='skip';cash_article.skip_allowed=True
            s.get(Category,self.fee_cat).director_policy='always'
        self.assertEqual(self.upload(first).status_code,200)
        submitted=self.post(f'/api/requests/{first}/decision',{'action':'submit'})
        self.assertEqual(submitted.status_code,200,submitted.text)
        self.assertTrue(submitted.json()['route']['director_required'])
        finance=self.finance_approve(first);self.assertEqual(finance.status_code,200,finance.text)
        self.assertEqual(self.row(first)['approval_stage'],'director')
        second=self.cash_request(submit=False)
        payload={**self.request_body('990000',account=self.cash),'version':self.row(second)['version'],
                 'reason':'Synthetic shared article threshold check',
                 'cash_funding':{'bank_account_id':self.acc,'gross_amount':'1000000','fee_percent':'1','fee_category_id':self.cat}}
        self.assertEqual(self.client.put(f'/api/requests/{second}',json=payload,headers=self.h).status_code,200)
        with unit(True) as s:
            category=s.get(Category,self.cat);category.director_policy='threshold';category.director_threshold_uzs=99500000
        self.assertEqual(self.upload(second).status_code,200)
        submitted=self.post(f'/api/requests/{second}/decision',{'action':'submit'})
        self.assertEqual(submitted.status_code,200,submitted.text)
        self.assertTrue(submitted.json()['route']['director_required'])

    def test_forecast_includes_unposted_fee_and_drops_it_after_withdrawal(self):
        rid=self.cash_request(submit=False)
        self.assertEqual(self.upload(rid).status_code,200)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'submit'}).status_code,200)
        pending=self.client.get('/api/dashboard',headers=self.h).json()
        pending_day=next(d for d in pending['forecast'] if d['date']==self.date)
        self.assertEqual(pending['outgoing7'],'0.00')
        self.assertEqual(pending_day['outgoing'],'0.00')
        self.assertEqual(pending_day['requested_outgoing'],'1000000.00')
        event=next(e for e in pending_day['pending_events'] if e['id']==rid)
        self.assertEqual(event['cash_amount'],'990000.00')
        self.assertEqual(event['cash_funding_fee'],'10000.00')
        self.assertEqual(self.approve(rid).status_code,200)
        before=self.client.get('/api/dashboard',headers=self.h).json()
        day=next(d for d in before['forecast'] if d['date']==self.date)
        self.assertEqual(before['outgoing7'],'1000000.00')
        self.assertEqual(day['outgoing'],'1000000.00')
        self.assertEqual(day['balance'],'0.00')
        event=next(e for e in day['events'] if e['id']==rid)
        self.assertEqual(event['amount'],'1000000.00')
        self.assertEqual(event['cash_amount'],'990000.00')
        self.assertEqual(event['cash_funding_bank_account_id'],self.acc)
        self.assertEqual(self.row(rid)['amount'],'990000.00')
        self.funding_action(rid,'withdraw',*self.stage_user('check'))
        after=self.client.get('/api/dashboard',headers=self.h).json()
        day=next(d for d in after['forecast'] if d['date']==self.date)
        self.assertEqual(after['fact_out'],'10000.00')
        self.assertEqual(after['outgoing7'],'990000.00')
        self.assertEqual(day['outgoing'],'990000.00')
        event=next(e for e in day['events'] if e['id']==rid)
        self.assertEqual(event['amount'],'990000.00')
        self.assertNotIn('cash_funding_fee',event)

    def test_retired_fee_article_allows_existing_cash_receipt_but_not_new_withdrawal(self):
        first=self.cash_request();second=self.cash_request()
        self.funding_action(first,'withdraw',*self.stage_user('check'))
        funding=self.row(first)['cash_funding']
        with unit(True) as s:
            key=f'company:{self.company}:category_catalog'
            row=s.get(Setting,key);config=json.loads(row.value) if row else {}
            config['retired']=list(set(config.get('retired',[]))|{self.fee_cat})
            if row:row.value=json.dumps(config)
            else:s.add(Setting(key=key,value=json.dumps(config)))
            fee=s.get(Ledger,funding['fee_ledger_id'])
            before=(fee.account_id,fee.category_id,fee.kind,fee.amount,fee.date,fee.reference)
        result,_,_=self.funding_action(first,'receive',*self.cash_payer())
        self.assertEqual(result.json()['cash_funding']['state'],'received')
        self.assertEqual(self.funding_balances(),{'bank':0,'cash':99000000,'transit':0})
        self.assertEqual(self.report()['totals'][8],'-10000.00')
        with unit() as s:
            fee=s.get(Ledger,funding['fee_ledger_id'])
            self.assertEqual(before,(fee.account_id,fee.category_id,fee.kind,fee.amount,fee.date,fee.reference))
        payload={'version':self.row(second)['cash_funding']['version'],'date':self.date,'reference':'New withdrawn fee'}
        denied=self.post(f'/api/requests/{second}/cash-funding/withdraw/preview',payload,*self.stage_user('check'))
        self.assertEqual(denied.status_code,422,denied.text)

    def test_transit_bot_contract_is_legacy_compatible_scoped_and_balanced(self):
        from decimal import Decimal
        rid=self.cash_request()
        self.funding_action(rid,'withdraw',*self.stage_user('check'))
        group=-1001111111111;foreign_group=-1002222222222
        bot=('synthetic-cash-funding-bot','synthetic-bot-secret-'+'x'*32)
        environment={'BOT_CLIENT_ID':bot[0],'BOT_CLIENT_SECRET':bot[1],
                     'BOT_REPORT_GROUPS':f'{group}:UZGERMED;{foreign_group}:ZUMA'}
        with patch.dict(os.environ,environment):
            response=self.client.get('/api/bot/v1/group-daily-summary',auth=bot,
                params={'chat_id':group,'report_date':self.date})
            self.assertEqual(response.status_code,200,response.text);daily=response.json()
            response=self.client.get('/api/bot/v1/morning-summary',auth=bot,
                params={'chat_id':group,'report_date':str(today()-timedelta(days=1)),'today':self.date})
            self.assertEqual(response.status_code,200,response.text);morning=response.json()
            foreign=self.client.get('/api/bot/v1/group-daily-summary',auth=bot,
                params={'chat_id':foreign_group,'report_date':self.date}).json()
        for payload in (daily,morning):
            self.assertEqual(payload['companies'],['UZGERMED'])
            for section in ('balances','income','expense'):
                self.assertTrue(all(row['kind'] in ('bank','cash') and row['company_code']=='UZGERMED'
                                    for row in payload[section] or []))
            transit=[row for row in payload['balances'] if row.get('source_kind')=='transit']
            self.assertEqual(len(transit),1)
            self.assertEqual(transit[0]['kind'],'cash')
            self.assertIn('Деньги в пути',transit[0]['name'])
            self.assertTrue(any('Недоступны для выдачи до получения кассой' in row['message']
                                for row in payload['warnings']))
        self.assertTrue(all(row['kind'] in ('bank','cash') for row in daily['opening_balances']))
        total=lambda rows:sum((Decimal(row['amount']) for row in rows or []),Decimal('0'))
        self.assertEqual(total(daily['opening_balances'])+total(daily['income'])-total(daily['expense']),total(daily['balances']))
        self.assertEqual(total(daily['expense']),Decimal('10000.00'))
        self.assertEqual(total(daily['balances']),Decimal('990000.00'))
        self.assertEqual(daily['income'],[])
        self.assertEqual(foreign['companies'],['ZUMA']);self.assertEqual(foreign['balances'],[])
        self.assertFalse(any('Деньги в пути' in row['message'] for row in foreign['warnings']))
        self.assertTrue(any(row['kind']=='transit' for row in self.client.get('/api/accounts',headers=self.h).json()))
        # Private harnesses may additionally feed these synthetic payloads into
        # the already deployed bot parser, without a dependency on its checkout.
        self.legacy_bot_payloads={'morning':morning,'daily':daily}

    def test_funding_reminders_follow_bank_receipt_and_independent_cash_payer(self):
        from app.bot_api import pending_items
        rid=self.cash_request(submit=False)
        receiver=self.cash_payer()
        with unit(True) as s:
            author=s.scalar(select(User).where(User.username=='cash_receiver'))
            author_id=author.id
            req=s.get(PaymentRequest,rid);req.creator_id=author_id;req.last_editor_id=author_id
            req.purpose='Synthetic invoice account 20208000123456789012'
            s.add(TelegramLink(user_id=author_id,telegram_user_id=7001002))
        self.assertEqual(self.upload(rid).status_code,200)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'submit'}).status_code,200)
        self.assertEqual(self.approve(rid).status_code,200)
        with unit(True) as s:
            checker=s.scalar(select(User).where(User.username=='checker'));banker_id=checker.id
            s.add(TelegramLink(user_id=banker_id,telegram_user_id=7001001))
        def items():
            with unit() as s:return [row for row in pending_items(s) if row['request_id']==rid]
        rows=items()
        self.assertEqual([(r['assignee_user_id'],r['stage']) for r in rows],[(banker_id,'bank_payment')])
        self.assertEqual(rows[0]['stage_label'],'Снятие наличных из банка')
        self.assertEqual(rows[0]['amount'],'990000.00')
        self.assertEqual(rows[0]['funding_gross_amount'],'1000000.00')
        self.assertIn('1000000.00',rows[0]['purpose'])
        self.assertNotIn('20208000123456789012',rows[0]['purpose'])
        self.assertIn('••9012',rows[0]['purpose'])
        self.funding_action(rid,'withdraw',*self.stage_user('check'))
        rows=items()
        self.assertEqual([(r['assignee_user_id'],r['stage']) for r in rows],[(author_id,'cash_payment')])
        self.assertEqual(rows[0]['stage_label'],'Получение наличных в кассу')
        # Receipt is not an expense: this cashier may receive their own cash
        # funding, but may not subsequently issue the expense to themself.
        self.funding_action(rid,'receive',*receiver)
        self.assertEqual(items(),[])
        independent=self.make_user('cashier','independent_cash_payer')
        try:
            with unit(True) as s:
                user=s.scalar(select(User).where(User.username=='independent_cash_payer'));payer_id=user.id
                s.add(TelegramLink(user_id=payer_id,telegram_user_id=7001003))
            rows=items()
            self.assertEqual([(r['assignee_user_id'],r['stage']) for r in rows],[(payer_id,'cash_payment')])
            self.assertEqual(rows[0]['stage_label'],'Выдача кассиром')
            with unit(True) as s:s.add(Setting(key=f'sections:{self.company}:{payer_id}',value=json.dumps(
                {'version':1,'sections':{'requests':'view'}})))
            self.assertEqual(items(),[])
        finally:independent[0].close()

    def test_imported_bank_document_cannot_be_debited_again_as_cash_withdrawal(self):
        from app.bank_statement_models import BankStatementBatch,BankStatementLine
        from app.bank_statement_guard import accounted_line_ids,require_reconciled_for_manual_payment,set_statement_reconciliation
        from app.bank_statement_unallocated import ensure_category
        with unit(True) as s:s.get(Account,self.acc).opening=200000000
        rid=self.cash_request()
        reference='Previously imported bank withdrawal'
        fingerprint='a'*64
        with unit(True) as s:
            s.info['company_id']=self.company
            admin=s.scalar(select(User).where(User.username=='admin'))
            category=ensure_category(s,'out')
            entry=Ledger(account_id=self.acc,category_id=category,kind='out',amount=100000000,
                         date=today(),reference='BANK-'+fingerprint,counterparty='Synthetic bank withdrawal',creator_id=admin.id)
            s.add(entry);s.flush()
            batch=BankStatementBatch(company_id=self.company,account_id=self.acc,created_by=admin.id,
                filename='Synthetic funding duplicate.xls',sha256='b'*64,account_number='20208000000000000001',
                owner_name='Synthetic company',owner_inn='123456789',currency='UZS',period_from=today(),period_to=today(),
                opening=200000000,closing=100000000,debit_total=100000000,credit_total=0,
                balance_matches=True,declared_totals_match=True,source_ready=True,status='committed')
            s.add(batch);s.flush()
            line=BankStatementLine(company_id=self.company,batch_id=batch.id,account_id=self.acc,line=1,date=today(),
                direction='out',amount=100000000,document_number=reference,correspondent_mfo='00001',
                correspondent_account='20208000000000000002',correspondent_name='Synthetic bank withdrawal',
                correspondent_inn='123456789',purpose='Completed withdrawal already imported',fingerprint=fingerprint,
                status='auto_outgoing_posted',ledger_id=entry.id,classification='external_outgoing',include_in_daily=True)
            s.add(line);s.flush()
            self.assertEqual(accounted_line_ids(s,[line],s.get(Account,self.acc)),{line.id})
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),batch.closing)
            set_statement_reconciliation(s,batch,True,admin)
            require_reconciled_for_manual_payment(s,s.get(Account,self.acc))
        payload={'version':self.row(rid)['cash_funding']['version'],'date':self.date,'reference':reference}
        denied=self.post(f'/api/requests/{rid}/cash-funding/withdraw/preview',payload,*self.stage_user('check'))
        self.assertEqual(denied.status_code,409,denied.text)
        self.assertIn('документ уже есть в операциях',denied.json()['detail'])
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),1)
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),100000000)
            funding=s.scalar(select(CashFunding).where(CashFunding.request_id==rid))
            self.assertEqual(funding.state,'unfunded');self.assertIsNone(funding.transfer_ledger_id)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(CashFundingTests(name) for name in CashFundingTests.__dict__ if name.startswith('test_'))

def tearDownModule():base.tearDownModule()
