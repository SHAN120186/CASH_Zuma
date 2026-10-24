"""Department metadata and account-derived request-document requirements; synthetic data only."""
import unittest
from sqlalchemy import select, inspect, text
import tests.test_site as base
from app.db import unit, engine, initialize, PaymentRequest, Ledger


class RequestFormRuleTests(base.SiteTests):
    def cash_account(self):
        response=self.post('/api/accounts',{'name':'Synthetic cash','kind':'cash','currency':'UZS','opening':'1000000','opening_date':self.date})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['id']

    def create_draft(self,account=None,department=''):
        body=self.request_body('100',account=account)
        if department:body['department']=department
        response=self.post('/api/requests',body)
        self.assertEqual(response.status_code,200,response.text)
        return response.json(),body

    def test_departments_roundtrip_and_omitted_update_keeps_selected_department(self):
        row,body=self.create_draft(department='qc')
        rid=row['id'];self.assertEqual(row['department'],'qc');self.assertEqual(row['department_label'],'QC')
        body.update(department='facilities',version=row['version'],reason='Correct the requesting department')
        response=self.client.put(f'/api/requests/{rid}',headers=self.h,json=body)
        self.assertEqual(response.status_code,200,response.text)
        row=response.json();self.assertEqual(row['department'],'facilities');self.assertEqual(row['department_label'],'Хозяйственная часть')
        body.pop('department');body.update(version=row['version'],purpose='Legacy client edits purpose only')
        response=self.client.put(f'/api/requests/{rid}',headers=self.h,json=body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['department'],'facilities')
        listed=next(r for r in self.client.get('/api/requests',headers=self.h).json() if r['id']==rid)
        self.assertEqual(listed['department'],'facilities')
        card=self.row(rid);self.assertEqual(card['department'],'facilities')
        for code in ('ml','engineering','warehouse','production','office',''):
            body.update(department=code,version=card['version'])
            response=self.client.put(f'/api/requests/{rid}',headers=self.h,json=body)
            self.assertEqual(response.status_code,200,response.text)
            card=response.json();self.assertEqual(card['department'],code)

    def test_unknown_department_rejected_without_creating_or_editing_request(self):
        body=self.request_body('100');body['department']='unrecognised_department'
        self.assertEqual(self.post('/api/requests',body).status_code,422)
        row,valid=self.create_draft(department='office');rid=row['id']
        body.update(version=row['version'],reason='Cannot assign unknown department')
        self.assertEqual(self.client.put(f'/api/requests/{rid}',headers=self.h,json=body).status_code,422)
        self.assertEqual(self.row(rid)['department'],'office')

    def test_cash_indent_only_can_be_submitted_checked_approved_and_paid(self):
        self.budget('1000');account=self.cash_account();row,_=self.create_draft(account=account,department='production');rid=row['id']
        self.assertEqual(row['required_documents'],['internal'])
        self.assertEqual(row['missing_documents'],['internal'])
        denied=self.post(f'/api/requests/{rid}/decision',{'action':'submit'})
        self.assertEqual(denied.status_code,409,denied.text)
        self.assertEqual(self.upload(rid,'internal').status_code,200)
        submitted=self.post(f'/api/requests/{rid}/decision',{'action':'submit'})
        self.assertEqual(submitted.status_code,200,submitted.text)
        self.assertEqual(submitted.json()['missing_documents'],[])
        approved=self.approve(rid);self.assertEqual(approved.status_code,200,approved.text)
        self.assertEqual(self.row(rid)['status'],'approved')
        cashier=self.make_user('cashier','cash_form_payer')
        try:
            paid=self.ledger('100','out',reference='CASH-INDENT-ONLY',account_id=account,request_id=rid,client=cashier)
            self.assertEqual(paid.status_code,200,paid.text)
            self.assertEqual(self.row(rid)['status'],'paid')
            with unit() as s:self.assertEqual(s.scalar(select(Ledger).where(Ledger.request_id==rid)).amount,10000)
        finally:cashier[0].close()

    def test_bank_contract_required_and_channel_cannot_spoof_cash(self):
        row,body=self.create_draft();rid=row['id']
        self.assertEqual(row['required_documents'],['internal','contract'])
        self.assertEqual(self.upload(rid,'internal').status_code,200)
        denied=self.post(f'/api/requests/{rid}/decision',{'action':'submit'})
        self.assertEqual(denied.status_code,409,denied.text)
        self.assertEqual(self.row(rid)['missing_documents'],['contract'])
        body['channel']='cash'
        self.assertEqual(self.post('/api/requests',body).status_code,422)
        body.update(version=self.version_of(rid),reason='Attempt mismatched payment channel')
        self.assertEqual(self.client.put(f'/api/requests/{rid}',headers=self.h,json=body).status_code,422)
        self.assertEqual(self.row(rid)['channel'],'bank')
        self.assertEqual(self.row(rid)['missing_documents'],['contract'])

    def test_changing_real_account_recomputes_required_documents_and_keeps_files(self):
        account=self.cash_account();row,body=self.create_draft(account=account);rid=row['id']
        internal=self.upload(rid,'internal');self.assertEqual(internal.status_code,200)
        body.update(account_id=self.acc,channel='bank',version=self.version_of(rid),reason='Correct payment method to bank')
        response=self.client.put(f'/api/requests/{rid}',headers=self.h,json=body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['required_documents'],['internal','contract'])
        self.assertEqual(response.json()['missing_documents'],['contract'])
        self.assertEqual(self.client.get(internal.json()['url'],headers=self.h).status_code,200)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'submit'}).status_code,409)

    def test_optional_cash_contract_can_be_removed_but_indent_stays_required(self):
        self.budget('1000');account=self.cash_account();row,_=self.create_draft(account=account);rid=row['id']
        internal=self.upload(rid,'internal').json();contract=self.upload(rid,'contract').json()
        self.assertEqual(self.post(f'/api/requests/{rid}/decision',{'action':'submit'}).status_code,200)
        self.assertEqual(self.check(rid).status_code,200)
        removed=self.post(f"/api/request-documents/{contract['id']}/remove",{'version':self.version_of(rid),'reason':'Optional contract is not applicable'})
        self.assertEqual(removed.status_code,200,removed.text)
        current=self.row(rid);self.assertIsNone(current['checked_by']);self.assertEqual(current['missing_documents'],[])
        refused=self.post(f"/api/request-documents/{internal['id']}/remove",{'version':self.version_of(rid),'reason':'Required indent must remain attached'})
        self.assertEqual(refused.status_code,409,refused.text)
        # Even a removed optional file remains available in protected version history.
        self.assertEqual(self.client.get(contract['url'],headers=self.h).status_code,200)

    def test_rules_expose_channel_policies_without_breaking_original_bank_field(self):
        response=self.client.get('/api/request-rules',headers=self.h)
        self.assertEqual(response.status_code,200,response.text)
        rules=response.json();self.assertEqual(rules['required_documents'],['internal','contract'])
        self.assertEqual(rules['required_documents_by_channel'],{'bank':['internal','contract'],'cash':['internal']})
        self.assertEqual(set(rules['departments']),{'qc','ml','engineering','warehouse','production','office','facilities'})

    def test_department_migration_is_idempotent_and_preserves_existing_request(self):
        row,_=self.create_draft();rid=row['id']
        before={k:row[k] for k in ('id','status','version','account_id','amount','purpose','project')}
        with engine.begin() as conn:conn.execute(text('ALTER TABLE payment_requests DROP COLUMN department'))
        initialize();initialize()
        with engine.connect() as conn:self.assertIn('department',{c['name'] for c in inspect(conn).get_columns('payment_requests')})
        after=self.row(rid)
        self.assertEqual({k:after[k] for k in before},before)
        self.assertEqual(after['department'],'')


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(RequestFormRuleTests(name) for name in RequestFormRuleTests.__dict__ if name.startswith('test_'))

def tearDownModule():base.tearDownModule()

if __name__=='__main__':unittest.main()
