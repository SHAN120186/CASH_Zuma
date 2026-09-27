"""Тесты используют отдельную временную БД. Тестовые пароли НЕ создают вход в рабочую систему."""
import os,sys,tempfile,unittest,json
from pathlib import Path
from datetime import timedelta
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
TMP=tempfile.TemporaryDirectory()
os.environ['DATA_DIR']=TMP.name
os.environ['ALLOWED_HOSTS']='testserver,localhost,127.0.0.1'
os.environ['COOKIE_SECURE']='0'
os.environ['PUBLIC_ORIGIN']=''
os.environ.pop('DATABASE_URL',None)
if os.getenv('TEST_DATABASE_URL'):
    from sqlalchemy.engine import make_url
    test_url=make_url(os.environ['TEST_DATABASE_URL'])
    if not (test_url.database or '').startswith('zuma_test_'):raise RuntimeError('Test database must start with zuma_test_')
    os.environ['DATABASE_URL']=os.environ['TEST_DATABASE_URL']
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import *
from app.services import today
from app.security import hash_password
PASSWORD='OnlyForTemporaryTests_9841!'
HASH=hash_password(PASSWORD)

def tearDownModule():
    engine.dispose()
    TMP.cleanup()

class SiteTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine);initialize()
        with unit(True) as s:
            admin=User(username='admin',name='Test Admin',role='admin',password_hash=HASH);s.add(admin);s.flush()
            # Администратор холдинга работает с финансами только по отдельному назначению в компании.
            for c in s.scalars(select(Company).where(Company.code!='UNASSIGNED')):s.add(CompanyUser(company_id=c.id,user_id=admin.id,role='finance'))
        self.client=TestClient(app)
        r=self.client.post('/api/login',json={'username':'admin','password':PASSWORD});self.assertEqual(r.status_code,200,r.text)
        self.h={'X-CSRF-Token':r.json()['csrf']}
        self.date=str(today());self.month=self.date[:7]
        b=self.client.get('/api/bootstrap').json();self.cat=b['categories'][1]['id'];self.company=next(c['id'] for c in b['companies'] if c['code']=='UZGERMED')
        r=self.post('/api/accounts',{'name':'Test bank','kind':'bank','currency':'UZS','opening':'1000000.00','opening_date':str(today()-timedelta(days=10))})
        self.acc=r.json()['id']
    def tearDown(self):
        for name in ('approver','director','payer'):
            if hasattr(self,name):getattr(self,name)[0].close()
        self.client.close()
    def post(self,path,data,client=None,headers=None):
        data=dict(data)
        if path=='/api/requests' and data.get('status','pending')=='pending':return self.documented_request(data,client,headers)
        rid=int(path.split('/')[3]) if path.startswith('/api/requests/') and path.endswith('/decision') else data.get('request_id') if path=='/api/ledger' else None
        key='version' if path.endswith('/decision') else 'request_version'
        if rid and key not in data:
            row=next((r for r in self.client.get('/api/requests').json() if r['id']==rid),None)
            if row:data[key]=row['version']
        return (client or self.client).post(path,json=data,headers=headers or self.h)
    def budget(self,limit='1000',mode='soft'):
        r=self.post('/api/budgets',{'category_id':self.cat,'month':self.month,'currency':'UZS','amount':limit,'mode':mode,'reason':'Подтверждено для тестирования','source':'test'})
        self.assertEqual(r.status_code,200,r.text)
    def documented_request(self,data,client=None,headers=None):
        cl=client or self.client;h=headers or self.h;payload=dict(data);desired=payload.pop('status','pending');payload['status']='draft'
        created=cl.post('/api/requests',json=payload,headers=h)
        if created.status_code!=200:return created
        rid=created.json()['id'];raw=b'%PDF-1.4\nsynthetic request document'
        for kind,name in [('internal','request.pdf'),('contract','contract.pdf')]:
            uploaded=cl.post(f'/api/requests/{rid}/documents',params={'kind':kind},content=raw,headers={**h,'Content-Type':'application/pdf','X-Filename':name})
            if uploaded.status_code!=200:return uploaded
        return self.post(f'/api/requests/{rid}/decision',{'action':'submit','version':created.json()['version']},client,headers) if desired=='pending' else created
    def request(self,n='600',purpose='Тестовая заявка на оплату',client=None,headers=None):
        return self.post('/api/requests',{'account_id':self.acc,'category_id':self.cat,'counterparty':'Supplier','amount':n,'date':self.date,'purpose':purpose},client,headers)
    def finance_approve(self,id,note='Подтверждаю необходимость расхода'):
        if not hasattr(self,'approver'):self.approver=self.make_user('finance','reviewer')
        return self.post(f'/api/requests/{id}/decision',{'action':'approve','note':note},*self.approver)
    def approve(self,id,note='Подтверждаю необходимость расхода'):
        """Финансист, затем директор: директор утверждает каждую заявку (Q03)."""
        row=next((x for x in self.client.get('/api/requests').json() if x['id']==id),None)
        if not row or row['approval_stage']!='director':
            r=self.finance_approve(id,note)
            if r.status_code!=200:return r
        if not hasattr(self,'director'):self.director=self.make_user('director','chief')
        return self.post(f'/api/requests/{id}/decision',{'action':'approve','note':note},*self.director)
    def ledger(self,amount='100',kind='in',reference='DOC1',client=None,**extra):
        data={'account_id':self.acc,'category_id':self.cat,'amount':amount,'kind':kind,'date':self.date,'reference':reference,'note':'Подтверждённая тестовая операция'};data.update(extra)
        if client is None and data.get('request_id'):
            # Оплату заявки проводит расчётный бухгалтер, а не автор или согласующий.
            if not hasattr(self,'payer'):self.payer=self.make_user('accountant','payer')
            client=self.payer
        return self.post('/api/ledger',data,*(client or ()))
    def make_user(self,role='employee',name='worker'):
        r=self.post('/api/users',{'username':name,'name':name,'password':PASSWORD,'role':role});self.assertEqual(r.status_code,200,r.text)
        cl=TestClient(app);r=cl.post('/api/login',json={'username':name,'password':PASSWORD});self.assertEqual(r.status_code,200,r.text)
        return cl,{'X-CSRF-Token':r.json()['csrf']}
    def test_release_metadata_is_public_and_never_cached(self):
        with TestClient(app) as anonymous:
            r=anonymous.get('/version.json')
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.headers['cache-control'],'no-store')
            self.assertRegex(r.json()['version'],r'^\d+\.\d+\.\d+$')
            self.assertTrue(r.json()['changes'])
            self.assertEqual(anonymous.get('/api/bootstrap').status_code,401)

    def test_01_login_and_protected_api(self):
        c=TestClient(app)
        self.assertEqual(c.get('/').status_code,200)
        self.assertEqual(c.get('/api/dashboard').status_code,401)
        self.assertEqual(c.post('/api/login',json={'username':'missing','password':'bad'}).status_code,401)
        self.assertEqual(c.get('/data/cashflow.sqlite3').status_code,404)
        self.assertEqual(c.get('/source/FinModel_2026_Zuma_Pharm_v8.xlsx').status_code,404)
        self.assertEqual(self.client.get('/api/me').status_code,200)
    def test_02_csrf_and_cross_origin(self):
        p={'name':'Second bank','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date}
        self.assertEqual(self.client.post('/api/accounts',json=p).status_code,403)
        self.assertEqual(self.client.post('/api/accounts',json=p,headers={**self.h,'Origin':'https://malicious.example'}).status_code,403)
    def test_03_soft_budget_needs_reason(self):
        self.budget()
        r=self.request();self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.approve(r.json()['id']).status_code,200)
        self.assertEqual(self.request('600','short').status_code,409)
        r=self.request('600');self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.approve(r.json()['id'],note='').status_code,409)
        self.assertEqual(self.approve(r.json()['id']).status_code,200)
    def test_04_hard_budget_blocks(self):
        self.budget(mode='hard');r=self.request('600');self.approve(r.json()['id'])
        self.assertEqual(self.request('500').status_code,409)
        self.assertEqual(self.ledger('500','out').status_code,409)
    def test_05_payment_no_double_count(self):
        self.budget();r=self.request('600');id=r.json()['id'];self.approve(id)
        d=self.client.get('/api/dashboard').json();self.assertEqual(d['outgoing7'],'600.00')
        r=self.ledger('600','out',request_id=id);self.assertEqual(r.status_code,200,r.text)
        d=self.client.get('/api/dashboard').json();self.assertEqual(d['outgoing7'],'0.00');self.assertEqual(d['balance'],'999400.00')
        b=self.client.get('/api/budgets').json();b=next(b for b in b if b['category_id']==self.cat);self.assertEqual(b['spent'],'600.00');self.assertEqual(b['reserved'],'0.00')
        self.assertEqual(self.ledger('600','out','DOC2',request_id=id).status_code,409)
    def test_06_duplicate_reference_rollback(self):
        self.assertEqual(self.ledger().status_code,200)
        self.assertEqual(self.ledger().status_code,409)
        self.assertEqual(self.client.get('/api/dashboard').json()['balance'],'1000100.00')
    def test_07_transfer_excluded_from_cashflow(self):
        r=self.post('/api/accounts',{'name':'Cash','kind':'cash','currency':'UZS','opening':'0','opening_date':self.date});target=r.json()['id']
        r=self.ledger('250','transfer',to_account_id=target);self.assertEqual(r.status_code,200,r.text)
        d=self.client.get('/api/dashboard').json();self.assertEqual(d['balance'],'1000000.00');self.assertEqual(d['fact_in'],'0.00');self.assertEqual(d['fact_out'],'0.00')
    def test_08_receipt_forecast_and_fact(self):
        r=self.post('/api/receipts',{'account_id':self.acc,'category_id':self.cat,'counterparty':'Buyer','amount':'250','date':self.date});id=r.json()['id']
        self.assertEqual(self.client.get('/api/dashboard').json()['incoming7'],'250.00')
        r=self.ledger('250','in',receipt_id=id);self.assertEqual(r.status_code,200,r.text)
        d=self.client.get('/api/dashboard').json();self.assertEqual(d['incoming7'],'0.00');self.assertEqual(d['balance'],'1000250.00')
    def test_09_employee_permissions_and_privacy(self):
        self.request('50')
        cl,h=self.make_user();r=self.request('100',client=cl,headers=h);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(cl.get('/api/dashboard').status_code,403);self.assertEqual(cl.get('/api/model').status_code,403)
        self.assertEqual(cl.get('/api/users').status_code,403);self.assertEqual(cl.get('/api/budgets').status_code,403)
        rs=cl.get('/api/requests').json();self.assertEqual(len(rs),1);self.assertTrue(rs[0]['budget']['hidden']);self.assertNotIn('spent',rs[0]['budget'])
        self.assertEqual(self.post(f"/api/requests/{r.json()['id']}/decision",{'action':'approve'},cl,h).status_code,403)
    def test_10_director_cannot_approve_own(self):
        cl,h=self.make_user('director','boss')
        own=self.request('100',client=cl,headers=h)
        self.assertEqual(own.status_code,200,own.text)
        self.assertEqual(self.post(f"/api/requests/{own.json()['id']}/decision",{'action':'approve'},cl,h).status_code,403)
        r=self.request('100')
        self.assertEqual(self.post(f"/api/requests/{r.json()['id']}/decision",{'action':'approve'},cl,h).status_code,403)
        self.assertEqual(cl.get('/api/dashboard').status_code,200)
        cl.close()
    def test_11_dates_negative_and_insufficient(self):
        self.assertEqual(self.ledger('1','out',date=str(today()+timedelta(days=1))).status_code,422)
        self.assertEqual(self.ledger('-1').status_code,422)
        self.assertEqual(self.ledger('NaN').status_code,422)
        self.assertEqual(self.ledger('1.001').status_code,422)
        self.assertEqual(self.ledger('1000001','out').status_code,409)
        self.assertEqual(self.client.get('/api/dashboard').json()['balance'],'1000000.00')
    def test_12_currency_separation(self):
        r=self.post('/api/accounts',{'name':'USD bank','kind':'bank','currency':'USD','opening':'100','opening_date':self.date});usd=r.json()['id']
        self.assertEqual(self.client.get('/api/dashboard?currency=UZS').json()['balance'],'1000000.00')
        self.assertEqual(self.client.get('/api/dashboard?currency=USD').json()['balance'],'100.00')
        self.assertEqual(self.ledger('10','transfer',to_account_id=usd).status_code,422)
    def test_13_storno_reopens_request(self):
        r=self.request('100');rid=r.json()['id'];self.approve(rid)
        r=self.ledger('100','out',request_id=rid);tid=r.json()['id']
        r=self.post(f'/api/ledger/{tid}/reverse',{'reason':'Исправление ошибочной операции'});self.assertEqual(r.status_code,200,r.text)
        d=self.client.get('/api/dashboard').json();self.assertEqual(d['balance'],'1000000.00');self.assertEqual(d['outgoing7'],'100.00')
        self.assertEqual(self.post(f'/api/ledger/{tid}/reverse',{'reason':'Повторная ошибочная операция'}).status_code,409)
    def test_14_excel_snapshot_isolated_and_idempotent(self):
        # Self-contained synthetic workbook; CI must never require financial source data.
        import io
        from datetime import date
        from openpyxl import Workbook
        book=Workbook();book.active.title='09_Statements'
        cash=book.create_sheet('09_CASH_Flow');balance=book.create_sheet('09b_Balance')
        cash['C4']=date(2023,1,1);cash['B54']='Test closing balance';cash['I54']=12500
        cash['D54']='=I54+1'
        balance['D8']='Test cash';balance['R8']=13500
        out=io.BytesIO();book.save(out);book.close();raw=out.getvalue()
        headers={**self.h,'Content-Type':'application/octet-stream','X-Filename':'FinModel.xlsx'}
        r=self.client.post('/api/model/upload',content=raw,headers=headers);self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['created'])
        r2=self.client.post('/api/model/upload',content=raw,headers=headers);self.assertFalse(r2.json()['created'])
        m=self.client.get('/api/model').json();self.assertEqual(len(m['versions']),1)
        self.assertEqual(m['snapshot']['summary']['cash_close_july']['value'],'12500')
        self.assertEqual(m['snapshot']['summary']['balance_cash_july']['value'],'13500')
        self.assertIn('09_CASH_Flow!D54',m['snapshot']['missing_cache'])
        self.assertTrue(any('2023' in x for x in m['snapshot']['warnings']))
        self.assertEqual(self.client.get('/api/dashboard').json()['balance'],'1000000.00')
    def test_15_revoked_session_and_logout(self):
        cl,h=self.make_user();uid=cl.get('/api/me').json()['user']['id']
        self.post(f'/api/users/{uid}',{'role':'employee','active':False})
        self.assertEqual(cl.get('/api/me').status_code,401)
        r=self.post('/api/logout',{});self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.get('/api/me').status_code,401)
    def test_16_login_rate_limit(self):
        cl=TestClient(app)
        for i in range(8):self.assertEqual(cl.post('/api/login',json={'username':'missing','password':'bad'}).status_code,401)
        self.assertEqual(cl.post('/api/login',json={'username':'missing','password':'bad'}).status_code,429)
    def test_17_reschedule_removes_approval(self):
        r=self.request('100');id=r.json()['id'];self.approve(id)
        r=self.post(f'/api/requests/{id}/decision',{'action':'reschedule','date':str(today()+timedelta(days=1)),'note':'Перенос по просьбе контрагента'})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['status'],'pending')
        self.assertEqual(self.client.get('/api/dashboard').json()['outgoing7'],'0.00')
    def test_18_exact_minor_units_and_csv(self):
        self.ledger('0.01',reference='CENT1');self.ledger('0.02',reference='CENT2')
        self.assertEqual(self.client.get('/api/dashboard').json()['balance'],'1000000.03')
        self.assertEqual(self.client.get('/api/export/ledger.csv').status_code,200)
    def test_19_reversed_expense_not_income(self):
        tid=self.ledger('100','out').json()['id']
        self.assertEqual(self.post(f'/api/ledger/{tid}/reverse',{'reason':'Исправление ошибочного расхода'}).status_code,200)
        d=self.client.get('/api/dashboard').json()
        self.assertEqual(d['fact_in'],'0.00')
        self.assertEqual(d['fact_out'],'0.00')
        b=next(b for b in self.client.get('/api/budgets').json() if b['category_id']==self.cat)
        self.assertEqual(b['spent'],'0.00')

    def test_20_reversed_income_not_budget_expense(self):
        self.budget('0','hard')
        tid=self.ledger('100','in').json()['id']
        self.assertEqual(self.post(f'/api/ledger/{tid}/reverse',{'reason':'Исправление ошибочного прихода'}).status_code,200)
        b=next(b for b in self.client.get('/api/budgets').json() if b['category_id']==self.cat)
        self.assertEqual(b['spent'],'0.00')
        self.assertFalse(b['over'])
        d=self.client.get('/api/dashboard').json()
        self.assertEqual((d['fact_in'],d['fact_out']),('0.00','0.00'))
        report=self.client.get(f'/api/report?year={today().year}').json()
        self.assertTrue(all(v=='0.00' for v in report['totals']))

    def test_21_forecast_detects_shortfall_on_individual_account(self):
        second=self.post('/api/accounts',{'name':'Empty bank','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date}).json()['id']
        r=self.documented_request({'account_id':second,'category_id':self.cat,'counterparty':'Supplier','amount':'100','date':self.date,'purpose':'Оплата с отдельного пустого счёта'})
        denied=self.approve(r.json()['id']);self.assertEqual(denied.status_code,409)
        self.assertIn('Недостаточно доступных средств',denied.text)
        day=self.client.get('/api/dashboard').json()['forecast'][0]
        self.assertEqual(day['balance'],'1000000.00');self.assertFalse(day['risk'])
        self.assertTrue(day['requested_risk']);self.assertEqual(day['requested_account_shortfalls'][0]['account_id'],second)

    def test_22_backdated_expense_checks_later_daily_balances(self):
        self.assertEqual(self.ledger('999950','out',reference='SPEND').status_code,200)
        self.assertEqual(self.ledger('100','out',reference='EARLIER',date=str(today()-timedelta(days=1))).status_code,409)
        self.assertEqual(self.client.get('/api/dashboard').json()['balance'],'50.00')

    def test_23_payment_in_another_month_moves_budget(self):
        next_month=(today().replace(day=28)+timedelta(days=4)).replace(day=1)
        r=self.documented_request({'account_id':self.acc,'category_id':self.cat,'counterparty':'Supplier','amount':'100','date':str(next_month),'purpose':'Ранняя оплата заявки следующего месяца'})
        rid=r.json()['id'];self.assertEqual(self.approve(rid).status_code,200)
        self.assertEqual(self.ledger('100','out',request_id=rid).status_code,200)
        current=next(b for b in self.client.get('/api/budgets').json() if b['category_id']==self.cat)
        future=next(b for b in self.client.get(f'/api/budgets?month={str(next_month)[:7]}').json() if b['category_id']==self.cat)
        self.assertEqual(current['spent'],'100.00')
        self.assertEqual(future['reserved'],'0.00')

    def edit_account(self,**changes):
        data={'name':'Corrected bank','kind':'bank','currency':'UZS','opening':'1000000','opening_date':str(today()-timedelta(days=10)),'allow_overdraft':False,'reason':'Исправление ошибочного ввода'}
        data.update(changes)
        return self.post(f'/api/accounts/{self.acc}',data)

    def test_24_edit_opening_recalculates_without_turnover(self):
        self.ledger('100','out')
        r=self.edit_account(opening='200')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['balance'],'100.00')
        d=self.client.get('/api/dashboard').json()
        self.assertEqual((d['balance'],d['fact_out']),('100.00','100.00'))
        with unit() as s:
            audit=s.scalar(select(Audit).where(Audit.action=='Изменён счёт / начальный остаток'))
            detail=json.loads(audit.detail)
            self.assertEqual(detail['before']['opening'],'1000000.00')
            self.assertEqual(detail['after']['opening'],'200.00')

    def test_25_edit_invalid_balance_rolls_back(self):
        self.ledger('100','out')
        self.assertEqual(self.edit_account(opening='50').status_code,409)
        a=self.client.get('/api/accounts').json()[0]
        self.assertEqual(a['opening'],'1000000.00')
        self.assertEqual(a['name'],'Test bank')

    def test_26_edit_currency_and_date_protect_history(self):
        self.ledger('100','in',date=str(today()-timedelta(days=1)))
        self.assertEqual(self.edit_account(currency='USD').status_code,409)
        self.assertEqual(self.edit_account(opening_date=self.date).status_code,409)
        self.assertEqual(self.edit_account(kind='cash',allow_overdraft=True).status_code,422)
        self.assertEqual(self.edit_account(reason='short').status_code,422)

    def test_27_edit_unused_currency_and_pending_currency_guard(self):
        self.assertEqual(self.edit_account(currency='USD').status_code,200)
        self.request('100')
        self.assertEqual(self.edit_account(currency='UZS').status_code,409)

    def test_28_employee_cannot_edit_account(self):
        cl,h=self.make_user()
        data={'name':'Wrong','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date,'reason':'Попытка изменения сотрудником'}
        self.assertEqual(self.post(f'/api/accounts/{self.acc}',data,cl,h).status_code,403)
        cl.close()

    def import_preview(self,rows):
        import csv,io
        from app.erp import HEADERS
        out=io.StringIO();writer=csv.writer(out,delimiter=';');writer.writerow(HEADERS)
        for row in rows:writer.writerow([row.get(k,'') for k in HEADERS])
        return self.client.post('/api/import/preview',content=out.getvalue().encode(),headers={**self.h,'X-Filename':'operations.csv','Content-Type':'application/octet-stream'})

    def import_row(self,**extra):
        row={'date':self.date,'kind':'out','account_id':self.acc,'category_id':self.cat,'amount':'100','reference':'IMPORT-1','counterparty':'Supplier','note':'Оплата подтверждена выпиской'}
        row.update(extra);return row

    def test_29_import_preview_no_write_commit_once(self):
        r=self.import_preview([self.import_row()]);self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()['can_commit'])
        self.assertEqual(self.client.get('/api/dashboard').json()['balance'],'1000000.00')
        id=r.json()['id'];self.assertEqual(self.post(f'/api/import/{id}/commit',{}).status_code,200)
        self.assertEqual(self.client.get('/api/dashboard').json()['balance'],'999900.00')
        self.assertEqual(self.post(f'/api/import/{id}/commit',{}).status_code,409)

    def test_30_import_duplicate_and_running_balance(self):
        r=self.import_preview([self.import_row(),self.import_row()]);self.assertFalse(r.json()['can_commit'])
        self.assertTrue(r.json()['errors'])
        self.assertEqual(self.post(f"/api/import/{r.json()['id']}/commit",{}).status_code,409)
        r=self.import_preview([self.import_row(amount='900000'),self.import_row(reference='IMPORT-2',amount='200000')])
        self.assertFalse(r.json()['can_commit'])
        self.assertEqual(self.client.get('/api/dashboard').json()['balance'],'1000000.00')

    def test_31_import_revalidates_at_commit_and_rolls_back(self):
        r=self.import_preview([self.import_row(reference='FIRST'),self.import_row(reference='CONFLICT')])
        self.ledger('1','in',reference='CONFLICT')
        self.assertEqual(self.post(f"/api/import/{r.json()['id']}/commit",{}).status_code,409)
        self.assertEqual(self.client.get('/api/dashboard').json()['balance'],'1000001.00')
        self.assertEqual(len(self.client.get('/api/ledger').json()),1)

    def test_32_accountant_read_only_and_director_exports(self):
        cl,h=self.make_user('accountant','bookkeeper')
        self.assertEqual(cl.get('/api/ledger').status_code,200)
        self.assertEqual(cl.get('/api/dashboard').status_code,403)
        self.assertEqual(cl.get('/api/export/report.xlsx').status_code,403)
        self.assertEqual(self.request(client=cl,headers=h).status_code,403)
        self.assertEqual(self.post('/api/accounts',{'name':'Denied','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date},cl,h).status_code,403)
        cl.close()
        cl,h=self.make_user('director','reader')
        self.assertEqual(cl.get('/api/export/report.xlsx').status_code,200)
        self.assertEqual(cl.get('/api/audit').status_code,200);cl.close()

    def test_33_jwt_bearer_tamper_expiry_and_revocation(self):
        import jwt
        from app.security import jwt_key
        r=self.client.post('/api/login',json={'username':'admin','password':PASSWORD})
        token=r.json()['access_token'];claims=jwt.decode(token,jwt_key(),algorithms=['HS256'],audience='zuma-api',issuer='zuma-treasury')
        self.assertEqual(claims['sub'],'1')
        c=TestClient(app);self.assertEqual(c.get('/api/me',headers={'Authorization':'Bearer '+token}).status_code,200)
        broken=token[:-10]+'AAAAAAAAAA'
        self.assertEqual(c.get('/api/me',headers={'Authorization':'Bearer '+broken}).status_code,401)
        claims['exp']=1;expired=jwt.encode(claims,jwt_key(),algorithm='HS256')
        self.assertEqual(c.get('/api/me',headers={'Authorization':'Bearer '+expired}).status_code,401)
        self.assertEqual(c.post('/api/logout',json={},headers={'Authorization':'Bearer '+token}).status_code,200)
        self.assertEqual(c.get('/api/me',headers={'Authorization':'Bearer '+token}).status_code,401);c.close()

    def test_34_document_protected_and_role_checked(self):
        id=self.ledger().json()['id'];raw=b'%PDF-1.4\n% test document'
        r=self.client.post(f'/api/ledger/{id}/document',content=raw,headers={**self.h,'X-Filename':'receipt.pdf','Content-Type':'application/octet-stream'})
        self.assertEqual(r.status_code,200,r.text);url=r.json()['url']
        c=TestClient(app);self.assertEqual(c.get(url).status_code,401);c.close()
        cl,h=self.make_user('accountant','docsreader');self.assertEqual(cl.get(url).content,raw)
        self.assertEqual(cl.post(f'/api/ledger/{id}/document',content=raw,headers={**h,'X-Filename':'receipt.pdf'}).status_code,403);cl.close()
        r=self.client.post(f'/api/ledger/{id}/document',content=b'<script>bad</script>',headers={**self.h,'X-Filename':'bad.html'})
        self.assertEqual(r.status_code,422)

    def test_34b_request_documents_required_versioned_and_protected(self):
        payload={'account_id':self.acc,'category_id':self.cat,'counterparty':'Supplier','amount':'100','date':self.date,'purpose':'Заявка с обязательными документами'}
        self.assertEqual(self.client.post('/api/requests',json=payload,headers=self.h).status_code,422)
        created=self.client.post('/api/requests',json={**payload,'status':'draft'},headers=self.h);self.assertEqual(created.status_code,200,created.text)
        rid=created.json()['id']
        missing=self.post(f'/api/requests/{rid}/decision',{'action':'submit'});self.assertEqual(missing.status_code,409);self.assertIn('Внутренняя',missing.text)
        bad=self.client.post(f'/api/requests/{rid}/documents',params={'kind':'internal'},content=b'<script>bad</script>',headers={**self.h,'X-Filename':'bad.html'})
        self.assertEqual(bad.status_code,422)
        raw=b'%PDF-1.4\nrequest document'
        first=self.client.post(f'/api/requests/{rid}/documents',params={'kind':'internal'},content=raw,headers={**self.h,'X-Filename':'request-v1.pdf'});self.assertEqual(first.status_code,200,first.text)
        second=self.client.post(f'/api/requests/{rid}/documents',params={'kind':'internal'},content=raw+b' v2',headers={**self.h,'X-Filename':'request-v2.pdf'});self.assertEqual(second.status_code,200,second.text);self.assertEqual(second.json()['version'],2)
        contract=self.client.post(f'/api/requests/{rid}/documents',params={'kind':'contract'},content=raw,headers={**self.h,'X-Filename':'contract.pdf'});self.assertEqual(contract.status_code,200,contract.text)
        for name in ('other-1.pdf','other-2.pdf'):
            self.assertEqual(self.client.post(f'/api/requests/{rid}/documents',params={'kind':'other'},content=raw,headers={**self.h,'X-Filename':name}).status_code,200)
        docs=self.client.get(f'/api/requests/{rid}/documents').json();self.assertEqual(len(docs),4);self.assertEqual(len([d for d in docs if d['kind']=='internal']),1)
        with TestClient(app) as anonymous:self.assertEqual(anonymous.get(docs[0]['url']).status_code,401)
        internal=next(d for d in docs if d['kind']=='internal');self.assertEqual(self.client.get(internal['url']).content,raw+b' v2')
        submitted=self.post(f'/api/requests/{rid}/decision',{'action':'submit'});self.assertEqual(submitted.status_code,200,submitted.text)
        denied=self.client.post(f'/api/requests/{rid}/documents',params={'kind':'other'},content=raw,headers={**self.h,'X-Filename':'late.pdf'});self.assertEqual(denied.status_code,409)

    def test_35_export_xlsx_pdf_and_formula_safety(self):
        from openpyxl import load_workbook
        import io
        with unit(True) as s:s.get(Category,self.cat).name='=1+1'
        self.ledger('100','out')
        r=self.client.get(f'/api/export/report.xlsx?year={today().year}')
        self.assertEqual(r.status_code,200,r.text[:200])
        wb=load_workbook(io.BytesIO(r.content));row=next(r for r in wb.active if r[0].value=='Выплата · =1+1');self.assertEqual(row[0].data_type,'s');self.assertEqual(row[today().month].value,-100);wb.close()
        r=self.client.get(f'/api/export/report.pdf?year={today().year}')
        self.assertEqual(r.status_code,200);self.assertTrue(r.content.startswith(b'%PDF-'))

    def test_36_xlsx_formula_rejected_and_role_reference(self):
        import io
        from openpyxl import Workbook
        from app.erp import HEADERS
        wb=Workbook();wb.active.append(HEADERS);row=self.import_row();row['amount']='=10+1';wb.active.append([row.get(k,'') for k in HEADERS]);out=io.BytesIO();wb.save(out);wb.close()
        r=self.client.post('/api/import/preview',content=out.getvalue(),headers={**self.h,'X-Filename':'operations.xlsx'})
        self.assertEqual(r.status_code,422)
        cl,h=self.make_user('finance','treasurer');uid=cl.get('/api/me').json()['user']['id'];cl.close()
        with unit() as s:
            u=s.get(User,uid);self.assertEqual(s.get(Role,u.role_id).name,'finance')

    def test_37_schedule_sends_once_without_real_email(self):
        from unittest.mock import patch
        import worker
        with patch.dict(os.environ,{'SMTP_HOST':'smtp.invalid','SMTP_FROM':'reports@example.invalid'}):
            r=self.post('/api/report-schedules',{'recipient':'recipient@example.invalid','currency':'UZS','hour':0,'enabled':True})
        self.assertEqual(r.status_code,200,r.text)
        with patch.object(worker,'send_report') as send:
            worker.report_tick();worker.report_tick()
            self.assertEqual(send.call_count,1)
        with unit() as s:self.assertEqual(s.get(ReportSchedule,r.json()['id']).last_sent,today())

    def test_38_schedule_failures_not_retried_repeatedly(self):
        from unittest.mock import patch
        import worker
        with unit(True) as s:s.add(ReportSchedule(recipient='recipient@example.invalid',currency='UZS',hour=0,enabled=True,created_by=1))
        with patch.object(worker,'send_report',side_effect=RuntimeError('secret must not be logged')) as send:
            worker.report_tick();worker.report_tick();self.assertEqual(send.call_count,1)
        with unit() as s:
            r=s.scalar(select(ReportSchedule));self.assertIsNone(r.last_sent);self.assertNotIn('secret',r.last_error)

    def test_39_category_and_schedule_admin_only(self):
        cl,h=self.make_user('finance','treasury')
        r=self.post('/api/categories',{'name':'Treasurer category','type':'income'},cl,h);self.assertEqual(r.status_code,403)
        r=self.post('/api/report-schedules',{'recipient':'r@example.invalid'},cl,h);self.assertEqual(r.status_code,403);cl.close()
        r=self.client.put(f'/api/categories/{self.cat}',json={'name':'Updated category','type':'outcome','activity':'operating'},headers=self.h)
        self.assertEqual(r.status_code,200)

    def test_40_self_approval_admin_and_editor_blocked(self):
        r=self.request('100').json()
        self.assertEqual(self.post(f"/api/requests/{r['id']}/decision",{'action':'approve','note':'Собственное согласование'}).status_code,403)
        cl,h=self.make_user('employee','initiator')
        r=self.request('100',client=cl,headers=h).json()
        data={k:r[k] for k in ('account_id','category_id','counterparty','amount','date','purpose','version')}
        data.update(status='pending',reason='Уточнение назначения платежа')
        response=self.client.put(f"/api/requests/{r['id']}",json=data,headers=self.h)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.post(f"/api/requests/{r['id']}/decision",{'action':'approve','note':'Согласование редактором'}).status_code,403)
        self.assertEqual(self.approve(r['id']).status_code,200)
        cl.close()

    def test_41_edit_version_and_stale_decision(self):
        r=self.request('100').json();rid=r['id']
        data={k:r[k] for k in ('account_id','category_id','counterparty','amount','date','purpose','version')}
        data.update(amount='120',status='pending',reason='Исправление суммы поставщика')
        first=self.client.put(f'/api/requests/{rid}',json=data,headers=self.h)
        self.assertEqual(first.status_code,200,first.text);self.assertGreater(first.json()['version'],r['version'])
        self.assertEqual(self.client.put(f'/api/requests/{rid}',json=data,headers=self.h).status_code,409)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'cancel','version':r['version'],'note':'Устаревшее действие пользователя'}).status_code,409)
        self.assertEqual(self.client.post(f'/api/requests/{rid}/decision',json={'action':'cancel','note':'Нет версии документа'},headers=self.h).status_code,428)

    def test_42_return_cancel_reserve_and_edit_rights(self):
        self.budget('1000','hard');r=self.request('600').json();rid=r['id'];self.approve(rid)
        b=lambda:next(b for b in self.client.get('/api/budgets').json() if b['category_id']==self.cat)
        self.assertEqual(b()['reserved'],'600.00')
        response=self.post(f'/api/requests/{rid}/decision',{'action':'return','note':'Нужно уточнить условия оплаты'})
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['status'],'returned');self.assertEqual(b()['reserved'],'0.00')
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'submit'}).status_code,200)
        self.approve(rid)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'cancel','note':'Поставка отменена поставщиком'}).status_code,200)
        self.assertEqual(b()['reserved'],'0.00')
        self.assertEqual(self.ledger('600','out',request_id=rid).status_code,409)
        cl,h=self.make_user()
        own=self.request('10',client=cl,headers=h).json()
        data={k:own[k] for k in ('account_id','category_id','counterparty','amount','date','purpose','version')};data['reason']='Изменение чужого документа'
        self.assertEqual(cl.put(f'/api/requests/{rid}',json=data,headers=h).status_code,403)
        cl.close()

    def test_43_two_forecasts_balance_invariants(self):
        from decimal import Decimal
        from random import Random
        rng=Random(51019)
        for i in range(12):
            r=self.documented_request({'account_id':self.acc,'category_id':self.cat,'counterparty':f'Supplier {i}','amount':str(rng.randrange(1,100000)),'date':str(today()+timedelta(days=rng.randrange(-3,7))),'purpose':'Проверка прогноза денежных средств','priority':'high','status':'draft' if i==0 else 'pending'}).json()
            if i%2 and i!=0:self.assertEqual(self.approve(r['id']).status_code,200)
        self.post('/api/receipts',{'account_id':self.acc,'category_id':self.cat,'counterparty':'Buyer','amount':'50.01','date':self.date})
        days=self.client.get('/api/dashboard?days=7').json()['forecast']
        for i,d in enumerate(days):
            for prefix in ('','requested_'):
                self.assertEqual(Decimal(d[prefix+'balance']),Decimal(d[prefix+'opening'])+Decimal(d['incoming'])-Decimal(d[prefix+'outgoing']))
                if i:self.assertEqual(d[prefix+'opening'],days[i-1][prefix+'balance'])
            self.assertLessEqual(Decimal(d['requested_balance']),Decimal(d['balance']))
        self.assertTrue(any(d['pending_events'] for d in days))

    def test_44_auditor_is_read_only(self):
        cl,h=self.make_user('auditor','auditor')
        for url in ('/api/requests','/api/audit','/api/budgets','/api/ledger','/api/dashboard','/api/report'):
            self.assertEqual(cl.get(url).status_code,200,url)
        self.assertEqual(self.request('10',client=cl,headers=h).status_code,403)
        self.assertEqual(self.post('/api/reserve',{'currency':'UZS','amount':'10'},cl,h).status_code,403)
        self.assertEqual(cl.get('/api/users').status_code,403)
        cl.close()

    def test_45_payment_version_and_reversal_version(self):
        r=self.request('100').json();rid=r['id'];approved=self.approve(rid).json()
        self.assertEqual(self.ledger('100','out',request_id=rid,request_version=r['version']).status_code,409)
        paid=self.ledger('100','out',request_id=rid);self.assertEqual(paid.status_code,200,paid.text)
        current=lambda:next(x for x in self.client.get('/api/requests').json() if x['id']==rid)
        self.assertGreater(current()['version'],approved['version']);v=current()['version']
        self.assertEqual(self.post(f"/api/ledger/{paid.json()['id']}/reverse",{'reason':'Исправление платёжного документа'}).status_code,200)
        self.assertGreater(current()['version'],v)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'cancel','version':v,'note':'Устаревшая вкладка после сторно'}).status_code,409)

    def test_46_requested_forecast_account_shortfall(self):
        aid=self.post('/api/accounts',{'name':'Second empty','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date}).json()['id']
        self.documented_request({'account_id':aid,'category_id':self.cat,'counterparty':'Supplier','amount':'10','date':self.date,'purpose':'Проверка сценария всех заявок'})
        d=self.client.get('/api/dashboard').json()['forecast'][0]
        self.assertFalse(d['risk']);self.assertTrue(d['requested_risk']);self.assertEqual(d['requested_account_shortfalls'][0]['account_id'],aid)

    def test_47_orm_optimistic_lock_two_sessions(self):
        from sqlalchemy.orm.exc import StaleDataError
        rid=self.request('100').json()['id']
        with Session(engine) as first,Session(engine) as second:
            a=first.get(PaymentRequest,rid);b=second.get(PaymentRequest,rid)
            a.purpose='Первое изменение документа';first.commit()
            b.purpose='Конфликтующее изменение документа'
            with self.assertRaises(StaleDataError):second.commit()
        with unit() as s:self.assertEqual(s.get(PaymentRequest,rid).purpose,'Первое изменение документа')

    def test_48_parallel_approvals_cannot_overreserve(self):
        from concurrent.futures import ThreadPoolExecutor
        self.budget('1000','hard')
        rs=[self.request('600').json() for _ in range(2)]
        for r in rs:self.assertEqual(self.finance_approve(r['id']).status_code,200)
        rows={x['id']:x for x in self.client.get('/api/requests').json()}
        rs=[rows[r['id']] for r in rs]
        cl,h=self.make_user('director','parallel_director')
        def approve(r):return cl.post(f"/api/requests/{r['id']}/decision",json={'version':r['version'],'action':'approve','note':'Параллельная проверка лимита'},headers=h).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(approve,rs))
        self.assertEqual(sorted(codes),[200,409])
        b=next(b for b in self.client.get('/api/budgets').json() if b['category_id']==self.cat)
        self.assertEqual(b['reserved'],'600.00');cl.close()

    def test_49_two_stage_approval_and_payment_gate(self):
        # A threshold stored before the owner's decision no longer skips the director.
        with unit(True) as s:s.add(Setting(key=f'company:{self.company}:approval_limit_UZS',value='100000000'))
        cashier,ch=self.make_user('cashier','cashier')
        director,dh=self.make_user('director','director2')
        try:
            r=self.request('101',client=cashier,headers=ch).json();rid=r['id']
            self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'approve'},director,dh).status_code,403)
            first=self.finance_approve(rid).json()
            self.assertEqual(first['status'],'pending');self.assertEqual(first['approval_stage'],'director')
            self.assertEqual(self.finance_approve(rid).status_code,403)
            self.assertEqual(self.ledger('101','out',request_id=rid).status_code,409)
            approved=self.post(f'/api/requests/{rid}/decision',{'action':'approve'},director,dh)
            self.assertEqual(approved.status_code,200,approved.text);self.assertEqual(approved.json()['status'],'approved')
            self.assertEqual(self.ledger('101','out',request_id=rid).status_code,200)
            low=self.request('1').json();first=self.finance_approve(low['id']).json()
            self.assertEqual(first['status'],'pending');self.assertEqual(first['approval_stage'],'director')
            self.assertEqual(self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'amount':'1','kind':'out','date':self.date,'reference':'BYPASS','note':'Без утверждения платежа'},cashier,ch).status_code,403)
        finally:cashier.close();director.close()

    def test_50_missing_limit_and_edit_reset(self):
        r=self.request('1').json();first=self.finance_approve(r['id']).json()
        self.assertEqual(first['status'],'pending')
        ret=self.post(f"/api/requests/{r['id']}/decision",{'action':'return','note':'Возвращаем на уточнение'})
        self.assertIsNone(ret.json()['finance_approved_by'])
        self.post(f"/api/requests/{r['id']}/decision",{'action':'submit'})
        row=next(x for x in self.client.get('/api/requests').json() if x['id']==r['id'])
        self.assertEqual(row['approval_stage'],'finance')

    def test_51_archive_preserves_history_and_blocks_new_entries(self):
        self.assertEqual(self.post(f'/api/accounts/{self.acc}/archive',{'archived':True,'reason':'Закрытие банковского счёта'}).status_code,409)
        aid=self.post('/api/accounts',{'name':'Closed bank','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date}).json()['id']
        self.ledger('10',account_id=aid,reference='CLOSEDIN')
        self.ledger('10','out',account_id=aid,reference='CLOSEDOUT')
        self.assertEqual(self.post(f'/api/accounts/{aid}/archive',{'archived':True,'reason':'Закрытие банковского счёта'}).status_code,200)
        self.assertNotIn(aid,[a['id'] for a in self.client.get('/api/accounts').json()])
        self.assertIn(aid,[a['id'] for a in self.client.get('/api/accounts?archived=true').json()])
        self.assertEqual(self.ledger('1',account_id=aid,reference='BLOCKED').status_code,409)
        self.assertEqual(len([x for x in self.client.get('/api/ledger').json() if x['account_id']==aid]),2)
        self.assertEqual(self.client.get('/api/dashboard').json()['balance'],'1000000.00')
        self.assertEqual(self.post(f'/api/accounts/{aid}/archive',{'archived':False,'reason':'Возобновление работы счёта'}).status_code,200)

    def test_52_server_filters_full_history_and_pagination(self):
        with unit(True) as s:
            for i in range(515):s.add(Ledger(account_id=self.acc,category_id=self.cat,kind='in',amount=100,date=today()-timedelta(days=2) if i==0 else today(),reference=f'BATCH-{i}',counterparty='Old supplier' if i==0 else 'Recent',creator_id=1))
        old=str(today()-timedelta(days=2))
        result=self.client.get('/api/ledger',params={'paginated':True,'date_from':old,'date_to':old,'q':'Old supplier','currency':'UZS'}).json()
        self.assertEqual(result['total'],1);self.assertEqual(result['items'][0]['reference'],'BATCH-0')
        page=self.client.get('/api/ledger?paginated=true&page=2').json()
        self.assertEqual(page['total'],515);self.assertEqual(len(page['items']),10)
        self.assertEqual(self.client.get('/api/ledger?date_from=2026-12-01&date_to=2026-01-01').status_code,422)
        for _ in range(6):self.request('1')
        requests=self.client.get('/api/requests',params={'paginated':True,'date_from':self.date,'date_to':self.date,'state':'pending','q':'Supplier'}).json()
        self.assertEqual(requests['total'],6)

    def test_53_password_reset_revokes_sessions_and_hides_password(self):
        cl,h=self.make_user('employee','resetme')
        try:
            uid=cl.get('/api/me').json()['user']['id']
            self.assertEqual(self.post(f'/api/users/{uid}/password',{'password':PASSWORD+'new'},cl,h).status_code,403)
            self.assertEqual(self.post(f'/api/users/{uid}/password',{'password':PASSWORD+'new'}).status_code,200)
            self.assertEqual(cl.get('/api/me').status_code,401)
            self.assertEqual(cl.post('/api/login',json={'username':'resetme','password':PASSWORD}).status_code,401)
            self.assertEqual(cl.post('/api/login',json={'username':'resetme','password':PASSWORD+'new'}).status_code,200)
            self.assertNotIn(PASSWORD,self.client.get('/api/audit').text)
            self.assertNotIn('password',self.client.get('/api/users').text)
        finally:cl.close()


    def test_54_director_for_every_amount_in_every_currency(self):
        from app.services import needs_director
        for curr in ('UZS','USD','EUR'):
            a=self.post('/api/accounts',{'name':'Currency '+curr,'kind':'bank','currency':curr,'opening':'0','opening_date':self.date}).json()['id']
            refused=self.post('/api/approval-policy',{'currency':curr,'amount':'100'})
            self.assertEqual(refused.status_code,409);self.assertIn('директор',refused.text)
            with unit() as ss:
                for n in (1,10000,10001):self.assertTrue(needs_director(ss,PaymentRequest(account_id=a,amount=n)))
        policy=self.client.get('/api/approval-policy').json()
        self.assertTrue(policy['director_always']);self.assertEqual(policy['limits'],{'UZS':None,'USD':None,'EUR':None})

    def test_55_cashflow_reconciles_balances_and_plan(self):
        import io
        from app.cash_report import report
        with unit(True) as ss:
            a=ss.get(Account,self.acc);a.opening_date=date(2025,1,1);a.opening=10000
            ss.add(Ledger(account_id=a.id,category_id=1,kind='in',amount=20000,date=date(2025,1,10),reference='CFIN',creator_id=1))
            ss.add(Ledger(account_id=a.id,category_id=self.cat,kind='out',amount=4000,date=date(2025,1,20),reference='CFOUT',creator_id=1))
            ss.add(Account(name='Archived report account',kind='bank',currency='UZS',opening=7000,opening_date=date(2025,2,15),archived=True,created_by=1))
            ss.add(Account(name='Month boundary account',kind='bank',currency='UZS',opening=5000,opening_date=date(2025,3,1),created_by=1))
        d=self.client.get('/api/report?year=2025').json()
        self.assertEqual(d['opening'][0],'100.00');self.assertEqual(d['totals'][0],'160.00');self.assertEqual(d['closing'][0],'260.00')
        self.assertEqual(d['opening'][1],'260.00');self.assertEqual(d['opening_adjustments'][1],'70.00');self.assertEqual(d['closing'][1],'330.00')
        self.assertEqual(d['opening'][2],'330.00');self.assertEqual(d['opening_adjustments'][2],'50.00');self.assertEqual(d['closing'][2],'380.00')
        self.assertEqual(d['closing'][11],'380.00')
        self.assertIsNone(d['plan_totals'][0])
        items=[{'category_id':r['category_id'],'kind':r['kind'],'amount':'0'} for r in d['rows']]
        next(r for r in items if r['category_id']==1)['amount']='180'
        next(r for r in items if r['category_id']==self.cat)['amount']='50'
        p={'month':'2025-01','currency':'UZS','version':0,'opening':'100','reason':'План утверждён для проверки','items':items}
        self.assertEqual(self.post('/api/cash-plan',p).status_code,200)
        self.assertEqual(self.post('/api/cash-plan',p).status_code,409)
        d=self.client.get('/api/report?year=2025').json()
        self.assertEqual(d['plan_totals'][0],'130.00');self.assertEqual(d['plan_closing'][0],'230.00')
        self.assertEqual(d['plan_opening'][1],'230.00');self.assertIsNone(d['plan_closing'][1])
        from openpyxl import load_workbook
        exported=self.client.get('/api/export/report.xlsx?year=2025&currency=UZS&mode=plan&start_month=1&end_month=2')
        self.assertEqual(exported.status_code,200)
        wb=load_workbook(io.BytesIO(exported.content));ws=wb.active
        self.assertEqual(ws.cell(2,8).value,'За период · План')
        self.assertEqual(ws.cell(3,9).value,100)
        self.assertEqual(ws.cell(ws.max_row,9).value,330)
        self.assertEqual(ws.cell(ws.max_row,8).value,'Не задан');wb.close()
        pdf=self.client.get('/api/export/report.pdf?year=2025&currency=UZS&mode=plan&start_month=1&end_month=2')
        self.assertEqual(pdf.status_code,200);self.assertTrue(pdf.content.startswith(b'%PDF'))
        p['version']=1;p['items'][0]['amount']=None
        self.assertEqual(self.post('/api/cash-plan',p).status_code,200)
        self.assertIsNone(self.client.get('/api/report?year=2025').json()['plan_totals'][0])
        cl,h=self.make_user('accountant','planreader')
        self.assertEqual(self.post('/api/cash-plan',p,cl,h).status_code,403);cl.close()

    def test_56_audit_tashkent_range_and_history(self):
        with unit(True) as ss:
            ss.add(Audit(company_id=self.company,user_id=1,action='Особое событие',entity='account',created_at=datetime(2025,1,1,20,30)))
            for i in range(205):ss.add(Audit(company_id=self.company,user_id=1,action='API GET',entity='api',created_at=datetime(2025,1,3)))
        r=self.client.get('/api/audit/history?date_from=2025-01-02&date_to=2025-01-02').json()
        self.assertEqual(r['total'],1);self.assertEqual(r['items'][0]['date'],'02.01.2025 01:30')
        r=self.client.get('/api/audit/history?date_from=2025-01-03&date_to=2025-01-03&technical=true&page=2').json()
        self.assertEqual(r['total'],205);self.assertEqual(len(r['items']),20)
        self.assertEqual(self.client.get('/api/audit/history?date_from=2025-02-01&date_to=2025-01-01').status_code,422)
        cl,h=self.make_user('employee','auditblocked');self.assertEqual(cl.get('/api/audit/history').status_code,403);cl.close()

    def test_57_budget_groups_and_named_import(self):
        group_only={'category_id':self.cat,'month':self.month,'currency':'UZS','amount':None,'cost_group':'fixed','reason':'Группа без нового лимита'}
        self.assertEqual(self.post('/api/budgets',group_only).status_code,200)
        before=self.client.get('/api/budgets?month='+self.month).json()
        self.assertIsNone(next(r for r in before if r['category_id']==self.cat)['limit'])
        r=self.post('/api/budgets',{'category_id':self.cat,'month':self.month,'currency':'UZS','amount':'100','cost_group':'variable','reason':'Настройка группы затрат'})
        self.assertEqual(r.status_code,200)
        rows=self.client.get('/api/budgets?month='+self.month).json()
        self.assertEqual(next(r for r in rows if r['category_id']==self.cat)['cost_group'],'variable')
        self.assertNotIn(1,[r['category_id'] for r in rows])
        self.assertEqual(self.post('/api/budgets',group_only).status_code,200)
        after=self.client.get('/api/budgets?month='+self.month).json()
        self.assertEqual(next(r for r in after if r['category_id']==self.cat)['limit'],'100.00')
        template=self.client.get('/api/import/template.csv').content.decode('utf-8-sig')
        raw=template+f'{self.date};Поступление;Test bank;;Поступления от покупателей;12.34;Покупатель;NAMED;Оплата по договору\r\n'
        result=self.client.post('/api/import/preview',content=raw.encode('utf-8'),headers={**self.h,'X-Filename':'named.csv'})
        self.assertEqual(result.status_code,200,result.text);self.assertTrue(result.json()['can_commit'],result.text)
        self.assertEqual(result.json()['rows'][0]['account_id'],self.acc)

    def test_59_company_scenarios_notes_group_preview_and_file_dedup(self):
        boot=self.client.get('/api/bootstrap').json();companies={c['code']:c['id'] for c in boot['companies']}
        self.assertIn('UZGERMED',companies);self.assertIn('ZUMA',companies)
        base=self.client.get(f"/api/report?year={self.date[:4]}&company_id={companies['UZGERMED']}&scenario=A").json()
        items=[{'category_id':r['category_id'],'kind':r['kind'],'amount':'0'} for r in base['rows']]
        next(r for r in items if r['category_id']==1)['amount']='100'
        for scenario,value in [('A','100'),('B','200'),('V','300')]:
            next(r for r in items if r['category_id']==1)['amount']=value
            body={'company_id':companies['UZGERMED'],'scenario':scenario,'month':self.month,'currency':'UZS','version':0,'opening':'1000000','reason':'Утверждён отдельный сценарий','items':items}
            self.assertEqual(self.post('/api/cash-plan',body).status_code,200)
        plans=[self.client.get(f"/api/report?year={self.date[:4]}&company_id={companies['UZGERMED']}&scenario={s}").json() for s in ('A','B','V')]
        self.assertEqual([p['plan_totals'][int(self.month[-2:])-1] for p in plans],['100.00','200.00','300.00'])
        self.assertEqual(plans[0]['totals'],plans[1]['totals']);self.assertEqual(plans[1]['totals'],plans[2]['totals'])
        self.assertEqual(self.post('/api/plan-note',{'company_id':companies['UZGERMED'],'scenario':'B','month':self.month,'currency':'UZS','indicator':'net','note':'Изоҳ: перенос оплаты'}).status_code,200)
        noted=self.client.get(f"/api/report?year={self.date[:4]}&company_id={companies['UZGERMED']}&scenario=B").json()
        self.assertEqual(noted['notes'][self.month+':net'],'Изоҳ: перенос оплаты')
        preview=self.client.get(f"/api/group-report?company_id={companies['UZGERMED']}&currency=UZS&scenario=A&month={self.month}&day={self.date}")
        self.assertEqual(preview.status_code,200,preview.text);self.assertIn('сценарий A',preview.json()['text'])
        self.assertNotIn('send',preview.json())
        template=self.client.get('/api/import/template.csv').content.decode('utf-8-sig')
        raw=template+f'{self.date};Поступление;Test bank;;Поступления от покупателей;1.00;Покупатель;DIGEST-1;Проверка повторного файла\r\n'
        first=self.client.post('/api/import/preview',content=raw.encode(),headers={**self.h,'X-Filename':'same.csv'});self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(self.post(f"/api/import/{first.json()['id']}/commit",{}).status_code,200)
        again=self.client.post('/api/import/preview',content=raw.encode(),headers={**self.h,'X-Filename':'same.csv'})
        self.assertEqual(again.status_code,409);self.assertIn('уже импортирован',again.text)

    def test_60_available_funds_reservations_release_and_payment_recheck(self):
        first=self.request('600000').json();self.assertEqual(self.approve(first['id']).status_code,200)
        second=self.request('500000').json();denied=self.approve(second['id'])
        self.assertEqual(denied.status_code,409);self.assertIn('резерв утверждённых заявок 600000.00',denied.text)
        self.assertEqual(self.post(f"/api/requests/{first['id']}/decision",{'action':'cancel','note':'Платёж отменён поставщиком'}).status_code,200)
        self.assertEqual(self.approve(second['id']).status_code,200)
        # A manual payment cannot consume money reserved for another approved request.
        self.assertEqual(self.ledger('600000','out',reference='UNRESERVED').status_code,409)
        paid=self.ledger('500000','out',reference='PAY-SECOND',request_id=second['id'])
        self.assertEqual(paid.status_code,200,paid.text)
        self.assertEqual(self.client.get('/api/dashboard').json()['balance'],'500000.00')

    def test_61_funds_are_isolated_by_account_company_and_currency(self):
        companies={c['code']:c['id'] for c in self.client.get('/api/bootstrap').json()['companies']}
        r=self.post('/api/accounts',{'name':'Zuma rich bank','company_id':companies['ZUMA'],'kind':'bank','currency':'UZS','opening':'9000000','opening_date':self.date},headers={**self.h,'X-Company-ID':str(companies['ZUMA'])});self.assertEqual(r.status_code,200,r.text)
        empty=self.post('/api/accounts',{'name':'UZGERMED empty bank','company_id':companies['UZGERMED'],'kind':'bank','currency':'UZS','opening':'0','opening_date':self.date}).json()['id']
        r=self.documented_request({'account_id':empty,'category_id':self.cat,'counterparty':'Supplier','amount':'1','date':self.date,'purpose':'Проверка изоляции денег'}).json()
        denied=self.approve(r['id']);self.assertEqual(denied.status_code,409);self.assertIn('UZS',denied.text)

    def test_62_group_report_bank_mtd_as_of_and_no_opening_or_transfer_double_count(self):
        companies={c['code']:c['id'] for c in self.client.get('/api/bootstrap').json()['companies']}
        cash=self.post('/api/accounts',{'name':'Test cash MTD','company_id':companies['UZGERMED'],'kind':'cash','currency':'UZS','opening':'0','opening_date':str(today()-timedelta(days=10))}).json()['id']
        self.assertEqual(self.ledger('100','in',reference='BANK-MTD').status_code,200)
        self.assertEqual(self.ledger('200','in',reference='CASH-MTD',account_id=cash).status_code,200)
        self.assertEqual(self.ledger('50','transfer',reference='TRANSFER-MTD',to_account_id=cash).status_code,200)
        r=self.client.get(f"/api/group-report?company_id={companies['UZGERMED']}&currency=UZS&scenario=A&month={self.month}&day={self.date}")
        self.assertEqual(r.status_code,200,r.text);data=r.json()
        self.assertEqual(data['bank_income_mtd'],'100.00')
        self.assertEqual(data['income_day'],'300.00') # external bank + cash income; transfer excluded
        self.assertEqual(data['opening'],'1000000.00')
        self.assertEqual(data['closing'],'1000300.00')
        future=(today()+timedelta(days=1)).isoformat()
        self.assertEqual(self.client.get(f"/api/group-report?company_id={companies['UZGERMED']}&currency=UZS&scenario=A&month={future[:7]}&day={future}").status_code,422)

    def test_63_plan_import_header_mapping_blanks_zero_details_and_dedup(self):
        import io
        from openpyxl import Workbook
        companies={c['code']:c['id'] for c in self.client.get('/api/bootstrap').json()['companies']}
        # Existing January A plan must survive a blank source cell.
        body={'company_id':companies['UZGERMED'],'scenario':'A','month':'2026-01','currency':'UZS','version':0,'opening':None,
              'reason':'Исходный план для проверки пустой ячейки','items':[{'category_id':self.cat,'kind':'out','amount':'99'}]}
        self.assertEqual(self.post('/api/cash-plan',body).status_code,200)
        wb=Workbook();ws=wb.active;ws.title='план на год';ws['H7']='Б';ws['I7']='А';ws['J7']='В'
        ws.append([])
        ws['A8']=date(2026,1,1);ws['E8']='Жами ҳаражатлар';ws['H8']=999999
        ws['A9']=date(2026,1,1);ws['B9']=1;ws['C9']='61';ws['E9']='Закупка сырья';ws['H9']=20;ws['I9']=None;ws['J9']=30
        # A coded expense remains a detail even when the optional sequence in B is blank.
        ws['A10']=date(2026,1,1);ws['C10']='7';ws['E10']='Курсовые разницы';ws['J10']=0
        ws['A11']=date(2026,1,1);ws['E11']='Жами тушум';ws['H11']=999999
        ws['A12']=date(2026,1,1);ws['B12']=2;ws['C12']='IN';ws['E12']='Доход после итога';ws['H12']=777 # income detail, ignored
        ws['A13']=date(2026,2,1);ws['E13']='Жами ҳаражатлар'
        ws['A14']=date(2026,2,1);ws['B14']=1;ws['C14']='61';ws['E14']='Закупка сырья';ws['H14']=25;ws['I14']=0;ws['J14']=35
        out=io.BytesIO();wb.save(out);wb.close();raw=out.getvalue()
        url=f"/api/plan-import/preview?company_id={companies['UZGERMED']}&year=2026&month_from=1&month_to=2"
        p=self.client.post(url,content=raw,headers={**self.h,'Content-Type':'application/octet-stream','X-Filename':'synthetic.xlsx'})
        self.assertEqual(p.status_code,200,p.text);data=p.json();self.assertTrue(data['can_commit']);self.assertEqual(len(data['rows']),3)
        self.assertEqual(data['rows'][0]['amounts'],{'A':None,'B':2000,'V':3000})
        self.assertEqual(data['rows'][1]['amounts'],{'A':None,'B':None,'V':0})
        mapping={data['rows'][0]['source_key']:str(self.cat),data['rows'][1]['source_key']:str(self.cat)}
        committed=self.post(f"/api/plan-import/{data['id']}/commit",{'mappings':mapping,'reason':'Подтверждение синтетического плана'})
        self.assertEqual(committed.status_code,200,committed.text)
        jan={s:self.client.get(f"/api/report?year=2026&company_id={companies['UZGERMED']}&scenario={s}&as_of=2026-01-31").json() for s in ('A','B','V')}
        idx=next(i for i,r in enumerate(jan['A']['rows']) if r['category_id']==self.cat and r['kind']=='out')
        self.assertEqual(jan['A']['rows'][idx]['plan'][0],'-99.00')
        self.assertEqual(jan['B']['rows'][idx]['plan'][0],'-20.00');self.assertEqual(jan['V']['rows'][idx]['plan'][0],'-30.00')
        feb=self.client.get(f"/api/report?year=2026&company_id={companies['UZGERMED']}&scenario=A&as_of=2026-02-28").json()
        self.assertEqual(feb['rows'][idx]['plan'][1],'0.00')
        self.assertEqual(self.client.post(url,content=raw,headers={**self.h,'Content-Type':'application/octet-stream','X-Filename':'synthetic.xlsx'}).status_code,409)

    def test_64_plan_import_reports_formula_without_cached_value(self):
        import io
        from openpyxl import Workbook
        companies={c['code']:c['id'] for c in self.client.get('/api/bootstrap').json()['companies']}
        wb=Workbook();ws=wb.active;ws.title='план на год';ws['H7']='Б';ws['I7']='А';ws['J7']='В'
        ws['A8']=date(2026,1,1);ws['E8']='Жами ҳаражатлар'
        ws['A9']=date(2026,1,1);ws['B9']=1;ws['C9']='61';ws['E9']='Закупка сырья';ws['H9']=20;ws['I9']=10;ws['J9']='=10+20'
        out=io.BytesIO();wb.save(out);wb.close()
        r=self.client.post(f"/api/plan-import/preview?company_id={companies['UZGERMED']}&year=2026&month_from=1&month_to=1",content=out.getvalue(),headers={**self.h,'Content-Type':'application/octet-stream','X-Filename':'formula.xlsx'})
        self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['can_commit'])
        self.assertIn('нет сохранённого числового результата',r.text)

    def plan_file(self,padded=False):
        import io,zipfile
        from openpyxl import Workbook
        book=Workbook();sheet=book.active;sheet.title='план на год'
        sheet['H7']='А';sheet['I7']='Б';sheet['J7']='В'
        sheet['A8']=date(2026,1,1);sheet['E8']='Жами ҳаражатлар'
        sheet['A9']=date(2026,1,1);sheet['C9']='61';sheet['E9']='Закупка сырья'
        sheet['H9']=10;sheet['I9']=20;sheet['J9']=30
        buf=io.BytesIO();book.save(buf);book.close()
        if padded:
            with zipfile.ZipFile(buf,'a') as z:z.writestr('test-padding.bin',b'x'*70000)
        return buf.getvalue()

    def preview_plan(self,raw=None,client=None,headers=None):
        companies={c['code']:c['id'] for c in self.client.get('/api/bootstrap').json()['companies']}
        url=f"/api/plan-import/preview?company_id={companies['UZGERMED']}&year=2026&month_from=1&month_to=1"
        return (client or self.client).post(url,content=raw or self.plan_file(),headers={**(headers or self.h),'Content-Type':'application/octet-stream','X-Filename':'test-plan.xlsx'})

    def test_65_operator_can_enter_plans_and_facts_but_not_approve_or_set_limits(self):
        cl,h=self.make_user('operator','operator1')
        try:
            data={'month':self.month,'currency':'UZS','scenario':'B','opening':None,'version':0,'reason':'План введён оператором','items':[{'category_id':self.cat,'kind':'out','amount':'120'}]}
            self.assertEqual(self.post('/api/cash-plan',data,cl,h).status_code,200)
            account={'name':'Operator cash','kind':'cash','currency':'UZS','opening':'90','opening_date':self.date}
            self.assertEqual(self.post('/api/accounts',account,cl,h).status_code,200)
            fact={'account_id':self.acc,'category_id':self.cat,'amount':'10','kind':'in','date':self.date,'reference':'OP-FACT','note':'Ввод факта оператором'}
            self.assertEqual(self.post('/api/ledger',fact,cl,h).status_code,200)
            rid=self.request().json()['id']
            self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'approve'},cl,h).status_code,403)
            self.assertEqual(self.post('/api/approval-policy',{'amount':'1'},cl,h).status_code,403)
            self.assertEqual(cl.get('/api/users').status_code,403)
            self.assertEqual(self.post('/api/budgets',{'category_id':self.cat,'month':self.month,'currency':'UZS','amount':'1','reason':'Не разрешено оператору'},cl,h).status_code,403)
            self.assertEqual(self.preview_plan(client=cl,headers=h).status_code,200)
        finally:cl.close()

    def test_66_investor_is_read_only_even_using_api_directly(self):
        cl,h=self.make_user('investor','investor1')
        try:
            for path in ('/api/dashboard','/api/accounts','/api/ledger','/api/report','/api/budgets'):
                self.assertEqual(cl.get(path).status_code,200,path)
            self.assertEqual(self.request(client=cl,headers=h).status_code,403)
            self.assertEqual(self.post('/api/accounts',{'name':'Denied','kind':'cash','currency':'UZS','opening':'0','opening_date':self.date},cl,h).status_code,403)
            self.assertEqual(self.post('/api/cash-plan',{'month':self.month,'currency':'UZS','version':0,'reason':'Попытка инвестора','items':[]},cl,h).status_code,403)
            self.assertEqual(self.post('/api/approval-policy',{'amount':'0'},cl,h).status_code,403)
            self.assertEqual(self.preview_plan(client=cl,headers=h).status_code,403)
            self.assertEqual(cl.get('/api/users').status_code,403)
        finally:cl.close()

    def test_67_director_controls_limits_and_editing_without_user_administration(self):
        cl,h=self.make_user('director','director_edit')
        try:
            for currency in ('UZS','USD','EUR'):
                self.assertEqual(self.post('/api/approval-policy',{'amount':'10','currency':currency},cl,h).status_code,409)
            self.assertTrue(cl.get('/api/approval-policy').json()['director_always'])
            self.assertEqual(cl.get('/api/users').status_code,403)
            rid=self.request('100').json()['id']
            self.assertEqual(self.finance_approve(rid).status_code,200)
            row=next(r for r in self.client.get('/api/requests').json() if r['id']==rid)
            edited=cl.put(f'/api/requests/{rid}',json={'account_id':self.acc,'category_id':self.cat,'counterparty':'Supplier','amount':'90','date':self.date,'purpose':'Изменение директором','reason':'Изменены условия оплаты','version':row['version']},headers=h)
            self.assertEqual(edited.status_code,200,edited.text)
            self.assertEqual(self.finance_approve(rid).status_code,200)
            denied=self.post(f'/api/requests/{rid}/decision',{'action':'approve'},cl,h)
            self.assertEqual(denied.status_code,403)
            self.assertIn('редактор',denied.text)
        finally:cl.close()

    def test_68_large_plan_upload_and_duplicate_pending_commits(self):
        raw=self.plan_file(padded=True);self.assertGreater(len(raw),65536)
        one=self.preview_plan(raw);two=self.preview_plan(raw)
        self.assertEqual(one.status_code,200,one.text);self.assertEqual(two.status_code,200,two.text)
        data={'mappings':{one.json()['rows'][0]['source_key']:str(self.cat)},'reason':'Подтверждение тестового плана'}
        self.assertEqual(self.post(f"/api/plan-import/{one.json()['id']}/commit",data).status_code,200)
        denied=self.post(f"/api/plan-import/{two.json()['id']}/commit",data)
        self.assertEqual(denied.status_code,409);self.assertIn('уже импортированы',denied.text)

    def test_69_stale_plan_import_preserves_concurrent_edit(self):
        p=self.preview_plan().json()
        self.assertEqual(self.post('/api/cash-plan',{'month':'2026-01','currency':'UZS','scenario':'A','version':0,'reason':'Другой пользователь изменил план','items':[{'category_id':self.cat,'kind':'out','amount':'777'}]}).status_code,200)
        denied=self.post(f"/api/plan-import/{p['id']}/commit",{'mappings':{p['rows'][0]['source_key']:str(self.cat)},'reason':'Подтверждение устаревшего плана'})
        self.assertEqual(denied.status_code,409);self.assertIn('изменился',denied.text)
        with unit() as s:
            plan=s.scalar(select(CashPlan).where(CashPlan.month=='2026-01',CashPlan.scenario=='A'))
            self.assertEqual(json.loads(plan.payload)[f'{self.cat}:out'],-77700)
            self.assertEqual(s.get(PlanImportBatch,p['id']).status,'preview')

    def test_70_readiness_and_database_outage_are_safe(self):
        from unittest.mock import patch
        from sqlalchemy.exc import OperationalError
        self.assertEqual(self.client.get('/ready').status_code,200)
        with patch('app.main.unit',side_effect=OperationalError('private SQL',{},Exception('secret connection'))):
            for path in ('/ready','/api/me'):
                r=self.client.get(path)
                self.assertEqual(r.status_code,503,r.text)
                self.assertNotIn('secret',r.text);self.assertNotIn('private SQL',r.text)
                self.assertEqual(r.headers['X-Content-Type-Options'],'nosniff')
        def technical_audit_unavailable(write=False):
            if write:raise OperationalError('http audit',{},Exception('temporary failure'))
            return unit()
        with patch('app.main.unit',side_effect=technical_audit_unavailable):
            self.assertEqual(self.client.get('/api/me').status_code,200)

    def test_71_plan_xml_rejects_entities_and_absurd_row_numbers(self):
        import io,zipfile
        raw=self.plan_file()
        for mode in ('entities','huge_row'):
            target=io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(raw)) as original,zipfile.ZipFile(target,'w') as changed:
                for entry in original.infolist():
                    data=original.read(entry.filename)
                    if entry.filename=='xl/worksheets/sheet1.xml':
                        if mode=='entities':
                            data=b'<!DOCTYPE worksheet [<!ENTITY test "ENTITY_VALUE">]>'+data.replace(b'\xd0\x90</t>',b'&test;</t>')
                        else:data=data.replace(b'r="A9"',b'r="A9999999999"')
                    changed.writestr(entry.filename,data)
            self.assertEqual(self.preview_plan(target.getvalue()).status_code,422,mode)

    def test_72_revoked_upload_permission_is_rechecked_before_write(self):
        from unittest.mock import patch
        import app.plan_import as importer
        raw=self.plan_file()
        cl,h=self.make_user('operator','revoked_upload')
        async def revoke_during_upload(request):
            with unit(True) as s:
                u=s.scalar(select(User).where(User.username=='revoked_upload'))
                u.role='investor'
                # The role that counts is the one assigned in the company.
                for m in s.scalars(select(CompanyUser).where(CompanyUser.user_id==u.id)):m.role='investor'
            return raw
        try:
            with patch.object(importer,'read_upload',side_effect=revoke_during_upload):
                self.assertEqual(self.preview_plan(raw,cl,h).status_code,403)
            with unit() as s:self.assertEqual(len(list(s.scalars(select(PlanImportBatch)))),0)
        finally:cl.close()

    def second_company(self):
        cid=next(c['id'] for c in self.client.get('/api/companies').json()['companies'] if c['code']=='ZUMA')
        h={**self.h,'X-Company-ID':str(cid)}
        cats=self.client.get('/api/bootstrap',headers=h).json()['categories']
        cat=next(c['id'] for c in cats if c['name']=='Закупка сырья')
        r=self.post('/api/accounts',{'name':'Test bank','kind':'bank','currency':'UZS','opening':'2000','opening_date':self.date},headers=h)
        self.assertEqual(r.status_code,200,r.text)
        return cid,h,cat,r.json()['id']

    def test_73_company_isolation_across_sections_exports_and_direct_ids(self):
        import io
        from openpyxl import load_workbook
        cid,h,cat,acc=self.second_company()
        self.assertNotEqual(cat,self.cat)
        self.assertEqual(self.client.get('/api/dashboard',headers=h).json()['balance'],'2000.00')
        self.assertEqual(self.client.get('/api/dashboard').json()['balance'],'1000000.00')
        ledger=self.ledger(reference='PRIVATE-UZGERMED').json()['id']
        req=self.request().json()['id']
        for path in ('/api/ledger','/api/requests','/api/receipts'):
            self.assertEqual(self.client.get(path,headers=h).json(),[],path)
        self.assertEqual(self.client.get('/api/ledger?paginated=true',headers=h).json()['total'],0)
        self.assertEqual(self.client.get('/api/requests?paginated=true',headers=h).json()['total'],0)
        self.assertNotIn('PRIVATE-UZGERMED',self.client.get('/api/export/ledger.csv',headers=h).text)
        self.assertEqual(self.post(f'/api/ledger/{ledger}/reverse',{'reason':'Cross company reversal'},headers=h).status_code,404)
        self.assertEqual(self.post(f'/api/requests/{req}/decision',{'action':'cancel','version':1,'note':'Cross company cancel'},headers=h).status_code,404)
        self.assertEqual(self.post(f'/api/accounts/{self.acc}/archive',{'archived':True,'reason':'Cross company account'},headers=h).status_code,404)
        self.assertEqual(self.client.get(f'/api/documents?ledger_id={ledger}',headers=h).status_code,404)
        upload=self.client.post(f'/api/ledger/{ledger}/document',content=b'%PDF-1.4\nsynthetic',headers={**self.h,'Content-Type':'application/pdf','X-Filename':'test.pdf'})
        self.assertEqual(upload.status_code,200,upload.text)
        self.assertEqual(self.client.get(f"/api/documents/{upload.json()['id']}",headers=h).status_code,404)
        r=self.ledger(kind='transfer',reference='CROSS',to_account_id=acc)
        self.assertEqual(r.status_code,404,r.text)
        body={'account_id':acc,'category_id':self.cat,'kind':'in','amount':'1','date':self.date,'reference':'FOREIGN-CATEGORY'}
        self.assertEqual(self.post('/api/ledger',body,headers=h).status_code,404)
        self.assertEqual(self.client.get('/api/report?company_id='+str(self.company),headers=h).status_code,409)
        exported=self.client.get('/api/export/report.xlsx?year='+self.date[:4],headers=h)
        self.assertEqual(exported.status_code,200,exported.text[:200] if exported.status_code!=200 else '')
        wb=load_workbook(io.BytesIO(exported.content));self.assertIn('Zuma',wb.active['A1'].value);wb.close()
        history=self.client.get('/api/audit/history',headers=h).json()
        self.assertFalse(any('PRIVATE-UZGERMED' in x['detail'] for x in history['items']))

    def test_74_company_membership_grant_revoke_and_forged_context(self):
        cid,h,cat,acc=self.second_company()
        cl,uh=self.make_user('operator','only_uzgermed')
        try:
            self.assertEqual([c['id'] for c in cl.get('/api/companies').json()['companies']],[self.company])
            self.assertEqual(cl.get('/api/accounts',headers={'X-Company-ID':str(cid)}).status_code,403)
            self.assertEqual(cl.get('/api/export/ledger.csv?company_id='+str(cid)).status_code,403)
            self.assertEqual(cl.get('/api/companies').status_code,200)
            self.assertFalse(any(u['username']=='only_uzgermed' for u in self.client.get('/api/users',headers=h).json()))
            self.assertEqual(self.post('/api/company-users',{'username':'only_uzgermed'},headers=h).status_code,422)
            self.assertEqual(self.post('/api/company-users',{'username':'only_uzgermed','role':'operator'},headers=h).status_code,200)
            users=self.client.get('/api/users',headers=h).json();uid=next(u['id'] for u in users if u['username']=='only_uzgermed')
            self.assertEqual(len(cl.get('/api/companies').json()['companies']),2)
            self.assertEqual(cl.get('/api/accounts',headers={'X-Company-ID':str(cid)}).json()[0]['id'],acc)
            self.assertEqual(self.client.delete('/api/company-users/'+str(uid),headers=h).status_code,200)
            self.assertEqual(cl.get('/api/accounts',headers={'X-Company-ID':str(cid)}).status_code,403)
            self.assertEqual(cl.get('/api/accounts').status_code,200)
            self.assertEqual(cl.post('/api/company-users',json={'username':'admin'},headers=uh).status_code,403)
        finally:cl.close()

    def test_75_company_catalog_budgets_policies_and_plan_payload(self):
        cid,h,cat,acc=self.second_company()
        self.budget('500','hard')
        self.assertTrue(all(b['limit'] is None for b in self.client.get('/api/budgets',headers=h).json()))
        self.assertIsNone(self.client.get('/api/approval-policy',headers=h).json()['limits']['UZS'])
        self.post('/api/reserve',{'currency':'UZS','amount':'500'})
        self.assertEqual(self.client.get('/api/dashboard',headers=h).json()['reserve'],'0.00')
        payload={'name':'Сырьё только ZUMA','type':'outcome','activity':'operating'}
        self.assertEqual(self.client.put('/api/categories/'+str(cat),json=payload,headers=h).status_code,200)
        self.assertEqual(self.client.put('/api/categories/'+str(self.cat),json=payload,headers=h).status_code,404)
        self.assertNotIn('Сырьё только ZUMA',[c['name'] for c in self.client.get('/api/bootstrap').json()['categories']])
        plan={'company_id':cid,'month':self.month,'currency':'UZS','scenario':'A','version':0,'items':[{'category_id':cat,'kind':'out','amount':'500'}],'reason':'План только этой компании'}
        self.assertEqual(self.post('/api/cash-plan',plan,headers=h).status_code,200)
        self.assertEqual(self.post('/api/cash-plan',plan).status_code,404)
        plan['version']=1;plan['items'][0]['category_id']=self.cat
        self.assertEqual(self.post('/api/cash-plan',plan,headers=h).status_code,404)
        plan_rows=self.client.get('/api/report?year='+self.date[:4],headers=h).json()['rows']
        self.assertTrue(all(r['category_id']!=self.cat for r in plan_rows))
        account={'name':'Moved','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date,'company_id':cid,'reason':'Cross company reassignment'}
        self.assertEqual(self.post('/api/accounts/'+str(self.acc),account).status_code,404)
        self.assertEqual(self.client.get('/api/accounts').json()[0]['company_id'],self.company)

    def test_76_company_import_and_count_filters(self):
        cid,h,cat,acc=self.second_company()
        text='date;kind;account_id;to_account_id;category_id;amount;counterparty;reference;note\n'+f'{self.date};in;{self.acc};;{self.cat};10;Client;IMPORT-SCOPED;test\n'
        r=self.client.post('/api/import/preview',content=text.encode(),headers={**self.h,'Content-Type':'text/csv','X-Filename':'scope.csv'})
        self.assertEqual(r.status_code,200,r.text);batch=r.json()['id']
        self.assertEqual(self.post(f'/api/import/{batch}/commit',{},headers=h).status_code,404)
        wrong=self.client.post('/api/import/preview',content=text.encode(),headers={**h,'Content-Type':'text/csv','X-Filename':'scope.csv'})
        self.assertEqual(wrong.status_code,200,wrong.text);self.assertFalse(wrong.json()['can_commit'])
        self.assertEqual(self.post(f'/api/import/{batch}/commit',{}).status_code,200)
        self.assertEqual(self.client.get('/api/ledger?paginated=true',headers=h).json()['total'],0)
        self.assertEqual(self.client.get('/api/ledger?paginated=true').json()['total'],1)
    def test_77_full_request_cycle_from_draft_to_payment_by_accountant(self):
        author=self.make_user('employee','cycle_author')
        director=self.make_user('director','cycle_director')
        book=self.make_user('accountant','cycle_book')
        row=lambda:next(x for x in self.client.get('/api/requests').json() if x['id']==rid)
        r=self.documented_request({'account_id':self.acc,'category_id':self.cat,'counterparty':'Supplier',
            'amount':'5000','date':self.date,'purpose':'Полный цикл заявки на оплату','status':'draft'},*author)
        self.assertEqual(r.status_code,200,r.text);rid=r.json()['id']
        self.assertEqual(r.json()['status'],'draft')
        stranger=self.make_user('employee','cycle_stranger')
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'submit','note':'Чужой черновик'},*stranger).status_code,403)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'submit','note':'Отправляю на согласование'},*author).status_code,200)
        self.assertEqual(row()['status'],'pending')
        back=self.post(f'/api/requests/{rid}/decision',{'action':'return','note':'Уточните сумму по договору'},*director)
        self.assertEqual(back.status_code,200,back.text);self.assertEqual(row()['status'],'returned')
        edit=author[0].put(f'/api/requests/{rid}',json={'account_id':self.acc,'category_id':self.cat,
            'counterparty':'Supplier','amount':'4000','date':self.date,'purpose':'Полный цикл заявки на оплату',
            'version':row()['version'],'reason':'Сумма уточнена по договору'},headers=author[1])
        self.assertEqual(edit.status_code,200,edit.text)
        self.assertEqual(row()['status'],'pending');self.assertIsNone(row()['finance_approved_by'])
        self.assertEqual(self.finance_approve(rid).status_code,200)
        self.assertEqual(row()['status'],'pending');self.assertEqual(row()['approval_stage'],'director')
        self.assertEqual(self.finance_approve(rid).status_code,403)
        ok=self.post(f'/api/requests/{rid}/decision',{'action':'approve','note':'Подтверждаю оплату по договору'},*director)
        self.assertEqual(ok.status_code,200,ok.text);self.assertEqual(row()['status'],'approved')
        part=self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'kind':'out','amount':'1000',
            'date':self.date,'reference':'CYCLE-PART','note':'Частичная оплата заявки','request_id':rid},*book)
        self.assertEqual(part.status_code,409,part.text)
        pay=self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'kind':'out','amount':'4000',
            'date':self.date,'reference':'CYCLE-PAY','note':'Оплата утверждённой заявки','request_id':rid},*book)
        self.assertEqual(pay.status_code,200,pay.text);self.assertEqual(row()['status'],'paid')
        again=self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'kind':'out','amount':'4000',
            'date':self.date,'reference':'CYCLE-PAY-2','note':'Повторная оплата заявки','request_id':rid},*book)
        self.assertEqual(again.status_code,409,again.text)
        actions=' | '.join(a['action'] for a in self.client.get('/api/audit').json())
        for expected in ('Создана заявка','Изменена заявка','Действие по заявке: return','Фактическая операция','Оплата заявки'):
            self.assertIn(expected,actions)

    def test_78_rejected_returned_and_cancelled_requests_are_never_paid(self):
        author=self.make_user('employee','stop_author')
        director=self.make_user('director','stop_director')
        book=self.make_user('accountant','stop_book')
        def pay(rid,reference):
            return self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'kind':'out','amount':'5000',
                'date':self.date,'reference':reference,'note':'Оплата по заявке','request_id':rid},*book)
        state=lambda i:next(x for x in self.client.get('/api/requests').json() if x['id']==i)
        rid=self.request('5000',client=author[0],headers=author[1]).json()['id']
        self.assertEqual(self.finance_approve(rid).status_code,200)
        rej=self.post(f'/api/requests/{rid}/decision',{'action':'reject','note':'Нет договора с поставщиком'},*director)
        self.assertEqual(rej.status_code,422,rej.text);self.assertIn('Верните заявку на доработку',rej.text)
        self.assertEqual(state(rid)['status'],'pending')
        # A record rejected before the change stays readable and cannot be paid or revived.
        with unit(True) as s:
            legacy=s.get(PaymentRequest,rid);legacy.status='rejected';legacy.finance_approved_by=None
        self.assertEqual(pay(rid,'STOP-REJECTED').status_code,409)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'approve','note':'Попытка согласовать отклонённую'},*director).status_code,409)
        returned=self.request('5000',client=author[0],headers=author[1]).json()['id']
        self.assertEqual(self.post(f'/api/requests/{returned}/decision',{'action':'return','note':'Вернуть на доработку'},*director).status_code,200)
        self.assertEqual(state(returned)['status'],'returned')
        self.assertEqual(pay(returned,'STOP-RETURNED').status_code,409)
        cancelled=self.request('5000',client=author[0],headers=author[1]).json()['id']
        self.assertEqual(self.post(f'/api/requests/{cancelled}/decision',{'action':'cancel','note':'Закупка отменена'},*author).status_code,200)
        self.assertEqual(state(cancelled)['status'],'cancelled')
        self.assertEqual(pay(cancelled,'STOP-CANCELLED').status_code,409)
        self.assertEqual(self.client.get('/api/accounts').json()[0]['balance'],'1000000.00')

    def test_79_accountant_pays_approved_requests_only(self):
        book=self.make_user('accountant','pay_book')
        author=self.make_user('employee','pay_author')
        rid=self.request('600',client=author[0],headers=author[1]).json()['id']
        early=self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'kind':'out','amount':'600',
            'date':self.date,'reference':'BOOK-EARLY','note':'Оплата до согласования','request_id':rid},*book)
        self.assertEqual(early.status_code,409,early.text)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'approve','note':'Бухгалтер согласует сам'},*book).status_code,403)
        self.assertEqual(self.request('600',client=book[0],headers=book[1]).status_code,403)
        pending=self.request('700',client=author[0],headers=author[1]).json()['id']
        self.assertEqual(self.approve(rid).status_code,200)
        visible=book[0].get('/api/requests',headers=book[1])
        self.assertEqual(visible.status_code,200,visible.text)
        self.assertTrue(any(x['id']==rid for x in visible.json()))
        self.assertFalse(any(x['id']==pending for x in visible.json()))
        self.assertTrue(all(x['budget']['hidden'] for x in visible.json()))
        wrong_amount=self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'kind':'out','amount':'500',
            'date':self.date,'reference':'BOOK-PARTIAL','note':'Неполная оплата','request_id':rid},*book)
        self.assertEqual(wrong_amount.status_code,409,wrong_amount.text)
        pay=self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'kind':'out','amount':'600',
            'date':self.date,'reference':'BOOK-PAY','note':'Оплата утверждённой заявки','request_id':rid},*book)
        self.assertEqual(pay.status_code,200,pay.text)
        own=book[0].post(f"/api/ledger/{pay.json()['id']}/document",content=b'%PDF-1.4\nsynthetic',
            headers={**book[1],'Content-Type':'application/pdf','X-Filename':'payment.pdf'})
        self.assertEqual(own.status_code,200,own.text)
        foreign=self.ledger(reference='BOOK-FOREIGN').json()['id']
        alien=book[0].post(f'/api/ledger/{foreign}/document',content=b'%PDF-1.4\nsynthetic',
            headers={**book[1],'Content-Type':'application/pdf','X-Filename':'alien.pdf'})
        self.assertEqual(alien.status_code,403,alien.text)
        entries={x['id']:x for x in book[0].get('/api/ledger',headers=book[1]).json()}
        self.assertTrue(entries[pay.json()['id']]['can_attach_document'])
        self.assertFalse(entries[foreign]['can_attach_document'])
        other_book=self.make_user('accountant','pay_other_book')
        entries=other_book[0].get('/api/ledger?paginated=true',headers=other_book[1]).json()['items']
        self.assertTrue(entries)
        self.assertTrue(all(not x['can_attach_document'] for x in entries))
        viewer=self.make_user('auditor','pay_doc_viewer')
        self.assertTrue(all(not x['can_attach_document'] for x in viewer[0].get('/api/ledger',headers=viewer[1]).json()))
        self.assertTrue(all(x['can_attach_document'] for x in self.client.get('/api/ledger').json()))
        for body in ({'kind':'out','amount':'10','reference':'BOOK-FREE','note':'Расход без заявки от бухгалтера'},
                     {'kind':'in','amount':'10','reference':'BOOK-IN','note':'Поступление от бухгалтера'}):
            denied=self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'date':self.date,**body},*book)
            self.assertEqual(denied.status_code,403,denied.text)
        second=self.post('/api/accounts',{'name':'Второй счёт','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date})
        transfer=self.post('/api/ledger',{'account_id':self.acc,'to_account_id':second.json()['id'],'kind':'transfer',
            'amount':'10','date':self.date,'reference':'BOOK-TRANSFER','note':'Перевод от бухгалтера'},*book)
        self.assertEqual(transfer.status_code,403,transfer.text)
        self.assertEqual(self.post('/api/accounts',{'name':'Счёт бухгалтера','kind':'bank','currency':'UZS',
            'opening':'0','opening_date':self.date},*book).status_code,403)
        self.assertEqual(self.post('/api/budgets',{'category_id':self.cat,'month':self.month,'currency':'UZS',
            'amount':'100','mode':'soft','reason':'Бухгалтер задаёт лимит'},*book).status_code,403)
        self.assertEqual(self.post(f'/api/ledger/{foreign}/reverse',{'reason':'Сторно от бухгалтера'},*book).status_code,403)
        for path in ('/api/users','/api/dashboard','/api/accounts','/api/budgets','/api/approval-policy','/api/audit'):
            self.assertEqual(book[0].get(path,headers=book[1]).status_code,403,path)

    def test_80_service_company_is_admin_only_on_every_context_path(self):
        with unit() as s:service=s.scalar(select(Company.id).where(Company.code=='UNASSIGNED'))
        cl,h=self.make_user('operator','legacy_operator')
        uid=next(u['id'] for u in self.client.get('/api/users').json() if u['username']=='legacy_operator')
        # Прежняя связь из миграции сохраняется в базе и не даёт доступа.
        with unit(True) as s:
            if not s.get(CompanyUser,(service,uid)):s.add(CompanyUser(company_id=service,user_id=uid))
        header={**h,'X-Company-ID':str(service)}
        self.assertNotIn('UNASSIGNED',[c['code'] for c in cl.get('/api/companies').json()['companies']])
        self.assertNotIn('UNASSIGNED',[c['code'] for c in cl.get('/api/bootstrap').json()['companies']])
        for path in ('/api/accounts','/api/ledger','/api/requests','/api/budgets','/api/report',
                     '/api/export/ledger.csv','/api/export/report.xlsx','/api/model','/api/receipts'):
            self.assertEqual(cl.get(path,headers=header).status_code,403,'header '+path)
            joiner='&' if '?' in path else '?'
            self.assertEqual(cl.get(f'{path}{joiner}company_id={service}').status_code,403,'query '+path)
        self.assertEqual(cl.get(f'/api/documents?ledger_id=1',headers=header).status_code,403)
        self.assertEqual(self.post('/api/accounts',{'name':'Служебный счёт','kind':'bank','currency':'UZS',
            'opening':'0','opening_date':self.date},cl,header).status_code,403)
        self.assertEqual(self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'kind':'in',
            'amount':'10','date':self.date,'reference':'SERVICE-1','note':'Запись в служебное пространство'},cl,header).status_code,403)
        self.assertEqual(self.post('/api/cash-plan',{'month':self.month,'currency':'UZS','scenario':'A','version':0,
            'items':[],'reason':'План в служебном пространстве'},cl,header).status_code,403)
        # Администратор продолжает работать с историей, связь не удалена.
        self.assertEqual(self.client.get('/api/accounts',headers={**self.h,'X-Company-ID':str(service)}).status_code,200)
        self.assertIn('UNASSIGNED',[c['code'] for c in self.client.get('/api/companies').json()['companies']])
        with unit() as s:self.assertIsNotNone(s.get(CompanyUser,(service,uid)))
        cl.close()

    def test_81_concurrent_payment_and_approval_use_independent_connections(self):
        import threading
        author=self.make_user('employee','race_author')
        rid=self.request('600',client=author[0],headers=author[1]).json()['id']
        self.assertEqual(self.approve(rid).status_code,200)
        version=next(x for x in self.client.get('/api/requests').json() if x['id']==rid)['version']
        payers=[self.make_user('accountant','race_book1'),self.make_user('accountant','race_book2')]
        start=threading.Barrier(2);results=[]
        def pay(index):
            client,headers=payers[index]
            body={'account_id':self.acc,'category_id':self.cat,'kind':'out','amount':'600','date':self.date,
                  'reference':f'RACE-PAY-{index}','note':'Одновременная оплата заявки','request_id':rid,
                  'request_version':version}
            start.wait()
            results.append(client.post('/api/ledger',json=body,headers=headers).status_code)
        threads=[threading.Thread(target=pay,args=(i,)) for i in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sorted(results).count(200),1,results)
        self.assertEqual(len([x for x in results if x!=200]),1,results)
        entries=[t for t in self.client.get('/api/ledger').json() if t['request_id']==rid]
        self.assertEqual(len(entries),1,entries)
        self.assertEqual(self.client.get('/api/accounts').json()[0]['balance'],'999400.00')
        # Два согласования, конкурирующих за один остаток на одну дату.
        small=self.post('/api/accounts',{'name':'Гонка остатка','kind':'bank','currency':'UZS','opening':'1000',
            'opening_date':self.date}).json()['id']
        ids=[self.post('/api/requests',{'account_id':small,'category_id':self.cat,'counterparty':'Supplier',
            'amount':'600','date':self.date,'purpose':'Заявка на один и тот же остаток'},*author).json()['id'] for _ in range(2)]
        for i in ids:self.assertEqual(self.finance_approve(i).status_code,200)
        versions={x['id']:x['version'] for x in self.client.get('/api/requests').json()}
        approvers=[self.make_user('director','race_dir1'),self.make_user('director','race_dir2')]
        gate=threading.Barrier(2);codes=[]
        def decide(index):
            client,headers=approvers[index]
            body={'action':'approve','note':'Одновременное согласование заявки','version':versions[ids[index]]}
            gate.wait()
            codes.append(client.post(f'/api/requests/{ids[index]}/decision',json=body,headers=headers).status_code)
        threads=[threading.Thread(target=decide,args=(i,)) for i in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(codes.count(200),1,codes)
        self.assertEqual(codes.count(409),1,codes)
        approved=[x for x in self.client.get('/api/requests').json() if x['id'] in ids and x['status']=='approved']
        self.assertEqual(len(approved),1,approved)

    def test_84_accountant_cannot_pay_foreign_company_or_stale_version(self):
        book=self.make_user('accountant','scope_book')
        author=self.make_user('employee','scope_author')
        cid,h,cat,acc=self.second_company()
        other=self.make_user('finance','scope_finance')
        self.assertEqual(self.post('/api/company-users',{'username':'scope_finance','role':'finance'},headers=h).status_code,200)
        boss=self.make_user('director','scope_director')
        self.assertEqual(self.post('/api/company-users',{'username':'scope_director','role':'director'},headers=h).status_code,200)
        foreign=self.post('/api/requests',{'account_id':acc,'category_id':cat,'counterparty':'Supplier',
            'amount':'100','date':self.date,'purpose':'Заявка другой компании'},headers=h)
        self.assertEqual(foreign.status_code,200,foreign.text)
        fid=foreign.json()['id']
        checked=self.post(f'/api/requests/{fid}/decision',{'action':'approve','version':foreign.json()['version'],
            'note':'Согласование в другой компании'},other[0],{**other[1],'X-Company-ID':str(cid)})
        self.assertEqual(checked.status_code,200,checked.text)
        approved=self.post(f'/api/requests/{fid}/decision',{'action':'approve','version':checked.json()['version'],
            'note':'Утверждение директором компании'},boss[0],{**boss[1],'X-Company-ID':str(cid)})
        self.assertEqual(approved.status_code,200,approved.text)
        self.assertEqual(approved.json()['status'],'approved')
        blind=self.post('/api/ledger',{'account_id':acc,'category_id':cat,'kind':'out','amount':'100',
            'date':self.date,'reference':'SCOPE-FOREIGN','note':'Оплата чужой компании','request_id':fid,
            'request_version':2},*book)
        self.assertEqual(blind.status_code,404,blind.text)
        forged=book[0].post('/api/ledger',json={'account_id':acc,'category_id':cat,'kind':'out','amount':'100',
            'date':self.date,'reference':'SCOPE-FORGED','note':'Оплата с чужим контекстом','request_id':fid,
            'request_version':2},headers={**book[1],'X-Company-ID':str(cid)})
        self.assertEqual(forged.status_code,403,forged.text)
        self.assertFalse(any(x['id']==fid for x in book[0].get('/api/requests',headers=book[1]).json()))
        rid=self.request('600',client=author[0],headers=author[1]).json()['id']
        current=self.request('600',client=author[0],headers=author[1]).json()['version']
        self.assertEqual(self.approve(rid).status_code,200)
        stale=self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'kind':'out','amount':'600',
            'date':self.date,'reference':'SCOPE-STALE','note':'Оплата по устаревшей версии','request_id':rid,
            'request_version':current},*book)
        self.assertEqual(stale.status_code,409,stale.text)
        fresh=self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'kind':'out','amount':'600',
            'date':self.date,'reference':'SCOPE-FRESH','note':'Оплата по актуальной версии','request_id':rid},*book)
        self.assertEqual(fresh.status_code,200,fresh.text)

    # ---- Защитный PR: права оплаты, разделение обязанностей, неизменность заявки при оплате.
    def pay_body(self,rid,**extra):
        body={'account_id':self.acc,'category_id':self.cat,'kind':'out','amount':'600','date':self.date,
              'reference':'PAY-'+str(rid)+'-'+str(len(extra)),'note':'Оплата утверждённой заявки','request_id':rid}
        body.update(extra);return body
    def cash_account(self,opening='500000'):
        r=self.post('/api/accounts',{'name':'Касса офиса','kind':'cash','currency':'UZS','opening':opening,'opening_date':str(today()-timedelta(days=10))})
        self.assertEqual(r.status_code,200,r.text);return r.json()['id']

    def test_85_only_the_channel_payer_outside_the_request_can_pay(self):
        from app import security
        author=self.make_user('employee','sod_author');cashier=self.make_user('cashier','sod_cashier')
        operator=self.make_user('operator','sod_operator');book=self.make_user('accountant','sod_book')
        rid=self.request('600',client=author[0],headers=author[1]).json()['id']
        self.assertEqual(self.approve(rid).status_code,200)
        # Author, admin, finance approver, director approver and a 'write' operator have no payment right.
        for who in (author,(self.client,self.h),self.approver,self.director,operator):
            r=self.post('/api/ledger',self.pay_body(rid),*who);self.assertEqual(r.status_code,403,r.text)
        # The cashier cannot pay from a bank account.
        r=self.post('/api/ledger',self.pay_body(rid),*cashier);self.assertEqual(r.status_code,403,r.text)
        self.assertIn('расчётный бухгалтер',r.text)
        # Even with a payment right, participants of the request are refused.
        saved={role:set(security.PERMS[role]) for role in ('finance','director','admin')}
        try:
            for role in saved:security.PERMS[role].add('pay_bank')
            for who in (self.approver,self.director):
                r=self.post('/api/ledger',self.pay_body(rid),*who);self.assertEqual(r.status_code,403,r.text)
                self.assertIn('согласующие',r.text)
            edited=self.request('700',client=author[0],headers=author[1]).json()
            data={k:edited[k] for k in ('account_id','category_id','counterparty','amount','date','purpose','version')}
            data.update(status='pending',reason='Правка администратором перед оплатой')
            self.assertEqual(self.client.put(f"/api/requests/{edited['id']}",json=data,headers=self.h).status_code,200)
            self.assertEqual(self.approve(edited['id']).status_code,200)
            r=self.post('/api/ledger',self.pay_body(edited['id'],amount='700'));self.assertEqual(r.status_code,403,r.text)
        finally:
            for role,perms in saved.items():security.PERMS[role].clear();security.PERMS[role].update(perms)
        self.assertEqual(self.post('/api/ledger',self.pay_body(rid),*book).status_code,200)

    def test_86_cash_requests_are_paid_by_a_cashier_who_is_not_their_author(self):
        till=self.cash_account()
        author=self.make_user('employee','cash_author');cashier=self.make_user('cashier','cash_payer')
        book=self.make_user('accountant','cash_book')
        rid=self.post('/api/requests',{'account_id':till,'category_id':self.cat,'counterparty':'Курьер','amount':'300',
            'date':self.date,'purpose':'Выдача наличных курьеру'},*author).json()['id']
        own=self.post('/api/requests',{'account_id':till,'category_id':self.cat,'counterparty':'Курьер','amount':'200',
            'date':self.date,'purpose':'Собственная кассовая заявка'},*cashier).json()['id']
        for i in (rid,own):self.assertEqual(self.approve(i).status_code,200)
        listed=[x['id'] for x in cashier[0].get('/api/requests',headers=cashier[1]).json()]
        self.assertIn(rid,listed);self.assertIn(own,listed)
        self.assertNotIn(rid,[x['id'] for x in book[0].get('/api/requests',headers=book[1]).json()])
        body=lambda i,n:{'account_id':till,'category_id':self.cat,'kind':'out','amount':n,'date':self.date,
                         'reference':f'CASH-{i}','note':'Выдача из кассы','request_id':i}
        r=self.post('/api/ledger',body(rid,'300'),*book);self.assertEqual(r.status_code,403,r.text);self.assertIn('кассир',r.text)
        r=self.post('/api/ledger',body(own,'200'),*cashier);self.assertEqual(r.status_code,403,r.text)
        r=self.post('/api/ledger',body(rid,'300'),*cashier);self.assertEqual(r.status_code,200,r.text)
        entry=next(x for x in self.client.get('/api/ledger').json() if x['request_id']==rid)
        self.assertEqual(entry['counterparty'],'Курьер')
        # The cashier still cannot record anything but a request payment.
        free={'account_id':till,'category_id':self.cat,'kind':'out','amount':'1','date':self.date,'reference':'CASH-FREE','note':'Выдача без заявки из кассы'}
        self.assertEqual(self.post('/api/ledger',free,*cashier).status_code,403)

    def test_87_payment_takes_every_value_from_the_approved_request(self):
        book=self.make_user('accountant','exact_book')
        other=self.post('/api/accounts',{'name':'Другой банк','kind':'bank','currency':'UZS','opening':'1000000','opening_date':str(today()-timedelta(days=10))}).json()['id']
        usd=self.post('/api/accounts',{'name':'Банк USD','kind':'bank','currency':'USD','opening':'1000000','opening_date':str(today()-timedelta(days=10))}).json()['id']
        other_cat=next(c['id'] for c in self.client.get('/api/bootstrap').json()['categories'] if c['id']!=self.cat and c['type']=='outcome')
        rid=self.request('600').json()['id'];self.assertEqual(self.approve(rid).status_code,200)
        for change in ({'counterparty':'Совсем другой получатель'},{'amount':'500'},{'amount':'700'},{'account_id':other},
                       {'account_id':usd},{'category_id':other_cat},{'to_account_id':other}):
            r=self.post('/api/ledger',self.pay_body(rid,**change),*book);self.assertEqual(r.status_code,409,(change,r.text))
        self.assertIn(self.post('/api/ledger',self.pay_body(rid,kind='in'),*book).status_code,(403,409))
        yesterday=str(today()-timedelta(days=1))
        r=self.post('/api/ledger',self.pay_body(rid,date=yesterday),*book);self.assertEqual(r.status_code,422,r.text)
        self.assertIn('раньше утверждения',r.text)
        self.assertEqual(self.post('/api/ledger',self.pay_body(rid,counterparty='Supplier'),*book).status_code,200)
        entry=next(x for x in self.client.get('/api/ledger').json() if x['request_id']==rid)
        self.assertEqual((entry['counterparty'],entry['amount'],entry['account_id']),('Supplier','600.00',self.acc))
        audit=[a for a in self.client.get('/api/audit').json() if a['action']=='Оплата заявки']
        self.assertTrue(audit)

    def test_88_budget_or_funds_short_after_approval_go_back_to_finance(self):
        book=self.make_user('accountant','short_book')
        state=lambda i:next(x for x in self.client.get('/api/requests').json() if x['id']==i)
        self.budget('1000','soft');rid=self.request('600').json()['id'];self.assertEqual(self.approve(rid).status_code,200)
        self.budget('500','soft')
        long_note='Очень подробное обоснование от плательщика, чтобы обойти лимит'
        r=self.post('/api/ledger',self.pay_body(rid,note=long_note),*book)
        self.assertEqual(r.status_code,409,r.text);self.assertIn('финансовому директору',r.text)
        self.assertEqual(state(rid)['status'],'approved')
        # Finance cannot use the payer's action; the payer returns the request with a reason.
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'return_finance','note':'Лимит статьи снижен после утверждения'},*self.approver).status_code,403)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'return_finance','note':'коротко'},*book).status_code,422)
        back=self.post(f'/api/requests/{rid}/decision',{'action':'return_finance','note':'Лимит статьи снижен после утверждения'},*book)
        self.assertEqual(back.status_code,200,back.text)
        row=state(rid);self.assertEqual((row['status'],row['approval_stage']),('pending','finance'))
        self.assertIsNone(row['approved_by']);self.assertIsNone(row['approved_on'])
        b=next(b for b in self.client.get('/api/budgets').json() if b['category_id']==self.cat);self.assertEqual(b['reserved'],'0.00')
        # Approvers accept the soft overrun explicitly; the payer may pay exactly within it.
        self.assertEqual(self.approve(rid,note='').status_code,409)
        self.assertEqual(self.approve(rid).status_code,200)
        self.assertEqual(self.post('/api/ledger',self.pay_body(rid),*book).status_code,200)
        # A hard budget lowered after approval blocks payment whatever the comment.
        hard=self.post('/api/categories',{'name':'Жёсткая статья','type':'outcome','activity':'operating'})
        cat=hard.json()['id'] if hard.status_code==200 else self.cat
        self.post('/api/budgets',{'category_id':cat,'month':self.month,'currency':'UZS','amount':'1000','mode':'hard','reason':'Жёсткий лимит для проверки','source':'test'})
        second=self.post('/api/requests',{'account_id':self.acc,'category_id':cat,'counterparty':'Supplier','amount':'400','date':self.date,'purpose':'Заявка под жёсткий лимит'}).json()['id']
        self.assertEqual(self.approve(second).status_code,200)
        self.post('/api/budgets',{'category_id':cat,'month':self.month,'currency':'UZS','amount':'100','mode':'hard','reason':'Лимит снижен после утверждения','source':'test'})
        r=self.post('/api/ledger',self.pay_body(second,category_id=cat,amount='400',note=long_note),*book)
        self.assertEqual(r.status_code,409,r.text)
        # Money that disappeared after approval also stops the payment with the same hint.
        small=self.post('/api/accounts',{'name':'Малый счёт','kind':'bank','currency':'UZS','opening':'1000','opening_date':str(today()-timedelta(days=10))}).json()['id']
        third=self.post('/api/requests',{'account_id':small,'category_id':self.cat,'counterparty':'Supplier','amount':'800','date':self.date,'purpose':'Заявка на малый остаток'}).json()['id']
        self.assertEqual(self.approve(third).status_code,200)
        edit={'name':'Малый счёт','kind':'bank','currency':'UZS','opening':'700','opening_date':str(today()-timedelta(days=10)),'allow_overdraft':False,'reason':'Уточнён начальный остаток'}
        self.assertEqual(self.post(f'/api/accounts/{small}',edit).status_code,200)
        r=self.post('/api/ledger',self.pay_body(third,account_id=small,amount='800',note=long_note),*book)
        self.assertEqual(r.status_code,409,r.text);self.assertIn('Недостаточно доступных средств',r.text);self.assertIn('финансовому директору',r.text)

    def test_89_reject_is_gone_and_old_rejected_requests_are_read_only(self):
        rid=self.request('600').json()['id'];self.assertEqual(self.finance_approve(rid).status_code,200)
        if not hasattr(self,'director'):self.director=self.make_user('director','chief')
        for who in (self.approver,self.director):
            r=self.post(f'/api/requests/{rid}/decision',{'action':'reject','note':'Нет договора с поставщиком'},*who)
            self.assertEqual(r.status_code,422,r.text)
        with unit(True) as s:
            old=s.get(PaymentRequest,rid);old.status='rejected';old.finance_approved_by=None
        listed=self.client.get('/api/requests?state=rejected').json()
        self.assertEqual([x['id'] for x in listed],[rid])
        row=listed[0]
        data={k:row[k] for k in ('account_id','category_id','counterparty','amount','date','purpose','version')}
        data.update(status='pending',reason='Попытка вернуть отклонённую заявку')
        self.assertEqual(self.client.put(f'/api/requests/{rid}',json=data,headers=self.h).status_code,409)
        for action in ('cancel','submit','approve','return_finance'):
            r=self.post(f'/api/requests/{rid}/decision',{'action':action,'note':'Действие над отклонённой заявкой'})
            self.assertIn(r.status_code,(403,409),(action,r.text))
        self.assertEqual(self.ledger('600','out',request_id=rid).status_code,409)
        self.assertEqual(self.client.get('/api/requests?state=rejected').json()[0]['status'],'rejected')
        self.assertEqual(self.client.get('/api/dashboard').status_code,200)
        self.assertEqual(self.client.get(f'/api/report?year={self.date[:4]}').status_code,200)

    def test_90_account_type_is_frozen_once_used(self):
        self.request('10')
        r=self.edit_account(kind='cash');self.assertEqual(r.status_code,409,r.text);self.assertIn('Тип счёта',r.text)
        fresh=self.post('/api/accounts',{'name':'Новый счёт','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date}).json()['id']
        edit={'name':'Новая касса','kind':'cash','currency':'UZS','opening':'0','opening_date':self.date,'allow_overdraft':False,'reason':'Ошибка при создании счёта'}
        self.assertEqual(self.post(f'/api/accounts/{fresh}',edit).status_code,200)

    def test_91_payment_columns_are_added_to_an_existing_database(self):
        from sqlalchemy import inspect as sa_inspect,text
        rid=self.request('600').json()['id'];self.assertEqual(self.approve(rid).status_code,200)
        with engine.begin() as conn:
            for column in ('approved_at','approved_overrun'):conn.execute(text(f'ALTER TABLE payment_requests DROP COLUMN {column}'))
        initialize();initialize()
        with engine.connect() as conn:
            columns={c['name'] for c in sa_inspect(conn).get_columns('payment_requests')}
        self.assertTrue({'approved_at','approved_overrun'}<=columns)
        row=next(x for x in self.client.get('/api/requests').json() if x['id']==rid)
        self.assertEqual((row['status'],row['amount']),('approved','600.00'));self.assertIsNone(row['approved_on'])
        # An approval without a recorded time falls back to the request's creation date.
        self.assertEqual(self.ledger('600','out',request_id=rid).status_code,200)

    def test_92_return_to_finance_is_the_payers_action_for_their_channel(self):
        till=self.cash_account();cashier=self.make_user('cashier','ret_cashier');book=self.make_user('accountant','ret_book')
        bank=self.request('100').json()['id']
        cash=self.post('/api/requests',{'account_id':till,'category_id':self.cat,'counterparty':'Курьер','amount':'100','date':self.date,'purpose':'Кассовая заявка на возврат'}).json()['id']
        for i in (bank,cash):self.assertEqual(self.approve(i).status_code,200)
        note={'action':'return_finance','note':'Не хватает денег на дату оплаты'}
        self.assertEqual(self.post(f'/api/requests/{bank}/decision',note,*cashier).status_code,403)
        self.assertEqual(self.post(f'/api/requests/{cash}/decision',note,*book).status_code,403)
        self.assertEqual(self.post(f'/api/requests/{cash}/decision',note,*cashier).status_code,200)
        self.assertEqual(self.post(f'/api/requests/{bank}/decision',note,*book).status_code,200)
        actions=[a['action'] for a in self.client.get('/api/audit').json()]
        self.assertIn('Действие по заявке: return_finance',actions)

    def test_93_backdated_payment_cannot_take_money_reserved_for_a_due_request(self):
        book=self.make_user('accountant','late_book')
        acc=self.post('/api/accounts',{'name':'Счёт с резервом','kind':'bank','currency':'UZS','opening':'1000','opening_date':str(today()-timedelta(days=20))}).json()['id']
        body=lambda n,d,p:{'account_id':acc,'category_id':self.cat,'counterparty':'Supplier','amount':n,'date':d,'purpose':p}
        x=self.post('/api/requests',body('400',str(today()-timedelta(days=5)),'Просроченная утверждённая заявка')).json()['id']
        y=self.post('/api/requests',body('600',self.date,'Заявка на сегодня')).json()['id']
        for i in (x,y):self.assertEqual(self.approve(i).status_code,200)
        with unit(True) as s:
            for i in (x,y):s.get(PaymentRequest,i).approved_at=now()-timedelta(days=10)
        # A manual expense can no longer take the reservation, so the shortfall comes from a corrected opening balance.
        spend={'account_id':acc,'category_id':self.cat,'kind':'out','amount':'400','date':str(today()-timedelta(days=1)),'reference':'LATE-OUT','note':'Расход без заявки задним числом'}
        self.assertEqual(self.post('/api/ledger',spend).status_code,409)
        edit={'name':'Счёт с резервом','kind':'bank','currency':'UZS','opening':'600','opening_date':str(today()-timedelta(days=20)),'allow_overdraft':False,'reason':'Уточнён начальный остаток счёта'}
        self.assertEqual(self.post(f'/api/accounts/{acc}',edit).status_code,200)
        pay=lambda d,ref:self.post('/api/ledger',{'account_id':acc,'category_id':self.cat,'kind':'out','amount':'600','date':d,'reference':ref,'request_id':y},*book)
        self.assertEqual(pay(self.date,'LATE-TODAY').status_code,409)
        r=pay(str(today()-timedelta(days=6)),'LATE-BACK');self.assertEqual(r.status_code,409,r.text);self.assertIn('финансовому директору',r.text)

    def test_94_finance_only_approvals_from_the_old_threshold_are_not_payable(self):
        book=self.make_user('accountant','legacy_book')
        rid=self.request('600').json()['id'];self.assertEqual(self.finance_approve(rid).status_code,200)
        with unit(True) as s:
            legacy=s.get(PaymentRequest,rid);legacy.status='approved';legacy.approved_by=legacy.finance_approved_by;legacy.approved_at=None
        r=self.post('/api/ledger',self.pay_body(rid),*book);self.assertEqual(r.status_code,409,r.text);self.assertIn('директора',r.text)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'return_finance','note':'Нужно утверждение директора'},*book).status_code,200)
        self.assertEqual(self.approve(rid).status_code,200)
        self.assertEqual(self.post('/api/ledger',self.pay_body(rid),*book).status_code,200)

    def test_95_soft_overrun_accepted_for_the_month_keeps_earlier_approvals_payable(self):
        book=self.make_user('accountant','overrun_book')
        self.budget('1000','soft')
        a=self.request('500').json()['id'];self.assertEqual(self.approve(a).status_code,200)
        b=self.request('700').json()['id'];self.assertEqual(self.approve(b).status_code,200)
        self.assertEqual(self.post('/api/ledger',self.pay_body(a,amount='500'),*book).status_code,200)
        self.assertEqual(self.post('/api/ledger',self.pay_body(b,amount='700'),*book).status_code,200)
        # A further cut of the limit is beyond what the approvers accepted.
        c=self.request('100').json()['id'];self.assertEqual(self.approve(c).status_code,200)
        self.budget('900','soft')
        r=self.post('/api/ledger',self.pay_body(c,amount='100'),*book);self.assertEqual(r.status_code,409,r.text)

    def test_96_overdue_request_paid_in_a_later_month_is_rescheduled_not_looped(self):
        book=self.make_user('accountant','month_book')
        acc=self.post('/api/accounts',{'name':'Долгий счёт','kind':'bank','currency':'UZS','opening':'100000','opening_date':str(today()-timedelta(days=62))}).json()['id']
        due=today().replace(day=1)-timedelta(days=5)
        rid=self.post('/api/requests',{'account_id':acc,'category_id':self.cat,'counterparty':'Supplier','amount':'100','date':str(due),'purpose':'Заявка прошлого месяца'}).json()['id']
        self.assertEqual(self.approve(rid).status_code,200)
        self.budget('50','soft')
        body=lambda ref:{'account_id':acc,'category_id':self.cat,'kind':'out','amount':'100','date':self.date,'reference':ref,'request_id':rid}
        r=self.post('/api/ledger',body('MONTH-1'),*book);self.assertEqual(r.status_code,409,r.text);self.assertIn('перенести срок',r.text)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'return_finance','note':'Бюджет месяца оплаты превышен'},*book).status_code,200)
        moved=self.post(f'/api/requests/{rid}/decision',{'action':'reschedule','date':self.date,'note':'Срок переносится на месяц оплаты'})
        self.assertEqual(moved.status_code,200,moved.text)
        self.assertEqual(self.approve(rid).status_code,200)
        self.assertEqual(self.post('/api/ledger',body('MONTH-2'),*book).status_code,200)

    def test_97_early_payment_cannot_take_money_reserved_for_an_earlier_due_request(self):
        book=self.make_user('accountant','early_book')
        acc=self.post('/api/accounts',{'name':'Счёт очередности','kind':'bank','currency':'UZS','opening':'150','opening_date':str(today()-timedelta(days=5))}).json()['id']
        body=lambda n,d,p:{'account_id':acc,'category_id':self.cat,'counterparty':'Supplier','amount':n,'date':d,'purpose':p}
        late=self.post('/api/requests',body('100',str(today()+timedelta(days=10)),'Заявка с поздним сроком')).json()['id']
        soon=self.post('/api/requests',body('100',str(today()+timedelta(days=5)),'Заявка с ранним сроком')).json()['id']
        for i in (late,soon):self.assertEqual(self.approve(i).status_code,200)
        pay=lambda rid,ref:self.post('/api/ledger',{'account_id':acc,'category_id':self.cat,'kind':'out','amount':'100','date':self.date,'reference':ref,'request_id':rid},*book)
        r=pay(late,'EARLY-LATE');self.assertEqual(r.status_code,409,r.text);self.assertIn('финансовому директору',r.text)
        self.assertEqual(pay(soon,'EARLY-SOON').status_code,200)

    def test_98_another_months_accepted_overrun_is_not_borrowed(self):
        book=self.make_user('accountant','borrow_book')
        acc=self.post('/api/accounts',{'name':'Счёт двух месяцев','kind':'bank','currency':'UZS','opening':'100000','opening_date':str(today()-timedelta(days=62))}).json()['id']
        self.budget('100','soft')
        big=self.request('150').json()['id'];self.assertEqual(self.approve(big).status_code,200)
        prev=today().replace(day=1)-timedelta(days=5)
        late=self.post('/api/requests',{'account_id':acc,'category_id':self.cat,'counterparty':'Supplier','amount':'10','date':str(prev),'purpose':'Заявка прошлого месяца'}).json()['id']
        self.assertEqual(self.approve(late).status_code,200)
        r=self.post('/api/ledger',{'account_id':acc,'category_id':self.cat,'kind':'out','amount':'10','date':self.date,'reference':'BORROW','request_id':late},*book)
        self.assertEqual(r.status_code,409,r.text);self.assertIn('перенести срок',r.text)
        self.assertEqual(self.post('/api/ledger',self.pay_body(big,amount='150'),*book).status_code,200)

    def test_99_manual_expenses_and_transfers_keep_due_reservations(self):
        acc=self.post('/api/accounts',{'name':'Счёт с резервом X','kind':'bank','currency':'UZS','opening':'1000','opening_date':str(today()-timedelta(days=20))}).json()['id']
        other=self.post('/api/accounts',{'name':'Второй счёт X','kind':'bank','currency':'UZS','opening':'0','opening_date':str(today()-timedelta(days=20))}).json()['id']
        rid=self.post('/api/requests',{'account_id':acc,'category_id':self.cat,'counterparty':'Supplier','amount':'1000','date':str(today()-timedelta(days=2)),'purpose':'Просроченная утверждённая заявка'}).json()['id']
        self.assertEqual(self.approve(rid).status_code,200)
        spend=lambda d,ref:self.post('/api/ledger',{'account_id':acc,'category_id':self.cat,'kind':'out','amount':'1000','date':d,'reference':ref,'note':'Расход без заявки со счёта с резервом'})
        self.assertEqual(spend(self.date,'X-TODAY').status_code,409)
        self.assertEqual(spend(str(today()-timedelta(days=5)),'X-BACK').status_code,409)
        move={'account_id':acc,'to_account_id':other,'kind':'transfer','amount':'1000','date':self.date,'reference':'X-MOVE','note':'Перевод зарезервированных денег'}
        self.assertEqual(self.post('/api/ledger',move).status_code,409)
        self.assertEqual(self.post('/api/ledger',{**move,'amount':'0.01','reference':'X-MOVE-0'}).status_code,409)
        self.assertEqual(self.ledger('1000','out',reference='X-PAY',account_id=acc,request_id=rid).status_code,200)

    def test_100_hard_budget_in_the_payment_month_asks_for_a_budget_revision(self):
        book=self.make_user('accountant','hard_book')
        acc=self.post('/api/accounts',{'name':'Счёт жёсткого месяца','kind':'bank','currency':'UZS','opening':'100000','opening_date':str(today()-timedelta(days=62))}).json()['id']
        prev=today().replace(day=1)-timedelta(days=5)
        rid=self.post('/api/requests',{'account_id':acc,'category_id':self.cat,'counterparty':'Supplier','amount':'100','date':str(prev),'purpose':'Заявка прошлого месяца под жёсткий лимит'}).json()['id']
        self.assertEqual(self.approve(rid).status_code,200)
        self.budget('50','hard')
        r=self.post('/api/ledger',{'account_id':acc,'category_id':self.cat,'kind':'out','amount':'100','date':self.date,'reference':'HARD-1','request_id':rid},*book)
        self.assertEqual(r.status_code,409,r.text);self.assertIn('пересмотреть бюджет',r.text);self.assertNotIn('перенести срок',r.text)

    def group_report(self,day,company=None):
        r=self.client.get(f"/api/group-report?company_id={company or self.company}&currency=UZS&scenario=A&month={str(day)[:7]}&day={day}")
        self.assertEqual(r.status_code,200,r.text)
        return r.json()
    def balances(self,company=None,currency='UZS'):
        from decimal import Decimal
        return format(sum(Decimal(a['balance']) for a in self.client.get('/api/accounts').json()
                          if a['company_id']==(company or self.company) and a['currency']==currency),'.2f')
    def past_request(self,account,day,purpose='Резерв на дату отчёта'):
        r=self.post('/api/requests',{'account_id':account,'category_id':self.cat,'counterparty':'Supplier','amount':'600','date':str(day),'purpose':purpose})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.approve(r.json()['id']).status_code,200)

    def test_101_group_report_includes_account_opened_on_report_day(self):
        new=self.post('/api/accounts',{'name':'Opened on report day','company_id':self.company,'kind':'bank','currency':'UZS','opening':'5000000.00','opening_date':self.date})
        self.assertEqual(new.status_code,200,new.text);new=new.json()['id']
        self.assertEqual(self.ledger('100','in',reference='NEW-DAY-IN',account_id=new).status_code,200)
        self.assertEqual(self.ledger('50','transfer',reference='OLD-TO-NEW',to_account_id=new).status_code,200)
        self.past_request(new,self.date)
        data=self.group_report(self.date)
        # The opening balance is set at the start of opening_date, so the day starts with it.
        self.assertEqual(data['opening'],'6000000.00')
        self.assertEqual((data['income_day'],data['expense_day']),('100.00','0.00'))
        self.assertEqual(data['closing'],'6000100.00')
        self.assertEqual(data['closing'],self.balances())
        self.assertEqual((data['reserved'],data['available']),('600.00','5999500.00'))
        self.assertIn('конец дня 6000100.00; резерв 600.00; доступно 5999500.00 UZS',data['text'])

    def test_102_report_day_before_newer_account_counts_it_as_zero(self):
        from fastapi import HTTPException
        from app.services import funds_state
        day=today()-timedelta(days=1)
        self.assertEqual(self.ledger('200','in',reference='OLD-DAY-IN',date=str(day)).status_code,200)
        self.past_request(self.acc,day)
        new=self.post('/api/accounts',{'name':'Opened later','company_id':self.company,'kind':'bank','currency':'UZS','opening':'5000000.00','opening_date':self.date}).json()['id']
        # A specific account still refuses dates before its own opening.
        early=self.post('/api/requests',{'account_id':new,'category_id':self.cat,'counterparty':'Supplier','amount':'1','date':str(day),'purpose':'Дата до открытия счёта'})
        self.assertEqual(early.status_code,422,early.text)
        with unit() as s:
            with self.assertRaises(HTTPException) as denied:funds_state(s,s.get(Account,new),day)
            self.assertEqual(denied.exception.status_code,422)
        data=self.group_report(day)
        self.assertEqual((data['opening'],data['income_day'],data['closing']),('1000000.00','200.00','1000200.00'))
        self.assertEqual((data['reserved'],data['available']),('600.00','999600.00'))
        # The Cash Flow report and its export at the same cut-off also leave the newer account out.
        m=day.month
        report=self.client.get(f'/api/report?year={day.year}&currency=UZS&company_id={self.company}&scenario=A&as_of={day}').json()
        self.assertEqual(set(report['closing'][m-1:]),{'1000200.00'})
        import io
        from openpyxl import load_workbook
        r=self.client.get(f'/api/export/report.xlsx?year={day.year}&currency=UZS&mode=actual&start_month={m}&end_month={m}&company_id={self.company}&scenario=A&as_of={day}')
        self.assertEqual(r.status_code,200,r.text)
        rows={row[0]:row[1:] for row in load_workbook(io.BytesIO(r.content)).active.iter_rows(values_only=True)}
        self.assertEqual(float(rows['Остаток на конец'][0]),1000200.0)
        # From its opening day the newer account is part of the report.
        self.assertEqual(self.group_report(self.date)['closing'],self.balances())

    def test_103_group_report_ordinary_days_are_unchanged(self):
        from app.services import account_balance,money
        day=today();prev=day-timedelta(days=1)
        cash=self.post('/api/accounts',{'name':'Ordinary cash','company_id':self.company,'kind':'cash','currency':'UZS','opening':'250.00','opening_date':str(day-timedelta(days=10))}).json()['id']
        self.assertEqual(self.ledger('300','in',reference='PREV-IN',date=str(prev)).status_code,200)
        self.assertEqual(self.ledger('100','in',reference='DAY-IN').status_code,200)
        self.assertEqual(self.ledger('40','out',reference='DAY-OUT').status_code,200)
        self.assertEqual(self.ledger('50','transfer',reference='DAY-TRANSFER',to_account_id=cash).status_code,200)
        self.past_request(self.acc,day)
        before,current=self.group_report(prev),self.group_report(day)
        self.assertEqual((before['opening'],before['income_day'],before['closing']),('1000250.00','300.00','1000550.00'))
        self.assertEqual((before['reserved'],before['available']),('0.00','1000550.00'))
        with unit() as s:
            accounts=s.scalars(select(Account).where(Account.company_id==self.company,Account.currency=='UZS'))
            legacy=sum(account_balance(s,a,prev) for a in accounts)
        self.assertEqual(current['opening'],money(legacy))
        self.assertEqual(current['opening'],before['closing'])
        self.assertEqual((current['income_day'],current['expense_day'],current['closing']),('100.00','40.00','1000610.00'))
        self.assertEqual(current['closing'],self.balances())
        self.assertEqual((current['reserved'],current['available']),('600.00','1000010.00'))


    # ---- Холдинг и компании: роль на пару «пользователь × компания», учредитель, заявитель.
    def company_ids(self):
        return {c['code']:c['id'] for c in self.client.get('/api/companies').json()['companies']}
    def user_in(self,company,role,name):
        """Создаёт пользователя в указанной компании и возвращает клиент и заголовки с этой компанией."""
        h={**self.h,'X-Company-ID':str(company)}
        r=self.client.post('/api/users',json={'username':name,'name':name,'password':PASSWORD,'role':role},headers=h)
        self.assertEqual(r.status_code,200,r.text)
        cl=TestClient(app);login=cl.post('/api/login',json={'username':name,'password':PASSWORD});self.assertEqual(login.status_code,200,login.text)
        return cl,{'X-CSRF-Token':login.json()['csrf'],'X-Company-ID':str(company)}

    def test_104_a_company_user_cannot_read_or_change_another_company(self):
        ids=self.company_ids();zuma=ids['ZUMA'];uzg=ids['UZGERMED']
        rid=self.request('600').json()['id'];self.assertEqual(self.approve(rid).status_code,200)
        cl,h=self.user_in(zuma,'finance','zuma_only')
        try:
            self.assertEqual([c['code'] for c in cl.get('/api/companies').json()['companies']],['ZUMA'])
            foreign={**h,'X-Company-ID':str(uzg)}
            for path in ('/api/bootstrap','/api/accounts','/api/requests','/api/dashboard','/api/ledger','/api/budgets'):
                self.assertEqual(cl.get(path,headers=foreign).status_code,403,path)
            self.assertEqual(cl.get(f'/api/report?company_id={uzg}').status_code,403)
            self.assertEqual(cl.get(f'/api/report?company_id={uzg}',headers=h).status_code,409)
            # Direct identifiers of the other company inside the own company context.
            row={'account_id':self.acc,'category_id':self.cat,'counterparty':'Supplier','amount':'600','date':self.date,'purpose':'Попытка чужой правки','version':1,'reason':'Попытка изменить чужую заявку'}
            self.assertEqual(cl.put(f'/api/requests/{rid}',json=row,headers=h).status_code,404)
            self.assertEqual(cl.post(f'/api/requests/{rid}/decision',json={'action':'cancel','version':3,'note':'Отмена чужой заявки'},headers=h).status_code,404)
            edit={'name':'Чужой счёт','kind':'bank','currency':'UZS','opening':'1','opening_date':self.date,'allow_overdraft':False,'reason':'Попытка изменить чужой счёт'}
            self.assertEqual(cl.post(f'/api/accounts/{self.acc}',json=edit,headers=h).status_code,404)
            spend={'account_id':self.acc,'category_id':self.cat,'kind':'out','amount':'1','date':self.date,'reference':'ALIEN','note':'Расход с чужого счёта'}
            self.assertEqual(cl.post('/api/ledger',json=spend,headers=h).status_code,404)
            self.assertEqual(cl.post('/api/ledger',json=spend,headers=foreign).status_code,403)
        finally:cl.close()
        self.assertEqual(self.client.get('/api/accounts').json()[0]['balance'],'1000000.00')

    def test_105_founder_sees_every_company_and_changes_nothing(self):
        ids=self.company_ids()
        rid=self.request('600').json()['id']
        cl,h=self.user_in(ids['UZGERMED'],'founder','owner')
        try:
            self.assertEqual(sorted(c['code'] for c in cl.get('/api/companies').json()['companies']),['UZGERMED','ZUMA'])
            for code in ('UZGERMED','ZUMA'):
                hh={**h,'X-Company-ID':str(ids[code])}
                me=cl.get('/api/bootstrap',headers=hh).json()['user']
                self.assertEqual((me['holding_role'],me['company_role']),('founder',None))
                self.assertEqual(me['permissions'],['audit','export','ledger','view'])
                for path in ('/api/dashboard','/api/accounts','/api/ledger','/api/requests','/api/budgets',f'/api/report?year={self.date[:4]}','/api/audit'):
                    self.assertEqual(cl.get(path,headers=hh).status_code,200,(code,path))
            writes=[('/api/accounts',{'name':'Учредитель','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date}),
                    ('/api/requests',{'account_id':self.acc,'category_id':self.cat,'counterparty':'Supplier','amount':'1','date':self.date,'purpose':'Заявка учредителя'}),
                    ('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'kind':'in','amount':'1','date':self.date,'reference':'OWNER','note':'Поступление учредителя'}),
                    ('/api/budgets',{'category_id':self.cat,'month':self.month,'currency':'UZS','amount':'1','mode':'soft','reason':'Бюджет учредителя'}),
                    ('/api/reserve',{'currency':'UZS','amount':'1'}),
                    ('/api/categories',{'name':'Статья учредителя','type':'outcome','activity':'operating'}),
                    ('/api/cash-plan',{'month':self.month,'currency':'UZS','version':0,'reason':'План учредителя','items':[]}),
                    ('/api/receipts',{'account_id':self.acc,'category_id':self.cat,'counterparty':'Покупатель','amount':'1','date':self.date}),
                    ('/api/approval-policy',{'amount':'1'}),
                    ('/api/users',{'username':'owner_made','name':'owner_made','password':PASSWORD,'role':'employee'}),
                    ('/api/company-users',{'username':'admin'}),
                    (f'/api/requests/{rid}/decision',{'action':'approve','version':1,'note':'Согласование учредителем'})]
            for path,body in writes:
                self.assertEqual(cl.post(path,json=body,headers=h).status_code,403,path)
            row={'account_id':self.acc,'category_id':self.cat,'counterparty':'Supplier','amount':'1','date':self.date,'purpose':'Правка учредителем','version':1,'reason':'Правка заявки учредителем'}
            self.assertEqual(cl.put(f'/api/requests/{rid}',json=row,headers=h).status_code,403)
            self.assertEqual(self.preview_plan(client=cl,headers=h).status_code,403)
        finally:cl.close()
        # The founder is not a member of a company and cannot be given a company role.
        self.assertEqual(self.post('/api/company-users',{'username':'owner','role':'finance'}).status_code,409)

    def test_106_requester_sees_only_own_requests_and_never_account_balances(self):
        foreign=self.request('600').json()['id']
        cl,h=self.make_user('employee','buyer')
        try:
            own=self.request('100',client=cl,headers=h).json()['id']
            listed=cl.get('/api/requests',headers=h).json()
            self.assertEqual([x['id'] for x in listed],[own]);self.assertNotIn(foreign,[x['id'] for x in listed])
            self.assertTrue(listed[0]['budget']['hidden']);self.assertNotIn('limit',{k for k,v in listed[0]['budget'].items() if v is not None})
            for path in ('/api/accounts','/api/dashboard','/api/ledger','/api/budgets',f'/api/report?year={self.date[:4]}',
                         f'/api/group-report?company_id={self.company}&currency=UZS&scenario=A&month={self.month}&day={self.date}'):
                self.assertEqual(cl.get(path,headers=h).status_code,403,path)
            accounts=cl.get('/api/bootstrap',headers=h).json()['accounts']
            self.assertTrue(accounts)
            self.assertTrue(all(not {'balance','opening','available'}&set(a) for a in accounts))
            self.assertEqual(self.post(f'/api/requests/{foreign}/decision',{'action':'cancel','note':'Отмена чужой заявки'},cl,h).status_code,403)
        finally:cl.close()

    def test_107_requester_budget_card_shows_only_availability_and_the_rest_after(self):
        book=self.make_user('accountant','card_book')
        self.budget('1000','soft');rid=self.request('600').json()['id'];self.assertEqual(self.approve(rid).status_code,200)
        cl,h=self.make_user('employee','card_buyer')
        try:
            q=lambda n:cl.get(f'/api/budget-check?account_id={self.acc}&category_id={self.cat}&amount={n}&date={self.date}',headers=h)
            ok=q('300').json()
            self.assertEqual(ok,{'period':self.month,'currency':'UZS','budget_set':True,'available':'400.00','after':'100.00','status':'ok','mode':'soft'})
            over=q('500').json();self.assertEqual((over['after'],over['status']),('-100.00','soft'))
            other=next(c['id'] for c in cl.get('/api/bootstrap',headers=h).json()['categories'] if c['id']!=self.cat and c['type']=='outcome')
            none=cl.get(f'/api/budget-check?account_id={self.acc}&category_id={other}&amount=1&date={self.date}',headers=h).json()
            self.assertEqual((none['budget_set'],none['status']),(False,'no_budget'))
            self.assertEqual(book[0].get(f'/api/budget-check?account_id={self.acc}&category_id={self.cat}&amount=1&date={self.date}',headers=book[1]).status_code,403)
        finally:cl.close()

    def test_108_one_user_has_different_roles_in_different_companies(self):
        ids=self.company_ids();zuma=ids['ZUMA'];uzg=ids['UZGERMED']
        cl,h=self.make_user('finance','multi')
        self.assertEqual(self.client.post('/api/company-users',json={'username':'multi','role':'director'},headers={**self.h,'X-Company-ID':str(zuma)}).status_code,200)
        hz={**h,'X-Company-ID':str(zuma)};hu={**h,'X-Company-ID':str(uzg)}
        try:
            self.assertEqual(cl.get('/api/bootstrap',headers=hu).json()['user']['role'],'finance')
            self.assertEqual(cl.get('/api/bootstrap',headers=hz).json()['user']['role'],'director')
            rid=self.request('600').json()['id']
            current=next(x for x in self.client.get('/api/requests').json() if x['id']==rid)
            first=cl.post(f'/api/requests/{rid}/decision',json={'action':'approve','version':current['version'],'note':'Проверено финансовым директором'},headers=hu)
            self.assertEqual(first.status_code,200,first.text);self.assertEqual(first.json()['approval_stage'],'director')
            self.assertEqual(cl.post(f'/api/requests/{rid}/decision',json={'action':'approve','version':first.json()['version'],'note':'Попытка директорского этапа'},headers=hu).status_code,403)
            cid,zh,cat,acc=self.second_company()
            zr=self.documented_request({'account_id':acc,'category_id':cat,'counterparty':'Supplier','amount':'100','date':self.date,'purpose':'Заявка второй компании'},headers=zh).json()
            self.assertEqual(cl.post(f"/api/requests/{zr['id']}/decision",json={'action':'approve','version':zr['version'],'note':'Попытка финансового этапа'},headers=hz).status_code,403)
            fin=self.user_in(zuma,'finance','zuma_fin')
            checked=fin[0].post(f"/api/requests/{zr['id']}/decision",json={'action':'approve','version':zr['version'],'note':'Проверено во второй компании'},headers=fin[1]).json()
            done=cl.post(f"/api/requests/{zr['id']}/decision",json={'action':'approve','version':checked['version'],'note':'Утверждаю директором'},headers=hz)
            self.assertEqual(done.status_code,200,done.text);self.assertEqual(done.json()['status'],'approved')
            fin[0].close()
        finally:cl.close()

    def test_109_holding_admin_gets_finances_only_by_a_separate_assignment(self):
        ids=self.company_ids();zuma=ids['ZUMA']
        cl,h=self.make_user('admin','admin2')
        try:
            me=cl.get('/api/bootstrap',headers=h).json()['user']
            self.assertEqual((me['holding_role'],me['company_role']),('admin',None))
            self.assertFalse({'write','request','approve','budget','pay_bank','pay_cash'}&set(me['permissions']))
            self.assertEqual(sorted(c['code'] for c in cl.get('/api/companies').json()['companies']),['UNASSIGNED','UZGERMED','ZUMA'])
            self.assertEqual(self.request(client=cl,headers=h).status_code,403)
            self.assertEqual(self.post('/api/accounts',{'name':'Счёт админа','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date},cl,h).status_code,403)
            self.assertEqual(self.post('/api/ledger',{'account_id':self.acc,'category_id':self.cat,'kind':'in','amount':'1','date':self.date,'reference':'ADM','note':'Поступление администратора'},cl,h).status_code,403)
            self.assertEqual(cl.get('/api/users',headers=h).status_code,200)
            # No self-assignment, and an admin needs a concrete role.
            self.assertEqual(self.post('/api/company-users',{'username':'admin2','role':'finance'},cl,h).status_code,409)
            self.assertEqual(self.post('/api/company-users',{'username':'admin2'}).status_code,422)
            self.assertEqual(self.post('/api/company-users',{'username':'admin2','role':'finance'}).status_code,200)
            self.assertEqual(self.request(client=cl,headers=h).status_code,200)
            self.assertEqual(self.request(client=cl,headers={**h,'X-Company-ID':str(zuma)}).status_code,403)
            uid=me['id']
            self.assertEqual(self.client.delete(f'/api/company-users/{uid}',headers=self.h).status_code,200)
            self.assertEqual(self.request(client=cl,headers=h).status_code,403)
            self.assertIn('UZGERMED',[c['code'] for c in cl.get('/api/companies').json()['companies']])
        finally:cl.close()

    def test_110_permissions_have_no_generic_pay_and_payments_stay_split(self):
        from app.security import PERMS,ROLES
        self.assertTrue(all('pay' not in PERMS[r] for r in ROLES))
        self.assertEqual(PERMS['accountant'],{'ledger','pay_bank'});self.assertIn('pay_cash',PERMS['cashier'])
        self.assertTrue(all(not {'pay_bank','pay_cash'}&PERMS[r] for r in ('admin','founder','director','finance','operator')))
        book=self.make_user('accountant','split_book')
        self.assertEqual(book[0].get('/api/bootstrap',headers=book[1]).json()['user']['permissions'],['ledger','pay_bank'])

    def test_111_company_role_column_is_added_to_an_existing_database(self):
        from sqlalchemy import inspect as sa_inspect,text
        cl,h=self.make_user('employee','kept_role')
        with engine.begin() as conn:conn.execute(text('ALTER TABLE company_users DROP COLUMN role'))
        initialize();initialize()
        with engine.connect() as conn:self.assertIn('role',{c['name'] for c in sa_inspect(conn).get_columns('company_users')})
        # The previous role becomes the explicit company role: no access is lost.
        with unit() as s:
            uid=s.scalar(select(User.id).where(User.username=='kept_role'))
            self.assertEqual([m.role for m in s.scalars(select(CompanyUser).where(CompanyUser.user_id==uid))],['employee'])
        self.assertEqual(cl.get('/api/bootstrap',headers=h).json()['user']['role'],'employee')
        self.assertEqual(self.request(client=cl,headers=h).status_code,200)
        cl.close()

    def test_112_a_holding_user_moved_to_a_company_role_stays_only_in_that_company(self):
        ids=self.company_ids();zuma=ids['ZUMA'];uzg=ids['UZGERMED']
        rid=self.request('600').json()['id']
        # A founder created in UZGERMED and an admin from before the migration with empty links everywhere.
        self.assertEqual(self.post('/api/users',{'username':'owner1','name':'owner1','password':PASSWORD,'role':'founder'}).status_code,200)
        self.assertEqual(self.post('/api/users',{'username':'legacy_admin','name':'legacy_admin','password':PASSWORD,'role':'admin'}).status_code,200)
        with unit(True) as s:
            uid=s.scalar(select(User.id).where(User.username=='legacy_admin'))
            for c in s.scalars(select(Company)):s.add(CompanyUser(company_id=c.id,user_id=uid,role=None))
        zh={**self.h,'X-Company-ID':str(zuma)}
        for name in ('owner1','legacy_admin'):
            uid=next(u['id'] for u in self.client.get('/api/users',headers=zh).json() if u['username']==name)
            self.assertEqual(self.client.post(f'/api/users/{uid}',json={'role':'finance','active':True},headers=zh).status_code,200)
            cl=TestClient(app);login=cl.post('/api/login',json={'username':name,'password':PASSWORD});h={'X-CSRF-Token':login.json()['csrf']}
            try:
                self.assertEqual([c['code'] for c in cl.get('/api/companies').json()['companies']],['ZUMA'],name)
                self.assertEqual(cl.get('/api/bootstrap',headers={**h,'X-Company-ID':str(uzg)}).status_code,403,name)
                self.assertEqual(cl.post(f'/api/requests/{rid}/decision',json={'action':'approve','version':1,'note':'Попытка в чужой компании'},headers={**h,'X-Company-ID':str(uzg)}).status_code,403,name)
                self.assertEqual(cl.get('/api/bootstrap',headers={**h,'X-Company-ID':str(zuma)}).json()['user']['role'],'finance',name)
            finally:cl.close()
        # Roles held before a change of holding status never come back later.
        def edit(name,role,company):
            hh={**self.h,'X-Company-ID':str(company)}
            uid=next(u['id'] for u in self.client.get('/api/users',headers=hh).json() if u['username']==name)
            self.assertEqual(self.client.post(f'/api/users/{uid}',json={'role':role,'active':True},headers=hh).status_code,200)
        def state(name,company):
            cl=TestClient(app);h={'X-CSRF-Token':cl.post('/api/login',json={'username':name,'password':PASSWORD}).json()['csrf']}
            try:
                codes=[c['code'] for c in cl.get('/api/companies').json()['companies']]
                me=cl.get('/api/bootstrap',headers={**h,'X-Company-ID':str(company)}).json()['user'] if company else None
                return codes,me
            finally:cl.close()
        self.user_in(zuma,'finance','promoted')[0].close()
        edit('promoted','founder',zuma);edit('promoted','investor',uzg)
        self.assertEqual(state('promoted',None)[0],['UZGERMED'])
        self.user_in(zuma,'finance','to_admin')[0].close()
        edit('to_admin','admin',zuma)
        me=state('to_admin',zuma)[1];self.assertEqual(me['company_role'],None);self.assertNotIn('write',me['permissions'])
        self.assertEqual(self.post('/api/company-users',{'username':'to_admin','role':'director'},headers={**self.h,'X-Company-ID':str(zuma)}).status_code,200)
        edit('to_admin','employee',uzg)
        self.assertEqual(state('to_admin',None)[0],['UZGERMED'])

    def test_113_admin_rights_come_only_from_the_assignment(self):
        cl,h=self.make_user('admin','plain_admin')
        op=self.make_user('operator','import_owner')
        try:
            # Archive and restore change an account: an admin without an assignment cannot.
            aid=self.post('/api/accounts',{'name':'Пустой счёт','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date}).json()['id']
            self.assertEqual(self.post(f'/api/accounts/{aid}/archive',{'archived':True,'reason':'Закрытие банковского счёта'},cl,h).status_code,403)
            # Nobody commits another user's import, the holding admin included.
            batch=self.preview_plan(client=op[0],headers=op[1]);self.assertEqual(batch.status_code,200,batch.text)
            self.assertEqual(self.post(f"/api/plan-import/{batch.json()['id']}/commit",{'mappings':{},'reason':'Загрузка чужого плана'}).status_code,403)
            # Counterparties of other people's requests are not sent to the requester.
            self.request('100')
            buyer=self.make_user('employee','cp_buyer')
            self.assertEqual(buyer[0].get('/api/bootstrap',headers=buyer[1]).json()['counterparties'],[])
            self.assertTrue(self.client.get('/api/bootstrap').json()['counterparties'])
            buyer[0].close()
        finally:cl.close();op[0].close()

    def test_114_an_assignment_only_adds_rights_and_the_service_company_gets_no_roles(self):
        with unit() as s:service=s.scalar(select(Company.id).where(Company.code=='UNASSIGNED'))
        rid=self.request('600').json()['id']
        cl,h=self.make_user('admin','viewer_admin')
        try:
            self.assertEqual(self.post('/api/company-users',{'username':'viewer_admin','role':'employee'}).status_code,200)
            listed=cl.get('/api/requests',headers=h).json()
            self.assertIn(rid,[x['id'] for x in listed]);self.assertFalse(listed[0]['budget'].get('hidden'))
        finally:cl.close()
        legacy=self.make_user('operator','legacy_everywhere')[0];legacy.close()
        with unit(True) as s:
            uid=s.scalar(select(User.id).where(User.username=='legacy_everywhere'))
            from sqlalchemy import delete
            s.execute(delete(CompanyUser).where(CompanyUser.user_id==uid))
            for c in s.scalars(select(Company)):s.add(CompanyUser(company_id=c.id,user_id=uid,role=None))
        initialize()
        with unit() as s:
            roles={m.company_id:m.role for m in s.scalars(select(CompanyUser).where(CompanyUser.user_id==uid))}
        self.assertIsNone(roles.pop(service));self.assertEqual(set(roles.values()),{'operator'})


if __name__=='__main__':unittest.main(verbosity=2)
