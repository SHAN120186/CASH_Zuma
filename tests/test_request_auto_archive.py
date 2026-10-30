"""Display archive keeps financial obligations and access unchanged."""
import unittest
from datetime import timedelta
from unittest.mock import patch
import tests.test_site as base
from sqlalchemy import select
from app.db import unit, now, PaymentRequest, Company, Audit
from app.services import budget_state

class RequestAutoArchiveTests(base.SiteTests):
    def listing(self, **params):
        r=self.client.get('/api/requests',params={'paginated':'true',**params},headers=self.h)
        self.assertEqual(r.status_code,200,r.text)
        data=r.json()
        return {x['id'] for x in data['items']},data['total']

    def test_boundary_period_and_financial_reserve(self):
        ids=[self.request('10',submit=False).json()['id'] for _ in range(5)]
        stamp=now()
        with unit(True) as s:
            for rid,status,age in zip(ids,['approved','approved','paid','pending','approved'],[24,23,48,48,None]):
                r=s.get(PaymentRequest,rid);r.status=status
                r.approved_at=stamp-timedelta(hours=age) if age is not None else None
            before=budget_state(s,self.cat,self.month,'UZS')['reserved']
        with patch('app.main.now',return_value=stamp):
            self.assertEqual(self.listing(archive='active'),({ids[1],ids[3],ids[4]},3))
            self.assertEqual(self.listing(archive='archived'),({ids[0],ids[2]},2))
            self.assertEqual(self.listing(),(set(ids),5))  # Existing API clients retain all rows.
            self.assertEqual(self.listing(archive='active',date_from=self.date,date_to=self.date),(set(ids),5))
            self.assertEqual(self.listing(archive='archived',date_from=self.date),(set(ids),5))
            self.assertEqual(self.listing(archive='archived',state='approved'),({ids[0]},1))
        with patch('app.main.now',return_value=stamp+timedelta(hours=1)):
            self.assertEqual(self.listing(archive='archived'),({ids[0],ids[1],ids[2]},3))
        self.assertEqual(self.row(ids[0])['status'],'approved')
        with unit() as s:
            self.assertEqual(budget_state(s,self.cat,self.month,'UZS')['reserved'],before)

    def test_closed_requests_use_closure_time(self):
        ids=[self.request('10',submit=False).json()['id'] for _ in range(3)]
        stamp=now()
        with unit(True) as s:
            for rid in ids:
                r=s.get(PaymentRequest,rid);r.status='cancelled';r.created_at=stamp-timedelta(days=30)
            for rid,age in [(ids[0],24),(ids[1],23)]:
                s.add(Audit(company_id=self.company,entity='request',entity_id=str(rid),action='Действие по заявке: cancel',created_at=stamp-timedelta(hours=age)))
        with patch('app.main.now',return_value=stamp):
            self.assertEqual(self.listing(archive='active'),({ids[1],ids[2]},2))
            self.assertEqual(self.listing(archive='archived'),({ids[0]},1))
            self.assertEqual(self.listing(archive='active',date_from=self.date),(set(ids),3))
            self.assertEqual(self.listing(archive='archived',state='cancelled'),({ids[0]},1))
        with patch('app.main.now',return_value=stamp+timedelta(hours=1)):
            self.assertEqual(self.listing(archive='archived'),({ids[0],ids[1]},2))
        self.assertEqual(self.row(ids[0])['status'],'cancelled')

    def test_archive_does_not_bypass_company_or_deletion(self):
        rid=self.request('10',submit=False).json()['id']
        with unit(True) as s:
            r=s.get(PaymentRequest,rid);r.status='approved';r.approved_at=now()-timedelta(days=2)
            other=s.scalar(select(Company.id).where(Company.id!=self.company,Company.code!='UNASSIGNED'))
        response=self.client.get('/api/requests?archive=archived',headers={**self.h,'X-Company-ID':str(other)})
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn(rid,[x['id'] for x in response.json()])
        with unit(True) as s:s.get(PaymentRequest,rid).status='deleted'
        self.assertEqual(self.listing(archive='archived'),(set(),0))
        self.assertEqual(self.listing(date_from=self.date),(set(),0))

def load_tests(loader, tests, pattern):
    return unittest.TestSuite(RequestAutoArchiveTests(name) for name in RequestAutoArchiveTests.__dict__ if name.startswith('test_'))

def tearDownModule():base.tearDownModule()

