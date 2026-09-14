from __future__ import annotations
import os,sys,argparse,socket,threading,webbrowser
from settings_loader import load_config
load_config()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--lan',action='store_true');parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    if sys.version_info<(3,11):raise SystemExit('Требуется Python 3.11 или новее. Рекомендуется 3.12/3.13.')
    port=int(os.getenv('PORT','8000'));host='0.0.0.0' if args.lan else '127.0.0.1'
    ips=[]
    if args.lan:
        try:ips=socket.gethostbyname_ex(socket.gethostname())[2]
        except OSError:pass
        old=os.getenv('ALLOWED_HOSTS','127.0.0.1,localhost')
        os.environ['ALLOWED_HOSTS']=','.join(set(old.split(',')+ips))
    from manage import init
    init()
    print('\n'+'='*55)
    print(f'ОТКРЫТЬ НА КОМПЬЮТЕРЕ: http://127.0.0.1:{port}')
    if args.lan:
        for ip in ips:
            if not ip.startswith('127.'):print(f'НА ТЕЛЕФОНЕ В ТОЙ ЖЕ СЕТИ: http://{ip}:{port}')
        print('HTTP в локальной сети — только для теста. Для реальных данных используйте HTTPS/VPN.')
        print('Не открывайте порт маршрутизатора в интернет.')
    print('Не закрывайте это окно, пока работаете с сайтом. Остановка: Ctrl+C.')
    print('='*55+'\n')
    if not args.no_browser:threading.Timer(1.5,lambda:webbrowser.open(f'http://127.0.0.1:{port}')).start()
    import uvicorn
    uvicorn.run('app.main:app',host=host,port=port,workers=1,proxy_headers=False,access_log=False)
if __name__=='__main__':main()
