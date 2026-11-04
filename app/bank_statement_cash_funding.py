"""Link bank evidence to existing cash-withdrawal groups without posting twice.

A withdrawal is already represented by a bank-to-transit transfer plus a
separate fee. A bank can show their gross total in one line, or each component
in two lines. Neither layout authorizes another bank expense or cash payment.
"""
from collections import Counter
import json
import re

from fastapi import HTTPException
from sqlalchemy import select

from .bank_statement_models import BankStatementBatch, BankStatementLine
from .db import Account, Ledger, now
from .section_access import can_edit_section
from .security import perms_of

CANDIDATE = 'cash_funding_candidate'
MATCHED = 'cash_funding_matched'
_BINDING = 'cash_funding_binding'
_FEE = re.compile(r'комис|банковск\w*\s+услуг|\b(?:fee|charge|commission)\b|комиссия|банк\w*\s+хизмат', re.I)
_CASH = re.compile(r'налич|снят\w*\s+(?:денег|средств)|\b(?:cash|naqd)\b|банкомат', re.I)


def _name(value):
    return ' '.join((value or '').casefold().split())


def _reference_matches(funding, line):
    reference = _name(funding.withdraw_reference)
    if not reference:
        return False
    if _name(line.document_number) == reference:
        return True
    # A separately numbered bank fee must explicitly cite the withdrawal's
    # document, never merely share an amount, day or the company's INN.
    return bool(_FEE.search(line.purpose or '') and re.search(
        r'(?<![\w])' + re.escape(reference) + r'(?![\w])', _name(line.purpose)))


def binding(line):
    """Read server-authored metadata only on an actually linked source row."""
    if line.status != MATCHED:
        return None
    try:
        value = json.loads(line.review_note)
        if not isinstance(value, dict) or value.get(_BINDING) != 1:
            return None
        if (type(value.get('funding_id')) is not int or value['funding_id'] <= 0
                or value.get('component') not in ('gross', 'net', 'fee')
                or not isinstance(value.get('ledger_ids'), list)
                or not 1 <= len(value['ledger_ids']) <= 2
                or any(type(key) is not int or key <= 0 for key in value['ledger_ids'])
                or len(set(value['ledger_ids'])) != len(value['ledger_ids'])
                or any(not isinstance(value.get(key), str) or not re.fullmatch(r'\d+\.\d{2}', value[key])
                       for key in ('gross_amount', 'fee_amount', 'net_amount'))):
            return None
        return {key: value[key] for key in ('funding_id', 'component', 'ledger_ids',
                                           'gross_amount', 'fee_amount', 'net_amount')}
    except (TypeError, ValueError):
        return None


def _fundings(s, account):
    from .db import CashFunding
    return list(s.scalars(select(CashFunding).where(
        CashFunding.company_id == account.company_id,
        CashFunding.bank_account_id == account.id)))


def _active_group(s, funding, account):
    """Validate the immutable bank legs, including the fee for a gross row."""
    from .services import deleted_ledger_ids, effective_cashflows
    if (funding.company_id != account.company_id or funding.bank_account_id != account.id
            or funding.state not in ('in_transit', 'received') or not funding.withdraw_date
            or funding.gross_amount != funding.net_amount + funding.fee_amount
            or funding.net_amount <= 0 or funding.fee_amount < 0
            or not funding.transfer_ledger_id or not funding.transit_account_id):
        return None
    target_ids = {funding.cash_account_id, funding.transit_account_id}
    targets = {item.id: item for item in s.scalars(select(Account).where(
        Account.id.in_(target_ids), Account.company_id == account.company_id,
        Account.currency == account.currency))}
    cash, transit = targets.get(funding.cash_account_id), targets.get(funding.transit_account_id)
    if not cash or not transit or cash.kind != 'cash' or transit.kind != 'transit':
        return None
    ids = {funding.transfer_ledger_id}
    if funding.fee_amount:
        if not funding.fee_ledger_id:
            return None
        ids.add(funding.fee_ledger_id)
    elif funding.fee_ledger_id is not None:
        return None
    deleted = deleted_ledger_ids(s)
    entries = {entry.id: entry for entry in s.scalars(effective_cashflows(s).where(
        Ledger.id.in_(ids), Ledger.account_id == account.id)) if entry.id not in deleted}
    if set(entries) != ids:
        return None
    transfer = entries[funding.transfer_ledger_id]
    if (transfer.kind != 'transfer' or transfer.to_account_id != funding.transit_account_id
            or transfer.amount != funding.net_amount or transfer.date != funding.withdraw_date
            or transfer.request_id is not None or transfer.receipt_id is not None):
        return None
    if funding.fee_amount:
        fee = entries[funding.fee_ledger_id]
        if (fee.kind != 'out' or fee.amount != funding.fee_amount
                or fee.date != funding.withdraw_date or fee.category_id != funding.fee_category_id
                or fee.request_id is not None or fee.receipt_id is not None):
            return None
    return entries


def _component_ids(funding, component):
    if component == 'fee':
        return [funding.fee_ledger_id]
    return ([funding.transfer_ledger_id] +
            ([funding.fee_ledger_id] if component == 'gross' and funding.fee_amount else []))


def _component(funding, line):
    if line.amount == funding.gross_amount:
        return 'gross'
    if funding.fee_amount and line.amount == funding.fee_amount and _FEE.search(line.purpose or ''):
        return 'fee'
    if funding.fee_amount and line.amount == funding.net_amount and not _FEE.search(line.purpose or ''):
        return 'net'
    return None


def _valid_binding(s, line, account, fundings):
    from .services import money
    value = binding(line)
    if not value or line.direction != 'out' or line.uncertain:
        return None
    funding = fundings.get(value['funding_id'])
    if (funding is None or line.account_id != account.id or line.company_id != account.company_id
            or line.date != funding.withdraw_date or not _reference_matches(funding, line)
            or _component(funding, line) != value['component']
            or line.classification != ('bank_fee' if value['component'] == 'fee' else 'internal_transfer')
            or value['ledger_ids'] != _component_ids(funding, value['component'])
            or any(value[key] != money(getattr(funding, key)) for key in ('gross_amount', 'fee_amount', 'net_amount'))
            or line.ledger_id != value['ledger_ids'][0] or _active_group(s, funding, account) is None):
        return None
    return value


def accounted_funding_line_ids(s, lines, account):
    """One bank source component may claim each active bank Ledger leg once."""
    if not any(line.status == MATCHED for line in lines):
        return set()
    fundings = {item.id: item for item in _fundings(s, account)}
    values = {line.id: _valid_binding(s, line, account, fundings) for line in lines if line.status == MATCHED}
    batches = {line.id: line.batch_id for line in lines}
    claims = Counter((batches[line_id], key) for line_id, value in values.items()
                     if value for key in value['ledger_ids'])
    # A plain pre-existing link must not overlap a compound withdrawal link.
    claims.update((line.batch_id, line.ledger_id) for line in lines if line.status != MATCHED and line.ledger_id is not None)
    # A separately confirmed repeat in another file may point to the same
    # historical fact. Only the explicit duplicate resolver can author that
    # link; it must not create a second debit hold across unresolved files.
    return {key for key, value in values.items()
            if value and all(claims[(batches[key], eid)] == 1 for eid in value['ledger_ids'])}


def classify_cash_funding(s, batch, lines, account, user, opening_ok):
    """Read-only exact matching, including blockers for partial/ambiguous groups."""
    from .services import money
    if (batch.account_id != account.id or batch.company_id != account.company_id
            or account.kind != 'bank' or account.currency != batch.currency or account.archived):
        raise HTTPException(409, 'Счёт или валюта изменились. Проверьте выписку.')
    fundings = _fundings(s, account)
    if not fundings:
        return {}
    candidates = [line for line in lines if line.direction == 'out' and line.status in (
        'review', 'reviewed', 'auto_pay_candidate', 'fee_candidate', 'fee_existing', 'outgoing_candidate', CANDIDATE)]
    groups = {}
    decisions = {}
    for line in candidates:
        related = [funding for funding in fundings if funding.withdraw_date == line.date
                   and _reference_matches(funding, line)]
        # A bank may number its fee separately without citing the withdrawal.
        # This is insufficient to link, but must block a second fee/outflow.
        possible = [funding for funding in fundings if funding.withdraw_date == line.date and (
            funding.fee_amount and line.amount == funding.fee_amount and _FEE.search(line.purpose or '')
            or line.amount in (funding.gross_amount, funding.net_amount) and _CASH.search(line.purpose or ''))]
        if related or possible:
            decisions[line.id] = {'status': 'review', 'reason': 'Снятие наличных требует проверки всей связанной группы.',
                                  'ledger_id': None, 'request_id': None}
        for funding in related:
            groups.setdefault(funding.id, []).append(line)
    if not decisions:
        return {}
    allowed = bool({'write', 'pay_bank'} & perms_of(user)) and can_edit_section(user, 'ledger')
    previous = list(s.scalars(select(BankStatementLine).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(
        BankStatementBatch.status == 'committed', BankStatementLine.account_id == account.id,
        BankStatementBatch.company_id == batch.company_id, BankStatementBatch.id != batch.id)))
    other_links = previous + [line for line in lines if line.id not in {row.id for row in candidates}]
    claimed = {line.ledger_id for line in other_links if line.ledger_id is not None}
    claimed.update(key for line in other_links for key in (binding(line) or {}).get('ledger_ids', []))
    previous_refs = {(_name(line.document_number), line.date) for line in previous}
    previous_fingerprints = {line.fingerprint for line in previous}
    fingerprints = Counter(line.fingerprint for line in lines)
    eligible_groups = {}
    for funding in fundings:
        rows = groups.get(funding.id, [])
        if not rows:
            continue
        legs = _active_group(s, funding, account)
        if (not allowed or not batch.source_ready or not opening_ok or legs is None
                or bool(set(legs) & claimed)
                or any(row.uncertain or row.batch_id != batch.id or row.company_id != batch.company_id
                       or row.account_id != account.id or row.request_id is not None
                       or row.fingerprint in previous_fingerprints or fingerprints[row.fingerprint] != 1
                       or (_name(row.document_number), row.date) in previous_refs for row in rows)):
            continue
        parts = {row.id: _component(funding, row) for row in rows}
        counts = Counter(parts.values())
        complete = ((len(rows) == 1 and counts['gross'] == 1)
                    or (len(rows) == 2 and counts['net'] == 1 and counts['fee'] == 1))
        if not complete:
            continue
        eligible_groups[funding.id] = (funding, rows, parts)
    competing = Counter(row.id for _, rows, _ in eligible_groups.values() for row in rows)
    for funding, rows, parts in eligible_groups.values():
        if any(competing[row.id] != 1 for row in rows):
            continue
        for line in rows:
            component = parts[line.id]
            ids = _component_ids(funding, component)
            decisions[line.id] = {'status': CANDIDATE, 'reason': '', 'request_id': None,
                'ledger_id': ids[0], 'cash_funding': {'funding_id': funding.id, 'component': component,
                    'ledger_ids': ids, 'gross_amount': money(funding.gross_amount),
                    'fee_amount': money(funding.fee_amount), 'net_amount': money(funding.net_amount),
                    'version': funding.version}}
    return decisions


def process_cash_funding(s, batch, lines, account, user, opening_ok, approved):
    """Link signed groups atomically; no new Ledger rows and no cash issue."""
    from .services import log
    fresh = classify_cash_funding(s, batch, lines, account, user, opening_ok)
    ready = {key: value for key, value in fresh.items() if value['status'] == CANDIDATE}
    for key, decision in ready.items():
        shown = approved.get(key, {})
        if (shown.get('status') != CANDIDATE or shown.get('ledger_id') != decision['ledger_id']
                or shown.get('cash_funding') != decision['cash_funding']):
            raise HTTPException(409, 'Группа снятия наличных изменилась. Проверьте выписку заново.')
    counts = Counter()
    for line in lines:
        decision = ready.get(line.id)
        if not decision:
            continue
        metadata = decision['cash_funding']
        line.status = MATCHED
        line.classification = 'bank_fee' if metadata['component'] == 'fee' else 'internal_transfer'
        line.ledger_id = decision['ledger_id']
        line.request_id = None
        line.reason = ''
        line.review_note = json.dumps({_BINDING: 1, **{key: metadata[key] for key in (
            'funding_id', 'component', 'ledger_ids', 'gross_amount', 'fee_amount', 'net_amount')}},
            ensure_ascii=False, sort_keys=True)
        line.reviewed_by = user.id
        line.reviewed_at = now()
        log(s, user, 'Связана выписка со снятием наличных', 'bank_statement_line', line.id,
            json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        counts[MATCHED] += 1
    return dict(counts)
