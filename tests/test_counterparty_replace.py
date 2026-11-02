"""Tests for guarded directory replacement; synthetic data only."""
import unittest
from sqlalchemy import select
from fastapi.testclient import TestClient
import tests.test_site as base
from app.db import Company, Counterparty, User, unit


class CounterpartyReplaceTests(base.SiteTests):
    def setUp(self):
        super().setUp()
        with unit(True) as s:
            s.add(User(username='holding.admin', name='Holding Admin', role='admin', password_hash=base.HASH))
            self.zuma = s.scalar(select(Company.id).where(Company.code == 'ZUMA'))
            s.add_all([Counterparty(company_id=self.company, name='Old UZ', inn='111111111', note='old'),
                       Counterparty(company_id=self.zuma, name='Old Z', inn='222222222', note='old')])
        self.holding = TestClient(base.app)
        login = self.holding.post('/api/login', json={'username': 'holding.admin', 'password': base.PASSWORD})
        self.assertEqual(login.status_code, 200, login.text)
        self.headers = {'X-CSRF-Token': login.json()['csrf'], 'X-Company-ID': str(self.company)}

    def tearDown(self):
        self.holding.close()
        super().tearDown()

    def test_guard_isolation_and_related_requests(self):
        request = self.request(submit=False, counterparty='Preserved supplier')
        self.assertEqual(request.status_code, 200, request.text)
        request_id = request.json()['id']
        saved = self.holding.get('/api/counterparties/replacement-snapshot', headers=self.headers)
        self.assertEqual(saved.status_code, 200, saved.text)
        body = {'expected_sha256': saved.json()['sha256'], 'rows': [
            {'name': 'Same name', 'inn': '123456789', 'note': ''},
            {'name': 'Same name', 'inn': '987654321', 'note': ''}]}
        done = self.holding.post('/api/counterparties/replace', json=body, headers=self.headers)
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual((done.json()['removed'], done.json()['added']), (2, 2))
        listing = self.holding.get('/api/counterparties', headers=self.headers).json()
        self.assertEqual({row['inn'] for row in listing}, {'123456789', '987654321'})
        other = self.holding.get('/api/counterparties', headers={**self.headers, 'X-Company-ID': str(self.zuma)}).json()
        self.assertEqual([(x['name'], x['inn']) for x in other], [('Old Z', '222222222')])
        after = self.row(request_id)
        self.assertEqual(after['counterparty'], 'Preserved supplier')
        edit = {k: after[k] for k in ('company_id','account_id','channel','currency','category_id','counterparty','amount','date','purpose','priority','version')}
        edit.update(status='draft',reason='Correct a historical test request')
        changed=self.client.put(f'/api/requests/{request_id}',json=edit,headers=self.h)
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual({row['inn'] for row in self.holding.get('/api/counterparties',headers=self.headers).json()},
                         {'123456789','987654321'},'editing an old request must not re-add a retired name')
        again = self.holding.post('/api/counterparties/replace', json=body, headers=self.headers)
        self.assertEqual(again.status_code, 409)
        self.assertEqual(self.holding.get('/api/counterparties', headers=self.headers).json(), listing)

    def test_authentication_validation_and_stale_snapshot(self):
        snapshot = self.holding.get('/api/counterparties/replacement-snapshot', headers=self.headers).json()['sha256']
        body = {'expected_sha256': snapshot, 'rows': [{'name': 'New name', 'inn': '123456789', 'note': ''}]}
        self.assertEqual(self.client.post('/api/counterparties/replace', json=body, headers=self.h).status_code, 403)
        self.assertEqual(self.holding.post('/api/counterparties/replace', json={'rows': body['rows']}, headers=self.headers).status_code, 422)
        duplicate = {'expected_sha256': snapshot, 'rows': [body['rows'][0], {'name': 'Other', 'inn': '123456789'}]}
        self.assertEqual(self.holding.post('/api/counterparties/replace', json=duplicate, headers=self.headers).status_code, 422)
        invalid = {'expected_sha256': snapshot, 'rows': [{'name': 'New', 'inn': '12x'}]}
        self.assertEqual(self.holding.post('/api/counterparties/replace', json=invalid, headers=self.headers).status_code, 422)
        self.assertEqual(self.holding.post('/api/counterparties', json={'name': 'Concurrent change'}, headers=self.headers).status_code, 200)
        self.assertEqual(self.holding.post('/api/counterparties/replace', json=body, headers=self.headers).status_code, 409)
        self.assertEqual(len(self.holding.get('/api/counterparties', headers=self.headers).json()), 2)

    def test_realistic_directory_fits_http_body_limit(self):
        snap=self.holding.get('/api/counterparties/replacement-snapshot',headers=self.headers).json()['sha256']
        rows=[{'name':f'Synthetic large supplier {i:04d}', 'inn':f'{i:09d}'} for i in range(1800)]
        result=self.holding.post('/api/counterparties/replace',json={'expected_sha256':snap,'rows':rows},headers=self.headers)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['added'],1800)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([CounterpartyReplaceTests('test_guard_isolation_and_related_requests'),
                               CounterpartyReplaceTests('test_authentication_validation_and_stale_snapshot'),
                               CounterpartyReplaceTests('test_realistic_directory_fits_http_body_limit')])


def tearDownModule():
    base.tearDownModule()
