"""Restore a real dump into an isolated database and verify financial row counts and sums."""
import sys,uuid,subprocess,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from settings_loader import load_config
load_config()
import worker
import psycopg
from psycopg import sql
url=worker.engine.url;name='zuma_restore_'+uuid.uuid4().hex[:12]
dump=worker.backup_postgres()
def connect(db):return psycopg.connect(host=url.host,port=url.port,user=url.username,password=url.password,dbname=db,autocommit=True,connect_timeout=10)
with connect('postgres') as admin:
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        env={**worker.pg_environment(),'PGDATABASE':name}
        subprocess.run([worker.pg_tool('pg_restore'),'--no-owner','--exit-on-error','--dbname',name,str(dump)],env=env,check=True,capture_output=True)
        with connect(url.database) as source,connect(name) as restored:
            for table in ('users','company_users','accounts','ledger','categories','budgets','payment_requests','expected_receipts','model_versions','documents','companies','cash_plans','plan_notes','plan_import_batches','import_batches','settings'):
                query=sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))
                assert source.execute(query).fetchone()==restored.execute(query).fetchone(),table
            def documents_digest(connection):
                checksum=hashlib.sha256()
                for ident,content in connection.execute('SELECT id,content FROM documents ORDER BY id'):
                    checksum.update(str(ident).encode());checksum.update(content)
                return checksum.hexdigest()
            assert documents_digest(source)==documents_digest(restored),'document contents'
            for table in ('cash_plans','plan_notes'):
                query=sql.SQL('SELECT to_jsonb(t) FROM {} t ORDER BY id').format(sql.Identifier(table))
                assert source.execute(query).fetchall()==restored.execute(query).fetchall(),table
            for table,field in [('accounts','opening'),('ledger','amount')]:
                query=sql.SQL('SELECT COALESCE(SUM({}),0) FROM {}').format(sql.Identifier(field),sql.Identifier(table))
                assert source.execute(query).fetchone()==restored.execute(query).fetchone(),table
        print('RESTORE VERIFIED: financial counts and totals match; disposable restore database removed.')
    finally:admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
