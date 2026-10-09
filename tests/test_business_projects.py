"""Company isolation and the complete folder-to-two-reports workflow, synthetic data only."""
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
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

sys.path.insert(0, str(ROOT / 'tests'))
from test_native_uzgermed import fixture as native_fixture

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

    def create(self, mode=None):
        body = {'title': 'Synthetic project folder', 'company_id': self.cid, 'request_key': str(uuid4())}
        if mode is not None:
            body['mode'] = mode
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

    def save_draft(self, project, inputs, client=None, headers=None, **params):
        return (client or self.client).put(f"/api/business-projects/{project['id']}/inputs",
            params={'company_id': self.cid, **params}, headers=headers or self.headers,
            json={'revision': project['revision'], 'inputs': inputs})

    def remove(self, project, client=None, headers=None, **params):
        return (client or self.client).delete(f"/api/business-projects/{project['id']}",
            params={'company_id': self.cid, 'expected_revision': project['revision'], **params},
            headers=headers or self.headers)

    def restore(self, project, client=None, headers=None, **params):
        return (client or self.client).post(f"/api/business-projects/{project['id']}/restore",
            params={'company_id': self.cid, 'expected_revision': project['revision'], **params},
            headers=headers or self.headers)

    def native_folder(self, changes=None):
        project, _ = self.create()
        source = native_fixture({('Стоим_проекта', 'B38'): (7, None),
                                 ('ВНД', 'D6'): (14, "'Стоим_проекта'!B38*2"),
                                 **(changes or {})})
        response = self.upload(project, source, 'Synthetic/native.xlsx')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), source

    def native_preview(self, project, overrides=None, client=None, headers=None,
                       data_updates=None, review_decisions=None, source_sha256=None, confirm_source_basis=None, **params):
        body = {'revision': project['revision'], 'overrides': overrides or {}}
        for key, value in {'data_updates': data_updates, 'review_decisions': review_decisions,
                           'source_sha256': source_sha256, 'confirm_source_basis': confirm_source_basis}.items():
            if value is not None:
                body[key] = value
        return (client or self.client).post(f"/api/business-projects/{project['id']}/native/preview",
            params={'company_id': self.cid, **params}, headers=headers or self.headers,
            json=body)

    def native_download(self, project, client=None, headers=None, **params):
        return (client or self.client).get(f"/api/business-projects/{project['id']}/native.xlsx",
            params={'company_id': self.cid, 'revision': project['revision'], **params},
            headers=headers or self.headers)

    def native_generate(self, project, overrides=None, start='2027-01-01', client=None, headers=None,
                        data_updates=None, review_decisions=None, source_sha256=None, confirm_source_basis=None, **params):
        body = {'revision': project['revision'], 'start': start, 'overrides': overrides or {}}
        for key, value in {'data_updates': data_updates, 'review_decisions': review_decisions,
                           'source_sha256': source_sha256, 'confirm_source_basis': confirm_source_basis}.items():
            if value is not None:
                body[key] = value
        return (client or self.client).post(f"/api/business-projects/{project['id']}/native/generate",
            params={'company_id': self.cid, **params}, headers=headers or self.headers,
            json=body)

    def native_complete_folder(self, changes=None):
        project, source = self.native_folder(changes)
        word = synthetic_source('.docx')
        uploaded = self.upload(project, word, 'Synthetic/Бизнес-план.docx')
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        return uploaded.json(), source, word

    def native_renderers(self, word, pdf_side_effect=None):
        stack = ExitStack()
        self.addCleanup(stack.close)
        patched = stack.enter_context(patch('app.native_documents.patch_business_docx', return_value=(word, {})))
        rendered = stack.enter_context(patch('app.native_renderer.docx_to_pdf',
            return_value=b'%PDF-1.4\nSynthetic native business plan\n%%EOF\n', side_effect=pdf_side_effect))
        teo = stack.enter_context(patch('app.native_teo.build_teo_pdf',
            return_value=b'%PDF-1.4\nSynthetic native TEO\n%%EOF\n'))
        return stack, patched, rendered, teo

    def native_review_description(self, values=(8, 9)):
        # A deterministic synthetic source review lets these API tests verify
        # decisions independently of the document parser's own fixture tests.
        return {'items': [{'key': 'Стоим_проекта!B38', 'group': 'Основные условия',
                           'label': 'Курс проекта', 'sheet': 'Стоим_проекта', 'cell': 'B38',
                           'unit': 'UZS/USD', 'value': 7, 'editable': True, 'min': 0.000001,
                           'max': 1000000, 'requires_decision': False,
                           'proposals': [{'id': f'source-{index}', 'value': value, 'source': f'Evidence-{index}.xlsx',
                                          'sheet': 'Параметры', 'cell': 'B2', 'unit': 'UZS/USD', 'period': '2027',
                                          'source_sha256': str(index)*64} for index, value in enumerate(values, 1)]}],
                'warnings': []}

    def test_native_input_review_applies_selected_source_and_preserves_uploaded_original(self):
        project, source = self.native_folder()
        decisions = {'Стоим_проекта!B38': {'choice': 'source', 'proposal_id': 'source-1'}}
        updates = {'Стоим_проекта!B38': 8}
        with patch('app.native_template_inputs.describe_inputs', side_effect=lambda *_: self.native_review_description()):
            initial = self.client.get(f"/api/business-projects/{project['id']}", params={'company_id': self.cid}).json()
            self.assertFalse(initial['native_model']['input_review']['complete'])
            response = self.native_preview(project, data_updates=updates, review_decisions=decisions,
                                           source_sha256=hashlib.sha256(source).hexdigest())
            self.assertEqual(response.status_code, 200, response.text)
            reviewed = response.json()
            profile = reviewed['native_model']
            self.assertTrue(profile['input_review']['complete'])
            self.assertEqual(profile['data_updates'], updates)
            self.assertEqual(profile['review_decisions'], decisions)
            self.assertEqual(profile['source_sha256'], hashlib.sha256(source).hexdigest())
            self.assertEqual(profile['input_review']['items'][0]['original_value'], 7)
            self.assertEqual(profile['input_review']['items'][0]['value'], 8)
            self.assertEqual(profile['overrides']['Стоим_проекта!B38'], 8)
            self.assertEqual(next(m['calculated'] for m in profile['metrics'] if m['key'] == 'ВНД!D6'), 16)
            downloaded = self.native_download(reviewed)
            self.assertEqual(downloaded.status_code, 200, downloaded.text[:100])
            self.assertEqual(self.client.get(project['files'][0]['download_url']).content, source)
            from app.native_workbook import recalculate
            self.assertEqual(recalculate(downloaded.content)[1]['values']['Стоим_проекта']['B38'], 8)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 0)

    def test_native_input_review_rejects_missing_decisions_unknown_keys_and_forged_proposals(self):
        project, _, word = self.native_complete_folder()
        key = 'Стоим_проекта!B38'
        invalid = [
            ({}, {}), ({key: 8}, {}), ({key: 8}, {key: {'choice': 'manual', 'proposal_id': 'source-1'}}),
            ({key: 8}, {key: {'choice': 'keep'}}), ({}, {key: {'choice': 'manual'}}),
            ({key: 8}, {key: {'choice': 'source'}}), ({key: 8}, {key: {'choice': 'source', 'proposal_id': 'forged'}}),
            ({key: 9}, {key: {'choice': 'source', 'proposal_id': 'source-1'}}),
            ({key: True}, {key: {'choice': 'manual'}}), ({key: '8'}, {key: {'choice': 'manual'}}),
            ({key: 1e99}, {key: {'choice': 'manual'}}), ({'ВНД!D6': 100}, {key: {'choice': 'keep'}}),
            ({key: 8}, {'Unknown!A1': {'choice': 'manual'}}), ({key: 8}, {key: {'choice': 'automatic'}}),
        ]
        stack, patched, rendered, teo = self.native_renderers(word)
        with stack, patch('app.native_template_inputs.describe_inputs', side_effect=lambda *_: self.native_review_description()):
            for updates, decisions in invalid:
                with self.subTest(updates=updates, decisions=decisions):
                    self.assertEqual(self.native_preview(project, data_updates=updates, review_decisions=decisions).status_code, 422)
                    self.assertEqual(self.native_generate(project, data_updates=updates, review_decisions=decisions).status_code, 422)
            conflict = self.native_preview(project, {key: 9}, data_updates={key: 8}, review_decisions={key: {'choice': 'manual'}})
            self.assertEqual(conflict.status_code, 422)
            bypass = self.client.post(f"/api/business-projects/{project['id']}/native/preview",
                params={'company_id': self.cid}, headers=self.headers,
                json={'revision': project['revision'], 'review_confirmed': True})
            self.assertEqual(bypass.status_code, 422)
            patched.assert_not_called(); rendered.assert_not_called(); teo.assert_not_called()
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 0)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)), 0)

    def test_native_input_review_decisions_are_immutable_report_provenance_and_retries_are_canonical(self):
        project, source, word = self.native_complete_folder()
        key, updates = 'Стоим_проекта!B38', {'Стоим_проекта!B38': 8}
        source_choice = {key: {'choice': 'source', 'proposal_id': 'source-1'}}
        stack, _, rendered, _ = self.native_renderers(word)
        with stack, patch('app.native_template_inputs.describe_inputs', side_effect=lambda *_: self.native_review_description()):
            response = self.native_generate(project, data_updates=updates, review_decisions=source_choice)
            self.assertEqual(response.status_code, 200, response.text)
            ready = response.json()
            first_id = ready['current_generation_id']
            again = self.native_generate(ready, {key: 8}, data_updates=updates, review_decisions=source_choice)
            self.assertEqual(again.status_code, 200, again.text)
            self.assertEqual(again.json()['current_generation_id'], first_id)
            manual = self.native_generate(again.json(), data_updates=updates, review_decisions={key: {'choice': 'manual'}})
            self.assertEqual(manual.status_code, 200, manual.text)
            self.assertNotEqual(manual.json()['current_generation_id'], first_id)
            self.assertEqual(rendered.call_count, 2)
            self.assertEqual(self.client.get(project['files'][0]['download_url']).content, source)
        with unit() as s:
            inputs = json.loads(s.get(BusinessGeneration, first_id).inputs_json)
            self.assertEqual(inputs['native_data_updates'], updates)
            self.assertEqual(inputs['native_review_decisions'], source_choice)
            self.assertEqual(inputs['native_overrides'], {})
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 8)

    def test_native_input_review_equal_duplicate_sources_require_an_explicit_keep(self):
        project, _ = self.native_folder()
        key = 'Стоим_проекта!B38'
        with patch('app.native_template_inputs.describe_inputs', side_effect=lambda *_: self.native_review_description((7, 7))):
            self.assertEqual(self.native_preview(project).status_code, 422)
            response = self.native_preview(project, review_decisions={key: {'choice': 'keep'}})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()['native_model']['input_review']['complete'])
            self.assertEqual(response.json()['native_model']['data_updates'], {})
            self.assertEqual(next(m['calculated'] for m in response.json()['native_model']['metrics'] if m['key'] == 'ВНД!D6'), 14)
        with patch('app.native_template_inputs.describe_inputs', side_effect=lambda *_: self.native_review_description((7,))):
            self.assertEqual(self.native_preview(project).status_code, 200)

    def test_native_input_review_requires_source_basis_confirmation_only_for_selected_uncertain_source(self):
        project, source, word = self.native_complete_folder()
        key, updates = 'Стоим_проекта!B38', {'Стоим_проекта!B38': 8}
        description = self.native_review_description()
        description['items'][0]['proposals'][0]['basis_confirmation'] = True
        choices = {key: {'choice': 'source', 'proposal_id': 'source-1'}}
        stack, _, rendered, _ = self.native_renderers(word)
        with stack, patch('app.native_template_inputs.describe_inputs', return_value=description):
            rejected = self.native_preview(project, data_updates=updates, review_decisions=choices)
            self.assertEqual(rejected.status_code, 422)
            self.assertIn('подтвердите валюту, единицы, период и базу цен', rejected.json()['detail'])
            self.assertEqual(self.native_generate(project, data_updates=updates, review_decisions=choices).status_code, 422)
            rendered.assert_not_called()
            self.assertEqual(self.native_preview(project, review_decisions={key: {'choice': 'keep'}}).status_code, 200)
            self.assertEqual(self.native_preview(project, data_updates=updates, review_decisions={key: {'choice': 'manual'}}).status_code, 200)
            checked = self.native_preview(project, data_updates=updates, review_decisions=choices, confirm_source_basis=True)
            self.assertEqual(checked.status_code, 200, checked.text)
            profile = checked.json()['native_model']
            self.assertTrue(profile['confirm_source_basis'])
            self.assertTrue(profile['input_review']['source_basis_confirmation_required'])
            self.assertTrue(profile['input_review']['complete'])
            self.assertEqual(self.native_download(checked.json()).status_code, 200)
            ready = self.native_generate(checked.json(), data_updates=updates, review_decisions=choices, confirm_source_basis=True)
            self.assertEqual(ready.status_code, 200, ready.text)
            self.assertTrue(ready.json()['native_model']['confirm_source_basis'])
            self.assertEqual(rendered.call_count, 1)
            self.assertEqual(self.client.get(project['files'][0]['download_url']).content, source)
            self.assertEqual(self.native_preview(project, data_updates=updates, review_decisions=choices, confirm_source_basis='true').status_code, 422)
        with unit() as s:
            generation = s.get(BusinessGeneration, ready.json()['current_generation_id'])
            self.assertTrue(json.loads(generation.inputs_json)['native_confirm_source_basis'])
        # An equal proposal left unused never asks for source-basis confirmation.
        equal = self.native_review_description((7,))
        equal['items'][0]['proposals'][0]['basis_confirmation'] = True
        with patch('app.native_template_inputs.describe_inputs', return_value=equal):
            unchanged = self.native_preview(project)
            self.assertEqual(unchanged.status_code, 200, unchanged.text)
            self.assertFalse(unchanged.json()['native_model']['confirm_source_basis'])
            self.assertFalse(unchanged.json()['native_model']['input_review']['source_basis_confirmation_required'])

    def test_native_input_review_source_sha_and_proposal_ids_cannot_reuse_a_new_source_revision(self):
        project, source = self.native_folder()
        key = 'Стоим_проекта!B38'
        choices = {key: {'choice': 'source', 'proposal_id': 'source-1'}}
        with patch('app.native_template_inputs.describe_inputs', side_effect=lambda *_: self.native_review_description()):
            reviewed = self.native_preview(project, data_updates={key: 8}, review_decisions=choices).json()
            changed = self.upload(reviewed, b'Changed evidence', 'updated-evidence.txt').json()
            self.assertEqual(self.native_preview(reviewed, data_updates={key: 8}, review_decisions=choices).status_code, 409)
            self.assertEqual(self.native_download(reviewed).status_code, 409)
            self.assertEqual(self.native_download(changed).status_code, 409)
            self.assertEqual(self.native_preview(changed, data_updates={key: 8}, review_decisions=choices, source_sha256='0'*64).status_code, 409)
        description = self.native_review_description()
        description['items'][0]['proposals'][0]['id'] = 'new-source-1'
        with patch('app.native_template_inputs.describe_inputs', return_value=description):
            response = self.native_preview(changed, data_updates={key: 8}, review_decisions=choices,
                                           source_sha256=hashlib.sha256(source).hexdigest())
            self.assertEqual(response.status_code, 422)

    def test_native_input_review_blank_input_requires_explicit_number_and_not_keep_or_zero_default(self):
        project, _ = self.native_folder({('Стоим_проекта', 'B38'): (None, None)})
        key = 'Стоим_проекта!B38'
        item = next(item for item in project['native_model']['input_review']['items'] if item['key'] == key)
        self.assertIsNone(item['value'])
        self.assertTrue(item['requires_decision'])
        self.assertEqual(self.native_preview(project).status_code, 422)
        self.assertEqual(self.native_preview(project, review_decisions={key: {'choice': 'keep'}}).status_code, 422)
        response = self.native_preview(project, data_updates={key: 8}, review_decisions={key: {'choice': 'manual'}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(next(m['calculated'] for m in response.json()['native_model']['metrics'] if m['key'] == 'ВНД!D6'), 16)

    def test_native_input_review_unused_staff_blanks_stay_optional_until_role_explicitly_selected(self):
        from app.native_uzgermed import REQUIRED_SHEETS
        with patch('test_native_uzgermed.REQUIRED_SHEETS', REQUIRED_SHEETS | {'Труд'}):
            project, source = self.native_folder({('Труд', 'A16'): ('Synthetic unused role', None),
                                                  ('Труд', 'B16'): (None, None), ('Труд', 'C16'): (None, None)})
        keys = ('Труд!B16', 'Труд!C16')
        items = {item['key']: item for item in project['native_model']['input_review']['items']}
        for key in keys:
            self.assertIsNone(items[key]['value'])
            self.assertFalse(items[key]['required'])
            self.assertFalse(items[key]['requires_decision'])
        original = self.native_preview(project)
        self.assertEqual(original.status_code, 200, original.text)
        self.assertTrue(original.json()['native_model']['input_review']['complete'])
        self.assertEqual(original.json()['native_model']['data_updates'], {})
        self.assertEqual(self.native_download(original.json()).status_code, 200)
        for key, value in zip(keys, (2, 50)):
            selected = self.native_preview(project, data_updates={key: value}, review_decisions={key: {'choice': 'manual'}})
            self.assertEqual(selected.status_code, 422)
            self.assertIn('численность и зарплату выбранной должности', selected.json()['detail'])
        complete = self.native_preview(project, data_updates={keys[0]: 2, keys[1]: 50},
                                       review_decisions={key: {'choice': 'manual'} for key in keys})
        self.assertEqual(complete.status_code, 200, complete.text)
        self.assertEqual(complete.json()['native_model']['data_updates'], {keys[0]: 2, keys[1]: 50})
        self.assertEqual(self.client.get(project['files'][0]['download_url']).content, source)

    def test_selected_template_marker_survives_upload_and_analysis_and_blocks_generic_fallback(self):
        project, _ = self.native_folder()
        marker = {'id': 17, 'title': 'Synthetic original template', 'xlsx_sha256': project['native_model']['source_sha256']}
        with unit(True) as s:
            record = s.get(BusinessProject, project['id'])
            extraction = json.loads(record.extraction_json)
            record.extraction_json = json.dumps({**extraction, 'template_selected': marker})
        uploaded = self.upload(project, b'Synthetic evidence', 'evidence.txt')
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        uploaded = uploaded.json()
        self.assertEqual(uploaded['extraction']['template_selected'], marker)
        analysed = self.analyse(uploaded)
        self.assertEqual(analysed.status_code, 200, analysed.text)
        analysed = analysed.json()
        self.assertEqual(analysed['extraction']['template_selected'], marker)
        self.assertIn('input_review', analysed['native_model'])
        with patch('app.native_projects.native_candidate', return_value=None):
            for response in (self.analyse(analysed), self.generate(analysed), self.save_draft(analysed, model())):
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn('выбранного шаблона', response.json()['detail'])
        corrupted = self.upload(analysed, synthetic_source('.xlsx'), 'Synthetic/native.xlsx')
        self.assertEqual(corrupted.status_code, 422, corrupted.text)
        current = self.client.get(f"/api/business-projects/{project['id']}", params={'company_id': self.cid}).json()
        self.assertEqual(current['revision'], analysed['revision'])
        self.assertEqual(current['extraction']['template_selected'], marker)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 0)

    def test_original_template_project_creation_checks_template_id_on_idempotent_retries(self):
        source = native_fixture()
        body = {'company_id': self.cid, 'title': 'Synthetic template project', 'request_key': str(uuid4()), 'template_id': 17}
        def use_template(session, project, template_id, user):
            self.assertIsNotNone(project.id)
            project.extraction_json = json.dumps({'mode': 'files', 'sources_pending': True,
                                                  'template_selected': {'id': template_id, 'title': 'Synthetic template'}})
            project.revision = 1
            session.add(BusinessSourceFile(company_id=self.cid, project_id=project.id, filename='native.xlsx',
                relative_path='template/native.xlsx', content=source, size=len(source), sha256=hashlib.sha256(source).hexdigest()))
        with patch('app.business_templates.use_template', side_effect=use_template) as copied:
            response = self.client.post('/api/business-projects', json=body, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            project = response.json()
            self.assertEqual(project['extraction']['template_selected']['id'], 17)
            self.assertEqual(project['source_count'], 1)
            self.assertTrue(project['native_model'])
            repeat = self.client.post('/api/business-projects', json=body, headers=self.headers)
            self.assertEqual(repeat.status_code, 200, repeat.text)
            self.assertEqual(repeat.json()['id'], project['id'])
            self.assertEqual(self.client.post('/api/business-projects', json={**body, 'template_id': 18}, headers=self.headers).status_code, 409)
            self.assertEqual(self.client.post('/api/business-projects', json={**body, 'mode': 'manual'}, headers=self.headers).status_code, 422)
            copied.assert_called_once()

    def test_identical_template_copies_are_one_model_but_different_models_are_rejected(self):
        project, source, word = self.native_complete_folder()
        copied = self.upload(project, source, 'Another folder/native-copy.xlsx')
        self.assertEqual(copied.status_code, 200, copied.text)
        copied = self.upload(copied.json(), word, 'Another folder/Бизнес-план-копия.docx')
        self.assertEqual(copied.status_code, 200, copied.text)
        copied = copied.json()
        stack, _, rendered, _ = self.native_renderers(word)
        with stack:
            ready = self.native_generate(copied)
            self.assertEqual(ready.status_code, 200, ready.text)
            rendered.assert_called_once()
        with unit() as s:
            generation = s.get(BusinessGeneration, ready.json()['current_generation_id'])
            self.assertEqual(len(json.loads(generation.source_manifest_json)), 4)
        different = native_fixture({('Стоим_проекта', 'B38'): (9, None)})
        response = self.upload(ready.json(), different, 'Another folder/different-model.xlsx')
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn('несколько оригинальных', response.json()['detail'])
        current = self.client.get(f"/api/business-projects/{project['id']}", params={'company_id': self.cid}).json()
        self.assertEqual(current['source_count'], 4)

    def test_manual_project_generates_four_files_without_sources_or_analysis(self):
        project, body = self.create('manual')
        self.assertEqual(project['mode'], 'manual')
        self.assertEqual(project['source_count'], 0)
        self.assertEqual(project['inputs'], {'title': body['title']})
        again = self.client.post('/api/business-projects', json=body, headers=self.headers)
        self.assertEqual(again.json()['id'], project['id'])
        wrong_mode = self.client.post('/api/business-projects', json={**body, 'mode': 'files'}, headers=self.headers)
        self.assertEqual(wrong_mode.status_code, 409)
        ready = self.generate(project)
        self.assertEqual(ready.status_code, 200, ready.text)
        ready = ready.json()
        self.assertEqual(ready['status'], 'ready')
        self.assertEqual(ready['mode'], 'manual')
        self.assertEqual(ready['extraction']['input_origin_label'], 'Введено пользователем')
        self.assertEqual(set(ready['extraction']['manual_fields']), set(model()))
        self.assertEqual(ready['extraction']['evidence'], [])
        self.assertEqual(ready['files'], [])
        self.assertEqual(len(ready['generations']), 1)
        retry = self.generate(ready).json()
        self.assertEqual(retry['current_generation_id'], ready['current_generation_id'])
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 4)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)), 0)
            generation = s.get(BusinessGeneration, ready['current_generation_id'])
            self.assertEqual(json.loads(generation.source_manifest_json), [])

    def test_incomplete_manual_draft_preserves_missing_values_and_retries_safely(self):
        project, _ = self.create('manual')
        partial = {'title': 'Incomplete manual project', 'opening_cash': '', 'equity': None,
                   'products': [{'name': 'Draft product', 'price': ''}], 'market': 'User supplied\nmarket note'}
        saved = self.save_draft(project, partial)
        self.assertEqual(saved.status_code, 200, saved.text)
        saved = saved.json()
        self.assertEqual(saved['revision'], project['revision'] + 1)
        self.assertEqual(saved['status'], 'needs_data')
        self.assertEqual(saved['inputs'], partial)
        self.assertEqual(saved['generations'], [])
        self.assertEqual(saved['current_generation_id'], None)
        self.assertTrue(any(issue['field'] == 'opening_cash' for issue in saved['validation']))
        retry = self.save_draft(project, partial)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()['revision'], saved['revision'])
        self.assertEqual(self.save_draft(project, {**partial, 'opening_cash': 1}).status_code, 409)
        self.assertEqual(self.generate(project).status_code, 409)
        not_ready = self.generate(saved, partial).json()
        self.assertEqual(not_ready['status'], 'needs_data')
        self.assertEqual(not_ready['generations'], [])

    def test_full_manual_narrative_draft_fits_project_body_limit(self):
        from app.business_model import MAX_NARRATIVE_LENGTH, NARRATIVE_FIELDS
        project, _ = self.create('manual')
        narrative = ('Synthetic project narrative. ' * 1000)[:MAX_NARRATIVE_LENGTH]
        inputs = {**model(), **{field: narrative for field in NARRATIVE_FIELDS}}
        encoded = json.dumps({'revision': project['revision'], 'inputs': inputs}).encode()
        self.assertGreater(len(encoded), 65536)
        self.assertLess(len(encoded), 1024 * 1024)
        saved = self.save_draft(project, inputs)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()['inputs'], inputs)
        self.assertEqual(saved.json()['revision'], project['revision'] + 1)
        rejected = self.client.put(f"/api/business-projects/{project['id']}/inputs",
            params={'company_id': self.cid}, headers={**self.headers, 'Content-Type': 'application/json'},
            content=b' ' * (1024 * 1024 + 1))
        self.assertEqual(rejected.status_code, 413)
        self.assertEqual(rejected.json(), {'detail': 'Неверный размер запроса.'})
        current = self.client.get(f"/api/business-projects/{project['id']}",
            params={'company_id': self.cid}, headers=self.headers).json()
        self.assertEqual(current['revision'], saved.json()['revision'])
        self.assertEqual(current['inputs'], inputs)

    def test_project_list_offers_the_latest_finished_business_plan_files(self):
        project, _ = self.create('manual')
        listing = lambda: {item['id']: item for item in self.client.get('/api/business-projects',
            params={'company_id': self.cid}, headers=self.headers).json()['items']}
        self.assertIsNone(listing()[project['id']]['latest_report'])
        ready = self.generate(project).json()
        latest = listing()[project['id']]['latest_report']
        self.assertEqual(latest['generation_id'], ready['current_generation_id'])
        self.assertTrue(latest['current'])
        self.assertEqual(latest['business_archive']['status'], 'ready')
        self.assertEqual(latest['business_archive']['company_id'], self.cid)
        self.assertEqual(latest['teo_archive']['status'], 'ready')
        archive = latest['business_archive']['id']
        for fmt in ('pdf', 'xlsx'):
            file = self.client.get(f'/api/report-archives/{archive}/files/{fmt}', params={'company_id': self.cid},
                                   headers=self.headers)
            self.assertEqual(file.status_code, 200, file.text)
        # A changed draft keeps the earlier finished files available, marked as not current.
        saved = self.save_draft(ready, {**model(), 'opening_cash': 600}).json()
        latest = listing()[project['id']]['latest_report']
        self.assertEqual(latest['generation_id'], ready['current_generation_id'])
        self.assertFalse(latest['current'])
        # Another company never sees the project or its files.
        other = self.client.get('/api/business-projects', params={'company_id': self.other}, headers=self.headers)
        self.assertNotIn(saved['id'], [item['id'] for item in other.json().get('items', [])] if other.status_code == 200 else [])
        removed = self.remove(saved)
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertNotIn(saved['id'], listing())

    def test_draft_edit_invalidates_current_generation_and_retains_immutable_reports(self):
        project, _ = self.create('manual')
        ready = self.generate(project).json()
        generation = ready['current_generation_id']
        with unit() as s:
            old_files = {row.id: bytes(row.content) for row in s.scalars(select(ReportArchiveFile))}
        same = self.save_draft(ready, model()).json()
        self.assertEqual(same['status'], 'ready')
        self.assertEqual(same['current_generation_id'], generation)
        changed = {**model(), 'opening_cash': 600}
        saved = self.save_draft(ready, changed).json()
        self.assertEqual(saved['revision'], ready['revision'] + 1)
        self.assertEqual(saved['current_generation_id'], None)
        self.assertEqual(len(saved['generations']), 1)
        new = self.generate(saved, changed).json()
        self.assertNotEqual(new['current_generation_id'], generation)
        self.assertEqual(len(new['generations']), 2)
        with unit() as s:
            for id, content in old_files.items():
                self.assertEqual(bytes(s.get(ReportArchiveFile, id).content), content)

    def test_manual_attachments_and_analysis_preserve_form_and_source_candidate(self):
        project, _ = self.create('manual')
        ready = self.generate(project).json()
        source_model = model()
        source_model['opening_cash'] = 999
        source_model['products'][0]['price'] = 99
        uploaded = self.upload(ready, json.dumps(source_model).encode()).json()
        self.assertEqual(uploaded['mode'], 'manual')
        self.assertEqual(uploaded['inputs'], model())
        self.assertEqual(uploaded['current_generation_id'], None)
        self.assertEqual(len(uploaded['generations']), 1)
        analysed = self.analyse(uploaded).json()
        self.assertEqual(analysed['status'], 'ready')
        self.assertEqual(analysed['inputs'], model())
        self.assertEqual(analysed['extraction']['inputs']['opening_cash'], 999)
        self.assertIn('opening_cash', analysed['extraction']['manual_fields'])
        self.assertIn('products', analysed['extraction']['manual_fields'])
        generation = analysed['current_generation_id']
        with unit() as s:
            result = json.loads(s.get(BusinessGeneration, generation).result_json)
            self.assertEqual(result['totals']['revenue'], '3000')
            self.assertEqual(len(json.loads(s.get(BusinessGeneration, generation).source_manifest_json)), 1)

    def test_manual_supporting_text_does_not_require_financial_workbook(self):
        project, _ = self.create('manual')
        saved = self.save_draft(project, model()).json()
        uploaded = self.upload(saved, b'Synthetic supporting document', 'notes.txt').json()
        self.assertEqual(uploaded['inputs'], model())
        # Direct generation also analyses optional documents; no analyse bypass.
        ready = self.generate(uploaded).json()
        self.assertEqual(ready['status'], 'ready')
        notice = next(issue for issue in ready['extraction']['issues'] if issue.get('code') == 'no_model')
        self.assertFalse(notice['requires_confirmation'])
        self.assertIn('пользователем', notice['message'])
        self.assertEqual(ready['validation'], [])
        self.assertTrue(any(issue.get('code') == 'text_reference' for issue in ready['extraction']['issues']))
        self.assertTrue(any(issue.get('code') == 'missing_or_invalid' for issue in ready['extraction']['original_issues']))
        self.assertFalse(any(issue.get('code') == 'missing_or_invalid' for issue in ready['extraction']['issues']))
        from pypdf import PdfReader
        with unit() as s:
            generation = s.get(BusinessGeneration, ready['current_generation_id'])
            for archive_id in (generation.business_archive_id, generation.teo_archive_id):
                pdf = s.scalar(select(ReportArchiveFile).where(ReportArchiveFile.archive_id == archive_id,
                                                              ReportArchiveFile.format == 'pdf'))
                text = '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(pdf.content)).pages)
                self.assertNotIn('Обязательное непустое', text)
                self.assertNotIn('Значение отсутствует', text)
                self.assertIn('notes.txt', text)
                self.assertIn('Финансовые данные вводятся пользователем', text)

    def test_manual_generation_retains_blocking_attachment_issues_until_confirmation(self):
        project, _ = self.create('manual')
        uploaded = self.upload(project, b'Synthetic evidence', 'evidence.txt').json()
        extraction = {'inputs': {}, 'evidence': [], 'documents': [], 'issues': [
            {'field': 'model', 'code': 'no_model', 'message': 'No financial workbook', 'requires_confirmation': True},
            {'field': 'products.price', 'code': 'price_conflict', 'message': 'Conflicting source prices',
             'requires_confirmation': True}]}
        with patch('app.business_sources.extract_sources', return_value=extraction):
            waiting = self.generate(uploaded).json()
        self.assertEqual(waiting['status'], 'needs_data')
        self.assertEqual(waiting['generations'], [])
        self.assertTrue(any(issue.get('source_code') == 'price_conflict' for issue in waiting['validation']))
        self.assertTrue(any(issue.get('code') == 'price_conflict' for issue in waiting['extraction']['issues']))
        with patch('app.business_sources.extract_sources', return_value=extraction):
            ready = self.generate(waiting, confirmed=True).json()
        self.assertEqual(ready['status'], 'ready')
        self.assertTrue(ready['extraction']['parameters_confirmed'])
        self.assertTrue(any(issue.get('code') == 'price_conflict' for issue in ready['extraction']['issues']))

    def test_manual_native_attachment_cannot_bypass_original_methodology(self):
        project, _ = self.create('manual')
        saved = self.save_draft(project, {**model(), 'resources': 'Preserved manual resources'}).json()
        uploaded = self.upload(saved, native_fixture(), 'native.xlsx').json()
        self.assertTrue(uploaded['native_model'])
        self.assertEqual(uploaded['inputs'], saved['inputs'])
        self.assertEqual(self.save_draft(uploaded, model()).status_code, 409)
        self.assertEqual(self.generate(uploaded, confirmed=True).status_code, 409)
        analysed = self.analyse(uploaded).json()
        self.assertTrue(analysed['native_model'])
        self.assertEqual(analysed['mode'], 'manual')
        self.assertEqual(analysed['inputs'], saved['inputs'])

    def test_manual_draft_rechecks_revision_after_source_inspection(self):
        project, _ = self.create('manual')
        concurrent = {'title': 'Concurrent draft'}
        def change_during_inspection(files):
            with unit(True) as s:
                record = s.get(BusinessProject, project['id'])
                record.inputs_json = json.dumps(concurrent)
                record.revision += 1
                from app.db import now
                record.updated_at = now()
            return None
        with patch('app.native_projects.native_candidate', side_effect=change_during_inspection):
            response = self.save_draft(project, {'title': 'Stale draft'})
        self.assertEqual(response.status_code, 409, response.text)
        with unit() as s:
            self.assertEqual(json.loads(s.get(BusinessProject, project['id']).inputs_json), concurrent)

    def test_draft_permissions_company_lifecycle_and_narrative_bounds(self):
        project, _ = self.create('manual')
        peer, headers = self.actor('draft_peer', 'finance')
        self.assertEqual(self.save_draft(project, {}, peer, headers).status_code, 403)
        reader, headers = self.actor('draft_reader', 'investor')
        self.assertEqual(self.save_draft(project, {}, reader, headers).status_code, 403)
        self.assertEqual(self.save_draft(project, {}, company_id=self.other,
            headers={**self.headers, 'X-Company-ID': str(self.other)}).status_code, 404)
        anonymous = TestClient(app)
        self.clients.append(anonymous)
        self.assertEqual(self.save_draft(project, {}, anonymous).status_code, 401)
        from app.business_model import MAX_NARRATIVE_LENGTH
        for value in ({'unsafe': 'shape'}, None, 'x' * (MAX_NARRATIVE_LENGTH + 1), 'text\x00control'):
            response = self.save_draft(project, {'title': 'Draft', 'strategy': value})
            self.assertEqual(response.status_code, 422, response.text)
        unchanged = self.client.get(f"/api/business-projects/{project['id']}", params={'company_id': self.cid}).json()
        self.assertEqual(unchanged['revision'], project['revision'])
        deleted = self.remove(project).json()
        self.assertEqual(self.save_draft(deleted, {'title': 'Deleted draft'}).status_code, 409)

    def test_legacy_file_project_still_requires_source_analysis(self):
        project, _ = self.create()
        self.assertEqual(project['mode'], 'files')
        self.assertEqual(self.generate(project).status_code, 422)
        uploaded = self.upload(project).json()
        self.assertEqual(self.generate(uploaded).status_code, 409)
        self.assertEqual(self.analyse(uploaded).json()['status'], 'ready')

    def test_native_generation_publishes_four_private_files_from_fresh_model_and_retries_without_rendering(self):
        project, source, word = self.native_complete_folder()
        stack, patched, rendered, teo = self.native_renderers(word)
        with stack:
            response = self.native_generate(project, {'Стоим_проекта!B38': 8})
            self.assertEqual(response.status_code, 200, response.text)
            ready = response.json()
            self.assertEqual(ready['status'], 'ready')
            self.assertEqual(len(ready['generations']), 1)
            generation = ready['generations'][0]
            self.assertEqual(generation['metrics']['npv'], 16)
            self.assertEqual(generation['business_archive']['period_start'], '2027-01-01')
            self.assertEqual(generation['business_archive']['period_end'], '2029-12-31')
            again = self.native_generate(ready, {'Стоим_проекта!B38': 8})
            self.assertEqual(again.status_code, 200, again.text)
            self.assertEqual(again.json()['current_generation_id'], ready['current_generation_id'])
            patched.assert_called_once()
            rendered.assert_called_once_with(word)
            teo.assert_called_once()
        files = {f['filename']: f for f in ready['files']}
        self.assertEqual(self.client.get(files['native.xlsx']['download_url']).content, source)
        self.assertEqual(self.client.get(files['Бизнес-план.docx']['download_url']).content, word)
        for archive in (generation['business_archive'], generation['teo_archive']):
            self.assertEqual(archive['status'], 'ready')
            for fmt in ('pdf', 'xlsx'):
                response = self.client.get(f"/api/bot/v1/report-archives/{archive['id']}/files/{fmt}",
                    params={'company_id': self.cid, 'telegram_user_id': 88101}, auth=BOT)
                self.assertEqual(response.status_code, 200, response.text[:100])
                self.assertTrue(response.content.startswith(b'%PDF-' if fmt == 'pdf' else b'PK'))
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchive)), 2)
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 4)
            self.assertEqual(s.scalar(select(func.count()).select_from(BusinessGeneration)), 1)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)), 0)

    def test_native_generator_update_creates_a_new_immutable_report_without_reusing_old_word_conclusions(self):
        project, source, word = self.native_complete_folder()
        stack, patched, rendered, _ = self.native_renderers(word)
        with stack:
            with patch('app.native_projects.GENERATOR_VERSION', '1.0'):
                before = self.native_generate(project).json()
            old_archive = before['generations'][0]['business_archive']['id']
            path = f'/api/report-archives/{old_archive}/files/pdf?company_id={self.cid}'
            old_bytes = self.client.get(path, headers=self.headers).content
            with patch('app.native_projects.GENERATOR_VERSION', '1.1'):
                response = self.native_generate(before)
                self.assertEqual(response.status_code, 200, response.text)
                after = response.json()
                retry = self.native_generate(after)
                self.assertEqual(retry.status_code, 200, retry.text)
                self.assertEqual(retry.json()['current_generation_id'], after['current_generation_id'])
            self.assertNotEqual(before['current_generation_id'], after['current_generation_id'])
            self.assertEqual(len(after['generations']), 2)
            self.assertEqual(patched.call_count, 2)
            self.assertEqual(rendered.call_count, 2)
            self.assertEqual(self.client.get(path, headers=self.headers).content, old_bytes)
        self.assertEqual(self.client.get(after['files'][0]['download_url'], headers=self.headers).content, source)
        with unit() as s:
            versions = [json.loads(g.result_json)['native_generator'] for g in s.scalars(
                select(BusinessGeneration).order_by(BusinessGeneration.id))]
            self.assertEqual(versions, ['1.0', '1.1'])

    def test_native_parameter_or_source_change_creates_new_immutable_report_versions(self):
        project, _, word = self.native_complete_folder()
        stack, _, rendered, _ = self.native_renderers(word)
        with stack:
            first = self.native_generate(project, {'Стоим_проекта!B38': 8}).json()
            second = self.native_generate(first, {'Стоим_проекта!B38': 9})
            self.assertEqual(second.status_code, 200, second.text)
            second = second.json()
            self.assertNotEqual(second['current_generation_id'], first['current_generation_id'])
            self.assertEqual(len(second['generations']), 2)
            self.assertEqual(second['generations'][0]['metrics']['npv'], 18)
            changed_source = native_fixture({('Стоим_проекта', 'B38'): (8, None),
                                            ('ВНД', 'D6'): (16, "'Стоим_проекта'!B38*2")})
            updated = self.upload(second, changed_source, 'Synthetic/native.xlsx')
            self.assertEqual(updated.status_code, 200, updated.text)
            third = self.native_generate(updated.json(), {'Стоим_проекта!B38': 9})
            self.assertEqual(third.status_code, 200, third.text)
            self.assertEqual(len(third.json()['generations']), 3)
            self.assertNotEqual(third.json()['current_generation_id'], second['current_generation_id'])
            self.assertEqual(rendered.call_count, 3)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchive)), 6)
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 12)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)), 0)

    def test_native_generation_checks_owner_company_revision_template_calendar_and_safe_parameters(self):
        incomplete, _ = self.native_folder()
        self.assertEqual(self.native_generate(incomplete).status_code, 422)
        project, _, word = self.native_complete_folder()
        peer, peer_headers = self.actor('native_gen_peer', 'finance')
        reader, reader_headers = self.actor('native_gen_reader', 'investor')
        for client, headers in ((peer, peer_headers), (reader, reader_headers)):
            self.assertEqual(self.native_generate(project, client=client, headers=headers).status_code, 403)
        anonymous = TestClient(app)
        self.clients.append(anonymous)
        self.assertEqual(self.native_generate(project, client=anonymous).status_code, 401)
        self.assertEqual(self.native_generate({**project, 'revision': project['revision']+1}).status_code, 409)
        foreign = {**self.headers, 'X-Company-ID': str(self.other)}
        self.assertEqual(self.native_generate(project, headers=foreign, company_id=self.other).status_code, 404)
        stack, patched, rendered, teo = self.native_renderers(word)
        with stack:
            for start in ('1999-01-01', '2098-01-01', '2027-01-02', 'not-a-date'):
                self.assertEqual(self.native_generate(project, start=start).status_code, 422)
            self.assertEqual(self.native_generate(project, {'ВНД!D6': 100}).status_code, 422)
            self.assertEqual(self.native_generate(project, {'РКЛ!AA7': 3}).status_code, 422)
            patched.assert_not_called()
            rendered.assert_not_called()
            teo.assert_not_called()
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchive)), 0)

    def test_native_generation_blocks_core_error_but_retains_unrelated_errors_as_evidence(self):
        blocked, _, word = self.native_complete_folder({('ВНД', 'D6'): ('#REF!', '#REF!')})
        unrelated, _, _ = self.native_complete_folder({('РКЛ', 'AB14'): ('#REF!', '#REF!')})
        stack, _, rendered, _ = self.native_renderers(word)
        with stack:
            response = self.native_generate(blocked)
            self.assertEqual(response.status_code, 422, response.text)
            rendered.assert_not_called()
            response = self.native_generate(unrelated)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()['native_model']['preview']['blocked'])
            self.assertEqual(response.json()['native_model']['preview']['error_count'], 1)
            self.assertEqual(len(response.json()['generations']), 1)
            rendered.assert_called_once()
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(BusinessGeneration)), 1)
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchive)), 2)

    def test_native_generation_rechecks_lifecycle_and_permission_after_pdf_rendering(self):
        project, _, word = self.native_complete_folder()
        def delete_restore_during_render(_):
            self.assertEqual(self.restore(self.remove(project).json()).status_code, 200)
            return b'%PDF-1.4\nSynthetic native test\n%%EOF\n'
        stack, _, _, _ = self.native_renderers(word, delete_restore_during_render)
        with stack:
            self.assertEqual(self.native_generate(project).status_code, 409)
        def revoke_during_render(_):
            with unit(True) as s:
                s.get(CompanyUser, (self.cid, self.uid)).role = 'investor'
            return b'%PDF-1.4\nSynthetic native test\n%%EOF\n'
        stack, _, _, _ = self.native_renderers(word, revoke_during_render)
        with stack:
            self.assertEqual(self.native_generate(project).status_code, 403)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(BusinessGeneration)), 0)
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 0)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)), 0)

    def test_native_original_model_uses_source_profile_without_generic_requirements_or_reports(self):
        project, _ = self.native_folder()
        self.assertTrue(project['native_model']['supported'])
        self.assertEqual(project['native_model']['sheet_count'], 11)
        self.assertEqual(project['validation'], [])
        reviewed = self.analyse(project)
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertEqual(reviewed.json()['generations'], [])
        self.assertEqual(reviewed.json()['validation'], [])
        self.assertEqual(self.generate(reviewed.json()).status_code, 409)
        self.assertEqual(self.native_download(reviewed.json()).status_code, 409)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchive)), 0)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)), 0)

    def test_native_preview_recalculates_changed_input_preserves_original_and_native_print_styles(self):
        project, original = self.native_folder()
        response = self.native_preview(project, {'Стоим_проекта!B38': 8})
        self.assertEqual(response.status_code, 200, response.text)
        profile = response.json()['native_model']
        self.assertFalse(profile['preview']['blocked'], profile['preview'])
        npv = next(m for m in profile['metrics'] if m['key'] == 'ВНД!D6')
        self.assertEqual(npv['original'], 14)
        self.assertEqual(npv['calculated'], 16)
        downloaded = self.native_download(response.json())
        self.assertEqual(downloaded.status_code, 200, downloaded.text[:100])
        self.assertEqual(downloaded.headers['Cache-Control'], 'no-store')
        self.assertEqual(downloaded.headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn(quote('Пересчитано_'), downloaded.headers['Content-Disposition'])
        self.assertEqual(self.client.get(project['files'][0]['download_url']).content, original)
        with ZipFile(io.BytesIO(original)) as before, ZipFile(io.BytesIO(downloaded.content)) as after:
            self.assertEqual(after.read('xl/styles.xml'), before.read('xl/styles.xml'))
            self.assertEqual(after.read('xl/printerSettings/printerSettings1.bin'), before.read('xl/printerSettings/printerSettings1.bin'))
            for name in before.namelist():
                if name.startswith('xl/worksheets/'):
                    self.assertIn(b'orientation="landscape"', after.read(name))
                    self.assertIn(b'paperSize="9"', after.read(name))
                    if b'ht="24"' in before.read(name):
                        self.assertIn(b'ht="24"', after.read(name))
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(BusinessGeneration)), 0)
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 0)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)), 0)

    def test_native_preview_and_download_enforce_owner_permission_company_and_revision(self):
        project, _ = self.native_folder()
        peer, peer_headers = self.actor('native_peer', 'finance')
        investor, investor_headers = self.actor('native_reader', 'investor')
        for client, headers in ((peer, peer_headers), (investor, investor_headers)):
            self.assertEqual(self.native_preview(project, client=client, headers=headers).status_code, 403)
            self.assertEqual(self.native_download(project, client=client, headers=headers).status_code, 403)
            viewed = client.get(f"/api/business-projects/{project['id']}", params={'company_id': self.cid}).json()
            self.assertNotIn('native_model', viewed)
        anonymous = TestClient(app)
        self.clients.append(anonymous)
        self.assertEqual(self.native_preview(project, client=anonymous).status_code, 401)
        self.assertEqual(self.native_download(project, client=anonymous).status_code, 401)
        stale = {**project, 'revision': project['revision']+1}
        self.assertEqual(self.native_preview(stale).status_code, 409)
        self.assertEqual(self.native_download(stale).status_code, 409)
        foreign = {**self.headers, 'X-Company-ID': str(self.other)}
        self.assertEqual(self.native_preview(project, headers=foreign, company_id=self.other).status_code, 404)
        self.assertEqual(self.native_download(project, headers=foreign, company_id=self.other).status_code, 404)

    def test_native_overrides_cannot_edit_formulas_or_unknown_cells_and_check_numbers_and_ranges(self):
        project, _ = self.native_folder()
        for overrides in ({'ВНД!D6': 3}, {'Unknown!A1': 3}, {'Стоим_проекта!B38': '8'},
                          {'Стоим_проекта!B38': True}, {'Стоим_проекта!B38': 0},
                          {'Раб_капит!B7': 0}):
            with self.subTest(overrides=overrides):
                response = self.native_preview(project, overrides)
                self.assertEqual(response.status_code, 422, response.text)
        formula_project, _ = self.native_folder({('Стоим_проекта', 'B38'): (7, '3+4')})
        self.assertEqual(self.native_preview(formula_project, {'Стоим_проекта!B38': 8}).status_code, 422)

    def test_native_readonly_credit_conditions_reject_overrides_before_calculation_or_publication(self):
        project, source, word = self.native_complete_folder()
        controls = {item['key']: item for item in project['native_model']['parameters']}
        keys = ('РКЛ!AA7', 'РКЛ!AA8', 'РКЛ!AA10')
        for key in keys:
            self.assertFalse(controls[key]['editable'])
            self.assertIsNotNone(controls[key]['value'])
        stack, patched, rendered, teo = self.native_renderers(word)
        with stack:
            for key in keys:
                for value in (controls[key]['value'], controls[key]['value'] + 1):
                    with self.subTest(key=key, value=value):
                        self.assertEqual(self.native_preview(project, {key: value}).status_code, 422)
                        self.assertEqual(self.native_generate(project, {key: value}).status_code, 422)
            patched.assert_not_called()
            rendered.assert_not_called()
            teo.assert_not_called()
        files = {item['filename']: item for item in project['files']}
        self.assertEqual(self.client.get(files['native.xlsx']['download_url']).content, source)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(BusinessGeneration)), 0)
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 0)

    def test_native_preview_exposes_dependent_formula_error_and_never_publishes_a_financial_report(self):
        project, original = self.native_folder({('ВНД', 'D6'): ('#REF!', '#REF!')})
        response = self.native_preview(project)
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()['native_model']['preview']
        self.assertTrue(preview['blocked'])
        self.assertIn('ВНД!D6', preview['dependent_errors'])
        self.assertGreater(preview['error_count'], 0)
        downloaded = self.native_download(response.json())
        self.assertEqual(downloaded.status_code, 200)
        self.assertIn(quote('Проверка_ошибок_'), downloaded.headers['Content-Disposition'])
        self.assertEqual(self.client.get(project['files'][0]['download_url']).content, original)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchive)), 0)
            self.assertEqual(s.scalar(select(func.count()).select_from(BusinessGeneration)), 0)

    def test_native_preview_and_download_recheck_delete_restore_lifecycle_after_calculation(self):
        project, _ = self.native_folder()
        from app.native_workbook import recalculate
        def change_lifecycle(*args, **kwargs):
            output = recalculate(*args, **kwargs)
            self.assertEqual(self.restore(self.remove(project).json()).status_code, 200)
            return output
        with patch('app.native_workbook.recalculate', side_effect=change_lifecycle):
            self.assertEqual(self.native_preview(project).status_code, 409)
        current = self.client.get(f"/api/business-projects/{project['id']}", params={'company_id': self.cid}).json()
        self.assertIsNone(current['native_model'].get('preview'))
        reviewed = self.native_preview(current).json()
        with patch('app.native_workbook.recalculate', side_effect=change_lifecycle):
            self.assertEqual(self.native_download(reviewed).status_code, 409)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchive)), 0)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)), 0)

    def test_delete_restore_retains_original_sources_parameters_and_generated_bytes(self):
        project = self.ready()
        generation = project['generations'][0]
        archive_ids = [generation[k]['id'] for k in ('business_archive', 'teo_archive')]
        with unit() as s:
            original = s.get(BusinessProject, project['id'])
            state = (original.inputs_json, original.extraction_json, original.current_fingerprint)
            source_bytes = [bytes(f.content) for f in s.scalars(select(BusinessSourceFile))]
            report_bytes = [bytes(f.content) for f in s.scalars(select(ReportArchiveFile))]
        deleted = self.remove(project)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()['status'], 'deleted')
        self.assertFalse(deleted.json()['can_edit'])
        self.assertTrue(deleted.json()['can_restore'])
        self.assertEqual(deleted.json()['revision'], project['revision'])
        self.assertEqual(self.client.get('/api/business-projects', params={'company_id': self.cid}).json()['items'], [])
        self.assertEqual(self.client.get('/api/report-archives', params={'company_id': self.cid}).json()['items'], [])
        for archive_id in archive_ids:
            self.assertEqual(self.client.get(f'/api/report-archives/{archive_id}/files/pdf',
                params={'company_id': self.cid}).status_code, 404)
            self.assertEqual(self.client.get(f'/api/bot/v1/report-archives/{archive_id}/files/pdf',
                params={'company_id': self.cid, 'telegram_user_id': 88101}, auth=BOT).status_code, 404)
            blocked = self.client.post(f'/api/report-archives/{archive_id}/restore',
                params={'company_id': self.cid, 'expected_version': generation['business_archive' if archive_id == archive_ids[0] else 'teo_archive']['version']},
                headers=self.headers)
            self.assertEqual(blocked.status_code, 409)
        self.assertEqual(self.client.get(project['files'][0]['download_url']).content, source_bytes[0])
        self.assertEqual(self.upload(project).status_code, 409)
        self.assertEqual(self.analyse(project).status_code, 409)
        self.assertEqual(self.generate(project).status_code, 409)
        self.assertEqual(self.remove(project).status_code, 200)
        restored = self.restore(deleted.json())
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()['status'], 'ready')
        self.assertEqual(restored.json()['current_generation_id'], project['current_generation_id'])
        self.assertEqual(restored.json()['revision'], project['revision'])
        with unit() as s:
            restored_project = s.get(BusinessProject, project['id'])
            self.assertEqual((restored_project.inputs_json, restored_project.extraction_json,
                              restored_project.current_fingerprint), state)
            self.assertEqual([bytes(f.content) for f in s.scalars(select(BusinessSourceFile))], source_bytes)
            self.assertEqual([bytes(f.content) for f in s.scalars(select(ReportArchiveFile))], report_bytes)
            self.assertEqual(s.scalar(select(func.count()).select_from(BusinessGeneration)), 1)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)), 0)
        self.assertEqual(self.generate(restored.json()).json()['current_generation_id'], project['current_generation_id'])

    def test_delete_restore_returns_each_prior_project_status_and_keeps_explicitly_removed_archive_deleted(self):
        draft, _ = self.create()
        self.assertEqual(self.restore(self.remove(draft).json()).json()['status'], 'draft')
        partial = model()
        del partial['opening_cash']
        needs = self.analyse(self.upload(draft, json.dumps(partial).encode()).json()).json()
        self.assertEqual(needs['status'], 'needs_data')
        restored = self.restore(self.remove(needs).json()).json()
        self.assertEqual(restored['status'], 'needs_data')
        self.assertEqual(restored['inputs'], needs['inputs'])
        ready = self.ready()
        archive = ready['generations'][0]['business_archive']
        removed = self.client.delete(f"/api/report-archives/{archive['id']}",
            params={'company_id': self.cid, 'expected_version': archive['version']}, headers=self.headers)
        self.assertEqual(removed.status_code, 200)
        restored = self.restore(self.remove(ready).json()).json()
        self.assertEqual(restored['status'], 'ready')
        archives = restored['generations'][0]
        self.assertEqual(archives['business_archive']['status'], 'deleted')
        self.assertEqual(archives['teo_archive']['status'], 'ready')

    def test_deleted_projects_are_owner_only_and_delete_restore_require_permission_company_and_revision(self):
        project = self.ready()
        peer, peer_headers = self.actor('delete_peer', 'finance')
        investor, investor_headers = self.actor('delete_reader', 'investor')
        for client, headers in ((peer, peer_headers), (investor, investor_headers)):
            self.assertEqual(self.remove(project, client, headers).status_code, 403)
            self.assertEqual(self.restore(project, client, headers).status_code, 403)
        self.assertEqual(self.remove(project, expected_revision=project['revision']+1).status_code, 409)
        foreign_headers = {**self.headers, 'X-Company-ID': str(self.other)}
        self.assertEqual(self.remove(project, headers=foreign_headers, company_id=self.other).status_code, 404)
        anonymous = TestClient(app)
        self.clients.append(anonymous)
        self.assertEqual(self.remove(project, anonymous, {}).status_code, 401)
        deleted = self.remove(project).json()
        owner_items = self.client.get('/api/business-projects',
            params={'company_id': self.cid, 'include_deleted': True}).json()['items']
        self.assertEqual([p['id'] for p in owner_items], [project['id']])
        for client in (peer, investor):
            self.assertEqual(client.get('/api/business-projects',
                params={'company_id': self.cid, 'include_deleted': True}).json()['items'], [])
            self.assertEqual(client.get(f"/api/business-projects/{project['id']}",
                params={'company_id': self.cid}).status_code, 404)
        self.assertEqual(self.restore(deleted, expected_revision=deleted['revision']+1).status_code, 409)
        self.assertEqual(self.restore(deleted, headers=foreign_headers, company_id=self.other).status_code, 404)

    def test_deletion_during_rendering_cannot_republish_or_restore_deleted_project(self):
        project, _ = self.create()
        project = self.upload(project).json()
        from app.business_exports import build_documents
        def remove_during_render(*args):
            output = build_documents(*args)
            self.assertEqual(self.remove(project).status_code, 200)
            return output
        with patch('app.business_exports.build_documents', side_effect=remove_during_render):
            self.assertEqual(self.analyse(project).status_code, 409)
        with unit() as s:
            self.assertEqual(s.get(BusinessProject, project['id']).status, 'deleted')
            self.assertEqual(s.scalar(select(func.count()).select_from(BusinessGeneration)), 0)
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 0)

    def test_delete_then_restore_during_rendering_or_streaming_cancels_old_write(self):
        project, _ = self.create()
        project = self.upload(project).json()
        from app.business_exports import build_documents
        def lifecycle_during_render(*args):
            output = build_documents(*args)
            self.assertEqual(self.restore(self.remove(project).json()).status_code, 200)
            return output
        with patch('app.business_exports.build_documents', side_effect=lifecycle_during_render):
            self.assertEqual(self.analyse(project).status_code, 409)
        from app.business_projects import validate_source
        def lifecycle_during_stream(raw, filename):
            result = validate_source(raw, filename)
            self.assertEqual(self.restore(self.remove(project).json()).status_code, 200)
            return result
        with patch('app.business_projects.validate_source', side_effect=lifecycle_during_stream):
            changed = json.dumps({**model(), 'opening_cash': 999}).encode()
            self.assertEqual(self.upload(project, changed).status_code, 409)
        with unit() as s:
            self.assertEqual(s.get(BusinessProject, project['id']).status, 'draft')
            self.assertEqual(s.get(BusinessProject, project['id']).revision, project['revision'])
            self.assertEqual(s.scalar(select(func.count()).select_from(BusinessGeneration)), 0)

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
