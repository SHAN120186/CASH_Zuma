"""Correct the cash date of an existing paid request using its bank statement."""
import hashlib
import hmac
import io
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from pypdf import PdfReader
from sqlalchemy import or_, select

from .bank_statement_identity import check_statement_currency, confirmed_bank_binding
from .bank_statement_models import BankStatementBatch, BankStatementLine
from .cash_funding import require_independent_ledger
from .clock import today
from .db import Account, Audit, Ledger, PaymentRequest, RequestDocument, now, unit
from .requests_api import can_view_request
from .section_access import can_edit_section
from .security import company_role, session_user
from .services import deleted_ledger_ids, effective_cashflows, get, log, money, validate_running_balance

router = APIRouter()
PATH = '/api/bank-statements/{batch_id}/payment-date'
ACTION = 'Исправлена дата списания по выписке'


class Evidence(BaseModel):
    request_id: int = Field(gt=0)
    document_number: str = Field(min_length=1, max_length=160)


class Confirmation(Evidence):
    expected: str = Field(pattern=r'^[a-f0-9]{64}$')


def may_correct(user):
    return (company_role(user) in ('finance', 'director')
            and can_edit_section(user, 'ledger', 'requests'))


def documented_payee(s, line, req, entry):
    """Keep an explicitly verified attachment match when the draft is reopened."""
    if req.counterparty_inn or not line.reviewed_by or entry.reference != line.document_number:
        return False
    record = s.scalar(select(Audit).where(Audit.company_id == line.company_id,
        Audit.action == ACTION, Audit.entity == 'ledger', Audit.entity_id == str(entry.id))
        .order_by(Audit.id.desc()).limit(1))
    if not record:
        return False
    proof = json.loads(record.detail)
    return (proof.get('line_id') == line.id and proof.get('request_id') == req.id
            and proof.get('fingerprint') == line.fingerprint and proof.get('document_id') is not None
            and proof.get('bank_date') == str(entry.date) and proof.get('amount') == money(entry.amount)
            and proof.get('bank_inn') == line.correspondent_inn)


def _evidence_file(s, req, line):
    if req.counterparty_inn:
        if req.counterparty_inn != line.correspondent_inn:
            raise HTTPException(409, 'ИНН получателя в заявке и в выписке различается.')
        return None
    # A blank INN requires a document tied to this request, not an amount-only match.
    for doc in s.scalars(select(RequestDocument).where(RequestDocument.request_id == req.id,
            RequestDocument.active.is_(True), RequestDocument.mime == 'application/pdf').order_by(RequestDocument.id)):
        number = Path(doc.filename).stem.strip()
        if len(number) < 4 or not any(c.isdigit() for c in number):
            continue
        pattern = re.compile(r'(?<!\w)' + re.escape(number) + r'(?!\w)', re.I)
        if not pattern.search(line.purpose) or len(doc.content) > 4_000_000:
            continue
        try:
            pages = PdfReader(io.BytesIO(doc.content)).pages
            content = '\n'.join(pages[i].extract_text() or '' for i in range(min(5, len(pages))))
        except Exception:
            continue
        if pattern.search(content) and re.search(r'(?<!\d)' + re.escape(line.correspondent_inn) + r'(?!\d)', content):
            return doc
    raise HTTPException(409, 'Нужен подтверждающий документ заявки с номером договора и ИНН получателя из выписки.')


def _review(s, user, batch_id, data):
    if not may_correct(user):
        raise HTTPException(403, 'Дату банковского списания уточняет финансовый директор или директор компании.')
    batch = get(s, BankStatementBatch, batch_id)
    if batch.created_by != user.id and user.role != 'admin':
        raise HTTPException(403, 'Черновик доступен автору или администратору.')
    account = get(s, Account, batch.account_id)
    if (batch.status != 'preview' or not batch.source_ready or not batch.balance_matches
            or batch.declared_totals_match is not True or account.archived or account.kind != 'bank'
            or account.company_id != batch.company_id or account.currency != batch.currency):
        raise HTTPException(409, 'Проверьте счёт и итоги черновика выписки.')
    check_statement_currency(account, {'account_number':batch.account_number,'currency':batch.currency})
    confirmed_bank_binding(s, account, batch.account_number, batch.owner_inn)
    lines = list(s.scalars(select(BankStatementLine).where(BankStatementLine.batch_id == batch.id,
        BankStatementLine.direction == 'out', BankStatementLine.document_number == data.document_number.strip())))
    if len(lines) != 1:
        raise HTTPException(409, 'Нужна одна строка списания с указанным номером платёжки.')
    line = lines[0]
    if (line.company_id != batch.company_id or line.account_id != account.id or line.amount <= 0
            or not line.include_in_daily or line.uncertain or line.occurrence != 1 or not line.correspondent_inn
            or line.correspondent_inn == batch.owner_inn or not line.correspondent_name
            or line.status not in ('review','paid_match','manual_match')
            or not (account.opening_date <= line.date <= today() and batch.period_from <= line.date <= batch.period_to)):
        raise HTTPException(409, 'Строка выписки недоступна для исправления даты оплаты.')
    req = get(s, PaymentRequest, data.request_id)
    if not can_view_request(s, user, req):
        raise HTTPException(404, 'Заявка не найдена.')
    if req.status != 'paid' or req.account_id != account.id or req.amount != line.amount or not req.approved_at:
        raise HTTPException(409, 'Нужна оплаченная заявка этого счёта на сумму платёжки.')
    markers = {int(n) for n in re.findall(r'\bCF-0*(\d+)\b', line.purpose, re.I)}
    if markers and markers != {req.id}:
        raise HTTPException(409, 'В назначении платежа указана другая заявка.')
    entries = list(s.scalars(effective_cashflows(s).where(Ledger.account_id == account.id,
        Ledger.kind == 'out', or_(Ledger.request_id == req.id, Ledger.reference == line.document_number))))
    if (len(entries) != 1 or entries[0].request_id != req.id or entries[0].reference != line.document_number
            or entries[0].amount != line.amount or entries[0].id in deleted_ledger_ids(s)):
        raise HTTPException(409, 'Нужна одна действующая оплата с тем же документом, счётом и суммой.')
    entry = entries[0]
    require_independent_ledger(s, entry)
    if entry.date == line.date:
        raise HTTPException(409, 'Дата оплаты уже совпадает с датой списания банка.')
    if line.request_id not in (None, req.id) or line.ledger_id not in (None, entry.id):
        raise HTTPException(409, 'У платёжки уже есть другая привязка.')
    conflict = s.scalar(select(BankStatementLine.id).join(BankStatementBatch).where(
        BankStatementLine.account_id == account.id, BankStatementLine.id != line.id,
        or_(BankStatementBatch.status == 'committed', BankStatementLine.classification.like('paid:%')
            & (BankStatementBatch.status == 'preview')),
        or_(BankStatementLine.request_id == req.id, BankStatementLine.ledger_id == entry.id,
            BankStatementLine.fingerprint == line.fingerprint)).limit(1))
    start, end = sorted((line.date, entry.date))
    reconciled = s.scalar(select(BankStatementBatch.id).where(BankStatementBatch.account_id == account.id,
        BankStatementBatch.status == 'committed', BankStatementBatch.period_from <= end,
        BankStatementBatch.period_to >= start).limit(1))
    if conflict or reconciled:
        raise HTTPException(409, 'Даты затрагивают проведённую выписку или сохранённую связь другой строки.')
    doc = _evidence_file(s, req, line)
    result = dict(line_id=line.id, batch_id=batch.id, request_id=req.id, ledger_id=entry.id,
        amount=money(entry.amount), currency=account.currency, old_date=str(entry.date), bank_date=str(line.date),
        approval=req.approved_at.isoformat(), document_number=line.document_number, bank_inn=line.correspondent_inn,
        payee=line.correspondent_name, fingerprint=line.fingerprint, document_id=doc.id if doc else None,
        document_hash=doc.sha256 if doc else None, document_name=doc.filename if doc else None)
    result['expected'] = hashlib.sha256(json.dumps([user.id, req.version, result], sort_keys=True).encode()).hexdigest()
    return result, line, entry, account


@router.post(PATH + '/preview')
def preview(batch_id: int, data: Evidence, request: Request):
    with unit() as s:
        user, _ = session_user(s, request, 'write')
        return _review(s, user, batch_id, data)[0]


@router.post(PATH + '/confirm')
def confirm(batch_id: int, data: Confirmation, request: Request):
    with unit(True) as s:
        user, _ = session_user(s, request, 'write')
        result, line, entry, account = _review(s, user, batch_id, data)
        if not hmac.compare_digest(data.expected, result['expected']):
            raise HTTPException(409, 'Данные изменились. Повторите проверку даты.')
        # Budget actuals follow the bank date. This neither approves new spending
        # nor updates an approved plan, its limits, or the original request.
        entry.date = line.date
        line.ledger_id, line.request_id = entry.id, entry.request_id
        line.status, line.classification = 'manual_match', f'paid:{entry.date}'
        line.reason, line.review_note = '', ACTION
        line.reviewed_by, line.reviewed_at = user.id, now()
        s.flush()
        validate_running_balance(s, account)
        log(s, user, ACTION, 'ledger', entry.id, json.dumps(result, ensure_ascii=False))
        return result
