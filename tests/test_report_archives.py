"""Prepared report archives: temporary data only, private bytes and live access checks."""
import io
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = TMP.name
os.environ['ALLOWED_HOSTS'] = 'testserver,localhost,127.0.0.1'
os.environ['COOKIE_SECURE'] = '0'
os.environ['PUBLIC_ORIGIN'] = ''
os.environ.pop('DATABASE_URL', None)
if os.getenv('TEST_DATABASE_URL'):
    from sqlalchemy.engine import make_url
    if not (make_url(os.environ['TEST_DATABASE_URL']).database or '').startswith('zuma_test_'):
        raise RuntimeError('Test database must start with zuma_test_')
    os.environ['DATABASE_URL'] = os.environ['TEST_DATABASE_URL']
BOT = ('archive-test-bot', 'test-only-archive-secret-' + 'x' * 32)

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import delete, func, select
from app import clock
from app.db import (Base, Company, CompanyUser, Ledger, ReportArchive, ReportArchiveFile,
                    TelegramLink, User, engine, initialize, unit)
from app.main import app
from app.report_archives import valid_file
from app.security import hash_password

PASSWORD = 'TemporaryArchiveTests_9831!'
HASH = hash_password(PASSWORD)
PDF = b'%PDF-1.4\nsynthetic private report\n%%EOF\n'


def spreadsheet():
    book = Workbook()
    book.active['A1'] = 'Synthetic report'
    book.active['A2'] = '=1+2'
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


XLSX = spreadsheet()


def tearDownModule():
    engine.dispose()
    # Keep the temporary DB alive during combined discovery, like test_bot_api.


class ReportArchiveTests(unittest.TestCase):
    def setUp(self):
        self.bot_environment = patch.dict(os.environ, {
            'BOT_CLIENT_ID': BOT[0], 'BOT_CLIENT_SECRET': BOT[1], 'BOT_REPORT_GROUPS': ''})
        self.bot_environment.start()
        self.addCleanup(self.bot_environment.stop)
        clock.FROZEN = date(2026, 10, 7)
        Base.metadata.drop_all(engine)
        initialize()
        with unit(True) as s:
            self.companies = {c.code: c.id for c in s.scalars(select(Company))}
            user = User(username='archive_admin', name='Archive Admin', role='admin', password_hash=HASH)
            s.add(user)
            s.flush()
            self.owner = user.id
            for code in ('UZGERMED', 'ZUMA'):
                s.add(CompanyUser(company_id=self.companies[code], user_id=user.id, role='finance'))
            s.add(TelegramLink(user_id=user.id, telegram_user_id=10001))
        self.clients = []
        self.client, self.headers = self.login('archive_admin')
        self.cid = self.companies['UZGERMED']

    def tearDown(self):
        for client in self.clients:
            client.close()
        clock.FROZEN = None

    def login(self, username):
        client = TestClient(app)
        self.clients.append(client)
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        return client, {'X-CSRF-Token': response.json()['csrf']}

    def user(self, username, role='finance', company=None, telegram_id=10002):
        with unit(True) as s:
            user = User(username=username, name=username, role=role, password_hash=HASH)
            s.add(user)
            s.flush()
            s.add(CompanyUser(company_id=company or self.cid, user_id=user.id, role=role))
            s.add(TelegramLink(user_id=user.id, telegram_user_id=telegram_id))
            uid = user.id
        client, headers = self.login(username)
        return uid, client, headers

    def body(self, company=None, **updates):
        body = {'company_id': company or self.cid, 'title': 'UZGERMED synthetic report',
                'period_start': '2026-01-01', 'period_end': '2028-12-31', 'request_key': str(uuid4())}
        body.update(updates)
        return body

    def create(self, body=None, client=None, headers=None, bot=False, telegram_id=10001):
        body = body or self.body()
        if bot:
            return self.client.post('/api/bot/v1/report-archives', params={'telegram_user_id': telegram_id}, json=body, auth=BOT)
        return (client or self.client).post('/api/report-archives', json=body,
                                          headers={**(headers or self.headers), 'X-Company-ID': str(body['company_id'])})

    def upload(self, rid, format, content=None, name=None, company=None, bot=False, telegram_id=10001,
               client=None, headers=None):
        content = PDF if content is None and format == 'pdf' else XLSX if content is None else content
        name = name or 'Отчёт.' + format
        params = {'company_id': company or self.cid}
        path = f'/api/report-archives/{rid}/files/{format}'
        extra = {'X-Filename': quote(name), 'Content-Type': 'application/octet-stream'}
        if bot:
            path = '/api/bot/v1' + path.removeprefix('/api')
            params['telegram_user_id'] = telegram_id
            return self.client.put(path, params=params, content=content, headers=extra, auth=BOT)
        return (client or self.client).put(path, params=params, content=content,
            headers={**(headers or self.headers), **extra, 'X-Company-ID': str(company or self.cid)})

    def ready(self, company=None, bot=False):
        response = self.create(self.body(company), bot=bot)
        self.assertEqual(response.status_code, 200, response.text)
        rid = response.json()['id']
        for format in ('pdf', 'xlsx'):
            response = self.upload(rid, format, company=company, bot=bot)
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'ready')
        return rid

    def listing(self, bot=False, telegram_id=10001, client=None, **params):
        params = {'company_id': self.cid, **params}
        path = '/api/bot/v1/report-archives' if bot else '/api/report-archives'
        if bot:
            return self.client.get(path, params={**params, 'telegram_user_id': telegram_id}, auth=BOT)
        return (client or self.client).get(path, params=params)

    def download(self, rid, format='pdf', company=None, bot=False, telegram_id=10001, client=None):
        path = f'/api/report-archives/{rid}/files/{format}'
        params = {'company_id': company or self.cid}
        if bot:
            return self.client.get('/api/bot/v1' + path.removeprefix('/api'),
                                   params={**params, 'telegram_user_id': telegram_id}, auth=BOT)
        return (client or self.client).get(path, params=params)

    def remove(self, record, client=None, headers=None, company=None, **params):
        company = company or self.cid
        return (client or self.client).delete(f"/api/report-archives/{record['id']}",
            params={'company_id': company, 'expected_version': record['version'], **params},
            headers={**(headers or self.headers), 'X-Company-ID': str(company)})

    def restore(self, record, client=None, headers=None, company=None, **params):
        company = company or self.cid
        return (client or self.client).post(f"/api/report-archives/{record['id']}/restore",
            params={'company_id': company, 'expected_version': record['version'], **params},
            headers={**(headers or self.headers), 'X-Company-ID': str(company)})

    def test_delete_restore_hides_report_from_site_and_bot_preserves_version_date_bytes_and_ledger(self):
        rid = self.ready()
        record = self.listing().json()['items'][0]
        self.assertTrue(record['can_delete'])
        deleted = self.remove(record)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()['status'], 'deleted')
        self.assertFalse(deleted.json()['can_download'])
        self.assertTrue(deleted.json()['can_restore'])
        self.assertEqual(self.listing().json()['items'], [])
        for params in ({}, {'include_drafts': True}, {'include_deleted': True, 'include_drafts': True}):
            self.assertEqual(self.listing(bot=True, **params).json()['items'], [])
        self.assertEqual([r['id'] for r in self.listing(include_deleted=True).json()['items']], [rid])
        for bot in (False, True):
            self.assertEqual(self.download(rid, bot=bot).status_code, 404)
            self.assertEqual(self.upload(rid, 'pdf', bot=bot).status_code, 409)
        self.assertEqual(self.remove(record).status_code, 200)
        restored = self.restore(deleted.json())
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()['status'], 'ready')
        self.assertEqual(restored.json()['version'], record['version'])
        self.assertEqual(restored.json()['uploaded_at'], record['uploaded_at'])
        for format, content in (('pdf', PDF), ('xlsx', XLSX)):
            self.assertEqual(self.download(rid, format, bot=True).content, content)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 2)
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchive)), 1)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)), 0)

    def test_deleted_partial_archive_restores_draft_without_publishing_and_version_is_not_reused(self):
        record = self.create().json()
        record = self.upload(record['id'], 'pdf').json()
        deleted = self.remove(record).json()
        self.assertEqual(self.create().json()['version'], record['version'] + 1)
        restored = self.restore(deleted)
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()['status'], 'draft')
        self.assertEqual(restored.json()['formats'], ['pdf'])
        self.assertTrue(restored.json()['can_upload'])
        self.assertEqual(self.download(record['id'], bot=True).status_code, 404)
        self.assertEqual(self.upload(record['id'], 'xlsx').json()['status'], 'ready')

    def test_delete_restore_and_trash_are_author_import_company_and_version_scoped(self):
        rid = self.ready()
        record = self.listing().json()['items'][0]
        _, peer, peer_headers = self.user('archive_delete_peer')
        _, investor, investor_headers = self.user('archive_delete_reader', role='investor', telegram_id=10003)
        for client, headers in ((peer, peer_headers), (investor, investor_headers)):
            self.assertEqual(self.remove(record, client, headers).status_code, 403)
            self.assertEqual(self.restore(record, client, headers).status_code, 403)
        anonymous = TestClient(app)
        self.clients.append(anonymous)
        self.assertEqual(self.remove(record, anonymous).status_code, 401)
        self.assertEqual(self.remove(record, expected_version=record['version']+1).status_code, 409)
        self.assertEqual(self.remove(record, company=self.companies['ZUMA']).status_code, 404)
        deleted = self.remove(record).json()
        for client in (peer, investor):
            self.assertEqual(self.listing(client=client, include_deleted=True).json()['items'], [])
            self.assertEqual(self.download(rid, client=client).status_code, 404)
        self.assertEqual(self.restore(deleted, expected_version=record['version']+1).status_code, 409)
        self.assertEqual(self.restore(deleted, company=self.companies['ZUMA']).status_code, 404)

    def test_delete_then_restore_during_archive_upload_cancels_streamed_write(self):
        record = self.create().json()
        def change_lifecycle(raw, filename, format):
            result = valid_file(raw, filename, format)
            self.assertEqual(self.restore(self.remove(record).json()).status_code, 200)
            return result
        with patch('app.report_archives.valid_file', side_effect=change_lifecycle):
            response = self.upload(record['id'], 'pdf')
        self.assertEqual(response.status_code, 409, response.text)
        with unit() as s:
            self.assertEqual(s.get(ReportArchive, record['id']).status, 'draft')
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 0)

    def test_pair_is_private_until_complete_and_durable_in_database(self):
        record = self.create().json()
        self.assertEqual(record['formats'], [])
        self.assertEqual(self.listing(bot=True).json()['items'], [])
        first = self.upload(record['id'], 'pdf').json()
        self.assertEqual(first['status'], 'draft')
        self.assertEqual(first['formats'], ['pdf'])
        self.assertEqual(self.download(record['id'], bot=True).status_code, 404)
        final = self.upload(record['id'], 'xlsx').json()
        self.assertEqual(final['formats'], ['pdf', 'xlsx'])
        self.assertEqual(final['status'], 'ready')
        self.assertFalse(final['can_upload'])
        # Drop pooled connections to simulate a process reconnect: bytes are not an ephemeral file.
        engine.dispose()
        for format, content in (('pdf', PDF), ('xlsx', XLSX)):
            result = self.download(record['id'], format, bot=True)
            self.assertEqual(result.status_code, 200, result.text[:100])
            self.assertEqual(result.content, content)
            self.assertIn("filename*=UTF-8''", result.headers['Content-Disposition'])
            self.assertEqual(result.headers['Cache-Control'], 'no-store')
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 2)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)), 0)

    def test_service_key_and_active_link_are_required(self):
        rid = self.ready()
        path = f'/api/bot/v1/report-archives/{rid}/files/pdf'
        params = {'company_id': self.cid, 'telegram_user_id': 10001}
        self.assertEqual(self.client.get(path, params=params).status_code, 401)
        self.assertEqual(self.client.get(path, params=params, auth=('wrong', 'wrong')).status_code, 401)
        self.assertEqual(self.client.get(path, params={'company_id': self.cid}, auth=BOT).status_code, 422)
        self.assertEqual(self.download(rid, bot=True, telegram_id=99999).status_code, 403)
        self.assertEqual(self.client.get('/api/bot/v1/report-archive-companies',
                                         params={'telegram_user_id': 99999}, auth=BOT).json(), {'linked': False, 'items': []})
        anonymous = TestClient(app)
        try:
            self.assertEqual(anonymous.get(f'/api/report-archives/{rid}/files/pdf', params={'company_id': self.cid}).status_code, 401)
        finally:
            anonymous.close()

    def test_idempotent_creation_is_tenant_scoped_and_metadata_bound(self):
        body = self.body()
        first = self.create(body).json()
        self.assertEqual(self.create(body).json()['id'], first['id'])
        changed = {**body, 'title': 'Changed'}
        self.assertEqual(self.create(changed).status_code, 409)
        second = self.create({**body, 'company_id': self.companies['ZUMA']}).json()
        self.assertNotEqual(second['id'], first['id'])
        self.assertEqual(second['version'], 1)
        self.assertEqual(self.create().json()['version'], 2)
        _, client, headers = self.user('other_finance')
        self.assertEqual(self.create(body, client, headers).status_code, 409)

    def test_ready_version_is_immutable_but_retry_is_safe(self):
        rid = self.ready(bot=True)
        self.assertEqual(self.upload(rid, 'pdf', bot=True).status_code, 200)
        self.assertEqual(self.upload(rid, 'pdf', content=PDF + b'change', bot=True).status_code, 409)
        self.assertEqual(self.download(rid).content, PDF)

    def test_company_scope_and_selected_company_are_checked_for_every_file(self):
        rid = self.ready()
        other = self.companies['ZUMA']
        self.assertEqual(self.download(rid, company=other, bot=True).status_code, 404)
        self.assertEqual(self.upload(rid, 'pdf', company=other, bot=True).status_code, 404)
        self.assertEqual(self.download(rid, company=other).status_code, 404)
        uid, client, headers = self.user('zuma_finance', company=other)
        self.assertEqual(self.download(rid, bot=True, telegram_id=10002).status_code, 403)
        self.assertEqual(self.download(rid, client=client).status_code, 403)
        self.assertEqual(self.listing(bot=True, telegram_id=10002).status_code, 403)
        self.assertEqual(self.upload(rid, 'pdf', client=client, headers=headers).status_code, 403)

    def test_import_export_permissions_and_owner_only_drafts(self):
        rid = self.create().json()['id']
        self.upload(rid, 'pdf')
        _, finance, headers = self.user('another_finance')
        self.assertEqual(self.listing(client=finance).json()['items'], [])
        self.assertEqual(self.listing(bot=True, telegram_id=10002, include_drafts=True).json()['items'], [])
        self.assertEqual(self.upload(rid, 'xlsx', client=finance, headers=headers).status_code, 403)
        self.assertEqual(self.download(rid, client=finance).status_code, 404)
        self.assertEqual(len(self.listing(bot=True, include_drafts=True).json()['items']), 1)
        _, investor, headers = self.user('report_investor', role='investor', telegram_id=10003)
        self.assertFalse(self.listing(client=investor).json()['can_upload'])
        self.assertEqual(self.create(client=investor, headers=headers).status_code, 403)
        self.assertEqual(self.create(bot=True, telegram_id=10003).status_code, 403)
        ready = self.ready()
        self.assertEqual(self.download(ready, client=investor).status_code, 200)
        _, employee, _ = self.user('report_employee', role='employee', telegram_id=10004)
        self.assertEqual(self.download(ready, client=employee).status_code, 403)
        self.assertEqual(self.download(ready, bot=True, telegram_id=10004).status_code, 403)

    def test_revocation_inactive_company_and_temporary_password_block_download(self):
        rid = self.ready()
        uid, client, _ = self.user('revoked_reader', role='investor')
        self.assertEqual(self.download(rid, bot=True, telegram_id=10002).status_code, 200)
        with unit(True) as s:
            s.execute(delete(CompanyUser).where(CompanyUser.user_id == uid))
        self.assertEqual(self.download(rid, bot=True, telegram_id=10002).status_code, 403)
        self.assertEqual(self.download(rid, client=client).status_code, 403)
        with unit(True) as s:
            s.get(User, self.owner).must_change_password = True
        self.assertEqual(self.download(rid, bot=True).status_code, 403)
        with unit(True) as s:
            s.get(User, self.owner).must_change_password = False
            s.get(Company, self.cid).active = False
        self.assertEqual(self.download(rid, bot=True).status_code, 403)
        self.assertEqual(self.download(rid).status_code, 403)

    def test_access_is_rechecked_after_streaming_before_pair_is_written(self):
        uid, client, headers = self.user('stream_uploader')
        rid = self.create(client=client, headers=headers).json()['id']
        def revoke(raw, filename, format):
            result = valid_file(raw, filename, format)
            with unit(True) as s:
                s.get(CompanyUser, (self.cid, uid)).role = 'investor'
            return result
        with patch('app.report_archives.valid_file', side_effect=revoke):
            response = self.upload(rid, 'pdf', client=client, headers=headers)
        self.assertEqual(response.status_code, 403, response.text)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 0)

    def test_period_overlap_and_upload_day_use_tashkent_boundary(self):
        first = self.ready()
        second = self.ready()
        with unit(True) as s:
            s.get(ReportArchive, first).uploaded_at = datetime(2026, 10, 7, 18, 59, 59)
            s.get(ReportArchive, second).uploaded_at = datetime(2026, 10, 7, 19, 0)
        self.assertEqual([r['id'] for r in self.listing(uploaded_on='2026-10-07').json()['items']], [first])
        self.assertEqual([r['id'] for r in self.listing(bot=True, uploaded_on='2026-10-08').json()['items']], [second])
        self.assertEqual(len(self.listing(date_from='2028-12-31', date_to='2029-02-01').json()['items']), 2)
        self.assertEqual(self.listing(date_from='2029-01-01').json()['items'], [])
        self.assertEqual(self.listing(date_to='2025-12-31').json()['items'], [])
        for params in ({'date_from': '2028-01-01', 'date_to': '2027-01-01'}, {'uploaded_on': '1900-01-01'}):
            self.assertEqual(self.listing(**params).status_code, 422)

    def test_filename_signatures_zip_structure_and_size_are_validated(self):
        rid = self.create().json()['id']
        for content, name, format in ((b'not a PDF', 'fake.pdf', 'pdf'), (PDF, '../secret.pdf', 'pdf'),
                                      (PDF, 'folder\\secret.pdf', 'pdf'), (b'not xlsx', 'fake.xlsx', 'xlsx'),
                                      (PDF, 'wrong.xlsx', 'pdf'), (b'', 'empty.pdf', 'pdf')):
            response = self.upload(rid, format, content, name)
            self.assertEqual(response.status_code, 422, response.text)
        for entry in ('../escape.xml', 'huge.xml', 'xl/vbaProject.bin'):
            stream = io.BytesIO()
            with ZipFile(io.BytesIO(XLSX)) as original, ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
                for item in original.infolist():
                    archive.writestr(item, original.read(item.filename))
                archive.writestr(entry, b'x' * (31 * 1024 * 1024) if entry == 'huge.xml' else b'unsafe')
            self.assertEqual(self.upload(rid, 'xlsx', stream.getvalue()).status_code, 422)
        self.assertEqual(self.upload(rid, 'pdf', b'%PDF-' + b'x' * (20 * 1024 * 1024)).status_code, 413)
        self.assertEqual(self.upload(rid, 'xlsx').status_code, 200)

    def test_metadata_validation_and_bot_company_permissions(self):
        for updates in ({'title': ''}, {'period_end': '2025-12-31'}, {'period_start': '1900-01-01'},
                        {'period_end': '2032-01-01'}, {'request_key': 'not-a-uuid'}):
            self.assertEqual(self.create(self.body(**updates)).status_code, 422)
        response = self.client.get('/api/bot/v1/report-archive-companies', params={'telegram_user_id': 10001}, auth=BOT)
        self.assertEqual(response.status_code, 200, response.text)
        companies = {c['code']: c for c in response.json()['items']}
        self.assertTrue(companies['UZGERMED']['can_upload'])
        self.assertNotIn('UNASSIGNED', companies)
        _, _, _ = self.user('only_reader', role='investor')
        response = self.client.get('/api/bot/v1/report-archive-companies', params={'telegram_user_id': 10002}, auth=BOT)
        self.assertEqual([c['code'] for c in response.json()['items']], ['UZGERMED'])
        self.assertFalse(response.json()['items'][0]['can_upload'])


if __name__ == '__main__':
    unittest.main()
