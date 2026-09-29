"""Карточка заявки, политика директора по статьям, календарь рабочих дней и ВрИО.
Вложения заявки — в erp.py (таблица request_documents)."""
import json
from datetime import date
from typing import Literal, Optional
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, func
from .db import (unit, now, User, Account, Category, PaymentRequest, RequestDocument, CalendarDay, Delegation, Audit)
from .security import (session_user, perms_of, has_role, payment_channels, pay_right, ROLES, COMPANY_ROLES)
from .services import (get, log, money, amount, today, approval_stage, participants, all_participants, route_complete,
                       DOCUMENT_KINDS, REQUIRED_DOCUMENTS, THRESHOLD_FIELDS, category_policy, request_json, request_journal)
from .workdays import due_minimums, LEAD_DAYS
from .workflow_setup import DEFAULT_THRESHOLDS

router = APIRouter()
MAX_DOCUMENT = 5 * 1024 * 1024
OPEN_STATUSES = ('draft', 'pending', 'returned', 'approved')


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


# ---------- Видимость и доступные действия ----------

def can_view_request(s, u, r):
    """Та же граница, что у списка заявок: реестр целиком, свои заявки, заявки к оплате своего канала
    и заявки на проверке у расчётного бухгалтера."""
    perms = perms_of(u)
    if 'view' in perms:
        return True
    if 'request' in perms and r.creator_id == u.id:
        return True
    account = s.get(Account, r.account_id)
    if r.status in ('approved', 'paid') and account and account.kind in payment_channels(perms):
        return True
    if 'request_check' in perms and (r.status == 'pending' or r.checked_by == u.id):
        return True
    return False


def visible(s, u, id):
    r = get(s, PaymentRequest, id)
    if not can_view_request(s, u, r):
        raise HTTPException(404, 'Запись не найдена.')
    return r


def can_edit(u, r):
    perms = perms_of(u)
    return 'request' in perms and (r.creator_id == u.id or 'request_edit' in perms)


STAGE_ROLE = {'check': 'accountant', 'finance': 'finance', 'director': 'director'}


def request_actions(s, u, r):
    """Действия, которые сервер примет от этого пользователя. Интерфейс только показывает их."""
    perms = perms_of(u)
    account = s.get(Account, r.account_id)
    stage = approval_stage(r)
    # Без прав согласующего, проверяющего или финансового руководителя сотрудник действует только в своих заявках.
    own_only = not ({'approve', 'request_edit', 'request_check'} & perms)
    actions = []
    editable = can_edit(u, r) and (not own_only or r.creator_id == u.id)
    if editable and r.status in ('draft', 'pending', 'returned'):
        actions.append('edit')
    if editable and r.status in ('draft', 'returned'):
        actions.append('submit')
    if editable and r.status in OPEN_STATUSES:
        actions.append('documents')
    if stage and has_role(u, STAGE_ROLE[stage]) and u.id not in participants(r) and ('approve' in perms or stage == 'check'):
        actions.append('check' if stage == 'check' else 'approve')
    if (('approve' in perms and r.status in ('pending', 'approved')) or (stage == 'check' and has_role(u, 'accountant'))) and not own_only:
        actions.append('return')
    if r.status == 'approved' and pay_right(account) in perms:
        actions.append('return_finance')
    if r.status in OPEN_STATUSES and ((r.creator_id == u.id and 'request' in perms) or ('approve' in perms and not own_only)):
        actions.append('close')
    if 'request_edit' in perms and r.status in ('pending', 'approved'):
        actions.append('reschedule')
    if r.status == 'approved' and pay_right(account) in perms and route_complete(r) and u.id not in all_participants(s, r):
        actions.append('pay')
    return actions


def request_view(s, u, r):
    data = request_json(s, r)
    # Без права просмотра реестра (заявитель, закупки, кассир, плательщик) прежний блок бюджета скрыт;
    # по выбранной статье и периоду остаётся карточка budget_card (лимит, использовано, резерв, доступно, после заявки).
    if 'view' not in perms_of(u):
        data['budget'] = {'limit': None, 'hidden': True, 'over': data['budget']['over'], 'mode': data['budget']['mode']}
    data['actions'] = request_actions(s, u, r)
    return data


# ---------- Карточка заявки ----------

def document_json(d, names):
    return {'id': d.id, 'kind': d.kind, 'kind_label': DOCUMENT_KINDS[d.kind], 'version': d.version, 'current': bool(d.active),
            'filename': d.filename, 'mime': d.mime, 'sha256': d.sha256, 'created_at': str(d.created_at),
            'created_by': names.get(d.created_by, ''), 'url': f'/api/request-documents/{d.id}'}


def names_of(s, ids):
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u.name for u in s.scalars(select(User).where(User.id.in_(ids)).execution_options(company_unscoped=True))}


@router.get('/api/requests/{id}')
def request_card(id: int, request: Request):
    with unit() as s:
        u, _ = session_user(s, request)
        r = visible(s, u, id)
        data = request_view(s, u, r)
        docs = list(s.scalars(select(RequestDocument).where(RequestDocument.request_id == id)
                              .order_by(RequestDocument.kind, RequestDocument.version, RequestDocument.id)))
        rows = list(s.scalars(select(Audit).where(request_journal(id)).order_by(Audit.id)))
        names = names_of(s, [r.creator_id, r.last_editor_id, r.checked_by, r.finance_approved_by, r.approved_by]
                         + [d.created_by for d in docs] + [a.user_id for a in rows] + [a.acting_for_id for a in rows])
        show_ip = 'audit' in perms_of(u)
        data['people'] = {k: names.get(getattr(r, k), '') for k in ('creator_id', 'last_editor_id', 'checked_by', 'finance_approved_by', 'approved_by')}
        data['documents_list'] = [document_json(d, names) for d in docs]
        data['history'] = [{'id': a.id, 'at': str(a.created_at), 'user': names.get(a.user_id, 'Система'),
                            'role': ROLES.get(a.role, a.role or ''), 'acting_for': names.get(a.acting_for_id) if a.acting_for_id else None,
                            'action': a.action, 'detail': a.detail, **({'ip': a.ip} if show_ip else {})} for a in rows]
        return data


@router.get('/api/request-rules')
def request_rules(request: Request):
    with unit() as s:
        session_user(s, request)
        return {'due_minimums': due_minimums(s, today()), 'lead_days': LEAD_DAYS, 'document_kinds': DOCUMENT_KINDS,
                'required_documents': list(REQUIRED_DOCUMENTS), 'max_document_mb': MAX_DOCUMENT // (1024 * 1024),
                'document_types': ['PDF', 'PNG', 'JPEG', 'DOCX', 'XLSX'], 'open_statuses': list(OPEN_STATUSES)}


# ---------- Политика директора по статьям ----------

def policy_json(c, names):
    effective = {cur: category_policy(c, cur) for cur in ('UZS', 'USD', 'EUR')}
    return {'id': c.id, 'name': c.name, 'policy': c.director_policy or 'always', 'skip_allowed': c.skip_allowed,
            'thresholds': {cur: money(getattr(c, field)) if getattr(c, field) is not None else None for cur, field in THRESHOLD_FIELDS.items()},
            'effective': {cur: {'policy': p, 'threshold': money(t) if t is not None else None} for cur, (p, t) in effective.items()},
            'changed_at': str(c.director_policy_changed_at) if c.director_policy_changed_at else None,
            'changed_by': names.get(c.director_policy_changed_by, '') if c.director_policy_changed_by else ''}


@router.get('/api/approval-policy')
def approval_policy(request: Request):
    with unit() as s:
        u, _ = session_user(s, request, 'approval_policy')
        cats = list(s.scalars(select(Category).where(Category.type == 'outcome').order_by(Category.id)))
        names = names_of(s, [c.director_policy_changed_by for c in cats])
        return {'categories': [policy_json(c, names) for c in cats],
                'defaults': {cur: money(v) if v is not None else None for cur, v in DEFAULT_THRESHOLDS.items()},
                'can_edit_skip_list': 'users' in perms_of(u),
                'rule': 'Директор участвует по политике статьи на момент отправки заявки. Нет политики или порога валюты — директор утверждает любую сумму.'}


@router.post('/api/approval-policy')
def set_approval_policy(request: Request):
    with unit(True) as s:
        session_user(s, request, 'approval_policy')
        raise HTTPException(409, 'Общий порог больше не используется: политика директора задаётся по каждой статье.')


class PolicyIn(Input):
    policy: Literal['always', 'threshold', 'skip']
    threshold_uzs: Optional[str] = None
    threshold_usd: Optional[str] = None
    threshold_eur: Optional[str] = None
    reason: str = Field(min_length=10, max_length=1000)


@router.put('/api/categories/{id}/director-policy')
def set_director_policy(id: int, data: PolicyIn, request: Request):
    with unit(True) as s:
        u, _ = session_user(s, request, 'approval_policy')
        c = get(s, Category, id)
        if c.type != 'outcome':
            raise HTTPException(422, 'Политика директора задаётся только для расходных статей.')
        if data.policy == 'skip' and not c.skip_allowed:
            raise HTTPException(409, 'Без директора можно проводить только статьи из утверждённого перечня регулярных платежей.')
        values = {f: amount(getattr(data, f)) if getattr(data, f) not in (None, '') else None
                  for f in ('threshold_uzs', 'threshold_usd', 'threshold_eur')}
        if data.policy == 'threshold' and not any(v is not None for v in values.values()):
            raise HTTPException(422, 'Для политики «выше порога» задайте порог хотя бы для одной валюты.')
        names = names_of(s, [c.director_policy_changed_by])
        before = policy_json(c, names)
        c.director_policy = data.policy
        for field, value in values.items():
            setattr(c, 'director_' + field, value if data.policy == 'threshold' else None)
        c.director_policy_changed_at = now()
        c.director_policy_changed_by = u.id
        s.flush()
        after = policy_json(c, names_of(s, [u.id]))
        log(s, u, 'Изменена политика директора по статье', 'category', c.id,
            json.dumps({'before': before, 'after': after, 'reason': data.reason}, ensure_ascii=False))
        return after


class SkipListIn(Input):
    allowed: bool
    reason: str = Field(min_length=10, max_length=1000)


@router.put('/api/categories/{id}/skip-allowed')
def set_skip_allowed(id: int, data: SkipListIn, request: Request):
    """Перечень регулярных статей без директора утверждает администратор холдинга."""
    with unit(True) as s:
        u, _ = session_user(s, request, 'users')
        c = get(s, Category, id)
        if c.type != 'outcome':
            raise HTTPException(422, 'Перечень составляется только из расходных статей.')
        if not data.allowed and c.director_policy == 'skip':
            raise HTTPException(409, 'Сначала смените политику статьи: сейчас директор по ней не участвует.')
        before = c.skip_allowed
        c.skip_allowed = data.allowed
        log(s, u, 'Изменён перечень статей без директора', 'category', c.id,
            json.dumps({'before': before, 'after': data.allowed, 'reason': data.reason}, ensure_ascii=False))
        return {'id': c.id, 'skip_allowed': c.skip_allowed}


# ---------- Календарь рабочих дней ----------

class CalendarIn(Input):
    day: date
    kind: Literal['holiday', 'workday']
    name: str = Field(min_length=2, max_length=160)


@router.get('/api/calendar-days')
def calendar_days(request: Request, year: int = Query(ge=2000, le=2100)):
    with unit() as s:
        session_user(s, request)
        rows = s.scalars(select(CalendarDay).where(CalendarDay.day >= date(year, 1, 1), CalendarDay.day <= date(year, 12, 31)).order_by(CalendarDay.day))
        return [{'day': str(d.day), 'kind': d.kind, 'name': d.name} for d in rows]


@router.post('/api/calendar-days')
def save_calendar_day(data: CalendarIn, request: Request):
    with unit(True) as s:
        u, _ = session_user(s, request, 'users')
        row = s.get(CalendarDay, data.day)
        before = {'kind': row.kind, 'name': row.name} if row else None
        if not row:
            row = CalendarDay(day=data.day, kind=data.kind, name=data.name, created_by=u.id)
            s.add(row)
        row.kind = data.kind
        row.name = data.name
        log(s, u, 'Изменён календарь рабочих дней', 'calendar', str(data.day),
            json.dumps({'before': before, 'after': {'kind': data.kind, 'name': data.name}}, ensure_ascii=False))
        return {'day': str(data.day), 'kind': data.kind, 'name': data.name}


@router.delete('/api/calendar-days/{day}')
def delete_calendar_day(day: date, request: Request):
    with unit(True) as s:
        u, _ = session_user(s, request, 'users')
        row = s.get(CalendarDay, day)
        if not row:
            raise HTTPException(404, 'Запись не найдена.')
        log(s, u, 'Изменён календарь рабочих дней', 'calendar', str(day), json.dumps({'before': {'kind': row.kind, 'name': row.name}, 'after': None}, ensure_ascii=False))
        s.delete(row)
        return {'ok': True}


# ---------- Временное замещение (ВрИО) ----------

class DelegationIn(Input):
    user_id: int
    replaced_user_id: int
    role: str = Field(min_length=2, max_length=20)
    starts_on: date
    ends_on: date
    reason: str = Field(min_length=10, max_length=1000)


def delegation_json(d, names):
    day = today()
    state = 'revoked' if d.revoked_at else 'planned' if d.starts_on > day else 'expired' if d.ends_on < day else 'active'
    return {'id': d.id, 'user_id': d.user_id, 'user': names.get(d.user_id, ''), 'replaced_user_id': d.replaced_user_id,
            'replaced': names.get(d.replaced_user_id, ''), 'role': d.role, 'role_label': ROLES[d.role],
            'starts_on': str(d.starts_on), 'ends_on': str(d.ends_on), 'reason': d.reason, 'state': state,
            'created_by': names.get(d.created_by, ''), 'created_at': str(d.created_at),
            'revoked_at': str(d.revoked_at) if d.revoked_at else None}


def member_role(s, cid, user):
    """Роль в компании, которую сотрудник может передать на время отсутствия: только явное назначение
    в этой компании (у сотрудника — единственное действующее)."""
    from . import access
    if not user.active or user.role == 'founder':
        return None
    valid = access.valid_assignments(s, user)
    if user.role != 'admin' and len(valid) != 1:
        return None
    return next((m.role for m in valid if m.company_id == cid), None)


def can_act_in(s, cid, user):
    """ВрИО работает в своей компании: сотрудник с действующим назначением именно в ней или
    администратор холдинга. Учредитель только читает и не замещает."""
    from . import access
    if not user.active or user.role == 'founder':
        return False
    if user.role == 'admin':
        return True
    return access.staff_company_ids(s, user) == [cid]


@router.get('/api/delegations')
def delegations(request: Request):
    with unit() as s:
        session_user(s, request, 'users')
        rows = list(s.scalars(select(Delegation).order_by(Delegation.starts_on.desc(), Delegation.id.desc())))
        names = names_of(s, [x for d in rows for x in (d.user_id, d.replaced_user_id, d.created_by)])
        return [delegation_json(d, names) for d in rows]


@router.post('/api/delegations')
def add_delegation(data: DelegationIn, request: Request):
    with unit(True) as s:
        admin, _ = session_user(s, request, 'users')
        cid = s.info['company_id']
        acting = s.scalar(select(User).where(User.id == data.user_id).execution_options(company_unscoped=True))
        replaced = s.scalar(select(User).where(User.id == data.replaced_user_id).execution_options(company_unscoped=True))
        if not acting or not replaced:
            raise HTTPException(404, 'Сотрудник не найден.')
        if acting.id == admin.id:
            raise HTTPException(409, 'Себя временно исполняющим не назначают: это делает другой администратор.')
        if acting.id == replaced.id:
            raise HTTPException(422, 'ВрИО и заменяемый сотрудник должны быть разными людьми.')
        if not acting.active:
            raise HTTPException(409, 'Учётная запись ВрИО отключена.')
        if data.role not in COMPANY_ROLES:
            raise HTTPException(422, 'Выберите роль в компании.')
        if acting.role == 'founder':
            raise HTTPException(409, 'Учредитель только просматривает данные и не замещает сотрудников.')
        if not can_act_in(s, cid, acting):
            raise HTTPException(409, 'ВрИО назначается из сотрудников этой компании: сотрудник другой компании '
                                     'сюда не допускается. Сначала переведите его в центре администрирования.')
        if member_role(s, cid, replaced) != data.role:
            raise HTTPException(409, f'У заменяемого сотрудника в этой компании нет роли «{ROLES[data.role]}».')
        if data.ends_on < data.starts_on:
            raise HTTPException(422, 'Окончание замещения раньше начала.')
        if data.ends_on < today():
            raise HTTPException(422, 'Срок замещения уже закончился.')
        overlap = s.scalar(select(func.count()).select_from(Delegation).where(
            Delegation.user_id == acting.id, Delegation.role == data.role, Delegation.revoked_at.is_(None),
            Delegation.starts_on <= data.ends_on, Delegation.ends_on >= data.starts_on))
        if overlap:
            raise HTTPException(409, 'У этого сотрудника уже есть замещение той же роли в эти даты.')
        d = Delegation(user_id=acting.id, replaced_user_id=replaced.id, role=data.role, starts_on=data.starts_on,
                       ends_on=data.ends_on, reason=data.reason, created_by=admin.id)
        s.add(d)
        s.flush()
        log(s, admin, 'Назначен ВрИО', 'delegation', d.id, json.dumps({'user': acting.name, 'replaced': replaced.name,
            'role': data.role, 'starts_on': str(data.starts_on), 'ends_on': str(data.ends_on), 'reason': data.reason}, ensure_ascii=False))
        return delegation_json(d, {acting.id: acting.name, replaced.id: replaced.name, admin.id: admin.name})


class RevokeIn(Input):
    reason: str = Field(min_length=10, max_length=1000)


@router.post('/api/delegations/{id}/revoke')
def revoke_delegation(id: int, data: RevokeIn, request: Request):
    with unit(True) as s:
        admin, _ = session_user(s, request, 'users')
        d = get(s, Delegation, id)
        if d.revoked_at:
            raise HTTPException(409, 'Замещение уже отменено.')
        d.revoked_at = now()
        d.revoked_by = admin.id
        log(s, admin, 'Отменено ВрИО', 'delegation', d.id, data.reason)
        return {'ok': True}
