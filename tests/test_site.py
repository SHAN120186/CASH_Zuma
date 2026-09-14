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
os.environ.pop('DATABASE_URL',None)
from fastapi.testclient import TestClient
from app.main import app
from app.db import *
from app.services import today
from app.security import hash_password
PASSWORD='OnlyForTemporaryTests_9841!'
HASH=hash_password(PASSWORD)

class SiteTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine);initialize()
        with unit(True) as s:s.add(User(username='admin',name='Test Admin',role='admin',password_hash=HASH))
        self.client=TestClient(app)
        r=self.client.post('/api/login',json={'username':'admin','password':PASSWORD});self.assertEqual(r.status_code,200,r.text)
        self.h={'X-CSRF-Token':r.json()['csrf']}
        self.date=str(today());self.month=self.date[:7]
        b=self.client.get('/api/bootstrap').json();self.cat=b['categories'][1]['id']
        r=self.post('/api/accounts',{'name':'Test bank','kind':'bank','currency':'UZS','opening':'1000000.00','opening_date':str(today()-timedelta(days=10))})
        self.acc=r.json()['id']
    def tearDown(self):self.client.close()
    def post(self,path,data,client=None,headers=None):return (client or self.client).post(path,json=data,headers=headers or self.h)
    def budget(self,limit='1000',mode='soft'):
        r=self.post('/api/budgets',{'category_id':self.cat,'month':self.month,'currency':'UZS','amount':limit,'mode':mode,'reason':'Подтверждено для тестирования','source':'test'})
        self.assertEqual(r.status_code,200,r.text)
    def request(self,n='600',purpose='Тестовая заявка на оплату',client=None,headers=None):
        return self.post('/api/requests',{'account_id':self.acc,'category_id':self.cat,'counterparty':'Supplier','amount':n,'date':self.date,'purpose':purpose},client,headers)
    def approve(self,id):
        return self.post(f'/api/requests/{id}/decision',{'action':'approve','note':'Подтверждаю необходимость расхода'})
    def ledger(self,amount='100',kind='in',reference='DOC1',**extra):
        data={'account_id':self.acc,'category_id':self.cat,'amount':amount,'kind':kind,'date':self.date,'reference':reference,'note':'Подтверждённая тестовая операция'};data.update(extra)
        return self.post('/api/ledger',data)
    def make_user(self,role='employee',name='worker'):
        r=self.post('/api/users',{'username':name,'name':name,'password':PASSWORD,'role':role});self.assertEqual(r.status_code,200,r.text)
        cl=TestClient(app);r=cl.post('/api/login',json={'username':name,'password':PASSWORD});self.assertEqual(r.status_code,200,r.text)
        return cl,{'X-CSRF-Token':r.json()['csrf']}
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
        self.assertEqual(self.post(f"/api/requests/{r.json()['id']}/decision",{'action':'approve'}).status_code,409)
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
        cl,h=self.make_user('director','boss');r=self.request('100',client=cl,headers=h)
        self.assertEqual(self.post(f"/api/requests/{r.json()['id']}/decision",{'action':'approve'},cl,h).status_code,403)
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
        path=ROOT/'source'/'FinModel_2026_Zuma_Pharm_v8.xlsx';raw=path.read_bytes()
        headers={**self.h,'Content-Type':'application/octet-stream','X-Filename':'FinModel.xlsx'}
        r=self.client.post('/api/model/upload',content=raw,headers=headers);self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['created'])
        r2=self.client.post('/api/model/upload',content=raw,headers=headers);self.assertFalse(r2.json()['created'])
        m=self.client.get('/api/model').json();self.assertEqual(len(m['versions']),1)
        self.assertEqual(m['snapshot']['summary']['cash_close_july']['value'],'1350593723')
        self.assertEqual(m['snapshot']['summary']['balance_cash_july']['value'],'1457280281')
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
if __name__=='__main__':unittest.main(verbosity=2)
