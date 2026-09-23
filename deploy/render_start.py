"""Render entry point: persistent PostgreSQL is mandatory; no local DB fallback."""
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url


def configure(env):
    raw = env.get('DATABASE_URL', '')
    try:
        url = make_url(raw)
        if url.get_backend_name() not in ('postgres', 'postgresql'):
            raise ValueError
        if not all((url.host, url.username, url.password, url.database)):
            raise ValueError
        if url.host in ('localhost', '127.0.0.1', '::1'):
            raise ValueError
        query = dict(url.query)
        if query.get('sslmode') not in ('require', 'verify-ca', 'verify-full'):
            raise ValueError
    except Exception:
        raise ValueError('DATABASE_URL must be an external PostgreSQL URL with TLS; use the Neon connection string.') from None
    if len(env.get('JWT_SECRET', '')) < 32:
        raise ValueError('Set a persistent JWT_SECRET of at least 32 characters.')
    host = env.get('RENDER_EXTERNAL_HOSTNAME', '')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*\.onrender\.com', host):
        raise ValueError('RENDER_EXTERNAL_HOSTNAME must be the assigned onrender.com hostname.')
    origin = env.get('PUBLIC_ORIGIN', '').rstrip('/') or 'https://' + host
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError('PUBLIC_ORIGIN must be a plain HTTPS origin.')
    if not re.fullmatch(r'[a-z0-9.-]+', parsed.hostname):
        raise ValueError('PUBLIC_ORIGIN contains an invalid hostname.')
    try:
        port = int(env.get('PORT', '10000'))
        if not 1024 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError('PORT must be between 1024 and 65535.') from None
    env['DATABASE_URL'] = url.set(drivername='postgresql+psycopg').render_as_string(hide_password=False)
    env['COOKIE_SECURE'] = '1'
    env['PUBLIC_ORIGIN'] = origin
    env['ALLOWED_HOSTS'] = ','.join(dict.fromkeys((host, parsed.hostname, '127.0.0.1', 'localhost')))
    return port


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        port = configure(os.environ)
        from app.db import initialize, unit, User, select
        initialize()
        with unit() as session:
            if not session.scalar(select(User.id).where(User.active == True, User.role == 'admin').limit(1)):
                raise ValueError('Restore the existing database with its administrator before publishing. No default user is created.')
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print('Database initialization failed. Check Neon availability and DATABASE_URL in Render settings.', file=sys.stderr)
        return 1
    import uvicorn
    # Do not trust arbitrary X-Forwarded-For. Render terminates HTTPS; the exact
    # public origin and Secure cookies are set above independently of proxy headers.
    uvicorn.run('app.main:app', host='0.0.0.0', port=port, workers=1,
                proxy_headers=False, access_log=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
