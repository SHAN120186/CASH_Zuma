"""Exact bank-charge signatures and company articles; isolated synthetic database."""
import unittest

from fastapi import HTTPException
from sqlalchemy import event, select, func

from tests.test_bank_statement_fees import FeeTests
from app.bank_statement_charge_categories import charge_category
from app.bank_statement_fees import classify_fee, exact_iabs_fee, post_fee, prepare_fee_context
from app.bank_statement_interest import classify_interest, exact_loan_interest, post_interest
from app.bank_statement_models import BankStatementLine
from app.db import Category, Ledger, PaymentRequest, Setting
from app.services import account_balance


class BankChargeTests(FeeTests):
    def fee(self):
        self.line.correspondent_account = '16401000000000000001'
        day = self.line.date.strftime('%d.%m.%Y')
        self.line.purpose = f'00111 BC {day} погашение Исх.плат.документ -10.00 от суммы 1000.00 от {day}'
        self.s.flush()
        return self.line

    def interest(self):
        self.line.correspondent_account = '16309000000000000001'
        self.line.purpose = '01014кредит фоизи учун толов'
        self.s.flush()
        return self.line

    def decision(self, kind='fee', **kwargs):
        if kind == 'interest':
            return classify_interest(self.s, self.batch, self.line, self.account, self.user, kwargs.pop('opening_ok', True), **kwargs)
        return classify_fee(self.s, self.batch, self.line, self.account, kwargs.pop('category_id', None), self.user,
                            kwargs.pop('opening_ok', True), **kwargs)

    def test_iabs_precise_template_amount_date_owner_and_account_are_all_required(self):
        self.fee()
        self.assertTrue(exact_iabs_fee(self.batch, self.line))
        self.assertEqual(self.decision()['status'], 'fee_candidate')
        original = self.line.purpose
        for purpose in ('100% оплата товаров', '00111 Ком. услуги за период', 'Комиссия банка',
                        original.replace('-10.00', '-10.01'), original.replace(self.line.date.strftime('%d.%m.%Y'), '01.01.2000')):
            self.line.purpose = purpose
            self.assertIsNone(self.decision())
        self.line.purpose = original
        self.line.correspondent_account = '20208000000000000001'
        self.assertIsNone(self.decision())
        self.line.correspondent_account = '16401000000000000001'
        self.line.correspondent_inn = '300000002'
        self.assertIsNone(self.decision())
        self.line.correspondent_inn = self.batch.owner_inn
        self.line.purpose = original.replace('Исх.плат.документ', 'Пополнение корп.пл.карты(КИБ)')
        self.assertEqual(self.decision()['status'], 'fee_candidate')

    def test_automatic_catalog_requires_both_company_code_and_meaning(self):
        self.fee()
        self.cat.code = '8'; self.cat.name = 'Банк хизматлари'
        self.s.flush()
        self.s.info.pop('company_id')  # Fixture setup for the unrelated company only.
        self.foreign.code = '8'; self.foreign.name = 'Банковские комиссии'; self.s.flush()
        self.s.info['company_id'] = self.company.id
        result = self.decision(category_id=self.foreign.id)
        self.assertEqual((result['category_id'], result['category_source']), (self.cat.id, 'bank_fee_auto'))
        self.cat.name = 'Материалы'; self.s.flush()
        result = self.decision()
        self.assertIsNone(result['category_id'])
        self.assertEqual(result['category_name'], 'Банковские комиссии (выписки)')
        self.cat.name = 'Банковские комиссии'; self.cat.type = 'income'; self.s.flush()
        self.assertIsNone(self.decision()['category_id'])
        self.cat.type = 'outcome'; self.s.add(Setting(key=f'company:{self.company.id}:category_catalog', value='{"retired":['+str(self.cat.id)+']}')); self.s.flush()
        self.assertIsNone(self.decision()['category_id'])

    def test_interest_uses_exact_code_owner_account_and_never_principal_or_generic_words(self):
        self.interest()
        self.cat.code = '63'; self.cat.name = 'Кредит фоизлари'; self.s.flush()
        result = self.decision('interest')
        self.assertEqual((result['status'], result['category_id'], result['category_source']), ('interest_candidate', self.cat.id, 'bank_interest_auto'))
        self.assertIsNone(self.decision())
        for purpose in ('Оплата процентов по кредиту', 'loan interest', '01014 Погашение основного долга',
                        '01014кредит фоизи ва асосий қарз', '01014кредит фоизи principal', '100% оплата товаров',
                        '00111 Ком. услуги за период'):
            self.line.purpose = purpose
            self.assertIsNone(self.decision('interest'))
        self.interest(); self.line.correspondent_account = '20208000000000000001'
        self.assertFalse(exact_loan_interest(self.batch, self.line))
        self.assertIsNone(self.decision('interest'))
        self.interest(); self.line.correspondent_inn = '300000002'
        self.assertIsNone(self.decision('interest'))

    def test_approved_bank_service_and_credit_percent_catalog_names_are_reused(self):
        self.fee(); self.cat.code = '8'; self.cat.name = 'Услуга банк (Проценты банка %)'; self.s.flush()
        self.assertEqual(self.decision()['category_id'], self.cat.id)
        self.interest(); self.cat.code = '63'; self.cat.name = 'Кредит % Зума'; self.s.flush()
        self.assertEqual(self.decision('interest')['category_id'], self.cat.id)
        self.cat.code = '8'; self.s.flush()
        self.assertIsNone(self.decision('interest')['category_id'], 'a familiar name with the wrong code is not the loan article')
        self.cat.code = '63'; self.cat.name = '100% оплата товара'; self.s.flush()
        self.assertIsNone(self.decision('interest')['category_id'])

    def test_preview_is_select_only_and_context_matches_direct_results(self):
        writes = []
        def record(conn, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
                writes.append(statement)
        self.fee()
        event.listen(self.engine, 'before_cursor_execute', record)
        try:
            direct = self.decision()
            context = prepare_fee_context(self.s, self.batch, [self.line], self.account, None)
            self.assertEqual(self.decision(context=context), direct)
            self.assertEqual(writes, [])
        finally:
            event.remove(self.engine, 'before_cursor_execute', record)
        self.interest()
        context = prepare_fee_context(self.s, self.batch, [self.line], self.account, None, charge_kind='interest')
        self.assertEqual(self.decision('interest', context=context), self.decision('interest'))
        self.assertEqual(self.s.scalar(select(func.count(Setting.key))), 0)
        self.assertEqual(self.s.scalar(select(func.count(Ledger.id))), 0)

    def test_uncertain_requests_and_human_conflicts_stay_in_review(self):
        for kind in ('fee', 'interest'):
            getattr(self, kind)()
            self.line.uncertain = True
            self.assertEqual(self.decision(kind)['status'], 'review')
            self.line.uncertain = False
            self.assertEqual(self.decision(kind, opening_ok=False)['status'], 'review')
            self.line.purpose += ' CF-00009'
            self.assertEqual(self.decision(kind)['status'], 'review')
            getattr(self, kind)()
            self.line.status = 'reviewed'; self.line.reviewed_by = self.user.id
            self.line.classification = 'internal_transfer'
            self.assertIsNone(self.decision(kind))
            self.line.status = 'review'; self.line.reviewed_by = None; self.line.classification = ''
            req = PaymentRequest(creator_id=self.user.id, category_id=self.cat.id, account_id=self.account.id,
                counterparty='Synthetic', counterparty_inn='', amount=self.line.amount, due_date=self.line.date,
                purpose='Synthetic hidden draft', status='draft')
            self.s.add(req); self.s.flush()
            self.assertEqual(self.decision(kind)['status'], 'review')
            self.s.delete(req); self.s.flush()

    def test_fees_and_interest_post_once_to_separate_technical_articles_after_confirmation(self):
        self.fee()
        with self.assertRaises(HTTPException):
            post_fee(self.s, self.batch, self.line, self.account, None, self.user, True)
        with self.s.begin_nested():
            fee = post_fee(self.s, self.batch, self.line, self.account, None, self.user, True, confirmed_decision=True)
            self.line.ledger_id = fee.id; self.line.status = 'auto_fee_posted'; self.s.flush()
        with self.assertRaises(HTTPException):
            post_fee(self.s, self.batch, self.line, self.account, None, self.user, True, confirmed_decision=True)
        second = BankStatementLine(company_id=self.company.id, batch_id=self.batch.id, account_id=self.account.id,
            line=7, date=self.line.date, direction='out', amount=2000, document_number='SYN-INTEREST',
            correspondent_mfo='00014', correspondent_account='16309000000000000001',
            correspondent_name='Synthetic bank', correspondent_inn=self.batch.owner_inn,
            purpose='01014кредит фоизи учун толов', fingerprint='b'*64, occurrence=1,
            status='review', reason='', include_in_daily=True, uncertain=False)
        self.s.add(second); self.s.flush()
        with self.assertRaises(HTTPException):
            post_interest(self.s, self.batch, second, self.account, self.user, True)
        with self.s.begin_nested():
            interest = post_interest(self.s, self.batch, second, self.account, self.user, True, confirmed_decision=True)
            second.ledger_id = interest.id; second.status = 'auto_interest_posted'; self.s.flush()
        self.assertNotEqual(fee.category_id, interest.category_id)
        self.assertEqual(self.s.get(Category, fee.category_id).name, 'Банковские комиссии (выписки)')
        self.assertEqual(self.s.get(Category, interest.category_id).name, 'Проценты по кредитам (выписки)')
        self.assertEqual(account_balance(self.s, self.account), 97000)
        self.assertEqual(self.s.scalar(select(func.count(Ledger.id))), 2)

    def test_failed_post_rolls_back_new_technical_article_with_ledger(self):
        self.interest(); self.account.opening = 0; self.s.flush()
        with self.assertRaises(HTTPException):
            with self.s.begin_nested():
                post_interest(self.s, self.batch, self.line, self.account, self.user, True, confirmed_decision=True)
        self.assertEqual(self.s.scalar(select(func.count(Setting.key))), 0)
        self.assertEqual(self.s.scalar(select(func.count(Ledger.id))), 0)
        self.assertIsNone(self.decision('interest')['category_id'])

    def test_damaged_technical_identity_stays_review_instead_of_blocking_other_rows(self):
        self.fee()
        self.s.add(Setting(key=f'company:{self.company.id}:bank_statement_fee_technical_category', value=str(self.income.id)))
        self.s.flush()
        context = prepare_fee_context(self.s, self.batch, [self.line], self.account, None)
        self.assertEqual(self.decision(context=context), self.decision())
        self.assertEqual(self.decision()['status'], 'review')
        self.interest()
        self.assertEqual(self.decision('interest')['status'], 'interest_candidate')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(BankChargeTests(name) for name in BankChargeTests.__dict__ if name.startswith('test_'))
