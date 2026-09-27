"""Карточка заявки, вложения, политика директора по статьям, календарь и ВрИО."""
import hashlib, io, json, secrets, zipfile
from datetime import date
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import unquote, quote
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, func
from sqlalchemy.orm.attributes import flag_modified
from .db import (unit, now, User, Account, Category, PaymentRequest, RequestDocument, CalendarDay, Delegation,
                 CompanyUser, Audit)
from .security import (session_user, perms_of, company_role, has_role, act_with_right, payment_channels, pay_right, ROLES,
                       COMPANY_ROLES, HOLDING_ROLES)
from .services import (get, log, money, amount, today, clear_approval, check_request_version, approval_stage,
                       participants, all_participants, route_complete, requires_director, DOCUMENT_KINDS, REQUIRED_DOCUMENTS, THRESHOLD_FIELDS,
                       category_policy, request_json, budget_card)
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
    # Без просмотра реестра и прав согласующего сотрудник действует только в своих заявках.
    own_only = not ({'view', 'approve', 'request_edit', 'request_check'} & perms)
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
    data['actions'] = request_actions(s, u, r)
    return data


# ---------- Карточка заявки ----------

def document_json(d, names):
    return {'id': d.id, 'kind': d.kind, 'kind_label': DOCUMENT_KINDS[d.kind], 'lineage_id': d.lineage_id, 'version': d.version,
            'current': d.is_current and not d.removed, 'removed': d.removed, 'filename': d.filename, 'mime': d.mime,
            'size': d.size, 'sha256': d.sha256, 'created_at': str(d.created_at), 'created_by': names.get(d.created_by, ''),
            'superseded_at': str(d.superseded_at) if d.superseded_at else None, 'url': f'/api/request-documents/{d.id}'}


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
        docs = list(s.scalars(select(RequestDocument).where(RequestDocument.request_id == id).order_by(RequestDocument.id)))
        rows = list(s.scalars(select(Audit).where(Audit.entity == 'request', Audit.entity_id == str(id)).order_by(Audit.id)))
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
                'document_types': ['PDF', 'PNG', 'JPEG', 'DOCX', 'XLSX']}


# ---------- Вложения ----------

ALLOWED = {'.pdf': ('application/pdf', b'%PDF-'), '.png': ('image/png', b'\x89PNG\r\n\x1a\n'),
           '.jpg': ('image/jpeg', b'\xff\xd8\xff'), '.jpeg': ('image/jpeg', b'\xff\xd8\xff'),
           '.docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', b'PK\x03\x04'),
           '.xlsx': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', b'PK\x03\x04')}
TYPE_ERROR = 'Документ заявки: PDF, PNG, JPEG, DOCX или XLSX, не больше 5 МБ.'


def check_file(raw, name):
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED or not raw.startswith(ALLOWED[suffix][1]):
        raise HTTPException(422, TYPE_ERROR)
    if suffix in ('.docx', '.xlsx'):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names = archive.namelist()
                if sum(i.file_size for i in archive.infolist()) > 50 * 1024 * 1024:
                    raise HTTPException(422, TYPE_ERROR)
        except zipfile.BadZipFile:
            raise HTTPException(422, TYPE_ERROR)
        folder = 'word/' if suffix == '.docx' else 'xl/'
        if '[Content_Types].xml' not in names or not any(n.startswith(folder) for n in names):
            raise HTTPException(422, TYPE_ERROR)
    return ALLOWED[suffix][0]


async def read_body(request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_DOCUMENT:
            raise HTTPException(413, 'Файл больше 5 МБ.')
    if not raw:
        raise HTTPException(422, 'Пустой файл.')
    return bytes(raw)


def document_target(s, u, id, version):
    """Заявка, к которой можно приложить или заменить документ: автор или финансовый руководитель."""
    r = visible(s, u, id)
    if not can_edit(u, r):
        raise HTTPException(403, 'Документы заявки прикладывает автор или финансовый руководитель.')
    act_with_right(u, 'request' if r.creator_id == u.id else 'request_edit')
    if r.status == 'rejected':
        raise HTTPException(409, 'Отклонённая заявка хранится только для чтения.')
    if r.status not in OPEN_STATUSES:
        raise HTTPException(409, 'Документы оплаченной или закрытой заявки не меняются.')
    check_request_version(r, version)
    return r


def reset_after_document_change(s, u, r):
    """Изменение документов после отправки снимает проверку и согласования; версия заявки растёт."""
    reset = r.status in ('pending', 'approved')
    if reset:
        clear_approval(r)
        r.status = 'pending'
    r.last_editor_id = u.id
    flag_modified(r, 'decision_note')
    return reset


def version_header(request):
    raw = request.headers.get('X-Request-Version') or request.query_params.get('version')
    try:
        return int(raw) if raw else None
    except ValueError:
        raise HTTPException(422, 'Некорректная версия заявки.')


@router.post('/api/requests/{id}/document')
async def add_request_document(id: int, request: Request, kind: Literal['indent', 'contract', 'other'] = Query(...),
                               replaces: Optional[int] = None):
    with unit() as s:
        u, _ = session_user(s, request)
        document_target(s, u, id, version_header(request))
    raw = await read_body(request)
    name = Path(unquote(request.headers.get('X-Filename', 'document'))).name[:220] or 'document'
    mime = check_file(raw, name)
    with unit(True) as s:
        u, _ = session_user(s, request)
        r = document_target(s, u, id, version_header(request))
        previous = None
        if replaces is not None:
            previous = get(s, RequestDocument, replaces)
            if previous.request_id != r.id or previous.kind != kind or not previous.is_current or previous.removed:
                raise HTTPException(409, 'Заменить можно только текущую версию документа этой заявки.')
        elif kind in REQUIRED_DOCUMENTS:
            previous = s.scalar(select(RequestDocument).where(RequestDocument.request_id == r.id, RequestDocument.kind == kind,
                                                              RequestDocument.is_current.is_(True), RequestDocument.removed.is_(False)))
        if previous:
            previous.is_current = False
            previous.superseded_at = now()
            previous.superseded_by = u.id
        reset = reset_after_document_change(s, u, r)
        s.flush()
        d = RequestDocument(request_id=r.id, kind=kind, version=previous.version + 1 if previous else 1,
                            lineage_id=previous.lineage_id if previous else None, filename=name, mime=mime, size=len(raw),
                            sha256=hashlib.sha256(raw).hexdigest(), content=raw, request_version=r.version, created_by=u.id)
        s.add(d)
        s.flush()
        if d.lineage_id is None:
            d.lineage_id = d.id
        log(s, u, 'Новая версия документа заявки' if previous else 'Добавлен документ заявки', 'request', r.id,
            json.dumps({'document_id': d.id, 'kind': kind, 'version': d.version, 'filename': name, 'size': len(raw),
                        'sha256': d.sha256, 'approval_reset': reset, 'request_version': r.version}, ensure_ascii=False))
        return {'id': d.id, 'version': d.version, 'request_version': r.version, 'approval_reset': reset, 'status': r.status}


class RemoveDocumentIn(Input):
    version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=1000)


@router.post('/api/request-documents/{doc_id}/remove')
def remove_request_document(doc_id: int, data: RemoveDocumentIn, request: Request):
    """Убрать можно только прочий документ; обязательные заменяются новой версией."""
    with unit(True) as s:
        u, _ = session_user(s, request)
        d = get(s, RequestDocument, doc_id)
        r = document_target(s, u, d.request_id, data.version)
        if d.kind in REQUIRED_DOCUMENTS:
            raise HTTPException(409, 'Обязательный документ не убирается: загрузите новую версию.')
        if not d.is_current or d.removed:
            raise HTTPException(409, 'Документ уже заменён или убран.')
        d.removed = True
        d.is_current = False
        d.superseded_at = now()
        d.superseded_by = u.id
        reset = reset_after_document_change(s, u, r)
        s.flush()
        log(s, u, 'Убран документ заявки', 'request', r.id, json.dumps({'document_id': d.id, 'kind': d.kind, 'filename': d.filename,
            'approval_reset': reset, 'reason': data.reason, 'request_version': r.version}, ensure_ascii=False))
        return {'ok': True, 'request_version': r.version, 'approval_reset': reset}


@router.get('/api/request-documents/{doc_id}')
def download_request_document(doc_id: int, request: Request):
    with unit() as s:
        u, _ = session_user(s, request)
        d = get(s, RequestDocument, doc_id)
        visible(s, u, d.request_id)
        return Response(d.content, media_type=d.mime, headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(d.filename),
                                                               'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


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
    role: Literal['director', 'finance', 'accountant', 'employee', 'auditor', 'cashier', 'operator', 'investor']
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
    """Роль сотрудника в компании, которую он может передать на время отсутствия."""
    if user.role == 'founder':
        return None
    m = s.get(CompanyUser, (cid, user.id))
    if not m:
        return None
    if m.role in COMPANY_ROLES:
        return m.role
    return None if user.role in HOLDING_ROLES else user.role


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
        if acting.role == 'founder':
            raise HTTPException(409, 'Учредитель только просматривает данные и не замещает сотрудников.')
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
