"""Заявки, созданные или проведённые с датой раньше дня ввода, выделяются в списке (2.28.2).

python tests/test_backdated_requests.py
"""
import sys, unittest
from datetime import timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests.test_site as base
from sqlalchemy import select
from app.db import unit, User, PaymentRequest
from app.services import today, now


class BackdatedTests(base.SiteTests):
    def listed(self, rid):
        rows = self.client.get('/api/requests', headers=self.h).json()
        return next(r for r in rows if r['id'] == rid)

    def admin_id(self):
        with unit() as s:
            return s.scalar(select(User.id).where(User.username == 'admin').execution_options(company_unscoped=True))

    def test_request_created_after_its_planned_date_is_flagged(self):
        # Старые заявки (до правила сроков) вводились с датой раньше дня создания.
        with unit(True) as s:
            r = PaymentRequest(creator_id=self.admin_id(), last_editor_id=self.admin_id(), category_id=self.cat, account_id=self.acc,
                               counterparty='Old supplier', amount=100, due_date=today() - timedelta(days=3),
                               purpose='Старая заявка с прошлой датой', status='draft', priority='normal')
            s.add(r); s.flush(); rid = r.id
        row = self.listed(rid)
        self.assertTrue(row['backdated_created'])
        self.assertFalse(row['backdated_posted'])

    def test_request_created_on_time_is_not_flagged(self):
        rid = self.request('200').json()['id']
        row = self.listed(rid)
        self.assertFalse(row['backdated_created'])
        self.assertFalse(row['backdated_posted'])

    def test_payment_posted_after_its_factual_date_is_flagged(self):
        self.budget('100000', 'soft')
        rid = self.request('300').json()['id']
        self.approve(rid)
        # Утверждена три дня назад; оплата введена с датой позавчера, но проведена сегодня.
        with unit(True) as s:
            r = s.get(PaymentRequest, rid)
            r.approved_at = now() - timedelta(days=3)
        paid = self.ledger('300.00', 'out', 'PAY-BACK', request_id=rid, date=str(today() - timedelta(days=2)))
        self.assertEqual(paid.status_code, 200, paid.text)
        row = self.listed(rid)
        self.assertEqual(row['status'], 'paid')
        self.assertTrue(row['backdated_posted'])
        self.assertFalse(row['backdated_created'])

    def test_payment_posted_on_its_own_day_is_not_flagged(self):
        self.budget('100000', 'soft')
        rid = self.request('250').json()['id']
        self.approve(rid)
        paid = self.ledger('250.00', 'out', 'PAY-SAME-DAY', request_id=rid)
        self.assertEqual(paid.status_code, 200, paid.text)
        self.assertFalse(self.listed(rid)['backdated_posted'])


for _name in dir(base.SiteTests):
    if _name.startswith('test'):
        setattr(BackdatedTests, _name, None)


def tearDownModule():
    base.tearDownModule()


if __name__ == '__main__':
    unittest.main(verbosity=2)
