"""Bulk action checks preserve the scoped, fresh single-request rules."""
import unittest
from datetime import date, datetime
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import event, select
import tests.test_site as base
from app.db import (unit, engine, User, Account, Company, CompanyUser,
                    PaymentRequest, Audit, CashFunding, RequestDocument, Ledger)
from app.security import scope_user
from app.services import request_batch, all_participants, round_participants, check_payer
from app.requests_api import request_view
from app.cash_funding import funding_for, _authorized


class RequestBatchHistoryTests(base.SiteTests):
    def fixture(self, count=50):
        with unit(True) as s:
            author = s.scalar(select(User).where(User.username == 'admin'))
            actors = {}
            for role in ('finance', 'director', 'accountant', 'cashier', 'procurement', 'employee', 'auditor', 'founder'):
                actor = User(username='bulk_' + role, name='Synthetic ' + role, role=role, password_hash=base.HASH)
                s.add(actor);s.flush()
                if role != 'founder':s.add(CompanyUser(company_id=self.company,user_id=actor.id,role=role))
                actors[role] = actor.id
            cash = Account(company_id=self.company,name='Bulk cash',kind='cash',currency='UZS',opening=100000000,
                           opening_date=date(2026,1,1),created_by=author.id)
            s.add(cash);s.flush()
            ids=[]
            for n in range(count):
                status = ('pending','approved','pending','approved','returned')[n % 5]
                r=PaymentRequest(creator_id=author.id,last_editor_id=author.id,category_id=self.cat,
                                 account_id=cash.id if n % 2 else self.acc,counterparty='Synthetic bulk recipient',
                                 amount=9900,due_date=date(2026,9,16),purpose='Synthetic bulk action parity',status=status,
                                 route_policy='skip',checked_by=actors['accountant'] if status=='approved' or n % 3 else None,
                                 finance_approved_by=actors['finance'] if status=='approved' else None,
                                 approved_at=datetime(2026,9,16) if status=='approved' else None)
                s.add(r);s.flush();ids.append(r.id)
                # Identical timestamps: return boundaries must use audit IDs.
                for action,actor in (('Изменена заявка',actors['cashier']),
                                     ('Действие по заявке: return',None),
                                     ('Добавлен документ заявки',actors['procurement']),
                                     ('Действие по заявке: check',actors['accountant'])):
                    s.add(Audit(company_id=self.company,user_id=actor,entity='request',entity_id=str(r.id),
                                action=action,detail='',created_at=datetime(2026,9,16,10)))
                    s.flush()
                if n % 4 == 3 and r.account_id==cash.id:
                    s.add(CashFunding(company_id=self.company,request_id=r.id,bank_account_id=self.acc,cash_account_id=cash.id,
                                      gross_amount=10000,fee_percent='1.00',fee_amount=100,net_amount=9900,
                                      fee_category_id=self.cat,state=('unfunded','in_transit','received')[n % 3],created_by=author.id))
                s.add(RequestDocument(request_id=r.id,kind='internal',filename='synthetic.pdf',mime='application/pdf',
                                      storage_key='synthetic-' + str(r.id),sha256='synthetic',content=b'x',version=1,active=True,
                                      created_by=author.id))
        return ids,actors

    def scoped(self, s, role, uid):
        s.info['company_id']=self.company
        user=s.get(User,uid)
        scope_user(user,role if role!='founder' else None)
        return user

    def test_bulk_actions_match_legacy_for_roles_funding_and_read_only_sections(self):
        ids,actors=self.fixture(10)
        for role,uid in actors.items():
            with self.subTest(role=role),unit() as s:
                user=self.scoped(s,role,uid)
                rows=list(s.scalars(select(PaymentRequest).where(PaymentRequest.id.in_(ids)).order_by(PaymentRequest.id)))
                expected=[request_view(s,user,r) for r in rows]
                batch=request_batch(s,rows)
                self.assertEqual(expected,[request_view(s,user,r,batch=batch) for r in rows])
                if role=='accountant':
                    self.assertTrue(any(row['cash_funding'] and row['cash_funding']['can_withdraw'] for row in expected))
                user._sections={'requests':'view'}
                self.assertTrue(all(not request_view(s,user,r,batch=batch)['actions'] for r in rows))

    def test_query_count_does_not_grow_with_action_rows_or_funding(self):
        ids,actors=self.fixture(50)
        for role in ('finance','accountant','cashier'):
            counts=[]
            for count in (1,10,50):
                with unit() as s:
                    user=self.scoped(s,role,actors[role])
                    rows=list(s.scalars(select(PaymentRequest).where(PaymentRequest.id.in_(ids[:count])).order_by(PaymentRequest.id)))
                    statements=[]
                    def counter(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
                    event.listen(engine,'before_cursor_execute',counter)
                    try:
                        batch=request_batch(s,rows)
                        data=[request_view(s,user,r,batch=batch) for r in rows]
                    finally:event.remove(engine,'before_cursor_execute',counter)
                    counts.append(len(statements))
                    self.assertEqual(len(data),count)
            self.assertEqual(counts,[counts[0]]*3,(role,counts))
            self.assertLessEqual(max(counts),11,(role,counts))
            print('ACTION_SQL_COUNTS',role,counts)

    def test_legacy_prefix_boundaries_round_ids_and_company_scope(self):
        ids,actors=self.fixture(12)
        rid,other=ids[0],ids[10]
        with unit(True) as s:
            foreign=s.scalar(select(Company).where(Company.id!=self.company,Company.code!='UNASSIGNED'))
            # Same request ID in a foreign company's generic journal must not leak.
            s.add(Audit(company_id=foreign.id,user_id=actors['employee'],entity='request',entity_id=str(rid),
                        action='Изменена заявка',detail='foreign',created_at=datetime(2026,9,16,10)))
            s.add(Audit(company_id=self.company,user_id=actors['director'],entity='request_document',entity_id='doc-a',
                        action='Добавлен документ к заявке',detail=f'request={rid}; synthetic legacy',created_at=datetime(2026,9,16,10)))
            s.add(Audit(company_id=self.company,user_id=actors['auditor'],entity='request_document',entity_id='doc-b',
                        action='Удалён документ из черновика заявки',detail=f'request={other}; not the first request',created_at=datetime(2026,9,16,10)))
            s.add(Audit(company_id=self.company,user_id=actors['employee'],entity='request_document',entity_id='doc-c',
                        action='Добавлен документ к заявке',detail=f'request={rid}0; prefix collision',created_at=datetime(2026,9,16,10)))
        with unit() as s:
            s.info['company_id']=self.company
            r=s.get(PaymentRequest,rid)
            batch=request_batch(s,[r])
            self.assertEqual(all_participants(s,r),all_participants(s,r,batch=batch))
            self.assertEqual(all_participants(s,r,False),all_participants(s,r,False,batch=batch))
            self.assertEqual(round_participants(s,r),round_participants(s,r,batch=batch))
            self.assertIn(actors['director'],all_participants(s,r,batch=batch))
            self.assertNotIn(actors['auditor'],all_participants(s,r,batch=batch))
            self.assertNotIn(actors['employee'],all_participants(s,r,batch=batch))
            self.assertNotIn(actors['cashier'],round_participants(s,r,batch=batch))
            self.assertIn(actors['procurement'],round_participants(s,r,batch=batch))
            self.assertIn(actors['accountant'],all_participants(s,r,batch=batch))
            # A historic checker is ignored for bank payment only, as before.
            r.checked_by=None
            self.assertNotIn(actors['accountant'],all_participants(s,r,False,batch=batch))

    def test_write_guards_query_fresh_history_after_a_read_batch(self):
        ids,actors=self.fixture(4)
        with unit(True) as s:
            user=self.scoped(s,'accountant',actors['accountant'])
            r=s.get(PaymentRequest,ids[1])
            r.account_id=self.acc;r.checked_by=actors['accountant']
            batch=request_batch(s,[r])
            self.assertNotIn(user.id,all_participants(s,r,False,batch=batch))
            s.add(Audit(company_id=self.company,user_id=user.id,entity='request',entity_id=str(r.id),action='Изменена заявка',detail='new edit after list read'))
            s.flush()
            self.assertIn(user.id,all_participants(s,r,False))
            with self.assertRaises(HTTPException) as denied:check_payer(s,user,r)
            self.assertIn('Автор, редакторы',denied.exception.detail)
            s.add(Audit(company_id=self.company,user_id=actors['employee'],entity='request',entity_id=str(r.id),action='Изменена заявка',detail='new round editor after list read'))
            s.flush()
            self.assertNotIn(actors['employee'],round_participants(s,r,batch=batch))
            self.assertIn(actors['employee'],round_participants(s,r))
            # Negative funding results are not left in Session.info for mutations.
            self.assertIsNone(funding_for(s,r.id))
            cash=s.scalar(select(Account).where(Account.kind=='cash'))
            funding=CashFunding(company_id=self.company,request_id=r.id,bank_account_id=self.acc,cash_account_id=cash.id,
                                gross_amount=10000,fee_percent='1.00',fee_amount=100,net_amount=r.amount,
                                fee_category_id=self.cat,state='unfunded',created_by=r.creator_id)
            s.add(funding);s.flush()
            self.assertEqual(funding_for(s,r.id).id,funding.id)
            r.account_id=cash.id
            _authorized(s,user,r,funding,'withdraw',batch=batch)
            with self.assertRaises(HTTPException) as denied:_authorized(s,user,r,funding,'withdraw')
            self.assertIn('Автор, редакторы',denied.exception.detail)

    def test_paid_backdated_flag_uses_earliest_payment_time_even_without_a_user(self):
        ids,actors=self.fixture(1)
        rid=ids[0]
        with unit(True) as s:
            r=s.get(PaymentRequest,rid);r.status='paid'
            s.add(Ledger(account_id=r.account_id,category_id=r.category_id,kind='out',amount=r.amount,
                         date=date(2026,9,10),reference='synthetic-paid-history',creator_id=r.creator_id,request_id=rid))
            for at in (datetime(2026,9,20),datetime(2026,9,9)):
                s.add(Audit(company_id=self.company,user_id=None,entity='request',entity_id=str(rid),
                            action='Оплата заявки',detail='',created_at=at))
        with unit() as s:
            user=self.scoped(s,'finance',actors['finance']);r=s.get(PaymentRequest,rid)
            legacy=request_view(s,user,r)
            batched=request_view(s,user,r,batch=request_batch(s,[r]))
            self.assertEqual(legacy,batched)
            self.assertFalse(batched['backdated_posted'])

    def test_card_payload_matches_legacy_serializer_with_history_and_documents(self):
        ids,_=self.fixture(4)
        for rid in (ids[0],ids[3]):
            with self.subTest(rid=rid),patch('app.requests_api.request_batch',return_value=None):
                legacy=self.client.get(f'/api/requests/{rid}',headers=self.h)
                self.assertEqual(legacy.status_code,200,legacy.text)
            batched=self.client.get(f'/api/requests/{rid}',headers=self.h)
            self.assertEqual(batched.status_code,200,batched.text)
            self.assertEqual(legacy.json(),batched.json())
            self.assertTrue(batched.json()['history'])
            self.assertTrue(batched.json()['documents_list'])


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(RequestBatchHistoryTests(name) for name in RequestBatchHistoryTests.__dict__ if name.startswith('test_'))


def tearDownModule():base.tearDownModule()


if __name__=='__main__':unittest.main()
