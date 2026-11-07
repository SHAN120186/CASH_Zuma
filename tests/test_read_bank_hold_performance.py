"""Read-side marker snapshots never bypass marked-account reconciliation."""
import unittest
from datetime import date, timedelta
from unittest.mock import patch
from fastapi import HTTPException
from sqlalchemy import event, select

import tests.test_site as base
from app.db import Account, Company, Ledger, User, engine, unit
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.bank_statement_guard import (pending_bank_debit_hold, read_pending_bank_debit_holds,
    set_statement_reconciliation, require_reconciled_for_manual_payment)
from app.services import dashboard, money


class ReadBankHoldTests(base.SiteTests):
    def seed(self):
        day=date.fromisoformat(self.date)
        with unit(True) as s:
            user=s.scalar(select(User).where(User.username=='admin'))
            foreign=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
            ids=[]
            for n in range(15):
                account=Account(company_id=self.company,name=f'Synthetic hold {n}',kind='bank',currency='UZS',
                    opening=100000,opening_date=day,created_by=user.id)
                s.add(account);s.flush();ids.append(account.id)
            other=Account(company_id=foreign,name='Synthetic foreign hold',kind='bank',currency='UZS',
                opening=100000,opening_date=day,created_by=user.id)
            s.add(other);s.flush();foreign_id=other.id
            # Two marked accounts, including a foreign account, with a pending,
            # already-accounted, and future debit. The other14 are unmarked.
            for account_id,company in ((ids[0],self.company),(other.id,foreign)):
                s.info['company_id']=company
                batch=BankStatementBatch(company_id=company,account_id=account_id,created_by=user.id,
                    filename='synthetic.xls',sha256=str(account_id).zfill(64),account_number=str(account_id).zfill(20),
                    owner_name='Synthetic',owner_inn='300000001',currency='UZS',period_from=day,period_to=day,
                    opening=100000,closing=99300,debit_total=700,credit_total=0,balance_matches=True,
                    declared_totals_match=True,source_ready=True,status='committed')
                s.add(batch);s.flush()
                entry=Ledger(account_id=account_id,kind='out',amount=200,date=day,reference='Synthetic paid',creator_id=user.id)
                s.add(entry);s.flush()
                for n,amount in enumerate((500,200,800)):
                    s.add(BankStatementLine(company_id=company,batch_id=batch.id,account_id=account_id,line=n+6,
                        date=day+timedelta(days=1) if n==2 else day,direction='out',amount=amount,
                        document_number=str(n),correspondent_mfo='',correspondent_account='',
                        correspondent_name='Synthetic',correspondent_inn='',purpose='Synthetic',
                        fingerprint=str(n).zfill(64),occurrence=1,status='review',reason='',
                        ledger_id=entry.id if n==1 else None,include_in_daily=True,uncertain=False))
                set_statement_reconciliation(s,batch,False,user)
        return ids,foreign,foreign_id

    def test_mixed_marker_parity_and_constant_queries_for_one_vs_fifteen_accounts(self):
        ids,_,_=self.seed();counts=[];legacy=[]
        for size in (1,15):
            with unit() as s:
                s.info['company_id']=self.company
                accounts=list(s.scalars(select(Account).where(Account.id.in_(ids[:size])).order_by(Account.id)))
                statements=[]
                def count(*args):statements.append(args[2])
                event.listen(engine,'before_cursor_execute',count)
                try:expected={a.id:pending_bank_debit_hold(s,a) for a in accounts}
                finally:event.remove(engine,'before_cursor_execute',count)
                legacy.append(len(statements));statements.clear()
                event.listen(engine,'before_cursor_execute',count)
                try:actual=read_pending_bank_debit_holds(s,accounts)
                finally:event.remove(engine,'before_cursor_execute',count)
                counts.append(len(statements))
                self.assertEqual(actual,expected);self.assertEqual(actual[ids[0]],500)
                self.assertTrue(all(value==0 for aid,value in actual.items() if aid!=ids[0]))
        self.assertEqual(counts[0],counts[1]);self.assertEqual(legacy[1]-legacy[0],14)
        self.assertLess(counts[1],legacy[1])
        print('READ_HOLD_SQL_COUNTS',{'scalar':legacy,'read_batch':counts})
        empty_counts=[]
        with unit() as s:
            s.info['company_id']=self.company
            unmarked=list(s.scalars(select(Account).where(Account.id.in_([self.acc]+ids[1:]))))
            self.assertEqual(len(unmarked),15)
            for size in (1,15):
                statements=[]
                event.listen(engine,'before_cursor_execute',count)
                try:self.assertEqual(read_pending_bank_debit_holds(s,unmarked[:size]),{a.id:0 for a in unmarked[:size]})
                finally:event.remove(engine,'before_cursor_execute',count)
                empty_counts.append(len(statements))
        self.assertEqual(empty_counts,[1,1])

    def test_scope_and_same_session_marker_changes_do_not_use_stale_snapshot(self):
        ids,foreign,foreign_id=self.seed()
        with unit() as s:
            foreign_account=s.get(Account,foreign_id)
            s.info['company_id']=self.company
            with self.assertRaises(HTTPException) as error:read_pending_bank_debit_holds(s,[foreign_account])
            self.assertEqual(error.exception.status_code,404)
            self.assertNotIn(foreign_id,read_pending_bank_debit_holds(s,list(s.scalars(select(Account)))))
        with unit(True) as s:
            s.info['company_id']=self.company
            account=s.get(Account,ids[0]);user=s.scalar(select(User).where(User.username=='admin'))
            batch=s.scalar(select(BankStatementBatch).where(BankStatementBatch.account_id==account.id))
            self.assertEqual(read_pending_bank_debit_holds(s,[account])[account.id],500)
            with self.assertRaises(HTTPException):require_reconciled_for_manual_payment(s,account)
            set_statement_reconciliation(s,batch,True,user);s.flush()
            self.assertEqual(read_pending_bank_debit_holds(s,[account])[account.id],0)
            require_reconciled_for_manual_payment(s,account)

    def test_http_dashboard_parity_and_sales_hidden_counts_do_not_hide_holds(self):
        ids,foreign,foreign_id=self.seed()
        response=self.client.get('/api/accounts',headers=self.h)
        self.assertEqual(response.status_code,200,response.text)
        rows={row['id']:row for row in response.json()}
        self.assertNotIn(foreign_id,rows);self.assertEqual(rows[ids[0]]['bank_pending_debits'],money(500))
        with patch('app.main.company_role',return_value='sales'):
            hidden=self.client.get('/api/accounts',headers=self.h)
        self.assertEqual(hidden.status_code,200,hidden.text)
        hidden_rows={row['id']:row for row in hidden.json()}
        self.assertEqual(hidden_rows[ids[0]]['unreconciled_statements'],0)
        self.assertEqual(hidden_rows[ids[0]]['bank_pending_debits'],money(500))
        self.assertEqual(hidden_rows[ids[0]]['spendable_balance'],rows[ids[0]]['spendable_balance'])
        def scalar(session,accounts,upto=None):return {a.id:pending_bank_debit_hold(session,a,upto) for a in accounts}
        with unit() as s:
            s.info['company_id']=self.company
            with patch('app.bank_statement_guard.read_pending_bank_debit_holds',side_effect=scalar):expected=dashboard(s,'UZS',7)
        with unit() as s:
            s.info['company_id']=self.company
            self.assertEqual(dashboard(s,'UZS',7),expected)
        client,headers=self.make_user('sales','synthetic_hold_sales')
        try:
            self.assertEqual(client.get('/api/accounts',headers=headers).status_code,403)
            with unit() as s:uid=s.scalar(select(User.id).where(User.username=='synthetic_hold_sales'))
            grant=self.client.put(f'/api/admin/users/{uid}/sections',headers=self.h,json={
                'company_id':self.company,'version':0,'sections':{'accounts':'view'},'reason':'Synthetic read-only grant'})
            self.assertEqual(grant.status_code,200,grant.text)
            login=client.post('/api/login',json={'username':'synthetic_hold_sales','password':base.PASSWORD})
            self.assertEqual(login.status_code,200,login.text)
            headers={'X-CSRF-Token':login.json()['csrf']}
            sales=client.get('/api/accounts',headers=headers)
            self.assertEqual(sales.status_code,200,sales.text)
            sales_rows={row['id']:row for row in sales.json()}
            self.assertNotIn(foreign_id,sales_rows)
            self.assertEqual(sales_rows[ids[0]]['unreconciled_statements'],0)
            self.assertEqual(sales_rows[ids[0]]['bank_pending_debits'],money(500))
            self.assertEqual(sales_rows[ids[0]]['spendable_balance'],rows[ids[0]]['spendable_balance'])
            self.assertEqual(client.get('/api/accounts',headers={**headers,'X-Company-ID':str(foreign)}).status_code,403)
        finally:client.close()


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(ReadBankHoldTests(name) for name in ReadBankHoldTests.__dict__ if name.startswith('test_'))


def tearDownModule():base.tearDownModule()
