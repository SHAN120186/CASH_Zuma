"""Read IABS HTML bank statements without executing or rendering source HTML.

The bank uses an ``.XLS`` extension for HTML; this parser deliberately does not
assume that every XLS file is this format. Its return value is JSON-compatible:
all amounts are decimal strings in original units, dates are ISO strings, and
bank identifiers remain text (including leading zeros). No currency is inferred.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import re

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10000
MAX_CELL_CHARS = 10000
MAX_CELLS = (MAX_ROWS + 30) * 10
MAX_DATABASE_AMOUNT = Decimal('92233720368547758.07')  # signed 64-bit minor units
MAX_OPERATION_AMOUNT = Decimal('100000000000000.00')  # existing application limit
_MONEY = re.compile(r'^(?:\d{1,3}(?: \d{3})+|\d+)(?:[.,]\d{1,2})?$')
_DATE = re.compile(r'\d{2}\.\d{2}\.\d{4}')


class StatementParseError(ValueError):
    """Malformed, unsafe, or unsupported statement; contains no private values."""


def _clean(value):
    return re.sub(r'\s+', ' ', value.replace('\xa0', ' ')).strip()


def _date(value):
    value = _clean(value)
    if not _DATE.fullmatch(value):
        raise StatementParseError('Неверная дата в банковской выписке.')
    try:
        return datetime.strptime(value, '%d.%m.%Y').date().isoformat()
    except ValueError:
        raise StatementParseError('Неверная дата в банковской выписке.') from None


def _amount(value, *, blank=False, signed=False):
    value = _clean(value)
    if not value and blank:
        return Decimal('0.00')
    unsigned = value[1:] if signed and value.startswith('-') else value
    if not _MONEY.fullmatch(unsigned) or len(value) > 32:
        raise StatementParseError('Некорректная сумма в банковской выписке.')
    normalized = value.replace(' ', '').replace(',', '.')
    if len(normalized.lstrip('-').split('.', 1)[0]) > 20:
        raise StatementParseError('Слишком большая сумма в банковской выписке.')
    try:
        return Decimal(normalized).quantize(Decimal('0.01'))
    except InvalidOperation:
        raise StatementParseError('Некорректная сумма в банковской выписке.') from None


def _money(value):
    return format(value, '.2f')


class _Tables(HTMLParser):
    """A bounded, inert text extractor. It never renders HTML or fetches URLs."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None
        self.cells = 0
        self.cell_size = 0
        self.ignore_depth = 0

    def handle_decl(self, decl):
        if not decl.lower().startswith('doctype html'):
            raise StatementParseError('Недопустимое содержимое банковской выписки.')

    def unknown_decl(self, data):
        raise StatementParseError('Недопустимое содержимое банковской выписки.')

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {'script', 'iframe', 'object', 'embed', 'form', 'input', 'svg', 'math'}:
            raise StatementParseError('Недопустимое содержимое банковской выписки.')
        if tag == 'style':
            self.ignore_depth += 1
            return
        if self.ignore_depth:
            return
        if tag == 'table':
            if self.table is not None or len(self.tables) >= 10:
                raise StatementParseError('Неподдерживаемая структура банковской выписки.')
            self.table = []
            self.tables.append(self.table)
        elif tag == 'tr' and self.table is not None:
            if len(self.table) > MAX_ROWS + 20:
                raise StatementParseError('Слишком много строк в банковской выписке.')
            self.row = []
            self.table.append(self.row)
            self.cell = None
        elif tag in {'td', 'th'} and self.row is not None:
            self.cells += 1
            if self.cells > MAX_CELLS or len(self.row) >= 32:
                raise StatementParseError('Слишком много ячеек в банковской выписке.')
            self.cell = []
            self.cell_size = 0
            self.row.append(self.cell)
        elif tag == 'br' and self.cell is not None:
            self.cell_size += 1
            if self.cell_size > MAX_CELL_CHARS:
                raise StatementParseError('Слишком длинная ячейка банковской выписки.')
            self.cell.append(' ')

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'style':
            self.ignore_depth = max(0, self.ignore_depth - 1)
            return
        if self.ignore_depth:
            return
        if tag == 'tr':
            self.row, self.cell = None, None
        elif tag == 'table':
            self.table, self.row, self.cell = None, None, None
        elif tag in {'td', 'th'}:
            self.cell = None

    def handle_data(self, data):
        if self.ignore_depth or self.cell is None:
            return
        if any(ord(ch) < 32 and ch not in '\t\n\r' for ch in data):
            raise StatementParseError('Недопустимые символы в банковской выписке.')
        self.cell_size += len(data)
        if self.cell_size > MAX_CELL_CHARS:
            raise StatementParseError('Слишком длинная ячейка банковской выписки.')
        self.cell.append(data)


def _metadata(tables):
    texts = [_clean(''.join(cell)) for table in tables for row in table for cell in row]
    period = []
    opening = []
    closing = []
    account = []
    for text in texts:
        if re.search(r'сведения\s+о\s+работе\s+сч[её]та', text, re.I):
            match = re.search(r'[сc]\s*(\d{2}\.\d{2}\.\d{4})\s+по\s+(\d{2}\.\d{2}\.\d{4})', text, re.I)
            if not match:
                raise StatementParseError('Не указан период банковской выписки.')
            period.append((_date(match.group(1)), _date(match.group(2))))
        match = re.search(r'остаток\s+на\s+начало\s+периода\s*:\s*(.+)', text, re.I)
        if match:
            opening.append(_amount(match.group(1), signed=True))
        match = re.search(r'остаток\s+на\s+конец\s+периода\s*:\s*(.+)', text, re.I)
        if match:
            closing.append(_amount(match.group(1), signed=True))
        match = re.search(r'[сc]ч[её]т\s*:\s*(\d{8,34})\s+(.+?)\s+инн\s*:\s*(\d{1,20})\s*$', text, re.I)
        if match:
            account.append((match.group(1), _clean(match.group(2)), match.group(3)))
    if len(period) != 1 or len(opening) != 1 or len(closing) != 1 or len(account) != 1:
        raise StatementParseError('Не удалось однозначно прочитать счёт, период и остатки выписки.')
    start, end = period[0]
    if start > end:
        raise StatementParseError('Период банковской выписки указан неверно.')
    return account[0], start, end, opening[0], closing[0]


def parse_bank_statement(raw: bytes) -> dict:
    """Parse a bank HTML statement and calculate, but do not change, financial data.

    ``ready`` is false if balances or declared turnovers are missing or do not
    reconcile, or if values exceed application/storage limits. The caller must
    reject writes in those cases and independently verify account and currency.
    """
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise StatementParseError('Размер банковской выписки недопустим.')
    if b'\x00' in raw or re.search(br'<!\s*(?:ENTITY|\[)', raw, re.I):
        raise StatementParseError('Недопустимое содержимое банковской выписки.')
    try:
        text = raw.decode('utf-8-sig') if raw.startswith(b'\xef\xbb\xbf') else raw.decode('cp1251')
    except UnicodeError:
        raise StatementParseError('Не удалось прочитать кодировку банковской выписки.') from None
    if not re.search(r'<\s*html(?:\s|>)', text[:4096], re.I):
        raise StatementParseError('Формат выписки не распознан. Нужна выписка IABS в HTML/XLS.')
    parser = _Tables()
    try:
        parser.feed(text)
        parser.close()
    except StatementParseError:
        raise
    except Exception:
        raise StatementParseError('Не удалось прочитать банковскую выписку.') from None
    (account, owner_name, owner_inn), start, end, opening, closing = _metadata(parser.tables)
    aliases = ('дата', 'номер документа', 'мфо коррес', 'счет корреспондент',
               'наименование коррес', 'инн корреспондент', 'назначение платежа', 'дебет', 'кредит')
    candidates = []
    for table in parser.tables:
        for i, row in enumerate(table):
            cols = [_clean(''.join(c)).lower().replace('ё', 'е') for c in row]
            if len(cols) == 9 and all(cols[n].startswith(label) for n, label in enumerate(aliases)):
                candidates.append((table, i))
    if len(candidates) != 1:
        raise StatementParseError('Не найдены необходимые столбцы банковской выписки.')
    table, header = candidates[0]
    table_number = next(i for i, candidate in enumerate(parser.tables) if candidate is table)
    previous_rows = sum(len(previous) for previous in parser.tables[:table_number])
    rows = []
    debit_total = credit_total = Decimal('0.00')
    declared = None
    for index, row in enumerate(table[header + 1:], start=header + 2):
        cells = [_clean(''.join(cell)) for cell in row]
        if not any(cells):
            continue
        if cells[0].lower().startswith('итоговый оборот'):
            if declared is not None or len(cells) < 3:
                raise StatementParseError('Итог оборотов в выписке указан неверно.')
            declared = (_amount(cells[-2], blank=True), _amount(cells[-1], blank=True))
            continue
        if declared is not None or len(cells) != 9 or len(rows) >= MAX_ROWS:
            raise StatementParseError('Строка банковской выписки имеет неверный формат.')
        day = _date(cells[0])
        if not start <= day <= end:
            raise StatementParseError('Дата операции вне периода банковской выписки.')
        if not cells[1] or len(cells[1]) > 160 or not cells[4] or len(cells[4]) > 240 or len(cells[6]) > 3000:
            raise StatementParseError('Проверьте номер, контрагента и назначение операции.')
        if not re.fullmatch(r'\d{1,20}', cells[2]) or not re.fullmatch(r'\d{1,34}', cells[3]):
            raise StatementParseError('Проверьте МФО и счёт корреспондента в выписке.')
        if cells[5] and not re.fullmatch(r'\d{1,20}', cells[5]):
            raise StatementParseError('Проверьте ИНН корреспондента в выписке.')
        debit, credit = _amount(cells[7], blank=True), _amount(cells[8], blank=True)
        if (debit > 0) == (credit > 0):
            raise StatementParseError('В строке должен быть один положительный дебет или кредит.')
        debit_total += debit
        credit_total += credit
        rows.append({'line': previous_rows + index, 'date': day, 'document_number': cells[1],
                     'correspondent_mfo': cells[2], 'correspondent_account': cells[3],
                     'correspondent_name': cells[4], 'correspondent_inn': cells[5], 'purpose': cells[6],
                     'debit': _money(debit), 'credit': _money(credit),
                     'amount': _money(debit if debit else credit), 'direction': 'out' if debit else 'in'})
    if not rows:
        raise StatementParseError('В банковской выписке нет операций.')
    calculated = opening + credit_total - debit_total
    balance_difference = closing - calculated
    balance_matches = balance_difference == 0
    totals_match = None if declared is None else declared == (debit_total, credit_total)
    storage_safe = all(abs(value) <= MAX_DATABASE_AMOUNT for value in
                       (opening, closing, debit_total, credit_total, calculated, balance_difference))
    operation_limit_safe = all(Decimal(row['amount']) <= MAX_OPERATION_AMOUNT for row in rows)
    return {'account_number': account, 'owner_name': owner_name, 'owner_inn': owner_inn,
            'period_from': start, 'period_to': end, 'currency': None,
            'opening_balance': _money(opening), 'closing_balance': _money(closing),
            'debit_total': _money(debit_total), 'credit_total': _money(credit_total),
            'calculated_closing_balance': _money(calculated), 'balance_difference': _money(balance_difference),
            'balance_matches': balance_matches, 'declared_totals_match': totals_match,
            'storage_safe': storage_safe, 'operation_limit_safe': operation_limit_safe,
            'ready': balance_matches and totals_match is True and storage_safe and operation_limit_safe, 'rows': rows}
