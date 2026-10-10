"""Reusable private report pairs: tenant isolation, revisions and copy integrity."""
import json
import sys
import unittest
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import test_business_projects as fixtures
from app.db import BusinessProject, BusinessSourceFile, BusinessTemplate

TMP, engine = fixtures.TMP, fixtures.engine
native_fixture, synthetic_source = fixtures.native_fixture, fixtures.synthetic_source
unit, select = fixtures.unit, fixtures.select


class BusinessTemplateTests(unittest.TestCase):
    setUp = fixtures.BusinessProjectTests.setUp
    tearDown = fixtures.BusinessProjectTests.tearDown
    login = fixtures.BusinessProjectTests.login
    actor = fixtures.BusinessProjectTests.actor
    create = fixtures.BusinessProjectTests.create
    upload = fixtures.BusinessProjectTests.upload
    remove = fixtures.BusinessProjectTests.remove

    @classmethod
    def tearDownClass(cls):
        engine.dispose()
        TMP.cleanup()

    def pair(self):
        project, _ = self.create()
        response = self.upload(project, native_fixture(), 'original/model.xlsx')
        self.assertEqual(response.status_code, 200, response.text)
        response = self.upload(response.json(), synthetic_source('.docx'), 'original/business-plan.docx')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def save(self, project, body=None, client=None, headers=None):
        body = body or {'revision': project['revision'], 'title': 'Synthetic template',
                        'request_key': str(uuid4())}
        response = (client or self.client).post(
            f"/api/business-projects/{project['id']}/save-template",
            params={'company_id': self.cid}, json=body, headers=headers or self.headers)
        return response, body

    def clone(self, template, **fields):
        body = {'company_id': self.cid, 'title': 'Synthetic new report',
                'request_key': str(uuid4()), 'template_id': template['id'], **fields}
        return self.client.post('/api/business-projects', json=body, headers=self.headers), body

    def test_saved_pair_is_idempotent_and_does_not_change_project(self):
        project = self.pair()
        first, body = self.save(project)
        self.assertEqual(first.status_code, 200, first.text)
        repeated, _ = self.save(project, body)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(first.json(), repeated.json())
        with unit() as s:
            self.assertEqual(s.get(BusinessProject, project['id']).revision, project['revision'])
            self.assertEqual(len(list(s.scalars(select(BusinessTemplate)))), 1)

    def test_copy_preserves_both_blobs_and_new_project_uses_native_mode(self):
        saved, _ = self.save(self.pair())
        response, _ = self.clone(saved.json())
        self.assertEqual(response.status_code, 200, response.text)
        copied = response.json()
        self.assertEqual(copied['source_count'], 2)
        self.assertTrue(copied['native_model']['supported'])
        self.assertEqual(copied['extraction']['template_selected']['id'], saved.json()['id'])
        with unit() as s:
            template = s.get(BusinessTemplate, saved.json()['id'])
            records = list(s.scalars(select(BusinessSourceFile).where(BusinessSourceFile.project_id == copied['id'])))
            actual = {record.filename: record.content for record in records}
            self.assertEqual(actual[template.xlsx_name], template.xlsx_content)
            self.assertEqual(actual[template.docx_name], template.docx_content)

    def test_template_survives_original_project_deletion(self):
        project = self.pair()
        saved, _ = self.save(project)
        self.assertEqual(self.remove(project).status_code, 200)
        response, _ = self.clone(saved.json())
        self.assertEqual(response.status_code, 200, response.text)

    def test_missing_word_and_stale_revision_are_rejected(self):
        project, _ = self.create()
        uploaded = self.upload(project, native_fixture(), 'model.xlsx').json()
        missing, _ = self.save(uploaded)
        self.assertEqual(missing.status_code, 422)
        completed = self.upload(uploaded, synthetic_source('.docx'), 'business-plan.docx').json()
        stale, _ = self.save(completed, {'revision': uploaded['revision'], 'title': 'Synthetic template',
                                       'request_key': str(uuid4())})
        self.assertEqual(stale.status_code, 409)

    def test_template_is_private_to_creator_and_company(self):
        project = self.pair()
        saved, _ = self.save(project)
        viewer, headers = self.actor('another_importer', 'finance')
        listed = viewer.get('/api/business-templates', params={'company_id': self.cid}, headers=headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()['items'], [])
        copied = viewer.post('/api/business-projects', headers=headers,
            json={'company_id': self.cid, 'title': 'Other project',
                  'request_key': str(uuid4()), 'template_id': saved.json()['id']})
        self.assertEqual(copied.status_code, 404)
        other_headers = {**self.headers, 'X-Company-ID': str(self.other)}
        result = self.client.post('/api/business-projects', headers=other_headers,
            json={'company_id': self.other, 'title': 'Other company',
                  'request_key': str(uuid4()), 'template_id': saved.json()['id']})
        self.assertEqual(result.status_code, 404)

    def test_create_request_cannot_be_replayed_with_another_template(self):
        project = self.pair()
        first, _ = self.save(project)
        second, _ = self.save(project, {'revision': project['revision'], 'title': 'Another template',
                                       'request_key': str(uuid4())})
        response, body = self.clone(first.json())
        self.assertEqual(response.status_code, 200, response.text)
        body['template_id'] = second.json()['id']
        repeated = self.client.post('/api/business-projects', json=body, headers=self.headers)
        self.assertEqual(repeated.status_code, 409)

    def test_template_cannot_be_used_for_generic_manual_project(self):
        saved, _ = self.save(self.pair())
        response, _ = self.clone(saved.json(), mode='manual')
        self.assertEqual(response.status_code, 422)

    def test_modified_saved_blob_cannot_silently_become_generic_model(self):
        saved, _ = self.save(self.pair())
        with unit(True) as s:
            template = s.get(BusinessTemplate, saved.json()['id'])
            template.xlsx_content = synthetic_source('.xlsx')
        response, _ = self.clone(saved.json())
        self.assertEqual(response.status_code, 409)


if __name__ == '__main__':
    unittest.main()
