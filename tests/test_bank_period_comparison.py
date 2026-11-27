"""Read-only period comparison, scope, source identity and evidence strength."""
from datetime import date, timedelta
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import func, select

from tests.test_bank_statement_api import BankStatementTests
from app.bank_period_comparison import _active_requests
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.db import Account, Company, Ledger, PaymentRequest, User, engine, unit


class PeriodComparisonTests(BankStatementTests):
    def test_card_uses_latest_visible_bank_day_and_keeps_ledger_independent(self):
        current = date.fromisoformat(self.date)
        day = str(current - timedelta(days=1))
        older = str(current - timedelta(days=2))
        def draft(stamp, debit):
            row = self.bank_row(str(self.counter + 11), date=stamp, amount=debit)
            src = dict(self.statement([row]), period_from=stamp, period_to=stamp,
                       debit_total=debit, closing_balance='0.00' if debit=='1000.00' else '400.00')
            response = self.preview(src)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()['id']

        draft(day, '600.00')
        latest = draft(day, '1000.00')
        draft(older, '600.00')
        broken = draft(self.date, '600.00')
        with unit(True) as s:
            s.get(BankStatementBatch, broken).balance_matches = False
        self.assertEqual(self.ledger('17.00','in',date=self.date).status_code,200)
        before = self.counts()
        def card(client, headers):
            response = client.get('/api/accounts?include_statement_balance=true', headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            return next(row for row in response.json() if row['id']==self.acc)

        result = card(self.client, self.h)
        self.assertEqual(result['balance'],'1000017.00')
        self.assertEqual(result['statement_balance'],dict(id=latest,date=day,status='preview',
            closing='0.00',book_closing='1000000.00',difference='-1000000.00'))
        other, auth = self.make_user('finance','other_card_reader')
        self.addCleanup(other.close)
        self.assertIsNone(card(other, auth)['statement_balance'])
        self.assertEqual(self.counts(),before)
        with unit(True) as s:
            s.get(BankStatementBatch,latest).status='deleted'
        self.assertEqual(card(self.client,self.h)['statement_balance']['closing'],'400.00')

    def compare(self, source, confirm=True, account_id=None, currency='UZS', login=None, **headers):
        client, auth = login or (self.client, self.h)
        with patch('app.bank_period_comparison.parse_period_statement', return_value=source):
            return client.post('/api/bank-statements/compare-period',
                params={'account_id': account_id or self.acc, 'currency': currency, 'confirm_account': confirm},
                headers={**auth, 'X-Filename': 'Synthetic.xlsx', **headers}, content=b'synthetic')

    def statement(self, rows=None):
        return {**self.source(rows), 'owner_name': 'OOO UZGERMED TEST'}

    def counts(self):
        with unit() as s:
            return [s.scalar(select(func.count()).select_from(model))
                    for model in (BankStatementBatch, BankStatementLine, Ledger, PaymentRequest)]

    def test_day_balances_use_file_history_and_separate_site_dates(self):
        first = date.fromisoformat(self.date)
        next_day = str(first + timedelta(days=1))
        rows = [dict(self.bank_row('31', amount='10.01'), direction='in'),
                self.bank_row('32', date=next_day, amount='3.24', line=7)]
        source = dict(self.statement(rows), period_to=next_day, credit_total='10.01',
                      debit_total='3.24', opening_balance='0.00', closing_balance='6.77')
        with unit(True) as s:
            self.payment_part(s, 'bank-day-test', 150)
        before = self.counts()
        data = self.compare(source).json()
        day = data['days'][next_day]
        self.assertEqual([day[k] for k in ('opening_balance','credit_total','debit_total','closing_balance')],
                         ['10.01','0.00','3.24','6.77'])
        self.assertEqual(day['site']['opening_balance'], data['days'][self.date]['site']['closing_balance'])
        self.assertEqual(day['site']['closing_balance'], data['site']['closing_balance'])
        self.assertEqual(self.compare(dict(source, credit_total='0.00')).json()['days'], {})
        self.assertEqual(self.counts(), before)

    def payment_part(self, session, document, minor, *, account=None, inn='300000002'):
        """An existing paid request and its outgoing fact; amounts are in minor units."""
        bank = session.get(Account, account or self.acc)
        day = date.fromisoformat(self.date)
        req = PaymentRequest(account_id=bank.id, category_id=self.cat, creator_id=bank.created_by,
            due_date=day, status='paid', amount=minor, counterparty='Synthetic supplier',
            counterparty_inn=inn, purpose='Synthetic invoice')
        session.add(req)
        session.flush()
        session.add(Ledger(account_id=bank.id, category_id=self.cat, creator_id=bank.created_by,
            request_id=req.id, reference=document, date=day, amount=minor, kind='out'))
        return req.id

    def test_payment_group_uses_paid_facts_and_exact_document_parts_without_writes(self):
        other = self.post('/api/accounts', {'name': 'Other synthetic bank', 'kind': 'bank',
            'currency': 'UZS', 'opening': '0', 'opening_date': self.date}).json()['id']
        with unit(True) as s:
            base = self.payment_part(s, '0078', 12135)
            second = self.payment_part(s, '0078/1', 20124)
            third = self.payment_part(s, '0078/2', 27741)
            s.get(PaymentRequest, third).amount = 27740
            for alias in ('78/3', '00780/3', '0078/0', '0078/3x', '0078/3/4'):
                self.payment_part(s, alias, 999)
            self.payment_part(s, '0078/3', 999, account=other)
        counts = self.counts()
        bank_day = str(date.fromisoformat(self.date) - timedelta(days=3))
        for debit, difference in [('600.00', '0.00'), ('600.01', '-0.01')]:
            with self.subTest(bank_amount=debit):
                bank = self.bank_row('0078', date=bank_day, amount=debit)
                source = {**self.statement([bank]), 'period_from': bank_day, 'period_to': self.date}
                response = self.compare(source)
                self.assertEqual(response.status_code, 200, response.text)
                report = response.json()
                row, = report['rows']
                self.assertEqual(row['status'], 'candidate')
                group = row['payment_group']
                self.assertCountEqual(group['request_ids'], [base, second, third])
                self.assertEqual((group['total_amount'], group['difference']), ('600.00', difference))
                self.assertEqual({r['request_id']: (r['document_number'], r['amount'], r['date'])
                                  for r in row['request_details']}, {
                    base: ('0078', '121.35', self.date), second: ('0078/1', '201.24', self.date),
                    third: ('0078/2', '277.41', self.date)})
                candidates = {p['request_id'] for p in report['unmatched_payments'] if p['status'] == 'candidate'}
                self.assertEqual(candidates, {base, second, third})
        with patch('app.bank_period_comparison.can_view_request', side_effect=lambda s, u, req: req.id != second):
            restricted = self.compare(self.statement([self.bank_row('0078')])).json()
        self.assertNotIn(second, restricted['rows'][0]['request_ids'])
        self.assertNotIn(second, [p['request_id'] for p in restricted['unmatched_payments']])
        group = restricted['rows'][0].get('payment_group')
        if group:
            self.assertEqual(group['total_amount'], '398.76')
        self.assertEqual(self.counts(), counts)

    def test_all_parts_visible_beyond_alternative_candidate_limit(self):
        with unit(True) as s:
            members = [self.payment_part(s, f'863/{n}' if n else '863', 100) for n in range(13)]
        row, = self.compare(self.statement([self.bank_row('863', amount='13.00')])).json()['rows']
        self.assertEqual(row['status'], 'candidate')
        self.assertEqual(row['payment_group']['total_amount'], '13.00')
        self.assertCountEqual(row['payment_group']['request_ids'], members)
        self.assertCountEqual([r['request_id'] for r in row['request_details']], members)

    def test_exact_part_references_still_work_outside_neighbouring_date_window(self):
        with unit(True) as s:
            members = [self.payment_part(s, doc, 30000) for doc in ('68', '68/1')]
        bank_day = str(date.fromisoformat(self.date) - timedelta(days=60))
        source = {**self.statement([self.bank_row('68', date=bank_day)]),
                  'period_from': bank_day, 'period_to': bank_day}
        report = self.compare(source).json()
        row, = report['rows']
        self.assertEqual(row['status'], 'candidate')
        self.assertCountEqual(row['payment_group']['request_ids'], members)
        self.assertEqual(report['unmatched_payments'], [])

    def test_saved_payment_link_takes_precedence_over_a_document_suffix(self):
        rid = self.approved()
        self.assertEqual(self.ledger('600', 'out', reference='825', request_id=rid).status_code, 200)
        source = self.statement([self.bank_row('825')])
        batch = self.preview(source).json()['id']
        self.assertEqual(self.commit(batch).status_code, 200)
        with unit(True) as s:
            self.payment_part(s, '825/1', 10000)
        before = self.counts()
        row, = self.compare(source).json()['rows']
        self.assertEqual((row['status'], row['request_ids']), ('matched', [rid]))
        self.assertFalse(row.get('payment_group'))
        self.assertEqual(self.counts(), before)

    def test_separately_listed_suffix_or_repeated_bank_debit_cannot_use_same_group_twice(self):
        with unit(True) as s:
            self.payment_part(s, '87', 30000)
            second = self.payment_part(s, '87/1', 30000)
        original = self.bank_row('87')
        separate = self.bank_row('87/1', line=7, amount='300.00')
        report = self.compare(self.statement([original, separate])).json()['rows']
        self.assertNotIn(second, (report[0].get('payment_group') or {}).get('request_ids', []))
        self.assertFalse(report[1].get('payment_group'))
        self.assertEqual(report[1]['request_ids'], [second])
        repeated = self.compare(self.statement([original, dict(original, line=7)])).json()['rows']
        for row in repeated:
            self.assertEqual(row['status'], 'candidate')
            self.assertFalse(row.get('payment_group'))

    def test_reversed_or_other_payee_fact_is_not_included_in_group(self):
        with unit(True) as s:
            self.payment_part(s, '695', 20000)
            self.payment_part(s, '695/1', 10000)
            foreign = self.payment_part(s, '695/2', 20000, inn='300009999')
            reversed_id = self.payment_part(s, '695/3', 10000)
            paid = s.scalar(select(Ledger).where(Ledger.request_id == reversed_id))
            s.add(Ledger(account_id=paid.account_id, category_id=paid.category_id, kind='in',
                date=paid.date, amount=paid.amount, reference='REV-PART-TEST', reversal_of=paid.id,
                creator_id=paid.creator_id))
        row, = self.compare(self.statement([self.bank_row('695')])).json()['rows']
        self.assertEqual(row['status'], 'candidate')
        group = row.get('payment_group')
        if group:
            self.assertEqual(group['total_amount'], '300.00')
            self.assertNotIn(foreign, group['request_ids'])
            self.assertNotIn(reversed_id, group['request_ids'])

    def test_duplicate_suffix_in_legacy_facts_is_not_summed(self):
        with unit(True) as s:
            self.payment_part(s, '681', 20000)
            self.payment_part(s, '681/1', 20000)
            last = self.payment_part(s, '681/2', 20000)

        def duplicate_suffix(*args):
            requests, facts = _active_requests(*args)
            # Keep the database uniqueness constraint intact while simulating ambiguous input to the matcher.
            entry = facts[last]
            facts[last] = SimpleNamespace(id=entry.id, amount=entry.amount, date=entry.date, reference='681/1')
            return requests, facts

        with patch('app.bank_period_comparison._active_requests', side_effect=duplicate_suffix):
            row, = self.compare(self.statement([self.bank_row('681')])).json()['rows']
        self.assertEqual(row['status'], 'candidate')
        self.assertFalse(row.get('payment_group'))

    def test_first_time_confirmation_then_exact_payment_and_unmatched_mirror(self):
        rid = self.approved()
        done = self.ledger('600', 'out', reference='0071', request_id=rid)
        self.assertEqual(done.status_code, 200, done.text)
        source = self.statement([self.bank_row('0071')])
        counts = self.counts()
        first = self.compare(source, confirm=False)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()['requires_confirmation'])
        self.assertNotIn('site', first.json())
        exact = self.compare(source)
        self.assertEqual(exact.status_code, 200, exact.text)
        out = exact.json()
        self.assertEqual((out['rows'][0]['status'], out['rows'][0]['request_ids']), ('matched', [rid]))
        self.assertEqual(out['unmatched_payments'], [])
        self.assertEqual(out['site'], {'opening_balance': '1000000.00', 'closing_balance': '999400.00',
            'opening_difference': '-999000.00', 'closing_difference': '-999000.00'})
        missing = self.compare(self.statement([self.bank_row('071', inn='300000999')])).json()
        self.assertEqual(missing['rows'][0]['status'], 'bank_only')
        self.assertEqual([(r['request_id'], r['status']) for r in missing['unmatched_payments']], [(rid, 'app_only')])
        self.assertEqual(counts, self.counts())

    def test_placeholder_inn_cannot_confirm_payment(self):
        rid = self.approved()
        self.assertEqual(self.ledger('600', 'out', reference='00017', request_id=rid).status_code, 200)
        with unit(True) as s:
            s.get(PaymentRequest, rid).counterparty_inn = '000000000'
        result = self.compare(self.statement([self.bank_row('00017', inn='000000000')])).json()
        self.assertEqual(result['rows'][0]['status'], 'candidate')
        self.assertEqual(result['unmatched_payments'][0]['request_id'], rid)

    def test_contract_conflict_stays_for_review_and_absent_or_ambiguous_is_unknown(self):
        rid = self.approved()
        with unit(True) as s:
            s.get(PaymentRequest, rid).purpose = 'Услуги по договору № TEST-17-Т'
        self.assertEqual(self.ledger('600', 'out', reference='A7', request_id=rid).status_code, 200)
        counts = self.counts()
        for purpose, bank_number, conflict in (
            ('Договор № TEST-19-Т', 'TEST-19-Т', True),
            ('Shartnoma № Test–17-T', 'TEST-17-T', False),
            ('Счёт № 41 за услуги', None, False),
            ('Договор № 41, № 42', None, False),
            ('Договор № 41 и договор № 42', None, False),
        ):
            with self.subTest(purpose=purpose):
                bank = dict(self.bank_row('A7'), purpose=purpose)
                result = self.compare(self.statement([bank])).json()
                row = result['rows'][0]
                detail, = row['request_details']
                self.assertEqual((row['contract_number'], detail['contract_number']), (bank_number, 'TEST-17-Т'))
                self.assertEqual((row['correspondent_inn'], detail['counterparty_inn']), ('300000002', '300000002'))
                self.assertEqual(detail['contract_mismatch'], conflict)
                self.assertEqual(row['status'], 'candidate' if conflict else 'matched')
                self.assertEqual(bool(result['unmatched_payments']), conflict)
        self.assertEqual(self.counts(), counts)

    def test_conflicting_inn_on_saved_link_is_visible_without_changing_the_link(self):
        rid = self.approved()
        self.assertEqual(self.ledger('600', 'out', reference='A8', request_id=rid).status_code, 200)
        source = self.statement([self.bank_row('A8')])
        batch_id = self.preview(source).json()['id']
        self.assertEqual(self.commit(batch_id).status_code, 200)
        with unit(True) as s:
            s.get(PaymentRequest, rid).counterparty_inn = '300000003'
        counts = self.counts()
        row = self.compare(source).json()['rows'][0]
        self.assertEqual((row['status'], row['request_ids']), ('candidate', [rid]))
        self.assertEqual(self.counts(), counts)
        with unit() as s:
            self.assertEqual(s.scalar(select(BankStatementLine.request_id).where(
                BankStatementLine.batch_id == batch_id)), rid)

    def test_repeated_document_wrong_cf_and_unpaid_do_not_match(self):
        rid = self.approved()
        self.assertEqual(self.compare(self.statement()).json()['rows'][0]['status'], 'candidate')
        self.assertEqual(self.ledger('600', 'out', reference='0001', request_id=rid).status_code, 200)
        pair = self.compare(self.statement([self.bank_row('0001'), self.bank_row('0001', line=7)])).json()
        self.assertEqual([r['status'] for r in pair['rows']], ['candidate', 'candidate'])
        self.assertEqual(pair['unmatched_payments'][0]['status'], 'candidate')
        mismatch = self.bank_row('0001')
        mismatch['purpose'] = f'Оплата по CF-{rid + 1:05d}'
        self.assertEqual(self.compare(self.statement([mismatch])).json()['rows'][0]['status'], 'candidate')
        mismatch['purpose'] = f'Оплата по СF {rid + 1:05d}'
        self.assertEqual(self.compare(self.statement([mismatch])).json()['rows'][0]['status'], 'candidate')
        mismatch['purpose'] = 'Банк'
        self.assertEqual(self.compare({**self.statement([mismatch]), 'ready': False}).json()['rows'][0]['status'], 'candidate')

    def test_date_outside_period_name_candidate_not_missing_for_period(self):
        rid = self.approved()
        self.assertEqual(self.ledger('600', 'out', reference='42', request_id=rid).status_code, 200)
        with unit(True) as s:
            req = s.get(PaymentRequest, rid)
            req.counterparty, req.counterparty_inn = 'ООО Synthetic Airport', ''
        day = str(date.fromisoformat(self.date) - timedelta(days=2))
        row = self.bank_row('99', date=day)
        row['correspondent_name'] = 'SYNTHETIC AIRPORT MCHJ'
        out = self.compare({**self.statement([row]), 'period_from': day, 'period_to': day}).json()
        self.assertEqual(out['rows'][0]['status'], 'candidate')
        detail = out['rows'][0]['request_details'][0]
        self.assertEqual((detail['request_id'], detail['date'], detail['document_number']), (rid, self.date, '42'))
        self.assertEqual(out['unmatched_payments'], [])

    def test_uploaded_draft_compares_existing_parts_without_reupload_or_posting(self):
        with unit(True) as s:
            members = [self.payment_part(s, number, 30000) for number in ('842', '842/1')]
        source = self.statement([self.bank_row('842')])
        ident = self.preview(source).json()['id']
        path = f'/api/bank-statements/period-sources/{ident}'
        params = {'account_id': self.acc, 'currency': 'UZS', 'draft': 'true'}
        before = self.counts()
        owner, headers = self.payer
        pending = owner.get(path, params=params, headers=headers)
        self.assertEqual(pending.status_code, 200, pending.text)
        self.assertTrue(pending.json()['requires_confirmation'])
        self.assertEqual(pending.json()['rows'], [])
        params['confirm_account'] = 'true'
        result = owner.get(path, params=params, headers=headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['rows'][0]['payment_group']['request_ids'], members)
        other, auth = self.make_user('accountant', 'other_draft_reader')
        try:
            self.assertEqual(other.get(path, params=params, headers=auth).status_code, 404)
        finally:
            other.close()
        self.assertEqual(self.counts(), before)
        with unit() as s:
            self.assertEqual(s.get(BankStatementBatch, ident).status, 'preview')
            self.assertIsNone(s.scalar(select(BankStatementLine).where(BankStatementLine.batch_id == ident)).ledger_id)

    def test_explicit_draft_link_reads_a_distant_paid_fact_only_in_its_own_draft(self):
        rid = self.approved()
        with unit(True) as s:
            req = s.get(PaymentRequest, rid)
            req.status = 'paid'
            payment_day = req.due_date + timedelta(days=45)
            # Exclude a suggestion from the planned-date window as well.
            req.due_date = payment_day
            payment = Ledger(account_id=self.acc, category_id=self.cat, kind='out', amount=req.amount,
                date=payment_day, reference='TREASURY-98', request_id=rid, creator_id=req.creator_id)
            s.add(payment)
            s.flush()
            ledger_id, version = payment.id, req.version
        source = self.statement([self.bank_row('1085')])
        draft = self.preview(source).json()
        sibling = self.preview(source).json()
        self.assertNotEqual(draft['id'], sibling['id'])
        client, headers = self.payer
        params = {'account_id':self.acc, 'currency':'UZS', 'draft':'true', 'confirm_account':'true'}

        def compare_draft(batch):
            response = client.get(f"/api/bank-statements/period-sources/{batch['id']}", params=params, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()['rows'][0]

        before = self.counts()
        response = client.post(f"/api/bank-statements/lines/{draft['rows'][0]['id']}/match?draft=true", headers=headers,
            json={'request_id':rid, 'request_version':version, 'payment_date':str(payment_day),
                  'confirm':True, 'note':'Verified contract and bank receipt'})
        self.assertEqual(response.status_code, 200, response.text)
        matched = compare_draft(draft)
        self.assertEqual((matched['status'], matched['request_ids']), ('matched', [rid]))
        self.assertIn('в этом черновике', matched['reason'])
        self.assertEqual(matched['request_details'][0]['date'], str(payment_day))
        self.assertEqual(matched['request_details'][0]['document_number'], 'TREASURY-98')
        self.assertNotEqual(compare_draft(sibling)['status'], 'matched')
        self.assertNotEqual(self.compare(source).json()['rows'][0]['status'], 'matched')
        self.assertEqual(self.counts(), before)
        with unit(True) as s:
            self.assertEqual(s.get(BankStatementBatch, draft['id']).status, 'preview')
            paid = s.get(Ledger, ledger_id)
            s.add(Ledger(account_id=self.acc, category_id=self.cat, kind='in', amount=paid.amount, date=paid.date,
                reference='REV-EXPLICIT-DRAFT', reversal_of=paid.id, creator_id=paid.creator_id))
        self.assertEqual(compare_draft(draft)['status'], 'candidate')

    def test_saved_statement_source_only_committed_and_active_links(self):
        rid = self.approved()
        self.assertEqual(self.ledger('600', 'out', reference='Internal', request_id=rid).status_code, 200)
        source = self.statement()
        preview = self.preview(source)
        self.assertEqual(preview.status_code, 200, preview.text)
        params = {'account_id': self.acc, 'currency': 'UZS'}
        base = '/api/bank-statements/period-sources/'
        self.assertEqual(self.client.get(base+'list', params=params).json()['items'], [])
        ident = preview.json()['id']
        draft_params = {**params, 'include_drafts': 'true'}
        listed, = self.client.get(base+'list', params=draft_params).json()['items']
        self.assertEqual((listed['id'], listed['status']), (ident, 'preview'))
        other, _ = self.make_user('accountant', 'other_period_viewer')
        self.assertEqual(other.get(base+'list', params=draft_params).json()['items'], [])
        self.assertEqual(self.client.get(base+str(ident), params=params).status_code, 404)
        commit = self.commit(ident)
        self.assertEqual(commit.status_code, 200, commit.text)
        self.assertEqual(self.client.get(base+'list', params=params).json()['items'][0]['id'], ident)
        result = self.client.get(base+str(ident), params=params)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual((result.json()['rows'][0]['status'], result.json()['binding']), ('matched', 'confirmed'))
        for key, value in [('account_number', '20200000000000000999'), ('owner_inn', '300009999')]:
            self.assertEqual(self.compare({**source, key: value}).status_code, 409)
        self.assertNotIn('requires_confirmation', self.compare(source, confirm=False).json())

    def test_unverified_source_disputed_stored_rows_and_already_claimed_fact_block_exact_match(self):
        rid = self.approved()
        self.assertEqual(self.ledger('600', 'out', reference='0021', request_id=rid).status_code, 200)
        source = self.statement([self.bank_row('0021')])
        batch = self.preview(source).json()['id']
        self.assertEqual(self.commit(batch).status_code, 200)
        self.assertEqual(self.compare(source).json()['rows'][0]['status'], 'matched')
        bad = {**source, 'ready':False, 'balance_matches':False, 'closing_balance':'123.00'}
        self.assertEqual(self.compare(bad).json()['rows'][0]['status'], 'candidate')
        competing = self.bank_row('0021')
        competing['correspondent_account'] = '20200000000000000901'
        self.assertEqual(self.compare(self.statement([competing])).json()['rows'][0]['status'], 'candidate')
        with unit(True) as s:
            line = s.scalar(select(BankStatementLine).where(BankStatementLine.batch_id == batch))
            original = line.ledger_id
            line.uncertain, line.request_id, line.ledger_id = True, None, None
            line.status = 'review'
        self.assertEqual(self.compare(source).json()['rows'][0]['status'], 'candidate')
        with unit(True) as s:
            line = s.scalar(select(BankStatementLine).where(BankStatementLine.batch_id == batch))
            line.uncertain, line.request_id, line.ledger_id, line.status = False, rid, original, 'paid_match'
            paid = s.get(Ledger, original)
            s.add(Ledger(account_id=paid.account_id, category_id=paid.category_id, kind='in',
                date=paid.date, amount=paid.amount, reference='REVERSE-TEST', reversal_of=paid.id,
                note='Synthetic reversal', creator_id=paid.creator_id))
        reversed_report = self.compare(source).json()
        self.assertEqual(reversed_report['rows'][0]['status'], 'candidate')
        self.assertEqual(reversed_report['unmatched_payments'], [])

    def test_wrong_company_role_hidden_section_and_currency(self):
        source = self.statement()
        self.assertEqual(self.compare({**source, 'owner_name': 'OOO ZUMA'}).status_code, 409)
        self.assertEqual(self.compare({**source, 'currency': 'USD'}).status_code, 409)
        result = self.post('/api/accounts', {'name': 'Dollar bank', 'kind':'bank', 'currency':'USD',
            'opening':'0.00', 'opening_date': self.date})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.compare(source, currency='USD', account_id=result.json()['id']).status_code, 409)
        with unit() as s:
            zuma = s.scalar(select(Company.id).where(Company.code == 'ZUMA'))
        self.assertEqual(self.compare(source, **{'X-Company-ID': str(zuma)}).status_code, 404)
        employee = self.make_user('employee', 'period_employee')
        try:
            self.assertEqual(self.compare(source, login=employee).status_code, 403)
        finally:
            employee[0].close()
        with unit() as s:
            uid = s.scalar(select(User.id).where(User.username == 'statement_payer'))
        def sections(version, mode):
            return self.client.put(f'/api/admin/users/{uid}/sections', headers=self.h,
                json={'company_id': self.company, 'version': version, 'sections': {'ledger':'view','requests':mode},
                      'reason':'Synthetic permission test for bank period'})
        setting = sections(0, 'view')
        self.assertEqual(setting.status_code, 200, setting.text)
        self.payer[0].close()
        self.payer = self.relogin('statement_payer')
        self.assertEqual(self.compare(source, login=self.payer).status_code, 200)
        self.assertEqual(sections(1, 'hidden').status_code, 200)
        self.payer[0].close()
        self.payer = self.relogin('statement_payer')
        self.assertEqual(self.compare(source, login=self.payer).status_code, 403)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(PeriodComparisonTests(name) for name in PeriodComparisonTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
