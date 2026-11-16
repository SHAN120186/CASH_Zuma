"""Admin password recovery does not grant workflow rights or silently reset users."""
import os
import unittest
from unittest.mock import patch
from cryptography.fernet import Fernet
import tests.test_site as base
from sqlalchemy import select
from app.db import unit, User, UserCredential, LoginSession, CompanyUser, Audit
from app import credential_vault, access
from app.security import verify_password

REASON = 'Сотрудник восстанавливает доступ к своей учётной записи'
NEW_PASSWORD = ' Manual-Secret_2026! '


class AdminCredentialTests(base.SiteTests):
    def setUp(self):
        self.vault_env = patch.dict(os.environ, {'CREDENTIAL_VAULT_KEY': Fernet.generate_key().decode()})
        self.vault_env.start()
        super().setUp()

    def tearDown(self):
        try:
            super().tearDown()
        finally:
            self.vault_env.stop()

    def create(self, username='vault.employee'):
        r = self.client.post('/api/admin/users', headers=self.h, json={
            'username': username, 'name': 'Synthetic vault employee', 'company_id': self.company,
            'role': 'employee', 'reason': REASON})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['user']['id'], r.json()['temporary_password']

    def reveal(self, uid, **kwargs):
        return self.client.post(f'/api/admin/users/{uid}/credential/reveal',
                                headers=kwargs.pop('headers', self.h), json={'reason': REASON}, **kwargs)

    def test_create_reveal_and_lists_never_contain_password_or_ciphertext(self):
        uid, password = self.create()
        with unit() as s:
            before_hash = s.get(User, uid).password_hash
            token = s.get(UserCredential, uid).ciphertext.decode('ascii')
            sessions = list(s.scalars(select(LoginSession.token_hash)))
        listed = self.client.get('/api/admin/users', headers=self.h)
        detail = self.client.get(f'/api/admin/users/{uid}', headers=self.h)
        self.assertEqual(detail.json()['credential_state'], 'stored')
        for r in (listed, detail):
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotIn(password, r.text)
            self.assertNotIn(before_hash, r.text)
            self.assertNotIn(token, r.text)
        revealed = self.reveal(uid)
        self.assertEqual(revealed.status_code, 200, revealed.text)
        self.assertEqual(revealed.json(), {'username': 'vault.employee', 'password': password})
        self.assertEqual(revealed.headers['Cache-Control'], 'no-store')
        with unit() as s:
            self.assertEqual(before_hash, s.get(User, uid).password_hash)
            self.assertEqual(sessions, list(s.scalars(select(LoginSession.token_hash))))
            audit = s.scalar(select(Audit).where(Audit.action == 'Просмотрен сохранённый пароль'))
            self.assertIsNotNone(audit)
            self.assertIn(REASON, audit.detail)
            self.assertNotIn(password, audit.detail)
            self.assertNotIn(token, audit.detail)

    def test_import_verifies_current_password_and_preserves_assignments_hash_and_session(self):
        uid, password = self.create()
        cl = base.TestClient(base.app)
        try:
            login = cl.post('/api/login', json={'username': 'vault.employee', 'password': password})
            self.assertEqual(login.status_code, 200)
            with unit(True) as s:
                credential_vault.forget(s, s.get(User, uid))
                old_hash = s.get(User, uid).password_hash
                links = [(m.company_id, m.role) for m in s.scalars(select(CompanyUser).where(CompanyUser.user_id == uid))]
            bad = self.client.post(f'/api/admin/users/{uid}/credential/import', headers=self.h,
                                   json={'password': 'Wrong-Known-Secret_2026!', 'reason': REASON})
            self.assertEqual(bad.status_code, 409, bad.text)
            self.assertEqual(self.reveal(uid).status_code, 404)
            saved = self.client.post(f'/api/admin/users/{uid}/credential/import', headers=self.h,
                                     json={'password': password, 'reason': REASON})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(self.reveal(uid).json()['password'], password)
            self.assertEqual(cl.get('/api/companies').status_code, 200)
            with unit() as s:
                self.assertEqual(old_hash, s.get(User, uid).password_hash)
                self.assertEqual(links, [(m.company_id, m.role) for m in s.scalars(select(CompanyUser).where(CompanyUser.user_id == uid))])
        finally:
            cl.close()

    def test_admin_only_post_csrf_and_origin_checks(self):
        uid, _ = self.create()
        for path in ('reveal', 'import'):
            url = f'/api/admin/users/{uid}/credential/{path}'
            body = {'reason': REASON, **({'password': NEW_PASSWORD} if path == 'import' else {})}
            self.assertEqual(self.client.post(url, json=body).status_code, 403)
            self.assertEqual(self.client.post(url, json=body, headers={**self.h, 'Origin': 'https://foreign.invalid'}).status_code, 403)
            self.assertEqual(self.client.get(url, headers=self.h).status_code, 405)
            for role in ('employee', 'accountant', 'founder'):
                cl, headers = self.user_in(self.company, role, 'vault_' + path + '_' + role)
                try:
                    denied = cl.post(url, json=body, headers=headers)
                    self.assertEqual(denied.status_code, 403, denied.text)
                finally:
                    cl.close()
            anonymous = base.TestClient(base.app)
            try:
                self.assertEqual(anonymous.post(url, json=body).status_code, 401)
            finally:
                anonymous.close()

    def test_explicit_custom_reset_keeps_spaces_and_revokes_old_session(self):
        uid, password = self.create()
        cl = base.TestClient(base.app)
        try:
            self.assertEqual(cl.post('/api/login', json={'username': 'vault.employee', 'password': password}).status_code, 200)
            reset = self.client.post(f'/api/admin/users/{uid}/password', headers=self.h,
                                     json={'reason': REASON, 'password': NEW_PASSWORD})
            self.assertEqual(reset.status_code, 200, reset.text)
            self.assertEqual(reset.json()['temporary_password'], NEW_PASSWORD)
            self.assertEqual(self.reveal(uid).json()['password'], NEW_PASSWORD)
            self.assertEqual(cl.get('/api/companies').status_code, 401)
            with unit() as s:
                self.assertTrue(verify_password(NEW_PASSWORD, s.get(User, uid).password_hash))
                self.assertFalse(s.get(User, uid).must_change_password)
            login = cl.post('/api/login', json={'username': 'vault.employee', 'password': NEW_PASSWORD})
            self.assertEqual(login.status_code, 200, login.text)
        finally:
            cl.close()

    def test_weak_reset_invalid_import_and_unavailable_key_do_not_corrupt_access(self):
        uid, password = self.create()
        weak = self.client.post(f'/api/admin/users/{uid}/password', headers=self.h,
                                json={'reason': REASON, 'password': '123456789012'})
        self.assertEqual(weak.status_code, 422)
        self.assertNotIn('123456789012', weak.text)
        with patch.dict(os.environ, {'CREDENTIAL_VAULT_KEY': ''}):
            self.assertFalse(self.client.get('/api/admin/meta', headers=self.h).json()['credential_vault_enabled'])
            self.assertEqual(self.reveal(uid).status_code, 503)
            r = self.client.post(f'/api/admin/users/{uid}/credential/import', headers=self.h,
                                 json={'password': password, 'reason': REASON})
            self.assertEqual(r.status_code, 503)
        self.assertEqual(self.reveal(uid).json()['password'], password, 'failed import rolls back forgetting the old entry')

    def test_archive_forgets_password_restore_stores_new_one(self):
        uid, password = self.create()
        r = self.client.post(f'/api/admin/users/{uid}/archive', headers=self.h, json={'reason': REASON})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.reveal(uid).status_code, 409)
        with unit() as s:
            self.assertIsNone(s.get(UserCredential, uid))
        r = self.client.post(f'/api/admin/users/{uid}/restore', headers=self.h,
                             json={'reason': REASON, 'company_id': self.company, 'role': 'employee'})
        self.assertEqual(r.status_code, 200, r.text)
        new_password = r.json()['temporary_password']
        self.assertNotEqual(new_password, password)
        self.assertEqual(self.reveal(uid).json()['password'], new_password)

    def test_missing_key_reset_keeps_login_functional_and_removes_old_copy(self):
        uid, _ = self.create()
        with patch.dict(os.environ, {'CREDENTIAL_VAULT_KEY': ''}):
            response = self.client.post(f'/api/admin/users/{uid}/password', headers=self.h,
                                        json={'reason': REASON, 'password': NEW_PASSWORD})
            self.assertEqual(response.status_code, 200, response.text)
        with unit() as s:
            self.assertIsNone(s.get(UserCredential, uid))
            self.assertTrue(verify_password(NEW_PASSWORD, s.get(User, uid).password_hash))
        self.assertEqual(self.reveal(uid).status_code, 404)
        short = self.client.post(f'/api/admin/users/{uid}/password', headers=self.h,
                                 json={'reason': REASON, 'password': 'TooShort!9'})
        self.assertEqual(short.status_code, 422)
        self.assertNotIn('TooShort!9', short.text)

    def test_audit_failure_rolls_back_create_reset_and_verified_import(self):
        uid, password = self.create()
        with unit() as s:
            old_hash = s.get(User, uid).password_hash
            old_token = s.get(UserCredential, uid).ciphertext
        with patch.object(access, 'record', side_effect=RuntimeError('Synthetic audit failure')):
            for url, body in (
                ('/api/admin/users', {'username': 'rollback.employee', 'name': 'Synthetic rollback',
                                     'company_id': self.company, 'role': 'employee', 'reason': REASON}),
                (f'/api/admin/users/{uid}/password', {'password': NEW_PASSWORD, 'reason': REASON}),
                (f'/api/admin/users/{uid}/credential/import', {'password': password, 'reason': REASON}),
            ):
                with self.assertRaises(RuntimeError):
                    self.client.post(url, headers=self.h, json=body)
        with unit() as s:
            self.assertIsNone(s.scalar(select(User.id).where(User.username == 'rollback.employee')))
            self.assertEqual(s.get(User, uid).password_hash, old_hash)
            self.assertEqual(s.get(UserCredential, uid).ciphertext, old_token)
        self.assertEqual(self.reveal(uid).json()['password'], password)

    def test_self_service_password_change_updates_vault_and_legacy_paths_also_store(self):
        uid, password = self.create()
        cl = base.TestClient(base.app)
        try:
            login = cl.post('/api/login', json={'username': 'vault.employee', 'password': password})
            changed = cl.post('/api/password', headers={'X-CSRF-Token': login.json()['csrf']},
                              json={'old_password': password, 'new_password': NEW_PASSWORD})
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertEqual(self.reveal(uid).json()['password'], NEW_PASSWORD)
        finally:
            cl.close()
        reset = self.client.post(f'/api/users/{uid}/password', headers=self.h, json={'password': base.PASSWORD})
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(self.reveal(uid).json()['password'], base.PASSWORD)
        edited = self.client.post(f'/api/users/{uid}', headers=self.h,
                                  json={'role': 'employee', 'active': True, 'password': NEW_PASSWORD})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(self.reveal(uid).json()['password'], NEW_PASSWORD)


for _name in dir(base.SiteTests):
    if _name.startswith('test'):
        setattr(AdminCredentialTests, _name, None)


def tearDownModule():
    base.tearDownModule()


if __name__ == '__main__':
    unittest.main(verbosity=2)
