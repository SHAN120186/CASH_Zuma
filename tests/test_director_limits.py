"""Порог участия директора вместо отменённого верхнего запрета; только изолированные данные."""
import unittest
from sqlalchemy import select, inspect, text
import tests.test_site as base
from app.db import Audit, Category, Company, Ledger, PaymentRequest, Setting, unit, engine, initialize
from app.director_thresholds import SOURCE_RULES, MIGRATION_KEY, migrate_director_thresholds
from app.services import category_policy


class DirectorThresholdTests(base.SiteTests):
    def policy(self, category, headers=None, client=None, **values):
        return (client or self.client).put(f'/api/categories/{category}/director-policy',
            headers=headers or self.h, json={**values, 'reason': 'Порог согласован владельцем компании'})

    def category(self, name, headers=None):
        result = self.post('/api/categories', {'name': name, 'type': 'outcome', 'activity': 'operating'}, headers=headers)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['id']

    def actors(self, company):
        result = {}
        for role in ('accountant', 'finance', 'director'):
            result[role] = self.user_in(company, role, f'threshold_{company}_{role}')
            self.addCleanup(result[role][0].close)
        return result

    def checked(self, value, category, account, headers, actors):
        result = self.request(value, category=category, account=account, headers=headers)
        self.assertEqual(result.status_code, 200, result.text)
        rid = result.json()['id']
        result = self.post(f'/api/requests/{rid}/decision', {'action': 'check'}, *actors['accountant'])
        self.assertEqual(result.status_code, 200, result.text)
        return rid

    def test_http_boundaries_in_both_companies_and_no_upper_ceiling(self):
        for code, company in self.company_ids().items():
            if code not in ('ZUMA', 'UZGERMED'):continue
            headers = {**self.h, 'X-Company-ID': str(company)}
            actors = self.actors(company)
            account = self.account('Threshold bank ' + code, headers=headers)
            category = self.category('17 · Закуп основных средств', headers)
            for value, needs in (('19999999.99', False), ('20000000', False),
                                 ('20000000.01', True), ('1000000000', True)):
                with self.subTest(company=code, value=value):
                    rid = self.checked(value, category, account, headers, actors)
                    before = self.row(rid, headers)
                    self.assertEqual(before['route']['threshold'], '20000000.00')
                    result = self.post(f'/api/requests/{rid}/decision',
                        {'action': 'approve', 'note': 'Synthetic independent finance review'}, *actors['finance'])
                    self.assertEqual(result.status_code, 200, result.text)
                    self.assertEqual(result.json()['status'], 'pending' if needs else 'approved')
                    if needs:
                        self.assertEqual(result.json()['approval_stage'], 'director')
                        result = self.post(f'/api/requests/{rid}/decision',
                            {'action': 'approve', 'note': 'Synthetic director approval above participation threshold'}, *actors['director'])
                        self.assertEqual(result.status_code, 200, result.text)
                        self.assertEqual(result.json()['status'], 'approved')
                    self.assertFalse(result.json()['director_limit']['exceeded'])
        with unit() as s:self.assertEqual(list(s.scalars(select(Ledger).execution_options(company_unscoped=True))), [])

    def test_zero_and_unlimited_finance_are_different_in_both_companies(self):
        for code, company in self.company_ids().items():
            if code not in ('ZUMA', 'UZGERMED'):continue
            headers = {**self.h, 'X-Company-ID': str(company)}
            actors = self.actors(company)
            account = self.account('Zero and unlimited ' + code, headers=headers)
            for name, value, needs in (('65 · Расчеты по предоставленным займам 4720', '0.01', True),
                                       ('59 · Доставка продукции', '1000000000', False),
                                       ('12 · Расходы на маркетинг (бонус)', '100000000', False),
                                       ('12 · Расходы на маркетинг (бонус)', '100000000.01', True)):
                with self.subTest(company=code, article=name, value=value):
                    existing = self.client.get('/api/bootstrap', headers=headers).json()['categories']
                    category = next((c['id'] for c in existing if c['name']==name), None) or self.category(name, headers)
                    # Existing fixture rows were deliberately reset to legacy always in base.setUp.
                    expected = None if 'Доставка' in name else ('100000000' if 'маркетинг' in name else '0')
                    if expected is None:
                        self.assertEqual(self.client.put(f'/api/categories/{category}/skip-allowed', headers=headers,
                            json={'allowed':True,'reason':'Владелец подтвердил статью без директора'}).status_code, 200)
                    self.assertEqual(self.policy(category, headers, policy_uzs='skip' if expected is None else 'threshold',
                                                 threshold_uzs=expected).status_code, 200)
                    rid = self.checked(value, category, account, headers, actors)
                    result = self.post(f'/api/requests/{rid}/decision',
                        {'action':'approve','note':'Synthetic finance stage without upper cap'}, *actors['finance'])
                    self.assertEqual(result.status_code, 200, result.text)
                    self.assertEqual(result.json()['status'], 'pending' if needs else 'approved')
                    if needs:
                        result = self.post(f'/api/requests/{rid}/decision',
                            {'action':'approve','note':'Synthetic director stage with no upper restriction'}, *actors['director'])
                        self.assertEqual(result.status_code, 200, result.text)

    def test_currency_override_partial_updates_zero_validation_and_legacy_api(self):
        self.assertEqual(self.set_policy(self.cat, 'threshold', uzs='5000000', usd='500').status_code, 200)
        result = self.policy(self.cat, policy_uzs='threshold', threshold_uzs='0')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['effective']['UZS'], {'policy':'threshold','threshold':'0.00'})
        self.assertEqual(result.json()['effective']['USD'], {'policy':'threshold','threshold':'500.00'})
        self.assertEqual(result.json()['effective']['EUR'], {'policy':'always','threshold':None})
        self.assertEqual(self.policy(self.cat, policy_uzs='skip').status_code, 409)
        for value in ('-1','NaN','Infinity','0.001'):
            self.assertEqual(self.policy(self.cat, policy_uzs='threshold', threshold_uzs=value).status_code, 422)
        self.assertEqual(self.policy(self.cat).status_code, 422)
        self.assertEqual(self.set_policy(self.cat, 'always').json()['policies'], {'UZS':None,'USD':None,'EUR':None})

    def test_migration_all_source_articles_and_no_code_only_match(self):
        expected = {}
        with unit(True) as s:
            s.add(Company(code='THIRD', name='Synthetic future company', active=False));s.flush()
            for co in s.scalars(select(Company).where(Company.code!='UNASSIGNED')):
                for code, title, threshold in SOURCE_RULES:
                    c=Category(company_id=co.id,code='source_'+code,name=f'{code} · {title}',type='outcome',
                        director_policy='threshold',director_threshold_usd=75000,director_limit_uzs=0)
                    s.add(c);s.flush();expected[c.id]=threshold
                c=Category(company_id=co.id,code='17',name='17 · Synthetic unrelated expense',type='outcome',
                           director_policy='always',director_limit_uzs=12300)
                s.add(c);s.flush();expected[c.id]='unmatched'
            s.delete(s.get(Setting,MIGRATION_KEY))
        migrate_director_thresholds(engine)
        with unit() as s:
            for c in s.scalars(select(Category).where(Category.id.in_(expected))):
                value=expected[c.id]
                self.assertIsNone(c.director_limit_uzs)
                if value=='unmatched':self.assertIsNone(c.director_policy_uzs)
                else:
                    self.assertEqual(category_policy(c,'UZS'),('skip',None) if value is None else ('threshold',value))
                    self.assertEqual(category_policy(c,'USD'),('threshold',75000))
                    self.assertEqual(category_policy(c,'EUR'),('always',None))
            self.assertTrue(s.scalar(select(Audit).where(Audit.action=='Настроен порог участия директора')))
        self.assertEqual(len(SOURCE_RULES),50)

    def test_migration_keeps_sent_route_financial_facts_and_manual_overrides_afterwards(self):
        pending=self.request('600').json()['id'];self.after_finance(pending)
        approved=self.request('600').json()['id'];self.approve(approved)
        before=[self.row(rid) for rid in (pending,approved)]
        with unit(True) as s:
            s.get(Category,self.cat).director_limit_uzs=0
            s.delete(s.get(Setting,MIGRATION_KEY))
        migrate_director_thresholds(engine)
        for rid,old in zip((pending,approved),before):
            fresh=self.row(rid)
            for key in ('amount','status','version','route','checked_by','finance_approved_by','approved_by'):
                self.assertEqual(fresh[key],old[key],key)
        self.assertEqual(self.policy(self.cat,policy_uzs='threshold',threshold_uzs='123').status_code,200)
        initialize()
        with unit() as s:self.assertEqual(category_policy(s.get(Category,self.cat),'UZS'),('threshold',12300))
        result=self.post(f'/api/requests/{pending}/decision',{'action':'approve','note':'Existing route remains approvable without cap'},*self.stage_user('director'))
        self.assertEqual(result.status_code,200,result.text)
        with unit() as s:self.assertEqual(list(s.scalars(select(Ledger))),[])

    def test_retired_upper_cap_endpoint_cannot_restore_a_ceiling(self):
        result=self.client.put(f'/api/categories/{self.cat}/director-limits',headers=self.h,
            json={'limit_uzs':'0','reason':'Synthetic stale client attempts to reintroduce cap'})
        self.assertEqual(result.status_code,409,result.text)
        self.assertIn('больше не используется',result.json()['detail'])
        with unit(True) as s:s.get(Category,self.cat).director_limit_uzs=0
        rid=self.request('1000000000').json()['id']
        self.assertEqual(self.approve(rid).status_code,200)

    def test_existing_database_adds_currency_policy_columns(self):
        rid=self.request('600').json()['id'];before=self.row(rid)
        with engine.begin() as conn:
            for cur in ('uzs','usd','eur'):conn.execute(text(f'ALTER TABLE categories DROP COLUMN director_policy_{cur}'))
        initialize()
        fields={c['name'] for c in inspect(engine).get_columns('categories')}
        self.assertTrue({'director_policy_uzs','director_policy_usd','director_policy_eur'}<=fields)
        after=self.row(rid)
        for key in ('amount','status','version','route'):self.assertEqual(after[key],before[key])

    def test_cash_funding_fee_can_strengthen_participation_without_an_upper_cap(self):
        cash=self.account('Synthetic cash threshold',kind='cash',opening='0')
        self.assertEqual(self.policy(self.cat,policy_uzs='threshold',threshold_uzs='995000').status_code,200)
        draft=self.post('/api/requests',{**self.request_body('990000',account=cash),'cash_funding':{
            'bank_account_id':self.acc,'gross_amount':'1000000','fee_percent':'1','fee_category_id':self.cat}})
        self.assertEqual(draft.status_code,200,draft.text)
        rid=draft.json()['id'];self.upload(rid)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'submit'}).status_code,200)
        row=self.after_finance(rid)
        self.assertEqual(row['approval_stage'],'director')
        result=self.post(f'/api/requests/{rid}/decision',{'action':'approve','note':'Gross same-article amount exceeds participation threshold'},*self.stage_user('director'))
        self.assertEqual(result.status_code,200,result.text)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(DirectorThresholdTests(name) for name in DirectorThresholdTests.__dict__ if name.startswith('test_'))


def tearDownModule():base.tearDownModule()
