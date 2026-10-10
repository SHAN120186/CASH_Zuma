"""Native TOC pagination, using synthetic DOCX/PDF without company data."""
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from pypdf import PdfWriter
from pypdf.annotations import Link
from pypdf.generic import Fit
import pytest

from app import native_renderer as renderer
from app.native_documents import toc_page_fields, update_toc_pages


WORD='http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def source_docx():
    entries=''.join(f'''<w:p><w:pPr><w:pStyle w:val="TOC1"/></w:pPr>
      <w:hyperlink w:anchor="_Toc{index}"><w:r><w:rPr><w:b/></w:rPr><w:t>Section {index}</w:t></w:r>
      <w:r><w:tab/></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r>
      <w:r><w:instrText xml:space="preserve"> PAGEREF _Toc{index} \\h </w:instrText></w:r>
      <w:r><w:fldChar w:fldCharType="separate"/></w:r>
      <w:r><w:rPr><w:i/></w:rPr><w:t>9</w:t></w:r><w:r><w:t/></w:r><w:r><w:t>9</w:t></w:r>
      <w:r><w:fldChar w:fldCharType="end"/></w:r></w:hyperlink></w:p>''' for index in (1,2))
    xml=f'''<w:document xmlns:w="{WORD}"><w:body><w:sdt><w:sdtContent>
      <w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>
      <w:r><w:instrText> TO</w:instrText></w:r><w:r><w:instrText>C \\o "1-3" \\h </w:instrText></w:r>
      <w:r><w:fldChar w:fldCharType="separate"/></w:r></w:p>{entries}
      <w:p><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p></w:sdtContent></w:sdt>
      <w:p><w:bookmarkStart w:id="1" w:name="_Toc1"/><w:r><w:t>Section 1</w:t></w:r></w:p>
      <w:p><w:bookmarkStart w:id="2" w:name="_Toc2"/><w:r><w:t>Section 2</w:t></w:r></w:p>
      <w:p><w:r><w:t>Amount 10000.50</w:t></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r>
      <w:r><w:instrText>PAGEREF _Toc1 \\h</w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>
      <w:r><w:t>99</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>
      </w:body></w:document>'''.encode()
    output=BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as package:
        package.writestr('word/document.xml',xml)
        package.writestr('word/styles.xml',f'<w:styles xmlns:w="{WORD}"/>')
        package.writestr('word/media/image.png',b'synthetic retained image')
        package.comment=b'retained archive comment'
    return output.getvalue()


def document(raw):
    with ZipFile(BytesIO(raw)) as package:return package.read('word/document.xml')


def changed_document(raw,xml):
    output=BytesIO()
    with ZipFile(BytesIO(raw)) as source,ZipFile(output,'w') as target:
        for item in source.infolist():target.writestr(item,xml if item.filename=='word/document.xml' else source.read(item))
    return output.getvalue()


def linked_pdf(pages,wrapped=True):
    writer=PdfWriter()
    for _ in range(max(pages)):writer.add_blank_page(600,800)
    # Reverse physical annotation storage to check visual reading order.
    for index,page in reversed(list(enumerate(pages))):
        y=700-index*40
        fit=Fit.xyz(left=72,top=600-index*40,zoom=0)
        writer.add_annotation(0,Link(rect=(72,y,500,y+12),target_page_index=page-1,fit=fit))
        if wrapped and index==0:
            writer.add_annotation(0,Link(rect=(72,y-14,500,y-2),target_page_index=page-1,fit=fit))
    writer.add_annotation(0,Link(rect=(72,20,200,32),url='https://example.invalid'))
    output=BytesIO();writer.write(output);return output.getvalue()


def fake_converter(monkeypatch,outputs):
    copies=[];calls=[]
    monkeypatch.setenv('NATIVE_SOFFICE','synthetic-office')
    class Process:
        pid=12345
        def __init__(self,command,**kwargs):
            directory=Path(command[command.index('--outdir')+1])
            copies.append((directory/'business.docx').read_bytes())
            assert not (directory/'business.pdf').exists(), 'A prior render must never be reused'
            content=outputs[len(copies)-1]
            if content is not None:(directory/'business.pdf').write_bytes(content)
        def wait(self,timeout=None):calls.append(timeout);return 0
    monkeypatch.setattr(renderer.subprocess,'Popen',Process)
    return copies,calls


def test_toc_patch_changes_only_cached_digits_preserves_fields_styles_and_other_parts():
    original=source_docx();fields=toc_page_fields(original)
    assert [(f.bookmark,f.page) for f in fields]==[('_Toc1',99),('_Toc2',99)]
    patched=update_toc_pages(original,[2,3])
    expected=document(original)
    for field,page in reversed(list(zip(fields,[2,3]))):
        for index,node in reversed(list(enumerate(field.nodes))):
            expected=expected[:node.start]+(str(page).encode() if index==0 else b'')+expected[node.end:]
    assert document(patched)==expected
    assert b'<w:t>Amount 10000.50</w:t>' in expected and b'<w:t>99</w:t>' in expected
    assert [f.page for f in toc_page_fields(patched)]==[2,3]
    assert update_toc_pages(patched,[2,3])==patched
    with ZipFile(BytesIO(original)) as before,ZipFile(BytesIO(patched)) as after:
        assert before.comment==after.comment
        for item in before.infolist():
            updated=after.getinfo(item.filename)
            for attribute in ('date_time','compress_type','comment','extra','internal_attr','external_attr'):
                assert getattr(item,attribute)==getattr(updated,attribute)
            if item.filename!='word/document.xml':assert before.read(item)==after.read(item.filename)


@pytest.mark.parametrize('change',[
    lambda xml:xml.replace(b'w:name="_Toc2"',b'w:name="missing"'),
    lambda xml:xml.replace(b'PAGEREF _Toc2',b'PAGEREF _Toc1'),
    lambda xml:xml.replace(b'<w:t>9</w:t>',b'<w:t>?</w:t>',1),
    lambda xml:xml.replace(b'<w:r><w:tab/></w:r>',b'<w:r><w:fldSimple w:instr="PAGEREF _Toc1"/></w:r>',1),
])
def test_ambiguous_or_unsupported_toc_is_rejected(change):
    raw=source_docx()
    with pytest.raises(ValueError):toc_page_fields(changed_document(raw,change(document(raw))))


def test_pdf_targets_sorted_deduplicated_and_external_links_excluded():
    assert renderer._toc_destination_pages(linked_pdf([2,4]),2)==[2,4]
    with pytest.raises(ValueError,match='сопоставить'):
        renderer._toc_destination_pages(linked_pdf([2,4]),3)


def test_renderer_rerenders_corrected_toc_without_changing_source(monkeypatch):
    pdf=linked_pdf([2,3]);copies,calls=fake_converter(monkeypatch,[pdf,pdf])
    source=source_docx();before=bytes(source)
    assert renderer.docx_to_pdf(source)==pdf
    assert source==before and len(copies)==2
    assert [f.page for f in toc_page_fields(copies[0])]==[99,99]
    assert [f.page for f in toc_page_fields(copies[1])]==[2,3]
    assert calls[0]==60 and 0<calls[1]<=60


def test_renderer_allows_one_pagination_shift_then_requires_stability(monkeypatch):
    copies,_=fake_converter(monkeypatch,[linked_pdf([2,3]),linked_pdf([2,4]),linked_pdf([2,4])])
    result=renderer.docx_to_pdf(source_docx())
    assert renderer._toc_destination_pages(result,2)==[2,4]
    assert len(copies)==3 and [f.page for f in toc_page_fields(copies[-1])]==[2,4]


def test_unstable_pagination_never_returns_stale_pdf_and_releases_lock(monkeypatch):
    copies,_=fake_converter(monkeypatch,[linked_pdf([2,3]),linked_pdf([2,4]),linked_pdf([3,4])])
    with pytest.raises(ValueError,match='стабилизировались'):renderer.docx_to_pdf(source_docx())
    assert len(copies)==3
    assert renderer._conversion.acquire(blocking=False);renderer._conversion.release()


def test_missing_second_pdf_never_reuses_first_pass(monkeypatch):
    copies,_=fake_converter(monkeypatch,[linked_pdf([2,3]),None])
    with pytest.raises(ValueError,match='преобразовать'):renderer.docx_to_pdf(source_docx())
    assert len(copies)==2


def test_toc_update_rejects_count_and_nonphysical_page_values():
    for pages in ([2],[2,True],[0,3],[2,10001]):
        with pytest.raises(ValueError):update_toc_pages(source_docx(),pages)
