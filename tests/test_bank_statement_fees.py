"""Synthetic commission tests against the normal journal service."""
import unittest
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.bank_statement_fees import classify_fee, post_fee, prepare_fee_context
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.clock import today
from app.db import Account, Base, Budget, Category, Company, Ledger, PaymentRequest, Role, User
from app.services import account_balance, effective_cashflows


class FeeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.s = Session(self.engine, expire_on_commit=False)
        s = self.s
        s.add_all([Role(name='finance'), Role(name='accountant')]); s.flush()
        self.company = Company(code='TEST', name='Synthetic Company')
        self.other = Company(code='OTHER', name='Other Synthetic Company')
        self.user = User(username='finance', name='Synthetic', password_hash='test', role='finance')
        s.add_all([self.company, self.other, self.user]); s.flush()
        self.account = Account(company_id=self.company.id, name='Synthetic bank', kind='bank', currency='UZS',
                               opening=100000, opening_date=today(), created_by=self.user.id)
        self.cat = Category(company_id=self.company.id, name='Bank fees', type='outcome')
        self.income = Category(company_id=self.company.id, name='Receipts', type='income')
        self.foreign = Category(company_id=self.other.id, name='Other fees', type='outcome')
        s.add_all([self.account, self.cat, self.income, self.foreign]); s.flush()
        self.batch = BankStatementBatch(company_id=self.company.id, account_id=self.account.id, created_by=self.user.id,
            filename='synthetic.xls', sha256='1'*64, account_number='20000000000000000001',
            owner_name='Synthetic Company', owner_inn='300000001', currency='UZS', period_from=today(),
            period_to=today(), opening=100000, closing=99000, debit_total=1000, credit_total=0,
            balance_matches=True, declared_totals_match=True, source_ready=True, status='preview')
        s.add(self.batch); s.flush()
        self.line = BankStatementLine(company_id=self.company.id, batch_id=self.batch.id, account_id=self.account.id,
            line=6, date=today(), direction='out', amount=1000, document_number='SYN-42',
            correspondent_mfo='00014', correspondent_account='20000000000000000002',
            correspondent_name='Synthetic bank fee', correspondent_inn='300000001',
            purpose='00011 Погаш. ком. SWIFT - synthetic', fingerprint='a'*64, occurrence=1,
            status='review', reason='', include_in_daily=True, uncertain=False)
        s.add(self.line); s.flush()
        s.info['company_id'] = self.company.id

    def tearDown(self):
        self.s.close(); self.engine.dispose()

    def classify(self, **overrides):
        values = dict(s=self.s, batch=self.batch, line=self.line, account=self.account,
                      category_id=self.cat.id, user=self.user, opening_ok=True)
        values.update(overrides)
        return classify_fee(**values)

    def test_only_specific_swift_commission_is_recognized(self):
        self.assertEqual(self.classify()['status'], 'fee_candidate')
        for description in ('00011 Погашение Исп.плат.документ - %%',
                            '00011 Пополнение корпоративной карты',
                            '00011 Оплата услуг контрагента',
                            '00011 SWIFT обработка'):
            self.line.purpose = description
            self.assertIsNone(self.classify())

    def test_existing_confirmed_commission_can_be_reconsidered(self):
        self.batch.status = 'committed'
        self.line.status = 'reviewed'; self.line.classification = 'bank_fee'
        self.s.flush()
        self.assertEqual(self.classify()['status'], 'fee_candidate')
        self.line.classification = 'internal_transfer'; self.s.flush()
        self.assertIsNone(self.classify())

    def test_accountant_classified_fee_is_reprocessed_without_swift_or_owner_inn(self):
        self.batch.status = 'committed'
        self.line.status = 'reviewed'; self.line.classification = 'bank_fee'
        self.line.reviewed_by = self.user.id; self.line.purpose = 'BC synthetic bank fee'
        self.line.correspondent_inn = '300000099'; self.s.flush()
        self.assertEqual(self.classify()['status'], 'fee_candidate')
        entry = post_fee(self.s, self.batch, self.line, self.account, self.cat.id, self.user, True)
        self.s.flush(); self.assertEqual(entry.category_id, self.cat.id)
        self.assertEqual(account_balance(self.s,self.account),99000)

    def test_manual_fee_without_proof_or_rights_is_not_posted(self):
        self.batch.status = 'committed'; self.line.status = 'reviewed'; self.line.classification = 'bank_fee'
        self.line.reviewed_by = None; self.line.purpose = 'BC synthetic bank fee'
        self.line.correspondent_inn = ''; self.s.flush()
        self.assertIsNone(self.classify())
        self.line.reviewed_by = self.user.id; self.s.flush()
        self.assertEqual(self.classify(opening_ok=False)['status'], 'review')
        self.user._permissions = frozenset({'ledger','pay_cash'})
        self.assertEqual(self.classify()['status'], 'review')
        del self.user._permissions
        req = PaymentRequest(creator_id=self.user.id, category_id=self.cat.id, account_id=self.account.id,
            counterparty='Synthetic', counterparty_inn='300000123', purpose='Synthetic request', amount=self.line.amount,
            due_date=today(), status='draft')
        self.s.add(req); self.s.flush()
        self.assertEqual(self.classify()['status'], 'review')
        self.assertEqual(len(list(self.s.scalars(select(Ledger)))),0)

    def test_actual_post_changes_balance_and_cashflow_once(self):
        entry = post_fee(self.s, self.batch, self.line, self.account, self.cat.id, self.user, True)
        self.s.flush()
        self.assertEqual(entry.kind, 'out')
        self.assertEqual(entry.category_id, self.cat.id)
        self.assertEqual(account_balance(self.s, self.account), 99000)
        self.assertEqual([x.id for x in self.s.scalars(effective_cashflows(self.s))], [entry.id])
        # An already written entry cannot cause a second expense, even if the
        # client retries before the bank row is linked.
        self.assertEqual(self.classify()['status'], 'review')
        with self.assertRaises(HTTPException):
            post_fee(self.s, self.batch, self.line, self.account, self.cat.id, self.user, True)
        self.assertEqual(len(list(self.s.scalars(select(Ledger)))), 1)

    def test_bank_payer_cannot_post_fee_without_confirmed_decision(self):
        self.user._permissions = frozenset({'ledger','pay_bank'})
        with self.assertRaises(HTTPException) as result:
            post_fee(self.s, self.batch, self.line, self.account, self.cat.id, self.user, True)
        self.assertEqual(result.exception.status_code,403)
        self.assertIsNone(self.s.scalar(select(Ledger.id)))

    def test_scope_type_owner_balance_and_permissions_are_mandatory(self):
        # A missing selection now plans a dedicated automatic article without
        # creating it during preview; explicitly invalid selections still fail.
        planned = self.classify(category_id=None)
        self.assertEqual((planned['status'], planned['category_id'], planned['category_source']),
                         ('fee_candidate', None, 'bank_fee_auto'))
        for kwargs in ({'category_id':self.income.id},
                       {'category_id':self.foreign.id}, {'opening_ok':False}):
            self.assertEqual(self.classify(**kwargs)['status'], 'review')
        self.line.correspondent_inn = '300000099'
        self.assertEqual(self.classify()['status'], 'review')
        self.line.correspondent_inn = self.batch.owner_inn
        self.user._permissions = frozenset({'ledger','pay_cash'})
        self.assertEqual(self.classify()['status'], 'review')
        del self.user._permissions
        self.user._sections = {'ledger': 'view'}
        self.assertEqual(self.classify()['status'], 'review')

    def test_duplicate_bank_lines_and_requests_block_candidate(self):
        duplicate = BankStatementLine(company_id=self.company.id, batch_id=self.batch.id, account_id=self.account.id,
            line=7, date=today(), direction='out', amount=self.line.amount, document_number=self.line.document_number,
            correspondent_mfo='00014', correspondent_account='20000000000000000002',
            correspondent_name='Other synthetic', correspondent_inn='300000001', purpose='Other payment',
            fingerprint='b'*64, occurrence=1, status='review', reason='', include_in_daily=True, uncertain=False)
        self.s.add(duplicate); self.s.flush()
        self.assertEqual(self.classify()['status'], 'review')
        self.s.delete(duplicate); self.s.flush()
        req = PaymentRequest(creator_id=self.user.id, category_id=self.cat.id, account_id=self.account.id,
            counterparty='Synthetic', counterparty_inn='', purpose='Synthetic request', amount=self.line.amount,
            due_date=today(), status='draft')
        self.s.add(req); self.s.flush()
        self.assertEqual(self.classify()['status'], 'review')

    def test_distinct_documents_of_equal_amount_post_once_each(self):
        second = BankStatementLine(company_id=self.company.id, batch_id=self.batch.id, account_id=self.account.id,
            line=7, date=today(), direction='out', amount=self.line.amount, document_number='SYN-43',
            correspondent_mfo='00014', correspondent_account='20000000000000000002',
            correspondent_name=self.line.correspondent_name, correspondent_inn=self.line.correspondent_inn,
            purpose=self.line.purpose, fingerprint='b'*64, occurrence=1, status='review', reason='',
            include_in_daily=True, uncertain=False)
        self.s.add(second); self.s.flush()
        first_entry = post_fee(self.s, self.batch, self.line, self.account, self.cat.id, self.user, True)
        # The caller links the bank row and journal entry in the same transaction.
        self.line.ledger_id = first_entry.id; self.line.status = 'fee_posted'; self.s.flush()
        self.assertEqual(self.classify(line=second)['status'], 'fee_candidate')
        next_entry = post_fee(self.s, self.batch, second, self.account, self.cat.id, self.user, True)
        second.ledger_id = next_entry.id; second.status = 'fee_posted'; self.s.flush()
        self.assertEqual(account_balance(self.s, self.account), 98000)
        self.assertEqual(len(list(self.s.scalars(select(Ledger)))), 2)

    def test_previous_statement_or_changed_manual_amount_requires_review(self):
        previous = BankStatementBatch(company_id=self.company.id, account_id=self.account.id, created_by=self.user.id,
            filename='previous.xls', sha256='2'*64, account_number=self.batch.account_number,
            owner_name=self.batch.owner_name, owner_inn=self.batch.owner_inn, currency='UZS', period_from=today(),
            period_to=today(), opening=100000, closing=99000, debit_total=1000, credit_total=0,
            balance_matches=True, declared_totals_match=True, source_ready=True, status='committed')
        self.s.add(previous); self.s.flush()
        prior = BankStatementLine(company_id=self.company.id, batch_id=previous.id, account_id=self.account.id,
            line=7, date=today(), direction='out', amount=self.line.amount, document_number='PREVIOUS',
            correspondent_mfo='00014', correspondent_account='20000000000000000002',
            correspondent_name='Other synthetic', correspondent_inn='300000001', purpose='Other payment',
            fingerprint='b'*64, occurrence=1, status='review', reason='', include_in_daily=True, uncertain=False)
        self.s.add(prior); self.s.flush()
        self.assertEqual(self.classify()['status'], 'review')
        prior.amount += 1; prior.document_number = self.line.document_number; self.s.flush()
        self.assertEqual(self.classify()['status'], 'review')
        self.s.delete(prior); self.s.flush()
        manual = Ledger(account_id=self.account.id, category_id=self.cat.id, kind='out', amount=self.line.amount + 1,
            date=today(), counterparty=self.line.correspondent_name, reference=self.line.document_number,
            creator_id=self.user.id, note='Synthetic previous fee')
        self.s.add(manual); self.s.flush()
        self.assertEqual(self.classify()['status'], 'review')

    def test_existing_ledger_is_linked_not_posted_and_reversal_blocks(self):
        existing = Ledger(account_id=self.account.id, category_id=self.cat.id, kind='out', amount=self.line.amount,
            date=today(), counterparty=self.line.correspondent_name, reference=self.line.document_number,
            creator_id=self.user.id, note='Synthetic previous fee')
        self.s.add(existing); self.s.flush()
        result = self.classify()
        self.assertEqual((result['status'], result['ledger_id']), ('fee_existing', existing.id))
        with self.assertRaises(HTTPException):
            post_fee(self.s, self.batch, self.line, self.account, self.cat.id, self.user, True)
        existing.counterparty = 'A different party'; self.s.flush()
        self.assertEqual(self.classify()['status'], 'review')
        existing.counterparty = self.line.correspondent_name
        reversal = Ledger(account_id=self.account.id, category_id=self.cat.id, kind='in', amount=self.line.amount,
            date=today(), counterparty=existing.counterparty, reference='SYN-REV', reversal_of=existing.id,
            creator_id=self.user.id, note='Synthetic reversal')
        self.s.add(reversal); self.s.flush()
        self.assertEqual(self.classify()['status'], 'review')

    def test_budget_and_actual_balance_remain_enforced(self):
        self.s.add(Budget(category_id=self.cat.id, month=today().strftime('%Y-%m'), currency='UZS', amount=1, mode='hard'))
        self.s.flush()
        with self.assertRaises(HTTPException):
            post_fee(self.s, self.batch, self.line, self.account, self.cat.id, self.user, True)
        self.s.query(Budget).delete(); self.account.opening = 0; self.s.flush()
        with self.assertRaises(HTTPException):
            post_fee(self.s, self.batch, self.line, self.account, self.cat.id, self.user, True)
        self.assertEqual(len(list(self.s.scalars(select(Ledger)))), 0)

    def test_preview_context_agrees_on_sixteen_rows_and_reduces_queries(self):
        lines=[self.line]
        for n in range(1,16):
            row=BankStatementLine(company_id=self.company.id,batch_id=self.batch.id,account_id=self.account.id,
                line=6+n,date=today(),direction='out',amount=1000+n,document_number=f'SYN-{n}',
                correspondent_mfo='00014',correspondent_account='20000000000000000002',
                correspondent_name=self.line.correspondent_name,correspondent_inn=self.line.correspondent_inn,
                purpose=self.line.purpose,fingerprint=f'{n:064x}',occurrence=1,status='review',reason='',
                include_in_daily=True,uncertain=False)
            self.s.add(row);lines.append(row)
        self.s.flush()
        req=PaymentRequest(creator_id=self.user.id,category_id=self.cat.id,account_id=self.account.id,
            counterparty='Synthetic',counterparty_inn='',purpose='Synthetic',amount=lines[1].amount,
            due_date=today(),status='draft')
        existing=Ledger(account_id=self.account.id,category_id=self.cat.id,kind='out',amount=lines[2].amount,
            date=today(),counterparty=lines[2].correspondent_name,reference=lines[2].document_number,
            creator_id=self.user.id,note='Synthetic')
        reversal_target=Ledger(account_id=self.account.id,category_id=self.cat.id,kind='out',amount=lines[3].amount,
            date=today(),counterparty=lines[3].correspondent_name,reference=lines[3].document_number,
            creator_id=self.user.id,note='Synthetic')
        self.s.add_all([req,existing,reversal_target]);self.s.flush()
        self.s.add(Ledger(account_id=self.account.id,category_id=self.cat.id,kind='in',amount=lines[3].amount,
            date=today(),counterparty='Synthetic',reference='SYN-REVERSE',reversal_of=reversal_target.id,
            creator_id=self.user.id,note='Synthetic'))
        lines[4].document_number=lines[5].document_number
        lines[4].amount=lines[5].amount
        self.s.flush()
        calls=[]
        def count(conn,cursor,statement,parameters,context,executemany):
            if statement.lstrip().upper().startswith('SELECT'):calls.append(statement)
        event.listen(self.engine,'before_cursor_execute',count)
        try:
            direct=[self.classify(line=line) for line in lines];uncached=len(calls)
            calls.clear()
            snapshot=prepare_fee_context(self.s,self.batch,lines,self.account,self.cat.id)
            cached=[self.classify(line=line,context=snapshot) for line in lines];batched=len(calls)
        finally:
            event.remove(self.engine,'before_cursor_execute',count)
        self.assertEqual(direct,cached)
        self.assertEqual(direct[1]['status'],'review')
        self.assertEqual(direct[2]['status'],'fee_existing')
        self.assertEqual(direct[3]['status'],'review')
        self.assertEqual(direct[4]['status'],'review')
        self.assertLessEqual(batched,8)
        self.assertGreater(uncached,batched*3)

    def test_preview_context_respects_ledger_linked_to_a_different_day(self):
        entry=Ledger(account_id=self.account.id,category_id=self.cat.id,kind='out',amount=self.line.amount,
            date=today(),counterparty=self.line.correspondent_name,reference=self.line.document_number,
            creator_id=self.user.id,note='Synthetic existing')
        self.s.add(entry);self.s.flush()
        prior=BankStatementBatch(company_id=self.company.id,account_id=self.account.id,created_by=self.user.id,
            filename='previous.xls',sha256='f'*64,account_number=self.batch.account_number,
            owner_name=self.batch.owner_name,owner_inn=self.batch.owner_inn,currency='UZS',
            period_from=today()-timedelta(days=1),period_to=today()-timedelta(days=1),opening=0,closing=0,
            debit_total=0,credit_total=0,balance_matches=True,source_ready=True,status='committed')
        self.s.add(prior);self.s.flush()
        row=BankStatementLine(company_id=self.company.id,batch_id=prior.id,account_id=self.account.id,
            line=7,date=today()-timedelta(days=1),direction='out',amount=self.line.amount,document_number='OLD',
            correspondent_mfo='',correspondent_account='',correspondent_name='Synthetic',
            correspondent_inn='',purpose='',fingerprint='f'*64,occurrence=1,status='fee_matched',
            reason='',include_in_daily=True,uncertain=False,ledger_id=entry.id)
        self.s.add(row);self.s.flush()
        snapshot=prepare_fee_context(self.s,self.batch,[self.line],self.account,self.cat.id)
        self.assertEqual(self.classify()['status'],'review')
        self.assertEqual(self.classify(context=snapshot),self.classify())
        with self.assertRaises(ValueError):
            self.classify(context=snapshot,category_id=self.income.id)


if __name__ == '__main__':
    unittest.main()
