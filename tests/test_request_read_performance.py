"""Read optimizations must preserve fresh money, access, history and pagination."""
import unittest
import json
from datetime import date, datetime, timedelta
from sqlalchemy import event, select
from sqlalchemy.orm import Session
import tests.test_site as base
from tests.test_request_batch_history import RequestBatchHistoryTests
from app.db import unit, engine, User, Account, Company, Ledger, PaymentRequest, CashFunding, Setting, Delegation, Audit, RequestDocument
from app.company_scope import setting_key
from app.services import request_batch
from app.requests_api import request_view


class RequestReadTests(base.SiteTests):
    fixture=RequestBatchHistoryTests.fixture

    def test_aggregate_matches_legacy_without_loading_unrelated_financial_rows(self):
        ids,_=self.fixture(4)
        with unit(True) as s:
            admin=s.scalar(select(User).where(User.username=='admin'))
            for n in range(500):
                s.add(Ledger(account_id=self.acc,category_id=self.cat,kind='out',amount=300,date=date(2026,9,16),
                             reference=f'bulk-{n}',creator_id=admin.id))
                s.add(PaymentRequest(creator_id=admin.id,category_id=self.cat,account_id=self.acc,
                    counterparty='Synthetic reserve',amount=500,due_date=date(2026,9,16),purpose='Synthetic',status='approved'))
            funding=s.scalar(select(CashFunding).where(CashFunding.request_id==ids[3]));funding.mode='batch'
            # A legacy inconsistent foreign-company article reference must not leak.
            other=s.scalar(select(Company).where(Company.code=='ZUMA'))
            a=Account(company_id=other.id,name='Foreign synthetic bank',kind='bank',currency='UZS',opening=0,
                      opening_date=date(2026,1,1),created_by=admin.id);s.add(a);s.flush()
            s.add(Ledger(account_id=a.id,category_id=self.cat,kind='out',amount=900000000,date=date(2026,9,16),
                         reference='foreign',creator_id=admin.id))
            original=Ledger(account_id=self.acc,category_id=self.cat,kind='out',amount=7654,date=date(2026,9,16),
                            reference='reversed',creator_id=admin.id);s.add(original);s.flush()
            s.add(Ledger(account_id=self.acc,category_id=self.cat,kind='out',amount=-7654,date=date(2026,9,16),
                         reference='reversal',creator_id=admin.id,reversal_of=original.id))
        for batched in (False,True):
            with unit() as s:
                s.info['company_id']=self.company
                user=s.scalar(select(User).where(User.username=='admin'))
                rs=list(s.scalars(select(PaymentRequest).where(PaymentRequest.id.in_(ids)).order_by(PaymentRequest.id)))
                loaded=[]
                def record(session,obj):loaded.append(type(obj).__name__)
                event.listen(Session,'loaded_as_persistent',record)
                try:
                    b=request_batch(s,rs) if batched else None
                    actual=[request_view(s,user,r,batch=b) for r in rs]
                finally:event.remove(Session,'loaded_as_persistent',record)
                if not batched:expected=actual
                else:
                    self.assertEqual(actual,expected)
                    self.assertNotIn('Ledger',loaded)
                    self.assertNotIn('PaymentRequest',loaded)
                    self.assertNotIn('Budget',loaded)
                    self.assertEqual(actual[0]['budget']['spent'],'1500.00')

    def test_http_list_card_query_bounds_pagination_and_fresh_values(self):
        ids,_=self.fixture(4)
        paths=['/api/requests?paginated=true&page_size=2',f'/api/requests/{ids[0]}']
        for path in paths:
            statements=[]
            def record(conn,cursor,statement,parameters,context,many):statements.append(statement.lower())
            event.listen(engine,'before_cursor_execute',record)
            try:response=self.client.get(path,headers=self.h)
            finally:event.remove(engine,'before_cursor_execute',record)
            self.assertEqual(response.status_code,200,response.text)
            selects=[sql for sql in statements if sql.lstrip().startswith('select')]
            self.assertLessEqual(len(selects),5,(path,selects))
            self.assertFalse(any('insert into' in sql for sql in statements))
            if path==paths[1]:
                self.assertEqual(sum('from request_documents' in sql for sql in selects),1)
                self.assertEqual(sum('from audit_log' in sql for sql in selects),1)
                self.assertFalse(any('request_documents.content' in sql for sql in selects))
            print('HTTP_REQUEST_SELECTS',path,len(selects))
        first=self.client.get(paths[0],headers=self.h).json()
        second=self.client.get(paths[0]+'&page=2',headers=self.h).json()
        beyond=self.client.get(paths[0]+'&page=99',headers=self.h).json()
        self.assertEqual((first['total'],second['total'],beyond['total']),(4,4,4))
        self.assertEqual([r['id'] for r in first['items']+second['items']],ids)
        self.assertEqual(beyond['items'],[])
        self.assertEqual(self.client.get(paths[0]+'&q=definitely-missing',headers=self.h).json()['total'],0)
        self.assertEqual(len(self.client.get('/api/requests',headers=self.h).json()),4)
        with unit(True) as s:s.get(PaymentRequest,ids[0]).amount=12345
        self.assertEqual(self.client.get(paths[1],headers=self.h).json()['amount'],'123.45')
        self.assertEqual(self.client.get(paths[0],headers=self.h).json()['items'][0]['amount'],'123.45')

    def test_card_evidence_matches_separate_reads_with_legacy_history_and_versions(self):
        from unittest.mock import patch
        from app.requests_api import names_of
        from app.services import request_journal
        ids,actors=self.fixture(4)
        with unit(True) as s:
            doc=s.scalar(select(RequestDocument).where(RequestDocument.request_id==ids[0]));doc.active=False
            s.add(RequestDocument(request_id=ids[0],kind='internal',filename='replacement.pdf',mime='application/pdf',
                storage_key='replacement',sha256='new',content=b'large-secret-document',version=2,active=True,
                created_by=actors['procurement']))
            s.add(Audit(company_id=self.company,user_id=actors['cashier'],acting_for_id=actors['finance'],
                entity='request_document',entity_id=str(doc.id),action='Заменён документ заявки',
                detail=f'request={ids[0]}; legacy',ip='192.0.2.1',role='cashier'))
            s.add(Audit(company_id=self.company,user_id=None,entity='request',entity_id=str(ids[0]),
                action='Synthetic system event',detail='system',role=None))
        def legacy(s,rid):
            docs=list(s.scalars(select(RequestDocument).where(RequestDocument.request_id==rid)
                .order_by(RequestDocument.kind,RequestDocument.version,RequestDocument.id)))
            rows=list(s.scalars(select(Audit).where(request_journal(rid)).order_by(Audit.id)))
            names=names_of(s,[d.created_by for d in docs]+[a.user_id for a in rows]+[a.acting_for_id for a in rows])
            return docs,rows,names
        for rid in ids:
            path=f'/api/requests/{rid}'
            actual=self.client.get(path,headers=self.h)
            with patch('app.requests_api.card_evidence',side_effect=legacy):
                expected=self.client.get(path,headers=self.h)
            self.assertEqual(actual.status_code,200,actual.text)
            self.assertEqual(actual.json(),expected.json())

    def test_joined_section_policy_is_fresh_and_removes_staff_round_trip(self):
        ids,_=self.fixture(2)
        client,headers=self.user_in(self.company,'finance','fresh_policy_reader')
        try:
            with unit() as s:uid=s.scalar(select(User.id).where(User.username=='fresh_policy_reader'))
            def get(path):
                statements=[]
                def record(conn,cursor,statement,parameters,context,many):statements.append(statement.lower())
                event.listen(engine,'before_cursor_execute',record)
                try:response=client.get(path,headers=headers)
                finally:event.remove(engine,'before_cursor_execute',record)
                self.assertLessEqual(sum(sql.lstrip().startswith('select') for sql in statements),5,statements)
                return response
            for mode,status in [('view',200),('hidden',403),('edit',200),('default',200)]:
                with unit(True) as s:
                    key=f'sections:{self.company}:{uid}'
                    row=s.get(Setting,key)
                    if row is None:row=Setting(key=key,value='');s.add(row)
                    row.value=json.dumps({'version':1,'sections':{'requests':mode}})
                listed=get('/api/requests?paginated=true');card=get(f'/api/requests/{ids[0]}')
                self.assertEqual((listed.status_code,card.status_code),(status,status))
                if mode=='view':self.assertEqual(card.json()['actions'],[])
        finally:client.close()

    def test_joined_departments_are_fresh_and_company_scoped(self):
        ids,_=self.fixture(2)
        with unit(True) as s:
            s.get(PaymentRequest,ids[0]).department='qc'
            s.info['company_id']=self.company
            key=setting_key(s,'departments')
            other=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
            s.info['company_id']=other
            s.add(Setting(key=setting_key(s,'departments'),value=json.dumps({'version':1,'items':[
                {'code':'qc','name_ru':'Чужой отдел','name_uz':'Begona','active':True}]})))
        def read():
            listed=self.client.get('/api/requests?paginated=true',headers=self.h)
            card=self.client.get(f'/api/requests/{ids[0]}',headers=self.h)
            self.assertEqual((listed.status_code,card.status_code),(200,200))
            return [next(r for r in listed.json()['items'] if r['id']==ids[0]),card.json()]
        defaults=read()
        self.assertEqual(defaults[0]['department_label'],defaults[1]['department_label'])
        self.assertNotEqual(defaults[0]['department_label'],'Чужой отдел')
        for version in (1,2):
            with unit(True) as s:
                row=s.get(Setting,key)
                if row is None:row=Setting(key=key,value='');s.add(row)
                row.value=json.dumps({'version':version,'items':[{'code':'qc','name_ru':f'Отдел {version}',
                    'name_uz':f'Bo‘lim {version}','active':True}]})
            for result in read():
                self.assertEqual(result['department_label'],f'Отдел {version}')
                self.assertEqual(result['department_name_uz'],f'Bo‘lim {version}')

    def test_delegation_changes_take_effect_on_the_next_list_read(self):
        ids,actors=self.fixture(2)
        client,headers=self.user_in(self.company,'employee','live_deputy')
        day=base.today()
        try:
            def total():
                response=client.get('/api/requests?paginated=true',headers=headers)
                self.assertEqual(response.status_code,200,response.text)
                return response.json()['total']
            self.assertEqual(total(),0)
            with unit(True) as s:
                uid=s.scalar(select(User.id).where(User.username=='live_deputy'))
                d=Delegation(company_id=self.company,user_id=uid,replaced_user_id=actors['finance'],
                    role='finance',starts_on=day+timedelta(days=1),ends_on=day+timedelta(days=2),created_by=actors['director'])
                s.add(d);s.flush();did=d.id
            self.assertEqual(total(),0)
            for changes,expected in (({'starts_on':day},2),({'ends_on':day-timedelta(days=1)},0),
                    ({'ends_on':day},2),({'revoked_at':datetime.now()},0),({'revoked_at':None},2)):
                with unit(True) as s:
                    d=s.get(Delegation,did)
                    for key,value in changes.items():setattr(d,key,value)
                self.assertEqual(total(),expected,changes)
            with unit(True) as s:s.get(User,actors['finance']).active=False
            self.assertEqual(total(),0)
        finally:client.close()


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(RequestReadTests(name) for name in RequestReadTests.__dict__ if name.startswith('test_'))

def tearDownModule():base.tearDownModule()
