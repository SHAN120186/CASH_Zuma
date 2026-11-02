"""Company isolation, request access and prefix search for counterparty lookup."""
import unittest
from sqlalchemy import select
import tests.test_site as base
from app.db import Company, Counterparty, Setting, unit
import json


class CounterpartyLookupTests(base.SiteTests):
    def test_lookup_rights_scope_limits_and_short_identifier(self):
        with unit(True) as s:
            other=s.scalar(select(Company).where(Company.code=='ZUMA')).id
            s.add_all([Counterparty(company_id=self.company,name='Shared name',inn='123456789'),
                       Counterparty(company_id=self.company,name='Short identifier',inn='3'),
                       Counterparty(company_id=other,name='Foreign',inn='123456789')])
            s.add_all([Counterparty(company_id=self.company,name=f'Example {i}',inn=f'555{i:06d}') for i in range(25)])
        worker,headers=self.make_user('employee','lookup-worker')
        self.addCleanup(worker.close)
        r=worker.get('/api/request-counterparties?inn=123456789',headers=headers)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual([x['name'] for x in r.json()['items']],['Shared name'])
        self.assertEqual(worker.get('/api/request-counterparties?inn=3',headers=headers).json()['items'][0]['name'],'Short identifier')
        self.assertEqual(len(worker.get('/api/request-counterparties?inn=555',headers=headers).json()['items']),20)
        self.assertEqual(worker.get('/api/request-counterparties?inn=1a',headers=headers).status_code,422)
        self.assertEqual(worker.get('/api/request-counterparties?inn=123',headers={**headers,'X-Company-ID':str(other)}).status_code,403)
        with unit(True) as s:
            from app.db import User
            u=s.scalar(select(User).where(User.username=='lookup-worker'))
            s.add(Setting(key=f'sections:{self.company}:{u.id}',value=json.dumps({'version':1,'sections':{'requests':'hidden'}})))
        self.assertEqual(worker.get('/api/request-counterparties?inn=123',headers=headers).status_code,403)

    def test_receipt_name_lookup_checks_the_full_directory_and_company(self):
        with unit(True) as s:
            other=s.scalar(select(Company).where(Company.code=='ZUMA')).id
            s.add_all([Counterparty(company_id=self.company,name='Same supplier',inn='111111111'),
                       Counterparty(company_id=self.company,name='Same supplier',inn='222222222'),
                       Counterparty(company_id=other,name='Same supplier',inn='333333333')])
        buyer,headers=self.make_user('procurement','receipt-lookup')
        self.addCleanup(buyer.close)
        r=buyer.get('/api/receipt-counterparties',params={'name':'Same supplier'},headers=headers)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual({p['inn'] for p in r.json()['items']},{'111111111','222222222'})
        self.assertEqual(buyer.get('/api/receipt-counterparties',params={'name':'Same supplier'},headers={**headers,'X-Company-ID':str(other)}).status_code,403)
        employee,h=self.make_user('employee','receipt-no-access')
        self.addCleanup(employee.close)
        self.assertEqual(employee.get('/api/receipt-counterparties',params={'name':'Same supplier'},headers=h).status_code,403)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite([CounterpartyLookupTests('test_lookup_rights_scope_limits_and_short_identifier'),
                               CounterpartyLookupTests('test_receipt_name_lookup_checks_the_full_directory_and_company')])


def tearDownModule():
    base.tearDownModule()
