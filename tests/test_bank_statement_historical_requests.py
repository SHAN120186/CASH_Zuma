"""Historical closed-request exceptions use synthetic, isolated bank facts only."""
import unittest
import json
from datetime import date
from sqlalchemy import select

import tests.test_site as base
from app.main import app, request_state
from app.bank_statement_historical_requests import router, HISTORICAL_ACTION
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.bank_statement_guard import set_statement_reconciliation
from app.db import Account, Audit, Category, Company, Ledger, PaymentRequest, RequestDocument, Setting, User, engine, unit
from app.services import account_balance, log

if not any(getattr(route, 'path', '') == '/api/bank-statements/lines/{id}/historical-request/options' for route in app.routes):
    app.include_router(router)


class HistoricalRequestTests(base.SiteTests):
    def prepared(self):
        created = self.post('/api/requests', {**self.request_body('100'), 'counterparty_inn': '300000002'})
        self.assertEqual(created.status_code, 200, created.text)
        self.rid = created.json()['id']
        with unit(True) as s:
            s.info['company_id'] = self.company
            account = s.get(Account, self.acc)
            user = s.scalar(select(User).where(User.username == 'admin'))
            # Model a retained legacy closure. The current close endpoint
            # permanently deletes unpaid requests and cannot create this history.
            req = s.get(PaymentRequest, self.rid)
            before = request_state(req)
            req.status = 'cancelled'
            req.decision_note = 'Synthetic legacy closed without payment'
            s.flush()
            log(s, user, 'Действие по заявке: close', 'request', req.id,
                json.dumps({'before': before, 'after': request_state(req),
                            'reason': req.decision_note}, ensure_ascii=False))
            day = date.fromisoformat(self.date)
            batch = BankStatementBatch(company_id=self.company, account_id=self.acc, created_by=user.id,
                filename='synthetic-historical.xls', sha256='a'*64, account_number='20200000000000000001',
                owner_name='Synthetic company', owner_inn='300000001', currency='UZS', period_from=day, period_to=day,
                opening=account.opening, closing=account.opening-10000, debit_total=10000, credit_total=0,
                balance_matches=True, declared_totals_match=True, source_ready=True, status='committed')
            s.add(batch); s.flush()
            line = BankStatementLine(company_id=self.company, batch_id=batch.id, account_id=self.acc, line=6,
                date=day, direction='out', amount=10000, document_number='SYN-HISTORY', correspondent_mfo='00014',
                correspondent_account='20200000000000000002', correspondent_name='Supplier', correspondent_inn='300000002',
                purpose=f'Synthetic bank fact CF-{self.rid:05d}', fingerprint='b'*64, occurrence=1,
                status='review', reason='Closed request needs explicit bank fact', include_in_daily=True, uncertain=False)
            s.add(line); s.flush(); self.lid, self.bid = line.id, batch.id
            s.info['company_id'] = self.company
            set_statement_reconciliation(s, batch, False, user)
        return {'request_id': self.rid, 'reason': 'Synthetic bank evidence checked independently', 'attest_bank_fact': True}

    def url(self, action):
        return f'/api/bank-statements/lines/{self.lid}/historical-request/{action}'

    def preview_fact(self, body):
        result = self.post(self.url('preview'), body)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_fact_pays_closed_request_without_fabricated_approval_and_reversal_restores_closed(self):
        body = self.prepared()
        before = self.row(self.rid)
        options = self.client.get(self.url('options'), headers=self.h)
        self.assertEqual(options.status_code, 200, options.text)
        self.assertEqual(options.json()['items'][0]['request_id'], self.rid)
        shown = self.preview_fact(body)
        with unit() as s:
            self.assertIsNone(s.scalar(select(Ledger.id)))
            self.assertEqual(s.get(PaymentRequest, self.rid).status, 'cancelled')
        committed = self.post(self.url('commit'), {**body, 'token': shown['token'], 'confirm': True})
        self.assertEqual(committed.status_code, 200, committed.text)
        self.assertEqual(committed.json()['status'], 'historical_request_paid')
        after = self.row(self.rid)
        self.assertEqual(after['status'], 'paid')
        for key in ('approved_by', 'finance_approved_by', 'checked_by', 'date'):
            self.assertEqual(after[key], before[key])
        with unit() as s:
            entry = s.get(Ledger, committed.json()['ledger_id'])
            req = s.get(PaymentRequest, self.rid)
            self.assertEqual(entry.date, date.fromisoformat(self.date))
            self.assertIsNone(req.approved_at)
            self.assertIsNone(req.route_at)
            self.assertTrue(s.scalar(select(Audit.id).where(Audit.action == HISTORICAL_ACTION, Audit.entity == 'ledger')))
        self.assertEqual(self.post(self.url('commit'), {**body, 'token': shown['token'], 'confirm': True}).status_code, 409)
        # Linked-payment reference metadata may be corrected later. Its immutable
        # ledger/request/company identity still restores the actual old status.
        with unit(True) as s:
            s.get(Ledger, committed.json()['ledger_id']).reference = 'SYN-CORRECTED-HISTORICAL-REF'
        reversed_result = self.post(f"/api/ledger/{committed.json()['ledger_id']}/reverse", {'reason': 'Synthetic historical fact reversal'})
        self.assertEqual(reversed_result.status_code, 200, reversed_result.text)
        self.assertEqual(self.row(self.rid)['status'], 'cancelled')

    def test_statement_delete_restores_original_closed_state(self):
        body = self.prepared(); shown = self.preview_fact(body)
        committed = self.post(self.url('commit'), {**body, 'token': shown['token'], 'confirm': True})
        self.assertEqual(committed.status_code, 200, committed.text)
        preview = self.client.get(f'/api/bank-statements/{self.bid}/delete-preview', headers=self.h)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()['operation_count'], 1)
        removed = self.post(f'/api/bank-statements/{self.bid}/delete', {'confirm': True,
            'expected': preview.json()['delete_token'], 'reason': 'Synthetic statement exception reversal'})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(self.row(self.rid)['status'], 'cancelled')
        with unit() as s:
            self.assertEqual(account_balance(s, s.get(Account, self.acc)), 100000000)

    def test_admin_attestation_stale_and_company_guards(self):
        body = self.prepared()
        finance = self.make_user('finance', 'synthetic_exception_finance')
        self.addCleanup(finance[0].close)
        self.assertEqual(finance[0].get(self.url('options'), headers=finance[1]).status_code, 403)
        self.assertEqual(self.post(self.url('preview'), {**body, 'attest_bank_fact': False}).status_code, 422)
        shown = self.preview_fact(body)
        self.assertEqual(self.post(self.url('commit'), {**body, 'token': shown['token'], 'confirm': False}).status_code, 422)
        with unit(True) as s:
            s.get(PaymentRequest, self.rid).purpose = 'Synthetic correction after signed preview'
        self.assertEqual(self.post(self.url('commit'), {**body, 'token': shown['token'], 'confirm': True}).status_code, 409)
        with unit() as s:
            foreign = s.scalar(select(Company.id).where(Company.code == 'ZUMA'))
        self.assertEqual(self.client.get(self.url('options'), headers={**self.h, 'X-Company-ID': str(foreign)}).status_code, 404)
        with unit() as s:
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_exact_duplicate_and_existing_or_reversed_journal_block_exception(self):
        body = self.prepared()
        with unit(True) as s:
            line = s.get(BankStatementLine, self.lid)
            other = BankStatementLine(**{column.key: getattr(line, column.key) for column in line.__table__.columns if column.key != 'id'})
            other.line = 7; s.add(other); s.flush(); duplicate_id = other.id
        self.assertEqual(self.post(self.url('preview'), body).status_code, 409)

        with unit(True) as s:
            s.delete(s.get(BankStatementLine, duplicate_id))
            account = s.get(Account, self.acc)
            old = Ledger(account_id=self.acc, category_id=self.cat, kind='out', amount=10000,
                date=date.fromisoformat(self.date), counterparty='Supplier', reference='BANK-'+'b'*64,
                note='Synthetic old reversed bank fact', creator_id=account.created_by)
            s.add(old); s.flush()
            s.add(Ledger(account_id=self.acc, category_id=self.cat, kind='in', amount=10000,
                date=old.date, counterparty=old.counterparty, reference='SYN-HISTORY-REV', reversal_of=old.id,
                note='Synthetic reversal', creator_id=old.creator_id))
        self.assertEqual(self.post(self.url('preview'), body).status_code, 409)

    def test_retired_historical_article_is_preserved_and_appears_in_cashflow_fact(self):
        body = self.prepared()
        with unit(True) as s:
            historical = s.get(Category, self.cat)
            historical.name = 'Synthetic historical laboratory'; historical.code = None
            replacement = Category(company_id=self.company, name='Synthetic current laboratory QA', code='SYN-35', type='outcome')
            s.add(replacement); s.flush(); replacement_id = replacement.id
            key = f'company:{self.company}:category_catalog'
            value = json.dumps({'order': [replacement_id], 'retired': [self.cat]})
            setting = s.get(Setting, key)
            if setting is None:
                s.add(Setting(key=key, value=value))
            else:
                setting.value = value
        options = self.client.get(self.url('options'), headers=self.h)
        self.assertEqual(options.status_code, 200, options.text)
        self.assertEqual([item['request_id'] for item in options.json()['items']], [self.rid])
        shown = self.preview_fact(body)
        self.assertEqual(shown['category_name'], 'Synthetic historical laboratory')
        committed = self.post(self.url('commit'), {**body, 'token': shown['token'], 'confirm': True})
        self.assertEqual(committed.status_code, 200, committed.text)
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest, self.rid).category_id, self.cat)
            self.assertEqual(s.get(Ledger, committed.json()['ledger_id']).category_id, self.cat)
            self.assertNotEqual(s.get(Ledger, committed.json()['ledger_id']).category_id, replacement_id)
        report = self.client.get('/api/report', headers=self.h, params={
            'company_id': self.company, 'currency': 'UZS', 'year': int(self.date[:4]), 'as_of': self.date})
        self.assertEqual(report.status_code, 200, report.text)
        row = next(row for row in report.json()['rows'] if row['category_id'] == self.cat and row['kind'] == 'out')
        self.assertEqual(row['values'][int(self.date[5:7])-1], '-100.00')

    def test_options_exposes_the_real_guard_for_a_matching_closed_request(self):
        self.prepared()
        with unit(True) as s:
            account = s.get(Account, self.acc)
            s.add(Ledger(account_id=self.acc, category_id=self.cat, kind='out', amount=10000,
                date=date.fromisoformat(self.date), counterparty='Supplier', reference='SYN-EXISTING-CLOSED-PAYMENT',
                request_id=self.rid, note='Synthetic previously recorded payment', creator_id=account.created_by))
        options = self.client.get(self.url('options'), headers=self.h)
        self.assertEqual(options.status_code, 200, options.text)
        self.assertEqual(options.json()['items'], [])
        self.assertEqual(options.json()['reason'], 'У заявки уже есть операция оплаты. Проверьте журнал.')

    def test_nonempty_mismatched_inn_is_reported_and_cannot_be_overridden_by_attestation(self):
        body = self.prepared()
        with unit(True) as s:
            s.get(PaymentRequest, self.rid).counterparty_inn = '300000099'
        options = self.client.get(self.url('options'), headers=self.h)
        self.assertEqual(options.status_code, 200, options.text)
        self.assertEqual(options.json()['items'], [])
        self.assertEqual(options.json()['reason'], 'ИНН закрытой заявки не совпадает с ИНН банковской строки. Проверьте контрагента.')
        self.assertEqual(self.post(self.url('preview'), {**body, 'attest_counterparty': True}).status_code, 409)
        with unit() as s:
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_legacy_empty_inn_exact_legal_name_needs_extra_attestation_and_preserves_empty_inn(self):
        body = self.prepared()
        with unit(True) as s:
            req = s.get(PaymentRequest, self.rid)
            req.counterparty_inn = ''; req.counterparty = 'ООО «Synthetic supplier»,'
            s.get(BankStatementLine, self.lid).correspondent_name = 'ООО "Synthetic supplier"'
        options = self.client.get(self.url('options'), headers=self.h)
        self.assertEqual(options.status_code, 200, options.text)
        item = options.json()['items'][0]
        self.assertTrue(item['requires_counterparty_confirmation'])
        self.assertEqual((item['counterparty_inn'], item['bank_inn']), ('', '300000002'))
        self.assertEqual(item['bank_counterparty'], 'ООО "Synthetic supplier"')
        self.assertEqual(self.post(self.url('preview'), body).status_code, 422)
        self.assertEqual(self.post(self.url('preview'), {**body, 'attest_counterparty': 'true'}).status_code, 422)
        body['attest_counterparty'] = True
        shown = self.preview_fact(body)
        self.assertTrue(shown['requires_counterparty_confirmation'])
        self.assertEqual(self.post(self.url('commit'), {**body, 'attest_counterparty': False,
            'token': shown['token'], 'confirm': True}).status_code, 422)
        committed = self.post(self.url('commit'), {**body, 'token': shown['token'], 'confirm': True})
        self.assertEqual(committed.status_code, 200, committed.text)
        with unit() as s:
            req = s.get(PaymentRequest, self.rid)
            self.assertEqual((req.status, req.counterparty_inn), ('paid', ''))
            audit = s.scalar(select(Audit).where(Audit.entity == 'ledger', Audit.entity_id == str(committed.json()['ledger_id']),
                Audit.action == HISTORICAL_ACTION))
            detail = json.loads(audit.detail)
            self.assertEqual(detail['request_before']['counterparty_inn'], '')
            self.assertEqual(detail['bank_inn'], '300000002')
            self.assertTrue(detail['attest_counterparty'])

    def test_empty_inn_different_or_empty_legal_name_does_not_allow_name_guessing(self):
        body = self.prepared()
        for name in ('Supplier extra', 'ООО Another supplier', '«»,'):
            with unit(True) as s:
                req = s.get(PaymentRequest, self.rid)
                req.counterparty_inn = ''; req.counterparty = name
            options = self.client.get(self.url('options'), headers=self.h)
            self.assertEqual(options.status_code, 200, options.text)
            self.assertEqual(options.json()['items'], [])
            self.assertIn('точное совпадение названия', options.json()['reason'])
            self.assertEqual(self.post(self.url('preview'), {**body, 'attest_counterparty': True}).status_code, 409)
        with unit() as s:
            self.assertIsNone(s.scalar(select(Ledger.id)))

    def test_request_document_version_change_invalidates_signed_preview(self):
        body = self.prepared()
        with unit(True) as s:
            req = s.get(PaymentRequest, self.rid); req.counterparty_inn = ''
            doc = RequestDocument(request_id=self.rid, kind='contract', filename='synthetic.pdf', mime='application/pdf',
                storage_key='synthetic-contract-version', sha256='1'*64, content=b'%PDF-1.4 synthetic',
                version=1, active=True, created_by=req.creator_id)
            s.add(doc); s.flush(); doc_id = doc.id
        body['attest_counterparty'] = True
        shown = self.preview_fact(body)
        with unit(True) as s:
            doc = s.get(RequestDocument, doc_id)
            doc.version = 2; doc.sha256 = '2'*64
        self.assertEqual(self.post(self.url('commit'), {**body, 'token': shown['token'], 'confirm': True}).status_code, 409)
        with unit() as s:
            self.assertIsNone(s.scalar(select(Ledger.id)))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(HistoricalRequestTests(name) for name in HistoricalRequestTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
