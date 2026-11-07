"""Retired model endpoints reject requests before reading Excel or Drive."""
import unittest
from unittest.mock import patch
from sqlalchemy import select,func
import tests.test_site as base
from app.db import ModelVersion,Ledger,unit


class DisabledModelImportTests(base.SiteTests):
    def test_model_upload_and_drive_sync_are_disabled_without_mutation(self):
        with unit() as s:
            before=(s.scalar(select(func.count()).select_from(ModelVersion)),s.scalar(select(func.count()).select_from(Ledger)))
        with patch('app.main.import_snapshot',side_effect=AssertionError('No workbook import')),\
             patch('app.drive.download_model',side_effect=AssertionError('No Drive request')):
            upload=self.client.post('/api/model/upload',content=b'not even a workbook',headers={**self.h,'X-Filename':'synthetic.xlsx'})
            self.assertEqual(upload.status_code,410,upload.text)
            sync=self.client.post('/api/model/drive-sync',headers=self.h)
            self.assertEqual(sync.status_code,410,sync.text)
        with unit() as s:
            self.assertEqual((s.scalar(select(func.count()).select_from(ModelVersion)),s.scalar(select(func.count()).select_from(Ledger))),before)

    def test_retired_endpoints_still_enforce_authentication_permissions_and_csrf(self):
        for endpoint in ('/api/model/upload','/api/model/drive-sync'):
            with base.TestClient(base.app) as anonymous:
                self.assertEqual(anonymous.post(endpoint,content=b'x').status_code,401)
            self.assertEqual(self.client.post(endpoint,content=b'x').status_code,403)
        client,headers=self.make_user('sales','disabled_model_sales')
        try:
            for endpoint in ('/api/model/upload','/api/model/drive-sync'):
                self.assertEqual(client.post(endpoint,content=b'x',headers=headers).status_code,403)
        finally:client.close()


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(DisabledModelImportTests(name) for name in DisabledModelImportTests.__dict__ if name.startswith('test_'))


def tearDownModule():base.tearDownModule()
