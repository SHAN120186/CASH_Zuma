"""Temporary HTTPS preview of committed snapshots; shared local application data."""
from __future__ import annotations
import argparse, io, json, os, re, shutil, socket, subprocess, sys, time, urllib.request, zipfile
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime' / 'preview'
STATUS, REQUEST, STOP = (RUNTIME/name for name in ('status.json', 'update.json', 'stop'))
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode().strip()

def published_revision():
    if git('status', '--porcelain'):
        raise RuntimeError('Commit and push the completed changes before publishing.')
    if git('rev-list', '--count', '@{upstream}..HEAD') != '0':
        raise RuntimeError('Push this commit to the upstream branch first.')
    return git('rev-parse', 'HEAD')

def extract_snapshot(raw, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for entry in archive.infolist():
            if not (destination/entry.filename).resolve().is_relative_to(destination):
                raise ValueError('Archive path is outside the release directory.')
        archive.extractall(destination)

def snapshot(revision):
    if not re.fullmatch(r'[0-9a-f]{40,64}', revision):
        raise ValueError('Expected a full Git commit hash.')
    directory = RUNTIME/'releases'/revision
    if not (directory/'.ready').exists():
        raw = subprocess.check_output(['git', 'archive', '--format=zip', revision], cwd=ROOT)
        extract_snapshot(raw, directory)
        for required in ('release.json', 'app/main.py', 'app/static/erp/index.html'):
            if not (directory/required).is_file():
                raise RuntimeError('The committed release is missing '+required)
        (directory/'.ready').touch()
    return directory

def preview_environment(url):
    host = urlparse(url).hostname
    if not re.fullmatch(r'[a-z0-9-]+\.trycloudflare\.com', host or '') or not url.startswith('https://'):
        raise ValueError('Expected an HTTPS Quick Tunnel address.')
    env = os.environ.copy()
    env.update(PUBLIC_ORIGIN=url, ALLOWED_HOSTS=f'127.0.0.1,localhost,{host}', COOKIE_SECURE='1',
        DATA_DIR=str(Path(env.get('DATA_DIR', str(ROOT/'data'))).resolve()), PYTHONUTF8='1', PYTHONUNBUFFERED='1')
    return env

def get_json(url):
    with urllib.request.urlopen(url, timeout=3) as response:return json.load(response)

def wait_ready(process, port):
    deadline = time.monotonic()+25
    while time.monotonic()<deadline:
        if process.poll() is not None:raise RuntimeError('Preview API stopped. See .runtime/preview/api.log.')
        try:
            if get_json(f'http://127.0.0.1:{port}/health').get('status')=='ok':return
        except (OSError, ValueError):pass
        time.sleep(.5)
    raise RuntimeError('Preview API did not become healthy.')

def stop_child(process):
    if process and process.poll() is None:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)

def write_status(state):
    tmp = STATUS.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=2), encoding='utf-8');tmp.replace(STATUS)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8002)
    parser.add_argument('--update', action='store_true')
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if args.status:print(STATUS.read_text() if STATUS.exists() else 'No preview status.');return
    if args.update or args.stop:
        state = json.loads(STATUS.read_text()) if STATUS.exists() else {}
        if state.get('state')!='running':raise RuntimeError('No running preview. Use START_WEB_SERVER.bat first.')
        get_json(f'http://127.0.0.1:{state["port"]}/health')
        if args.stop:STOP.touch();print('Preview stop requested.');return
        revision = published_revision()
        tmp = REQUEST.with_suffix('.tmp')
        tmp.write_text(json.dumps({'revision':revision}), encoding='utf-8');tmp.replace(REQUEST)
        print('Publishing commit '+revision, flush=True)
        deadline = time.monotonic()+50
        while time.monotonic()<deadline:
            current = json.loads(STATUS.read_text())
            if current.get('revision')==revision and not REQUEST.exists():print('Published: '+current['url']);return
            if current.get('error') and not REQUEST.exists():raise RuntimeError(current['error'])
            time.sleep(.5)
        raise RuntimeError('Publish is still pending; inspect --status.')
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1', args.port))==0:
            raise RuntimeError(f'Port {args.port} is occupied. Use --update or choose a free port.')
    revision = published_revision()
    directory = snapshot(revision)
    cloudflared = shutil.which('cloudflared')
    if not cloudflared:raise RuntimeError('Install cloudflared from Cloudflare first.')
    sys.path.insert(0, str(ROOT))
    from settings_loader import load_config
    load_config()
    STOP.unlink(missing_ok=True);REQUEST.unlink(missing_ok=True)
    tunnel = api = None
    state = {'state':'starting', 'supervisor_pid':os.getpid(), 'port':args.port, 'revision':revision}
    write_status(state)
    with (RUNTIME/'tunnel.log').open('w', encoding='utf-8') as tunnel_log, (RUNTIME/'api.log').open('a', encoding='utf-8') as api_log:
        try:
            tunnel = subprocess.Popen([cloudflared, 'tunnel', '--no-autoupdate', '--url', f'http://127.0.0.1:{args.port}'],
                stdout=tunnel_log, stderr=tunnel_log, creationflags=FLAGS)
            url = None
            deadline = time.monotonic()+45
            while time.monotonic()<deadline:
                if tunnel.poll() is not None:break
                found = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', (RUNTIME/'tunnel.log').read_text(encoding='utf-8', errors='replace'))
                if found:url=found.group();break
                time.sleep(.5)
            if not url:raise RuntimeError('Could not obtain a tunnel URL. See .runtime/preview/tunnel.log.')
            env = preview_environment(url)
            def start_api(folder):
                child = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1',
                    '--port', str(args.port), '--proxy-headers', '--forwarded-allow-ips', '127.0.0.1', '--no-access-log'],
                    cwd=folder, env={**env, 'PYTHONPATH':str(folder)}, stdout=api_log, stderr=api_log, creationflags=FLAGS)
                try:wait_ready(child, args.port)
                except Exception:stop_child(child);raise
                return child
            api = start_api(directory)
            state.update(state='running', url=url, api_pid=api.pid, tunnel_pid=tunnel.pid)
            write_status(state)
            (ROOT/'PUBLIC_URL.txt').write_text(url+'\n', encoding='utf-8')
            print('PREVIEW_URL='+url, flush=True)
            print('VERSION='+json.loads((directory/'release.json').read_text())['version'], flush=True)
            while not STOP.exists():
                if tunnel.poll() is not None or api.poll() is not None:raise RuntimeError('A preview process stopped. Restart START_WEB_SERVER.bat.')
                if REQUEST.exists():
                    try:
                        next_revision = json.loads(REQUEST.read_text())['revision']
                        next_directory = snapshot(next_revision)
                        if next_revision!=revision:
                            stop_child(api)
                            try:api=start_api(next_directory)
                            except Exception:
                                api=start_api(directory)
                                raise RuntimeError('New release failed; previous code restored. Database changes are not rolled back.')
                            revision, directory = next_revision, next_directory
                        state.update(revision=revision, api_pid=api.pid);state.pop('error', None)
                    except Exception as exc:state['error']=str(exc)
                    write_status(state);REQUEST.unlink(missing_ok=True)
                time.sleep(.5)
        finally:
            stop_child(api);stop_child(tunnel)
            state['state']='stopped';write_status(state);STOP.unlink(missing_ok=True)

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:pass
    except Exception as exc:print('ERROR: '+str(exc), file=sys.stderr);sys.exit(1)
