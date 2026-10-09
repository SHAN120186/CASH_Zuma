"""Synthetic template slots, VML fallback and opaque ZIP-part preservation."""
from io import BytesIO
from pathlib import Path
import sys
import unittest
from zipfile import ZipFile,ZIP_DEFLATED
from defusedxml import ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.native_documents import patch_business_docx,report_sources,prepare_pdf_layout,COST_ROWS,COST_COLUMNS,WORD,NO_LOSS_TEXT,POSITIVE_CASH_TEXT,PROFIT_CELLS,CASH_FLOW_CELLS,CUMULATIVE_CASH_CELLS,_containers,OBSOLETE_SECTOR_PARAGRAPH,OBSOLETE_SECTOR_TEXT,PHARMACEUTICAL_MARKET_TEXT


def paragraph(text):return '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>'+text+'</w:t></w:r></w:p>'
def cell(text):
    # Deliberately split one number across differently styled Word runs.
    return '<w:tc><w:tcPr><w:tcW w:w="1600" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>'+text[:1]+'</w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>'+text[1:]+'</w:t></w:r></w:p></w:tc>'


def template():
    cost={(r,c) for r,_ in COST_ROWS for c,_ in COST_COLUMNS}
    tables=[]
    for index in range(9):
        rows=[]
        for row in range(15 if index==0 else 4):
            cells=[]
            for col in range(5 if index==0 else 2):
                value='10' if index==0 and (row,col) in cost or index==6 and col==1 else 'label'
                if index==0 and (row,col)==(14,1):value='1 000'
                if index==6 and (row,col)==(3,1):value='10%'
                cells.append(cell(value))
            rows.append('<w:tr>'+''.join(cells)+'</w:tr>')
        tables.append('<w:tbl><w:tblPr><w:tblStyle w:val="Retained"/></w:tblPr>'+''.join(rows)+'</w:tbl>')
    paragraphs=['<w:p/>' for _ in range(147)]
    paragraphs[0]=paragraph('Synthetic template, no business data')
    paragraphs[12]='<w:p><mc:AlternateContent><mc:Choice Requires="w"><w:r><w:pict><v:shape><v:textbox>'+paragraph('Кредит 1 000 долларов США')+'</v:textbox></v:shape></w:pict></w:r></mc:Choice><mc:Fallback><w:r><w:pict><v:shape><v:textbox>'+paragraph('Кредит 1 000 долларов США')+'</v:textbox></v:shape></w:pict></w:r></mc:Fallback></mc:AlternateContent></w:p>'
    paragraphs[146]=paragraph('Для приобретения оборудования кредит 1 000 долларов США; код 123 сохраняется.')
    xml='<w:document xmlns:w="'+WORD+'" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body>'+''.join(paragraphs)+''.join(tables)+'<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>'
    raw=BytesIO()
    with ZipFile(raw,'w',compression=ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml','<Types/>')
        archive.writestr('word/document.xml',xml)
        archive.writestr('word/styles.xml','<retained-styles/>')
        archive.writestr('word/header1.xml','<retained-header/>')
        archive.writestr('word/_rels/document.xml.rels','<retained-relationships/>')
        archive.writestr('word/media/image1.bin',b'\x00\xffOpaque synthetic media')
    return raw.getvalue()


def values():
    result={'Стоим_проекта':{col+str(row):10 for _,row in COST_ROWS for _,col in COST_COLUMNS},'ТОЧКА':{'B6':10,'B7':10,'B8':10,'B10':.1}}
    result['Стоим_проекта'].update(B38=1000,D34=1000)
    return result


def package(xml,styles=None):
    output=BytesIO()
    with ZipFile(output,'w',compression=ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml',xml)
        if styles is not None:archive.writestr('word/styles.xml',styles)
        archive.writestr('word/media/opaque.bin',b'unchanged synthetic image')
    return output.getvalue()


def change_xml(raw,transform):
    output=BytesIO()
    with ZipFile(BytesIO(raw)) as source,ZipFile(output,'w') as target:
        for item in source.infolist():target.writestr(item,transform(source.read(item)) if item.filename=='word/document.xml' else source.read(item))
    return output.getvalue()


class NativeDocumentTests(unittest.TestCase):
    def test_verified_obsolete_sector_paragraph_changes_only_its_text_and_keeps_runs_and_zip_parts(self):
        custom='Авторский пример: на рынке текстиля сохраняется собственная оценка.'
        split=OBSOLETE_SECTOR_TEXT.index('на рынке')
        styled=('<w:p><w:pPr><w:spacing w:after="100"/></w:pPr>'
                '<w:r><w:rPr><w:i/></w:rPr><w:t>'+OBSOLETE_SECTOR_TEXT[:split]+'</w:t></w:r>'
                '<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">'+OBSOLETE_SECTOR_TEXT[split:]+'</w:t></w:r></w:p>')
        raw=change_xml(patch_business_docx(template(),values())[0],lambda xml:xml.replace(b'</w:body>',
            ('<w:p/>'*(OBSOLETE_SECTOR_PARAGRAPH-147)+styled+paragraph(custom)+paragraph(OBSOLETE_SECTOR_TEXT)+'</w:body>').encode()))
        updated,meta=patch_business_docx(raw,values())
        edits=[edit for edit in meta['edits'] if edit.get('reason')=='obsolete_sector_reference']
        self.assertEqual(len(edits),1)
        self.assertEqual(edits[0]['paragraph'],OBSOLETE_SECTOR_PARAGRAPH)
        self.assertEqual(meta['updated_fields'],1)
        with ZipFile(BytesIO(raw)) as before,ZipFile(BytesIO(updated)) as after:
            self.assertEqual(before.namelist(),after.namelist())
            for name in before.namelist():
                if name!='word/document.xml':self.assertEqual(before.read(name),after.read(name))
            original=before.read('word/document.xml');patched=after.read('word/document.xml')
        original_spans=_containers(original);patched_spans=_containers(patched)
        for key,span in original_spans.items():
            if key==('paragraph',OBSOLETE_SECTOR_PARAGRAPH):continue
            self.assertEqual(original[slice(*span)],patched[slice(*patched_spans[key])],key)
        content=patched[slice(*patched_spans['paragraph',OBSOLETE_SECTOR_PARAGRAPH])]
        tree=ET.fromstring(('<root xmlns:w="'+WORD+'">').encode()+content+b'</root>')
        self.assertEqual(''.join(tree.itertext()),PHARMACEUTICAL_MARKET_TEXT+' ')
        self.assertEqual(content.count(b'<w:r>'),2)
        self.assertIn(b'<w:rPr><w:i/></w:rPr>',content)
        self.assertIn(b'<w:rPr><w:b/></w:rPr>',content)
        self.assertIn(b'<w:spacing w:after="100"/>',content)
        self.assertIn(custom.encode(),patched)
        self.assertEqual(patched.count(OBSOLETE_SECTOR_TEXT.encode()),1,'an identical sentence outside the verified slot is preserved')

    def test_obsolete_sector_slot_with_custom_or_nonexact_text_is_unchanged(self):
        for text in (OBSOLETE_SECTOR_TEXT.replace('бурно','умеренно'),
                     OBSOLETE_SECTOR_TEXT.rstrip(), ' '+OBSOLETE_SECTOR_TEXT,
                     PHARMACEUTICAL_MARKET_TEXT):
            raw=change_xml(patch_business_docx(template(),values())[0],lambda xml:xml.replace(b'</w:body>',
                ('<w:p/>'*(OBSOLETE_SECTOR_PARAGRAPH-147)+paragraph(text)+'</w:body>').encode()))
            updated,meta=patch_business_docx(raw,values())
            self.assertEqual(updated,raw)
            self.assertFalse(any(edit.get('reason')=='obsolete_sector_reference' for edit in meta['edits']))

    def test_only_numeric_text_changes_other_parts_and_native_markup_survive(self):
        raw=template();data=values();data['Стоим_проекта'].update(B10=20,D34=2000,B38=1500)
        updated,meta=patch_business_docx(raw,data)
        self.assertEqual(meta['mapped_table_cells'],49)
        self.assertEqual(meta['mapped_paragraph_mentions'],3)
        self.assertGreater(meta['updated_fields'],3)
        with ZipFile(BytesIO(raw)) as source,ZipFile(BytesIO(updated)) as target:
            self.assertEqual(source.namelist(),target.namelist())
            for part in source.namelist():
                if part!='word/document.xml':self.assertEqual(source.read(part),target.read(part),part)
            xml=target.read('word/document.xml').decode()
            original=source.read('word/document.xml').decode()
        self.assertEqual(xml.count('<v:shape>'),original.count('<v:shape>'))
        self.assertEqual(xml.count('<w:rPr><w:b/></w:rPr>'),original.count('<w:rPr><w:b/></w:rPr>'))
        self.assertIn('Кредит 2 000 долларов США',xml)
        self.assertIn('код 123 сохраняется',xml)
        self.assertEqual(len(report_sources(raw)),50)

    def test_unresolved_required_value_blocks_instead_of_preserving_old_number(self):
        data=values();data['ТОЧКА']['B6']='#REF!'
        with self.assertRaises(ValueError):patch_business_docx(template(),data)
        del data['ТОЧКА']['B6']
        with self.assertRaises(ValueError):patch_business_docx(template(),data)

    def test_explicit_zero_and_rounding_are_rendered_without_missing_value_guess(self):
        data=values();data['Стоим_проекта'].update(B10=0,F10=10.5)
        updated,meta=patch_business_docx(template(),data)
        edits={(e.get('table'),e.get('row'),e.get('cell')):e['after'] for e in meta['edits'] if e['kind']=='table'}
        self.assertEqual(edits[0,2,1],'0')
        self.assertEqual(edits[0,2,4],'11')
        data['Стоим_проекта']['B10']=None
        with self.assertRaises(ValueError):patch_business_docx(template(),data)

    def test_template_shape_and_untrusted_xml_are_not_silently_reinterpreted(self):
        data=values()
        with ZipFile(BytesIO(template())) as source:
            content=source.read('word/document.xml')
        for xml in [content.replace(b'<w:tbl>',b'<w:other>'),b'<!DOCTYPE w:document [<!ENTITY unsafe "x">]><w:document xmlns:w="'+WORD.encode()+b'"><w:body/></w:document>']:
            stream=BytesIO()
            with ZipFile(stream,'w') as archive:archive.writestr('word/document.xml',xml)
            with self.assertRaises(Exception):patch_business_docx(stream.getvalue(),data)

    def test_fractional_currency_format_and_whole_archive_identity(self):
        raw=change_xml(template(),lambda xml:xml.replace('Кредит 1 000'.encode(),'Кредит 10'.encode()).replace('кредит 1 000'.encode(),'кредит 10'.encode()).replace(b' 000</w:t>',b' 000,00</w:t>'))
        data=values();data['Стоим_проекта']['D34']=10
        updated,meta=patch_business_docx(raw,data)
        self.assertEqual(meta['updated_fields'],0)
        self.assertEqual(updated,raw)
        data['Стоим_проекта']['B38']=1000.5
        _,meta=patch_business_docx(raw,data)
        self.assertEqual(meta['edits'][0]['after'],'1 000,50')

    def test_tax_rate_prose_uses_current_source_without_integer_rate_rounding(self):
        def add_vat(xml):
            xml=xml.decode()
            return xml.replace('</w:body>',('<w:p/>'*(521-147)+paragraph('НДС – 10%.'))+'</w:body>').encode()
        raw=change_xml(template(),add_vat);data=values();data['НДС']={'F4':.125}
        updated,meta=patch_business_docx(raw,data)
        self.assertTrue(any(s=={'sheet':'НДС','cell':'F4'} for s in report_sources(raw)))
        self.assertTrue(any(e['after']=='12,5%' for e in meta['edits']))
        with ZipFile(BytesIO(updated)) as target:self.assertIn('НДС – 12,5%.',target.read('word/document.xml').decode())
        data['НДС']['F4']='#VALUE!'
        with self.assertRaises(ValueError):patch_business_docx(raw,data)

    def test_staff_three_numbers_sharing_a_styled_run_are_all_updated(self):
        sentence='Штат работников предприятия составляет 3 человек. Административно-управленческий аппарат – 1 человек, производственные персонал –2 человек (приложение №1).'
        raw=change_xml(template(),lambda xml:xml.replace(b'</w:body>',('<w:p/>'*(506-147)+paragraph(sentence)+'</w:body>').encode()))
        data=values();data['Труд']={'B35':20,'B48':10}
        updated,meta=patch_business_docx(raw,data)
        self.assertEqual(sum(e.get('paragraph')==506 for e in meta['edits']),3)
        with ZipFile(BytesIO(updated)) as target:
            xml=target.read('word/document.xml').decode()
            self.assertIn('составляет 30 человек',xml)
            self.assertIn('аппарат – 10 человек',xml)
            self.assertIn('персонал –20 человек (приложение №1)',xml)

    def test_utilities_costs_and_total_use_the_fresh_native_cells(self):
        def utility_table(xml):
            rows=[['label']*5,['label']*5,['Электроэнергия','unit','10','2,00','20'],['Газ','unit','4','3,00','12'],['ВСЕГО','','','','32']]
            table=('<w:tbl>'+''.join('<w:tr>'+''.join(cell(v) for v in row)+'</w:tr>' for row in rows)+'</w:tbl>').encode()
            start,end=_containers(xml)['table',5]
            return xml[:start]+table+xml[end:]
        raw=change_xml(template(),utility_table);data=values()
        data['Труд']={'C5':10,'D5':4,'E5':40,'C8':4,'D8':3,'E8':12,'E9':52}
        _,meta=patch_business_docx(raw,data)
        edits={(e.get('row'),e.get('cell')):e['after'] for e in meta['edits'] if e.get('table')==5}
        self.assertEqual(edits,{(2,3):'4,00',(2,4):'40',(4,4):'52'})
        self.assertEqual(sum(s['sheet']=='Труд' for s in report_sources(raw)),7)
        data['Труд']['E9']='#DIV/0!'
        with self.assertRaises(ValueError):patch_business_docx(raw,data)

    def test_optimistic_conclusions_require_every_fresh_month_not_annual_totals(self):
        def add_conclusions(xml):
            xml=xml.decode()
            return xml.replace('</w:body>','<w:p/>'*(522-147)+paragraph(NO_LOSS_TEXT)+'<w:p/>'*(538-523)+paragraph(POSITIVE_CASH_TEXT)+'</w:body>').encode()
        raw=change_xml(template(),add_conclusions);data=values()
        data['Приб_Убыт']={cell:1 for cell in PROFIT_CELLS}
        data['Поток_нал']={cell:1 for cell in CASH_FLOW_CELLS+CUMULATIVE_CASH_CELLS}
        _,meta=patch_business_docx(raw,data)
        self.assertFalse(any(e['kind']=='narrative' for e in meta['edits']))
        self.assertEqual(sum(s['sheet']=='Приб_Убыт' for s in report_sources(raw)),36)
        self.assertEqual(sum(s['sheet']=='Поток_нал' for s in report_sources(raw)),72)
        data['Приб_Убыт']['M64']=-1
        data['Поток_нал']['M83']=0
        updated,meta=patch_business_docx(raw,data)
        self.assertEqual(sum(e['kind']=='narrative' for e in meta['edits']),2)
        with ZipFile(BytesIO(updated)) as target:
            xml=target.read('word/document.xml').decode()
            self.assertIn('имеются месяцы с убытком',xml)
            self.assertIn('не подтверждается',xml)
            self.assertNotIn(NO_LOSS_TEXT,xml)
        data['Поток_нал']['M83']='#REF!'
        with self.assertRaises(ValueError):patch_business_docx(raw,data)

    def test_first_year_cumulative_cash_uses_row32_and_missing_or_error_blocks(self):
        raw=change_xml(template(),lambda xml:xml.replace(b'</w:body>',('<w:p/>'*(538-147)+paragraph(POSITIVE_CASH_TEXT)+'</w:body>').encode()))
        data=values();data['Поток_нал']={cell:1 for cell in CASH_FLOW_CELLS+CUMULATIVE_CASH_CELLS}
        addresses=[s['cell'] for s in report_sources(raw) if s['sheet']=='Поток_нал']
        targets=set(addresses)
        self.assertTrue({'C32','N32','B58','M58','B83','M83'}.issubset(targets))
        self.assertTrue({'C30','N30','B56','M56','B81','M81'}.issubset(targets))
        self.assertFalse(any(cell.endswith('30') for cell in CUMULATIVE_CASH_CELLS))
        self.assertEqual(addresses.count('C30'),1)
        self.assertEqual(addresses.count('C32'),1)
        self.assertFalse({'C27','B55','B80'} & targets)
        self.assertEqual(len(targets),72)
        # Positive net cash in row30 cannot stand in for cumulative row32.
        data['Поток_нал'].update({column+'30':1 for column in 'CDEFGHIJKLMN'})
        del data['Поток_нал']['C32']
        with self.assertRaisesRegex(ValueError,'C32'):patch_business_docx(raw,data)
        data['Поток_нал']['C32']='#REF!'
        with self.assertRaises(ValueError):patch_business_docx(raw,data)
        data['Поток_нал']['C32']=-1
        _,meta=patch_business_docx(raw,data)
        self.assertTrue(any(e['kind']=='narrative' and e['paragraph']==538 for e in meta['edits']))
        data['Поток_нал']['D30']=1
        del data['Поток_нал']['C30']
        with self.assertRaisesRegex(ValueError,'C30'):patch_business_docx(raw,data)
        data['Поток_нал']['C30']='#REF!'
        with self.assertRaises(ValueError):patch_business_docx(raw,data)
        # Positive cash after repayment cannot replace a negative total cash
        # flow after first-year financing needs; cumulative cash may stay >0.
        data['Поток_нал'].update(C30=1,C32=1,D30=-1,D27=1)
        _,meta=patch_business_docx(raw,data)
        self.assertTrue(any(e['kind']=='narrative' and e['paragraph']==538 for e in meta['edits']))

    def test_pdf_copy_keeps_headings_blank_bridge_and_header_not_body_rows(self):
        heading='<w:p><w:pPr><w:pStyle w:val="H1"/><w:keepNext w:val="0"/></w:pPr><w:r><w:t>8. Synthetic chapter</w:t></w:r></w:p>'
        blank='<w:p><w:pPr/></w:p>'
        normal=paragraph('1. Ordinary numbered body list')
        table='<w:tbl><w:tr><w:trPr><w:tblHeader/></w:trPr>'+cell('Header A')+cell('Header B')+'</w:tr><w:tr>'+cell('body A')+cell('body B')+'</w:tr></w:tbl>'
        final='<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
        xml='<w:document xmlns:w="'+WORD+'"><w:body>'+heading+blank+normal+table+final+'<w:sectPr/></w:body></w:document>'
        styles='<w:styles xmlns:w="'+WORD+'"><w:style w:styleId="H1"><w:name w:val="heading 1"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style></w:styles>'
        raw=package(xml,styles);updated=prepare_pdf_layout(raw)
        with ZipFile(BytesIO(raw)) as source,ZipFile(BytesIO(updated)) as target:
            self.assertEqual(source.read('word/document.xml'),xml.encode())
            self.assertEqual(source.read('word/styles.xml'),target.read('word/styles.xml'))
            self.assertEqual(source.read('word/media/opaque.bin'),target.read('word/media/opaque.bin'))
            result=ET.fromstring(target.read('word/document.xml'))
        q='{'+WORD+'}';body=result.find(q+'body');paras=body.findall(q+'p')
        self.assertIsNotNone(paras[0].find(q+'pPr/'+q+'keepNext'))
        self.assertIsNotNone(paras[1].find(q+'pPr/'+q+'keepNext'))
        self.assertIsNone(paras[2].find(q+'pPr/'+q+'keepNext'))
        self.assertIsNone(paras[-1].find('.//'+q+'br'))
        rows=body.find(q+'tbl').findall(q+'tr')
        self.assertEqual(sum(p.find(q+'pPr/'+q+'keepNext') is not None for p in rows[0].iter(q+'p')),2)
        self.assertFalse(any(p.find(q+'pPr/'+q+'keepNext') is not None for p in rows[1].iter(q+'p')))
        self.assertEqual(prepare_pdf_layout(updated),updated)

    def test_pdf_copy_never_removes_nonempty_middle_or_section_page_breaks(self):
        q='{'+WORD+'}'
        samples=[paragraph('Text')[:-6]+'<w:r><w:br w:type="page"/></w:r></w:p>',
                 '<w:p><w:pPr><w:sectPr><w:type w:val="nextPage"/></w:sectPr></w:pPr><w:r><w:br w:type="page"/></w:r></w:p>',
                 '<w:p><w:r><w:drawing/><w:br w:type="page"/></w:r></w:p>']
        for final in samples:
            xml='<w:document xmlns:w="'+WORD+'"><w:body><w:p><w:r><w:br w:type="page"/></w:r></w:p>'+final+'<w:sectPr/></w:body></w:document>'
            raw=package(xml);updated=prepare_pdf_layout(raw)
            self.assertEqual(updated,raw)
            with ZipFile(BytesIO(updated)) as target:self.assertEqual(len(ET.fromstring(target.read('word/document.xml')).findall('.//'+q+'br')),2)

    def test_pdf_copy_keeps_exact_market_subheadings_with_their_first_body(self):
        heading='Сегментация по типу потребителей (B2B / B2G / B2C)'
        subheading='Государственный сектор (B2G):'
        xml='<w:document xmlns:w="'+WORD+'"><w:body>'+paragraph(heading)+'<w:p/>'+paragraph(subheading)+paragraph('Synthetic first sector body.')+paragraph('Other body.')+'<w:sectPr/></w:body></w:document>'
        raw=package(xml);updated=prepare_pdf_layout(raw);q='{'+WORD+'}'
        with ZipFile(BytesIO(raw)) as source,ZipFile(BytesIO(updated)) as target:
            self.assertEqual(source.read('word/document.xml'),xml.encode())
            self.assertEqual(source.read('word/media/opaque.bin'),target.read('word/media/opaque.bin'))
            before=ET.fromstring(source.read('word/document.xml'));after=ET.fromstring(target.read('word/document.xml'))
            self.assertEqual(list(before.itertext()),list(after.itertext()))
            paras=after.find(q+'body').findall(q+'p')
        self.assertTrue(all(p.find(q+'pPr/'+q+'keepNext') is not None for p in paras[:3]))
        self.assertTrue(all(p.find(q+'pPr/'+q+'keepNext') is None for p in paras[3:]))
        self.assertEqual(prepare_pdf_layout(updated),updated)


if __name__=='__main__':unittest.main()
