"""Synthetic tests for account-scoped unreconciled-statement payment guard."""
import unittest
from datetime import date
from fastapi import HTTPException
from sqlalchemy import select

from tests.test_site import SiteTests
from app.bank_statement_guard import set_statement_reconciliation, unreconciled_statements, require_reconciled_for_manual_payment
from app.bank_statement_models import BankStatementBatch
from app.db import Account, Company, Setting, User, engine, now, unit


class StatementGuardTests(SiteTests):
    def setUp(self):
        super().setUp()
        self.day=date.fromisoformat(self.date)
        with unit(True) as s:
            user=s.scalar(select(User).where(User.username=='admin'))
            self.admin=user.id
            foreign=s.scalar(select(Company.id).where(Company.code=='ZUMA'));self.foreign_company=foreign
            other=Account(company_id=self.company,name='Synthetic other bank',kind='bank',currency='UZS',
                          opening=0,opening_date=self.day,created_by=user.id)
            foreign_account=Account(company_id=foreign,name='Synthetic foreign bank',kind='bank',currency='UZS',
                                    opening=0,opening_date=self.day,created_by=user.id)
            s.add_all([other,foreign_account]);s.flush()
            self.other,self.foreign=other.id,foreign_account.id
            def batch(company,account,n):
                return BankStatementBatch(company_id=company,account_id=account,created_by=user.id,
                    filename='synthetic.xls',sha256=str(n)*64,account_number=f'{n:020d}',owner_name='Synthetic',
                    owner_inn='300000001',currency='UZS',period_from=self.day,period_to=self.day,opening=0,
                    closing=0,debit_total=0,credit_total=0,balance_matches=True,declared_totals_match=True,
                    source_ready=True,status='committed',committed_at=now())
            b1=batch(self.company,self.acc,1);b2=batch(self.company,self.acc,2)
            b3=batch(self.company,self.other,3);b4=batch(foreign,self.foreign,4)
            s.add_all([b1,b2,b3,b4]);s.flush()
            self.b1,self.b2,self.b3,self.b4=(b.id for b in (b1,b2,b3,b4))

    def update(self,batch_id,complete,company=None):
        with unit(True) as s:
            s.info['company_id']=company or self.company
            set_statement_reconciliation(s,s.get(BankStatementBatch,batch_id),complete,s.get(User,self.admin))

    def blocked(self,account_id,company=None):
        with unit() as s:
            s.info['company_id']=company or self.company
            a=s.get(Account,account_id)
            return len(unreconciled_statements(s,a))

    def test_multiple_statement_markers_release_only_when_all_are_resolved(self):
        self.update(self.b1,False);self.update(self.b2,False)
        self.assertEqual(self.blocked(self.acc),2)
        with unit() as s:
            s.info['company_id']=self.company
            with self.assertRaises(HTTPException) as e:require_reconciled_for_manual_payment(s,s.get(Account,self.acc))
            self.assertEqual(e.exception.status_code,409)
        self.update(self.b1,True);self.assertEqual(self.blocked(self.acc),1)
        self.update(self.b2,True);self.assertEqual(self.blocked(self.acc),0)
        with unit() as s:
            s.info['company_id']=self.company
            require_reconciled_for_manual_payment(s,s.get(Account,self.acc))

    def test_isolation_by_company_and_account(self):
        self.update(self.b1,False)
        self.assertEqual(self.blocked(self.other),0)
        self.assertEqual(self.blocked(self.foreign,self.foreign_company),0)
        self.update(self.b4,False,self.foreign_company)
        self.assertEqual(self.blocked(self.acc),1)
        self.assertEqual(self.blocked(self.foreign,self.foreign_company),1)
        self.update(self.b1,True)
        self.assertEqual(self.blocked(self.foreign,self.foreign_company),1)
        with unit() as s:
            foreign_account=s.get(Account,self.foreign)
            s.info['company_id']=self.company
            with self.assertRaises(HTTPException):unreconciled_statements(s,foreign_account)

    def test_invalid_inputs_and_transaction_rollback_never_release(self):
        self.update(self.b1,False)
        with self.assertRaises(RuntimeError):
            with unit(True) as s:
                s.info['company_id']=self.company
                set_statement_reconciliation(s,s.get(BankStatementBatch,self.b1),True,s.get(User,self.admin))
                raise RuntimeError('abort synthetic transaction')
        self.assertEqual(self.blocked(self.acc),1)
        with unit(True) as s:
            s.info['company_id']=self.company
            b=s.get(BankStatementBatch,self.b1);user=s.get(User,self.admin)
            with self.assertRaises(ValueError):set_statement_reconciliation(s,b,None,user)
            b.status='preview'
            with self.assertRaises(ValueError):set_statement_reconciliation(s,b,True,user)
            b.status='committed'
        self.assertEqual(self.blocked(self.acc),1)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(StatementGuardTests(name) for name in StatementGuardTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
