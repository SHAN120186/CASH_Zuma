"""Synthetic end-to-end bank import tests; never connect to production."""
import unittest
import json
from datetime import timedelta
from unittest.mock import patch
from sqlalchemy import select

from tests.test_site import SiteTests
from app.db import PaymentRequest, Ledger, Account, Company, Setting, User, unit, engine
from app.section_access import key


class BankStatementTests(SiteTests):
    def setUp(self):
        super().setUp()
        self.payer = self.make_user('accountant', 'statement_payer')
        self.counter = 0

    def approved(self, amount='600'):
        self.budget('100000')
        response = self.request(amount)
        self.assertEqual(response.status_code, 200, response.text)
        rid = response.json()['id']
        with unit(True) as s:
            s.get(PaymentRequest, rid).counterparty_inn = '300000002'
        result = self.approve(rid)
        self.assertEqual(result.status_code, 200, result.text)
        return rid

    def source(self, rows=None, ready=True):
        if rows is None:
            rows = [self.bank_row()]
        return {'account_number':'20200000000000000001', 'owner_name':'Test', 'owner_inn':'300000001',
                'period_from':self.date, 'period_to':self.date, 'opening_balance':'1000.00',
                'closing_balance':'400.00', 'debit_total':'600.00', 'credit_total':'0.00',
                'balance_matches':ready, 'declared_totals_match':ready, 'ready':ready, 'rows':rows}

    def bank_row(self, number='1', date=None, amount='600.00', inn='300000002', line=6):
        return {'line':line,'date':date or self.date,'document_number':number,'correspondent_mfo':'00014',
                'correspondent_account':'20200000000000000002','correspondent_name':'Supplier',
                'correspondent_inn':inn,'purpose':'Оплата по договору', 'debit':amount, 'credit':'0.00',
                'amount':amount,'direction':'out'}

    def preview(self, source):
        self.counter += 1
        with patch('app.bank_statement_api.parse_bank_statement', return_value=source):
            cl, headers = self.payer
            return cl.post('/api/bank-statements/preview', params={'account_id':self.acc,'currency':'UZS'},
                           content=f'synthetic-{self.counter}'.encode(), headers={**headers, 'X-Filename':'test.xls'})

    def commit(self, id, **kwargs):
        cl, headers = self.payer
        return cl.post(f'/api/bank-statements/{id}/commit',headers=headers,
                       json=kwargs or {'confirm_account':True,'confirm_currency':True})

    def test_unique_match_without_cf_and_no_preview_posting(self):
        rid = self.approved()
        response = self.preview(self.source()); self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['rows'][0]['status'],'auto_pay_candidate')
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest,rid).status,'approved')
            self.assertIsNone(s.scalar(select(Ledger).where(Ledger.request_id==rid)))
        denied = self.commit(response.json()['id'],confirm_account=True,confirm_currency=False)
        self.assertEqual(denied.status_code,422,denied.text)
        done = self.commit(response.json()['id']); self.assertEqual(done.status_code,200,done.text)
        self.assertEqual(done.json()['auto_paid'],1)
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest,rid).status,'paid')
            self.assertIsNotNone(s.scalar(select(Ledger).where(Ledger.request_id==rid)))

    def test_two_possible_bank_lines_or_requests_are_reviewed(self):
        rid = self.approved()
        rows = [self.bank_row('1'), self.bank_row('2',line=7)]
        result = self.preview(self.source(rows)); self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(all(r['status']=='review' for r in result.json()['rows']))
        with unit(True) as s:
            req = s.get(PaymentRequest,rid)
            duplicate = PaymentRequest(creator_id=req.creator_id,category_id=req.category_id,
                account_id=req.account_id,counterparty=req.counterparty,counterparty_inn=req.counterparty_inn,
                purpose='Другой черновик',amount=req.amount,due_date=req.due_date,status='draft')
            s.add(duplicate)
        result = self.preview(self.source()); self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['rows'][0]['status'],'review')
        self.assertEqual(self.commit(result.json()['id']).json()['auto_paid'],0)

    def test_previous_disputed_row_blocks_later_different_document(self):
        first = self.preview(self.source()); self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(self.commit(first.json()['id']).status_code,200)
        self.approved()
        second = self.preview(self.source([self.bank_row('other')])); self.assertEqual(second.status_code,200,second.text)
        self.assertEqual(second.json()['rows'][0]['status'],'review')
        self.assertEqual(self.commit(second.json()['id']).json()['auto_paid'],0)

    def test_mismatch_and_duplicate_rows_do_not_pay(self):
        self.approved()
        rows=[self.bank_row('1'),self.bank_row('1',line=7)]
        p=self.preview(self.source(rows)); self.assertEqual(p.status_code,200,p.text)
        self.assertTrue(all(r['status']=='ambiguous_duplicate' and r['uncertain'] for r in p.json()['rows']))
        self.assertEqual(self.commit(p.json()['id']).json()['auto_paid'],0)
        bad=self.preview(self.source([self.bank_row('3',inn='300000999')])); self.assertEqual(bad.status_code,200,bad.text)
        self.assertEqual(bad.json()['rows'][0]['status'],'review')
        blocked=self.preview(self.source([self.bank_row('4')],ready=False)); self.assertEqual(blocked.status_code,200,blocked.text)
        self.assertEqual(self.commit(blocked.json()['id']).status_code,409)

    def test_different_rows_have_different_candidates_and_sales_cannot_view_daily(self):
        self.approved()
        rows = [self.bank_row('1'),self.bank_row('2',amount='1.00',inn='300000999',line=7)]
        preview = self.preview(self.source(rows)); self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual([line['status'] for line in preview.json()['rows']], ['auto_pay_candidate','review'])
        sales, headers = self.make_user('sales','statement_sales')
        try:
            with unit(True) as s:
                user = s.scalar(select(User).where(User.username == 'statement_sales').execution_options(company_unscoped=True))
                s.add(Setting(key=key(self.company,user.id),value=json.dumps({'version':1,'sections':{'home':'view'}})))
            result = sales.get('/api/bank-statements/daily',params={'date':self.date,'currency':'UZS'},headers=headers)
            self.assertEqual(result.status_code,403,result.text)
        finally:
            sales.close()

    def test_insufficient_actual_balance_never_posts(self):
        rid = self.approved()
        with unit(True) as s:
            s.get(Account,self.acc).opening = 0
        preview=self.preview(self.source()); self.assertEqual(preview.status_code,200,preview.text)
        result=self.commit(preview.json()['id']); self.assertEqual(result.status_code,200,result.text)
        self.assertEqual((result.json()['auto_paid'],result.json()['review']),(0,1))
        cl,h=self.payer
        reviews=cl.get('/api/bank-statements/reviews',headers=h).json()['items']
        self.assertIn('Недостаточно фактических средств',reviews[0]['reason'])
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest,rid).status,'approved')
            self.assertIsNone(s.scalar(select(Ledger).where(Ledger.request_id==rid)))

    def test_manual_match_different_planned_date_then_duplicate_blocked(self):
        rid = self.approved()
        with unit(True) as s:
            s.get(PaymentRequest,rid).due_date += timedelta(days=1)
        preview=self.preview(self.source()); self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(preview.json()['rows'][0]['status'],'review')
        self.assertEqual(self.commit(preview.json()['id']).status_code,200)
        cl,h=self.payer; line_id=preview.json()['rows'][0]['id']
        result=cl.get(f'/api/bank-statements/lines/{line_id}/candidates',headers=h)
        self.assertEqual(result.status_code,200,result.text)
        candidate=result.json()['items'][0]
        self.assertNotEqual(candidate['date'],self.date)
        body={'request_id':rid,'request_version':candidate['version'],'confirm':True,'note':'Проверено по документу банка'}
        result=cl.post(f'/api/bank-statements/lines/{line_id}/match',headers=h,json=body)
        self.assertEqual(result.status_code,200,result.text); self.assertEqual(result.json()['status'],'manual_paid')
        self.assertEqual(cl.post(f'/api/bank-statements/lines/{line_id}/match',headers=h,json=body).status_code,409)
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger).where(Ledger.request_id==rid)))),1)

    def test_manual_link_existing_payment_does_not_post_twice(self):
        rid=self.approved()
        paid=self.ledger('600','out',request_id=rid); self.assertEqual(paid.status_code,200,paid.text)
        with unit(True) as s:
            req=s.get(PaymentRequest,rid); req.due_date += timedelta(days=1)
            # Another plausible request makes the automatic result ambiguous;
            # a person can still select the proven existing payment.
            s.add(PaymentRequest(creator_id=req.creator_id,category_id=req.category_id,
                account_id=req.account_id,counterparty=req.counterparty,counterparty_inn=req.counterparty_inn,
                purpose='Synthetic ambiguous request',amount=req.amount,
                due_date=req.due_date-timedelta(days=1),status='draft'))
        preview=self.preview(self.source()); self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(self.commit(preview.json()['id']).status_code,200)
        cl,h=self.payer; line_id=preview.json()['rows'][0]['id']
        candidates=cl.get(f'/api/bank-statements/lines/{line_id}/candidates',headers=h).json()['items']
        candidate=next(c for c in candidates if c['id']==rid)
        result=cl.post(f'/api/bank-statements/lines/{line_id}/match',headers=h,json={
            'request_id':rid,'request_version':candidate['version'],'confirm':True,'note':'Сверено с банковским документом'})
        self.assertEqual(result.status_code,200,result.text); self.assertEqual(result.json()['status'],'manual_match')
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger).where(Ledger.request_id==rid)))),1)

    def test_other_company_cannot_see_import_and_commit_cannot_be_replayed(self):
        p=self.preview(self.source()); self.assertEqual(p.status_code,200,p.text)
        self.assertEqual(self.commit(p.json()['id']).status_code,200)
        self.assertEqual(self.commit(p.json()['id']).status_code,409)
        with unit() as s:
            other=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
        cl,h=self.payer
        foreign={**h,'X-Company-ID':str(other)}
        result=cl.get(f"/api/bank-statements/{p.json()['id']}",headers=foreign)
        self.assertIn(result.status_code,(403,404),result.text)

    def test_failed_payment_does_not_undo_other_row(self):
        first=self.approved('700')
        response=self.request('600'); self.assertEqual(response.status_code,200,response.text)
        second=response.json()['id']
        with unit(True) as s:
            s.get(PaymentRequest,second).counterparty_inn='300000003'
        self.assertEqual(self.approve(second).status_code,200)
        with unit(True) as s:
            s.get(Account,self.acc).opening=60000
        p=self.preview(self.source([self.bank_row('1',amount='700.00'),
            self.bank_row('2',amount='600.00',inn='300000003',line=7)]))
        self.assertEqual(p.status_code,200,p.text)
        result=self.commit(p.json()['id']); self.assertEqual(result.status_code,200,result.text)
        self.assertEqual((result.json()['auto_paid'],result.json()['review']),(1,1))
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest,first).status,'approved')
            self.assertEqual(s.get(PaymentRequest,second).status,'paid')

    def test_hidden_requests_do_not_trigger_or_disclose_match(self):
        self.approved()
        with unit(True) as s:
            user=s.scalar(select(User).where(User.username=='statement_payer').execution_options(company_unscoped=True))
            s.add(Setting(key=key(self.company,user.id),value=json.dumps({'version':1,'sections':{'requests':'hidden'}})))
        p=self.preview(self.source()); self.assertEqual(p.status_code,200,p.text)
        line=p.json()['rows'][0]
        self.assertEqual(line['status'],'review'); self.assertIsNone(line['request_id'])
        self.assertEqual(self.commit(p.json()['id']).json()['auto_paid'],0)


def load_tests(loader, tests, pattern):
    # Do not re-run inherited SiteTests here.
    return unittest.TestSuite(BankStatementTests(name) for name in BankStatementTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
