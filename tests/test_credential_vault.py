"""Isolated vault tests with synthetic credentials; never contact production."""
import base64
from datetime import datetime, timedelta
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = TMP.name
os.environ.pop('DATABASE_URL', None)
TEST_URL = os.getenv('TEST_DATABASE_URL')
if TEST_URL:
    from sqlalchemy.engine import make_url
    if not (make_url(TEST_URL).database or '').startswith('zuma_test_'):
        raise RuntimeError('Test database must start with zuma_test_')
    os.environ['DATABASE_URL'] = TEST_URL

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import Session
from app.db import Base, LoginSession, Role, User, UserCredential, engine as app_engine
from app import credential_vault as vault

PASSWORD = 'Synthetic-vault-password-123!'


class CredentialVaultTests(unittest.TestCase):
    def setUp(self):
        self.key = Fernet.generate_key().decode('ascii')
        env = patch.dict(os.environ, {'CREDENTIAL_VAULT_KEY': self.key})
        env.start()
        self.addCleanup(env.stop)
        self.raw_engine = create_engine(TEST_URL or 'sqlite://')
        self.schema = None
        if TEST_URL:
            # Every test owns a fresh schema, including on a shared test server.
            self.schema = 'vault_' + uuid.uuid4().hex
            with self.raw_engine.begin() as conn:
                conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
            self.engine = self.raw_engine.execution_options(schema_translate_map={None: self.schema})
        else:
            self.engine = self.raw_engine
        Base.metadata.create_all(self.engine, tables=[Role.__table__, User.__table__,
                                                    UserCredential.__table__, LoginSession.__table__])
        with Session(self.engine) as s, s.begin():
            s.add(Role(name='employee'))
            s.flush()
            users = [User(username=name, name='Synthetic ' + name, role='employee', password_hash='synthetic-login-hash')
                     for name in ('alice', 'bob')]
            s.add_all(users)
            s.flush()
            self.alice, self.bob = [u.id for u in users]
            s.add(LoginSession(user_id=self.alice, token_hash='synthetic-session-hash', csrf='synthetic-csrf',
                               expires_at=datetime.now() + timedelta(hours=1)))

    def tearDown(self):
        if self.schema:
            with self.raw_engine.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.raw_engine.dispose()

    def save(self, user_id=None, password=PASSWORD):
        with Session(self.engine) as s, s.begin():
            user = s.get(User, user_id or self.alice)
            self.assertTrue(vault.store(s, user, password, actor_id=self.bob))

    def assert_error(self, state, s, user):
        with self.assertRaises(vault.VaultError) as failure:
            vault.reveal(s, user)
        self.assertEqual(failure.exception.state, state)
        self.assertNotIn(PASSWORD, str(failure.exception))

    def test_ciphertext_round_trip_randomization_and_no_authentication_changes(self):
        self.save()
        with Session(self.engine) as s, s.begin():
            user = s.get(User, self.alice)
            row = s.get(UserCredential, self.alice)
            self.assertIn('ciphertext', inspect(row).unloaded)
            first_token = row.ciphertext
            self.assertNotIn(PASSWORD.encode(), first_token)
            self.assertEqual(vault.reveal(s, user), PASSWORD)
            self.assertTrue(vault.store(s, user, PASSWORD, actor_id=self.bob))
            self.assertNotEqual(first_token, row.ciphertext)
            self.assertEqual(row.updated_by, self.bob)
            self.assertIsNotNone(row.updated_at)
            self.assertEqual(user.password_hash, 'synthetic-login-hash')
            self.assertEqual(user.username, 'alice')
            self.assertTrue(user.active)
            self.assertFalse(user.must_change_password)
            self.assertEqual(len(list(s.scalars(select(LoginSession)))), 1)

    def test_same_key_survives_module_reload_and_new_database_session(self):
        self.save()
        importlib.reload(vault)
        with Session(self.engine) as s:
            self.assertTrue(vault.configured())
            self.assertEqual(vault.reveal(s, s.get(User, self.alice)), PASSWORD)

    def test_states_are_one_metadata_query_without_loading_ciphertext(self):
        self.save()
        with Session(self.engine) as s:
            users = list(s.scalars(select(User)))
            captured = []
            def record(conn, cursor, statement, parameters, context, many):
                captured.append(statement)
            event.listen(self.engine, 'before_cursor_execute', record)
            try:
                self.assertEqual(vault.states(s, users), {self.alice: 'stored', self.bob: 'missing'})
            finally:
                event.remove(self.engine, 'before_cursor_execute', record)
            self.assertEqual(len(captured), 1)
            self.assertNotIn('ciphertext', captured[0].lower())
            self.assertEqual(vault.states(s, []), {})

    def test_missing_credentials_fail_closed(self):
        with Session(self.engine) as s:
            self.assert_error('missing', s, s.get(User, self.alice))

    def test_password_hash_change_marks_stale_without_revealing_previous_value(self):
        self.save()
        with Session(self.engine) as s, s.begin():
            user = s.get(User, self.alice)
            user.password_hash = 'synthetic-new-login-hash'
            self.assertEqual(vault.states(s, [user]), {self.alice: 'stale'})
            self.assert_error('stale', s, user)

    def test_missing_invalid_and_wrong_keys_never_use_the_jwt_secret(self):
        self.save()
        invalid_keys = ['', 'invalid', '\u043a\u043b\u044e\u0447', base64.urlsafe_b64encode(b'x' * 31).decode(),
                        Fernet.generate_key().decode()]
        for key in invalid_keys:
            with self.subTest(valid_length=len(key)), patch.dict(os.environ, {'CREDENTIAL_VAULT_KEY': key,
                                                                            'JWT_SECRET': self.key}):
                with Session(self.engine) as s:
                    user = s.get(User, self.alice)
                    self.assertEqual(vault.states(s, [user]), {self.alice: 'locked'})
                    self.assert_error('locked', s, user)

    def test_store_without_valid_key_forgets_old_copy_and_keeps_hash_and_session(self):
        for key in ('', 'invalid'):
            self.save()
            with patch.dict(os.environ, {'CREDENTIAL_VAULT_KEY': key}):
                self.assertFalse(vault.configured())
                with Session(self.engine) as s, s.begin():
                    user = s.get(User, self.alice)
                    self.assertFalse(vault.store(s, user, PASSWORD))
                    self.assertEqual(user.password_hash, 'synthetic-login-hash')
            with Session(self.engine) as s:
                self.assertIsNone(s.get(UserCredential, self.alice))
                self.assertEqual(len(list(s.scalars(select(LoginSession)))), 1)

    def test_tampered_ciphertext_fails_closed(self):
        self.save()
        with Session(self.engine) as s, s.begin():
            row = s.get(UserCredential, self.alice)
            row.ciphertext = row.ciphertext[:-5] + b'xxxxx'
        with Session(self.engine) as s:
            self.assert_error('locked', s, s.get(User, self.alice))

    def test_swapped_ciphertext_is_rejected_even_when_users_have_the_same_login_hash(self):
        self.save()
        self.save(self.bob, 'Other-synthetic-password-456!')
        with Session(self.engine) as s, s.begin():
            alice, bob = [s.get(UserCredential, uid) for uid in (self.alice, self.bob)]
            alice.ciphertext, bob.ciphertext = bob.ciphertext, alice.ciphertext
        with Session(self.engine) as s:
            self.assert_error('locked', s, s.get(User, self.alice))
            self.assert_error('locked', s, s.get(User, self.bob))

    def test_authenticated_but_invalid_envelope_is_rejected(self):
        self.save()
        for change in ({'v': 2}, {'username': 'other-user'}, {'hash_fingerprint': 'other-hash'},
                       {'password': None}, {'password': ''}, {'password': 'x' * 129}, {'user_id': True}):
            with self.subTest(changed_fields=list(change)):
                with Session(self.engine) as s, s.begin():
                    row = s.get(UserCredential, self.alice)
                    envelope = {'v': 1, 'user_id': self.alice, 'username': 'alice',
                                'hash_fingerprint': row.hash_fingerprint, 'password': PASSWORD}
                    envelope.update(change)
                    row.ciphertext = Fernet(self.key.encode()).encrypt(json.dumps(envelope).encode())
                with Session(self.engine) as s:
                    self.assert_error('locked', s, s.get(User, self.alice))

    def test_forget_is_idempotent_and_does_not_touch_login_credentials(self):
        self.save()
        with Session(self.engine) as s, s.begin():
            user = s.get(User, self.alice)
            vault.forget(s, user)
        with Session(self.engine) as s, s.begin():
            user = s.get(User, self.alice)
            vault.forget(s, user)
            self.assertIsNone(s.get(UserCredential, self.alice))
            self.assertEqual(user.password_hash, 'synthetic-login-hash')
            self.assertEqual(len(list(s.scalars(select(LoginSession)))), 1)

    def test_vault_write_rolls_back_with_the_password_transaction(self):
        self.save()
        try:
            with Session(self.engine) as s, s.begin():
                user = s.get(User, self.alice)
                user.password_hash = 'synthetic-replacement-hash'
                vault.store(s, user, 'Replacement-synthetic-password-789!')
                s.flush()
                raise RuntimeError('Synthetic rollback')
        except RuntimeError:
            pass
        with Session(self.engine) as s:
            user = s.get(User, self.alice)
            self.assertEqual(user.password_hash, 'synthetic-login-hash')
            self.assertEqual(vault.reveal(s, user), PASSWORD)


def tearDownModule():
    app_engine.dispose()
    TMP.cleanup()
