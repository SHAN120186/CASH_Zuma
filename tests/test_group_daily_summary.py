"""Daily group reports use disposable data and never contact Telegram or a real site."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

# Configure the existing disposable bot API database before importing the app.
from tests import test_bot_api as base
from sqlalchemy import select
from app.db import (Account, Audit, Category, Company, CompanyUser, Delegation, Ledger, PaymentRequest,
                    TelegramGroup, TelegramLink, User, engine, unit)


PATH = '/api/bot/v1/group-daily-summary'


def tearDownModule():
    engine.dispose()


class GroupDailySummaryTests(unittest.TestCase):
    # Reuse setup/helpers without inheriting the morning endpoint's test cases.
    setUp = base.BotApiTests.setUp
    tearDown = base.BotApiTests.tearDown
    account = base.BotApiTests.account
    post = base.BotApiTests.post
    ledger = base.BotApiTests.ledger
    zuma = base.BotApiTests.zuma
    category = base.BotApiTests.category

    def daily(self, chat=base.GROUP, day=None, auth=base.BOT):
        return self.client.get(PATH, params={'chat_id': chat, 'report_date': str(day or self.today)}, auth=auth)

    def data(self, **kwargs):
        response = self.daily(**kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def request_row(self, status, amount='100', due=None, account=None):
        with unit(True) as s:
            author = s.scalar(select(User).where(User.username == 'admin'))
            r = PaymentRequest(creator_id=author.id, account_id=account or self.acc, category_id=self.cat,
                               counterparty='Private supplier', purpose='Private request description',
                               amount=int(amount) * 100, due_date=due or self.today, status=status)
            s.add(r)
            s.flush()
            return r.id

    def linked_report_user(self, role='director', assignments=None, active=True, password_change=False):
        sequence = getattr(self, 'report_user_sequence', 0) + 1
        self.report_user_sequence = sequence
        telegram_id = 700000000 + sequence
        with unit(True) as s:
            user = User(username=f'report-user-{sequence}', name=f'Report User {sequence}', role=role,
                        password_hash=base.HASH, active=active, must_change_password=password_change)
            s.add(user)
            s.flush()
            for company_id, company_role in (assignments if assignments is not None else [(self.companies['UZGERMED'], role)]):
                s.add(CompanyUser(user_id=user.id, company_id=company_id, role=company_role))
            s.add(TelegramLink(user_id=user.id, telegram_user_id=telegram_id))
            return user.id, telegram_id

    def director_companies(self, telegram_id, auth=base.BOT):
        return self.client.get('/api/bot/v1/director-report-companies', params={'telegram_user_id': telegram_id}, auth=auth)

    def director_summary(self, telegram_id, company_id=None, day=None, auth=base.BOT):
        return self.client.get('/api/bot/v1/director-daily-summary', params={
            'telegram_user_id': telegram_id, 'company_id': company_id or self.companies['UZGERMED'],
            'report_date': str(day or self.today)}, auth=auth)

    def director_access(self, telegram_id):
        response = self.client.get('/api/bot/v1/access', params={'telegram_user_id': telegram_id}, auth=base.BOT)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['user']

    def test_service_auth_future_date_and_request_parameters(self):
        for auth in (None, ('test-bot', 'wrong-secret-' + 'y' * 30), ('other-bot', base.BOT[1])):
            response = self.daily(auth=auth)
            self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(self.client.get(PATH, params={'chat_id': base.GROUP}, auth=base.BOT).status_code, 422)
        self.assertEqual(self.daily(day=self.today + timedelta(days=1)).status_code, 422)

    def test_group_scope_is_one_active_company_in_every_section(self):
        foreign = self.account('Foreign company bank', 'bank', 'UZS', '777', headers=self.zuma())
        category = self.category(self.zuma())
        posted = self.post('/api/ledger', {'account_id': foreign, 'category_id': category, 'amount': '7', 'kind': 'in',
                                         'date': str(self.today), 'reference': 'FOREIGN-IN'}, headers=self.zuma())
        self.assertEqual(posted.status_code, 200, posted.text)
        self.ledger('12', 'in', 'OWN-IN', day=self.today)
        self.request_row('pending')
        for chat, code in ((base.GROUP, 'UZGERMED'), (base.GROUP_ZUMA, 'ZUMA')):
            data = self.data(chat=chat)
            self.assertEqual(data['companies'], [code])
            for section in ('balances', 'opening_balances', 'income', 'expense', 'operations', 'open_requests', 'warnings'):
                self.assertTrue(all(row['company_code'] == code for row in data[section]))
            names = {row['name'] for row in data['balances']}
            self.assertEqual('Foreign company bank' in names, code == 'ZUMA')
        for chat in (base.GROUP_BOTH, -1009999999999, 555001):
            self.assertEqual(self.daily(chat=chat).status_code, 403)
        with unit(True) as s:
            s.get(Company, self.companies['ZUMA']).active = False
        self.assertEqual(self.daily(chat=base.GROUP_BOTH).status_code, 403)
        self.assertEqual(self.daily(chat=base.GROUP_ZUMA).status_code, 403)

    def test_stored_registration_requires_active_admin_owner(self):
        chat = -1008888888888
        with unit(True) as s:
            owner = s.scalar(select(User).where(User.username == 'admin'))
            s.add(TelegramGroup(chat_id=chat, title='Synthetic group', companies='UZGERMED', added_by=owner.id))
        self.assertEqual(self.data(chat=chat)['companies'], ['UZGERMED'])
        with unit(True) as s:
            s.scalar(select(User).where(User.username == 'admin')).active = False
        self.assertEqual(self.daily(chat=chat).status_code, 403)

    def test_eight_am_current_balances_include_already_posted_today(self):
        self.ledger('100', 'in', 'YESTERDAY-IN')
        self.ledger('25', 'in', 'TODAY-IN', day=self.today)
        with patch('app.bot_api.now', return_value=datetime(2026, 9, 16, 3, 0)):
            data = self.data()
        self.assertEqual(data['balances'][0]['amount'], '1000125.00')
        self.assertEqual(data['opening_balances'][0]['amount'], '1000100.00')
        timestamp = datetime.fromisoformat(data['generated_at'])
        self.assertEqual(timestamp.utcoffset(), timedelta(0))
        self.assertEqual(timestamp.hour, 3)  # 08:00 Asia/Tashkent.
        self.assertEqual(data['report_date'], str(self.today))

    def test_same_date_movements_transfers_and_currencies_remain_separate(self):
        cash = self.account('Office cash', 'cash', 'UZS', '500')
        usd = self.account('USD bank', 'bank', 'USD', '1000')
        self.ledger('100', 'in', 'IN-TODAY', day=self.today)
        self.ledger('30', 'out', 'OUT-TODAY', day=self.today, account=cash)
        self.ledger('5', 'in', 'USD-IN', day=self.today, account=usd)
        self.ledger('20', 'transfer', 'TRANSFER-TODAY', day=self.today, to_account_id=cash, category_id=None)
        self.ledger('7', 'in', 'IN-YESTERDAY')
        data = self.data()
        self.assertEqual([(r['kind'], r['currency'], r['amount']) for r in data['income']],
                         [('bank', 'UZS', '100.00'), ('bank', 'USD', '5.00')])
        self.assertEqual([(r['kind'], r['currency'], r['amount']) for r in data['expense']], [('cash', 'UZS', '30.00')])
        self.assertEqual(len(data['operations']), 4)
        transfers = [r for r in data['operations'] if r['kind'] == 'transfer']
        self.assertEqual(len(transfers), 1)
        self.assertEqual((transfers[0]['account_name'], transfers[0]['to_account_name'], transfers[0]['amount']),
                         ('Test bank', 'Office cash', '20.00'))
        balances = {r['name']: r['amount'] for r in data['balances']}
        self.assertEqual(balances, {'Test bank': '1000087.00', 'Office cash': '490.00', 'USD bank': '1005.00'})
        self.assertTrue(all(r['date'] == str(self.today) for r in data['operations']))

    def test_reversals_remove_turnover_but_balance_uses_full_posting_journal(self):
        cash = self.account('Office cash', 'cash', 'UZS', '500')
        entries = [self.ledger('7', 'in', 'CORRECT-IN', day=self.today),
                   self.ledger('9', 'out', 'CORRECT-OUT', day=self.today),
                   self.ledger('11', 'transfer', 'CORRECT-TRANSFER', day=self.today, to_account_id=cash, category_id=None)]
        for entry in entries:
            response = self.post(f'/api/ledger/{entry}/reverse', {'reason': 'Synthetic correction for test'})
            self.assertEqual(response.status_code, 200, response.text)
        with unit() as s:
            self.assertEqual(len(list(s.scalars(select(Ledger)))), 6)
        data = self.data()
        self.assertEqual((data['income'], data['expense'], data['operations']), ([], [], []))
        self.assertEqual({r['name']: r['amount'] for r in data['balances']}, {'Test bank': '1000000.00', 'Office cash': '500.00'})

    def test_all_current_open_requests_include_future_due_approved(self):
        future = self.today + timedelta(days=10)
        pending = self.request_row('pending', '200')
        approved = self.request_row('approved', '300', due=future)
        overdue = self.request_row('approved', '400', due=self.yesterday)
        for status in ('draft', 'returned', 'paid', 'cancelled', 'rejected'):
            self.request_row(status)
        data = self.data(day=self.yesterday)
        rows = {r['number']: r for r in data['open_requests']}
        self.assertEqual(set(rows), {f'CF-{rid:05d}' for rid in (pending, approved, overdue)})
        self.assertEqual((rows[f'CF-{approved:05d}']['status'], rows[f'CF-{approved:05d}']['due_date']), ('approved', str(future)))
        self.assertEqual(rows[f'CF-{pending:05d}']['stage_label'], 'Проверка финансовым директором')
        self.assertEqual(rows[f'CF-{overdue:05d}']['stage_label'], f'Просрочено с {self.yesterday:%d.%m.%Y}')
        self.assertEqual(data['open_requests_as_of'], str(self.today))
        self.assertEqual(data['report_date'], str(self.yesterday))
        self.assertNotIn('Private supplier', str(data))
        self.assertNotIn('Private request description', str(data))

    def test_no_data_and_actual_zero_are_explicit(self):
        empty = self.data(chat=base.GROUP_ZUMA)
        for section in ('balances', 'opening_balances', 'income', 'expense', 'operations', 'open_requests', 'warnings'):
            self.assertEqual(empty[section], [])
        self.account('Zero balance', 'cash', 'UZS', '0', headers=self.zuma())
        zero = self.data(chat=base.GROUP_ZUMA)
        self.assertEqual(zero['balances'][0]['amount'], '0.00')
        self.assertEqual(zero['opening_balances'][0]['amount'], '0.00')
        self.assertNotEqual(empty['revision'], zero['revision'])

    def test_account_opened_today_and_archived_account_with_daily_activity(self):
        opened = self.account('Opened today', 'bank', 'EUR', '50', opened=self.today)
        cash = self.account('Closed cash', 'cash', 'UZS', '20')
        self.ledger('20', 'transfer', 'CLOSE-CASH', day=self.today, account=cash, to_account_id=self.acc, category_id=None)
        archived = self.post(f'/api/accounts/{cash}/archive', {'archived': True, 'reason': 'Synthetic closure'})
        self.assertEqual(archived.status_code, 200, archived.text)
        data = self.data()
        self.assertEqual({r['name']: r['amount'] for r in data['opening_balances']}['Opened today'], '50.00')
        self.assertEqual({r['name']: r['amount'] for r in data['balances']}['Closed cash'], '0.00')
        historical = self.data(day=self.yesterday)
        self.assertNotIn('Opened today', {r['name'] for r in historical['balances']})
        self.assertEqual({r['name']: r['amount'] for r in historical['balances']}['Closed cash'], '20.00')

    def test_financial_text_is_masked_in_all_new_sections(self):
        bank_number, card_number = '20208000900123456789', '8600123456789012'
        cash = self.account('Cash ' + card_number, 'cash', 'UZS', '100')
        with unit(True) as s:
            s.get(Account, self.acc).name = 'Bank ' + bank_number
            s.get(Category, self.cat).name = 'Category ' + card_number
        self.ledger('20', 'transfer', 'Reference ' + bank_number, day=self.today, to_account_id=cash,
                    category_id=None, note='Description ' + card_number)
        blank = self.ledger('5', 'in', 'TEMP-REFERENCE', day=self.today)
        with unit(True) as s:
            s.get(Ledger, blank).reference = ''
        self.request_row('pending')
        response = self.daily()
        self.assertEqual(response.status_code, 200, response.text)
        for sensitive in (bank_number, card_number):
            self.assertNotIn(sensitive, response.text)
        self.assertIn(f'CF-OP-{blank}', response.text)
        self.assertIn('••6789', response.text)
        self.assertIn('••9012', response.text)

    def test_revision_ignores_generation_time_but_tracks_posted_financial_edits(self):
        with patch('app.bot_api.now', return_value=datetime(2026, 9, 16, 3, 0)):
            first = self.data()
        with patch('app.bot_api.now', return_value=datetime(2026, 9, 16, 15, 0)):
            later = self.data()
        self.assertNotEqual(first['generated_at'], later['generated_at'])
        self.assertEqual(first['revision'], later['revision'])
        self.assertRegex(first['revision'], r'^[0-9a-f]{64}$')
        self.ledger('25', 'in', 'LATE-POSTING', day=self.today)
        changed = self.data()
        self.assertNotEqual(first['revision'], changed['revision'])
        self.assertEqual(changed['revision'], self.data()['revision'])
        self.request_row('pending')
        self.assertNotEqual(changed['revision'], self.data()['revision'])
        with unit() as s:
            actions = list(s.scalars(select(Audit).where(Audit.action == 'Бот: дневной отчёт группы')))
            self.assertTrue(actions)
            self.assertTrue(all(a.company_id == self.companies['UZGERMED'] for a in actions))

    def test_report_is_consistent_if_a_new_posting_commits_between_reads(self):
        from app import bot_api
        original_balance = bot_api.account_balance
        injected = False

        def balance_with_concurrent_posting(s, account, report_date):
            nonlocal injected
            if not injected:
                injected = True
                with unit(True) as writer:
                    author = writer.scalar(select(User).where(User.username == 'admin'))
                    writer.add(Ledger(account_id=self.acc, category_id=self.cat, kind='in', amount=500,
                                      date=self.today, reference='CONCURRENT-IN', creator_id=author.id))
            return original_balance(s, account, report_date)

        with patch('app.bot_api.account_balance', side_effect=balance_with_concurrent_posting):
            before = self.data()
        self.assertEqual(before['operations'], [])
        self.assertEqual(before['balances'][0]['amount'], '1000000.00')
        after = self.data()
        self.assertEqual(after['balances'][0]['amount'], '1000005.00')
        self.assertEqual(len(after['operations']), 1)
        self.assertNotEqual(before['revision'], after['revision'])

    def test_revision_tracks_financial_contents_independently_of_recipient_routing(self):
        migrated_chat = -1007777777777
        with unit(True) as s:
            owner = s.scalar(select(User).where(User.username == 'admin'))
            s.add(TelegramGroup(chat_id=migrated_chat, title='Synthetic migrated group', companies='UZGERMED', added_by=owner.id))
        original, migrated = self.data(), self.data(chat=migrated_chat)
        self.assertNotEqual(original['chat_id'], migrated['chat_id'])
        self.assertNotEqual(original['scope_id'], migrated['scope_id'])
        self.assertEqual(original['revision'], migrated['revision'])
        first_id, first_telegram = self.linked_report_user()
        second_id, second_telegram = self.linked_report_user()
        private_first = self.director_summary(first_telegram).json()
        private_second = self.director_summary(second_telegram).json()
        self.assertNotEqual(private_first['user_id'], private_second['user_id'])
        self.assertNotEqual(private_first['scope_id'], private_second['scope_id'])
        self.assertEqual(original['revision'], private_first['revision'])
        self.assertEqual(private_first['revision'], private_second['revision'])
        self.ledger('5', 'in', 'FINANCIAL-CHANGE', day=self.today)
        updated = self.data(chat=migrated_chat)
        self.assertNotEqual(migrated['revision'], updated['revision'])

    def test_private_director_report_uses_same_figures_with_explicit_user_scope(self):
        user_id, telegram_id = self.linked_report_user()
        self.ledger('12', 'in', 'DIRECTOR-IN', day=self.today)
        self.request_row('approved', due=self.today + timedelta(days=10))
        listing = self.director_companies(telegram_id)
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.json(), {'linked': True, 'user_id': user_id, 'companies': [
            {'id': self.companies['UZGERMED'], 'code': 'UZGERMED', 'name': 'UZGERMED'}]})
        access = self.director_access(telegram_id)
        self.assertEqual((access['user_id'], access['director_companies']), (user_id, ['UZGERMED']))
        response = self.director_summary(telegram_id)
        self.assertEqual(response.status_code, 200, response.text)
        private, group = response.json(), self.data()
        for section in ('report_date', 'companies', 'balances', 'opening_balances', 'income', 'expense',
                        'operations', 'open_requests', 'open_requests_as_of', 'warnings'):
            self.assertEqual(private[section], group[section])
        self.assertEqual((private['chat_id'], private['user_id']), (telegram_id, user_id))
        self.assertNotEqual(private['scope_id'], group['scope_id'])
        self.assertEqual(private['revision'], self.director_summary(telegram_id).json()['revision'])
        self.assertEqual(self.director_summary(telegram_id, self.companies['ZUMA']).status_code, 403)
        self.assertEqual(self.director_summary(telegram_id, day=self.today + timedelta(days=1)).status_code, 422)
        with unit() as s:
            rows = list(s.scalars(select(Audit).where(Audit.action == 'Бот: личный отчёт директора')))
            self.assertTrue(rows)
            self.assertTrue(all(a.user_id == user_id and a.entity_id == str(user_id) for a in rows))
            self.assertTrue(all(str(telegram_id) not in a.detail for a in rows))

    def test_private_reports_require_bot_auth_and_positive_recipient(self):
        _, telegram_id = self.linked_report_user()
        self.assertEqual(self.director_companies(telegram_id, auth=None).status_code, 401)
        self.assertEqual(self.director_summary(telegram_id, auth=None).status_code, 401)
        self.assertEqual(self.director_companies(-100123).status_code, 422)
        self.assertEqual(self.director_summary(-100123).status_code, 422)
        absent = 799999999
        self.assertEqual(self.director_companies(absent).json(), {'linked': False, 'user_id': None, 'companies': []})
        self.assertEqual(self.director_access(absent)['director_companies'], [])
        self.assertEqual(self.director_summary(absent).status_code, 403)

    def test_finance_export_admin_global_role_and_delegation_do_not_grant_private_reports(self):
        denied = []
        for role in ('finance', 'accountant', 'cashier', 'employee', 'investor', 'auditor',
                     'operator', 'material_accountant', 'procurement', 'sales'):
            denied.append(self.linked_report_user(role))
        denied.append(self.linked_report_user('admin', [(self.companies['UZGERMED'], 'finance')]))
        denied.append(self.linked_report_user('founder', [(self.companies['UZGERMED'], 'director')]))
        denied.append(self.linked_report_user('director', []))
        denied.append(self.linked_report_user('director', [(self.companies['UZGERMED'], 'finance')]))
        denied.append(self.linked_report_user('director', [(self.companies['UZGERMED'], None)]))
        replacement_id, replacement_telegram = self.linked_report_user('finance')
        director_id, _ = self.linked_report_user()
        with unit(True) as s:
            s.add(Delegation(company_id=self.companies['UZGERMED'], user_id=replacement_id, replaced_user_id=director_id,
                             role='director', starts_on=self.yesterday, ends_on=self.today + timedelta(days=2),
                             reason='Synthetic temporary delegation', created_by=director_id))
        denied.append((replacement_id, replacement_telegram))
        for user_id, telegram_id in denied:
            with self.subTest(user_id=user_id):
                listing = self.director_companies(telegram_id)
                self.assertEqual(listing.status_code, 200, listing.text)
                self.assertEqual(listing.json()['companies'], [])
                self.assertEqual(self.director_access(telegram_id)['director_companies'], [])
                self.assertEqual(self.director_summary(telegram_id).status_code, 403)

    def test_director_rights_stop_after_role_password_company_or_user_change(self):
        for change in ('role', 'password', 'company', 'user', 'membership', 'conflict'):
            with self.subTest(change=change):
                user_id, telegram_id = self.linked_report_user()
                self.assertEqual(self.director_summary(telegram_id).status_code, 200)
                with unit(True) as s:
                    user = s.get(User, user_id)
                    member = s.get(CompanyUser, (self.companies['UZGERMED'], user_id))
                    if change == 'role':
                        member.role = 'finance'
                    elif change == 'password':
                        user.must_change_password = True
                    elif change == 'company':
                        s.get(Company, self.companies['UZGERMED']).active = False
                    elif change == 'user':
                        user.active = False
                    elif change == 'membership':
                        s.delete(member)
                    else:
                        s.add(CompanyUser(user_id=user_id, company_id=self.companies['ZUMA'], role='director'))
                self.assertEqual(self.director_companies(telegram_id).json()['companies'], [])
                self.assertEqual(self.director_access(telegram_id)['director_companies'], [])
                self.assertEqual(self.director_summary(telegram_id).status_code, 403)
                if change == 'company':
                    with unit(True) as s:
                        s.get(Company, self.companies['UZGERMED']).active = True

    def test_director_scope_changes_when_telegram_link_moves_to_another_user(self):
        original_id, telegram_id = self.linked_report_user()
        replacement_id, other_telegram = self.linked_report_user()
        first = self.director_summary(telegram_id).json()
        other = self.director_summary(other_telegram).json()
        self.assertNotEqual(first['scope_id'], other['scope_id'])
        with unit(True) as s:
            s.delete(s.get(TelegramLink, original_id))
            s.flush()
            s.get(TelegramLink, replacement_id).telegram_user_id = telegram_id
        replaced = self.director_summary(telegram_id).json()
        self.assertEqual(replaced['user_id'], replacement_id)
        self.assertNotEqual(first['scope_id'], replaced['scope_id'])
        self.assertEqual(self.director_access(telegram_id)['user_id'], replacement_id)
        with unit(True) as s:
            s.delete(s.get(TelegramLink, replacement_id))
        self.assertEqual(self.director_companies(telegram_id).json()['linked'], False)
        self.assertEqual(self.director_access(telegram_id)['director_companies'], [])
        self.assertEqual(self.director_summary(telegram_id).status_code, 403)


if __name__ == '__main__':
    unittest.main(verbosity=2)
