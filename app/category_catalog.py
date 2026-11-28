"""Company-specific display order; retired rows retain their historical identity."""
import json
from sqlalchemy import select
from .db import Category, Setting

def catalog(s, company_id=None, include_retired=False):
    cid=s.info.get('company_id') if company_id is None else company_id
    setting=s.get(Setting,f'company:{cid}:category_catalog') if cid is not None else None
    config=json.loads(setting.value) if setting else {}
    order={int(v):i for i,v in enumerate(config.get('order',[]))}
    retired=set(config.get('retired',[]))
    q=select(Category)
    if cid is not None:q=q.where(Category.company_id==cid)
    rows=list(s.scalars(q))
    rows.sort(key=lambda c:(0 if c.type=='income' else 1,order.get(c.id,len(order)+c.id),c.id) if config else (0,c.id,c.id))
    # Служебные статьи конвертации валют ведёт только казначейство: в формах они не выбираются,
    # а в отчётах видны как исторические строки с фактом.
    return [(c,c.id in retired or c.activity=='conversion') for c in rows
            if include_retired or (c.id not in retired and c.activity!='conversion')]
