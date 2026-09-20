"""Manual desktop launcher: start local services once and open the current public URL."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime'
STATUS = RUNTIME / 'preview' / 'status.json'
sys.path.insert(0, str(ROOT))
from settings_loader import load_config
from deploy.preview import preview_environment


def healthy(port):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/health', timeout=2) as response:
            return json.load(response).get('status') == 'ok'
    except (OSError, ValueError):
        return False


def occupied(port):
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def running_url():
    try:
        state = json.loads(STATUS.read_text(encoding='utf-8'))
        if state.get('state') != 'running' or state.get('port') != 8002:
            return None
        url = state['url']
        preview_environment(url)  # Only open a URL issued by the managed tunnel.
        return url if healthy(8002) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def launch(script, args=(), env=None):
    label = Path(script).stem
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0
    with (RUNTIME / f'{label}-launch.out.log').open('ab') as out, (RUNTIME / f'{label}-launch.err.log').open('ab') as err:
        return subprocess.Popen(
            [sys.executable, str(ROOT / script), *args], cwd=ROOT,
            env=env or os.environ.copy(), stdin=subprocess.DEVNULL,
            stdout=out, stderr=err, creationflags=flags,
            start_new_session=os.name != 'nt',
        )


def ensure_database():
    cluster, binpath = os.getenv('PG_CLUSTER'), os.getenv('PG_BIN')
    if cluster and binpath:
        ctl = str(Path(binpath) / ('pg_ctl.exe' if os.name == 'nt' else 'pg_ctl'))
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        status = subprocess.run([ctl, '-D', cluster, 'status'], capture_output=True, creationflags=flags, timeout=10)
        if status.returncode:
            print('Запускаю PostgreSQL…', flush=True)
            result = subprocess.run([ctl, '-D', cluster, '-l', str(Path(cluster).parent / 'postgres.log'),
                                     'start', '-w', '-t', '30'], capture_output=True, creationflags=flags, timeout=40)
            if result.returncode:
                raise RuntimeError('PostgreSQL не запустился. Проверьте postgres.log рядом с каталогом базы.')
    try:
        from app.db import engine
        with engine.connect() as connection:
            connection.exec_driver_sql('SELECT 1')
    except Exception:
        raise RuntimeError('Нет подключения к рабочей базе. Существующие данные не изменены.') from None


def await_ready(check, process, timeout, message):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        result = check()
        if result:
            return result
        if process is not None and process.poll() is not None:
            raise RuntimeError(message)
        time.sleep(.5)
    raise RuntimeError(message)


@contextmanager
def launcher_lock():
    # OS-managed lock is released even if the launcher is closed or the PC restarts.
    path = RUNTIME / 'open-site.lock'
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Запуск уже выполняется. Дождитесь открытия сайта.') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def ensure_site():
    if not (ROOT / 'config.local.env').exists():
        raise RuntimeError('Не найдены настройки существующей базы. Запуск остановлен, чтобы не создать пустую базу.')
    load_config()
    ensure_database()
    if not healthy(8001):
        if occupied(8001):
            raise RuntimeError('Локальный порт занят, но сайт не отвечает. См. журналы в .runtime.')
        print('Запускаю базу данных и локальный сайт…', flush=True)
        env = {**os.environ, 'PORT': '8001', 'PUBLIC_ORIGIN': 'http://127.0.0.1:8001',
               'COOKIE_SECURE': '0', 'ALLOWED_HOSTS': '127.0.0.1,localhost',
               'PYTHONUTF8': '1', 'PYTHONUNBUFFERED': '1'}
        process = launch('start_local.py', ('--no-browser',), env)
        await_ready(lambda: healthy(8001), process, 45,
                    'Локальный сайт не запустился. См. .runtime/start_local-launch.err.log.')
    url = running_url()
    if url:
        return url
    if occupied(8002):
        # Allow an independently started supervisor to finish without duplicating it.
        return await_ready(running_url, None, 20,
                           'Туннель ещё не готов. См. .runtime/preview/tunnel.log.')
    print('Получаю ссылку для доступа через интернет…', flush=True)
    process = launch('deploy/preview.py')
    return await_ready(running_url, process, 80,
                       'Туннель не запустился. Проверьте интернет и .runtime/preview-launch.err.log.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    try:
        with launcher_lock():
            url = ensure_site()
            (ROOT / 'PUBLIC_URL.txt').write_text(url + '\n', encoding='utf-8')
            print('Сайт запущен. Текущая ссылка: ' + url, flush=True)
            print('Компьютер должен оставаться включённым. Новая ссылка сохранена в PUBLIC_URL.txt.', flush=True)
            if not args.no_browser:
                webbrowser.open(url)
        return 0
    except Exception as exc:
        print('Не удалось открыть сайт: ' + str(exc), file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
