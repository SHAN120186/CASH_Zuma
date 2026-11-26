"""Decode a bounded IABS XLSX for statement preview or read-only period comparison."""
import io
import math
import posixpath
import re
import zipfile
import zlib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from openpyxl.utils.datetime import CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900, from_excel

from .bank_operating_day_parser import account_heading, parse_operating_days

from .bank_statement_parser import (
    MAX_BYTES, MAX_CELL_CHARS, MAX_CELLS, MAX_DATABASE_AMOUNT, MAX_ROWS,
    StatementParseError, _clean, parse_bank_statement, parse_statement_tables,
)

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
MAX_EXPANDED = 16 * 1024 * 1024


def _worksheet(archive):
    entries = archive.infolist()
    names = {entry.filename for entry in entries}
    if (len(entries) > 128 or len(names) != len(entries)
            or sum(entry.file_size for entry in entries) > MAX_EXPANDED
            or any(entry.flag_bits & 1 for entry in entries)):
        raise StatementParseError('Структура или распакованный размер банковского XLSX недопустимы.')
    workbook = ET.fromstring(archive.read('xl/workbook.xml'), forbid_dtd=True)
    sheets = workbook.findall(f'{NS}sheets/{NS}sheet')
    if len(sheets) != 1:
        raise StatementParseError('Для сверки нужен один лист выписки по одному банковскому счёту.')
    props = workbook.find(f'{NS}workbookPr')
    epoch = CALENDAR_MAC_1904 if props is not None and props.get('date1904') in {'1', 'true'} else CALENDAR_WINDOWS_1900
    relationships = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'), forbid_dtd=True)
    targets = [rel.attrib['Target'] for rel in relationships
               if rel.get('Id') == sheets[0].attrib[f'{REL}id']
               and rel.get('Type') == 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet'
               and rel.get('TargetMode', 'Internal') == 'Internal']
    if len(targets) != 1:
        raise StatementParseError('Не удалось определить лист банковской выписки.')
    sheet_path = posixpath.normpath(posixpath.join('xl', targets[0])).lstrip('/')
    if not sheet_path.startswith('xl/worksheets/') or sheet_path not in names:
        raise StatementParseError('Не удалось прочитать лист банковской выписки.')
    return sheet_path, epoch


def _string(node):
    value = ''.join(text.text or '' for text in node.iter(f'{NS}t'))
    if len(value) > MAX_CELL_CHARS:
        raise StatementParseError('Слишком длинная ячейка банковской выписки.')
    return _clean(value)


def _number_as_money(text):
    # Excel stores binary doubles. Only discard binary representation noise
    # near a whole cent; a genuine fractional cent is not a bank amount.
    value = Decimal(text)
    if not value.is_finite() or value.copy_abs() > MAX_DATABASE_AMOUNT:
        raise StatementParseError('Некорректная сумма в банковской выписке.')
    cents = value.quantize(Decimal('0.01'))
    tolerance = min(Decimal('0.00001'), Decimal.from_float(math.ulp(float(value))))
    if abs(value - cents) > tolerance:
        raise StatementParseError('Сумма в банковской выписке содержит доли тийина.')
    return format(cents, '.2f')


def _cell_text(cell, column, strings, epoch, daily=False):
    if cell.find(f'{NS}f') is not None:
        raise StatementParseError('Для сверки нужен банковский XLSX с исходными значениями без формул.')
    kind = cell.get('t', 'n')
    value = cell.findtext(f'{NS}v', '')
    if kind == 'inlineStr':
        return _string(cell)
    if not value:
        return ''
    if kind == 's':
        if not value.isascii() or not value.isdigit() or len(value) > 7 or int(value) >= len(strings):
            raise StatementParseError('Неверная ссылка на текст ячейки банковского XLSX.')
        return strings[int(value)]
    if kind == 'str':
        if len(value) > MAX_CELL_CHARS:
            raise StatementParseError('Слишком длинная ячейка банковской выписки.')
        return _clean(value)
    date_column = column == 'A' or (daily and column == 'B')
    booking_time = daily and column == 'A'
    if kind == 'd' and date_column:
        day = datetime.fromisoformat(value)
        if not booking_time and day.time() != datetime.min.time():
            raise StatementParseError('Неверная дата в банковской выписке.')
        return day.strftime('%d.%m.%Y')
    if kind != 'n' or len(value) > 48:
        raise StatementParseError('Недопустимый тип ячейки банковского XLSX.')
    if column in ({'E', 'F'} if daily else {'H', 'I'}):
        return _number_as_money(value)
    if date_column:
        if not re.fullmatch(r'[0-9]{1,7}(?:\.[0-9]+)?' if daily else r'[0-9]{1,7}', value):
            raise StatementParseError('Неверная дата в банковской выписке.')
        serial = Decimal(value)
        if not booking_time and int(serial) != serial:
            raise StatementParseError('Неверная дата операционного дня в банковской выписке.')
        day = from_excel(int(serial), epoch)
        if not isinstance(day, date):
            raise StatementParseError('Неверная дата в банковской выписке.')
        return day.strftime('%d.%m.%Y')
    # Numbers longer than 15 significant digits cannot identify an account
    # reliably in an Excel numeric cell. IABS exports those as exact strings.
    if not re.fullmatch(r'[0-9]{1,15}', value):
        raise StatementParseError('Реквизиты с длинными номерами должны храниться в XLSX как текст.')
    return value


def _xlsx_table(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        sheet_path, epoch = _worksheet(archive)
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            with archive.open('xl/sharedStrings.xml') as source:
                for _, node in ET.iterparse(source, events=('end',), forbid_dtd=True):
                    if node.tag == f'{NS}si':
                        if len(strings) >= MAX_CELLS:
                            raise StatementParseError('Слишком много ячеек в банковской выписке.')
                        strings.append(_string(node))
                        node.clear()
        table = []
        daily = False
        with archive.open(sheet_path) as source:
            for _, node in ET.iterparse(source, events=('end',), forbid_dtd=True):
                if node.tag != f'{NS}row':
                    continue
                row_number = int(node.attrib['r'])
                row_limit = 4 * MAX_ROWS + 30 if daily else MAX_ROWS + 30
                if not len(table) < row_number <= row_limit:
                    raise StatementParseError('Неверный порядок или слишком много строк в банковской выписке.')
                while len(table) + 1 < row_number:
                    table.append([])
                cells = [''] * 9
                seen = set()
                for cell in node.findall(f'{NS}c'):
                    ref = cell.attrib['r']
                    if not re.fullmatch(('[A-F]' if daily else '[A-I]') + str(row_number), ref) or ref in seen:
                        raise StatementParseError('Неверные столбцы или повтор ячейки в банковской выписке.')
                    seen.add(ref)
                    cells[ord(ref[0]) - ord('A')] = _cell_text(cell, ref[0], strings, epoch, daily)
                if row_number == 2 and account_heading(cells[0]) and not any(cells[1:]):
                    daily = True
                table.append(cells)
                node.clear()
    return table, daily


def parse_xlsx_statement(raw: bytes, *, allow_empty=False) -> dict:
    """Parse one IABS worksheet under the same validation rules as an HTML statement."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise StatementParseError('Размер банковской выписки недопустим.')
    try:
        table, daily = _xlsx_table(raw)
        if daily:
            return parse_operating_days(table, allow_empty=allow_empty)
    except StatementParseError:
        raise
    except (ValueError, OverflowError, KeyError, InvalidOperation, ParseError, DefusedXmlException,
            OSError, RuntimeError, zipfile.BadZipFile, zlib.error):
        raise StatementParseError('Не удалось прочитать банковскую выписку IABS в формате XLSX.') from None
    return parse_statement_tables([table], allow_empty=allow_empty)


def parse_period_statement(raw: bytes) -> dict:
    """A day without movement can still be compared using the declared balances."""
    if isinstance(raw, bytes) and raw.startswith(b'PK'):
        return parse_xlsx_statement(raw, allow_empty=True)
    return parse_bank_statement(raw, allow_empty=True)
