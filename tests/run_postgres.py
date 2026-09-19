"""Run the suite in a uniquely named disposable PostgreSQL database."""
import os,sys,uuid,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from settings_loader import load_config
load_config()
from sqlalchemy.engine import make_url
import psycopg
from psycopg import sql
url=make_url(os.environ['DATABASE_URL'])
name='zuma_test_'+uuid.uuid4().hex[:12]
with psycopg.connect(host=url.host,port=url.port,user=url.username,password=url.password,dbname='postgres',autocommit=True) as admin:
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        env={**os.environ,'TEST_DATABASE_URL':url.set(database=name).render_as_string(hide_password=False)}
        result=subprocess.run([sys.executable,str(ROOT/'tests'/'test_site.py')],env=env,cwd=ROOT)
    finally:
        admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
sys.exit(result.returncode)
