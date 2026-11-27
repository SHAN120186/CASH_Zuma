"""Archive reads aggregate closure history once and retain the display boundary."""
import unittest
from datetime import timedelta
from unittest.mock import patch
from sqlalchemy import event, select
import tests.test_site as base
from app.db import Audit, Company, PaymentRequest, engine, now, unit


class RequestArchivePerformanceTests(base.SiteTests):
    def listing(self, **params):
        response=self.client.get('/api/requests',params={'paginated':'true','page_size':100,**params},headers=self.h)
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        return {row['id'] for row in data['items']},data['total']

    def test_latest_matching_action_cutoff_missing_history_and_company_scope(self):
        ids=[self.request('10',submit=False).json()['id'] for _ in range(7)]
        stamp=now()
        with unit(True) as s:
            other=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
            for rid,status in zip(ids,('cancelled','cancelled','rejected','rejected','cancelled','paid','draft')):
                s.get(PaymentRequest,rid).status=status
            events=[
                # The other status's action must not change this status's clock.
                (ids[0],'cancel',24,self.company),(ids[0],'reject',1,self.company),
                (ids[1],'cancel',48,self.company),(ids[1],'close',23,self.company),
                (ids[2],'reject',24,self.company),(ids[2],'close',1,self.company),
                (ids[3],'cancel',48,self.company),
                # Historical corruption in another company must not close this row.
                (ids[4],'cancel',48,other),
            ]
            for rid,action,hours,company in events:
                s.add(Audit(company_id=company,entity='request',entity_id=str(rid),
                    action='Действие по заявке: '+action,created_at=stamp-timedelta(hours=hours)))
        with patch('app.main.now',return_value=stamp):
            self.assertEqual(self.listing(archive='active'),({ids[1],ids[3],ids[4],ids[6]},4))
            self.assertEqual(self.listing(archive='archived'),({ids[0],ids[2],ids[5]},3))
            self.assertEqual(self.listing(archive='all'),(set(ids),7))
            self.assertEqual(self.listing(archive='all',date_from=self.date),(set(ids),7))
            self.assertEqual(self.listing(archive='active',date_from=self.date),({ids[1],ids[3],ids[4],ids[6]},4))
            self.assertEqual(self.listing(archive='archived',date_to=self.date),({ids[0],ids[2],ids[5]},3))
        with patch('app.main.now',return_value=stamp+timedelta(hours=1)):
            self.assertEqual(self.listing(archive='archived'),({ids[0],ids[1],ids[2],ids[5]},4))

    def test_archive_page_has_no_correlated_audit_scan_or_read_writes(self):
        ids=[self.request('10',submit=False).json()['id'] for _ in range(16)]
        stamp=now()
        with unit(True) as s:
            for rid in ids:
                s.get(PaymentRequest,rid).status='cancelled'
                s.add(Audit(company_id=self.company,entity='request',entity_id=str(rid),
                    action='Действие по заявке: close',created_at=stamp-timedelta(hours=48)))
            for _ in range(1000):
                s.add(Audit(company_id=self.company,entity='synthetic',entity_id='0',action='Synthetic unrelated history'))
        captured=[]
        def record(conn,cursor,statement,parameters,context,many):
            captured.append((statement,parameters))
        event.listen(engine,'before_cursor_execute',record)
        try:
            with patch('app.main.now',return_value=stamp):
                response=self.client.get('/api/requests?archive=archived&paginated=true&page_size=15',headers=self.h)
        finally:
            event.remove(engine,'before_cursor_execute',record)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['total'],16)
        self.assertEqual(len(response.json()['items']),15)
        self.assertFalse(any(sql.lstrip().lower().startswith(('insert','update','delete')) for sql,_ in captured))
        selects=[(sql,params) for sql,params in captured if sql.lstrip().lower().startswith('select')]
        self.assertLessEqual(len(selects),5)
        page_sql,page_params=next((sql,params) for sql,params in selects if 'count(*) over' in sql.lower())
        if engine.dialect.name=='sqlite':
            with engine.connect() as conn:
                plan=[row[-1] for row in conn.exec_driver_sql('EXPLAIN QUERY PLAN '+page_sql,page_params)]
            # The closure lookup used to be a correlated audit scan per row.
            self.assertFalse(any('CORRELATED SCALAR SUBQUERY' in item for item in plan),plan)
            self.assertEqual(sum('SCAN audit_log' in item for item in plan),1,plan)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(RequestArchivePerformanceTests(name)
        for name in RequestArchivePerformanceTests.__dict__ if name.startswith('test_'))


def tearDownModule():base.tearDownModule()
