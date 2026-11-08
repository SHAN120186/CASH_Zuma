"""Read optimizations must preserve fresh money, access, history and pagination."""
import unittest
from datetime import date
from sqlalchemy import event, select
from sqlalchemy.orm import Session
import tests.test_site as base
from tests.test_request_batch_history import RequestBatchHistoryTests
from app.db import unit, engine, User, Account, Company, Ledger, PaymentRequest, CashFunding
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
            self.assertLessEqual(len(selects),9,(path,selects))
            self.assertFalse(any('insert into' in sql for sql in statements))
            if path==paths[1]:
                self.assertEqual(sum('from request_documents' in sql for sql in selects),1)
                self.assertEqual(sum('from audit_log' in sql for sql in selects),1)
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


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(RequestReadTests(name) for name in RequestReadTests.__dict__ if name.startswith('test_'))

def tearDownModule():base.tearDownModule()
