"""Isolated tests for read-only rematching of approved request payments."""
import unittest
from fastapi import HTTPException
from sqlalchemy import select

from tests.test_bank_statement_auto_integration import AutoBankIntegrationTests
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.bank_statement_reprocess_payments import classify_reprocess_payments, process_reprocess_payments
from app.db import Account, Category, Ledger, PaymentRequest, User, engine, unit
from app.security import scope_user


class ReprocessPaymentTests(AutoBankIntegrationTests):
    def setUp(self):
        super().setUp()
        self.rid=self.approved()
        with unit(True) as s:s.get(Account,self.acc).opening=50000
        raw=self.statement().replace(b'1000000.00',b'500.00').replace(b'999580.00',b'80.00')
        p=self.preview(raw);self.assertEqual(p.status_code,200,p.text)
        result=self.confirmed(p.json(),token=False)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['auto_paid'],0)
        self.batch_id=p.json()['id']

    def context(self,s):
        s.info['company_id']=self.company
        user=s.scalar(select(User).where(User.username=='bank_synthetic_accountant'));scope_user(user,'accountant')
        batch=s.get(BankStatementBatch,self.batch_id);account=s.get(Account,self.acc)
        lines=list(s.scalars(select(BankStatementLine).where(BankStatementLine.batch_id==batch.id)
                             .order_by(BankStatementLine.line)))
        return batch,lines,account,user

    def test_readonly_rematch_and_approved_preview_are_required(self):
        with unit() as s:
            batch,lines,account,user=self.context(s)
            prior=[(l.status,l.request_id,l.ledger_id,l.reason) for l in lines]
            decisions=classify_reprocess_payments(s,batch,lines,account,user)
            self.assertEqual(len(decisions),1)
            decision=next(iter(decisions.values()))
            self.assertEqual((decision['status'],decision['request_id']),('auto_pay_candidate',self.rid))
            self.assertEqual(decision['request_version'],s.get(PaymentRequest,self.rid).version)
            self.assertEqual(prior,[(l.status,l.request_id,l.ledger_id,l.reason) for l in lines])
        with self.assertRaises(HTTPException):
            with unit(True) as s:
                batch,lines,account,user=self.context(s)
                process_reprocess_payments(s,batch,lines,account,user,decisions)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))),0)
        with unit(True) as s:
            c=Category(company_id=self.company,name='Synthetic incoming category',type='income',activity='operating')
            s.add(c);s.flush();incoming_id=c.id
        receipt=self.ledger('200','in',reference='SYN-INCOMING',category_id=incoming_id)
        self.assertEqual(receipt.status_code,200,receipt.text)
        bad={key:{**value,'request_version':value['request_version']+1} for key,value in decisions.items()}
        with self.assertRaises(HTTPException):
            with unit(True) as s:
                batch,lines,account,user=self.context(s)
                process_reprocess_payments(s,batch,lines,account,user,bad)
        with unit(True) as s:
            batch,lines,account,user=self.context(s)
            self.assertEqual(process_reprocess_payments(s,batch,lines,account,user,decisions),1)
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest,self.rid).status,'paid')
            self.assertEqual(len(list(s.scalars(select(Ledger).where(Ledger.request_id==self.rid)))),1)
        with self.assertRaises(HTTPException):
            with unit(True) as s:
                batch,lines,account,user=self.context(s)
                process_reprocess_payments(s,batch,lines,account,user,decisions)

    def test_all_candidates_and_bank_rows_count_towards_uniqueness(self):
        with unit(True) as s:
            batch,lines,account,user=self.context(s)
            payment=next(line for line in lines if line.document_number=='PAY001')
            req=s.get(PaymentRequest,self.rid)
            duplicate=PaymentRequest(creator_id=req.creator_id,category_id=req.category_id,
                account_id=req.account_id,counterparty='Synthetic duplicate',counterparty_inn=req.counterparty_inn,
                purpose='Synthetic duplicate',amount=req.amount,due_date=req.due_date,status='draft')
            s.add(duplicate);s.flush()
            self.assertEqual(classify_reprocess_payments(s,batch,lines,account,user),{})
            s.delete(duplicate);s.flush()
            extra=BankStatementLine(company_id=self.company,batch_id=batch.id,account_id=self.acc,line=30,
                date=payment.date,direction='out',amount=payment.amount,document_number='PAY002',
                correspondent_mfo=payment.correspondent_mfo,correspondent_account=payment.correspondent_account,
                correspondent_name=payment.correspondent_name,correspondent_inn=payment.correspondent_inn,
                purpose=payment.purpose,fingerprint='d'*64,occurrence=1,status='review',reason='',
                include_in_daily=True,uncertain=False)
            s.add(extra);s.flush()
            self.assertEqual(classify_reprocess_payments(s,batch,lines+[extra],account,user),{})

    def test_manual_classification_uncertain_rows_and_roles_are_not_overridden(self):
        with unit(True) as s:
            batch,lines,account,user=self.context(s)
            payment=next(line for line in lines if line.document_number=='PAY001')
            payment.status='reviewed';payment.classification='bank_fee';s.flush()
            self.assertEqual(classify_reprocess_payments(s,batch,lines,account,user),{})
            payment.status='review';payment.classification='';payment.uncertain=True;s.flush()
            self.assertEqual(classify_reprocess_payments(s,batch,lines,account,user),{})
            payment.uncertain=False;s.flush()
            accountant=s.scalar(select(User).where(User.username=='bank_synthetic_accountant'))
            scope_user(accountant,'accountant')
            self.assertEqual(len(classify_reprocess_payments(s,batch,lines,account,accountant)),1)
            finance=s.scalar(select(User).where(User.username=='reviewer'))
            scope_user(finance,'finance')
            self.assertEqual(classify_reprocess_payments(s,batch,lines,account,finance),{})
            accountant._sections={'requests':'hidden'}
            self.assertEqual(classify_reprocess_payments(s,batch,lines,account,accountant),{})

    def test_http_reprocess_uses_incoming_before_exact_payment_and_fee_atomically(self):
        client,headers=self.accountant
        p=client.post(f'/api/bank-statements/{self.batch_id}/reprocess/preview',headers=headers,
                           json={'fee_category_id':self.cat})
        self.assertEqual(p.status_code,200,p.text);self.assertTrue(p.json()['full_reconciliation'],p.text)
        payment=next(row for row in p.json()['rows'] if row['document_number']=='PAY001')
        self.assertEqual((payment['status'],payment['request_id']),('auto_pay_candidate',self.rid))
        self.assertGreater(payment['request_version'],0)
        done=client.post(f'/api/bank-statements/{self.batch_id}/reprocess/commit',headers=headers,
            json={'fee_category_id':self.cat,'preview_token':p.json()['preview_token'],'confirm':True})
        self.assertEqual(done.status_code,200,done.text);self.assertTrue(done.json()['full_reconciliation'],done.text)
        with unit() as s:
            entries=list(s.scalars(select(Ledger)))
            self.assertEqual(len(entries),3)
            self.assertEqual(sum(entry.amount if entry.kind=='in' else -entry.amount for entry in entries),-42000)
            self.assertEqual(s.get(PaymentRequest,self.rid).status,'paid')
            self.assertEqual(len([entry for entry in entries if entry.request_id==self.rid]),1)
        again=client.post(f'/api/bank-statements/{self.batch_id}/reprocess/commit',headers=headers,
            json={'fee_category_id':self.cat,'preview_token':p.json()['preview_token'],'confirm':True})
        self.assertEqual(again.status_code,409,again.text)

    def test_accountant_reprocesses_incoming_and_request_without_general_write(self):
        client,headers=self.accountant
        p=client.post(f'/api/bank-statements/{self.batch_id}/reprocess/preview',headers=headers,
                      json={'fee_category_id':self.cat})
        self.assertEqual(p.status_code,200,p.text);self.assertTrue(p.json()['full_reconciliation'],p.text)
        done=client.post(f'/api/bank-statements/{self.batch_id}/reprocess/commit',headers=headers,
            json={'fee_category_id':self.cat,'preview_token':p.json()['preview_token'],'confirm':True})
        self.assertEqual(done.status_code,200,done.text);self.assertTrue(done.json()['full_reconciliation'],done.text)
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest,self.rid).status,'paid')
            self.assertEqual(len(list(s.scalars(select(Ledger)))),3)
        self.assertEqual(client.post(f'/api/bank-statements/{self.batch_id}/reprocess/commit',headers=headers,
            json={'fee_category_id':self.cat,'preview_token':p.json()['preview_token'],'confirm':True}).status_code,409)

    def test_accountant_reprocess_never_bypasses_self_approval_rule(self):
        client,headers=self.accountant
        with unit(True) as s:
            accountant=s.scalar(select(User).where(User.username=='bank_synthetic_accountant'))
            s.get(PaymentRequest,self.rid).finance_approved_by=accountant.id
        p=client.post(f'/api/bank-statements/{self.batch_id}/reprocess/preview',headers=headers,
                      json={'fee_category_id':self.cat})
        self.assertEqual(p.status_code,200,p.text)
        done=client.post(f'/api/bank-statements/{self.batch_id}/reprocess/commit',headers=headers,
            json={'fee_category_id':self.cat,'preview_token':p.json()['preview_token'],'confirm':True})
        self.assertEqual(done.status_code,200,done.text);self.assertFalse(done.json()['full_reconciliation'])
        self.assertEqual(done.json()['posted'],2)
        self.assertEqual(done.json()['posting_summary']['pending'],
                         {'count':1,'credit_total':'0.00','debit_total':'600.00'})
        self.assertEqual(done.json()['posting_summary']['conservative_closing'],'80.00')
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest,self.rid).status,'approved')
            self.assertIsNone(s.scalar(select(Ledger.id).where(Ledger.request_id==self.rid)))
            self.assertEqual(sorted(entry.kind for entry in s.scalars(select(Ledger))),['in','out'])

    def test_http_reprocess_rejects_changed_request_after_preview(self):
        client,headers=self.accountant
        p=client.post(f'/api/bank-statements/{self.batch_id}/reprocess/preview',headers=headers,
                           json={'fee_category_id':self.cat})
        self.assertEqual(p.status_code,200,p.text);self.assertTrue(p.json()['full_reconciliation'],p.text)
        with unit(True) as s:
            request=s.get(PaymentRequest,self.rid);request.purpose='Synthetic changed approval request'
        done=client.post(f'/api/bank-statements/{self.batch_id}/reprocess/commit',headers=headers,
            json={'fee_category_id':self.cat,'preview_token':p.json()['preview_token'],'confirm':True})
        self.assertEqual(done.status_code,409,done.text)
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest,self.rid).status,'approved')
            self.assertEqual(len(list(s.scalars(select(Ledger)))),0)

    def test_possible_existing_debit_blocks_automatic_second_payment(self):
        with unit(True) as s:
            batch,lines,account,user=self.context(s)
            payment=next(line for line in lines if line.document_number=='PAY001')
            s.add(Ledger(account_id=self.acc,category_id=self.cat,kind='out',amount=payment.amount,
                date=payment.date,counterparty='Synthetic already paid',reference='SYN-OLD',
                note='Synthetic independent debit',creator_id=user.id))
            s.flush()
            self.assertEqual(classify_reprocess_payments(s,batch,lines,account,user),{})

    def test_reprocess_does_not_bypass_separation_of_duties(self):
        client,headers=self.accountant
        with unit(True) as s:
            actor=s.scalar(select(User).where(User.username=='bank_synthetic_accountant'))
            s.get(PaymentRequest,self.rid).finance_approved_by=actor.id
        p=client.post(f'/api/bank-statements/{self.batch_id}/reprocess/preview',headers=headers,
                           json={'fee_category_id':self.cat})
        self.assertEqual(p.status_code,200,p.text)
        done=client.post(f'/api/bank-statements/{self.batch_id}/reprocess/commit',headers=headers,
            json={'fee_category_id':self.cat,'preview_token':p.json()['preview_token'],'confirm':True})
        self.assertEqual(done.status_code,200,done.text);self.assertFalse(done.json()['full_reconciliation'])
        self.assertEqual(done.json()['posted'],2)
        self.assertEqual(done.json()['posting_summary']['pending'],
                         {'count':1,'credit_total':'0.00','debit_total':'600.00'})
        self.assertEqual(done.json()['posting_summary']['conservative_closing'],'80.00')
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest,self.rid).status,'approved')
            self.assertIsNone(s.scalar(select(Ledger.id).where(Ledger.request_id==self.rid)))
            self.assertEqual(sorted(entry.kind for entry in s.scalars(select(Ledger))),['in','out'])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ReprocessPaymentTests(name) for name in ReprocessPaymentTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
