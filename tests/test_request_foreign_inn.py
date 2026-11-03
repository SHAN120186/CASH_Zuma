"""Optional request INN across currencies; use isolated, synthetic accounts and documents."""
import unittest
from sqlalchemy import select, func
import tests.test_site as base
from app.db import Account, Company, Counterparty, Ledger, PaymentRequest, unit


class ForeignRequestInnTests(base.SiteTests):
    def account(self, currency, kind='bank'):
        result = self.post('/api/accounts', {
            'name': f'Synthetic {currency} {kind}', 'kind': kind, 'currency': currency,
            'opening': '1000', 'opening_date': self.date,
        })
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['id']

    def currency_budget(self, currency):
        result = self.post('/api/budgets', {
            'category_id': self.cat, 'month': self.month, 'currency': currency,
            'amount': '1000', 'mode': 'hard', 'reason': 'Synthetic foreign request budget', 'source': 'test',
        })
        self.assertEqual(result.status_code, 200, result.text)

    def test_foreign_blank_inn_create_submit_edit_approve_and_pay(self):
        for currency in ('USD', 'EUR'):
            account = self.account(currency)
            self.currency_budget(currency)
            for explicit_blank in (False, True):
                with self.subTest(currency=currency, explicit_blank=explicit_blank):
                    name = f'Synthetic {currency} foreign supplier {explicit_blank}'
                    body = self.request_body('100', account=account, counterparty=name)
                    if explicit_blank:
                        body['counterparty_inn'] = ''
                    created = self.post('/api/requests', body)
                    self.assertEqual(created.status_code, 200, created.text)
                    rid = created.json()['id']
                    self.assertEqual(created.json()['counterparty_inn'], '')
                    self.assertEqual(created.json()['currency'], currency)
                    # A manual foreign name can be corrected in the draft without an INN.
                    body.update(counterparty=name + ' draft', version=self.version_of(rid), reason='Correct synthetic foreign beneficiary')
                    edited = self.client.put(f'/api/requests/{rid}', json=body, headers=self.h)
                    self.assertEqual(edited.status_code, 200, edited.text)
                    for kind in ('internal', 'contract'):
                        self.assertEqual(self.upload(rid, kind).status_code, 200)
                    submitted = self.post(f'/api/requests/{rid}/decision', {'action': 'submit'})
                    self.assertEqual(submitted.status_code, 200, submitted.text)
                    self.assertEqual(submitted.json()['counterparty_inn'], '')
                    # Editing an already submitted request retains its route and blank INN.
                    body.update(counterparty=name + ' final', status='pending', version=self.version_of(rid))
                    edited = self.client.put(f'/api/requests/{rid}', json=body, headers=self.h)
                    self.assertEqual(edited.status_code, 200, edited.text)
                    self.assertEqual(edited.json()['status'], 'pending')
                    self.assertEqual(edited.json()['counterparty'], body['counterparty'])
                    self.assertEqual(edited.json()['counterparty_inn'], '')
                    approved = self.approve(rid)
                    self.assertEqual(approved.status_code, 200, approved.text)
                    paid = self.ledger('100', 'out', reference=f'SYNTHETIC-{currency}-{explicit_blank}', account_id=account, request_id=rid)
                    self.assertEqual(paid.status_code, 200, paid.text)
                    row = self.row(rid)
                    self.assertEqual((row['status'], row['counterparty'], row['counterparty_inn']), ('paid', body['counterparty'], ''))
                    with unit() as s:
                        entry = s.scalar(select(Ledger).where(Ledger.request_id == rid))
                        self.assertEqual((entry.account_id, entry.amount), (account, 10000))
                        self.assertEqual(s.get(Account, entry.account_id).currency, currency)
                        party = s.scalar(select(Counterparty).where(Counterparty.name == body['counterparty']))
                        self.assertIsNotNone(party)
                        self.assertEqual(party.inn, '')

    def test_uzs_bank_and_cash_keep_the_existing_optional_inn_and_document_rules(self):
        # INN was already optional in UZS and cash: do not introduce a new requirement.
        self.currency_budget('UZS')
        for kind, account, required in (
            ('bank', self.acc, ('internal', 'contract')),
            ('cash', self.account('UZS', 'cash'), ('internal',)),
        ):
            with self.subTest(kind=kind):
                result = self.post('/api/requests', {**self.request_body('100', account=account), 'counterparty_inn': ''})
                self.assertEqual(result.status_code, 200, result.text)
                rid = result.json()['id']
                self.assertEqual(result.json()['required_documents'], list(required))
                self.assertEqual(self.post(f'/api/requests/{rid}/decision', {'action': 'submit'}).status_code, 409)
                for document_kind in required:
                    self.assertEqual(self.upload(rid, document_kind).status_code, 200)
                submitted = self.post(f'/api/requests/{rid}/decision', {'action': 'submit'})
                self.assertEqual(submitted.status_code, 200, submitted.text)
                self.assertEqual(submitted.json()['counterparty_inn'], '')

    def test_client_currency_and_company_cannot_override_the_real_account(self):
        body = {**self.request_body('100'), 'counterparty_inn': ''}
        with unit() as s:
            before = s.scalar(select(func.count(PaymentRequest.id)))
            other_company = s.scalar(select(Company).where(Company.code == 'ZUMA')).id
        for overrides, status in (({'currency': 'USD'}, 422), ({'currency': 'EUR'}, 422), ({'company_id': other_company}, 409)):
            result = self.post('/api/requests', {**body, **overrides})
            self.assertEqual(result.status_code, status, result.text)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count(PaymentRequest.id))), before)
        created = self.post('/api/requests', body)
        self.assertEqual(created.status_code, 200, created.text)
        rid = created.json()['id']
        edited = self.client.put(f'/api/requests/{rid}', json={**body, 'currency': 'USD', 'version': self.version_of(rid), 'reason': 'Attempt synthetic currency mismatch'}, headers=self.h)
        self.assertEqual(edited.status_code, 422, edited.text)
        self.assertEqual((self.row(rid)['currency'], self.row(rid)['account_id']), ('UZS', self.acc))

    def test_supplied_foreign_inn_still_requires_existing_numeric_syntax(self):
        for currency in ('USD', 'EUR'):
            account = self.account(currency)
            body = self.request_body('100', account=account, counterparty=f'Synthetic {currency} identified supplier')
            for invalid in ('bad', '12-345', '1' * 21):
                result = self.post('/api/requests', {**body, 'counterparty_inn': invalid})
                self.assertEqual(result.status_code, 422, result.text)
            created = self.post('/api/requests', {**body, 'counterparty_inn': '009876543'})
            self.assertEqual(created.status_code, 200, created.text)
            rid = created.json()['id']
            self.assertEqual(self.row(rid)['counterparty_inn'], '009876543')
            edited = self.client.put(f'/api/requests/{rid}', json={**body, 'counterparty_inn': 'bad', 'version': self.version_of(rid), 'reason': 'Reject malformed supplied identifier'}, headers=self.h)
            self.assertEqual(edited.status_code, 422, edited.text)
            self.assertEqual(self.row(rid)['counterparty_inn'], '009876543')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ForeignRequestInnTests(name) for name in ForeignRequestInnTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    base.tearDownModule()
