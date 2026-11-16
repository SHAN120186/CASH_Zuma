"""Company expansion must not copy money or widen employee access."""
import json
import unittest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session
import tests.test_site as base
from app.db import Base, Category, Company, CompanyUser, Document, Setting, initialize, unit
from app.company_expansion import migrate_company_expansion


class ExpansionTests(base.SiteTests):
    def test_four_workspaces_and_employee_boundary(self):
        ids = self.company_ids()
        codes = {'UZGERMED', 'ZUMA', 'SAMO', 'FAZO'}
        self.assertEqual({c['code'] for c in self.client.get('/api/companies').json()['companies']
                          if c['code'] != 'UNASSIGNED'}, codes)
        with unit() as s:
            before = [(c.id, c.company_id, c.name) for c in s.scalars(select(Category).order_by(Category.id))]
            assignments = [(c.company_id, c.user_id, c.role) for c in s.scalars(select(CompanyUser).order_by(CompanyUser.company_id, CompanyUser.user_id))]
        initialize()
        initialize()
        with unit() as s:
            self.assertEqual(before, [(c.id, c.company_id, c.name) for c in s.scalars(select(Category).order_by(Category.id))])
            self.assertEqual(assignments, [(c.company_id, c.user_id, c.role) for c in s.scalars(select(CompanyUser).order_by(CompanyUser.company_id, CompanyUser.user_id))])
        original = self.client.get('/api/bootstrap', headers={**self.h, 'X-Company-ID': str(ids['ZUMA'])}).json()
        for code in ('SAMO', 'FAZO'):
            response = self.client.get('/api/bootstrap', headers={**self.h, 'X-Company-ID': str(ids[code])})
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            self.assertEqual(data['accounts'], [])
            self.assertEqual([c['name'] for c in data['categories']], [c['name'] for c in original['categories']])
            self.assertTrue({c['id'] for c in data['categories']}.isdisjoint(c['id'] for c in original['categories']))
        cl, h = self.user_in(ids['SAMO'], 'employee', 'samo_employee')
        try:
            self.assertEqual([c['code'] for c in cl.get('/api/companies').json()['companies']], ['SAMO'])
            self.assertEqual(cl.get('/api/bootstrap', headers={**h, 'X-Company-ID': str(ids['FAZO'])}).status_code, 403)
        finally:
            cl.close()

    def test_metadata_defers_binary_but_download_keeps_bytes(self):
        # Query shape is independent of file size: no attachment bytes for a list.
        sql = str(select(Document).compile())
        self.assertNotIn('documents.content', sql)
        entry = self.ledger('100', reference='FILE-EXPANSION').json()['id']
        uploaded = self.client.post(f'/api/ledger/{entry}/document', content=base.PDF,
                                   headers={**self.h, 'X-Filename': 'proof.pdf'})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        did = uploaded.json()['id']
        with unit() as s:
            doc = s.get(Document, did)
            self.assertIn('content', inspect(doc).unloaded)
        listed = self.client.get('/api/documents', params={'ledger_id': entry}, headers=self.h)
        self.assertEqual(listed.status_code, 200, listed.text)
        downloaded = self.client.get(f'/api/documents/{did}', headers=self.h)
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertEqual(downloaded.content, base.PDF)


class MigrationTests(unittest.TestCase):
    def test_existing_workspace_preserved_and_catalog_ids_remapped(self):
        engine = create_engine('sqlite://')
        try:
            Base.metadata.create_all(engine)
            with Session(engine) as s, s.begin():
                zuma = Company(code='ZUMA', name='Zuma', active=True)
                samo = Company(code='SAMO', name='Existing SAMO', active=False)
                s.add_all([zuma, samo]); s.flush()
                category = Category(company_id=zuma.id, name='Synthetic tax', code='43', activity='operating',
                                    type='outcome', director_policy_uzs='threshold', director_threshold_uzs=123400)
                s.add(category); s.flush()
                source_id = category.id
                s.add(Setting(key=f'company:{zuma.id}:category_catalog', value=json.dumps({'order':[source_id], 'retired':[source_id]})))
                s.add(Setting(key=f'company:{zuma.id}:departments', value='{"version":2,"items":[]}'))
            migrate_company_expansion(engine)
            migrate_company_expansion(engine)
            with Session(engine) as s:
                self.assertEqual(s.scalar(select(Company.name).where(Company.code=='SAMO')), 'Existing SAMO')
                self.assertFalse(s.scalar(select(Company.active).where(Company.code=='SAMO')))
                fazo = s.scalar(select(Company).where(Company.code=='FAZO'))
                copied = s.scalar(select(Category).where(Category.company_id==fazo.id))
                self.assertNotEqual(copied.id, source_id)
                self.assertEqual(copied.director_threshold_uzs, 123400)
                config = json.loads(s.get(Setting, f'company:{fazo.id}:category_catalog').value)
                self.assertEqual(config, {'order':[copied.id], 'retired':[copied.id]})
                self.assertEqual(s.get(Setting, f'company:{fazo.id}:departments').value, '{"version":2,"items":[]}')
                self.assertEqual(list(s.scalars(select(CompanyUser))), [])
                self.assertEqual(len(list(s.scalars(select(Category)))), 2)
        finally:
            engine.dispose()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([ExpansionTests('test_four_workspaces_and_employee_boundary'),
                              ExpansionTests('test_metadata_defers_binary_but_download_keeps_bytes'),
                              MigrationTests('test_existing_workspace_preserved_and_catalog_ids_remapped')])


def tearDownModule():
    base.tearDownModule()
