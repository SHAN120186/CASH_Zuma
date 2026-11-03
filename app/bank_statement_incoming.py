"""Conservative recognition of bank-statement receipts.

Preview never creates financial entries.  The caller must acquire ``unit(True)``
and obtain a fresh, explicit operator confirmation before calling ``process``.
All database queries are company-scoped by the authenticated session.
"""
from collections import Counter
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import or_, select

from .bank_statement_models import BankStatementBatch, BankStatementLine
from .category_catalog import catalog
from .clock import today
from .db import Category, Ledger, Receipt
from .bank_statement_permissions import can_post_statement
from .security import perms_of
from .services import day_start_balance, log, money, post_ledger


def opening_matches(s, batch, account):
    """The bank opening is comparable only after this account existed."""
    return (account.id == batch.account_id and batch.period_from >= account.opening_date
            and day_start_balance(s, account, batch.period_from) == batch.opening)


def _active_income_categories(s):
    return {item.id: item for item, retired in catalog(s, include_retired=True)
            if not retired and item.type == 'income'}


def _normal_name(value):
    return ' '.join((value or '').casefold().split())


def _key(line):
    return (line.account_id, line.date, line.amount, line.correspondent_inn)


def classify_incoming(s, batch, lines, account, user, income_category_id=None, unallocated=False):
    """Return a non-mutating decision for each eligible incoming line.

    Both existing manual ledger rows and already accepted bank lines are
    considered before allowing a new entry.  A matching amount alone never
    authorizes linkage or a second posting.  ``income_category_id`` is an
    explicit selection by this operator, never a company-wide inferred rule.
    """
    decisions = {}
    allowed = can_post_statement(user)
    active_categories = _active_income_categories(s)
    if income_category_id is not None and income_category_id not in active_categories:
        raise HTTPException(422, 'Выберите действующую статью поступлений выбранной компании.')
    if (account.id != batch.account_id or account.company_id != batch.company_id or account.archived
            or account.kind != 'bank' or account.currency != batch.currency):
        raise HTTPException(409, 'Счёт или валюта изменились. Проверьте выписку.')
    opening_ok = opening_matches(s, batch, account)
    rows = [line for line in lines if line.direction == 'in'
            and line.status in ('review', 'reviewed', 'income_candidate', 'income_matched_candidate')]
    current_ids = {line.id for line in lines}
    candidate_ids = {line.id for line in rows}
    bank_rows = list(s.scalars(select(BankStatementLine).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(
            BankStatementBatch.status == 'committed', BankStatementLine.account_id == account.id,
            BankStatementLine.direction == 'in')))
    prior_bank_rows = ([line for line in bank_rows if line.id not in current_ids]
                       + [line for line in lines if line.direction == 'in' and line.id not in candidate_ids])
    relevant_bank_rows = rows + prior_bank_rows
    bank_counts = Counter(_key(line) for line in relevant_bank_rows if line.correspondent_inn)
    document_counts = Counter((line.account_id, line.date, line.document_number)
                              for line in relevant_bank_rows if line.document_number)
    receipts = list(s.scalars(select(Receipt).where(Receipt.account_id == account.id,
                                                   Receipt.status.in_(('expected', 'received')))))
    ledger = list(s.scalars(select(Ledger).where(or_(Ledger.account_id == account.id,
                                                     Ledger.to_account_id == account.id))))
    reversed_ids = {entry.reversal_of for entry in ledger if entry.reversal_of is not None}
    linked_ids = set(s.scalars(select(BankStatementLine.ledger_id).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(
            BankStatementBatch.status == 'committed', BankStatementLine.ledger_id.is_not(None),
            BankStatementLine.id.not_in(current_ids))))

    for line in rows:
        result = {'status': 'review', 'reason': '', 'category_id': None, 'receipt_id': None, 'ledger_id': None}
        decisions[line.id] = result
        if (not batch.source_ready or line.batch_id != batch.id or line.company_id != batch.company_id
                or line.account_id != account.id):
            result['reason'] = 'Данные выписки или счёта требуют проверки.'; continue
        if line.uncertain:
            result['reason'] = 'Возможный повтор выписки требует проверки.'; continue
        if line.classification not in ('', 'unresolved', 'receipt'):
            result['reason'] = 'Ранее выбрана другая классификация операции.'; continue
        if not allowed:
            result['reason'] = 'Для проведения поступления нужно право записи операций.'; continue
        if not opening_ok:
            result['reason'] = 'Начальный остаток выписки не совпадает с остатком счёта сайта.'; continue
        if line.date < account.opening_date or line.date > today():
            result['reason'] = 'Дата поступления вне допустимого периода счёта.'; continue
        if not batch.owner_inn or not line.correspondent_inn:
            result['reason'] = 'Для распознавания нужен ИНН владельца и контрагента.'; continue
        if line.correspondent_inn == batch.owner_inn:
            result['reason'] = 'Операция со своим ИНН требует проверки перевода.'; continue
        # A bank correction may change amount or INN and therefore the full
        # fingerprint. Reusing a document on the same date is ambiguous even
        # where the other fields differ; never assume it is an additional event.
        if not line.document_number or document_counts[(line.account_id,line.date,line.document_number)] != 1:
            result['reason'] = 'Номер документа повторяется: нужна проверка поступления.'; continue

        plans = [r for r in receipts if r.inn and r.inn == line.correspondent_inn
                 and r.amount == line.amount and r.due_date == line.date]
        if len(plans) > 1:
            result['reason'] = 'Несколько планов поступления совпадают с выпиской.'; continue
        receipt = plans[0] if plans else None
        # Distinct documents in a validated statement are separate movements,
        # even if the same customer sends equal amounts on the same day. But a
        # single expected plan cannot be assigned to two such movements.
        if receipt and bank_counts[_key(line)] != 1:
            result['reason'] = 'Несколько строк выписки похожи на один план поступления.'; continue
        prior_similar = [other for other in prior_bank_rows if _key(other) == _key(line)]
        if any(other.status not in ('auto_income_posted', 'income_matched') or not other.ledger_id
               for other in prior_similar):
            result['reason'] = 'Похожее поступление из прошлой выписки требует проверки.'; continue
        # An otherwise matching plan on a different date could be a late or
        # early receipt. Posting as unallocated would leave a false forecast.
        if not receipt and any(r.inn == line.correspondent_inn and r.amount == line.amount
                               and (r.status == 'expected' or abs((r.due_date-line.date).days) <= 7)
                               for r in receipts if r.inn):
            result['reason'] = 'Похожий план поступления имеет другую дату: нужна проверка.'; continue
        if any(e.account_id == account.id and e.kind == 'in' and e.reference == line.document_number
               and (e.date != line.date or e.amount != line.amount) for e in ledger):
            result['reason'] = 'Номер документа уже есть в журнале с другими реквизитами.'; continue
        # A manual incoming or a transfer on the same account/day/amount may be
        # this bank event even when its document reference was entered differently.
        linked_to_different_document = {other.ledger_id for other in prior_bank_rows
            if other.ledger_id and other.status in ('auto_income_posted', 'income_matched')
            and other.date == line.date and other.amount == line.amount
            and other.document_number != line.document_number}
        existing = [e for e in ledger if e.id not in linked_to_different_document
                    and e.date == line.date and e.amount == line.amount
                    and ((e.account_id == account.id and e.kind == 'in')
                         or (e.to_account_id == account.id and e.kind == 'transfer'))]
        if existing:
            if len(existing) != 1:
                result['reason'] = 'Найдены похожие операции: возможен повтор.'; continue
            entry = existing[0]
            same_reference = entry.reference in (line.document_number, 'BANK-' + line.fingerprint)
            same_receipt = bool(receipt and entry.receipt_id == receipt.id and receipt.status == 'received')
            same_name = _normal_name(entry.counterparty) == _normal_name(line.correspondent_name)
            attach_receipt = bool(receipt and receipt.status == 'expected' and entry.receipt_id is None
                and entry.category_id == receipt.category_id and same_reference and same_name
                and not any(e.receipt_id == receipt.id for e in ledger))
            if receipt and not (same_receipt or attach_receipt):
                result['reason'] = 'План и существующую операцию нужно сверить вручную.'; continue
            if (entry.kind == 'in' and entry.id not in reversed_ids and not entry.reversal_of
                    and entry.id not in linked_ids and (same_receipt or (same_reference and same_name))):
                result.update(status='income_matched_candidate', ledger_id=entry.id,
                              receipt_id=receipt.id if attach_receipt else entry.receipt_id,
                              category_id=entry.category_id, attach_receipt=attach_receipt)
            else:
                result['reason'] = 'Похожая операция уже есть в журнале или была отменена.'
            continue
        if receipt and receipt.status != 'expected':
            result['reason'] = 'План уже закрыт или отменён: проверьте операцию.'; continue
        # A manually entered bank transaction can have a mistaken ledger date.
        # Treat the same document or a nearby same-party amount as ambiguous.
        if any(e.account_id == account.id and e.kind == 'in' and e.amount == line.amount
               and e.date != line.date and (e.reference == line.document_number
                    or (abs((e.date-line.date).days) <= 7
                        and _normal_name(e.counterparty) == _normal_name(line.correspondent_name)))
               for e in ledger):
            result['reason'] = 'Похожая операция на другую дату требует проверки.'; continue
        if any(e.reference == 'BANK-' + line.fingerprint and e.account_id == account.id for e in ledger):
            result['reason'] = 'Номер банковской операции уже занят и требует проверки.'; continue
        category_id = receipt.category_id if receipt else income_category_id
        if category_id is None and not receipt and unallocated:
            result.update(status='income_candidate', category_source='unclassified')
            continue
        if category_id not in active_categories:
            result['reason'] = 'Для поступления не выбрана действующая статья.'; continue
        result.update(status='income_candidate', category_id=category_id,
                      receipt_id=receipt.id if receipt else None)
    # One pre-existing transaction cannot satisfy two different bank lines.
    ledger_counts = Counter(item['ledger_id'] for item in decisions.values()
                            if item['ledger_id'] is not None)
    for item in decisions.values():
        if item['ledger_id'] is not None and ledger_counts[item['ledger_id']] > 1:
            item.update(status='review', reason='Несколько строк подходят к одной операции.',
                        category_id=None, receipt_id=None, ledger_id=None)
    return decisions


def approved_decision_matches(decision, approved, candidate_statuses, placeholder_source=None, placeholder_id=None):
    """A confirmation never authorizes a newly discovered or different candidate."""
    if not approved or approved.get('status') not in candidate_statuses:
        return False
    if decision.get('status') != approved.get('status'):
        return False
    if any(decision.get(key) != approved.get(key) for key in ('ledger_id','receipt_id','attach_receipt')):
        return False
    if decision.get('category_id') != approved.get('category_id'):
        if not (approved.get('category_id') is None and approved.get('category_source') == placeholder_source
                and placeholder_source is not None and placeholder_id is not None
                and decision.get('category_id') == placeholder_id):
            return False
    return True


def process_incoming(s, batch, lines, account, user, income_category_id=None, approved=None):
    """Recheck and apply receipts under the caller's serialized write guard."""
    decisions = classify_incoming(s, batch, lines, account, user, income_category_id)
    placeholder = s.get(Category, income_category_id) if income_category_id is not None else None
    placeholder_id = (placeholder.id if placeholder and placeholder.company_id == batch.company_id
                      and placeholder.type == 'income' and placeholder.activity == 'unclassified' else None)
    if not can_post_statement(user) or ('write' not in perms_of(user) and approved is None):
        return {'review': len(decisions)}
    counts = Counter()
    for line in sorted((l for l in lines if l.id in decisions), key=lambda l: (l.date, l.line, l.id)):
        decision = decisions[line.id]
        status = decision['status']
        if approved is not None and not approved_decision_matches(decision,approved.get(line.id),
                ('income_candidate','income_matched_candidate'),'unclassified',placeholder_id):
            line.status='review'; line.reason='Предпросмотр изменился: проверьте операцию заново.'
            counts['review']+=1
            continue
        if status == 'income_matched_candidate':
            if decision.get('attach_receipt'):
                entry = s.get(Ledger, decision['ledger_id'])
                receipt = s.get(Receipt, decision['receipt_id'])
                entry.receipt_id = receipt.id
                receipt.status = 'received'
                log(s, user, 'Связано поступление с выпиской', 'receipt', receipt.id,
                    f'операция {entry.id}; строка выписки {line.id}')
            line.ledger_id = decision['ledger_id']
            line.classification = 'receipt'
            line.status = 'income_matched'
            line.reason = ''
            log(s, user, 'Связана выписка с операцией', 'bank_statement_line', line.id,
                f'операция {line.ledger_id}')
        elif status == 'income_candidate':
            try:
                with s.begin_nested():
                    data = SimpleNamespace(account_id=account.id, to_account_id=None,
                        category_id=decision['category_id'], kind='in', amount=money(line.amount),
                        date=line.date, counterparty=line.correspondent_name[:160],
                        reference='BANK-' + line.fingerprint,
                        note='Банковская выписка, документ ' + line.document_number,
                        request_id=None, request_version=None, receipt_id=decision['receipt_id'])
                    entry = post_ledger(s, user, data, bank_statement_import=True)
                    line.ledger_id = entry.id
                    line.classification = 'receipt'
                    line.status = 'auto_income_posted'
                    line.reason = ''
            except HTTPException as exc:
                line.status = 'review'; line.reason = str(exc.detail)[:160]
        else:
            line.status = 'review'; line.reason = decision['reason']
        counts[line.status] += 1
    return dict(counts)
