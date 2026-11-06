"""Request numbers use the same scoped, filtered query as text search."""
import unittest
from datetime import date, timedelta

import tests.test_site as base
from app.db import PaymentRequest, now, unit


class RequestNumberSearchTests(base.SiteTests):
    def insert_request(self, rid, **values):
        data={'id':rid,'creator_id':1,'category_id':self.cat,'account_id':self.acc,
              'counterparty':'Neutral vendor','purpose':'Neutral payment','amount':100,
              'due_date':date.fromisoformat(self.date),'status':'draft'}
        data.update(values)
        with unit(True) as s:s.add(PaymentRequest(**data))
        return rid

    def listing(self, q, client=None, headers=None, **params):
        response=(client or self.client).get('/api/requests',
            params={'paginated':'true','q':q,**params},headers=headers or self.h)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def assert_matches(self, q, expected, **params):
        data=self.listing(q,**params)
        self.assertEqual([r['id'] for r in data['items']],expected)
        self.assertEqual(data['total'],len(expected))
        return data

    def test_number_formats_match_exact_id_and_return_display_number(self):
        self.insert_request(93)
        self.insert_request(193)
        for q in ('93','00093','CF-00093','cf-00093','  CF-00093 \t',' CF-93 '):
            with self.subTest(q=q):
                data=self.assert_matches(q,[93])
                self.assertEqual(data['items'][0]['number'],'CF-00093')
        for q in ('3','CF-0009','CF-'):
            with self.subTest(q=q):self.assert_matches(q,[])
        self.assertEqual(self.row(93)['number'],'CF-00093')

    def test_number_search_filters_before_pagination(self):
        for rid in range(1,13):self.insert_request(rid)
        self.insert_request(93)
        unfiltered=self.listing('',page=3,page_size=5)
        self.assertEqual(unfiltered['total'],13)
        self.assertIn(93,[r['id'] for r in unfiltered['items']])
        self.assert_matches('CF-00093',[93],page=1,page_size=5)
        page=self.listing('93',page=2,page_size=5)
        self.assertEqual(page['total'],1)
        self.assertEqual(page['items'],[])
        response=self.client.get('/api/requests',params={'q':'CF-00093'},headers=self.h)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual([r['id'] for r in response.json()],[93])

    def test_number_search_preserves_company_and_employee_visibility(self):
        self.insert_request(93)
        employee,h=self.make_user('employee','number_search_employee')
        try:
            uid=employee.get('/api/me',headers=h).json()['user']['id']
            self.insert_request(94,creator_id=uid)
            for q in ('93','CF-00093'):
                with self.subTest(q=q):self.assert_matches(q,[],client=employee,headers=h)
            self.assert_matches('CF-00094',[94],client=employee,headers=h)
        finally:employee.close()
        _,other_h,other_cat,other_acc=self.second_company()
        self.insert_request(95,category_id=other_cat,account_id=other_acc)
        self.assert_matches('CF-00093',[],headers=other_h)
        self.assert_matches('CF-00095',[95],headers=other_h)
        self.assert_matches('95',[])

    def test_number_search_preserves_archive_status_period_and_currency(self):
        self.insert_request(93,status='paid',approved_at=now()-timedelta(days=2))
        self.assert_matches('CF-00093',[],archive='active')
        self.assert_matches('CF-00093',[93],archive='archived')
        self.assert_matches('93',[],state='pending')
        self.assert_matches('93',[93],state='paid')
        day=date.fromisoformat(self.date)
        self.assert_matches('93',[],date_from=str(day+timedelta(days=1)))
        self.assert_matches('93',[],date_to=str(day-timedelta(days=1)))
        self.assert_matches('93',[],currency='USD')
        self.assert_matches('93',[93],currency='UZS')
        # Selecting a period retains the existing rule: include both archive views.
        self.assert_matches('93',[93],archive='active',date_from=self.date,date_to=self.date)

    def test_number_search_never_returns_deleted_requests(self):
        self.insert_request(93,status='deleted',approved_at=now()-timedelta(days=2))
        for q in ('93','CF-00093'):
            for params in ({},{'archive':'archived'},{'date_from':self.date,'date_to':self.date}):
                with self.subTest(q=q,params=params):self.assert_matches(q,[],**params)

    def test_text_search_is_preserved_and_large_numbers_are_safe(self):
        self.insert_request(93,purpose='Unique purpose token')
        self.insert_request(94,counterparty='Unique vendor token',purpose='Invoice 93')
        self.assert_matches('  Unique purpose  ',[93])
        self.assert_matches('unique vendor',[94])
        self.assert_matches('Test bank',[93,94])
        self.assert_matches('93',[93,94])  # Numeric text still participates in the OR.
        for q in ('9223372036854775808','CF-9223372036854775808','9'*5000,'CF-'+'9'*5000):
            with self.subTest(length=len(q)):self.assert_matches(q,[])
        self.assert_matches('0'*5000+'93',[93])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(RequestNumberSearchTests(name) for name in RequestNumberSearchTests.__dict__ if name.startswith('test_'))


def tearDownModule():base.tearDownModule()
