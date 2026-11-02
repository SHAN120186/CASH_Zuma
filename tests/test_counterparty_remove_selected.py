"""Exact, company-scoped deletion preserves every other directory row and history."""
import json
import unittest
from fastapi.testclient import TestClient
from sqlalchemy import select
import tests.test_site as base
from app.db import Audit, Company, Counterparty, User, unit


class RemoveSelectedTests(base.SiteTests):
    def setUp(self):
        super().setUp()
        with unit(True) as s:
            s.add(User(username='holding.admin', name='Holding Admin', role='admin', password_hash=base.HASH))
            self.zuma = s.scalar(select(Company.id).where(Company.code == 'ZUMA'))
            a=Counterparty(company_id=self.company,name='Same supplier',inn='123456789',note='Selected')
            b=Counterparty(company_id=self.company,name='Same supplier',inn='987654321',note='Keep')
            foreign=Counterparty(company_id=self.zuma,name='Same supplier',inn='123456789',note='Foreign')
            s.add_all([a,b,foreign]);s.flush()
            self.selected={'id':a.id,'name':a.name,'inn':a.inn}
            self.other={'id':b.id,'name':b.name,'inn':b.inn}
            self.foreign={'id':foreign.id,'name':foreign.name,'inn':foreign.inn}
        self.holding=TestClient(base.app)
        login=self.holding.post('/api/login',json={'username':'holding.admin','password':base.PASSWORD})
        self.assertEqual(login.status_code,200,login.text)
        self.headers={'X-CSRF-Token':login.json()['csrf'],'X-Company-ID':str(self.company)}

    def tearDown(self):
        self.holding.close()
        super().tearDown()

    def test_only_selected_rows_removed_and_history_retained(self):
        body=self.request_body(counterparty='Same supplier');body['counterparty_inn']=self.selected['inn']
        request=self.post('/api/requests',body);self.assertEqual(request.status_code,200,request.text)
        rid=request.json()['id']
        result=self.holding.post('/api/counterparties/remove-selected',json={'rows':[self.selected]},headers=self.headers)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json(),{'company_id':self.company,'removed_ids':[self.selected['id']],'removed':1})
        rows=self.holding.get('/api/counterparties',headers=self.headers).json()
        self.assertEqual([(c['id'],c['inn']) for c in rows],[(self.other['id'],self.other['inn'])])
        zrows=self.holding.get('/api/counterparties',headers={**self.headers,'X-Company-ID':str(self.zuma)}).json()
        self.assertEqual([(c['id'],c['inn']) for c in zrows],[(self.foreign['id'],self.foreign['inn'])])
        historical=self.row(rid)
        self.assertEqual((historical['counterparty'],historical['counterparty_inn']),('Same supplier','123456789'))
        with unit() as s:
            audit=s.scalar(select(Audit).where(Audit.action=='Удалены контрагенты').order_by(Audit.id.desc()))
            self.assertEqual(audit.company_id,self.company)
            self.assertEqual(json.loads(audit.detail)['removed'],[{**self.selected,'note':'Selected'}])

    def test_mismatches_cross_company_and_duplicates_leave_all_untouched(self):
        url='/api/counterparties/remove-selected'
        before=self.holding.get('/api/counterparties',headers=self.headers).json()
        for rows in ([self.selected,{**self.other,'inn':'000000000'}], [self.selected,self.foreign],
                     [self.selected,{**self.other,'name':'Wrong'}]):
            self.assertEqual(self.holding.post(url,json={'rows':rows},headers=self.headers).status_code,409)
            self.assertEqual(self.holding.get('/api/counterparties',headers=self.headers).json(),before)
        self.assertEqual(self.holding.post(url,json={'rows':[self.selected,self.selected]},headers=self.headers).status_code,422)
        self.assertEqual(self.holding.post(url,json={'rows':[self.selected]},headers={'X-Company-ID':str(self.company)}).status_code,403)
        self.assertEqual(self.client.post(url,json={'rows':[self.selected]},headers=self.h).status_code,403)
        self.assertEqual(self.holding.get('/api/counterparties',headers=self.headers).json(),before)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([RemoveSelectedTests('test_only_selected_rows_removed_and_history_retained'),
                               RemoveSelectedTests('test_mismatches_cross_company_and_duplicates_leave_all_untouched')])


def tearDownModule():
    base.tearDownModule()
