"""Cancellation of synthetic, erroneously recorded withdrawals only."""
import unittest
from tests.test_cash_funding import CashFundingTests
from sqlalchemy import select,func
from app.db import CashFunding,Ledger,PaymentRequest,unit,engine
from app.services import effective_cashflows

class CashFundingCancelTests(CashFundingTests):
    def withdrawn(self):
        rid=self.cash_request()
        self.funding_action(rid,'withdraw',*self.stage_user('check'))
        return rid

    def preview_cancel(self,rid,**extra):
        f=self.row(rid)['cash_funding']
        body={'version':f['version'],'date':f['withdraw_date'],'reference':f['withdraw_reference'],
              'note':'Synthetic mistaken withdrawal entry',**extra}
        return body,self.post(f'/api/requests/{rid}/cash-funding/cancel/preview',body)

    def commit_cancel(self,rid,body,preview,**extra):
        return self.post(f'/api/requests/{rid}/cash-funding/cancel/commit',
            {**body,'token':preview['token'],'confirm':True,**extra})

    def test_cancel_restores_bank_and_fee_without_cash_income_and_allows_delete(self):
        rid=self.withdrawn();body,p=self.preview_cancel(rid)
        self.assertEqual(p.status_code,200,p.text)
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),2)
        result=self.commit_cancel(rid,body,p.json());self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['cash_funding']['state'],'cancelled')
        self.assertEqual(self.funding_balances(),{'bank':100000000,'cash':0,'transit':0})
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),4)
            self.assertEqual(list(s.scalars(effective_cashflows(s))),[])
        self.assertEqual(self.commit_cancel(rid,body,p.json()).status_code,409)
        removed=self.post(f'/api/requests/{rid}/delete',{'version':result.json()['version'],'reason':'Synthetic request no longer needed'})
        self.assertEqual(removed.status_code,200,removed.text)
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),4)

    def test_zero_fee_cancellation_has_only_one_reversal(self):
        rid=self.cash_request(pct='0',net='1000000')
        self.funding_action(rid,'withdraw',*self.stage_user('check'))
        body,p=self.preview_cancel(rid);self.assertEqual(p.status_code,200,p.text)
        result=self.commit_cancel(rid,body,p.json());self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.funding_balances(),{'bank':100000000,'cash':0,'transit':0})
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),2)

    def test_batch_funding_cancellation_does_not_pay_child_requests(self):
        rid=self.cash_request()
        with unit(True) as s:s.scalar(select(CashFunding).where(CashFunding.request_id==rid)).mode='batch'
        self.funding_action(rid,'withdraw',*self.stage_user('check'))
        body,p=self.preview_cancel(rid);self.assertEqual(p.status_code,200,p.text)
        result=self.commit_cancel(rid,body,p.json());self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['cash_funding']['state'],'cancelled')
        self.assertFalse(result.json()['cash_funding']['completed'])
        with unit() as s:self.assertIsNone(s.scalar(select(Ledger.id).where(Ledger.request_id.is_not(None))))

    def test_accountant_and_cashier_cannot_cancel(self):
        rid=self.withdrawn();body,p=self.preview_cancel(rid)
        self.assertEqual(p.status_code,200,p.text)
        for actor in (self.stage_user('check'),self.cash_payer()):
            denied=self.post(f'/api/requests/{rid}/cash-funding/cancel/preview',body,*actor)
            self.assertEqual(denied.status_code,403,denied.text)

    def test_received_cash_cannot_be_cancelled(self):
        rid=self.withdrawn();self.funding_action(rid,'receive',*self.cash_payer())
        body,p=self.preview_cancel(rid);self.assertEqual(p.status_code,409,p.text)
        self.assertEqual(self.funding_balances(),{'bank':0,'cash':99000000,'transit':0})

    def test_reason_confirmation_and_token_are_required(self):
        rid=self.withdrawn();body,p=self.preview_cancel(rid,note='short');self.assertEqual(p.status_code,422,p.text)
        body,p=self.preview_cancel(rid);self.assertEqual(p.status_code,200,p.text)
        self.assertEqual(self.commit_cancel(rid,body,p.json(),confirm=False).status_code,422)
        self.assertEqual(self.commit_cancel(rid,body,p.json(),note='Changed cancellation reason').status_code,409)
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),2)

    def test_statement_link_blocks_cancellation(self):
        from app.bank_statement_models import BankStatementLine
        rid=self.withdrawn();batch,line=self.unresolved_bank_review()
        with unit(True) as s:
            f=s.scalar(select(CashFunding).where(CashFunding.request_id==rid))
            s.get(BankStatementLine,line).ledger_id=f.transfer_ledger_id
        body,p=self.preview_cancel(rid);self.assertEqual(p.status_code,409,p.text)
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),2)

    def test_stale_preview_after_receipt_cannot_reverse_cash(self):
        rid=self.withdrawn();body,p=self.preview_cancel(rid);self.assertEqual(p.status_code,200,p.text)
        self.funding_action(rid,'receive',*self.cash_payer())
        result=self.commit_cancel(rid,body,p.json());self.assertEqual(result.status_code,409,result.text)
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),3)

    def test_insufficient_transit_is_not_partially_reversed(self):
        rid=self.withdrawn()
        with unit(True) as s:
            f=s.scalar(select(CashFunding).where(CashFunding.request_id==rid))
            s.add(Ledger(account_id=f.transit_account_id,kind='out',amount=1,date=f.withdraw_date,
                reference='SYNTH-CONSUMED',note='Synthetic dependency',creator_id=f.created_by))
        body,p=self.preview_cancel(rid);self.assertEqual(p.status_code,409,p.text)
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),3)


    def test_late_validation_failure_rolls_back_both_reversals(self):
        from unittest.mock import patch
        from fastapi import HTTPException
        rid=self.withdrawn();body,p=self.preview_cancel(rid);self.assertEqual(p.status_code,200,p.text)
        with patch('app.cash_funding.validate_running_balance',side_effect=HTTPException(409,'Synthetic final constraint')):
            result=self.commit_cancel(rid,body,p.json())
        self.assertEqual(result.status_code,409,result.text)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),2)
            self.assertEqual(s.scalar(select(CashFunding).where(CashFunding.request_id==rid)).state,'in_transit')

    def test_company_finance_and_director_may_cancel_but_other_company_cannot(self):
        rid=self.withdrawn();body,p=self.preview_cancel(rid)
        for role in ('finance','director'):
            actor=self.make_user(role,'cancel_'+role)
            result=self.post(f'/api/requests/{rid}/cash-funding/cancel/preview',body,*actor)
            self.assertEqual(result.status_code,200,result.text)
        from app.db import Company
        with unit() as s:other=s.scalar(select(Company.id).where(Company.id!=self.company))
        denied=self.post(f'/api/requests/{rid}/cash-funding/cancel/preview',body,self.client,{**self.h,'X-Company-ID':str(other)})
        self.assertIn(denied.status_code,(403,404))


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(CashFundingCancelTests(name) for name in CashFundingCancelTests.__dict__ if name.startswith('test_'))

def tearDownModule():engine.dispose()
