"""Synthetic read-only explanations of all debits; no production data."""
import json
import unittest
from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy import select

from tests.test_bank_statement_api import BankStatementTests
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.db import Company, Ledger, PaymentRequest, Setting, User, engine, unit


class ExpenseSummaryTests(BankStatementTests):
    def source_rows(self, rows):
        value=self.source(rows)
        debit=sum(Decimal(row['amount']) for row in rows if row['direction']=='out')
        credit=sum(Decimal(row['amount']) for row in rows if row['direction']=='in')
        value.update(opening_balance='1000000.00',closing_balance=str(Decimal('1000000.00')+credit-debit),
                     debit_total=str(debit),credit_total=str(credit))
        return value

    def expense_row(self, number='1', amount='600.00', purpose='Synthetic goods payment',
            inn='300000002', own_account='20200000000000000002', line=6):
        row=self.bank_row(number=number,amount=amount,inn=inn,line=line)
        row.update(purpose=purpose,correspondent_account=own_account)
        return row

    def details(self, bid, client=None, headers=None):
        cl,h=self.payer
        response=(client or cl).get(f'/api/bank-statements/{bid}',headers=headers or h)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_all_debits_explained_and_automatic_fee_article_requires_no_manual_selection(self):
        rid=self.approved()
        rows=[self.expense_row(),self.expense_row('FEE',amount='20.00',inn='300000001',
                    purpose='00011 Погаш. ком. SWIFT - synthetic',line=7),
              self.expense_row('NO-REQUEST',amount='75.00',inn='300000005',line=8)]
        p=self.preview(self.source_rows(rows));self.assertEqual(p.status_code,200,p.text)
        data=p.json();debits=data['rows']
        self.assertTrue(data['full_reconciliation'])
        self.assertEqual(debits[1]['category_source'], 'bank_fee_auto')
        self.assertEqual([r['expense_kind'] for r in debits],['request','bank_fee','no_request'])
        self.assertEqual(debits[0]['expense_request_id'],rid)
        self.assertEqual(debits[0]['expense_category_id'],self.cat)
        self.assertTrue(debits[0]['expense_category_name'])
        self.assertEqual((debits[2]['correspondent_name'],debits[2]['amount']),('Supplier','75.00'))
        with unit() as s:
            before=[(r.id,r.status,r.request_id,r.ledger_id) for r in s.scalars(select(BankStatementLine))]
            self.assertEqual(len(list(s.scalars(select(Ledger)))),0)
        self.assertEqual(self.details(data['id'])['rows'][0]['expense_kind'],'request')
        with unit() as s:
            after=[(r.id,r.status,r.request_id,r.ledger_id) for r in s.scalars(select(BankStatementLine))]
            self.assertEqual(before,after)
            self.assertEqual(s.get(PaymentRequest,rid).status,'approved')
            self.assertEqual(len(list(s.scalars(select(Ledger)))),0)

    def test_paid_request_matches_actual_payment_date_and_does_not_add_a_second_operation(self):
        rid=self.approved()
        paid=self.ledger('600','out',request_id=rid);self.assertEqual(paid.status_code,200,paid.text)
        with unit(True) as s:s.get(PaymentRequest,rid).due_date += timedelta(days=1)
        p=self.preview(self.source_rows([self.expense_row()]));self.assertEqual(p.status_code,200,p.text)
        row=p.json()['rows'][0]
        self.assertEqual((row['expense_kind'],row['expense_request_id'],row['expense_request_status']),('request',rid,'paid'))
        self.assertTrue(row['expense_existing_operation'])
        self.details(p.json()['id'])
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger).where(Ledger.request_id==rid)))),1)

    def test_unapproved_or_multiple_requests_are_not_reported_as_absent(self):
        rid=self.approved()
        with unit(True) as s:s.get(PaymentRequest,rid).status='draft'
        p=self.preview(self.source_rows([self.expense_row()]));self.assertEqual(p.status_code,200,p.text)
        self.assertEqual(p.json()['rows'][0]['expense_reason'],'request_not_approved')
        with unit(True) as s:
            original=s.get(PaymentRequest,rid);original.status='approved'
            s.add(PaymentRequest(creator_id=original.creator_id,category_id=original.category_id,
                account_id=original.account_id,counterparty=original.counterparty,counterparty_inn=original.counterparty_inn,
                amount=original.amount,due_date=original.due_date,status='draft',purpose='Synthetic conflicting draft'))
        p=self.preview(self.source_rows([self.expense_row()]));self.assertEqual(p.status_code,200,p.text)
        self.assertEqual(p.json()['rows'][0]['expense_kind'],'unresolved')
        self.assertEqual(p.json()['rows'][0]['expense_reason'],'request_not_unique')
        self.assertIsNone(p.json()['rows'][0]['expense_request_id'])

    def test_hidden_requests_do_not_disclose_id_or_article_but_still_block_false_no_request(self):
        rid=self.approved()
        p=self.preview(self.source_rows([self.expense_row()]));self.assertEqual(p.status_code,200,p.text)
        with unit(True) as s:
            user=s.scalar(select(User).where(User.username=='statement_payer'))
            s.merge(Setting(key=f'sections:{self.company}:{user.id}',value=json.dumps({'version':1,'sections':{'requests':'hidden'}})))
        row=self.details(p.json()['id'])['rows'][0]
        self.assertEqual((row['expense_kind'],row['expense_reason']),('unresolved','request_not_available'))
        for key in ['expense_request_id','expense_category_id','expense_category_name','request_id']:
            self.assertIsNone(row[key],key)
        check=self.preview(self.source_rows([self.expense_row('HIDDEN-AGAIN')]))
        self.assertEqual(check.status_code,200,check.text)
        for key in ['expense_request_id','expense_category_id','expense_category_name','request_id']:
            self.assertIsNone(check.json()['rows'][0][key],key)
        self.assertIsNotNone(rid)

    def test_changed_or_reversed_linked_payments_and_fees_do_not_look_reconciled(self):
        rid=self.approved()
        paid=self.ledger('600','out',request_id=rid);self.assertEqual(paid.status_code,200,paid.text)
        p=self.preview(self.source_rows([self.expense_row()]));self.assertEqual(p.status_code,200,p.text)
        with unit(True) as s:
            entry=s.scalar(select(Ledger).where(Ledger.request_id==rid));entry.amount+=1
        self.assertEqual(self.details(p.json()['id'])['rows'][0]['expense_reason'],'linked_operation_changed')
        with unit(True) as s:
            entry=s.scalar(select(Ledger).where(Ledger.request_id==rid));entry.amount-=1
            s.add(Ledger(account_id=entry.account_id,category_id=entry.category_id,kind='in',amount=entry.amount,
                date=entry.date,counterparty=entry.counterparty,reference='SYNTH-EXPENSE-REV',
                note='Synthetic reversal',reversal_of=entry.id,creator_id=entry.creator_id))
        self.assertEqual(self.details(p.json()['id'])['rows'][0]['expense_reason'],'linked_operation_changed')
        fee=self.preview(self.source_rows([self.expense_row('FEE-CHANGED','20.00',
            '00011 Погаш. ком. SWIFT - synthetic','300000001')]))
        self.assertEqual(fee.status_code,200,fee.text)
        with unit(True) as s:
            user=s.scalar(select(User).where(User.username=='admin'))
            entry=Ledger(account_id=self.acc,category_id=self.cat,kind='out',amount=2100,
                date=date.fromisoformat(self.date),counterparty='Synthetic bank',reference='SYNTH-CHANGED-FEE',
                note='Synthetic changed fee',creator_id=user.id)
            s.add(entry);s.flush()
            line=s.get(BankStatementLine,fee.json()['rows'][0]['id']);line.ledger_id=entry.id
        row=self.details(fee.json()['id'])['rows'][0]
        self.assertEqual((row['expense_kind'],row['expense_reason']),('unresolved','linked_operation_changed'))

    def test_bank_interest_and_exact_iabs_fees_are_separate_from_transfers(self):
        day=date.fromisoformat(self.date).strftime('%d.%m.%Y')
        rows=[self.expense_row('SWIFT','20.00','00011 Погаш. ком. SWIFT - synthetic','300000001',line=6),
              self.expense_row('INTEREST','30.00','01014кредит фоизи учун толов','300000001','16309000000000000001',line=7),
              self.expense_row('IABS','2.00',f'00111 BC {day} погашение Исх.плат.документ -2.00 от суммы 500.00 от {day}',
                       '300000001','16401000000000000001',line=8),
              self.expense_row('OWN','40.00','00634Пополнение корпоративной карты','300000001',line=9)]
        p=self.preview(self.source_rows(rows));self.assertEqual(p.status_code,200,p.text)
        self.assertEqual([r['expense_kind'] for r in p.json()['rows']],['bank_fee','bank_interest','bank_fee','unresolved'])
        self.assertEqual(p.json()['rows'][2]['expense_evidence'],'explicit_iabs_fee')
        self.assertEqual(p.json()['rows'][3]['expense_reason'],'own_inn_does_not_prove_transfer')
        self.assertEqual(p.json()['rows'][2]['status'],'fee_candidate')

    def test_percent_goods_and_utility_abbreviation_are_not_bank_fees(self):
        rows=[self.expense_row('GOODS','5.00','Оплата 100% за товар'),
              self.expense_row('UTIL','6.00','00111 Ком. услуги за период',line=7)]
        p=self.preview(self.source_rows(rows));self.assertEqual(p.status_code,200,p.text)
        self.assertEqual([r['expense_kind'] for r in p.json()['rows']],['no_request','no_request'])
        self.assertEqual([r['status'] for r in p.json()['rows']],['outgoing_candidate','outgoing_candidate'])

    def test_duplicate_or_overlapping_source_rows_remain_visible_but_not_false_unique_matches(self):
        self.approved()
        rows=[self.expense_row(),self.expense_row(line=7)]
        p=self.preview(self.source_rows(rows));self.assertEqual(p.status_code,200,p.text)
        self.assertTrue(all(row['expense_reason']=='duplicate_or_overlap' for row in p.json()['rows']))
        single=self.preview(self.source_rows([self.expense_row('different')]));self.assertEqual(single.status_code,200,single.text)
        bid=single.json()['id']
        with unit(True) as s:s.get(BankStatementBatch,bid).status='committed'
        overlap=self.preview(self.source_rows([self.expense_row('different')]));self.assertEqual(overlap.status_code,200,overlap.text)
        self.assertEqual(overlap.json()['rows'][0]['expense_reason'],'duplicate_or_overlap')

    def test_summary_details_enforce_company_and_existing_role_boundaries(self):
        p=self.preview(self.source_rows([self.expense_row()]));self.assertEqual(p.status_code,200,p.text)
        bid=p.json()['id']
        with unit() as s:foreign=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
        other=self.client.get(f'/api/bank-statements/{bid}',headers={**self.h,'X-Company-ID':str(foreign)})
        self.assertEqual(other.status_code,404,other.text)
        reader,rh=self.make_user('material_accountant','synthetic_expense_reader')
        try:self.assertEqual(reader.get(f'/api/bank-statements/{bid}',headers=rh).status_code,403)
        finally:reader.close()


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(ExpenseSummaryTests(name) for name in ExpenseSummaryTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
