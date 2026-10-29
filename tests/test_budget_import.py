import io,unittest
from openpyxl import Workbook
import tests.test_site as base
from app.budget_import import parse_budget
from app.db import unit,Category,Budget,Ledger,User,CompanyUser,Setting
from fastapi.testclient import TestClient
from sqlalchemy import select,func

def book(rows,offset=0):
    w=Workbook();s=w.active
    for i,row in enumerate(rows,1):
        for j,value in enumerate(row,1+offset):s.cell(i,j,value)
    out=io.BytesIO();w.save(out);return out.getvalue()

class BudgetImportTests(unittest.TestCase):
    setUp=base.SiteTests.setUp
    tearDown=base.SiteTests.tearDown
    post=base.SiteTests.post
    def preview(self,rows,headers=None):
        return self.client.post('/api/budgets/import/preview?month='+self.month,content=book(rows),headers={**self.h,'X-Filename':'budget.xlsx',**(headers or {})})
    def test_replace_blank_zero_and_repeat(self):
        with unit(True) as s:
            a=Category(company_id=self.company,code='001',name='Synthetic A',type='outcome');b=Category(company_id=self.company,code='002',name='Synthetic B',type='outcome');s.add_all([a,b]);s.flush();aid,bid=a.id,b.id
            s.add_all([Budget(category_id=aid,month=self.month,currency='UZS',amount=900,mode='hard'),Budget(category_id=bid,month=self.month,currency='UZS',amount=800)])
        p=self.preview([['Код статьи','Название','Сумма'],['001','Synthetic A',0],['002','Synthetic B',None]])
        self.assertEqual(p.status_code,200,p.text);d=p.json();self.assertEqual(d['rows'][0]['suggestion'],str(aid))
        body={'mappings':{'001':str(aid)}}
        r=self.post(f'/api/budgets/import/{d["id"]}/commit',body);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.post(f'/api/budgets/import/{d["id"]}/commit',body).status_code,409)
        with unit() as s:
            self.assertEqual(s.scalar(select(Budget).where(Budget.category_id==aid)).amount,0)
            self.assertEqual(s.scalar(select(Budget).where(Budget.category_id==aid)).mode,'hard')
            self.assertEqual(s.scalar(select(Budget).where(Budget.category_id==bid)).amount,800)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),0)
    def test_stale_and_company_scope(self):
        d=self.preview([['T1','Synthetic C',150]]).json()
        with unit(True) as s:s.add(Category(company_id=self.company,name='Changed catalog',type='outcome'))
        self.assertEqual(self.post(f'/api/budgets/import/{d["id"]}/commit',{'mappings':{'T1':'new'}}).status_code,409)
        other=next(c['id'] for c in self.client.get('/api/bootstrap').json()['companies'] if c['code']=='ZUMA')
        r=self.client.post(f'/api/budgets/import/{d["id"]}/commit',json={'mappings':{'T1':'new'}},headers={**self.h,'X-Company-ID':str(other)})
        self.assertIn(r.status_code,(403,404))
    def test_duplicate_atomic_and_creation(self):
        p=self.preview([['7','Synthetic expense',123.456],['8','Synthetic empty',None]]);self.assertEqual(p.status_code,200,p.text);d=p.json()
        r=self.post(f'/api/budgets/import/{d["id"]}/commit',{'mappings':{'7':'new'}});self.assertEqual(r.status_code,200,r.text)
        with unit() as s:
            c=s.scalar(select(Category).where(Category.code=='7'));self.assertEqual(s.scalar(select(Budget).where(Budget.category_id==c.id)).amount,12346)
        d=self.preview([['9','Duplicate',1],['9','Duplicate',2]]).json();self.assertTrue(d['issues'])
        self.assertEqual(self.post(f'/api/budgets/import/{d["id"]}/commit',{'mappings':{'9':'new'}}).status_code,422)
    def test_readonly_denied_and_income_rejected(self):
        with unit(True) as s:
            u=User(username='budget.viewer',name='Synthetic viewer',role='auditor',password_hash=base.HASH);s.add(u);s.flush();s.add(CompanyUser(user_id=u.id,company_id=self.company,role='auditor'))
            c=Category(company_id=self.company,code='INC',name='Synthetic income',type='income');s.add(c);s.flush();cid=c.id
        with TestClient(base.app) as client:
            auth=client.post('/api/login',json={'username':'budget.viewer','password':base.PASSWORD}).json()
            r=client.post('/api/budgets/import/preview?month='+self.month,content=book([['1','Synthetic',10]]),headers={'X-CSRF-Token':auth['csrf'],'X-Filename':'budget.xlsx'})
            self.assertEqual(r.status_code,403)
        d=self.preview([['INC','Synthetic income',100]]).json();self.assertTrue(d['rows'][0]['income'])
        self.assertEqual(self.post(f'/api/budgets/import/{d["id"]}/commit',{'mappings':{'INC':str(cid)}}).status_code,422)
        self.assertEqual(self.post(f'/api/budgets/import/{d["id"]}/commit',{'mappings':{'INC':'new'}}).status_code,409)

    def test_hidden_catalog_cannot_create_via_budget(self):
        with unit(True) as s:
            u=User(username='budget.director',name='Synthetic director',role='director',password_hash=base.HASH);s.add(u);s.flush();s.add(CompanyUser(user_id=u.id,company_id=self.company,role='director'))
            s.add(Setting(key=f'sections:{self.company}:{u.id}',value='{"version":1,"sections":{"categories":"hidden"}}'))
        with TestClient(base.app) as client:
            auth=client.post('/api/login',json={'username':'budget.director','password':base.PASSWORD}).json();h={'X-CSRF-Token':auth['csrf'],'X-Filename':'budget.xlsx'}
            p=client.post('/api/budgets/import/preview?month='+self.month,content=book([['77','Synthetic new',10]]),headers=h)
            self.assertEqual(p.status_code,200,p.text);d=p.json();self.assertFalse(d['can_create'])
            r=client.post(f'/api/budgets/import/{d["id"]}/commit',json={'mappings':{'77':'new'}},headers=h);self.assertEqual(r.status_code,403,r.text)

    def test_parser_formatting_formula_errors(self):
        rows,issues=parse_budget(book([['4','Synthetic',123.45],['5','Blank','\u00a0'],[None,'Жами ҳаражатлар','summary']],7));self.assertEqual(len(rows),2);self.assertFalse(issues);self.assertEqual(rows[0]['amount'],12345)
        _,issues=parse_budget(book([['4','Synthetic','=1+1']]));self.assertTrue(issues)
        _,issues=parse_budget(book([['4','Synthetic',-1]]));self.assertTrue(issues)

def tearDownModule():base.tearDownModule()

if __name__=='__main__':unittest.main()
