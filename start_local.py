"""Start local PostgreSQL, API and the daily backup/report worker together."""
import os,sys,subprocess,socket,webbrowser
from pathlib import Path
from settings_loader import load_config
load_config()
ROOT=Path(__file__).resolve().parent

def main():
    cluster=os.getenv('PG_CLUSTER');binpath=os.getenv('PG_BIN')
    if cluster and binpath:
        ctl=str(Path(binpath)/'pg_ctl.exe')
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        status=subprocess.run([ctl,'-D',cluster,'status'],capture_output=True,creationflags=flags)
        if status.returncode:
            subprocess.run([ctl,'-D',cluster,'-l',str(Path(cluster).parent/'postgres.log'),'start','-w'],check=True,creationflags=flags)
    port=int(os.getenv('PORT','8001'))
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1',port))==0:
            print(f'Port {port} is already in use. Open the running site or stop it before restarting.')
            webbrowser.open(f'http://127.0.0.1:{port}');return
    log=(ROOT/'data'/'worker.log').open('a',encoding='utf-8')
    worker=subprocess.Popen([sys.executable,'worker.py'],cwd=ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        from start import main as start_server
        start_server()
    finally:
        worker.terminate();worker.wait(timeout=10);log.close()

if __name__=='__main__':main()
