"""Manual recognition of an executed bank debit predating request approval."""
import io
import re

from fastapi import HTTPException
from pypdf import PdfReader
from sqlalchemy import func, or_, select

from .bank_statement_historical_requests import REQUEST_ID, _conservation, _exact_source_guard, _target
from .bank_statement_models import BankStatementBatch, BankStatementLine
from .clock import today
from .db import Ledger, PaymentRequest, RequestDocument
from .services import local_date


def debit_predates_approval(line, req):
    return req.status == 'approved' and req.approved_at and line.date < local_date(req.approved_at)


def treasury_account(line, req):
    """Read the final payee from the structured treasury payment purpose."""
    fields = (line.purpose or '').split('~', 3)
    if (not re.fullmatch(r'23402[0-9]{15}', line.correspondent_account or '')
            or len(fields) != 4 or fields[0] != '09510'
            or not re.fullmatch(r'[0-9]{27}', fields[1])
            or not re.fullmatch(r'[0-9]{9}', fields[2]) or fields[2] != req.counterparty_inn):
        return None
    return fields[1]


def payee_candidate(line, req, draft):
    return bool(line.correspondent_inn and (req.counterparty_inn == line.correspondent_inn
                or (not draft and debit_predates_approval(line, req) and treasury_account(line, req))))


def check_early_debit(s, line, req, confirmed_day):
    if confirmed_day != line.date:
        raise HTTPException(409, 'Подтвердите дату ранее исполненной банковской платёжки.')
    _, batch, account = _target(s, line.id, 'out')
    if (not batch.balance_matches or batch.declared_totals_match is not True
            or batch.opening + batch.credit_total - batch.debit_total != batch.closing
            or not batch.period_from <= line.date <= batch.period_to
            or not batch.owner_inn or not batch.account_number or line.occurrence != 1
            or req.account_id != account.id or req.amount != line.amount or not req.counterparty_inn
            or not (line.date < local_date(req.approved_at) <= today())
            or batch.owner_inn in (line.correspondent_inn, req.counterparty_inn)):
        raise HTTPException(409, 'Проверьте счёт, получателя, сумму, даты и итоги подтверждённой выписки.')
    if s.scalar(select(BankStatementBatch.id).where(
            BankStatementBatch.company_id == batch.company_id, BankStatementBatch.status == 'committed',
            BankStatementBatch.account_number == line.correspondent_account).limit(1)):
        raise HTTPException(409, 'Перевод между собственными счетами нельзя привязать к заявке на оплату.')
    markers = {int(value) for value in REQUEST_ID.findall(line.purpose)}
    if markers and markers != {req.id}:
        raise HTTPException(409, 'В платёжке указан другой номер заявки.')
    if not markers and s.scalar(select(PaymentRequest.id).where(PaymentRequest.id != req.id,
            PaymentRequest.account_id == account.id, PaymentRequest.amount == req.amount,
            PaymentRequest.counterparty_inn == req.counterparty_inn,
            PaymentRequest.status.in_(('approved', 'paid'))).limit(1)):
        raise HTTPException(409, 'По сумме и получателю найдено несколько заявок.')
    if s.scalar(select(Ledger.id).where(Ledger.request_id == req.id).limit(1)):
        raise HTTPException(409, 'В журнале уже существует операция этой заявки.')
    collision = s.scalar(select(BankStatementLine.id).join(BankStatementBatch).where(
        BankStatementLine.id != line.id, BankStatementLine.account_id == account.id,
        BankStatementBatch.status.in_(('preview', 'committed')),
        or_(BankStatementLine.request_id == req.id,
            (BankStatementLine.fingerprint == line.fingerprint) & BankStatementLine.ledger_id.is_not(None),
            (BankStatementLine.document_number == line.document_number) & (BankStatementLine.direction == 'out')
            & (BankStatementLine.date == line.date) & ((BankStatementLine.batch_id == batch.id)
                | (BankStatementLine.fingerprint != line.fingerprint) | (BankStatementLine.occurrence != 1)))).limit(1))
    if collision:
        raise HTTPException(409, 'У платёжки есть другая строка или привязка. Проверьте повтор.')
    _exact_source_guard(s, line, batch, account)
    _conservation(s, batch, account)
    if req.counterparty_inn == line.correspondent_inn:
        return None
    personal = treasury_account(line, req)
    if personal:
        docs = s.scalars(select(RequestDocument).where(RequestDocument.request_id == req.id,
            RequestDocument.active.is_(True), RequestDocument.mime == 'application/pdf',
            func.length(RequestDocument.content) <= 4_000_000).order_by(RequestDocument.id))
        for doc in docs:
            try:
                pages = PdfReader(io.BytesIO(doc.content)).pages
                words = ' '.join(pages[i].extract_text() or '' for i in range(min(len(pages), 5)))
            except Exception:
                continue
            if all(re.search(r'(?<![0-9])' + value + r'(?![0-9])', words)
                   for value in (personal, req.counterparty_inn)):
                return doc.id
    raise HTTPException(409, 'Для оплаты через казначейство сверьте ИНН и лицевой счёт получателя с документом заявки.')
