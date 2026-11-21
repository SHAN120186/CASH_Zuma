"""Company expansion must not copy money or widen employee access."""
import json
import unittest
from sqlalchemy import create_engine, inspect, or_, select
from sqlalchemy.orm import Session
import tests.test_site as base
from app.db import Account, Base, Category, Company, CompanyUser, Document, Role, Setting, User, initialize, unit
from app.company_expansion import MARKER, UZYARN_MARKER, migrate_company_expansion


class ExpansionTests(base.SiteTests):
    def test_five_workspaces_and_employee_boundary(self):
        ids = self.company_ids()
        codes = {'UZGERMED', 'ZUMA', 'SAMO', 'FAZO', 'UZYARN'}
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
        for code in ('SAMO', 'FAZO', 'UZYARN'):
            response = self.client.get('/api/bootstrap', headers={**self.h, 'X-Company-ID': str(ids[code])})
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            self.assertEqual(data['accounts'], [])
            self.assertEqual([c['name'] for c in data['categories']], [c['name'] for c in original['categories']])
            self.assertTrue({c['id'] for c in data['categories']}.isdisjoint(c['id'] for c in original['categories']))
        self.assertEqual(next(c['name'] for c in self.client.get('/api/companies').json()['companies']
                              if c['code']=='UZYARN'), 'Uzyarn')
        cl, h = self.user_in(ids['SAMO'], 'employee', 'samo_employee')
        try:
            self.assertEqual([c['code'] for c in cl.get('/api/companies').json()['companies']], ['SAMO'])
            self.assertEqual(cl.get('/api/bootstrap', headers={**h, 'X-Company-ID': str(ids['FAZO'])}).status_code, 403)
            self.assertEqual(cl.get('/api/bootstrap', headers={**h, 'X-Company-ID': str(ids['UZYARN'])}).status_code, 403)
        finally:
            cl.close()
        cl, h = self.user_in(ids['UZYARN'], 'employee', 'uzyarn_employee')
        try:
            self.assertEqual([c['code'] for c in cl.get('/api/companies').json()['companies']], ['UZYARN'])
            self.assertEqual(cl.get('/api/bootstrap', headers={**h, 'X-Company-ID': str(ids['UZYARN'])}).status_code, 200)
            self.assertEqual(cl.get('/api/bootstrap', headers={**h, 'X-Company-ID': str(ids['ZUMA'])}).status_code, 403)
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
                self.assertEqual(len(list(s.scalars(select(Category)))), 3)
                self.assertEqual(s.scalar(select(Company.name).where(Company.code=='UZYARN')), 'Uzyarn')
        finally:
            engine.dispose()

    @staticmethod
    def snapshot(s, company_ids):
        """Compare persisted legacy rows, including money and credentials, verbatim."""
        result = {}
        for model in (Company, Category, Account, User, CompanyUser, Setting):
            table = model.__table__
            query = select(table).order_by(*table.primary_key.columns)
            if model is Company:
                query = query.where(Company.id.in_(company_ids))
            elif 'company_id' in table.c:
                query = query.where(table.c.company_id.in_(company_ids))
            elif model is Setting:
                query = query.where(Setting.key != UZYARN_MARKER,
                                    or_(~Setting.key.like('company:%'), Setting.key.in_(
                        [f'company:{cid}:{key}' for cid in company_ids
                         for key in ('category_catalog', 'departments', 'reserve_UZS')])))
            result[table.name] = [tuple(row) for row in s.execute(query)]
        return result

    def test_uzyarn_added_after_prior_expansion_without_changing_legacy_rows(self):
        from datetime import date
        engine = create_engine('sqlite://')
        try:
            Base.metadata.create_all(engine)
            with Session(engine) as s, s.begin():
                companies = [Company(code=code, name=f'Existing {code}', active=code!='SAMO')
                             for code in ('ZUMA', 'UZGERMED', 'SAMO', 'FAZO')]
                s.add_all(companies + [Role(name='finance')]); s.flush()
                ids = [c.id for c in companies]
                zuma = companies[0]
                user = User(username='existing.finance', name='Existing finance', role='finance',
                            password_hash=base.HASH, must_change_password=False)
                s.add(user); s.flush()
                s.add(CompanyUser(company_id=zuma.id, user_id=user.id, role='finance'))
                s.add(Account(company_id=zuma.id, name='Existing funded bank', kind='bank',
                              currency='UZS', opening=123456789, opening_date=date(2026, 10, 1),
                              created_by=user.id))
                category = Category(company_id=zuma.id, name='Existing expense', code='43',
                                    activity='operating', type='outcome', director_policy_uzs='threshold',
                                    director_threshold_uzs=123400, director_policy_changed_by=user.id)
                s.add(category); s.flush()
                source_id = category.id
                s.add_all([Setting(key=MARKER, value='legacy-complete'),
                           Setting(key=f'company:{zuma.id}:category_catalog', value=json.dumps(
                               {'order':[source_id], 'retired':[source_id]})),
                           Setting(key=f'company:{zuma.id}:departments', value='{"version":3,"items":[]}'),
                           Setting(key=f'company:{zuma.id}:reserve_UZS', value='9876500')])
            with Session(engine) as s:
                before = self.snapshot(s, ids)
            migrate_company_expansion(engine)
            with Session(engine) as s:
                self.assertEqual(self.snapshot(s, ids), before)
                self.assertEqual(s.get(Setting, MARKER).value, 'legacy-complete')
                self.assertIsNotNone(s.get(Setting, UZYARN_MARKER))
                added = s.scalar(select(Company).where(Company.code=='UZYARN'))
                self.assertEqual((added.name, added.active), ('Uzyarn', True))
                self.assertEqual(list(s.scalars(select(Account).where(Account.company_id==added.id))), [])
                self.assertEqual(list(s.scalars(select(CompanyUser).where(CompanyUser.company_id==added.id))), [])
                copied = s.scalar(select(Category).where(Category.company_id==added.id))
                self.assertNotEqual(copied.id, source_id)
                self.assertEqual((copied.name, copied.code, copied.director_threshold_uzs),
                                 ('Existing expense', '43', 123400))
                self.assertIsNone(copied.director_policy_changed_by)
                self.assertEqual(json.loads(s.get(Setting, f'company:{added.id}:category_catalog').value),
                                 {'order':[copied.id], 'retired':[copied.id]})
                self.assertEqual(s.get(Setting, f'company:{added.id}:departments').value,
                                 '{"version":3,"items":[]}')
                self.assertIsNone(s.get(Setting, f'company:{added.id}:reserve_UZS'))
                all_ids = ids + [added.id]
                after = self.snapshot(s, all_ids)
            migrate_company_expansion(engine)
            with Session(engine) as s:
                self.assertEqual(self.snapshot(s, all_ids), after)
                self.assertEqual(len(list(s.scalars(select(Company)))), 5)
        finally:
            engine.dispose()

    def test_existing_uzyarn_is_preserved_including_disabled_state_and_assignments(self):
        from datetime import date
        engine = create_engine('sqlite://')
        try:
            Base.metadata.create_all(engine)
            with Session(engine) as s, s.begin():
                zuma = Company(code='ZUMA', name='Existing Zuma', active=True)
                uzyarn = Company(code='UZYARN', name='Owner-managed Uzyarn', active=False)
                s.add_all([zuma, uzyarn, Role(name='cashier')]); s.flush()
                ids = [zuma.id, uzyarn.id]
                user = User(username='uzyarn.original', name='Original user', role='cashier',
                            password_hash=base.HASH, must_change_password=False)
                s.add(user); s.flush()
                s.add_all([CompanyUser(company_id=uzyarn.id, user_id=user.id, role='cashier'),
                           Account(company_id=uzyarn.id, name='Existing own cash', kind='cash',
                                   currency='UZS', opening=87654321, opening_date=date(2026, 9, 1),
                                   created_by=user.id),
                           Category(company_id=zuma.id, name='Do not overwrite Uzyarn with this',
                                    activity='operating', type='outcome'),
                           Category(company_id=uzyarn.id, name='Private Uzyarn category',
                                    activity='financing', type='income'),
                           Setting(key=MARKER, value='1'),
                           Setting(key=f'company:{uzyarn.id}:departments', value='{"version":8,"items":[]}'),
                           Setting(key=f'company:{uzyarn.id}:reserve_UZS', value='432100')])
            with Session(engine) as s:
                before = self.snapshot(s, ids)
            migrate_company_expansion(engine)
            migrate_company_expansion(engine)
            with Session(engine) as s:
                self.assertEqual(self.snapshot(s, ids), before)
                self.assertIsNotNone(s.get(Setting, UZYARN_MARKER))
                self.assertEqual(len(list(s.scalars(select(Company)))), 2)
        finally:
            engine.dispose()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([ExpansionTests('test_five_workspaces_and_employee_boundary'),
                              ExpansionTests('test_metadata_defers_binary_but_download_keeps_bytes'),
                              MigrationTests('test_existing_workspace_preserved_and_catalog_ids_remapped'),
                              MigrationTests('test_uzyarn_added_after_prior_expansion_without_changing_legacy_rows'),
                              MigrationTests('test_existing_uzyarn_is_preserved_including_disabled_state_and_assignments')])


def tearDownModule():
    base.tearDownModule()
