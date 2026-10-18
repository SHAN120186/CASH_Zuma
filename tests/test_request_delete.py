"""Holding admin deletion preserves actual ledger and company isolation."""
import unittest
import tests.test_site as base
from sqlalchemy import select
from app.db import unit, Ledger, RequestDocument, PaymentRequest, Audit

class RequestDeleteTests(base.SiteTests):
    def test_admin_deletes_request_and_documents(self):
        rid=self.request(submit=False).json()['id']
        self.upload(rid)
        version=self.version_of(rid)
        response=self.post(f'/api/requests/{rid}/delete',{'version':version,'reason':'Confirmed test cleanup'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(self.row(rid))
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest,rid).status,'deleted')
            event=s.scalar(select(Audit).where(Audit.entity=='request',Audit.entity_id==str(rid),Audit.action=='Удалена заявка'))
            self.assertEqual(event.role,'admin')
            self.assertTrue(list(s.scalars(select(RequestDocument).where(RequestDocument.request_id==rid))))

    def test_nonadmin_cannot_delete_and_stale_version_rejected(self):
        rid=self.request(submit=False).json()['id']
        user,headers=self.make_user('finance','deleter')
        body={'version':self.version_of(rid),'reason':'Confirmed test cleanup'}
        response=self.post(f'/api/requests/{rid}/delete',body,user,headers)
        self.assertEqual(response.status_code,403,response.text)
        body['version']+=100
        self.assertEqual(self.post(f'/api/requests/{rid}/delete',body).status_code,409)

    def test_admin_without_company_assignment_can_edit_and_delete(self):
        from app.db import CompanyUser, User, Company
        from sqlalchemy import delete
        rid=self.request(submit=False).json()['id']
        with unit(True) as s:
            uid=s.scalar(select(User.id).where(User.username=='admin').execution_options(company_unscoped=True))
            s.execute(delete(CompanyUser).where(CompanyUser.user_id==uid).execution_options(company_unscoped=True))
        row=self.row(rid)
        self.assertIn('edit',row['actions'])
        self.assertIn('delete',row['actions'])
        body=self.request_body('333');body.update(version=row['version'],reason='Admin corrects the request',status='draft')
        response=self.client.put(f'/api/requests/{rid}',json=body,headers=self.h)
        self.assertEqual(response.status_code,200,response.text)
        with unit() as s:
            other=s.scalar(select(Company.id).where(Company.id!=self.company,Company.code!='UNASSIGNED'))
        wrong=self.client.post(f'/api/requests/{rid}/delete',json={'version':response.json()['version'],'reason':'Confirmed test cleanup'},headers={**self.h,'X-Company-ID':str(other)})
        self.assertEqual(wrong.status_code,404,wrong.text)
        self.assertIsNotNone(self.row(rid))

    def test_deleting_paid_request_keeps_actual_payment(self):
        self.budget('100000','soft')
        rid=self.request('300').json()['id']
        self.approve(rid)
        # Link an actual movement, as a completed payment does.
        with unit(True) as s:
            r=s.get(PaymentRequest,rid);r.status='paid'
            entry=Ledger(account_id=self.acc,category_id=self.cat,kind='out',amount=30000,
                date=base.today(),counterparty='Supplier',reference='DELETE-PRESERVE',note='',
                request_id=rid,creator_id=r.creator_id)
            s.add(entry);s.flush();eid=entry.id
        response=self.post(f'/api/requests/{rid}/delete',{'version':self.version_of(rid),'reason':'Confirmed test cleanup'})
        self.assertEqual(response.status_code,200,response.text)
        with unit() as s:
            entry=s.get(Ledger,eid)
            self.assertIsNotNone(entry)
            self.assertEqual(entry.amount,30000)
            self.assertEqual(entry.request_id,rid)
        reversed=self.post(f'/api/ledger/{eid}/reverse',{'reason':'Correct the preserved actual payment'})
        self.assertEqual(reversed.status_code,200,reversed.text)
        self.assertIsNone(self.row(rid))
        with unit() as s:self.assertEqual(s.get(PaymentRequest,rid).status,'deleted')

def load_tests(loader, tests, pattern):
    return unittest.TestSuite(RequestDeleteTests(name) for name in RequestDeleteTests.__dict__ if name.startswith('test_'))

def tearDownModule():base.tearDownModule()

if __name__=='__main__':unittest.main()
