import hashlib, hmac, secrets, os, base64
import bcrypt, jwt
from datetime import datetime, timezone
from datetime import timedelta
from fastapi import HTTPException, Request
from sqlalchemy import select, delete
from .db import User, LoginSession, LoginAttempt, now, DATA

ROLES = {'admin':'Администратор холдинга', 'founder':'Учредитель', 'director':'Директор', 'finance':'Финансовый директор',
         'cashier':'Кассир', 'accountant':'Расчётный бухгалтер', 'employee':'Заявитель', 'auditor':'Аудитор',
         'operator':'Сотрудник / оператор', 'investor':'Инвестор / управленец',
         'material_accountant':'Материальный бухгалтер', 'procurement':'Отдел закупок'}
# Роли холдинга действуют во всех компаниях; остальные роли назначаются на пару
# «пользователь × компания» (company_users.role).
HOLDING_ROLES = ('admin', 'founder')
COMPANY_ROLES = tuple(r for r in ROLES if r not in HOLDING_ROLES)
PERMS = {
 # Администратор холдинга: компании, пользователи, назначения, справочники, аудит.
 # Финансовые действия — только по отдельному назначению роли в конкретной компании.
 'admin': {'view','ledger','export','users','catalog','audit','approval_policy'},
 # Учредитель: все компании только для чтения.
 'founder': {'view','ledger','export','audit'},
 'director': {'view','ledger','export','request','write','approve','budget','plan','import','schedule','catalog','audit','approval_policy','request_edit'},
 'cashier': {'request','ledger','pay_cash'},
 'finance': {'view','ledger','export','request','write','approve','budget','plan','import','schedule','request_edit'},
 # Расчётный бухгалтер проверяет реквизиты и комплектность заявок и оплачивает банк.
 'accountant': {'ledger','pay_bank','request_check'},
 'employee': {'request'},
 'auditor': {'view','ledger','export','audit'},
 'operator': {'view','ledger','export','request','write','plan','import','schedule'},
 'investor': {'view','ledger','export'},
 # Материальный бухгалтер: только чтение журнала операций и документов к ним. Отдельного
 # материального (складского) учёта в продукте нет; остальные разделы — после согласования.
 'material_accountant': {'ledger'},
 # Закупки создают и отслеживают свои заявки, но не видят счета и остатки.
 'procurement': {'request'},
}

# Paying an approved request is a separate right per channel: the bank account
# is paid by the settlement accountant, the cash desk by the cashier. The
# general right to write never pays a request.
PAY_RIGHTS = {'bank': 'pay_bank', 'cash': 'pay_cash'}


def pay_right(account):
    return PAY_RIGHTS['cash' if account.kind == 'cash' else 'bank']


def payment_channels(permissions):
    """Account kinds the permissions can pay."""
    return {kind for kind, right in PAY_RIGHTS.items() if right in permissions}


def scope_user(user, assigned, acting=(), member=True):
    """Роль и права пользователя в выбранной компании.

    Учредитель всегда только читает. Администратор холдинга получает финансовые
    права роли лишь при отдельном назначении в этой компании. Сотрудник работает только
    по явной роли назначения; без неё прав в компании нет.
    ``acting`` — действующие замещения (ВрИО) в этой компании: их роли добавляют права,
    а собственная роль пользователя не меняется. ``member`` оставлен для совместимости вызовов."""
    if user.role == 'founder':
        role = None
        acting = ()
    elif user.role == 'admin':
        role = assigned if assigned in COMPANY_ROLES else None
    else:
        # Для сотрудника действует только явное назначение в выбранной компании.
        # Поле users.role хранится для совместимости и не расширяет область доступа.
        role = assigned if assigned in COMPANY_ROLES else None
    permissions = set(PERMS.get(user.role, set())) if user.role in HOLDING_ROLES else set()
    if role:
        permissions |= PERMS[role]
    delegated = {}
    for d in acting:
        if d['role'] in COMPANY_ROLES and d['role'] != role and d['role'] not in delegated:
            delegated[d['role']] = d
            permissions |= PERMS[d['role']]
    user._company_role = role
    user._acting = delegated
    user._acted = None
    user._permissions = frozenset(permissions)


def has_role(user, role):
    """Собственная роль в компании или действующее замещение этой роли."""
    return company_role(user) == role or role in getattr(user, '_acting', {})


def act_as(user, role):
    """Отмечает роль, в которой выполняется действие: для аудита ВрИО хранит заменяемого."""
    if company_role(user) == role:
        user._acted = (role, None)
    elif role in getattr(user, '_acting', {}):
        user._acted = (role, user._acting[role]['replaced_user_id'])
    return has_role(user, role)


def act_with_right(user, right):
    """Отмечает роль, которая дала право: собственная роль в компании, замещение (ВрИО)
    или роль холдинга. Журнал записывает именно её и заменяемого сотрудника."""
    own = company_role(user)
    if own and right in PERMS[own]:
        return act_as(user, own)
    for role in getattr(user, '_acting', {}):
        if right in PERMS[role]:
            return act_as(user, role)
    if user.role in HOLDING_ROLES and right in PERMS.get(user.role, set()):
        user._acted = (user.role, None)
        return True
    return right in perms_of(user)


def perms_of(user):
    """Права в выбранной компании; до выбора компании — права собственной роли."""
    if hasattr(user, '_permissions'):
        return user._permissions
    return frozenset(PERMS.get(user.role, set()))


def company_role(user):
    """Роль в выбранной компании, по которой работают этапы согласования и оплаты."""
    if hasattr(user, '_company_role'):
        return user._company_role
    return None if user.role in HOLDING_ROLES else user.role


def can_attach_document(user, entry):
    """Call only after the entry has been restricted to the selected company."""
    permissions = perms_of(user)
    return 'write' in permissions or (
        bool(payment_channels(permissions)) and entry.creator_id == user.id and entry.request_id is not None
    )

def jwt_key():
    configured=os.getenv('JWT_SECRET','')
    if configured:
        if len(configured)<32:raise RuntimeError('JWT_SECRET must be at least 32 characters')
        return configured
    path=DATA/'jwt.secret'
    if not path.exists():
        try:
            # Ключ подписи сессий читает только владелец процесса: другие пользователи компьютера его не видят.
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w',encoding='ascii') as f:f.write(secrets.token_urlsafe(48))
        except FileExistsError:pass
    return path.read_text(encoding='ascii').strip()

def issue_token(user_id):
    stamp=datetime.now(timezone.utc)
    return jwt.encode({'sub':str(user_id),'jti':secrets.token_urlsafe(24),'iat':stamp,'exp':stamp+timedelta(minutes=60),'iss':'zuma-treasury','aud':'zuma-api'},jwt_key(),algorithm='HS256')
def digest(s): return hashlib.sha256(s.encode()).hexdigest()
def hash_password(password):
    if not 12 <= len(password) <= 128:
        raise ValueError('Пароль должен содержать от 12 до 128 символов.')
    if password.lower() in {'password1234','123456789012','qwerty1234567'} or len(set(password))<6:
        raise ValueError('Выберите более сложный пароль.')
    if password.isdigit() or password.isalpha():
        raise ValueError('Пароль должен содержать буквы и цифры или другие символы.')
    # SHA-256 prehash avoids bcrypt's 72-byte truncation for long/Unicode passwords.
    value=base64.b64encode(hashlib.sha256(password.encode()).digest())
    return 'bcrypt_sha256$'+bcrypt.hashpw(value,bcrypt.gensalt(rounds=12)).decode()
# Проверка для отсутствующего пользователя занимает столько же времени, сколько для существующего.
DUMMY_HASH=hash_password('Dummy-'+secrets.token_urlsafe(24))

def verify_password(password, encoded):
    try:
        if encoded.startswith('bcrypt_sha256$'):
            value=base64.b64encode(hashlib.sha256(password.encode()).digest())
            return bcrypt.checkpw(value,encoded.split('$',1)[1].encode())
        method, n, salt, expected=encoded.split('$')
        actual=hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(n)).hex()
        return method=='pbkdf2_sha256' and hmac.compare_digest(actual,expected)
    except (ValueError, TypeError): return False

def session_user(s, request: Request, permission=None):
    bearer=request.headers.get('Authorization','')
    token=bearer[7:] if bearer.startswith('Bearer ') else request.cookies.get('zuma_session','')
    try:
        claims=jwt.decode(token,jwt_key(),algorithms=['HS256'],issuer='zuma-treasury',audience='zuma-api',options={'require':['sub','exp','iat','jti']})
    except jwt.PyJWTError:raise HTTPException(401,'Сессия завершена. Войдите снова.')
    session=s.get(LoginSession, digest(token)) if token else None
    if not session or session.expires_at <= now():
        raise HTTPException(401, 'Сессия завершена. Войдите снова.')
    user=s.get(User,session.user_id)
    if not user or not user.active: raise HTTPException(401,'Учётная запись отключена.')
    if claims['sub']!=str(user.id):raise HTTPException(401,'Неверный токен.')
    request.state.user_id=user.id
    # Адрес клиента для журнала: на Render — последнее значение X-Forwarded-For (client_ip);
    # исходный заголовок прокси хранится отдельно и не проверяется.
    user._ip=client_ip(request)
    user._forwarded=(request.headers.get('X-Forwarded-For') or '')[:200] or None
    # Временный пароль открывает только смену пароля, выход и сведения о себе.
    if user.must_change_password and request.url.path not in ('/api/me','/api/logout','/api/password'):
        raise HTTPException(403,'Смените временный пароль: до этого остальные разделы недоступны.')
    if not bearer.startswith('Bearer ') and request.method not in ('GET','HEAD'):
        if not hmac.compare_digest(request.headers.get('X-CSRF-Token','').encode('utf-8','surrogateescape'), session.csrf.encode()):
            raise HTTPException(403, 'Защитный токен не совпадает. Обновите страницу.')
    from .company_scope import activate
    activate(s, request, user)
    # Права проверяются после выбора компании: роль назначается отдельно в каждой.
    if permission and permission not in perms_of(user):
        raise HTTPException(403,'У вашей роли нет прав на это действие.')
    if permission:act_with_right(user,permission)
    return user,session

def client_ip(request):
    """Адрес клиента. На Render все запросы приходят от его прокси, а настоящий адрес
    прокси дописывает последним в X-Forwarded-For; значения левее мог подставить клиент."""
    peer=request.client.host if request.client else 'local'
    if os.getenv('UZGERMED_HOSTING')=='render':
        forwarded=[x.strip() for x in request.headers.get('X-Forwarded-For','').split(',') if x.strip()]
        if forwarded:return forwarded[-1][:64]
    return peer

# Пороги за 10 минут: один адрес и логин — 8 ошибок; один адрес по разным логинам — 30;
# один логин с разных адресов — 50 (высокий порог, чтобы чужие ошибки не блокировали сотрудника).
LOGIN_LIMITS=(8,30,50)

def login_keys(request, username):
    ip=client_ip(request);name=username.lower()
    return [digest('login-user-ip:'+name+'|'+ip),digest('login-ip:'+ip),digest('login-user:'+name)]
def login_limited(s,keys):
    return any((a:=s.get(LoginAttempt,k)) and a.count>=limit and a.since>now()-timedelta(minutes=10) for k,limit in zip(keys,LOGIN_LIMITS))
def record_failure(s,keys):
    for k in keys:
        a=s.get(LoginAttempt,k)
        if not a:
            s.add(LoginAttempt(key=k,count=1,since=now()))
        elif a.since<now()-timedelta(minutes=10):a.count=1;a.since=now()
        else:a.count+=1

def role_label(holding, role):
    if holding and role:return f'{ROLES[holding]} · {ROLES[role]}'
    return ROLES.get(holding or role,'Без роли в компании')


def user_json(user):
    """Текущий пользователь: роль и права в выбранной компании, включая замещения (ВрИО)."""
    holding = user.role if user.role in HOLDING_ROLES else None
    role = company_role(user)
    acting = [{'role':r,'role_label':ROLES[r],'replaced_user_id':d['replaced_user_id'],'replaced_name':d.get('replaced_name',''),
               'ends_on':d.get('ends_on')} for r,d in getattr(user,'_acting',{}).items()]
    label = role_label(holding,role) if (holding or role) else 'Без роли в компании'
    if acting:label += ''.join(' · ВрИО '+a['role_label'] for a in acting)
    return {'id':user.id,'username':user.username,'name':user.name,'role':role or user.role,
            'holding_role':holding,'company_role':role,'role_label':label,'acting':acting,
            'roles':sorted({x for x in [role,*[a['role'] for a in acting]] if x}),
            'permissions':sorted(perms_of(user)),'active':user.active,'must_change_password':bool(user.must_change_password)}


def member_json(user, assigned):
    """Сотрудник в списке выбранной компании с его ролью именно в этой компании."""
    holding = user.role if user.role in HOLDING_ROLES else None
    if user.role == 'founder':role = None
    elif holding:role = assigned if assigned in COMPANY_ROLES else None
    else:role = assigned if assigned in COMPANY_ROLES else None
    return {'id':user.id,'username':user.username,'name':user.name,'role':role or user.role,
            'holding_role':holding,'company_role':role,'role_label':role_label(holding,role),'active':user.active}
