"""Historical templates stay private; disabled Excel cannot be parsed or cloned."""
import hashlib
import unittest
from unittest.mock import patch
from uuid import uuid4
from tests import test_business_projects as fixtures
from app.db import BusinessProject, BusinessTemplate


class BusinessTemplateTests(unittest.TestCase):
    setUp = fixtures.BusinessProjectTests.setUp
    tearDown = fixtures.BusinessProjectTests.tearDown
    login = fixtures.BusinessProjectTests.login
    actor = fixtures.BusinessProjectTests.actor
    create = fixtures.BusinessProjectTests.create
    remove = fixtures.BusinessProjectTests.remove

    @classmethod
    def tearDownClass(cls):
        fixtures.engine.dispose()

    def legacy_template(self):
        workbook, word = b'legacy workbook must never be parsed', b'legacy document'
        with fixtures.unit(True) as s:
            t = BusinessTemplate(company_id=self.cid, created_by=self.uid, title='Saved template',
                request_key=str(uuid4()), xlsx_name='original.xlsx', docx_name='business.docx',
                xlsx_content=workbook, xlsx_size=len(workbook), xlsx_sha256=hashlib.sha256(workbook).hexdigest(),
                docx_content=word, docx_size=len(word), docx_sha256=hashlib.sha256(word).hexdigest())
            s.add(t); s.flush(); return t.id, workbook, word

    def save(self, project, client=None, headers=None, revision=None):
        return (client or self.client).post(f"/api/business-projects/{project['id']}/save-template",
            params={'company_id': self.cid}, headers=headers or self.headers,
            json={'revision': project['revision'] if revision is None else revision,
                  'title': 'New template', 'request_key': str(uuid4())})

    def clone(self, tid, company=None, client=None, headers=None):
        return (client or self.client).post('/api/business-projects', headers=headers or self.headers,
            json={'company_id': company or self.cid, 'title': 'Clone', 'request_key': str(uuid4()),
                  'template_id': tid})

    def test_save_is_disabled_before_source_parsing_and_preserves_revision(self):
        project, _ = self.create('manual')
        with patch('app.native_projects.native_candidate', side_effect=AssertionError('read workbook')):
            response = self.save(project)
        self.assertEqual(response.status_code, 410, response.text)
        with fixtures.unit() as s:
            self.assertEqual(s.get(BusinessProject, project['id']).revision, project['revision'])
            self.assertEqual(list(s.scalars(fixtures.select(BusinessTemplate))), [])

    def test_save_keeps_revision_and_owner_boundaries(self):
        project, _ = self.create('manual')
        self.assertEqual(self.save(project, revision=project['revision']+1).status_code, 409)
        peer, headers = self.actor('template_peer', 'finance')
        self.assertEqual(self.save(project, peer, headers).status_code, 403)
        reader, headers = self.actor('template_reader', 'investor')
        self.assertEqual(self.save(project, reader, headers).status_code, 403)

    def test_save_keeps_company_boundary(self):
        project, _ = self.create('manual')
        result = self.client.post(f"/api/business-projects/{project['id']}/save-template",
            params={'company_id': self.other}, headers={**self.headers, 'X-Company-ID': str(self.other)},
            json={'revision': project['revision'], 'title': 'Other company', 'request_key': str(uuid4())})
        self.assertEqual(result.status_code, 404)

    def test_historical_template_metadata_is_listed_without_upload_or_blobs(self):
        tid, workbook, word = self.legacy_template()
        result = self.client.get('/api/business-templates', params={'company_id': self.cid}, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse(result.json()['can_upload'])
        self.assertEqual(result.json()['items'][0]['id'], tid)
        self.assertNotIn('xlsx_content', result.json()['items'][0])
        with fixtures.unit() as s:
            t = s.get(BusinessTemplate, tid)
            self.assertEqual(bytes(t.xlsx_content), workbook)
            self.assertEqual(bytes(t.docx_content), word)

    def test_template_cannot_be_cloned_and_no_empty_project_is_created(self):
        tid, _, _ = self.legacy_template()
        with patch('app.native_projects.native_candidate', side_effect=AssertionError('read workbook')):
            result = self.clone(tid)
        self.assertEqual(result.status_code, 410, result.text)
        with fixtures.unit() as s:
            self.assertEqual(list(s.scalars(fixtures.select(BusinessProject))), [])

    def test_historical_template_remains_private_to_creator_and_company(self):
        tid, _, _ = self.legacy_template()
        peer, headers = self.actor('another_importer', 'finance')
        self.assertEqual(peer.get('/api/business-templates', params={'company_id': self.cid},
                                  headers=headers).json()['items'], [])
        self.assertEqual(self.clone(tid, client=peer, headers=headers).status_code, 404)
        self.assertEqual(self.clone(tid, company=self.other,
                         headers={**self.headers, 'X-Company-ID': str(self.other)}).status_code, 404)

    def test_historical_template_survives_project_deletion(self):
        project, _ = self.create('manual')
        tid, workbook, _ = self.legacy_template()
        self.assertEqual(self.remove(project).status_code, 200)
        with fixtures.unit() as s:
            self.assertEqual(bytes(s.get(BusinessTemplate, tid).xlsx_content), workbook)


if __name__ == '__main__':
    unittest.main()
