"""Block manual request payments while a confirmed statement is unreconciled.

One durable marker per company/account/batch prevents resolving one statement
from accidentally releasing another. Callers must hold the shared write guard;
the account's regular journal correction routes remain untouched.
"""
import json

from fastapi import HTTPException
from sqlalchemy import select

from .db import Account, Ledger, Setting
from .bank_statement_models import BankStatementBatch, BankStatementLine

CREATED_BANK_STATUSES = frozenset({'auto_paid', 'manual_paid', 'auto_income_posted',
    'auto_fee_posted', 'auto_interest_posted', 'auto_outgoing_posted',
    'historical_transfer_posted', 'historical_request_paid', 'manual_distinct_income'})
REQUEST_PAID_BANK_STATUSES = frozenset({'auto_paid', 'paid_match', 'manual_paid',
    'manual_match', 'historical_request_paid'})


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


def refresh_reconciliation_after_reversal(s, user, entry):
    """A reversed bank fact is pending again, including its conservative debit hold.

    Called under the same write guard after flushing the inverse. Cover both
    transfer accounts and later statement openings, not just directly linked
    rows. Whole-statement deletion invokes this only after all inverses are
    flushed and its own batch is marked deleted, so only surviving batches refresh.
    """
    from .bank_statement_api import _line_data, _posting_summary
    from .services import get
    affected = {entry.account_id, entry.to_account_id} - {None}
    for batch in s.scalars(select(BankStatementBatch).where(
            BankStatementBatch.status == 'committed', BankStatementBatch.source_ready.is_(True),
            BankStatementBatch.account_id.in_(affected), BankStatementBatch.period_to >= entry.date)):
        account = get(s, Account, batch.account_id)
        lines = list(s.scalars(select(BankStatementLine).where(BankStatementLine.batch_id == batch.id)))
        summary = _posting_summary(s, batch, lines, account, [_line_data(line) for line in lines], scope='result')
        complete = bool(summary['partial_allowed'] and summary['pending']['count'] == 0
            and summary['ledger_closing'] == summary['bank_closing'])
        set_statement_reconciliation(s, batch, complete, user)


def accounted_line_ids(s, lines, account):
    """Bank rows backed by a unique, active ledger movement on the correct side."""
    from collections import Counter
    from .services import deleted_ledger_ids, effective_cashflows
    ids = {line.ledger_id for line in lines if line.ledger_id is not None}
    entries = {entry.id: entry for entry in s.scalars(effective_cashflows(s).where(
        Ledger.id.in_(ids)))} if ids else {}
    deleted = deleted_ledger_ids(s)
    ties = Counter((line.ledger_id, line.direction) for line in lines if line.ledger_id is not None)
    from .bank_statement_cash_funding import MATCHED as FUNDING_MATCHED, accounted_funding_line_ids
    result = accounted_funding_line_ids(s, lines, account)
    for line in lines:
        # A gross bank debit can represent the net transfer plus its fee. The
        # compound validator proves both active legs instead of comparing the
        # gross source amount to just the transfer's net Ledger amount.
        if line.status == FUNDING_MATCHED:
            continue
        entry = entries.get(line.ledger_id)
        if (not entry or entry.id in deleted or line.account_id != account.id
                or line.company_id != account.company_id or entry.date != line.date or entry.amount != line.amount
                or ties[(entry.id, line.direction)] != 1):
            continue
        same_side = ((entry.kind == line.direction and entry.account_id == account.id)
            or (entry.kind == 'transfer' and ((line.direction == 'out' and entry.account_id == account.id)
                or (line.direction == 'in' and entry.to_account_id == account.id))))
        if same_side:
            result.add(line.id)
    return result


def read_pending_bank_debit_holds(s, accounts, upto=None, *, marker_counts=None):
    """Skip empty marker lookups in one read response, without caching money.

    Counts must come from this session's selected company, independently of any
    role-based presentation filtering. Marked accounts retain the full scalar
    reconciliation checks; payment/write callers continue using that helper.
    """
    accounts = list(accounts)
    company_id = s.info.get('company_id')
    if company_id is None:
        return {a.id: pending_bank_debit_hold(s, a, upto) for a in accounts}
    if any(a.company_id != company_id for a in accounts):
        raise HTTPException(404, 'Счёт не найден в выбранной компании.')
    if not accounts:
        return {}
    counts = unreconciled_account_counts(s) if marker_counts is None else marker_counts
    return {a.id: pending_bank_debit_hold(s, a, upto) if counts.get(a.id, 0) else 0
            for a in accounts}


def pending_bank_debit_hold(s, account, upto=None):
    """Minor-unit hold for unaccounted bank debits, never credit pending income.

    The raw ledger remains the financial record. Manual spending/availability
    callers subtract this hold; signed statement processors keep their ordinary
    raw balance checks because they are accounting for these same bank debits.
    Overlapping unresolved statements may conservatively reserve twice.
    """
    from .clock import today
    markers = unreconciled_statements(s, account)
    if not markers:
        return 0
    batch_ids = []
    for marker in markers:
        suffix = marker.key.rsplit(':', 1)[-1]
        if suffix.isdigit():
            batch_ids.append(int(suffix))
    if not batch_ids:
        return 0
    lines = list(s.scalars(select(BankStatementLine).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(
        BankStatementBatch.id.in_(batch_ids), BankStatementBatch.status == 'committed',
        BankStatementBatch.account_id == account.id, BankStatementBatch.company_id == account.company_id,
        BankStatementLine.account_id == account.id, BankStatementLine.company_id == account.company_id,
        BankStatementLine.direction == 'out', BankStatementLine.date <= (upto or today()))))
    accounted = accounted_line_ids(s, lines, account)
    return sum(line.amount for line in lines if line.id not in accounted)
