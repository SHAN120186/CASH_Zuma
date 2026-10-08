"""Synthetic native OOXML recalculation fixtures; no business source files."""
from io import BytesIO
from pathlib import Path
import re
import sys
import unittest
from zipfile import ZipFile, ZIP_DEFLATED
from defusedxml import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.native_workbook import inspect_native, recalculate, dependency_issues, Parser, ExcelError

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'


def workbook(rows, extra_sheet='', names='', extras=None, date1904=False):
    def worksheet(cells):
        return '<worksheet xmlns="'+NS+'"><sheetData>'+cells+'</sheetData><pageMargins left="0.5" right="0.5" top="0.7" bottom="0.7" header="0.3" footer="0.3"/></worksheet>'
    raw=BytesIO()
    with ZipFile(raw,'w',compression=ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        archive.writestr('xl/workbook.xml','<workbook xmlns="'+NS+'" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><workbookPr date1904="'+('1' if date1904 else '0')+'"/><sheets><sheet name="Main" sheetId="1" r:id="rId1"/><sheet name="Other tab" sheetId="2" r:id="rId2"/></sheets><definedNames>'+names+'</definedNames></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml',worksheet(rows))
        archive.writestr('xl/worksheets/sheet2.xml',worksheet(extra_sheet))
        archive.writestr('xl/styles.xml','<styleSheet xmlns="'+NS+'"><cellXfs count="1"><xf numFmtId="0"/></cellXfs></styleSheet>')
        for name,content in (extras or {}).items():archive.writestr(name,content)
    return raw.getvalue()


def numeric(cell,value):return '<c r="'+cell+'" s="0"><v>'+str(value)+'</v></c>'
def formula(cell,value,cached='999999',attributes=''):
    return '<c r="'+cell+'" s="0"><f'+attributes+'>'+value.replace('&','&amp;').replace('<','&lt;')+'</f><v>'+cached+'</v></c>'
def row(number,cells):return '<row r="'+str(number)+'">'+cells+'</row>'
def text(cell,value):return '<c r="'+cell+'" t="inlineStr"><is><t>'+value+'</t></is></c>'


class NativeWorkbookTests(unittest.TestCase):
    def test_fresh_arithmetic_overrides_and_untouched_parts(self):
        raw=workbook(row(1,numeric('A1',3)+formula('B1','A1*2')+formula('C1','B1+1')),
            extras={'xl/drawings/drawing1.xml':b'<unchanged/>','custom.bin':b'\x01\x00synthetic'})
        updated,result=recalculate(raw,{'Main':{'A1':4}})
        self.assertEqual(result['values']['Main']['B1'],8)
        self.assertEqual(result['values']['Main']['C1'],9)
        self.assertTrue(result['complete'])
        self.assertFalse(result['used_cached_formula_results'])
        with ZipFile(BytesIO(raw)) as source,ZipFile(BytesIO(updated)) as target:
            self.assertEqual(source.namelist(),target.namelist())
            for name in source.namelist():
                if name!='xl/worksheets/sheet1.xml':self.assertEqual(source.read(name),target.read(name),name)
            xml=target.read('xl/worksheets/sheet1.xml').decode()
            self.assertIn('<f>A1*2</f><v>8</v>',xml)
            self.assertIn('s="0"',xml)
        self.assertEqual(recalculate(raw)[1]['values']['Main']['B1'],6)

    def test_unsupported_and_broken_references_never_use_cached_numbers(self):
        raw=workbook(row(1,formula('A1','Missing!B1')+formula('B1','A1+2')+formula('C1','DANGER(A1)')+formula('D1','#REF!+1')))
        _,result=recalculate(raw)
        self.assertFalse(result['complete'])
        self.assertEqual(result['values']['Main']['A1'],'#REF!')
        self.assertEqual(result['values']['Main']['B1'],'#REF!')
        self.assertEqual(result['values']['Main']['C1'],'#NAME?')
        self.assertEqual(result['error_counts'],{'#REF!':3,'#NAME?':1})
        self.assertFalse(any(value==999999 for value in result['values']['Main'].values()))

    def test_lazy_if_iferror_and_no_blank_source_error_coercion(self):
        raw=workbook(row(1,formula('A1','IF(1=1,7,1/0)')+formula('B1','IFERROR(1/0,3)')+formula('C1','IF(0,1/0,2)')+formula('D1','IFERROR(#REF!,0)')+formula('E1','F1+1')+'<c r="F1" t="e"><v>#REF!</v></c>'))
        _,r=recalculate(raw)
        self.assertEqual([r['values']['Main'][c] for c in ['A1','B1','C1','D1','E1']],[7,3,2,0,'#REF!'])

    def test_excel_blanks_sum_averagea_and_sumproduct(self):
        raw=workbook(row(1,numeric('A1',2)+text('B1','ignored')+'<c r="C1" t="b"><v>1</v></c>')+
            row(2,formula('A2','SUM(A1:D1)')+formula('B2','AVERAGEA(A1:D1)')+formula('C2','SUMPRODUCT(A1:B1,A1:B1)')+formula('D2','D1+1')))
        _,r=recalculate(raw)
        self.assertEqual(r['values']['Main']['A2'],2)
        self.assertEqual(r['values']['Main']['B2'],1)
        self.assertEqual(r['values']['Main']['C2'],4)
        self.assertEqual(r['values']['Main']['D2'],1)
        _,blank=recalculate(workbook(row(1,formula('A1','+B1')+formula('C1','IF(TRUE,B1,7)'))))
        self.assertEqual(blank['values']['Main']['A1'],0)
        self.assertEqual(blank['values']['Main']['C1'],0)

    def test_named_refs_case_insensitive_scoped_and_quoted_sheet(self):
        names='<definedName name="FX">\'Other tab\'!$A$1</definedName><definedName name="FX" localSheetId="0">Main!$A$1</definedName>'
        raw=workbook(row(1,numeric('A1',2)+formula('B1','fx*3')+formula('C1',"'Other tab'!A1+1")),row(1,numeric('A1',5)+formula('B1','FX+1')),names)
        _,r=recalculate(raw)
        self.assertEqual(r['values']['Main']['B1'],6)
        self.assertEqual(r['values']['Main']['C1'],6)
        self.assertEqual(r['values']['Other tab']['B1'],6)

    def test_shared_formula_translation_and_declared_transpose(self):
        raw=workbook(row(1,numeric('A1',2)+formula('B1','A1*2',attributes=' t="shared" si="0" ref="B1:B2"')+formula('C1','TRANSPOSE(A1:A2)',attributes=' t="array" ref="C1:D1"')+numeric('D1',999))+
            row(2,numeric('A2',3)+formula('B2','',attributes=' t="shared" si="0"')))
        updated,r=recalculate(raw)
        self.assertEqual(r['values']['Main']['B2'],6)
        self.assertEqual([r['values']['Main'][c] for c in ['C1','D1']],[2,3])
        self.assertTrue(r['complete'])
        self.assertEqual(recalculate(updated)[1]['values']['Main']['B2'],6)

    def test_financial_functions_and_excel_date(self):
        raw=workbook(row(1,numeric('A1',-100)+numeric('B1',110)+formula('C1','IRR(A1:B1)')+formula('D1','NPV(10%,A1:B1)')+formula('E1','EDATE(60,0)')+formula('F1','EDATE(61,1)')))
        _,r=recalculate(raw)
        self.assertAlmostEqual(r['values']['Main']['C1'],.1,9)
        self.assertAlmostEqual(r['values']['Main']['D1'],0,9)
        self.assertEqual(r['values']['Main']['E1'],60)
        self.assertEqual(r['values']['Main']['F1'],92)

    def test_invalid_irr_and_explicit_formula_repair(self):
        raw=workbook(row(1,numeric('A1',2)+numeric('B1',3)+formula('C1','IRR(A1:B1)')+formula('D1','#REF!')))
        _,r=recalculate(raw,repairs={'Main':{'D1':'=SUM(A1:B1)'}})
        self.assertEqual(r['values']['Main']['C1'],'#NUM!')
        self.assertEqual(r['values']['Main']['D1'],5)
        with self.assertRaises(ValueError):recalculate(raw,{'Main':{'D1':5}})

    def test_cycles_names_limits_and_no_arbitrary_execution(self):
        raw=workbook(row(1,formula('A1','B1')+formula('B1','A1')+formula('C1','cycle')),
                     names='<definedName name="cycle">cycle</definedName>')
        _,r=recalculate(raw)
        self.assertEqual(r['error_counts'],{'#REF!':3})
        for expression in ['__import__("os")','[1]Sheet!A1','1;print(1)']:
            with self.assertRaises(ExcelError):Parser(expression).parse()
        _,bounded=recalculate(workbook(row(1,formula('A1','SUM(A1:XFD10000)'))))
        self.assertEqual(bounded['values']['Main']['A1'],'#VALUE!')
        self.assertEqual(inspect_native(raw)['arithmetic_verified'],False)

    def test_unary_plus_preserves_label_and_array_values(self):
        raw=workbook(row(1,text('A1','Sample label')+formula('B1','+A1')+numeric('C1',2)+formula('D1','+TRANSPOSE(C1:C2)',attributes=' t="array" ref="D1:E1"')+numeric('E1',77))+
            row(2,numeric('C2',3)))
        _,r=recalculate(raw)
        self.assertEqual(r['values']['Main']['B1'],'Sample label')
        self.assertEqual([r['values']['Main'][c] for c in ['D1','E1']],[2,3])
        self.assertTrue(r['complete'])

    def test_unresolved_caches_are_errors_not_old_numeric_and_selfclosing_keeps_next_cell(self):
        raw=workbook(row(1,'<c r="A1" s="0"/>'+formula('B1','#REF!',cached='4321')+formula('C1','B1+1',cached='4322')))
        updated,r=recalculate(raw)
        with ZipFile(BytesIO(updated)) as archive:
            tree=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        cells={node.attrib['r']:node for node in tree.findall('.//{'+NS+'}c')}
        self.assertEqual(cells['B1'].attrib['t'],'e')
        self.assertEqual(cells['B1'].find('{'+NS+'}v').text,'#REF!')
        self.assertEqual(cells['C1'].find('{'+NS+'}v').text,'#REF!')
        self.assertEqual(r['error_count'],2)
        self.assertTrue(r['issues'][0]['root'])

    def test_shared_anchor_repair_does_not_break_followers_on_reopen(self):
        raw=workbook(row(1,numeric('A1',2)+formula('B1','A1*2',attributes=' t="shared" si="0" ref="B1:B2"'))+
            row(2,numeric('A2',3)+formula('B2','',attributes=' t="shared" si="0"')))
        updated,r=recalculate(raw,repairs={'Main':{'B1':'=A1*3'}})
        self.assertEqual(r['values']['Main']['B1'],6)
        self.assertEqual(r['values']['Main']['B2'],6)
        self.assertEqual(recalculate(updated)[1]['values']['Main']['B2'],6)

    def test_chart_caches_follow_changed_input_and_clear_failed_points(self):
        chart='<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:numRef><c:f>Main!$B$1:$C$1</c:f><c:numCache><c:formatCode>0.00</c:formatCode><c:ptCount val="2"/><c:pt idx="0"><c:v>999</c:v></c:pt><c:pt idx="1"><c:v>998</c:v></c:pt></c:numCache></c:numRef></c:chartSpace>'
        raw=workbook(row(1,numeric('A1',2)+formula('B1','A1*3')+formula('C1','#REF!')),extras={'xl/charts/chart1.xml':chart})
        updated,r=recalculate(raw,{'Main':{'A1':4}})
        with ZipFile(BytesIO(updated)) as archive:xml=archive.read('xl/charts/chart1.xml').decode()
        self.assertIn('<c:v>12</c:v>',xml)
        self.assertNotIn('999',xml)
        self.assertNotIn('998',xml)
        self.assertIn('<c:formatCode>0.00</c:formatCode>',xml)
        self.assertEqual(r['chart_cache_status'],'fresh_with_gaps')
        self.assertEqual(r['chart_caches_updated'],1)

    def test_extreme_input_exponents_are_rejected_before_sparse_formatting(self):
        for value in ['1e-999999999999','1e999999999999']:
            with self.assertRaises(ValueError):recalculate(workbook(row(1,numeric('A1',value))))

    def test_output_dependency_gate_keeps_active_errors_and_ignores_inactive_tail(self):
        raw=workbook(row(1,numeric('A1',2)+formula('B1','A1*3')+formula('C1','#REF!')+formula('D1','C1+1')))
        _,r=recalculate(raw)
        self.assertFalse(r['complete'])
        good=dependency_issues(r,{'Main':['B1']})
        self.assertTrue(good['complete'])
        self.assertEqual(good['dependency_count'],2)
        bad=dependency_issues(r,{'Main':['D1']})
        self.assertFalse(bad['complete'])
        self.assertEqual(bad['error_count'],2)
        self.assertEqual(bad['root_issues'][0]['cell'],'C1')

    def test_inspection_reports_function_scope_without_claiming_calculation(self):
        raw=workbook(row(1,formula('A1','SUM(1,2)')+formula('B1','UNKNOWN(3)')))
        summary=inspect_native(raw)
        self.assertEqual(summary['formula_count'],2)
        self.assertEqual(summary['unsupported_functions'],['UNKNOWN'])
        self.assertFalse(summary['arithmetic_verified'])


if __name__=='__main__':unittest.main()
