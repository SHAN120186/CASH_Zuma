"""Atomic, guarded replacement of one company's counterparty directory.

This maintenance endpoint never touches requests, receipts, ledger entries or
financial amounts. All writers use unit(True)'s database-wide transaction guard.
"""
import hashlib
import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from .db import Counterparty, unit
from .security import session_user
from .services import log

router = APIRouter()


class ReplaceRow(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=160)
    inn: str = Field(default='', max_length=20, pattern=r'^\d*$')
    note: str = Field(default='', max_length=240)


class Replacement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    rows: list[ReplaceRow] = Field(min_length=1, max_length=5000)

    @model_validator(mode='after')
    def unique_inn(self):
        inns = [row.inn for row in self.rows if row.inn]
        if len(inns) != len(set(inns)):
            raise ValueError('Повторяющийся ИНН в списке.')
        return self


def snapshot(rows):
    """Canonical hash includes identity and all mutable attributes, order-independent."""
    data = [{'id': c.id, 'name': c.name, 'inn': c.inn, 'note': c.note} for c in rows]
    data.sort(key=lambda r: r['id'])
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def admin(s, request):
    user, _ = session_user(s, request, 'counterparties')
    if user.role != 'admin' or user.username != 'holding.admin':
        raise HTTPException(403, 'Замена справочника доступна администратору холдинга.')
    return user


@router.get('/api/counterparties/replacement-snapshot')
def replacement_snapshot(request: Request):
    with unit() as s:
        admin(s, request)
        rows = list(s.scalars(select(Counterparty).order_by(Counterparty.id)))
        return {'count': len(rows), 'sha256': snapshot(rows),
                'company_id': s.info['company_id']}


@router.post('/api/counterparties/replace')
def replace_counterparties(data: Replacement, request: Request):
    with unit(True) as s:
        user = admin(s, request)
        old = list(s.scalars(select(Counterparty).order_by(Counterparty.id)))
        before = snapshot(old)
        if before != data.expected_sha256:
            raise HTTPException(409, 'Справочник изменился. Обновите резервную копию и повторите проверку.')
        for row in old:
            s.delete(row)
        # Flush deletes before inserting to avoid collisions with previous names.
        s.flush()
        for item in data.rows:
            s.add(Counterparty(**item.model_dump()))
        s.flush()
        new = list(s.scalars(select(Counterparty).order_by(Counterparty.id)))
        after = snapshot(new)
        detail = json.dumps({'removed': len(old), 'added': len(new),
                             'before_sha256': before, 'after_sha256': after},
                            ensure_ascii=False, separators=(',', ':'))
        log(s, user, 'Заменён справочник контрагентов', 'counterparty', 'replace', detail)
        return {'removed': len(old), 'added': len(new), 'sha256': after,
                'company_id': s.info['company_id']}
