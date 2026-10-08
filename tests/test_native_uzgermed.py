"""Native profile recognition and honest saved/fresh values; synthetic OOXML only."""
import io
import math
import re
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.native_uzgermed import METRICS, PARAMETERS, READ_ONLY_PARAMETERS, REQUIRED_SHEETS, describe_profile

S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P = 'http://schemas.openxmlformats.org/package/2006/relationships'


def fixture(changes=None, omitted=()):
    sheets = {name: {} for name in sorted(REQUIRED_SHEETS - set(omitted))}
    for sheet, address, _, _, low, high, step, _ in PARAMETERS:
        if sheet in sheets:
            value = low if low > 0 else min(high, 0.25)
            if step:
                value = max(1, low)
            sheets[sheet][address] = (value, None)
    for sheet, address, _, _ in METRICS:
        if sheet in sheets:
            sheets[sheet][address] = (3, '1+2')
    if 'РКЛ' in sheets:
        sheets['РКЛ']['AA7'] = (0, None)
        sheets['РКЛ']['AA8'] = (3, None)
        sheets['РКЛ']['AA10'] = (1, None)
    for sheet, address, text in (
        ('Стоим_проекта', 'B4', 'ООО "UZGERMED PHARM"'),
        ('Стоим_проекта', 'A2', 'СТОИМОСТЬ ПРОЕКТА'),
        ('Фин_план', 'A2', 'ФИНАНСОВЫЙ ПЛАН'),
        ('ВНД', 'A1', 'Расчет NPV и IRR'),
    ):
        if sheet in sheets:
            sheets[sheet][address] = (text, None)
    for (sheet, address), cell in (changes or {}).items():
        if sheet in sheets:
            sheets[sheet][address] = cell
    book = ET.Element(f'{{{S}}}workbook')
    book_sheets = ET.SubElement(book, f'{{{S}}}sheets')
    rels = ET.Element(f'{{{P}}}Relationships')
    stream = io.BytesIO()
    with ZipFile(stream, 'w') as archive:
        content_types = ET.Element('Types', {'xmlns': 'http://schemas.openxmlformats.org/package/2006/content-types'})
        ET.SubElement(content_types, 'Default', {'Extension': 'xml', 'ContentType': 'application/xml'})
        ET.SubElement(content_types, 'Default', {'Extension': 'rels', 'ContentType': 'application/vnd.openxmlformats-package.relationships+xml'})
        ET.SubElement(content_types, 'Override', {'PartName': '/xl/workbook.xml', 'ContentType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'})
        for index, (name, cells) in enumerate(sheets.items(), 1):
            ET.SubElement(content_types, 'Override', {'PartName': f'/xl/worksheets/sheet{index}.xml', 'ContentType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'})
            ET.SubElement(book_sheets, f'{{{S}}}sheet', {'name': name, 'sheetId': str(index), f'{{{R}}}id': f'rId{index}'})
            ET.SubElement(rels, f'{{{P}}}Relationship', {'Id': f'rId{index}', 'Type': f'{R}/worksheet', 'Target': f'worksheets/sheet{index}.xml'})
            tree = ET.Element(f'{{{S}}}worksheet')
            data = ET.SubElement(tree, f'{{{S}}}sheetData')
            rows = {}
            for address, (value, formula) in cells.items():
                row_number = re.search(r'\d+', address).group()
                if row_number not in rows:
                    rows[row_number] = ET.SubElement(data, f'{{{S}}}row', {'r': row_number, 'ht': '24', 'customHeight': '1'})
                row = rows[row_number]
                attributes = {'r': address}
                if isinstance(value, str):
                    attributes['t'] = 'e' if value.startswith('#') else 'inlineStr'
                node = ET.SubElement(row, f'{{{S}}}c', attributes)
                if formula:
                    ET.SubElement(node, f'{{{S}}}f').text = formula
                if value is not None:
                    if attributes.get('t') == 'inlineStr':
                        inline = ET.SubElement(node, f'{{{S}}}is')
                        ET.SubElement(inline, f'{{{S}}}t').text = value
                    else:
                        ET.SubElement(node, f'{{{S}}}v').text = str(value)
            ET.SubElement(tree, f'{{{S}}}pageSetup', {'paperSize': '9', 'orientation': 'landscape', 'fitToWidth': '1'})
            archive.writestr(f'xl/worksheets/sheet{index}.xml', ET.tostring(tree))
        ET.SubElement(content_types, 'Override', {'PartName': '/xl/styles.xml', 'ContentType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml'})
        archive.writestr('[Content_Types].xml', ET.tostring(content_types))
        archive.writestr('_rels/.rels', f'<Relationships xmlns="{P}"><Relationship Id="rId1" Type="{R}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        ET.SubElement(rels, f'{{{P}}}Relationship', {'Id': 'rStyles', 'Type': f'{R}/styles', 'Target': 'styles.xml'})
        archive.writestr('xl/styles.xml', f'<styleSheet xmlns="{S}"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        archive.writestr('xl/printerSettings/printerSettings1.bin', b'Synthetic unchanged native print settings')
        archive.writestr('xl/workbook.xml', ET.tostring(book))
        archive.writestr('xl/_rels/workbook.xml.rels', ET.tostring(rels))
    return stream.getvalue()


class NativeUzgermedTests(unittest.TestCase):
    def test_profile_uses_original_cells_and_does_not_promote_formulas_to_editable_inputs(self):
        profile = describe_profile(fixture())
        self.assertTrue(profile['supported'])
        self.assertEqual(profile['sheet_count'], len(REQUIRED_SHEETS))
        self.assertEqual(len(profile['parameters']), 13)
        self.assertEqual(profile['issues'], [])
        self.assertTrue(all(math.isfinite(p['value']) for p in profile['parameters']))
        self.assertTrue(all(p['editable'] == ((p['sheet'], p['cell']) not in READ_ONLY_PARAMETERS)
                            for p in profile['parameters']))
        controls = {p['key']: p for p in profile['parameters']}
        self.assertIn('Стоим_проекта!D28', controls)
        self.assertNotIn('Стоим_проекта!D34', controls)
        self.assertIn('РКЛ!AA6', controls)
        self.assertNotIn('Кредиты!D7', controls)
        self.assertEqual(controls['РКЛ!AA6']['unit'], 'доля')
        self.assertIn('дисконтирования', controls['РКЛ!AA6']['help'])
        metrics = {m['key']: m for m in profile['metrics']}
        self.assertEqual(metrics['ВНД!E6']['unit'], '%')
        self.assertEqual(metrics['ВНД!E6']['original'], 3)
        self.assertEqual(metrics['ВНД!E6']['original_source'], 'excel_saved_cache')
        self.assertNotIn('calculated', metrics['ВНД!E6'])

    def test_unreferenced_credit_conditions_are_readonly_numeric_source_facts(self):
        profile = describe_profile(fixture())
        controls = {p['key']: p for p in profile['parameters']}
        readonly = {key for key, item in controls.items() if not item['editable']}
        self.assertEqual(readonly, {'РКЛ!AA7', 'РКЛ!AA8', 'РКЛ!AA10'})
        for key in readonly:
            with self.subTest(key=key):
                item = controls[key]
                self.assertIsNotNone(item['value'])
                self.assertIn('справочно', item['label'])
                self.assertIn('Формулы не используют эту ячейку', item['help'])
                self.assertIn('исправленную книгу', item['help'])
        self.assertTrue(controls['РКЛ!AA6']['editable'])
        self.assertTrue(controls['Стоим_проекта!D28']['editable'])
        self.assertEqual(profile['issues'], [])

    def test_raw_xml_precision_is_preserved_for_controls_and_original_saved_metrics(self):
        tiny = Decimal('0.0000000000001')
        precise = Decimal('0.123456789012345')
        cached = Decimal('7.123456789012345')
        profile = describe_profile(fixture({
            ('Производ. с учетом загрузки', 'B19'): (tiny, None),
            ('Производ. с учетом загрузки', 'Q19'): (precise, None),
            ('Приб_Убыт', 'N4'): (cached, '1+2'),
        }), {'Приб_Убыт': {'N4': 3}})
        controls = {item['key']: item for item in profile['parameters']}
        self.assertEqual(controls['Производ. с учетом загрузки!B19']['value'], float(tiny))
        self.assertNotEqual(controls['Производ. с учетом загрузки!B19']['value'], 0)
        self.assertEqual(controls['Производ. с учетом загрузки!Q19']['value'], float(precise))
        self.assertEqual(Decimal(controls['Производ. с учетом загрузки!Q19']['original_text']), precise)
        metric = next(item for item in profile['metrics'] if item['key'] == 'Приб_Убыт!N4')
        self.assertEqual(metric['original'], float(cached))
        self.assertEqual(Decimal(metric['original_text']), cached)
        self.assertEqual(metric['calculated'], 3)
        self.assertEqual(metric['original_source'], 'excel_saved_cache')

    def test_identity_requires_native_sheet_structure_company_and_financial_labels(self):
        self.assertFalse(describe_profile(fixture(omitted=['РКЛ']))['supported'])
        for position, value in ((('Стоим_проекта', 'B4'), 'Another company'),
                                (('Стоим_проекта', 'A2'), 'Other template'),
                                (('ВНД', 'A1'), 'Only NPV')):
            with self.subTest(position=position):
                profile = describe_profile(fixture({position: (value, None)}))
                self.assertFalse(profile['supported'])
                self.assertEqual(profile['parameters'], [])
                self.assertEqual(profile['metrics'], [])

    def test_formula_or_error_in_original_control_is_unavailable_and_keeps_diagnostic(self):
        for cell in ((3, '1+2'), ('#REF!', None), (None, None)):
            with self.subTest(cell=cell):
                profile = describe_profile(fixture({('Стоим_проекта', 'B38'): cell}))
                control = next(p for p in profile['parameters'] if p['key'] == 'Стоим_проекта!B38')
                self.assertIsNone(control['value'])
                self.assertFalse(control['editable'])
                self.assertTrue(any(i['sheet'] == 'Стоим_проекта' and i['cell'] == 'B38' for i in profile['issues']))

    def test_error_saved_output_is_not_zero_and_unmapped_original_errors_are_counted(self):
        profile = describe_profile(fixture({('ВНД', 'E6'): ('#NUM!', 'IRR(B3:B6)'),
                                           ('РКЛ', 'AB14'): ('#REF!', '#REF!')}))
        metric = next(m for m in profile['metrics'] if m['key'] == 'ВНД!E6')
        self.assertIsNone(metric['original'])
        self.assertEqual(metric['error'], '#NUM!')
        self.assertEqual(profile['source_error_count'], 2)
        self.assertEqual(profile['source_error_counts'], {'#NUM!': 1, '#REF!': 1})
        self.assertTrue(any(i['code'] == 'original_metric_unavailable' for i in profile['issues']))

    def test_fresh_results_are_distinct_from_saved_cache_and_missing_errors_do_not_fall_back(self):
        fresh = {'ВНД': {'D6': 9, 'E6': '#NUM!'}, 'Стоим_проекта': {'D34': 0}}
        metrics = {m['key']: m for m in describe_profile(fixture(), fresh)['metrics']}
        self.assertEqual(metrics['ВНД!D6']['original'], 3)
        self.assertEqual(metrics['ВНД!D6']['calculated'], 9)
        self.assertIsNone(metrics['ВНД!E6']['calculated'])
        self.assertEqual(metrics['ВНД!E6']['calculated_error'], '#NUM!')
        self.assertEqual(metrics['Стоим_проекта!D34']['calculated'], 0)
        self.assertIsNone(metrics['Приб_Убыт!N4']['calculated'])
        self.assertEqual(metrics['ВНД!D6']['calculated_source'], 'recalculated_formula')

    def test_nonfinite_boolean_and_missing_fresh_results_cannot_become_financial_numbers(self):
        for value in (float('inf'), float('-inf'), float('nan'), True, None, '', '1e-400'):
            with self.subTest(value=value):
                metrics = describe_profile(fixture(), {'ВНД': {'D6': value}})['metrics']
                self.assertIsNone(next(m for m in metrics if m['key'] == 'ВНД!D6')['calculated'])

    def test_malformed_workbook_errors_remain_visible(self):
        profile = describe_profile(b'not a workbook')
        self.assertFalse(profile['supported'])
        self.assertEqual(profile['issues'][0]['code'], 'unreadable_workbook')


if __name__ == '__main__':
    unittest.main()
