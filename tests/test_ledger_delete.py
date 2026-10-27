import unittest,json
from sqlalchemy import select,func
import tests.test_site as base
from app.db import unit,Ledger,Account,Audit,Document,Receipt
from app.services import account_balance,deleted_ledger_ids

class LedgerDeleteTests(base.SiteTests):
    def entry(self,id):
        return next(x for x in self.client.get('/api/ledger?include_deleted=true',headers=self.h).json() if x['id']==id)
    def remove(self,id,token=None,headers=None,client=None):
        return (client or self.client).request('DELETE',f'/api/ledger/{id}',headers=headers or self.h,json={'expected':token or self.entry(id)['edit_token'],'reason':'Remove synthetic mistaken movement'})
    def balance(self,id=None):
        with unit() as s:return account_balance(s,s.get(Account,id or self.acc),base.FROZEN_TODAY)
    def test_income_expense_and_history_are_consistent(self):
        initial=self.balance()
        for kind in ('in','out'):
            id=self.ledger('100',kind,reference='DELETE-'+kind).json()['id']
            self.assertEqual(self.remove(id).status_code,200)
            self.assertEqual(self.balance(),initial)
            self.assertFalse(any(x['id']==id for x in self.client.get('/api/ledger',headers=self.h).json()))
            self.assertTrue(self.entry(id)['deleted']);self.assertFalse(self.entry(id)['can_edit']);self.assertFalse(self.entry(id)['can_delete'])
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Audit).where(Audit.action=='Удалена операция')),2)
    def test_transfer_reverts_both_accounts(self):
        other=self.post('/api/accounts',{'name':'Synthetic destination','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date}).json()['id']
        before=self.balance();id=self.ledger('100','transfer',reference='DELETE-TRANSFER',to_account_id=other).json()['id']
        response=self.remove(id);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.balance(),before);self.assertEqual(self.balance(other),0)
    def test_already_reversed_pair_is_hidden_without_another_reversal(self):
        id=self.ledger('100',reference='DELETE-REVERSED').json()['id']
        inverse=self.post(f'/api/ledger/{id}/reverse',{'reason':'Synthetic ordinary reversal'}).json()['id']
        response=self.remove(inverse);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(set(response.json()['ids']),{id,inverse})
        with unit() as s:self.assertEqual(s.scalar(select(func.count()).select_from(Ledger)),2)
        self.assertEqual(self.remove(id).status_code,409)
    def test_stale_version_and_csrf_cannot_delete(self):
        id=self.ledger('100',reference='DELETE-STALE').json()['id']
        self.assertEqual(self.remove(id,token='0'*64).status_code,409)
        self.assertEqual(self.remove(id,headers={'X-CSRF-Token':'invalid'}).status_code,403)
        self.assertFalse(self.entry(id)['deleted'])
    def test_only_holding_admin_and_only_selected_company(self):
        id=self.ledger('100',reference='DELETE-SCOPED').json()['id'];token=self.entry(id)['edit_token']
        with unit() as session:original_company=session.get(Account,self.acc).company_id
        other=next(c['id'] for c in self.client.get('/api/bootstrap').json()['companies'] if c['id']!=original_company)
        self.assertEqual(self.remove(id,headers={**self.h,'X-Company-ID':str(other)},token=token).status_code,404)
        client,headers=self.make_user('director','delete_director')
        try:
            self.assertEqual(self.remove(id,token=token,headers=headers,client=client).status_code,403)
            self.assertEqual(client.get('/api/ledger?include_deleted=true',headers=headers).status_code,403)
        finally:client.close()
        self.assertFalse(self.entry(id)['deleted'])
    def test_insufficient_balance_rolls_back_registry_and_inverse(self):
        id=self.ledger('100',reference='DELETE-FUNDED').json()['id']
        self.assertEqual(self.ledger('1000050','out',reference='SPENT-RECEIPT').status_code,200)
        response=self.remove(id);self.assertEqual(response.status_code,409,response.text)
        with unit() as s:
            self.assertEqual(deleted_ledger_ids(s),set());self.assertIsNone(s.scalar(select(Ledger).where(Ledger.reversal_of==id)))
    def test_approved_future_request_stays_funded(self):
        self.budget('2000000','soft')
        id=self.ledger('100',reference='DELETE-RESERVED-INCOME').json()['id']
        rid=self.request('1000050').json()['id'];response=self.approve(rid)
        self.assertEqual(response.status_code,200,response.text)
        response=self.remove(id);self.assertEqual(response.status_code,409,response.text)
        self.assertFalse(self.entry(id)['deleted'])
        with unit() as s:self.assertIsNone(s.scalar(select(Ledger).where(Ledger.reversal_of==id)))
    def test_paid_request_returns_to_approved_and_can_be_paid_again(self):
        self.budget('100000','soft');rid=self.request('100').json()['id'];self.approve(rid)
        id=self.ledger('100','out',reference='DELETE-PAYMENT',request_id=rid).json()['id']
        response=self.remove(id);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.row(rid)['status'],'approved')
        response=self.ledger('100','out',reference='REPAY',request_id=rid);self.assertEqual(response.status_code,200,response.text)
    def test_receipt_and_documents_are_preserved(self):
        rid=self.post('/api/receipts',{'account_id':self.acc,'category_id':self.cat,'counterparty':'Buyer','amount':'100','date':self.date}).json()['id']
        id=self.ledger('100','in',reference='DELETE-RECEIPT',receipt_id=rid).json()['id']
        upload=self.client.post(f'/api/ledger/{id}/document',content=base.PDF,headers={**self.h,'Content-Type':'application/pdf','X-Filename':'synthetic.pdf'})
        self.assertEqual(upload.status_code,200,upload.text)
        response=self.remove(id);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.get(f"/api/documents/{upload.json()['id']}",headers=self.h).status_code,200)
        with unit() as s:
            self.assertEqual(s.get(Receipt,rid).status,'expected')
            self.assertIsNone(s.get(Ledger,id).receipt_id)
            entry=json.loads(s.scalar(select(Audit.detail).where(Audit.action=='Удалена операция')))
            self.assertEqual(entry['before']['receipt_id'],str(rid))
    def test_archived_account_can_be_corrected_and_csv_omits_deleted_pair(self):
        id=self.ledger('100',reference='DELETE-ARCHIVED').json()['id']
        with unit(True) as s:s.get(Account,self.acc).archived=True
        response=self.remove(id);self.assertEqual(response.status_code,200,response.text)
        csv=self.client.get('/api/export/ledger.csv',headers=self.h).text
        self.assertNotIn('DELETE-ARCHIVED',csv)
        with unit() as s:self.assertIsNotNone(s.get(Ledger,id));self.assertIsNotNone(s.scalar(select(Ledger).where(Ledger.reversal_of==id)))

def load_tests(loader,tests,pattern):return unittest.TestSuite(LedgerDeleteTests(n) for n in LedgerDeleteTests.__dict__ if n.startswith('test_'))
def tearDownModule():base.engine.dispose()
if __name__=='__main__':unittest.main()
