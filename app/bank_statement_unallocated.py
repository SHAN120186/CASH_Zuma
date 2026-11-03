"""Company-specific temporary classification for unallocated bank receipts."""
from fastapi import HTTPException
from sqlalchemy import select
from .db import Category, Setting

ACTIVITY = 'unclassified'
NAMES = {'in': 'Не распределено — поступления из банка', 'out': 'Не распределено — списания из банка'}

def category_id(s, kind='in'):
    if kind not in NAMES:
        raise ValueError('Invalid unallocated direction')
    cid = s.info.get('company_id')
    if cid is None:
        raise HTTPException(409, 'Сначала выберите компанию.')
    setting = s.get(Setting, f"company:{cid}:bank_unallocated_{'income' if kind == 'in' else 'outgoing'}")
    if not setting:
        return None
    try:
        category = s.get(Category, int(setting.value))
    except (TypeError, ValueError):
        raise HTTPException(409, 'Настройка нераспределённых поступлений повреждена.') from None
    if not category or category.company_id != cid or category.type != ('income' if kind == 'in' else 'outcome') or category.activity != ACTIVITY:
        raise HTTPException(409, 'Статья нераспределённых поступлений требует проверки администратора.')
    return category.id

def ensure_category(s, kind='in'):
    """Create only during an explicitly confirmed statement transaction."""
    existing = category_id(s, kind)
    if existing is not None:
        return existing
    cid = s.info['company_id']
    names = set(s.scalars(select(Category.name).where(Category.company_id == cid)))
    name = NAMES[kind]
    number = 2
    while name in names:
        name = f'{NAMES[kind]} ({number})'
        number += 1
    category = Category(company_id=cid, name=name, type='income' if kind == 'in' else 'outcome', activity=ACTIVITY, cost_group='other')
    s.add(category)
    s.flush()
    s.add(Setting(key=f"company:{cid}:bank_unallocated_{'income' if kind == 'in' else 'outgoing'}", value=str(category.id)))
    s.flush()
    return category.id
