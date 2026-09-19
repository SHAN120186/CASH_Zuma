"""Explicit, transactional migration into an empty PostgreSQL database."""
import sys,os,sqlite3,json
import datetime as dt
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from settings_loader import load_config
load_config()
from app.db import *
from sqlalchemy import text,delete

def main():
    if SQLITE:raise SystemExit('Set DATABASE_URL to PostgreSQL before migration.')
    source=ROOT/'data'/'cashflow.sqlite3'
    backup=ROOT/'backups'/('before-postgres-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3')
    backup.parent.mkdir(exist_ok=True)
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(backup) as dst:src.backup(dst)
    initialize();counts={}
    with sqlite3.connect(backup) as src,engine.begin() as conn:
        src.row_factory=sqlite3.Row
        if conn.scalar(select(User.id).limit(1)):raise SystemExit('Destination is not empty; migration cancelled.')
        names={r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        roles={name:id for id,name in conn.execute(select(Role.id,Role.name))}
        for table in Base.metadata.sorted_tables:
            if table.name not in names or table.name in ('sessions','login_attempts','roles'):continue
            records=[dict(r) for r in src.execute('SELECT * FROM "'+table.name+'"')]
            conn.execute(delete(table))
            for r in records:
                r={k:v for k,v in r.items() if k in table.c}
                if table.name=='users':r['role_id']=roles[r['role']]
                if table.name=='categories' and 'type' not in r:r['type']='income' if r['name'] in ('Поступления от покупателей','Получение кредита') else 'outcome'
                for col in table.columns:
                    value=r.get(col.name)
                    if value is None:continue
                    if isinstance(col.type,DateTime):r[col.name]=dt.datetime.fromisoformat(value)
                    elif isinstance(col.type,Date):r[col.name]=dt.date.fromisoformat(value)
                    elif isinstance(col.type,Boolean):r[col.name]=bool(value)
                conn.execute(table.insert().values(**r))
            if 'id' in table.c:
                conn.execute(text("SELECT setval(pg_get_serial_sequence(:t,'id'), COALESCE((SELECT MAX(id) FROM \""+table.name+"\"),1), EXISTS(SELECT 1 FROM \""+table.name+"\"))"),{'t':table.name})
            count=conn.scalar(select(__import__('sqlalchemy').func.count()).select_from(table))
            if count!=len(records):raise RuntimeError('Row count mismatch: '+table.name)
            counts[table.name]=count
    print(json.dumps({'backup':str(backup),'verified_counts':counts},ensure_ascii=False))

if __name__=='__main__':main()
