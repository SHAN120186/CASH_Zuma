"""Administrative statement removal; all data in this suite are synthetic."""
import unittest
from datetime import date, timedelta
from sqlalchemy import select
from tests.test_bank_statement_auto_integration import AutoBankIntegrationTests
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.bank_statement_guard import set_statement_reconciliation, unreconciled_batch_ids
from app.db import Account, Category, Ledger, PaymentRequest, Receipt, Audit, User, Company, now, unit, engine
from app.services import account_balance, deleted_ledger_ids


class StatementDeleteTests(AutoBankIntegrationTests):
    def listing(self, client=None, headers=None):
        return (client or self.client).get('/api/bank-statements/admin/batches',headers=headers or self.h)

    def remove(self, item, client=None, headers=None, **updates):
        payload={'confirm':True,'reason':'Synthetic correction of imported statement','expected':item['delete_token']}
        payload.update(updates)
        return (client or self.client).post(f"/api/bank-statements/{item['id']}/delete",json=payload,headers=headers or self.h)

    def import_statement(self, payment=False):
        response=self.preview(self.statement(payment),self.cat)
        self.assertEqual(response.status_code,200,response.text)
        result=self.confirmed(response.json(),self.cat)
        self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(result.json()['full_reconciliation'])
        return response.json()['id']

    def item(self,id):
        response=self.listing(); self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(any(row['id']==id for row in response.json()['items']))
        detail=self.client.get(f'/api/bank-statements/{id}/delete-preview',headers=self.h)
        self.assertEqual(detail.status_code,200,detail.text)
        return detail.json()

    def test_mixed_statement_reverses_created_entries_and_restores_request(self):
        req=self.approved(); batch=self.import_statement(True)
        with unit() as s:paid_version=s.get(PaymentRequest,req).version
        item=self.item(batch); self.assertEqual((item['operation_count'],item['linked_count']),(3,0))
        done=self.remove(item); self.assertEqual(done.status_code,200,done.text)
        self.assertEqual(done.json()['removed_operations'],3)
        with unit() as s:
            s.info['company_id']=self.company
            self.assertEqual(s.get(BankStatementBatch,batch).status,'deleted')
            self.assertEqual(s.get(PaymentRequest,req).status,'approved')
            self.assertGreater(s.get(PaymentRequest,req).version,paid_version)
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),100000000)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),6)
            self.assertEqual(len(deleted_ledger_ids(s)),6)
            self.assertTrue(s.scalar(select(Audit).where(Audit.action=='Удалена банковская выписка')))
        self.assertNotIn(batch,[row['id'] for row in self.listing().json()['items']])
        self.assertEqual(self.remove(item).status_code,409)
        repeat=self.preview(self.statement(True),self.cat)
        self.assertEqual(repeat.status_code,200,repeat.text)
        self.assertFalse(repeat.json()['full_reconciliation'])
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))),6)

    def test_manual_paid_is_reversed_and_request_version_advances(self):
        req=self.approved(); preview=self.preview(self.statement(True))
        self.assertEqual(preview.status_code,200,preview.text)
        batch=preview.json()['id']; line=next(r for r in preview.json()['rows'] if r['status']=='auto_pay_candidate')
        with unit(True) as s:s.get(PaymentRequest,req).due_date+=timedelta(days=1)
        accepted=self.confirmed(preview.json(),token=False);self.assertEqual(accepted.status_code,200,accepted.text)
        with unit() as s:version=s.get(PaymentRequest,req).version
        matched=self.client.post(f"/api/bank-statements/lines/{line['id']}/match",headers=self.h,
            json={'request_id':req,'request_version':version,'confirm':True,'note':'Synthetic manually checked match'})
        self.assertEqual(matched.status_code,200,matched.text);self.assertEqual(matched.json()['status'],'manual_paid')
        with unit() as s:paid_version=s.get(PaymentRequest,req).version
        result=self.remove(self.item(batch));self.assertEqual(result.status_code,200,result.text)
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest,req).status,'approved')
            self.assertGreater(s.get(PaymentRequest,req).version,paid_version)

    def test_created_receipt_restored_and_version_advances(self):
        with unit(True) as s:
            category=s.scalar(select(Category).where(Category.company_id==self.company,Category.type=='income'))
            plan=Receipt(account_id=self.acc,category_id=category.id,counterparty='Synthetic Buyer',inn='300000003',
                amount=20000,due_date=date.fromisoformat(self.date),creator_id=s.get(Account,self.acc).created_by,
                note='Synthetic expected receipt')
            s.add(plan);s.flush();receipt_id=plan.id
        batch=self.import_statement(False)
        with unit() as s:
            receipt=s.get(Receipt,receipt_id);self.assertEqual(receipt.status,'received');received_version=receipt.version
        result=self.remove(self.item(batch));self.assertEqual(result.status_code,200,result.text)
        with unit() as s:
            receipt=s.get(Receipt,receipt_id);self.assertEqual(receipt.status,'expected');self.assertGreater(receipt.version,received_version)

    def test_refuses_other_roles_and_stale_or_invalid_confirmation(self):
        batch=self.import_statement(False); item=self.item(batch)
        self.assertEqual(self.remove(item,client=self.accountant[0],headers=self.accountant[1]).status_code,403)
        self.assertEqual(self.remove(item,expected='0'*64).status_code,409)
        self.assertEqual(self.remove(item,confirm=False).status_code,422)
        self.assertEqual(self.remove(item,reason='x').status_code,422)
        with unit() as s:other_company=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
        foreign_headers={**self.h,'X-Company-ID':str(other_company)}
        self.assertIn(self.client.get(f'/api/bank-statements/{batch}/delete-preview',headers=foreign_headers).status_code,(403,404))
        self.assertIn(self.remove(item,headers=foreign_headers).status_code,(403,404))
        with unit() as s:
            self.assertEqual(s.get(BankStatementBatch,batch).status,'committed')
            self.assertEqual(len(list(s.scalars(select(Ledger)))),2)

    def test_changed_row_invalidates_delete_token(self):
        batch=self.import_statement(False); item=self.item(batch)
        with unit(True) as s:
            row=s.scalar(select(BankStatementLine).where(BankStatementLine.batch_id==batch))
            row.classification='changed'
        self.assertEqual(self.remove(item).status_code,409)
        with unit() as s:self.assertEqual(s.get(BankStatementBatch,batch).status,'committed')

    def test_changed_account_balance_invalidates_delete_token(self):
        batch=self.import_statement(False);item=self.item(batch)
        with unit(True) as s:
            account=s.get(Account,self.acc)
            s.add(Ledger(account_id=self.acc,category_id=self.cat,kind='in',amount=100,date=date.fromisoformat(self.date),
                counterparty='Synthetic',reference='SYNTH-AFTER-PREVIEW',note='Synthetic late operation',creator_id=account.created_by))
        self.assertEqual(self.remove(item).status_code,409)
        with unit() as s:self.assertEqual(s.get(BankStatementBatch,batch).status,'committed')

    def test_later_dependent_statement_blocks_all_reversals(self):
        batch=self.import_statement(False)
        with unit(True) as s:
            source=s.get(BankStatementBatch,batch)
            later=BankStatementBatch(company_id=source.company_id,account_id=source.account_id,created_by=source.created_by,
                filename='synthetic-later.xls',sha256='a'*64,account_number=source.account_number,owner_name=source.owner_name,
                owner_inn=source.owner_inn,currency=source.currency,period_from=source.period_to+timedelta(days=1),
                period_to=source.period_to+timedelta(days=1),opening=source.closing,closing=source.closing,debit_total=0,
                credit_total=0,balance_matches=True,source_ready=True,status='committed',committed_at=now())
            s.add(later)
        result=self.remove(self.item(batch)); self.assertEqual(result.status_code,409,result.text)
        with unit() as s:
            self.assertEqual(s.get(BankStatementBatch,batch).status,'committed')
            self.assertEqual(len(list(s.scalars(select(Ledger)))),2)

    def test_delete_clears_only_its_unreconciled_guard(self):
        preview=self.preview(self.statement(False));self.assertEqual(preview.status_code,200,preview.text)
        accepted=self.confirmed(preview.json(),token=False);self.assertEqual(accepted.status_code,200,accepted.text)
        batch=preview.json()['id']
        with unit(True) as s:
            source=s.get(BankStatementBatch,batch)
            other=BankStatementBatch(company_id=source.company_id,account_id=source.account_id,created_by=source.created_by,
                filename='synthetic-guard.xls',sha256='9'*64,account_number=source.account_number,owner_name=source.owner_name,
                owner_inn=source.owner_inn,currency=source.currency,period_from=source.period_from,period_to=source.period_to,
                opening=source.opening,closing=source.closing,debit_total=0,credit_total=0,balance_matches=True,
                source_ready=True,status='committed',committed_at=now())
            s.add(other);s.flush();other_id=other.id;s.info['company_id']=self.company
            set_statement_reconciliation(s,other,False,s.get(User,source.created_by))
        result=self.remove(self.item(batch));self.assertEqual(result.status_code,200,result.text)
        with unit() as s:
            s.info['company_id']=self.company
            self.assertEqual(unreconciled_batch_ids(s),{other_id})

    def test_existing_correction_requires_manual_review(self):
        batch=self.import_statement(False)
        with unit(True) as s:
            original=s.scalar(select(Ledger).where(Ledger.kind=='out'))
            s.add(Ledger(account_id=original.account_id,category_id=original.category_id,kind='in',amount=original.amount,
                date=original.date,counterparty=original.counterparty,reference='SYNTH-REVERSE',note='Synthetic correction',
                reversal_of=original.id,creator_id=original.creator_id))
        result=self.remove(self.item(batch)); self.assertEqual(result.status_code,409,result.text)
        with unit() as s:
            self.assertEqual(s.get(BankStatementBatch,batch).status,'committed')
            self.assertEqual(len(list(s.scalars(select(Ledger)))),3)

    def test_already_deleted_operation_is_not_reversed_twice(self):
        batch=self.import_statement(False)
        with unit() as s:entry=s.scalar(select(Ledger).where(Ledger.kind=='out'));entry_id=entry.id
        listed=next(x for x in self.client.get('/api/ledger?include_deleted=true',headers=self.h).json() if x['id']==entry_id)
        first=self.client.request('DELETE',f'/api/ledger/{entry_id}',headers=self.h,
            json={'expected':listed['edit_token'],'reason':'Synthetic prior individual deletion'})
        self.assertEqual(first.status_code,200,first.text)
        result=self.remove(self.item(batch)); self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['removed_operations'],1)
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))),4)
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),100000000)

    def test_preexisting_linked_operation_is_not_deleted(self):
        batch=self.import_statement(False)
        with unit(True) as s:
            original=s.get(BankStatementBatch,batch)
            existing=Ledger(account_id=self.acc,category_id=self.cat,kind='in',amount=500,date=original.period_to,
                counterparty='Synthetic independent payment',reference='PREEXIST',note='Synthetic independent entry',creator_id=original.created_by)
            s.add(existing);s.flush();entry_id=existing.id
            s.add(BankStatementLine(company_id=original.company_id,batch_id=batch,account_id=self.acc,line=99,
                date=original.period_to,direction='in',amount=500,document_number='PREEXIST',correspondent_mfo='0',
                correspondent_account='0',correspondent_name='Synthetic',correspondent_inn='300000099',purpose='Synthetic',
                fingerprint='e'*64,status='income_matched',include_in_daily=False,ledger_id=entry_id,reason='',review_note=''))
        item=self.item(batch); self.assertEqual(item['linked_count'],1)
        result=self.remove(item); self.assertEqual(result.status_code,200,result.text)
        with unit() as s:
            self.assertIsNotNone(s.get(Ledger,entry_id))
            self.assertIsNone(s.scalar(select(Ledger).where(Ledger.reversal_of==entry_id)))
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),100000500)

    def test_linked_internal_transfer_is_preserved(self):
        batch=self.import_statement(False)
        account=self.post('/api/accounts',{'name':'Synthetic transfer destination','kind':'bank','currency':'UZS','opening':'0','opening_date':self.date})
        self.assertEqual(account.status_code,200,account.text);target=account.json()['id']
        with unit(True) as s:
            original=s.get(BankStatementBatch,batch)
            transfer=Ledger(account_id=self.acc,to_account_id=target,kind='transfer',amount=500,date=original.period_to,
                counterparty='',reference='PREEXIST-TRANSFER',note='Synthetic independent transfer',creator_id=original.created_by)
            s.add(transfer);s.flush();entry_id=transfer.id
            s.add(BankStatementLine(company_id=original.company_id,batch_id=batch,account_id=self.acc,line=100,
                date=original.period_to,direction='out',amount=500,document_number='TRANSFER',correspondent_mfo='0',
                correspondent_account='0',correspondent_name='Synthetic',correspondent_inn='300000001',purpose='Synthetic',
                fingerprint='f'*64,status='manual_transfer_match',include_in_daily=True,ledger_id=entry_id,reason='',review_note=''))
        result=self.remove(self.item(batch));self.assertEqual(result.status_code,200,result.text)
        with unit() as s:
            self.assertIsNone(s.scalar(select(Ledger).where(Ledger.reversal_of==entry_id)))
            self.assertEqual(account_balance(s,s.get(Account,target)),500)

    def test_external_statement_link_prevents_deleting_owned_operation(self):
        batch=self.import_statement(False)
        with unit(True) as s:
            original=s.get(BankStatementBatch,batch)
            duplicate=BankStatementBatch(company_id=original.company_id,account_id=original.account_id,created_by=original.created_by,
                filename='synthetic-link.xls',sha256='c'*64,account_number=original.account_number,owner_name=original.owner_name,
                owner_inn=original.owner_inn,currency=original.currency,period_from=original.period_from,period_to=original.period_to,
                opening=original.opening,closing=original.closing,debit_total=0,credit_total=0,balance_matches=True,
                source_ready=True,status='committed',committed_at=now())
            s.add(duplicate);s.flush()
            entry=s.scalar(select(Ledger).where(Ledger.kind=='in'))
            s.add(BankStatementLine(company_id=original.company_id,batch_id=duplicate.id,account_id=self.acc,line=1,
                date=original.period_to,direction='in',amount=entry.amount,document_number='INC001',correspondent_mfo='0',
                correspondent_account='0',correspondent_name='Synthetic',correspondent_inn='300000003',purpose='Synthetic',
                fingerprint='d'*64,status='income_matched',include_in_daily=False,ledger_id=entry.id,reason='',review_note=''))
        result=self.remove(self.item(batch)); self.assertEqual(result.status_code,409,result.text)
        self.assertIn('другой выпиской',result.json()['detail'])
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))),2)

    def test_overlapping_statement_without_movements_can_be_removed_first(self):
        batch=self.import_statement(False)
        with unit(True) as s:
            source=s.get(BankStatementBatch,batch)
            duplicate=BankStatementBatch(company_id=source.company_id,account_id=source.account_id,created_by=source.created_by,
                filename='synthetic-overlap.xls',sha256='b'*64,account_number=source.account_number,owner_name=source.owner_name,
                owner_inn=source.owner_inn,currency=source.currency,period_from=source.period_from,period_to=source.period_to,
                opening=source.opening,closing=source.closing,debit_total=0,credit_total=0,balance_matches=True,
                source_ready=True,status='committed',committed_at=now())
            s.add(duplicate);s.flush();other=duplicate.id
        result=self.remove(self.item(batch)); self.assertEqual(result.status_code,409,result.text)
        result=self.remove(self.item(other)); self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['removed_operations'],0)
        result=self.remove(self.item(batch)); self.assertEqual(result.status_code,200,result.text)

    def test_later_spending_rolls_back_every_reversal(self):
        with unit(True) as s:s.get(Account,self.acc).opening=0
        raw=self.statement(False).replace(b'1000000.00',b'0.00').replace(b'1000180.00',b'180.00')
        p=self.preview(raw,self.cat); self.assertEqual(p.status_code,200,p.text)
        self.assertEqual(self.confirmed(p.json(),self.cat).status_code,200)
        with unit(True) as s:
            s.add(Ledger(account_id=self.acc,category_id=self.cat,kind='out',amount=15000,date=s.get(Account,self.acc).opening_date,
                counterparty='Synthetic',reference='SYNTH-LATER',note='Synthetic later expense',creator_id=s.get(BankStatementBatch,p.json()['id']).created_by))
        result=self.remove(self.item(p.json()['id'])); self.assertEqual(result.status_code,409,result.text)
        with unit() as s:
            s.info['company_id']=self.company
            self.assertEqual(s.get(BankStatementBatch,p.json()['id']).status,'committed')
            self.assertEqual(len(list(s.scalars(select(Ledger)))),3)
            self.assertFalse(deleted_ledger_ids(s))


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(StatementDeleteTests(name) for name in StatementDeleteTests.__dict__ if name.startswith('test_'))

def tearDownModule():
    engine.dispose()
