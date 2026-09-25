"""PostgreSQL archive creation without application writes or exposed credentials."""
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path


def pg_tool(name, environ=None):
    env = os.environ if environ is None else environ
    # Backup clients may be newer than the local server. Never replace PG_BIN,
    # which is also used to start the existing local database cluster.
    folder = env.get('PG_BACKUP_BIN') or env.get('PG_BIN')
    return str(Path(folder) / (name + ('.exe' if os.name == 'nt' else ''))) if folder else name


def pg_environment(url, environ=None):
    env = dict(os.environ if environ is None else environ)
    env.update(PGHOST=url.host or 'localhost', PGPORT=str(url.port or 5432),
               PGUSER=url.username or '', PGPASSWORD=url.password or '',
               PGDATABASE=url.database or '')
    # libpq subprocesses do not inherit SQLAlchemy URL parameters automatically.
    # Preserve TLS verification and channel binding instead of falling back to
    # libpq's default sslmode=prefer.
    for parameter, variable in {
        'sslmode': 'PGSSLMODE', 'sslrootcert': 'PGSSLROOTCERT',
        'sslcert': 'PGSSLCERT', 'sslkey': 'PGSSLKEY', 'sslcrl': 'PGSSLCRL',
        'sslcrldir': 'PGSSLCRLDIR', 'channel_binding': 'PGCHANNELBINDING',
        'connect_timeout': 'PGCONNECT_TIMEOUT', 'application_name': 'PGAPPNAME',
        'options': 'PGOPTIONS', 'target_session_attrs': 'PGTARGETSESSIONATTRS',
        'gssencmode': 'PGGSSENCMODE', 'client_encoding': 'PGCLIENTENCODING',
    }.items():
        if parameter in url.query:
            value = url.query[parameter]
            if not isinstance(value, str):
                raise ValueError('Repeated PostgreSQL connection parameters are unsupported.')
            env[variable] = value
    env.setdefault('PGCONNECT_TIMEOUT', '15')
    return env


def create_dump(url, folder, environ=None):
    """Write a uniquely named archive; publish it only after pg_restore --list."""
    env = pg_environment(url, environ)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    target = folder / f'zuma-{stamp}-{uuid.uuid4().hex[:12]}.dump'
    pending = target.with_suffix('.partial')
    try:
        with pending.open('xb'):
            pass
        subprocess.run([pg_tool('pg_dump', env), '--format=custom', '--no-owner',
                        '--file', str(pending)], env=env, check=True,
                       capture_output=True, timeout=300)
        subprocess.run([pg_tool('pg_restore', env), '--list', str(pending)],
                       env=env, check=True, capture_output=True, timeout=30)
        pending.replace(target)
    except subprocess.CalledProcessError as exc:
        pending.unlink(missing_ok=True)
        if b'server version mismatch' in (exc.stderr or b''):
            raise RuntimeError('Backup client is older than the PostgreSQL server. '
                               'Set PG_BACKUP_BIN to a compatible client; keep PG_BIN '
                               'unchanged for the local database.') from None
        raise
    except Exception:
        pending.unlink(missing_ok=True)
        raise
    return target
