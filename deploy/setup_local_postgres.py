"""Create an isolated local PostgreSQL cluster; never touch an existing installation's data."""
import os,sys,secrets,subprocess
from pathlib import Path
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
BIN=Path(os.getenv('PG_BIN',r'C:\Program Files\PostgreSQL\17\data\bin'))
CLUSTER=Path(os.getenv('LOCALAPPDATA',str(ROOT/'data')))/'ZumaTreasury'/'postgres17'
PORT=55432

def run(name,*args,**kw):
    return subprocess.run([str(BIN/(name+'.exe')),*map(str,args)],check=True,**kw)

def main():
    config=ROOT/'config.local.env'
    if config.exists():
        print('config.local.env exists; refusing to overwrite credentials.');return
    if CLUSTER.exists():raise SystemExit('Cluster directory already exists; inspect it before setup.')
    CLUSTER.parent.mkdir(parents=True,exist_ok=True)
    password=secrets.token_urlsafe(32)
    pwfile=CLUSTER.parent/'init-password.tmp'
    try:
        pwfile.write_text(password,encoding='ascii')
        run('initdb','-D',CLUSTER,'-U','zuma','--pwfile',pwfile,'--auth-host=scram-sha-256','--auth-local=scram-sha-256','--encoding=UTF8','--locale=C')
    finally:pwfile.unlink(missing_ok=True)
    with (CLUSTER/'postgresql.conf').open('a',encoding='utf-8') as f:
        f.write(f"\nlisten_addresses='127.0.0.1'\nport={PORT}\n")
    run('pg_ctl','-D',CLUSTER,'-l',CLUSTER.parent/'postgres.log','start','-w')
    env={**os.environ,'PGPASSWORD':password}
    run('createdb','-h','127.0.0.1','-p',PORT,'-U','zuma','zuma',env=env)
    config.write_text(f'DATABASE_URL=postgresql+psycopg://zuma:{quote(password)}@127.0.0.1:{PORT}/zuma\nPORT=8001\nPUBLIC_ORIGIN=http://127.0.0.1:8001\nCOOKIE_SECURE=0\nALLOWED_HOSTS=127.0.0.1,localhost\nPG_BIN={BIN}\nPG_CLUSTER={CLUSTER}\nJWT_SECRET={secrets.token_urlsafe(48)}\n',encoding='utf-8')
    print('PostgreSQL initialized on localhost:55432; credentials stored in ignored config.local.env')

if __name__=='__main__':main()
