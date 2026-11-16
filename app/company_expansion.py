"""Add empty company workspaces without copying money or staff assignments."""
import json
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .db import Category, Company, Setting

NEW_COMPANIES = ('SAMO', 'FAZO')
MARKER = 'company_expansion_samo_fazo_v1'


def migrate_company_expansion(engine):
    with Session(engine) as s, s.begin():
        if engine.dialect.name != 'sqlite':
            s.execute(text('SELECT pg_advisory_xact_lock(7312801)'))
        if s.get(Setting, MARKER):
            return
        source = s.scalar(select(Company).where(Company.code == 'ZUMA'))
        if source is None:
            raise RuntimeError('ZUMA workspace is required before company expansion')
        categories = list(s.scalars(select(Category).where(Category.company_id == source.id).order_by(Category.id)))
        fields = [c.name for c in Category.__table__.columns
                  if c.name not in ('id', 'company_id', 'director_policy_changed_by', 'director_limit_changed_by')]
        source_catalog = s.get(Setting, f'company:{source.id}:category_catalog')
        source_departments = s.get(Setting, f'company:{source.id}:departments')
        for code in NEW_COMPANIES:
            # An existing company is owned by its administrator. Never reset it.
            if s.scalar(select(Company.id).where(Company.code == code)) is not None:
                continue
            company = Company(code=code, name=code, active=True)
            s.add(company)
            s.flush()
            copies = []
            for original in categories:
                copied = Category(company_id=company.id, **{f: getattr(original, f) for f in fields})
                copies.append(copied)
            s.add_all(copies)
            s.flush()
            ids = {original.id: copied.id for original, copied in zip(categories, copies)}
            if source_catalog:
                config = json.loads(source_catalog.value)
                config = {key: [ids[i] for i in config.get(key, []) if i in ids]
                          for key in ('order', 'retired')}
                s.add(Setting(key=f'company:{company.id}:category_catalog', value=json.dumps(config)))
            if source_departments:
                s.add(Setting(key=f'company:{company.id}:departments', value=source_departments.value))
        s.add(Setting(key=MARKER, value='1'))
