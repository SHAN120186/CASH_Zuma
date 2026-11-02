"""Request INN is optional, survives old clients and never silently follows a changed name."""
import unittest
from sqlalchemy import inspect, text
import tests.test_site as base
from sqlalchemy import select
from app.db import Counterparty, engine, initialize, unit


class RequestInnTests(base.SiteTests):
    def test_new_and_legacy_clients(self):
        body=self.request_body(counterparty='Supplier A')
        body['counterparty_inn']='009876543'
        created=self.post('/api/requests',body)
        self.assertEqual(created.status_code,200,created.text)
        rid=created.json()['id'];self.assertEqual(created.json()['counterparty_inn'],'009876543')
        self.assertEqual(self.row(rid)['counterparty_inn'],'009876543')
        legacy=self.request_body(counterparty='Supplier A')
        legacy.update(version=self.version_of(rid),reason='Исправление назначения')
        edited=self.client.put(f'/api/requests/{rid}',json=legacy,headers=self.h)
        self.assertEqual(edited.status_code,200,edited.text);self.assertEqual(edited.json()['counterparty_inn'],'009876543')
        legacy.update(counterparty='Supplier B',version=edited.json()['version'])
        edited=self.client.put(f'/api/requests/{rid}',json=legacy,headers=self.h)
        self.assertEqual(edited.status_code,200,edited.text);self.assertEqual(edited.json()['counterparty_inn'],'')
        legacy.update(version=edited.json()['version'],counterparty_inn='123456789')
        edited=self.client.put(f'/api/requests/{rid}',json=legacy,headers=self.h)
        self.assertEqual(edited.status_code,200,edited.text);self.assertEqual(edited.json()['counterparty_inn'],'123456789')
        legacy.update(version=edited.json()['version'],counterparty_inn='bad')
        self.assertEqual(self.client.put(f'/api/requests/{rid}',json=legacy,headers=self.h).status_code,422)

    def test_migration_keeps_legacy_requests(self):
        created=self.post('/api/requests',self.request_body(counterparty='Legacy supplier'))
        self.assertEqual(created.status_code,200,created.text);rid=created.json()['id']
        with engine.begin() as conn:
            conn.execute(text('ALTER TABLE payment_requests DROP COLUMN counterparty_inn'))
        initialize()
        self.assertIn('counterparty_inn',{c['name'] for c in inspect(engine).get_columns('payment_requests')})
        row=self.row(rid);self.assertEqual(row['counterparty'],'Legacy supplier');self.assertEqual(row['counterparty_inn'],'')

    def test_remember_does_not_duplicate_an_existing_tax_id(self):
        with unit(True) as s:s.add(Counterparty(company_id=self.company,name='Known supplier',inn='009876543'))
        body=self.request_body(counterparty='Alternative spelling')
        body['counterparty_inn']='009876543'
        result=self.post('/api/requests',body)
        self.assertEqual(result.status_code,200,result.text)
        with unit() as s:
            rows=list(s.scalars(select(Counterparty).where(Counterparty.company_id==self.company,Counterparty.inn=='009876543')))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0].name,'Known supplier')


def load_tests(loader,tests,pattern):
    return unittest.TestSuite([RequestInnTests('test_new_and_legacy_clients'),RequestInnTests('test_migration_keeps_legacy_requests'),
                               RequestInnTests('test_remember_does_not_duplicate_an_existing_tax_id')])


def tearDownModule():
    base.tearDownModule()
