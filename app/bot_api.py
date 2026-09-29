"""Protected API for the Cash Zuma Telegram bot, the user's Telegram link and the holding
administrators' list of summary groups on the site.

The bot authenticates as a service (HTTP Basic, BOT_CLIENT_ID / BOT_CLIENT_SECRET).
Its sessions are deliberately unscoped: company rules are applied explicitly here,
from the group allow-list (BOT_REPORT_GROUPS and groups a holding administrator
connected from Telegram) or from each responsible user's own company access.
The bot never receives rows it would have to filter itself.
Contract: Cash_Zuma_Bot/API_CONTRACT_RU.md.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, insert, or_, select

from .company_scope import SERVICE_CODE
from .db import (Account, Audit, Budget, Category, Company, CompanyUser, Delegation, Ledger, LoginAttempt, PaymentRequest,
                 Setting, TelegramGroup, TelegramLink, TelegramLinkCode, User, now, unit)
from .security import COMPANY_ROLES, PERMS, client_ip, digest, login_limited, pay_right, record_failure, session_user
from .services import (account_balance, all_participants, approval_stage, budget_state, effective_cashflows, funds_state, log,
                       money, participants, round_participants, route_complete)
from .services import today as tashkent_today

router = APIRouter()

CURRENCY_ORDER = {'UZS': 0, 'USD': 1, 'EUR': 2}
STAGE_LABELS = {'check': 'Проверка реквизитов расчётным бухгалтером',
                'finance': 'Проверка финансовым директором', 'director': 'Утверждение директором',
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


def audit(company_ids, action, entity_id, detail, user_id=None):
    """Bot calls are recorded once per affected company so each company journal shows them.
    ``user_id`` is the site user behind a Telegram button; Telegram ids are never logged."""
    with unit(True) as s:
        for company_id in company_ids or [None]:
            s.add(Audit(company_id=company_id, user_id=user_id, action=action, entity='bot',
                        entity_id=str(entity_id), detail=detail))


def bot_username():
    name = os.getenv('TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')
    return name if re.fullmatch(r'[A-Za-z0-9_]{5,32}', name) else None


def config_groups():
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


def stored_codes(row):
    return {c for c in row.companies.split(',') if c}


def report_groups(s):
    """Every group allowed to receive the morning summary:
    {chat_id: {'companies': {codes}, 'source': 'config' | 'site', 'title': str}}.

    Groups connected from Telegram live in telegram_groups; BOT_REPORT_GROUPS wins for its chat ids
    (a stored row under such an id is removed at startup, see drop_shadowed_groups)."""
    # A stored group counts only while the holding administrator who connected it could still
    # manage it (active admin). Rows left without one — changed directly in the database, restored
    # from a backup, inserted by hand — get no summary and are removed at startup.
    owners = select(User.id).where(User.active.is_(True), User.role == 'admin')
    groups = {g.chat_id: {'companies': stored_codes(g), 'source': 'site', 'title': g.title}
              for g in s.scalars(select(TelegramGroup).where(TelegramGroup.added_by.in_(owners)))}
    for chat_id, codes in config_groups().items():
        title = groups[chat_id]['title'] if chat_id in groups else ''
        groups[chat_id] = {'companies': codes, 'source': 'config', 'title': title}
    return groups


def group_scope(s, chat_id, group):
    """(codes, scope_id): the active companies the group may receive now and a fingerprint of them.

    The bot stores the fingerprint with a summary snapshot and compares it with the current one
    before every part it sends again after a failure or a restart; a different or missing value
    means the rights changed and the stored parts must not go out. It is derived from the current
    company list, not stored, so it also moves when a company is switched off or the group is
    replaced by BOT_REPORT_GROUPS. The chat id is part of it: equal company lists in two groups
    never share a fingerprint."""
    active = active_codes(s)
    codes = sorted(active[c].code for c in group['companies'] if c in active)
    if not codes:
        return [], None
    scope_id = hashlib.sha256((f'{chat_id}|' + ','.join(c.upper() for c in codes)).encode()).hexdigest()[:16]
    return codes, scope_id


def journal_path(path):
    """Request path for the audit journal: a Telegram user id in it is personal data."""
    return '/api/bot/v1/telegram-users/{telegram_user_id}' if path.startswith('/api/bot/v1/telegram-users/') else path


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
    # Wrong keys are throttled per client address like /api/login (8 failures in 10 minutes),
    # and the journal records the first refusal of a window only: an anonymous caller must not
    # be able to grow the journal or guess the secret without limit.
    keys = [digest('bot-ip:' + client_ip(request))]
    with unit(True) as s:
        if login_limited(s, keys):
            raise HTTPException(429, 'Слишком много попыток. Повторите через 10 минут.')
        if not (same_id and same_secret):
            record_failure(s, keys)
            first = s.get(LoginAttempt, keys[0]).count == 1
    if not (same_id and same_secret):
        if first:
            audit(None, 'Бот: отказ в доступе', '', journal_path(request.url.path))
        raise HTTPException(401, 'Неверный ключ бота.', headers={'WWW-Authenticate': 'Basic realm="cash-zuma-bot"'})
    # Технический журнал HTTP ведётся для вошедших пользователей и для бота с верным ключом.
    request.state.bot = True


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
    with unit() as s:
        group = report_groups(s).get(chat_id)
    if not group:
        audit(None, 'Бот: группа не разрешена', chat_id, f'Сводка за {report_date} не выдана')
        raise HTTPException(403, 'Группа не разрешена для утренней сводки.')
    codes = group['companies']

    with unit() as s:
        companies = {c.id: c for c in s.scalars(select(Company).where(Company.active.is_(True)))
                     if c.code.upper() in codes}
        if not companies:
            raise HTTPException(403, 'Для группы не найдено активных компаний.')
        scope_codes, scope_id = group_scope(s, chat_id, group)
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
        pending = [request_line(r, STAGE_LABELS[approval_stage(r)])
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
                  'companies': scope_codes, 'scope_id': scope_id, 'balances': balances, 'income': movements('in'), 'expense': movements('out'),
                  'payments_today': payments, 'pending_approvals': pending, 'warnings': warnings}
    audit(list(companies), 'Бот: утренняя сводка', chat_id, f'Группа {chat_id}; сводка за {report_date:%d.%m.%Y}')
    return result


# ---------------------------------------------------------------- pending requests

def company_role_of(user, company, members):
    """The user's role in this company, by the same rule as security.scope_user and
    company_scope.available_companies: the founder only reads; everybody else, holding
    administrators included, acts only through a role explicitly assigned in this company
    (users.role never widens access), and staff never in the service company."""
    assigned = members.get(company.id, {})
    if user.role == 'founder':
        return None
    if user.role != 'admin' and company.code == SERVICE_CODE:
        return None
    role = assigned.get(user.id)
    if role not in COMPANY_ROLES:
        return None
    # Сотрудник действует, только пока назначен ровно в одну компанию (access.access_state):
    # при конфликте назначений напоминаний нет, как нет и доступа на сайте.
    if user.role != 'admin' and sum(1 for people in members.get('_valid', {}).values() if user.id in people) > 1:
        return None
    return role


def roles_in(user, company, members, acting):
    """Роли, в которых пользователь может действовать в компании: своя роль (company_role_of) и
    действующие замещения (ВрИО) в этой компании, как в security.scope_user. Учредитель не действует."""
    own = company_role_of(user, company, members)
    roles = {own} if own else set()
    if user.role != 'founder':
        roles |= acting.get((company.id, user.id), set())
    return roles


STAGE_ROLE = {'check': 'accountant', 'finance': 'finance', 'director': 'director'}


def responsible(r, account, company, users, members, acting=None, past=None):
    """Stage of an actionable request and the people who can act on it now (app/main.py decide,
    services.check_payer). Nobody else is reminded: a reminder must lead to a possible action.
    Pending requests: accountant check, finance, then the director only when the route snapshot
    requires one; nobody acts twice in one request and nobody who edited it or its files in this round
    (``past`` = services.round_participants). Payment: only when every required stage is done, never by
    anybody who took part in the request in this or an earlier round (``past`` = services.all_participants)."""
    acting = acting or {}
    if r.status == 'pending':
        stage = approval_stage(r)
        role = STAGE_ROLE[stage]
        fits = lambda roles: role in roles
        excluded = participants(r) | (past or set())
    elif r.status == 'approved':
        if not route_complete(r):
            return None, []
        # The payment channel decides the payer: bank - settlement accountant, cash - cashier.
        right = pay_right(account)
        stage = 'cash_payment' if account.kind == 'cash' else 'bank_payment'
        fits = lambda roles: any(right in PERMS.get(x, ()) for x in roles)
        excluded = participants(r) | (past or set())
    else:
        return None, []
    return stage, [u for u in users if u.id not in excluded and fits(roles_in(u, company, members, acting))]


def acting_roles(s):
    """Действующие замещения (ВрИО) на сегодня: (компания, пользователь) -> роли."""
    current = tashkent_today()
    acting = {}
    for d in s.scalars(select(Delegation).where(Delegation.revoked_at.is_(None), Delegation.starts_on <= current,
                                                Delegation.ends_on >= current)):
        if d.role in COMPANY_ROLES:
            acting.setdefault((d.company_id, d.user_id), set()).add(d.role)
    return acting


def pending_items(s, user_id=None):
    """Requests waiting for an action, one row per responsible person with a linked Telegram,
    ordered by request. With ``user_id`` only that person's rows, chosen by the same rules."""
    origin = os.getenv('PUBLIC_ORIGIN', '').rstrip('/')
    url = origin + '/#requests' if origin.startswith('https://') else ''
    people_query = select(User).where(User.active.is_(True))
    if user_id is not None:
        people_query = people_query.where(User.id == user_id)
    users = list(s.scalars(people_query.order_by(User.id)))
    if not users:
        return []
    companies = {c.id: c for c in s.scalars(select(Company).where(Company.active.is_(True)))}
    accounts = {a.id: a for a in s.scalars(select(Account))}
    categories = {c.id: c.name for c in s.scalars(select(Category))}
    links = {link.user_id: link.telegram_user_id for link in s.scalars(select(TelegramLink))}
    members = {}
    for m in s.scalars(select(CompanyUser)):
        members.setdefault(m.company_id, {})[m.user_id] = m.role
        if m.role in COMPANY_ROLES and m.company_id in companies and companies[m.company_id].code != SERVICE_CODE:
            members.setdefault('_valid', {}).setdefault(m.company_id, set()).add(m.user_id)
    acting = acting_roles(s)
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
        past = all_participants(s, r) if r.status == 'approved' else round_participants(s, r)
        stage, people = responsible(r, account, company, users, members, acting, past)
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
    return items


@router.get('/api/bot/v1/pending-requests')
def pending_requests(request: Request):
    authenticate_bot(request)
    with unit() as s:
        items = pending_items(s)
    audit(None, 'Бот: заявки для напоминаний', '', f'Строк: {len(items)}')
    return {'items': items}


# ---------------------------------------------------------------- Telegram user (private chat buttons)

def linked_user(s, telegram_user_id):
    """The active site user this Telegram account is linked to, or None."""
    link = s.scalar(select(TelegramLink).where(TelegramLink.telegram_user_id == telegram_user_id))
    user = s.get(User, link.user_id) if link else None
    return user if user is not None and user.active else None


@router.get('/api/bot/v1/telegram-users/{telegram_user_id}')
def telegram_user(request: Request, telegram_user_id: int = Path(gt=0, lt=2**53)):
    """«📋 Мои заявки» / «👤 Моя привязка»: who pressed the button and what waits for them."""
    authenticate_bot(request)
    with unit() as s:
        user = linked_user(s, telegram_user_id)
        if user is None:
            result = {'linked': False, 'user_id': None, 'name': None, 'is_admin': False, 'items': []}
        else:
            result = {'linked': True, 'user_id': user.id, 'name': user.name, 'is_admin': user.role == 'admin',
                      'items': pending_items(s, user.id)}
    if result['linked']:
        audit(None, 'Бот: заявки пользователя', result['user_id'], f'Строк: {len(result["items"])}',
              user_id=result['user_id'])
    else:
        audit(None, 'Бот: заявки пользователя', '', 'Telegram не привязан к активному пользователю')
    return result


@router.get('/api/bot/v1/access')
def access_check(request: Request, chat_id: int | None = Query(default=None, gt=-2**63, lt=0),
                 telegram_user_id: int | None = Query(default=None, gt=0, lt=2**53)):
    """Lightweight rights check before the bot re-sends stored parts of a message (a long summary,
    a list of requests). The bot compares ``companies`` with the ones the message was built for and
    drops the parts whose company is no longer allowed. No journal row: this call is frequent and
    changes nothing. Both parameters are optional but at least one is required."""
    authenticate_bot(request)
    if chat_id is None and telegram_user_id is None:
        raise HTTPException(422, 'Укажите chat_id группы или telegram_user_id.')
    result = {'checked_at': utc_iso(now()), 'group': None, 'user': None}
    with unit() as s:
        if chat_id is not None:
            state = group_state(s, chat_id, None)
            result['group'] = {'chat_id': chat_id, 'allowed': state['connected'], 'companies': state['companies']}
        if telegram_user_id is not None:
            user = linked_user(s, telegram_user_id)
            if user is None:
                result['user'] = {'linked': False, 'user_id': None, 'is_admin': False, 'companies': []}
            else:
                from . import access
                if user.role in ('admin', 'founder'):
                    codes = sorted(c.code for c in access.business_companies(s).values())
                else:
                    companies = access.business_companies(s)
                    codes = sorted(companies[i].code for i in access.staff_company_ids(s, user))
                result['user'] = {'linked': True, 'user_id': user.id, 'is_admin': user.role == 'admin', 'companies': codes}
    return result


# ---------------------------------------------------------------- summary groups connected from Telegram

class GroupIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    chat_id: int = Field(gt=-2**63, lt=2**63)
    title: str = Field(default='', max_length=255)
    companies: list[str] = Field(max_length=100)
    telegram_user_id: int = Field(gt=0, lt=2**53)


class GroupRemoveIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    chat_id: int = Field(gt=-2**63, lt=2**63)
    telegram_user_id: int = Field(gt=0, lt=2**53)


def require_group(chat_id):
    if chat_id >= 0:
        raise HTTPException(422, 'Сводку можно подключить только к группе Telegram: её ID отрицательный.')


def active_codes(s):
    """Upper-case code → active company."""
    return {c.code.upper(): c for c in s.scalars(select(Company).where(Company.active.is_(True)))}


def group_state(s, chat_id, user):
    """GET /report-groups/{chat_id} as seen by ``user``, the site user who pressed the button (or None)."""
    group = report_groups(s).get(chat_id)
    active = active_codes(s)
    can_manage = user is not None and user.role == 'admin' and (group is None or group['source'] != 'config')
    choices = []
    if can_manage:
        choices = [{'code': c.code, 'name': c.name} for c in sorted(active.values(), key=lambda c: (c.name, c.code))
                   if c.code.upper() != SERVICE_CODE]
    return {'chat_id': chat_id, 'connected': group is not None, 'source': group['source'] if group else None,
            'companies': sorted(active[c].code for c in group['companies'] if c in active) if group else [],
            'can_manage': can_manage, 'choices': choices}


REFUSALS = {403: 'Настраивать сводку может только администратор холдинга с привязанным Telegram.',
            409: 'Эта группа задана в настройках сайта (BOT_REPORT_GROUPS) и из Telegram не меняется.'}


def group_manager(s, chat_id, telegram_user_id):
    """(user, refusal): refusal is None when this person may change the group, else (status, reason)."""
    user = linked_user(s, telegram_user_id)
    if user is None:
        return None, (403, 'Telegram не привязан к активному пользователю')
    if user.role != 'admin':
        return user, (403, 'Не администратор холдинга')
    if chat_id in config_groups():
        return user, (409, 'Группа задана в BOT_REPORT_GROUPS')
    return user, None


def check_manager(chat_id, telegram_user_id):
    """Refusals are audited here, outside the write transaction (audit() opens its own)."""
    with unit() as s:
        user, refusal = group_manager(s, chat_id, telegram_user_id)
    if refusal:
        audit(None, 'Бот: отказ в настройке группы', chat_id, refusal[1], user_id=user.id if user else None)
        raise HTTPException(refusal[0], REFUSALS[refusal[0]])


def locked_manager(s, chat_id, telegram_user_id):
    """The same check again under the write lock, in case access changed a moment ago."""
    user, refusal = group_manager(s, chat_id, telegram_user_id)
    if refusal:
        raise HTTPException(refusal[0], REFUSALS[refusal[0]])
    return user


def chosen_companies(s, codes):
    """Validated selection → active companies in the requested order."""
    if not codes:
        raise HTTPException(422, 'Выберите хотя бы одну компанию.')
    active, known, chosen = active_codes(s), {c.code.upper() for c in s.scalars(select(Company))}, []
    for raw in codes:
        code = raw.strip().upper()
        label = mask_digits(raw, 40) or '(пусто)'
        if code == SERVICE_CODE:
            raise HTTPException(422, 'Служебное пространство «Не распределено» нельзя подключить к сводке.')
        if code not in known:
            raise HTTPException(422, f'Компания «{label}» не найдена.')
        if code not in active:
            raise HTTPException(422, f'Компания «{label}» отключена.')
        if active[code] in chosen:
            raise HTTPException(422, f'Компания «{label}» указана дважды.')
        chosen.append(active[code])
    return chosen


def group_detail(title, chat_id, codes, before=None):
    """Audit text: the group and its companies, never its members."""
    text = (f'Группа «{title}» ({chat_id})' if title else f'Группа {chat_id}') + '; компании: ' + ', '.join(sorted(codes))
    if before is not None:
        text += '; было: ' + (', '.join(sorted(before)) or '—')
    return text


def company_ids(s, codes):
    """Ids of these companies, inactive ones included, so each company journal shows the change."""
    wanted = {c.upper() for c in codes}
    return sorted(c.id for c in s.scalars(select(Company)) if c.code.upper() in wanted)


def journal_group(s, codes, action, chat_id, detail, user_id=None):
    """One audit row per company of the group, inside the caller's transaction.

    A plain INSERT on purpose: the change concerns every company of the group, while a browser
    session (e.g. editing a user) is scoped to the selected company and company_scope refuses
    ORM rows of the other companies."""
    s.execute(insert(Audit.__table__), [
        {'company_id': company_id, 'user_id': user_id, 'action': action, 'entity': 'telegram_group',
         'entity_id': str(chat_id), 'detail': detail}
        for company_id in company_ids(s, codes) or [None]])


def drop_group(s, row, action, user_id=None, reason=''):
    """Delete a stored group and record it in the journal of each of its companies."""
    chat_id, codes = row.chat_id, stored_codes(row)
    detail = group_detail(row.title, chat_id, codes) + (f'; {reason}' if reason else '')
    s.delete(row)
    journal_group(s, codes, action, chat_id, detail, user_id)


def drop_groups_of(s, user, actor):
    """A group connected from Telegram lives only while the holding administrator who connected
    it could still manage it: called whenever a user's role or active flag changes (main.edit_user)."""
    if user.active and user.role == 'admin':
        return 0
    reason = 'отключён' if not user.active else 'больше не администратор холдинга'
    rows = list(s.scalars(select(TelegramGroup).where(TelegramGroup.added_by == user.id)
                          .order_by(TelegramGroup.chat_id)))
    for row in rows:
        drop_group(s, row, 'Telegram-группа отключена', actor.id if actor else None,
                   f'автоматически: подключивший её {user.name} {reason}')
    return len(rows)


def drop_shadowed_groups():
    """Startup: a stored group whose chat id is now fixed in BOT_REPORT_GROUPS is removed, so that
    deleting the entry later cannot bring back the old, possibly wider company list. Idempotent."""
    configured = list(config_groups())
    if not configured:
        return 0
    shadowed = select(TelegramGroup).where(TelegramGroup.chat_id.in_(configured)).order_by(TelegramGroup.chat_id)
    with unit() as s:
        if s.scalar(shadowed.limit(1)) is None:
            return 0
    with unit(True) as s:
        rows = list(s.scalars(shadowed))
        for row in rows:
            drop_group(s, row, 'Telegram-группа заменена настройкой сервера', None,
                       'группа задана в BOT_REPORT_GROUPS')
    if rows:
        logging.warning('BOT_REPORT_GROUPS: removed %d Telegram group(s) connected from Telegram under the same id', len(rows))
    return len(rows)


def drop_orphan_groups():
    """Startup: remove stored groups whose connecting administrator is gone, disabled or no longer a
    holding administrator (report_groups already ignores them). Idempotent."""
    owners = select(User.id).where(User.active.is_(True), User.role == 'admin')
    orphans = (select(TelegramGroup).where(or_(TelegramGroup.added_by.is_(None), TelegramGroup.added_by.not_in(owners)))
               .order_by(TelegramGroup.chat_id))
    with unit() as s:
        if s.scalar(orphans.limit(1)) is None:
            return 0
    with unit(True) as s:
        rows = list(s.scalars(orphans))
        for row in rows:
            drop_group(s, row, 'Telegram-группа отключена', None,
                       'автоматически: у группы нет действующего администратора холдинга, который её подключил')
    if rows:
        logging.warning('Telegram groups: removed %d group(s) without an active connecting administrator', len(rows))
    return len(rows)


@router.get('/api/bot/v1/report-groups')
def list_report_groups(request: Request):
    """Every connected group with its active companies: the bot sends the morning summary by this list."""
    authenticate_bot(request)
    items = []
    with unit() as s:
        for chat_id, group in sorted(report_groups(s).items()):
            codes, scope_id = group_scope(s, chat_id, group)
            if codes:
                items.append({'chat_id': chat_id, 'title': group['title'], 'companies': codes,
                              'source': group['source'], 'scope_id': scope_id})
    audit(None, 'Бот: группы сводки', '', f'Групп: {len(items)}')
    return {'items': items}


@router.get('/api/bot/v1/report-groups/{chat_id}/scope')
def report_group_scope(request: Request, chat_id: int):
    """What the group may receive right now: the bot asks before every part of a stored summary it
    sends again (after a refusal, a lost answer or a restart) and stops when the answer differs from
    the scope stored with the snapshot. A group that is no longer allowed — removed, its companies
    switched off, the administrator who connected it disabled or demoted — answers 200 with
    allowed=false, so the bot can tell "not allowed any more" (stop, record) from "could not check"
    (a 5xx or no connection: postpone the financial message)."""
    authenticate_bot(request)
    require_group(chat_id)
    with unit() as s:
        group = report_groups(s).get(chat_id)
        codes, scope_id = group_scope(s, chat_id, group) if group else ([], None)
    if not codes:
        audit(None, 'Бот: группа не разрешена', chat_id, 'Проверка области доступа перед отправкой сохранённой сводки')
        return {'chat_id': chat_id, 'allowed': False, 'companies': [], 'scope_id': None, 'source': None}
    return {'chat_id': chat_id, 'allowed': True, 'companies': codes, 'scope_id': scope_id, 'source': group['source']}


@router.get('/api/bot/v1/report-groups/{chat_id}')
def get_report_group(request: Request, chat_id: int, telegram_user_id: int = Query(gt=0, lt=2**53)):
    authenticate_bot(request)
    require_group(chat_id)
    with unit() as s:
        user = linked_user(s, telegram_user_id)
        result = group_state(s, chat_id, user)
    audit(None, 'Бот: настройки группы', chat_id, 'Подключена' if result['connected'] else 'Не подключена',
          user_id=user.id if user else None)
    return result


@router.post('/api/bot/v1/report-groups')
def save_report_group(data: GroupIn, request: Request):
    """Connect a group to the morning summary or change its companies (holding administrator only)."""
    authenticate_bot(request)
    require_group(data.chat_id)
    check_manager(data.chat_id, data.telegram_user_id)
    title = ' '.join(data.title.split())
    with unit(True) as s:
        user = locked_manager(s, data.chat_id, data.telegram_user_id)
        codes = [c.code.upper() for c in chosen_companies(s, data.companies)]
        row = s.get(TelegramGroup, data.chat_id)
        if row is None:
            before, owner = None, user.id
            row = TelegramGroup(chat_id=data.chat_id, title=title, companies=','.join(codes), added_by=user.id, added_at=now())
            s.add(row)
            action, affected = 'Telegram-группа подключена', set(codes)
        else:
            before, owner = stored_codes(row), row.added_by
            row.title = title or row.title
            row.companies = ','.join(codes)
            # The group now depends on this administrator: another admin can take a group over.
            row.added_by = user.id
            action, affected = 'Telegram-группа изменена', before | set(codes)
        detail = group_detail(row.title, data.chat_id, codes, before)
        if owner != user.id:
            detail += f'; теперь группа закреплена за {user.name}'
        journal_group(s, affected, action, data.chat_id, detail, user.id)
        s.flush()
        return group_state(s, data.chat_id, user)


@router.post('/api/bot/v1/report-groups/remove')
def remove_report_group(data: GroupRemoveIn, request: Request):
    """Stop the morning summary for a group connected from Telegram."""
    authenticate_bot(request)
    require_group(data.chat_id)
    check_manager(data.chat_id, data.telegram_user_id)
    with unit(True) as s:
        user = locked_manager(s, data.chat_id, data.telegram_user_id)
        row = s.get(TelegramGroup, data.chat_id)
        if row is None:
            s.add(Audit(user_id=user.id, action='Бот: отключение группы', entity='telegram_group',
                        entity_id=str(data.chat_id), detail='Группа не была подключена'))
            return {'removed': False}
        drop_group(s, row, 'Telegram-группа отключена', user.id)
        return {'removed': True}


class MigrateIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    from_chat_id: int = Field(gt=-2**63, lt=0)
    to_chat_id: int = Field(gt=-2**63, lt=0)


@router.post('/api/bot/v1/report-groups/migrate')
def migrate_report_group(data: MigrateIn, request: Request):
    """Telegram turned a basic group into a supergroup and gave it a new id: the connection moves
    with its companies, title, author and date."""
    authenticate_bot(request)
    old_id, new_id = data.from_chat_id, data.to_chat_id
    if old_id == new_id:
        raise HTTPException(422, 'Старый и новый ID группы совпадают.')
    pair = f'Группа {old_id} → {new_id}'
    if old_id in config_groups() or new_id in config_groups():
        audit(None, 'Бот: перенос группы отклонён', new_id, pair + ': задана в BOT_REPORT_GROUPS')
        raise HTTPException(409, 'Группа задана в настройках сервера (BOT_REPORT_GROUPS) и из Telegram не меняется.')
    with unit(True) as s:
        old = s.get(TelegramGroup, old_id)
        taken = old is not None and s.get(TelegramGroup, new_id) is not None
        if old is not None and not taken:
            codes, title = stored_codes(old), old.title
            moved = TelegramGroup(chat_id=new_id, title=title, companies=old.companies,
                                  added_by=old.added_by, added_at=old.added_at)
            s.delete(old)
            s.flush()
            s.add(moved)
            journal_group(s, codes, 'Telegram-группа перенесена', new_id,
                          group_detail(title, new_id, codes) + f'; стала супергруппой, прежний ID {old_id}')
    if old is None:
        audit(None, 'Бот: перенос группы', new_id, pair + ': старая группа не была подключена кнопкой')
        return {'migrated': False}
    if taken:
        audit(None, 'Бот: перенос группы отклонён', new_id, pair + ': новая группа уже подключена')
        raise HTTPException(409, 'Новая группа уже подключена к сводке.')
    return {'migrated': True}


# ---------------------------------------------------------------- summary groups on the site («Пользователи»)

def require_holding_admin(user):
    if user.role != 'admin':
        raise HTTPException(403, 'Telegram-группы сводки видит и отключает только администратор холдинга.')


@router.get('/api/telegram-groups')
def site_report_groups(request: Request):
    """Every group that receives the morning summary, for the holding administrators on the site.
    The list is holding-wide whatever company is selected on the page."""
    with unit() as s:
        user, _ = session_user(s, request)
        require_holding_admin(user)
        companies = {c.code.upper(): c for c in s.scalars(select(Company))}
        stored = {g.chat_id: g for g in s.scalars(select(TelegramGroup))}
        names = {u.id: u.name for u in s.scalars(select(User).execution_options(company_unscoped=True))}
        items = []
        for chat_id, group in report_groups(s).items():
            row = stored.get(chat_id) if group['source'] == 'site' else None
            listed = [{'code': companies[c].code, 'name': companies[c].name, 'active': companies[c].active}
                      if c in companies else {'code': c, 'name': c, 'active': False} for c in group['companies']]
            items.append({'chat_id': chat_id, 'title': group['title'], 'source': group['source'],
                          'companies': sorted(listed, key=lambda c: (c['name'].casefold(), c['code'])),
                          'added_by': names.get(row.added_by) if row else None,
                          'added_at': utc_iso(row.added_at) if row else None,
                          'can_remove': row is not None})
    items.sort(key=lambda g: (not g['title'], g['title'].casefold(), g['chat_id']))  # untitled last
    return {'items': items}


@router.delete('/api/telegram-groups/{chat_id}')
def site_remove_report_group(request: Request, chat_id: int = Path(gt=-2**63, lt=0)):
    """Disconnect a group connected from Telegram, e.g. one whose chat no longer exists."""
    with unit(True) as s:
        user, _ = session_user(s, request)
        require_holding_admin(user)
        if chat_id in config_groups():
            raise HTTPException(409, 'Эта группа задана в настройках сервера (BOT_REPORT_GROUPS); '
                                     'отключить её может только технический администратор.')
        row = s.get(TelegramGroup, chat_id)
        if row is None:
            return {'removed': False}
        drop_group(s, row, 'Telegram-группа отключена', user.id, 'отключена на сайте')
        return {'removed': True}


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
        if row.expires_at <= now():
            return {'revoked': False}  # nothing active to cancel: the stale row is only cleaned up
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
