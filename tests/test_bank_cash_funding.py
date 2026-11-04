"""Synthetic bank evidence for cash funding: no production money or files."""
import json
import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

from tests.test_site import SiteTests
from app.bank_statement_api import _evaluated, _apply_reconciled, _payload
from app.bank_statement_cash_funding import (CANDIDATE, MATCHED, accounted_funding_line_ids,
    classify_cash_funding, process_cash_funding)
from app.bank_statement_guard import (accounted_line_ids, pending_bank_debit_hold,
    set_statement_reconciliation)
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.cash_report import report
from app.db import Account, CashFunding, Category, Company, Ledger, PaymentRequest, TelegramGroup, User, engine, now, unit
from app.services import account_balance, effective_cashflows


class BankCashFundingTests(SiteTests):
    def test_group_statement_separates_verified_cash_withdrawal_and_fee(self):
        self.date = str(date.fromisoformat(self.date) - timedelta(days=1))
        chat = -1008888888888
        with unit(True) as s:
            batch, lines, bank, cash, transit, user, fee_cat, req, funding = self.prepared(s)
            checked = _evaluated(s, batch, lines, bank, user, fee_cat.id)
            self.assertTrue(_apply_reconciled(s, batch, lines, bank, user, fee_cat.id, checked, True))
            s.add(TelegramGroup(chat_id=chat, title='Test group', companies='UZGERMED', added_by=user.id))
        with patch.dict(os.environ, {'BOT_CLIENT_ID':'test-client', 'BOT_CLIENT_SECRET':'x'*40}):
            response = self.client.get('/api/bot/v1/group-statement-reconciliation',
                params={'chat_id':chat, 'report_date':self.date}, auth=('test-client','x'*40))
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['statement_state'], 'confirmed')
        by_kind = {row['kind']: row['amount'] for row in data['expenses'] if row['countable']}
        self.assertEqual(by_kind.get('internal_transfer'), '990000.00')
        self.assertEqual(by_kind.get('bank_fee'), '10000.00')
        self.assertNotIn('no_request', by_kind)

    def test_group_statement_keeps_split_cash_withdrawal_and_fee_distinct(self):
        self.date = str(date.fromisoformat(self.date) - timedelta(days=1))
        chat = -1008888888888
        with unit(True) as s:
            batch, lines, bank, cash, transit, user, fee_cat, req, funding = self.prepared(s, 'split')
            checked = _evaluated(s, batch, lines, bank, user, fee_cat.id)
            self.assertTrue(_apply_reconciled(s, batch, lines, bank, user, fee_cat.id, checked, True))
            s.add(TelegramGroup(chat_id=chat, title='Test group', companies='UZGERMED', added_by=user.id))
        with patch.dict(os.environ, {'BOT_CLIENT_ID':'test-client', 'BOT_CLIENT_SECRET':'x'*40}):
            response = self.client.get('/api/bot/v1/group-statement-reconciliation',
                params={'chat_id':chat, 'report_date':self.date}, auth=('test-client','x'*40))
        self.assertEqual(response.status_code, 200, response.text)
        by_kind = {row['kind']: row['amount'] for row in response.json()['expenses'] if row['countable']}
        self.assertEqual(by_kind.get('internal_transfer'), '990000.00')
        self.assertEqual(by_kind.get('bank_fee'), '10000.00')
        self.assertNotIn('no_request', by_kind)

    def test_group_statement_does_not_assert_stale_cash_funding(self):
        self.date = str(date.fromisoformat(self.date) - timedelta(days=1))
        chat = -1008888888888
        with unit(True) as s:
            batch, lines, bank, cash, transit, user, fee_cat, req, funding = self.prepared(s)
            checked = _evaluated(s, batch, lines, bank, user, fee_cat.id)
            self.assertTrue(_apply_reconciled(s, batch, lines, bank, user, fee_cat.id, checked, True))
            s.get(Ledger, funding.transfer_ledger_id).amount += 1
            s.add(TelegramGroup(chat_id=chat, title='Test group', companies='UZGERMED', added_by=user.id))
        with patch.dict(os.environ, {'BOT_CLIENT_ID':'test-client', 'BOT_CLIENT_SECRET':'x'*40}):
            response = self.client.get('/api/bot/v1/group-statement-reconciliation',
                params={'chat_id':chat, 'report_date':self.date}, auth=('test-client','x'*40))
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()['expenses'][0]
        self.assertEqual(row['kind'], 'unresolved')
        self.assertFalse(row['countable'])

    def prepared(self, s, layout='gross'):
        s.info['company_id'] = self.company
        bank = s.get(Account, self.acc)
        user = s.scalar(select(User).where(User.username == 'admin').execution_options(company_unscoped=True))
        day = date.fromisoformat(self.date)
        cash = Account(company_id=self.company, name='Synthetic cash', kind='cash', currency='UZS',
                       opening=0, opening_date=bank.opening_date, created_by=user.id)
        transit = Account(company_id=self.company, name='Synthetic transit', kind='transit', currency='UZS',
                          opening=0, opening_date=bank.opening_date, created_by=user.id)
        fee_cat = Category(company_id=self.company, name='Synthetic withdrawal fee', type='outcome', activity='operating')
        s.add_all([cash, transit, fee_cat]); s.flush()
        req = PaymentRequest(creator_id=user.id, last_editor_id=user.id, category_id=self.cat,
            account_id=cash.id, counterparty='Synthetic payee', amount=99000000, due_date=day,
            purpose='Synthetic cash request', status='approved', priority='urgent')
        s.add(req); s.flush()
        funding = CashFunding(company_id=self.company, request_id=req.id, bank_account_id=bank.id,
            cash_account_id=cash.id, transit_account_id=transit.id, gross_amount=100000000,
            fee_percent='1', fee_amount=1000000, net_amount=99000000, fee_category_id=fee_cat.id,
            state='in_transit', withdraw_date=day, withdraw_reference='WITHDRAW-001', created_by=user.id)
        s.add(funding); s.flush()
        transfer = Ledger(account_id=bank.id, to_account_id=transit.id, kind='transfer', amount=funding.net_amount,
            date=day, reference=f'CF-CASH-{funding.id}-WITHDRAW', creator_id=user.id)
        fee = Ledger(account_id=bank.id, category_id=fee_cat.id, kind='out', amount=funding.fee_amount,
            date=day, reference=f'CF-CASH-{funding.id}-FEE', creator_id=user.id)
        s.add_all([transfer, fee]); s.flush()
        funding.transfer_ledger_id = transfer.id; funding.fee_ledger_id = fee.id
        batch = BankStatementBatch(company_id=self.company, account_id=bank.id, created_by=user.id,
            filename='synthetic-withdrawal.xls', sha256='c'*64, account_number='20200000000000000001',
            owner_name='Synthetic company', owner_inn='300000001', currency='UZS', period_from=day,
            period_to=day, opening=bank.opening, closing=bank.opening-funding.gross_amount,
            debit_total=funding.gross_amount, credit_total=0, balance_matches=True,
            declared_totals_match=True, source_ready=True, status='committed', committed_at=now()-timedelta(hours=1))
        s.add(batch); s.flush()
        specs = [(funding.gross_amount, 'WITHDRAW-001', 'Получение наличных с комиссией')]
        if layout == 'split':
            specs = [(funding.net_amount, 'WITHDRAW-001', 'Получение наличных'),
                     (funding.fee_amount, 'FEE-001', 'Комиссия за снятие по WITHDRAW-001')]
        lines = []
        for index, (value, document, purpose) in enumerate(specs):
            line = BankStatementLine(company_id=self.company, batch_id=batch.id, account_id=bank.id,
                line=index+6, date=day, direction='out', amount=value, document_number=document,
                correspondent_mfo='00014', correspondent_account='20200000000000000002',
                correspondent_name='Synthetic bank', correspondent_inn='300000001', purpose=purpose,
                fingerprint=f'{index+1:064x}', occurrence=1, status='review', include_in_daily=True)
            s.add(line); lines.append(line)
        s.flush()
        return batch, lines, bank, cash, transit, user, fee_cat, req, funding

    def test_gross_line_links_transfer_and_fee_without_new_ledger(self):
        with unit(True) as s:
            batch, lines, bank, cash, transit, user, fee_cat, req, funding = self.prepared(s)
            before = len(list(s.scalars(select(Ledger))))
            checked = _evaluated(s, batch, lines, bank, user, fee_cat.id)
            self.assertEqual(checked['rows'][0]['status'], CANDIDATE)
            self.assertEqual(checked['rows'][0]['cash_funding']['ledger_ids'],
                             [funding.transfer_ledger_id, funding.fee_ledger_id])
            self.assertEqual(checked['posting_summary']['linked']['debit_total'], '1000000.00')
            self.assertTrue(checked['posting_summary']['partial_allowed'])
            self.assertTrue(_apply_reconciled(s, batch, lines, bank, user, fee_cat.id, checked, True))
            self.assertEqual(lines[0].status, MATCHED)
            self.assertEqual(accounted_line_ids(s, lines, bank), {lines[0].id})
            self.assertEqual(len(list(s.scalars(select(Ledger)))), before)
            self.assertEqual((account_balance(s, bank), account_balance(s, cash), account_balance(s, transit)),
                             (0, 0, 99000000))
            self.assertEqual(req.status, 'approved')
            second = _evaluated(s, batch, lines, bank, user, fee_cat.id, reprocess=True)
            self.assertEqual(second['posting_summary']['pending']['count'], 0)
            self.assertTrue(_apply_reconciled(s, batch, lines, bank, user, fee_cat.id, second, False))
            self.assertEqual(len(list(s.scalars(select(Ledger)))), before)
            result = _payload(s, batch, user)['rows'][0]
            self.assertEqual(result['expense_kind'], 'internal_transfer')
            self.assertFalse(result['review_note'].startswith('{'))

    def test_split_net_and_fee_link_each_existing_leg_once(self):
        with unit(True) as s:
            batch, lines, bank, cash, transit, user, fee_cat, req, funding = self.prepared(s, 'split')
            checked = _evaluated(s, batch, lines, bank, user, fee_cat.id)
            self.assertEqual([row['status'] for row in checked['rows']], [CANDIDATE, CANDIDATE])
            self.assertEqual([row['cash_funding']['component'] for row in checked['rows']], ['net', 'fee'])
            self.assertTrue(_apply_reconciled(s, batch, lines, bank, user, fee_cat.id, checked, True))
            self.assertEqual(accounted_funding_line_ids(s, lines, bank), {line.id for line in lines})
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 2)
            set_statement_reconciliation(s, batch, False, user);s.flush()
            self.assertEqual(pending_bank_debit_hold(s, bank), 0)
            rows = _payload(s, batch, user)['rows']
            self.assertEqual([row['expense_kind'] for row in rows], ['internal_transfer', 'bank_fee'])

    def test_zero_fee_links_only_net_transfer_and_adds_no_external_expense(self):
        with unit(True) as s:
            batch, lines, bank, cash, transit, user, fee_cat, req, funding = self.prepared(s)
            old_fee=s.get(Ledger,funding.fee_ledger_id)
            funding.fee_ledger_id=None;funding.fee_amount=0;funding.fee_percent='0.00'
            funding.net_amount=funding.gross_amount;req.amount=funding.net_amount
            s.get(Ledger,funding.transfer_ledger_id).amount=funding.net_amount
            s.delete(old_fee);s.flush()
            checked=_evaluated(s,batch,lines,bank,user,fee_cat.id)
            self.assertEqual(checked['rows'][0]['cash_funding']['ledger_ids'],[funding.transfer_ledger_id])
            self.assertTrue(_apply_reconciled(s,batch,lines,bank,user,fee_cat.id,checked,True))
            self.assertEqual(accounted_line_ids(s,lines,bank),{lines[0].id})
            self.assertEqual(report(s,2026,'UZS',self.company,as_of=date.fromisoformat(self.date))['totals'][8],'0.00')
            self.assertEqual(len(list(s.scalars(select(Ledger)))),1)

    def test_partial_and_unreferenced_fee_stay_on_review_not_posted_twice(self):
        with unit(True) as s:
            batch, lines, bank, cash, transit, user, fee_cat, req, funding = self.prepared(s, 'split')
            lines[1].purpose = 'Комиссия за получение наличных без номера документа'
            checked = _evaluated(s, batch, lines, bank, user, fee_cat.id)
            self.assertEqual([row['status'] for row in checked['rows']], ['review', 'review'])
            self.assertTrue(all(row['ledger_id'] is None for row in checked['rows']))
            self.assertFalse(_apply_reconciled(s, batch, lines, bank, user, fee_cat.id, checked, True))
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 2)

    def test_competing_rows_and_groups_do_not_claim_existing_funding(self):
        with unit(True) as s:
            batch, lines, bank, cash, transit, user, fee_cat, req, funding = self.prepared(s)
            clone = BankStatementLine(**{column.name: getattr(lines[0], column.name)
                for column in BankStatementLine.__table__.columns if column.name != 'id'})
            clone.fingerprint = 'f'*64; clone.line += 1
            s.add(clone);s.flush()
            checked = classify_cash_funding(s, batch, [*lines, clone], bank, user, True)
            self.assertTrue(all(row['status'] == 'review' for row in checked.values()))
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 2)

    def test_stale_signed_group_and_view_only_cannot_link(self):
        with unit(True) as s:
            batch, lines, bank, cash, transit, user, fee_cat, req, funding = self.prepared(s)
            approved = classify_cash_funding(s, batch, lines, bank, user, True)
            funding.version += 1
            with self.assertRaisesRegex(Exception, '409'):
                process_cash_funding(s, batch, lines, bank, user, True, approved)
            user.role = 'accountant'
            user._sections = {'ledger': 'view'}
            checked = classify_cash_funding(s, batch, lines, bank, user, True)
            self.assertEqual(checked[lines[0].id]['status'], 'review')
            self.assertIsNone(lines[0].ledger_id)

    def test_prior_batch_claim_and_cancelled_fee_are_never_linked_again(self):
        with unit(True) as s:
            batch, lines, bank, cash, transit, user, fee_cat, req, funding = self.prepared(s)
            approved = classify_cash_funding(s, batch, lines, bank, user, True)
            process_cash_funding(s, batch, lines, bank, user, True, approved)
            another = BankStatementBatch(**{column.name: getattr(batch, column.name)
                for column in BankStatementBatch.__table__.columns if column.name != 'id'})
            another.sha256 = 'd'*64;another.status = 'preview';s.add(another);s.flush()
            repeated = BankStatementLine(**{column.name: getattr(lines[0], column.name)
                for column in BankStatementLine.__table__.columns if column.name != 'id'})
            repeated.batch_id = another.id;repeated.status='review';repeated.ledger_id=None
            repeated.fingerprint='e'*64;repeated.review_note='';s.add(repeated);s.flush()
            self.assertEqual(classify_cash_funding(s, another, [repeated], bank, user, True)[repeated.id]['status'], 'review')
            reverse = Ledger(account_id=bank.id, category_id=fee_cat.id, kind='in',
                amount=funding.fee_amount, date=funding.withdraw_date, reference='REV-FEE-TEST',
                reversal_of=funding.fee_ledger_id, creator_id=user.id)
            s.add(reverse);s.flush()
            self.assertEqual(accounted_funding_line_ids(s, lines, bank), set())
            checked = _evaluated(s, batch, lines, bank, user, fee_cat.id)
            self.assertEqual(checked['rows'][0]['status'], 'review')

    def test_foreign_cash_currency_or_changed_bank_amount_blocks_group(self):
        with unit(True) as s:
            batch, lines, bank, cash, transit, user, fee_cat, req, funding = self.prepared(s)
            cash.currency='USD';s.flush()
            self.assertEqual(classify_cash_funding(s, batch, lines, bank, user, True)[lines[0].id]['status'], 'review')
            cash.currency='UZS'
            transfer=s.get(Ledger,funding.transfer_ledger_id);transfer.amount+=1;s.flush()
            self.assertEqual(classify_cash_funding(s, batch, lines, bank, user, True)[lines[0].id]['status'], 'review')
            transfer.amount-=1
            other=s.scalar(select(Company).where(Company.id!=self.company,Company.code!='UNASSIGNED'))
            cash.company_id=other.id
            with self.assertRaises(HTTPException) as caught:s.flush()
            self.assertEqual(caught.exception.status_code,404)
            cash.company_id=self.company;s.flush()

    def test_cashflow_counts_fee_then_cash_issue_not_internal_transfer(self):
        with unit(True) as s:
            batch, lines, bank, cash, transit, user, fee_cat, req, funding = self.prepared(s)
            cutoff=date.fromisoformat(self.date)
            current=report(s,2026,'UZS',self.company,as_of=cutoff)
            self.assertEqual(current['totals'][8], '-10000.00')
            self.assertEqual(sum(account_balance(s,a) for a in (bank,cash,transit)), 99000000)
            receive=Ledger(account_id=transit.id,to_account_id=cash.id,kind='transfer',amount=funding.net_amount,
                date=cutoff,reference='CF-CASH-RECEIVE-TEST',creator_id=user.id)
            s.add(receive);s.flush();funding.receipt_ledger_id=receive.id;funding.state='received'
            self.assertEqual(report(s,2026,'UZS',self.company,as_of=cutoff)['totals'][8], '-10000.00')
            self.assertEqual((account_balance(s,cash),account_balance(s,transit)),(99000000,0))
            issue=Ledger(account_id=cash.id,category_id=req.category_id,kind='out',amount=req.amount,
                date=cutoff,reference='CF-CASH-ISSUE-TEST',request_id=req.id,creator_id=user.id)
            s.add(issue);s.flush();req.status='paid'
            final=report(s,2026,'UZS',self.company,as_of=cutoff)
            self.assertEqual(final['totals'][8], '-1000000.00')
            self.assertEqual(sum(account_balance(s,a) for a in (bank,cash,transit)),0)
            self.assertEqual(len(list(s.scalars(effective_cashflows(s)))),4)
            self.assertEqual(next(row for row in final['rows'] if row['key']==f'{fee_cat.id}:out')['values'][8],'-10000.00')

    def test_deleting_statement_preserves_all_already_recorded_funding_facts(self):
        with unit(True) as s:
            batch, lines, bank, cash, transit, user, fee_cat, req, funding = self.prepared(s)
            approved=classify_cash_funding(s,batch,lines,bank,user,True)
            process_cash_funding(s,batch,lines,bank,user,True,approved)
            batch_id=batch.id;req_id=req.id;bank_id=bank.id
            funding.state='received'
            receive=Ledger(account_id=transit.id,to_account_id=cash.id,kind='transfer',amount=funding.net_amount,
                date=funding.withdraw_date,reference='CF-CASH-RECEIVE-TEST',creator_id=user.id)
            s.add(receive);s.flush();funding.receipt_ledger_id=receive.id
            issue=Ledger(account_id=cash.id,category_id=req.category_id,kind='out',amount=req.amount,
                date=funding.withdraw_date,reference='CF-CASH-ISSUE-TEST',request_id=req.id,creator_id=user.id)
            s.add(issue);s.flush();req.status='paid'
        headers={**self.h,'X-Company-ID':str(self.company)}
        preview=self.client.get(f'/api/bank-statements/{batch_id}/delete-preview',headers=headers)
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(preview.json()['operation_count'],0)
        response=self.client.post(f'/api/bank-statements/{batch_id}/delete',headers=headers,json={
            'expected':preview.json()['delete_token'],'reason':'Synthetic bank file deletion, preserve existing facts','confirm':True})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['removed_operations'],0)
        with unit() as s:
            s.info['company_id']=self.company
            self.assertEqual(s.get(BankStatementBatch,batch_id).status,'deleted')
            self.assertEqual(s.get(PaymentRequest,req_id).status,'paid')
            self.assertEqual(len(list(s.scalars(effective_cashflows(s)))),4)
            self.assertEqual(account_balance(s,s.get(Account,bank_id)),0)

    def test_signed_reprocess_history_daily_and_bot_readback_share_same_compound_fact(self):
        with unit(True) as s:
            batch, lines, bank, cash, transit, user, fee_cat, req, funding = self.prepared(s)
            batch_id=batch.id;fee_id=fee_cat.id;bank_id=bank.id
        headers={**self.h,'X-Company-ID':str(self.company)}
        endpoint=f'/api/bank-statements/{batch_id}/reprocess'
        preview=self.client.post(endpoint+'/preview',headers=headers,json={'fee_category_id':fee_id})
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(preview.json()['rows'][0]['status'],CANDIDATE)
        payload={'fee_category_id':fee_id,'preview_token':preview.json()['preview_token'],'confirm':True}
        commit=self.client.post(endpoint+'/commit',headers=headers,json=payload)
        self.assertEqual(commit.status_code,200,commit.text)
        self.assertEqual((commit.json()['posted'],commit.json()['linked']),(0,1))
        self.assertTrue(commit.json()['full_reconciliation'])
        self.assertEqual(self.client.post(endpoint+'/commit',headers=headers,json=payload).status_code,409)
        history=self.client.get(f'/api/bank-statements/history/{batch_id}',headers=headers)
        self.assertEqual(history.status_code,200,history.text)
        self.assertEqual(history.json()['rows'][0]['operation_state'],'active')
        self.assertEqual(history.json()['posting_summary']['pending']['count'],0)
        daily=self.client.get('/api/bank-statements/daily',headers=headers,params={'date':self.date,'currency':'UZS'})
        self.assertEqual(daily.status_code,200,daily.text)
        self.assertEqual((daily.json()['debit_total'],daily.json()['credit_total']),('1000000.00','0.00'))
        with unit() as s:
            from app.bot_api import statement_report_warnings
            s.info['company_id']=self.company
            company=s.get(Company,self.company);bank=s.get(Account,bank_id)
            messages=statement_report_warnings(s,{company.id:company},{bank.id:bank},date.fromisoformat(self.date))
            self.assertTrue(any('Состояние: сверено.' in item['message'] for item in messages))
            self.assertTrue(any('Учтено строк: 1 из 1' in item['message'] for item in messages))
            self.assertEqual(len(list(s.scalars(select(Ledger)))),2)

    def test_explicit_overlapping_gross_duplicate_keeps_compound_proof_without_second_hold(self):
        with unit(True) as s:
            batch, lines, bank, cash, transit, user, fee_cat, req, funding = self.prepared(s)
            process_cash_funding(s,batch,lines,bank,user,True,
                classify_cash_funding(s,batch,lines,bank,user,True))
            pending=BankStatementLine(**{column.name:getattr(lines[0],column.name)
                for column in BankStatementLine.__table__.columns if column.name!='id'})
            pending.amount=100000;pending.document_number='PENDING-OTHER';pending.fingerprint='f'*64
            pending.status='review';pending.ledger_id=None;pending.review_note='';pending.classification='unresolved'
            s.add(pending);batch.debit_total+=pending.amount;batch.closing-=pending.amount
            newer=BankStatementBatch(**{column.name:getattr(batch,column.name)
                for column in BankStatementBatch.__table__.columns if column.name!='id'})
            newer.sha256='d'*64;newer.committed_at=now();newer.debit_total=funding.gross_amount
            newer.closing=bank.opening-funding.gross_amount;s.add(newer);s.flush()
            duplicate=BankStatementLine(**{column.name:getattr(lines[0],column.name)
                for column in BankStatementLine.__table__.columns if column.name!='id'})
            duplicate.batch_id=newer.id;duplicate.status='ambiguous_duplicate';duplicate.uncertain=True
            duplicate.ledger_id=None;duplicate.review_note='';duplicate.classification='unresolved'
            duplicate.include_in_daily=True;s.add(duplicate);s.flush()
            set_statement_reconciliation(s,batch,False,user);set_statement_reconciliation(s,newer,False,user)
            duplicate_id=duplicate.id;new_id=newer.id;bank_id=bank.id
        headers={**self.h,'X-Company-ID':str(self.company)}
        path=f'/api/bank-statements/lines/{duplicate_id}'
        choices=self.client.get(path+'/duplicate-candidates',headers=headers)
        self.assertEqual(choices.status_code,200,choices.text)
        self.assertEqual(len(choices.json()['items']),1)
        choice=choices.json()['items'][0]
        response=self.client.post(path+'/duplicate-resolve',headers=headers,json={
            'prior_line_id':choice['line_id'],'token':choice['token'],
            'reason':'Synthetic confirmation of repeated bank evidence, not another withdrawal','confirm':True})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],MATCHED)
        self.assertEqual(self.client.post(path+'/duplicate-resolve',headers=headers,json={
            'prior_line_id':choice['line_id'],'token':choice['token'],
            'reason':'Synthetic replay is forbidden','confirm':True}).status_code,409)
        history=self.client.get(f'/api/bank-statements/history/{new_id}',headers=headers)
        self.assertEqual(history.json()['rows'][0]['operation_state'],'active')
        self.assertEqual(history.json()['posting_summary']['pending']['count'],0)
        with unit() as s:
            s.info['company_id']=self.company
            bank=s.get(Account,bank_id)
            self.assertEqual(pending_bank_debit_hold(s,bank),100000)
            self.assertFalse(s.get(BankStatementLine,duplicate_id).include_in_daily)
            self.assertEqual(len(list(s.scalars(select(Ledger)))),2)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(BankCashFundingTests(name) for name in BankCashFundingTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
