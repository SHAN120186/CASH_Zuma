"""Переход на персональные учётные записи: перечень, резервная копия, проверка плана и применение.

Запуск только владельцем или по его явному разрешению. Строка подключения берётся из
переменной окружения, имя которой передаётся в --database-url-env (по умолчанию
TRANSITION_DATABASE_URL), и никогда не печатается. Пароли, перечень сотрудников и журнал
пишутся только в папку --out, которая должна быть вне репозитория и вне OneDrive.

  python deploy/access_transition.py inventory --out D:\\private
  python deploy/access_transition.py backup    --out D:\\private
  python deploy/access_transition.py verify    --out D:\\private --backup D:\\private\\zuma-....dump   (восстановление в отдельную базу)
  python deploy/access_transition.py plan  plan.json --out D:\\private           (проверка, без изменений)
  python deploy/access_transition.py apply plan.json --out D:\\private --backup D:\\private\\zuma-....dump

Формат plan.json:
  {"site_url": "https://cash-zuma.onrender.com/",
   "reason": "Переход на персональные учётные записи",
   "keep": ["login", {"login": "x", "company": "ZUMA", "role": "finance"}],
   "accounts": [{"section": "ZUMA", "name": "ФИО", "role": "procurement", "login": "zuma.aziz"},
                {"section": "HOLDING", "name": "ФИО", "role": "founder", "login": "owner"},
                {"section": "UZGERMED", "name": null, "role": "cashier", "login": null}],
   "archive_others": true}
Запись без ФИО и логина попадает в список со статусом «ожидает сотрудника» и не создаётся.
"""
from __future__ import annotations
import argparse, csv, json, os, re, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGIN = re.compile(r'^[a-z0-9_.-]{3,80}$')
SECTIONS = {'HOLDING': 'Холдинг'}


def connect_app(url_env):
    url = os.environ.get(url_env)
    if not url:
        sys.exit(f'Задайте строку подключения в переменной окружения {url_env}.')
    os.environ['DATABASE_URL'] = url
    sys.path.insert(0, str(ROOT))
    import app.db as db
    return db


def synced(folder):
    """Папка внутри OneDrive: корни из переменных окружения или папка «OneDrive» / «OneDrive - …» в пути."""
    roots = [Path(v).resolve() for k in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial') if (v := os.getenv(k))]
    if any(r == folder or r in folder.parents for r in roots):
        return True
    return any(part.lower() == 'onedrive' or part.lower().startswith('onedrive - ') for part in folder.parts)


def out_dir(path):
    folder = Path(path).resolve()
    if ROOT in folder.parents or folder == ROOT or synced(folder):
        sys.exit('Папка --out должна быть вне репозитория и вне OneDrive: там будут пароли и сведения о сотрудниках.')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')


def inventory(db):
    """Пользователи, назначения и связи с документами — без хешей паролей и токенов."""
    from sqlalchemy import select, func
    from app.access import access_state, links
    with db.unit() as s:
        companies = {c.id: c.code for c in s.scalars(select(db.Company))}
        counts = {}
        for name, column in [('requests_created', db.PaymentRequest.creator_id), ('finance_approved', db.PaymentRequest.finance_approved_by),
                             ('director_approved', db.PaymentRequest.approved_by), ('ledger_created', db.Ledger.creator_id),
                             ('audit_rows', db.Audit.user_id), ('request_documents', db.RequestDocument.created_by)]:
            counts[name] = dict(s.execute(select(column, func.count()).group_by(column)).all())
        telegram = set(s.scalars(select(db.TelegramLink.user_id)))
        sessions = dict(s.execute(select(db.LoginSession.user_id, func.count()).where(db.LoginSession.expires_at > db.now())
                                  .group_by(db.LoginSession.user_id)).all())
        rows = []
        for u in s.scalars(select(db.User).order_by(db.User.id)):
            state = access_state(s, u)
            rows.append({'id': u.id, 'login': u.username, 'name': u.name, 'holding_role': u.role if u.role in ('admin', 'founder') else None,
                         'active': u.active, 'state': state['state'], 'created_at': str(u.created_at),
                         'assignments': [[companies.get(m.company_id, m.company_id), m.role] for m in links(s, u.id)],
                         'telegram': u.id in telegram, 'active_sessions': sessions.get(u.id, 0),
                         **{k: v.get(u.id, 0) for k, v in counts.items()}})
        return {'taken_at': stamp(), 'companies': companies, 'users': rows}


VERIFY_TABLES = ('users', 'company_users', 'companies', 'accounts', 'ledger', 'payment_requests', 'expected_receipts', 'budgets',
                 'categories', 'documents', 'request_documents', 'audit_log', 'cash_plans', 'telegram_links', 'settings')


def verify_backup(db, dump):
    """Восстанавливает копию в отдельную временную базу на том же сервере и сверяет строки и суммы."""
    import subprocess, uuid, psycopg
    from psycopg import sql
    from deploy.postgres_backup import pg_environment, pg_tool
    url = db.engine.url
    name = 'zuma_restore_' + uuid.uuid4().hex[:12]

    def connect(database):
        return psycopg.connect(host=url.host, port=url.port or 5432, user=url.username, password=url.password, dbname=database,
                               autocommit=True, connect_timeout=20, sslmode=url.query.get('sslmode', 'prefer'))
    with connect(url.database) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            env = {**pg_environment(url), 'PGDATABASE': name}
            subprocess.run([pg_tool('pg_restore'), '--no-owner', '--exit-on-error', '--dbname', name, str(dump)],
                           env=env, check=True, capture_output=True, timeout=600)
            result = {}
            with connect(name) as restored:
                for table in VERIFY_TABLES:
                    q = sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))
                    result[table] = [admin.execute(q).fetchone()[0], restored.execute(q).fetchone()[0]]
                for table, field in (('accounts', 'opening'), ('ledger', 'amount'), ('payment_requests', 'amount')):
                    q = sql.SQL('SELECT COALESCE(SUM({}),0) FROM {}').format(sql.Identifier(field), sql.Identifier(table))
                    result[table + '.' + field] = [str(admin.execute(q).fetchone()[0]), str(restored.execute(q).fetchone()[0])]
        finally:
            admin.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(name)))
    return result, {k: v for k, v in result.items() if v[0] != v[1]}


def verified(folder, backup):
    for f in folder.glob('backup-verified-*.json'):
        data = json.loads(f.read_text(encoding='utf-8'))
        if data.get('backup') == backup.name and not data.get('mismatch'):
            return True
    return False


def load_plan(path):
    plan = json.loads(Path(path).read_text(encoding='utf-8'))
    plan.setdefault('keep', [])
    plan.setdefault('accounts', [])
    plan.setdefault('archive_others', True)
    if len(plan.get('reason', '')) < 10:
        sys.exit('В плане нужна причина перехода (reason) не короче 10 символов.')
    return plan


def check_plan(db, plan):
    """Все ошибки плана сразу; ничего не меняется."""
    from sqlalchemy import select
    from app.security import COMPANY_ROLES, HOLDING_ROLES
    from app.access import valid_assignments
    errors, actions = [], {'create': [], 'pending': [], 'archive': [], 'keep': [], 'reassign': []}
    with db.unit() as s:
        codes = {c.code: c.id for c in s.scalars(select(db.Company).where(db.Company.active.is_(True)).execution_options(company_unscoped=True))}
        users = {u.username: u for u in s.scalars(select(db.User).execution_options(company_unscoped=True))}
        keep = {}
        for item in plan['keep']:
            item = {'login': item} if isinstance(item, str) else item
            u = users.get(item['login'])
            if not u:
                errors.append(f"Сохраняемый логин {item['login']} не найден.")
                continue
            if not u.active:
                errors.append(f"Сохраняемый логин {item['login']} в архиве: добавьте его в accounts как новую запись или восстановите вручную.")
            if item.get('company'):
                if item['company'] not in codes or item['company'] == 'UNASSIGNED':
                    errors.append(f"Неизвестная компания {item['company']} у {item['login']}.")
                if item.get('role') not in COMPANY_ROLES:
                    errors.append(f"Неизвестная роль {item.get('role')} у {item['login']}.")
                actions['reassign'].append(item)
            elif u.role not in HOLDING_ROLES and len(valid_assignments(s, u)) != 1:
                errors.append(f"У сохраняемого сотрудника {item['login']} нет единственного назначения: укажите company и role.")
            keep[item['login']] = item
            actions['keep'].append(item['login'])
        seen = set()
        for a in plan['accounts']:
            section, role, login, name = a.get('section'), a.get('role'), a.get('login'), a.get('name')
            if section != 'HOLDING' and (section not in codes or section == 'UNASSIGNED'):
                errors.append(f'Неизвестный раздел {section}: HOLDING или код компании ({", ".join(c for c in codes if c != "UNASSIGNED")}).')
            if section == 'HOLDING' and role not in HOLDING_ROLES:
                errors.append(f'В разделе HOLDING роль только admin или founder: {login or name}.')
            if section != 'HOLDING' and role not in COMPANY_ROLES:
                errors.append(f'Неизвестная роль {role} у {login or name}.')
            if not name or not login:
                actions['pending'].append(a)
                continue
            if not LOGIN.match(login):
                errors.append(f'Логин {login}: 3–80 символов a-z, 0-9, точка, дефис, подчёркивание.')
            if login in seen or login in users:
                errors.append(f'Логин {login} уже занят или повторяется в плане.')
            seen.add(login)
            actions['create'].append(a)
        if plan['archive_others']:
            actions['archive'] = [u.username for u in users.values() if u.active and u.username not in keep]
        admins_after = [l for l in keep if users.get(l) and users[l].role == 'admin' and users[l].active] + \
                       [a['login'] for a in actions['create'] if a.get('section') == 'HOLDING' and a.get('role') == 'admin']
        if not admins_after:
            errors.append('После перехода не останется ни одного активного администратора: сохраните свой вход (keep) или создайте администратора.')
    return errors, actions


def write_credentials(folder, plan, created, pending, archived):
    url = plan.get('site_url', 'https://cash-zuma.onrender.com/')
    from app.security import ROLES
    rows = [(SECTIONS.get(a['section'], a['section']), a['name'], ROLES[a['role']], a['login'], password, url, 'создан')
            for a, password in created]
    rows += [(SECTIONS.get(a['section'], a['section']), a.get('name') or '—', ROLES.get(a.get('role'), a.get('role')), '—', '—', url, 'ожидает сотрудника')
             for a in pending]
    rows += [('Архив', name, '—', login, '—', '—', 'архивирован') for login, name in archived]
    name = folder / f'logins-{stamp()}.csv'
    with name.open('x', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['Раздел', 'ФИО', 'Роль', 'Логин', 'Временный пароль', 'Адрес входа', 'Статус'])
        for section in ['Холдинг', 'ZUMA', 'UZGERMED']:
            w.writerows(sorted(r for r in rows if r[0] == section))
        w.writerows(sorted(r for r in rows if r[0] not in ('Холдинг', 'ZUMA', 'UZGERMED')))
    return name


def apply(db, plan, folder, backup):
    """Одна транзакция: архивирование, назначения сохраняемых и создание новых учётных записей."""
    from sqlalchemy import select
    from app import access
    from app.security import hash_password
    backup = Path(backup)
    if not backup.exists() or backup.stat().st_size == 0:
        sys.exit('Нужна проверенная резервная копия (--backup): файл не найден или пуст.')
    if not db.SQLITE and not verified(folder, backup):
        sys.exit('Сначала проверьте эту копию командой verify: восстановление в отдельную базу должно пройти без расхождений.')
    errors, actions = check_plan(db, plan)
    if errors:
        sys.exit('План не применён:\n- ' + '\n- '.join(errors))
    reason = plan['reason']
    journal = {'started_at': stamp(), 'backup': backup.name, 'archived': [], 'created': [], 'reassigned': [], 'pending': len(actions['pending'])}
    created = []
    with db.unit(True) as s:
        users = {u.username: u for u in s.scalars(select(db.User).execution_options(company_unscoped=True))}
        codes = {c.code: c.id for c in s.scalars(select(db.Company).execution_options(company_unscoped=True))}
        for item in actions['reassign']:
            access.assign(s, None, users[item['login']], codes[item['company']], item['role'], reason)
            journal['reassigned'].append({'login': item['login'], 'company': item['company'], 'role': item['role']})
        archived = []
        for login in actions['archive']:
            u = users[login]
            access.archive(s, None, u, reason)
            archived.append((login, u.name))
            journal['archived'].append(login)
        for a in actions['create']:
            password = access.temporary_password()
            holding = a['section'] == 'HOLDING'
            u = db.User(username=a['login'], name=a['name'], password_hash=hash_password(password), role=a['role'], must_change_password=True)
            s.add(u)
            s.flush()
            if not holding:
                s.add(db.CompanyUser(company_id=codes[a['section']], user_id=u.id, role=a['role']))
                s.flush()
            access.record(s, None, 'Создан пользователь (переход на персональные учётные записи)', u,
                          None, reason)
            created.append((a, password))
            journal['created'].append({'login': a['login'], 'section': a['section'], 'role': a['role']})
    journal['finished_at'] = stamp()
    creds = write_credentials(folder, plan, created, actions['pending'], archived)
    (folder / f'transition-journal-{journal["finished_at"]}.json').write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'Готово: создано {len(created)}, архивировано {len(archived)}, переназначено {len(journal["reassigned"])}, '
          f'ожидают сотрудника {len(actions["pending"])}. Закрытый список: {creds.name}. Журнал без паролей рядом с ним.')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['inventory', 'backup', 'verify', 'plan', 'apply'])
    p.add_argument('plan', nargs='?')
    p.add_argument('--out', required=True)
    p.add_argument('--backup')
    p.add_argument('--database-url-env', default='TRANSITION_DATABASE_URL')
    args = p.parse_args()
    folder = out_dir(args.out)
    db = connect_app(args.database_url_env)
    if args.command == 'inventory':
        data = inventory(db)
        target = folder / f'inventory-{data["taken_at"]}.json'
        target.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        states = {}
        for u in data['users']:
            states[u['state']] = states.get(u['state'], 0) + 1
        print(f'Пользователей: {len(data["users"])}; состояние: {states}. Файл: {target.name}')
    elif args.command == 'backup':
        from deploy.postgres_backup import create_dump
        if db.SQLITE:
            sys.exit('Резервная копия этим способом делается только для PostgreSQL.')
        print('Резервная копия:', create_dump(db.engine.url, folder).name)
    elif args.command == 'verify':
        if not args.backup:
            sys.exit('Укажите --backup с файлом копии.')
        result, mismatch = verify_backup(db, args.backup)
        report = {'backup': Path(args.backup).name, 'tables': result, 'mismatch': mismatch}
        (folder / f'backup-verified-{stamp()}.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
        print('Копия проверена: строки и суммы совпадают.' if not mismatch else f'РАСХОЖДЕНИЯ: {sorted(mismatch)}')
        sys.exit(1 if mismatch else 0)
    elif args.command == 'plan':
        errors, actions = check_plan(db, load_plan(args.plan))
        print(json.dumps({'errors': errors, 'create': [a['login'] for a in actions['create']], 'pending': len(actions['pending']),
                          'archive': actions['archive'], 'keep': actions['keep'], 'reassign': actions['reassign']}, ensure_ascii=False, indent=1))
        sys.exit(1 if errors else 0)
    else:
        if not args.backup:
            sys.exit('Для применения укажите --backup с проверенной резервной копией.')
        apply(db, load_plan(args.plan), folder, args.backup)


if __name__ == '__main__':
    main()
