import unittest
from sqlalchemy import select,func
import tests.test_site as base
from app.db import unit,Account,Company,CompanyUser,User,Ledger,PaymentRequest
from app.services import account_balance

class ApprovalFundingTests(base.SiteTests):
    def test_empty_account_approval_then_funded_payment_in_each_company(self):
        people={role:self.make_user(role,'fund_'+role) for role in ('accountant','finance','director','cashier')}
        try:
            with unit(True) as s:
                companies=[(c.id,c.code) for c in s.scalars(select(Company).where(Company.code.in_(['ZUMA','UZGERMED'])))]
            for cid,code in companies:
                with unit(True) as s:
                    for role in people:
                        u=s.scalar(select(User).where(User.username=='fund_'+role).execution_options(company_unscoped=True))
                        for membership in s.scalars(select(CompanyUser).where(CompanyUser.user_id==u.id)):
                            s.delete(membership)
                        s.flush()
                        s.add(CompanyUser(company_id=cid,user_id=u.id,role=role))
                h={**self.h,'X-Company-ID':str(cid)}
                bootstrap=self.client.get('/api/bootstrap',headers=h).json()
                cat=next(x['id'] for x in bootstrap['categories'] if x['type']=='outcome')
                inc=next(x['id'] for x in bootstrap['categories'] if x['type']=='income')
                for kind in ('bank','cash'):
                    with self.subTest(company=code,channel=kind):
                        acc=self.post('/api/accounts',{'name':'Empty '+kind,'kind':kind,'currency':'UZS','opening':'0','opening_date':self.date,'allow_overdraft':kind=='bank'},headers=h).json()['id']
                        req=self.request('100',headers=h,account=acc,category=cat)
                        self.assertEqual(req.status_code,200,req.text);rid=req.json()['id']
                        for role,action in [('accountant','check'),('finance','approve'),('director','approve')]:
                            row=self.row(rid,h)
                            if row['status']=='approved':break
                            cl,rh=people[role]
                            r=self.post(f'/api/requests/{rid}/decision',{'action':action,'note':'Synthetic funding approval'},cl,{**rh,'X-Company-ID':str(cid)})
                            self.assertEqual(r.status_code,200,r.text)
                        self.assertEqual(self.row(rid,h)['status'],'approved')
                        # A future receipt is not cash, even when it covers the whole request.
                        self.assertEqual(self.post('/api/receipts',{'account_id':acc,'category_id':inc,'counterparty':'Expected only','amount':'100','date':self.date},headers=h).status_code,200)
                        cl,ph=people['accountant' if kind=='bank' else 'cashier'];ph={**ph,'X-Company-ID':str(cid)}
                        body={'account_id':acc,'category_id':cat,'kind':'out','amount':'100','date':self.date,'reference':'PAY-'+code+kind,'request_id':rid}
                        denied=self.post('/api/ledger',body,cl,ph)
                        self.assertEqual(denied.status_code,409,denied.text)
                        self.assertIn('Недостаточно фактических средств',denied.text)
                        self.assertEqual(self.row(rid,h)['status'],'approved')
                        for value in ('99','1'):
                            r=self.post('/api/ledger',{'account_id':acc,'category_id':inc,'kind':'in','amount':value,'date':self.date,'reference':'IN-'+code+kind+value},headers=h)
                            self.assertEqual(r.status_code,200,r.text)
                            paid=self.post('/api/ledger',body,cl,ph)
                            self.assertEqual(paid.status_code,409 if value=='99' else 200,paid.text)
                        self.assertEqual(self.row(rid,h)['status'],'paid')
                        self.assertEqual(self.post('/api/ledger',body,cl,ph).status_code,409)
                        with unit() as s:
                            a=s.get(Account,acc);self.assertEqual(account_balance(s,a),0)
                            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger).where(Ledger.request_id==rid)),1)
        finally:
            for cl,_ in people.values():cl.close()

def load_tests(loader,tests,pattern):return unittest.TestSuite(ApprovalFundingTests(n) for n in ApprovalFundingTests.__dict__ if n.startswith('test_'))
def tearDownModule():base.engine.dispose()
if __name__=='__main__':unittest.main()
