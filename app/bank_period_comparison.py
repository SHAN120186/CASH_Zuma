"""Compare bank evidence to request payments on demand. All operations are reads."""
from collections import Counter, defaultdict
from datetime import date
import re
from types import SimpleNamespace
import unicodedata
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, or_, select

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
from .services import deleted_ledger_ids, effective_cashflows, get, money

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


def _context(s, request, account_id, currency):
    user, _ = session_user(s, request, ('view', 'pay_bank'))
    if user.role not in HOLDING_ROLES and company_role(user) not in ('finance', 'director', 'accountant'):
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


def _stored_evidence(s, account, source, start, end, part_ledgers):
    relevant = BankStatementLine.date.between(start, end)
    if part_ledgers:
        relevant = or_(relevant, BankStatementLine.ledger_id.in_(part_ledgers))
    lines = list(s.scalars(select(BankStatementLine).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(
        BankStatementBatch.status == 'committed',
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


def _comparison_rows(source, requests, facts, evidence, parts_by_order):
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
                    and facts[proven.request_id].id == proven.ledger_id):
                status, reason, related = 'matched', 'Связь с действующей оплатой сохранена в подтверждённой выписке.', {proven.request_id}
            elif proven and proven.request_id is None:
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


def _report(s, user, account, source, binding):
    stamp = revision(s)
    start, end = (date.fromisoformat(source[key]) for key in ('period_from', 'period_to'))
    requests, facts = _active_requests(s, user, account, source, start, end)
    parts = _numbered_parts(source, facts)
    part_ledgers = {facts[rid].id for members in parts.values() for rid in members}
    evidence = _stored_evidence(s, account, source, start, end, part_ledgers)
    rows, matched, possible = _comparison_rows(source, requests, facts, evidence, parts)
    unmatched = [{'request_id': rid, 'document_number': fact.reference, 'amount': money(fact.amount),
                  'date': str(fact.date), 'counterparty': requests[rid].counterparty,
                  'status': 'candidate' if rid in possible else 'app_only'}
                 for rid, fact in sorted(facts.items(), key=lambda pair: (pair[1].date, pair[0]))
                 if start <= fact.date <= end and rid not in matched]
    first = balances(s, [account], start)[account.id] if start >= account.opening_date else None
    last = first
    if start != end:
        last = balances(s, [account], end)[account.id] if end >= account.opening_date else None
    if revision(s) != stamp:
        raise HTTPException(409, 'Данные изменились во время сверки. Откройте выписку повторно.')
    return {'account': {'id': account.id, 'name': account.name, 'currency': account.currency},
            'statement': {k: v for k, v in source.items() if k != 'rows'}, 'binding': binding,
            'site': {'opening_balance': money(first['opening']) if first else None,
                     'closing_balance': money(last['closing']) if last else None,
                     'opening_difference': money(_minor(source['opening_balance']) - first['opening']) if first else None,
                     'closing_difference': money(_minor(source['closing_balance']) - last['closing']) if last else None},
            'rows': rows, 'unmatched_payments': unmatched}


@router.get('/period-sources/list')
def saved_sources(request: Request, account_id: int, currency: str = currency_param):
    with unit() as s:
        _, account = _context(s, request, account_id, currency)
        items = s.execute(select(BankStatementBatch.id, BankStatementBatch.filename,
            BankStatementBatch.period_from, BankStatementBatch.period_to).where(
            BankStatementBatch.account_id == account.id, BankStatementBatch.status == 'committed'
            ).order_by(BankStatementBatch.period_to.desc(), BankStatementBatch.id.desc()))
        return {'items': [dict(row._mapping) for row in items]}


@router.get('/period-sources/{id}')
def compare_saved(id: int, request: Request, account_id: int, currency: str = currency_param):
    with unit() as s:
        user, account = _context(s, request, account_id, currency)
        batch = get(s, BankStatementBatch, id)
        if batch.account_id != account.id or batch.status != 'committed' or batch.currency != account.currency:
            raise HTTPException(404, 'Подтверждённая выписка для этого счёта не найдена.')
        source = _statement(batch)
        check_statement_currency(account, source)
        source['rows'] = [_line_data(line) for line in s.scalars(select(BankStatementLine).where(
            BankStatementLine.batch_id == batch.id).order_by(BankStatementLine.line))]
        return _report(s, user, account, source, 'confirmed')


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
        if binding == 'owner_only' and not confirm_account:
            return {'requires_confirmation': True, 'binding': binding,
                    'account': {'id': account.id, 'name': account.name, 'currency': account.currency},
                    'statement': {k: v for k, v in source.items() if k != 'rows'},
                    'rows': [], 'unmatched_payments': []}
        return _report(s, user, account, source, binding)
