"""Synthetic matching tests; no real bank data or production database."""
import unittest
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from tests.test_bank_statement_incoming import IncomingTests
from app.bank_statement_existing import classify_existing, process_existing
from app.bank_statement_models import BankStatementLine
from app.db import Ledger, engine, unit
from app.services import account_balance


class ExistingOperationTests(IncomingTests):
    def existing(self, s, line, account, user, category, kind='out'):
        line.direction = kind
        entry=Ledger(account_id=account.id,category_id=category.id,kind=kind,
            amount=line.amount,date=line.date,counterparty=line.correspondent_name,
            reference=line.document_number,note='Synthetic existing entry',creator_id=user.id)
        s.add(entry);s.flush();return entry

    def test_exact_independent_outgoing_is_linked_without_new_operation(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s)
            entry=self.existing(s,lines[0],account,user,cat)
            balance=account_balance(s,account)
            decision=classify_existing(s,batch,lines,account,user,True)[lines[0].id]
            self.assertEqual((decision['status'],decision['ledger_id']),('other_matched_candidate',entry.id))
            self.assertEqual(process_existing(s,batch,lines,account,user,True)['other_matched'],1)
            self.assertEqual(lines[0].ledger_id,entry.id)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),1)
            self.assertEqual(account_balance(s,account),balance)
            self.assertEqual(process_existing(s,batch,lines,account,user,True),{})

    def test_exact_incoming_links_but_possible_receipt_blocks(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s)
            entry=self.existing(s,lines[0],account,user,cat,'in')
            self.assertEqual(classify_existing(s,batch,lines,account,user,True)[lines[0].id]['status'],'other_matched_candidate')
            self.add_plan(s,account,cat)
            self.assertEqual(classify_existing(s,batch,lines,account,user,True)[lines[0].id]['status'],'review')
            self.assertEqual(process_existing(s,batch,lines,account,user,True),{})
            self.assertEqual(len(list(s.scalars(select(Ledger)))),1)

    def test_mismatch_reversal_already_linked_and_own_inn_are_not_linked(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s,status='committed')
            entry=self.existing(s,lines[0],account,user,cat)
            entry.amount+=1;s.flush()
            self.assertEqual(classify_existing(s,batch,lines,account,user,True)[lines[0].id]['status'],'review')
            entry.amount-=1;entry.counterparty='Another recipient';s.flush()
            self.assertEqual(classify_existing(s,batch,lines,account,user,True)[lines[0].id]['status'],'review')
            entry.counterparty=lines[0].correspondent_name
            reverse=Ledger(account_id=account.id,category_id=cat.id,kind='in',amount=entry.amount,
                date=entry.date,counterparty='',reference='REV-SYN',note='',creator_id=user.id,reversal_of=entry.id)
            s.add(reverse);s.flush()
            self.assertEqual(classify_existing(s,batch,lines,account,user,True)[lines[0].id]['status'],'review')
            s.delete(reverse);s.flush()
            lines[0].correspondent_inn=batch.owner_inn
            self.assertEqual(classify_existing(s,batch,lines,account,user,True)[lines[0].id]['status'],'review')
            self.assertEqual(process_existing(s,batch,lines,account,user,True),{})

    def test_two_rows_or_previous_link_cannot_claim_same_ledger(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s,count=2,status='committed')
            entry=self.existing(s,lines[0],account,user,cat)
            lines[1].direction='out';lines[1].document_number=lines[0].document_number
            lines[1].correspondent_inn='300000003';s.flush()
            results=classify_existing(s,batch,lines,account,user,True)
            self.assertTrue(all(d['status']=='review' for d in results.values()))
            self.assertEqual(process_existing(s,batch,lines,account,user,True),{})
            lines[1].document_number='different';lines[1].ledger_id=entry.id
            lines[1].status='other_matched';s.flush()
            self.assertEqual(classify_existing(s,batch,lines,account,user,True)[lines[0].id]['status'],'review')

    def test_equal_amounts_with_distinct_documents_link_distinct_operations(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s,count=2)
            first=self.existing(s,lines[0],account,user,cat)
            second=self.existing(s,lines[1],account,user,cat)
            results=classify_existing(s,batch,lines,account,user,True)
            self.assertEqual({results[line.id]['ledger_id'] for line in lines},{first.id,second.id})
            balance=account_balance(s,account)
            self.assertEqual(process_existing(s,batch,lines,account,user,True)['other_matched'],2)
            self.assertEqual(account_balance(s,account),balance)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),2)

    def test_accountant_can_link_existing_but_view_only_and_wrong_company_cannot(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s)
            self.existing(s,lines[0],account,user,cat)
            user.role='accountant'
            self.assertEqual(classify_existing(s,batch,lines,account,user,True)[lines[0].id]['status'],'other_matched_candidate')
            user._sections={'ledger':'view'}
            self.assertEqual(classify_existing(s,batch,lines,account,user,True)[lines[0].id]['status'],'review')
            self.assertEqual(process_existing(s,batch,lines,account,user,True),{})
            user._sections=None;batch.company_id+=1000
            with self.assertRaises(HTTPException):classify_existing(s,batch,lines,account,user,True)
            batch.company_id-=1000

    def test_changed_document_amount_across_batches_is_ambiguous(self):
        with unit(True) as s:
            old,old_lines,account,user,cat=self.prepared(s,status='committed')
            old_lines[0].direction='out';old_lines[0].amount-=1
            batch,lines,account,user,cat=self.prepared(s)
            entry=self.existing(s,lines[0],account,user,cat)
            s.flush()
            self.assertEqual(classify_existing(s,batch,lines,account,user,True)[lines[0].id]['status'],'review')
            self.assertEqual(process_existing(s,batch,lines,account,user,True),{})
            self.assertEqual(len(list(s.scalars(select(Ledger)))),1)

    def test_opening_mismatch_and_uncertain_row_cannot_link(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s)
            self.existing(s,lines[0],account,user,cat)
            self.assertEqual(classify_existing(s,batch,lines,account,user,False)[lines[0].id]['status'],'review')
            lines[0].uncertain=True
            self.assertEqual(classify_existing(s,batch,lines,account,user,True)[lines[0].id]['status'],'review')
            self.assertEqual(process_existing(s,batch,lines,account,user,True),{})


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ExistingOperationTests(name) for name in ExistingOperationTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
