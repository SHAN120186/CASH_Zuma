"""Единое правило доступа к компаниям.

Сотрудник работает ровно в одной компании и только по явной роли из company_users.role.
Пользователи холдинга (администратор, учредитель) видят компании по своей роли;
финансовое назначение администратора в компании выдаёт другой администратор.
Все пути изменения доступа — центр администрирования, прежние endpoints, перенос
учётных записей и служебные команды — вызывают функции этого модуля.
"""
import json, secrets, string
from fastapi import HTTPException
from sqlalchemy import select, delete, func, or_
from .db import User, CompanyUser, Company, LoginSession, now
from .security import ROLES, HOLDING_ROLES, COMPANY_ROLES, client_ip

SERVICE_CODE = 'UNASSIGNED'
# Без похожих символов (0/O, 1/l/I): пароль диктуют и переписывают вручную.
PASSWORD_ALPHABET = ''.join(ch for ch in string.ascii_letters + string.digits if ch not in '0O1lI')


def temporary_password(length=20):
    """Случайный временный пароль: буквы обоих регистров и цифры."""
    while True:
        value = ''.join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))
        if any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value):
            return value


def unscoped_user(s, id):
    """Пользователь по id независимо от выбранной компании (реестр холдинга)."""
    user = s.scalar(select(User).where(User.id == id).execution_options(company_unscoped=True))
    if not user:
        raise HTTPException(404, 'Пользователь не найден.')
    return user


def business_companies(s):
    return {c.id: c for c in s.scalars(select(Company).where(Company.active.is_(True), Company.code != SERVICE_CODE)
                                       .execution_options(company_unscoped=True))}


def links(s, user_id):
    return list(s.scalars(select(CompanyUser).where(CompanyUser.user_id == user_id).execution_options(company_unscoped=True)))


def valid_assignments(s, user):
    """Назначения, которые открывают компанию: действующая бизнес-компания и роль из перечня."""
    companies = business_companies(s)
    return [m for m in links(s, user.id) if m.company_id in companies and m.role in COMPANY_ROLES]


def access_state(s, user):
    """Состояние доступа для реестра: ok, archived, no_company, conflict, no_role."""
    rows = links(s, user.id)
    companies = business_companies(s)
    valid = [m for m in rows if m.company_id in companies and m.role in COMPANY_ROLES]
    stale = [m for m in rows if m not in valid]
    if not user.active:
        state = 'archived'
    elif user.role in HOLDING_ROLES:
        state = 'ok'
    elif len(valid) > 1:
        state = 'conflict'
    elif not valid:
        state = 'no_role' if stale else 'no_company'
    else:
        state = 'ok'
    return {'state': state, 'valid': valid, 'stale': stale}


def staff_company_ids(s, user):
    """Компании сотрудника. Конфликт (назначения в нескольких компаниях) приостанавливает доступ,
    пока администратор явно не выберет компанию: предупреждения недостаточно."""
    valid = valid_assignments(s, user)
    return [valid[0].company_id] if len(valid) == 1 else []


def snapshot(s, user):
    companies = {c.id: c for c in s.scalars(select(Company).execution_options(company_unscoped=True))}
    return {'active': user.active, 'holding_role': user.role if user.role in HOLDING_ROLES else None,
            'assignments': sorted([[companies[m.company_id].code if m.company_id in companies else m.company_id, m.role]
                                   for m in links(s, user.id)], key=lambda x: str(x[0]))}


def scope_home(s, user):
    """Роль и права вне выбранной компании (вход, «О себе»): у сотрудника — его единственное
    назначение, у пользователя холдинга — роль холдинга. Прежнее поле роли не используется."""
    from .security import scope_user
    if user.role in HOLDING_ROLES:
        scope_user(user, None)
        return
    valid = valid_assignments(s, user)
    scope_user(user, valid[0].role if len(valid) == 1 else None)


def end_sessions(s, user_id):
    return s.execute(delete(LoginSession).where(LoginSession.user_id == user_id)).rowcount or 0


def revoke_links_and_sessions(s, user, actor=None):
    """После изменения прав: сеансы завершаются, Telegram отвязывается, а группы сводки,
    подключённые пользователем, отключаются, если он больше не действующий администратор холдинга."""
    from .bot_api import revoke_telegram, drop_groups_of
    end_sessions(s, user.id)
    revoke_telegram(s, user.id)
    drop_groups_of(s, user, actor)


def end_delegations(s, actor, user, reason):
    """Замещения (ВрИО), потерявшие основание после изменения доступа, отменяются: ВрИО работает
    только в своей компании, а заменяемый сотрудник должен по-прежнему иметь эту роль.
    Замещение может относиться к другой компании, чем выбранная у администратора, поэтому запись
    идёт без ограничения выбранной компанией; каждая отмена попадает в журнал своей компании."""
    from .db import Delegation, Audit
    from .clock import today
    from .requests_api import can_act_in, member_role
    rows = list(s.scalars(select(Delegation).where(Delegation.revoked_at.is_(None), Delegation.ends_on >= today(),
                                                   or_(Delegation.user_id == user.id, Delegation.replaced_user_id == user.id))
                          .execution_options(company_unscoped=True)))
    ended = []
    cid = s.info.pop('company_id', None)
    try:
        s.flush()
        for d in rows:
            acting = user if d.user_id == user.id else unscoped_user(s, d.user_id)
            replaced = user if d.replaced_user_id == user.id else unscoped_user(s, d.replaced_user_id)
            if can_act_in(s, d.company_id, acting) and member_role(s, d.company_id, replaced) == d.role:
                continue
            d.revoked_at = now()
            d.revoked_by = actor.id if actor else None
            s.add(Audit(company_id=d.company_id, user_id=actor.id if actor else None, action='Отменено ВрИО', entity='delegation',
                        entity_id=str(d.id), detail=f'Изменён доступ сотрудника: {reason}'))
            ended.append(d.id)
        s.flush()
    finally:
        if cid is not None:
            s.info['company_id'] = cid
    return ended


def record(s, actor, action, user, before, reason, extra=None, request=None):
    """Запись журнала об изменении доступа: исполнитель, время (created_at), причина, было и стало."""
    from .services import log
    detail = {'before': before, 'after': snapshot(s, user), 'reason': reason}
    if extra:
        detail.update(extra)
    if request is not None and request.client:
        detail['ip'] = client_ip(request)
    log(s, actor, action, 'user', user.id, json.dumps(detail, ensure_ascii=False))


def business_company(s, company_id):
    company = s.scalar(select(Company).where(Company.id == company_id).execution_options(company_unscoped=True))
    if not company or not company.active:
        raise HTTPException(404, 'Компания не найдена.')
    if company.code == SERVICE_CODE:
        raise HTTPException(409, 'Служебное пространство «Не распределено» не назначается сотрудникам.')
    return company


def check_role(role):
    if role not in COMPANY_ROLES:
        raise HTTPException(422, 'Выберите роль в компании.')


def assign(s, actor, user, company_id, role, reason, request=None):
    """Назначение в компании по единому правилу.

    Сотрудник: остаётся ровно одно назначение — в выбранной компании; прежние компании
    отзываются. Администратор холдинга: финансовое назначение именно в этой компании,
    себе его не выдают. Учредителю назначения не нужны. Прежние сеансы завершаются."""
    check_role(role)
    company = business_company(s, company_id)
    if user.role == 'founder':
        raise HTTPException(409, 'Учредитель видит все компании только для чтения; назначение не требуется.')
    if actor is not None and user.id == actor.id:
        raise HTTPException(409, 'Роль в компании себе не назначают: её выдаёт другой администратор.')
    if not user.active:
        raise HTTPException(409, 'Учётная запись в архиве: восстановите её с новым назначением.')
    before = snapshot(s, user)
    if user.role not in HOLDING_ROLES:
        s.execute(delete(CompanyUser).where(CompanyUser.user_id == user.id, CompanyUser.company_id != company.id))
        # Прежнее поле роли сохраняется для совместимости и совпадает с назначением.
        user.role = role
    m = s.get(CompanyUser, (company.id, user.id))
    if not m:
        m = CompanyUser(company_id=company.id, user_id=user.id)
        s.add(m)
    m.role = role
    s.flush()
    # Изменение прав отвязывает Telegram и завершает сеансы (TELEGRAM_BOT_RU.md).
    revoke_links_and_sessions(s, user, actor)
    ended = end_delegations(s, actor, user, reason)
    record(s, actor, 'Назначение в компании', user, before, reason, {'company': company.code, 'role': role, 'delegations_ended': ended}, request)
    return m


def unassign(s, actor, user, company_id, reason, request=None):
    """Снимает доступ к одной компании (у администратора холдинга — финансовое назначение)."""
    if user.role == 'founder':
        raise HTTPException(409, 'Учредитель видит все компании только для чтения.')
    before = snapshot(s, user)
    s.execute(delete(CompanyUser).where(CompanyUser.user_id == user.id, CompanyUser.company_id == company_id))
    s.flush()
    revoke_links_and_sessions(s, user, actor)
    ended = end_delegations(s, actor, user, reason)
    record(s, actor, 'Снято назначение администратора в компании' if user.role == 'admin' else 'Отозван доступ к компании',
           user, before, reason, {'company_id': company_id, 'delegations_ended': ended}, request)


def admins_left(s, excluding):
    return s.scalar(select(func.count()).select_from(User).where(User.role == 'admin', User.active.is_(True), User.id != excluding)
                    .execution_options(company_unscoped=True))


def set_holding(s, actor, user, holding_role, reason, company_id=None, role=None, request=None):
    """Явная смена статуса холдинга. Все прежние назначения снимаются: статус не возвращается сам.
    Бывший пользователь холдинга получает ровно одно назначение в компании."""
    if actor is not None and user.id == actor.id:
        raise HTTPException(409, 'Собственный статус холдинга не меняют: это делает другой администратор.')
    if holding_role not in (None, 'admin', 'founder'):
        raise HTTPException(422, 'Статус холдинга: администратор, учредитель или сотрудник компании.')
    if user.role == 'admin' and holding_role != 'admin' and not admins_left(s, user.id):
        raise HTTPException(409, 'В системе должен оставаться активный администратор.')
    before = snapshot(s, user)
    s.execute(delete(CompanyUser).where(CompanyUser.user_id == user.id))
    if holding_role:
        user.role = holding_role
    else:
        check_role(role)
        company = business_company(s, company_id)
        user.role = role
        s.add(CompanyUser(company_id=company.id, user_id=user.id, role=role))
    s.flush()
    revoke_links_and_sessions(s, user, actor)
    ended = end_delegations(s, actor, user, reason)
    record(s, actor, 'Изменён статус холдинга', user, before, reason, {'holding_role': holding_role, 'delegations_ended': ended}, request)


def archive(s, actor, user, reason, request=None):
    """Вход, сеансы, Telegram и все назначения отключаются. Пользователь остаётся автором документов."""
    if actor is not None and user.id == actor.id:
        raise HTTPException(409, 'Нельзя архивировать собственную учётную запись.')
    if user.role == 'admin' and user.active and not admins_left(s, user.id):
        raise HTTPException(409, 'В системе должен оставаться активный администратор.')
    if not user.active:
        raise HTTPException(409, 'Учётная запись уже в архиве.')
    before = snapshot(s, user)
    user.active = False
    user.must_change_password = True
    s.execute(delete(CompanyUser).where(CompanyUser.user_id == user.id))
    revoke_links_and_sessions(s, user, actor)
    s.flush()
    ended = end_delegations(s, actor, user, reason)
    record(s, actor, 'Пользователь архивирован; доступ ко всем компаниям отозван', user, before, reason, {'delegations_ended': ended}, request)


def restore(s, actor, user, reason, holding_role=None, company_id=None, role=None, request=None):
    """Восстановление только с явным новым назначением и новым временным паролем.
    Прежний статус холдинга не возвращается сам: его указывают явно."""
    from .security import hash_password
    if user.active:
        raise HTTPException(409, 'Учётная запись уже действует.')
    if holding_role not in (None, 'admin', 'founder'):
        raise HTTPException(422, 'Статус холдинга: администратор, учредитель или сотрудник компании.')
    before = snapshot(s, user)
    s.execute(delete(CompanyUser).where(CompanyUser.user_id == user.id))
    if holding_role:
        user.role = holding_role
    else:
        check_role(role)
        company = business_company(s, company_id)
        user.role = role
        s.add(CompanyUser(company_id=company.id, user_id=user.id, role=role))
    password = temporary_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    user.active = True
    s.flush()
    end_sessions(s, user.id)
    record(s, actor, 'Пользователь восстановлен из архива', user, before, reason, {'holding_role': holding_role}, request)
    return password


def reset_password(s, actor, user, reason, request=None):
    """Новый временный пароль: прежние сеансы и Telegram отключаются, при входе пароль нужно сменить."""
    from .security import hash_password
    if actor is not None and user.id == actor.id:
        raise HTTPException(409, 'Свой пароль меняют в профиле с вводом текущего пароля.')
    if not user.active:
        raise HTTPException(409, 'Учётная запись в архиве: восстановите её с новым назначением.')
    before = snapshot(s, user)
    password = temporary_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    revoke_links_and_sessions(s, user, actor)
    s.flush()
    record(s, actor, 'Сброшен пароль; сеансы и Telegram отключены', user, before, reason, None, request)
    return password
