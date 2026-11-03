"""Precise IABS loan-interest recognition, separate from fees and principal."""
import re
from types import SimpleNamespace

from fastapi import HTTPException

from .bank_statement_charge_categories import charge_category
from .bank_statement_fees import _classify_charge
from .services import money, post_ledger


IABS_INTEREST = re.compile(r'^\s*01014\s*(?:кредит\s*)?фоиз\w*(?:\s|$)', re.I)
PRINCIPAL = re.compile(r'основн\w*\s+долг|тело\s+кредит|principal|асосий\s+(?:қарз|карз)|asosiy\s+qarz', re.I)


def exact_loan_interest(batch, line):
    return bool(batch.owner_inn and line.correspondent_inn == batch.owner_inn
                and str(line.correspondent_account or '').startswith('16309')
                and IABS_INTEREST.search(line.purpose or '')
                and not PRINCIPAL.search(line.purpose or ''))


def classify_interest(s, batch, line, account, user, opening_ok, context=None):
    # Manual classifications of another kind are left intact by the common
    # checks. A broad 'проценты' mention alone never authorizes posting.
    if line.direction != 'out' or not exact_loan_interest(batch, line):
        return None
    return _classify_charge(s, batch, line, account, None, user, opening_ok,
                            'interest', False, True, context)


def post_interest(s, batch, line, account, user, opening_ok, *, confirmed_decision=False):
    """Recheck under the caller's confirmed write guard and nested savepoint."""
    if not confirmed_decision:
        raise HTTPException(403, 'Проценты можно провести только после подтверждения выписки.')
    verdict = classify_interest(s, batch, line, account, user, opening_ok)
    if not verdict or verdict['status'] != 'interest_candidate':
        raise HTTPException(409, verdict['reason'] if verdict else 'Операция не распознана как проценты по кредиту.')
    category_id = charge_category(s, batch, 'interest', create=True)['category_id']
    data = SimpleNamespace(account_id=account.id, to_account_id=None, category_id=category_id,
        kind='out', amount=money(line.amount), date=line.date, counterparty=line.correspondent_name,
        reference='BANK-' + line.fingerprint, note='Проценты по кредиту, документ ' + line.document_number,
        request_id=None, request_version=None, receipt_id=None)
    return post_ledger(s, user, data, bank_statement_import=True)
