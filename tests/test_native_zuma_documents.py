"""Synthetic ZUMA layout: distinct prose addresses and company-pair validation."""
from io import BytesIO
from zipfile import ZipFile
import unittest

from test_native_documents import template, values, paragraph, change_xml
from app.native_documents import (patch_business_docx, report_sources, _containers,
    NO_LOSS_TEXT, POSITIVE_CASH_TEXT, PROFIT_CELLS, CASH_FLOW_CELLS,
    CUMULATIVE_CASH_CELLS, OBSOLETE_SECTOR_TEXT, PHARMACEUTICAL_MARKET_TEXT,
    ZUMA_LOCATION_TEXT, ZUMA_LOCATION_EVIDENCE, ZUMA_LOCATION_REPLACEMENT)


def zuma_template():
    def transform(xml):
        spans=_containers(xml)
        replacements={7:paragraph('БИЗНЕС-ПЛАН ООО «ZUMA-PHARMA»'),
                      52:paragraph('ООО «ZUMA-PHARMA»'),
                      144:paragraph('Оборотный капитал: кредит 1 000 долларов США; код 123.'),
                      146:paragraph(ZUMA_LOCATION_EVIDENCE)}
        for index,text in sorted(replacements.items(),reverse=True):
            start,end=spans['paragraph',index];xml=xml[:start]+text.encode()+xml[end:]
        appended=['<w:p/>' for _ in range(147,496)]
        for index,text in {
            333:ZUMA_LOCATION_TEXT,
            385:'Штат работников предприятия составляет 3 человек. Административно-управленческий аппарат – 1 человек, производственные персонал –2 человек.',
            400:'НДС – 0%.',401:NO_LOSS_TEXT,420:POSITIVE_CASH_TEXT+' ',
            478:OBSOLETE_SECTOR_TEXT}.items():
            appended[index-147]=paragraph(text)
        at=_containers(xml)['table',0][0]
        return xml[:at]+''.join(appended).encode()+xml[at:]
    return change_xml(template(),transform)


def fresh_values():
    data=values()
    data['Труд']={'B35':20,'B56':10,'B48':99}
    data['НДС']={'F4':.12}
    data['Приб_Убыт']={cell:1 for cell in PROFIT_CELLS}
    data['Поток_нал']={cell:1 for cell in CASH_FLOW_CELLS+CUMULATIVE_CASH_CELLS}
    return data


class ZumaDocumentsTests(unittest.TestCase):
    def test_zuma_prose_uses_its_own_staff_credit_vat_and_monthly_bindings(self):
        raw=zuma_template();data=fresh_values()
        data['Стоим_проекта']['D34']=2000
        data['Приб_Убыт']['M64']=-1
        data['Поток_нал']['M83']=-1
        updated,meta=patch_business_docx(raw,data,profile_id='zuma-36m-usd')
        self.assertEqual(meta['profile_id'],'zuma-36m-usd')
        targets=report_sources(raw,profile_id='zuma-36m-usd')
        self.assertIn({'sheet':'Труд','cell':'B56'},targets)
        self.assertNotIn({'sheet':'Труд','cell':'B48'},targets)
        with ZipFile(BytesIO(raw)) as source,ZipFile(BytesIO(updated)) as output:
            for name in source.namelist():
                if name!='word/document.xml':self.assertEqual(source.read(name),output.read(name))
            xml=output.read('word/document.xml').decode()
        self.assertIn('кредит 2 000 долларов США; код 123.',xml)
        self.assertIn('составляет 30 человек',xml)
        self.assertIn('аппарат – 10 человек',xml)
        self.assertIn('НДС – 12%.',xml)
        self.assertNotIn(NO_LOSS_TEXT,xml)
        self.assertNotIn(POSITIVE_CASH_TEXT,xml)
        self.assertIn(PHARMACEUTICAL_MARKET_TEXT,xml)
        self.assertIn(ZUMA_LOCATION_REPLACEMENT,xml)
        self.assertNotIn(ZUMA_LOCATION_TEXT,xml)

    def test_unresolved_zuma_staff_or_monthly_result_cannot_keep_old_prose(self):
        for sheet,cell in (('Труд','B56'),('Приб_Убыт','M64'),('Поток_нал','M83')):
            data=fresh_values();data[sheet][cell]='#REF!'
            with self.assertRaises(ValueError):patch_business_docx(zuma_template(),data,'zuma-36m-usd')

    def test_company_mismatch_blocks_both_directions_and_unknown_profile(self):
        for raw,profile in ((template(),'zuma-36m-usd'),(zuma_template(),'uzgermed-36m-usd'),
                            (zuma_template(),'unknown')):
            with self.assertRaises(ValueError):patch_business_docx(raw,fresh_values(),profile)
            with self.assertRaises(ValueError):report_sources(raw,profile)

    def test_location_correction_requires_unchanged_slot_and_source_evidence(self):
        for before,after in ((ZUMA_LOCATION_EVIDENCE,'Место производства не указано.'),
                             (ZUMA_LOCATION_TEXT,'Авторское описание своей площадки.')):
            raw=change_xml(zuma_template(),lambda xml:xml.replace(before.encode(),after.encode()))
            _,meta=patch_business_docx(raw,fresh_values(),'zuma-36m-usd')
            self.assertFalse(any(e.get('reason')=='unrelated_company_location_corrected_from_source' for e in meta['edits']))


if __name__=='__main__':unittest.main()
