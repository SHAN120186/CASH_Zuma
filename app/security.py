import hashlib, hmac, secrets
from datetime import timedelta
from fastapi import HTTPException, Request
from sqlalchemy import select, delete
from .db import User, LoginSession, LoginAttempt, now

ROLES = {'admin':'Администратор', 'director':'Директор', 'finance':'Финансист',
         'accountant':'Бухгалтер', 'employee':'Сотрудник'}
PERMS = {
 'admin': {'view','write','approve','budget','import','users','schedule'},
 'director': {'view','approve'},
 'finance': {'view','write','budget','import','schedule'},
 'accountant': {'view','write'},
 'employee': set(),
}
def digest(s): return hashlib.sha256(s.encode()).hexdigest()
def hash_password(password):
    if not 12 <= len(password) <= 128:
        raise ValueError('Пароль должен содержать от 12 до 128 символов.')
    if password.lower() in {'password1234','123456789012','qwerty1234567'}:
        raise ValueError('Выберите более сложный пароль.')
    salt=secrets.token_hex(16)
    value=hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000).hex()
    return f'pbkdf2_sha256$600000${salt}${value}'
def verify_password(password, encoded):
    try:
        method, n, salt, expected=encoded.split('$')
        actual=hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(n)).hex()
        return method=='pbkdf2_sha256' and hmac.compare_digest(actual,expected)
    except (ValueError, TypeError): return False

def session_user(s, request: Request, permission=None):
    token=request.cookies.get('zuma_session','')
    session=s.get(LoginSession, digest(token)) if token else None
    if not session or session.expires_at <= now():
        raise HTTPException(401, 'Сессия завершена. Войдите снова.')
    user=s.get(User,session.user_id)
    if not user or not user.active: raise HTTPException(401,'Учётная запись отключена.')
    if request.method not in ('GET','HEAD'):
        if not hmac.compare_digest(request.headers.get('X-CSRF-Token',''), session.csrf):
            raise HTTPException(403, 'Защитный токен не совпадает. Обновите страницу.')
    if permission and permission not in PERMS.get(user.role,set()):
        raise HTTPException(403,'У вашей роли нет прав на это действие.')
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
