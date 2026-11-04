"""Financial leaders delete scoped requests without deleting actual payments."""
import unittest
import tests.test_site as base
from sqlalchemy import select
from app.db import unit, Ledger, RequestDocument, PaymentRequest, Audit, User, Company, CompanyUser

class RequestDeleteTests(base.SiteTests):
    def test_admin_deletes_request_and_documents(self):
        rid=self.request(submit=False).json()['id']
        document=self.upload(rid).json()
        version=self.version_of(rid)
        response=self.post(f'/api/requests/{rid}/delete',{'version':version,'reason':'Confirmed test cleanup'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(self.row(rid))
        self.assertNotIn(rid,[r['id'] for r in self.client.get('/api/requests',headers=self.h).json()])
        self.assertEqual(self.client.get(document['url'],headers=self.h).status_code,403)
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest,rid).status,'deleted')
            event=s.scalar(select(Audit).where(Audit.entity=='request',Audit.entity_id==str(rid),Audit.action=='Удалена заявка'))
            self.assertEqual(event.role,'admin')
            self.assertTrue(list(s.scalars(select(RequestDocument).where(RequestDocument.request_id==rid))))

    def test_lower_role_cannot_delete_and_stale_version_rejected(self):
        rid=self.request(submit=False).json()['id']
        user,headers=self.make_user('employee','deleter')
        body={'version':self.version_of(rid),'reason':'Confirmed test cleanup'}
        response=self.post(f'/api/requests/{rid}/delete',body,user,headers)
        self.assertEqual(response.status_code,403,response.text)
        body['version']+=100
        self.assertEqual(self.post(f'/api/requests/{rid}/delete',body).status_code,409)

    def test_finance_and_director_delete_other_authors_and_all_statuses(self):
        for role in ('finance','director'):
            client,headers=self.make_user(role,'delete_'+role)
            try:
                for status in ('draft','pending','returned','approved','cancelled','rejected'):
                    with self.subTest(role=role,status=status):
                        rid=self.request(submit=False).json()['id']
                        with unit(True) as s:s.get(PaymentRequest,rid).status=status
                        row=client.get(f'/api/requests/{rid}',headers=headers).json()
                        self.assertIn('delete',row['actions'])
                        result=self.post(f'/api/requests/{rid}/delete',{'version':row['version'],'reason':'Financial leader removes obsolete request'},client,headers)
                        self.assertEqual(result.status_code,200,result.text)
                        self.assertIsNone(self.row(rid))
                        with unit() as s:
                            event=s.scalar(select(Audit).where(Audit.entity=='request',Audit.entity_id==str(rid),Audit.action=='Удалена заявка'))
                            self.assertEqual(event.role,role)
                            self.assertIsNone(event.acting_for_id)
            finally:client.close()

    def test_unprivileged_roles_and_founder_do_not_gain_delete(self):
        rid=self.request(submit=False).json()['id']
        body={'version':self.version_of(rid),'reason':'Attempt to remove another request'}
        for role in ('accountant','cashier','employee','auditor','operator','investor','material_accountant','procurement','sales','founder'):
            with self.subTest(role=role):
                client,headers=self.make_user(role,'no_delete_'+role)
                try:
                    permissions=client.get('/api/bootstrap',headers=headers).json()['user']['permissions']
                    self.assertNotIn('request_delete',permissions)
                    response=self.post(f'/api/requests/{rid}/delete',body,client,headers)
                    self.assertEqual(response.status_code,403,response.text)
                finally:client.close()
        self.assertIsNotNone(self.row(rid))

    def test_financial_leader_cannot_delete_other_company_or_stale_request(self):
        client,headers=self.make_user('finance','scoped_deleter')
        try:
            with unit() as s:
                other=s.scalar(select(Company.id).where(Company.id!=self.company,Company.code!='UNASSIGNED'))
                uid=s.scalar(select(User.id).where(User.username=='scoped_deleter'))
                foreign_cat=s.scalar(select(base.Category.id).where(base.Category.company_id==other))
                admin=s.scalar(select(User.id).where(User.username=='admin'))
            foreign_account=self.post('/api/accounts',{'name':'Foreign request bank','kind':'bank','currency':'UZS','company_id':other,'opening':'0','opening_date':self.date},headers={**self.h,'X-Company-ID':str(other)}).json()['id']
            with unit(True) as s:
                foreign=PaymentRequest(creator_id=admin,category_id=foreign_cat,account_id=foreign_account,
                    counterparty='Foreign Supplier',amount=100,due_date=base.today(),purpose='Other company request',status='draft',priority='urgent')
                s.add(foreign);s.flush();foreign_id=foreign.id
            body={'version':1,'reason':'Try to remove foreign company record'}
            self.assertEqual(self.post(f'/api/requests/{foreign_id}/delete',body,client,headers).status_code,404)
            self.assertEqual(self.post(f'/api/requests/{foreign_id}/delete',body,client,{**headers,'X-Company-ID':str(other)}).status_code,403)
            rid=self.request(submit=False).json()['id']
            stale={'version':self.version_of(rid)+1,'reason':'Request changed before deletion'}
            self.assertEqual(self.post(f'/api/requests/{rid}/delete',stale,client,headers).status_code,409)
            self.assertIsNotNone(self.row(rid))
        finally:client.close()

    def test_readonly_requests_section_blocks_financial_leader_deletion(self):
        client,headers=self.make_user('director','readonly_deleter')
        rid=self.request(submit=False).json()['id']
        with unit() as s:uid=s.scalar(select(User.id).where(User.username=='readonly_deleter'))
        response=self.client.put(f'/api/admin/users/{uid}/sections',headers=self.h,json={'company_id':self.company,'version':0,'sections':{'requests':'view'},'reason':'Read-only request access for this employee'})
        self.assertEqual(response.status_code,200,response.text)
        client.close();client,headers=self.relogin('readonly_deleter')
        try:
            row=client.get(f'/api/requests/{rid}',headers=headers).json()
            self.assertNotIn('delete',row['actions'])
            self.assertEqual(self.post(f'/api/requests/{rid}/delete',{'version':row['version'],'reason':'Cannot delete read-only request'},client,headers).status_code,403)
            self.assertIsNotNone(self.row(rid))
            response=self.client.put(f'/api/admin/users/{uid}/sections',headers=self.h,json={'company_id':self.company,'version':1,'sections':{'requests':'hidden'},'reason':'Hide requests for this financial leader'})
            self.assertEqual(response.status_code,200,response.text)
            client.close();client,headers=self.relogin('readonly_deleter')
            self.assertEqual(client.get(f'/api/requests/{rid}',headers=headers).status_code,403)
            self.assertEqual(self.post(f'/api/requests/{rid}/delete',{'version':row['version'],'reason':'Cannot delete hidden request'},client,headers).status_code,403)
        finally:client.close()

    def test_section_edit_grant_does_not_allow_lower_role_to_delete(self):
        client,headers=self.make_user('employee','section_editor')
        with unit() as s:uid=s.scalar(select(User.id).where(User.username=='section_editor'))
        response=self.client.put(f'/api/admin/users/{uid}/sections',headers=self.h,json={'company_id':self.company,'version':0,'sections':{'requests':'edit'},'reason':'Allow request editing, not finance role'})
        self.assertEqual(response.status_code,200,response.text)
        client.close();client,headers=self.relogin('section_editor')
        try:
            rid=self.request(submit=False).json()['id']
            row=client.get(f'/api/requests/{rid}',headers=headers).json()
            self.assertIn('edit',row['actions']);self.assertNotIn('delete',row['actions'])
            response=self.post(f'/api/requests/{rid}/delete',{'version':row['version'],'reason':'Section editor cannot delete a request'},client,headers)
            self.assertEqual(response.status_code,403,response.text)
            self.assertIsNotNone(self.row(rid))
        finally:client.close()

    def test_delegated_finance_deletion_records_replaced_employee_and_expires(self):
        from datetime import timedelta
        from app.db import Delegation
        owner,_=self.make_user('finance','absent_finance');owner.close()
        client,headers=self.make_user('employee','acting_deleter')
        with unit() as s:
            replaced=s.scalar(select(User.id).where(User.username=='absent_finance'))
            actor=s.scalar(select(User.id).where(User.username=='acting_deleter'))
        with unit(True) as s:
            delegation=Delegation(company_id=self.company,user_id=actor,replaced_user_id=replaced,role='finance',
                starts_on=base.today(),ends_on=base.today()+timedelta(days=1),reason='Financial director on approved leave',created_by=replaced)
            s.add(delegation);s.flush();did=delegation.id
        try:
            rid=self.request(submit=False).json()['id']
            row=client.get(f'/api/requests/{rid}',headers=headers).json()
            self.assertIn('delete',row['actions'])
            response=self.post(f'/api/requests/{rid}/delete',{'version':row['version'],'reason':'Acting finance removes obsolete request'},client,headers)
            self.assertEqual(response.status_code,200,response.text)
            with unit(True) as s:
                audit=s.scalar(select(Audit).where(Audit.entity=='request',Audit.entity_id==str(rid),Audit.action=='Удалена заявка'))
                self.assertEqual(audit.role,'finance');self.assertEqual(audit.acting_for_id,replaced)
                s.get(Delegation,did).ends_on=base.today()-timedelta(days=1)
            rid=self.request(submit=False).json()['id']
            self.assertEqual(self.post(f'/api/requests/{rid}/delete',{'version':self.version_of(rid),'reason':'Expired finance delegation cannot delete'},client,headers).status_code,403)
        finally:client.close()

    def test_financial_leaders_delete_paid_request_without_changing_fact(self):
        for role in ('finance','director'):
            client,headers=self.make_user(role,'paid_deleter_'+role)
            try:
                rid=self.request(submit=False).json()['id']
                document=self.upload(rid).json()
                with unit(True) as s:
                    r=s.get(PaymentRequest,rid);r.status='paid'
                    entry=Ledger(account_id=self.acc,category_id=self.cat,kind='out',amount=30000,date=base.today(),
                        counterparty='Supplier',reference='PRESERVED-'+role,note='',request_id=rid,creator_id=r.creator_id)
                    s.add(entry);s.flush();eid=entry.id
                before=self.client.get('/api/accounts',headers=self.h).json()
                result=self.post(f'/api/requests/{rid}/delete',{'version':self.version_of(rid),'reason':'Remove obsolete paid request, preserve fact'},client,headers)
                self.assertEqual(result.status_code,200,result.text)
                self.assertEqual(result.json()['preserved_payments'],1)
                self.assertEqual(self.client.get('/api/accounts',headers=self.h).json(),before)
                with unit() as s:
                    self.assertEqual(s.get(Ledger,eid).amount,30000)
                    self.assertEqual(s.get(Ledger,eid).request_id,rid)
                    self.assertIsNotNone(s.get(RequestDocument,document['id']))
                self.assertEqual(client.get(document['url'],headers=headers).status_code,403)
                self.assertIsNone(self.row(rid))
            finally:client.close()

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
