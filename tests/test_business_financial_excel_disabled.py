"""Disabled financial Excel never parses sources; manual reports and stored files survive."""
import hashlib
import io
import json
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tests import test_business_projects as fixtures
from app.db import BusinessProject, BusinessSourceFile, ReportArchiveFile
from sqlalchemy import event, func, select


class FinancialExcelDisabledTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        fixtures.engine.dispose()

    setUp = fixtures.BusinessProjectTests.setUp
    tearDown = fixtures.BusinessProjectTests.tearDown
    login = fixtures.BusinessProjectTests.login
    actor = fixtures.BusinessProjectTests.actor
    create = fixtures.BusinessProjectTests.create
    upload = fixtures.BusinessProjectTests.upload
    analyse = fixtures.BusinessProjectTests.analyse
    generate = fixtures.BusinessProjectTests.generate
    save_draft = fixtures.BusinessProjectTests.save_draft

    def legacy_excel(self, native=False, mode='files', zipped=False):
        project, _ = self.create(mode)
        content = b'legacy workbook must never be opened'
        filename = 'original.xlsx'
        if zipped:
            stream = io.BytesIO()
            with ZipFile(stream, 'w') as archive:
                archive.writestr('old/original.xlsx', content)
                archive.writestr('note.txt', b'Synthetic evidence')
            filename, content = 'old-folder.zip', stream.getvalue()
        with fixtures.unit(True) as s:
            row = BusinessSourceFile(company_id=self.cid, project_id=project['id'], relative_path=filename,
                                     filename=filename, content=content, size=len(content),
                                     sha256=hashlib.sha256(content).hexdigest())
            s.add(row); s.flush(); source_id = row.id
            p = s.get(BusinessProject, project['id'])
            extraction = json.loads(p.extraction_json)
            if native:
                extraction['native'] = {'supported': True, 'source_name': 'original.xlsx',
                                        'metrics': [{'key': 'saved', 'calculated': 42}],
                                        'input_review': {'complete': True, 'items': []}}
            p.extraction_json = json.dumps(extraction)
        return project, source_id, content

    def test_detail_uses_saved_metadata_without_loading_blobs_or_workbooks(self):
        project, _, _ = self.legacy_excel(native=True)
        statements = []
        def capture(conn, cursor, statement, params, context, many):
            statements.append(statement)
        event.listen(fixtures.engine, 'before_cursor_execute', capture)
        try:
            with patch('app.native_projects.native_candidate', side_effect=AssertionError('workbook read')):
                response = self.client.get(f"/api/business-projects/{project['id']}",
                                           params={'company_id': self.cid}, headers=self.headers)
        finally:
            event.remove(fixtures.engine, 'before_cursor_execute', capture)
        self.assertEqual(response.status_code, 200, response.text)
        metadata = response.json()['native_model']
        self.assertEqual(metadata['metrics'][0]['calculated'], 42)
        self.assertTrue(metadata['read_only'])
        self.assertFalse(metadata['calculation_enabled'])
        self.assertFalse(response.json()['capabilities']['excel_recalculation'])
        self.assertFalse(any('business_source_files.content' in statement for statement in statements))

    def test_excel_and_zip_upload_rejected_before_excel_validation(self):
        project, _ = self.create('manual')
        with patch('app.business_projects.valid_file', side_effect=AssertionError('parsed workbook')):
            for ext in ('.xlsx', '.xltx'):
                result = self.upload(project, b'not a workbook', 'original'+ext)
                self.assertEqual(result.status_code, 410, result.text)
            stream = io.BytesIO()
            with ZipFile(stream, 'w') as archive:
                archive.writestr('nested/original.XLSX', b'not a workbook')
            result = self.upload(project, stream.getvalue(), 'folder.zip')
            self.assertEqual(result.status_code, 410, result.text)
        with fixtures.unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(BusinessSourceFile)), 0)

    def test_native_actions_and_recalculated_download_disabled_without_snapshot(self):
        project, _, _ = self.legacy_excel(native=True)
        with patch('app.native_projects.snapshot', side_effect=AssertionError('read source')):
            for path, body in [('preview', {'revision': project['revision']}),
                               ('generate', {'revision': project['revision'], 'start': '2027-01-01'})]:
                result = self.client.post(f"/api/business-projects/{project['id']}/native/{path}",
                                          params={'company_id': self.cid}, headers=self.headers, json=body)
                self.assertEqual(result.status_code, 410, result.text)
            result = self.client.get(f"/api/business-projects/{project['id']}/native.xlsx",
                                     params={'company_id': self.cid, 'revision': project['revision']}, headers=self.headers)
            self.assertEqual(result.status_code, 410, result.text)
            stale = self.client.post(f"/api/business-projects/{project['id']}/native/preview",
                                      params={'company_id': self.cid}, headers=self.headers,
                                      json={'revision': project['revision']+1})
            self.assertEqual(stale.status_code, 409)
            foreign = self.client.post(f"/api/business-projects/{project['id']}/native/preview",
                                        params={'company_id': self.other},
                                        headers={**self.headers, 'X-Company-ID': str(self.other)},
                                        json={'revision': project['revision']})
            self.assertEqual(foreign.status_code, 404)

    def test_legacy_excel_analysis_blocked_and_original_download_preserved(self):
        for zipped in (False, True):
            project, source_id, content = self.legacy_excel(zipped=zipped)
            with patch('app.business_projects.expanded_sources', side_effect=AssertionError('expanded source')):
                result = self.analyse(project)
                self.assertEqual(result.status_code, 410, result.text)
            download = self.client.get(f"/api/business-projects/{project['id']}/sources/{source_id}/download",
                                        params={'company_id': self.cid}, headers=self.headers)
            self.assertEqual(download.status_code, 200, download.text)
            self.assertEqual(download.content, content)

    def test_disabled_native_keeps_non_owner_and_read_only_permissions(self):
        project, _, _ = self.legacy_excel(native=True)
        for name, role in [('native_peer', 'finance'), ('native_reader', 'investor')]:
            client, headers = self.actor(name, role)
            response = client.post(f"/api/business-projects/{project['id']}/native/preview",
                                   params={'company_id': self.cid}, headers=headers,
                                   json={'revision': project['revision']})
            self.assertEqual(response.status_code, 403, response.text)

    def test_manual_draft_and_generation_ignore_legacy_excel_without_deleting_it(self):
        project, source_id, content = self.legacy_excel(mode='manual')
        with patch('app.native_projects.native_candidate', side_effect=AssertionError('read workbook')):
            saved = self.save_draft(project, fixtures.model())
            self.assertEqual(saved.status_code, 200, saved.text)
            ready = self.generate(saved.json())
            self.assertEqual(ready.status_code, 200, ready.text)
            self.assertEqual(ready.json()['status'], 'ready')
        with fixtures.unit() as s:
            self.assertEqual(bytes(s.get(BusinessSourceFile, source_id).content), content)
            self.assertEqual(s.scalar(select(func.count()).select_from(ReportArchiveFile)), 4)

    def test_non_excel_source_analysis_still_works(self):
        project, _ = self.create()
        uploaded = self.upload(project)
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        analysed = self.analyse(uploaded.json())
        self.assertEqual(analysed.status_code, 200, analysed.text)
        self.assertEqual(analysed.json()['status'], 'ready')


if __name__ == '__main__':
    unittest.main()
