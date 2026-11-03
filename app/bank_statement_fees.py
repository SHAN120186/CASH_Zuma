"""Conservative classification and posting of identifiable bank commissions.

The caller owns the write transaction, statement confirmation and savepoint.
No financial changes occur when evaluating a candidate.  This module recognises
only narrow bank signatures with matching account/INN/date/amount evidence;
an account's own INN or an isolated percent sign is not enough.
"""
import re
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select

from .bank_statement_models import BankStatementBatch, BankStatementLine
from .bank_statement_charge_categories import charge_category
from .clock import today
from .category_catalog import catalog
from .db import Ledger, PaymentRequest
from .bank_statement_permissions import can_post_statement
from .security import perms_of
from .services import get, money, post_ledger


SWIFT_COMMISSION = re.compile(r'^\s*\d+\s+погаш\.\s*ком\.\s*swift(?:\s|\-|$)', re.I)
REQUEST_NUMBER = re.compile(r'(?<![A-Za-z0-9])CF-0*[1-9][0-9]*(?![A-Za-z0-9])', re.I)
IABS_FEE = re.compile(
    r'^\s*00111\s+B[CС]\s+(\d{2}\.\d{2}\.\d{4})\s+погашение\s+'
    r'(?:Исх\.плат\.документ|Пополнение\s+корп\.пл\.карты\s*\(КИБ\))'
    r'\s*-\s*(\d+(?:[.,]\d{1,2})?)\s+от\s+суммы\s+\d', re.I)


def exact_iabs_fee(batch, line):
    match = IABS_FEE.search(line.purpose or '')
    if (not match or not batch.owner_inn or line.correspondent_inn != batch.owner_inn
            or not str(line.correspondent_account or '').startswith('16401')):
        return False
    try:
        return match[1] == line.date.strftime('%d.%m.%Y') and Decimal(match[2].replace(',', '.')) * 100 == line.amount
    except InvalidOperation:
        return False


def _review(reason):
    return {'status': 'review', 'reason': reason, 'ledger_id': None}


def _valid_category(s, batch, category_id):
    """Require an explicitly selected, active expense article of this company."""
    if not isinstance(category_id, int) or isinstance(category_id, bool) or category_id <= 0:
        return False
    return any(c.id == category_id and c.company_id == batch.company_id and c.type == 'outcome'
               for c, retired in catalog(s, batch.company_id) if not retired)


def prepare_fee_context(s, batch, lines, account, category_id, *, charge_kind='fee'):
    """Snapshot read-only fee checks for preview; never use across a write.

    The posting path deliberately performs fresh checks after each operation.
    """
    days = {line.date for line in lines if line.direction == 'out'}
    if not days:
        return {'key': (batch.id, account.id, category_id), 'category_valid': False,
                'requests': [], 'bank': [], 'ledger': [], 'reversed': set(), 'used': []}
    requests = list(s.scalars(select(PaymentRequest).where(
        PaymentRequest.account_id == account.id, PaymentRequest.due_date.in_(days))))
    bank = list(s.execute(select(BankStatementLine, BankStatementBatch.status).join(
        BankStatementBatch, BankStatementBatch.id == BankStatementLine.batch_id).where(
        BankStatementLine.account_id == account.id, BankStatementLine.date.in_(days))))
    ledger = list(s.scalars(select(Ledger).where(Ledger.account_id == account.id, Ledger.kind == 'out',
        Ledger.date.in_(days), Ledger.reversal_of.is_(None))))
    ids = [entry.id for entry in ledger]
    reversed_ids = set(s.scalars(select(Ledger.reversal_of).where(Ledger.reversal_of.in_(ids)))) if ids else set()
    used = list(s.execute(select(BankStatementLine.id, BankStatementLine.ledger_id).join(
        BankStatementBatch, BankStatementBatch.id == BankStatementLine.batch_id).where(
        BankStatementLine.ledger_id.in_(ids), BankStatementBatch.status == 'committed'))) if ids else []
    automatic = None
    if category_id is None:
        try:
            automatic = charge_category(s, batch, charge_kind)
        except HTTPException as exc:
            automatic = {'error': str(exc.detail)}
    return {'key': (batch.id, account.id, category_id), 'category_valid': _valid_category(s, batch, category_id),
            'automatic': automatic, 'charge_kind': charge_kind,
            'requests': requests, 'bank': bank, 'ledger': ledger, 'reversed': reversed_ids, 'used': used}


def classify_fee(s, batch, line, account, category_id, user, opening_ok, context=None):
    """Return None for non-fees, otherwise a safe candidate, link or review.

    ``opening_ok`` is computed by the common bank-import workflow against the
    site's opening-of-day balance before any statement operation is posted.
    Category ID is supplied and confirmed separately for the selected company.
    """
    reviewed_fee = (line.status == 'reviewed' and line.classification == 'bank_fee'
                    and line.reviewed_by is not None)
    swift_fee = bool(SWIFT_COMMISSION.search(line.purpose or ''))
    iabs_fee = exact_iabs_fee(batch, line)
    if line.direction != 'out' or not (reviewed_fee or swift_fee or iabs_fee):
        return None
    return _classify_charge(s, batch, line, account, category_id, user, opening_ok,
                            'fee', reviewed_fee, iabs_fee or category_id is None, context)


def _classify_charge(s, batch, line, account, category_id, user, opening_ok,
                     kind, reviewed_charge, automatic, context=None):
    classification = 'bank_fee' if kind == 'fee' else 'bank_interest'
    # Previously classified commissions are eligible for a deliberate retry;
    # do not silently change a human classification as transfer or other.
    if ((line.status == 'reviewed' or line.reviewed_by is not None)
            and line.classification != classification):
        return None
    if line.status not in ('review', 'reviewed', f'{kind}_candidate', f'{kind}_existing') or line.uncertain:
        return _review('Возможный повтор операции: нужна проверка бухгалтера.')
    if (not batch.source_ready or batch.company_id != account.company_id or
            line.company_id != batch.company_id or line.account_id != account.id or
            line.batch_id != batch.id or account.id != batch.account_id or
            account.archived or account.kind != 'bank' or account.currency != batch.currency):
        return _review('Счёт или выписка изменились: нужна проверка бухгалтера.')
    # A previously recorded, explicit accountant classification is stronger
    # evidence than the narrow automatic SWIFT signature; it may describe a
    # bank charge against another correspondent or without an INN.
    if not reviewed_charge and (not batch.owner_inn or batch.owner_inn != line.correspondent_inn):
        return _review('Комиссия не подтверждена реквизитами выписки.')
    if REQUEST_NUMBER.search(line.purpose or '') or line.request_id is not None:
        return _review('В операции указан номер заявки: нужна проверка бухгалтера.')
    if line.amount <= 0 or line.date < account.opening_date or line.date > today():
        return _review('Дата или сумма банковского списания требует проверки.')
    if not opening_ok:
        return _review('Начальный остаток выписки не совпадает с остатком сайта.')
    if context is not None and context['key'] != (batch.id, account.id, category_id):
        raise ValueError('Fee preview context does not match the statement.')
    metadata = {}
    if automatic:
        metadata = (context.get('automatic') if context is not None and context.get('charge_kind') == kind else None)
        if metadata is None:
            try:
                metadata = charge_category(s, batch, kind)
            except HTTPException as exc:
                return _review(str(exc.detail))
        if metadata.get('error'):
            return _review(metadata['error'])
        category_id = metadata['category_id']
        category_valid = True  # A missing technical article is created only when posting.
    else:
        category_valid = context['category_valid'] if context is not None else _valid_category(s, batch, category_id)
        metadata = {'category_id': category_id}
    if not category_valid:
        return _review('Выберите действующую расходную статью комиссии этой компании.')
    if not can_post_statement(user):
        return _review('Для проведения комиссии нужно право изменения операций.')
    if not line.document_number or not line.correspondent_name or len(line.correspondent_name) > 160:
        return _review('Неполные реквизиты комиссии: нужна проверка бухгалтера.')

    # No inference from a single candidate where any request could describe the
    # same withdrawal. Include unapproved/hidden requests in this check.
    request_query = select(PaymentRequest.id).where(
        PaymentRequest.account_id == account.id, PaymentRequest.amount == line.amount,
        PaymentRequest.due_date == line.date)
    if line.correspondent_inn:
        request_query = request_query.where(PaymentRequest.counterparty_inn.in_((line.correspondent_inn, '')))
    possible_request = (any(r.amount == line.amount and r.due_date == line.date and
        (not line.correspondent_inn or r.counterparty_inn in (line.correspondent_inn, ''))
        for r in context['requests']) if context is not None else s.scalar(request_query.limit(1)))
    if possible_request:
        return _review('Возможна связь с заявкой: нужна проверка бухгалтера.')

    # Distinct document numbers within one statement are distinct movements.
    # The same amount/date/document, or a similar line in an earlier statement,
    # remains ambiguous because document numbers are not globally unique.
    duplicate_query = select(BankStatementLine.id).join(
        BankStatementBatch, BankStatementBatch.id == BankStatementLine.batch_id).where(
        BankStatementLine.id != line.id, BankStatementLine.account_id == account.id,
        BankStatementLine.date == line.date, BankStatementLine.direction == 'out',
        (((BankStatementLine.batch_id == batch.id) & (BankStatementLine.amount == line.amount) &
          (BankStatementLine.document_number == line.document_number)) |
         ((BankStatementLine.batch_id != batch.id) & (BankStatementBatch.status == 'committed') &
          ((BankStatementLine.amount == line.amount) |
           (BankStatementLine.document_number == line.document_number))))).limit(1)
    duplicate = (any(other.id != line.id and other.direction == 'out' and other.date == line.date and
        ((other.batch_id == batch.id and other.amount == line.amount and
          other.document_number == line.document_number) or
         (other.batch_id != batch.id and status == 'committed' and
          (other.amount == line.amount or other.document_number == line.document_number)))
        for other, status in context['bank']) if context is not None else s.scalar(duplicate_query))
    if duplicate:
        return _review('Несколько похожих списаний: нужна проверка бухгалтера.')

    # Ledger lacks an INN. Only a unique exact document and counterparty link is
    # safe; any other same-date/same-amount ledger movement blocks a new entry.
    # A different statement row already linked to its ledger entry with a
    # distinct bank document cannot be this row's existing transaction.
    linked_query = select(BankStatementLine.ledger_id).join(
        BankStatementBatch, BankStatementBatch.id == BankStatementLine.batch_id).where(
        BankStatementLine.id != line.id, BankStatementLine.account_id == account.id,
        BankStatementLine.date == line.date, BankStatementLine.amount == line.amount,
        BankStatementLine.document_number != line.document_number, BankStatementLine.ledger_id.is_not(None),
        ((BankStatementLine.batch_id == batch.id) | (BankStatementBatch.status == 'committed')))
    linked_other = ({other.ledger_id for other, status in context['bank']
        if other.id != line.id and other.date == line.date and other.amount == line.amount and
        other.document_number != line.document_number and other.ledger_id is not None and
        (other.batch_id == batch.id or status == 'committed')}
        if context is not None else set(s.scalars(linked_query)))
    ledger_query = select(Ledger).where(
        Ledger.account_id == account.id, Ledger.kind == 'out', Ledger.date == line.date,
        ((Ledger.amount == line.amount) | (Ledger.reference == line.document_number)),
        Ledger.reversal_of.is_(None))
    possible = [entry for entry in (context['ledger'] if context is not None else s.scalars(ledger_query))
        if entry.date == line.date and (entry.amount == line.amount or entry.reference == line.document_number)
        if entry.id not in linked_other or entry.reference == line.document_number]
    if possible:
        if len(possible) != 1:
            return _review('В журнале найдены похожие операции: нужна проверка.')
        entry = possible[0]
        reversed_entry = (entry.id in context['reversed'] if context is not None else
                          s.scalar(select(Ledger.id).where(Ledger.reversal_of == entry.id).limit(1)))
        used_query = select(BankStatementLine.id).join(
            BankStatementBatch, BankStatementBatch.id == BankStatementLine.batch_id).where(
            BankStatementLine.id != line.id, BankStatementLine.ledger_id == entry.id,
            BankStatementBatch.status == 'committed').limit(1)
        if context is not None:
            # A linked target line does not itself count as a different use.
            used = any(other_id != line.id and ledger_id == entry.id for other_id, ledger_id in context['used'])
        else:
            used = s.scalar(used_query)
        if (reversed_entry or used or entry.amount != line.amount or entry.reference != line.document_number or
                entry.category_id != category_id or entry.counterparty.strip().casefold() !=
                line.correspondent_name.strip().casefold()):
            return _review('В журнале найдена похожая операция: нужна проверка.')
        return {'status': f'{kind}_existing', 'reason': '', 'ledger_id': entry.id, **metadata}
    return {'status': f'{kind}_candidate', 'reason': '', 'ledger_id': None, **metadata}


def post_fee(s, batch, line, account, category_id, user, opening_ok, *, confirmed_decision=False):
    """Recheck the candidate and use the ordinary ledger safeguards.

    Call within the shared write guard and a nested savepoint.  The caller must
    attach the returned entry and audit the bank-statement row atomically.
    """
    if 'write' not in perms_of(user) and not confirmed_decision:
        raise HTTPException(403, 'Комиссию можно провести только после подтверждения выписки.')
    verdict = classify_fee(s, batch, line, account, category_id, user, opening_ok)
    if not verdict or verdict['status'] != 'fee_candidate':
        raise HTTPException(409, verdict['reason'] if verdict else 'Операция не распознана как комиссия.')
    if verdict.get('category_source') == 'bank_fee_auto':
        if not confirmed_decision:
            raise HTTPException(403, 'Автоматическая банковская статья доступна после подтверждения выписки.')
        category_id = charge_category(s, batch, 'fee', create=True)['category_id']
    op = SimpleNamespace(account_id=account.id, to_account_id=None, category_id=category_id,
        kind='out', amount=money(line.amount), date=line.date, counterparty=line.correspondent_name,
        reference='BANK-' + line.fingerprint, note='Банковская комиссия, документ ' + line.document_number,
        request_id=None, request_version=None, receipt_id=None)
    return post_ledger(s, user, op, bank_statement_import=True)
