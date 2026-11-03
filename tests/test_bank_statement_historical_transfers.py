"""Synthetic, atomic accounting for a verified historical own-bank transfer."""
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from sqlalchemy import select

import tests.test_site as base
from app.bank_statement_historical_transfers import CREATED_STATUS, router
from app.bank_statement_guard import pending_bank_debit_hold, set_statement_reconciliation, unreconciled_statements
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.db import Account, Audit, Company, Ledger, PaymentRequest, Setting, User, engine, now, unit
from app.main import app
from app.services import account_balance, funds_state, post_ledger

if not any(getattr(route, 'path', '') == '/api/bank-statements/lines/{id}/historical-transfer/options' for route in app.routes):
    app.include_router(router)


class HistoricalTransferTests(unittest.TestCase):
    """Compose the isolated site fixture without inheriting its unrelated tests."""
    def __getattr__(self, name):
        fixture = self.__dict__.get('_site')
        if fixture is not None:
            return getattr(fixture, name)
        raise AttributeError(name)

    def setUp(self):
        self._site = base.SiteTests()
        self._site.setUp()
        self.day = date.fromisoformat(self.date)
        with unit(True) as s:
            admin = s.scalar(select(User).where(User.username == 'admin'))
            self.admin_id = admin.id
            self.foreign_company = s.scalar(select(Company.id).where(Company.code == 'ZUMA'))
            s.get(Account, self.acc).opening = 100000
            destination = self.account(s, 'Synthetic own destination')
            spare = self.account(s, 'Synthetic spare destination')
            self.destination, self.spare = destination.id, spare.id
            batch = self.batch(s, self.acc, '20200000000000000001', opening=100000, closing=15000, debit=85000)
            self.batch_id = batch.id
            target = self.line(s, batch, self.acc, 'out', 80000, 'TRANSFER-1', 'a' * 64, number=6)
            pending = self.line(s, batch, self.acc, 'out', 5000, 'OTHER-1', 'b' * 64, number=7)
            pending.correspondent_inn = '300000009'
            self.line_id, self.pending_id = target.id, pending.id
            s.info['company_id'] = self.company
            set_statement_reconciliation(s, batch, False, admin)

    def tearDown(self):
        self._site.tearDown()

    def account(self, s, name, *, company=None, currency='UZS', kind='bank', opening=0, archived=False, day=None):
        result = Account(company_id=company or self.company, name=name, kind=kind, currency=currency,
            opening=opening, opening_date=day or self.day - timedelta(days=1), archived=archived, created_by=self.admin_id)
        s.add(result);s.flush()
        return result

    def batch(self, s, account, bank_number, *, opening=0, closing=0, debit=0, credit=0, day=None):
        result = BankStatementBatch(company_id=self.company, account_id=account, created_by=self.admin_id,
            filename='synthetic-own-transfer.html', sha256='c' * 64, account_number=bank_number,
            owner_name='Synthetic Company', owner_inn='300000001', currency='UZS',
            period_from=day or self.day, period_to=day or self.day, opening=opening, closing=closing,
            debit_total=debit, credit_total=credit, balance_matches=True, declared_totals_match=True,
            source_ready=True, status='committed', committed_at=now())
        s.add(result);s.flush()
        return result

    def line(self, s, batch, account, direction, amount, document, fingerprint, *, number):
        result = BankStatementLine(company_id=self.company, batch_id=batch.id, account_id=account,
            line=number, date=self.day, direction=direction, amount=amount, document_number=document,
            correspondent_mfo='00014', correspondent_account='20200000000000000002',
            correspondent_name='Synthetic Company', correspondent_inn='300000001',
            purpose='Synthetic own corporate card transfer', fingerprint=fingerprint, occurrence=1,
            status='review', reason='Synthetic own transfer awaiting verification', include_in_daily=True, uncertain=False)
        s.add(result);s.flush()
        return result

    def path(self, action, line_id=None):
        return f'/api/bank-statements/lines/{line_id or self.line_id}/historical-transfer/{action}'

    def preview(self, *, destination=None, client=None, headers=None, **extra):
        body = {'to_account_id': destination or self.destination,
            'reason': 'Проверены факт банка, реквизиты и собственный счёт получателя', **extra}
        return (client or self.client).post(self.path('preview'), json=body, headers=headers or self.h)

    def confirm(self, checked, *, destination=None, client=None, headers=None, **extra):
        body = {'to_account_id': destination or self.destination,
            'reason': 'Проверены факт банка, реквизиты и собственный счёт получателя',
            'preview_token': checked['preview_token'], 'confirm_bank_fact': True, 'confirm_ownership': True, **extra}
        return (client or self.client).post(self.path('commit'), json=body, headers=headers or self.h)

    def snapshot(self):
        with unit() as s:
            return {model.__tablename__: [tuple(getattr(row, col.key) for col in model.__table__.columns)
                for row in s.scalars(select(model).order_by(*model.__mapper__.primary_key))]
                for model in (Account, Ledger, PaymentRequest, BankStatementBatch, BankStatementLine, Setting)}

    def test_options_and_preview_are_read_only_and_atomic_commit_releases_only_its_own_hold(self):
        before = self.snapshot()
        options = self.client.get(self.path('options'), headers=self.h)
        self.assertEqual(options.status_code, 200, options.text)
        self.assertEqual({row['id'] for row in options.json()['accounts']}, {self.destination, self.spare})
        self.assertTrue(all(not row['correspondence_confirmed'] for row in options.json()['accounts']))
        checked = self.preview()
        self.assertEqual(checked.status_code, 200, checked.text)
        data = checked.json()
        self.assertEqual((data['operation']['date'], data['operation']['amount'], data['operation']['kind']),
                         (self.date, '800.00', 'transfer'))
        self.assertEqual((data['reconciliation']['ledger_closing'], data['reconciliation']['projected_closing'],
                          data['reconciliation']['pending_debit_total'], data['reconciliation']['projected_pending_debit_total']),
                         ('1000.00', '200.00', '850.00', '50.00'))
        self.assertEqual(data['reconciliation']['conservative_closing'], '150.00')
        self.assertEqual(self.snapshot(), before)
        result = self.confirm(data)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['status'], CREATED_STATUS)
        self.assertFalse(result.json()['full_reconciliation'])
        with unit() as s:
            s.info['company_id'] = self.company
            source = s.get(Account, self.acc)
            self.assertEqual((account_balance(s, source), account_balance(s, s.get(Account, self.destination))), (20000, 80000))
            self.assertEqual((pending_bank_debit_hold(s, source), funds_state(s, source, self.day)['available']), (5000, 15000))
            self.assertEqual(len(unreconciled_statements(s, source)), 1)
            entry = s.scalar(select(Ledger))
            self.assertEqual((entry.kind, entry.account_id, entry.to_account_id, entry.date, entry.amount),
                             ('transfer', self.acc, self.destination, self.day, 80000))
            line = s.get(BankStatementLine, self.line_id)
            self.assertEqual((line.status, line.ledger_id, line.classification), (CREATED_STATUS, entry.id, 'internal_transfer'))
            self.assertTrue(s.scalar(select(Audit.id).where(Audit.action == 'Оформлен исторический банковский перевод')))
        after = self.snapshot()
        self.assertEqual(self.confirm(data).status_code, 409)
        self.assertEqual(self.snapshot(), after)
        dashboard = self.client.get('/api/dashboard', headers=self.h, params={'currency': 'UZS'})
        self.assertEqual(dashboard.status_code, 200, dashboard.text)
        self.assertEqual((dashboard.json()['fact_in'], dashboard.json()['fact_out'], dashboard.json()['balance']), ('0.00', '0.00', '1000.00'))
        report = self.client.get('/api/report', headers=self.h, params={'year': self.day.year, 'currency': 'UZS'})
        self.assertEqual(report.status_code, 200, report.text)
        self.assertTrue(all(value == '0.00' for value in report.json()['totals']))

    def test_last_pending_transfer_proves_exact_bank_closing_and_clears_marker(self):
        with unit(True) as s:
            s.delete(s.get(BankStatementLine, self.pending_id))
            batch = s.get(BankStatementBatch, self.batch_id);batch.debit_total = 80000;batch.closing = 20000
        checked = self.preview()
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertTrue(checked.json()['reconciliation']['full_reconciliation'])
        result = self.confirm(checked.json())
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['full_reconciliation'])
        with unit() as s:
            s.info['company_id'] = self.company
            self.assertEqual(unreconciled_statements(s, s.get(Account, self.acc)), [])
            self.assertEqual(pending_bank_debit_hold(s, s.get(Account, self.acc)), 0)

    def test_role_csrf_explicit_confirmations_reason_and_company_are_required(self):
        finance, fh = self._site.make_user('finance', 'synthetic_historical_finance')
        accountant, ah = self._site.make_user('accountant', 'synthetic_historical_payer')
        try:
            checked = self.preview().json()
            for client, headers in ((finance, fh), (accountant, ah)):
                self.assertEqual(client.get(self.path('options'), headers=headers).status_code, 403)
                self.assertEqual(self.preview(client=client, headers=headers).status_code, 403)
                self.assertEqual(self.confirm(checked, client=client, headers=headers).status_code, 403)
            self.assertEqual(self.client.post(self.path('preview'), json={'to_account_id': self.destination, 'reason': 'Long synthetic reason'}).status_code, 403)
            self.assertEqual(self.preview(reason='short').status_code, 422)
            self.assertEqual(self.preview(confirm=True).status_code, 422)
            for flag in ('confirm_bank_fact', 'confirm_ownership'):
                self.assertEqual(self.confirm(checked, **{flag: False}).status_code, 422)
                self.assertEqual(self.confirm(checked, **{flag: 'true'}).status_code, 422)
            foreign = {**self.h, 'X-Company-ID': str(self.foreign_company)}
            self.assertEqual(self.client.get(self.path('options'), headers=foreign).status_code, 404)
            self.assertEqual(self.preview(headers=foreign).status_code, 404)
            with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))), 0)
        finally:
            finance.close();accountant.close()

    def test_destination_scope_currency_archiving_date_and_binding_are_rechecked(self):
        with unit(True) as s:
            invalid = [self.account(s, 'Synthetic USD', currency='USD').id,
                self.account(s, 'Synthetic cash', kind='cash').id,
                self.account(s, 'Synthetic archived', archived=True).id,
                self.account(s, 'Synthetic future', day=self.day + timedelta(days=1)).id,
                self.account(s, 'Synthetic foreign', company=self.foreign_company).id]
            self.batch(s, self.destination, '20200000000000000002', day=self.day - timedelta(days=1))
        options = self.client.get(self.path('options'), headers=self.h).json()
        self.assertEqual(options['accounts'], [{'id': self.destination, 'name': 'Synthetic own destination', 'currency': 'UZS', 'correspondence_confirmed': True}])
        for account_id in [self.acc, *invalid, self.spare]:
            with self.subTest(account=account_id):self.assertIn(self.preview(destination=account_id).status_code, (404, 409))
        checked = self.preview()
        self.assertEqual(checked.status_code, 200, checked.text)
        with unit(True) as s:s.get(Account, self.destination).archived = True
        self.assertEqual(self.confirm(checked.json()).status_code, 409)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))), 0)

    def test_stale_reason_account_or_financial_snapshot_cannot_commit(self):
        checked = self.preview().json()
        self.assertEqual(self.confirm(checked, reason='Different but sufficiently long verified reason').status_code, 409)
        with unit(True) as s:s.get(Account, self.destination).name = 'Synthetic renamed destination'
        self.assertEqual(self.confirm(checked).status_code, 409)
        fresh = self.preview().json()
        with unit(True) as s:
            s.add(Ledger(account_id=self.destination, category_id=self.cat, kind='in', amount=100,
                date=self.day, counterparty='Synthetic deposit', reference='SYNTH-HIST-CHANGE',
                note='Synthetic state change after preview', creator_id=self.admin_id))
        before = self.snapshot()
        self.assertEqual(self.confirm(fresh).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_existing_duplicate_or_reversed_movement_is_never_recreated(self):
        with unit(True) as s:
            entry = Ledger(account_id=self.acc, to_account_id=self.destination, kind='transfer', amount=80000,
                date=self.day, counterparty='Synthetic existing own transfer', reference='BANK-' + 'a' * 64,
                note='Synthetic existing own transfer', creator_id=self.admin_id)
            s.add(entry);s.flush()
            s.add(Ledger(account_id=self.destination, to_account_id=self.acc, kind='transfer', amount=80000,
                date=self.day, counterparty='Synthetic reversal', reference='SYNTH-HIST-REV',
                note='Synthetic reversed own transfer', creator_id=self.admin_id, reversal_of=entry.id))
        before = self.snapshot()
        result = self.preview()
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.snapshot(), before)
        with unit(True) as s:
            line = s.get(BankStatementLine, self.line_id);line.uncertain = True
        self.assertEqual(self.client.get(self.path('options'), headers=self.h).status_code, 409)

    def test_closed_source_conflicting_classification_and_stored_duplicate_are_rejected(self):
        for field, value, original in [('status', 'deleted', 'committed'), ('source_ready', False, True)]:
            with unit(True) as s:setattr(s.get(BankStatementBatch, self.batch_id), field, value)
            self.assertEqual(self.preview().status_code, 409)
            with unit(True) as s:setattr(s.get(BankStatementBatch, self.batch_id), field, original)
        with unit(True) as s:s.get(BankStatementLine, self.line_id).classification = 'bank_fee'
        self.assertEqual(self.preview().status_code, 409)
        with unit(True) as s:
            original = s.get(BankStatementLine, self.line_id);original.classification = ''
            values = {column.key: getattr(original, column.key) for column in BankStatementLine.__table__.columns if column.key != 'id'}
            values.update(line=10, document_number='SYNTH-DUPLICATE')
            s.add(BankStatementLine(**values))
        self.assertEqual(self.preview().status_code, 409)

    def test_unreceived_income_and_approved_reservations_do_not_fund_a_historical_transfer(self):
        with unit(True) as s:
            target = s.get(BankStatementLine, self.line_id);target.amount = 120000
            batch = s.get(BankStatementBatch, self.batch_id);batch.debit_total = 125000;batch.credit_total = 50000;batch.closing = 25000
            receipt = self.line(s, batch, self.acc, 'in', 50000, 'UNVERIFIED-INCOME', 'd' * 64, number=8)
            receipt.correspondent_inn = '300000003'
        before = self.snapshot()
        result = self.preview()
        self.assertEqual(result.status_code, 409, result.text)
        self.assertIn('непроведённые поступления', result.json()['detail'])
        self.assertEqual(self.snapshot(), before)
        with unit(True) as s:
            target = s.get(BankStatementLine, self.line_id);target.amount = 80000
            batch = s.get(BankStatementBatch, self.batch_id);batch.debit_total = 85000;batch.credit_total = 0;batch.closing = 15000
            s.delete(s.get(BankStatementLine, receipt.id))
            s.add(PaymentRequest(creator_id=self.admin_id, category_id=self.cat, account_id=self.acc,
                counterparty='Synthetic reserved supplier', purpose='Synthetic approved reservation',
                amount=30000, due_date=self.day, status='approved'))
        result = self.preview()
        self.assertEqual(result.status_code, 409, result.text)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))), 0)

    def test_existing_destination_statement_is_protected_without_automatic_opposite_link(self):
        with unit(True) as s:
            destination_batch = self.batch(s, self.destination, '20200000000000000002', closing=80000, credit=80000)
            incoming = self.line(s, destination_batch, self.destination, 'in', 80000, 'OPPOSITE-PENDING', 'e' * 64, number=6)
            incoming.correspondent_account = '20200000000000000001'
        before = self.snapshot()
        result = self.preview()
        self.assertEqual(result.status_code, 409, result.text)
        self.assertIn('другой подтверждённой выписки', result.json()['detail'])
        self.assertEqual(self.snapshot(), before)

    def test_late_invariant_failure_rolls_back_transfer_link_balance_and_audit(self):
        checked = self.preview().json()
        before = self.snapshot()
        original = post_ledger
        def add_unexpected(s, user, data, **kwargs):
            entry = original(s, user, data, **kwargs)
            s.add(Ledger(account_id=self.acc, category_id=self.cat, kind='in', amount=1,
                date=self.day, counterparty='Synthetic unexpected extra entry', reference='SYNTH-HIST-LATE',
                note='Synthetic atomic rollback proof', creator_id=user.id))
            s.flush()
            return entry
        with patch('app.bank_statement_historical_transfers.post_ledger', side_effect=add_unexpected):
            result = self.confirm(checked)
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.snapshot(), before)
        with unit() as s:
            self.assertIsNone(s.scalar(select(Audit.id).where(Audit.action == 'Оформлен исторический банковский перевод')))

    def historical_date_fixture(self):
        bank_day = self.day - timedelta(days=2)
        with unit(True) as s:
            s.get(Account, self.acc).opening = 300000
            for account_id in (self.destination, self.spare):
                s.get(Account, account_id).opening_date = self.day - timedelta(days=20)
            batch = s.get(BankStatementBatch, self.batch_id)
            batch.period_from = batch.period_to = bank_day
            batch.opening = 300000;batch.closing = 215000
            for line_id in (self.line_id, self.pending_id):s.get(BankStatementLine, line_id).date = bank_day
        return bank_day

    def test_nearby_same_destination_transfer_cannot_be_created_again_with_another_reference(self):
        bank_day = self.historical_date_fixture()
        with unit(True) as s:
            s.add(Ledger(account_id=self.acc, to_account_id=self.destination, kind='transfer', amount=80000,
                date=bank_day + timedelta(days=1), counterparty='Synthetic own destination',
                reference='SYNTH-MANUAL-WRONG-DATE', note='Synthetic manual bank-date mismatch', creator_id=self.admin_id))
        before = self.snapshot()
        result = self.preview()
        self.assertEqual(result.status_code, 409, result.text)
        self.assertIn('Есть похожая или отменённая операция', result.json()['detail'])
        self.assertEqual(self.snapshot(), before)

    def test_nearby_reversed_transfer_is_not_recreated_and_bank_document_blocks_any_date(self):
        bank_day = self.historical_date_fixture()
        with unit(True) as s:
            entry = Ledger(account_id=self.acc, to_account_id=self.destination, kind='transfer', amount=80000,
                date=bank_day + timedelta(days=1), counterparty='Synthetic own destination',
                reference='SYNTH-HIST-REVERSED-NEARBY', note='Synthetic reversed date mismatch', creator_id=self.admin_id)
            s.add(entry);s.flush();entry_id = entry.id
            reversal = Ledger(account_id=self.destination, to_account_id=self.acc, kind='transfer', amount=80000,
                date=entry.date, counterparty='Synthetic own transfer reversal', reference='SYNTH-HIST-REVERSED-PAIR',
                note='Synthetic reversal', creator_id=self.admin_id, reversal_of=entry.id)
            s.add(reversal);s.flush();reversal_id = reversal.id
        self.assertEqual(self.preview().status_code, 409)
        with unit(True) as s:
            s.delete(s.get(Ledger, reversal_id));s.delete(s.get(Ledger, entry_id))
            # A bank reference collision is unsafe even outside the nearby-date
            # window and even when another destination was selected manually.
            s.add(Ledger(account_id=self.acc, to_account_id=self.spare, kind='transfer', amount=100,
                date=bank_day - timedelta(days=9), counterparty='Synthetic other destination', reference='TRANSFER-1',
                note='Synthetic old bank-document reference', creator_id=self.admin_id))
        before = self.snapshot()
        result = self.preview()
        self.assertEqual(result.status_code, 409, result.text)
        self.assertIn('Есть похожая или отменённая операция', result.json()['detail'])
        self.assertEqual(self.snapshot(), before)

    def test_different_destination_same_amount_with_different_reference_remains_distinct(self):
        bank_day = self.historical_date_fixture()
        with unit(True) as s:
            s.add(Ledger(account_id=self.acc, to_account_id=self.spare, kind='transfer', amount=80000,
                date=bank_day + timedelta(days=1), counterparty='Synthetic spare destination',
                reference='SYNTH-LEGITIMATE-OTHER-DESTINATION', note='Synthetic distinct own transfer', creator_id=self.admin_id))
        checked = self.preview()
        self.assertEqual(checked.status_code, 200, checked.text)
        result = self.confirm(checked.json())
        self.assertEqual(result.status_code, 200, result.text)
        with unit() as s:
            entries = list(s.scalars(select(Ledger).where(Ledger.kind == 'transfer')))
            self.assertEqual(len(entries), 2)
            self.assertEqual({entry.to_account_id for entry in entries}, {self.destination, self.spare})
            self.assertEqual(account_balance(s, s.get(Account, self.destination)), 80000)
            self.assertEqual(account_balance(s, s.get(Account, self.spare)), 80000)


def tearDownModule():
    engine.dispose()
