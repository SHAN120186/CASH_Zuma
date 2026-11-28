"""Казначейство: оплата корпоративной картой, конвертация сумов в валюту и части валютной оплаты.

Все суммы — целые сотые доли валюты своего счёта. Остатки по-прежнему считаются
только по журналу Ledger; эти таблицы хранят параметры документов и ссылки на
их проводки, чтобы цепочку можно было проследить от заявки до каждой операции.
"""
from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text
from .db import Base, now


class CardPayment(Base):
    """Оплата заявки корпоративной картой: план комиссии, снимки остатка и контроль бухгалтерии."""
    __tablename__ = 'treasury_card_payments'
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False)
    request_id = Column(Integer, ForeignKey('payment_requests.id'), nullable=False, unique=True)
    card_account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False)
    fee_percent = Column(String(20), nullable=False, default='0.00')
    fee_amount = Column(BigInteger, nullable=False, default=0)
    fee_category_id = Column(Integer, ForeignKey('categories.id'), nullable=True)
    # Остаток карты, зафиксированный при сохранении заявки, и доступная часть без резервов.
    balance_on_save = Column(BigInteger, nullable=True)
    available_on_save = Column(BigInteger, nullable=True)
    saved_at = Column(DateTime, nullable=True)
    state = Column(String(12), nullable=False, default='planned')  # planned / paid
    paid_date = Column(Date, nullable=True)
    paid_reference = Column(String(120), nullable=False, default='')
    actual_fee = Column(BigInteger, nullable=True)
    balance_before = Column(BigInteger, nullable=True)
    balance_after = Column(BigInteger, nullable=True)
    payment_ledger_id = Column(Integer, ForeignKey('ledger.id'), nullable=True, unique=True)
    fee_ledger_id = Column(Integer, ForeignKey('ledger.id'), nullable=True, unique=True)
    # Реестр контроля бухгалтерии: none до оплаты, to_check после, затем verified или issue.
    control_status = Column(String(12), nullable=False, default='none')
    statement_checked = Column(Boolean, nullable=False, default=False)
    documents_checked = Column(Boolean, nullable=False, default=False)
    control_note = Column(Text, nullable=False, default='')
    controlled_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    controlled_at = Column(DateTime, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)
    __mapper_args__ = {'version_id_col': version}


class CurrencyConversion(Base):
    """«Конвертация сум => валюта»: блочный счёт, курс, комиссия и зачисление валюты по заявке."""
    __tablename__ = 'treasury_conversions'
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False)
    request_id = Column(Integer, ForeignKey('payment_requests.id'), nullable=False)
    source_account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False)   # сумовой расчётный счёт
    target_account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False)   # валютный счёт заявки
    block_account_id = Column(Integer, ForeignKey('accounts.id'), nullable=True)     # блочный счёт под конвертацию
    planned_amount = Column(BigInteger, nullable=False)  # валюта к покупке
    planned_rate = Column(String(24), nullable=False)    # сумов за единицу валюты
    planned_uzs = Column(BigInteger, nullable=False)     # сумы на покупку по плановому курсу
    fee_percent = Column(String(20), nullable=False, default='0.00')
    planned_fee = Column(BigInteger, nullable=False, default=0)
    block_amount = Column(BigInteger, nullable=False)    # сумы к переводу на блочный счёт
    fee_category_id = Column(Integer, ForeignKey('categories.id'), nullable=False)
    state = Column(String(12), nullable=False, default='planned')  # planned / blocked / credited / cancelled
    block_date = Column(Date, nullable=True)
    block_reference = Column(String(120), nullable=False, default='')
    block_ledger_id = Column(Integer, ForeignKey('ledger.id'), nullable=True, unique=True)
    credit_date = Column(Date, nullable=True)
    credit_reference = Column(String(120), nullable=False, default='')
    rate = Column(String(24), nullable=True)
    sold_uzs = Column(BigInteger, nullable=True)
    credited_amount = Column(BigInteger, nullable=True)
    actual_fee = Column(BigInteger, nullable=True)
    sell_ledger_id = Column(Integer, ForeignKey('ledger.id'), nullable=True, unique=True)
    buy_ledger_id = Column(Integer, ForeignKey('ledger.id'), nullable=True, unique=True)
    fee_ledger_id = Column(Integer, ForeignKey('ledger.id'), nullable=True, unique=True)
    returned_uzs = Column(BigInteger, nullable=False, default=0)
    return_date = Column(Date, nullable=True)
    return_reference = Column(String(120), nullable=False, default='')
    return_ledger_id = Column(Integer, ForeignKey('ledger.id'), nullable=True, unique=True)
    note = Column(Text, nullable=False, default='')
    version = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)
    __mapper_args__ = {'version_id_col': version}


class PaymentSplit(Base):
    """Частичная оплата: оплаченная часть выделяется в связанную заявку, исходная уменьшается на неё.

    Так резерв, бюджет, прогноз и сверка выписки продолжают работать по обычным правилам
    полной оплаты заявки, а исходная заявка показывает «Частично оплачена».
    """
    __tablename__ = 'treasury_payment_splits'
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False)
    parent_request_id = Column(Integer, ForeignKey('payment_requests.id'), nullable=False)
    child_request_id = Column(Integer, ForeignKey('payment_requests.id'), nullable=False, unique=True)
    sequence = Column(Integer, nullable=False)
    amount = Column(BigInteger, nullable=False)
    parent_amount_before = Column(BigInteger, nullable=False)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)
