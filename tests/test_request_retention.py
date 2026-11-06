"""Permanent unpaid-request removal stays atomic and preserves monetary facts."""
import json
import unittest
from datetime import date
from unittest.mock import patch

import tests.test_site as base
from fastapi import HTTPException
from sqlalchemy import func, select
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.db import (Audit, CashFunding, CashFundingPaymentGroup, CashFundingPaymentItem, Company,
                    Ledger, PaymentRequest, RequestDocument, Setting, User, SQLITE, unit)
from app.request_retention import (ID_HIGH_WATER_KEY, permanently_delete_unpaid_request,
                                   purge_cancelled_requests)
from app.services import request_journal


class RequestRetentionTests(base.SiteTests):
    def close(self,rid,**values):
        body={'action':'close','note':'This synthetic request is no longer needed',**values}
        return self.post(f'/api/requests/{rid}/decision',body)

    def draft(self,**values):
        response=self.request(submit=False,**values)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['id']

    def fact(self,s,rid,reference='Synthetic preserved fact'):
        r=s.get(PaymentRequest,rid)
        entry=Ledger(account_id=r.account_id,category_id=r.category_id,kind='out',amount=100,
                     date=date.fromisoformat(self.date),counterparty='Synthetic vendor',
                     reference=reference,note='',request_id=rid,creator_id=r.creator_id)
        s.add(entry);s.flush()
        return entry

    def funding_plan(self):
        if not hasattr(self,'retention_cash'):
            response=self.post('/api/accounts',{'name':'Synthetic cash','kind':'cash','currency':'UZS',
                'opening':'0','opening_date':self.date})
            self.assertEqual(response.status_code,200,response.text)
            self.retention_cash=response.json()['id']
        body=self.request_body('99',account=self.retention_cash)
        body['cash_funding']={'bank_account_id':self.acc,'gross_amount':'100','fee_percent':'1','fee_category_id':self.cat}
        response=self.post('/api/requests',body)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['id']

    def statement_link(self,s,rid,status):
        day=date.fromisoformat(self.date)
        batch=BankStatementBatch(company_id=self.company,account_id=self.acc,created_by=1,
            filename='synthetic.xls',sha256='a'*64,account_number='synthetic',owner_name='Synthetic',
            owner_inn='synthetic',currency='UZS',period_from=day,period_to=day,opening=0,closing=0,
            debit_total=0,credit_total=0,balance_matches=True,source_ready=False,status='preview')
        s.add(batch);s.flush()
        line=BankStatementLine(company_id=self.company,batch_id=batch.id,account_id=self.acc,line=1,
            date=day,direction='out',amount=100,document_number='synthetic',correspondent_mfo='',
            correspondent_account='',correspondent_name='',correspondent_inn='',purpose='synthetic',
            fingerprint=str(rid),status=status,request_id=rid)
        s.add(line);s.flush()
        return line.id

    def maintenance(self,s):
        s.info['company_id']=self.company
        return s.get(User,1)

    def test_close_physically_removes_versions_and_history_but_keeps_general_audit(self):
        rid=self.draft(purpose='Private synthetic request contents')
        self.assertEqual(self.upload(rid).status_code,200)
        self.assertEqual(self.upload(rid).status_code,200)
        other=self.draft()
        with unit(True) as s:
            s.add(Audit(company_id=self.company,entity='request_document',entity_id='synthetic',
                action='Добавлен документ к заявке',detail=f'request={rid}; private synthetic file'))
            unassigned=s.scalar(select(Company.id).where(Company.code=='UNASSIGNED'))
            for legacy_company in (None,unassigned):
                s.add(Audit(company_id=legacy_company,entity='request_document',entity_id='legacy',
                    action='Добавлен документ к заявке',detail=f'request={rid}; old private file history'))
            s.add(Audit(company_id=unassigned,entity='request_document',entity_id='unrelated',
                action='Добавлен документ к заявке',detail=f'request={other}; keep another request history'))
            s.add(Audit(company_id=self.company,entity='system',action='Unrelated synthetic event',detail='preserve'))
            documents=list(s.scalars(select(RequestDocument.id).where(RequestDocument.request_id==rid)))
        response=self.close(rid)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json(),{'deleted':True,'permanent':True,'id':rid})
        self.assertEqual(self.client.get(f'/api/requests/{rid}',headers=self.h).status_code,404)
        with unit() as s:
            self.assertIsNone(s.get(PaymentRequest,rid))
            self.assertTrue(all(s.get(RequestDocument,did) is None for did in documents))
            self.assertEqual(s.scalar(select(func.count(Audit.id)).where(request_journal(rid))),0)
            self.assertIsNotNone(s.get(PaymentRequest,other))
            self.assertIsNotNone(s.scalar(select(Audit).where(Audit.detail==f'request={other}; keep another request history')))
            self.assertIsNotNone(s.scalar(select(Audit).where(Audit.action=='Unrelated synthetic event')))
            event=s.scalar(select(Audit).where(Audit.entity=='request_retention'))
            self.assertEqual(event.entity_id,'')
            self.assertEqual(json.loads(event.detail),{'count':1})
            self.assertNotIn('Private',event.detail)
        with self.assertRaises(HTTPException) as caught:
            with unit(True) as s:
                s.delete(s.scalar(select(Audit).where(Audit.action=='Unrelated synthetic event')))
                s.flush()
        self.assertEqual(caught.exception.status_code,409)
        self.assertEqual(self.close(rid,version=1).status_code,404)

    def test_rights_versions_and_explanation_remain_required(self):
        rid=self.draft()
        version=self.version_of(rid)
        self.assertEqual(self.close(rid,version=version+1).status_code,409)
        self.assertEqual(self.close(rid,note='short').status_code,422)
        stranger,h=self.make_user('employee','retention_stranger')
        try:
            response=self.post(f'/api/requests/{rid}/decision',
                {'action':'close','version':version,'note':'Cannot remove another author request'},stranger,h)
            self.assertEqual(response.status_code,404)
        finally:stranger.close()
        _,other_h,_,_=self.second_company()
        response=self.client.post(f'/api/requests/{rid}/decision',headers=other_h,json={
            'action':'close','version':version,'note':'Cannot remove another company request'})
        self.assertEqual(response.status_code,404)
        self.assertIsNotNone(self.row(rid))

    def test_any_linked_ledger_blocks_removal_and_preserves_files_history_and_balance(self):
        rid=self.draft()
        document=self.upload(rid).json()['id']
        with unit(True) as s:entry_id=self.fact(s,rid).id
        before=self.client.get('/api/accounts',headers=self.h).json()
        response=self.close(rid)
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(self.client.get('/api/accounts',headers=self.h).json(),before)
        with unit() as s:
            self.assertIsNotNone(s.get(PaymentRequest,rid))
            self.assertIsNotNone(s.get(RequestDocument,document))
            self.assertEqual(s.get(Ledger,entry_id).request_id,rid)
            self.assertGreater(s.scalar(select(func.count(Audit.id)).where(request_journal(rid))),0)
        # Even an inconsistent legacy cross-company FK must block, never break a fact.
        _,_,foreign_cat,foreign_acc=self.second_company()
        with unit(True) as s:
            entry=s.get(Ledger,entry_id);entry.account_id=foreign_acc;entry.category_id=foreign_cat
        self.assertEqual(self.close(rid).status_code,409)
        with unit() as s:self.assertEqual(s.get(Ledger,entry_id).request_id,rid)

    def test_unfunded_plan_is_removed_but_received_money_is_never_removed(self):
        rid=self.funding_plan()
        with unit() as s:fid=s.scalar(select(CashFunding.id).where(CashFunding.request_id==rid))
        self.assertEqual(self.close(rid).status_code,200)
        with unit() as s:self.assertIsNone(s.get(CashFunding,fid))
        rid=self.funding_plan()
        for state in ('in_transit','received'):
            with unit(True) as s:s.scalar(select(CashFunding).where(CashFunding.request_id==rid)).state=state
            response=self.close(rid)
            self.assertEqual(response.status_code,409,response.text)
            with unit() as s:self.assertEqual(s.scalar(select(CashFunding).where(CashFunding.request_id==rid)).state,state)

    def test_unfunded_plan_with_actual_ledger_leg_and_payment_group_is_blocked(self):
        funding_rid=self.funding_plan()
        target=self.draft()
        with unit(True) as s:
            funding=s.scalar(select(CashFunding).where(CashFunding.request_id==funding_rid))
            entry=self.fact(s,target)
            funding.transfer_ledger_id=entry.id
            group=CashFundingPaymentGroup(company_id=self.company,funding_id=funding.id,key='synthetic',
                payload_hash='b'*64,total_amount=100,remaining_amount=0,cash_balance=0,funding_version=funding.version,
                request_version=1,date=date.fromisoformat(self.date),reference='synthetic',note='',creator_id=1)
            s.add(group);s.flush()
            s.add(CashFundingPaymentItem(company_id=self.company,group_id=group.id,request_id=target,
                ledger_id=entry.id,amount=100))
        self.assertEqual(self.close(funding_rid).status_code,409)
        self.assertEqual(self.close(target).status_code,409)
        with unit() as s:
            self.assertIsNotNone(s.get(PaymentRequest,funding_rid))
            self.assertIsNotNone(s.get(PaymentRequest,target))
            self.assertEqual(s.scalar(select(func.count(CashFundingPaymentItem.id))),1)

    def test_bank_statement_candidate_and_posted_link_both_block_removal(self):
        for status in ('auto_pay_candidate','auto_paid'):
            rid=self.draft()
            with unit(True) as s:line_id=self.statement_link(s,rid,status)
            response=self.close(rid)
            self.assertEqual(response.status_code,409,response.text)
            with unit() as s:
                line=s.get(BankStatementLine,line_id)
                self.assertEqual((line.request_id,line.status),(rid,status))

    def test_rollback_restores_request_files_and_history_if_final_audit_fails(self):
        rid=self.draft()
        doc=self.upload(rid).json()['id']
        with self.assertRaises(RuntimeError):
            with unit(True) as s:
                user=self.maintenance(s)
                with patch('app.request_retention.log',side_effect=RuntimeError('Synthetic failure')):
                    permanently_delete_unpaid_request(s,user,s.get(PaymentRequest,rid))
        with unit() as s:
            self.assertIsNotNone(s.get(PaymentRequest,rid))
            self.assertIsNotNone(s.get(RequestDocument,doc))
            self.assertGreater(s.scalar(select(func.count(Audit.id)).where(request_journal(rid))),0)

    def test_cancelled_purge_requires_reviewed_versions_and_preserves_other_statuses_and_companies(self):
        eligible=self.draft()
        self.upload(eligible)
        blocked=self.draft()
        deleted=self.draft()
        _,other_h,other_cat,other_acc=self.second_company()
        foreign=self.request(submit=False,headers=other_h,account=other_acc,category=other_cat).json()['id']
        with unit(True) as s:
            for rid in (eligible,blocked,foreign):s.get(PaymentRequest,rid).status='cancelled'
            s.get(PaymentRequest,deleted).status='deleted'
            self.fact(s,blocked)
        with unit() as s:
            report=purge_cancelled_requests(s,self.maintenance(s),company_id=self.company)
            self.assertEqual([x['id'] for x in report['eligible']],[eligible])
            self.assertEqual([x['id'] for x in report['blocked']],[blocked])
            self.assertIsNotNone(s.get(PaymentRequest,eligible))
        expected={x['id']:x['version'] for x in report['eligible']}
        with unit(True) as s:s.get(PaymentRequest,eligible).purpose='Changed after preview'
        with self.assertRaises(HTTPException) as caught:
            with unit(True) as s:purge_cancelled_requests(s,self.maintenance(s),company_id=self.company,
                dry_run=False,expected_versions=expected)
        self.assertEqual(caught.exception.status_code,409)
        with unit(True) as s:
            user=self.maintenance(s)
            fresh=purge_cancelled_requests(s,user,company_id=self.company)
            result=purge_cancelled_requests(s,user,company_id=self.company,dry_run=False,
                expected_versions={x['id']:x['version'] for x in fresh['eligible']})
            self.assertEqual(result['deleted'],1)
        with unit() as s:
            self.assertIsNone(s.get(PaymentRequest,eligible))
            for rid in (blocked,deleted,foreign):self.assertIsNotNone(s.get(PaymentRequest,rid))

    def test_request_numbers_are_never_reused_after_deleting_highest_id_across_companies(self):
        first=self.draft()
        highest=self.draft()
        self.assertEqual(self.close(highest).status_code,200)
        _,other_h,other_cat,other_acc=self.second_company()
        response=self.request(submit=False,headers=other_h,account=other_acc,category=other_cat)
        self.assertEqual(response.status_code,200,response.text)
        new=response.json()['id']
        self.assertGreater(new,highest)
        self.assertGreater(self.draft(),new)
        self.assertIsNotNone(self.row(first))

    @unittest.skipUnless(SQLITE,'SQLite legacy rowid allocation')
    def test_sqlite_legacy_ids_are_preserved_when_no_high_watermark_exists(self):
        self.draft()
        with unit(True) as s:
            s.delete(s.get(Setting,ID_HIGH_WATER_KEY))
            s.add(PaymentRequest(id=900,creator_id=1,category_id=self.cat,account_id=self.acc,
                counterparty='Synthetic legacy request',purpose='Synthetic legacy request',amount=100,
                due_date=date.fromisoformat(self.date),status='draft'))
        self.assertEqual(self.close(900).status_code,200)
        self.assertEqual(self.draft(),901)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(RequestRetentionTests(name) for name in RequestRetentionTests.__dict__ if name.startswith('test_'))


def tearDownModule():base.tearDownModule()
