"""A confirmed preview cannot silently authorize a changed bank decision."""
import unittest
from sqlalchemy import select
from tests.test_bank_statement_incoming import IncomingTests
from app.bank_statement_incoming import classify_incoming, process_incoming
from app.bank_statement_existing import classify_existing, process_existing
from app.bank_statement_unallocated_out import classify_unallocated_out, process_unallocated_out
from app.db import Category, Ledger, engine, unit


class ApprovedPreviewTests(IncomingTests):
    def test_new_income_candidate_not_in_preview_cannot_post(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s)
            line=lines[0]
            approved={line.id:{'status':'review'}}
            self.assertEqual(process_incoming(s,batch,lines,account,user,cat.id,approved=approved)['review'],1)
            self.assertIsNone(s.scalar(select(Ledger.id)))
            line.status='review';preview=classify_incoming(s,batch,lines,account,user,unallocated=True)
            self.assertEqual(preview[line.id]['category_source'],'unclassified')
            approved=preview
            self.assertEqual(process_incoming(s,batch,lines,account,user,cat.id,approved=approved)['review'],1)
            line.status='review'
            category=Category(company_id=self.company,name='Synthetic unknown income',type='income',
                              activity='unclassified',cost_group='other');s.add(category);s.flush()
            self.assertEqual(process_incoming(s,batch,lines,account,user,category.id,approved=approved)['auto_income_posted'],1)

    def test_changed_receipt_or_article_cannot_post(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s)
            preview=classify_incoming(s,batch,lines,account,user,cat.id)
            preview[lines[0].id]['receipt_id']=99999
            self.assertEqual(process_incoming(s,batch,lines,account,user,cat.id,approved=preview)['review'],1)
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_unapproved_outgoing_and_system_placeholder(self):
        with unit(True) as s:
            batch,lines,account,user,_=self.prepared(s)
            line=lines[0];line.direction='out';line.purpose='Synthetic supplier payment'
            cat=Category(company_id=self.company,name='Synthetic unknown expense',type='outcome',
                         activity='unclassified',cost_group='other');s.add(cat);s.flush()
            self.assertEqual(process_unallocated_out(s,batch,lines,account,user,cat.id,
                approved={line.id:{'status':'review'}})['review'],1)
            self.assertIsNone(s.scalar(select(Ledger.id)))
            line.status='review';preview=classify_unallocated_out(s,batch,lines,account,user,unallocated=True)
            self.assertEqual(process_unallocated_out(s,batch,lines,account,user,cat.id,approved=preview)['auto_outgoing_posted'],1)

    def test_existing_ledger_changed_since_preview_is_not_linked(self):
        with unit(True) as s:
            batch,lines,account,user,cat=self.prepared(s)
            line=lines[0];line.direction='out'
            entry=Ledger(account_id=account.id,category_id=cat.id,kind='out',amount=line.amount,date=line.date,
                counterparty=line.correspondent_name,reference=line.document_number,note='',creator_id=user.id)
            s.add(entry);s.flush()
            preview=classify_existing(s,batch,lines,account,user,True)
            self.assertEqual(preview[line.id]['status'],'other_matched_candidate')
            preview[line.id]['ledger_id']+=1
            self.assertEqual(process_existing(s,batch,lines,account,user,True,approved=preview)['review'],1)
            self.assertIsNone(line.ledger_id)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ApprovedPreviewTests(name) for name in ApprovedPreviewTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
