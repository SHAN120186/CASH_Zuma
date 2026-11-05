"""Director authority caps: isolated requests, companies and money facts."""
import unittest
from sqlalchemy import select, inspect, text
import tests.test_site as base
from app.db import Category, Company, Ledger, PaymentRequest, Setting, unit, engine, initialize
from app.director_limits import TABLE_LIMITS, migrate_director_limits

TABLE_NAMES = {'12': 'Расходы на маркетинг (бонус)',
               '65': 'Расчеты по предоставленным займам 4720 (Моддий ёрдам тарикасидаги туловлар)',
               '70': 'Долгосрочные займы 7820', '78': 'Само учун карз',
               '98': 'Девидент', '17': 'Закуп основных средств'}


class DirectorLimitTests(base.SiteTests):
    def set_limit(self, category=None, uzs=None, usd=None, eur=None, client=None, headers=None):
        return (client or self.client).put(f'/api/categories/{category or self.cat}/director-limits',
            headers=headers or self.h, json={'limit_uzs': uzs, 'limit_usd': usd, 'limit_eur': eur,
                                            'reason': 'Владелец утвердил лимит директора'})

    def final(self, rid):
        return self.post(f'/api/requests/{rid}/decision', {'action': 'approve', 'note': 'Утверждение в пределах полномочий'},
                         *self.stage_user('director'))

    def test_table_migration_all_companies_idempotency_and_no_formula_guess(self):
        with unit(True) as s:
            s.add(Company(code='THIRD', name='Synthetic third company', active=False));s.flush()
            companies = list(s.scalars(select(Company)))
            for company in companies:
                for code in [*TABLE_LIMITS, '8', '101', '53', '999']:
                    s.add(Category(company_id=company.id, code=code, name=TABLE_NAMES.get(code, 'Synthetic code ' + code),
                                   type='outcome', activity='operating'))
            s.delete(s.get(Setting, 'director_upper_limits_v1'))
        migrate_director_limits(engine)
        with unit() as s:
            unknown = s.scalar(select(Company.id).where(Company.code == 'UNASSIGNED'))
            for category in s.scalars(select(Category).where(Category.code.is_not(None))):
                expected = TABLE_LIMITS.get(category.code) if category.company_id != unknown else None
                self.assertEqual(category.director_limit_uzs, expected, (category.company_id, category.code))
                self.assertIsNone(category.director_limit_usd);self.assertIsNone(category.director_limit_eur)
            equipment = s.scalar(select(Category).where(Category.company_id == self.company, Category.name == 'Оборудование'))
            self.assertEqual(equipment.director_limit_uzs, 10_000_000_000)
        self.assertEqual(self.set_limit(uzs='900').status_code, 200)
        initialize()
        with unit() as s:self.assertEqual(s.get(Category, self.cat).director_limit_uzs, 90000)

    def test_existing_database_adds_limit_columns_without_rewriting_requests(self):
        rid = self.request('600').json()['id']
        before = self.row(rid)
        with engine.begin() as conn:
            for field in ('director_limit_uzs', 'director_limit_usd', 'director_limit_eur', 'director_limit_changed_at'):
                conn.execute(text(f'ALTER TABLE categories DROP COLUMN {field}'))
        initialize()
        self.assertIn('director_limit_uzs', {c['name'] for c in inspect(engine).get_columns('categories')})
        after = self.row(rid)
        for key in ('amount', 'status', 'version', 'route', 'checked_by', 'finance_approved_by', 'approved_by'):
            self.assertEqual(after[key], before[key], key)

    def test_legacy_codes_in_names_are_migrated_for_every_company(self):
        expected = {}
        with unit(True) as s:
            for company in s.scalars(select(Company).where(Company.code != 'UNASSIGNED')):
                for separator in ('·', '|', '—'):
                    for code in TABLE_LIMITS:
                        category = Category(company_id=company.id, name=f'{code} {separator} {TABLE_NAMES[code]}', type='outcome')
                        s.add(category);s.flush();expected[category.id] = TABLE_LIMITS[code]
                # Numbers belong to a company's own catalog. A matching number
                # cannot cap an unrelated article, and another number cannot
                # hide the same actual expense type in a different company.
                for code, name, limit in [('17', '17 · Other expense', None), ('65', '65 · Refund expense', None),
                        ('70', '70 · Other financing', None), ('other', '271 · Закуп основных средств', 10_000_000_000),
                        ('other2', '273 · Дивиденды', 0), ('98', '98 · Двидент', 0),
                        (None, 'Synthetic 17 · not a code prefix', None)]:
                    category = Category(company_id=company.id, code=code, name=name, type='outcome')
                    s.add(category);s.flush();expected[category.id] = limit
            s.delete(s.get(Setting, 'director_upper_limits_v1'))
        migrate_director_limits(engine)
        with unit() as s:
            for category in s.scalars(select(Category).where(Category.id.in_(expected))):
                self.assertEqual(category.director_limit_uzs, expected[category.id], category.name)
                self.assertEqual(category.director_limit_changed_at is not None, expected[category.id] is not None)

    def test_exact_cap_allowed_above_cap_rejected_and_transaction_unchanged(self):
        self.assertEqual(self.set_limit(uzs='100000000').status_code, 200)
        exact = self.request('100000000').json()['id'];self.after_finance(exact)
        accepted = self.final(exact);self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()['status'], 'approved')
        high = self.request('100000000.01').json()['id'];self.after_finance(high)
        before = self.row(high)
        self.assertNotIn('approve', self.stage_user('director')[0].get(f'/api/requests/{high}', headers=self.director[1]).json()['actions'])
        denied = self.final(high);self.assertEqual(denied.status_code, 409, denied.text)
        self.assertIn('100 000 000 UZS', denied.json()['detail'])
        after = self.row(high)
        self.assertEqual((after['status'], after['version'], after['approved_by']), ('pending', before['version'], None))
        self.assertTrue(after['director_limit']['exceeded'])
        with unit() as s:self.assertEqual(list(s.scalars(select(Ledger))), [])

    def test_zero_blocks_final_finance_approval_and_skip_cannot_bypass(self):
        self.assertEqual(self.set_limit(uzs='0').status_code, 200)
        self.assertEqual(self.set_policy(self.cat, 'threshold', uzs='1000000').status_code, 200)
        rid = self.request('1').json()['id'];self.assertEqual(self.check(rid).status_code, 200)
        response = self.finance_approve(rid)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIsNone(self.row(rid)['finance_approved_by'])
        with unit(True) as s:
            category = s.get(Category, self.cat);category.skip_allowed = True;category.director_policy = 'skip'
        other = self.request('1').json()['id'];self.assertEqual(self.check(other).status_code, 200)
        self.assertEqual(self.finance_approve(other).status_code, 409)
        self.assertEqual(self.set_limit(uzs=None).status_code, 200)
        self.assertEqual(self.finance_approve(other).json()['status'], 'approved')

    def test_current_limit_covers_old_pending_but_keeps_approved_payment(self):
        pending = self.request('600').json()['id'];self.after_finance(pending)
        approved = self.request('600').json()['id'];self.assertEqual(self.approve(approved).status_code, 200)
        self.assertEqual(self.set_limit(uzs='500').status_code, 200)
        self.assertEqual(self.final(pending).status_code, 409)
        self.assertEqual(self.ledger('600', 'out', 'Prior approved request', request_id=approved).status_code, 200)
        self.assertEqual(self.set_limit(uzs='600').status_code, 200)
        self.assertEqual(self.final(pending).json()['status'], 'approved')

    def test_currency_limits_are_independent_and_exact_minor_units(self):
        self.set_limit(uzs='0', usd='7.50', eur='0')
        usd = self.account('Synthetic USD cap', 'USD');eur = self.account('Synthetic EUR cap', 'EUR')
        for account, value, code in [(usd, '7.50', 200), (usd, '7.51', 409), (eur, '0.01', 409), (self.acc, '0.01', 409)]:
            rid = self.request(value, account=account).json()['id'];self.after_finance(rid)
            self.assertEqual(self.final(rid).status_code, code)

    def test_only_admin_edits_caps_audit_and_company_isolation(self):
        for role in ('director', 'finance', 'founder', 'employee'):
            cl, headers = self.make_user(role, 'cap_' + role)
            try:self.assertEqual(self.set_limit(uzs='500', client=cl, headers=headers).status_code, 403, role)
            finally:cl.close()
        result = self.set_limit(uzs='123.45');self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['director_limits'], {'UZS': '123.45', 'USD': None, 'EUR': None})
        self.assertEqual(self.set_policy(self.cat, 'always').json()['director_limits']['UZS'], '123.45')
        with unit() as s:other = s.scalar(select(Company.id).where(Company.code == 'ZUMA'))
        foreign = self.post('/api/categories', {'name': 'Separate company cap', 'type': 'outcome', 'activity': 'operating'},
                            headers={**self.h, 'X-Company-ID': str(other)}).json()['id']
        self.assertEqual(self.set_limit(category=foreign, uzs='1').status_code, 404)
        self.assertEqual(self.set_limit(category=foreign, uzs='1', headers={**self.h, 'X-Company-ID': str(other)}).status_code, 200)
        self.assertEqual(self.set_limit(category=next(c['id'] for c in self.client.get('/api/bootstrap').json()['categories'] if c['type'] == 'income'), uzs='1').status_code, 422)
        for value in ('-1', 'NaN', 'Infinity', '1.001', '100000000000001'):
            self.assertEqual(self.set_limit(uzs=value).status_code, 422, value)
        self.assertEqual(self.row(self.request('1').json()['id'])['director_limit']['items'][0]['limit'], '123.45')
        audit = self.client.get('/api/audit', headers=self.h).json()
        self.assertTrue(any(row['action'] == 'Изменён лимит директора по статье' for row in audit))

    def test_cash_funding_checks_fee_and_combined_same_category(self):
        cash = self.account('Synthetic cash cap', kind='cash', opening='0')
        fee = self.post('/api/categories', {'name': 'Synthetic cap fee', 'type': 'outcome', 'activity': 'operating'}).json()['id']
        self.set_limit(uzs='995000');self.set_limit(category=fee, uzs='9999')
        for fee_category in (self.cat, fee):
            body = {**self.request_body('990000', account=cash), 'cash_funding': {
                'bank_account_id': self.acc, 'gross_amount': '1000000', 'fee_percent': '1', 'fee_category_id': fee_category}}
            draft = self.post('/api/requests', body);self.assertEqual(draft.status_code, 200, draft.text)
            rid = draft.json()['id'];self.upload(rid)
            self.assertEqual(self.post(f'/api/requests/{rid}/decision', {'action': 'submit'}).status_code, 200)
            self.after_finance(rid)
            response = self.final(rid);self.assertEqual(response.status_code, 409, response.text)
            blocked = next(item for item in self.row(rid)['director_limit']['items'] if item['exceeded'])
            self.assertEqual(blocked['amount'], '1000000.00' if fee_category == self.cat else '10000.00')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(DirectorLimitTests(name) for name in DirectorLimitTests.__dict__ if name.startswith('test_'))


def tearDownModule():base.tearDownModule()
