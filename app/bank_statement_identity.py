"""Account and currency checks shared by statement import and comparison."""
import re

from fastapi import HTTPException
from sqlalchemy import select

from .bank_statement_models import BankStatementBatch


def confirmed_bank_binding(s, account, number, owner_inn=None):
    known = s.execute(select(BankStatementBatch.account_id, BankStatementBatch.account_number,
        BankStatementBatch.owner_inn).where(BankStatementBatch.status == 'committed',
        (BankStatementBatch.account_id == account.id) | (BankStatementBatch.account_number == number)).distinct()).all()
    for account_id, bank_number, inn in known:
        if account_id != account.id or bank_number != number:
            raise HTTPException(409, 'Банковский счёт уже связан с другим счётом сайта. Проверьте соответствие.')
        if owner_inn is not None and inn != owner_inn:
            raise HTTPException(409, 'Реквизиты выписки расходятся с подтверждёнными реквизитами счёта.')
    return bool(known)


def check_statement_currency(account, source):
    # Uzbek corporate 202xx accounts carry a currency code in positions 6–8.
    # Only recognized codes in this account family supplement source metadata.
    number = source['account_number']
    encoded = None
    if re.fullmatch(r'202[0-9]{17}', number):
        encoded = {'000': 'UZS', '840': 'USD', '978': 'EUR'}.get(number[5:8])
    if any(value and value != account.currency for value in (source.get('currency'), encoded)):
        raise HTTPException(409, 'Валюта выписки расходится с валютой выбранного счёта.')
