from __future__ import annotations
import argparse,getpass,re,sys,json,sqlite3,zipfile
from pathlib import Path
from datetime import datetime
from settings_loader import load_config
load_config()
from app.db import *
from sqlalchemy import delete, func
from app.security import hash_password
from app.model_import import import_snapshot

def make_user(reset=None):
    with unit() as s:
        exists=s.scalar(select(User).where(User.username==reset)) if reset else None
        if reset and not exists:raise ValueError('Такого пользователя нет.')
    name=''
    if reset:username=reset
    else:
        username=input('Придумайте логин администратора [director]: ').strip().lower() or 'director'
        if not re.fullmatch(r'[a-z0-9_.-]{3,80}',username):raise ValueError('Логин: 3–80 латинских букв/цифр/._-')
        name=input('Имя администратора [Шерзод]: ').strip() or 'Шерзод'
    while True:
        password=getpass.getpass('Придумайте пароль (не менее 12 символов, ввод скрыт): ')
        confirm=getpass.getpass('Повторите пароль: ')
        if password!=confirm:print('Пароли не совпадают.');continue
        try:encoded=hash_password(password);break
        except ValueError as e:print(str(e))
    with unit(True) as s:
        if reset:
            u=s.scalar(select(User).where(User.username==username));u.password_hash=encoded
            s.execute(delete(LoginSession).where(LoginSession.user_id==u.id))
            s.add(Audit(user_id=u.id,action='Сброс пароля через консоль',entity='user',entity_id=str(u.id)))
        else:
            if s.scalar(select(User.id).where(User.username==username)):raise ValueError('Этот логин уже существует.')
            u=User(username=username,name=name[:160],password_hash=encoded,role='admin');s.add(u);s.flush()
            s.add(Audit(user_id=u.id,action='Создан первый администратор',entity='user',entity_id=str(u.id)))
    print(f'Готово. Логин: {username}. Пароль — тот, который вы только что задали.')

def init(no_user=False):
    initialize()
    # Первоначальная модель переносится только если ещё не было импортов.
    with unit() as s:need=s.scalar(select(ModelVersion.id).limit(1)) is None;needuser=s.scalar(select(User.id).limit(1)) is None
    source=Path(os.getenv('FINMODEL_PATH',str(ROOT/'source'/'FinModel_2026_Zuma_Pharm_v8.xlsx')))
    if need and source.exists():
        with unit(True) as s:
            rec,created=import_snapshot(s,source.read_bytes(),source.name,'Первоначальный импорт прикреплённого XLSX')
            snap=json.loads(rec.snapshot)
            print(f'FinModel: {len(snap["sheet_names"])} листов, версия {rec.id}. Импортирован отдельный снимок; счета и касса не заполнены.')
    if needuser and not no_user:make_user()
    print('База данных подготовлена. Операции сохраняются на сервере.')

def backup():
    target=ROOT/'backups';target.mkdir(exist_ok=True)
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    if not SQLITE:raise ValueError('Для PostgreSQL используйте pg_dump (команда в инструкции).')
    dbpath=Path(engine.url.database);tmp=target/(stamp+'.sqlite3')
    with sqlite3.connect(dbpath) as src,sqlite3.connect(tmp) as dst:src.backup(dst)
    dest=target/('cashflow_'+stamp+'.zip')
    with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED) as z:
        z.write(tmp,'cashflow.sqlite3')
        for p in (DATA/'models').glob('*.xlsx'):z.write(p,'models/'+p.name)
    tmp.unlink();print('Резервная копия:',dest)
    print('Архив содержит финансовые данные и хеши паролей. Храните его в защищённом месте.')

def main():
    parser=argparse.ArgumentParser(description='Управление ZUMA Cash Flow')
    parser.add_argument('command',choices=['init','create-admin','reset-password','import-model','sync-drive','backup','check'])
    parser.add_argument('value',nargs='?');parser.add_argument('--no-user',action='store_true')
    a=parser.parse_args()
    try:
        if a.command=='init':init(a.no_user)
        elif a.command=='create-admin':initialize();make_user()
        elif a.command=='reset-password':
            if not a.value:raise ValueError('Укажите логин: python manage.py reset-password director')
            make_user(a.value)
        elif a.command=='import-model':
            if not a.value:raise ValueError('Укажите путь к XLSX.')
            initialize();path=Path(a.value)
            with unit(True) as s:r,new=import_snapshot(s,path.read_bytes(),path.name,'Импорт через консоль')
            print('Версия:',r.id,'Новый импорт:',new)
        elif a.command=='sync-drive':
            from app.drive import download_model
            raw,name,source=download_model()
            with unit(True) as s:r,new=import_snapshot(s,raw,name,source)
            print('Версия:',r.id,'Новый импорт:',new)
        elif a.command=='backup':backup()
        elif a.command=='check':
            with unit() as s:print('База доступна. Пользователей:',s.scalar(select(func.count()).select_from(User)))
    except Exception as e:
        print('Ошибка:',str(e));return 1
    return 0
if __name__=='__main__':sys.exit(main())
