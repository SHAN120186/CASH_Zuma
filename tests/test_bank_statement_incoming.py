"""Synthetic-only isolated integration tests for incoming reconciliation."""
import unittest
from datetime import date, timedelta
from sqlalchemy import select
from fastapi import HTTPException

from tests.test_site import SiteTests
from app.bank_statement_incoming import classify_incoming, opening_matches, process_incoming
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.cash_report import report
from app.db import Account, Category, Ledger, Receipt, User, engine, unit
from app.services import account_balance
from app.security import scope_user


class IncomingTests(SiteTests):
    def prepared(self, s, count=1, status='preview'):
        s.info['company_id'] = self.company
        account = s.get(Account, self.acc)
        user = s.scalar(select(User).where(User.username == 'admin').execution_options(company_unscoped=True))
        cat = s.scalar(select(Category).where(Category.company_id == self.company, Category.type == 'income'))
        day = date.fromisoformat(self.date)
        batch = BankStatementBatch(company_id=self.company, account_id=account.id, created_by=user.id,
            filename='test.xls', sha256='a'*64, account_number='20200000000000000001',
            owner_name='Synthetic company', owner_inn='300000001', currency='UZS', period_from=day,
            period_to=day, opening=account.opening, closing=account.opening + 60000*count,
            debit_total=0, credit_total=60000*count, balance_matches=True, declared_totals_match=True,
            source_ready=True, status=status)
        s.add(batch); s.flush()
        lines = []
        for i in range(count):
            line = BankStatementLine(company_id=self.company, batch_id=batch.id, account_id=account.id,
                line=6+i, date=day, direction='in', amount=60000, document_number=f'DOC-{i}',
                correspondent_mfo='00014', correspondent_account='20200000000000000002',
                correspondent_name='Synthetic buyer', correspondent_inn='300000002', purpose='Test',
                fingerprint=f'{i:064x}', occurrence=1, status='review', include_in_daily=True)
            s.add(line); lines.append(line)
        s.flush()
        return batch, lines, account, user, cat

    def add_plan(self, s, account, cat):
        plan=Receipt(account_id=account.id, category_id=cat.id, counterparty='Synthetic buyer',
            inn='300000002', amount=60000, due_date=date.fromisoformat(self.date),
            creator_id=account.created_by, note='Synthetic planned receipt')
        s.add(plan); s.flush(); return plan

    def test_unique_expected_receipt_posted_once_and_reported(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s)
            plan=self.add_plan(s,account,cat)
            preview=classify_incoming(s,batch,lines,account,user)
            self.assertEqual(preview[lines[0].id]['status'],'income_candidate')
            self.assertEqual(preview[lines[0].id]['receipt_id'],plan.id)
            self.assertEqual(plan.status,'expected')
            self.assertEqual(account_balance(s,account),account.opening)
            result=process_incoming(s,batch,lines,account,user)
            self.assertEqual(result['auto_income_posted'],1)
            self.assertEqual(plan.status,'received')
            self.assertEqual(account_balance(s,account),account.opening+60000)
            self.assertEqual(process_incoming(s,batch,lines,account,user),{})
            self.assertEqual(len(list(s.scalars(select(Ledger).where(Ledger.receipt_id==plan.id)))),1)
            data=report(s,2026,'UZS',self.company,as_of=date.fromisoformat(self.date))
            self.assertEqual(next(r for r in data['rows'] if r['category_id']==cat.id and r['kind']=='in')['values'][8],'600.00')

    def test_unallocated_preview_does_not_create_or_post(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s)
            preview=classify_incoming(s,batch,lines,account,user,unallocated=True)[lines[0].id]
            self.assertEqual((preview['status'],preview['category_id'],preview['category_source']),
                             ('income_candidate',None,'unclassified'))
            self.assertIsNone(s.scalar(select(Ledger.id)))
            self.assertEqual(process_incoming(s,batch,lines,account,user)['review'],1)
            self.assertIsNone(s.scalar(select(Ledger.id)))
            self.assertEqual(process_incoming(s,batch,lines,account,user,cat.id)['auto_income_posted'],1)

    def test_bank_payer_cannot_post_without_reviewed_decision(self):
        self.stage_user('check')
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s)
            user=s.scalar(select(User).where(User.username=='checker'))
            scope_user(user,'accountant')
            self.assertEqual(classify_incoming(s,batch,lines,account,user,cat.id)[lines[0].id]['status'],'income_candidate')
            self.assertEqual(process_incoming(s,batch,lines,account,user,cat.id)['review'],1)
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_existing_manual_operation_links_unique_plan_without_new_ledger(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s)
            plan=self.add_plan(s,account,cat)
            entry=Ledger(account_id=account.id,category_id=cat.id,kind='in',amount=60000,
                date=date.fromisoformat(self.date),counterparty='Synthetic buyer',reference='DOC-0',
                note='',creator_id=user.id)
            s.add(entry); s.flush()
            self.assertTrue(opening_matches(s,batch,account))
            self.assertEqual(classify_incoming(s,batch,lines,account,user)[lines[0].id]['status'],'income_matched_candidate')
            self.assertEqual(process_incoming(s,batch,lines,account,user)['income_matched'],1)
            self.assertEqual(entry.receipt_id,plan.id); self.assertEqual(plan.status,'received')
            self.assertEqual(account_balance(s,account),account.opening+60000)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),1)

    def test_ambiguous_manual_or_reversed_operation_never_posts(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s)
            entry=Ledger(account_id=account.id,category_id=cat.id,kind='in',amount=60000,
                date=date.fromisoformat(self.date),counterparty='Different name',reference='OTHER',note='',creator_id=user.id)
            s.add(entry);s.flush()
            self.assertEqual(process_incoming(s,batch,lines,account,user,cat.id)['review'],1)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),1)
            entry.reference='DOC-0';entry.counterparty='Synthetic buyer'
            reverse=Ledger(account_id=account.id,category_id=cat.id,kind='out',amount=60000,
                date=entry.date,counterparty='',reference='REV-1',note='',creator_id=user.id,reversal_of=entry.id)
            s.add(reverse);s.flush()
            self.assertEqual(process_incoming(s,batch,lines,account,user,cat.id)['review'],1)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),2)

    def test_duplicate_own_inn_wrong_opening_and_missing_rights(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s,count=2)
            lines[1].document_number=lines[0].document_number
            self.assertTrue(all(d['status']=='review' for d in classify_incoming(s,batch,lines,account,user,cat.id).values()))
            lines[1].correspondent_inn='300000003';lines[0].correspondent_inn=batch.owner_inn
            self.assertEqual(classify_incoming(s,batch,lines,account,user,cat.id)[lines[0].id]['status'],'review')
            batch.source_ready=False
            self.assertTrue(all(d['status']=='review' for d in classify_incoming(s,batch,lines,account,user,cat.id).values()))
            batch.source_ready=True
            batch.opening+=1
            self.assertTrue(all(d['status']=='review' for d in classify_incoming(s,batch,lines,account,user,cat.id).values()))
            batch.opening-=1
            user._permissions=frozenset({'ledger','pay_cash'})
            self.assertEqual(process_incoming(s,batch,lines,account,user,cat.id)['review'],2)
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_duplicate_historical_statement_or_plan_stays_for_review(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s,status='committed')
            self.add_plan(s,account,cat);self.add_plan(s,account,cat)
            self.assertEqual(classify_incoming(s,batch,lines,account,user)[lines[0].id]['status'],'review')
            for plan in list(s.scalars(select(Receipt))):s.delete(plan)
            s.flush()
            another=BankStatementLine(company_id=self.company,batch_id=batch.id,account_id=account.id,
                line=8,date=lines[0].date,direction='in',amount=60000,document_number='OTHER',
                correspondent_mfo='00014',correspondent_account='20200000000000000002',
                correspondent_name='Synthetic buyer',correspondent_inn='300000002',purpose='Test',
                fingerprint='f'*64,occurrence=1,status='review',include_in_daily=True)
            s.add(another);s.flush()
            self.assertEqual(classify_incoming(s,batch,lines,account,user,cat.id)[lines[0].id]['status'],'review')
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_cross_company_category_is_rejected(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s)
            foreign=s.scalar(select(Category.id).where(Category.company_id!=self.company,
                Category.type=='income').execution_options(company_unscoped=True))
            if foreign is None:
                # Synthetic ID suffices to verify no arbitrary category is accepted.
                foreign=999999
            with self.assertRaises(HTTPException):
                classify_incoming(s,batch,lines,account,user,foreign)
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_nearby_manual_entry_or_plan_cannot_create_unallocated_duplicate(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s)
            plan=self.add_plan(s,account,cat);plan.due_date+=timedelta(days=1);s.flush()
            self.assertEqual(classify_incoming(s,batch,lines,account,user,cat.id)[lines[0].id]['status'],'review')
            s.delete(plan);s.flush()
            entry=Ledger(account_id=account.id,category_id=cat.id,kind='in',amount=60000,
                date=date.fromisoformat(self.date)+timedelta(days=1),counterparty='Synthetic buyer',
                reference='Other ref',note='',creator_id=user.id)
            s.add(entry);s.flush()
            self.assertEqual(process_incoming(s,batch,lines,account,user,cat.id)['review'],1)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),1)

    def test_two_bank_rows_cannot_claim_the_same_existing_ledger(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s,count=2)
            lines[1].correspondent_inn='300000003'
            lines[1].document_number=lines[0].document_number
            entry=Ledger(account_id=account.id,category_id=cat.id,kind='in',amount=60000,
                date=date.fromisoformat(self.date),counterparty='Synthetic buyer',reference='DOC-0',
                note='',creator_id=user.id)
            s.add(entry);s.flush()
            decisions=classify_incoming(s,batch,lines,account,user,cat.id)
            self.assertTrue(all(d['status']=='review' for d in decisions.values()))
            self.assertEqual(process_incoming(s,batch,lines,account,user,cat.id)['review'],2)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),1)

    def test_different_documents_equal_amount_are_separate_without_a_plan(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s,count=2)
            decisions=classify_incoming(s,batch,lines,account,user,cat.id)
            self.assertTrue(all(d['status']=='income_candidate' for d in decisions.values()))
            self.assertEqual(process_incoming(s,batch,lines,account,user,cat.id)['auto_income_posted'],2)
            self.assertEqual(account_balance(s,account),account.opening+120000)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),2)

    def test_equal_amount_documents_cannot_both_close_one_plan(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s,count=2)
            plan=self.add_plan(s,account,cat)
            decisions=classify_incoming(s,batch,lines,account,user,cat.id)
            self.assertTrue(all(d['status']=='review' for d in decisions.values()))
            self.assertEqual(process_incoming(s,batch,lines,account,user,cat.id)['review'],2)
            self.assertEqual(plan.status,'expected')
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_prior_posted_different_document_does_not_block_equal_amount(self):
        with unit(True) as s:
            old,old_lines,account,user,cat=self.prepared(s,status='committed')
            self.assertEqual(process_incoming(s,old,old_lines,account,user,cat.id)['auto_income_posted'],1)
            batch,lines,account,user,cat=self.prepared(s)
            lines[0].document_number='DOC-1';lines[0].fingerprint='f'*64;s.flush()
            self.assertEqual(classify_incoming(s,batch,lines,account,user,cat.id)[lines[0].id]['status'],'income_candidate')
            self.assertEqual(process_incoming(s,batch,lines,account,user,cat.id)['auto_income_posted'],1)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),2)

    def test_same_document_different_amount_in_prior_statement_or_ledger_is_reviewed(self):
        with unit(True) as s:
            old,old_lines,account,user,cat=self.prepared(s,status='committed')
            batch,lines,account,user,cat=self.prepared(s)
            lines[0].amount=70000;lines[0].correspondent_inn='300000003';lines[0].fingerprint='f'*64;s.flush()
            self.assertEqual(classify_incoming(s,batch,lines,account,user,cat.id)[lines[0].id]['status'],'review')
            old_lines[0].document_number='different';s.flush()
            entry=Ledger(account_id=account.id,category_id=cat.id,kind='in',amount=60000,
                date=lines[0].date,counterparty='Synthetic buyer',reference='DOC-0',note='',creator_id=user.id)
            s.add(entry);s.flush()
            self.assertEqual(process_incoming(s,batch,lines,account,user,cat.id)['review'],1)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),1)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(IncomingTests(name) for name in IncomingTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
