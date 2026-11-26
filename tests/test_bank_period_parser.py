import io
import unittest
import zipfile
from datetime import date, datetime
from unittest.mock import patch

from openpyxl import Workbook
from openpyxl.utils.datetime import CALENDAR_MAC_1904

from app.bank_period_parser import parse_period_statement
from app.bank_statement_parser import StatementParseError, parse_bank_statement


def workbook():
    book = Workbook()
    sheet = book.active
    sheet.append(['IABS / Клиент-Банк'])
    sheet.append(['Сведения о работе счета с 01.04.2025 по 05.04.2025'])
    sheet.append(['Счет: 11111111111111111111 Демо Банк ИНН: 111000222'])
    sheet.append(['Остаток на начало периода: 25.00', 'Остаток на конец периода: 45.15'])
    sheet.append(['Дата', 'Номер документа', 'МФО корресп.', 'Счет корреспондента',
                  'Наименование корресп.', 'ИНН корреспондента', 'Назначение платежа', 'Дебет', 'Кредит'])
    sheet.append([date(2025, 4, 2), '0008', '00011', '01234567890123456789',
                  'Тестовый клиент', '004560000', 'Поступление', None, 30.15])
    sheet.append([date(2025, 4, 3), 77, '00011', '01234567890123456789',
                  'Тестовый банк', '004560000', 'Комиссия банка', 10, None])
    sheet.append(['Итоговый оборот за период:', None, None, None, None, None, None, 10, 30.15])
    return book


def raw(book):
    content = io.BytesIO()
    book.save(content)
    return content.getvalue()


def daily_workbook():
    book = Workbook()
    sheet = book.active
    for day, opening, closing in [(2, 0, 25), (3, 25, 50)]:
        label = f'{day:02}.04.2025'
        for row in (
            ['Демо Банк'], ['Лицевой счет: 20214000900000000001'],
            ['Клиент 000123 Инн 111000222'], ['Период выписки с 01.04.2025 по 03.04.2025'],
            ['Дата последней проводки : 03.04.2025'],
            ['Входящий остаток на ' + label, None, None, None, None, opening],
            ['Дата', 'Дата', 'Номер', 'Корреспондент: Банк/Счет/ИНН Наименование Назначение', 'Дебет', 'Кредит'],
            ['проводки', 'Опер. дня', 'документа', 'платежа'],
            [datetime(2025, 3, 31, 10, 15), date(2025, 4, day), '0008',
             'МФО:00123 Счет: 00100000000000000001 ИНН:123456789', None, 25],
            [None, None, None, 'Получатель'], [None, None, None, 'Назначение платежа'],
            ['Всего оборотов', None, None, None, 0, 25],
            ['Остаток на конец дня ' + label, None, None, None, None, closing],
        ):
            sheet.append(row)
    return book


class PeriodParserTests(unittest.TestCase):
    def test_operational_date_and_multiline_payment(self):
        for epoch in (None, CALENDAR_MAC_1904):
            book = daily_workbook()
            if epoch:
                book.epoch = epoch
            result = parse_period_statement(raw(book))
            self.assertTrue(result['ready'])
            self.assertEqual(result['closing_balance'], '50.00')
            self.assertEqual([row['date'] for row in result['rows']], ['2025-04-02', '2025-04-03'])
            self.assertEqual([row['line'] for row in result['rows']], [9, 22])
            entry = result['rows'][0]
            self.assertEqual((entry['document_number'], entry['correspondent_mfo'], entry['correspondent_name'],
                              entry['purpose']), ('0008', '00123', 'Получатель', 'Назначение платежа'))

    def test_operational_blocks_reject_partial_or_inconsistent_data(self):
        for cell, value in [('F12', 26), ('F13', 24), ('F19', 24), ('D10', None),
                            ('B9', datetime(2025, 4, 2, 13)), ('B22', date(2025, 4, 2)),
                            ('A15', 'Лицевой счет: 20214000900000000002'), ('F22', '=1+24')]:
            with self.subTest(cell=cell):
                book = daily_workbook()
                book.active[cell] = value
                with self.assertRaises(StatementParseError):
                    parse_period_statement(raw(book))

    def test_actual_dates_zero_in_unused_column_and_leading_zero_identifiers(self):
        for mac_epoch in (False, True):
            with self.subTest(mac_epoch=mac_epoch):
                book = workbook()
                if mac_epoch:
                    book.epoch = CALENDAR_MAC_1904
                book.active['H6'] = 0
                statement = parse_period_statement(raw(book))
                self.assertTrue(statement['ready'])
                self.assertEqual(statement['period_from'], '2025-04-01')
                self.assertEqual(statement['rows'][0]['date'], '2025-04-02')
                self.assertEqual(statement['rows'][0]['document_number'], '0008')
                self.assertEqual(statement['rows'][0]['correspondent_account'], '01234567890123456789')
                self.assertEqual(statement['rows'][0]['correspondent_mfo'], '00011')
                self.assertEqual(statement['rows'][1]['direction'], 'out')
                self.assertEqual(statement['credit_total'], '30.15')
                self.assertEqual(statement['debit_total'], '10.00')
                self.assertEqual(statement['closing_balance'], '45.15')
                self.assertEqual(statement['balance_difference'], '0.00')
                self.assertIsNone(statement['currency'])

    def test_totals_or_closing_disagreement_remains_visible(self):
        book = workbook()
        book.active['B4'] = 'Остаток на конец периода: 45.14'
        book.active['I8'] = 30.16
        statement = parse_period_statement(raw(book))
        self.assertFalse(statement['ready'])
        self.assertFalse(statement['declared_totals_match'])
        self.assertEqual(statement['balance_difference'], '-0.01')

    def test_valid_zero_activity_statement_still_has_bank_opening_and_closing(self):
        book = workbook()
        sheet = book.active
        sheet.delete_rows(6, 2)
        sheet['H6'] = sheet['I6'] = 0
        sheet['B4'] = 'Остаток на конец периода: 25.00'
        statement = parse_period_statement(raw(book))
        self.assertTrue(statement['ready'])
        self.assertEqual(statement['rows'], [])
        self.assertEqual(statement['closing_balance'], '25.00')
        sheet['H6'] = sheet['I6'] = None
        self.assertFalse(parse_period_statement(raw(book))['ready'])
        sheet.delete_rows(6)
        self.assertFalse(parse_period_statement(raw(book))['ready'])

    def test_inconsistent_account_date_currency_precision_or_formula_rejected(self):
        for address, bad_value in (
            ('A2', 'Ноябрь'), ('A3', 'Счет: не определён'), ('A6', date(2025, 5, 1)),
            ('I6', 30.151), ('D6', 12345678901234567890), ('H7', '=1+9'),
        ):
            with self.subTest(address=address):
                book = workbook()
                book.active[address] = bad_value
                with self.assertRaises(StatementParseError):
                    parse_period_statement(raw(book))

    def test_xlsx_financial_comparison_does_not_enable_xlsx_posting(self):
        with self.assertRaises(StatementParseError):
            parse_bank_statement(raw(workbook()))

    def test_duplicate_sheet_multiple_accounts_and_bounded_archive(self):
        book = workbook()
        book.create_sheet('Второй счёт')
        with self.assertRaises(StatementParseError):
            parse_period_statement(raw(book))
        with patch('app.bank_period_parser.MAX_EXPANDED', 100), self.assertRaises(StatementParseError):
            parse_period_statement(raw(workbook()))
        with self.assertRaises(StatementParseError):
            parse_period_statement(b'PK invalid')

    def test_xlsx_binary_numeric_noise_and_sparse_header(self):
        content = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw(workbook()))) as original:
            with zipfile.ZipFile(content, 'w', zipfile.ZIP_DEFLATED) as modified:
                for name in original.namelist():
                    entry = original.read(name)
                    if name == 'xl/worksheets/sheet1.xml':
                        entry = entry.replace(b'>30.15<', b'>30.149999999999999<')
                    modified.writestr(name, entry)
        self.assertEqual(parse_period_statement(content.getvalue())['credit_total'], '30.15')


if __name__ == '__main__':
    unittest.main()
