"""Protected API for the Cash Zuma Telegram bot and the user's Telegram link.

The bot authenticates as a service (HTTP Basic, BOT_CLIENT_ID / BOT_CLIENT_SECRET).
Its sessions are deliberately unscoped: company rules are applied explicitly here,
from the group allow-list (BOT_REPORT_GROUPS) or from each responsible user's own
company access. The bot never receives rows it would have to filter itself.
Contract: Cash_Zuma_Bot/API_CONTRACT_RU.md.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import logging
import os
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select

from .company_scope import SERVICE_CODE
from .db import (Account, Audit, Budget, Category, Company, CompanyUser, Ledger, PaymentRequest,
                 Setting, TelegramLink, TelegramLinkCode, User, now, unit)
from .security import digest, session_user
from .services import account_balance, budget_state, effective_cashflows, funds_state, log, money
from .services import today as tashkent_today

router = APIRouter()

CURRENCY_ORDER = {'UZS': 0, 'USD': 1, 'EUR': 2}
STAGE_LABELS = {'finance': 'Проверка финансистом', 'director': 'Утверждение директором',
                'bank_payment': 'Оплата расчётным бухгалтером', 'cash_payment': 'Выдача кассиром'}
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no 0/O, 1/I: codes are retyped by hand
CODE_TTL = timedelta(minutes=10)
WORKDAY_START = time(8, 0)
TASHKENT_OFFSET = timedelta(hours=5)  # UTC+5 all year, as in services.today()
DIGIT_RUN = re.compile(r'\d(?:[\d \-./_\u2013]*\d)?(?:,\d{2}(?!\d))?')
AMOUNT = re.compile(r'\d+[.,]\d{2}')
GROUP_ENTRY = re.compile(r'^\s*(-\d{5,})\s*:\s*([A-Za-z0-9_.,\s-]+?)\s*$')


# ---------------------------------------------------------------- helpers

def mask_digits(text, limit=None):
    """Hide account/card numbers (9+ digits, any common separators) but keep amounts like 150000000.00.

    The bot applies the same rule in bot/security.py redact_free_text."""
    def mask(match):
        run = match.group(0)
        digits = re.sub(r'\D', '', run)
        return run if len(digits) < 9 or AMOUNT.fullmatch(run) else '••' + digits[-4:]
    text = DIGIT_RUN.sub(mask, ' '.join(str(text or '').split()))
    if limit and len(text) > limit:
        text = text[:limit - 1].rstrip() + '…'
    return text


def utc_iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def audit(company_ids, action, entity_id, detail):
    """Bot calls are recorded once per affected company so each company journal shows them."""
    with unit(True) as s:
        for company_id in company_ids or [None]:
            s.add(Audit(company_id=company_id, user_id=None, action=action, entity='bot',
                        entity_id=str(entity_id), detail=detail))


def bot_username():
    name = os.getenv('TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')
    return name if re.fullmatch(r'[A-Za-z0-9_]{5,32}', name) else None


def report_groups():
    """BOT_REPORT_GROUPS: "-1001234567890:UZGERMED,ZUMA; -100…:ZUMA" → {chat_id: {codes}}."""
    groups = {}
    for part in os.getenv('BOT_REPORT_GROUPS', '').split(';'):
        if not part.strip():
            continue
        match = GROUP_ENTRY.match(part)
        if not match:
            logging.warning('BOT_REPORT_GROUPS: skipped a malformed entry')
            continue
        codes = {c.strip().upper() for c in match.group(2).split(',') if c.strip()}
        if codes:
            groups[int(match.group(1))] = codes
    return groups


def authenticate_bot(request):
    client_id = os.getenv('BOT_CLIENT_ID', '').strip()
    secret = os.getenv('BOT_CLIENT_SECRET', '').strip()
    if not client_id or len(secret) < 32:
        raise HTTPException(503, 'API бота на сайте не настроен.')
    user, password = '', ''
    header = request.headers.get('Authorization', '')
    if header.startswith('Basic '):
        try:
            user, _, password = base64.b64decode(header[6:], validate=True).decode('utf-8').partition(':')
        except (binascii.Error, UnicodeDecodeError):
            pass
    # Both comparisons always run so the response time does not reveal which part matched.
    same_id = hmac.compare_digest(user.encode(), client_id.encode())
    same_secret = hmac.compare_digest(password.encode(), secret.encode())
    if not (same_id and same_secret):
        audit(None, 'Бот: отказ в доступе', '', request.url.path)
        raise HTTPException(401, 'Неверный ключ бота.', headers={'WWW-Authenticate': 'Basic realm="cash-zuma-bot"'})


def currency_key(currency):
    return CURRENCY_ORDER.get(currency, len(CURRENCY_ORDER)), currency


def day_start(s, account, day):
    """Balance at the start of ``day``; opening balances are set for the start of opening_date."""
    if day == account.opening_date:
        return account.opening
    return account_balance(s, account, day - timedelta(days=1))


def request_number(r):
    return f'CF-{r.id:05d}'


# ---------------------------------------------------------------- morning summary

@router.get('/api/bot/v1/morning-summary')
def morning_summary(request: Request, chat_id: int, report_date: date, today: date = Query()):
    authenticate_bot(request)
    if today > tashkent_today() or report_date != today - timedelta(days=1):
        raise HTTPException(422, 'Сводка строится на сегодня по Ташкенту за предыдущий календарный день.')
    codes = report_groups().get(chat_id)
    if not codes:
        audit(None, 'Бот: группа не разрешена', chat_id, f'Сводка за {report_date} не выдана')
        raise HTTPException(403, 'Группа не разрешена для утренней сводки.')

    with unit() as s:
        companies = {c.id: c for c in s.scalars(select(Company).where(Company.active.is_(True)))
                     if c.code.upper() in codes}
        if not companies:
            raise HTTPException(403, 'Для группы не найдено активных компаний.')
        name = {cid: c.name for cid, c in companies.items()}
        all_accounts = {a.id: a for a in s.scalars(select(Account).where(Account.company_id.in_(list(companies))))}
        # An account opened after ``today`` does not exist yet. An archived account holds no money now,
        # but one archived today may still have held some at the start of the day.
        open_accounts = [a for a in all_accounts.values() if a.opening_date <= today
                         and (not a.archived or day_start(s, a, today) != 0)]
        categories = {c.id: c.name for c in s.scalars(select(Category).where(Category.company_id.in_(list(companies))))}

        balances = None
        if open_accounts:
            ordered = sorted(open_accounts, key=lambda a: (name[a.company_id], a.kind, currency_key(a.currency), a.name))
            balances = [{'company': name[a.company_id], 'kind': a.kind, 'name': mask_digits(a.name, 80),
                         'account_tail': '', 'currency': a.currency, 'amount': money(day_start(s, a, today))}
                        for a in ordered]

        totals = {'in': {}, 'out': {}}
        for t in s.scalars(effective_cashflows(s).where(Ledger.date == report_date)):
            a = all_accounts.get(t.account_id)
            if a is None or t.kind not in totals:
                continue  # other companies; transfers move money inside the company
            key = (name[a.company_id], a.kind, a.currency)
            totals[t.kind][key] = totals[t.kind].get(key, 0) + t.amount

        def movements(kind):
            rows = sorted(totals[kind].items(), key=lambda item: (item[0][0], item[0][1], currency_key(item[0][2])))
            return [{'company': c, 'kind': k, 'currency': cur, 'amount': money(v)} for (c, k, cur), v in rows] or None

        requests = list(s.scalars(select(PaymentRequest).where(
            PaymentRequest.account_id.in_(list(all_accounts)),
            PaymentRequest.status.in_(['approved', 'pending'])).order_by(PaymentRequest.due_date, PaymentRequest.id)))

        def request_line(r, label):
            a = all_accounts[r.account_id]
            return {'number': request_number(r), 'company': name[a.company_id], 'currency': a.currency,
                    'amount': money(r.amount), 'purpose': categories.get(r.category_id, ''), 'stage_label': label}

        payments = [request_line(r, f'Просрочено с {r.due_date:%d.%m.%Y}' if r.due_date < today else '')
                    for r in requests if r.status == 'approved' and r.due_date <= today]
        pending = [request_line(r, STAGE_LABELS['director' if r.finance_approved_by else 'finance'])
                   for r in requests if r.status == 'pending']

        warnings = []
        available = {}
        for a in sorted(open_accounts, key=lambda a: (name[a.company_id], currency_key(a.currency), a.name)):
            state = funds_state(s, a, today)
            key = (a.company_id, a.currency)
            available[key] = available.get(key, 0) + state['available']
            if state['available'] < 0:
                label = mask_digits(a.name, 80)
                if state['reserved']:
                    message = (f'{label}: не хватает {money(-state["available"])} {a.currency} '
                               f'на утверждённые платежи по {today:%d.%m.%Y}')
                else:
                    message = f'{label}: отрицательный остаток {money(state["actual"])} {a.currency}'
                warnings.append({'company': name[a.company_id], 'message': message})
        for (cid, currency), value in sorted(available.items(), key=lambda item: (name[item[0][0]], currency_key(item[0][1]))):
            reserve = s.get(Setting, f'company:{cid}:reserve_{currency}')
            if reserve and int(reserve.value) > 0 and value < int(reserve.value):
                warnings.append({'company': name[cid], 'message':
                                 f'Доступно {money(value)} {currency} — ниже минимального резерва {money(int(reserve.value))} {currency}'})
        month = str(today)[:7]
        for b in s.scalars(select(Budget).where(Budget.month == month, Budget.category_id.in_(list(categories)))
                           .order_by(Budget.category_id, Budget.currency)):
            state = budget_state(s, b.category_id, month, b.currency)
            if state['over']:
                category = s.get(Category, b.category_id)
                warnings.append({'company': name[category.company_id], 'message':
                                 f'Бюджет «{category.name}» за {today:%m.%Y} превышен на '
                                 f'{money(state["used"] - state["limit"])} {b.currency}'})

        result = {'chat_id': chat_id, 'report_date': str(report_date), 'today': str(today),
                  'balances': balances, 'income': movements('in'), 'expense': movements('out'),
                  'payments_today': payments, 'pending_approvals': pending, 'warnings': warnings}
    audit(list(companies), 'Бот: утренняя сводка', chat_id, f'Группа {chat_id}; сводка за {report_date:%d.%m.%Y}')
    return result


# ---------------------------------------------------------------- pending requests

def responsible(r, account, company, users, members):
    """Stage of an actionable request and the people who can act on it now."""
    def reachable(u):
        if u.role == 'admin':
            return True
        return company.code != SERVICE_CODE and u.id in members.get(company.id, ())

    if r.status == 'pending':
        if r.finance_approved_by is None:
            stage, chain, excluded = 'finance', [('finance',), ('admin',)], {r.creator_id, r.last_editor_id}
        else:
            # The director approval must come from someone other than the financier (app/main.py decide).
            stage, chain = 'director', [('director',), ('admin',)]
            excluded = {r.creator_id, r.last_editor_id, r.finance_approved_by}
    elif r.status == 'approved' and account.kind == 'bank':
        stage, chain, excluded = 'bank_payment', [('accountant',), ('finance',), ('admin',)], set()
    elif r.status == 'approved':
        # A cashier sees only the requests they created (app/main.py requests_list), so only the
        # author-cashier can pay it from the list; other cash requests go to the financier.
        stage, chain, excluded = 'cash_payment', [('cashier',), ('finance',), ('admin',)], set()
    else:
        return None, []
    for roles in chain:
        found = [u for u in users if u.role in roles and u.id not in excluded and reachable(u)
                 and (u.role != 'cashier' or u.id == r.creator_id)]
        if found:
            return stage, found
    return stage, []


@router.get('/api/bot/v1/pending-requests')
def pending_requests(request: Request):
    authenticate_bot(request)
    origin = os.getenv('PUBLIC_ORIGIN', '').rstrip('/')
    url = origin + '/#requests' if origin.startswith('https://') else ''
    with unit() as s:
        companies = {c.id: c for c in s.scalars(select(Company).where(Company.active.is_(True)))}
        accounts = {a.id: a for a in s.scalars(select(Account))}
        categories = {c.id: c.name for c in s.scalars(select(Category))}
        users = list(s.scalars(select(User).where(User.active.is_(True)).order_by(User.id)))
        links = {link.user_id: link.telegram_user_id for link in s.scalars(select(TelegramLink))}
        members = {}
        for m in s.scalars(select(CompanyUser)):
            members.setdefault(m.company_id, set()).add(m.user_id)
        # Every change of a request (creation, edit, decision, payment reversal) is audited
        # with entity='request'; the latest one is when it entered its current stage.
        entered = {}
        for entity_id, stamp in s.execute(select(Audit.entity_id, func.max(Audit.created_at))
                                          .where(Audit.entity == 'request').group_by(Audit.entity_id)):
            if str(entity_id).isdigit():
                entered[int(entity_id)] = stamp

        items = []
        current = tashkent_today()
        for r in s.scalars(select(PaymentRequest).where(PaymentRequest.status.in_(['pending', 'approved']))
                           .order_by(PaymentRequest.id)):
            if r.status == 'approved' and r.due_date > current:
                continue  # payment is due later; the payer is reminded from the due date
            account = accounts.get(r.account_id)
            company = companies.get(account.company_id) if account else None
            if company is None:
                continue
            stage, people = responsible(r, account, company, users, members)
            since = entered.get(r.id, r.created_at)
            if r.status == 'approved':
                # Payment waits from the start of the working day it is due, not from the approval.
                since = max(since, datetime.combine(r.due_date, WORKDAY_START) - TASHKENT_OFFSET)
            purpose = mask_digits(f'{categories.get(r.category_id, "")}: {r.purpose}', 200)
            for u in people:
                if u.id not in links:
                    continue  # nobody to write to; linking is up to the user
                items.append({'request_id': r.id, 'number': request_number(r), 'status': r.status,
                              'stage': stage, 'stage_label': STAGE_LABELS[stage],
                              'assignee_user_id': u.id, 'assignee_telegram_id': links[u.id],
                              'stage_entered_at': utc_iso(since),
                              'company': company.name, 'amount': money(r.amount), 'currency': account.currency,
                              'purpose': purpose, 'url': url})
    audit(None, 'Бот: заявки для напоминаний', '', f'Строк: {len(items)}')
    return {'items': items}


# ---------------------------------------------------------------- Telegram link

class LinkIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    code: str = Field(min_length=6, max_length=16, pattern=r'^[A-Za-z0-9]+$')
    telegram_user_id: int = Field(gt=0, lt=2**53)


def code_hash(code):
    return digest('telegram-link:' + code.strip().upper())


@router.post('/api/bot/v1/telegram-links')
def confirm_telegram_link(data: LinkIn, request: Request):
    authenticate_bot(request)
    with unit(True) as s:
        row = s.get(TelegramLinkCode, code_hash(data.code))
        user = s.get(User, row.user_id) if row and row.expires_at > now() else None
        if user is None or not user.active:
            raise HTTPException(404, 'Код не найден или устарел.')
        other = s.scalar(select(TelegramLink).where(TelegramLink.telegram_user_id == data.telegram_user_id))
        if other and other.user_id != user.id:
            raise HTTPException(409, 'Этот Telegram уже привязан к другому пользователю.')
        s.delete(row)
        link = s.get(TelegramLink, user.id)
        if link is None:
            s.add(TelegramLink(user_id=user.id, telegram_user_id=data.telegram_user_id, linked_at=now()))
        else:
            link.telegram_user_id = data.telegram_user_id
            link.linked_at = now()
        log(s, user, 'Привязан Telegram', 'user', user.id, 'Подтверждено ботом одноразовым кодом')
        return {'user_id': user.id, 'name': user.name}


class RevokeIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    code: str = Field(min_length=6, max_length=16, pattern=r'^[A-Za-z0-9]+$')


@router.post('/api/bot/v1/telegram-links/revoke')
def revoke_telegram_link_code(data: RevokeIn, request: Request):
    """The bot saw this code in a group chat: other members have seen it, so it must stop working."""
    authenticate_bot(request)
    with unit(True) as s:
        row = s.get(TelegramLinkCode, code_hash(data.code))
        if row is None:
            return {'revoked': False}
        s.delete(row)
        s.add(Audit(user_id=row.user_id, action='Код привязки Telegram отменён', entity='user',
                    entity_id=str(row.user_id), detail='Код был отправлен в группу'))
        return {'revoked': True}


def revoke_telegram(s, user_id):
    """Remove a user's Telegram link and pending codes, e.g. together with their sessions."""
    s.execute(delete(TelegramLinkCode).where(TelegramLinkCode.user_id == user_id))
    return s.execute(delete(TelegramLink).where(TelegramLink.user_id == user_id)).rowcount > 0


@router.get('/api/telegram')
def telegram_status(request: Request):
    with unit() as s:
        user, _ = session_user(s, request)
        link = s.get(TelegramLink, user.id)
        return {'linked': link is not None, 'linked_at': utc_iso(link.linked_at) if link else None,
                'telegram_id_tail': str(link.telegram_user_id)[-4:] if link else None,
                'bot_username': bot_username()}


@router.post('/api/telegram/code')
def telegram_code(request: Request):
    code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    with unit(True) as s:
        user, _ = session_user(s, request)
        # A new code cancels the previous one; the code itself is never stored or logged.
        s.execute(delete(TelegramLinkCode).where(TelegramLinkCode.user_id == user.id))
        s.add(TelegramLinkCode(code_hash=code_hash(code), user_id=user.id, expires_at=now() + CODE_TTL))
        log(s, user, 'Выдан код привязки Telegram', 'user', user.id)
    name = bot_username()
    return {'code': code, 'expires_in': int(CODE_TTL.total_seconds()),
            'deep_link': f'https://t.me/{name}?start={code}' if name else None}


@router.delete('/api/telegram')
def telegram_unlink(request: Request):
    with unit(True) as s:
        user, _ = session_user(s, request)
        if revoke_telegram(s, user.id):
            log(s, user, 'Отвязан Telegram', 'user', user.id)
    return {'ok': True}
