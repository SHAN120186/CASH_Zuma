"""HTTP logging stays durable without serializing unrelated financial work."""
import asyncio
import os
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

import tests.test_site as base
from app.db import Account, Audit, User, engine, unit
from app.http_audit import persist_http_audit


class HttpAuditTests(base.SiteTests):
    def http_rows(self):
        with unit() as session:
            return list(session.scalars(select(Audit).where(Audit.entity == 'http').order_by(Audit.id)))

    def test_persistence_commits_one_append_without_a_financial_guard_query(self):
        with unit() as session:
            user_id = session.scalar(select(User.id).where(User.username == 'admin'))
        statements = []

        def counter(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement.lower())

        event.listen(engine, 'before_cursor_execute', counter)
        try:
            persist_http_audit(dict(company_id=self.company, user_id=user_id, action='API GET',
                                    entity='http', detail='/api/audit-test; status=200',
                                    ip='198.51.100.7', forwarded_for='198.51.100.7'))
        finally:
            event.remove(engine, 'before_cursor_execute', counter)
        self.assertTrue(any('insert into audit_log' in sql for sql in statements), statements)
        self.assertFalse(any('write_guard' in sql or 'begin immediate' in sql for sql in statements), statements)
        # A new independent session must see the committed row when the helper returns.
        row = self.http_rows()[-1]
        self.assertEqual((row.company_id, row.user_id, row.action, row.entity, row.detail),
                         (self.company, user_id, 'API GET', 'http', '/api/audit-test; status=200'))
        self.assertEqual((row.ip, row.forwarded_for), ('198.51.100.7', '198.51.100.7'))
        self.assertIsNotNone(row.created_at)

    def test_authenticated_get_keeps_company_user_status_proxy_and_redacts_query(self):
        forwarded = '198.51.100.17, 203.0.113.9'
        headers = {**self.h, 'X-Company-ID': str(self.company), 'X-Forwarded-For': forwarded}
        before = len(self.http_rows())
        with patch.dict(os.environ, {'UZGERMED_HOSTING': 'render'}):
            response = self.client.get('/api/requests?q=private-query-marker', headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        rows = self.http_rows()
        self.assertEqual(len(rows), before + 1)
        row = rows[-1]
        self.assertEqual(row.company_id, self.company)
        with unit() as session:
            self.assertEqual(row.user_id, session.scalar(select(User.id).where(User.username == 'admin')))
        self.assertEqual((row.action, row.entity, row.detail), ('API GET', 'http', '/api/requests; status=200'))
        self.assertEqual((row.ip, row.forwarded_for), ('203.0.113.9', forwarded))
        self.assertNotIn('private-query-marker', row.detail)
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_authenticated_denial_is_logged_but_anonymous_calls_are_not(self):
        employee, headers = self.make_user('employee', 'http_audit_employee')
        try:
            before = len(self.http_rows())
            response = employee.get('/api/accounts', headers=headers)
            self.assertEqual(response.status_code, 403, response.text)
            rows = self.http_rows()
            self.assertEqual(len(rows), before + 1)
            self.assertEqual(rows[-1].detail, '/api/accounts; status=403')
            self.assertEqual(rows[-1].company_id, self.company)
            with unit() as session:
                self.assertEqual(rows[-1].user_id, session.scalar(select(User.id).where(User.username == 'http_audit_employee')))
        finally:
            employee.close()
        before = len(self.http_rows())
        anonymous = TestClient(base.app)
        try:
            self.assertEqual(anonymous.get('/api/me').status_code, 401)
            self.assertEqual(anonymous.get('/api/no-such-route').status_code, 404)
        finally:
            anonymous.close()
        self.assertEqual(len(self.http_rows()), before)

    def test_secondary_audit_outage_does_not_mask_committed_business_write(self):
        error = OperationalError('private SQL', {}, Exception('private connection information'))
        before_http = len(self.http_rows())
        with patch('app.main.persist_http_audit', side_effect=error), self.assertLogs(level='ERROR') as logged:
            response = self.post('/api/accounts', {'name': 'Synthetic audit outage account', 'kind': 'bank',
                                                  'currency': 'UZS', 'opening': '10', 'opening_date': self.date})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertNotIn('private', response.text)
        self.assertTrue(any('Could not persist HTTP audit' in line for line in logged.output))
        self.assertFalse(any('private connection information' in line or 'private SQL' in line for line in logged.output))
        self.assertEqual(len(self.http_rows()), before_http)
        with unit() as session:
            account = session.get(Account, response.json()['id'])
            self.assertEqual((account.name, account.opening), ('Synthetic audit outage account', 1000))
            business = list(session.scalars(select(Audit).where(Audit.entity == 'account',
                            Audit.entity_id == str(account.id), Audit.action == 'Создан счёт / касса')))
            self.assertEqual(len(business), 1, 'the guarded business audit must commit with the account')

    def test_authenticated_bot_http_path_remains_redacted(self):
        synthetic_id = 77112233
        credentials = ('audit-test-bot', 'SyntheticAuditBotOnly_' + 'x' * 32)
        before = len(self.http_rows())
        with patch.dict(os.environ, {'BOT_CLIENT_ID': credentials[0], 'BOT_CLIENT_SECRET': credentials[1]}):
            response = self.client.get('/api/bot/v1/telegram-users/' + str(synthetic_id), auth=credentials)
        self.assertEqual(response.status_code, 200, response.text)
        rows = self.http_rows()
        self.assertEqual(len(rows), before + 1)
        row = rows[-1]
        self.assertEqual(row.detail, '/api/bot/v1/telegram-users/{telegram_user_id}; status=200')
        self.assertIsNone(row.user_id)
        self.assertIsNone(row.company_id)
        self.assertNotIn(str(synthetic_id), row.detail)

    def test_slow_audit_commit_leaves_event_loop_free_and_response_waits_for_it(self):
        entered, release = threading.Event(), threading.Event()
        worker_ids = []
        loop_ids = []
        before = len(self.http_rows())

        def slow_persist(payload):
            worker_ids.append(threading.get_ident())
            entered.set()
            if not release.wait(8):
                raise AssertionError('The event loop could not release the audit worker')
            persist_http_audit(payload)

        async def concurrent_requests():
            loop_ids.append(threading.get_ident())
            transport = httpx.ASGITransport(app=base.app)
            async with httpx.AsyncClient(transport=transport, base_url='http://testserver',
                                         cookies=self.client.cookies) as client:
                audited = asyncio.create_task(client.get('/api/me'))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                    health = await asyncio.wait_for(client.get('/health'), timeout=2)
                    self.assertEqual(health.status_code, 200)
                    self.assertFalse(audited.done(), 'an authenticated response must wait for its audit commit')
                finally:
                    release.set()
                response = await asyncio.wait_for(audited, timeout=3)
                self.assertEqual(response.status_code, 200)

        with patch('app.main.persist_http_audit', side_effect=slow_persist):
            asyncio.run(concurrent_requests())
        self.assertEqual(len(worker_ids), 1)
        self.assertNotEqual(worker_ids[0], loop_ids[0], 'database work must not run on the event-loop thread')
        rows = self.http_rows()
        self.assertEqual(len(rows), before + 1)
        self.assertEqual(rows[-1].detail, '/api/me; status=200')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(HttpAuditTests(name) for name in HttpAuditTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    base.tearDownModule()


if __name__ == '__main__':
    unittest.main()
