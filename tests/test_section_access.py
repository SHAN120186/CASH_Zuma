import unittest
import tests.test_site as base
from sqlalchemy import select,delete
from app.db import unit,CompanyUser,User,Ledger

class SectionAccessTests(base.SiteTests):
    def save_sections(self,uid,sections,version=0):
        return self.client.put(f'/api/admin/users/{uid}/sections',headers=self.h,json={'company_id':self.company,'version':version,'sections':sections,'reason':'Explicit owner approved module policy'})
    def uid(self,name):
        with unit() as s:return s.scalar(select(User.id).where(User.username==name))
    def test_admin_without_assignment_can_manage_accounts_and_correct_operations(self):
        with unit(True) as s:s.execute(delete(CompanyUser).where(CompanyUser.user_id==self.uid('admin')))
        permissions=self.client.get('/api/bootstrap',headers=self.h).json()['user']['permissions']
        self.assertTrue({'write','budget','plan','import','request_edit','audit'}<=set(permissions))
        account=self.post('/api/accounts',{'name':'Admin standalone','kind':'bank','currency':'UZS','opening':'1000','opening_date':self.date})
        self.assertEqual(account.status_code,200,account.text)
        entry=self.ledger('100',reference='CORRECT-TEST').json()['id']
        row=next(r for r in self.client.get('/api/ledger',headers=self.h).json() if r['id']==entry)
        body={'account_id':row['account_id'],'to_account_id':None,'category_id':row['category_id'],'kind':row['kind'],'amount':'150','date':row['date'],'counterparty':row['counterparty'],'reference':row['reference'],'note':'Corrected','request_id':None,'receipt_id':None,'expected':row['edit_token'],'reason':'Correct initial posted amount'}
        response=self.client.put(f'/api/ledger/{entry}',headers=self.h,json=body)
        self.assertEqual(response.status_code,200,response.text)
        with unit() as s:
            self.assertEqual(s.get(Ledger,entry).amount,10000)
            self.assertEqual(s.get(Ledger,response.json()['id']).amount,15000)
            self.assertIsNotNone(s.scalar(select(Ledger).where(Ledger.reversal_of==entry)))
        self.assertEqual(self.client.put(f'/api/ledger/{entry}',headers=self.h,json=body).status_code,409)
    def test_hidden_and_view_enforced_and_existing_sessions_end(self):
        cl,h=self.make_user('finance','restricted_finance');uid=self.uid('restricted_finance')
        response=self.save_sections(uid,{'accounts':'hidden','ledger':'view','requests':'view','calendar':'edit','pnl':'hidden'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(cl.get('/api/bootstrap',headers=h).status_code,401)
        cl,h=self.relogin('restricted_finance')
        self.assertEqual(cl.get('/api/accounts',headers=h).status_code,403)
        self.assertEqual(cl.get('/api/ledger',headers=h).status_code,200)
        self.assertEqual(self.ledger('1',client=(cl,h),reference='BLOCKED').status_code,403)
        rid=self.request(submit=False).json()['id']
        self.assertEqual(cl.get(f'/api/requests/{rid}',headers=h).json()['actions'],[])
        self.assertEqual(self.save_sections(uid,{'accounts':'view'},version=0).status_code,409)
        self.assertEqual(self.save_sections(uid,{'bad':'edit'},version=1).status_code,422)
        self.assertEqual(cl.put(f'/api/admin/users/{uid}/sections',headers=h,json={'company_id':self.company,'version':1,'sections':{},'reason':'Try self elevation illegally'}).status_code,403)
        cl.close()
    def test_module_grant_does_not_expand_other_sections_or_company(self):
        cl,h=self.make_user('employee','custom_employee');uid=self.uid('custom_employee')
        self.assertEqual(self.save_sections(uid,{'home':'view','ledger':'view','accounts':'hidden'}).status_code,200)
        cl.close();cl,h=self.relogin('custom_employee')
        self.request(submit=False)
        self.assertEqual(cl.get('/api/requests',headers=h).json(),[])
        self.assertEqual(cl.get('/api/ledger',headers=h).status_code,200)
        self.assertEqual(cl.get('/api/accounts',headers=h).status_code,403)
        self.assertEqual(cl.get('/api/bootstrap',headers={**h,'X-Company-ID':str(self.company+999)}).status_code,403)
        cl.close()
    def test_balance_view_does_not_return_hidden_pnl_and_cannot_replace_source(self):
        from app.db import FinancialStatementSource
        from unittest.mock import patch
        cl,h=self.make_user('finance','balance_reader');uid=self.uid('balance_reader')
        self.assertEqual(self.save_sections(uid,{'pnl':'hidden','balance':'view'}).status_code,200)
        cl.close();cl,h=self.relogin('balance_reader')
        with unit(True) as s:
            s.add(FinancialStatementSource(company_id=self.company,created_by=uid,filename='synthetic.xlsx',sha256='a'*64,size=1,snapshot='{}',content=b'x'))
        with patch('app.financial_statements.current_source_review',return_value={'reports':{'pnl':{'secret':123},'balance':{'rows':[]}},'requirements':[]}):
            response=cl.get('/api/financial-statements',headers=h)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(set(response.json()['source']['reports']),{'balance'})
        self.assertEqual(cl.post('/api/financial-statements/upload',headers=h,content=b'x').status_code,403)
        self.assertEqual(cl.get('/api/financial-statements/reconciliation?year=2026',headers=h).status_code,403)
        cl.close()
    def test_custom_visibility_does_not_remove_existing_accountant_payment_right(self):
        self.budget('100000','soft');rid=self.request('100').json()['id'];self.approve(rid)
        book=self.make_user('accountant','custom_payer');uid=self.uid('custom_payer')
        self.assertEqual(self.save_sections(uid,{'report':'hidden'}).status_code,200)
        book[0].close();book=self.relogin('custom_payer')
        response=self.ledger('100','out',reference='CUSTOM-PAY',request_id=rid,client=book)
        self.assertEqual(response.status_code,200,response.text)
        book[0].close()

    def test_admin_cannot_be_locked_and_founder_cannot_become_editor(self):
        self.assertEqual(self.save_sections(self.uid('admin'),{'users':'hidden'}).status_code,409)
        cl,h=self.make_user('founder','custom_founder')
        self.assertEqual(self.save_sections(self.uid('custom_founder'),{'ledger':'edit'}).status_code,422)
        cl.close()

def load_tests(loader,tests,pattern):return unittest.TestSuite(SectionAccessTests(n) for n in SectionAccessTests.__dict__ if n.startswith('test_'))
def tearDownModule():base.tearDownModule()
if __name__=='__main__':unittest.main()
