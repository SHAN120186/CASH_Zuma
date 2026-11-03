"""Block manual request payments while a confirmed statement is unreconciled.

One durable marker per company/account/batch prevents resolving one statement
from accidentally releasing another. Callers must hold the shared write guard;
the account's regular journal correction routes remain untouched.
"""
import json

from fastapi import HTTPException
from sqlalchemy import select

from .db import Account, Setting


def _prefix(company_id, account_id):
    return f'bsu:{company_id}:{account_id}:'


def _key(batch):
    return f'{_prefix(batch.company_id, batch.account_id)}{batch.id}'


def set_statement_reconciliation(s, batch, fully_reconciled, user):
    """Set or clear only this committed statement's marker inside the write transaction."""
    from .services import get, log
    if type(fully_reconciled) is not bool or batch.status != 'committed' or not batch.source_ready:
        raise ValueError('A committed, source-ready statement and an explicit reconciliation result are required.')
    account = get(s, Account, batch.account_id)
    if account.company_id != batch.company_id or (
            s.info.get('company_id') is not None and s.info['company_id'] != batch.company_id):
        raise HTTPException(404, 'Счёт выписки не найден в выбранной компании.')
    key = _key(batch)
    marker = s.get(Setting, key)
    if fully_reconciled:
        if marker:
            s.delete(marker)
            log(s, user, 'Сверена банковская выписка', 'bank_statement', batch.id,
                json.dumps({'account_id': account.id}, ensure_ascii=False))
    else:
        value = json.dumps({'batch_id': batch.id, 'account_id': account.id,
            'company_id': batch.company_id, 'period_from': str(batch.period_from),
            'period_to': str(batch.period_to)}, sort_keys=True, separators=(',', ':'))
        if marker is None:
            s.add(Setting(key=key, value=value))
            log(s, user, 'Банковская выписка требует сверки', 'bank_statement', batch.id,
                json.dumps({'account_id': account.id}, ensure_ascii=False))
        else:
            marker.value = value


def unreconciled_statements(s, account):
    """List markers for this account only, scoped by the current company."""
    if s.info.get('company_id') is not None and s.info['company_id'] != account.company_id:
        raise HTTPException(404, 'Счёт не найден в выбранной компании.')
    prefix = _prefix(account.company_id, account.id)
    return list(s.scalars(select(Setting).where(Setting.key.like(prefix + '%'))))


def unreconciled_account_counts(s):
    """One query for the selected company, rather than one per bank account."""
    company_id = s.info.get('company_id')
    if company_id is None:
        return {}
    prefix = f'bsu:{company_id}:'
    result = {}
    for key in s.scalars(select(Setting.key).where(Setting.key.like(prefix + '%'))):
        pieces = key[len(prefix):].split(':')
        if len(pieces) == 2 and all(piece.isdigit() for piece in pieces):
            account_id = int(pieces[0])
            result[account_id] = result.get(account_id, 0) + 1
    return result


def unreconciled_batch_ids(s):
    """Keep fully reviewed batches reachable until their closing balance is confirmed."""
    company_id = s.info.get('company_id')
    if company_id is None:
        return set()
    prefix = f'bsu:{company_id}:'
    result = set()
    for key in s.scalars(select(Setting.key).where(Setting.key.like(prefix + '%'))):
        pieces = key[len(prefix):].split(':')
        if len(pieces) == 2 and all(piece.isdigit() for piece in pieces):
            result.add(int(pieces[1]))
    return result


def require_reconciled_for_manual_payment(s, account):
    """Only call for manual request payments; never for regular corrections."""
    if unreconciled_statements(s, account):
        raise HTTPException(409, 'Счёт ожидает сверки банковской выписки. '
                            'Сверьте выписку перед новой оплатой заявки.')
