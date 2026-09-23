"""Daily PostgreSQL backups and explicitly enabled email reports. Run as a service."""
import os,sys,time,subprocess,smtplib,ssl,json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from email.message import EmailMessage
from settings_loader import load_config
if __name__=='__main__':load_config()
from app.db import engine,SQLITE,ROOT,DATA,ReportSchedule,Audit,User,unit,initialize,select
from app.erp import render_report

def pg_tool(name):
    return str(Path(os.environ['PG_BIN'])/(name+('.exe' if os.name=='nt' else ''))) if os.getenv('PG_BIN') else name

def pg_environment():
    url=engine.url
    return {**os.environ,'PGHOST':url.host or 'localhost','PGPORT':str(url.port or 5432),'PGUSER':url.username or '', 'PGPASSWORD':url.password or '', 'PGDATABASE':url.database or ''}

def backup_postgres():
    if SQLITE:raise RuntimeError('PostgreSQL backup requires DATABASE_URL for PostgreSQL')
    folder=Path(os.getenv('BACKUP_DIR',str(ROOT/'backups')));folder.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    target=folder/f'zuma-{stamp}.dump';pending=target.with_suffix('.partial')
    try:
        subprocess.run([pg_tool('pg_dump'),'--format=custom','--no-owner','--file',str(pending)],env=pg_environment(),check=True,capture_output=True,timeout=300)
        subprocess.run([pg_tool('pg_restore'),'--list',str(pending)],check=True,capture_output=True,timeout=30)
        pending.replace(target)
    except Exception:
        pending.unlink(missing_ok=True);raise
    # Keep files until an administrator chooses retention; never delete backups implicitly.
    with unit(True) as s:s.add(Audit(action='Резервная копия PostgreSQL',entity='backup',detail=target.name))
    return target

def send_report(recipient,attachments):
    host=os.getenv('SMTP_HOST','');sender=os.getenv('SMTP_FROM','')
    if not host or not sender:raise RuntimeError('SMTP is not configured')
    msg=EmailMessage();msg['From']=sender;msg['To']=recipient;msg['Subject']='ZUMA · Ежедневный отчёт Cash Flow'
    msg.set_content('Во вложении отчёт по фактическим операциям. Валюты не конвертируются.')
    for name,data,mime in attachments:
        major,minor=mime.split('/',1);msg.add_attachment(data,maintype=major,subtype=minor,filename=name)
    implicit=os.getenv('SMTP_TLS_MODE','starttls')=='ssl'
    port=int(os.getenv('SMTP_PORT','465' if implicit else '587'))
    if implicit:client=smtplib.SMTP_SSL(host,port,timeout=30,context=ssl.create_default_context())
    else:client=smtplib.SMTP(host,port,timeout=30)
    with client:
        if not implicit:client.starttls(context=ssl.create_default_context())
        if os.getenv('SMTP_USER'):client.login(os.environ['SMTP_USER'],os.environ.get('SMTP_PASSWORD',''))
        client.send_message(msg)

def report_tick():
    current=datetime.now(timezone(timedelta(hours=5)));day=current.date()
    with unit() as s:ids=list(s.scalars(select(ReportSchedule.id).where(ReportSchedule.enabled==True)))
    for id in ids:
        with unit(True) as s:
            r=s.get(ReportSchedule,id)
            if not r or not r.enabled or r.hour>current.hour or r.last_attempt==day:continue
            owner=s.get(User,r.created_by)
            if not owner or not owner.active or owner.role!='admin':
                r.enabled=False;r.last_error='Создатель расписания больше не является активным администратором.';continue
            r.last_attempt=day;r.last_error='Отправка начата; если статус не изменится, проверьте журнал SMTP.'
            recipient,currency=r.recipient,r.currency
        try:
            with unit() as s:
                attachments=[(f'cashflow-{day.year}-{currency}.xlsx',render_report(s,day.year,currency,'xlsx',company_id=r.company_id),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),(f'cashflow-{day.year}-{currency}.pdf',render_report(s,day.year,currency,'pdf',company_id=r.company_id),'application/pdf')]
            send_report(recipient,attachments)
            with unit(True) as s:
                r=s.get(ReportSchedule,id)
                if r:r.last_sent=day;r.last_error=''
                s.add(Audit(action='Отчёт отправлен',entity='schedule',entity_id=str(id),detail=recipient))
        except Exception as exc:
            with unit(True) as s:
                r=s.get(ReportSchedule,id)
                if r:r.last_error='Ошибка отправки: '+type(exc).__name__+'. Проверьте настройки SMTP. Автоповтор сегодня отключён.'
                s.add(Audit(action='Ошибка рассылки',entity='schedule',entity_id=str(id),detail=type(exc).__name__))

def tick():
    if not SQLITE:
        marker=DATA/'last-backup-date.txt';day=datetime.now(timezone.utc).date().isoformat()
        if not marker.exists() or marker.read_text().strip()!=day:
            backup_postgres();marker.write_text(day)
    report_tick()

if __name__=='__main__':
    initialize()
    if '--backup' in sys.argv:print(backup_postgres())
    elif '--once' in sys.argv:tick()
    else:
        while True:
            try:tick()
            except Exception as exc:print('Worker error:',type(exc).__name__,flush=True)
            time.sleep(60)
