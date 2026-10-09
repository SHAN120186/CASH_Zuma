"""Patch verified financial text slots in retained company Word templates.

No DOCX reconstruction or Office execution happens here. Only w:t character
data in verified slots is changed; runs, tables, VML fallback copies, styles,
relationships, media and every other ZIP part are retained byte for byte.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import unescape
from io import BytesIO
import re
from xml.parsers import expat
from xml.sax.saxutils import escape
from zipfile import ZipFile

from defusedxml import ElementTree as ET

WORD = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
MAX_DOCX = 20*1024*1024
MAX_XML = 30*1024*1024
MONEY = re.compile(r'^[+-]?\d[\d \u00a0\u202f]*(?:[.,]\d+)?\s*%?$')
# Mapping is structural, never a global find/replace of matching business values.
COST_ROWS = ((2,10),(3,14),(4,18),(5,21),(6,27),(7,28),(8,29),(9,30),(10,31),(11,33),(12,34))
COST_COLUMNS = ((1,'B'),(2,'D'),(3,'E'),(4,'F'))
BREAK_EVEN = ((0,'B6',1),(1,'B7',1),(2,'B8',1),(3,'B10',100))
UTILITY_CELLS = ((2,2,'C5'),(2,3,'D5'),(2,4,'E5'),(3,2,'C8'),(3,3,'D8'),(3,4,'E8'),(4,4,'E9'))
STAFF_PARAGRAPH = 506
STAFF_SLOTS = (
    (re.compile(r'Штат работников предприятия составляет\s+(?P<amount>\d+)\s+человек'),('B35','B48')),
    (re.compile(r'Административно-управленческий аппарат\s*[–−-]\s*(?P<amount>\d+)\s+человек'),('B48',)),
    (re.compile(r'производственн\w* персонал\s*[–−-]\s*(?P<amount>\d+)\s+человек',re.I),('B35',)),
)
CREDIT_PARAGRAPHS = {12:2,146:1}
CREDIT_AMOUNT = re.compile(r'(?<![\d.,])(?P<amount>\d(?:[\d \u00a0\u202f]*\d)?)(?=\s*(?:долл(?:ар(?:ов|а))?\.?\s*США|доллар(?:ов|а)?\s*США|USD|US\$))',re.I)
VAT_PARAGRAPH = 521
VAT_TEXT = re.compile(r'^\s*НДС\s*[–−-]\s*(?P<amount>\d+(?:[.,]\d+)?\s*%)\.\s*$')
NO_LOSS_PARAGRAPH = 522
NO_LOSS_TEXT = 'Убытки в течение всего горизонта планирования производственной деятельности предприятия не наблюдаются.'
POSITIVE_CASH_PARAGRAPH = 538
POSITIVE_CASH_TEXT = 'Поток наличности в целом по проекту на протяжении всего горизонта планирования будет положительным. Кумулятивный поток наличности на протяжении всего периода будет положительным.'
OBSOLETE_SECTOR_PARAGRAPH = 597
OBSOLETE_SECTOR_TEXT = 'Анализ существующего положения на рынке текстиля позволяет сделать вывод, что данный рынок бурно развивается. '
PHARMACEUTICAL_MARKET_TEXT = 'Оценка фармацевтического рынка требует актуальных данных о спросе, ценах и конкурентах.'
PROFIT_CELLS = tuple(column+str(row) for row in (18,41,64) for column in 'BCDEFGHIJKLM')
CASH_FLOW_CELLS = tuple(column+str(row) for row,columns in ((30,'CDEFGHIJKLMN'),(56,'BCDEFGHIJKLM'),(81,'BCDEFGHIJKLM')) for column in columns)
CUMULATIVE_CASH_CELLS = tuple(column+str(row) for row,columns in ((32,'CDEFGHIJKLMN'),(58,'BCDEFGHIJKLM'),(83,'BCDEFGHIJKLM')) for column in columns)

ZUMA_PROFILE = 'zuma-36m-usd'
ZUMA_LOCATION_PARAGRAPH = 333
ZUMA_LOCATION_TEXT = ('Участок осуществления проекта, расположен по адресу: Республика Узбекистан, '
                      'Ташкентская область, Ю. Чирчик район, Бордонкул К.Ф.Й., Дустлик мах. '
                      'Общая площадь территории составляет 10\u00a0880,00 кв.м.')
ZUMA_LOCATION_EVIDENCE = ('Предприятие расположено в Паркентском районе Ташкентской области и осуществляет '
                          'производственную деятельность на территории СЭЗ «Parkent-Farm».')
ZUMA_LOCATION_REPLACEMENT = ('Производственная площадка предприятия расположена в Паркентском районе '
                             'Ташкентской области, на территории СЭЗ «Parkent-Farm».')


def _bindings(xml, spans, profile_id=None):
    """Select an inspected layout by its company markers, not by file name.

    Expected profiles bind the Word to the selected Excel. Unlabelled synthetic
    legacy fixtures retain the old layout only when no expected profile is given.
    """
    def text(index):
        key=('paragraph',index)
        return ''.join(n.value for n in _text_nodes(xml,spans[key])) if key in spans else ''
    identity=' '.join((text(7)+' '+text(52)).upper().split())
    zuma='ZUMA-PHARMA' in identity
    if profile_id == ZUMA_PROFILE and not zuma or zuma and profile_id not in (None,ZUMA_PROFILE):
        raise ValueError('Excel и Word относятся к разным шаблонам компании. Добавьте оригинальную пару одной фирмы.')
    if profile_id not in (None,ZUMA_PROFILE,'uzgermed-36m-usd'):
        raise ValueError('Профиль Word не поддерживается.')
    if profile_id == 'uzgermed-36m-usd' and 'UZGERMED PHARM' not in identity:
        raise ValueError('Word не содержит название компании выбранного Excel. Добавьте оригинальный Word этой фирмы.')
    return {'profile_id':ZUMA_PROFILE if zuma else 'uzgermed-36m-usd',
            'staff':385 if zuma else STAFF_PARAGRAPH,
            'staff_cells':('B35','B56') if zuma else ('B35','B48'),
            'credit':{12:2,144:1} if zuma else CREDIT_PARAGRAPHS,
            'vat':400 if zuma else VAT_PARAGRAPH,
            'no_loss':401 if zuma else NO_LOSS_PARAGRAPH,
            'positive_cash':420 if zuma else POSITIVE_CASH_PARAGRAPH,
            'sector':478 if zuma else OBSOLETE_SECTOR_PARAGRAPH}


def _tag_end(raw,start):
    quote=None
    for index in range(start,len(raw)):
        char=raw[index]
        if quote:
            if char==quote:quote=None
        elif char in (34,39):quote=char
        elif char==62:return index+1
    raise ValueError('Malformed XML tag')


def _containers(raw):
    """Locate exact byte spans for direct body paragraphs and table cells."""
    parser=expat.ParserCreate(namespace_separator='|')
    stack=[];spans={};elements=0
    def start(name,attributes):
        nonlocal elements
        elements+=1
        if elements>200000:raise ValueError('Document XML complexity limit')
        parent=stack[-1] if stack else None
        position=parser.CurrentByteIndex;opening_end=_tag_end(raw,position)
        frame={'name':name,'start':position,'opening_end':opening_end,'empty':raw[position:opening_end].rstrip().endswith(b'/>'),
               'key':None,'rows':0,'cells':0,'paragraphs':0,'tables':0}
        if parent:
            if parent['name']==WORD+'|body' and name==WORD+'|p':
                frame['key']=('paragraph',parent['paragraphs']);parent['paragraphs']+=1
            elif parent['name']==WORD+'|body' and name==WORD+'|tbl':
                frame['key']=('table',parent['tables']);parent['tables']+=1
            elif parent['key'] and parent['key'][0]=='table' and name==WORD+'|tr':
                frame['key']=('row',parent['key'][1],parent['rows']);parent['rows']+=1
            elif parent['key'] and parent['key'][0]=='row' and name==WORD+'|tc':
                frame['key']=('cell',parent['key'][1],parent['key'][2],parent['cells']);parent['cells']+=1
        stack.append(frame)
    def end(name):
        frame=stack.pop()
        if frame['key']:
            # Containers are p/tbl/tr/tc, never empty t elements.
            spans[frame['key']]=(frame['start'],frame['opening_end'] if frame['empty'] else _tag_end(raw,parser.CurrentByteIndex))
    def reject(*args):raise ValueError('DTD/entities are not allowed in a report template')
    parser.StartElementHandler=start;parser.EndElementHandler=end
    parser.StartDoctypeDeclHandler=reject;parser.EntityDeclHandler=reject;parser.ExternalEntityRefHandler=reject
    parser.Parse(raw,True)
    return spans


@dataclass(frozen=True)
class Text:
    start:int
    end:int
    value:str


def _text_nodes(raw,span):
    # Namespace aliases are taken from the document; drawing a:t is not Word w:t.
    aliases={match[1].decode() if match[1] else '' for match in re.finditer(rb'xmlns(?::([A-Za-z_][\w.-]*))?=["\']'+WORD.encode()+rb'["\']',raw)}
    if not aliases:raise ValueError('Word namespace is absent')
    prefixes='|'.join(re.escape(prefix+':' if prefix else '') for prefix in sorted(aliases))
    pattern=re.compile(rb'<(?P<prefix>'+prefixes.encode()+rb')t\b[^>]*?(?<!/)>(?P<text>.*?)</(?P=prefix)t\s*>',re.S)
    start,end=span
    return [Text(start+match.start('text'),start+match.end('text'),unescape(match['text'].decode('utf8')))
            for match in pattern.finditer(raw[start:end])]


def _numeric(value):
    if isinstance(value,bool) or value is None:raise ValueError('A required report value is missing or not numeric')
    try:number=Decimal(str(value))
    except (InvalidOperation,ValueError) as error:raise ValueError('A report value is not numeric') from error
    if not number.is_finite() or abs(number)>Decimal('1e15'):raise ValueError('A required report value is unresolved or out of range')
    return number


def _value(values,sheet,cell):
    if not isinstance(values,dict) or not isinstance(values.get(sheet),dict) or cell not in values[sheet]:
        raise ValueError('A required template source cell is absent: '+sheet+'!'+cell)
    return _numeric(values[sheet][cell])


def _format(number,original,scale=1):
    fraction=re.search(r'([.,])(\d+)\s*%?$',original.strip())
    places=len(fraction[2]) if fraction else 0
    if places>12:raise ValueError('Template number format exceeds supported precision')
    rounded=(number*Decimal(scale)).quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_UP)
    parts=format(rounded,'.'+str(places)+'f').split('.')
    result=parts[0]
    separator=next((char for char in ('\u00a0','\u202f',' ') if re.search(r'\d'+re.escape(char)+r'\d',original)),None)
    if separator:result=format(int(parts[0]),',').replace(',',separator)
    if places:result+=fraction[1]+parts[1]
    return result+('%' if original.rstrip().endswith('%') else '')


def _replace(nodes,start,end,new,patches):
    """Replace a decoded character span across runs, without removing run XML."""
    position=0;inserted=False
    for node in nodes:
        left,right=position,position+len(node.value);position=right
        if right<=start or left>=end:continue
        a=max(0,start-left);b=min(len(node.value),end-left)
        replacement=new if not inserted else ''
        inserted=True
        patches.append((node.start,node.end,node.value,a,b,replacement))
    if not inserted:raise ValueError('A verified numeric slot contains no editable Word text')


def _read(raw):
    if not isinstance(raw,bytes) or not raw or len(raw)>MAX_DOCX:raise ValueError('DOCX must be nonempty and no larger than 20 MiB')
    with ZipFile(BytesIO(raw)) as package:
        members=package.infolist()
        if len(members)>2500 or sum(i.file_size for i in members)>100*1024*1024 or any(i.flag_bits&1 or i.file_size>MAX_XML for i in members):
            raise ValueError('DOCX archive exceeds safe bounds')
        names=[i.filename for i in members]
        if len(set(names))!=len(names) or any('..' in n.replace('\\','/').split('/') or n.startswith(('/','\\')) for n in names):
            raise ValueError('Unsafe DOCX archive paths')
        if 'word/document.xml' not in names or any(n.lower().endswith('vbaproject.bin') for n in names):raise ValueError('A macro-free Word document is required')
        xml=package.read('word/document.xml')
    ET.fromstring(xml,forbid_dtd=True,forbid_entities=True,forbid_external=True)
    return xml


def _package_document(raw,xml):
    """Keep the complete source archive when its document part is unchanged."""
    with ZipFile(BytesIO(raw)) as source:
        if source.read('word/document.xml')==xml:return raw
        output=BytesIO()
        with ZipFile(output,'w') as target:
            for item in source.infolist():target.writestr(item,xml if item.filename=='word/document.xml' else source.read(item))
            target.comment=source.comment
    return output.getvalue()


@dataclass(frozen=True)
class TocPage:
    bookmark:str
    page:int
    nodes:tuple[Text,...]


def toc_page_fields(raw):
    """Locate cached PAGEREF digits inside an existing complex TOC field.

    Field instructions, hyperlinks and run properties are retained. Body page
    references are deliberately excluded: the renderer only maps TOC links.
    """
    xml=_read(raw);parser=expat.ParserCreate(namespace_separator='|')
    fields=[];result=[];bookmarks=set();capture=None;elements=0;seen_toc=False
    def start(name,attributes):
        nonlocal capture,elements,seen_toc
        elements+=1
        if elements>200000:raise ValueError('Document XML complexity limit')
        if name==WORD+'|bookmarkStart':bookmarks.add(attributes.get(WORD+'|name',''))
        if name==WORD+'|fldChar':
            kind=attributes.get(WORD+'|fldCharType')
            if kind=='begin':
                fields.append({'instruction':'','separated':False,'toc':False,'nodes':[]})
            elif kind=='separate' and fields:
                field=fields[-1];field['separated']=True
                field['toc']=bool(re.match(r'^\s*TOC(?:\s|$)',field['instruction'],re.I))
                seen_toc=seen_toc or field['toc']
            elif kind=='end' and fields:
                field=fields.pop();instruction=field['instruction']
                if any(parent['toc'] for parent in fields) and re.match(r'^\s*PAGEREF(?:\s|$)',instruction,re.I):
                    match=re.fullmatch(r'\s*PAGEREF\s+(_Toc\d+)\s*(?:\\h\s*)?',instruction,re.I)
                    value=''.join(node.value for node in field['nodes'])
                    if not match or not field['separated'] or not re.fullmatch(r'[0-9]{1,5}',value):
                        raise ValueError('Оглавление Word содержит неподдерживаемое поле номера страницы.')
                    result.append(TocPage(match[1],int(value),tuple(field['nodes'])))
        if name==WORD+'|fldSimple' and any(field['toc'] for field in fields):
            raise ValueError('Оглавление Word содержит неподдерживаемое простое поле.')
        if name in (WORD+'|t',WORD+'|instrText'):
            position=parser.CurrentByteIndex;opening_end=_tag_end(xml,position)
            capture={'name':name,'start':opening_end,'text':''}
    def text(value):
        if capture is not None:capture['text']+=value
    def end(name):
        nonlocal capture
        if capture is None or capture['name']!=name:return
        if fields:
            field=fields[-1]
            if name==WORD+'|instrText' and not field['separated']:
                field['instruction']+=capture['text']
            elif name==WORD+'|t' and field['separated'] and capture['text']:
                field['nodes'].append(Text(capture['start'],parser.CurrentByteIndex,capture['text']))
        capture=None
    parser.StartElementHandler=start;parser.CharacterDataHandler=text;parser.EndElementHandler=end
    parser.Parse(xml,True)
    if seen_toc and (fields or not result or len(result)>500):
        raise ValueError('Не удалось однозначно прочитать оглавление Word.')
    names=[field.bookmark for field in result]
    if len(set(names))!=len(names) or any(name not in bookmarks for name in names):
        raise ValueError('В оглавлении Word отсутствует или повторяется ссылка на раздел.')
    return result


def update_toc_pages(raw,pages):
    """Patch only cached numeric text; preserve the original native TOC."""
    fields=toc_page_fields(raw)
    if len(fields)!=len(pages) or any(type(page) is not int or not 1<=page<=10000 for page in pages):
        raise ValueError('Номера страниц PDF не совпадают со структурой оглавления Word.')
    xml=_read(raw);patches=[]
    for field,page in zip(fields,pages):
        if field.page==page:continue
        for index,node in enumerate(field.nodes):
            patches.append((node.start,node.end,str(page).encode('utf8') if index==0 else b''))
    for start,end,replacement in sorted(patches,reverse=True):xml=xml[:start]+replacement+xml[end:]
    return _package_document(raw,xml)


def _paragraph_spans(raw):
    parser=expat.ParserCreate(namespace_separator='|');stack=[];spans=[]
    def start(name,attributes):
        position=parser.CurrentByteIndex;opening_end=_tag_end(raw,position)
        frame=(name,position,opening_end,raw[position:opening_end].rstrip().endswith(b'/>'),None)
        if name==WORD+'|p':
            frame=(*frame[:4],len(spans));spans.append(None)
        stack.append(frame)
    def end(name):
        name,position,opening_end,empty,index=stack.pop()
        if index is not None:spans[index]=(position,opening_end if empty else _tag_end(raw,parser.CurrentByteIndex))
    parser.StartElementHandler=start;parser.EndElementHandler=end
    parser.Parse(raw,True)
    return spans


def _keep_next(raw):
    """Change only a paragraph's own keepNext property, preserving its markup."""
    opening=re.match(rb'<(?P<prefix>(?:[A-Za-z_][\w.-]*:)?)p\b[^>]*>',raw)
    if not opening:raise ValueError('Malformed Word paragraph')
    prefix=opening['prefix'];keep=b'<'+prefix+b'keepNext/>'
    if opening[0].rstrip().endswith(b'/>'):
        return opening[0].rstrip()[:-2]+b'><'+prefix+b'pPr>'+keep+b'</'+prefix+b'pPr></'+prefix+b'p>'
    ppr=re.search(rb'<'+re.escape(prefix)+rb'pPr\b[^>]*>',raw)
    if not ppr:return raw[:opening.end()]+b'<'+prefix+b'pPr>'+keep+b'</'+prefix+b'pPr>'+raw[opening.end():]
    if ppr[0].rstrip().endswith(b'/>'):
        return raw[:ppr.start()]+ppr[0].rstrip()[:-2]+b'>'+keep+b'</'+prefix+b'pPr>'+raw[ppr.end():]
    end=raw.find(b'</'+prefix+b'pPr>',ppr.end())
    if end<0:raise ValueError('Malformed Word paragraph properties')
    properties=raw[ppr.end():end]
    existing=re.search(rb'<'+re.escape(prefix)+rb'keepNext\b[^>]*(?:/>|>\s*</'+re.escape(prefix)+rb'keepNext\s*>)',properties)
    if existing:
        replacement=properties[:existing.start()]+keep+properties[existing.end():]
    else:
        style=re.match(rb'\s*<'+re.escape(prefix)+rb'pStyle\b[^>]*(?:/>|>\s*</'+re.escape(prefix)+rb'pStyle\s*>)',properties)
        at=style.end() if style else 0
        replacement=properties[:at]+keep+properties[at:]
    return raw[:ppr.end()]+replacement+raw[end:]


def prepare_pdf_layout(raw):
    """Create a render-only copy that keeps true headings with their content.

    Heading styles/outline levels identify chapters. Retained numbered financial
    subheadings use ordinary styles, so their exact labels are included. Only
    explicitly marked or shaded, bold initial header rows are kept with the next
    row; data rows remain free to paginate. A final empty hard page break can be
    removed, but text, images and section breaks are never removed.
    """
    xml=_read(raw);document=ET.fromstring(xml);q='{'+WORD+'}';body=document.find(q+'body')
    if body is None:raise ValueError('The Word document has no body')
    paragraphs=list(document.iter(q+'p'));spans=_paragraph_spans(xml)
    if len(paragraphs)!=len(spans):raise ValueError('Word paragraph structure mismatch')
    indices={id(p):i for i,p in enumerate(paragraphs)};selected=set();heading_styles=set()
    with ZipFile(BytesIO(raw)) as package:
        if 'word/styles.xml' in package.namelist():
            styles=ET.fromstring(package.read('word/styles.xml'),forbid_dtd=True,forbid_entities=True,forbid_external=True)
            for style in styles.findall(q+'style'):
                name=style.find(q+'name');outline=style.find(q+'pPr/'+q+'outlineLvl')
                if (name is not None and re.fullmatch(r'heading\s+[1-9]',name.get(q+'val',''),re.I)) or (outline is not None and outline.get(q+'val') in [str(i) for i in range(9)]):
                    heading_styles.add(style.get(q+'styleId'))
    def text(p):return ''.join(n.text or '' for n in p.iter(q+'t')).strip()
    def empty(p):
        return not text(p) and not any(n.tag in {q+'br',q+'pageBreakBefore',q+'drawing',q+'pict',q+'object',q+'fldChar',q+'instrText',q+'sectPr',q+'tab',q+'sym',q+'footnoteReference',q+'endnoteReference',q+'commentReference',q+'delText'} or n.tag.endswith('}oMath') for n in p.iter())
    heading_labels=re.compile(r'^(?:10\.2\.\s*Расчет прибылей и убытков|10\.3\.\s*Поток реальных денег|12\.3\.\s*Оценка возможности осуществления проекта\.?)$')
    # These two source subheadings use ordinary bold body styles. The first
    # introduces the market segments; the second must remain with its text.
    ordinary_heading_labels={'Сегментация по типу потребителей (B2B / B2G / B2C)','Государственный сектор (B2G):'}
    direct=list(body);bridge=False
    for child in direct:
        if child.tag!=q+'p':bridge=False;continue
        style=child.find(q+'pPr/'+q+'pStyle');outline=child.find(q+'pPr/'+q+'outlineLvl')
        is_heading=bool(text(child)) and (style is not None and style.get(q+'val') in heading_styles or outline is not None and outline.get(q+'val') in [str(i) for i in range(9)] or bool(heading_labels.fullmatch(text(child))) or ' '.join(text(child).split()) in ordinary_heading_labels)
        if is_heading:selected.add(indices[id(child)]);bridge=True
        elif bridge and empty(child):selected.add(indices[id(child)])
        else:bridge=False
    for table in body.findall(q+'tbl'):
        rows=table.findall(q+'tr')
        for row_index,row in enumerate(rows[:-1]):
            marked=row.find(q+'trPr/'+q+'tblHeader')
            cells=row.findall(q+'tc')
            shaded=bool(cells) and all(c.find(q+'tcPr/'+q+'shd') is not None for c in cells)
            bold=any(c.find('.//'+q+'b') is not None for c in cells)
            explicit=marked is not None and marked.get(q+'val','true').lower() not in ('0','false','off')
            if not explicit and not (row_index<2 and shaded and bold):break
            selected.update(indices[id(p)] for p in row.iter(q+'p'))
    replacements={}
    for index in selected:
        start,end=spans[index];updated=_keep_next(xml[start:end])
        if updated!=xml[start:end]:replacements[start,end]=updated
    # Only a last, empty body paragraph can lose a hard page break. The final
    # source paragraph often has no break; no whitespace trimming is attempted.
    content=[c for c in direct if c.tag!=q+'sectPr']
    if content and content[-1].tag==q+'p':
        last=content[-1];visible=text(last) or any(n.tag in {q+'drawing',q+'pict',q+'object',q+'fldChar',q+'instrText',q+'sectPr',q+'tab',q+'sym',q+'footnoteReference',q+'endnoteReference',q+'commentReference',q+'delText'} or n.tag.endswith('}oMath') for n in last.iter())
        page_breaks=[n for n in last.iter(q+'br') if n.get(q+'type')=='page']
        before=last.find(q+'pPr/'+q+'pageBreakBefore')
        if not visible and (page_breaks or before is not None):
            start,end=spans[indices[id(last)]];fragment=replacements.get((start,end),xml[start:end])
            prefix=re.match(rb'<((?:[A-Za-z_][\w.-]*:)?)p\b',fragment)[1]
            fragment=re.sub(rb'<'+re.escape(prefix)+rb'br\b(?=[^>]*\b(?:[\w.-]+:)?type=["\']page["\'])[^>]*/>',b'',fragment)
            fragment=re.sub(rb'<'+re.escape(prefix)+rb'pageBreakBefore\b[^>]*/>',b'',fragment)
            replacements[start,end]=fragment
    patched=xml
    for (start,end),replacement in sorted(replacements.items(),reverse=True):patched=patched[:start]+replacement+patched[end:]
    ET.fromstring(patched,forbid_dtd=True,forbid_entities=True,forbid_external=True)
    return _package_document(raw,patched)


def report_sources(raw,profile_id=None):
    """Return the verified source-cell targets actually present in this template."""
    xml=_read(raw);spans=_containers(xml);bindings=_bindings(xml,spans,profile_id);sources=[]
    for word_row,excel_row in COST_ROWS:
        for word_column,excel_column in COST_COLUMNS:
            key=('cell',0,word_row,word_column)
            if key not in spans:raise ValueError('The project-cost table does not match the retained template')
            old=''.join(n.value for n in _text_nodes(xml,spans[key]))
            if MONEY.fullmatch(old.strip()):sources.append({'sheet':'Стоим_проекта','cell':excel_column+str(excel_row)})
    sources.append({'sheet':'Стоим_проекта','cell':'B38'})
    sources.extend({'sheet':'ТОЧКА','cell':cell} for _,cell,_ in BREAK_EVEN)
    if _has_utility_table(xml,spans):sources.extend({'sheet':'Труд','cell':cell} for _,_,cell in UTILITY_CELLS)
    key=('paragraph',bindings['staff'])
    if key in spans:
        old=''.join(n.value for n in _text_nodes(xml,spans[key]))
        if all(pattern.search(old) for pattern,_ in STAFF_SLOTS):sources.extend({'sheet':'Труд','cell':cell} for cell in bindings['staff_cells'])
    if any(('paragraph',index) in spans and CREDIT_AMOUNT.search(''.join(n.value for n in _text_nodes(xml,spans['paragraph',index]))) for index in bindings['credit']):
        sources.append({'sheet':'Стоим_проекта','cell':'D34'})
    key=('paragraph',bindings['vat'])
    if key in spans:
        old=''.join(n.value for n in _text_nodes(xml,spans[key]))
        if VAT_TEXT.fullmatch(old):sources.append({'sheet':'НДС','cell':'F4'})
    for index,expected,sheet,cells in ((bindings['no_loss'],NO_LOSS_TEXT,'Приб_Убыт',PROFIT_CELLS),
                                       (bindings['positive_cash'],POSITIVE_CASH_TEXT,'Поток_нал',CASH_FLOW_CELLS+CUMULATIVE_CASH_CELLS)):
        key=('paragraph',index)
        if key in spans and ' '.join(''.join(n.value for n in _text_nodes(xml,spans[key])).split())==expected:
            sources.extend({'sheet':sheet,'cell':cell} for cell in cells)
    return sources


def _has_utility_table(xml,spans):
    return all(('cell',5,row,0) in spans and ''.join(n.value for n in _text_nodes(xml,spans['cell',5,row,0])).strip()==label
               for row,label in ((2,'Электроэнергия'),(3,'Газ'),(4,'ВСЕГО')))


def patch_business_docx(raw,values,profile_id=None):
    """Return (docx_bytes, metadata), updating only verified template bindings.

    Table0 maps project cost, table6 maps break-even, and explicit prose slots
    map credit and VAT. Two optimistic source conclusions are checked against
    all 36 fresh monthly results, and replaced if they no longer hold. Table4 is
    a market-share statement with no model binding. Unverified text or embedded
    pictures are not guessed or rewritten.
    """
    xml=_read(raw);spans=_containers(xml);bindings=_bindings(xml,spans,profile_id);patches=[];edits=[];mapped=[]
    def cell(table,row,column,sheet,address,scale=1,optional=False):
        key=('cell',table,row,column)
        if key not in spans:raise ValueError('The Word table structure does not match the retained template')
        nodes=_text_nodes(xml,spans[key]);old=''.join(n.value for n in nodes)
        if optional and not MONEY.fullmatch(old.strip()):return
        if not MONEY.fullmatch(old.strip()):raise ValueError('A verified numeric table slot is no longer numeric')
        value=_value(values,sheet,address);new=_format(value,old,scale)
        mapped.append({'kind':'table','table':table,'row':row,'cell':column,'sheet':sheet,'address':address})
        if old.strip()==new:return
        leading=len(old)-len(old.lstrip());trailing=len(old.rstrip())
        _replace(nodes,leading,trailing,new,patches)
        edits.append({**mapped[-1],'before':old,'after':new})
    for word_row,excel_row in COST_ROWS:
        for word_column,excel_column in COST_COLUMNS:cell(0,word_row,word_column,'Стоим_проекта',excel_column+str(excel_row),optional=True)
    cell(0,14,1,'Стоим_проекта','B38')
    for row,address,scale in BREAK_EVEN:cell(6,row,1,'ТОЧКА',address,scale)
    if _has_utility_table(xml,spans):
        for row,column,address in UTILITY_CELLS:cell(5,row,column,'Труд',address)
    paragraphs=0
    for index,expected in bindings['credit'].items():
        key=('paragraph',index)
        if key not in spans:continue
        nodes=_text_nodes(xml,spans[key]);old=''.join(n.value for n in nodes)
        matches=list(CREDIT_AMOUNT.finditer(old))
        if not matches:continue
        if len(matches)!=expected:raise ValueError('The credit-amount paragraph no longer matches its verified slots')
        value=_value(values,'Стоим_проекта','D34')
        for match in matches:
            before=match['amount'];after=_format(value,before)
            binding={'kind':'paragraph','paragraph':index,'sheet':'Стоим_проекта','address':'D34','start':match.start('amount'),'end':match.end('amount')}
            mapped.append(binding);paragraphs+=1
            if before!=after:
                _replace(nodes,match.start('amount'),match.end('amount'),after,patches)
                edits.append({**binding,'before':before,'after':after})
    key=('paragraph',bindings['staff'])
    if key in spans:
        nodes=_text_nodes(xml,spans[key]);old=''.join(n.value for n in nodes)
        matches=[pattern.search(old) for pattern,_ in STAFF_SLOTS]
        if all(matches):
            production,admin=bindings['staff_cells']
            for match,addresses in zip(matches,((production,admin),(admin,),(production,))):
                before=match['amount'];after=_format(sum(_value(values,'Труд',address) for address in addresses),before)
                binding={'kind':'paragraph','paragraph':bindings['staff'],'sheet':'Труд','addresses':list(addresses),'start':match.start('amount'),'end':match.end('amount')}
                mapped.append(binding);paragraphs+=1
                if before!=after:
                    _replace(nodes,match.start('amount'),match.end('amount'),after,patches)
                    edits.append({**binding,'before':before,'after':after})
    key=('paragraph',bindings['vat'])
    if key in spans:
        nodes=_text_nodes(xml,spans[key]);old=''.join(n.value for n in nodes);match=VAT_TEXT.fullmatch(old)
        if match:
            number=_value(values,'НДС','F4')*100
            # Tax-rate wording must not round a supplied fractional percentage
            # into a different rate; retain the source decimal separator.
            before=match['amount'];digits=format(number,'f').rstrip('0').rstrip('.') if number else '0'
            if number==number.to_integral():digits=format(number.quantize(Decimal(1)),'f')
            after=digits.replace('.',',' if ',' in before or '.' not in before else '.')+'%'
            binding={'kind':'paragraph','paragraph':bindings['vat'],'sheet':'НДС','address':'F4','start':match.start('amount'),'end':match.end('amount')}
            mapped.append(binding);paragraphs+=1
            if before!=after:
                _replace(nodes,match.start('amount'),match.end('amount'),after,patches)
                edits.append({**binding,'before':before,'after':after})
    for index,expected,sheet,cells,comparison,replacement in (
            (bindings['no_loss'],NO_LOSS_TEXT,'Приб_Убыт',PROFIT_CELLS,'nonnegative',
             'В пересчитанном прогнозе имеются месяцы с убытком. Помесячные результаты представлены в финансовом приложении.'),
            (bindings['positive_cash'],POSITIVE_CASH_TEXT,'Поток_нал',CASH_FLOW_CELLS+CUMULATIVE_CASH_CELLS,'positive',
             'Помесячный и кумулятивный поток наличности представлены в пересчитанном финансовом приложении. Положительный поток на протяжении всего периода не подтверждается.')):
        key=('paragraph',index)
        if key not in spans:continue
        nodes=_text_nodes(xml,spans[key]);old=''.join(n.value for n in nodes)
        if ' '.join(old.split())!=expected:continue
        numbers=[_value(values,sheet,address) for address in cells]
        binding={'kind':'narrative','paragraph':index,'sheet':sheet,'addresses':list(cells),'comparison':comparison}
        mapped.append(binding)
        holds=all(n>=0 for n in numbers) if comparison=='nonnegative' else all(n>0 for n in numbers)
        if holds:continue
        leading=len(old)-len(old.lstrip());trailing=len(old.rstrip())
        _replace(nodes,leading,trailing,replacement,patches)
        edits.append({**binding,'before':old,'after':replacement,'reason':'fresh_monthly_results_do_not_support_source_conclusion'})
    # This exact retained paragraph refers to another industry. Correct only
    # its verified slot, without guessing market growth or changing custom prose.
    key=('paragraph',bindings['sector'])
    if key in spans:
        nodes=_text_nodes(xml,spans[key]);old=''.join(n.value for n in nodes)
        if old==OBSOLETE_SECTOR_TEXT:
            binding={'kind':'narrative','paragraph':bindings['sector']}
            mapped.append(binding)
            leading=len(old)-len(old.lstrip());trailing=len(old.rstrip())
            _replace(nodes,leading,trailing,PHARMACEUTICAL_MARKET_TEXT,patches)
            edits.append({**binding,'before':old,'after':PHARMACEUTICAL_MARKET_TEXT,
                          'reason':'obsolete_sector_reference'})
    # Replace a verified leftover from another company only when the retained
    # ZUMA document itself supplies its actual production location. No floor
    # area or legal-address-to-factory inference is added.
    key=('paragraph',ZUMA_LOCATION_PARAGRAPH)
    evidence=('paragraph',146)
    if bindings['profile_id']==ZUMA_PROFILE and key in spans and evidence in spans:
        nodes=_text_nodes(xml,spans[key]);old=''.join(n.value for n in nodes)
        confirmed=''.join(n.value for n in _text_nodes(xml,spans[evidence]))
        if old==ZUMA_LOCATION_TEXT and ZUMA_LOCATION_EVIDENCE in confirmed:
            binding={'kind':'narrative','paragraph':ZUMA_LOCATION_PARAGRAPH,'source_paragraph':146}
            mapped.append(binding)
            _replace(nodes,0,len(old),ZUMA_LOCATION_REPLACEMENT,patches)
            edits.append({**binding,'before':old,'after':ZUMA_LOCATION_REPLACEMENT,
                          'reason':'unrelated_company_location_corrected_from_source'})
    # Several numeric mentions may share one w:t node. Merge their character
    # edits before serialising, so one edit cannot overwrite another.
    by_span={}
    for start,end,original,left,right,replacement in patches:
        node=by_span.setdefault((start,end),{'original':original,'edits':[]})
        if node['original']!=original or any(left<b and a<right for a,b,_ in node['edits']):
            raise ValueError('Overlapping verified Word text edits require a template revision')
        node['edits'].append((left,right,replacement))
    patched=xml
    for (start,end),node in sorted(by_span.items(),reverse=True):
        text=node['original']
        for left,right,replacement in sorted(node['edits'],reverse=True):text=text[:left]+replacement+text[right:]
        patched=patched[:start]+escape(text).encode('utf8')+patched[end:]
    ET.fromstring(patched,forbid_dtd=True,forbid_entities=True,forbid_external=True)
    return _package_document(raw,patched),{'profile_id':bindings['profile_id'],'edits':edits,'updated_fields':len(edits),'mapped_fields':mapped,
             'mapped_table_cells':sum(i['kind']=='table' for i in mapped),'mapped_paragraph_mentions':paragraphs,
             'preserved_other_zip_parts':True,'preserved_media':True,
             'scope':'verified_project_cost_utilities_break_even_credit_vat_staff_monthly_conclusions_sector_prose','unmapped_numeric_text':'retained_without_inference'}
