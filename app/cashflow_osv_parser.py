"""Read an account-5110 turnover hierarchy and a separate Cash Flow template.

Neither parser posts transactions. Source totals are independently reconciled at
the article, account and overall levels before a snapshot may be committed.
"""
from __future__ import annotations
import io, re, zipfile, posixpath
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from defusedxml import ElementTree as ET
from openpyxl.utils.datetime import from_excel, CALENDAR_WINDOWS_1900, CALENDAR_MAC_1904

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
RID='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
MAX_FILE=8*1024*1024
MAX_ROWS=50000
ZERO=Decimal('0.00')
SCENARIOS={'А':'A','A':'A','Б':'B','B':'B','В':'V','V':'V'}

def text(value):return re.sub(r'\s+',' ',str(value or '').replace('\xa0',' ')).strip()
def dec(value,blank=True):
    if value is None or (isinstance(value,str) and not value.strip()):
        if blank:return ZERO
        raise ValueError('Отсутствует денежная сумма.')
    if isinstance(value,bool):raise ValueError('Логическое значение вместо суммы.')
    try:n=Decimal(str(value))
    except (InvalidOperation,ValueError):raise ValueError('Некорректная денежная сумма.')
    if not n.is_finite() or abs(n)>Decimal('1000000000000000'):raise ValueError('Сумма должна быть конечным числом допустимого размера.')
    rounded=n.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    if abs(n-rounded)>Decimal('.00001'):raise ValueError('Сумма должна иметь максимум два десятичных знака.')
    return rounded
def fmt(n):return format(n.quantize(Decimal('.01'),rounding=ROUND_HALF_UP),'f')
def code(value):
    if isinstance(value,(int,float)) and not isinstance(value,bool) and int(value)==value:return str(int(value))
    return text(value)

class Book:
    def __init__(self,raw):
        if not raw or len(raw)>MAX_FILE:raise ValueError('Размер XLSX должен быть не более 8 МБ.')
        try:self.z=zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:raise ValueError('Файл не является XLSX.')
        entries=self.z.infolist();names=[x.filename for x in entries]
        if len(names)!=len(set(names)) or len(entries)>3000:raise ValueError('Некорректная структура XLSX.')
        if any(x.flag_bits&1 or x.filename.startswith('/') or '..' in x.filename.split('/') for x in entries):raise ValueError('Небезопасная структура XLSX.')
        if sum(x.file_size for x in entries)>100*1024*1024:raise ValueError('Распакованный XLSX больше 100 МБ.')
        if any('vbaProject' in n or n.startswith('xl/externalLinks/') for n in names):raise ValueError('Макросы и внешние ссылки XLSX не поддерживаются.')
        if not {'xl/workbook.xml','xl/_rels/workbook.xml.rels','[Content_Types].xml'}<=set(names):raise ValueError('Отсутствует структура книги XLSX.')
        self.strings=[]
        if 'xl/sharedStrings.xml' in names:
            for item in ET.fromstring(self.z.read('xl/sharedStrings.xml')).findall('s:si',NS):
                self.strings.append(''.join(t.text or '' for t in item.iterfind('.//s:t',NS)))
        book=ET.fromstring(self.z.read('xl/workbook.xml'));rels={r.attrib['Id']:r.attrib['Target'] for r in ET.fromstring(self.z.read('xl/_rels/workbook.xml.rels'))}
        self.sheets={}
        for s in book.findall('s:sheets/s:sheet',NS):
            target=rels[s.attrib[RID]];path=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
            if not path.startswith('xl/') or '..' in path.split('/'):raise ValueError('Некорректный путь листа.')
            self.sheets[s.get('name')]=path
        prop=book.find('s:workbookPr',NS);self.epoch=CALENDAR_MAC_1904 if prop is not None and prop.get('date1904') in ('1','true') else CALENDAR_WINDOWS_1900
        self.indents=[]
        if 'xl/styles.xml' in names:
            styles=ET.fromstring(self.z.read('xl/styles.xml'))
            for xf in styles.findall('s:cellXfs/s:xf',NS):
                a=xf.find('s:alignment',NS);self.indents.append(int(a.get('indent','0')) if a is not None else 0)
    def close(self):self.z.close()
    def sheet(self,name,max_col=50):
        if name not in self.sheets:raise ValueError('Не найден лист «'+name+'».')
        cells={};root=ET.fromstring(self.z.read(self.sheets[name]));count=0
        for c in root.findall('s:sheetData/s:row/s:c',NS):
            ref=c.get('r','');m=re.fullmatch(r'([A-Z]+)([0-9]+)',ref)
            if not m:raise ValueError('Некорректный адрес ячейки.')
            row=int(m.group(2));col=0
            for ch in m.group(1):col=col*26+ord(ch)-64
            if row>MAX_ROWS:raise ValueError('На листе больше 50 000 строк.')
            if col>max_col:continue
            count+=1
            if count>1500000:raise ValueError('Слишком много ячеек на листе.')
            vn=c.find('s:v',NS);value=vn.text if vn is not None else None;typ=c.get('t')
            if typ=='s' and value is not None:value=self.strings[int(value)]
            elif typ=='inlineStr':value=''.join(x.text or '' for x in c.iterfind('.//s:t',NS))
            elif typ not in ('str','e','b') and value is not None:
                try:value=Decimal(value)
                except InvalidOperation:pass
            fn=c.find('s:f',NS);idx=int(c.get('s','0'))
            cells[(row,col)]={'value':value,'formula':fn.text if fn is not None else None,'has_formula':fn is not None,'error':typ=='e','indent':self.indents[idx] if idx<len(self.indents) else 0,'ref':ref}
        return cells

def parse_bank(raw):
    book=Book(raw)
    try:
        name=next(iter(book.sheets));cells=book.sheet(name,7)
        def item(r,c):return cells.get((r,c),{'value':None,'has_formula':False,'error':False,'indent':0})
        def val(r,c):return item(r,c)['value']
        def amounts(r):
            for c in range(2,8):
                if item(r,c)['has_formula'] or item(r,c)['error']:raise ValueError(f'Строка {r}: в банковской ОСВ нужны значения, без формул и ошибок Excel.')
            return [dec(val(r,c)) for c in range(2,8)]
        def control(r):
            b,c,d,e,f,g=amounts(r);return {'row':r,'opening':b-c,'debit':d,'credit':e,'closing':f-g}
        roots=[];finals=[];banks=[];groups=[];records=[];bank=None;group=None;started=False;seen=set()
        for r in sorted({x[0] for x in cells}):
            a=item(r,1);label=text(a['value']);indent=a['indent']
            if not label:continue
            if a['has_formula'] or a['error']:raise ValueError(f'Строка {r}: код или дата ОСВ содержит формулу или ошибку.')
            if code(a['value'])=='5110' and indent==0:
                roots.append(control(r));started=True;bank=None;group=None;continue
            if not started:continue
            if label.casefold() in ('итого','всего'):
                finals.append(control(r));continue
            match=re.fullmatch(r'Обороты\s+за\s+(\d{2}\.\d{2}\.\d{2}(?:\d{2})?)',label,re.I)
            if match:
                if not bank or not group or not group.get('code'):raise ValueError(f'Строка {r}: обороты без счёта или кода статьи.')
                ds=match.group(1)
                try:day=datetime.strptime(ds,'%d.%m.%y' if len(ds)==8 else '%d.%m.%Y').date()
                except ValueError:raise ValueError(f'Строка {r}: неверная дата.')
                if day.year<2000 or day.year>2100:raise ValueError(f'Строка {r}: год вне допустимого диапазона.')
                b,c,d,e,f,g=amounts(r)
                if d<0 or e<0:raise ValueError(f'Строка {r}: отрицательный оборот.')
                identity=(bank['key'],group['code'],day.isoformat())
                if identity in seen:raise ValueError(f'Строка {r}: повтор счёта, статьи и даты.')
                seen.add(identity)
                record={'date':day.isoformat(),'month':day.strftime('%Y-%m'),'code':group['code'],'name':group['name'],'debit':fmt(d),'credit':fmt(e),'internal':group['code']=='00-000010','source_row':r,'source_ref':f"'{name}'!A{r}:G{r}",'bank_key':bank['key'],'bank_label':bank['label']}
                records.append(record);group['records'].append(record);continue
            if indent==1:
                bank={**control(r),'key':f'bank_{len(banks)+1}','label':f'Банк {len(banks)+1}'};banks.append(bank);group=None
            elif indent==2:
                if not bank:raise ValueError(f'Строка {r}: статья вне банковского счёта.')
                group={**control(r),'bank':bank['key'],'name':label,'code':None,'records':[]};groups.append(group)
            elif indent==3:
                if not group or group.get('code'):raise ValueError(f'Строка {r}: код вне статьи или повтор кода.')
                cd=code(a['value'])
                if not re.fullmatch(r'(?:[0-9]{1,6}|00-[0-9]{6})',cd):raise ValueError(f'Строка {r}: неизвестный формат кода статьи.')
                group['code']=cd;group['code_control']=control(r)
            else:raise ValueError(f'Строка {r}: неизвестная строка внутри ОСВ.')
        if len(roots)!=1 or len(finals)!=1 or not records or not banks:raise ValueError('Нужна одна ОСВ счёта 5110 с банками, статьями, дневными оборотами и строкой «Итого».')
        def sums(rows):return sum((dec(x['debit']) for x in rows),ZERO),sum((dec(x['credit']) for x in rows),ZERO)
        def equal(actual,expected,message):
            if actual!=expected:raise ValueError(message)
        for g in groups:
            if not g.get('code') or not g['records']:raise ValueError(f'Строка {g["row"]}: статья без кода или дневных оборотов.')
            d,e=sums(g['records'])
            for ctrl in (g,g['code_control']):equal((d,e),(ctrl['debit'],ctrl['credit']),f'Строка {ctrl["row"]}: сумма дневных оборотов не совпадает с итогом статьи.')
        for b in banks:
            d,e=sums([x for x in records if x['bank_key']==b['key']]);equal((d,e),(b['debit'],b['credit']),f'Строка {b["row"]}: обороты банка не сходятся.');equal(b['opening']+d-e,b['closing'],f'Строка {b["row"]}: остатки банка не сходятся.')
        d,e=sums(records);root=roots[0]
        for ctrl in roots+finals:equal((d,e),(ctrl['debit'],ctrl['credit']),f'Строка {ctrl["row"]}: общий итог не совпадает с деталями.');equal(root['opening'],ctrl['opening'],'Начальные остатки итогов не совпадают.');equal(root['closing'],ctrl['closing'],'Конечные остатки итогов не совпадают.')
        equal(sum((b['opening'] for b in banks),ZERO),root['opening'],'Начальные остатки банков не совпадают с общим итогом.');equal(sum((b['closing'] for b in banks),ZERO),root['closing'],'Конечные остатки банков не совпадают с общим итогом.');equal(root['opening']+d-e,root['closing'],'Начальный остаток плюс обороты не равен конечному.')
        internal=[x for x in records if x['internal']];di,ei=sums(internal);equal(di,ei,'Внутренние переводы не сбалансированы между счетами.')
        first=min(x['date'] for x in records);last=max(x['date'] for x in records)
        return {'records':records,'metadata':{'record_count':len(records),'bank_count':len(banks),'banks':[{'bank_key':b['key'],'label':b['label']} for b in banks],'first_date':first,'last_date':last,'year_from':int(first[:4]),'year_to':int(last[:4]),'opening':fmt(root['opening']),'inflow':fmt(d),'outflow':fmt(e),'closing':fmt(root['closing']),'internal_inflow':fmt(di),'internal_outflow':fmt(ei),'source_sheet':name},'issues':[{'level':'info','code':'daily_aggregate','message':'ОСВ содержит дневные обороты по статье и счёту, а не отдельные платежи.'},{'level':'info','code':'units_confirmation','message':'Компания, валюта и единица исходных сумм подтверждаются при сохранении.'}]}
    finally:book.close()

def parse_template(raw,years=None):
    book=Book(raw)
    try:
        budget=book.sheet('Бюджет',7);plans=book.sheet('план на год',10)
        def v(sheet,r,c):return sheet.get((r,c),{}).get('value')
        categories=[];direction=None;seen=set()
        for r in sorted({x[0] for x in budget}):
            name=text(v(budget,r,5));label=name.casefold()
            if 'жами ҳаражат' in label:direction='expense';continue
            if 'жами тушум' in label:direction='receipt';continue
            if label in ('фарки','фарқи') or 'утган ойдаги' in label:direction=None
            cd=code(v(budget,r,2))
            if not direction or not cd or not name:continue
            if not re.fullmatch(r'[0-9]{1,6}',cd):continue
            if cd in seen:raise ValueError(f'Бюджет!B{r}: повтор кода статьи.')
            seen.add(cd);categories.append({'code':cd,'name':name,'group':text(v(budget,r,4)),'kind':direction,'source_ref':f'Бюджет!B{r}:G{r}'})
        if not categories:raise ValueError('В шаблоне не найдены кодированные статьи расходов и поступлений.')
        columns={}
        for c in (8,9,10):
            scenario=SCENARIOS.get(text(v(plans,6,c)).upper()[:1])
            if not scenario or scenario in columns:raise ValueError('Нужны разные заголовки А, Б, В в H6:J6 шаблона.')
            columns[scenario]=c
        result=[];seen=set();issues=[];exclude=[]
        for r in sorted({x[0] for x in plans}):
            if r<8:continue
            dt=v(plans,r,1)
            try:dt=from_excel(float(dt),book.epoch) if isinstance(dt,(Decimal,int,float)) else dt
            except (ValueError,OverflowError,TypeError):continue
            if not isinstance(dt,(date,datetime)) or not 2000<=dt.year<=2100 or (years and dt.year not in years):continue
            cd=code(v(plans,r,3));name=text(v(plans,r,5))
            if cd not in {x['code'] for x in categories} or not name:continue
            if re.search(r'остат|колди|қолди|жами|итог|фарк|фарқ',name,re.I):exclude.append(f'план на год!{r}');continue
            month=dt.strftime('%Y-%m');key=(month,cd)
            if key in seen:raise ValueError(f'план на год!C{r}: повтор статьи в месяце.')
            seen.add(key);amounts={};statuses={};refs={}
            for scenario,c in columns.items():
                item=plans.get((r,c),{});value=item.get('value');ref=f'план на год!{chr(64+c)}{r}';refs[scenario]=ref
                if item.get('error') or (item.get('has_formula') and value is None):
                    statuses[scenario]='error';amounts[scenario]=None;issues.append({'level':'warning','code':'plan_error','message':'Ошибка или нет сохранённого результата формулы плана.','cell':ref});continue
                if value is None or (isinstance(value,str) and not value.strip()):statuses[scenario]='missing';amounts[scenario]=None;continue
                try:n=Decimal(str(value))
                except (InvalidOperation,ValueError):statuses[scenario]='error';amounts[scenario]=None;issues.append({'level':'warning','code':'plan_error','message':'План содержит текст вместо суммы.','cell':ref});continue
                if not n.is_finite() or n<0 or abs(n)>Decimal('1000000000000000'):statuses[scenario]='error';amounts[scenario]=None;issues.append({'level':'warning','code':'plan_error','message':'План содержит недопустимую сумму.','cell':ref});continue
                statuses[scenario]='numeric';amounts[scenario]=str(n)
            result.append({'month':month,'code':cd,'name':name,'amounts':amounts,'statuses':statuses,'refs':refs})
        if not result:raise ValueError('В шаблоне нет плановых статей с месяцами.')
        if exclude:issues.append({'level':'info','code':'opening_not_income','message':'Строки начальных остатков исключены из поступлений.'})
        return {'categories':categories,'plans':result,'issues':issues,'scenario_columns':{k:chr(64+c) for k,c in columns.items()}}
    finally:book.close()
