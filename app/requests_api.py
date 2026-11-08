"""Карточка заявки, политика директора по статьям, календарь рабочих дней и ВрИО.
Вложения заявки — в erp.py (таблица request_documents)."""
from .department_catalog import labels as department_labels, catalog as department_catalog
from .category_catalog import catalog
import json
from datetime import date
from typing import Literal, Optional
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, func, literal, union_all
from .db import (unit, now, User, Account, Category, PaymentRequest, RequestDocument, CalendarDay, Delegation, Audit)
from .security import (session_user, perms_of, has_role, payment_channels, pay_right, ROLES, COMPANY_ROLES)
from .services import (get, log, money, amount, today, approval_stage, round_participants, all_participants, route_complete,
                       DOCUMENT_KINDS, REQUIRED_DOCUMENTS, REQUIRED_DOCUMENTS_BY_CHANNEL, REQUEST_DEPARTMENTS,
                       THRESHOLD_FIELDS, category_policy, request_json, request_journal, request_batch, request_reference_query)
from .workdays import due_minimums, LEAD_DAYS
from .company_scope import setting_key
from .workflow_setup import DEFAULT_THRESHOLDS
from .director_limits import limits_json

router = APIRouter()
MAX_DOCUMENT = 10 * 1024 * 1024
OPEN_STATUSES = ('draft', 'pending', 'returned', 'approved')


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


# ---------- Видимость и доступные действия ----------

def can_view_request(s, u, r):
    """Та же граница, что у списка заявок: реестр целиком, свои заявки, заявки к оплате своего канала
    и заявки на проверке у расчётного бухгалтера."""
    if r.status == 'deleted':
        return False
    perms = perms_of(u)
    if 'view' in perms:
        return True
    if 'request' in perms and r.creator_id == u.id:
        return True
    account = s.get(Account, r.account_id)
    if r.status in ('approved', 'paid') and account and account.kind in payment_channels(perms):
        return True
    if r.status in ('approved','paid') and 'pay_bank' in perms:
        from .cash_funding import funding_for
        if funding_for(s,r.id) is not None:return True
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
    return 'request_edit' in perms or ('request' in perms and r.creator_id == u.id)


STAGE_ROLE = {'check': 'accountant', 'finance': 'finance', 'director': 'director'}


def request_actions(s, u, r, batch=None, director_limit=None):
    """Действия, которые сервер примет от этого пользователя. Интерфейс только показывает их."""
    perms = perms_of(u)
    account = s.get(Account, r.account_id)
    stage = approval_stage(r)
    from .cash_funding import funding_for,capabilities
    funding=batch['fundings'].get(r.id) if batch is not None else funding_for(s,r.id)
    # Без прав согласующего, проверяющего или финансового руководителя сотрудник действует только в своих заявках.
    own_only = not ({'approve', 'request_edit', 'request_check'} & perms)
    actions = ['delete'] if 'request_delete' in perms else []
    editable = can_edit(u, r) and (not own_only or r.creator_id == u.id)
    if editable and r.status in ('draft', 'pending', 'returned'):
        actions.append('edit')
    if editable and r.status in ('draft', 'returned'):
        actions.append('submit')
    if editable and r.status in OPEN_STATUSES:
        actions.append('documents')
    if stage and has_role(u, STAGE_ROLE[stage]) and u.id not in round_participants(s, r, batch=batch) and (('approve' in perms and stage!='check') or (stage=='check' and 'request_check' in perms)):
        actions.append('check' if stage == 'check' else 'approve')
    if (('approve' in perms and r.status in ('pending', 'approved')) or (stage == 'check' and 'request_check' in perms and has_role(u, 'accountant'))) and not own_only:
        actions.append('return')
    if r.status == 'approved' and pay_right(account) in perms:
        actions.append('return_finance')
    if r.status in OPEN_STATUSES and ((r.creator_id == u.id and 'request' in perms) or ('approve' in perms and not own_only)):
        actions.append('close')
    if 'request_edit' in perms and r.status in ('pending', 'approved'):
        actions.append('reschedule')
    if r.status == 'approved' and pay_right(account) in perms and route_complete(r) and u.id not in all_participants(s, r, include_check=account.kind!='bank' and not has_role(u,'accountant'), batch=batch) and (funding is None or (funding.state=='received' and funding.mode!='batch')):
        actions.append('pay')
    if funding:
        allowed=capabilities(s,u,r,funding,batch=batch)
        if allowed['can_withdraw']:actions.append('cash_withdraw')
        if allowed['can_receive']:actions.append('cash_receive')
        if allowed['can_cancel']:actions.append('cash_cancel')
        if funding.state!='unfunded':
            actions=[a for a in actions if a not in ('edit','submit','documents','return','return_finance','reschedule')]
        if funding.state=='in_transit':actions=[a for a in actions if a not in ('close','delete')]
    return actions


def request_view(s, u, r, batch=None):
    data = request_json(s, r, batch=batch)
    if data.get('cash_funding'):
        from .cash_funding import funding_for,capabilities
        funding=batch['fundings'].get(r.id) if batch else funding_for(s,r.id)
        data['cash_funding'].update(capabilities(s,u,r,funding,batch=batch))
    # Без права просмотра реестра (заявитель, закупки, кассир, плательщик) прежний блок бюджета скрыт;
    # по выбранной статье и периоду остаётся карточка budget_card (лимит, использовано, резерв, доступно, после заявки).
    if 'view' not in perms_of(u):
        data['budget'] = {'limit': None, 'hidden': True, 'over': data['budget']['over'], 'mode': data['budget']['mode']}
    data['actions'] = [] if (getattr(u,'_sections',None) or {}).get('requests')=='view' else request_actions(s, u, r, batch=batch, director_limit=data['director_limit'])
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
    return dict(s.execute(select(User.id,User.name).where(User.id.in_(ids)).execution_options(company_unscoped=True)).all())


PEOPLE_FIELDS = ('creator_id', 'last_editor_id', 'checked_by', 'finance_approved_by', 'approved_by')


def card_reference_query(s, id):
    query=request_reference_query([id],department_key=setting_key(s,'departments'))
    # As names_of: historical names may outlive company membership. Only names
    # linked to this scoped request are selected, never other user attributes.
    for field in PEOPLE_FIELDS:
        person=User.__table__.alias('person_'+field)
        query=query.outerjoin(person,person.c.id==getattr(PaymentRequest,field)).add_columns(person.c.name)
    return query


def card_evidence(s, id):
    """One round trip for document metadata, history and their display names.

    UNION ALL avoids a documents x history join. Both branches retain ORM
    company filters. File contents are deliberately not part of this query.
    These detached records are presentation-only, never used by write guards.
    """
    from types import SimpleNamespace
    author=User.__table__.alias('evidence_author')
    acting=User.__table__.alias('evidence_acting')
    docs=select(literal('document'),RequestDocument.id,RequestDocument.created_at,RequestDocument.created_by,
        RequestDocument.kind,RequestDocument.version,RequestDocument.active,RequestDocument.filename,
        RequestDocument.mime,RequestDocument.sha256,literal(''),literal(''),literal(''),literal(None),
        author.c.name,literal(None),literal('request'),literal(str(id))).outerjoin(author,author.c.id==RequestDocument.created_by)\
        .where(RequestDocument.request_id==id)
    history=select(literal('audit'),Audit.id,Audit.created_at,Audit.user_id,Audit.action,literal(0),literal(False),
        literal(''),literal(''),literal(''),Audit.detail,Audit.role,Audit.ip,Audit.acting_for_id,
        author.c.name,acting.c.name,Audit.entity,Audit.entity_id).outerjoin(author,author.c.id==Audit.user_id)\
        .outerjoin(acting,acting.c.id==Audit.acting_for_id).where(request_journal(id))
    documents=[];journal=[];names={}
    for source,identity,at,uid,kind,version,active,filename,mime,sha,detail,role,ip,acting_id,name,acting_name,entity,entity_id in s.execute(union_all(docs,history)):
        if uid and name is not None:names[uid]=name
        if acting_id and acting_name is not None:names[acting_id]=acting_name
        if source=='document':
            documents.append(SimpleNamespace(id=identity,request_id=id,created_at=at,created_by=uid,kind=kind,
                version=version,active=active,filename=filename,mime=mime,sha256=sha))
        else:
            journal.append(SimpleNamespace(id=identity,created_at=at,user_id=uid,action=kind,
                detail=detail,role=role,ip=ip,acting_for_id=acting_id,entity=entity,entity_id=entity_id))
    documents.sort(key=lambda d:(d.kind,d.version,d.id));journal.sort(key=lambda a:a.id)
    return documents,journal,names


@router.get('/api/requests/{id}')
def request_card(id: int, request: Request):
    with unit() as s:
        u, _ = session_user(s, request)
        reference=s.execute(card_reference_query(s,id)).first()
        if reference is None or not can_view_request(s,u,reference[0]):
            raise HTTPException(404,'Запись не найдена.')
        r=reference[0]
        docs,rows,names=card_evidence(s,id)
        data = request_view(s, u, r, batch=request_batch(s,[r],documents=docs,journal_rows=rows,references=[reference]))
        names.update({getattr(r,field):name for field,name in zip(PEOPLE_FIELDS,reference[7:]) if name is not None})
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
                'required_documents': list(REQUIRED_DOCUMENTS),
                'required_documents_by_channel': {k:list(v) for k,v in REQUIRED_DOCUMENTS_BY_CHANNEL.items()},
                'departments':department_labels(s), 'department_catalog':department_catalog(s), 'max_document_mb': MAX_DOCUMENT // (1024 * 1024),
                'document_types': ['PDF', 'PNG', 'JPEG', 'DOCX', 'XLSX'], 'open_statuses': list(OPEN_STATUSES)}


# ---------- Политика директора по статьям ----------

def policy_json(c, names):
    effective = {cur: category_policy(c, cur) for cur in ('UZS', 'USD', 'EUR')}
    return {'id': c.id, 'name': c.name, 'policy': c.director_policy or 'always', 'skip_allowed': c.skip_allowed,
            'policies': {cur: getattr(c, 'director_policy_' + cur.lower()) for cur in ('UZS', 'USD', 'EUR')},
            'code': c.code, 'director_limits': limits_json(c),
            'limit_changed_at': str(c.director_limit_changed_at) if c.director_limit_changed_at else None,
            'limit_changed_by': names.get(c.director_limit_changed_by, '') if c.director_limit_changed_by else '',
            'thresholds': {cur: money(getattr(c, field)) if getattr(c, field) is not None else None for cur, field in THRESHOLD_FIELDS.items()},
            'effective': {cur: {'policy': p, 'threshold': money(t) if t is not None else None} for cur, (p, t) in effective.items()},
            'changed_at': str(c.director_policy_changed_at) if c.director_policy_changed_at else None,
            'changed_by': names.get(c.director_policy_changed_by, '') if c.director_policy_changed_by else ''}


@router.get('/api/approval-policy')
def approval_policy(request: Request):
    with unit() as s:
        u, _ = session_user(s, request, 'approval_policy')
        cats = [c for c,_ in catalog(s) if c.type=='outcome']
        names = names_of(s, [uid for c in cats for uid in (c.director_policy_changed_by, c.director_limit_changed_by)])
        return {'categories': [policy_json(c, names) for c in cats],
                'defaults': {cur: money(v) if v is not None else None for cur, v in DEFAULT_THRESHOLDS.items()},
                'can_edit_skip_list': 'users' in perms_of(u),
                'can_edit_director_limits': False,
                'rule': 'Директор участвует по политике статьи на момент отправки заявки. Нет политики или порога валюты — директор утверждает любую сумму.'}


@router.post('/api/approval-policy')
def set_approval_policy(request: Request):
    with unit(True) as s:
        session_user(s, request, 'approval_policy')
        raise HTTPException(409, 'Общий порог больше не используется: политика директора задаётся по каждой статье.')


class PolicyIn(Input):
    policy: Optional[Literal['always', 'threshold', 'skip']] = None
    policy_uzs: Optional[Literal['always', 'threshold', 'skip']] = None
    policy_usd: Optional[Literal['always', 'threshold', 'skip']] = None
    policy_eur: Optional[Literal['always', 'threshold', 'skip']] = None
    threshold_uzs: Optional[str] = None
    threshold_usd: Optional[str] = None
    threshold_eur: Optional[str] = None
    reason: str = Field(min_length=10, max_length=1000)


class DirectorLimitsIn(Input):
    limit_uzs: Optional[str] = None
    limit_usd: Optional[str] = None
    limit_eur: Optional[str] = None
    reason: str = Field(min_length=10, max_length=1000)


@router.put('/api/categories/{id}/director-limits')
def set_director_limits(id: int, data: DirectorLimitsIn, request: Request):
    with unit(True) as s:
        u, _ = session_user(s, request, 'approval_policy')
        if 'users' not in perms_of(u):
            raise HTTPException(403, 'Лимит директора изменяет только администратор холдинга.')
        get(s, Category, id)
        raise HTTPException(409, 'Верхний лимит директора больше не используется. Настройте порог участия.')


@router.put('/api/categories/{id}/director-policy')
def set_director_policy(id: int, data: PolicyIn, request: Request):
    with unit(True) as s:
        u, _ = session_user(s, request, 'approval_policy')
        c = get(s, Category, id)
        if c.type != 'outcome':
            raise HTTPException(422, 'Политика директора задаётся только для расходных статей.')
        legacy = data.policy is not None
        currencies = ('UZS', 'USD', 'EUR')
        selected = currencies if legacy else tuple(cur for cur in currencies
            if {'policy_' + cur.lower(), 'threshold_' + cur.lower()} & data.model_fields_set)
        if not selected:
            raise HTTPException(422, 'Укажите политику или порог хотя бы для одной валюты.')
        if (data.policy == 'skip' or any(getattr(data, 'policy_' + cur.lower()) == 'skip' for cur in selected)) and not c.skip_allowed:
            raise HTTPException(409, 'Без директора можно проводить только статьи из утверждённого перечня регулярных платежей.')
        names = names_of(s, [c.director_policy_changed_by, c.director_limit_changed_by])
        before = policy_json(c, names)
        if legacy:
            values = {f: amount(getattr(data, f), allow_zero=True) if getattr(data, f) not in (None, '') else None
                      for f in ('threshold_uzs', 'threshold_usd', 'threshold_eur')}
            if data.policy == 'threshold' and not any(v is not None for v in values.values()):
                raise HTTPException(422, 'Для политики «выше порога» задайте порог хотя бы для одной валюты.')
            c.director_policy = data.policy
            for cur in currencies:
                setattr(c, 'director_policy_' + cur.lower(), None)
            for field, value in values.items():
                setattr(c, 'director_' + field, value if data.policy == 'threshold' else None)
        else:
            for cur in selected:
                suffix = cur.lower()
                policy_field, threshold_field = 'policy_' + suffix, 'threshold_' + suffix
                if policy_field in data.model_fields_set:
                    setattr(c, 'director_' + policy_field, getattr(data, policy_field))
                if threshold_field in data.model_fields_set:
                    value = getattr(data, threshold_field)
                    setattr(c, 'director_' + threshold_field, None if value in (None, '') else amount(value, allow_zero=True))
                if (getattr(c, 'director_' + policy_field) == 'threshold'
                        and getattr(c, 'director_' + threshold_field) is None):
                    raise HTTPException(422, 'Для политики «выше порога» задайте порог хотя бы для одной валюты.')
        c.director_policy_changed_at = now()
        c.director_policy_changed_by = u.id
        s.flush()
        after = policy_json(c, names_of(s, [u.id, c.director_limit_changed_by]))
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
        if not data.allowed and (c.director_policy == 'skip' or any(
                getattr(c, 'director_policy_' + cur.lower()) == 'skip' for cur in ('UZS', 'USD', 'EUR'))):
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


def delegation_json(d, names, in_force=True):
    """``in_force`` — результат access.delegation_in_force для записи в сроке: запись, потерявшая
    основание мимо сайта (заменяемый отключён или сменил роль в восстановленной копии), показывается
    как «stale»: прав не даёт, администратор видит её и может отменить."""
    day = today()
    state = ('revoked' if d.revoked_at else 'planned' if d.starts_on > day else 'expired' if d.ends_on < day
             else 'active' if in_force else 'stale')
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
        from .access import delegation_in_force
        rows = list(s.scalars(select(Delegation).order_by(Delegation.starts_on.desc(), Delegation.id.desc())))
        names = names_of(s, [x for d in rows for x in (d.user_id, d.replaced_user_id, d.created_by)])
        day = today()
        return [delegation_json(d, names, d.revoked_at is not None or not (d.starts_on <= day <= d.ends_on)
                                or delegation_in_force(s, d, day)) for d in rows]


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
