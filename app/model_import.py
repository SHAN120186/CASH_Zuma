"""Точный снимок сохранённых значений XLSX. Формулы НЕ пересчитываются.
Даты/ошибки/нули не подменяются догадками; исходный файл не изменяется.
"""
from __future__ import annotations
import io, json, hashlib, posixpath
from zipfile import ZipFile, BadZipFile
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from sqlalchemy import select
from .db import ModelVersion, Audit, DATA

NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
RID='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
MAX_SIZE=20*1024*1024

def col(n):
    out=''
    while n: n,r=divmod(n-1,26);out=chr(65+r)+out
    return out

def read_xlsx(raw):
    if len(raw)>MAX_SIZE: raise ValueError('Файл больше 20 МБ.')
    try:
        with ZipFile(io.BytesIO(raw)) as z:
            if len(z.infolist())>2500 or sum(x.file_size for x in z.infolist())>150*1024*1024:
                raise ValueError('Слишком большой распакованный XLSX.')
            def xml(path): return ET.fromstring(z.read(path))
            strings=[]
            if 'xl/sharedStrings.xml' in z.namelist():
                strings=[''.join(t.text or '' for t in si.iter('{'+NS['m']+'}t')) for si in xml('xl/sharedStrings.xml')]
            rels={v.attrib['Id']:v.attrib['Target'] for v in xml('xl/_rels/workbook.xml.rels')}
            wb=xml('xl/workbook.xml'); names={};dates1904=False
            props=wb.find('m:workbookPr',NS)
            if props is not None:dates1904=props.attrib.get('date1904') in ('1','true')
            for sh in wb.find('m:sheets',NS):
                target=rels[sh.attrib[RID]]
                path=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
                names[sh.attrib['name']]=path
            wanted=['09_Statements','09_CASH_Flow','09b_Balance','04_Opex','06b_Credit_Schedule','09_CASH_Flow_TEST','98_CashMacro_Import','11_Checks']
            if not {'09_Statements','09_CASH_Flow'} <= names.keys():
                raise ValueError('Ожидается структура FinModel Zuma: листы 09_Statements и 09_CASH_Flow.')
            data={}; errors=[];missing_cache=[]
            for name in wanted:
                if name not in names:continue
                d={}
                for c in xml(names[name]).findall('.//m:sheetData/m:row/m:c',NS):
                    address=c.attrib['r'];v=c.find('m:v',NS);f=c.find('m:f',NS)
                    val=v.text if v is not None else None
                    typ=c.attrib.get('t','n')
                    if typ=='s' and val is not None:val=strings[int(val)]
                    if typ=='inlineStr':val=''.join(t.text or '' for t in c.iter('{'+NS['m']+'}t'))
                    d[address]={'value':val,'formula':f.text if f is not None else None,'type':typ}
                    if typ=='e':errors.append({'sheet':name,'cell':address,'error':val})
                    if f is not None and val is None:missing_cache.append(name+'!'+address)
                data[name]=d
            return names,data,errors,missing_cache,dates1904
    except (BadZipFile, KeyError, IndexError, ET.ParseError, DefusedXmlException) as e:
        raise ValueError('Не удалось прочитать XLSX. Нужен обычный, не зашифрованный Excel-файл.') from e

def build_snapshot(raw, filename):
    names,data,errors,missing,epoch1904=read_xlsx(raw)
    base=datetime(1904,1,1) if epoch1904 else datetime(1899,12,30)
    def value(sh,a):return data.get(sh,{}).get(a,{}).get('value')
    def date_at(sh,a):
        try:return (base+timedelta(days=float(value(sh,a)))).date().isoformat()
        except (ValueError,TypeError,OverflowError):return None
    def period(sh,c,r):return {'column':col(c),'date':date_at(sh,f'{col(c)}{r}'),'source':f'{sh}!{col(c)}{r}'}
    def rows(sh,label_col,row_ids,cols):
        out=[]
        for r in row_ids:
            label=value(sh,f'{label_col}{r}')
            if not label or not str(label).strip():continue
            out.append({'row':r,'label':label,'cells':[
                dict(data.get(sh,{}).get(f'{col(c)}{r}',{'value':None,'formula':None,'type':'n'}),
                     source=f'{sh}!{col(c)}{r}') for c in cols]})
        return out
    sheets=[]
    def add(key,sh,title,lc,rs,cs,header,paired=False):
        if sh not in data:return
        sheets.append({'key':key,'sheet':sh,'title':title,'paired':paired,
            'periods':[period(sh,c,header) for c in cs], 'rows':rows(sh,lc,rs,cs)})
    add('pnl','09_Statements','План / факт · финансовый результат','D',range(7,25),list(range(5,29)),4,True)
    add('cfplan','09_Statements','Cash Flow · блок финмодели','D',range(28,43),list(range(5,29)),26,True)
    add('cash','09_CASH_Flow','Cash Flow · исходный лист','B',[6,8,9,10,11,12,13,16,17,18,19,45,49,54,56,57],list(range(3,15)),4)
    add('balance','09b_Balance','Баланс · план / факт','D',range(7,38),list(range(5,29)),4,True)
    add('opex','04_Opex','Операционные расходы · источник','A',range(8,11),list(range(2,14)),6)
    add('debt','06b_Credit_Schedule','Кредитный график · источник','A',range(7,16),list(range(2,14)),5)
    warnings=[
        'Импортированы сохранённые значения Excel. Формулы и внешние связи на сервере не пересчитываются.',
        'Месячные показатели модели не являются банковской выпиской или реестром кассовых операций. Они не включены в текущие остатки.',
        'Нули в будущих столбцах сохранены как в файле. Они не означают подтверждённый нулевой факт.',
        'План прибылей и убытков не переносится автоматически в платёжный календарь: нужны даты оплаты, счета и подтверждение статей.'
    ]
    for sh,cell in [('09_CASH_Flow','C4'),('09_Statements','E26'),('04_Opex','B6')]:
        d=date_at(sh,cell)
        if d and not d.startswith('2026'):
            warnings.append(f'{sh}!{cell}: дата источника {d}, хотя файл называется FinModel 2026. Год не исправлен автоматически.')
    if errors:warnings.append(f'В проверенных листах обнаружено {len(errors)} ячеек с ошибками Excel; они сохранены отдельно, не заменены нулями.')
    if missing:warnings.append(f'{len(missing)} формульных ячеек без сохранённого результата. Пустые результаты не считаются нулём.')
    summary={}
    for key,sh,a in [('cash_close_july','09_CASH_Flow','I54'),('balance_cash_july','09b_Balance','R8')]:
        summary[key]={'value':value(sh,a),'source':sh+'!'+a}
    return {'filename':filename,'sheet_names':list(names),'sheets':sheets,
            'warnings':warnings,'errors':errors[:250],'missing_cache':missing[:250],
            'error_count':len(errors),'missing_cache_count':len(missing),'summary':summary}

def import_snapshot(s,raw,filename,source='Загрузка XLSX',user_id=None):
    sha=hashlib.sha256(raw).hexdigest()
    old=s.scalar(select(ModelVersion).where(ModelVersion.sha256==sha))
    if old:return old,False
    snap=build_snapshot(raw,filename)
    archive=DATA/'models';archive.mkdir(exist_ok=True)
    target=archive/(sha+'.xlsx')
    if not target.exists():
        tmp=archive/(sha+'.tmp');tmp.write_bytes(raw);tmp.replace(target)
    rec=ModelVersion(sha256=sha,filename=filename[:220],source=source[:240],snapshot=json.dumps(snap,ensure_ascii=False))
    s.add(rec);s.flush()
    s.add(Audit(user_id=user_id,action='Импорт FinModel',entity='model',entity_id=str(rec.id),detail=f'{filename}; SHA256 {sha}'))
    return rec,True
