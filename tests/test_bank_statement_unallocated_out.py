"""Synthetic bank-debit tests; no real files or production data."""
import unittest
from datetime import date, timedelta
from fastapi import HTTPException
from sqlalchemy import select

from tests.test_bank_statement_incoming import IncomingTests
from app.bank_statement_unallocated_out import classify_unallocated_out, process_unallocated_out
from app.cash_report import report
from app.db import Budget, Category, Ledger, PaymentRequest, engine, unit
from app.services import account_balance


class UnallocatedOutTests(IncomingTests):
    def prepare_out(self,s,count=1,status='preview'):
        batch,lines,account,user,_=self.prepared(s,count,status)
        category=Category(company_id=self.company,name='Synthetic unallocated expense',
                          type='outcome',activity='unclassified',cost_group='other')
        s.add(category)
        for line in lines:line.direction='out';line.correspondent_name='Synthetic supplier';line.purpose='Synthetic goods payment'
        s.flush()
        return batch,lines,account,user,category

    def test_external_debit_is_previewed_without_write_then_posted_once(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepare_out(s)
            before=account_balance(s,account)
            preview=classify_unallocated_out(s,batch,lines,account,user,unallocated=True)[lines[0].id]
            self.assertEqual((preview['status'],preview['category_id'],preview['category_source']),
                             ('outgoing_candidate',None,'unclassified_out'))
            self.assertIsNone(s.scalar(select(Ledger.id)))
            self.assertEqual(process_unallocated_out(s,batch,lines,account,user)['review'],1)
            self.assertEqual(account_balance(s,account),before)
            self.assertEqual(process_unallocated_out(s,batch,lines,account,user,cat.id)['auto_outgoing_posted'],1)
            self.assertEqual(account_balance(s,account),before-60000)
            entry=s.scalar(select(Ledger))
            self.assertEqual((entry.kind,entry.category_id,entry.amount),('out',cat.id,60000))
            self.assertEqual(process_unallocated_out(s,batch,lines,account,user,cat.id),{})
            data=report(s,2026,'UZS',self.company,as_of=date.fromisoformat(self.date))
            self.assertEqual(next(g for g in data['groups'] if g['activity']=='unclassified')['values'][8],'-600.00')

    def test_distinct_documents_equal_amount_are_two_debits(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepare_out(s,count=2)
            before=account_balance(s,account)
            self.assertTrue(all(d['status']=='outgoing_candidate' for d in
                classify_unallocated_out(s,batch,lines,account,user,cat.id).values()))
            self.assertEqual(process_unallocated_out(s,batch,lines,account,user,cat.id)['auto_outgoing_posted'],2)
            self.assertEqual(account_balance(s,account),before-120000)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),2)

    def test_own_inn_missing_inn_known_own_account_or_transfer_stay_review(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepare_out(s)
            line=lines[0]
            for value in ('',batch.owner_inn):
                line.correspondent_inn=value
                self.assertEqual(classify_unallocated_out(s,batch,lines,account,user,cat.id)[line.id]['status'],'review')
            line.correspondent_inn='300000002';line.correspondent_account=batch.account_number
            self.assertEqual(classify_unallocated_out(s,batch,lines,account,user,cat.id)[line.id]['status'],'review')
            line.correspondent_account='20200000000000000002';line.purpose='Пополнение корпоративной карты'
            self.assertEqual(classify_unallocated_out(s,batch,lines,account,user,cat.id)[line.id]['status'],'review')
            line.purpose='Пополнение корп.пл.карты'
            self.assertEqual(classify_unallocated_out(s,batch,lines,account,user,cat.id)[line.id]['status'],'review')
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_possible_commissions_percent_or_request_reference_stay_review(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepare_out(s)
            line=lines[0]
            for purpose in ('Погаш. ком. SWIFT', 'Комиссия банка', 'Начисленные %%',
                            'Оплата процентов', 'Test CF-00005', 'Оплата по тарифу bank fee',
                            'Расчетно-кассовое обслуживание'):
                line.purpose=purpose
                self.assertEqual(classify_unallocated_out(s,batch,lines,account,user,cat.id)[line.id]['status'],
                                 'review',purpose)
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_possible_request_even_different_date_or_missing_inn_stays_review(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepare_out(s)
            req=PaymentRequest(creator_id=user.id,category_id=cat.id,account_id=account.id,
                counterparty=lines[0].correspondent_name,counterparty_inn='',amount=lines[0].amount,
                due_date=lines[0].date+timedelta(days=1),purpose='Synthetic request',status='draft')
            s.add(req);s.flush()
            self.assertEqual(classify_unallocated_out(s,batch,lines,account,user,cat.id)[lines[0].id]['status'],'review')
            req.due_date-=timedelta(days=90);req.status='approved';s.flush()
            self.assertEqual(classify_unallocated_out(s,batch,lines,account,user,cat.id)[lines[0].id]['status'],'review')
            req.status='paid';s.flush()
            self.assertEqual(classify_unallocated_out(s,batch,lines,account,user,cat.id)[lines[0].id]['status'],'review')
            self.assertEqual(process_unallocated_out(s,batch,lines,account,user,cat.id)['review'],1)
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_prior_same_document_changed_amount_or_inn_and_manual_ledger_stay_review(self):
        with unit(True) as s:
            prior,prior_lines,account,user,cat=self.prepare_out(s,status='committed')
            # Second fixture uses a distinct synthetic system category name.
            s.delete(cat);s.flush()
            batch,lines,account,user,cat=self.prepare_out(s)
            lines[0].amount+=100;lines[0].correspondent_inn='300000003';lines[0].fingerprint='f'*64;s.flush()
            self.assertEqual(classify_unallocated_out(s,batch,lines,account,user,cat.id)[lines[0].id]['status'],'review')
            prior_lines[0].document_number='DIFFERENT';s.flush()
            existing=Ledger(account_id=account.id,category_id=cat.id,kind='out',amount=60000,
                date=lines[0].date,counterparty='Synthetic supplier',reference=lines[0].document_number,
                note='Synthetic existing entry',creator_id=user.id)
            s.add(existing);s.flush()
            self.assertEqual(process_unallocated_out(s,batch,lines,account,user,cat.id)['review'],1)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),1)

    def test_reversed_existing_transaction_never_posts_again(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepare_out(s)
            old=Ledger(account_id=account.id,category_id=cat.id,kind='out',amount=lines[0].amount,
                date=lines[0].date,counterparty='Synthetic supplier',reference='DOC-0',note='',creator_id=user.id)
            s.add(old);s.flush()
            reverse=Ledger(account_id=account.id,category_id=cat.id,kind='in',amount=old.amount,
                date=old.date,counterparty='',reference='REV-OLD',note='',creator_id=user.id,reversal_of=old.id)
            s.add(reverse);s.flush()
            self.assertEqual(process_unallocated_out(s,batch,lines,account,user,cat.id)['review'],1)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),2)

    def test_rights_opening_bad_source_and_wrong_category_are_enforced(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepare_out(s)
            line=lines[0]
            batch.opening+=1
            self.assertEqual(classify_unallocated_out(s,batch,lines,account,user,cat.id)[line.id]['status'],'review')
            batch.opening-=1;batch.source_ready=False
            self.assertEqual(classify_unallocated_out(s,batch,lines,account,user,cat.id)[line.id]['status'],'review')
            batch.source_ready=True;user.role='accountant'
            self.assertEqual(process_unallocated_out(s,batch,lines,account,user,cat.id)['review'],1)
            user.role='admin'
            with self.assertRaises(HTTPException):classify_unallocated_out(s,batch,lines,account,user,999999)
            ordinary=s.scalar(select(Category).where(Category.company_id==self.company,Category.type=='outcome',Category.activity!='unclassified'))
            with self.assertRaises(HTTPException):classify_unallocated_out(s,batch,lines,account,user,ordinary.id)
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_hard_budget_and_reserved_funds_cannot_be_bypassed(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepare_out(s)
            s.add(Budget(category_id=cat.id,month=self.month,currency='UZS',amount=0,mode='hard',source='Synthetic'))
            s.flush()
            self.assertEqual(process_unallocated_out(s,batch,lines,account,user,cat.id)['review'],1)
            self.assertIsNone(s.scalar(select(Ledger.id)))
            budget=s.scalar(select(Budget).where(Budget.category_id==cat.id));budget.amount=100000000;s.flush()
            req=PaymentRequest(creator_id=user.id,category_id=cat.id,account_id=account.id,counterparty='Other supplier',
                counterparty_inn='300000099',amount=account.opening,due_date=date.fromisoformat(self.date)+timedelta(days=1),
                purpose='Synthetic approved obligation',status='approved')
            s.add(req);s.flush()
            self.assertEqual(process_unallocated_out(s,batch,lines,account,user,cat.id)['review'],1)
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_distinct_prior_document_can_be_followed_by_equal_amount(self):
        with unit(True) as s:
            old,old_lines,account,user,cat=self.prepare_out(s,status='committed')
            self.assertEqual(process_unallocated_out(s,old,old_lines,account,user,cat.id)['auto_outgoing_posted'],1)
            # Reuse the same company-scoped system article.
            from app.bank_statement_models import BankStatementBatch, BankStatementLine
            batch=BankStatementBatch(company_id=self.company,account_id=account.id,created_by=user.id,
                filename='second.xls',sha256='b'*64,account_number=old.account_number,owner_name=old.owner_name,
                owner_inn=old.owner_inn,currency='UZS',period_from=old.period_from,period_to=old.period_to,
                opening=old.opening,closing=old.closing,debit_total=60000,credit_total=0,balance_matches=True,
                declared_totals_match=True,source_ready=True,status='preview')
            s.add(batch);s.flush()
            line=BankStatementLine(company_id=self.company,batch_id=batch.id,account_id=account.id,line=6,
                date=old_lines[0].date,direction='out',amount=60000,document_number='DOC-1',
                correspondent_mfo='00014',correspondent_account='20200000000000000002',
                correspondent_name='Synthetic supplier',correspondent_inn='300000002',purpose='Synthetic goods payment',
                fingerprint='f'*64,occurrence=1,status='review',include_in_daily=True)
            s.add(line);s.flush()
            self.assertEqual(classify_unallocated_out(s,batch,[line],account,user,cat.id)[line.id]['status'],'outgoing_candidate')
            self.assertEqual(process_unallocated_out(s,batch,[line],account,user,cat.id)['auto_outgoing_posted'],1)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),2)

    def test_paid_request_is_ignored_only_when_prior_bank_link_is_proven_active(self):
        with unit(True) as s:
            old,old_lines,account,user,cat=self.prepare_out(s,status='committed')
            req=PaymentRequest(creator_id=user.id,category_id=cat.id,account_id=account.id,
                counterparty='Synthetic supplier',counterparty_inn='300000002',amount=60000,
                due_date=old_lines[0].date,purpose='Synthetic request',status='paid')
            s.add(req);s.flush()
            entry=Ledger(account_id=account.id,category_id=cat.id,kind='out',amount=60000,
                date=old_lines[0].date,counterparty='Synthetic supplier',reference='DOC-0',
                note='',creator_id=user.id,request_id=req.id)
            s.add(entry);s.flush()
            old_lines[0].status='auto_paid';old_lines[0].ledger_id=entry.id;old_lines[0].request_id=req.id
            from app.bank_statement_models import BankStatementBatch, BankStatementLine
            batch=BankStatementBatch(company_id=self.company,account_id=account.id,created_by=user.id,
                filename='new.xls',sha256='c'*64,account_number=old.account_number,owner_name=old.owner_name,
                owner_inn=old.owner_inn,currency='UZS',period_from=old.period_from,period_to=old.period_to,
                opening=old.opening,closing=old.closing,debit_total=60000,credit_total=0,balance_matches=True,
                declared_totals_match=True,source_ready=True,status='preview')
            s.add(batch);s.flush()
            line=BankStatementLine(company_id=self.company,batch_id=batch.id,account_id=account.id,line=6,
                date=old_lines[0].date,direction='out',amount=60000,document_number='DOC-NEW',
                correspondent_mfo='00014',correspondent_account='20200000000000000002',
                correspondent_name='Synthetic supplier',correspondent_inn='300000002',purpose='Synthetic payment',
                fingerprint='d'*64,occurrence=1,status='review',include_in_daily=True)
            s.add(line);s.flush()
            self.assertEqual(classify_unallocated_out(s,batch,[line],account,user,cat.id)[line.id]['status'],'outgoing_candidate')
            old_lines[0].uncertain=True;s.flush()
            self.assertEqual(classify_unallocated_out(s,batch,[line],account,user,cat.id)[line.id]['status'],'review')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(UnallocatedOutTests(name) for name in UnallocatedOutTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
