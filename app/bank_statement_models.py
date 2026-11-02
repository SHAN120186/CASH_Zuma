"""Private, company-scoped imported bank statements and their review rows."""
from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text
from .db import Base, now


class BankStatementBatch(Base):
    __tablename__ = 'bank_statement_batches'
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    filename = Column(String(220), nullable=False)
    sha256 = Column(String(64), nullable=False)
    account_number = Column(String(34), nullable=False)
    owner_name = Column(String(240), nullable=False)
    owner_inn = Column(String(20), nullable=False)
    currency = Column(String(3), nullable=False)
    period_from = Column(Date, nullable=False)
    period_to = Column(Date, nullable=False)
    opening = Column(BigInteger, nullable=False)
    closing = Column(BigInteger, nullable=False)
    debit_total = Column(BigInteger, nullable=False)
    credit_total = Column(BigInteger, nullable=False)
    balance_matches = Column(Boolean, nullable=False)
    declared_totals_match = Column(Boolean, nullable=True)
    source_ready = Column(Boolean, nullable=False)
    status = Column(String(16), nullable=False, default='preview')
    created_at = Column(DateTime, nullable=False, default=now)
    committed_at = Column(DateTime, nullable=True)


class BankStatementLine(Base):
    __tablename__ = 'bank_statement_lines'
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False)
    batch_id = Column(Integer, ForeignKey('bank_statement_batches.id'), nullable=False)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False)
    line = Column(Integer, nullable=False)
    date = Column(Date, nullable=False)
    direction = Column(String(4), nullable=False)
    amount = Column(BigInteger, nullable=False)
    document_number = Column(String(160), nullable=False)
    correspondent_mfo = Column(String(20), nullable=False)
    correspondent_account = Column(String(34), nullable=False)
    correspondent_name = Column(String(240), nullable=False)
    correspondent_inn = Column(String(20), nullable=False)
    purpose = Column(Text, nullable=False)
    fingerprint = Column(String(64), nullable=False)
    occurrence = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default='review')
    reason = Column(String(160), nullable=False, default='')
    include_in_daily = Column(Boolean, nullable=False, default=False)
    uncertain = Column(Boolean, nullable=False, default=False)
    request_id = Column(Integer, ForeignKey('payment_requests.id'), nullable=True)
    ledger_id = Column(Integer, ForeignKey('ledger.id'), nullable=True)
    classification = Column(String(32), nullable=False, default='')
    reviewed_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_note = Column(String(1000), nullable=False, default='')
