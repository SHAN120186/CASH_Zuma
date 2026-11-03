"""Conservative posting of confirmed bank debits without an identified article.

This module never makes a bank payment.  The calling statement workflow must
obtain an explicit preview confirmation, hold ``unit(True)``, and reconcile the
whole bank statement including its closing balance before committing entries.
"""
from collections import Counter
import re
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import or_, select

from .bank_statement_incoming import opening_matches, approved_decision_matches
from .bank_statement_models import BankStatementBatch, BankStatementLine
from .category_catalog import catalog
from .clock import today
from .db import Ledger, PaymentRequest
from .bank_statement_permissions import can_post_statement
from .security import perms_of
from .services import deleted_ledger_ids, money, post_ledger


REQUEST_NUMBER = re.compile(r'(?<![A-Za-z0-9])CF-0*[1-9][0-9]*(?![A-Za-z0-9])', re.I)
# Broad signals are intentionally held for an accountant rather than silently
# turning a bank charge, loan interest or corporate-card transfer into an
# ordinary external expense.  The statement's full text is preserved.
POSSIBLE_FEE = re.compile(
    r'комис|комисс|ком\.\s|\bswift\b|процент|%|банковск\w*\s+(?:услуг|обслуж)|'
    r'хизмат\w*\s*(?:банк|банка)|банк\w*\s*(?:хизмат|услуг)|'
    r'расч[её]тно[-\s]кассов\w*|обслуживан\w*\s+(?:сч[её]т|банк)|'
    r'удержан\w*\s+(?:банком|за\s+услуг)|\bbank\s+(?:fee|charge|service)', re.I)
POSSIBLE_TRANSFER = re.compile(
    r'внутренн\w*\s+перевод|между\s+своими|собственн\w*\s+сч[её]т|'
    r'перевод\w*\s+(?:на|между)\s+(?:сво[йею]|собственн)|'
    r'корпоративн\w*.{0,20}карт|корп\.?\s*(?:пл\.?\s*)?карт|'
    r'(?:карт|card).{0,24}(?:корпоративн|корп\.)|'
    r'\bown\s+(?:account|transfer)\b|ўз\s+(?:ҳисоб|хисоб)', re.I)


def _name(value):
    return ' '.join((value or '').casefold().split())


def _key(line):
    return (line.account_id, line.date, line.amount, line.correspondent_inn)


def _active_unclassified_expense(s):
    return {item.id: item for item, retired in catalog(s, include_retired=True)
            if not retired and item.type == 'outcome' and item.activity == 'unclassified'}


def classify_unallocated_out(s, batch, lines, account, user, outgoing_category_id=None,
                             unallocated=False):
    """Return non-mutating decisions for potentially independent bank debits.

    With ``unallocated=True``, candidates can be previewed without creating the
    system category; the confirmation must pass its real, company-scoped ID.
    """
    allowed = can_post_statement(user)
    categories = _active_unclassified_expense(s)
    if outgoing_category_id is not None and outgoing_category_id not in categories:
        raise HTTPException(422, 'Выберите действующую статью нераспределённых расходов этой компании.')
    if (account.id != batch.account_id or account.company_id != batch.company_id or account.archived
            or account.kind != 'bank' or account.currency != batch.currency):
        raise HTTPException(409, 'Счёт или валюта изменились. Проверьте выписку.')
    opening_ok = opening_matches(s, batch, account)
    rows = [line for line in lines if line.direction == 'out'
            and line.status in ('review', 'reviewed', 'outgoing_candidate')]
    current_ids = {line.id for line in lines}
    candidate_ids = {line.id for line in rows}
    previous = list(s.scalars(select(BankStatementLine).join(BankStatementBatch,
        BankStatementBatch.id == BankStatementLine.batch_id).where(
            BankStatementBatch.status == 'committed', BankStatementLine.account_id == account.id,
            BankStatementLine.direction == 'out')))
    prior_rows = ([line for line in previous if line.id not in current_ids]
                  + [line for line in lines if line.direction == 'out' and line.id not in candidate_ids])
    bank_rows = rows + prior_rows
    documents = Counter((line.account_id, line.date, line.document_number)
                        for line in bank_rows if line.document_number)
    requests = list(s.scalars(select(PaymentRequest).where(PaymentRequest.account_id == account.id)))
    ledger = list(s.scalars(select(Ledger).where(or_(Ledger.account_id == account.id,
                                                     Ledger.to_account_id == account.id))))
    ledger_by_id = {entry.id: entry for entry in ledger}
    reversed_ids = {entry.reversal_of for entry in ledger if entry.reversal_of is not None}
    deleted_ids = deleted_ledger_ids(s)
    known_accounts = {item.account_number for item in s.scalars(select(BankStatementBatch).where(
        BankStatementBatch.status == 'committed', BankStatementBatch.company_id == batch.company_id))}
    known_accounts.add(batch.account_number)
    decisions = {}
    posted_statuses = {'auto_outgoing_posted','other_matched','auto_paid','paid_match','manual_paid',
                       'manual_match','auto_fee_posted','fee_matched'}

    def already_paid_by_another_bank_document(req, line):
        """Ignore a historical request only with a proved, active bank link."""
        if req.status != 'paid':
            return False
        for other in prior_rows:
            entry=ledger_by_id.get(other.ledger_id)
            if (other.document_number != line.document_number and other.include_in_daily and not other.uncertain
                    and other.status in ('auto_paid','paid_match','manual_paid','manual_match')
                    and entry and entry.request_id == req.id and entry.id not in reversed_ids
                    and entry.id not in deleted_ids and entry.reversal_of is None
                    and entry.account_id == account.id and entry.kind == 'out'
                    and entry.amount == req.amount == other.amount and entry.date == other.date):
                return True
        return False

    for line in rows:
        result = {'status':'review','reason':'','category_id':None,'ledger_id':None}
        decisions[line.id] = result
        if (not batch.source_ready or line.batch_id != batch.id or line.company_id != batch.company_id
                or line.account_id != account.id):
            result['reason'] = 'Данные выписки или счёта требуют проверки.'; continue
        if line.uncertain:
            result['reason'] = 'Возможный повтор выписки требует проверки.'; continue
        if line.classification not in ('','unresolved','other'):
            result['reason'] = 'Выбранное назначение операции требует проверки.'; continue
        if not allowed:
            result['reason'] = 'Для проведения списания нужно право записи операций.'; continue
        if not opening_ok:
            result['reason'] = 'Начальный остаток выписки не совпадает с остатком счёта сайта.'; continue
        if line.date < account.opening_date or line.date > today() or line.amount <= 0:
            result['reason'] = 'Дата или сумма списания требует проверки.'; continue
        if not batch.owner_inn or not line.correspondent_inn:
            result['reason'] = 'Для распознавания нужен ИНН владельца и контрагента.'; continue
        if (line.correspondent_inn == batch.owner_inn or
                line.correspondent_account in known_accounts or POSSIBLE_TRANSFER.search(line.purpose or '')):
            result['reason'] = 'Возможный собственный перевод требует проверки.'; continue
        if POSSIBLE_FEE.search((line.purpose or '')+' '+(line.correspondent_name or '')):
            result['reason'] = 'Возможная банковская комиссия или проценты требуют проверки.'; continue
        if REQUEST_NUMBER.search(line.purpose or '') or line.request_id:
            result['reason'] = 'Возможно списание по заявке: требуется проверка.'; continue
        if not line.document_number or not line.correspondent_name or documents[(line.account_id,line.date,line.document_number)] != 1:
            result['reason'] = 'Номер документа повторяется или не заполнен.'; continue
        # A request can be overdue by months. Do not defeat the approval flow by
        # treating a same-account/INN/amount request as an unrelated debit just
        # because its planned date is outside an arbitrary time window.
        if any(req.amount == line.amount
               and (not req.counterparty_inn or req.counterparty_inn == line.correspondent_inn)
               and not already_paid_by_another_bank_document(req,line)
               for req in requests):
            result['reason'] = 'Похожая заявка требует проверки перед списанием.'; continue
        similar = [other for other in prior_rows if _key(other) == _key(line)]
        def proven_prior(other):
            entry=ledger_by_id.get(other.ledger_id)
            return bool(other.status in posted_statuses and entry and entry.id not in reversed_ids
                and entry.id not in deleted_ids and entry.reversal_of is None and entry.account_id == account.id
                and entry.kind in ('out','transfer') and entry.date == other.date and entry.amount == other.amount)
        if any(not proven_prior(other) for other in similar):
            result['reason'] = 'Похожее списание из прошлой выписки требует проверки.'; continue
        if any(entry.account_id == account.id and entry.reference == line.document_number
               and (entry.date != line.date or entry.amount != line.amount) for entry in ledger):
            result['reason'] = 'Номер документа уже есть в журнале с другими реквизитами.'; continue
        # Existing same-day amounts may already be manually recorded.  Ignore
        # only entries proven linked to a distinct bank document.
        linked_other = {other.ledger_id for other in prior_rows if proven_prior(other)
            and other.date == line.date
            and other.amount == line.amount and other.document_number != line.document_number}
        existing = [entry for entry in ledger if entry.id not in linked_other
                    and entry.account_id == account.id and entry.date == line.date
                    and entry.amount == line.amount and entry.kind in ('out','transfer')]
        if existing:
            result['reason'] = 'Похожая операция уже есть в журнале или была отменена.'; continue
        if any(entry.account_id == account.id and entry.kind in ('out','transfer')
               and entry.amount == line.amount and entry.date != line.date
               and (entry.reference == line.document_number or
                    (abs((entry.date-line.date).days) <= 7
                     and _name(entry.counterparty) == _name(line.correspondent_name))) for entry in ledger):
            result['reason'] = 'Похожая операция на другую дату требует проверки.'; continue
        if any(entry.account_id == account.id and entry.reference == 'BANK-' + line.fingerprint for entry in ledger):
            result['reason'] = 'Номер банковской операции уже занят и требует проверки.'; continue
        if outgoing_category_id is None and unallocated:
            result.update(status='outgoing_candidate',category_source='unclassified_out');continue
        if outgoing_category_id not in categories:
            result['reason'] = 'Для списания не выбрана действующая статья.'; continue
        result.update(status='outgoing_candidate',category_id=outgoing_category_id)
    return decisions


def process_unallocated_out(s, batch, lines, account, user, outgoing_category_id=None, approved=None):
    """Recheck and post under the caller's write guard and outer savepoint."""
    decisions=classify_unallocated_out(s,batch,lines,account,user,outgoing_category_id)
    if not can_post_statement(user) or ('write' not in perms_of(user) and approved is None):
        return {'review':len(decisions)}
    counts=Counter()
    for line in sorted((item for item in lines if item.id in decisions),key=lambda item:(item.date,item.line,item.id)):
        result=decisions[line.id]
        if approved is not None and not approved_decision_matches(result,approved.get(line.id),
                ('outgoing_candidate',),'unclassified_out',outgoing_category_id):
            line.status='review';line.reason='Предпросмотр изменился: проверьте операцию заново.'
            counts['review']+=1
            continue
        if result['status'] == 'outgoing_candidate':
            try:
                with s.begin_nested():
                    data=SimpleNamespace(account_id=account.id,to_account_id=None,
                        category_id=result['category_id'],kind='out',amount=money(line.amount),date=line.date,
                        counterparty=line.correspondent_name[:160],reference='BANK-'+line.fingerprint,
                        note='Банковская выписка, документ '+line.document_number+'; контрагент: '+line.correspondent_name,
                        request_id=None,request_version=None,receipt_id=None)
                    entry=post_ledger(s,user,data,bank_statement_import=True)
                    line.ledger_id=entry.id;line.status='auto_outgoing_posted';line.classification='other';line.reason=''
            except HTTPException as exc:
                line.status='review';line.reason=str(exc.detail)[:160]
        else:
            line.status='review';line.reason=result['reason']
        counts[line.status]+=1
    return dict(counts)
