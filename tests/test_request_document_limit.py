"""Exercise upload limits through the real HTTP middleware with temporary data."""
import io
import unittest
import zipfile
import tests.test_site as base


class RequestDocumentLimitTests(base.SiteTests):
    @staticmethod
    def compressed_office(suffix, expanded_mib):
        """Synthetic valid OOXML with a large XML comment, never a financial file.

        Stream repetitive padding so the test exercises a large expanded member
        without allocating its full contents or requiring a large fixture.
        """
        output=io.BytesIO()
        workbook=suffix=='.xlsx'
        folder='xl' if workbook else 'word'
        main_type=('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml' if workbook
                   else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml')
        main_part='xl/workbook.xml' if workbook else 'word/document.xml'
        content_types=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/{main_part}" ContentType="{main_type}"/>'
            + ('<Override PartName="/xl/worksheets/sheet1.xml" '
               'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' if workbook else '')
            + '</Types>')
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
            archive.writestr('[Content_Types].xml',content_types)
            archive.writestr('_rels/.rels',
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                f'Target="{main_part}"/></Relationships>')
            if workbook:
                archive.writestr('xl/workbook.xml',
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    '<sheets><sheet name="Synthetic" sheetId="1" r:id="rId1"/></sheets></workbook>')
                archive.writestr('xl/_rels/workbook.xml.rels',
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                    'Target="worksheets/sheet1.xml"/></Relationships>')
                member='xl/worksheets/sheet1.xml'
                beginning=b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/><!--'
                ending=b'--></worksheet>'
            else:
                member=f'{folder}/document.xml'
                beginning=b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/><!--'
                ending=b'--></w:body></w:document>'
            with archive.open(member,'w') as part:
                part.write(beginning)
                block=b' '*(1024*1024)
                for _ in range(expanded_mib):part.write(block)
                part.write(ending)
        return output.getvalue()

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

    def test_large_compressed_xlsx_upload_download_and_retry_are_exactly_once(self):
        content=self.compressed_office('.xlsx',101)
        self.assertLess(len(content),10*1024*1024)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            expanded=sum(item.file_size for item in archive.infolist())
            self.assertGreater(expanded,100*1024*1024)
            self.assertLess(expanded,128*1024*1024)
        draft=self.request(submit=False)
        self.assertEqual(draft.status_code,200,draft.text)
        rid=draft.json()['id']
        uploaded=self.upload(rid,content=content,name='synthetic-expanded-101.xlsx')
        self.assertEqual(uploaded.status_code,200,uploaded.text)
        first=uploaded.json()
        downloaded=self.client.get(first['url'],headers=self.h)
        self.assertEqual(downloaded.status_code,200)
        self.assertEqual(downloaded.content,content)
        self.assertEqual(downloaded.headers['content-type'],
                         'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        retried=self.upload(rid,content=content,name='synthetic-expanded-101.xlsx')
        self.assertEqual(retried.status_code,200,retried.text)
        self.assertTrue(retried.json()['duplicate'])
        self.assertEqual(retried.json()['id'],first['id'])
        self.assertEqual(retried.json()['request_version'],first['request_version'])
        history=self.client.get(f'/api/requests/{rid}/documents',headers=self.h,params={'history':True})
        self.assertEqual(history.status_code,200,history.text)
        self.assertEqual([row['id'] for row in history.json()],[first['id']])
        row=self.row(rid)
        self.assertEqual(row['status'],'draft')
        self.assertEqual(sum(item['action']=='Добавлен документ заявки' for item in row['history']),1)

    def test_xlsx_expanded_over_128_mebibytes_is_rejected_with_its_actual_limit(self):
        content=self.compressed_office('.xlsx',129)
        self.assertLess(len(content),10*1024*1024)
        draft=self.request(submit=False)
        self.assertEqual(draft.status_code,200,draft.text)
        rid=draft.json()['id']
        result=self.upload(rid,content=content,name='synthetic-expanded-129.xlsx')
        self.assertEqual(result.status_code,422,result.text)
        self.assertEqual(result.json()['detail'],'Содержимое XLSX после распаковки превышает 128 МБ.')
        self.assertEqual(self.client.get(f'/api/requests/{rid}/documents',headers=self.h).json(),[])

    def test_docx_expanded_over_50_mebibytes_retains_its_original_limit(self):
        content=self.compressed_office('.docx',51)
        self.assertLess(len(content),10*1024*1024)
        draft=self.request(submit=False)
        self.assertEqual(draft.status_code,200,draft.text)
        rid=draft.json()['id']
        result=self.upload(rid,content=content,name='synthetic-expanded-51.docx')
        self.assertEqual(result.status_code,422,result.text)
        self.assertEqual(result.json()['detail'],'Содержимое DOCX после распаковки превышает 50 МБ.')
        self.assertEqual(self.client.get(f'/api/requests/{rid}/documents',headers=self.h).json(),[])


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(RequestDocumentLimitTests(name) for name in RequestDocumentLimitTests.__dict__
                              if name.startswith('test_'))


def tearDownModule():
    base.tearDownModule()
