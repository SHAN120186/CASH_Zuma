"""Exercise upload limits through the real HTTP middleware with temporary data."""
import unittest
import tests.test_site as base


class RequestDocumentLimitTests(base.SiteTests):
    def test_request_files_accept_ten_mebibytes_but_not_more(self):
        rules=self.client.get('/api/request-rules',headers=self.h)
        self.assertEqual(rules.status_code,200,rules.text)
        self.assertEqual(rules.json()['max_document_mb'],10)
        draft=self.request(submit=False)
        self.assertEqual(draft.status_code,200,draft.text)
        rid=draft.json()['id']
        # Raw PDF uploads exercise both Content-Length middleware and stream reader.
        for size, expected in ((5*1024*1024+1,200),(10*1024*1024,200),(10*1024*1024+1,413)):
            content=b'%PDF-'+b'x'*(size-5)
            result=self.upload(rid,'other',content=content,name=f'synthetic-{size}.pdf')
            self.assertEqual(result.status_code,expected,result.text)
        # The separate ledger-document endpoint must retain its existing 5 MiB limit.
        result=self.client.post('/api/ledger/1/document',content=b'%PDF-'+b'x'*(5*1024*1024),
                                headers={**self.h,'X-Filename':'synthetic.pdf'})
        self.assertEqual(result.status_code,413,result.text)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite([RequestDocumentLimitTests('test_request_files_accept_ten_mebibytes_but_not_more')])


def tearDownModule():
    base.tearDownModule()
