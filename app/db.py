from __future__ import annotations
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from sqlalchemy import (create_engine, event, Column, Integer, BigInteger, String,
                        Text, Boolean, ForeignKey, Date, DateTime, UniqueConstraint, select, LargeBinary)
from sqlalchemy.orm import declarative_base, Session

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv('DATA_DIR', str(ROOT / 'data'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DB_URL = os.getenv('DATABASE_URL', 'sqlite:///' + str(DATA / 'cashflow.sqlite3'))
SQLITE = DB_URL.startswith('sqlite:')
engine = create_engine(DB_URL, connect_args={'check_same_thread': False, 'timeout': 30} if SQLITE else {}, pool_pre_ping=True)
if SQLITE:
    @event.listens_for(engine, 'connect')
    def configure_sqlite(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA busy_timeout=30000')
Base = declarative_base()
def now(): return datetime.now(timezone.utc).replace(tzinfo=None)

class Guard(Base):
    __tablename__ = 'write_guard'
    id = Column(Integer, primary_key=True)
    value = Column(Integer, nullable=False, default=0)

class Role(Base):
    __tablename__ = 'roles'
    id = Column(Integer, primary_key=True)
    name = Column(String(20), unique=True, nullable=False)

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False)
    name = Column(String(160), nullable=False)
    password_hash = Column(Text, nullable=False)
    role = Column(String(20), nullable=False)
    role_id = Column(Integer, ForeignKey('roles.id'), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=now, nullable=False)

class LoginSession(Base):
    __tablename__ = 'sessions'
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    csrf = Column(String(100), nullable=False)
    expires_at = Column(DateTime, nullable=False)

class LoginAttempt(Base):
    __tablename__ = 'login_attempts'
    key = Column(String(64), primary_key=True)
    count = Column(Integer, nullable=False, default=0)
    since = Column(DateTime, nullable=False, default=now)

class Category(Base):
    __tablename__ = 'categories'
    id = Column(Integer, primary_key=True)
    name = Column(String(160), unique=True, nullable=False)
    activity = Column(String(20), nullable=False, default='operating')
    type = Column(String(12), nullable=False, default='outcome')
    cost_group = Column(String(12), nullable=False, default='other')

class CashPlan(Base):
    __tablename__ = 'cash_plans'
    id = Column(Integer, primary_key=True)
    month = Column(String(7), nullable=False)
    currency = Column(String(3), nullable=False)
    opening = Column(BigInteger, nullable=True)
    payload = Column(Text, nullable=False, default='{}')
    version = Column(Integer, nullable=False, default=1)
    __table_args__ = (UniqueConstraint('month', 'currency'),)

class Counterparty(Base):
    __tablename__ = 'counterparties'
    id = Column(Integer, primary_key=True)
    name = Column(String(160), unique=True, nullable=False)
    inn = Column(String(20), nullable=False, default='')
    note = Column(String(240), nullable=False, default='')

class Account(Base):
    __tablename__ = 'accounts'
    id = Column(Integer, primary_key=True)
    name = Column(String(160), unique=True, nullable=False)
    kind = Column(String(12), nullable=False) # bank / cash
    currency = Column(String(3), nullable=False)
    opening = Column(BigInteger, nullable=False) # сотые доли валюты; не float
    opening_date = Column(Date, nullable=False) # начало дня
    allow_overdraft = Column(Boolean, nullable=False, default=False)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)

    archived = Column(Boolean, nullable=False, default=False)

class Budget(Base):
    __tablename__ = 'budgets'
    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey('categories.id'), nullable=False)
    month = Column(String(7), nullable=False)
    currency = Column(String(3), nullable=False)
    amount = Column(BigInteger, nullable=False)
    mode = Column(String(4), nullable=False, default='soft')
    source = Column(String(240), nullable=False, default='Введён вручную')
    __table_args__ = (UniqueConstraint('category_id','month','currency'),)

class PaymentRequest(Base):
    __tablename__ = 'payment_requests'
    id = Column(Integer, primary_key=True)
    creator_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    category_id = Column(Integer, ForeignKey('categories.id'), nullable=False)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False)
    counterparty = Column(String(160), nullable=False)
    purpose = Column(Text, nullable=False)
    project = Column(String(120), nullable=False, default='')
    amount = Column(BigInteger, nullable=False)
    due_date = Column(Date, nullable=False)
    status = Column(String(20), nullable=False, default='pending')
    decision_note = Column(Text, nullable=False, default='')
    approved_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    finance_approved_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    last_editor_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    priority = Column(String(12), nullable=False, default='normal')
    created_at = Column(DateTime, nullable=False, default=now)
    __mapper_args__ = {'version_id_col': version}

class Receipt(Base):
    __tablename__ = 'expected_receipts'
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False)
    category_id = Column(Integer, ForeignKey('categories.id'), nullable=False)
    counterparty = Column(String(160), nullable=False)
    amount = Column(BigInteger, nullable=False)
    due_date = Column(Date, nullable=False)
    note = Column(Text, nullable=False, default='')
    creator_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    status = Column(String(20), nullable=False, default='expected')

class Ledger(Base):
    __tablename__ = 'ledger'
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False)
    to_account_id = Column(Integer, ForeignKey('accounts.id'), nullable=True)
    category_id = Column(Integer, ForeignKey('categories.id'), nullable=True)
    kind = Column(String(12), nullable=False) # in / out / transfer
    amount = Column(BigInteger, nullable=False)
    date = Column(Date, nullable=False)
    counterparty = Column(String(160), nullable=False, default='')
    reference = Column(String(160), nullable=False)
    note = Column(Text, nullable=False, default='')
    request_id = Column(Integer, ForeignKey('payment_requests.id'), nullable=True, unique=True)
    receipt_id = Column(Integer, ForeignKey('expected_receipts.id'), nullable=True, unique=True)
    reversal_of = Column(Integer, ForeignKey('ledger.id'), nullable=True, unique=True)
    creator_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)
    __table_args__ = (UniqueConstraint('account_id','reference'),)

class ModelVersion(Base):
    __tablename__ = 'model_versions'
    id = Column(Integer, primary_key=True)
    sha256 = Column(String(64), nullable=False, unique=True)
    filename = Column(String(220), nullable=False)
    imported_at = Column(DateTime, nullable=False, default=now)
    source = Column(String(240), nullable=False)
    snapshot = Column(Text, nullable=False)

class Audit(Base):
    __tablename__ = 'audit_log'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    action = Column(String(80), nullable=False)
    entity = Column(String(50), nullable=False)
    entity_id = Column(String(80), nullable=False, default='')
    detail = Column(Text, nullable=False, default='')
    created_at = Column(DateTime, nullable=False, default=now)

class Setting(Base):
    __tablename__ = 'settings'
    key = Column(String(60), primary_key=True)
    value = Column(Text, nullable=False)

@event.listens_for(User,'before_insert')
@event.listens_for(User,'before_update')
def sync_role_reference(mapper,connection,target):
    target.role_id=connection.scalar(select(Role.id).where(Role.name==target.role))
    if target.role_id is None:raise ValueError('Unknown role')

class Document(Base):
    __tablename__ = 'documents'
    id = Column(Integer, primary_key=True)
    ledger_id = Column(Integer, ForeignKey('ledger.id'), nullable=False)
    filename = Column(String(220), nullable=False)
    mime = Column(String(100), nullable=False)
    storage_key = Column(String(80), unique=True, nullable=False)
    sha256 = Column(String(64), nullable=False)
    content = Column(LargeBinary, nullable=False)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=now, nullable=False)

class ImportBatch(Base):
    __tablename__ = 'import_batches'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    filename = Column(String(220), nullable=False)
    digest = Column(String(64), nullable=False)
    payload = Column(Text, nullable=False)
    status = Column(String(20), default='preview', nullable=False)
    created_at = Column(DateTime, default=now, nullable=False)

class ReportSchedule(Base):
    __tablename__ = 'report_schedules'
    id = Column(Integer, primary_key=True)
    recipient = Column(String(254), nullable=False)
    currency = Column(String(3), nullable=False)
    hour = Column(Integer, nullable=False, default=9)
    enabled = Column(Boolean, nullable=False, default=False)
    last_sent = Column(Date, nullable=True)
    last_attempt = Column(Date, nullable=True)
    last_error = Column(String(300), nullable=False, default='')
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)

@contextmanager
def unit(write=False):
    """Сериализация записей: лимит/остаток проверяются и меняются атомарно."""
    with Session(engine, expire_on_commit=False) as s:
        try:
            if write:
                if SQLITE: s.connection().exec_driver_sql('BEGIN IMMEDIATE')
                else: s.execute(select(Guard).where(Guard.id == 1).with_for_update()).scalar_one()
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise

def initialize():
    Base.metadata.create_all(engine)
    # Additive migration of the pilot schema. Existing financial rows are kept.
    from sqlalchemy import inspect, text
    with engine.begin() as conn:
        if not SQLITE:conn.execute(text('SELECT pg_advisory_xact_lock(7312801)'))
        columns={c['name'] for c in inspect(conn).get_columns('users')}
        if 'role_id' not in columns:conn.execute(text('ALTER TABLE users ADD COLUMN role_id INTEGER REFERENCES roles(id)'))
        columns={c['name'] for c in inspect(conn).get_columns('categories')}
        if 'cost_group' not in columns:conn.execute(text("ALTER TABLE categories ADD COLUMN cost_group VARCHAR(12) NOT NULL DEFAULT 'other'"))
        if 'type' not in columns:
            conn.execute(text("ALTER TABLE categories ADD COLUMN type VARCHAR(12) NOT NULL DEFAULT 'outcome'"))
            conn.execute(text("UPDATE categories SET type='income' WHERE name IN ('Поступления от покупателей','Получение кредита')"))
        if 'last_attempt' not in {c['name'] for c in inspect(conn).get_columns('report_schedules')}:
            conn.execute(text('ALTER TABLE report_schedules ADD COLUMN last_attempt DATE'))
        columns={c['name'] for c in inspect(conn).get_columns('payment_requests')}
        if 'finance_approved_by' not in columns:conn.execute(text('ALTER TABLE payment_requests ADD COLUMN finance_approved_by INTEGER REFERENCES users(id)'))
        if 'archived' not in {c['name'] for c in inspect(conn).get_columns('accounts')}:
            conn.execute(text('ALTER TABLE accounts ADD COLUMN archived BOOLEAN NOT NULL DEFAULT FALSE'))
        for name,definition in [('version','INTEGER NOT NULL DEFAULT 1'),('last_editor_id','INTEGER REFERENCES users(id)'),('priority',"VARCHAR(12) NOT NULL DEFAULT 'normal'")]:
            if name not in columns:conn.execute(text(f'ALTER TABLE payment_requests ADD COLUMN {name} {definition}'))
    with Session(engine) as s:
        for name in ('admin','director','finance','accountant','employee','auditor','cashier'):
            if not s.scalar(select(Role).where(Role.name==name)):s.add(Role(name=name))
        s.flush()
        roles={r.name:r.id for r in s.scalars(select(Role))}
        for u in s.scalars(select(User)):u.role_id=roles[u.role]
        if not s.get(Guard, 1): s.add(Guard(id=1))
        if not s.scalar(select(Category.id).limit(1)):
            for name, activity in [
                ('Поступления от покупателей','operating'),('Закупка сырья','operating'),
                ('Упаковка и материалы','operating'),('Оплата труда','operating'),
                ('Налоги','operating'),('Логистика','operating'),('Маркетинг','operating'),
                ('Прочие операционные расходы','operating'),('Оборудование','investing'),
                ('Получение кредита','financing'),('Погашение основного долга','financing'),
                ('Проценты по кредитам','financing'),('Дивиденды','financing')]:
                s.add(Category(name=name, activity=activity,type='income' if name in ('Поступления от покупателей','Получение кредита') else 'outcome'))
        s.commit()
