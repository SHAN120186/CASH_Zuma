"""One-time group registration: isolated database, synthetic users, no Telegram/network calls."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch

# This module configures a disposable SQLite database before importing the app.
from tests import test_bot_api as base
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select
from app import clock
from app.bot_api import GROUP_CODE_REFUSAL, group_code_hash
from app.db import (Account, Audit, Base, Company, CompanyUser, TelegramGroup,
                    TelegramGroupCode, TelegramLink, User, engine, initialize, now, unit)
from app.main import app


CHAT = -1009876543210
ISSUE = '/api/admin/telegram-group-codes'
REGISTER = '/api/bot/v1/report-groups/register'


def tearDownModule():
    engine.dispose()


class GroupRegistrationTests(unittest.TestCase):
    def setUp(self):
        clock.FROZEN = base.FROZEN_TODAY
        self.config = patch.dict(base.os.environ, {'BOT_REPORT_GROUPS':
            f'{base.GROUP}:UZGERMED; {base.GROUP_BOTH}:UZGERMED,ZUMA; {base.GROUP_ZUMA}:ZUMA'})
        self.config.start()
        self.addCleanup(self.config.stop)
        Base.metadata.drop_all(engine)
        initialize()
        with unit(True) as s:
            user = User(username='group_admin', name='Synthetic Administrator', role='admin',
                        password_hash=base.HASH)
            s.add(user)
            s.flush()
            self.admin_id = user.id
            self.ids = {c.code: c.id for c in s.scalars(select(Company))}
        self.clients = []
        self.client, self.h, self.bearer = self.login('group_admin')

    def tearDown(self):
        for client in self.clients:
            client.close()
        clock.FROZEN = None

    def login(self, username):
        client = TestClient(app)
        self.clients.append(client)
        response = client.post('/api/login', json={'username': username, 'password': base.PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        return client, {'X-CSRF-Token': result['csrf']}, {'Authorization': 'Bearer ' + result['access_token']}

    def user(self, role, username):
        with unit(True) as s:
            user = User(username=username, name='Synthetic ' + role, role=role, password_hash=base.HASH)
            s.add(user)
            s.flush()
            uid = user.id
            if role not in ('admin', 'founder'):
                s.add(CompanyUser(user_id=uid, company_id=self.ids['ZUMA'], role=role))
        return uid, self.login(username)

    def issue(self, chat=CHAT, company='ZUMA', client=None, headers=None):
        return (client or self.client).post(ISSUE, json={'chat_id': chat, 'company_code': company},
                                           headers=self.h if headers is None else headers)

    def code(self, chat=CHAT, company='ZUMA'):
        response = self.issue(chat, company)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['code']

    def consume(self, code, chat=CHAT, actor=7654321, title='Synthetic finance group', auth=base.BOT, client=None):
        return (client or self.client).post(REGISTER, json={
            'code': code, 'chat_id': chat, 'title': title, 'telegram_user_id': actor,
        }, auth=auth)

    def assert_refused(self, response):
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json(), {'detail': GROUP_CODE_REFUSAL})

    def test_registration_needs_no_telegram_link_and_is_company_specific(self):
        for index, company in enumerate(('ZUMA', 'UZGERMED')):
            with self.subTest(company=company):
                chat = CHAT - index
                response = self.issue(chat, company.lower())
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                code = result['code']
                self.assertRegex(code, r'^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{20}$')
                self.assertEqual((result['chat_id'], result['company_code']), (chat, company))
                expiry = datetime.fromisoformat(result['expires_at'])
                self.assertEqual(expiry.utcoffset(), timedelta(0))
                self.assertTrue(now() + timedelta(minutes=9) < expiry.replace(tzinfo=None) <= now() + timedelta(minutes=10))
                with unit() as s:
                    pending = s.get(TelegramGroupCode, group_code_hash(code))
                    self.assertEqual((pending.chat_id, pending.company_id, pending.issued_by),
                                     (chat, self.ids[company], self.admin_id))
                    self.assertNotIn(code, pending.code_hash)
                    self.assertIsNone(s.get(TelegramGroup, chat))
                    self.assertEqual(s.scalar(select(func.count()).select_from(TelegramLink)), 0)
                response = self.consume(code.lower(), chat=chat)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {'registered': True, 'chat_id': chat,
                                                  'companies': [company], 'company_names': [result['company_name']]})
                with unit() as s:
                    group = s.get(TelegramGroup, chat)
                    self.assertEqual((group.added_by, group.companies), (self.admin_id, company))
                    self.assertIsNone(s.get(TelegramGroupCode, group_code_hash(code)))
                listed = self.client.get('/api/bot/v1/report-groups', auth=base.BOT).json()['items']
                self.assertEqual(next(g['companies'] for g in listed if g['chat_id'] == chat), [company])

    def test_expired_unknown_and_malformed_codes_have_one_safe_refusal(self):
        code = self.code()
        with unit(True) as s:
            s.get(TelegramGroupCode, group_code_hash(code)).expires_at = now() - timedelta(seconds=1)
        for value in (code, 'A' * 20, 'invalid-code', ''):
            with self.subTest(code_type=len(value)):
                self.assert_refused(self.consume(value))
        with unit() as s:
            self.assertIsNone(s.get(TelegramGroup, CHAT))

    def test_wrong_chat_preserves_code_then_replay_is_refused(self):
        code = self.code()
        self.assert_refused(self.consume(code, chat=CHAT - 1))
        with unit() as s:
            self.assertIsNotNone(s.get(TelegramGroupCode, group_code_hash(code)))
            self.assertIsNone(s.get(TelegramGroup, CHAT - 1))
        self.assertEqual(self.consume(code).status_code, 200)
        self.assert_refused(self.consume(code, title='Replay must not change title'))
        with unit() as s:
            self.assertEqual(s.get(TelegramGroup, CHAT).title, 'Synthetic finance group')

    def test_actor_needs_no_site_authority_but_issuer_does(self):
        actor_id, _ = self.user('founder', 'group_actor')
        # The bot, rather than a personal site link, verifies Telegram group admin status.
        self.assertEqual(self.consume(self.code(), actor=actor_id).status_code, 200)
        with unit() as s:
            self.assertEqual(s.get(TelegramGroup, CHAT).added_by, self.admin_id)
            self.assertEqual(s.scalar(select(func.count()).select_from(TelegramLink)), 0)
        for index, changes in enumerate(({'active': False}, {'role': 'founder'}, {'must_change_password': True})):
            with self.subTest(changes=changes):
                chat = CHAT - index - 1
                code = self.code(chat)
                with unit(True) as s:
                    issuer = s.get(User, self.admin_id)
                    for key, value in changes.items():
                        setattr(issuer, key, value)
                self.assert_refused(self.consume(code, chat=chat, actor=actor_id))
                with unit(True) as s:
                    issuer = s.get(User, self.admin_id)
                    issuer.active, issuer.role, issuer.must_change_password = True, 'admin', False

    def test_own_password_change_revokes_pending_group_codes(self):
        code = self.code()
        changed = self.client.post('/api/password', json={
            'old_password': base.PASSWORD, 'new_password': 'AnotherSyntheticPassword_6829!',
        }, headers=self.h)
        self.assertEqual(changed.status_code, 200, changed.text)
        with unit() as s:
            self.assertIsNone(s.get(TelegramGroupCode, group_code_hash(code)))
        self.assert_refused(self.consume(code))

    def test_admin_password_reset_revokes_pending_group_codes(self):
        code = self.code()
        _, (other, headers, _) = self.user('admin', 'second_admin')
        changed = other.post(f'/api/admin/users/{self.admin_id}/password',
                             json={'reason': 'Synthetic password reset verification'}, headers=headers)
        self.assertEqual(changed.status_code, 200, changed.text)
        with unit() as s:
            self.assertIsNone(s.get(TelegramGroupCode, group_code_hash(code)))
        self.assert_refused(self.consume(code))

    def test_role_change_revokes_pending_group_codes(self):
        code = self.code()
        _, (other, headers, _) = self.user('admin', 'second_admin')
        changed = other.put(f'/api/admin/users/{self.admin_id}/holding',
                            json={'holding_role': 'founder', 'reason': 'Synthetic holding role change'}, headers=headers)
        self.assertEqual(changed.status_code, 200, changed.text)
        with unit() as s:
            self.assertIsNone(s.get(TelegramGroupCode, group_code_hash(code)))
        self.assert_refused(self.consume(code))

    def test_archiving_issuer_revokes_pending_group_codes(self):
        code = self.code()
        _, (other, headers, _) = self.user('admin', 'second_admin')
        changed = other.post(f'/api/admin/users/{self.admin_id}/archive',
                             json={'reason': 'Synthetic issuer archive verification'}, headers=headers)
        self.assertEqual(changed.status_code, 200, changed.text)
        with unit() as s:
            self.assertIsNone(s.get(TelegramGroupCode, group_code_hash(code)))
        self.assert_refused(self.consume(code))

    def test_inactive_unknown_and_service_companies_cannot_be_connected(self):
        code = self.code()
        with unit(True) as s:
            s.get(Company, self.ids['ZUMA']).active = False
        self.assert_refused(self.consume(code))
        for company in ('ZUMA', 'MISSING', 'UNASSIGNED'):
            with self.subTest(company=company):
                self.assertEqual(self.issue(company=company).status_code, 422)
        with unit() as s:
            self.assertIsNone(s.get(TelegramGroup, CHAT))

    def test_cookie_bearer_and_bot_auth_boundaries(self):
        with TestClient(app) as anonymous:
            self.assertEqual(self.issue(client=anonymous, headers={}).status_code, 401)
        self.assertEqual(self.issue(headers={}).status_code, 403, 'Cookie POST needs CSRF')
        for role in ('founder', 'finance', 'employee'):
            with self.subTest(role=role):
                _, (client, headers, _) = self.user(role, 'forbidden_' + role)
                self.assertEqual(self.issue(client=client, headers=headers).status_code, 403)
        issued = self.issue(headers=self.bearer)
        self.assertEqual(issued.status_code, 200, issued.text)
        code = issued.json()['code']
        for auth in (None, (base.BOT[0], 'SyntheticWrongServiceSecret_9827!')):
            self.assertEqual(self.consume(code, auth=auth).status_code, 401)
        self.assertEqual(self.consume(code).status_code, 200)

    def test_rotating_code_does_not_touch_group_until_consumed(self):
        first = self.code()
        self.assertEqual(self.consume(first, title='Existing group').status_code, 200)
        with unit() as s:
            before = s.get(TelegramGroup, CHAT).added_at
        second, third = self.code(), self.code()
        self.assertNotEqual(second, third)
        with unit() as s:
            group = s.get(TelegramGroup, CHAT)
            self.assertEqual((group.title, group.added_at), ('Existing group', before))
            self.assertEqual(s.scalar(select(func.count()).select_from(TelegramGroupCode)), 1)
        self.assert_refused(self.consume(second))
        self.assertEqual(self.consume(third, title='Updated group').status_code, 200)
        with unit() as s:
            self.assertEqual(s.get(TelegramGroup, CHAT).title, 'Updated group')

    def test_registered_group_cannot_switch_company_at_either_stage(self):
        self.assertEqual(self.consume(self.code()).status_code, 200)
        self.assertEqual(self.issue(company='UZGERMED').status_code, 409)
        code = self.code()
        with unit(True) as s:
            s.get(TelegramGroup, CHAT).companies = 'UZGERMED'
        self.assertEqual(self.consume(code).status_code, 409)
        with unit() as s:
            self.assertEqual(s.get(TelegramGroup, CHAT).companies, 'UZGERMED')
            self.assertIsNotNone(s.get(TelegramGroupCode, group_code_hash(code)))
        with unit(True) as s:
            s.delete(s.get(TelegramGroup, CHAT))
        self.assertEqual(self.consume(code).status_code, 200)

    def test_server_config_is_never_overwritten(self):
        self.assertEqual(self.issue(chat=base.GROUP, company='UZGERMED').status_code, 409)
        code = self.code()
        with patch.dict(base.os.environ, {'BOT_REPORT_GROUPS': f'{CHAT}:ZUMA'}):
            self.assertEqual(self.consume(code).status_code, 409)
            self.assertEqual(self.issue().status_code, 409)
            with unit() as s:
                self.assertIsNone(s.get(TelegramGroup, CHAT))
                self.assertIsNotNone(s.get(TelegramGroupCode, group_code_hash(code)))
        self.assertEqual(self.consume(code).status_code, 200)

    def test_concurrent_consumption_is_single_use(self):
        code = self.code()
        def consume_once():
            with TestClient(app) as client:
                return self.consume(code, client=client).status_code
        with ThreadPoolExecutor(max_workers=2) as workers:
            statuses = list(workers.map(lambda _: consume_once(), range(2)))
        self.assertEqual(sorted(statuses), [200, 403])
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(TelegramGroup)), 1)
            self.assertEqual(s.scalar(select(func.count()).select_from(TelegramGroupCode)), 0)

    def test_code_is_absent_from_audit_titles_and_validation_errors(self):
        code = self.code()
        bad = self.client.post(REGISTER, json={'code': code, 'chat_id': CHAT, 'title': 'x' * 256,
                                              'telegram_user_id': 123}, auth=base.BOT)
        self.assertEqual(bad.status_code, 422)
        self.assertNotIn(code, bad.text)
        self.assertEqual(self.consume(code, title='Accidental pasted code ' + code.lower()).status_code, 200)
        with unit() as s:
            self.assertNotIn(code.lower(), s.get(TelegramGroup, CHAT).title.lower())
            for audit in s.scalars(select(Audit)):
                self.assertNotIn(code.lower(), (audit.action + audit.detail).lower())

    def test_additive_initialization_preserves_existing_data_and_large_chat_id(self):
        with unit(True) as s:
            s.add(TelegramGroup(chat_id=CHAT, title='Legacy group', companies='ZUMA', added_by=self.admin_id))
            account = Account(name='Synthetic legacy account', kind='bank', currency='UZS',
                              company_id=self.ids['ZUMA'], opening=123456, opening_date=base.FROZEN_TODAY,
                              created_by=self.admin_id)
            s.add(account)
            s.flush()
            aid = account.id
        TelegramGroupCode.__table__.drop(engine)
        self.assertNotIn('telegram_group_codes', inspect(engine).get_table_names())
        initialize()
        initialize()
        self.assertIn('telegram_group_codes', inspect(engine).get_table_names())
        with unit() as s:
            self.assertEqual(s.get(TelegramGroup, CHAT).title, 'Legacy group')
            self.assertEqual(s.get(Account, aid).opening, 123456)
            self.assertEqual(s.get(User, self.admin_id).password_hash, base.HASH)
        code = self.code()
        self.assertEqual(self.consume(code).status_code, 200)


if __name__ == '__main__':
    unittest.main()
