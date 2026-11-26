"""IABS personal-account report: daily sections, three physical rows per payment."""
import re
from decimal import Decimal

from .bank_statement_parser import (
    MAX_ROWS, StatementParseError, _amount, _date, parse_statement_body,
)


DATE = r'(\d{2}\.\d{2}\.\d{4})'
HEADER = ['дата', 'дата', 'номер', 'корреспондент: банк/счет/инн наименование назначение', 'дебет', 'кредит']
SUBHEADER = ['проводки', 'опер. дня', 'документа', 'платежа', '', '']
PARTY = r'МФО:\s*(\d{1,20})\s+Сч[её]т:\s*(\d{1,34})\s+ИНН:\s*(\d{0,20})'


def account_heading(text):
    return bool(re.fullmatch(r'Лицевой сч[её]т:\s*\d{8,34}', text, re.I))


def _error(line, reason):
    return StatementParseError(f'Строка {line} выписки по операционным дням: {reason}.')


def _balance(row, line):
    debit, credit = (_amount(value, blank=True, signed=True) for value in row[4:6])
    if not any(row[4:6]) or (debit and credit):
        raise _error(line, 'ожидался один входящий или исходящий остаток')
    return credit - debit


def _match(pattern, value, line):
    match = re.fullmatch(pattern, value, re.I)
    if not match:
        raise _error(line, 'не распознаны реквизиты или дата дневного блока')
    return match.groups()


def parse_operating_days(table, *, allow_empty):
    """Use the operational date for the ledger and verify the bank's controls for every day."""
    numbered = [(i, row[:6]) for i, row in enumerate(table, 1) if any(row)]
    pos = 0

    def take(allowed):
        nonlocal pos
        if pos == len(numbered):
            raise _error(len(table) + 1, 'дневной блок оборван')
        line, row = numbered[pos]
        pos += 1
        if any(value for i, value in enumerate(row) if i not in allowed):
            raise _error(line, 'лишние данные в строке')
        return line, row

    numbered_payments = []
    identity = period = opening = closing = previous_day = bank_title = None
    total_out = total_in = Decimal(0)
    while pos < len(numbered):
        line, row = take({0})
        if not row[0] or (bank_title is not None and bank_title != row[0]):
            raise _error(line, 'изменился заголовок банка')
        bank_title = row[0]
        line, row = take({0})
        number, = _match(r'Лицевой сч[её]т:\s*(\d{8,34})', row[0], line)
        line, row = take({0})
        client, inn = _match(r'Клиент\s+(\d{1,34})\s+ИНН\s*:?\s*(\d{1,20})', row[0], line)
        current_identity = (number, f'Клиент {client}', inn)
        line, row = take({0})
        start, end = map(_date, _match(r'Период выписки с\s+' + DATE + r'\s+по\s+' + DATE, row[0], line))
        if start > end or (identity is not None and (identity != current_identity or period != (start, end))):
            raise _error(line, 'различаются счёт, владелец или период выписки')
        identity, period = current_identity, (start, end)
        line, row = take({0})
        _date(_match(r'Дата последней проводки\s*:\s*' + DATE, row[0], line)[0])
        line, row = take({0, 4, 5})
        day = _date(_match(r'Входящий остаток на\s+' + DATE, row[0], line)[0])
        day_open = _balance(row, line)
        if not start <= day <= end or (previous_day and day <= previous_day):
            raise _error(line, 'нарушен порядок операционных дней')
        if closing is not None and closing != day_open:
            raise _error(line, 'входящий остаток отличается от остатка предыдущего дня')
        if opening is None:
            opening = day_open
        previous_day = day
        for expected in (HEADER, SUBHEADER):
            line, row = take(range(6))
            if [c.casefold().replace('ё', 'е') for c in row] != expected:
                raise _error(line, 'не распознаны заголовки столбцов')
        day_out = day_in = Decimal(0)
        while True:
            line, row = take(range(6))
            if row[0].casefold() in {'сумма оборотов', 'всего оборотов'}:
                if any(row[1:4]) or not any(row[4:]) or (
                    _amount(row[4], blank=True), _amount(row[5], blank=True)
                ) != (day_out, day_in):
                    raise _error(line, 'суммы платежей расходятся с оборотами за день')
                break
            if len(numbered_payments) >= MAX_ROWS:
                raise _error(line, 'слишком много платежей')
            _date(row[0].split(' ')[0])  # Booking date can fall outside the statement period.
            if _date(row[1]) != day:
                raise _error(line, 'дата операционного дня отличается от заголовка')
            mfo, account, party_inn = _match(PARTY, row[3], line)
            _, name = take({3})
            purpose_line, purpose = take({3})
            if not purpose[3]:
                raise _error(purpose_line, 'не указано назначение платежа')
            numbered_payments.append((line, [row[1], row[2], mfo, account, name[3], party_inn,
                                             purpose[3], row[4], row[5]]))
            day_out += _amount(row[4], blank=True)
            day_in += _amount(row[5], blank=True)
        line, row = take({0, 4, 5})
        end_day, = _match(r'(?:Исходящий остаток на|Остаток на конец дня)\s+' + DATE, row[0], line)
        closing = _balance(row, line)
        if _date(end_day) != day or day_open + day_in - day_out != closing:
            raise _error(line, 'не совпадают дата или остаток на конец дня')
        total_out += day_out
        total_in += day_in
    numbered_payments.append((len(table) + 1, ['Итоговый оборот', str(total_out), str(total_in)]))
    return parse_statement_body(numbered_payments, identity, *period, opening, closing, allow_empty=allow_empty)
