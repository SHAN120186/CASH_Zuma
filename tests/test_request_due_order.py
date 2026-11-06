"""Payment-date ordering remains deterministic within existing request boundaries."""
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from sqlalchemy import select

from tests.test_site import SiteTests
from app.db import Account, Audit, Category, Company, PaymentRequest, User, engine, now, unit


class RequestDueOrderTests(SiteTests):
    def setUp(self):
        super().setUp()
        self.day = date.fromisoformat(self.date)
        self.stamp = now()
        self.sequence = 0
        self.accounts = {(self.company, 'bank', 'UZS'): self.acc}
        with unit() as s:
            self.admin = s.scalar(select(User.id).where(User.username == 'admin'))
            self.foreign = s.scalar(select(Company.id).where(Company.code == 'ZUMA'))

    def seed(self, offset, *, company=None, status='draft', kind='bank', currency='UZS', creator=None,
             approved_age=None, tag='Synthetic due-order request'):
        company = company or self.company
        self.sequence += 1
        with unit(True) as s:
            category = s.scalar(select(Category).where(Category.company_id == company, Category.type == 'outcome'))
            if category is None:
                category = Category(company_id=company, name='Synthetic order expense', type='outcome', activity='operating')
                s.add(category);s.flush()
            account_key = (company, kind, currency)
            if account_key not in self.accounts:
                account = Account(company_id=company, name=f'Synthetic {company} {kind} {currency}', kind=kind,
                    currency=currency, opening=10000000, opening_date=self.day - timedelta(days=60), created_by=self.admin)
                s.add(account);s.flush();self.accounts[account_key] = account.id
            row = PaymentRequest(creator_id=creator or self.admin, category_id=category.id,
                account_id=self.accounts[account_key], counterparty=tag, purpose='Synthetic payment-date ordering',
                amount=1000, due_date=self.day + timedelta(days=offset), status=status,
                created_at=self.stamp + timedelta(minutes=self.sequence),
                approved_at=self.stamp - timedelta(hours=approved_age) if approved_age is not None else None)
            s.add(row);s.flush()
            return row.id

    def listing(self, *, company=None, client=None, headers=None, **params):
        response = (client or self.client).get('/api/requests', params=params,
            headers={**(headers or self.h), 'X-Company-ID': str(company or self.company)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_both_companies_sort_before_pagination_with_stable_same_date_ties(self):
        offsets = [3, 0, -9, 1, -1, 0, 30]
        expected = {self.company: [], self.foreign: []}
        for offset in offsets:
            for company in expected:
                expected[company].append((offset, self.seed(offset, company=company)))
        for company, pairs in expected.items():
            with self.subTest(company=company):
                ordered_ids = [rid for _, rid in sorted(pairs)]
                all_rows = self.listing(company=company)
                self.assertEqual([row['id'] for row in all_rows], ordered_ids)
                self.assertEqual([row['date'] for row in all_rows], sorted(row['date'] for row in all_rows))
                paged = []
                for page in range(1, 5):
                    result = self.listing(company=company, paginated=True, page=page, page_size=2)
                    self.assertEqual((result['total'], result['page'], result['page_size']), (7, page, 2))
                    paged.extend(row['id'] for row in result['items'])
                self.assertEqual(paged, ordered_ids)
                self.assertEqual(len(set(paged)), 7)
                self.assertEqual(self.listing(company=company, paginated=True, page=5, page_size=2)['items'], [])

    def test_period_archive_status_currency_and_deleted_filters_keep_existing_behavior(self):
        old = self.seed(-7, status='approved', approved_age=48)
        current = self.seed(0, status='approved', approved_age=2)
        paid = self.seed(7, status='paid', approved_age=2)
        draft = self.seed(-3)
        removed = self.seed(-30, status='deleted')
        usd = self.seed(1, status='pending', currency='USD', tag='Synthetic USD search target')
        cancelled = self.seed(-5, status='cancelled')
        with unit(True) as s:
            s.add(Audit(company_id=self.company, entity='request', entity_id=str(cancelled),
                action='Действие по заявке: cancel', created_at=self.stamp - timedelta(hours=48)))
        def ids(**params):
            return [row['id'] for row in self.listing(**params)]
        with patch('app.main.now', return_value=self.stamp):
            self.assertEqual(ids(), [old, cancelled, draft, current, usd, paid])
            # Approved-but-unpaid requests remain active regardless of age;
            # paid requests are archived immediately under the retention policy.
            self.assertEqual(ids(archive='active'), [old, draft, current, usd])
            self.assertEqual(ids(archive='archived'), [cancelled, paid])
            # Selecting a period retains both archived and active rows, as before.
            self.assertEqual(ids(archive='active', date_from=str(self.day - timedelta(days=7)), date_to=str(self.day)),
                             [old, cancelled, draft, current])
            self.assertEqual(ids(archive='archived', date_from=str(self.day)), [current, usd, paid])
            self.assertEqual(ids(state='approved'), [old, current])
            self.assertEqual(ids(currency='UZS'), [old, cancelled, draft, current, paid])
            self.assertEqual(ids(q='USD search target'), [usd])
        self.assertNotIn(removed, ids())
        invalid = self.client.get('/api/requests', params={'date_from': self.date, 'date_to': str(self.day - timedelta(days=1))}, headers=self.h)
        self.assertEqual(invalid.status_code, 422)

    def test_employee_and_cashier_visibility_stays_scoped_while_visible_rows_are_due_ordered(self):
        employee, eh = self.make_user('employee', 'synthetic_due_employee')
        cashier, ch = self.make_user('cashier', 'synthetic_due_cashier')
        try:
            with unit() as s:
                employee_id = s.scalar(select(User.id).where(User.username == 'synthetic_due_employee'))
                cashier_id = s.scalar(select(User.id).where(User.username == 'synthetic_due_cashier'))
            employee_future = self.seed(5, creator=employee_id)
            employee_overdue = self.seed(-2, creator=employee_id)
            bank = self.seed(-8, status='approved')
            cash_future = self.seed(3, status='paid', kind='cash')
            cash_overdue = self.seed(-1, status='approved', kind='cash')
            cash_pending = self.seed(-4, status='pending', kind='cash')
            own_bank = self.seed(0, creator=cashier_id)
            self.seed(-9, status='approved', kind='cash', company=self.foreign)
            own = self.listing(client=employee, headers=eh, paginated=True, page_size=1)
            next_page = self.listing(client=employee, headers=eh, paginated=True, page=2, page_size=1)
            self.assertEqual(own['total'], 2)
            self.assertEqual([own['items'][0]['id'], next_page['items'][0]['id']], [employee_overdue, employee_future])
            visible = self.listing(client=cashier, headers=ch)
            self.assertEqual([row['id'] for row in visible], [cash_overdue, own_bank, cash_future])
            self.assertFalse({bank, cash_pending, employee_overdue, employee_future} & {row['id'] for row in visible})
            denied = cashier.get('/api/requests', headers={**ch, 'X-Company-ID': str(self.foreign)})
            self.assertEqual(denied.status_code, 403)
        finally:
            employee.close();cashier.close()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(RequestDueOrderTests(name) for name in RequestDueOrderTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    engine.dispose()
