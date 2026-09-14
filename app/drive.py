"""Read-only Google Drive sync. Credentials are stored ONLY on the user's server."""
import os,re
from .model_import import MAX_SIZE

def download_model():
    credentials_file=os.getenv('GOOGLE_APPLICATION_CREDENTIALS','')
    file_id=os.getenv('GOOGLE_DRIVE_FILE_ID','')
    if not credentials_file or not file_id:raise RuntimeError('Google Drive ещё не настроен. Укажите путь к серверному ключу и ID файла; не отправляйте ключ в чат.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{10,200}',file_id):raise ValueError('Некорректный ID файла Google Drive.')
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
    except ImportError as e:raise RuntimeError('Установите requirements-drive.txt на сервере.') from e
    try:
        credentials=service_account.Credentials.from_service_account_file(credentials_file,scopes=['https://www.googleapis.com/auth/drive.readonly'])
        with AuthorizedSession(credentials) as session:
            base='https://www.googleapis.com/drive/v3/files/'+file_id
            meta=session.get(base,params={'fields':'name,mimeType,size,modifiedTime','supportsAllDrives':'true'},timeout=30)
            if meta.status_code!=200:raise RuntimeError(f'Google Drive вернул {meta.status_code}. Проверьте API и доступ сервисного аккаунта к файлу.')
            info=meta.json();mime=info.get('mimeType','')
            if mime=='application/vnd.google-apps.spreadsheet':
                url=base+'/export';params={'mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
            elif mime=='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
                url=base;params={'alt':'media','supportsAllDrives':'true'}
            else:raise RuntimeError('Файл должен быть XLSX или Google Sheets со структурой FinModel.')
            if int(info.get('size',0))>MAX_SIZE:raise RuntimeError('Файл Google Drive больше 20 МБ.')
            with session.get(url,params=params,timeout=(15,60),stream=True) as r:
                if r.status_code!=200:raise RuntimeError(f'Ошибка скачивания Google Drive: HTTP {r.status_code}.')
                out=bytearray()
                for chunk in r.iter_content(65536):
                    out.extend(chunk)
                    if len(out)>MAX_SIZE:raise RuntimeError('Скачиваемый файл больше 20 МБ.')
            name=info.get('name','FinModel.xlsx')
            if not name.lower().endswith('.xlsx'):name+='.xlsx'
            return bytes(out),name,'Google Drive: '+file_id+'; '+info.get('modifiedTime','')
    except RuntimeError:raise
    except Exception as e:
        raise RuntimeError('Не удалось подключиться к Google Drive. Проверьте сеть, ключ, Drive API и права доступа. Секреты в журнал не выводятся.') from e
