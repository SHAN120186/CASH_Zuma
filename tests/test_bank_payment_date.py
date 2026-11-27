import io
import unittest
from datetime import date, timedelta

from reportlab.pdfgen import canvas
from sqlalchemy import func, select

from tests import test_bank_statement_api as fixtures
from app.db import Account, Ledger, PaymentRequest, RequestDocument, engine, unit
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.services import account_balance


class BankDateTests(unittest.TestCase):
    def setUp(self):
        f = self.f = fixtures.BankStatementTests()
        f.setUp()
        self.addCleanup(f.tearDown)
        self.rid = f.approved()
        self.fin, self.headers = f.make_user('finance', 'statement_date_finance')
        self.addCleanup(self.fin.close)
        self.bank_day = date.fromisoformat(f.date).replace(day=1) - timedelta(days=1)
        self.source = f.source([f.bank_row('TEST-71', date=str(self.bank_day))])
        self.source.update(period_from=str(self.bank_day), period_to=str(self.bank_day))
        with unit(True) as s:
            req = s.get(PaymentRequest, self.rid)
            req.status = 'paid'
            self.approval = req.approved_at
            account = s.get(Account, f.acc)
            account.opening_date = self.bank_day
            account.opening = 100000
            entry = Ledger(account_id=f.acc, request_id=req.id, category_id=req.category_id, kind='out',
                date=date.fromisoformat(f.date), amount=req.amount, reference='TEST-71', creator_id=req.creator_id)
            s.add(entry)
            s.flush()
            self.entry_id = entry.id
        draft = f.preview(self.source).json()
        self.bid = draft['id']
        self.line = draft['rows'][0]['id']
        with unit(True) as s:
            from app.db import User
            finance = s.scalar(select(User).where(User.username=='statement_date_finance'))
            s.get(BankStatementBatch, self.bid).created_by = finance.id
        self.base = f'/api/bank-statements/{self.bid}/payment-date/'
        self.data = {'request_id':self.rid, 'document_number':'TEST-71'}

    def call(self, stage='preview', **extra):
        return self.fin.post(self.base + stage, headers=self.headers, json={**self.data, **extra})

    def test_cross_month_keeps_approval_one_debit_and_draft_link(self):
        doc = io.BytesIO()
        pdf = canvas.Canvas(doc)
        pdf.drawString(40, 700, 'Contract INV-T071 / Supplier 300000002')
        pdf.save()
        with unit(True) as s:
            req = s.get(PaymentRequest, self.rid)
            req.counterparty_inn = ''
            s.get(BankStatementLine, self.line).purpose = 'Supplier contract INV-T071'
            self.assertEqual(account_balance(s, s.get(Account,self.f.acc), self.bank_day), 100000)
        self.assertEqual(self.call().status_code, 409)
        with unit(True) as s:
            s.add(RequestDocument(request_id=self.rid, kind='contract', mime='application/pdf',
                filename='INV-T071.pdf', storage_key='test-date-proof', sha256='1'*64,
                content=doc.getvalue(), version=1, active=True, created_by=1))
        preview = self.call()
        self.assertEqual(preview.status_code, 200, preview.text)
        with unit() as s:
            version = s.get(PaymentRequest, self.rid).version
        expected = preview.json()['expected']
        self.assertEqual(self.call('confirm', expected='0'*64).status_code, 409)
        result = self.call('confirm', expected=expected)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.call('confirm', expected=expected).status_code, 409)
        refreshed = self.fin.post(f'/api/bank-statements/{self.bid}/resume',
            headers=self.headers, json={'fee_category_id':None})
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        with unit() as s:
            req = s.get(PaymentRequest,self.rid)
            account = s.get(Account,self.f.acc)
            self.assertEqual((req.status, req.version, req.approved_at), ('paid',version,self.approval))
            self.assertEqual(s.get(Ledger,self.entry_id).date, self.bank_day)
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger).where(Ledger.request_id==self.rid)),1)
            self.assertEqual(account_balance(s,account,self.bank_day),40000)
            self.assertEqual(account_balance(s,account,date.fromisoformat(self.f.date)),40000)
            self.assertEqual(s.get(BankStatementLine,self.line).ledger_id,self.entry_id)
            self.assertEqual(s.get(BankStatementBatch,self.bid).status,'preview')

    def test_permissions_wrong_payee_and_negative_history(self):
        client, h = self.f.payer
        self.assertEqual(client.post(self.base+'preview',headers=h,json=self.data).status_code,403)
        with unit(True) as s:
            s.get(PaymentRequest,self.rid).counterparty_inn='300000003'
        self.assertEqual(self.call().status_code,409)
        with unit(True) as s:
            s.get(PaymentRequest,self.rid).counterparty_inn='300000002'
            s.get(Account,self.f.acc).opening=0
        preview=self.call()
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(self.call('confirm',expected=preview.json()['expected']).status_code,409)
        with unit() as s:
            self.assertEqual(s.get(Ledger,self.entry_id).date,date.fromisoformat(self.f.date))
            self.assertIsNone(s.get(BankStatementLine,self.line).ledger_id)


class EarlierBankFactTests(unittest.TestCase):
    def test_single_draft_fact_with_incomplete_book_retries_and_archives(self):
        f = fixtures.BankStatementTests()
        f.setUp()
        self.addCleanup(f.tearDown)
        rid = f.approved()
        day = date.fromisoformat(f.date) - timedelta(days=1)
        row = f.bank_row('SYN-D1', date=str(day))
        source = dict(f.source([row]), period_from=str(day), period_to=str(day), owner_name='UZGERMED TEST')
        draft = f.preview(source).json()
        line_id = draft['rows'][0]['id']
        with unit(True) as s:
            req = s.get(PaymentRequest, rid)
            version, approval = req.version, req.approved_at
            s.get(Account, f.acc).opening = 100
        cl, headers = f.payer
        path = f'/api/bank-statements/lines/{line_id}/recognize-debit'
        body = dict(request_id=rid, request_version=version, confirm=True,
                    payment_date=str(day), note='Synthetic bank fact')
        def record(**changes):
            return cl.post(path, headers=headers, json={**body, **changes})
        self.assertEqual(record(confirm=False).status_code, 422)
        self.assertEqual(record(payment_date=f.date).status_code, 409)
        result = record()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(record().json(), result.json())
        resumed = cl.post(f"/api/bank-statements/{draft['id']}/resume", headers=headers, json={})
        self.assertEqual(resumed.json()['rows'][0]['ledger_id'], result.json()['ledger_id'])
        other = f.preview(source).json()['rows'][0]['id']
        retry = cl.post(f'/api/bank-statements/lines/{other}/recognize-debit', headers=headers, json=body)
        self.assertEqual(retry.status_code, 409)
        with unit() as s:
            entry, = s.scalars(select(Ledger).where(Ledger.request_id == rid))
            req = s.get(PaymentRequest, rid)
            self.assertEqual((req.status, req.approved_at, entry.date, entry.amount), ('paid', approval, day, 60000))
            self.assertEqual(s.get(BankStatementBatch, draft['id']).status, 'preview')
        for archive, expected in [('archived', True), ('active', False)]:
            response = cl.get('/api/requests', headers=headers, params={'archive':archive, 'q':f'CF-{rid:05d}'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(any(item['id'] == rid for item in response.json()), expected)

    def test_treasury_debit_keeps_approval_and_posts_only_once(self):
        f = fixtures.BankStatementTests()
        f.setUp()
        self.addCleanup(f.tearDown)
        rid = f.approved()
        day = date.fromisoformat(f.date) - timedelta(days=1)
        personal = '4' * 27
        row = f.bank_row('SYN-19', date=str(day), inn='300000003')
        row.update(correspondent_account='23402' + '0'*15,
                   purpose='09510~' + personal + '~300000002~Synthetic services')
        source = dict(f.source([row]), period_from=str(day), period_to=str(day))
        with unit(True) as s:
            s.get(Account, f.acc).opening = 100000
            approval = s.get(PaymentRequest, rid).approved_at
        draft = f.preview(source).json()
        cl, headers = f.payer
        path = f"/api/bank-statements/lines/{draft['rows'][0]['id']}"
        with unit() as s:
            version = s.get(PaymentRequest, rid).version
        body = dict(request_id=rid, request_version=version, confirm=True, note='Verified synthetic debit')
        def match(**extra):
            return cl.post(path + '/match', headers=headers, json={**body, **extra})
        self.assertEqual(match(payment_date=str(day)).status_code, 409)
        committed = f.commit(draft['id'])
        self.assertEqual(committed.status_code, 200, committed.text)
        items = cl.get(path + '/candidates', headers=headers).json()['items']
        self.assertEqual([(i['id'], i['approved_after_bank']) for i in items], [(rid, f.date)])
        self.assertEqual(match().status_code, 409)
        self.assertEqual(match(payment_date=str(day)).status_code, 409)
        doc = io.BytesIO()
        pdf = canvas.Canvas(doc)
        pdf.drawString(40,700,'Synthetic payee 300000002 / ' + personal)
        pdf.save()
        with unit(True) as s:
            s.add(RequestDocument(request_id=rid, kind='contract', mime='application/pdf',
                filename='test.pdf', storage_key='synthetic-early-proof', sha256='1'*64,
                content=doc.getvalue(), version=1, active=True, created_by=1))
            s.get(Account, f.acc).opening += 100
        self.assertEqual(match(payment_date=str(day)).status_code, 409)
        with unit(True) as s:
            self.assertEqual(s.get(PaymentRequest,rid).status,'approved')
            self.assertIsNone(s.scalar(select(Ledger.id).where(Ledger.request_id == rid)))
            s.get(Account, f.acc).opening -= 100
        result = match(payment_date=str(day))
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(match(payment_date=str(day)).status_code,409)
        with unit() as s:
            entry, = s.scalars(select(Ledger).where(Ledger.request_id == rid))
            req = s.get(PaymentRequest, rid)
            self.assertEqual((entry.amount,entry.date,req.approved_at,req.status), (60000,day,approval,'paid'))
            self.assertEqual(account_balance(s,s.get(Account,f.acc)),40000)


def tearDownModule():
    engine.dispose()
