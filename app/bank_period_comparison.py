"""Compare bank evidence to request payments on demand. All operations are reads."""
from collections import Counter, defaultdict
from datetime import date
import re
from types import SimpleNamespace
import unicodedata
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import case, func, or_, select

from .bank_period_parser import parse_period_statement
from .bank_statement_api import _fingerprint, _line_data, _minor, _statement
from .bank_statement_identity import check_statement_currency, confirmed_bank_binding
from .bank_statement_expenses import _fee_evidence
from .bank_statement_guard import REQUEST_PAID_BANK_STATUSES, accounted_line_ids
from .bank_statement_models import BankStatementBatch, BankStatementLine
from .bank_statement_parser import MAX_BYTES, StatementParseError
from .daily_reconciliation import balances, revision
from .db import Account, Company, Ledger, PaymentRequest, unit
from .erp import read_upload
from .requests_api import can_view_request
from .security import HOLDING_ROLES, company_role, session_user
from .services import batch_account_balances, deleted_ledger_ids, effective_cashflows, get, local_date, money

router = APIRouter(prefix='/api/bank-statements')
currency_param = Query(pattern=r'^(UZS|USD|EUR)$')
CF_REFERENCE = re.compile(r'(?<!\w)[CС]F[\s\-–—‑]*0*([1-9][0-9]*)(?!\w)', re.I)
PAYMENT_PART = re.compile(r'([0-9]+)(?:/([1-9][0-9]*))?')
CONTRACT_REFERENCE = re.compile(
    r'\b(?:договор(?:у|а|ом)?|контракт(?:у|а|ом)?|shartnoma(?:si)?|шартнома(?:си)?|contract)'
    r'\s*(?:№|#|no?\.|no?(?=[\s\d]))\s*(\w+(?:[-/.]\w+)*)', re.I)
CONTRACT_DASHES = str.maketrans('‐‑‒–—−', '------')
CONTRACT_LOOKALIKES = str.maketrans('АВЕКМНОРСТХ', 'ABEKMHOPCTX')


def _contract_number(purpose):
    # Only compare a single explicitly labelled contract, never an invoice or a payment order.
    text = (purpose or '').translate(CONTRACT_DASHES)
    numbers = set()
    for match in CONTRACT_REFERENCE.finditer(text):
        number = match[1].upper()
        if not number.strip('0') or not any(char.isdigit() for char in number) or re.match(
                r'\s*(?:[-/]|(?:,|;|и\b|ва\b|and\b)\s*(?:№\s*)?\d|[,;]\s*№)', text[match.end():], re.I):
            return None
        numbers.add(number)
    return numbers.pop() if len(numbers) == 1 else None


def _contracts_differ(bank, site):
    return bool(bank and site and bank.translate(CONTRACT_LOOKALIKES) != site.translate(CONTRACT_LOOKALIKES))


def _financial_reader(user):
    return user.role in HOLDING_ROLES or company_role(user) in ('finance', 'director', 'accountant')


def _context(s, request, account_id, currency):
    user, _ = session_user(s, request, ('view', 'pay_bank'))
    if not _financial_reader(user):
        raise HTTPException(403, 'Сверка доступна финансовой службе и руководству.')
    if getattr(user, '_sections', None) and user._sections.get('requests') == 'hidden':
        raise HTTPException(403, 'Для сверки необходим доступ к заявкам.')
    account = get(s, Account, account_id)
    if account.kind != 'bank' or account.currency != currency or account.archived:
        raise HTTPException(422, 'Выберите действующий банковский счёт с этой валютой.')
    return user, account


def _binding(s, account, source):
    if confirmed_bank_binding(s, account, source['account_number'], source['owner_inn']):
        return 'confirmed'
    # Old site accounts have no stored bank number. On first use the operator
    # must verify the selected account and currency after seeing source metadata.
    company = get(s, Company, account.company_id)
    owner_words = set(re.findall(r'[^\W_]+', source['owner_name'].casefold()))
    if company.code.casefold() not in owner_words:
        raise HTTPException(409, 'Владелец выписки не совпадает с выбранной компанией.')
    return 'owner_only'


def _request_references(row):
    # CF identifiers and bank order numbers are independent namespaces.
    return {int(digits) for field in ('purpose', 'document_number') for digits in CF_REFERENCE.findall(row[field])
            if len(digits) <= 18}


def _active_requests(s, user, account, source, start, end):
    # In-period totals need every active site payment in the period. A bounded
    # neighbouring window also catches payments booked a few days late. Exact
    # request or order references still work beyond that window.
    first = date.fromordinal(max(date.min.toordinal(), start.toordinal() - 31))
    last = date.fromordinal(min(date.max.toordinal(), end.toordinal() + 31))
    outgoing = [row for row in source['rows'] if row['direction'] == 'out']
    amounts = {_minor(row['amount']) for row in outgoing}
    references = {rid for row in outgoing for rid in _request_references(row)}
    # Saved sources can explicitly link a payment outside the date window even
    # when the bank and treasury use entirely different document numbers.
    references.update(row['request_id'] for row in outgoing if row.get('request_id'))
    documents = {row['document_number'] for row in outgoing}
    criteria = [Ledger.date.between(first, last), Ledger.request_id.in_(references), Ledger.reference.in_(documents)]
    base_numbers = {doc for doc in documents if re.fullmatch(r'[0-9]+', doc) and doc.strip('0')}
    if base_numbers:
        # Look up all referenced families in one query, even when the payment date is outside the window.
        base = (func.split_part(Ledger.reference, '/', 1) if s.get_bind().dialect.name == 'postgresql'
                else func.substr(Ledger.reference, 1, func.instr(Ledger.reference, '/') - 1))
        criteria.append(Ledger.reference.contains('/') & base.in_(base_numbers))
    request_scope = (PaymentRequest.account_id == account.id, PaymentRequest.status.in_(('approved', 'paid')))
    candidates = (effective_cashflows(s).join(PaymentRequest, Ledger.request_id == PaymentRequest.id)
                  .add_columns(PaymentRequest).where(Ledger.account_id == account.id, Ledger.kind == 'out',
                                                    *request_scope, or_(*criteria)))
    request_query = select(PaymentRequest).where(*request_scope, or_(PaymentRequest.id.in_(references),
        PaymentRequest.due_date.between(first, last) & PaymentRequest.amount.in_(amounts)))
    requests = {req.id: req for req in s.scalars(request_query)}
    facts = {}
    deleted = deleted_ledger_ids(s)
    for entry, req in s.execute(candidates):
        requests[req.id] = req
        if entry.id not in deleted and req.status == 'paid':
            facts[req.id] = entry
    requests = {rid: req for rid, req in requests.items() if can_view_request(s, user, req)}
    facts = {rid: entry for rid, entry in facts.items() if rid in requests}
    return requests, facts


def _stored_evidence(s, account, source, start, end, relevant_ledgers, draft_id):
    relevant = BankStatementLine.date.between(start, end)
    if relevant_ledgers:
        relevant = or_(relevant, BankStatementLine.ledger_id.in_(relevant_ledgers))
    scope = BankStatementBatch.status == 'committed'
    if draft_id is not None:
        scope = or_(scope, (BankStatementBatch.status == 'preview') & (BankStatementBatch.id == draft_id)
                    & (BankStatementLine.status == 'manual_match') & BankStatementLine.classification.like('paid:%'))
    lines = list(s.scalars(select(BankStatementLine).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(
        scope,
        BankStatementBatch.account_number == source['account_number'], BankStatementBatch.owner_inn == source['owner_inn'],
        BankStatementLine.account_id == account.id, relevant)))
    active = accounted_line_ids(s, lines, account) if lines else set()
    groups = defaultdict(list)
    for line in lines:
        groups[line.fingerprint].append(line)
    # Uncertain, reversed and overlapping claims must also be visible to the
    # matcher. Discarding them would allow a coincidental number to go green.
    claimed = {line.ledger_id for line in lines if line.ledger_id is not None}
    return groups, active, claimed


def _indexes(requests, facts):
    by_payee, by_name, by_document = defaultdict(set), defaultdict(set), defaultdict(set)
    for req in requests.values():
        if req.counterparty_inn:
            by_payee[(req.amount, req.counterparty_inn)].add(req.id)
        name = _payee_name(req.counterparty)
        if name:
            by_name[(req.amount, name)].add(req.id)
    for rid, fact in facts.items():
        by_document[fact.reference].add(rid)
    return by_payee, by_name, by_document


def _payee_name(value):
    tokens = re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', value or '').casefold())
    forms = {'ооо', 'мчж', 'mchj', 'llc', 'ао', 'aj', 'оао', 'ип', 'oao'}
    name = ' '.join(token for token in tokens if token not in forms)
    return name if len(name) >= 5 and any(ch.isalpha() for ch in name) else ''


def _numbered_parts(source, facts):
    bank_orders = {row['document_number'] for row in source['rows'] if row['direction'] == 'out'}
    grouped = defaultdict(list)
    for rid, fact in facts.items():
        match = PAYMENT_PART.fullmatch(fact.reference)
        if match and match[1] in bank_orders and match[1].strip('0'):
            suffix = match[2] or ''
            grouped[match[1]].append((len(suffix), suffix, rid))
    # An unsuffixed payment is the anchor. Sort canonical suffixes numerically, keeping leading zeros in the base.
    families = {}
    for number, parts in grouped.items():
        suffixes = {suffix for _, suffix, _ in parts}
        if len(parts) > 1 and '' in suffixes and len(parts) == len(suffixes):
            families[number] = [rid for _, _, rid in sorted(parts)]
    return families


def _recipient_matches(row, req):
    bank_inn, site_inn = row['correspondent_inn'], req.counterparty_inn or ''
    if bank_inn.strip('0') and site_inn.strip('0'):
        return bank_inn == site_inn
    name = _payee_name(row['correspondent_name'])
    return bool(name and name == _payee_name(req.counterparty))


def _eligible_parts(row, members, requests, facts, order_counts, claimed, cf_ids):
    number = row['document_number']
    if not members or order_counts[number] != 1:
        return []
    parts = [rid for rid in members if _recipient_matches(row, requests[rid])]
    if len(parts) < 2 or facts[parts[0]].reference != number or not cf_ids.issubset(parts):
        return []
    known_inns = {requests[rid].counterparty_inn for rid in parts if (requests[rid].counterparty_inn or '').strip('0')}
    if len(known_inns) > 1:
        return []
    if any(facts[rid].id in claimed for rid in parts):
        return []
    if any(facts[rid].reference in order_counts for rid in parts[1:]):
        return []
    return parts


def _comparison_rows(source, requests, facts, evidence, parts_by_order, draft_id):
    by_payee, by_name, by_document = _indexes(requests, facts)
    request_contracts = {rid: _contract_number(req.purpose) for rid, req in requests.items()}
    stored, active_lines, claimed_ledgers = evidence
    rows = []
    matched, possible = set(), set()
    outgoing = [row for row in source['rows'] if row['direction'] == 'out']
    fingerprints = Counter(_fingerprint(source['account_number'], row) for row in outgoing)
    order_counts = Counter(row['document_number'] for row in outgoing)
    exact_by_request = Counter()
    for row in source['rows']:
        day, amount = date.fromisoformat(row['date']), _minor(row['amount'])
        bank_contract = _contract_number(row['purpose']) if row['direction'] == 'out' else None
        status, reason, related = 'incoming', 'Поступление по выписке.', set()
        parts = []
        if row['direction'] == 'out':
            fingerprint = _fingerprint(source['account_number'], row)
            old = stored.get(fingerprint, [])
            cf_ids = _request_references(row)
            ids = cf_ids.intersection(requests)
            same_payee = set(by_payee.get((amount, row['correspondent_inn']), ()))
            name = _payee_name(row['correspondent_name'])
            for rid in by_name.get((amount, name), ()):
                if not row['correspondent_inn'] or not requests[rid].counterparty_inn:
                    same_payee.add(rid)
            for rid in same_payee:
                site_day = facts[rid].date if rid in facts else requests[rid].due_date
                if abs((day - site_day).days) <= 31:
                    ids.add(rid)
            same_doc = by_document.get(row['document_number'], set())
            for rid in same_doc:
                if facts[rid].amount == amount or (row['correspondent_inn']
                        and requests[rid].counterparty_inn == row['correspondent_inn']):
                    ids.add(rid)
            status, reason = 'bank_only', 'Для банковского списания не найдена подтверждённая заявка.'
            duplicate = fingerprints[fingerprint] > 1
            # A saved link or overlapping bank lines take precedence over a shared document number.
            unlinked = (len(old) == 1 and old[0].ledger_id is None and old[0].request_id is None
                        and not old[0].classification)
            if not old or unlinked:
                parts = _eligible_parts(row, parts_by_order.get(row['document_number']), requests, facts,
                                        order_counts, claimed_ledgers, cf_ids)
            proven = old[0] if (len(old) == 1 and source['ready'] and not duplicate
                and old[0].id in active_lines and not old[0].uncertain and old[0].include_in_daily) else None
            if (proven and proven.status in REQUEST_PAID_BANK_STATUSES and proven.request_id in facts
                    and facts[proven.request_id].id == proven.ledger_id and requests[proven.request_id].amount == amount
                    and (proven.batch_id != draft_id or proven.classification == f'paid:{facts[proven.request_id].date}')):
                status, related = 'matched', {proven.request_id}
                reason = ('Связь с действующей оплатой сохранена в этом черновике.' if proven.batch_id == draft_id else
                          'Связь с действующей оплатой сохранена в подтверждённой выписке.')
            elif proven and proven.batch_id != draft_id and proven.request_id is None:
                if proven.classification == 'bank_fee' or proven.status in ('auto_fee_posted', 'fee_matched'):
                    status, reason = 'fee', 'Комиссия учтена отдельной банковской операцией.'
                else:
                    status, reason = 'other', 'Строка связана с действующей операцией без оплаты заявки.'
            elif old:
                related = ids | set(parts) | {line.request_id for line in old if line.request_id in requests}
                status = 'candidate'
                reason = 'Сохранённая строка требует проверки: проверьте связь, сторно, повторы и итог выписки.'
            else:
                hint_line = SimpleNamespace(**{**row, 'date': day, 'amount': amount,
                                               'classification': '', 'reviewed_by': None})
                fee = _fee_evidence(SimpleNamespace(owner_inn=source['owner_inn']), hint_line)
                exact = {rid for rid in same_doc if facts[rid].date == day and facts[rid].amount == amount
                         and facts[rid].id not in claimed_ledgers and requests[rid].amount == amount
                         and row['correspondent_inn'].strip('0') and row['document_number'].strip('0 ')
                         and requests[rid].counterparty_inn == row['correspondent_inn']}
                if fee:
                    parts = []
                if parts:
                    related = ids | set(parts)
                    status, reason = 'candidate', 'Найдены части платёжки. Сверьте даты, реквизиты и общую сумму.'
                elif (source['ready'] and len(exact) == 1 and order_counts[row['document_number']] == 1
                        and (not cf_ids or cf_ids == exact) and not old and not fee):
                    related = exact
                    status, reason = 'matched', 'Совпали платёжка, дата, сумма и ИНН получателя с действующей оплатой.'
                elif ids or duplicate or cf_ids:
                    related = ids
                    status = 'candidate'
                    if duplicate:
                        reason = 'В выписке повторяется строка. Требуется проверить оригинал.'
                    elif cf_ids and cf_ids != ids:
                        reason = 'Номер заявки в назначении расходится с доступными данными. Проверьте реквизиты.'
                    else:
                        reason = 'Возможное совпадение. Сверьте номер платёжки, дату, сумму и получателя.'
                elif fee:
                    status = 'fee' if fee[0] == 'bank_fee' else 'other'
                    reason = 'Комиссия банка по реквизитам выписки.' if status == 'fee' else 'Банковские проценты по реквизитам выписки.'
            if status == 'matched':
                exact_by_request.update(related)
            else:
                possible.update(related)
        part_ids = set(parts)
        selected = parts + sorted(related - part_ids)[:max(0, 12 - len(parts))]
        details = [{'request_id': rid, 'amount': money(facts[rid].amount if rid in part_ids else requests[rid].amount),
                    'counterparty': requests[rid].counterparty, 'status': requests[rid].status,
                    'counterparty_inn': requests[rid].counterparty_inn,
                    'contract_number': request_contracts[rid],
                    'contract_mismatch': _contracts_differ(bank_contract, request_contracts[rid]),
                    'date': str(facts[rid].date) if rid in facts else None,
                    'document_number': facts[rid].reference if rid in facts else None}
                   for rid in selected]
        rows.append({key: row[key] for key in ('line', 'date', 'direction', 'amount',
            'document_number', 'correspondent_name', 'correspondent_inn', 'purpose')} | {'status': status, 'reason': reason,
            'contract_number': bank_contract,
            'request_ids': selected, 'request_count': len(related), 'request_details': details})
        if parts:
            total = sum(facts[rid].amount for rid in parts)
            rows[-1]['payment_group'] = {'request_ids': parts, 'total_amount': money(total),
                                         'difference': money(total - amount)}
    # Two different bank debits must never both count as one paid request.
    for row in rows:
        if row['status'] == 'matched':
            rid = row['request_ids'][0]
            detail = row['request_details'][0]
            site_inn = (detail['counterparty_inn'] or '').strip()
            bank_inn = (row['correspondent_inn'] or '').strip()
            if exact_by_request[rid] > 1:
                row.update(status='candidate', reason='На одну оплату претендуют несколько списаний.')
                possible.add(rid)
            elif (detail['contract_mismatch'] or
                  (site_inn.strip('0') and bank_inn.strip('0') and site_inn != bank_inn)):
                row.update(status='candidate', reason='Реквизиты выписки и заявки расходятся. Проверьте ИНН и номер договора.')
                possible.add(rid)
            else:
                matched.add(rid)
    return rows, matched, possible


def _operating_days(s, account, source, start, end, first):
    """Calculate each bank day's balances from this file, and its site balances from journal legs."""
    if not source['ready'] or not source['balance_matches'] or source['declared_totals_match'] is False:
        return {}
    credits, debits = Counter(), Counter()
    for row in source['rows']:
        day = date.fromisoformat(row['date'])
        if not start <= day <= end or row['direction'] not in ('in', 'out'):
            return {}
        amounts = credits if row['direction'] == 'in' else debits
        amounts[day] += _minor(row['amount'])
    bank = _minor(source['opening_balance'])
    if (sum(credits.values()) != _minor(source['credit_total'])
            or sum(debits.values()) != _minor(source['debit_total'])
            or bank + sum(credits.values()) - sum(debits.values()) != _minor(source['closing_balance'])):
        return {}
    booked_from = max(start, account.opening_date)
    site = first['opening'] if first else None
    if site is None and booked_from <= end:
        site = balances(s, [account], booked_from)[account.id]['opening']
    outgoing_leg = case((Ledger.kind == 'in', Ledger.amount), else_=-Ledger.amount)
    net = case((Ledger.account_id == account.id, outgoing_leg), else_=0) + case(
        (Ledger.to_account_id == account.id, Ledger.amount), else_=0)
    journal = dict(s.execute(select(Ledger.date, func.sum(net)).where(
        Ledger.date.between(booked_from, end),
        or_(Ledger.account_id == account.id, Ledger.to_account_id == account.id)
    ).group_by(Ledger.date)).all())
    days = {}
    for day in sorted({start, end} | credits.keys() | debits.keys() | journal.keys()):
        site_open = site if day >= booked_from else None
        if site_open is not None:
            site += journal.get(day, 0)
        closing = bank + credits[day] - debits[day]
        days[day.isoformat()] = {
            'opening_balance': money(bank), 'credit_total': money(credits[day]),
            'debit_total': money(debits[day]), 'closing_balance': money(closing),
            'site': {'opening_balance': money(site_open), 'closing_balance': money(site)}
                    if site_open is not None else None,
        }
        bank = closing
    return days


def _report(s, user, account, source, binding, draft_id=None):
    stamp = revision(s)
    start, end = (date.fromisoformat(source[key]) for key in ('period_from', 'period_to'))
    requests, facts = _active_requests(s, user, account, source, start, end)
    parts = _numbered_parts(source, facts)
    relevant_ledgers = {facts[rid].id for members in parts.values() for rid in members}
    relevant_ledgers.update(row['ledger_id'] for row in source['rows'] if row.get('ledger_id'))
    evidence = _stored_evidence(s, account, source, start, end, relevant_ledgers, draft_id)
    rows, matched, possible = _comparison_rows(source, requests, facts, evidence, parts, draft_id)
    unmatched = [{'request_id': rid, 'document_number': fact.reference, 'amount': money(fact.amount),
                  'date': str(fact.date), 'counterparty': requests[rid].counterparty,
                  'status': 'candidate' if rid in possible else 'app_only'}
                 for rid, fact in sorted(facts.items(), key=lambda pair: (pair[1].date, pair[0]))
                 if start <= fact.date <= end and rid not in matched]
    first = balances(s, [account], start)[account.id] if start >= account.opening_date else None
    last = first
    if start != end:
        last = balances(s, [account], end)[account.id] if end >= account.opening_date else None
    days = _operating_days(s, account, source, start, end, first)
    if revision(s) != stamp:
        raise HTTPException(409, 'Данные изменились во время сверки. Откройте выписку повторно.')
    return {'account': {'id': account.id, 'name': account.name, 'currency': account.currency},
            'statement': {k: v for k, v in source.items() if k != 'rows'}, 'binding': binding,
            'site': {'opening_balance': money(first['opening']) if first else None,
                     'closing_balance': money(last['closing']) if last else None,
                     'opening_difference': money(_minor(source['opening_balance']) - first['opening']) if first else None,
                     'closing_difference': money(_minor(source['closing_balance']) - last['closing']) if last else None},
            'rows': rows, 'unmatched_payments': unmatched, 'days': days}


def _visible_sources(user, include_drafts):
    batch = BankStatementBatch
    visible = batch.status == 'committed'
    if include_drafts:
        draft = batch.status == 'preview'
        if user.role != 'admin':
            draft = draft & (batch.created_by == user.id)
        visible = visible | draft
    return visible


def account_statement_balances(s, user, accounts, as_of):
    """Report the file's closing balance alongside the ledger on that same day."""
    sections = getattr(user, '_sections', None) or {}
    banks = {a.id: a for a in accounts if a.kind == 'bank' and not a.archived}
    if not banks or not _financial_reader(user) or 'hidden' in (sections.get('ledger'), sections.get('requests')):
        return {}
    b = BankStatementBatch
    candidates = s.execute(select(b.account_id, b.id, b.period_to, b.status, b.closing,
        b.account_number, b.owner_inn, b.owner_name, b.currency).where(
        b.company_id == s.info['company_id'], b.account_id.in_(banks), _visible_sources(user, True),
        b.period_from <= b.period_to, b.period_to <= as_of,
        b.source_ready.is_(True), b.balance_matches.is_(True), b.declared_totals_match.is_(True),
        b.opening + b.credit_total - b.debit_total == b.closing,
    ).order_by(b.account_id, b.period_to.desc(), b.id.desc()))
    selected = {}
    by_day = defaultdict(list)
    for row in candidates:
        account = banks[row.account_id]
        if account.id in selected or row.currency != account.currency:
            continue
        try:
            check_statement_currency(account, row._mapping)
            _binding(s, account, row._mapping)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            continue
        selected[account.id] = row
        by_day[row.period_to].append(account)

    result = {}
    for day, items in by_day.items():
        book = batch_account_balances(s, items, day)
        for account in items:
            row = selected[account.id]
            started = account.opening_date <= day
            result[account.id] = dict(id=row.id, date=str(day), status=row.status, closing=money(row.closing),
                book_closing=money(book[account.id]) if started else None,
                difference=money(row.closing-book[account.id]) if started else None)
    return result


def payment_document_rows(s, user, account, req, reference, fact_date):
    """Show visible debits with the submitted order number and approved amount."""
    if not reference.strip() or not _financial_reader(user) or 'hidden' in (
            (getattr(user, '_sections', None) or {}).get(key) for key in ('requests', 'ledger')):
        return []
    b, line = BankStatementBatch, BankStatementLine
    rows = s.execute(select(b, line).join(line, line.batch_id == b.id).where(
        b.company_id == account.company_id, b.account_id == account.id, b.currency == account.currency,
        _visible_sources(user, True), b.source_ready.is_(True), b.balance_matches.is_(True),
        b.declared_totals_match.is_(True), b.opening + b.credit_total - b.debit_total == b.closing,
        line.company_id == b.company_id, line.account_id == account.id, line.direction == 'out',
        line.date.between(b.period_from, b.period_to), line.date <= fact_date,
        line.date >= date.fromordinal(max(date.min.toordinal(), fact_date.toordinal()-31)),
        line.document_number == reference.strip(), line.amount == req.amount
    ).order_by(b.period_to.desc(), b.id.desc(), line.line))
    items = {}
    for batch, row in rows:
        key = (row.fingerprint, row.occurrence)
        if key in items:
            continue
        metadata = {k: getattr(batch, k) for k in ('account_number', 'owner_inn', 'owner_name', 'currency')}
        try:
            check_statement_currency(account, metadata)
            _binding(s, account, metadata)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            continue
        items[key] = dict(batch_id=batch.id, status=batch.status, line=row.line, date=str(row.date),
            reference=row.document_number, amount=money(row.amount), currency=account.currency,
            counterparty=row.correspondent_name, inn=row.correspondent_inn, purpose=row.purpose)
    return list(items.values())


@router.get('/payment-evidence/{request_id}')
def payment_evidence(request_id: int, request: Request, account_id: int, currency: str = currency_param,
                     reference: str = Query(min_length=2, max_length=160), fact_date: date = Query()):
    with unit() as s:
        user, account = _context(s, request, account_id, currency)
        req = get(s, PaymentRequest, request_id)
        if req.account_id != account.id or not can_view_request(s, user, req):
            raise HTTPException(404, 'Заявка не найдена.')
        return dict(request_id=req.id, approved_on=str(local_date(req.approved_at)) if req.approved_at else None,
                    rows=payment_document_rows(s, user, account, req, reference, fact_date))


@router.get('/period-sources/list')
def saved_sources(request: Request, account_id: int, currency: str = currency_param, include_drafts: bool = False):
    with unit() as s:
        user, account = _context(s, request, account_id, currency)
        visible = _visible_sources(user, include_drafts)
        items = s.execute(select(BankStatementBatch.id, BankStatementBatch.filename,
            BankStatementBatch.period_from, BankStatementBatch.period_to, BankStatementBatch.status).where(
            BankStatementBatch.account_id == account.id, visible
            ).order_by(BankStatementBatch.period_to.desc(), BankStatementBatch.id.desc()))
        return {'items': [dict(row._mapping) for row in items]}


def _confirm_owner_only(account, source, binding, confirm_account):
    if binding == 'owner_only' and not confirm_account:
        return {'requires_confirmation': True, 'binding': binding,
                'account': {'id': account.id, 'name': account.name, 'currency': account.currency},
                'statement': {k: v for k, v in source.items() if k != 'rows'},
                'rows': [], 'unmatched_payments': []}


@router.get('/period-sources/{id}')
def compare_saved(id: int, request: Request, account_id: int, currency: str = currency_param,
                  draft: bool = False, confirm_account: bool = False):
    with unit() as s:
        user, account = _context(s, request, account_id, currency)
        batch = get(s, BankStatementBatch, id)
        if batch.account_id != account.id or batch.currency != account.currency:
            raise HTTPException(404, 'Запись не найдена.')
        if draft:
            if batch.status != 'preview' or (batch.created_by != user.id and user.role != 'admin'):
                raise HTTPException(404, 'Запись не найдена.')
        elif batch.status != 'committed':
            raise HTTPException(404, 'Подтверждённая выписка для этого счёта не найдена.')
        source = _statement(batch)
        check_statement_currency(account, source)
        binding = _binding(s, account, source) if draft else 'confirmed'
        confirmation = _confirm_owner_only(account, source, binding, confirm_account)
        if confirmation:
            return confirmation
        source['rows'] = [_line_data(line) for line in s.scalars(select(BankStatementLine).where(
            BankStatementLine.batch_id == batch.id).order_by(BankStatementLine.line))]
        return _report(s, user, account, source, binding, draft_id=batch.id if draft else None)


@router.post('/compare-period')
async def compare_file(request: Request, account_id: int, currency: str = currency_param, confirm_account: bool = False):
    with unit() as s:
        _context(s, request, account_id, currency)
    filename = unquote(request.headers.get('X-Filename', ''))
    if len(filename) > 220 or not filename.lower().endswith(('.xlsx', '.xls', '.html', '.htm')):
        raise HTTPException(422, 'Выберите банковскую выписку IABS в формате XLSX, XLS или HTML.')
    raw = await read_upload(request, limit=MAX_BYTES, too_large='Файл больше 5 МБ.')
    try:
        source = parse_period_statement(raw)
    except StatementParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    with unit() as s:
        user, account = _context(s, request, account_id, currency)
        check_statement_currency(account, source)
        binding = _binding(s, account, source)
        confirmation = _confirm_owner_only(account, source, binding, confirm_account)
        if confirmation:
            return confirmation
        return _report(s, user, account, source, binding)
