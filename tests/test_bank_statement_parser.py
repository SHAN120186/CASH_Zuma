"""Synthetic bank statements only: no production account or counterparty data."""
import unittest

from app.bank_statement_parser import MAX_BYTES, StatementParseError, parse_bank_statement


HEADER = '''<html><head><meta charset="windows-1251"><style>td { text-align: right }</style></head><body>
<table><tr><td>Выдуманный банк</td><td>Дата выгрузки 03.11.2026</td></tr>
<tr><td colspan="2">Сведения о работе счета c 01.11.2026 по 02.11.2026</td></tr>
<tr><td colspan="2">Cчет: 20200000000000000001&nbsp;&nbsp; ООО Тестовая фирма&nbsp;&nbsp; ИНН: 300000001</td></tr>
<tr><td>Остаток на начало периода: 1 000.00</td><td>Остаток на конец периода: 1 050.50</td></tr></table>
<table><thead><tr><th>Дата</th><th>Номер документа</th><th>МФО корресп.</th>
<th>Счет корреспондента</ht><th>Наименование корресп.</th><th>ИНН корреспондента</th>
<th>Назначение платежа</th><th>Дебет</th><th>Кредит</th></thead>'''
OUT = '''<tr><td>01.11.2026</td><td>0001</td><td>00014</td><td>20200000000000000002</td>
<td>Поставщик &amp; партнёр</td><td>300000002</td><td>Оплата по счёту</td><td>150.25</td><td>&nbsp;</td></tr>'''
IN = '''<tr><td>02.11.2026</td><td>0001</td><td>00014</td><td>20200000000000000003</td>
<td>Покупатель</td><td>300000003</td><td>Поступление</td><td>&nbsp;</td><td>200.75</td></tr>'''
FOOTER = '''<tr><td>Итоговый оборот</td><td></td><td></td><td>150.25</td><td>200.75</td></tr></table></body></html>'''


def raw(*parts):
    return ''.join(parts).encode('cp1251')


class BankStatementParserTests(unittest.TestCase):
    def test_valid_statement_and_identifiers_are_not_coerced_or_deduplicated(self):
        result = parse_bank_statement(raw(HEADER, OUT, IN, FOOTER))
        self.assertEqual((result['period_from'], result['period_to']), ('2026-11-01', '2026-11-02'))
        self.assertEqual((result['opening_balance'], result['closing_balance']), ('1000.00', '1050.50'))
        self.assertEqual((result['debit_total'], result['credit_total']), ('150.25', '200.75'))
        self.assertEqual(result['account_number'], '20200000000000000001')
        self.assertEqual(result['owner_inn'], '300000001')
        self.assertIsNone(result['currency'])
        self.assertTrue(result['ready'])
        self.assertTrue(result['balance_matches'])
        self.assertTrue(result['declared_totals_match'])
        self.assertEqual(len(result['rows']), 2)
        self.assertEqual([r['document_number'] for r in result['rows']], ['0001', '0001'])
        self.assertEqual([r['direction'] for r in result['rows']], ['out', 'in'])
        self.assertEqual(result['rows'][0]['correspondent_mfo'], '00014')
        self.assertEqual(result['rows'][0]['correspondent_name'], 'Поставщик & партнёр')
        self.assertEqual([r['date'] for r in result['rows']], ['2026-11-01', '2026-11-02'])
        self.assertEqual([r['line'] for r in result['rows']], [6, 7])

    def test_balance_and_declared_total_discrepancies_are_visible_and_block_ready(self):
        mismatch = parse_bank_statement(raw(HEADER.replace('1 050.50', '1 050.51'), OUT, IN, FOOTER))
        self.assertEqual(mismatch['balance_difference'], '0.01')
        self.assertFalse(mismatch['balance_matches'])
        self.assertFalse(mismatch['ready'])
        mismatch = parse_bank_statement(raw(HEADER, OUT, IN, FOOTER.replace('200.75</td>', '200.76</td>')))
        self.assertFalse(mismatch['declared_totals_match'])
        self.assertFalse(mismatch['ready'])
        missing = parse_bank_statement(raw(HEADER, OUT, IN, '</table></body></html>'))
        self.assertTrue(missing['balance_matches'])
        self.assertIsNone(missing['declared_totals_match'])
        self.assertFalse(missing['ready'])

    def test_invalid_or_ambiguous_row_is_rejected(self):
        bad = [
            OUT.replace('150.25</td><td>&nbsp;', '150.25</td><td>1.00'),
            OUT.replace('150.25', '1,000'),
            OUT.replace('150.25', '-150.25'),
            OUT.replace('150.25', 'not money'),
            OUT.replace('150.25', '1' * 21),
            OUT.replace('01.11.2026', '32.11.2026'),
            OUT.replace('01.11.2026', '01.10.2026'),
            OUT.replace('00014</td>', 'abc</td>'),
            OUT.replace('300000002</td>', 'INN</td>'),
            OUT.replace('<td>Поставщик &amp; партнёр</td>', '<td></td>'),
        ]
        for out in bad:
            with self.subTest(out=out), self.assertRaises(StatementParseError):
                parse_bank_statement(raw(HEADER, out, IN, FOOTER))

    def test_multiple_statement_tables_and_active_content_are_rejected(self):
        for payload in (raw(HEADER, OUT, IN, FOOTER, '<script>alert(1)</script>'),
                        raw(HEADER, OUT, IN, FOOTER, '<iframe src="https://invalid"></iframe>'),
                        b'<html><!ENTITY x SYSTEM "file:///secret"><table></table></html>',
                        raw(HEADER, OUT, IN, FOOTER, HEADER, OUT, IN, FOOTER),
                        b'not html', b'', b'X' * (MAX_BYTES + 1)):
            with self.subTest(length=len(payload)), self.assertRaises(StatementParseError):
                parse_bank_statement(payload)

    def test_decimal_comma_and_space_grouping_preserve_exact_cents(self):
        header = HEADER.replace('1 000.00', '1\xa0000,00').replace('1 050.50', '1\xa0050,50')
        result = parse_bank_statement(raw(header, OUT.replace('150.25','150,25'), IN.replace('200.75','200,75'),
                                          FOOTER.replace('150.25','150,25').replace('200.75','200,75')))
        self.assertTrue(result['ready'])
        self.assertEqual(result['calculated_closing_balance'], '1050.50')

    def test_overdraft_balance_does_not_turn_negative_transactions_into_income(self):
        header=HEADER.replace('1 000.00', '-1 000.00').replace('1 050.50', '-949.50')
        result=parse_bank_statement(raw(header, OUT, IN, FOOTER))
        self.assertTrue(result['ready'])
        self.assertEqual((result['opening_balance'],result['closing_balance']),('-1000.00','-949.50'))

    def test_values_outside_database_or_application_limits_are_not_ready(self):
        # An otherwise balanced statement whose balances overflow signed bigint cents.
        header=HEADER.replace('1 000.00', '100 000 000 000 000 000.00').replace('1 050.50', '100 000 000 000 000 050.50')
        result=parse_bank_statement(raw(header, OUT, IN, FOOTER))
        self.assertTrue(result['balance_matches'])
        self.assertFalse(result['storage_safe'])
        self.assertFalse(result['ready'])
        # A valid bigint amount can still exceed the website's own transaction limit.
        header=HEADER.replace('1 000.00', '200 000 000 000 000.00').replace('1 050.50', '99 999 999 999 999.99')
        out=OUT.replace('150.25', '100 000 000 000 000.01')
        footer=FOOTER.replace('150.25', '100 000 000 000 000.01').replace('200.75', '')
        result=parse_bank_statement(raw(header, out, footer))
        self.assertTrue(result['storage_safe'])
        self.assertFalse(result['operation_limit_safe'])
        self.assertFalse(result['ready'])


if __name__ == '__main__':
    unittest.main()
