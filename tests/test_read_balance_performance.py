"""Read-only account batching preserves ledger facts and bounds SQL work."""
import unittest
from datetime import date,timedelta
from unittest.mock import patch
from sqlalchemy import event,select

import tests.test_site as base
from app.db import Account,Company,Ledger,User,engine,unit
from app.services import account_balance,batch_account_balances,money


class ReadBalancePerformanceTests(base.SiteTests):
    def seed_balances(self):
        day=date.fromisoformat(self.date)
        with unit(True) as s:
            user=s.scalar(select(User.id).where(User.username=='admin'))
            foreign=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
            specs=[('bank',10000,self.company,day-timedelta(days=10)),
                   ('cash',500,self.company,day),('outside',800,self.company,day),
                   ('future',9999,self.company,day+timedelta(days=1)),
                   ('negative',0,self.company,day),('foreign',90000,foreign,day)]
            accounts={}
            for name,opening,company,opened in specs:
                a=Account(company_id=company,name='Synthetic '+name,kind='bank' if name=='bank' else 'cash',
                          currency='UZS',opening=opening,opening_date=opened,created_by=user)
                s.add(a);s.flush();accounts[name]=a.id
            def entry(name,kind,value,reference,target=None,when=day,reversal=None):
                t=Ledger(account_id=accounts[name],to_account_id=accounts[target] if target else None,
                         kind=kind,amount=value,date=when,reference=reference,creator_id=user,reversal_of=reversal)
                s.add(t);s.flush();return t.id
            entry('bank','in',1000,'SYN-IN')
            entry('bank','out',300,'SYN-OUT')
            entry('bank','transfer',200,'SYN-TRANSFER-OUT','cash')
            entry('outside','transfer',400,'SYN-TRANSFER-IN','bank')
            original=entry('bank','out',50,'SYN-REVERSED')
            entry('bank','in',50,'SYN-REVERSAL',reversal=original)
            entry('bank','in',500,'SYN-FUTURE',when=day+timedelta(days=1))
            entry('negative','out',2000,'SYN-NEGATIVE')
            entry('foreign','in',12345,'SYN-FOREIGN')
            # A malformed legacy cross-company target must not bypass source scope.
            entry('foreign','transfer',777,'SYN-FOREIGN-LEGACY','bank')
        return accounts,foreign

    def test_parity_for_openings_transfers_reversals_negative_and_cutoffs(self):
        ids,_=self.seed_balances();day=date.fromisoformat(self.date)
        with unit() as s:
            s.info['company_id']=self.company
            accounts=list(s.scalars(select(Account).where(Account.id.in_(ids.values()))))
            for cutoff in (day-timedelta(days=20),day-timedelta(days=1),day,day+timedelta(days=1)):
                expected={a.id:account_balance(s,a,cutoff) for a in accounts}
                self.assertEqual(batch_account_balances(s,accounts,cutoff),expected)
            current=batch_account_balances(s,accounts,day)
            self.assertEqual(current[ids['bank']],10900)
            self.assertEqual(current[ids['cash']],700)
            self.assertEqual(current[ids['outside']],400)
            self.assertEqual(current[ids['future']],0)
            self.assertEqual(current[ids['negative']],-2000)
            # Transfers from/to an omitted account still affect the chosen side.
            bank=s.get(Account,ids['bank'])
            self.assertEqual(batch_account_balances(s,[bank],day),{bank.id:10900})
            self.assertEqual(batch_account_balances(s,[],day),{})

    def test_company_scope_excludes_foreign_ledger_and_accounts(self):
        ids,foreign=self.seed_balances()
        with unit() as s:
            s.info['company_id']=self.company
            accounts=list(s.scalars(select(Account)))
            balances=batch_account_balances(s,accounts)
            self.assertNotIn(ids['foreign'],balances)
            self.assertEqual(balances[ids['bank']],account_balance(s,s.get(Account,ids['bank'])))
        with unit() as s:
            s.info['company_id']=foreign
            accounts=list(s.scalars(select(Account)))
            balances=batch_account_balances(s,accounts)
            self.assertNotIn(ids['bank'],balances)
            self.assertEqual(balances[ids['foreign']],101568)

    def test_ledger_select_count_is_constant_for_one_or_many_accounts(self):
        ids,_=self.seed_balances()
        with unit() as s:
            s.info['company_id']=self.company
            accounts=list(s.scalars(select(Account)))
            counts=[];legacy_counts=[]
            for chosen in (accounts[:1],accounts):
                statements=[]
                def counter(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
                event.listen(engine,'before_cursor_execute',counter)
                try:batch_account_balances(s,chosen)
                finally:event.remove(engine,'before_cursor_execute',counter)
                counts.append(len(statements))
                statements.clear()
                event.listen(engine,'before_cursor_execute',counter)
                try:
                    for a in chosen:account_balance(s,a)
                finally:event.remove(engine,'before_cursor_execute',counter)
                legacy_counts.append(len(statements))
            self.assertEqual(counts,[1,1])
            self.assertGreater(legacy_counts[-1],counts[-1])
            print('READ_BALANCE_SQL_COUNTS',{'legacy':legacy_counts,'batch':counts})

    def test_accounts_http_and_dashboard_match_scalar_balances_without_changing_facts(self):
        self.seed_balances()
        with unit() as s:
            s.info['company_id']=self.company
            expected={a.id:account_balance(s,a) for a in s.scalars(select(Account))}
        response=self.client.get('/api/accounts',headers=self.h)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual({r['id']:r['balance'] for r in response.json()},{aid:money(value) for aid,value in expected.items()})
        from app.services import dashboard
        with unit() as s:
            s.info['company_id']=self.company
            with patch('app.services.batch_account_balances',side_effect=lambda session,accounts,upto=None:
                       {a.id:account_balance(session,a,upto) for a in accounts}):
                scalar=dashboard(s,'UZS',7)
        with unit() as s:
            s.info['company_id']=self.company
            batched=dashboard(s,'UZS',7)
        self.assertEqual(batched,scalar)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(ReadBalancePerformanceTests(name) for name in ReadBalancePerformanceTests.__dict__ if name.startswith('test_'))


def tearDownModule():base.tearDownModule()
