"""Real company actors, persisted author routes, and holding-admin oversight."""
import json
import unittest
from datetime import date, datetime, timedelta
from sqlalchemy import select, func, delete, update

from tests import test_site as base
from tests.test_cash_funding_batch import CashFundingBatchTests
from app.db import (Account, Audit, CashFunding, CashFundingPaymentGroup, Category, Company, CompanyUser,
                    Delegation, Ledger, PaymentRequest, User, unit, initialize)
from app.security import ADMIN_WORKFLOW_RIGHTS, PERMS, scope_user, perms_of, act_with_right
from app.services import approval_stage, route_complete, today, account_balance


class FinanceAuthorWorkflowTests(CashFundingBatchTests):
    def actor(self,role,name,company=None):
        name=name.lower()
        pair=self.make_user(role,name)
        cid=company or self.company
        with unit(True) as s:
            user=s.scalar(select(User).where(User.username==name));uid=user.id
            if cid!=self.company:
                s.execute(delete(CompanyUser).where(CompanyUser.user_id==uid))
                s.add(CompanyUser(company_id=cid,user_id=uid,role=role))
        return pair[0],{**pair[1],'X-Company-ID':str(cid)}

    def test_finance_author_check_then_director_then_bank_payment_in_both_companies(self):
        with unit() as s:companies=list(s.execute(select(Company.id,Company.code).where(Company.code.in_(('ZUMA','UZGERMED')))))
        for cid,code in companies:
            with self.subTest(company=code):
                headers={**self.h,'X-Company-ID':str(cid)}
                category=self.post('/api/categories',{'name':'Author route '+code,'type':'outcome','activity':'operating'},headers=headers).json()['id']
                bank=self.post('/api/accounts',{'name':'Author bank '+code,'kind':'bank','currency':'UZS','opening':'1000','opening_date':self.date},headers=headers).json()['id']
                with unit(True) as s:
                    c=s.get(Category,category);c.skip_allowed=True;c.director_policy='skip'
                finance=self.actor('finance','author_'+code,cid);checker=self.actor('accountant','check_'+code,cid);director=self.actor('director','director_'+code,cid)
                response=self.request('100',client=finance[0],headers=finance[1],account=bank,category=category)
                self.assertEqual(response.status_code,200,response.text);rid=response.json()['id']
                row=self.row(rid,headers)
                self.assertTrue(row['route']['finance_author']);self.assertFalse(row['route']['finance_required'])
                self.assertEqual(row['route']['policy'],'skip');self.assertTrue(row['route']['director_required'])
                self.assertEqual(row['approval_stage'],'check');self.assertIsNone(row['finance_approved_by'])
                checked=self.post(f'/api/requests/{rid}/decision',{'action':'check'},*checker)
                self.assertEqual(checked.status_code,200,checked.text);self.assertEqual(checked.json()['approval_stage'],'director')
                admin=self.client.get(f'/api/requests/{rid}',headers=headers).json()
                self.assertFalse({'check','approve','pay'} & set(admin['actions']))
                for actor in ((self.client,headers),finance,checker):
                    denied=self.post(f'/api/requests/{rid}/decision',{'action':'approve'},*actor)
                    self.assertEqual(denied.status_code,403,denied.text)
                approved=self.post(f'/api/requests/{rid}/decision',{'action':'approve'},*director)
                self.assertEqual(approved.status_code,200,approved.text)
                self.assertEqual(approved.json()['status'],'approved');self.assertIsNone(approved.json()['finance_approved_by'])
                paid=self.ledger('100','out',reference='Author payment '+code,client=checker,
                    account_id=bank,category_id=category,request_id=rid)
                self.assertEqual(paid.status_code,200,paid.text);self.assertEqual(self.row(rid,headers)['status'],'paid')
                for pair in (finance,checker,director):pair[0].close()

    def test_ordinary_route_and_admin_crud_remain(self):
        rid=self.request('100').json()['id']
        self.assertTrue(self.row(rid)['route']['finance_required'])
        self.assertEqual(self.check(rid).status_code,200)
        self.assertEqual(self.row(rid)['approval_stage'],'finance')
        self.assertEqual(self.finance_approve(rid).status_code,200)
        self.assertEqual(self.row(rid)['approval_stage'],'director')
        approved=self.post(f'/api/requests/{rid}/decision',{'action':'approve'},*self.stage_user('director'))
        self.assertEqual(approved.status_code,200,approved.text)
        paid=self.ledger('100','out',reference='Normal route payment',request_id=rid)
        self.assertEqual(paid.status_code,200,paid.text)
        bank=self.post('/api/accounts',{'name':'Admin managed account','kind':'cash','currency':'UZS','opening':'100','opening_date':self.date})
        self.assertEqual(bank.status_code,200,bank.text)
        self.assertEqual(self.post(f'/api/requests/{rid}/delete',{'version':self.row(rid)['version'],
            'reason':'Administrative hiding preserves completed payment'}).status_code,200)
        with unit() as s:self.assertIsNotNone(s.scalar(select(Ledger.id).where(Ledger.request_id==rid)))

    def test_admin_never_gets_workflow_rights_from_assignments_or_delegations(self):
        with unit() as s:
            user=s.scalar(select(User).where(User.username=='admin'))
            for assigned in (None,'finance','director','accountant','cashier'):
                scope_user(user,assigned,[{'role':role,'replaced_user_id':999} for role in ('finance','director','accountant','cashier')])
                self.assertFalse(ADMIN_WORKFLOW_RIGHTS & perms_of(user))
                self.assertTrue({'write','users','catalog','request_edit','request_delete','import'}<=perms_of(user))
                for right in ADMIN_WORKFLOW_RIGHTS:self.assertFalse(act_with_right(user,right))
            user._permissions=frozenset({'approve','request_check','pay_bank','pay_cash','write'})
            self.assertEqual(perms_of(user),{'write'})
        finance=self.stage_user('finance');rid=self.request('100',client=finance[0],headers=finance[1]).json()['id']
        row=self.row(rid);self.assertNotIn('check',row['actions']);self.assertNotIn('return',row['actions'])
        for action in ('check','approve','return'):
            response=self.post(f'/api/requests/{rid}/decision',{'action':action})
            self.assertEqual(response.status_code,403,response.text)
        self.assertEqual(self.row(rid)['version'],row['version'])
        permissions=self.client.get('/api/bootstrap',headers=self.h).json()['user']['permissions']
        self.assertFalse(ADMIN_WORKFLOW_RIGHTS & set(permissions))

    def test_finance_snapshot_survives_role_change_return_resubmit_and_reschedule(self):
        author=self.actor('finance','persisted_author');rid=self.request('100',client=author[0],headers=author[1]).json()['id']
        self.assertEqual(self.check(rid).status_code,200)
        returned=self.post(f'/api/requests/{rid}/decision',{'action':'return','note':'Synthetic source documents need correction'},*self.stage_user('director'))
        self.assertEqual(returned.status_code,200,returned.text)
        with unit(True) as s:s.scalar(select(User).where(User.username=='persisted_author')).role='admin'
        initialize()
        self.assertTrue(self.row(rid)['route']['finance_author'])
        submitted=self.post(f'/api/requests/{rid}/decision',{'action':'submit'},*author)
        self.assertEqual(submitted.status_code,200,submitted.text);self.assertEqual(submitted.json()['approval_stage'],'check')
        self.assertEqual(self.check(rid).status_code,200)
        moved=self.post(f'/api/requests/{rid}/decision',{'action':'reschedule','date':self.date,
            'note':'Administrative date correction requires company approval'})
        self.assertEqual(moved.status_code,200,moved.text);self.assertEqual(moved.json()['approval_stage'],'director')
        self.assertIsNone(moved.json()['finance_approved_by'])

    def test_safe_pending_migration_preserves_checks_versions_facts_and_completed_routes(self):
        finance=self.actor('finance','migration_author');checker=self.stage_user('check')
        with unit(True) as s:
            author=s.scalar(select(User).where(User.username=='migration_author'))
            check=s.scalar(select(User).where(User.username=='checker'))
            def row(status='pending',**values):
                item=PaymentRequest(creator_id=author.id,last_editor_id=author.id,category_id=self.cat,account_id=self.acc,
                    counterparty='Synthetic migration recipient',purpose='Synthetic old source',amount=10000,due_date=date.fromisoformat(self.date),
                    status=status,route_policy='skip',route_threshold=222000,checked_by=check.id,finance_author=None,**values)
                s.add(item);s.flush()
                s.add(Audit(company_id=self.company,user_id=author.id,action='Создана заявка',entity='request',entity_id=str(item.id),role='finance',detail=''))
                return item
            pending=row();approved=row('approved',finance_approved_by=check.id,approved_by=check.id)
            paid=row('paid',finance_approved_by=check.id,approved_by=check.id)
            partial=row(finance_approved_by=check.id);with_fact=row()
            s.add(Ledger(account_id=self.acc,category_id=self.cat,kind='out',amount=10000,date=date.fromisoformat(self.date),
                counterparty='Synthetic historical fact',reference='PRESERVED-FACT',request_id=with_fact.id,creator_id=check.id))
            ids=[pending.id,approved.id,paid.id,partial.id,with_fact.id];versions=[r.version for r in (pending,approved,paid,partial,with_fact)]
        initialize();initialize()
        with unit() as s:
            rows=[s.get(PaymentRequest,id) for id in ids];current=rows[0]
            self.assertTrue(current.finance_author);self.assertEqual(current.version,versions[0]+1)
            self.assertEqual(current.checked_by,check.id);self.assertIsNone(current.finance_approved_by);self.assertIsNone(current.approved_by)
            self.assertEqual((current.route_policy,current.route_threshold,current.status),('skip',222000,'pending'))
            self.assertEqual(approval_stage(current),'director')
            self.assertTrue(all(r.finance_author is None for r in rows[1:]));self.assertEqual([r.version for r in rows[1:]],versions[1:])
            audits=list(s.scalars(select(Audit).where(Audit.action=='Уточнён маршрут заявки финансового директора')))
            self.assertEqual(len(audits),1);self.assertEqual(audits[0].entity_id,str(current.id));self.assertIsNone(audits[0].user_id)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),1)

    def test_ambiguous_legacy_holding_admin_not_reclassified_as_finance_author(self):
        with unit(True) as s:
            admin=s.scalar(select(User).where(User.username=='admin'))
            row=PaymentRequest(creator_id=admin.id,last_editor_id=admin.id,category_id=self.cat,account_id=self.acc,
                counterparty='Legacy administrator',purpose='Synthetic historical role ambiguity',amount=10000,
                due_date=date.fromisoformat(self.date),status='pending',route_policy='skip',finance_author=None)
            s.add(row);s.flush();id=row.id
            s.add(Audit(company_id=self.company,user_id=admin.id,action='Создана заявка',entity='request',entity_id=str(id),role='finance',detail=''))
        initialize()
        with unit() as s:self.assertFalse(s.get(PaymentRequest,id).finance_author)

    def test_delegated_finance_creation_persists_actual_creation_role(self):
        actor=self.actor('accountant','delegated_author');finance=self.actor('finance','replaced_finance')
        with unit(True) as s:
            admin=s.scalar(select(User).where(User.username=='admin'))
            uid=s.scalar(select(User.id).where(User.username=='delegated_author'));replaced=s.scalar(select(User.id).where(User.username=='replaced_finance'))
            s.add(Delegation(company_id=self.company,user_id=uid,replaced_user_id=replaced,role='finance',starts_on=today(),
                ends_on=today()+timedelta(days=2),reason='Synthetic authorised finance replacement',created_by=admin.id))
        created=self.request('100',client=actor[0],headers=actor[1])
        self.assertEqual(created.status_code,200,created.text);self.assertTrue(created.json()['route']['finance_author'])
        with unit() as s:
            audit=s.scalar(select(Audit).where(Audit.entity=='request',Audit.entity_id==str(created.json()['id']),Audit.action=='Создана заявка'))
            self.assertEqual(audit.role,'finance');self.assertEqual(audit.acting_for_id,replaced)

    def test_admin_cash_workflow_and_replayed_group_are_denied_without_mutations(self):
        self.fixture();row=self.row(self.parent)
        self.assertFalse(row['cash_funding']['can_withdraw']);self.assertFalse(row['cash_funding']['can_receive'])
        self.assertFalse(self.state((self.client,self.h))['can_pay'])
        parent_pay=self.ledger('990000','out',client=(self.client,self.h),account_id=self.cash,category_id=self.cat,request_id=self.parent)
        self.assertEqual(parent_pay.status_code,403,parent_pay.text)
        result,payload,_=self.pay()
        with unit(True) as s:
            admin=s.scalar(select(User).where(User.username=='admin'))
            # Historical administrator-issued group, created before this release.
            s.execute(update(CashFundingPaymentGroup).where(CashFundingPaymentGroup.id==result['group']['id']).values(creator_id=admin.id))
        replay=self.post(f'/api/requests/{self.parent}/cash-funding/payments/commit',payload)
        self.assertEqual(replay.status_code,403,replay.text)
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),5)

    def test_malformed_finance_author_director_snapshot_cannot_be_paid(self):
        finance=self.stage_user('finance');created=self.request('100',client=finance[0],headers=finance[1]);rid=created.json()['id']
        self.assertEqual(self.check(rid).status_code,200)
        with unit(True) as s:
            row=s.get(PaymentRequest,rid);row.status='approved';row.approved_by=row.checked_by;row.approved_at=datetime.combine(today(),datetime.min.time())
        denied=self.ledger('100','out',reference='Invalid director snapshot',request_id=rid)
        self.assertEqual(denied.status_code,409,denied.text)
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),0)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(FinanceAuthorWorkflowTests(name) for name in FinanceAuthorWorkflowTests.__dict__ if name.startswith('test_'))


def tearDownModule():base.tearDownModule()
