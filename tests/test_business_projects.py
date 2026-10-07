"""Company isolation and the complete folder-to-two-reports workflow, synthetic data only."""
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote
from uuid import uuid4
from zipfile import ZipFile

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

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from app.db import (Base, BusinessGeneration, BusinessProject, BusinessSourceFile, Company,
                    CompanyUser, Ledger, ReportArchive, ReportArchiveFile, TelegramLink,
                    User, engine, initialize, unit)
from app.main import app
from app.security import hash_password

PASSWORD = 'SyntheticProjectTests_7731!'
HASH = hash_password(PASSWORD)
BOT = ('synthetic-project-bot', 'private-test-only-'+'x'*35)


def model():
    return {'title': 'Synthetic medicine production', 'currency': 'USD', 'start': '2027-01-01', 'months': 3,
            'tax_rate': 0.15, 'discount_rate': 0.12, 'opening_cash': 500, 'opening_receivables': 0,
            'opening_inventory': 0, 'opening_payables': 0, 'initial_investment': 100,
            'receivable_days': 0, 'inventory_days': 0, 'payable_days': 0,
            'products': [{'name': 'Synthetic product', 'unit': 'pack', 'price': 10, 'unit_cost': 3, 'quantities': 100}],
            'fixed_costs': 100, 'assets': [], 'loans': [], 'equity': 0}


def synthetic_source(extension):
    """Native fixture parts include a formula cache, without private data."""
    if extension in ('.xlsx', '.xltx', '.docx', '.zip'):
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as archive:
            if extension == '.zip':
                archive.writestr('folder/source.txt', 'Synthetic source')
            elif extension == '.docx':
                archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
                archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Synthetic source</w:t></w:r></w:p></w:body></w:document>')
            else:
                content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.' + ('template.main+xml' if extension == '.xltx' else 'sheet.main+xml')
                archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/xl/workbook.xml" ContentType="'+content_type+'"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
                archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
                archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Пример" sheetId="1" r:id="rId1"/></sheets></workbook>')
                archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
                archive.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:B2"/><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Образец</t></is></c><c r="B1"><f>2+3</f><v>5</v></c></row></sheetData><mergeCells count="1"><mergeCell ref="A2:B2"/></mergeCells><pageSetup paperSize="9" orientation="portrait" scale="75"/></worksheet>')
                archive.writestr('xl/printerSettings/printerSettings1.bin', b'Synthetic native print settings')
        return stream.getvalue()
    return {'.pdf': b'%PDF-1.4\n% Synthetic source\n%%EOF\n',
            '.png': b'\x89PNG\r\n\x1a\nsynthetic source', '.jpg': b'\xff\xd8\xffsynthetic source',
            '.jpeg': b'\xff\xd8\xffsynthetic source', '.json': b'{"description":"Synthetic source"}',
            '.csv': 'Название,Описание\nПример,Исходник\n'.encode(),
            '.txt': '<script>synthetic source text</script>'.encode()}[extension]


class BusinessProjectTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'BOT_CLIENT_ID': BOT[0], 'BOT_CLIENT_SECRET': BOT[1]})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        Base.metadata.drop_all(engine)
        initialize()
        with unit(True) as s:
            self.cid = s.scalar(select(Company.id).where(Company.code == 'UZGERMED'))
            self.other = s.scalar(select(Company.id).where(Company.code == 'ZUMA'))
            owner = User(username='project_owner', name='Project Owner', role='admin', password_hash=HASH)
            s.add(owner)
            s.flush()
            self.uid = owner.id
            for cid in (self.cid, self.other):
                s.add(CompanyUser(company_id=cid, user_id=owner.id, role='finance'))
            s.add(TelegramLink(user_id=owner.id, telegram_user_id=88101))
        self.clients = []
        self.client, self.headers = self.login('project_owner')

    def tearDown(self):
        for client in self.clients:
            client.close()

    @classmethod
    def tearDownClass(cls):
        engine.dispose()
        TMP.cleanup()

    def login(self, username):
        client = TestClient(app)
        self.clients.append(client)
        result = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(result.status_code, 200, result.text)
        return client, {'X-CSRF-Token': result.json()['csrf'], 'X-Company-ID': str(self.cid)}

    def actor(self, name, role, company=None):
        with unit(True) as s:
            user = User(username=name, name=name, role=role, password_hash=HASH)
            s.add(user)
            s.flush()
            s.add(CompanyUser(company_id=company or self.cid, user_id=user.id, role=role))
        return self.login(name)

    def create(self):
        body = {'title': 'Synthetic project folder', 'company_id': self.cid, 'request_key': str(uuid4())}
        response = self.client.post('/api/business-projects', json=body, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), body

    def upload(self, project, content=None, path='inputs/project.json', client=None, headers=None, revision=None):
        raw = json.dumps(model()).encode() if content is None else content
        return (client or self.client).put(f"/api/business-projects/{project['id']}/sources",
            params={'company_id': self.cid, 'expected_revision': project['revision'] if revision is None else revision},
            headers={**(headers or self.headers), 'X-Filename': quote(path.split('/')[-1]), 'X-Relative-Path': quote(path)},
            content=raw)

    def analyse(self, project, overrides=None):
        return self.client.post(f"/api/business-projects/{project['id']}/analyse", params={'company_id': self.cid},
                                headers=self.headers, json={'revision': project['revision'], 'overrides': overrides or {}})

    def ready(self):
        project, _ = self.create()
        response = self.upload(project)
        self.assertEqual(response.status_code, 200, response.text)
        response = self.analyse(response.json())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'ready', response.text)
        return response.json()

    def generate(self, project, inputs=None, confirmed=False):
        return self.client.post(f"/api/business-projects/{project['id']}/generate", params={'company_id': self.cid},
                                headers=self.headers, json={'revision': project['revision'], 'inputs': inputs or model(),
                                                           'confirm_sources': confirmed})

    def test_folder_automatically_calculates_two_private_report_pairs_without_ledger(self):
        project = self.ready()
        self.assertEqual(len(project['generations']), 1)
        generation = project['generations'][0]
        business, teo = generation['business_archive'], generation['teo_archive']
        self.assertNotEqual(business['id'], teo['id'])
        self.assertEqual(business['period_end'], '2027-03-31')
        for archive in (business, teo):
            for fmt in ('pdf', 'xlsx'):
                response = self.client.get(f"/api/bot/v1/report-archives/{archive['id']}/files/{fmt}",
                    params={'company_id': self.cid, 'telegram_user_id': 88101}, auth=BOT)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.content.startswith(b'%PDF-' if fmt == 'pdf' else b'PK'))
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 4)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)), 0)
        engine.dispose()
        again = self.client.get(f"/api/business-projects/{project['id']}", params={'company_id': self.cid})
        self.assertEqual(again.json()['generations'][0]['id'], generation['id'])

    def test_missing_inputs_are_not_zero_and_completed_parameters_generate(self):
        project, _ = self.create()
        partial = model()
        del partial['opening_cash']
        del partial['discount_rate']
        project = self.upload(project, json.dumps(partial).encode()).json()
        result = self.analyse(project)
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual(body['status'], 'needs_data')
        self.assertEqual(body['generations'], [])
        self.assertNotIn('opening_cash', body['inputs'])
        self.assertTrue(any(i['field'] == 'opening_cash' for i in body['validation']))
        self.assertEqual(self.generate(body).json()['status'], 'ready')

    def test_idempotent_retries_and_stale_revision_cannot_duplicate_or_overwrite(self):
        project, body = self.create()
        again = self.client.post('/api/business-projects', json=body, headers=self.headers)
        self.assertEqual(again.json()['id'], project['id'])
        updated = self.upload(project).json()
        self.assertEqual(self.upload(project).json()['revision'], updated['revision'])
        changed = json.dumps({**model(), 'opening_cash': 999}).encode()
        self.assertEqual(self.upload(project, changed).status_code, 409)
        self.assertEqual(self.analyse(project).status_code, 409)
        ready = self.analyse(updated).json()
        self.assertEqual(self.generate(ready).json()['generations'][0]['id'], ready['generations'][0]['id'])
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(BusinessSourceFile)), 1)
            self.assertEqual(s.scalar(select(func.count()).select_from(BusinessGeneration)), 1)

    def test_changed_source_invalidates_parameters_preserves_prior_files_and_creates_new_pair(self):
        ready = self.ready()
        changed = model()
        changed['products'][0]['price'] = 20
        updated = self.upload(ready, json.dumps(changed).encode()).json()
        self.assertEqual(updated['status'], 'draft')
        self.assertEqual(updated['inputs'], {})
        new = self.analyse(updated).json()
        self.assertEqual(new['status'], 'ready')
        self.assertEqual(len(new['generations']), 2)
        self.assertNotEqual(new['generations'][0]['business_archive']['id'], ready['generations'][0]['business_archive']['id'])

    def test_source_conflicts_need_explicit_parameter_confirmation(self):
        project, _ = self.create()
        project = self.upload(project).json()
        extraction = {'inputs': model(), 'evidence': [], 'issues': [{'field': 'price', 'message': 'Two conflicting sources',
                                                                   'requires_confirmation': True}], 'documents': []}
        with patch('app.business_sources.extract_sources', return_value=extraction):
            reviewed = self.analyse(project).json()
        self.assertEqual(reviewed['status'], 'needs_data')
        self.assertEqual(self.generate(reviewed).json()['status'], 'needs_data')
        ready = self.generate(reviewed, confirmed=True).json()
        self.assertEqual(ready['status'], 'ready')
        self.assertTrue(ready['extraction']['parameters_confirmed'])

    def test_supplied_calendar_and_completed_values_replace_stale_parser_warnings(self):
        project, _ = self.create()
        project = self.upload(project).json()
        partial = model()
        del partial['start']
        del partial['opening_cash']
        extraction = {'inputs': partial, 'evidence': [], 'documents': [], 'issues': [
            {'field': 'start', 'code': 'forecast_start', 'message': 'Choose the forecast month',
             'requires_confirmation': True},
            {'field': 'opening_cash', 'code': 'missing_or_invalid', 'message': 'Original cash is missing',
             'severity': 'error'},
            {'field': 'products.price', 'code': 'price_factor_vat', 'message': 'Confirm the price basis',
             'requires_confirmation': True}]}
        with patch('app.business_sources.extract_sources', return_value=extraction):
            reviewed = self.analyse(project, {'start': model()['start']}).json()
        self.assertEqual(reviewed['status'], 'needs_data')
        self.assertEqual(reviewed['inputs']['start'], model()['start'])
        self.assertEqual(sum(i['field'] == 'opening_cash' for i in reviewed['validation']), 1)
        self.assertFalse(any(i['field'] == 'start' for i in reviewed['validation']))
        self.assertEqual([i['code'] for i in reviewed['extraction']['issues']], ['price_factor_vat'])
        # A resolved date does not implicitly confirm a conflicting financial source.
        completed = self.generate(reviewed).json()
        self.assertEqual(completed['status'], 'needs_data')
        self.assertEqual([i['code'] for i in completed['validation']], ['source_issues'])
        self.assertEqual(completed['validation'][0]['source_code'], 'price_factor_vat')
        self.assertEqual(self.generate(completed, confirmed=True).json()['status'], 'ready')

        with patch('app.business_sources.extract_sources', return_value=extraction):
            invalid = self.analyse(project, {'start': '2027-01-02'}).json()
        self.assertTrue(any(i.get('code') == 'forecast_start' for i in invalid['extraction']['issues']))
        self.assertTrue(any(i['field'] == 'start' for i in invalid['validation']))

    def test_anonymous_foreign_company_and_non_owner_cannot_change_source(self):
        project = self.ready()
        anonymous = TestClient(app)
        self.clients.append(anonymous)
        self.assertEqual(anonymous.get('/api/business-projects', params={'company_id': self.cid}).status_code, 401)
        self.assertEqual(self.client.get(f"/api/business-projects/{project['id']}",
                                       params={'company_id': self.other}).status_code, 404)
        peer, headers = self.actor('project_peer', 'finance')
        view = peer.get(f"/api/business-projects/{project['id']}", params={'company_id': self.cid}).json()
        self.assertFalse(view['can_edit'])
        self.assertEqual(view['inputs'], {})
        self.assertEqual(view['extraction'], {})
        self.assertEqual(self.upload(project, client=peer, headers=headers).status_code, 403)
        investor, headers = self.actor('project_reader', 'investor')
        self.assertFalse(investor.get('/api/business-projects', params={'company_id': self.cid}).json()['can_upload'])
        self.assertEqual(self.upload(project, client=investor, headers=headers).status_code, 403)
        foreign, _ = self.actor('project_foreign', 'finance', self.other)
        self.assertEqual(foreign.get(f"/api/business-projects/{project['id']}",
                                    params={'company_id': self.cid}).status_code, 403)

    def test_native_sources_download_as_exact_original_bytes_with_utf8_attachment_names(self):
        project, _ = self.create()
        expected_mimes = {'.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                          '.xltx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.template',
                          '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                          '.pdf': 'application/pdf', '.csv': 'text/csv', '.txt': 'text/plain',
                          '.json': 'application/json', '.zip': 'application/zip',
                          '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}
        for extension, media_type in expected_mimes.items():
            with self.subTest(extension=extension):
                raw = synthetic_source(extension)
                filename = 'Образец "исходник"' + extension
                uploaded = self.upload(project, raw, 'Папка/' + filename)
                self.assertEqual(uploaded.status_code, 200, uploaded.text)
                project = uploaded.json()
                metadata = next(f for f in project['files'] if f['filename'] == filename)
                self.assertEqual(metadata['sha256'], hashlib.sha256(raw).hexdigest())
                self.assertNotIn('content', metadata)
                self.assertEqual(metadata['download_url'], f"/api/business-projects/{project['id']}/sources/{metadata['id']}/download?company_id={self.cid}")
                response = self.client.get(metadata['download_url'])
                self.assertEqual(response.status_code, 200, response.text[:100])
                self.assertEqual(response.content, raw)
                self.assertEqual(hashlib.sha256(response.content).hexdigest(), metadata['sha256'])
                self.assertEqual(response.headers['Content-Type'].split(';')[0], media_type)
                self.assertEqual(response.headers['Content-Disposition'], 'attachment; filename="source'+extension+'"; filename*=UTF-8\'\''+quote(filename, safe=''))
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
                if extension == '.xlsx':
                    with ZipFile(io.BytesIO(response.content)) as archive:
                        self.assertIn(b'<f>2+3</f><v>5</v>', archive.read('xl/worksheets/sheet1.xml'))
                        self.assertEqual(archive.read('xl/printerSettings/printerSettings1.bin'), b'Synthetic native print settings')
        again = self.client.get(f"/api/business-projects/{project['id']}", params={'company_id': self.cid}).json()
        self.assertEqual(again['revision'], project['revision'])
        self.assertEqual(again['generations'], [])

    def test_source_download_requires_owner_import_right_and_authenticated_session(self):
        project, _ = self.create()
        project = self.upload(project).json()
        url = project['files'][0]['download_url']
        anonymous = TestClient(app)
        self.clients.append(anonymous)
        self.assertEqual(anonymous.get(url).status_code, 401)
        peer, _ = self.actor('source_peer', 'finance')
        self.assertEqual(peer.get(url).status_code, 403)
        peer_detail = peer.get(f"/api/business-projects/{project['id']}", params={'company_id': self.cid}).json()
        self.assertTrue(all('download_url' not in f for f in peer_detail['files']))
        with unit(True) as s:
            s.get(CompanyUser, (self.cid, self.uid)).role = 'investor'
        self.assertEqual(self.client.get(url).status_code, 403)
        owner_detail = self.client.get(f"/api/business-projects/{project['id']}", params={'company_id': self.cid}).json()
        self.assertFalse(owner_detail['can_edit'])
        self.assertTrue(all('download_url' not in f for f in owner_detail['files']))

    def test_source_download_checks_company_and_project_before_ownership(self):
        first, _ = self.create()
        first = self.upload(first).json()
        second, _ = self.create()
        second = self.upload(second).json()
        source = first['files'][0]
        wrong_project_url = f"/api/business-projects/{second['id']}/sources/{source['id']}/download?company_id={self.cid}"
        self.assertEqual(self.client.get(wrong_project_url).status_code, 404)
        peer, _ = self.actor('wrong_project_peer', 'finance')
        self.assertEqual(peer.get(wrong_project_url).status_code, 404)
        wrong_company_url = f"/api/business-projects/{first['id']}/sources/{source['id']}/download?company_id={self.other}"
        self.assertEqual(self.client.get(wrong_company_url, headers={'X-Company-ID': str(self.other)}).status_code, 404)
        missing_url = f"/api/business-projects/{first['id']}/sources/{source['id']+9999}/download?company_id={self.cid}"
        self.assertEqual(self.client.get(missing_url).status_code, 404)
        with unit(True) as s:
            s.get(BusinessSourceFile, source['id']).company_id = self.other
        self.assertEqual(self.client.get(source['download_url']).status_code, 404)
        detail = self.client.get(f"/api/business-projects/{first['id']}", params={'company_id': self.cid}).json()
        self.assertTrue(all('download_url' not in f for f in detail['files']))

    def test_access_and_revision_are_rechecked_after_rendering(self):
        project, _ = self.create()
        project = self.upload(project).json()
        from app.business_exports import build_documents
        def revoke(*args):
            output = build_documents(*args)
            with unit(True) as s:
                s.get(CompanyUser, (self.cid, self.uid)).role = 'investor'
            return output
        with patch('app.business_exports.build_documents', side_effect=revoke):
            self.assertEqual(self.analyse(project).status_code, 403)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(BusinessGeneration)), 0)
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 0)

    def test_safe_zip_import_and_traversal_macros_invalid_content_are_rejected(self):
        project, _ = self.create()
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as archive:
            archive.writestr('folder/project.json', json.dumps(model()))
        updated = self.upload(project, stream.getvalue(), 'folder/sources.zip')
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(self.analyse(updated.json()).json()['status'], 'ready')
        for path, raw in (('../outside.json', b'{}'), ('folder\\outside.json', b'{}'),
                          ('folder/fake.pdf', b'not pdf'), ('folder/bad.json', b'{"price":NaN}')):
            result = self.upload(updated.json(), raw, path)
            self.assertEqual(result.status_code, 422, result.text)
        bad = io.BytesIO()
        with ZipFile(bad, 'w') as archive:
            archive.writestr('../outside.json', '{}')
        self.assertEqual(self.upload(updated.json(), bad.getvalue(), 'bad.zip').status_code, 422)
        nested = io.BytesIO()
        with ZipFile(nested, 'w') as archive:
            archive.writestr('another.zip', stream.getvalue())
        self.assertEqual(self.upload(updated.json(), nested.getvalue(), 'nested.zip').status_code, 422)

    def test_template_is_authenticated_and_unconfirmed_numbers_are_not_exported(self):
        result = self.client.get('/api/business-projects/template.xlsx', params={'company_id': self.cid})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.content.startswith(b'PK'))
        reader, _ = self.actor('template_reader', 'investor')
        self.assertEqual(reader.get('/api/business-projects/template.xlsx', params={'company_id': self.cid}).status_code, 403)

    def test_previous_fingerprint_can_be_restored_after_an_incomplete_edit(self):
        ready = self.ready()
        partial = model()
        del partial['tax_rate']
        self.assertEqual(self.generate(ready, partial).json()['status'], 'needs_data')
        restored = self.generate(ready).json()
        self.assertEqual(restored['status'], 'ready')
        self.assertEqual(restored['generations'][0]['id'], ready['generations'][0]['id'])

    def test_restored_parameters_select_the_matching_reports_after_another_calculation(self):
        first = self.ready()
        original_id = first['current_generation_id']
        changed = model()
        changed['products'][0]['price'] = '20'
        second = self.generate(first, changed).json()
        self.assertNotEqual(second['current_generation_id'], original_id)
        restored = self.generate(second, model()).json()
        self.assertEqual(restored['status'], 'ready')
        self.assertEqual(restored['current_generation_id'], original_id)
        self.assertEqual(len(restored['generations']), 2)
        self.assertEqual(restored['inputs'], model())

    def test_precision_error_keeps_inputs_and_publishes_no_mismatched_reports(self):
        project, _ = self.create()
        inputs = model()
        inputs['products'][0].update(price='1000000000000000', unit_cost='999999999999999',
                                     quantities='1000000000000000')
        project = self.upload(project, json.dumps(inputs).encode()).json()
        response = self.analyse(project)
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()
        self.assertEqual(detail['status'], 'needs_data')
        self.assertEqual(detail['inputs'], inputs)
        self.assertEqual(detail['generations'], [])
        self.assertTrue(any(i['field'] == 'precision' for i in detail['validation']))
        self.assertEqual(self.generate(detail, inputs, confirmed=True).json()['status'], 'needs_data')
        self.assertEqual(self.generate(detail, model(), confirmed=True).json()['status'], 'ready')


if __name__ == '__main__':
    unittest.main()
