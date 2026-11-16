"""Exact report values with one narrow ledger read, using synthetic data only."""
import unittest
from datetime import timedelta
from unittest.mock import patch
from sqlalchemy import event, select
from tests import test_group_daily_summary as daily_tests
from app import bot_api
from app.db import Account, Ledger, User, unit, engine
from app.services import account_balance, day_start_balance

class LegacyBalances:
    def __init__(self,s,accounts,through):self.s=s
    def closing(self,a,day):return account_balance(self.s,a,day)
    def opening(self,a,day):return day_start_balance(self.s,a,day)

class ReportBalanceTransferTests(unittest.TestCase):
    setUp=daily_tests.GroupDailySummaryTests.setUp
    tearDown=daily_tests.GroupDailySummaryTests.tearDown
    account=daily_tests.GroupDailySummaryTests.account
    post=daily_tests.GroupDailySummaryTests.post
    ledger=daily_tests.GroupDailySummaryTests.ledger
    zuma=daily_tests.GroupDailySummaryTests.zuma
    category=daily_tests.GroupDailySummaryTests.category
    data=daily_tests.GroupDailySummaryTests.data
    daily=daily_tests.GroupDailySummaryTests.daily
    request_row=daily_tests.GroupDailySummaryTests.request_row

    def seed(self):
        with unit(True) as s:
            user=s.scalar(select(User).where(User.username=='admin'))
            ids=[self.acc]
            for n,day in enumerate([self.today,self.today+timedelta(days=1),self.yesterday]):
                a=Account(company_id=self.companies['UZGERMED'],name='Synthetic '+str(n),kind='cash',currency='UZS',
                          opening=100+n,opening_date=day,created_by=user.id)
                s.add(a);s.flush();ids.append(a.id)
            for n in range(100):
                s.add(Ledger(account_id=ids[n%4],to_account_id=ids[(n+1)%4] if n%3==2 else None,
                    kind=['in','out','transfer'][n%3],amount=100+n,date=self.today-timedelta(days=n%3),
                    reference='synthetic-'+str(n),note='not needed for balances '*50,creator_id=user.id))
            # Both legs of a same-account transfer must cancel, even for legacy data.
            s.add(Ledger(account_id=ids[0],to_account_id=ids[0],kind='transfer',amount=987,
                         date=self.today,reference='self-transfer',creator_id=user.id))
        return ids

    def test_reader_parity_dates_opening_day_and_unbounded_integer_arithmetic(self):
        ids=self.seed()
        with unit() as s:
            accounts={a.id:a for a in s.scalars(select(Account).where(Account.id.in_(ids)))}
            reader=bot_api.ReportBalances(s,accounts,self.today)
            for day in [self.today-timedelta(days=10),self.yesterday,self.today,self.today+timedelta(days=1)]:
                for a in accounts.values():
                    self.assertEqual(reader.closing(a,day),account_balance(s,a,day))
                    self.assertEqual(reader.opening(a,day),day_start_balance(s,a,day))
            # Python arithmetic remains exact above SQL int64 SUM range.
            reader.rows=[(ids[0],None,'in',10**19,self.today)]*2
            reader.by_date={}
            self.assertEqual(reader.closing(accounts[ids[0]],self.today),accounts[ids[0]].opening+2*10**19)

    def test_complete_payload_and_revision_match_legacy_reader(self):
        self.seed(); self.request_row('pending')
        actual=self.data()
        with patch.object(bot_api,'ReportBalances',LegacyBalances):expected=self.data()
        actual.pop('generated_at');expected.pop('generated_at')
        self.assertEqual(actual,expected)

    def test_one_narrow_select_for_all_accounts_dates_and_no_shared_cache(self):
        ids=self.seed()
        with unit() as s:
            accounts={a.id:a for a in s.scalars(select(Account).where(Account.id.in_(ids)))}
            statements=[]
            def capture(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
            event.listen(engine,'before_cursor_execute',capture)
            try:
                reader=bot_api.ReportBalances(s,accounts,self.today)
                for a in accounts.values():
                    reader.closing(a,self.today);reader.opening(a,self.today);reader.closing(a,self.yesterday)
            finally:event.remove(engine,'before_cursor_execute',capture)
            self.assertEqual(len(statements),1)
            projection=statements[0].split('FROM')[0]
            self.assertNotIn('note',projection);self.assertNotIn('reference',projection)
            self.assertEqual(len(projection.split(',')),5)
            self.assertEqual(bot_api.ReportBalances(s,{},self.today).rows,[])
        before=self.data()['revision']
        self.ledger('9','in','fresh-read',day=self.today)
        self.assertNotEqual(before,self.data()['revision'])

    def test_statement_and_transit_warnings_match_legacy_reader(self):
        from app.bank_statement_models import BankStatementBatch
        with unit(True) as s:
            author=s.scalar(select(User).where(User.username=='admin'))
            account=s.get(Account,self.acc)
            s.add(BankStatementBatch(company_id=account.company_id,account_id=account.id,created_by=author.id,
                filename='synthetic.xls',sha256='a'*64,account_number='synthetic',owner_name='Synthetic',owner_inn='synthetic',
                currency='UZS',period_from=self.today,period_to=self.today,opening=account.opening,closing=account.opening,
                debit_total=0,credit_total=0,balance_matches=True,source_ready=True,status='committed'))
            s.add(Account(company_id=account.company_id,name='Synthetic transit',kind='transit',currency='UZS',
                          opening=12345,opening_date=self.yesterday,created_by=author.id))
        actual=self.data()
        with patch.object(bot_api,'ReportBalances',LegacyBalances):expected=self.data()
        actual.pop('generated_at');expected.pop('generated_at')
        self.assertEqual(actual,expected)
        self.assertTrue(actual['warnings'])
        # Same warning text/revision for historical, incomplete and pending files.
        from app.bank_statement_models import BankStatementLine
        for mode in ('opening_day', 'historical', 'incomplete', 'pending', 'zero_transit'):
            with self.subTest(mode=mode):
                with unit(True) as s:
                    batch=s.scalar(select(BankStatementBatch))
                    account=s.get(Account,self.acc)
                    if mode=='opening_day':
                        account.opening_date=self.today
                    elif mode=='historical':
                        account.opening_date=self.yesterday-timedelta(days=1)
                        batch.period_from=batch.period_to=self.yesterday
                    elif mode=='incomplete':
                        batch.source_ready=False
                    elif mode=='pending':
                        batch.source_ready=True
                        s.add(BankStatementLine(company_id=batch.company_id,batch_id=batch.id,account_id=account.id,
                            line=1,date=self.yesterday,direction='out',amount=123,document_number='synthetic',
                            correspondent_mfo='',correspondent_account='',correspondent_name='',correspondent_inn='',
                            purpose='synthetic',fingerprint='b'*64))
                    else:
                        for a in s.scalars(select(Account).where(Account.kind=='transit')):a.opening=0
                actual=self.data()
                with patch.object(bot_api,'ReportBalances',LegacyBalances):expected=self.data()
                actual.pop('generated_at');expected.pop('generated_at')
                self.assertEqual(actual,expected)

    def test_company_scope_and_synthetic_transfer_size(self):
        import json
        ids=self.seed()
        with unit() as s:
            s.info['company_id']=self.companies['UZGERMED']
            accounts={a.id:a for a in s.scalars(select(Account))}
            reader=bot_api.ReportBalances(s,accounts,self.today)
            for a in accounts.values():
                self.assertEqual(reader.closing(a,self.today),account_balance(s,a,self.today))
            full=list(s.execute(select(*Ledger.__table__.c).where(Ledger.date<=self.today)))
            legacy_calls=sum(int(a.opening_date<=self.today)+int(a.opening_date<self.today) for a in accounts.values())
            full_bytes=len(json.dumps([list(row) for row in full],default=str).encode())*legacy_calls
            narrow_bytes=len(json.dumps([list(row) for row in reader.rows],default=str).encode())
            self.assertLess(narrow_bytes,full_bytes/5)
            print('SYNTHETIC_LEDGER_JSON_BYTES legacy=%d narrow=%d old_calls=%d new_calls=1' %
                  (full_bytes,narrow_bytes,legacy_calls))
