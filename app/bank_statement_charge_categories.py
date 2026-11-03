"""Company-scoped automatic bank-charge articles; preview is SELECT-only."""
import re

from fastapi import HTTPException
from sqlalchemy import select

from .category_catalog import catalog
from .db import Category, Setting


CHARGES = {
    'fee': ('8', 'Банковские комиссии (выписки)', 'operating'),
    'interest': ('63', 'Проценты по кредитам (выписки)', 'financing'),
}
FEE_NAME = re.compile(
    r'банковск\w*\s+(?:комисси|услуг|обслуж)|банк\w*\s+(?:комисси|хизмат)|'
    r'услуг\w*\s+банк\w*|комисси\w*\s+банка|\bbank\s+(?:fee|charge|service|xizmat)', re.I)
INTEREST_NAME = re.compile(
    r'(?:кредит|займ)\w*.{0,30}(?:процент|фоиз)|(?:процент|фоиз)\w*.{0,30}(?:кредит|займ)|'
    r'(?:кредит|займ)\w*\s*%|\bloan\s+interest\b|\bkredit\w*.{0,30}foiz', re.I)


def charge_category(s, batch, kind, *, create=False):
    """Code plus meaning can reuse an active catalog row of this company only.

    The fallback has a dedicated stored identity, rather than borrowing a
    same-named or same-code article whose business meaning is unknown.
    ``create`` is reserved for the caller's confirmed posting savepoint.
    """
    code, name, activity = CHARGES[kind]
    cid = batch.company_id
    if s.info.get('company_id') != cid:
        raise HTTPException(409, 'Компания банковской статьи изменилась. Проверьте выписку.')
    rows = [c for c, retired in catalog(s, cid, include_retired=True) if not retired]
    matcher = FEE_NAME if kind == 'fee' else INTEREST_NAME
    matched = [c for c in rows if c.company_id == cid and c.type == 'outcome'
               and str(c.code or '').strip() == code and matcher.search(c.name)]
    if len(matched) == 1:
        category = matched[0]
        return {'category_id': category.id, 'category_source': f'bank_{kind}_auto',
                'category_name': category.name, 'category_code': category.code}
    key = f'company:{cid}:bank_statement_{kind}_technical_category'
    setting = s.get(Setting, key)
    if setting:
        try:
            category = next((c for c in rows if c.id == int(setting.value)), None)
        except (ValueError, TypeError):
            category = None
        if (category is None or category.company_id != cid or category.type != 'outcome'
                or category.activity != activity or not category.name.startswith(name)):
            raise HTTPException(409, 'Техническая банковская статья требует проверки администратора.')
        return {'category_id': category.id, 'category_source': f'bank_{kind}_auto',
                'category_name': category.name, 'category_code': category.code}
    names = set(s.scalars(select(Category.name).where(Category.company_id == cid)))
    chosen, suffix = name, 2
    while chosen in names:
        chosen = f'{name} ({suffix})'
        suffix += 1
    if not create:
        return {'category_id': None, 'category_source': f'bank_{kind}_auto',
                'category_name': chosen, 'category_code': None}
    category = Category(company_id=cid, name=chosen, type='outcome', activity=activity, cost_group='other')
    s.add(category)
    s.flush()
    s.add(Setting(key=key, value=str(category.id)))
    s.flush()
    return {'category_id': category.id, 'category_source': f'bank_{kind}_auto',
            'category_name': category.name, 'category_code': None}
