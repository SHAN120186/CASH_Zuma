"""Home-only access must not expand financial, request or company permissions."""
import unittest
from types import SimpleNamespace

import tests.test_site as base
from sqlalchemy import select
from app.db import unit, User
from app.security import PERMS, COMPANY_ROLES, scope_user, perms_of
from app.section_access import defaults


class HomeAccessTests(base.SiteTests):
    def uid(self, name):
        with unit() as s:
            return s.scalar(select(User.id).where(User.username == name))

    def sections(self, uid, policy, version=0):
        return self.client.put(f'/api/admin/users/{uid}/sections', headers=self.h, json={
            'company_id': self.company, 'version': version, 'sections': policy,
            'reason': 'Owner enabled the compact home page only',
        })

    def test_all_assigned_roles_have_home_by_default(self):
        for role in PERMS:
            with self.subTest(role=role):
                user = SimpleNamespace(role=role)
                scope_user(user, role if role in COMPANY_ROLES else None)
                self.assertIn('home_view', perms_of(user))
                self.assertEqual(defaults(perms_of(user))['home'], 'view')

    def test_unassigned_employee_has_no_home_or_other_company_permissions(self):
        for role in COMPANY_ROLES:
            with self.subTest(role=role):
                user = SimpleNamespace(role=role)
                scope_user(user, None)
                self.assertEqual(perms_of(user), frozenset())
                self.assertEqual(defaults(perms_of(user))['home'], 'hidden')

    def test_home_default_alone_does_not_open_other_sections(self):
        modes = defaults({'home_view'})
        self.assertEqual(modes.pop('home'), 'view')
        self.assertEqual(set(modes.values()), {'hidden'})

    def test_home_grant_preserves_financial_and_request_denials(self):
        # A material accountant may read ledger documents but no request queue,
        # balances, budgets, calendar or dashboard, even with explicit home access.
        cl, h = self.make_user('material_accountant', 'home_reader')
        cl.close()
        uid = self.uid('home_reader')
        response = self.sections(uid, {'home': 'view'})
        self.assertEqual(response.status_code, 200, response.text)
        cl, h = self.relogin('home_reader')
        try:
            boot = cl.get('/api/bootstrap', headers=h)
            self.assertEqual(boot.status_code, 200, boot.text)
            user = boot.json()['user']
            self.assertEqual(set(user['permissions']), {'ledger', 'home_view'})
            self.assertEqual(user['sections']['home'], 'view')
            self.assertEqual(user['sections']['requests'], 'hidden')
            self.assertEqual(cl.get('/api/ledger', headers=h).status_code, 200)
            for path in ('/api/dashboard', '/api/accounts', '/api/budgets',
                         '/api/report', '/api/requests', '/api/receipts'):
                with self.subTest(path=path):
                    self.assertEqual(cl.get(path, headers=h).status_code, 403)
            self.assertEqual(cl.get('/api/bootstrap', headers={
                **h, 'X-Company-ID': str(self.company + 999),
            }).status_code, 403)
        finally:
            cl.close()

    def test_home_does_not_expand_cashier_request_visibility_or_actions(self):
        foreign = self.request(submit=False).json()['id']
        cl, h = self.make_user('cashier', 'home_cashier')
        cl.close()
        self.assertEqual(self.sections(self.uid('home_cashier'), {'home': 'view'}).status_code, 200)
        cl, h = self.relogin('home_cashier')
        try:
            user = cl.get('/api/bootstrap', headers=h).json()['user']
            self.assertEqual(set(user['permissions']), PERMS['cashier'])
            self.assertNotIn('view', user['permissions'])
            self.assertNotIn('approve', user['permissions'])
            self.assertEqual(cl.get('/api/requests', headers=h).json(), [])
            self.assertEqual(cl.get(f'/api/requests/{foreign}', headers=h).status_code, 404)
            self.assertEqual(cl.get('/api/dashboard', headers=h).status_code, 403)
            self.assertEqual(cl.get('/api/receipts', headers=h).status_code, 403)
        finally:
            cl.close()

    def test_existing_hidden_home_policy_remains_explicit_and_other_modes_stay_intact(self):
        cl, h = self.make_user('cashier', 'hidden_home_cashier')
        cl.close()
        uid = self.uid('hidden_home_cashier')
        policy = {'home': 'hidden', 'requests': 'view', 'ledger': 'hidden'}
        self.assertEqual(self.sections(uid, policy).status_code, 200)
        cl, h = self.relogin('hidden_home_cashier')
        try:
            user = cl.get('/api/bootstrap', headers=h).json()['user']
            self.assertEqual(user['sections']['home'], 'hidden')
            self.assertEqual(user['sections']['requests'], 'view')
            self.assertEqual(user['sections']['ledger'], 'hidden')
        finally:
            cl.close()
        self.assertEqual(self.sections(uid, {**policy, 'home': 'view'}, version=1).status_code, 200)
        cl, h = self.relogin('hidden_home_cashier')
        try:
            user = cl.get('/api/bootstrap', headers=h).json()['user']
            self.assertEqual(user['sections']['home'], 'view')
            self.assertEqual(user['sections']['requests'], 'view')
            self.assertEqual(user['sections']['ledger'], 'hidden')
            self.assertEqual(cl.get('/api/ledger', headers=h).status_code, 403)
            self.assertEqual(cl.get('/api/accounts', headers=h).status_code, 403)
        finally:
            cl.close()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(HomeAccessTests(name) for name in HomeAccessTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    base.tearDownModule()


if __name__ == '__main__':
    unittest.main()
