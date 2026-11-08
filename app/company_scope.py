"""Company context for every authenticated business transaction.

Background maintenance sessions are intentionally unscoped. HTTP sessions are
scoped by session_user after authentication; queries and object writes then share
the same boundary, including imports, downloads and aggregate subqueries.
"""
import json
import re
from fastapi import HTTPException
from sqlalchemy import event, select, inspect, text, or_
from sqlalchemy.orm import Session, with_loader_criteria
from sqlalchemy.schema import CreateTable
from .db import (Company, CompanyUser, User, Account, Category, Counterparty,
                 CashPlan, PlanNote, PaymentRequest, Receipt, Ledger, Budget,
                 Document, ImportBatch, PlanImportBatch, BudgetImportBatch, Audit, ModelVersion,
                 ReportSchedule, Setting, RequestDocument, Delegation, ReportArchive, ReportArchiveFile,
                 BusinessProject, BusinessSourceFile, BusinessGeneration, FinancialStatementSource, IfrsRatingSource,
                 CashFlowOsvSource, CashFlowOsvPreview, CashFunding, CashFundingPaymentGroup, CashFundingPaymentItem)
from .bank_statement_models import BankStatementBatch, BankStatementLine

# Вложения заявок принадлежат компании через свою заявку (criteria ниже), а не собственным полем.
DIRECT = (Account, Category, Counterparty, CashPlan, PlanNote, PlanImportBatch, BudgetImportBatch,
          ImportBatch, Audit, ModelVersion, ReportSchedule, Delegation, ReportArchive, ReportArchiveFile,
          BusinessProject, BusinessSourceFile, BusinessGeneration, FinancialStatementSource, IfrsRatingSource,
          CashFlowOsvSource, CashFlowOsvPreview, BankStatementBatch, BankStatementLine, CashFunding,
          CashFundingPaymentGroup, CashFundingPaymentItem)


def active_delegations(s, user_id, company_id=None):
    """Действующие замещения (ВрИО): не отозваны, сегодня в пределах срока и с сохранившимся
    основанием (access.delegation_in_force: заместитель допущен в компанию, заменяемый активен и
    держит эту роль). Тот же критерий применяет API бота."""
    from .clock import today
    from .access import delegation_in_force
    day = today()
    q = select(Delegation).where(Delegation.user_id == user_id, Delegation.revoked_at.is_(None),
                                 Delegation.starts_on <= day, Delegation.ends_on >= day)
    if company_id is not None:
        q = q.where(Delegation.company_id == company_id)
    return [d for d in s.scalars(q.execution_options(company_unscoped=True)) if delegation_in_force(s, d, day)]


SERVICE_CODE = 'UNASSIGNED'


def available_companies(s, user):
    """Единственный источник допустимых компаний: список, заголовок и параметр запроса."""
    q = select(Company).where(Company.active.is_(True))
    if user.role != 'admin':
        # Служебное пространство хранит исторические записи без подтверждённого владельца.
        # Интерфейс его скрывает; сервер тоже не принимает его как контекст сотрудника,
        # даже если прежняя связь CompanyUser сохранилась после миграции.
        q = q.where(Company.code != SERVICE_CODE)
        if user.role != 'founder':
            # Учредитель видит все компании холдинга. Сотрудник — ровно одну: связь без
            # действующей роли доступа не даёт, а назначения в нескольких компаниях
            # приостанавливают доступ до явного решения администратора.
            from .access import staff_company_ids
            q = q.where(Company.id.in_(staff_company_ids(s, user)))
    return list(s.scalars(q.order_by(Company.code == 'UNASSIGNED', Company.name)))


def activate(s, request, user):
    if request.url.path in ('/api/me', '/api/logout', '/api/password', '/api/companies',
                            '/api/telegram', '/api/telegram/code') or request.url.path.startswith('/api/admin/'):
        # Центр администрирования работает с реестром холдинга, а не с выбранной компанией.
        return
    header = request.headers.get('X-Company-ID')
    query = request.query_params.get('company_id')
    if header and query and header != query:
        raise HTTPException(409, 'Компания запроса не совпадает с выбранной. Обновите раздел.')
    raw = header or query
    try:
        # Compatibility with existing API clients: never aggregate all companies.
        cid = int(raw) if raw else None
    except (TypeError, ValueError):
        raise HTTPException(422, 'Некорректная компания.')
    # Validate the current assignments on every request, in one round trip.
    # Staff with more than one valid company still have no access at all.
    from .security import COMPANY_ROLES
    q=select(Company,CompanyUser).outerjoin(CompanyUser,
        (CompanyUser.company_id==Company.id)&(CompanyUser.user_id==user.id)).where(Company.active.is_(True))
    if user.role!='admin':q=q.where(Company.code!=SERVICE_CODE)
    if user.role not in ('admin','founder'):q=q.where(CompanyUser.role.in_(COMPANY_ROLES))
    choices=s.execute(q.execution_options(company_unscoped=True)).all()
    if user.role not in ('admin','founder') and len(choices)!=1:choices=[]
    chosen=next(((c,m) for c,m in choices if (c.id==cid if raw else c.code=='UZGERMED')),None)
    if chosen is None:
        raise HTTPException(403, 'Нет доступа к выбранной компании.')
    company,membership=chosen;cid=company.id
    from .security import scope_user
    acting = []
    for d in active_delegations(s, user.id, cid):
        replaced = s.get(User, d.replaced_user_id)
        acting.append({'id': d.id, 'role': d.role, 'replaced_user_id': d.replaced_user_id,
                       'replaced_name': replaced.name if replaced else '', 'ends_on': str(d.ends_on)})
    scope_user(user, membership.role if membership else None, acting,
               member=membership is not None or user.role in ('admin', 'founder'))
    s.info['company_id'] = cid
    request.state.company_id = cid


def criteria(cid):
    from .clock import today
    day = today()
    a = Account.__table__.c
    l = Ledger.__table__.c
    c = Category.__table__.c
    aids = select(a.id).where(a.company_id == cid)
    lids = select(l.id).where(l.account_id.in_(aids))
    rids = select(PaymentRequest.id).where(PaymentRequest.account_id.in_(aids))
    cats = select(c.id).where(c.company_id == cid)
    return [(cls, cls.company_id == cid) for cls in DIRECT] + [
        (PaymentRequest, PaymentRequest.account_id.in_(aids)),
        (Receipt, Receipt.account_id.in_(aids)),
        (Ledger, Ledger.account_id.in_(aids)),
        (Document, Document.ledger_id.in_(lids)),
        (RequestDocument, RequestDocument.request_id.in_(rids)),
        (Budget, Budget.category_id.in_(cats)),
        (User, or_(User.role.in_(('admin', 'founder')), User.id.in_(select(CompanyUser.user_id).where(CompanyUser.company_id == cid)),
                   User.id.in_(select(Delegation.user_id).where(Delegation.company_id == cid, Delegation.revoked_at.is_(None),
                                                                 Delegation.starts_on <= day, Delegation.ends_on >= day)))),
    ]


@event.listens_for(Session, 'do_orm_execute')
def restrict_queries(state):
    cid = state.session.info.get('company_id')
    if cid is not None and state.is_select and not state.execution_options.get('company_unscoped'):
        state.statement = state.statement.options(*[
            with_loader_criteria(cls, condition, include_aliases=True)
            for cls, condition in criteria(cid)
        ])


def check_object(s, obj):
    cid = s.info.get('company_id')
    if cid is None:
        return
    if isinstance(obj, Company) and obj.id != cid:
        raise HTTPException(404, 'Запись не найдена в выбранной компании.')
    if isinstance(obj, DIRECT) and obj.company_id != cid:
        raise HTTPException(404, 'Запись не найдена в выбранной компании.')
    refs = []
    if isinstance(obj, (PaymentRequest, Receipt, Ledger)):
        refs.append((Account, obj.account_id))
        if obj.category_id is not None:
            refs.append((Category, obj.category_id))
    if isinstance(obj, Ledger) and obj.to_account_id is not None:
        refs.append((Account, obj.to_account_id))
    if isinstance(obj, Budget):
        refs.append((Category, obj.category_id))
    if isinstance(obj, Document):
        refs.append((Ledger, obj.ledger_id))
    if isinstance(obj, RequestDocument):
        refs.append((PaymentRequest, obj.request_id))
    if isinstance(obj, CashFunding):
        refs.extend([(PaymentRequest,obj.request_id),(Account,obj.bank_account_id),
                     (Account,obj.cash_account_id),(Category,obj.fee_category_id)])
        if obj.transit_account_id is not None:refs.append((Account,obj.transit_account_id))
    if isinstance(obj, CashFundingPaymentGroup):refs.append((CashFunding,obj.funding_id))
    if isinstance(obj, CashFundingPaymentItem):
        refs.extend([(CashFundingPaymentGroup,obj.group_id),(PaymentRequest,obj.request_id),(Ledger,obj.ledger_id)])
    if isinstance(obj, ReportArchiveFile):
        refs.append((ReportArchive, obj.archive_id))
    if isinstance(obj, (BusinessSourceFile, BusinessGeneration)):
        refs.append((BusinessProject, obj.project_id))
    if isinstance(obj, BusinessGeneration):
        refs.extend([(ReportArchive, obj.business_archive_id), (ReportArchive, obj.teo_archive_id)])
    for cls, id in refs:
        value = s.get(cls, id)
        if value is None:
            raise HTTPException(404, 'Связанная запись не найдена в выбранной компании.')
        check_object(s, value)


@event.listens_for(Session, 'before_flush')
def restrict_writes(s, *_):
    if any(isinstance(o,(CashFundingPaymentGroup,CashFundingPaymentItem)) for o in list(s.dirty)+list(s.deleted)):
        raise HTTPException(409,'Группа наличной оплаты и её связи хранятся без изменений.')
    if any(isinstance(o, (FinancialStatementSource,CashFlowOsvSource)) for o in list(s.dirty) + list(s.deleted)):
        raise HTTPException(409, 'Исходники финансовых отчётов не изменяются. Загрузите новую версию.')
    # Журнал аудита только дополняется: записи не изменяются и не удаляются.
    if not s.info.get('audit_maintenance') and any(isinstance(o, Audit) for o in list(s.dirty) + list(s.deleted)):
        raise HTTPException(409, 'Журнал аудита не изменяется и не удаляется.')
    cid = s.info.get('company_id')
    if cid is None:
        return
    for obj in list(s.new) + list(s.dirty) + list(s.deleted):
        if isinstance(obj, DIRECT):
            if obj in s.new and obj.company_id is None:
                obj.company_id = cid
            history = inspect(obj).attrs.company_id.history
            if any(old != cid for old in history.deleted):
                raise HTTPException(409, 'Перенос записи между компаниями здесь недоступен.')
        check_object(s, obj)


def setting_key(s, key):
    cid = s.info.get('company_id')
    return f'company:{cid}:{key}' if cid is not None else key


def get_setting(s, key, company_id=None):
    cid=s.info.get('company_id', company_id)
    return s.get(Setting, f'company:{cid}:{key}' if cid is not None else key)


def migrate_columns(engine):
    """Add ownership and replace global name uniqueness without deleting data."""
    tables = ('categories', 'counterparties', 'audit_log', 'import_batches', 'model_versions', 'report_schedules')
    with engine.begin() as conn:
        if conn.dialect.name != 'sqlite':
            conn.execute(text('SELECT pg_advisory_xact_lock(7312801)'))
        for table in tables:
            if 'company_id' not in {c['name'] for c in inspect(conn).get_columns(table)}:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN company_id INTEGER REFERENCES companies(id)'))
        if conn.dialect.name != 'sqlite':
            for cls in (Category, Counterparty, Account):
                table = cls.__tablename__
                for c in inspect(conn).get_unique_constraints(table):
                    if c['column_names'] == ['name']:
                        name = conn.dialect.identifier_preparer.quote(c['name'])
                        conn.execute(text(f'ALTER TABLE {table} DROP CONSTRAINT {name}'))
                if cls is not Counterparty:
                    conn.execute(text(f'CREATE UNIQUE INDEX IF NOT EXISTS ux_{table}_company_name ON {table}(company_id,name)'))
    if engine.dialect.name == 'sqlite':
        # SQLite cannot drop an inline UNIQUE constraint. Rebuild only these
        # three small tables; IDs and all inbound foreign keys stay unchanged.
        with engine.connect() as conn:
            conn.exec_driver_sql('PRAGMA foreign_keys=OFF'); conn.commit()
            try:
                with conn.begin():
                    for cls in (Category, Counterparty, Account):
                        table = cls.__table__
                        if not any(c['column_names'] == ['name'] for c in inspect(conn).get_unique_constraints(table.name)):
                            continue
                        sql = str(CreateTable(table).compile(dialect=conn.dialect))
                        sql = sql.replace('CREATE TABLE '+table.name, 'CREATE TABLE '+table.name+'_scope_tmp', 1)
                        conn.exec_driver_sql(sql)
                        columns = ','.join(c.name for c in table.columns)
                        conn.exec_driver_sql(f'INSERT INTO {table.name}_scope_tmp ({columns}) SELECT {columns} FROM {table.name}')
                        conn.exec_driver_sql(f'DROP TABLE {table.name}')
                        conn.exec_driver_sql(f'ALTER TABLE {table.name}_scope_tmp RENAME TO {table.name}')
                    if conn.exec_driver_sql('PRAGMA foreign_key_check').fetchone():
                        raise RuntimeError('Company migration: foreign key check failed')
            finally:
                conn.exec_driver_sql('PRAGMA foreign_keys=ON'); conn.commit()


def migrate_counterparty_names(engine):
    """Allow the same name with distinct INNs, retaining all existing rows.

    Catalog/API validation handles duplicate identifiers. This migration does
    not impose an INN index on older databases that may have duplicate blanks.
    """
    target = ['company_id', 'name']
    if engine.dialect.name != 'sqlite':
        with engine.begin() as conn:
            conn.execute(text('SELECT pg_advisory_xact_lock(7312801)'))
            quote = conn.dialect.identifier_preparer.quote
            for constraint in inspect(conn).get_unique_constraints('counterparties'):
                if constraint['column_names'] == target and constraint.get('name'):
                    conn.execute(text(f'ALTER TABLE counterparties DROP CONSTRAINT {quote(constraint["name"])}'))
            for index in inspect(conn).get_indexes('counterparties'):
                if index.get('unique') and index.get('column_names') == target:
                    conn.execute(text(f'DROP INDEX {quote(index["name"])}'))
        return

    # SQLite cannot drop a table-level UNIQUE constraint. Reuse the original
    # CREATE statement rather than reconstructing from ORM metadata: this keeps
    # any newer columns and other constraints intact. Preserve explicit indexes
    # and triggers, IDs, and all inbound references.
    with engine.connect() as conn:
        conn.exec_driver_sql('PRAGMA foreign_keys=OFF'); conn.commit()
        try:
            with conn.begin():
                inspector = inspect(conn)
                has_constraint = any(c['column_names'] == target for c in inspector.get_unique_constraints('counterparties'))
                matching_indexes = {i['name'] for i in inspector.get_indexes('counterparties')
                                    if i.get('unique') and i.get('column_names') == target}
                if has_constraint:
                    sql = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name='counterparties'").scalar_one()
                    pattern = r',\s*(?:CONSTRAINT\s+(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|\w+)\s+)?UNIQUE\s*\(\s*["`\[]?company_id["`\]]?\s*,\s*["`\[]?name["`\]]?\s*\)'
                    sql, removed = re.subn(pattern, '', sql, flags=re.IGNORECASE)
                    if removed != 1:
                        raise RuntimeError('Cannot safely migrate counterparties name constraint')
                    sql, renamed = re.subn(r'^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:"counterparties"|`counterparties`|\[counterparties\]|counterparties)',
                                           'CREATE TABLE counterparties_name_migration_tmp', sql, count=1, flags=re.IGNORECASE)
                    if renamed != 1:
                        raise RuntimeError('Cannot safely rename counterparties table for migration')
                    objects = conn.exec_driver_sql("SELECT type,name,sql FROM sqlite_master WHERE tbl_name='counterparties' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
                    quote = conn.dialect.identifier_preparer.quote
                    columns = ','.join(quote(c['name']) for c in inspector.get_columns('counterparties'))
                    conn.exec_driver_sql(sql)
                    conn.exec_driver_sql(f'INSERT INTO counterparties_name_migration_tmp ({columns}) SELECT {columns} FROM counterparties')
                    conn.exec_driver_sql('DROP TABLE counterparties')
                    conn.exec_driver_sql('ALTER TABLE counterparties_name_migration_tmp RENAME TO counterparties')
                    for obj_type, name, ddl in objects:
                        if obj_type != 'index' or name not in matching_indexes:
                            conn.exec_driver_sql(ddl)
                else:
                    quote = conn.dialect.identifier_preparer.quote
                    for name in matching_indexes:
                        conn.exec_driver_sql(f'DROP INDEX {quote(name)}')
                if conn.exec_driver_sql('PRAGMA foreign_key_check').fetchone():
                    raise RuntimeError('Counterparty migration: foreign key check failed')
        finally:
            conn.exec_driver_sql('PRAGMA foreign_keys=ON'); conn.commit()


def migrate_data(engine):
    """One-time migration: copy shared catalogs, preserve financial ownership.

    Existing accounts/plans already have an owner. Unattributed budgets/imports
    stay under UNASSIGNED. No money or account ownership is inferred.
    """
    with Session(engine) as s, s.begin():
        # Однократный перенос по компаниям проставляет company_id и в журнале аудита.
        s.info['audit_maintenance'] = True
        if engine.dialect.name != 'sqlite':
            s.execute(text('SELECT pg_advisory_xact_lock(7312801)'))
        marker = 'company_workspaces_v1'
        if s.get(Setting, marker):
            return
        companies = list(s.scalars(select(Company)))
        default = next(c.id for c in companies if c.code == 'UZGERMED')
        unknown = next(c.id for c in companies if c.code == 'UNASSIGNED')
        cross_transfer=s.execute(text('SELECT l.id FROM ledger l JOIN accounts a ON a.id=l.account_id JOIN accounts b ON b.id=l.to_account_id WHERE a.company_id<>b.company_id LIMIT 1')).first()
        if cross_transfer:
            raise RuntimeError('Сначала проверьте межфирменный внутренний перевод №'+str(cross_transfer[0])+': принадлежность его счетов различается. Миграция данных отменена.')
        categories = list(s.scalars(select(Category)))
        mapping = {}
        for old in categories:
            old.company_id = old.company_id or default
            for co in companies:
                if co.id == old.company_id:
                    mapping[(old.id, co.id)] = old.id
                else:
                    copy = Category(company_id=co.id, name=old.name, activity=old.activity, type=old.type, cost_group=old.cost_group)
                    s.add(copy); s.flush()
                    mapping[(old.id, co.id)] = copy.id
        accounts = {a.id: a for a in s.scalars(select(Account))}
        for cls in (Ledger, PaymentRequest, Receipt):
            for row in s.scalars(select(cls)):
                if row.category_id is not None:
                    row.category_id = mapping[(row.category_id, accounts[row.account_id].company_id)]
        for row in s.scalars(select(Budget)):
            row.category_id = mapping[(row.category_id, unknown)]
        for plan in s.scalars(select(CashPlan)):
            payload = json.loads(plan.payload)
            plan.payload = json.dumps({f'{mapping[(int(k.split(":")[0]),plan.company_id)]}:{k.split(":")[1]}': v for k,v in payload.items()})
        # Prepared imports reference the old shared catalog. Require a fresh
        # preview rather than silently changing a document awaiting approval.
        for cls in (ImportBatch, PlanImportBatch):
            for batch in s.scalars(select(cls)):
                if batch.status == 'preview': batch.status = 'invalid'
        for cls in (Counterparty, ImportBatch, ModelVersion, ReportSchedule):
            for row in s.scalars(select(cls)):
                if row.company_id is None: row.company_id = unknown
        for cls in (Ledger, PaymentRequest, Receipt):
            for row in s.scalars(select(cls)):
                name = row.counterparty.strip()
                cid = accounts[row.account_id].company_id
                if name and not s.scalar(select(Counterparty.id).where(Counterparty.company_id==cid, Counterparty.name==name)):
                    s.add(Counterparty(company_id=cid, name=name)); s.flush()
        for row in s.scalars(select(Audit)):
            cls = {'account':Account,'ledger':Ledger,'request':PaymentRequest,'receipt':Receipt,'cash_plan':CashPlan,'plan_import':PlanImportBatch}.get(row.entity)
            obj = s.get(cls,int(row.entity_id)) if cls and row.entity_id.isdigit() else None
            row.company_id = getattr(obj, 'company_id', None) or (accounts[obj.account_id].company_id if isinstance(obj,(Ledger,PaymentRequest,Receipt)) else unknown)
        # Previously all users could see every company. Preserve that access;
        # newly created users are assigned only to their selected company.
        for u in s.scalars(select(User)):
            for co in companies:
                s.add(CompanyUser(company_id=co.id,user_id=u.id))
        for row in list(s.scalars(select(Setting))):
            if row.key.startswith(('reserve_', 'approval_limit_')):
                ids=[c.id for c in companies] if row.key.startswith('approval_limit_') else [unknown]
                for cid in ids:s.add(Setting(key=f'company:{cid}:{row.key}',value=row.value))
        s.add(Setting(key=marker,value='1'))
