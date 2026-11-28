"""Синтетическое казначейство: корпоративная карта, конвертация через блочный счёт и частичная оплата."""
import unittest
from datetime import timedelta

from sqlalchemy import func, select

import tests.test_site as base
from app.db import Account, Category, Ledger, PaymentRequest, unit
from app.services import account_balance, today
from app.treasury_models import CardPayment, CurrencyConversion, PaymentSplit


class TreasuryTests(base.SiteTests):
    # ---------------------------------------------------------------- helpers
    def account(self, name, kind, currency, opening):
        response = self.post('/api/accounts', {'name': name, 'kind': kind, 'currency': currency, 'opening': opening,
                                               'opening_date': str(today() - timedelta(days=10))})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def fee_category(self, name='Комиссия за оплату корп. картой'):
        response = self.post('/api/categories', {'name': name, 'type': 'outcome', 'activity': 'operating'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def balance(self, account_id):
        with unit() as s:
            return account_balance(s, s.get(Account, account_id))

    def payer_client(self):
        if not hasattr(self, 'payer'):
            self.payer = self.make_user('accountant', 'payer')
        return self.payer

    def treasury(self, rid, client=None):
        client = client or (self.client, self.h)
        response = client[0].get(f'/api/treasury/requests/{rid}', headers=client[1])
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def approved(self, rid, docs=('internal',)):
        for kind in docs:
            self.assertEqual(self.upload(rid, kind).status_code, 200)
        response = self.post(f'/api/requests/{rid}/decision', {'action': 'submit'})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.approve(rid)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.row(rid)['status'], 'approved')

    def card_request(self, n='1000000', fee_percent='1', opening='5000000.00', submit=True):
        if not hasattr(self, 'card'):
            self.card = self.account('Корп. карта Humo •• 4417', 'card', 'UZS', opening)
            self.card_fee = self.fee_category()
        body = self.request_body(n, purpose='Оплата хостинга корпоративной картой', account=self.card)
        body['card'] = {'fee_percent': fee_percent, 'fee_category_id': self.card_fee}
        response = self.post('/api/requests', body)
        self.assertEqual(response.status_code, 200, response.text)
        rid = response.json()['id']
        if submit:
            self.approved(rid)
        return rid

    def card_pay(self, rid, fee=None, client=None, reference='SLIP-1', note=''):
        view = self.treasury(rid)['card']
        payload = {'version': view['version'], 'request_version': self.version_of(rid), 'date': self.date,
                   'reference': reference, 'fee_amount': fee if fee is not None else view['fee_amount'], 'note': note}
        return self.post(f'/api/treasury/requests/{rid}/card-pay', payload, *(client or self.payer_client()))

    def fx_setup(self):
        self.uzs = self.account('Сумовой расчётный счёт', 'bank', 'UZS', '200000000.00')
        self.usd = self.account('Валютный счёт USD', 'bank', 'USD', '0.00')
        self.fx_fee = self.fee_category('Комиссия банка за конвертацию')

    def fx_request(self, n='10000'):
        body = self.request_body(n, purpose='Оплата инвойса INV-77 нерезиденту', account=self.usd, counterparty='Supplier GmbH')
        response = self.post('/api/requests', body)
        self.assertEqual(response.status_code, 200, response.text)
        rid = response.json()['id']
        self.approved(rid, ('internal', 'contract'))
        return rid

    def plan(self, rid, amount='10000', rate='12850.50', fee='0.5'):
        response = self.post(f'/api/treasury/requests/{rid}/conversions', {
            'request_version': self.version_of(rid), 'source_account_id': self.uzs, 'amount': amount, 'rate': rate,
            'fee_percent': fee, 'fee_category_id': self.fx_fee})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['fx']['conversions'][-1]

    def step(self, conversion, action, client=None, **body):
        payload = {'version': conversion['version'], 'date': self.date, 'reference': body.pop('reference', 'DOC-' + action), **body}
        return self.post(f"/api/treasury/conversions/{conversion['id']}/{action}", payload, *(client or self.payer_client()))

    # ---------------------------------------------------------------- iteration 1
    def test_card_payment_posts_amount_and_fee_and_reduces_balance(self):
        rid = self.card_request()
        view = self.treasury(rid)
        self.assertEqual(view['kind'], 'card')
        card = view['card']
        self.assertEqual((card['balance_on_save'], card['fee_amount'], card['total']), ('5000000.00', '10000.00', '1010000.00'))
        self.assertEqual(self.row(rid)['required_documents'], ['internal'])
        payer = self.payer_client()
        actions = payer[0].get(f'/api/requests/{rid}', headers=payer[1]).json()['actions']
        self.assertIn('card_pay', actions)
        self.assertNotIn('pay', actions)
        generic = self.ledger('1000000', 'out', 'GENERIC', request_id=rid, account_id=self.card)
        self.assertEqual(generic.status_code, 409, generic.text)
        self.assertIn('корпоративной картой', generic.json()['detail'])
        paid = self.card_pay(rid)
        self.assertEqual(paid.status_code, 200, paid.text)
        card = paid.json()['card']
        self.assertEqual((card['state'], card['control_status']), ('paid', 'to_check'))
        self.assertEqual((card['balance_before'], card['balance_after']), ('5000000.00', '3990000.00'))
        self.assertEqual(self.row(rid)['status'], 'paid')
        self.assertEqual(self.balance(self.card), 399000000)
        with unit() as s:
            entries = list(s.scalars(select(Ledger).where(Ledger.account_id == self.card).order_by(Ledger.id)))
            self.assertEqual([(e.kind, e.amount, e.category_id, e.request_id) for e in entries],
                             [('out', 100000000, self.cat, rid), ('out', 1000000, self.card_fee, None)])
            fee_id = entries[1].id
        again = self.card_pay(rid, reference='SLIP-2')
        self.assertEqual(again.status_code, 409, again.text)
        reverse = self.post(f'/api/ledger/{fee_id}/reverse', {'reason': 'Попытка отдельного сторно комиссии'})
        self.assertEqual(reverse.status_code, 409, reverse.text)
        register = payer[0].get('/api/treasury/card-control', headers=payer[1]).json()
        self.assertEqual(register['counts']['to_check'], 1)
        row = register['items'][0]
        self.assertEqual((row['number'], row['fee'], row['total']), (f'CF-{rid:05d}', '10000.00', '1010000.00'))
        early = self.post(f"/api/treasury/card-control/{row['id']}", {'version': row['version'], 'status': 'verified'}, *payer)
        self.assertEqual(early.status_code, 422, early.text)
        done = self.post(f"/api/treasury/card-control/{row['id']}", {'version': row['version'], 'status': 'verified',
                         'statement_checked': True, 'documents_checked': True, 'note': 'Сверено с выпиской'}, *payer)
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(done.json()['control_status'], 'verified')
        report = self.client.get('/api/treasury/report', headers=self.h).json()
        line = next(x for x in report['cards'] if x['account_id'] == self.card)
        self.assertEqual((line['opening'], line['spent'], line['fees'], line['closing'], line['balanced']),
                         ('5000000.00', '1000000.00', '10000.00', '3990000.00', True))
        self.assertEqual(line['control']['verified'], 1)

    def test_card_overdraft_is_blocked_until_top_up(self):
        rid = self.card_request(n='99500', opening='100000.00')
        check = self.client.get('/api/treasury/card-balance', headers=self.h,
                                params={'account_id': self.card, 'amount': '99500', 'fee_percent': '1', 'request_id': rid}).json()
        self.assertEqual((check['enough'], check['shortage']), (False, '495.00'))
        denied = self.card_pay(rid)
        self.assertEqual(denied.status_code, 409, denied.text)
        self.assertIn('Недостаточно средств на корпоративной карте', denied.json()['detail'])
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(Ledger).where(Ledger.account_id == self.card)), 0)
        top_up = self.post('/api/ledger', {'account_id': self.acc, 'to_account_id': self.card, 'kind': 'transfer', 'amount': '1000',
                                           'date': self.date, 'reference': 'TOPUP-1', 'note': 'Пополнение корпоративной карты'})
        self.assertEqual(top_up.status_code, 200, top_up.text)
        paid = self.card_pay(rid)
        self.assertEqual(paid.status_code, 200, paid.text)
        self.assertEqual(self.balance(self.card), 10000000 + 100000 - 9950000 - 99500)

    def test_card_fee_needs_category_and_changed_fee_needs_reason(self):
        self.card = self.account('Корп. карта Uzcard', 'card', 'UZS', '1000000.00')
        self.card_fee = self.fee_category()
        body = self.request_body('1000', account=self.card)
        body['card'] = {'fee_percent': '2'}
        missing = self.post('/api/requests', body)
        self.assertEqual(missing.status_code, 422, missing.text)
        body['card'] = {'fee_percent': '2', 'fee_category_id': self.cat_conversion_guard()}
        system = self.post('/api/requests', body)
        self.assertEqual(system.status_code, 422, system.text)
        rid = self.card_request(n='1000', fee_percent='2', opening='1000000.00')
        changed = self.card_pay(rid, fee='25')
        self.assertEqual(changed.status_code, 422, changed.text)
        paid = self.card_pay(rid, fee='25', note='Банк удержал комиссию по тарифу 2,5%')
        self.assertEqual(paid.status_code, 200, paid.text)
        self.assertEqual(paid.json()['card']['actual_fee'], '25.00')

    def cat_conversion_guard(self):
        with unit(True) as s:
            category = Category(company_id=self.company, name='Служебная проверка конвертации', type='outcome', activity='conversion')
            s.add(category)
            s.flush()
            return category.id

    def test_card_payment_cancel_restores_balance_and_request(self):
        rid = self.card_request()
        self.assertEqual(self.card_pay(rid).status_code, 200)
        view = self.treasury(rid)['card']
        denied = self.post(f'/api/treasury/requests/{rid}/card-cancel', {'version': view['version'], 'reason': 'Ошибочно учтённая оплата картой'},
                           *self.payer_client())
        self.assertEqual(denied.status_code, 403, denied.text)
        cancelled = self.post(f'/api/treasury/requests/{rid}/card-cancel', {'version': view['version'], 'reason': 'Ошибочно учтённая оплата картой'},
                              *self.stage_user('finance'))
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()['card']['state'], 'planned')
        self.assertEqual(self.row(rid)['status'], 'approved')
        self.assertEqual(self.balance(self.card), 500000000)
        self.assertEqual(self.card_pay(rid, reference='SLIP-RETRY').status_code, 200)

    def test_card_draft_can_be_closed_and_its_plan_removed(self):
        rid = self.card_request(submit=False)
        closed = self.post(f'/api/requests/{rid}/decision', {'action': 'close', 'note': 'Оплата больше не требуется'})
        self.assertEqual(closed.status_code, 200, closed.text)
        with unit() as s:
            self.assertIsNone(s.get(PaymentRequest, rid))
            self.assertEqual(s.scalar(select(func.count()).select_from(CardPayment)), 0)

    # ---------------------------------------------------------------- iteration 2
    def test_usd_request_full_cycle_with_partial_payment_and_leftovers(self):
        self.fx_setup()
        rid = self.fx_request('10000')
        view = self.treasury(rid)['fx']
        self.assertEqual((view['payment_status'], view['need_to_convert']), ('unpaid', '10000.00'))
        conversion = self.plan(rid)
        self.assertEqual((conversion['planned_uzs'], conversion['planned_fee'], conversion['block_amount']),
                         ('128505000.00', '642525.00', '129147525.00'))
        early = self.step(conversion, 'credit', amount='10000', rate='12850.50')
        self.assertEqual(early.status_code, 409, early.text)
        blocked = self.step(conversion, 'block', reference='PP-101')
        self.assertEqual(blocked.status_code, 200, blocked.text)
        conversion = blocked.json()['fx']['conversions'][0]
        self.assertEqual(conversion['state'], 'blocked')
        self.assertEqual(self.balance(self.uzs), 20000000000 - 12914752500)
        with unit() as s:
            block = s.scalar(select(Account).where(Account.kind == 'block'))
            self.assertEqual((block.currency, account_balance(s, block)), ('UZS', 12914752500))
            block_id = block.id
        dashboard = self.client.get('/api/dashboard?currency=UZS', headers=self.h).json()
        self.assertEqual(dashboard['in_transit_balance'], '129147525.00')
        credited = self.step(conversion, 'credit', reference='FX-DEAL-7', amount='10050', rate='12780', fee='642000')
        self.assertEqual(credited.status_code, 200, credited.text)
        fx = credited.json()['fx']
        conversion = fx['conversions'][0]
        self.assertEqual((conversion['state'], conversion['sold_uzs'], conversion['credited_amount'], conversion['leftover_uzs']),
                         ('credited', '128439000.00', '10050.00', '66525.00'))
        self.assertEqual(conversion['effective_rate'], '12780.00')
        self.assertEqual(self.balance(self.usd), 1005000)
        self.assertEqual(fx['converted'], '10050.00')
        self.assertTrue(all(conversion['documents'][key] for key in ('block', 'sell', 'buy', 'fee')))
        returned = self.step(conversion, 'return', reference='PP-102')
        self.assertEqual(returned.status_code, 200, returned.text)
        self.assertEqual(self.balance(block_id), 0)
        self.assertEqual(self.balance(self.uzs), 20000000000 - 12914752500 + 6652500)
        payer = self.payer_client()
        too_much = self.post(f'/api/treasury/requests/{rid}/supplier-pay', {'request_version': self.version_of(rid), 'amount': '10001',
                             'date': self.date, 'reference': 'SWIFT-1'}, *payer)
        self.assertEqual(too_much.status_code, 422, too_much.text)
        part = self.post(f'/api/treasury/requests/{rid}/supplier-pay', {'request_version': self.version_of(rid), 'amount': '6000',
                         'date': self.date, 'reference': 'SWIFT-1', 'note': 'Предоплата 60% по инвойсу'}, *payer)
        self.assertEqual(part.status_code, 200, part.text)
        fx = part.json()['fx']
        self.assertEqual((fx['payment_status'], fx['requested'], fx['paid'], fx['outstanding']), ('partial', '10000.00', '6000.00', '4000.00'))
        # Bought currency stays earmarked for the unpaid remainder; only the excess over the request is free.
        self.assertEqual((fx['earmarked'], fx['need_to_convert'], fx['unused_converted']), ('4050.00', '0.00', '50.00'))
        self.assertEqual(self.row(rid)['amount'], '4000.00')
        child = fx['parts'][0]['request_id']
        self.assertEqual(self.row(child)['status'], 'paid')
        self.assertEqual(self.treasury(child)['fx']['parent']['id'], rid)
        final = self.post(f'/api/treasury/requests/{rid}/supplier-pay', {'request_version': self.version_of(rid), 'amount': '4000',
                          'date': self.date, 'reference': 'SWIFT-2'}, *payer)
        self.assertEqual(final.status_code, 200, final.text)
        fx = final.json()['fx']
        self.assertEqual((fx['payment_status'], fx['paid'], fx['outstanding'], fx['unused_converted']), ('paid', '10000.00', '0.00', '50.00'))
        self.assertEqual(fx['account']['free'], '50.00')
        self.assertEqual([p['amount'] for p in fx['parts']], ['6000.00', '4000.00'])
        self.assertEqual(self.balance(self.usd), 5000)
        uzs_report = self.client.get('/api/report', headers=self.h, params={'year': today().year, 'currency': 'UZS',
                                     'company_id': self.company, 'as_of': self.date}).json()
        group = next(g for g in uzs_report['groups'] if g['activity'] == 'conversion')
        self.assertEqual(group['values'][today().month - 1], '-128439000.00')
        usd_report = self.client.get('/api/report', headers=self.h, params={'year': today().year, 'currency': 'USD',
                                     'company_id': self.company, 'as_of': self.date}).json()
        group = next(g for g in usd_report['groups'] if g['activity'] == 'conversion')
        self.assertEqual(group['values'][today().month - 1], '10050.00')
        report = self.client.get('/api/treasury/report', headers=self.h).json()['fx']
        row = next(r for r in report['requests'] if r['request_id'] == rid)
        self.assertEqual((row['requested'], row['blocked_uzs'], row['converted'], row['paid'], row['free'], row['status']),
                         ('10000.00', '129147525.00', '10050.00', '10000.00', '50.00', 'paid'))
        self.assertFalse(any(r['request_id'] == child for r in report['requests']))
        bootstrap = self.client.get('/api/bootstrap', headers=self.h).json()
        system = [c for c in bootstrap['categories'] if c['activity'] == 'conversion']
        self.assertEqual(len(system), 2)
        self.assertTrue(all(c['retired'] for c in system))

    def test_block_requires_approval_and_respects_uzs_reserves(self):
        self.fx_setup()
        body = self.request_body('100', account=self.usd, counterparty='Supplier GmbH')
        rid = self.post('/api/requests', body).json()['id']
        for kind in ('internal', 'contract'):
            self.assertEqual(self.upload(rid, kind).status_code, 200)
        self.assertEqual(self.post(f'/api/requests/{rid}/decision', {'action': 'submit'}).status_code, 200)
        conversion = self.plan(rid, amount='100', rate='12800', fee='0')
        pending = self.step(conversion, 'block')
        self.assertEqual(pending.status_code, 409, pending.text)
        self.assertEqual(self.approve(rid).status_code, 200)
        uzs_rid = self.post('/api/requests', self.request_body('199000000', account=self.uzs)).json()['id']
        self.approved(uzs_rid, ('internal', 'contract'))
        reserved = self.step(conversion, 'block')
        self.assertEqual(reserved.status_code, 409, reserved.text)
        self.assertIn('Недостаточно доступных средств', reserved.json()['detail'])

    def test_credit_cannot_spend_more_than_blocked_and_conversion_legs_are_protected(self):
        self.fx_setup()
        rid = self.fx_request('1000')
        conversion = self.plan(rid, amount='1000', rate='12800', fee='0')
        conversion = self.step(conversion, 'block').json()['fx']['conversions'][0]
        excess = self.step(conversion, 'credit', amount='1000', rate='12900', fee='0')
        self.assertEqual(excess.status_code, 409, excess.text)
        self.assertIn('На блочном счёте', excess.json()['detail'])
        with unit() as s:
            block_leg = s.scalar(select(CurrencyConversion.block_ledger_id))
        reverse = self.post(f'/api/ledger/{block_leg}/reverse', {'reason': 'Попытка отдельного сторно перевода'})
        self.assertEqual(reverse.status_code, 409, reverse.text)
        deleted = self.post(f'/api/requests/{rid}/delete', {'version': self.version_of(rid), 'reason': 'Попытка удалить заявку с блоком'},
                            *self.stage_user('finance'))
        self.assertEqual(deleted.status_code, 409, deleted.text)
        manual = self.post('/api/ledger', {'account_id': conversion and self.block_account_id(), 'category_id': self.cat, 'kind': 'out',
                           'amount': '1', 'date': self.date, 'reference': 'MANUAL-BLOCK', 'note': 'Ручной расход с блочного счёта'})
        self.assertEqual(manual.status_code, 409, manual.text)
        refund = self.step(conversion, 'return', reference='PP-REFUND')
        self.assertEqual(refund.status_code, 200, refund.text)
        self.assertEqual(refund.json()['fx']['conversions'][0]['state'], 'cancelled')
        self.assertEqual(self.balance(self.uzs), 20000000000)

    def block_account_id(self):
        with unit() as s:
            return s.scalar(select(Account.id).where(Account.kind == 'block'))

    def test_supplier_payment_keeps_segregation_and_budget_reserve(self):
        self.fx_setup()
        self.post('/api/ledger', {'account_id': self.usd, 'category_id': self.cat_income(), 'kind': 'in', 'amount': '5000',
                                  'date': self.date, 'reference': 'USD-IN', 'note': 'Поступление валюты от покупателя'})
        rid = self.fx_request('3000')
        author = self.post(f'/api/treasury/requests/{rid}/supplier-pay', {'request_version': self.version_of(rid), 'amount': '1000',
                           'date': self.date, 'reference': 'SWIFT-A'}, *self.stage_user('finance'))
        self.assertEqual(author.status_code, 403, author.text)
        part = self.post(f'/api/treasury/requests/{rid}/supplier-pay', {'request_version': self.version_of(rid), 'amount': '1000',
                         'date': self.date, 'reference': 'SWIFT-B'}, *self.payer_client())
        self.assertEqual(part.status_code, 200, part.text)
        with unit() as s:
            s.info['company_id'] = self.company
            parent = s.get(PaymentRequest, rid)
            split = s.scalar(select(PaymentSplit))
            child = s.get(PaymentRequest, split.child_request_id)
            self.assertEqual((parent.status, parent.amount, child.status, child.amount), ('approved', 200000, 'paid', 100000))
            self.assertEqual((child.checked_by, child.finance_approved_by, child.approved_by),
                             (parent.checked_by, parent.finance_approved_by, parent.approved_by))
        from app.services import budget_state
        with unit() as s:
            s.info['company_id'] = self.company
            state = budget_state(s, self.cat, self.month, 'USD')
        # The paid part is spent once and only the remaining parent amount stays reserved.
        self.assertEqual((state['spent'], state['reserved']), (100000, 200000))
        self.assertEqual(self.client.get('/api/treasury/report', headers=self.h).json()['fx']['accounts'][0]['free'], '2000.00')

    def cat_income(self):
        response = self.post('/api/categories', {'name': 'Поступления в валюте', 'type': 'income', 'activity': 'operating'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def test_bot_maps_new_account_kinds_to_supported_values(self):
        from app.bot_api import bot_account_kind
        self.assertEqual(bot_account_kind('card'), {'kind': 'bank', 'source_kind': 'card'})
        self.assertEqual(bot_account_kind('block'), {'kind': 'bank', 'source_kind': 'block'})
        self.assertEqual(bot_account_kind('transit'), {'kind': 'cash', 'source_kind': 'transit'})
        self.assertEqual(bot_account_kind('bank'), {'kind': 'bank'})


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(TreasuryTests(name) for name in TreasuryTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    base.tearDownModule()


if __name__ == '__main__':
    unittest.main()
