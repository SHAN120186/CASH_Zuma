"""Rehearse the company migration on a disposable copy of PostgreSQL.

The source database is only dumped, never initialized or updated by this script.
Financial fingerprints use category names because shared catalog IDs are split.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from settings_loader import load_config
load_config()
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


def fingerprint(conn):
    cats=dict(conn.execute('SELECT id,name FROM categories'))
    result={}
    for table in ('accounts','ledger','payment_requests','expected_receipts','budgets','cash_plans','plan_notes','users'):
        records=[r[0] for r in conn.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t ORDER BY id').format(sql.Identifier(table)))]
        for row in records:
            if row.get('category_id') is not None:
                row['category']=cats[row.pop('category_id')]
            if table=='payment_requests':row.pop('version')  # Category IDs can invalidate an open edit form.
            if table=='cash_plans':
                row['payload']={cats[int(k.split(':')[0])]+':'+k.split(':')[1]:v for k,v in json.loads(row['payload']).items()}
        result[table]=records
    result['documents']=[(id,hashlib.sha256(content).hexdigest()) for id,content in conn.execute('SELECT id,content FROM documents ORDER BY id')]
    return result


def main():
    url=make_url(os.environ['DATABASE_URL'])
    name='zuma_restore_company_'+uuid.uuid4().hex[:10]
    folder=Path(os.environ.get('BACKUP_DIR',ROOT/'backups'));folder.mkdir(parents=True,exist_ok=True)
    dump=folder/('before-company-workspaces-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'.dump')
    def tool(name):return str(Path(os.environ['PG_BIN'])/(name+('.exe' if os.name=='nt' else '')))
    env={**os.environ,'PGHOST':url.host,'PGPORT':str(url.port or 5432),'PGUSER':url.username,'PGPASSWORD':url.password,'PGDATABASE':url.database}
    def connect(db):return psycopg.connect(host=url.host,port=url.port,user=url.username,password=url.password,dbname=db,autocommit=True,connect_timeout=10)
    subprocess.run([tool('pg_dump'),'--format=custom','--no-owner','--file',str(dump)],env=env,check=True,capture_output=True)
    with connect('postgres') as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            subprocess.run([tool('pg_restore'),'--no-owner','--exit-on-error','--dbname',name,str(dump)],env=env,check=True,capture_output=True)
            with connect(name) as conn:before=fingerprint(conn)
            with tempfile.TemporaryDirectory() as data:
                test_env={**os.environ,'DATABASE_URL':url.set(database=name).render_as_string(hide_password=False),'DATA_DIR':data}
                for iteration in range(2):
                    subprocess.run([sys.executable,'-c','from app.db import initialize; initialize()'],cwd=ROOT,env=test_env,check=True,capture_output=True)
                    with connect(name) as conn:
                        after=fingerprint(conn)
                        if after!=before:
                            for table in before:
                                if before[table]!=after[table]:
                                    print('Mismatch:',table,'row counts',len(before[table]),len(after[table]))
                                    for old,new in zip(before[table],after[table]):
                                        if old!=new and isinstance(old,dict):print('Changed fields:',[k for k in set(old)|set(new) if old.get(k)!=new.get(k)]);break
                            raise AssertionError('Financial records changed during migration')
                        count=conn.execute('SELECT count(*) FROM categories').fetchone()[0]
                        if iteration:assert count==category_count,'Migration is not idempotent'
                        category_count=count
                        assert conn.execute('SELECT count(*) FROM categories WHERE company_id IS NULL').fetchone()[0]==0
            print('Company migration verified twice on a disposable PostgreSQL copy; balances, transactions, requests, budgets, plans, users and document contents preserved.')
            print('Source backup:',dump)
        finally:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


if __name__=='__main__':main()
