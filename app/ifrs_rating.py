"""Versioned management scorecard. No dependency on the reference rating XLSX.

Inputs are the private Drive model's saved monthly results. Ratios, scores,
coverage, checks and risk decisions are calculated here, never copied from the
example dashboard. This is not a certification of IFRS compliance.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import time
import urllib.request
from threading import RLock
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from .clock import today
from .db import Company, FinancialStatementSource, Setting, unit
from .financial_statements import read_workbook, number, date_value, col
from .security import session_user, require_permission, perms_of
from .services import log
from .model_import import MAX_SIZE

router = APIRouter()
DRIVE_FILE_ID = '1XCZbSDWBLjf-X5lqvBaLQhvb3R0eRSRr'
METHOD_VERSION = 'zuma-rating-1.0'
SOURCE_URL = f'https://docs.google.com/spreadsheets/d/{DRIVE_FILE_ID}/edit#gid=900803246'
_source_cache = None
_source_lock = RLock()
_source_future = None
_source_executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='rating-drive')


def download_rating_source(force=False):
    """Read the exact user-selected file. No sharing changes or arbitrary URLs.

    The file was verified as already readable by link. Private deployments may
    instead configure the existing read-only service-account connector.
    """
    global _source_cache, _source_future
    with _source_lock:
        if not force and _source_cache and time.monotonic()-_source_cache[0] < 300:
            return _source_cache[1]
        if _source_future is None or _source_future.done():
            _source_future = _source_executor.submit(_fetch_rating_source)
        future = _source_future
    # DNS, redirects and service-account retries must not leave the UI waiting
    # indefinitely. Concurrent callers share one bounded background download.
    try:
        return future.result(timeout=25)
    except (OSError,ValueError,RuntimeError,FutureTimeout) as exc:
        raise RuntimeError('Не удалось обновить источник Google Drive. Проверьте доступ к файлу и повторите попытку.') from exc


def _fetch_rating_source():
    global _source_cache
    if os.getenv('GOOGLE_APPLICATION_CREDENTIALS'):
        from .drive import download_model
        raw,name,origin = download_model(file_id=DRIVE_FILE_ID)
    else:
        url = f'https://drive.google.com/uc?export=download&id={DRIVE_FILE_ID}'
        with urllib.request.urlopen(url,timeout=8) as response:
            raw = response.read(MAX_SIZE+1)
        if len(raw)>MAX_SIZE or not raw.startswith(b'PK'):
            raise ValueError('Google Drive не вернул допустимый файл XLSX.')
        name,origin = 'FinModel Zuma_Pharm.xlsx','Google Drive: '+DRIVE_FILE_ID
    build_rating(raw)
    result = (raw,name,origin,datetime.now(timezone.utc).isoformat())
    with _source_lock:
        _source_cache = (time.monotonic(),result)
    return result

# Descending lower bounds (>=), or ascending upper bounds (<=). All boundaries
# are exposed in the API and methodology. These are management rules, not IFRS.
RULES = [
    ('plan_execution', 'Рост', 'Выполнение плана выручки', '%', 6, 'linear', []),
    ('revenue_trend', 'Рост', 'Рост выручки: последний / первый месяц', '%', 4, 'higher', [(0.2,10),(0.1,8),(0,6),(-0.1,3)]),
    ('gross_margin', 'Рентабельность', 'Валовая маржа', '%', 5, 'higher', [(0.4,10),(0.3,8),(0.2,6),(0.1,3)]),
    ('ebitda_margin', 'Рентабельность', 'Маржа EBITDA модели', '%', 7, 'higher', [(0.25,10),(0.2,8),(0.1,6),(0.05,3)]),
    ('net_margin', 'Рентабельность', 'Чистая маржа', '%', 5, 'higher', [(0.2,10),(0.15,8),(0.1,6),(0.05,3)]),
    ('roa', 'Рентабельность', 'ROA, годовая оценка', '%', 3, 'higher', [(0.2,10),(0.15,8),(0.1,6),(0.05,3)]),
    ('current_ratio', 'Ликвидность', 'Текущая ликвидность', 'x', 5, 'higher', [(2,10),(1.5,8),(1,6),(0.75,3)]),
    ('quick_ratio', 'Ликвидность', 'Быстрая ликвидность', 'x', 5, 'higher', [(1.5,10),(1,8),(0.75,6),(0.5,4),(0.25,2)]),
    ('cash_ratio', 'Ликвидность', 'Абсолютная ликвидность', 'x', 5, 'higher', [(0.3,10),(0.2,8),(0.1,6),(0.05,3)]),
    ('net_debt_ebitda', 'Долговая нагрузка', 'Чистый долг / EBITDA', 'x', 6, 'lower', [(1,10),(2,8),(3,6),(4,3)]),
    ('debt_equity', 'Долговая нагрузка', 'Долг / капитал', 'x', 4, 'lower', [(0.5,10),(1,8),(1.5,6),(2,3)]),
    ('interest_coverage', 'Долговая нагрузка', 'Покрытие процентов', 'x', 5, 'higher', [(6,10),(4,8),(3,6),(1.5,3)]),
    ('ocf_ebitda', 'Денежный поток', 'Поток модели / EBITDA', '%', 10, 'higher', [(1,10),(0.8,8),(0.5,5),(0.2,2)]),
    ('ocf_income', 'Денежный поток', 'Поток модели / чистая прибыль', '%', 5, 'higher', [(1,10),(0.8,8),(0.5,5),(0.2,2)]),
    ('dso', 'Оборотный капитал', 'DSO · погашение дебиторской задолженности', 'days', 2, 'lower', [(30,10),(60,7),(90,4),(120,1)]),
    ('dio', 'Оборотный капитал', 'DIO · оборачиваемость запасов', 'days', 4, 'lower', [(60,10),(90,7),(120,4),(240,1)]),
    ('ccc', 'Оборотный капитал', 'Денежный цикл', 'days', 2, 'lower', [(60,10),(90,7),(120,4),(240,1)]),
    ('inventory_revenue', 'Оборотный капитал', 'Запасы / выручка периода', '%', 2, 'lower', [(0.2,10),(0.35,7),(0.5,4),(0.8,1)]),
    ('model_checks', 'Качество данных', 'Контрольные флаги модели', '%', 4, 'checks', []),
    ('completeness', 'Качество данных', 'Полнота пакета данных', '%', 5, 'linear', []),
    ('debt_mapping', 'Качество данных', 'Сверка обязательств с кредитами', 'flag', 6, 'mapping', []),
]

FORMULAS = {
    'plan_execution':'Revenue / Revenue plan', 'revenue_trend':'Revenue last / Revenue first − 1',
    'gross_margin':'Gross profit / Revenue', 'ebitda_margin':'EBITDA / Revenue',
    'net_margin':'Net income / Revenue', 'roa':'Net income × 12 / months / Assets',
    'current_ratio':'Current assets / Current liabilities',
    'quick_ratio':'(Cash + AR + Other receivables) / Current liabilities',
    'cash_ratio':'Cash / Current liabilities',
    'net_debt_ebitda':'(ST loans + LT loans − Cash − Deposits) / (EBITDA × 12 / months)',
    'debt_equity':'(ST loans + LT loans) / Equity', 'interest_coverage':'EBIT / Interest',
    'ocf_ebitda':'Cash flow proxy / EBITDA', 'ocf_income':'Cash flow proxy / Net income',
    'dso':'AR × days / Revenue', 'dio':'Inventory × days / COGS',
    'ccc':'DSO + DIO − DPO', 'inventory_revenue':'Inventory / Revenue',
    'model_checks':'OK flags / 6 (11_Checks!B6:B11)',
    'completeness':'Available data blocks / 10',
    'debt_mapping':'ABS(Total liabilities − SUM(liability rows 22:31)) ≤ 1',
}

GAPS = [
    ('audit', 'Аудиторское заключение и примечания', 'Подписанная отчётность и раскрытия МСФО.'),
    ('aging', 'Возраст задолженности и запасов', 'Просрочка по дебиторам; партии, сроки годности и неликвиды.'),
    ('debt_schedule', 'Долговые договоры и ковенанты', 'Графики основного долга и процентов, обеспечение и лимиты.'),
    ('production', 'Производство и MRP', 'План / факт выпуска, точность MRP и случаи дефицита сырья.'),
    ('quality', 'QC и GMP', 'Срок выпуска серии, процент брака и критические замечания GMP.'),
    ('engineering', 'Оборудование и обслуживание', 'Простои и исполнение плана профилактического обслуживания.'),
]

PHARMA_KPIS = [
    ('MRP','Точность планирования материалов','≥ 95%'),
    ('MRP','Критический дефицит сырья','0'),
    ('Warehouse','Запасы старше 180 дней','< 10%'),
    ('Warehouse','Оборачиваемость средних запасов','Отраслевой ориентир'),
    ('FG Store','Медленно реализуемая готовая продукция','< 10–15%'),
    ('QC','Срок выпуска серии после контроля','По группе продукции'),
    ('QC','Доля забракованных серий','< 1–2%'),
    ('Engineering','Доля простоев оборудования','< 5%'),
    ('Engineering','Выполнение плана техобслуживания','≥ 95%'),
    ('GMP','Критические замечания GMP','0'),
    ('Production','Выполнение плана производства','≥ 95%'),
    ('Costing','Неблагоприятное отклонение стоимости серии','< 5%'),
]


def score_value(value, direction, bands):
    if value is None:
        return None
    value = Decimal(str(value))
    if direction == 'linear':
        return max(Decimal(0), min(Decimal(10), value * 10))
    if direction == 'checks':
        return max(Decimal(0), min(Decimal(8), value * 8))
    if direction == 'mapping':
        return Decimal(10 if value == 1 else 2)
    for threshold, points in bands:
        threshold = Decimal(str(threshold))
        if (direction == 'higher' and value >= threshold) or (direction == 'lower' and value <= threshold):
            return Decimal(points)
    return Decimal(0)


def grade(score):
    return next((label for minimum, label in [(90,'A+'),(80,'A'),(70,'B+'),(60,'B'),(50,'C'),(0,'D')] if score >= minimum), 'D')


def build_rating(raw, end_month=None, as_of=None):
    as_of = as_of or today()
    sheets, epoch = read_workbook(raw)
    issues, provenance = [], {}
    expected = {'09_Statements': {7:'Revenue',11:'COGS',12:'Gross',16:'EBITDA',18:'EBIT',20:'Interest',23:'Net'},
                '09b_Balance': {8:'Cash',9:'Accounts receivable',13:'Inventory',19:'Total',22:'Accounts payable',
                                26:'Debt',30:'Loans',31:'Loans',35:'Total Liabilities',36:'Equity'}}
    for sheet, labels in expected.items():
        for row, label in labels.items():
            actual = str(sheets[sheet].get(f'D{row}',{}).get('value') or '').strip()
            if not actual.lower().startswith(label.lower()):
                raise ValueError('Структура FinModel изменилась. Проверьте строки отчётности перед расчётом рейтинга.')

    def value(sheet, address):
        cell = sheets.get(sheet, {}).get(address, {})
        n = number(cell.get('value')) if cell.get('type') not in ('e','s','str','inlineStr','b') else None
        if '#REF!' in (cell.get('formula') or '').upper():
            n = None
        return n

    mapping = sheets.get('Mapping', {})
    rows = sorted({int(a[1:]) for a in mapping if a.startswith('A') and a[1:].isdigit() and int(a[1:]) > 1})
    pairs = {(value('Mapping', f'A{r}'), value('Mapping', f'B{r}')) for r in rows if any(value('Mapping', f'{c}{r}') is not None for c in 'FGHIJK')}
    years = {int(y) for y,m in pairs if y is not None and y == int(y) and 2000 <= y <= 2100}
    if len(years) != 1:
        raise ValueError('Для рейтинга нужен один подтверждённый год в Mapping.')
    year = years.pop()
    months = sorted({int(m) for y,m in pairs if y == year and m is not None and m == int(m) and 1 <= m <= 12 and (year,int(m)) < (as_of.year,as_of.month)})
    if not months:
        raise ValueError('Нет закрытых месяцев с фактическими данными Mapping.')
    end_month = end_month or max(months)
    if end_month not in months:
        raise ValueError('Выбранный месяц не закрыт или отсутствует в Mapping.')
    period = list(range(1, end_month + 1))
    missing_months = sorted(set(period) - set(months))
    if missing_months:
        issues.append({'code':'period_gap','severity':'critical','title':'Внутри периода отсутствуют месяцы Mapping.'})
    monthly = []
    inputs = {}
    flows = {'revenue':7,'cogs':11,'gross_profit':12,'ebitda':16,'ebit':18,'interest':20,'net_income':23}
    stocks = {'cash':8,'ar':9,'other_ar':10,'inventory':13,'deposits':17,'assets':19,'ap':22,'st_debt':30,'lt_debt':31,'liabilities':35,'equity':36}
    for month in period:
        pc, fc = col(3 + 2*month), col(4 + 2*month)
        valid = month in months
        for sheet in ('09_Statements','09b_Balance'):
            header = date_value(sheets[sheet].get(pc+'4', {}).get('value'), epoch)
            if not header or (header.year,header.month) != (year,month):
                valid = False
                issues.append({'code':'period_header','severity':'critical','title':'Дата заголовка отчёта не совпадает с Mapping.'})
        row = {'month':month, 'period':f'{year}-{month:02}', 'sources':{}}
        for key, rn in flows.items():
            row[key] = value('09_Statements',fc+str(rn)) if valid else None
            row['sources'][key] = '09_Statements!'+fc+str(rn)
        row['plan'] = value('09_Statements',pc+'7') if valid else None
        row['sources']['plan'] = '09_Statements!'+pc+'7'
        for key, rn in stocks.items():
            row[key] = value('09b_Balance',fc+str(rn)) if valid else None
            row['sources'][key] = '09b_Balance!'+fc+str(rn)
        for key, start, stop in [('current_assets',8,17),('current_liabilities',22,30),('visible_liabilities',22,31)]:
            # Row 26 is the verified Debt section heading, not an input.
            vals = [value('09b_Balance',fc+str(r)) for r in range(start,stop+1) if r != 26]
            if start == 22 and (sheets['09b_Balance'].get(fc+'26',{}).get('value') not in (None,'') or sheets['09b_Balance'].get(fc+'26',{}).get('has_formula')):
                vals.append(None)
            row[key] = sum(vals) if valid and all(v is not None for v in vals) else None
            row['sources'][key] = f'09b_Balance!{fc}{start}:{fc}{stop}'
        # The source CF's calendar row contains a stale year. Month identifiers
        # in row 3 are accepted only with a single Mapping year and full coverage.
        cfcol = col(month+2)
        cfvalid = valid and value('09_CASH_Flow',cfcol+'3') == month
        receipts, payments = value('09_CASH_Flow',cfcol+'8'), value('09_CASH_Flow',cfcol+'16')
        row['ocf'] = receipts-payments if cfvalid and receipts is not None and payments is not None else None
        row['sources']['ocf'] = f'09_CASH_Flow!{cfcol}8 − {cfcol}16'
        monthly.append(row)

    for key in [*flows,'plan','ocf']:
        vals = [r[key] for r in monthly]
        inputs[key] = sum(vals) if all(v is not None for v in vals) else None
        provenance[key] = [r['sources'][key] for r in monthly]
    latest = monthly[-1]
    for key in [*stocks,'current_assets','current_liabilities','visible_liabilities']:
        inputs[key] = latest[key]
        provenance[key] = [latest['sources'][key]]
    days = sum(calendar.monthrange(year,m)[1] for m in period)

    def ratio(a,b):
        return a/b if a is not None and b is not None and b > 0 else None

    def total(*values):
        return sum(values) if all(v is not None for v in values) else None

    i = inputs
    annual_ebitda = i['ebitda'] * 12/end_month if i['ebitda'] is not None else None
    debt = total(i['st_debt'],i['lt_debt'])
    net_debt = total(debt, -i['cash'] if i['cash'] is not None else None, -i['deposits'] if i['deposits'] is not None else None)
    trend = ratio(latest['revenue'],monthly[0]['revenue']) if end_month > 1 else None
    dso, dio, dpo = [ratio(i[key]*days if i[key] is not None else None, i[den]) for key,den in [('ar','revenue'),('inventory','cogs'),('ap','cogs')]]
    checks = [sheets.get('11_Checks',{}).get(f'B{r}',{}).get('value') for r in range(6,12)]
    checks_ratio = Decimal(sum(v == 'OK' for v in checks))/6 if all(v in ('OK','FAIL') for v in checks) else None
    blocks = [{'key':key,'available':all(i[v] is not None for v in fields)} for key,fields in [
        ('pnl',list(flows)),('balance',list(stocks)),('cash_flow',['ocf'])]]
    blocks.append({'key':'checks','available':checks_ratio is not None})
    blocks.extend({'key':key,'available':False,'title':title,'requirement':requirement} for key,title,requirement in GAPS)
    mapping_difference = i['liabilities'] - i['visible_liabilities'] if i['liabilities'] is not None and i['visible_liabilities'] is not None else None
    metrics = {
        'plan_execution':ratio(i['revenue'],i['plan']), 'revenue_trend':trend-1 if trend is not None else None,
        'gross_margin':ratio(i['gross_profit'],i['revenue']), 'ebitda_margin':ratio(i['ebitda'],i['revenue']),
        'net_margin':ratio(i['net_income'],i['revenue']), 'roa':ratio(i['net_income']*12/end_month if i['net_income'] is not None else None,i['assets']),
        'current_ratio':ratio(i['current_assets'],i['current_liabilities']),
        'quick_ratio':ratio(total(i['cash'],i['ar'],i['other_ar']),i['current_liabilities']),
        'cash_ratio':ratio(i['cash'],i['current_liabilities']), 'net_debt_ebitda':ratio(net_debt,annual_ebitda),
        'debt_equity':ratio(debt,i['equity']), 'interest_coverage':ratio(i['ebit'],i['interest']),
        'ocf_ebitda':ratio(i['ocf'],i['ebitda']), 'ocf_income':ratio(i['ocf'],i['net_income']),
        'dso':dso,'dio':dio,'ccc':total(dso,dio,-dpo if dpo is not None else None),
        'inventory_revenue':ratio(i['inventory'],i['revenue']), 'model_checks':checks_ratio,
        'completeness':Decimal(sum(b['available'] for b in blocks))/len(blocks),
        'debt_mapping':None if mapping_difference is None else Decimal(abs(mapping_difference)<=1),
    }
    scorecard = []
    for key, category, title, unit_name, weight, direction, bands in RULES:
        val = metrics[key]
        score = score_value(val,direction,bands)
        # A loss or non-positive equity/EBITDA is adverse, not favourable leverage.
        if key in ('net_debt_ebitda','debt_equity'):
            denominator = annual_ebitda if key == 'net_debt_ebitda' else i['equity']
            if denominator is not None and denominator <= 0 and debt is not None and debt > 0:
                score = Decimal(0)
        scorecard.append({'key':key,'category':category,'title':title,'unit':unit_name,'weight':weight,
                          'value':val,'score':score,'points':score*weight/10 if score is not None else None,
                          'formula':FORMULAS[key],'direction':direction,'bands':bands,
                          'status':'unavailable' if score is None else 'critical' if score < 4 else 'watch' if score < 7 else 'good'})
        if key == 'plan_execution' and val is not None and 0 < val < 1 and score >= 4:
            scorecard[-1]['status'] = 'watch'
    cap = False
    if mapping_difference is not None and abs(mapping_difference)>1:
        issues.append({'code':'debt_mapping','severity':'critical','title':'Итог обязательств не совпадает с суммой строк, включая кредиты.'})
        cap = True
    if i['ocf'] is not None and i['net_income'] is not None and i['ocf'] < 0 < i['net_income']:
        issues.append({'code':'cash_conversion','severity':'critical','title':'Положительная прибыль сопровождается отрицательным денежным потоком модели.'})
        cap = True
    if metrics['cash_ratio'] is not None and metrics['cash_ratio'] < Decimal('.05'):
        issues.append({'code':'cash_shortage','severity':'critical','title':'Деньги покрывают менее 5% текущих обязательств.'})
    if metrics['plan_execution'] is not None and metrics['plan_execution'] < Decimal('.9'):
        issues.append({'code':'plan_gap','severity':'warning','title':'Выручка ниже плана более чем на 10%.'})
    if dio is not None and dio>120:
        issues.append({'code':'inventory','severity':'warning','title':'Запасы превышают 120 дней себестоимости.'})
    if i['ebitda'] is not None and i['ebitda'] == i['ebit']:
        issues.append({'code':'ebitda','severity':'warning','title':'В модели EBITDA равна EBIT; распределение амортизации требует проверки.'})
    issues.append({'code':'cash_proxy','severity':'warning','title':'Поток модели — приближение: включает строки инвестиционных и финансовых поступлений. Требуется классификация OCF.'})
    coverage = sum(r['weight'] for r in scorecard if r['score'] is not None)
    points = sum(r['points'] for r in scorecard if r['points'] is not None)
    complete = coverage == 100 and not missing_months and not any(x['code']=='period_header' for x in issues)
    total_score = points if complete else None
    final_grade = grade(total_score) if total_score is not None else None
    if final_grade in ('A+','A','B+','B') and cap:
        final_grade = 'C'
    categories = []
    for category in dict.fromkeys(r[1] for r in RULES):
        group = [r for r in scorecard if r['category']==category]
        weight = sum(r['weight'] for r in group)
        categories.append({'title':category,'weight':weight,'points':sum(r['points'] for r in group if r['points'] is not None),
                           'complete':all(r['score'] is not None for r in group)})
    result = {'method_version':METHOD_VERSION,'year':year,'end_month':end_month,'available_months':months,
              'period':{'start':f'{year}-01-01','end':f'{year}-{end_month:02}-{calendar.monthrange(year,end_month)[1]}','months':end_month,'days':days},
              'score':total_score,'known_points':points,'grade':final_grade,'raw_grade':grade(total_score) if complete else None,
              'capped':complete and final_grade!=grade(total_score),'coverage_weight':coverage,'status':'provisional' if complete else 'incomplete',
              'inputs':inputs,'provenance':provenance,'monthly':monthly,'scorecard':scorecard,'categories':categories,
              'issues':issues,'data_blocks':blocks,'pharma_kpis':[{'module':module,'title':title,'target':target,'value':None} for module,title,target in PHARMA_KPIS],
              'mapping_difference':mapping_difference,'dpo':dpo}
    # Decimal throughout calculations; serialization never emits NaN/Infinity.
    return json.loads(json.dumps(result,default=lambda v:format(v,'f') if isinstance(v,Decimal) else v))


def binding_key(cid):
    return f'ifrs_rating:{cid}'


def authorize(s,request,write=False):
    user,_ = session_user(s,request,'export')
    if write:
        require_permission(user,'import')
    company = s.get(Company,s.info['company_id'])
    if company.code != 'ZUMA':
        raise HTTPException(404,'Источник IFRS Rating подключён только для компании Zuma.')
    return user,company.id


def save_drive_source(s,user,cid,raw,filename,origin):
    # Validate before storing. No transactions, plans, or source model changes.
    build_rating(raw)
    digest = hashlib.sha256(raw).hexdigest()
    source = s.scalar(select(FinancialStatementSource).where(FinancialStatementSource.sha256==digest))
    if source is None:
        source = FinancialStatementSource(company_id=cid,created_by=user.id,filename=filename,sha256=digest,
                                          size=len(raw),content=raw,snapshot='{}')
        s.add(source)
        s.flush()
    binding = {'source_id':source.id,'file_id':DRIVE_FILE_ID,'synced_at':datetime.now(timezone.utc).isoformat(),'origin':origin}
    setting = s.get(Setting,binding_key(cid))
    if setting is None:
        s.add(Setting(key=binding_key(cid),value=json.dumps(binding)))
    else:
        setting.value = json.dumps(binding)
    log(s,user,'Обновлён источник IFRS Rating','financial_statement_source',source.id,filename)
    return binding


@router.get('/api/ifrs-rating')
def get_rating(request:Request,end_month:int|None=Query(default=None,ge=1,le=12),company_id:int|None=Query(default=None,ge=1)):
    with unit() as s:
        user,cid = authorize(s,request)
        setting = s.get(Setting,binding_key(cid))
        binding = json.loads(setting.value) if setting else None
        source = s.get(FinancialStatementSource,binding['source_id']) if binding else None
        base = {'company_id':cid,'source_url':SOURCE_URL if 'download' in perms_of(user) else None,
                'can_sync':'import' in perms_of(user),'binding':binding}
        saved = (source.content,source.filename,source.sha256) if source else None
    try:
        raw,name,origin,synced_at = download_rating_source()
        digest = hashlib.sha256(raw).hexdigest()
        base['binding'] = {'synced_at':synced_at,'file_id':DRIVE_FILE_ID,'origin':origin}
        base['stale'] = False
    except RuntimeError as exc:
        if saved is None:
            with _source_lock:
                cached = _source_cache[1] if _source_cache else None
            if cached is None:
                raise HTTPException(503,str(exc)) from exc
            raw,name,origin,synced_at = cached
            digest = hashlib.sha256(raw).hexdigest()
            base['binding'] = {'synced_at':synced_at,'file_id':DRIVE_FILE_ID,'origin':origin}
        else:
            raw,name,digest = saved
        base.update(stale=True,warning=str(exc))
    try:
        report = build_rating(raw,end_month)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    return {**base,'report':report,'source':{'filename':name,'sha256':digest}}


@router.post('/api/ifrs-rating/sync')
def sync_rating(request:Request,company_id:int|None=Query(default=None,ge=1)):
    with unit() as s:
        _,cid = authorize(s,request,True)
    try:
        raw,name,origin,_ = download_rating_source(force=True)
        with unit(True) as s:
            user,current_cid = authorize(s,request,True)
            if current_cid != cid:
                raise HTTPException(409,'Компания запроса изменилась. Обновите раздел.')
            binding = save_drive_source(s,user,cid,raw,name,origin)
        return {'company_id':cid,'binding':binding}
    except (ValueError,RuntimeError) as exc:
        raise HTTPException(422,str(exc)) from exc
