"""HTTP bank-charge pipeline: synthetic source only, with isolated test-site data."""
import unittest
from sqlalchemy import select, func

from tests.test_bank_statement_auto_integration import AutoBankIntegrationTests
from app.db import Category, Ledger, Setting, engine, unit


class BankChargeIntegrationTests(AutoBankIntegrationTests):
    def charges_source(self):
        day = self.date[8:10] + '.' + self.date[5:7] + '.' + self.date[:4]
        head = f'''<html><body><table>
        <tr><td>Сведения о работе счета c {day} по {day}</td></tr>
        <tr><td>Cчет: 20200000000000000001 Synthetic Company ИНН: 300000001</td></tr>
        <tr><td>Остаток на начало периода: 1000000.00</td><td>Остаток на конец периода: 999900.00</td></tr>
        </table><table><tr><th>Дата</th><th>Номер документа</th><th>МФО корресп.</th>
        <th>Счет корреспондента</th><th>Наименование корресп.</th><th>ИНН корреспондента</th>
        <th>Назначение платежа</th><th>Дебет</th><th>Кредит</th></tr>'''
        rows = []
        for kind, account, amount, purpose in (
            ('FEE', '16401000000000000001', '20.00', f'00111 BC {day} погашение Исх.плат.документ -20.00 от суммы 500.00 от {day}'),
            ('INTEREST', '16309000000000000001', '30.00', '01014кредит фоизи учун толов'),
        ):
            for number in (1, 2):
                rows.append(f'<tr><td>{day}</td><td>SYN-{kind}-{number}</td><td>00014</td><td>{account}</td>'
                    f'<td>Synthetic bank</td><td>300000001</td><td>{purpose}</td><td>{amount}</td><td></td></tr>')
        tail = '<tr><td>Итоговый оборот</td><td>100.00</td><td>0.00</td></tr></table></body></html>'
        return (head + ''.join(rows) + tail).encode('cp1251')

    def test_multi_fee_and_interest_create_no_preview_articles_and_post_each_once(self):
        with unit() as s:
            before = (s.scalar(select(func.count(Category.id))), s.scalar(select(func.count(Setting.key))))
        result = self.preview(self.charges_source())
        self.assertEqual(result.status_code, 200, result.text)
        preview = result.json()
        self.assertEqual([row['status'] for row in preview['rows']], ['fee_candidate', 'fee_candidate', 'interest_candidate', 'interest_candidate'])
        self.assertTrue(all(row['category_id'] is None for row in preview['rows']))
        with unit() as s:
            self.assertEqual((s.scalar(select(func.count(Category.id))), s.scalar(select(func.count(Setting.key)))), before)
            self.assertEqual(s.scalar(select(func.count(Ledger.id))), 0)
        posted = self.confirmed(preview)
        self.assertEqual(posted.status_code, 200, posted.text)
        with unit() as s:
            entries = list(s.scalars(select(Ledger)))
            self.assertEqual(len(entries), 4)
            fees = [entry for entry in entries if s.get(Category, entry.category_id).name == 'Банковские комиссии (выписки)']
            interest = [entry for entry in entries if s.get(Category, entry.category_id).name == 'Проценты по кредитам (выписки)']
            self.assertEqual((len(fees), len(interest)), (2, 2))
            self.assertEqual((sum(entry.amount for entry in fees), sum(entry.amount for entry in interest)), (4000, 6000))
            self.assertEqual(len({entry.category_id for entry in entries}), 2)
        self.assertEqual(self.confirmed(preview).status_code, 409)
        with unit() as s:
            self.assertEqual(s.scalar(select(func.count(Ledger.id))), 4)

    def test_company_catalog_articles_are_reused_without_technical_settings(self):
        with unit(True) as s:
            fee = s.get(Category, self.cat); fee.code = '8'; fee.name = 'Банк хизматлари'
            interest = Category(company_id=self.company, code='63', name='Кредит фоизлари', type='outcome', activity='financing')
            s.add(interest); s.flush()
            fee_id, interest_id = fee.id, interest.id
        result = self.preview(self.charges_source())
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual([row['category_id'] for row in result.json()['rows']], [fee_id, fee_id, interest_id, interest_id])
        posted = self.confirmed(result.json())
        self.assertEqual(posted.status_code, 200, posted.text)
        with unit() as s:
            self.assertEqual({entry.category_id for entry in s.scalars(select(Ledger))}, {fee_id, interest_id})
            self.assertEqual(list(s.scalars(select(Setting).where(Setting.key.like('%bank_statement_%_technical_category')))), [])

    def test_same_named_unknown_article_does_not_change_the_signed_planned_identity(self):
        with unit(True) as s:
            s.add(Category(company_id=self.company, name='Банковские комиссии (выписки)', type='outcome'))
        preview = self.preview(self.charges_source())
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual([row['category_name'] for row in preview.json()['rows'][:2]],
                         ['Банковские комиссии (выписки) (2)'] * 2)
        posted = self.confirmed(preview.json())
        self.assertEqual(posted.status_code, 200, posted.text)
        with unit() as s:
            entries = list(s.scalars(select(Ledger)))
            self.assertEqual(len(entries), 4)
            fee_names = [s.get(Category, entry.category_id).name for entry in entries if entry.amount == 2000]
            self.assertEqual(fee_names, ['Банковские комиссии (выписки) (2)'] * 2)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(BankChargeIntegrationTests(name) for name in BankChargeIntegrationTests.__dict__ if name.startswith('test_'))


def tearDownModule():
    # Other HTTP suites share test_site.TMP; its owner performs final cleanup.
    engine.dispose()
