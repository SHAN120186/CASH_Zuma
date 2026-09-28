"""Центр администрирования холдинга: реестр пользователей, назначения, архив, пароли и сеансы.

Работает независимо от выбранной в финансовом интерфейсе компании. Доступ — только у
администратора холдинга; проверка выполняется на сервере для каждого запроса.
"""
from datetime import timedelta
from typing import Literal, Optional
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, func
from .db import unit, User, Company, CompanyUser, LoginSession, Audit, TelegramLink, now
from .security import session_user, ROLES, HOLDING_ROLES, COMPANY_ROLES, hash_password
from . import access

router = APIRouter()
STATES = {'ok': 'Действует', 'archived': 'В архиве', 'no_company': 'Нет компании',
          'conflict': 'Несколько компаний', 'no_role': 'Нет действующей роли'}


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


def holding_admin(s, request):
    user, _ = session_user(s, request, 'users')
    if user.role != 'admin':
        raise HTTPException(403, 'Раздел доступен только администратору холдинга.')
    return user


def user_row(s, u, companies, sessions, telegram, last_login):
    state = access.access_state(s, u)
    assignments = [{'company_id': m.company_id, 'company': companies[m.company_id].name if m.company_id in companies else '—',
                    'code': companies[m.company_id].code if m.company_id in companies else '', 'role': m.role,
                    'role_label': ROLES.get(m.role, 'Без роли'), 'valid': m in state['valid']}
                   for m in access.links(s, u.id)]
    assignments.sort(key=lambda x: x['company'])
    holding = u.role if u.role in HOLDING_ROLES else None
    valid = [a for a in assignments if a['valid']]
    return {'id': u.id, 'username': u.username, 'name': u.name, 'active': u.active, 'holding_role': holding,
            'holding_label': ROLES[holding] if holding else None, 'state': state['state'], 'state_label': STATES[state['state']],
            'assignments': assignments, 'company_role': valid[0]['role'] if len(valid) == 1 else None,
            'company_id': valid[0]['company_id'] if len(valid) == 1 else None,
            'must_change_password': bool(u.must_change_password), 'sessions': sessions.get(u.id, 0),
            'telegram': u.id in telegram, 'last_login': last_login.get(u.id), 'created_at': str(u.created_at)}


def registry(s):
    companies = {c.id: c for c in s.scalars(select(Company).execution_options(company_unscoped=True))}
    sessions = dict(s.execute(select(LoginSession.user_id, func.count()).where(LoginSession.expires_at > now())
                              .group_by(LoginSession.user_id)).all())
    telegram = set(s.scalars(select(TelegramLink.user_id)))
    last_login = {uid: str(at) for uid, at in s.execute(select(Audit.user_id, func.max(Audit.created_at))
                  .where(Audit.action == 'Вход в систему').group_by(Audit.user_id).execution_options(company_unscoped=True)).all()}
    return companies, sessions, telegram, last_login


@router.get('/api/admin/meta')
def admin_meta(request: Request):
    with unit() as s:
        holding_admin(s, request)
        companies = access.business_companies(s)
        return {'companies': [{'id': c.id, 'code': c.code, 'name': c.name} for c in sorted(companies.values(), key=lambda c: c.name)],
                'company_roles': [[r, ROLES[r]] for r in COMPANY_ROLES], 'holding_roles': [[r, ROLES[r]] for r in HOLDING_ROLES],
                'states': STATES}


@router.get('/api/admin/users')
def admin_users(request: Request, q: str = '', company_id: Optional[int] = None, role: str = '', state: str = ''):
    """Единый реестр пользователей холдинга с поиском и фильтрами."""
    with unit() as s:
        holding_admin(s, request)
        data = registry(s)
        rows = [user_row(s, u, *data) for u in s.scalars(select(User).order_by(User.name).execution_options(company_unscoped=True))]
        needle = q.strip().lower()
        if needle:
            rows = [r for r in rows if needle in r['name'].lower() or needle in r['username'].lower()]
        if company_id is not None:
            rows = [r for r in rows if any(a['company_id'] == company_id for a in r['assignments'])]
        if role:
            rows = [r for r in rows if r['holding_role'] == role or any(a['role'] == role for a in r['assignments'])]
        if state:
            rows = [r for r in rows if r['state'] == state]
        return rows


@router.get('/api/admin/users/{id}')
def admin_user(id: int, request: Request):
    with unit() as s:
        holding_admin(s, request)
        u = access.unscoped_user(s, id)
        row = user_row(s, u, *registry(s))
        names = {x.id: x.name for x in s.scalars(select(User).execution_options(company_unscoped=True))}
        history = s.scalars(select(Audit).where(Audit.entity == 'user', Audit.entity_id == str(id))
                            .order_by(Audit.id.desc()).limit(100).execution_options(company_unscoped=True))
        row['history'] = [{'id': a.id, 'at': str(a.created_at), 'actor': names.get(a.user_id, 'Система'), 'action': a.action,
                           'detail': a.detail} for a in history]
        return row


class CreateIn(Input):
    username: str = Field(pattern=r'^[a-z0-9_.-]{3,80}$')
    name: str = Field(min_length=2, max_length=160)
    holding_role: Optional[Literal['admin', 'founder']] = None
    company_id: Optional[int] = None
    role: Optional[str] = None
    reason: str = Field(min_length=10, max_length=1000)


@router.post('/api/admin/users')
def create_user(data: CreateIn, request: Request):
    """Новая учётная запись со случайным временным паролем; сменить его нужно при первом входе."""
    with unit(True) as s:
        admin = holding_admin(s, request)
        if s.scalar(select(User.id).where(User.username == data.username).execution_options(company_unscoped=True)):
            raise HTTPException(409, 'Такой логин уже занят.')
        if not data.holding_role:
            access.check_role(data.role)
            access.business_company(s, data.company_id)
        password = access.temporary_password()
        u = User(username=data.username, name=data.name, password_hash=hash_password(password),
                 role=data.holding_role or data.role, must_change_password=True)
        s.add(u)
        s.flush()
        if not data.holding_role:
            s.add(CompanyUser(company_id=data.company_id, user_id=u.id, role=data.role))
            s.flush()
        access.record(s, admin, 'Создан пользователь', u, None,
                      data.reason, None, request)
        return {'user': user_row(s, u, *registry(s)), 'temporary_password': password}


class NameIn(Input):
    name: str = Field(min_length=2, max_length=160)
    reason: str = Field(min_length=10, max_length=1000)


@router.put('/api/admin/users/{id}')
def rename_user(id: int, data: NameIn, request: Request):
    """Изменение имени не меняет роли и назначения."""
    with unit(True) as s:
        admin = holding_admin(s, request)
        u = access.unscoped_user(s, id)
        before = access.snapshot(s, u)
        old = u.name
        u.name = data.name
        access.record(s, admin, 'Изменено имя пользователя', u, before, data.reason, {'name_before': old, 'name_after': data.name}, request)
        return user_row(s, u, *registry(s))


class AssignmentIn(Input):
    company_id: int
    role: str
    reason: str = Field(min_length=10, max_length=1000)


@router.put('/api/admin/users/{id}/assignment')
def set_assignment(id: int, data: AssignmentIn, request: Request):
    with unit(True) as s:
        admin = holding_admin(s, request)
        u = access.unscoped_user(s, id)
        access.assign(s, admin, u, data.company_id, data.role, data.reason, request)
        return user_row(s, u, *registry(s))


class UnassignIn(Input):
    company_id: int
    reason: str = Field(min_length=10, max_length=1000)


@router.post('/api/admin/users/{id}/unassign')
def remove_assignment(id: int, data: UnassignIn, request: Request):
    with unit(True) as s:
        admin = holding_admin(s, request)
        u = access.unscoped_user(s, id)
        if u.id == admin.id:
            raise HTTPException(409, 'Собственные назначения меняет другой администратор.')
        access.unassign(s, admin, u, data.company_id, data.reason, request)
        return user_row(s, u, *registry(s))


class HoldingIn(Input):
    holding_role: Optional[Literal['admin', 'founder']] = None
    company_id: Optional[int] = None
    role: Optional[str] = None
    reason: str = Field(min_length=10, max_length=1000)


@router.put('/api/admin/users/{id}/holding')
def set_holding(id: int, data: HoldingIn, request: Request):
    with unit(True) as s:
        admin = holding_admin(s, request)
        u = access.unscoped_user(s, id)
        access.set_holding(s, admin, u, data.holding_role, data.reason, data.company_id, data.role, request)
        return user_row(s, u, *registry(s))


class ReasonIn(Input):
    reason: str = Field(min_length=10, max_length=1000)


@router.post('/api/admin/users/{id}/archive')
def archive_user(id: int, data: ReasonIn, request: Request):
    """Безопасно убирает доступ, сохраняя автора финансовых записей и аудит."""
    with unit(True) as s:
        admin = holding_admin(s, request)
        u = access.unscoped_user(s, id)
        access.archive(s, admin, u, data.reason, request)
        return {'ok': True, 'user': user_row(s, u, *registry(s))}


@router.post('/api/admin/users/{id}/restore')
def restore_user(id: int, data: HoldingIn, request: Request):
    with unit(True) as s:
        admin = holding_admin(s, request)
        u = access.unscoped_user(s, id)
        password = access.restore(s, admin, u, data.reason, data.holding_role, data.company_id, data.role, request)
        return {'user': user_row(s, u, *registry(s)), 'temporary_password': password}


@router.post('/api/admin/users/{id}/password')
def reset_password(id: int, data: ReasonIn, request: Request):
    with unit(True) as s:
        admin = holding_admin(s, request)
        u = access.unscoped_user(s, id)
        password = access.reset_password(s, admin, u, data.reason, request)
        return {'user': user_row(s, u, *registry(s)), 'temporary_password': password}


@router.post('/api/admin/users/{id}/sessions/end')
def end_sessions(id: int, data: ReasonIn, request: Request):
    with unit(True) as s:
        admin = holding_admin(s, request)
        u = access.unscoped_user(s, id)
        before = access.snapshot(s, u)
        count = access.end_sessions(s, u.id)
        access.record(s, admin, 'Завершены сеансы пользователя', u, before, data.reason, {'sessions': count}, request)
        return {'ended': count}


@router.get('/api/admin/audit')
def holding_audit(request: Request, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), user_id: Optional[int] = None):
    """Журнал холдинга: все компании и события без компании (вход, пароли, Telegram, бот, копии)."""
    with unit() as s:
        holding_admin(s, request)
        companies = {c.id: c.code for c in s.scalars(select(Company).execution_options(company_unscoped=True))}
        names = {u.id: u.name for u in s.scalars(select(User).execution_options(company_unscoped=True))}
        q = select(Audit).where(~Audit.action.like('API %'))
        if user_id is not None:
            q = q.where(Audit.user_id == user_id)
        total = s.scalar(select(func.count()).select_from(q.execution_options(company_unscoped=True).subquery()))
        rows = s.scalars(q.order_by(Audit.id.desc()).offset((page - 1) * page_size).limit(page_size).execution_options(company_unscoped=True))
        return {'total': total, 'items': [{'id': a.id, 'at': str(a.created_at), 'company': companies.get(a.company_id, 'Холдинг'),
                                           'user': names.get(a.user_id, 'Система'), 'action': a.action, 'entity': a.entity,
                                           'entity_id': a.entity_id, 'detail': a.detail} for a in rows]}
