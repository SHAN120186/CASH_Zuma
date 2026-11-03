"""Match existing request payments to bank dates without making a new payment."""
import unittest
from datetime import timedelta
from sqlalchemy import select
from tests.test_bank_statement_api import BankStatementTests
from app.db import Account, Category, Company, Ledger, PaymentRequest, engine, unit


class ActualPaymentMatchTests(BankStatementTests):
    def paid_with_different_due_date(self):
        rid=self.approved()
        paid=self.ledger('600','out',request_id=rid)
        self.assertEqual(paid.status_code,200,paid.text)
        with unit(True) as s:
            s.get(PaymentRequest,rid).due_date += timedelta(days=1)
        return rid

    def test_actual_date_links_existing_payment_without_second_post(self):
        rid=self.paid_with_different_due_date()
        preview=self.preview(self.source()); self.assertEqual(preview.status_code,200,preview.text)
        row=preview.json()['rows'][0]
        self.assertEqual((row['status'],row['request_id']),('paid_match',rid))
        done=self.commit(preview.json()['id']); self.assertEqual(done.status_code,200,done.text)
        self.assertEqual((done.json()['linked_paid'],done.json()['auto_paid']),(1,0))
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger).where(Ledger.request_id==rid)))),1)
        duplicate=self.preview(self.source()); self.assertEqual(duplicate.status_code,200,duplicate.text)
        self.assertEqual(duplicate.json()['rows'][0]['status'],'ambiguous_duplicate')
        self.assertEqual(self.commit(duplicate.json()['id']).json()['auto_paid'],0)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger).where(Ledger.request_id==rid)))),1)

    def test_two_possible_bank_rows_are_not_linked(self):
        self.paid_with_different_due_date()
        p=self.preview(self.source([self.bank_row('1'),self.bank_row('2',line=7)]))
        self.assertEqual(p.status_code,200,p.text)
        self.assertEqual([row['status'] for row in p.json()['rows']],['review','review'])
        self.assertEqual(self.commit(p.json()['id']).json()['linked_paid'],0)

    def test_two_matching_requests_are_not_linked_even_when_one_is_hidden_or_draft(self):
        rid=self.paid_with_different_due_date()
        with unit(True) as s:
            original=s.get(PaymentRequest,rid)
            s.add(PaymentRequest(creator_id=original.creator_id,category_id=original.category_id,
                account_id=original.account_id,counterparty=original.counterparty,counterparty_inn=original.counterparty_inn,
                purpose='Synthetic candidate',amount=original.amount,
                due_date=original.due_date-timedelta(days=1),status='draft'))
        p=self.preview(self.source()); self.assertEqual(p.status_code,200,p.text)
        self.assertEqual(p.json()['rows'][0]['status'],'review')
        self.assertEqual(self.commit(p.json()['id']).json()['linked_paid'],0)

    def test_reversed_existing_payment_cannot_be_linked(self):
        rid=self.paid_with_different_due_date()
        with unit(True) as s:
            entry=s.scalar(select(Ledger).where(Ledger.request_id==rid))
            s.add(Ledger(account_id=entry.account_id,category_id=entry.category_id,kind='in',amount=entry.amount,
                date=entry.date,counterparty=entry.counterparty,reference='SYNTH-REV',note='Synthetic reversal',
                reversal_of=entry.id,creator_id=entry.creator_id))
        p=self.preview(self.source()); self.assertEqual(p.status_code,200,p.text)
        self.assertNotEqual(p.json()['rows'][0]['status'],'paid_match')
        self.assertEqual(self.commit(p.json()['id']).json()['linked_paid'],0)

    def test_reversal_after_preview_invalidates_signed_confirmation(self):
        rid=self.paid_with_different_due_date()
        p=self.preview(self.source()); self.assertEqual(p.status_code,200,p.text)
        self.assertEqual(p.json()['rows'][0]['status'],'paid_match')
        with unit(True) as s:
            entry=s.scalar(select(Ledger).where(Ledger.request_id==rid))
            s.add(Ledger(account_id=entry.account_id,category_id=entry.category_id,kind='in',amount=entry.amount,
                date=entry.date,counterparty=entry.counterparty,reference='SYNTH-REVERSE-AFTER-PREVIEW',
                note='Synthetic reversal',reversal_of=entry.id,creator_id=entry.creator_id))
        result=self.commit(p.json()['id'],confirm_account=True,confirm_currency=True,
                           preview_token=p.json()['preview_token'])
        self.assertEqual(result.status_code,409,result.text)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger).where(Ledger.request_id==rid)))),1)

    def test_actual_date_for_foreign_account_does_not_create_a_match(self):
        rid=self.paid_with_different_due_date()
        with unit(True) as s:
            original=s.get(PaymentRequest,rid)
            other=s.scalar(select(Company.id).where(Company.code=='ZUMA').execution_options(company_unscoped=True))
            account=Account(company_id=other,name='Synthetic foreign',kind='bank',currency='UZS',opening=0,
                opening_date=original.due_date-timedelta(days=1),created_by=original.creator_id)
            s.add(account);s.flush()
            req=PaymentRequest(creator_id=original.creator_id,category_id=original.category_id,account_id=account.id,
                counterparty=original.counterparty,counterparty_inn=original.counterparty_inn,purpose='Synthetic foreign',
                amount=original.amount,due_date=original.due_date,status='paid')
            s.add(req);s.flush()
            s.add(Ledger(account_id=account.id,category_id=req.category_id,kind='out',amount=req.amount,
                date=req.due_date-timedelta(days=1),counterparty=req.counterparty,reference='FOREIGN',
                note='Synthetic payment',request_id=req.id,creator_id=req.creator_id))
        p=self.preview(self.source()); self.assertEqual(p.status_code,200,p.text)
        self.assertEqual((p.json()['rows'][0]['status'],p.json()['rows'][0]['request_id']),('paid_match',rid))

    def test_approved_request_with_different_date_is_not_automatically_paid(self):
        rid=self.approved()
        with unit(True) as s:s.get(PaymentRequest,rid).due_date += timedelta(days=1)
        p=self.preview(self.source()); self.assertEqual(p.status_code,200,p.text)
        self.assertEqual(p.json()['rows'][0]['status'],'review')
        self.assertEqual(self.commit(p.json()['id']).json()['auto_paid'],0)
        with unit() as s:self.assertEqual(s.get(PaymentRequest,rid).status,'approved')


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(ActualPaymentMatchTests(name) for name in ActualPaymentMatchTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
