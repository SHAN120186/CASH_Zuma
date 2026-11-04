"""Synthetic group/overview/bot availability while cash is still in transit."""
from decimal import Decimal
from datetime import date
import os
import unittest
from unittest.mock import patch

from sqlalchemy import select

from tests.test_cash_funding_audit import CashFundingAuditTests
from tests import test_site as base
from app.db import Account, Company, Setting, User, unit


class CashFundingGroupAvailabilityTests(CashFundingAuditTests):
    GROUP = -1007654321098
    BOT = ('cash-funding-availability-test', 'synthetic-service-secret-' + 'x'*36)

    def setUp(self):
        super().setUp()
        self.environment=patch.dict(os.environ,{
            'BOT_CLIENT_ID':self.BOT[0],'BOT_CLIENT_SECRET':self.BOT[1],
            'BOT_REPORT_GROUPS':f'{self.GROUP}:UZGERMED'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def group(self,day=None,company=None,currency='UZS'):
        day=day or date.fromisoformat(self.date)
        company=company or self.company
        response=self.client.get('/api/group-report',headers={**self.h,'X-Company-ID':str(company)},params={
            'company_id':company,'month':str(day)[:7],'day':str(day),'currency':currency})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def assert_available(self,assets,transit,available):
        data=self.group()
        self.assertEqual((data['closing'],data['in_transit_balance'],data['available']),
                         (assets,transit,available))
        overview=self.client.get('/api/dashboard',headers={**self.h,'X-Company-ID':str(self.company)},
                                 params={'currency':'UZS'}).json()
        self.assertEqual((overview['balance'],overview['in_transit_balance']), (assets,transit))
        self.assertEqual(Decimal(data['available']),Decimal(overview['spendable_balance'])-
                         Decimal(data['reserved'])-Decimal(data['funding_fee_reserved']))
        return data

    def test_group_keeps_assets_but_never_spends_transit_during_full_cycle(self):
        rid=self.cash_request()
        initial=self.assert_available('1000000.00','0.00','0.00')
        self.assertEqual((initial['reserved'],initial['funding_fee_reserved']),('990000.00','10000.00'))
        self.assertIn('резерв комиссии 10000.00',initial['text'])
        self.funding_action(rid,'withdraw',*self.stage_user('check'))
        waiting=self.assert_available('990000.00','990000.00','-990000.00')
        self.assertIn('деньги в пути 990000.00',waiting['text'])
        self.assertIn('доступно -990000.00',waiting['text'])
        self.assertEqual((waiting['reserved'],waiting['funding_fee_reserved']),('990000.00','0.00'))
        self.funding_action(rid,'receive',*self.cash_payer())
        self.assert_available('990000.00','0.00','0.00')
        self.issue(rid)
        paid=self.assert_available('0.00','0.00','0.00')
        self.assertEqual((paid['reserved'],paid['funding_fee_reserved']),('0.00','0.00'))

    def test_group_uses_historical_cutoff_after_later_withdrawal_and_receipt(self):
        rid=self.cash_request()
        before=date.fromisoformat(self.date)
        # The approved request is due tomorrow in this historical slice. Neither
        # its net payment nor its planned funding fee is reserved before due.
        future=self.group(date(2026,9,15))
        self.assertEqual((future['reserved'],future['funding_fee_reserved'],future['available']),
                         ('0.00','0.00','1000000.00'))
        self.set_day(date(2026,9,17))
        self.funding_action(rid,'withdraw',*self.stage_user('check'))
        historical=self.group(before)
        self.assertEqual((historical['closing'],historical['in_transit_balance'],historical['available']),
                         ('1000000.00','0.00','0.00'))
        self.assertEqual(historical['funding_fee_reserved'],'10000.00')
        self.set_day(date(2026,9,18))
        self.funding_action(rid,'receive',*self.cash_payer())
        historical=self.group(before)
        self.assertEqual((historical['funding_fee_reserved'],historical['available']),('10000.00','0.00'))
        waiting=self.group(date(2026,9,17))
        self.assertEqual((waiting['closing'],waiting['in_transit_balance'],waiting['available']),
                         ('990000.00','990000.00','-990000.00'))
        self.assertEqual(waiting['funding_fee_reserved'],'0.00')
        self.assert_available('990000.00','0.00','0.00')

    def test_transit_is_scoped_by_company_and_currency(self):
        rid=self.cash_request()
        with unit(True) as session:
            user=session.scalar(select(User).where(User.username=='admin'))
            other=session.scalar(select(Company).where(Company.code=='ZUMA'))
            other_id=other.id
            session.info['company_id']=self.company
            session.add(Account(company_id=self.company,name='Synthetic USD transit',kind='transit',currency='USD',
                                opening=22200,opening_date=date.fromisoformat(self.date),created_by=user.id))
            session.flush()
            session.info['company_id']=other_id
            session.add(Account(company_id=other_id,name='Synthetic foreign transit',kind='transit',currency='UZS',
                                opening=77700,opening_date=date.fromisoformat(self.date),created_by=user.id))
        self.assert_available('1000000.00','0.00','0.00')
        usd=self.group(currency='USD')
        self.assertEqual((usd['closing'],usd['in_transit_balance'],usd['available']),('222.00','222.00','0.00'))
        self.assertEqual(usd['funding_fee_reserved'],'0.00')
        foreign=self.group(company=other_id)
        self.assertEqual((foreign['closing'],foreign['in_transit_balance'],foreign['available']),('777.00','777.00','0.00'))
        self.assertEqual(foreign['funding_fee_reserved'],'0.00')
        self.funding_action(rid,'withdraw',*self.stage_user('check'))
        self.assert_available('990000.00','990000.00','-990000.00')

    def test_bot_preserves_transit_assets_but_warns_they_do_not_cover_reserve(self):
        rid=self.cash_request()
        self.funding_action(rid,'withdraw',*self.stage_user('check'))
        withdrawal_day=date.fromisoformat(self.date)
        self.set_day(date(2026,9,17))
        with unit(True) as session:
            session.add(Setting(key=f'company:{self.company}:reserve_UZS',value='100'))
        response=self.client.get('/api/bot/v1/group-daily-summary',auth=self.BOT,
            params={'chat_id':self.GROUP,'report_date':str(withdrawal_day)})
        self.assertEqual(response.status_code,200,response.text)
        daily=response.json()
        self.assertEqual(sum(Decimal(row['amount']) for row in daily['balances']),Decimal('990000.00'))
        transit=[row for row in daily['balances'] if row.get('source_kind')=='transit']
        self.assertEqual(len(transit),1)
        self.assertEqual((transit[0]['kind'],transit[0]['amount']),('cash','990000.00'))
        self.assertEqual(sum(Decimal(row['amount']) for row in daily['expense']),Decimal('10000.00'))
        self.assertTrue(any('пути' in row['message'] for row in daily['warnings']))
        response=self.client.get('/api/bot/v1/morning-summary',auth=self.BOT,
            params={'chat_id':self.GROUP,'report_date':str(withdrawal_day),'today':self.date})
        self.assertEqual(response.status_code,200,response.text)
        morning=response.json()
        self.assertEqual(sum(Decimal(row['amount']) for row in morning['balances']),Decimal('990000.00'))
        self.assertTrue(any('Доступно -990000.00' in row['message'] for row in morning['warnings']))
        self.assert_available('990000.00','990000.00','-990000.00')


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(CashFundingGroupAvailabilityTests(name)
        for name in CashFundingGroupAvailabilityTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    base.tearDownModule()
