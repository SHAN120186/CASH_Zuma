import hashlib, hmac, secrets, os, base64
import bcrypt, jwt
from datetime import datetime, timezone
from datetime import timedelta
from fastapi import HTTPException, Request
from sqlalchemy import select, delete
from .db import User, LoginSession, LoginAttempt, now, DATA

ROLES = {'admin':'Администратор', 'director':'Директор', 'finance':'Финансист / казначей', 'cashier':'Кассир',
         'accountant':'Бухгалтер', 'employee':'Инициатор', 'auditor':'Аудитор',
         'operator':'Сотрудник / оператор', 'investor':'Инвестор / управленец'}
PERMS = {
 'admin': {'view','ledger','export','request','write','approve','budget','plan','import','users','schedule','catalog','audit','approval_policy','request_edit'},
 'director': {'view','ledger','export','request','write','approve','budget','plan','import','schedule','catalog','audit','approval_policy','request_edit'},
 'cashier': {'request','ledger','write'},
 'finance': {'view','ledger','export','request','write','approve','budget','plan','import','schedule','request_edit'},
 'accountant': {'ledger','pay'},
 'employee': {'request'},
 'auditor': {'view','ledger','export','audit'},
 'operator': {'view','ledger','export','request','write','plan','import','schedule'},
 'investor': {'view','ledger','export'},
}

def can_attach_document(user, entry):
    """Call only after the entry has been restricted to the selected company."""
    permissions = PERMS.get(user.role, set())
    return 'write' in permissions or (
        'pay' in permissions and entry.creator_id == user.id and entry.request_id is not None
    )

def jwt_key():
    configured=os.getenv('JWT_SECRET','')
    if configured:
        if len(configured)<32:raise RuntimeError('JWT_SECRET must be at least 32 characters')
        return configured
    path=DATA/'jwt.secret'
    if not path.exists():
        try:
            with path.open('x',encoding='ascii') as f:f.write(secrets.token_urlsafe(48))
        except FileExistsError:pass
    return path.read_text(encoding='ascii').strip()

def issue_token(user_id):
    stamp=datetime.now(timezone.utc)
    return jwt.encode({'sub':str(user_id),'jti':secrets.token_urlsafe(24),'iat':stamp,'exp':stamp+timedelta(minutes=60),'iss':'zuma-treasury','aud':'zuma-api'},jwt_key(),algorithm='HS256')
def digest(s): return hashlib.sha256(s.encode()).hexdigest()
def hash_password(password):
    if not 12 <= len(password) <= 128:
        raise ValueError('Пароль должен содержать от 12 до 128 символов.')
    if password.lower() in {'password1234','123456789012','qwerty1234567'}:
        raise ValueError('Выберите более сложный пароль.')
    # SHA-256 prehash avoids bcrypt's 72-byte truncation for long/Unicode passwords.
    value=base64.b64encode(hashlib.sha256(password.encode()).digest())
    return 'bcrypt_sha256$'+bcrypt.hashpw(value,bcrypt.gensalt(rounds=12)).decode()
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
    if not bearer.startswith('Bearer ') and request.method not in ('GET','HEAD'):
        if not hmac.compare_digest(request.headers.get('X-CSRF-Token',''), session.csrf):
            raise HTTPException(403, 'Защитный токен не совпадает. Обновите страницу.')
    if permission and permission not in PERMS.get(user.role,set()):
        raise HTTPException(403,'У вашей роли нет прав на это действие.')
    from .company_scope import activate
    activate(s, request, user)
    return user,session

def login_keys(request, username):
    ip=request.client.host if request.client else 'local'
    return [digest('login-ip:'+ip),digest('login-user:'+username.lower())]
def login_limited(s,keys):
    return any((a:=s.get(LoginAttempt,k)) and a.count>=8 and a.since>now()-timedelta(minutes=10) for k in keys)
def record_failure(s,keys):
    for k in keys:
        a=s.get(LoginAttempt,k)
        if not a:
            s.add(LoginAttempt(key=k,count=1,since=now()))
        elif a.since<now()-timedelta(minutes=10):a.count=1;a.since=now()
        else:a.count+=1

def user_json(user):
    return {'id':user.id,'username':user.username,'name':user.name,'role':user.role,
            'role_label':ROLES[user.role],'permissions':sorted(PERMS[user.role]),'active':user.active}
