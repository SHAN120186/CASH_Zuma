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
    def operation_body(self,row,amount=None):
        return {'account_id':row['account_id'],'to_account_id':row['to_account_id'],'category_id':row['category_id'],'kind':row['kind'],'amount':amount or row['amount'],'date':row['date'],'counterparty':row['counterparty'],'reference':row['reference'],'note':'Correct operation details','request_id':row['request_id'],'receipt_id':row['receipt_id'],'expected':row['edit_token'],'reason':'Owner confirmed accounting correction'}
    def test_insufficient_funds_correction_rolls_back_every_ledger_change(self):
        account=self.post('/api/accounts',{'name':'Atomic correction','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date}).json()['id']
        original=self.ledger('100',reference='ATOMIC-IN',account_id=account).json()['id']
        self.assertEqual(self.ledger('90','out',reference='ATOMIC-OUT',account_id=account).status_code,200)
        rows=self.client.get('/api/ledger',headers=self.h).json();row=next(r for r in rows if r['id']==original)
        response=self.client.put(f'/api/ledger/{original}',headers=self.h,json=self.operation_body(row,'50'))
        self.assertEqual(response.status_code,409,response.text)
        with unit() as s:
            self.assertEqual(s.get(Ledger,original).amount,10000)
            self.assertIsNone(s.scalar(select(Ledger).where(Ledger.reversal_of==original)))
        self.assertEqual(len(self.client.get('/api/ledger',headers=self.h).json()),len(rows))
    def test_paid_request_metadata_can_change_but_amount_is_locked(self):
        self.budget('100000','soft');rid=self.request('100').json()['id'];self.approve(rid)
        entry=self.ledger('100','out',reference='LINKED-EDIT',request_id=rid).json()['id']
        row=next(r for r in self.client.get('/api/ledger',headers=self.h).json() if r['id']==entry)
        response=self.client.put(f'/api/ledger/{entry}',headers=self.h,json=self.operation_body(row))
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.row(rid)['status'],'paid')
        row=next(r for r in self.client.get('/api/ledger',headers=self.h).json() if r['id']==entry)
        self.assertEqual(self.client.put(f'/api/ledger/{entry}',headers=self.h,json=self.operation_body(row,'101')).status_code,409)
        with unit() as s:self.assertEqual(s.get(Ledger,entry).amount,10000)

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

    def report_records(self,uid):
        from app.db import BusinessProject,ReportArchive
        from datetime import date
        from uuid import uuid4
        with unit(True) as s:
            project=BusinessProject(company_id=self.company,title='Synthetic own project',created_by=uid,request_key=str(uuid4()))
            foreign=BusinessProject(company_id=self.company,title='Synthetic foreign project',created_by=self.uid('admin'),request_key=str(uuid4()))
            archive=ReportArchive(company_id=self.company,title='Synthetic archive',created_by=uid,request_key=str(uuid4()),version=1,status='ready',period_start=date(2026,1,1),period_end=date(2026,1,31))
            s.add_all([project,foreign,archive]);s.flush();return project.id,foreign.id,archive.id

    def test_read_only_reports_hide_all_write_capabilities(self):
        from unittest.mock import patch
        cl,h=self.make_user('finance','read_only_reports');uid=self.uid('read_only_reports');pid,_,aid=self.report_records(uid)
        self.assertEqual(self.save_sections(uid,{k:'view' for k in ['business','reports','rating','pnl','balance']}).status_code,200)
        cl.close();cl,h=self.relogin('read_only_reports')
        projects=cl.get(f'/api/business-projects?company_id={self.company}',headers=h).json()
        self.assertFalse(projects['can_upload'])
        own=next(x for x in projects['items'] if x['id']==pid)
        self.assertFalse(own['can_edit']);self.assertFalse(own['can_delete']);self.assertFalse(own['can_restore'])
        self.assertFalse(cl.get(f'/api/business-templates?company_id={self.company}',headers=h).json()['can_upload'])
        archives=cl.get(f'/api/report-archives?company_id={self.company}',headers=h).json()
        self.assertFalse(archives['can_upload']);self.assertFalse(next(x for x in archives['items'] if x['id']==aid)['can_delete'])
        fs=cl.get('/api/financial-statements',headers=h).json();self.assertFalse(fs['can_upload']);self.assertFalse(fs['can_confirm_revenue_basis'])
        cl.close()

    def test_read_only_archive_cannot_be_changed_through_business_editor(self):
        cl,h=self.make_user('finance','business_editor');uid=self.uid('business_editor');pid,foreign,aid=self.report_records(uid)
        self.assertEqual(self.save_sections(uid,{'reports':'view','business':'edit'}).status_code,200)
        cl.close();cl,h=self.relogin('business_editor')
        projects=cl.get(f'/api/business-projects?company_id={self.company}',headers=h).json()
        self.assertTrue(next(x for x in projects['items'] if x['id']==pid)['can_edit'])
        self.assertFalse(next(x for x in projects['items'] if x['id']==foreign)['can_edit'])
        archive=cl.get(f'/api/report-archives?company_id={self.company}',headers=h).json()
        self.assertFalse(archive['can_upload']);self.assertFalse(next(x for x in archive['items'] if x['id']==aid)['can_delete'])
        r=cl.delete(f'/api/report-archives/{aid}?company_id={self.company}&expected_version=1',headers=h)
        self.assertEqual(r.status_code,403,r.text)
        cl.close()

    def test_shared_source_capabilities_require_both_reports_to_be_editable(self):
        cl,h=self.make_user('finance','shared_source_editor');uid=self.uid('shared_source_editor')
        self.assertEqual(self.save_sections(uid,{'pnl':'edit','balance':'view'}).status_code,200)
        cl.close();cl,h=self.relogin('shared_source_editor')
        self.assertFalse(cl.get('/api/financial-statements',headers=h).json()['can_upload'])
        self.assertEqual(self.save_sections(uid,{'pnl':'edit','balance':'edit'},version=1).status_code,200)
        cl.close();cl,h=self.relogin('shared_source_editor')
        flags=cl.get('/api/financial-statements',headers=h).json()
        self.assertTrue(flags['can_upload']);self.assertTrue(flags['can_confirm_revenue_basis'])
        cl.close()


    def test_read_only_rating_hides_sync_action(self):
        from unittest.mock import patch
        from app.db import Company
        cl,h=self.make_user('finance','rating_reader');uid=self.uid('rating_reader')
        with unit(True) as s:
            cid=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
            s.execute(delete(CompanyUser).where(CompanyUser.user_id==uid))
            s.add(CompanyUser(company_id=cid,user_id=uid,role='finance'))
        saved=self.client.put(f'/api/admin/users/{uid}/sections',headers=self.h,json={'company_id':cid,'version':0,'sections':{'rating':'view'},'reason':'Reader may only view the rating'})
        self.assertEqual(saved.status_code,200,saved.text)
        cl.close();cl,h=self.relogin('rating_reader',company=cid)
        with patch('app.ifrs_rating.download_rating_source',return_value=(b'synthetic','synthetic.xlsx','test','2026-01-01')),patch('app.ifrs_rating.build_rating',return_value={}):
            rating=cl.get('/api/ifrs-rating',headers=h)
        self.assertEqual(rating.status_code,200,rating.text);self.assertFalse(rating.json()['can_sync'])
        self.assertEqual(cl.post('/api/ifrs-rating/sync',headers=h).status_code,403)
        cl.close()

    def test_admin_cannot_be_locked_and_founder_cannot_become_editor(self):
        self.assertEqual(self.save_sections(self.uid('admin'),{'users':'hidden'}).status_code,409)
        cl,h=self.make_user('founder','custom_founder')
        self.assertEqual(self.save_sections(self.uid('custom_founder'),{'ledger':'edit'}).status_code,422)
        cl.close()

def load_tests(loader,tests,pattern):return unittest.TestSuite(SectionAccessTests(n) for n in SectionAccessTests.__dict__ if n.startswith('test_'))
def tearDownModule():base.tearDownModule()
if __name__=='__main__':unittest.main()
