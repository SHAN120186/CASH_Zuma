"""Company rules classify eligible synthetic bank facts without minting duplicates."""
import json
import unittest
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

import tests.test_bank_statement_auto_integration as base
from app.bank_statement_models import BankStatementBatch, BankStatementLine
from app.bank_statement_unallocated import category_id as unallocated_category_id, ensure_category
from app.db import Account, Audit, Category, Company, Ledger, PaymentRequest, Receipt, Setting, User, engine, unit
from app.services import account_balance, money

URL = '/api/bank-statements/category-rules'


class CategoryRuleTests(unittest.TestCase):
    def __getattr__(self, name):
        fixture = self.__dict__.get('_site')
        if fixture is not None:
            return getattr(fixture, name)
        raise AttributeError(name)

    def setUp(self):
        self._site = base.AutoBankIntegrationTests()
        self._site.setUp()
        self.day = date.fromisoformat(self.date)
        with unit(True) as s:
            self.admin_id = s.scalar(select(User.id).where(User.username == 'admin'))
            self.foreign = s.scalar(select(Company.id).where(Company.code == 'ZUMA'))
            self.income = self.category(s, self.company, 'income', 'Synthetic rule receipts')
            self.expense = self.category(s, self.company, 'outcome', 'Synthetic rule expenses')
            self.other_income = self.category(s, self.company, 'income', 'Synthetic receipt priority')
            self.other_expense = self.category(s, self.company, 'outcome', 'Synthetic second expenses')

    def tearDown(self):
        self._site.tearDown()

    def category(self, s, company, kind, name):
        item = Category(company_id=company, name=name, type=kind, activity='operating')
        s.add(item);s.flush()
        return item.id

    def account(self, s, company=None, opening=100000000):
        self._account_number = self.__dict__.get('_account_number', 0) + 1
        item = Account(company_id=company or self.company, name=f'Synthetic rule bank {self._account_number}', kind='bank', currency='UZS',
            opening=opening, opening_date=self.day - timedelta(days=10), created_by=self.admin_id)
        s.add(item);s.flush()
        return item.id

    def headers(self, company=None, headers=None):
        return {**(headers or self.h), 'X-Company-ID': str(company or self.company)}

    def rules(self, company=None, client=None, headers=None):
        result = (client or self.client).get(URL, headers=self.headers(company, headers))
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def rule(self, direction, category, **criteria):
        return {'direction': direction, 'category_id': category, 'enabled': True, **criteria}

    def save(self, items, *, company=None, revision=None, client=None, headers=None):
        if revision is None:
            revision = self.rules(company, client, headers)['revision']
        return (client or self.client).put(URL, headers=self.headers(company, headers), json={'revision': revision, 'items': items})

    def row(self, doc, direction, value, inn='300000005', name='Synthetic supplier', purpose='Synthetic goods purchase', account=None):
        return {'document': doc, 'direction': direction, 'amount': value, 'inn': inn, 'name': name,
            'purpose': purpose, 'account': account or '20200000000000000002'}

    def upload(self, rows, *, account=None, company=None, client=None, headers=None, fee=None):
        account = account or self.acc
        with unit() as s:opening = Decimal(money(s.get(Account, account).opening))
        incoming = sum((Decimal(row['amount']) for row in rows if row['direction'] == 'in'), Decimal(0))
        outgoing = sum((Decimal(row['amount']) for row in rows if row['direction'] == 'out'), Decimal(0))
        day = self.day.strftime('%d.%m.%Y')
        raw = (f'<html><body><table><tr><td>Сведения о работе счета c {day} по {day}</td></tr>'
            f'<tr><td>Cчет: {20200000000010000000 + account} Synthetic Company ИНН: 300000001</td></tr>'
            f'<tr><td>Остаток на начало периода: {opening:.2f}</td><td>Остаток на конец периода: {opening+incoming-outgoing:.2f}</td></tr>'
            '</table><table><tr><th>Дата</th><th>Номер документа</th><th>МФО корресп.</th><th>Счет корреспондента</th>'
            '<th>Наименование корресп.</th><th>ИНН корреспондента</th><th>Назначение платежа</th><th>Дебет</th><th>Кредит</th></tr>')
        for row in rows:
            debit = row['amount'] if row['direction'] == 'out' else ''
            credit = row['amount'] if row['direction'] == 'in' else ''
            raw += (f'<tr><td>{day}</td><td>{row["document"]}</td><td>00014</td><td>{row["account"]}</td>'
                f'<td>{row["name"]}</td><td>{row["inn"]}</td><td>{row["purpose"]}</td><td>{debit}</td><td>{credit}</td></tr>')
        raw += f'<tr><td>Итоговый оборот</td><td>{outgoing:.2f}</td><td>{incoming:.2f}</td></tr></table></body></html>'
        params = {'account_id': account, 'currency': 'UZS'}
        if fee is not None:params['fee_category_id'] = fee
        self.last_upload = (raw.encode('cp1251'), params, self.headers(company, headers))
        result = (client or self.client).post('/api/bank-statements/preview', params=params,
            content=self.last_upload[0], headers={**self.last_upload[2], 'X-Filename': 'synthetic-rules.html'})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def confirm(self, checked, *, company=None, client=None, headers=None, fee=None):
        return (client or self.client).post(f'/api/bank-statements/{checked["id"]}/commit', headers=self.headers(company, headers),
            json={'confirm_account': True, 'confirm_currency': True, 'fee_category_id': fee, 'preview_token': checked['preview_token']})

    def financial_snapshot(self):
        with unit() as s:
            return {model.__tablename__: [tuple(getattr(row, column.key) for column in model.__table__.columns)
                for row in s.scalars(select(model).order_by(*model.__mapper__.primary_key))]
                for model in (Account, Category, Ledger, PaymentRequest, Receipt)}

    def ledger_for(self, document):
        with unit() as s:
            line = s.scalar(select(BankStatementLine).where(BankStatementLine.document_number == document))
            return s.get(Ledger, line.ledger_id)

    def budget_for(self, amount, mode='hard'):
        result = self._site.post('/api/budgets', {'category_id': self.expense, 'month': self.month, 'currency': 'UZS',
            'amount': amount, 'mode': mode, 'reason': 'Synthetic rule budget validation', 'source': 'test'})
        self.assertEqual(result.status_code, 200, result.text)

    def test_rules_manage_cas_and_company_scope_without_financial_writes(self):
        initial = self.rules()
        self.assertEqual(initial['items'], [])
        self.assertTrue(initial['can_manage'])
        before = self.financial_snapshot()
        saved = self.save([self.rule('in', self.income, inn='300000003')], revision=initial['revision'])
        self.assertEqual(saved.status_code, 200, saved.text)
        current = saved.json()
        self.assertEqual(len(current['items'][0]['id']), 32)
        self.assertNotEqual(current['revision'], initial['revision'])
        self.assertEqual(self.save([], revision=initial['revision']).status_code, 409)
        self.assertEqual(self.save(current['items']).json()['revision'], current['revision'])
        self.assertEqual(self.financial_snapshot(), before)
        self.assertEqual(self.rules(self.foreign)['items'], [])
        self.assertEqual(self.save(current['items'], company=self.foreign).status_code, 422)
        payer, ph = self._site.accountant
        self.assertFalse(self.rules(client=payer, headers=ph)['can_manage'])
        self.assertEqual(self.save(current['items'], client=payer, headers=ph).status_code, 403)
        self.assertEqual(self.client.put(URL, json={'revision': current['revision'], 'items': []}).status_code, 403)
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Audit).where(Audit.action == 'Изменены правила статей банковской выписки')))), 1)

    def test_invalid_targets_and_weak_or_duplicate_criteria_are_rejected(self):
        with unit(True) as s:
            foreign_cat = self.category(s, self.foreign, 'income', 'Synthetic foreign income')
            s.info['company_id'] = self.company
            technical = ensure_category(s, 'in')
            s.merge(Setting(key=f'company:{self.company}:category_catalog', value=json.dumps({'retired': [self.other_income]})))
        invalid = [self.rule('in', self.expense, inn='300000003'), self.rule('in', foreign_cat, inn='300000003'),
            self.rule('in', technical, inn='300000003'), self.rule('in', self.other_income, inn='300000003'),
            self.rule('in', 999999, inn='300000003'), self.rule('in', self.income),
            self.rule('in', self.income, purpose_contains='00030'), self.rule('in', self.income, inn='1'),
            self.rule('in', self.income, purpose_contains='buy')]
        for item in invalid:
            with self.subTest(item=item):self.assertEqual(self.save([item]).status_code, 422)
        same = self.rule('in', self.income, id='same', inn='300000003')
        self.assertEqual(self.save([same, same]).status_code, 422)
        accepted = self.save([self.rule('in', self.income, contract_contains='130-26')])
        self.assertEqual(accepted.status_code, 200, accepted.text)

    def test_both_company_directions_all_criteria_unknowns_totals_and_retries(self):
        for company in (self.company, self.foreign):
            with self.subTest(company=company):
                with unit(True) as s:
                    incoming_cat = self.category(s, company, 'income', f'Synthetic receipts {company}')
                    outgoing_cat = self.category(s, company, 'outcome', f'Synthetic expenses {company}')
                    account = self.acc if company == self.company else self.account(s, company)
                configured = self.save([
                    self.rule('in', incoming_cat, inn='300000003', counterparty='SYNTHETIC BUYER', currency='UZS',
                              purpose_contains='invoice', contract_contains='130-26'),
                    self.rule('out', outgoing_cat, inn='300000005', counterparty='Synthetic supplier',
                              purpose_contains='goods', contract_contains='55-2026')], company=company)
                self.assertEqual(configured.status_code, 200, configured.text)
                before = self.financial_snapshot()
                rows = [self.row(f'IN-{company}', 'in', '200.00', '300000003', 'Synthetic   Buyer', 'Invoice №130-26 materials'),
                    self.row(f'OUT-{company}', 'out', '75.00', purpose='Goods contract 55-2026'),
                    self.row(f'UNKNOWN-IN-{company}', 'in', '30.00', '300000004', 'Unknown buyer', 'Other sale'),
                    self.row(f'UNKNOWN-OUT-{company}', 'out', '20.00', '300000006', 'Unknown supplier', 'Other goods')]
                checked = self.upload(rows, account=account, company=company)
                self.assertEqual(self.financial_snapshot(), before)
                self.assertEqual(checked['category_rules_revision'], configured.json()['revision'])
                self.assertEqual([row.get('category_source') for row in checked['rows']],
                                 ['bank_rule', 'bank_rule', 'unclassified', 'unclassified_out'])
                done = self.confirm(checked, company=company)
                self.assertEqual(done.status_code, 200, done.text);self.assertTrue(done.json()['full_reconciliation'])
                self.assertEqual(self.ledger_for(f'IN-{company}').category_id, incoming_cat)
                self.assertEqual(self.ledger_for(f'OUT-{company}').category_id, outgoing_cat)
                with unit() as s:
                    entries = list(s.scalars(select(Ledger).where(Ledger.account_id == account)))
                    self.assertEqual(len(entries), 4);self.assertEqual(len({entry.reference for entry in entries}), 4)
                    self.assertEqual(account_balance(s, s.get(Account, account)), 100013500)
                    self.assertTrue(all(s.get(Category, self.ledger_for(doc).category_id).activity == 'unclassified'
                        for doc in (f'UNKNOWN-IN-{company}', f'UNKNOWN-OUT-{company}')))
                self.assertEqual(self.confirm(checked, company=company).status_code, 409)
                raw, params, headers = self.last_upload
                self.assertEqual(self.client.post('/api/bank-statements/preview', params=params, content=raw,
                    headers={**headers, 'X-Filename': 'synthetic-rules.html'}).status_code, 409)
                refresh = self.client.post(f'/api/bank-statements/{checked["id"]}/reprocess/preview', json={}, headers=self.headers(company))
                self.assertEqual(refresh.status_code, 200, refresh.text)
                retry = self.client.post(f'/api/bank-statements/{checked["id"]}/reprocess/commit',
                    json={'preview_token': refresh.json()['preview_token'], 'confirm': True}, headers=self.headers(company))
                self.assertEqual(retry.status_code, 200, retry.text);self.assertEqual(retry.json()['posted'], 0)

    def test_conflicting_rules_stay_unallocated_and_agreeing_rules_share_one_article(self):
        saved = self.save([self.rule('in', self.income, inn='300000003'),
            self.rule('in', self.other_income, inn='300000003'),
            self.rule('out', self.expense, inn='300000005'),
            self.rule('out', self.expense, purpose_contains='goods')])
        self.assertEqual(saved.status_code, 200, saved.text)
        checked = self.upload([self.row('CONFLICT-IN', 'in', '200', '300000003', 'Synthetic buyer'), self.row('AGREE-OUT', 'out', '75')])
        self.assertEqual(checked['rows'][0]['category_rule_status'], 'conflict')
        self.assertEqual(checked['rows'][0]['category_source'], 'unclassified')
        self.assertEqual(checked['rows'][1]['category_id'], self.expense)
        self.assertEqual(len(checked['rows'][1]['category_rule_ids']), 2)
        done = self.confirm(checked)
        self.assertEqual(done.status_code, 200, done.text)
        incoming_entry = self.ledger_for('CONFLICT-IN')
        with unit() as s:self.assertEqual(s.get(Category, incoming_entry.category_id).activity, 'unclassified')
        self.assertEqual(self.ledger_for('AGREE-OUT').category_id, self.expense)

    def test_rule_changes_even_no_match_or_aba_invalidate_the_signed_confirmation(self):
        checked = self.upload([self.row('STALE-IN', 'in', '200', '300000003', 'Synthetic buyer')])
        changed = self.save([self.rule('in', self.income, inn='999999999')])
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(self.confirm(checked).status_code, 409)
        fresh = self.client.post(f'/api/bank-statements/{checked["id"]}/resume', json={}, headers=self.h).json()
        policy_a = changed.json()
        disabled = [{**item, 'enabled': False} for item in policy_a['items']]
        policy_b = self.save(disabled).json()
        policy_a_again = self.save(policy_a['items']).json()
        self.assertNotEqual(policy_a_again['revision'], policy_a['revision'])
        self.assertEqual(self.save(policy_a['items'], revision=policy_a['revision']).status_code, 409)
        self.assertEqual(self.confirm(fresh).status_code, 409)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))), 0)

    def test_existing_manual_articles_take_priority_and_reversed_rows_cannot_be_reposted(self):
        self.assertEqual(self.save([self.rule('in', self.income, inn='300000003'),
            self.rule('out', self.expense, inn='300000005')]).status_code, 200)
        with unit(True) as s:
            for kind, amount, category, name, document in (
                ('in', 20000, self.other_income, 'Synthetic buyer', 'EXISTING-IN'),
                ('out', 7500, self.other_expense, 'Synthetic supplier', 'EXISTING-OUT')):
                s.add(Ledger(account_id=self.acc, category_id=category, kind=kind, amount=amount,
                    date=self.day, counterparty=name, reference=document, note='Synthetic prior fact', creator_id=self.admin_id))
        checked = self.upload([self.row('EXISTING-IN', 'in', '200', '300000003', 'Synthetic buyer'),
            self.row('EXISTING-OUT', 'out', '75')])
        self.assertEqual([row['status'] for row in checked['rows']], ['income_matched_candidate', 'other_matched_candidate'])
        self.assertEqual([row['category_id'] for row in checked['rows']], [self.other_income, self.other_expense])
        done = self.confirm(checked)
        self.assertEqual(done.status_code, 200, done.text);self.assertTrue(done.json()['full_reconciliation'])
        with unit(True) as s:
            entry = s.get(Ledger, self.ledger_for('EXISTING-OUT').id)
            s.add(Ledger(account_id=self.acc, category_id=entry.category_id, kind='in', amount=entry.amount,
                date=self.day, counterparty='', reference='REVERSE-EXISTING', note='Synthetic reversal',
                creator_id=self.admin_id, reversal_of=entry.id))
        refreshed = self.client.post(f'/api/bank-statements/{checked["id"]}/reprocess/preview', json={}, headers=self.h)
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual(refreshed.json()['rows'][1]['status'], 'review')
        repeated = self.client.post(f'/api/bank-statements/{checked["id"]}/reprocess/commit',
            json={'preview_token': refreshed.json()['preview_token'], 'confirm': True}, headers=self.h)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 3)
            self.assertEqual(s.get(Ledger, entry.id).category_id, self.other_expense)

    def test_retired_category_stales_preview_and_corrupt_rules_fail_closed_without_preview_writes(self):
        self.assertEqual(self.save([self.rule('in', self.income, inn='300000003')]).status_code, 200)
        checked = self.upload([self.row('RETIRED-IN', 'in', '200', '300000003', 'Synthetic buyer')])
        with unit(True) as s:
            s.merge(Setting(key=f'company:{self.company}:category_catalog', value=json.dumps({'retired': [self.income]})))
        self.assertEqual(self.confirm(checked).status_code, 409)
        self.assertFalse(self.rules()['items'][0]['category_valid'])
        refreshed = self.client.post(f'/api/bank-statements/{checked["id"]}/resume', json={}, headers=self.h)
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual(refreshed.json()['rows'][0]['category_rule_status'], 'invalid')
        self.assertEqual(refreshed.json()['rows'][0]['category_source'], 'unclassified')
        with unit(True) as s:
            s.get(Setting, f'company:{self.company}:bank_statement_category_rules').value = '{broken synthetic configuration'
        before = self.financial_snapshot()
        broken = self.rules()
        self.assertEqual(broken['items'], []);self.assertIn('повреждены', broken['warning'])
        safe = self.client.post(f'/api/bank-statements/{checked["id"]}/resume', json={}, headers=self.h)
        self.assertEqual(safe.status_code, 200, safe.text)
        self.assertEqual(safe.json()['rows'][0]['category_source'], 'unclassified')
        self.assertIn('повреждены', safe.json()['category_rules_warning'])
        self.assertEqual(self.financial_snapshot(), before)
        repaired = self.save([], revision=broken['revision'])
        self.assertEqual(repaired.status_code, 200, repaired.text);self.assertEqual(repaired.json()['warning'], '')

    def test_bank_accountant_can_apply_signed_approved_rules_without_rule_management(self):
        self.assertEqual(self.save([self.rule('in', self.income, inn='300000003'),
            self.rule('out', self.expense, inn='300000005')]).status_code, 200)
        payer, ph = self._site.accountant
        checked = self.upload([self.row('PAYER-IN', 'in', '200', '300000003', 'Synthetic buyer'),
            self.row('PAYER-OUT', 'out', '75')], client=payer, headers=ph)
        self.assertTrue(all(row['category_source'] == 'bank_rule' for row in checked['rows']))
        done = self.confirm(checked, client=payer, headers=ph)
        self.assertEqual(done.status_code, 200, done.text);self.assertTrue(done.json()['full_reconciliation'])
        self.assertEqual(self.ledger_for('PAYER-IN').category_id, self.income)
        self.assertEqual(self.ledger_for('PAYER-OUT').category_id, self.expense)

    def test_request_receipt_fee_and_interest_categories_have_priority_over_rules(self):
        rid = self._site.approved()
        with unit(True) as s:
            receipt = Receipt(account_id=self.acc, category_id=self.other_income, counterparty='Synthetic buyer', inn='300000003',
                amount=20000, due_date=self.day, creator_id=self.admin_id, note='Synthetic expected receipt')
            s.add(receipt);s.flush();receipt_id = receipt.id
        saved = self.save([self.rule('in', self.income, inn='300000003'),
            self.rule('out', self.expense, inn='300000002'), self.rule('out', self.expense, inn='300000001')])
        self.assertEqual(saved.status_code, 200, saved.text)
        rows = [self.row('PLAN-IN', 'in', '200', '300000003', 'Synthetic buyer'),
            self.row('REQUEST-OUT', 'out', '600', '300000002', 'Supplier', 'Synthetic order'),
            self.row('FEE-OUT', 'out', '20', '300000001', 'Synthetic bank', '00011 Погаш. ком. SWIFT - synthetic'),
            self.row('INTEREST-OUT', 'out', '30', '300000001', 'Synthetic bank', '01014кредит фоизи учун толов', account='16309000000000000001')]
        checked = self.upload(rows, fee=self.cat, client=self.accountant[0], headers=self.accountant[1])
        self.assertEqual([row['status'] for row in checked['rows']], ['income_candidate', 'auto_pay_candidate', 'fee_candidate', 'interest_candidate'])
        self.assertEqual(checked['rows'][0]['category_id'], self.other_income)
        self.assertTrue(all(row.get('category_source') != 'bank_rule' for row in checked['rows']))
        done = self.confirm(checked, fee=self.cat, client=self.accountant[0], headers=self.accountant[1])
        self.assertEqual(done.status_code, 200, done.text);self.assertTrue(done.json()['full_reconciliation'])
        self.assertEqual(self.ledger_for('PLAN-IN').category_id, self.other_income)
        self.assertEqual(self.ledger_for('REQUEST-OUT').category_id, self.cat)
        self.assertEqual(self.ledger_for('FEE-OUT').category_id, self.cat)
        self.assertNotEqual(self.ledger_for('INTEREST-OUT').category_id, self.expense)
        with unit() as s:
            self.assertEqual(s.get(PaymentRequest, rid).status, 'paid')
            self.assertEqual(s.get(Receipt, receipt_id).status, 'received')

    def test_rules_do_not_unlock_own_transfers_duplicates_or_a_possible_request(self):
        draft = self._site.request('75', submit=False).json()['id']
        with unit(True) as s:s.get(PaymentRequest, draft).counterparty_inn = '300000005'
        self.assertEqual(self.save([self.rule('in', self.income, inn='300000003'),
            self.rule('out', self.expense, inn='300000001'), self.rule('out', self.expense, inn='300000005')]).status_code, 200)
        duplicate = self.row('DUP-IN', 'in', '100', '300000003', 'Synthetic buyer')
        checked = self.upload([duplicate, duplicate, self.row('OWN-OUT', 'out', '50', '300000001', 'Synthetic Company', 'Own account transfer'),
            self.row('REQUEST-CANDIDATE', 'out', '75')])
        self.assertEqual([row['status'] for row in checked['rows']], ['ambiguous_duplicate', 'ambiguous_duplicate', 'review', 'review'])
        self.assertTrue(all(row.get('category_source') != 'bank_rule' for row in checked['rows']))
        done = self.confirm(checked)
        self.assertEqual(done.status_code, 200, done.text)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))), 0)

    def test_hard_and_soft_overruns_fallback_once_to_unallocated_with_visible_audit(self):
        self.assertEqual(self.save([self.rule('out', self.expense, inn='300000005')]).status_code, 200)
        for mode in ('hard', 'soft'):
            with self.subTest(mode=mode):
                self.budget_for('50', mode)
                with unit(True) as s:account = self.account(s)
                checked = self.upload([self.row(f'OVER-{mode}', 'out', '75')], account=account)
                self.assertTrue(checked['rows'][0]['category_rule_budget_over'])
                self.assertEqual(checked['rows'][0]['category_rule_budget_policy'], 'unallocated_on_budget_overrun')
                done = self.confirm(checked)
                self.assertEqual(done.status_code, 200, done.text);self.assertTrue(done.json()['full_reconciliation'])
                entry = self.ledger_for(f'OVER-{mode}')
                with unit() as s:
                    self.assertEqual(s.get(Category, entry.category_id).activity, 'unclassified')
                    self.assertEqual(account_balance(s, s.get(Account, account)), 99992500)
                history = self.client.get(f'/api/bank-statements/history/{checked["id"]}', headers=self.h)
                self.assertEqual(history.status_code, 200, history.text)
                row = history.json()['rows'][0]
                self.assertEqual(row['category_id'], entry.category_id)
                self.assertEqual(row['category_rule_status'], 'budget_fallback')
                self.assertEqual(row['intended_category_name'], 'Synthetic rule expenses')
                self.assertIn('Превышен бюджет', row['category_rule_fallback_reason'])
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 2)
            self.assertEqual(len(list(s.scalars(select(Ledger).where(Ledger.category_id == self.expense)))), 0)

    def test_aggregate_budget_is_rechecked_for_each_row_and_does_not_stale_its_own_batch(self):
        self.budget_for('100')
        self.assertEqual(self.save([self.rule('out', self.expense, inn='300000005')]).status_code, 200)
        checked = self.upload([self.row('FIRST-OUT', 'out', '60'), self.row('SECOND-OUT', 'out', '60')])
        self.assertTrue(all(not row['category_rule_budget_over'] for row in checked['rows']))
        done = self.confirm(checked)
        self.assertEqual(done.status_code, 200, done.text);self.assertTrue(done.json()['full_reconciliation'])
        self.assertEqual(self.ledger_for('FIRST-OUT').category_id, self.expense)
        second = self.ledger_for('SECOND-OUT')
        with unit() as s:
            self.assertEqual(s.get(Category, second.category_id).activity, 'unclassified')
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 2)
        report = self.client.get('/api/report', params={'year': self.day.year, 'currency': 'UZS'}, headers=self.h)
        self.assertEqual(report.status_code, 200, report.text)
        self.assertEqual(report.json()['totals'][self.day.month - 1], '-120.00')

    def test_budget_fallback_does_not_bypass_funds_and_existing_rows_are_not_recategorized(self):
        self.budget_for('50')
        self.assertEqual(self.save([self.rule('out', self.expense, inn='300000005')]).status_code, 200)
        with unit(True) as s:
            account = self.account(s, opening=10000)
            s.add(PaymentRequest(creator_id=self.admin_id, account_id=account, category_id=self.cat,
                counterparty='Synthetic reserved supplier', amount=5000, due_date=self.day, purpose='Synthetic reserve', status='approved'))
        checked = self.upload([self.row('FUNDS-BLOCKED', 'out', '75')], account=account)
        done = self.confirm(checked)
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(done.json()['auto_outgoing'], 0)
        with unit() as s:self.assertEqual(len(list(s.scalars(select(Ledger)))), 0)
        # A previously posted fact remains in its actual article on rule edits.
        with unit(True) as s:other = self.account(s)
        self.budget_for('1000')
        existing = self.upload([self.row('ALREADY-POSTED', 'out', '75')], account=other)
        self.assertEqual(self.confirm(existing).status_code, 200)
        entry = self.ledger_for('ALREADY-POSTED')
        self.assertEqual(entry.category_id, self.expense)
        self.assertEqual(self.save([self.rule('out', self.other_expense, inn='300000005')]).status_code, 200)
        history = self.client.get(f'/api/bank-statements/history/{existing["id"]}', headers=self.h)
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()['rows'][0]['category_id'], self.expense)
        recheck = self.client.post(f'/api/bank-statements/{existing["id"]}/reprocess/preview', json={}, headers=self.h)
        self.assertEqual(recheck.status_code, 200, recheck.text)
        self.assertEqual(recheck.json()['rows'][0]['category_id'], self.expense)
        self.assertEqual(self.ledger_for('ALREADY-POSTED').category_id, self.expense)


def tearDownModule():
    engine.dispose()
