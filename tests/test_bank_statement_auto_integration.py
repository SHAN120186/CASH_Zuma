"""HTTP-level bank import tests using entirely synthetic statement content."""
import unittest
import json
from datetime import date, timedelta
from sqlalchemy import select

from tests.test_site import SiteTests
from app.db import Account, Audit, Budget, Category, Company, Ledger, PaymentRequest, Setting, User, engine, now, unit
from app.bank_statement_models import BankStatementBatch
from app.bank_statement_guard import set_statement_reconciliation, unreconciled_statements
from app.services import account_balance, budget_state, funds_state


class AutoBankIntegrationTests(SiteTests):
    def setUp(self):
        super().setUp()
        self.requester = self.make_user('employee', 'bank_synthetic_requester')
        self.accountant = self.make_user('accountant', 'bank_synthetic_accountant')

    def tearDown(self):
        self.requester[0].close(); self.accountant[0].close()
        super().tearDown()

    def statement(self, include_payment=True, external=False):
        day = self.date[8:10]+'.'+self.date[5:7]+'.'+self.date[:4]
        debit = '695.00' if include_payment and external else '620.00' if include_payment else '95.00' if external else '20.00'
        close = '999505.00' if include_payment and external else '999580.00' if include_payment else '1000105.00' if external else '1000180.00'
        head = f'''<html><body><table>
        <tr><td>Сведения о работе счета c {day} по {day}</td></tr>
        <tr><td>Cчет: 20200000000000000001 Synthetic Company ИНН: 300000001</td></tr>
        <tr><td>Остаток на начало периода: 1000000.00</td><td>Остаток на конец периода: {close}</td></tr>
        </table><table><tr><th>Дата</th><th>Номер документа</th><th>МФО корресп.</th>
        <th>Счет корреспондента</th><th>Наименование корресп.</th><th>ИНН корреспондента</th>
        <th>Назначение платежа</th><th>Дебет</th><th>Кредит</th></tr>'''
        def row(doc, name, inn, purpose, debit_amount='', credit_amount=''):
            return f'''<tr><td>{day}</td><td>{doc}</td><td>00014</td><td>20200000000000000002</td>
            <td>{name}</td><td>{inn}</td><td>{purpose}</td><td>{debit_amount}</td><td>{credit_amount}</td></tr>'''
        credit = row('INC001','Synthetic Buyer','300000003','Incoming synthetic','','200.00')
        payment = row('PAY001','Supplier','300000002','Synthetic order','600.00') if include_payment else ''
        fee = row('FEE001','Synthetic bank fee','300000001','00011 Погаш. ком. SWIFT - synthetic','20.00')
        other = row('OTH001','Synthetic supplier','300000005','Synthetic goods purchase','75.00') if external else ''
        tail = f'<tr><td>Итоговый оборот</td><td>{debit}</td><td>200.00</td></tr></table></body></html>'
        return (head+credit+payment+fee+other+tail).encode('cp1251')

    def approved(self):
        self.budget('100000')
        response = self.request('600', client=self.requester[0], headers=self.requester[1])
        self.assertEqual(response.status_code, 200, response.text)
        rid = response.json()['id']
        with unit(True) as s:
            s.get(PaymentRequest,rid).counterparty_inn = '300000002'
        approved = self.approve(rid)
        self.assertEqual(approved.status_code, 200, approved.text)
        return rid

    def preview(self, raw, category=None, client=None, headers=None):
        params = {'account_id':self.acc, 'currency':'UZS'}
        if category is not None:
            params['fee_category_id'] = category
        return (client or self.client).post('/api/bank-statements/preview', params=params, content=raw,
            headers={**(headers or self.h), 'X-Filename':'synthetic.xls','Content-Type':'application/octet-stream'})

    def confirmed(self, preview, category=None, client=None, headers=None, token=True):
        body = {'confirm_account':True,'confirm_currency':True,'fee_category_id':category}
        if token:
            body['preview_token'] = preview['preview_token']
        return (client or self.client).post(f"/api/bank-statements/{preview['id']}/commit", json=body,
                                             headers=headers or self.h)

    def test_mixed_statement_posts_receipt_approved_request_and_fee_once(self):
        rid = self.approved(); raw = self.statement()
        response = self.preview(raw, self.cat); self.assertEqual(response.status_code,200,response.text)
        p=response.json(); self.assertTrue(p['opening_matches'])
        self.assertEqual([r['status'] for r in p['rows']], ['income_candidate','auto_pay_candidate','fee_candidate'])
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))),0)
            self.assertEqual(s.get(PaymentRequest,rid).status,'approved')
        result=self.confirmed(p,self.cat); self.assertEqual(result.status_code,200,result.text)
        self.assertEqual((result.json()['auto_income'],result.json()['auto_paid'],result.json()['auto_fee']),(1,1,1))
        with unit() as s:
            entries=list(s.scalars(select(Ledger)))
            self.assertEqual(len(entries),3)
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),99958000)
            self.assertEqual(s.get(PaymentRequest,rid).status,'paid')
        report=self.client.get('/api/report',headers=self.h,params={'year':int(self.date[:4]),'currency':'UZS','company_id':self.company})
        self.assertEqual(report.status_code,200,report.text)
        month=int(self.date[5:7])-1; data=report.json()
        unknown=next(r for r in data['rows'] if r['activity']=='unclassified' and r['kind']=='in')
        expense=next(r for r in data['rows'] if r['category_id']==self.cat and r['kind']=='out')
        self.assertEqual((unknown['values'][month],expense['values'][month],data['totals'][month]),('200.00','-620.00','-420.00'))
        self.assertEqual(self.confirmed(p,self.cat).status_code,409)
        self.assertEqual(self.preview(raw,self.cat).status_code,409)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))),3)

    def test_old_confirmed_statement_can_be_reprocessed_without_duplicate(self):
        raw=self.statement(False)
        response=self.preview(raw); self.assertEqual(response.status_code,200,response.text)
        previous=response.json()
        old=self.confirmed(previous,token=False); self.assertEqual(old.status_code,200,old.text)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))),0)
        listing=self.client.get('/api/bank-statements/reprocess/batches',headers=self.h)
        self.assertEqual(listing.status_code,200,listing.text)
        self.assertIn(previous['id'],[x['id'] for x in listing.json()['items']])
        url=f"/api/bank-statements/{previous['id']}/reprocess/preview"
        p=self.client.post(url,json={'fee_category_id':self.cat},headers=self.h)
        self.assertEqual(p.status_code,200,p.text)
        apply=self.client.post(f"/api/bank-statements/{previous['id']}/reprocess/commit",headers=self.h,
            json={'fee_category_id':self.cat,'preview_token':p.json()['preview_token'],'confirm':True})
        self.assertEqual(apply.status_code,200,apply.text); self.assertEqual(apply.json()['posted'],2)
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))),2)
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),100018000)
        again=self.client.post(f"/api/bank-statements/{previous['id']}/reprocess/commit",headers=self.h,
            json={'fee_category_id':self.cat,'preview_token':p.json()['preview_token'],'confirm':True})
        self.assertEqual(again.status_code,409,again.text)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))),2)

    def test_previously_reviewed_non_swift_fee_can_be_reprocessed(self):
        raw=self.statement(False).replace('00011 Погаш. ком. SWIFT - synthetic'.encode('cp1251'),
                                          b'BC synthetic non-SWIFT bank fee')
        p=self.preview(raw); self.assertEqual(p.status_code,200,p.text)
        statement=p.json(); self.assertEqual(self.confirmed(statement,token=False).status_code,200)
        fee=next(row for row in statement['rows'] if row['direction']=='out')
        response=self.accountant[0].post(f"/api/bank-statements/lines/{fee['id']}/review",headers=self.accountant[1],
            json={'classification':'bank_fee','note':'Synthetic accountant checked this fee'})
        self.assertEqual(response.status_code,200,response.text); self.assertEqual(response.json()['status'],'reviewed')
        check=self.client.post(f"/api/bank-statements/{statement['id']}/reprocess/preview",headers=self.h,
            json={'fee_category_id':self.cat})
        self.assertEqual(check.status_code,200,check.text)
        self.assertEqual(next(row for row in check.json()['rows'] if row['id']==fee['id'])['status'],'fee_candidate')
        applied=self.client.post(f"/api/bank-statements/{statement['id']}/reprocess/commit",headers=self.h,
            json={'fee_category_id':self.cat,'preview_token':check.json()['preview_token'],'confirm':True})
        self.assertEqual(applied.status_code,200,applied.text); self.assertEqual(applied.json()['posted'],2)
        with unit() as s:
            entries=list(s.scalars(select(Ledger))); self.assertEqual(len(entries),2)
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),100018000)

    def test_stale_preview_token_aborts_before_any_financial_writes(self):
        response=self.preview(self.statement(False),self.cat); self.assertEqual(response.status_code,200,response.text)
        preview=response.json()
        with unit(True) as s:s.get(Account,self.acc).opening += 1
        result=self.confirmed(preview,self.cat)
        self.assertEqual(result.status_code,409,result.text)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))),0)

    def test_unresolved_outgoing_never_inflates_available_cash(self):
        raw=self.statement(False).replace('00011 Погаш. ком. SWIFT - synthetic'.encode('cp1251'),
                                          b'Synthetic outgoing awaiting verification')
        response=self.preview(raw,self.cat); self.assertEqual(response.status_code,200,response.text)
        result=self.confirmed(response.json(),self.cat)
        self.assertIn(result.status_code,(200,409),result.text)
        with unit() as s:
            entries=list(s.scalars(select(Ledger)))
            self.assertTrue(any(entry.kind=='in' for entry in entries))
            # Verified credits post, while the unresolved debit is held back
            # from spendable funds rather than silently treated as income.
            state=funds_state(s,s.get(Account,self.acc),date.fromisoformat(self.date))
            self.assertEqual(state['bank_pending_debits'],2000)
            self.assertLessEqual(state['available'],100018000)

    def test_fee_rejected_by_budget_keeps_credit_but_holds_unposted_debit(self):
        self.budget('0',mode='hard')
        response=self.preview(self.statement(False),self.cat); self.assertEqual(response.status_code,200,response.text)
        result=self.confirmed(response.json(),self.cat)
        self.assertIn(result.status_code,(200,409),result.text)
        with unit() as s:
            entries=list(s.scalars(select(Ledger)))
            self.assertTrue(any(entry.kind=='in' for entry in entries))
            self.assertFalse(any(entry.kind=='out' for entry in entries))
            state=funds_state(s,s.get(Account,self.acc),date.fromisoformat(self.date))
            self.assertEqual(state['bank_pending_debits'],2000)
            self.assertLessEqual(state['available'],100018000)

    def test_unreconciled_closing_balance_rolls_back_the_new_batch(self):
        # An unrelated same-day expense isn't represented by the bank file.
        # Its date leaves the opening balance unchanged, but would make the
        # post-import closing differ from the bank by 50.00.
        existing=self.ledger('50','out',reference='SYN-OTHER')
        self.assertEqual(existing.status_code,200,existing.text)
        response=self.preview(self.statement(False),self.cat); self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['opening_matches'])
        result=self.confirmed(response.json(),self.cat)
        self.assertIn(result.status_code,(200,409),result.text)
        with unit() as s:
            entries=list(s.scalars(select(Ledger)))
            self.assertEqual(len(entries),1)
            self.assertEqual(entries[0].reference,'SYN-OTHER')
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),99995000)

    def test_exact_approved_payment_posts_once_when_another_debit_needs_review(self):
        rid=self.approved()
        # The other debit has the company INN and may be an internal transfer.
        raw=self.statement(external=True).replace(b'<td>300000005</td>',b'<td>300000001</td>')
        response=self.preview(raw,self.cat);self.assertEqual(response.status_code,200,response.text)
        p=response.json();self.assertFalse(p['full_reconciliation'])
        self.assertIn('auto_pay_candidate',[r['status'] for r in p['rows']])
        result=self.confirmed(p,self.cat);self.assertEqual(result.status_code,200,result.text)
        self.assertFalse(result.json()['full_reconciliation'])
        self.assertEqual((result.json()['auto_paid'],result.json()['auto_income'],result.json()['auto_fee']),(1,1,1))
        with unit() as s:
            entries=list(s.scalars(select(Ledger)))
            self.assertEqual(len(entries),3)
            payment=next(entry for entry in entries if entry.request_id==rid)
            self.assertEqual((payment.kind,payment.amount),('out',60000))
            self.assertEqual(s.get(PaymentRequest,rid).status,'paid')
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),99958000)
            self.assertEqual(funds_state(s,s.get(Account,self.acc),date.fromisoformat(self.date))['available'],99950500)
        self.assertEqual(self.confirmed(p,self.cat).status_code,409)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))),3)

    def test_closing_mismatch_cannot_leak_an_exact_approved_payment(self):
        rid=self.approved()
        # An extra existing expense is not represented in this statement:
        # candidate checks pass, but the closing reconciliation must fail.
        existing=self.ledger('50','out',reference='SYN-OTHER')
        self.assertEqual(existing.status_code,200,existing.text)
        response=self.preview(self.statement(),self.cat);self.assertEqual(response.status_code,200,response.text)
        p=response.json();self.assertFalse(p['full_reconciliation'])
        result=self.confirmed(p,self.cat);self.assertEqual(result.status_code,200,result.text)
        self.assertFalse(result.json()['full_reconciliation'])
        self.assertEqual((result.json()['auto_paid'],result.json()['auto_income'],result.json()['auto_fee']),(0,0,0))
        with unit() as s:
            entries=list(s.scalars(select(Ledger)))
            self.assertEqual(len(entries),1)
            payments=[entry for entry in entries if entry.request_id==rid]
            self.assertEqual(payments,[])
            self.assertEqual(s.get(PaymentRequest,rid).status,'approved')
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),99995000)
        self.assertEqual(self.confirmed(p,self.cat).status_code,409)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))),1)

    def test_accountant_reconciles_incoming_funded_request_without_general_write(self):
        """A bank payer can post one fully reconciled statement, never generic entries."""
        rid=self.approved()
        with unit(True) as s:s.get(Account,self.acc).opening=50000
        raw=self.statement().replace(b'1000000.00',b'500.00').replace(b'999580.00',b'80.00')
        finance,fh=self.make_user('finance','bank_split_finance')
        accountant,ah=self.accountant
        try:
            fin=self.preview(raw,self.cat,client=finance,headers=fh)
            self.assertEqual(fin.status_code,200,fin.text)
            self.assertFalse(fin.json()['full_reconciliation'])
            self.assertNotIn('auto_pay_candidate',[r['status'] for r in fin.json()['rows']])
            book=self.preview(raw,self.cat,client=accountant,headers=ah)
            self.assertEqual(book.status_code,200,book.text)
            self.assertTrue(book.json()['full_reconciliation'])
            self.assertIn('auto_pay_candidate',[r['status'] for r in book.json()['rows']])
            result=self.confirmed(book.json(),self.cat,client=accountant,headers=ah)
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual((result.json()['auto_paid'],result.json()['auto_income'],result.json()['auto_fee']),(1,1,1))
            with unit() as s:
                self.assertEqual(s.get(PaymentRequest,rid).status,'paid')
                self.assertEqual(len(list(s.scalars(select(Ledger)))),3)
                self.assertEqual(account_balance(s,s.get(Account,self.acc)),8000)
            self.assertEqual(self.confirmed(book.json(),self.cat,client=accountant,headers=ah).status_code,409)
        finally:
            finance.close()

    def test_partial_statement_blocks_manual_payments_until_each_statement_is_reconciled(self):
        rid=self.approved()
        raw=self.statement(False)
        p=self.preview(raw);self.assertEqual(p.status_code,200,p.text)
        # Model an older client that confirmed without the signed posting flow.
        self.assertTrue(p.json()['full_reconciliation'])
        committed=self.confirmed(p.json(),token=False);self.assertEqual(committed.status_code,200,committed.text)
        self.assertFalse(committed.json()['full_reconciliation'])
        batch_id=p.json()['id']
        blocked=self.ledger('600','out',reference='SYN-BLOCKED',request_id=rid)
        self.assertEqual(blocked.status_code,409,blocked.text)
        self.assertIn('сверк',blocked.text.casefold())
        payload={'account_id':self.acc,'category_id':self.cat,'amount':'600','kind':'out','date':self.date,
                 'reference':'SYN-BYPASS','note':'Synthetic payment','request_id':rid,
                 'request_version':self.version_of(rid),'bank_statement_fact':True}
        rejected=self.accountant[0].post('/api/ledger',headers=self.accountant[1],json=payload)
        self.assertEqual(rejected.status_code,422,rejected.text)
        with unit(True) as s:
            admin=s.scalar(select(User).where(User.username=='admin'))
            old=BankStatementBatch(company_id=self.company,account_id=self.acc,created_by=admin.id,
                filename='synthetic-empty.xls',sha256='e'*64,account_number='20200000000000000001',
                owner_name='Synthetic Company',owner_inn='300000001',currency='UZS',
                period_from=date.fromisoformat(self.date)-timedelta(days=1),
                period_to=date.fromisoformat(self.date)-timedelta(days=1),opening=100000000,closing=100000000,
                debit_total=0,credit_total=0,balance_matches=True,declared_totals_match=True,
                source_ready=True,status='committed',committed_at=now())
            s.add(old);s.flush();other_id=old.id
            set_statement_reconciliation(s,old,False,admin)
        q=self.client.post(f'/api/bank-statements/{batch_id}/reprocess/preview',headers=self.h,
                           json={'fee_category_id':self.cat})
        self.assertEqual(q.status_code,200,q.text);self.assertTrue(q.json()['full_reconciliation'],q.text)
        done=self.client.post(f'/api/bank-statements/{batch_id}/reprocess/commit',headers=self.h,
            json={'fee_category_id':self.cat,'preview_token':q.json()['preview_token'],'confirm':True})
        self.assertEqual(done.status_code,200,done.text);self.assertTrue(done.json()['full_reconciliation'])
        self.assertEqual(self.ledger('600','out',reference='SYN-STILL-BLOCKED',request_id=rid).status_code,409)
        q=self.client.post(f'/api/bank-statements/{other_id}/reprocess/preview',headers=self.h,json={})
        self.assertEqual(q.status_code,200,q.text);self.assertTrue(q.json()['full_reconciliation'],q.text)
        done=self.client.post(f'/api/bank-statements/{other_id}/reprocess/commit',headers=self.h,
            json={'preview_token':q.json()['preview_token'],'confirm':True})
        self.assertEqual(done.status_code,200,done.text);self.assertTrue(done.json()['full_reconciliation'])
        paid=self.ledger('600','out',reference='SYN-AFTER-RECONCILE',request_id=rid)
        self.assertEqual(paid.status_code,200,paid.text)
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest,rid).status,'paid')
            actions=list(s.scalars(select(Audit.action).where(Audit.entity=='bank_statement')))
            self.assertIn('Банковская выписка требует сверки',actions)
            self.assertIn('Сверена банковская выписка',actions)

    def test_statement_payment_remains_allowed_after_prior_partial_statement(self):
        rid=self.approved()
        p=self.preview(self.statement(False))
        self.assertEqual(p.status_code,200,p.text)
        self.assertEqual(self.confirmed(p.json(),token=False).status_code,200)
        self.assertEqual(self.ledger('600','out',reference='SYN-MANUAL-DENY',request_id=rid).status_code,409)
        accountant,headers=self.accountant
        p2=self.preview(self.statement(),client=accountant,headers=headers)
        self.assertEqual(p2.status_code,200,p2.text)
        self.assertIn('auto_pay_candidate',[r['status'] for r in p2.json()['rows']])
        result=self.confirmed(p2.json(),client=accountant,headers=headers)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['auto_paid'],1)
        with unit() as s:
            entries=list(s.scalars(select(Ledger).where(Ledger.request_id==rid)))
            self.assertEqual(len(entries),1)
            self.assertEqual(s.get(PaymentRequest,rid).status,'paid')
            actor=s.scalar(select(User).where(User.username=='bank_synthetic_accountant'))
            audit=s.scalar(select(Audit).where(Audit.action=='Оплата заявки',
                                                Audit.entity=='request',Audit.entity_id==str(rid)))
            self.assertIsNotNone(audit)
            self.assertEqual(audit.user_id,actor.id)

    def test_partial_statement_does_not_block_other_account_or_company(self):
        self.budget('100000')
        p=self.preview(self.statement(False));self.assertEqual(p.status_code,200,p.text)
        self.assertEqual(self.confirmed(p.json()).status_code,200)
        second=self.post('/api/accounts',{'name':'Synthetic separate bank','kind':'bank','currency':'UZS',
            'opening':'1000000','opening_date':str(date.fromisoformat(self.date)-timedelta(days=1))})
        self.assertEqual(second.status_code,200,second.text)
        second_id=second.json()['id']
        req=self.request('100',client=self.requester[0],headers=self.requester[1],account=second_id)
        self.assertEqual(req.status_code,200,req.text);rid=req.json()['id']
        self.assertEqual(self.approve(rid).status_code,200)
        paid=self.ledger('100','out',reference='SYN-OTHER-ACCOUNT',request_id=rid,account_id=second_id)
        self.assertEqual(paid.status_code,200,paid.text)
        with unit(True) as s:
            admin=s.scalar(select(User).where(User.username=='admin'))
            foreign=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
            account=Account(company_id=foreign,name='Synthetic foreign bank',kind='bank',currency='UZS',
                opening=100000000,opening_date=date.fromisoformat(self.date)-timedelta(days=1),created_by=admin.id)
            category=Category(company_id=foreign,name='Synthetic foreign expense',type='outcome',activity='operating')
            s.add_all([account,category]);s.flush()
            s.add(Budget(category_id=category.id,month=self.month,currency='UZS',amount=100000000,mode='hard',source='Synthetic'))
            foreign_request=PaymentRequest(creator_id=admin.id,category_id=category.id,account_id=account.id,
                counterparty='Synthetic',counterparty_inn='300000002',purpose='Synthetic foreign payment',
                amount=10000,due_date=date.fromisoformat(self.date),status='approved',checked_by=admin.id,
                finance_approved_by=admin.id,approved_at=now(),route_policy='skip')
            s.add(foreign_request);s.flush()
            foreign_id,foreign_cat,foreign_rid=account.id,category.id,foreign_request.id
        foreign_book,fh=self.user_in(foreign,'accountant','synthetic_foreign_book')
        try:
            body={'account_id':foreign_id,'category_id':foreign_cat,'amount':'100','kind':'out','date':self.date,
                  'reference':'SYN-FOREIGN','note':'Synthetic foreign payment','request_id':foreign_rid,
                  'request_version':1}
            foreign_result=foreign_book.post('/api/ledger',headers=fh,json=body)
            self.assertEqual(foreign_result.status_code,200,foreign_result.text)
        finally:
            foreign_book.close()

    def test_external_debit_allocation_obeys_budget_scope_and_does_not_change_cash(self):
        response=self.preview(self.statement(False,external=True),self.cat)
        self.assertEqual(response.status_code,200,response.text); p=response.json()
        self.assertTrue(p['full_reconciliation'])
        self.assertEqual([r['status'] for r in p['rows']], ['income_candidate','fee_candidate','outgoing_candidate'])
        result=self.confirmed(p,self.cat); self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(result.json()['full_reconciliation'])
        self.assertEqual((result.json()['auto_income'],result.json()['auto_fee'],result.json()['auto_outgoing']),(1,1,1))
        listing=self.client.get('/api/bank-statements/outgoing-unallocated',headers=self.h)
        self.assertEqual(listing.status_code,200,listing.text); item=listing.json()['items'][0]
        year=int(self.date[:4]); month=int(self.date[5:7])-1
        report=self.client.get('/api/report',headers=self.h,params={'year':year,'currency':'UZS','company_id':self.company})
        self.assertEqual(report.status_code,200,report.text); before=report.json()['totals'][month]
        self.assertEqual(before,'105.00')
        with unit(True) as s:
            category=Category(company_id=self.company,name='Synthetic hard expense',type='outcome',activity='operating')
            s.add(category); s.flush(); target=category.id
            s.add(Budget(category_id=target,month=self.month,currency='UZS',amount=0,mode='hard',source='Synthetic'))
            foreign=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
            foreign_cat=Category(company_id=foreign,name='Foreign synthetic expense',type='outcome',activity='operating')
            s.add(foreign_cat);s.flush();foreign_id=foreign_cat.id
        url=f"/api/bank-statements/lines/{item['id']}/outgoing-category"
        payload={'category_id':target,'edit_token':item['edit_token'],'reason':'Synthetic accounting allocation'}
        client,headers=self.accountant
        wrongcompany=client.post(url,json=payload,headers={**headers,'X-Company-ID':str(foreign)})
        self.assertIn(wrongcompany.status_code,(403,404,409),wrongcompany.text)
        wrongcat=client.post(url,json={**payload,'category_id':foreign_id},headers=headers)
        self.assertEqual(wrongcat.status_code,422,wrongcat.text)
        self.assertEqual(client.post(url,json={**payload,'edit_token':'0'*64},headers=headers).status_code,409)
        self.assertEqual(client.post(url,json={**payload,'amount':'999999'},headers=headers).status_code,422)
        fee=next(r for r in p['rows'] if r['status']=='fee_candidate')
        self.assertEqual(client.post(f"/api/bank-statements/lines/{fee['id']}/outgoing-category",json=payload,headers=headers).status_code,409)
        blocked=client.post(url,json=payload,headers=headers)
        self.assertEqual(blocked.status_code,409,blocked.text)
        with unit(True) as s:
            b=s.scalar(select(Budget).where(Budget.category_id==target)); b.amount=10000
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),100010500)
        accepted=client.post(url,json=payload,headers=headers)
        self.assertEqual(accepted.status_code,200,accepted.text)
        self.assertFalse(accepted.json()['budget']['over'])
        self.assertEqual(client.post(url,json=payload,headers=headers).status_code,409)
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))),3)
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),100010500)
            budget=budget_state(s,target,self.month,'UZS')
            self.assertEqual((budget['spent'],budget['remaining']),(7500,2500))
        after=self.client.get('/api/report',headers=self.h,params={'year':year,'currency':'UZS','company_id':self.company})
        self.assertEqual(after.status_code,200,after.text)
        self.assertEqual(after.json()['totals'][month],before)
        target_row=next(r for r in after.json()['rows'] if r['category_id']==target and r['kind']=='out')
        self.assertEqual(target_row['values'][month],'-75.00')

    def test_overdue_matching_request_is_not_bypassed_by_unallocated_expense(self):
        with unit(True) as s:
            admin=s.scalar(select(User).where(User.username=='admin'))
            s.add(PaymentRequest(creator_id=admin.id,account_id=self.acc,category_id=self.cat,
                counterparty='Synthetic supplier',counterparty_inn='300000005',purpose='Synthetic overdue',
                amount=7500,due_date=date.fromisoformat(self.date)-timedelta(days=30),status='pending'))
        response=self.preview(self.statement(False,external=True),self.cat)
        self.assertEqual(response.status_code,200,response.text)
        p=response.json(); external=next(r for r in p['rows'] if r['document_number']=='OTH001')
        self.assertEqual(external['status'],'review')
        self.assertFalse(p['full_reconciliation'])
        result=self.confirmed(p,self.cat); self.assertEqual(result.status_code,200,result.text)
        self.assertFalse(result.json()['full_reconciliation'])
        with unit() as s:
            entries=list(s.scalars(select(Ledger)))
            self.assertEqual(len(entries),2)
            self.assertFalse(any(entry.kind=='out' and entry.amount==7500 for entry in entries))

    def test_accountant_posts_statement_but_cannot_change_default_fee(self):
        client,headers=self.accountant
        response=self.preview(self.statement(False),self.cat,client,headers)
        self.assertEqual(response.status_code,200,response.text)
        p=response.json(); self.assertEqual([r['status'] for r in p['rows']],['income_candidate','fee_candidate'])
        result=self.confirmed(p,self.cat,client,headers)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual((result.json()['auto_income'],result.json()['auto_fee']),(1,1))
        settings=self.client.get('/api/bank-statements/settings',headers=self.h)
        self.assertEqual(settings.status_code,200,settings.text)
        self.assertIsNone(settings.json()['fee_category_id'])
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))),2)

    def test_accountant_statement_cannot_forge_general_ledger_capability(self):
        client,headers=self.accountant
        body={'account_id':self.acc,'category_id':self.cat,'amount':'20','kind':'out','date':self.date,
              'reference':'SYN-FAKE','note':'Synthetic arbitrary operation'}
        for kind in ('in','out','transfer'):
            response=client.post('/api/ledger',headers=headers,json={**body,'kind':kind})
            self.assertIn(response.status_code,(403,422),response.text)
        for field in ('bank_statement_fact','bank_statement_import'):
            response=client.post('/api/ledger',headers=headers,json={**body,field:True})
            self.assertEqual(response.status_code,422,response.text)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))),0)

    def test_accountant_posts_external_debit_and_not_mismatched_statement(self):
        client,headers=self.accountant
        raw=self.statement(False,external=True)
        p=self.preview(raw,self.cat,client,headers);self.assertEqual(p.status_code,200,p.text)
        self.assertTrue(p.json()['full_reconciliation'])
        forged=self.confirmed({**p.json(),'preview_token':'0'*64},self.cat,client,headers)
        self.assertEqual(forged.status_code,409,forged.text)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))),0)
        done=self.confirmed(p.json(),self.cat,client,headers);self.assertEqual(done.status_code,200,done.text)
        self.assertEqual((done.json()['auto_income'],done.json()['auto_fee'],done.json()['auto_outgoing']),(1,1,1))
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))),3)
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),100010500)

    def test_accountant_posts_verified_credit_and_holds_unresolved_transfer(self):
        client,headers=self.accountant
        raw=self.statement(False,external=True).replace(b'<td>300000005</td>',b'<td>300000001</td>')
        preview=self.preview(raw,self.cat,client,headers)
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertFalse(preview.json()['full_reconciliation'])
        done=self.confirmed(preview.json(),self.cat,client,headers)
        self.assertEqual(done.status_code,200,done.text)
        self.assertFalse(done.json()['full_reconciliation'])
        with unit() as s:
            entries=list(s.scalars(select(Ledger)))
            self.assertTrue(any(entry.kind=='in' and entry.amount==20000 for entry in entries))
            self.assertFalse(any(entry.kind=='out' and entry.amount==7500 for entry in entries))
            self.assertLessEqual(funds_state(s,s.get(Account,self.acc),date.fromisoformat(self.date))['available'],100010500)
            self.assertTrue(unreconciled_statements(s,s.get(Account,self.acc)))

    def test_accountant_fee_choice_does_not_replace_company_setting(self):
        client,headers=self.accountant
        with unit(True) as s:
            alt=Category(company_id=self.company,name='Synthetic alternate fee',type='outcome',activity='operating')
            foreign=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
            other=Category(company_id=foreign,name='Synthetic other fee',type='outcome',activity='operating')
            retired=Category(company_id=self.company,name='Synthetic retired fee',type='outcome',activity='operating')
            s.add_all([alt,other,retired]);s.flush(); alt_id,other_id,retired_id=alt.id,other.id,retired.id
            s.merge(Setting(key=f'company:{self.company}:bank_statement_fee_category',value=str(self.cat)))
            s.merge(Setting(key=f'company:{self.company}:category_catalog',value=json.dumps({'retired':[retired_id]})))
        for invalid in (other_id,retired_id):
            response=self.preview(self.statement(False),invalid,client,headers)
            self.assertEqual(response.status_code,422,response.text)
        response=self.preview(self.statement(False),alt_id,client,headers)
        self.assertEqual(response.status_code,200,response.text)
        changed=self.confirmed(response.json(),self.cat,client,headers)
        self.assertEqual(changed.status_code,409,changed.text)
        done=self.confirmed(response.json(),alt_id,client,headers)
        self.assertEqual(done.status_code,200,done.text)
        with unit() as s:
            entry=next(e for e in s.scalars(select(Ledger)) if e.kind=='out')
            self.assertEqual(entry.category_id,alt_id)
        settings=self.client.get('/api/bank-statements/settings',headers=self.h)
        self.assertEqual(settings.status_code,200,settings.text)
        self.assertEqual(settings.json()['fee_category_id'],self.cat)

    def test_changed_approved_request_version_invalidates_first_preview(self):
        rid=self.approved(); client,headers=self.accountant
        preview=self.preview(self.statement(),self.cat,client,headers)
        self.assertEqual(preview.status_code,200,preview.text)
        match=next(r for r in preview.json()['rows'] if r['status']=='auto_pay_candidate')
        self.assertIsNotNone(match.get('request_version'))
        with unit(True) as s:s.get(PaymentRequest,rid).version+=1
        result=self.confirmed(preview.json(),self.cat,client,headers)
        self.assertEqual(result.status_code,409,result.text)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))),0)

    def test_accountant_needs_editable_section_and_company_access(self):
        client,headers=self.accountant
        with unit(True) as s:
            user=s.scalar(select(User).where(User.username=='bank_synthetic_accountant'))
            foreign=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
            uid=user.id
        for mode in ('view','hidden'):
            with unit(True) as s:
                s.merge(Setting(key=f'sections:{self.company}:{uid}',value=json.dumps({'version':1,'sections':{'ledger':mode}})))
            response=self.preview(self.statement(False),self.cat,client,headers)
            self.assertEqual(response.status_code,403,response.text)
            listing=client.get('/api/bank-statements/reprocess/batches',headers=headers)
            if mode=='hidden':self.assertEqual(listing.status_code,403,listing.text)
        with unit(True) as s:s.delete(s.get(Setting,f'sections:{self.company}:{uid}'))
        foreign_result=self.preview(self.statement(False),self.cat,client,{**headers,'X-Company-ID':str(foreign)})
        self.assertIn(foreign_result.status_code,(403,404,409),foreign_result.text)

    def test_accountant_cannot_commit_reprocess_after_ledger_access_is_removed(self):
        client,headers=self.accountant
        first=self.preview(self.statement(False),None,client,headers)
        self.assertEqual(first.status_code,200,first.text)
        self.assertTrue(first.json()['full_reconciliation'])
        batch_id=first.json()['id']
        result=self.confirmed(first.json(),None,client,headers,token=False)
        self.assertEqual(result.status_code,200,result.text)
        with unit() as s:
            self.assertTrue(unreconciled_statements(s,s.get(Account,self.acc)))
            uid=s.scalar(select(User.id).where(User.username=='bank_synthetic_accountant'))
        for mode in ('view','hidden'):
            with unit(True) as s:
                old=s.get(Setting,f'sections:{self.company}:{uid}')
                if old:s.delete(old)
            preview=client.post(f'/api/bank-statements/{batch_id}/reprocess/preview',headers=headers,
                                json={'fee_category_id':self.cat})
            self.assertEqual(preview.status_code,200,preview.text)
            self.assertTrue(preview.json()['full_reconciliation'])
            with unit(True) as s:
                s.merge(Setting(key=f'sections:{self.company}:{uid}',value=json.dumps({'version':1,'sections':{'ledger':mode}})))
            attempted=client.post(f'/api/bank-statements/{batch_id}/reprocess/commit',headers=headers,
                                  json={'fee_category_id':self.cat,'preview_token':preview.json()['preview_token'],'confirm':True})
            self.assertEqual(attempted.status_code,403,attempted.text)
            with unit() as s:
                self.assertIsNone(s.scalar(select(Ledger.id)))
                self.assertTrue(unreconciled_statements(s,s.get(Account,self.acc)))

    def test_accountant_reprocesses_verified_statement_once(self):
        client,headers=self.accountant
        preview=self.preview(self.statement(False),None,client,headers)
        self.assertEqual(preview.status_code,200,preview.text)
        batch_id=preview.json()['id']
        self.assertEqual(self.confirmed(preview.json(),None,client,headers,token=False).status_code,200)
        listing=client.get('/api/bank-statements/reprocess/batches',headers=headers)
        self.assertEqual(listing.status_code,200,listing.text)
        self.assertIn(batch_id,[row['id'] for row in listing.json()['items']])
        p=client.post(f'/api/bank-statements/{batch_id}/reprocess/preview',headers=headers,
                      json={'fee_category_id':self.cat})
        self.assertEqual(p.status_code,200,p.text)
        self.assertTrue(p.json()['full_reconciliation'])
        payload={'fee_category_id':self.cat,'preview_token':p.json()['preview_token'],'confirm':True}
        done=client.post(f'/api/bank-statements/{batch_id}/reprocess/commit',headers=headers,json=payload)
        self.assertEqual(done.status_code,200,done.text)
        self.assertEqual(done.json()['posted'],2)
        self.assertEqual(client.post(f'/api/bank-statements/{batch_id}/reprocess/commit',headers=headers,json=payload).status_code,409)
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))),2)
            self.assertFalse(unreconciled_statements(s,s.get(Account,self.acc)))

    def test_category_change_invalidates_preview(self):
        for changed_field in ('name', 'activity'):
            response=self.preview(self.statement(False),self.cat); self.assertEqual(response.status_code,200,response.text)
            p=response.json()
            with unit(True) as s:
                category=s.get(Category,self.cat)
                previous=getattr(category,changed_field)
                setattr(category,changed_field,'Synthetic renamed bank fees' if changed_field=='name' else 'financing')
            result=self.confirmed(p,self.cat)
            self.assertEqual(result.status_code,409,result.text)
            with unit(True) as s:
                self.assertEqual(len(list(s.scalars(select(Ledger)))),0)
                setattr(s.get(Category,self.cat),changed_field,previous)

    def test_same_file_reuses_preview_batch(self):
        raw=self.statement(False)
        first=self.preview(raw,self.cat); second=self.preview(raw,self.cat)
        self.assertEqual(first.status_code,200,first.text); self.assertEqual(second.status_code,200,second.text)
        self.assertEqual(first.json()['id'],second.json()['id'])
        with unit() as s:
            previews=list(s.scalars(select(BankStatementBatch).where(BankStatementBatch.status=='preview')))
            self.assertEqual(len(previews),1)

    def test_non_ascii_preview_token_is_rejected_cleanly(self):
        response=self.preview(self.statement(False),self.cat); self.assertEqual(response.status_code,200,response.text)
        p=response.json()
        result=self.client.post(f"/api/bank-statements/{p['id']}/commit",headers=self.h,
            json={'confirm_account':True,'confirm_currency':True,'fee_category_id':self.cat,'preview_token':'é'*64})
        self.assertEqual(result.status_code,422,result.text)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))),0)

    def test_company_scope_and_income_allocation_require_rights_and_version(self):
        response=self.preview(self.statement(False),self.cat); self.assertEqual(response.status_code,200,response.text)
        p=response.json(); result=self.confirmed(p,self.cat)
        self.assertEqual(result.status_code,200,result.text); self.assertEqual(result.json()['auto_income'],1)
        listing=self.client.get('/api/bank-statements/income-unallocated',headers=self.h)
        self.assertEqual(listing.status_code,200,listing.text)
        item=listing.json()['items'][0]; income=self.category_id('Поступления от покупателей')
        payload={'category_id':income,'edit_token':item['edit_token'],'reason':'Synthetic accounting classification'}
        url=f"/api/bank-statements/lines/{item['id']}/income-category"
        with unit() as s:other=s.scalar(select(Company.id).where(Company.code=='ZUMA'))
        foreign=self.accountant[0].post(url,json=payload,headers={**self.accountant[1],'X-Company-ID':str(other)})
        self.assertIn(foreign.status_code,(403,404,409),foreign.text)
        foreign_admin=self.client.post(url,json=payload,headers={**self.h,'X-Company-ID':str(other)})
        self.assertIn(foreign_admin.status_code,(403,404,409),foreign_admin.text)
        stale=self.accountant[0].post(url,json={**payload,'edit_token':'0'*64},headers=self.accountant[1])
        self.assertEqual(stale.status_code,409,stale.text)
        amount_change=self.accountant[0].post(url,json={**payload,'amount':'999999'},headers=self.accountant[1])
        self.assertEqual(amount_change.status_code,422,amount_change.text)
        fee_row=next(row for row in p['rows'] if row['direction']=='out')
        wrong_line=self.accountant[0].post(f"/api/bank-statements/lines/{fee_row['id']}/income-category",json=payload,
                                          headers=self.accountant[1])
        self.assertEqual(wrong_line.status_code,409,wrong_line.text)
        with unit(True) as s:
            foreign_category=Category(company_id=other,name='Foreign synthetic income',type='income')
            s.add(foreign_category); s.flush(); foreign_id=foreign_category.id
        wrong_category=self.accountant[0].post(url,json={**payload,'category_id':foreign_id},headers=self.accountant[1])
        self.assertEqual(wrong_category.status_code,422,wrong_category.text)
        accepted=self.accountant[0].post(url,json=payload,headers=self.accountant[1])
        self.assertEqual(accepted.status_code,200,accepted.text)
        self.assertEqual(self.accountant[0].post(url,json=payload,headers=self.accountant[1]).status_code,409)
        with unit() as s:
            entries=list(s.scalars(select(Ledger))); self.assertEqual(len(entries),2)
            received=next(x for x in entries if x.kind=='in')
            self.assertEqual(received.category_id,income)
            self.assertEqual(account_balance(s,s.get(Account,self.acc)),100018000)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(AutoBankIntegrationTests(name) for name in AutoBankIntegrationTests.__dict__
                              if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
