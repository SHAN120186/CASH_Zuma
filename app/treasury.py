"""Казначейство: корпоративные карты и валютные заявки с конвертацией через блочный счёт.

Команды фиксируют уже выполненные банковские факты и никогда не отправляют поручений в банк.
Каждая запись идёт внутри unit(write=True): остатки, резервы и состояние документа
проверяются и меняются атомарно, версии документов защищают от повторного проведения.

Итерация 1 — канал «Корпоративная карта»:
  заявка оплачивается со счёта вида ``card``; при сохранении фиксируется остаток карты,
  при оплате проводятся две операции — «Оплата корп. картой» по статье заявки и
  «Комиссия за оплату корп. картой» по статье комиссии; сумма и комиссия вместе не могут
  превышать остаток карты. Оплата попадает в реестр контроля бухгалтерии.

Итерация 2 — валютная заявка (банковская заявка в USD/EUR):
  «Конвертация сум => валюта» хранит план (сумма, курс, комиссия), перевод сумов на
  блочный счёт (служебный счёт вида ``block``), зачисление валюты («Приход Банк $») и
  возврат неиспользованных сумов. Оплата поставщику может быть частичной: оплаченная часть
  выделяется в связанную заявку, исходная уменьшается на неё и остаётся утверждённой.
"""
import datetime as dt
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from types import SimpleNamespace
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, union_all

from .db import Account, Category, Ledger, PaymentRequest, RequestDocument, Setting, User, now, unit
from .security import act_with_right, has_role, perms_of, session_user
from .section_access import can_edit_section
from .services import (account_balance, amount, budget_state, check_payer, day_start_balance, effective_cashflows,
                       enforce_reserved_funds, funds_state, get, local_date, log, money, reversal_reference,
                       route_complete, today, validate_running_balance)
from .treasury_models import CardPayment, CurrencyConversion, PaymentSplit

router = APIRouter()

SYSTEM_ACTIVITIES = ('unclassified', 'conversion')
CONVERSION_ACTIVITY = 'conversion'
CONVERSION_CATEGORIES = {
    'out': ('Конвертация валюты: продажа сумов', 'outcome'),
    'in': ('Конвертация валюты: покупка валюты', 'income'),
}
BLOCK_ACCOUNT_NAME = 'Блочный счёт под конвертацию · {currency}'
CARD_STATES = ('planned', 'paid')
CONVERSION_STATES = ('planned', 'blocked', 'credited', 'cancelled')
CONTROL_STATES = ('to_check', 'verified', 'issue')


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


# ---------------------------------------------------------------- расчёты


def is_card(account):
    return account is not None and account.kind == 'card'


def is_fx(account):
    return account is not None and account.kind == 'bank' and account.currency != 'UZS'


def percent_value(value):
    try:
        percent = Decimal(str(value if value not in (None, '') else '0').replace(',', '.'))
        if not percent.is_finite() or not 0 <= percent < 100 or percent != percent.quantize(Decimal('.01')):
            raise ValueError()
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(422, 'Комиссия должна быть от 0 до 99,99%, не более двух знаков после запятой.')
    return percent


def fee_of(base, percent):
    """Комиссия в сотых долях: округление половины вверх один раз, без float."""
    return int((Decimal(base) * percent / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def rate_value(value):
    try:
        rate = Decimal(str(value).replace(' ', '').replace(',', '.'))
        if not rate.is_finite() or rate <= 0 or rate > Decimal('10000000') or rate != rate.quantize(Decimal('.0001')):
            raise ValueError()
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(422, 'Курс конвертации — положительное число сумов за единицу валюты, не более четырёх знаков после запятой.')
    return rate


def rate_text(rate):
    text = format(rate.quantize(Decimal('.0001')), 'f').rstrip('0').rstrip('.')
    whole, _, fraction = text.partition('.')
    return whole + '.' + fraction.ljust(2, '0')


def uzs_for(minor, rate):
    """Сумы (в тийинах) за сумму валюты в центах по курсу «сумов за единицу»."""
    return int((Decimal(minor) * rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def effective_rate(sold, bought):
    if not bought:
        return None
    return rate_text((Decimal(sold) / Decimal(bought)).quantize(Decimal('.0001'), rounding=ROUND_HALF_UP))


def number(request_id):
    return f'CF-{request_id:05d}'


# ---------------------------------------------------------------- справочники


def active_outcome_category(s, category_id, message='Для комиссии выберите действующую расходную статью своей компании.'):
    from .category_catalog import catalog
    category = s.get(Category, category_id) if category_id else None
    active = {c.id for c, _ in catalog(s)}
    if (category is None or category.type != 'outcome' or category.activity in SYSTEM_ACTIVITIES
            or category.id not in active):
        raise HTTPException(422, message)
    return category


def conversion_category(s, kind):
    """Служебная статья конвертации компании; создаётся только внутри проведения."""
    cid = s.info.get('company_id')
    if cid is None:
        raise HTTPException(409, 'Сначала выберите компанию.')
    key = f'company:{cid}:treasury_conversion_{kind}'
    name, category_type = CONVERSION_CATEGORIES[kind]
    setting = s.get(Setting, key)
    if setting is not None:
        try:
            category = s.get(Category, int(setting.value))
        except (TypeError, ValueError):
            category = None
        if (category is None or category.company_id != cid or category.type != category_type
                or category.activity != CONVERSION_ACTIVITY):
            raise HTTPException(409, 'Служебная статья конвертации требует проверки администратора.')
        return category.id
    names = set(s.scalars(select(Category.name).where(Category.company_id == cid)))
    unique, n = name, 2
    while unique in names:
        unique, n = f'{name} ({n})', n + 1
    category = Category(company_id=cid, name=unique, type=category_type, activity=CONVERSION_ACTIVITY, cost_group='other')
    s.add(category)
    s.flush()
    s.add(Setting(key=key, value=str(category.id)))
    s.flush()
    return category.id


def block_account(s, user, source):
    """Служебный блочный счёт компании для сумов, отправленных на конвертацию."""
    found = s.scalar(select(Account).where(Account.kind == 'block', Account.currency == source.currency,
                                           Account.company_id == source.company_id, Account.archived.is_(False))
                     .order_by(Account.id).limit(1))
    if found is not None:
        return found
    name = BLOCK_ACCOUNT_NAME.format(currency=source.currency)
    if s.scalar(select(Account.id).where(Account.name == name, Account.company_id == source.company_id)):
        raise HTTPException(409, 'Имя блочного счёта занято обычным счётом. Переименуйте обычный счёт.')
    account = Account(name=name, kind='block', currency=source.currency, company_id=source.company_id, opening=0,
                      opening_date=date(2000, 1, 1), allow_overdraft=False, created_by=user.id)
    s.add(account)
    s.flush()
    log(s, user, 'Создан блочный счёт под конвертацию', 'account', account.id, source.currency)
    return account


# ---------------------------------------------------------------- связи с журналом


def treasury_ledger_ids(s):
    """Операции документов казначейства: одна выборка, без загрузки объектов."""
    columns = [CardPayment.payment_ledger_id, CardPayment.fee_ledger_id, CurrencyConversion.block_ledger_id,
               CurrencyConversion.sell_ledger_id, CurrencyConversion.buy_ledger_id, CurrencyConversion.fee_ledger_id,
               CurrencyConversion.return_ledger_id]
    query = union_all(*[select(column.label('ledger_id')).where(column.is_not(None)) for column in columns])
    return {row[0] for row in s.execute(query)}


def card_for(s, request_id):
    return s.scalar(select(CardPayment).where(CardPayment.request_id == request_id))


def conversions_for(s, request_id):
    return list(s.scalars(select(CurrencyConversion).where(CurrencyConversion.request_id == request_id)
                          .order_by(CurrencyConversion.id)))


def removal_blockers(s, r):
    """Проверки перед полным удалением неоплаченной заявки (request_retention)."""
    blockers = []
    card = s.scalar(select(CardPayment).where(CardPayment.request_id == r.id).execution_options(company_unscoped=True))
    if card is not None and (card.state != 'planned' or card.payment_ledger_id or card.fee_ledger_id):
        blockers.append('По заявке проведена оплата корпоративной картой. Операции и комиссия должны сохраняться.')
    moving = s.scalar(select(CurrencyConversion.id).where(CurrencyConversion.request_id == r.id,
                      CurrencyConversion.state.not_in(('planned', 'cancelled'))).limit(1).execution_options(company_unscoped=True))
    if moving is not None:
        blockers.append('По заявке учтена конвертация: перевод на блочный счёт и зачисление валюты должны сохраняться.')
    split = s.scalar(select(PaymentSplit.id).where((PaymentSplit.parent_request_id == r.id) | (PaymentSplit.child_request_id == r.id))
                     .limit(1).execution_options(company_unscoped=True))
    if split is not None:
        blockers.append('Заявка связана с частичной оплатой поставщику. Оплаченные части должны сохраняться.')
    return blockers


def delete_plans(s, request_id):
    """Удаляет несостоявшиеся планы казначейства вместе с неоплаченной заявкой."""
    for row in s.scalars(select(CardPayment).where(CardPayment.request_id == request_id)):
        s.delete(row)
    for row in s.scalars(select(CurrencyConversion).where(CurrencyConversion.request_id == request_id)):
        s.delete(row)


def closing_documents_allowed(s, user, payment):
    """Право приложить чек или закрывающий документ к оплаченной картой заявке, без побочных эффектов.

    До отметки «Сверено» прикладывают автор заявки, финансовый руководитель и расчётный бухгалтер
    (контроль). Возвращает право, которое даёт доступ, или None."""
    if payment.status != 'paid':
        return None
    account = s.get(Account, payment.account_id)
    if not is_card(account):
        return None
    plan = card_for(s, payment.id)
    if plan is None or plan.state != 'paid' or plan.control_status == 'verified':
        return None
    permissions = perms_of(user)
    if payment.creator_id == user.id and 'request' in permissions:
        return 'request'
    if 'request_edit' in permissions:
        return 'request_edit'
    if can_control(user):
        return 'request_check' if 'request_check' in permissions else 'pay_bank'
    return None


def closing_documents_access(s, user, payment):
    """Вызывается из erp.request_document_access перед обычными правилами изменения документов."""
    right = closing_documents_allowed(s, user, payment)
    if right is None:
        return False
    act_with_right(user, right)
    return True


def require_settled_conversions(s, r):
    """Нельзя убрать заявку, пока сумы лежат на блочном счёте по её конвертации (до или после зачисления валюты)."""
    for conversion in s.scalars(select(CurrencyConversion).where(CurrencyConversion.request_id == r.id,
                                                                 CurrencyConversion.state.in_(('blocked', 'credited')))):
        if conversion_leftover(conversion) > 0:
            raise HTTPException(409, 'Сумы по заявке находятся на блочном счёте. Сначала учтите зачисление валюты или верните сумы на расчётный счёт.')


# ---------------------------------------------------------------- план оплаты картой


class CardPlanIn(Input):
    fee_percent: str = Field(default='0', max_length=20)
    fee_category_id: Optional[int] = Field(default=None, ge=1)


def card_state(s, account, exclude_request=None):
    day = max(today(), account.opening_date)
    state = funds_state(s, account, day, exclude_request, include_bank_hold=False)
    return {'balance': state['actual'], 'reserved': state['reserved'], 'available': state['available'], 'as_of': str(day)}


def save_card_plan(s, user, request, data, *, provided=True):
    """Вызывается при создании и правке заявки: план комиссии и снимок остатка карты."""
    account = get(s, Account, request.account_id)
    current = card_for(s, request.id)
    if not is_card(account):
        if current is not None:
            if current.state != 'planned':
                raise HTTPException(409, 'По заявке уже проведена оплата корпоративной картой. Канал оплаты больше не меняется.')
            s.delete(current)
        if provided and data is not None:
            raise HTTPException(422, 'Комиссию корпоративной карты указывают только для канала «Корпоративная карта».')
        return None
    if current is not None and current.state != 'planned':
        raise HTTPException(409, 'Оплата корпоративной картой уже проведена. Параметры заявки больше не меняются.')
    if data is None:
        data = CardPlanIn(fee_percent=current.fee_percent, fee_category_id=current.fee_category_id) if current else CardPlanIn()
    percent = percent_value(data.fee_percent)
    fee = fee_of(request.amount, percent)
    category = None
    if fee > 0 and data.fee_category_id is None:
        raise HTTPException(422, 'Выберите статью комиссии за оплату корпоративной картой.')
    if data.fee_category_id is not None:
        category = active_outcome_category(s, data.fee_category_id)
    if current is None:
        current = CardPayment(company_id=account.company_id, request_id=request.id, card_account_id=account.id,
                              created_by=user.id)
        s.add(current)
    state = card_state(s, account, request.id)
    current.card_account_id = account.id
    current.fee_percent = format(percent, '.2f')
    current.fee_amount = fee
    current.fee_category_id = category.id if category else None
    current.balance_on_save = state['balance']
    current.available_on_save = state['available']
    current.saved_at = now()
    s.flush()
    log(s, user, 'Настроена оплата корпоративной картой', 'request', request.id, json.dumps({
        'card_account_id': account.id, 'fee_percent': current.fee_percent, 'fee_amount': money(fee),
        'fee_category_id': current.fee_category_id, 'balance_on_save': money(state['balance']),
        'available_on_save': money(state['available'])}, ensure_ascii=False))
    return current


def after_request_saved(s, user, request, data, *, provided=True):
    """Общий вызов из POST/PUT заявки: план карты и согласованность запланированной конвертации."""
    save_card_plan(s, user, request, data, provided=provided)
    for conversion in conversions_for(s, request.id):
        if conversion.state == 'cancelled':
            continue
        if conversion.target_account_id != request.account_id:
            raise HTTPException(409, 'По заявке есть конвертация на другой валютный счёт. Сначала отмените её, затем меняйте счёт заявки.')


@router.get('/api/treasury/card-balance')
def card_balance(request: Request, account_id: int, value: str = Query('', alias='amount'), fee_percent: str = '0',
                 request_id: Optional[int] = None):
    """Остаток корпоративной карты для формы заявки: только чтение, без раскрытия других счетов."""
    with unit() as s:
        session_user(s, request, ('request', 'request_edit', 'pay_bank', 'view'))
        account = get(s, Account, account_id)
        if not is_card(account) or account.archived:
            raise HTTPException(404, 'Корпоративная карта не найдена.')
        state = card_state(s, account, request_id)
        result = {'account_id': account.id, 'account': account.name, 'currency': account.currency,
                  'balance': money(state['balance']), 'reserved': money(state['reserved']),
                  'available': money(state['available']), 'as_of': state['as_of']}
        if value:
            n = amount(value)
            fee = fee_of(n, percent_value(fee_percent))
            result.update({'amount': money(n), 'fee_amount': money(fee), 'total': money(n + fee),
                           'enough': state['available'] >= n + fee, 'shortage': money(max(0, n + fee - state['available']))})
        return result


# ---------------------------------------------------------------- оплата картой


class CardPayIn(Input):
    version: int = Field(ge=1)
    request_version: int = Field(ge=1)
    date: date
    reference: str = Field(min_length=1, max_length=120)
    fee_amount: str = Field(max_length=40)
    note: str = Field(default='', max_length=1000)


class CardCancelIn(Input):
    version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=1000)


def _visible(s, user, request_id):
    from .requests_api import visible
    return visible(s, user, request_id)


def _require_money_section(user):
    if not can_edit_section(user, 'requests', 'ledger'):
        raise HTTPException(403, 'Для этого раздела доступен только просмотр.')


def _shortage(account, day, balance, total, main, fee):
    return HTTPException(409,
        f'Недостаточно средств на корпоративной карте «{account.name}» на {day}: остаток {money(balance)} {account.currency}, '
        f'требуется {money(total)} {account.currency} (сумма оплаты {money(main)} + комиссия {money(fee)}). '
        'Оплата не проведена: пополните карту и повторите.')


@router.post('/api/treasury/requests/{id}/card-pay')
def card_pay(id: int, data: CardPayIn, request: Request):
    from .services import post_ledger
    with unit(True) as s:
        user, _ = session_user(s, request)
        req = _visible(s, user, id)
        account = get(s, Account, req.account_id)
        if not is_card(account):
            raise HTTPException(409, 'Заявка оплачивается не корпоративной картой.')
        plan = card_for(s, req.id)
        if plan is None:
            raise HTTPException(409, 'В заявке нет параметров оплаты картой. Откройте заявку и сохраните её заново.')
        _require_money_section(user)
        if req.status != 'approved' or not route_complete(req):
            raise HTTPException(409, 'Сначала заявка должна пройти все этапы согласования.')
        if plan.state != 'planned':
            raise HTTPException(409, 'Оплата картой уже проведена. Обновите карточку, не повторяйте платёж.')
        if plan.version != data.version or req.version != data.request_version:
            raise HTTPException(409, 'Заявка или параметры карты изменились. Обновите карточку.')
        check_payer(s, user, req)
        if account.archived:
            raise HTTPException(409, 'Корпоративная карта в архиве. Сначала восстановите её.')
        fee = amount(data.fee_amount, allow_zero=True)
        if fee and plan.fee_category_id is None:
            raise HTTPException(409, 'Для комиссии не выбрана статья. Верните заявку на доработку и укажите статью комиссии.')
        if fee != plan.fee_amount and len(data.note.strip()) < 10:
            raise HTTPException(422, 'Фактическая комиссия отличается от плановой: укажите пояснение не короче 10 символов.')
        if data.date > today():
            raise HTTPException(422, 'Будущий платёж — это заявка, а не факт оплаты.')
        earliest = max(local_date(req.approved_at or req.created_at), account.opening_date)
        if data.date < earliest:
            raise HTTPException(422, f'Дата оплаты не может быть раньше утверждения заявки и начала учёта карты ({earliest}).')
        total = req.amount + fee
        for day in sorted({data.date, today()}):
            balance = account_balance(s, account, day)
            if balance < total:
                raise _shortage(account, day, balance, total, req.amount, fee)
        if fee:
            same = plan.fee_category_id == req.category_id and str(req.due_date)[:7] == str(data.date)[:7]
            state = budget_state(s, plan.fee_category_id, str(data.date)[:7], account.currency, fee + (req.amount if same else 0), req.id)
            if state['over'] and state['mode'] == 'hard':
                raise HTTPException(409, 'Жёсткий лимит статьи комиссии превышен. Сначала пересмотрите бюджет комиссии.')
        before = account_balance(s, account, data.date)
        reference = data.reference.strip()
        payment = post_ledger(s, user, SimpleNamespace(
            account_id=account.id, to_account_id=None, category_id=req.category_id, kind='out', amount=money(req.amount),
            date=data.date, counterparty=req.counterparty, reference=reference,
            note=data.note.strip() or f'Оплата корп. картой по {number(req.id)}', request_id=req.id,
            request_version=req.version, receipt_id=None))
        fee_entry = None
        if fee:
            fee_entry = Ledger(account_id=account.id, category_id=plan.fee_category_id, kind='out', amount=fee, date=data.date,
                               counterparty='Комиссия банка', reference=(reference + '-FEE')[:160],
                               note=f'Комиссия за оплату корп. картой по {number(req.id)}', creator_id=user.id)
            s.add(fee_entry)
            s.flush()
        validate_running_balance(s, account)
        plan.state = 'paid'
        plan.paid_date = data.date
        plan.paid_reference = reference
        plan.actual_fee = fee
        plan.balance_before = before
        plan.balance_after = account_balance(s, account, data.date)
        plan.payment_ledger_id = payment.id
        plan.fee_ledger_id = fee_entry.id if fee_entry else None
        plan.control_status = 'to_check'
        plan.statement_checked = plan.documents_checked = False
        s.flush()
        log(s, user, 'Оплата корпоративной картой', 'request', req.id, json.dumps({
            'card_payment_id': plan.id, 'date': str(data.date), 'reference': reference, 'amount': money(req.amount),
            'fee': money(fee), 'total': money(total), 'balance_before': money(before), 'balance_after': money(plan.balance_after),
            'payment_ledger_id': payment.id, 'fee_ledger_id': plan.fee_ledger_id, 'note': data.note}, ensure_ascii=False))
        return treasury_view(s, user, req)


@router.post('/api/treasury/requests/{id}/card-cancel')
def card_cancel(id: int, data: CardCancelIn, request: Request):
    """Исправление ошибочно учтённой оплаты картой: обе операции сторнируются исходной датой."""
    with unit(True) as s:
        user, _ = session_user(s, request)
        req = _visible(s, user, id)
        plan = card_for(s, req.id)
        if plan is None or plan.state != 'paid':
            raise HTTPException(409, 'По заявке нет проведённой оплаты картой.')
        if 'request_delete' not in perms_of(user) or not can_edit_section(user, 'requests', 'ledger'):
            raise HTTPException(403, 'Ошибочную оплату картой отменяет финансовый директор, директор или администратор.')
        act_with_right(user, 'request_delete')
        if plan.version != data.version:
            raise HTTPException(409, 'Параметры оплаты картой изменились. Обновите карточку.')
        account = get(s, Account, plan.card_account_id)
        for leg in (plan.payment_ledger_id, plan.fee_ledger_id):
            if leg is None:
                continue
            original = get(s, Ledger, leg)
            if original.reversal_of or s.scalar(select(Ledger.id).where(Ledger.reversal_of == leg)):
                raise HTTPException(409, 'Операция оплаты уже сторнирована.')
            s.add(Ledger(account_id=original.account_id, kind='in', amount=original.amount, date=original.date,
                         category_id=original.category_id, reference=reversal_reference(s, original.account_id, original.id),
                         reversal_of=original.id, counterparty=original.counterparty, note=data.reason, creator_id=user.id))
            original.request_id = None
            s.flush()
            log(s, user, 'Сторно операции оплаты картой', 'ledger', original.id, data.reason)
        if req.status == 'paid':
            req.status = 'approved'
        plan.state = 'planned'
        plan.paid_date = None
        plan.paid_reference = ''
        plan.actual_fee = plan.balance_before = plan.balance_after = None
        plan.payment_ledger_id = plan.fee_ledger_id = None
        plan.control_status = 'none'
        plan.statement_checked = plan.documents_checked = False
        plan.control_note = ''
        plan.controlled_by = plan.controlled_at = None
        s.flush()
        validate_running_balance(s, account)
        log(s, user, 'Отменена оплата корпоративной картой', 'request', req.id, json.dumps({'reason': data.reason}, ensure_ascii=False))
        return treasury_view(s, user, req)


# ---------------------------------------------------------------- реестр контроля бухгалтерии


class ControlIn(Input):
    version: int = Field(ge=1)
    status: Literal['to_check', 'verified', 'issue']
    statement_checked: bool = False
    documents_checked: bool = False
    note: str = Field(default='', max_length=2000)


def can_control(user):
    return bool({'request_check', 'pay_bank'} & perms_of(user)) and can_edit_section(user, 'requests', 'ledger')


def _names(s, ids):
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return dict(s.execute(select(User.id, User.name).where(User.id.in_(ids)).execution_options(company_unscoped=True)).all())


def control_row(plan, req, account, names, documents, user):
    fee = plan.actual_fee or 0
    return {'id': plan.id, 'version': plan.version, 'request_id': req.id, 'number': number(req.id), 'counterparty': req.counterparty,
            'purpose': req.purpose, 'department': req.department or '', 'card_account_id': account.id, 'card': account.name,
            'currency': account.currency, 'date': str(plan.paid_date) if plan.paid_date else None, 'reference': plan.paid_reference,
            'amount': money(req.amount), 'fee': money(fee), 'total': money(req.amount + fee),
            'balance_before': money(plan.balance_before) if plan.balance_before is not None else None,
            'balance_after': money(plan.balance_after) if plan.balance_after is not None else None,
            'control_status': plan.control_status, 'statement_checked': plan.statement_checked,
            'documents_checked': plan.documents_checked, 'control_note': plan.control_note,
            'controlled_by': names.get(plan.controlled_by, ''), 'controlled_at': str(plan.controlled_at) if plan.controlled_at else None,
            'documents': documents.get(req.id, 0), 'payment_ledger_id': plan.payment_ledger_id, 'fee_ledger_id': plan.fee_ledger_id,
            'can_control': can_control(user)}


@router.get('/api/treasury/card-control')
def card_control(request: Request, status: str = 'to_check', date_from: Optional[date] = None, date_to: Optional[date] = None):
    with unit() as s:
        user, _ = session_user(s, request, ('view', 'pay_bank', 'request_check'))
        if status not in (*CONTROL_STATES, 'all'):
            raise HTTPException(422, 'Неизвестный статус проверки.')
        query = (select(CardPayment, PaymentRequest, Account).join(PaymentRequest, PaymentRequest.id == CardPayment.request_id)
                 .join(Account, Account.id == CardPayment.card_account_id).where(CardPayment.state == 'paid'))
        if status != 'all':
            query = query.where(CardPayment.control_status == status)
        if date_from:
            query = query.where(CardPayment.paid_date >= date_from)
        if date_to:
            query = query.where(CardPayment.paid_date <= date_to)
        rows = s.execute(query.order_by(CardPayment.paid_date.desc(), CardPayment.id.desc()).limit(500)).all()
        names = _names(s, [plan.controlled_by for plan, _, _ in rows])
        ids = [req.id for _, req, _ in rows]
        documents = dict(s.execute(select(RequestDocument.request_id, func.count(RequestDocument.id)).where(
            RequestDocument.request_id.in_(ids), RequestDocument.active.is_(True)).group_by(RequestDocument.request_id)).all()) if ids else {}
        counts = dict(s.execute(select(CardPayment.control_status, func.count(CardPayment.id)).where(CardPayment.state == 'paid')
                                .group_by(CardPayment.control_status)).all())
        return {'items': [control_row(plan, req, account, names, documents, user) for plan, req, account in rows],
                'counts': {key: int(counts.get(key, 0)) for key in CONTROL_STATES}, 'status': status,
                'can_control': can_control(user)}


@router.post('/api/treasury/card-control/{payment_id}')
def mark_control(payment_id: int, data: ControlIn, request: Request):
    with unit(True) as s:
        user, _ = session_user(s, request)
        if not can_control(user):
            raise HTTPException(403, 'Сверку оплат корпоративной картой отмечает расчётный бухгалтер.')
        act_with_right(user, 'request_check' if 'request_check' in perms_of(user) else 'pay_bank')
        plan = get(s, CardPayment, payment_id)
        if plan.state != 'paid':
            raise HTTPException(409, 'Оплата картой ещё не проведена.')
        if plan.version != data.version:
            raise HTTPException(409, 'Отметка уже изменена другим сотрудником. Обновите реестр.')
        note = data.note.strip()
        if data.status == 'verified' and not (data.statement_checked and data.documents_checked):
            raise HTTPException(422, 'Отметить «Сверено» можно после сверки с банковской выпиской и получения закрывающих документов.')
        if data.status == 'issue' and len(note) < 10:
            raise HTTPException(422, 'Опишите расхождение не короче 10 символов.')
        before = {'status': plan.control_status, 'statement_checked': plan.statement_checked, 'documents_checked': plan.documents_checked}
        plan.control_status = data.status
        plan.statement_checked = data.statement_checked
        plan.documents_checked = data.documents_checked
        plan.control_note = note
        plan.controlled_by = user.id
        plan.controlled_at = now()
        s.flush()
        log(s, user, 'Контроль оплаты корпоративной картой', 'request', plan.request_id, json.dumps({
            'card_payment_id': plan.id, 'before': before, 'after': {'status': data.status, 'statement_checked': data.statement_checked,
            'documents_checked': data.documents_checked}, 'reason': note}, ensure_ascii=False))
        req = get(s, PaymentRequest, plan.request_id)
        account = get(s, Account, plan.card_account_id)
        documents = {req.id: s.scalar(select(func.count(RequestDocument.id)).where(RequestDocument.request_id == req.id,
                                                                                    RequestDocument.active.is_(True)))}
        return control_row(plan, req, account, _names(s, [user.id]), documents, user)


# ---------------------------------------------------------------- валютная заявка и конвертация


class ConversionPlanIn(Input):
    request_version: int = Field(ge=1)
    source_account_id: int = Field(ge=1)
    amount: str = Field(max_length=40)
    rate: str = Field(max_length=40)
    fee_percent: str = Field(default='0', max_length=20)
    fee_category_id: int = Field(ge=1)
    note: str = Field(default='', max_length=1000)


class ConversionStepIn(Input):
    version: int = Field(ge=1)
    date: Optional[dt.date] = None
    reference: str = Field(default='', max_length=120)
    amount: Optional[str] = Field(default=None, max_length=40)
    rate: Optional[str] = Field(default=None, max_length=40)
    fee: Optional[str] = Field(default=None, max_length=40)
    note: str = Field(default='', max_length=1000)


class SupplierPayIn(Input):
    request_version: int = Field(ge=1)
    amount: str = Field(max_length=40)
    date: date
    reference: str = Field(min_length=2, max_length=160)
    note: str = Field(default='', max_length=3000)


def can_plan_conversion(user):
    return bool({'request_edit', 'pay_bank'} & perms_of(user)) and can_edit_section(user, 'requests')


def can_execute(user):
    return 'pay_bank' in perms_of(user) and can_edit_section(user, 'requests', 'ledger')


def family(s, req):
    """Исходная заявка и её оплаченные части (связанные заявки)."""
    link = s.scalar(select(PaymentSplit).where(PaymentSplit.child_request_id == req.id))
    parent = get(s, PaymentRequest, link.parent_request_id) if link else req
    splits = list(s.scalars(select(PaymentSplit).where(PaymentSplit.parent_request_id == parent.id).order_by(PaymentSplit.sequence)))
    children = {c.id: c for c in s.scalars(select(PaymentRequest).where(PaymentRequest.id.in_([x.child_request_id for x in splits])))} if splits else {}
    return parent, link, splits, children


def payment_ledgers(s, request_ids):
    if not request_ids:
        return {}
    return {entry.request_id: entry for entry in s.scalars(select(Ledger).where(Ledger.request_id.in_(request_ids)))}


def fx_account_state(s, account, exclude_request=None):
    balance = account_balance(s, account)
    query = select(func.coalesce(func.sum(PaymentRequest.amount), 0)).where(
        PaymentRequest.account_id == account.id, PaymentRequest.status == 'approved')
    if exclude_request is not None:
        query = query.where(PaymentRequest.id != exclude_request)
    reserved = int(s.scalar(query) or 0)
    return {'balance': balance, 'reserved': reserved, 'free': balance - reserved}


def conversion_leftover(conversion, block_entry=None):
    """Сумы этой конвертации, оставшиеся на блочном счёте."""
    if conversion.state not in ('blocked', 'credited'):
        return 0
    blocked = block_entry.amount if block_entry is not None else conversion.block_amount
    return blocked - (conversion.sold_uzs or 0) - (conversion.actual_fee or 0) - (conversion.returned_uzs or 0)


def conversion_json(s, conversion, accounts, ledgers, user, req):
    block_entry = ledgers.get(conversion.block_ledger_id)
    leftover = conversion_leftover(conversion, block_entry)
    approved = req.status == 'approved' and route_complete(req)
    executor = can_execute(user)

    def leg(entry_id):
        entry = ledgers.get(entry_id)
        if entry is None:
            return None
        account = accounts.get(entry.account_id)
        return {'ledger_id': entry.id, 'date': str(entry.date), 'reference': entry.reference, 'amount': money(entry.amount),
                'kind': entry.kind, 'account': account.name if account else '', 'currency': account.currency if account else ''}

    source = accounts.get(conversion.source_account_id)
    target = accounts.get(conversion.target_account_id)
    block = accounts.get(conversion.block_account_id)
    return {
        'id': conversion.id, 'version': conversion.version, 'state': conversion.state,
        'source_account_id': conversion.source_account_id, 'source_account': source.name if source else '',
        'target_account_id': conversion.target_account_id, 'target_account': target.name if target else '',
        'target_currency': target.currency if target else '', 'block_account': block.name if block else '',
        'planned_amount': money(conversion.planned_amount), 'planned_rate': conversion.planned_rate,
        'planned_uzs': money(conversion.planned_uzs), 'fee_percent': conversion.fee_percent,
        'planned_fee': money(conversion.planned_fee), 'block_amount': money(conversion.block_amount),
        'fee_category_id': conversion.fee_category_id, 'note': conversion.note,
        'block_date': str(conversion.block_date) if conversion.block_date else None, 'block_reference': conversion.block_reference,
        'credit_date': str(conversion.credit_date) if conversion.credit_date else None, 'credit_reference': conversion.credit_reference,
        'rate': conversion.rate, 'sold_uzs': money(conversion.sold_uzs) if conversion.sold_uzs is not None else None,
        'credited_amount': money(conversion.credited_amount) if conversion.credited_amount is not None else None,
        'actual_fee': money(conversion.actual_fee) if conversion.actual_fee is not None else None,
        'effective_rate': effective_rate(conversion.sold_uzs, conversion.credited_amount) if conversion.credited_amount else None,
        'returned_uzs': money(conversion.returned_uzs or 0),
        'return_date': str(conversion.return_date) if conversion.return_date else None, 'return_reference': conversion.return_reference,
        'leftover_uzs': money(max(leftover, 0)),
        'documents': {'block': leg(conversion.block_ledger_id), 'sell': leg(conversion.sell_ledger_id),
                      'buy': leg(conversion.buy_ledger_id), 'fee': leg(conversion.fee_ledger_id), 'return': leg(conversion.return_ledger_id)},
        'can_block': conversion.state == 'planned' and approved and executor,
        'can_credit': conversion.state == 'blocked' and executor,
        'can_return': conversion.state in ('blocked', 'credited') and leftover > 0 and executor,
        'can_cancel': conversion.state == 'planned' and can_plan_conversion(user),
    }


def fx_json(s, user, req, account):
    parent, link, splits, children = family(s, req)
    paid_ids = [c.id for c in children.values()] + [parent.id]
    ledgers_by_request = payment_ledgers(s, paid_ids)
    parts = []
    for split in splits:
        child = children.get(split.child_request_id)
        entry = ledgers_by_request.get(split.child_request_id)
        parts.append({'request_id': split.child_request_id, 'number': number(split.child_request_id), 'sequence': split.sequence,
                      'amount': money(split.amount), 'status': child.status if child else '',
                      'date': str(entry.date) if entry else None, 'reference': entry.reference if entry else '',
                      'ledger_id': entry.id if entry else None})
    final = ledgers_by_request.get(parent.id)
    if final is not None:
        parts.append({'request_id': parent.id, 'number': number(parent.id), 'sequence': len(splits) + 1, 'amount': money(final.amount),
                      'status': parent.status, 'date': str(final.date), 'reference': final.reference, 'ledger_id': final.id})
    requested = parent.amount + sum(x.amount for x in splits)
    paid = sum(x.amount for x in splits if children.get(x.child_request_id) is not None and children[x.child_request_id].status == 'paid')
    if parent.status == 'paid':
        paid += parent.amount
    outstanding = requested - paid
    status = 'paid' if outstanding <= 0 and paid > 0 else 'partial' if paid > 0 else 'unpaid'
    conversions = conversions_for(s, parent.id)
    account_ids = {account.id} | {x for c in conversions for x in (c.source_account_id, c.target_account_id, c.block_account_id) if x}
    accounts = {a.id: a for a in s.scalars(select(Account).where(Account.id.in_(account_ids)))}
    ledger_ids = {x for c in conversions for x in (c.block_ledger_id, c.sell_ledger_id, c.buy_ledger_id, c.fee_ledger_id, c.return_ledger_id) if x}
    ledgers = {e.id: e for e in s.scalars(select(Ledger).where(Ledger.id.in_(ledger_ids)))} if ledger_ids else {}
    converted = sum(c.credited_amount or 0 for c in conversions if c.state == 'credited')
    in_progress = sum(c.planned_amount for c in conversions if c.state in ('planned', 'blocked'))
    # Валюта, купленная под заявку и ещё не оплаченная, закреплена за ней. Свободным остатком
    # заявки считается только купленное сверх заявленной суммы.
    earmarked = max(converted - paid, 0)
    others = fx_account_state(s, account, exclude_request=parent.id)
    spare = max(others['free'] - earmarked, 0)
    chain_need = max(0, outstanding - earmarked - in_progress) if parent.status in ('pending', 'approved') else 0
    need = max(0, chain_need - spare)
    block_waiting = sum(max(conversion_leftover(c, ledgers.get(c.block_ledger_id)), 0) for c in conversions)
    pay_allowed = (parent.status == 'approved' and route_complete(parent) and can_execute(user)
                   and 'pay' in _actions(s, user, parent))
    whole = fx_account_state(s, account)
    planner = parent.status in ('pending', 'approved') and can_plan_conversion(user) and not link
    sources = [{'id': a.id, 'name': a.name} for a in s.scalars(select(Account).where(
        Account.kind == 'bank', Account.currency == 'UZS', Account.archived.is_(False), Account.company_id == account.company_id)
        .order_by(Account.name))] if planner else []
    fee_categories = []
    if planner:
        from .category_catalog import catalog
        fee_categories = [{'id': c.id, 'name': c.name} for c, _ in catalog(s) if c.type == 'outcome' and c.activity not in SYSTEM_ACTIVITIES]
    return {
        'parent': {'id': parent.id, 'number': number(parent.id)} if link else None,
        'sequence': link.sequence if link else None,
        'request_id': parent.id, 'number': number(parent.id), 'currency': account.currency,
        'payment_status': status, 'requested': money(requested), 'paid': money(paid), 'outstanding': money(max(outstanding, 0)),
        'parts': parts,
        'conversions': [conversion_json(s, c, accounts, ledgers, user, parent) for c in conversions],
        'converted': money(converted), 'in_progress': money(in_progress), 'earmarked': money(earmarked),
        'unused_converted': money(max(converted - requested, 0)), 'spare_on_account': money(spare),
        'need_to_convert': money(need), 'block_waiting_uzs': money(block_waiting),
        'account': {'id': account.id, 'name': account.name, 'currency': account.currency, 'balance': money(whole['balance']),
                    'reserved': money(whole['reserved']), 'free': money(whole['free'])},
        'available_for_request': money(min(max(outstanding, 0), earmarked + spare)),
        'source_accounts': sources, 'fee_categories': fee_categories,
        'can_plan': planner,
        'can_pay': pay_allowed and not link,
        'max_payment': money(max(0, min(parent.amount, whole['balance']))) if parent.status == 'approved' else '0.00',
    }


def _actions(s, user, req):
    from .requests_api import request_actions
    return request_actions(s, user, req)


def card_json(s, user, req, account):
    plan = card_for(s, req.id)
    live = card_state(s, account, req.id)
    actions = _actions(s, user, req)
    fee = plan.actual_fee if plan and plan.actual_fee is not None else (plan.fee_amount if plan else 0)
    category = s.get(Category, plan.fee_category_id) if plan and plan.fee_category_id else None
    total = req.amount + fee
    names = _names(s, [plan.controlled_by] if plan else [])
    return {
        'id': plan.id if plan else None, 'version': plan.version if plan else None, 'state': plan.state if plan else 'planned',
        'account_id': account.id, 'account': account.name, 'currency': account.currency,
        'fee_percent': plan.fee_percent if plan else '0.00', 'fee_amount': money(plan.fee_amount if plan else 0),
        'fee_category_id': plan.fee_category_id if plan else None, 'fee_category': category.name if category else '',
        'actual_fee': money(plan.actual_fee) if plan and plan.actual_fee is not None else None,
        'amount': money(req.amount), 'total': money(total),
        'balance': money(live['balance']), 'available': money(live['available']), 'reserved': money(live['reserved']),
        'enough': live['balance'] >= total if req.status == 'approved' else live['available'] >= total,
        'balance_on_save': money(plan.balance_on_save) if plan and plan.balance_on_save is not None else None,
        'available_on_save': money(plan.available_on_save) if plan and plan.available_on_save is not None else None,
        'saved_at': str(plan.saved_at) if plan and plan.saved_at else None,
        'paid_date': str(plan.paid_date) if plan and plan.paid_date else None, 'paid_reference': plan.paid_reference if plan else '',
        'balance_before': money(plan.balance_before) if plan and plan.balance_before is not None else None,
        'balance_after': money(plan.balance_after) if plan and plan.balance_after is not None else None,
        'payment_ledger_id': plan.payment_ledger_id if plan else None, 'fee_ledger_id': plan.fee_ledger_id if plan else None,
        'control_status': plan.control_status if plan else 'none',
        'statement_checked': bool(plan and plan.statement_checked), 'documents_checked': bool(plan and plan.documents_checked),
        'control_note': plan.control_note if plan else '', 'controlled_by': names.get(plan.controlled_by, '') if plan else '',
        'controlled_at': str(plan.controlled_at) if plan and plan.controlled_at else None,
        'can_pay': 'card_pay' in actions and plan is not None and plan.state == 'planned',
        'can_cancel': bool(plan and plan.state == 'paid' and 'request_delete' in perms_of(user)
                           and can_edit_section(user, 'requests', 'ledger')),
        'can_control': bool(plan and plan.state == 'paid' and can_control(user)),
    }


def treasury_view(s, user, req):
    account = get(s, Account, req.account_id)
    result = {'request_id': req.id, 'number': number(req.id), 'status': req.status, 'version': req.version,
              'kind': 'card' if is_card(account) else 'fx' if is_fx(account) else None}
    if result['kind'] == 'card':
        result['card'] = card_json(s, user, req, account)
    elif result['kind'] == 'fx':
        result['fx'] = fx_json(s, user, req, account)
    return result


@router.get('/api/treasury/requests/{id}')
def read_treasury(id: int, request: Request):
    with unit() as s:
        user, _ = session_user(s, request)
        return treasury_view(s, user, _visible(s, user, id))


def _conversion_context(s, user, conversion_id, data):
    conversion = get(s, CurrencyConversion, conversion_id)
    req = _visible(s, user, conversion.request_id)
    if conversion.version != data.version:
        raise HTTPException(409, 'Конвертация уже изменена. Обновите карточку заявки.')
    source = get(s, Account, conversion.source_account_id)
    target = get(s, Account, conversion.target_account_id)
    if target.id != req.account_id or not is_fx(target) or source.kind != 'bank' or source.currency != 'UZS':
        raise HTTPException(409, 'Счета конвертации больше не соответствуют заявке. Нужна проверка.')
    return conversion, req, source, target


def _fact_date(data, *floors):
    if data.date is None:
        raise HTTPException(422, 'Укажите фактическую дату банковского документа.')
    if data.date > today():
        raise HTTPException(422, 'Подтверждайте только фактически выполненный этап, без будущей даты.')
    floor = max(f for f in floors if f is not None)
    if data.date < floor:
        raise HTTPException(422, f'Дата не может быть раньше {floor}.')
    if len(data.reference.strip()) < 1:
        raise HTTPException(422, 'Укажите номер банковского документа.')
    return data.date


STEP_DOCUMENTS = {
    'block': (CurrencyConversion.source_account_id, CurrencyConversion.block_reference, CurrencyConversion.block_date),
    'credit': (CurrencyConversion.target_account_id, CurrencyConversion.credit_reference, CurrencyConversion.credit_date),
    'return': (CurrencyConversion.source_account_id, CurrencyConversion.return_reference, CurrencyConversion.return_date),
}


def _document_unused(s, step, account_id, reference, conversion_id, day):
    """Один банковский документ учитывается один раз: ни другой конвертацией, ни операцией журнала.

    Проводки конвертации хранят служебные номера FX-…, поэтому номер банка сверяется с документами
    всех конвертаций по тому же счёту и дате, а не только с журналом."""
    account_field, reference_field, date_field = STEP_DOCUMENTS[step]
    other = s.scalar(select(CurrencyConversion.id).where(CurrencyConversion.id != conversion_id, account_field == account_id,
                                                         reference_field == reference, date_field == day).limit(1))
    if other is not None or s.scalar(select(Ledger.id).where(Ledger.account_id == account_id, Ledger.reference == reference,
                                                             Ledger.date == day).limit(1)) is not None:
        raise HTTPException(409, 'Этот банковский документ уже учтён. Повторный денежный факт заблокирован.')


@router.post('/api/treasury/requests/{id}/conversions')
def plan_conversion(id: int, data: ConversionPlanIn, request: Request):
    with unit(True) as s:
        user, _ = session_user(s, request)
        req = _visible(s, user, id)
        target = get(s, Account, req.account_id)
        if not can_plan_conversion(user):
            raise HTTPException(403, 'Конвертацию планирует финансовый директор, директор или расчётный бухгалтер.')
        act_with_right(user, 'request_edit' if 'request_edit' in perms_of(user) else 'pay_bank')
        if not is_fx(target):
            raise HTTPException(422, 'Конвертация доступна для валютной банковской заявки.')
        if s.scalar(select(PaymentSplit.id).where(PaymentSplit.child_request_id == req.id)) is not None:
            raise HTTPException(409, 'Конвертацию планируют в исходной валютной заявке.')
        if req.status not in ('pending', 'approved'):
            raise HTTPException(409, 'Конвертацию планируют для заявки на согласовании или утверждённой заявки.')
        if req.version != data.request_version:
            raise HTTPException(409, 'Заявка изменилась. Обновите карточку.')
        source = get(s, Account, data.source_account_id)
        if source.kind != 'bank' or source.currency != 'UZS' or source.archived or source.company_id != target.company_id:
            raise HTTPException(422, 'Выберите действующий сумовой банковский счёт той же компании.')
        bought = amount(data.amount)
        rate = rate_value(data.rate)
        percent = percent_value(data.fee_percent)
        uzs = uzs_for(bought, rate)
        fee = fee_of(uzs, percent)
        if uzs + fee > 10_000_000_000_000_000:
            raise HTTPException(422, 'Сумма конвертации слишком велика.')
        category = active_outcome_category(s, data.fee_category_id, 'Для комиссии за конвертацию выберите действующую расходную статью.')
        conversion = CurrencyConversion(company_id=target.company_id, request_id=req.id, source_account_id=source.id,
                                        target_account_id=target.id, planned_amount=bought, planned_rate=rate_text(rate),
                                        planned_uzs=uzs, fee_percent=format(percent, '.2f'), planned_fee=fee,
                                        block_amount=uzs + fee, fee_category_id=category.id, state='planned',
                                        note=data.note, created_by=user.id)
        s.add(conversion)
        s.flush()
        log(s, user, 'Запланирована конвертация', 'request', req.id, json.dumps({
            'conversion_id': conversion.id, 'source_account_id': source.id, 'amount': money(bought), 'currency': target.currency,
            'rate': conversion.planned_rate, 'uzs': money(uzs), 'fee': money(fee), 'block_amount': money(uzs + fee),
            'note': data.note}, ensure_ascii=False))
        return treasury_view(s, user, req)


@router.post('/api/treasury/conversions/{conversion_id}/{action}')
def conversion_step(conversion_id: int, action: str, data: ConversionStepIn, request: Request):
    if action not in ('block', 'credit', 'return', 'cancel'):
        raise HTTPException(404, 'Этап не найден.')
    with unit(True) as s:
        user, _ = session_user(s, request)
        conversion, req, source, target = _conversion_context(s, user, conversion_id, data)
        prefix = f'FX-{conversion.id}'
        if action == 'cancel':
            if not can_plan_conversion(user):
                raise HTTPException(403, 'Отменить план конвертации может финансовый директор, директор или расчётный бухгалтер.')
            act_with_right(user, 'request_edit' if 'request_edit' in perms_of(user) else 'pay_bank')
            if conversion.state != 'planned':
                raise HTTPException(409, 'Отменить можно только запланированную конвертацию. Переведённые сумы возвращаются отдельным этапом.')
            if len(data.note.strip()) < 10:
                raise HTTPException(422, 'Укажите причину отмены не короче 10 символов.')
            conversion.state = 'cancelled'
            conversion.note = (conversion.note + '\n' if conversion.note else '') + 'Отмена: ' + data.note.strip()
            s.flush()
            log(s, user, 'Отменена запланированная конвертация', 'request', req.id,
                json.dumps({'conversion_id': conversion.id, 'reason': data.note}, ensure_ascii=False))
            return treasury_view(s, user, req)
        if not can_execute(user):
            raise HTTPException(403, 'Банковские этапы конвертации подтверждает расчётный бухгалтер.')
        act_with_right(user, 'pay_bank')
        if action == 'block':
            if conversion.state != 'planned':
                raise HTTPException(409, 'Этот этап уже проведён или пока недоступен. Обновите заявку, не повторяйте денежный факт.')
            if req.status != 'approved' or not route_complete(req):
                raise HTTPException(409, 'Сначала заявка должна пройти все этапы согласования.')
            if source.archived:
                raise HTTPException(409, 'Сумовой счёт в архиве. Сначала восстановите его.')
            day = _fact_date(data, source.opening_date, local_date(req.approved_at or req.created_at))
            reference = data.reference.strip()
            _document_unused(s, 'block', source.id, reference, conversion.id, day)
            value = amount(data.amount) if data.amount else conversion.block_amount
            if value < conversion.planned_uzs + conversion.planned_fee and len(data.note.strip()) < 10:
                raise HTTPException(422, 'Сумма перевода меньше плановой покупки с комиссией: укажите пояснение не короче 10 символов.')
            # Перевод на блочный счёт не забирает деньги, обещанные утверждённым сумовым заявкам.
            enforce_reserved_funds(s, source, day, value, include_bank_hold=False)
            block = block_account(s, user, source)
            entry = Ledger(account_id=source.id, to_account_id=block.id, kind='transfer', amount=value, date=day,
                           reference=prefix + '-BLOCK', counterparty='Блочный счёт под конвертацию',
                           note=f'Перевод на блочный счёт по {number(req.id)}; документ банка {reference}. {data.note}'.strip(),
                           creator_id=user.id)
            s.add(entry)
            s.flush()
            validate_running_balance(s, source)
            validate_running_balance(s, block)
            conversion.block_account_id = block.id
            conversion.block_ledger_id = entry.id
            conversion.block_amount = value
            conversion.block_date = day
            conversion.block_reference = reference
            conversion.state = 'blocked'
            label = 'Сумы переведены на блочный счёт'
        elif action == 'credit':
            if conversion.state != 'blocked':
                raise HTTPException(409, 'Зачисление валюты учитывается после перевода сумов на блочный счёт.')
            block = get(s, Account, conversion.block_account_id)
            day = _fact_date(data, conversion.block_date, target.opening_date)
            reference = data.reference.strip()
            if data.amount is None or data.rate is None:
                raise HTTPException(422, 'Укажите зачисленную сумму валюты и курс конвертации.')
            bought = amount(data.amount)
            rate = rate_value(data.rate)
            sold = uzs_for(bought, rate)
            fee = amount(data.fee, allow_zero=True) if data.fee not in (None, '') else fee_of(sold, percent_value(conversion.fee_percent))
            block_entry = get(s, Ledger, conversion.block_ledger_id)
            available = block_entry.amount - (conversion.returned_uzs or 0)
            if sold + fee > available:
                raise HTTPException(409,
                    f'На блочном счёте по этой конвертации {money(available)} UZS, требуется {money(sold + fee)} UZS '
                    f'(покупка {money(sold)} + комиссия {money(fee)}). Проверьте курс и сумму или переведите недостающие сумы новой конвертацией.')
            if fee:
                state = budget_state(s, conversion.fee_category_id, str(day)[:7], block.currency, fee)
                if state['over'] and state['mode'] == 'hard':
                    raise HTTPException(409, 'Жёсткий лимит статьи комиссии за конвертацию превышен. Сначала пересмотрите бюджет.')
            _document_unused(s, 'credit', target.id, reference, conversion.id, day)
            out_category = conversion_category(s, 'out')
            in_category = conversion_category(s, 'in')
            label_text = f'Конвертация UZS → {target.currency} по {number(req.id)}, курс {rate_text(rate)}; документ банка {reference}'
            sell = Ledger(account_id=block.id, category_id=out_category, kind='out', amount=sold, date=day,
                          reference=prefix + '-SELL', counterparty='Конвертация валюты', note=label_text, creator_id=user.id)
            buy = Ledger(account_id=target.id, category_id=in_category, kind='in', amount=bought, date=day,
                         reference=prefix + '-BUY', counterparty='Конвертация валюты', note=label_text, creator_id=user.id)
            s.add_all([sell, buy])
            s.flush()
            fee_entry = None
            if fee:
                fee_entry = Ledger(account_id=block.id, category_id=conversion.fee_category_id, kind='out', amount=fee, date=day,
                                   reference=prefix + '-FEE', counterparty='Комиссия банка за конвертацию',
                                   note=f'Комиссия за конвертацию по {number(req.id)}; документ банка {reference}', creator_id=user.id)
                s.add(fee_entry)
                s.flush()
            validate_running_balance(s, block)
            validate_running_balance(s, target)
            conversion.credit_date = day
            conversion.credit_reference = reference
            conversion.rate = rate_text(rate)
            conversion.sold_uzs = sold
            conversion.credited_amount = bought
            conversion.actual_fee = fee
            conversion.sell_ledger_id = sell.id
            conversion.buy_ledger_id = buy.id
            conversion.fee_ledger_id = fee_entry.id if fee_entry else None
            conversion.state = 'credited'
            label = 'Зачислена валюта по конвертации'
        else:
            if conversion.state not in ('blocked', 'credited'):
                raise HTTPException(409, 'Возврат доступен для сумов на блочном счёте.')
            block = get(s, Account, conversion.block_account_id)
            leftover = conversion_leftover(conversion, get(s, Ledger, conversion.block_ledger_id))
            if leftover <= 0:
                raise HTTPException(409, 'На блочном счёте по этой конвертации не осталось сумов.')
            if conversion.return_ledger_id is not None:
                raise HTTPException(409, 'Остаток уже возвращён.')
            day = _fact_date(data, conversion.credit_date or conversion.block_date, source.opening_date)
            reference = data.reference.strip()
            _document_unused(s, 'return', source.id, reference, conversion.id, day)
            entry = Ledger(account_id=block.id, to_account_id=source.id, kind='transfer', amount=leftover, date=day,
                           reference=prefix + '-RETURN', counterparty='Возврат с блочного счёта',
                           note=f'Возврат неиспользованных сумов по {number(req.id)}; документ банка {reference}. {data.note}'.strip(),
                           creator_id=user.id)
            s.add(entry)
            s.flush()
            validate_running_balance(s, block)
            validate_running_balance(s, source)
            conversion.returned_uzs = (conversion.returned_uzs or 0) + leftover
            conversion.return_date = day
            conversion.return_reference = reference
            conversion.return_ledger_id = entry.id
            if conversion.state == 'blocked':
                conversion.state = 'cancelled'
            label = 'Неиспользованные сумы возвращены с блочного счёта'
        s.flush()
        log(s, user, label, 'request', req.id, json.dumps({
            'conversion_id': conversion.id, 'action': action, 'state': conversion.state, 'date': str(data.date),
            'reference': data.reference, 'note': data.note, 'block_ledger_id': conversion.block_ledger_id,
            'sell_ledger_id': conversion.sell_ledger_id, 'buy_ledger_id': conversion.buy_ledger_id,
            'fee_ledger_id': conversion.fee_ledger_id, 'return_ledger_id': conversion.return_ledger_id}, ensure_ascii=False))
        return treasury_view(s, user, req)


SPLIT_FIELDS = ('creator_id', 'category_id', 'account_id', 'counterparty', 'counterparty_inn', 'project', 'department',
                'due_date', 'approved_by', 'finance_approved_by', 'last_editor_id', 'priority', 'created_at', 'approved_at',
                'approved_overrun', 'checked_by', 'checked_at', 'route_policy', 'route_threshold', 'route_at', 'finance_author')


@router.post('/api/treasury/requests/{id}/supplier-pay')
def supplier_pay(id: int, data: SupplierPayIn, request: Request):
    """«Расход => Поставщик»: полная или частичная оплата валютной заявки с валютного счёта."""
    from .request_retention import next_request_id
    from .services import post_ledger
    with unit(True) as s:
        user, _ = session_user(s, request)
        req = _visible(s, user, id)
        account = get(s, Account, req.account_id)
        if not is_fx(account):
            raise HTTPException(422, 'Частичная оплата поставщику доступна для валютной банковской заявки.')
        if s.scalar(select(PaymentSplit.id).where(PaymentSplit.child_request_id == req.id)) is not None:
            raise HTTPException(409, 'Оплачивайте исходную валютную заявку, а не её часть.')
        _require_money_section(user)
        if req.status != 'approved' or not route_complete(req):
            raise HTTPException(409, 'Оплатить можно только утверждённую заявку, прошедшую все этапы согласования.')
        if req.version != data.request_version:
            raise HTTPException(409, 'Заявка изменилась. Обновите карточку.')
        check_payer(s, user, req)
        value = amount(data.amount)
        if value > req.amount:
            raise HTTPException(422, f'Сумма больше остатка заявки к оплате: {money(req.amount)} {account.currency}.')
        target = req
        if value < req.amount:
            sequence = (s.scalar(select(func.max(PaymentSplit.sequence)).where(PaymentSplit.parent_request_id == req.id)) or 0) + 1
            before = req.amount
            child = PaymentRequest(id=next_request_id(s), amount=value, status='approved',
                                   purpose=f'Часть {sequence} оплаты заявки {number(req.id)}. {req.purpose}'[:3000],
                                   decision_note=f'Частичная оплата заявки {number(req.id)}',
                                   **{field: getattr(req, field) for field in SPLIT_FIELDS})
            s.add(child)
            req.amount = before - value
            s.flush()
            s.add(PaymentSplit(company_id=account.company_id, parent_request_id=req.id, child_request_id=child.id,
                               sequence=sequence, amount=value, parent_amount_before=before, created_by=user.id))
            s.flush()
            log(s, user, 'Выделена часть оплаты поставщику', 'request', req.id, json.dumps({
                'child_request_id': child.id, 'sequence': sequence, 'amount': money(value), 'before': money(before),
                'after': money(req.amount)}, ensure_ascii=False))
            log(s, user, 'Создана часть оплаты заявки', 'request', child.id, json.dumps({
                'parent_request_id': req.id, 'sequence': sequence, 'amount': money(value)}, ensure_ascii=False))
            target = child
        post_ledger(s, user, SimpleNamespace(
            account_id=account.id, to_account_id=None, category_id=target.category_id, kind='out', amount=money(value),
            date=data.date, counterparty=target.counterparty, reference=data.reference.strip(), note=data.note,
            request_id=target.id, request_version=target.version, receipt_id=None), manual_payment=True)
        return treasury_view(s, user, req)


# ---------------------------------------------------------------- отчёт


def _period(date_from, date_to):
    end = date_to or today()
    start = date_from or end.replace(day=1)
    if start > end:
        raise HTTPException(422, 'Начало периода позже окончания.')
    return start, end


@router.get('/api/treasury/report')
def treasury_report(request: Request, date_from: Optional[date] = None, date_to: Optional[date] = None):
    """«Движение по корп. картам и валютным заявкам». Остатки — по журналу, обороты — без сторнированных пар."""
    start, end = _period(date_from, date_to)
    with unit() as s:
        user, _ = session_user(s, request, ('view', 'pay_bank', 'request_check'))
        accounts = list(s.scalars(select(Account).order_by(Account.name)))
        cards = [a for a in accounts if a.kind == 'card' and (not a.archived or account_balance(s, a, end) != 0)]
        fee_ids = {row[0] for row in s.execute(select(CardPayment.fee_ledger_id).where(CardPayment.fee_ledger_id.is_not(None)))}
        section1 = []
        for card in cards:
            if card.opening_date > end:
                continue
            # Карта, открытая внутри периода, начинает его со своего начального остатка.
            first = max(start, card.opening_date)
            opening = day_start_balance(s, card, first)
            flows = list(s.scalars(effective_cashflows(s).where(Ledger.date >= first, Ledger.date <= end,
                                   (Ledger.account_id == card.id) | (Ledger.to_account_id == card.id))))
            top_ups = sum(t.amount for t in flows if (t.kind == 'in' and t.account_id == card.id) or (t.kind == 'transfer' and t.to_account_id == card.id))
            moved_out = sum(t.amount for t in flows if t.kind == 'transfer' and t.account_id == card.id)
            spent = sum(t.amount for t in flows if t.kind == 'out' and t.account_id == card.id and t.id not in fee_ids)
            fees = sum(t.amount for t in flows if t.kind == 'out' and t.account_id == card.id and t.id in fee_ids)
            closing = account_balance(s, card, end)
            statuses = dict(s.execute(select(CardPayment.control_status, func.count(CardPayment.id)).where(
                CardPayment.card_account_id == card.id, CardPayment.state == 'paid', CardPayment.paid_date >= start,
                CardPayment.paid_date <= end).group_by(CardPayment.control_status)).all())
            control = {key: int(statuses.get(key, 0)) for key in CONTROL_STATES}
            section1.append({'account_id': card.id, 'card': card.name, 'currency': card.currency, 'opening': money(opening),
                             'top_ups': money(top_ups - moved_out), 'spent': money(spent), 'fees': money(fees),
                             'closing': money(closing), 'control': control,
                             'control_status': 'issue' if control['issue'] else 'to_check' if control['to_check'] else 'verified' if control['verified'] else 'none',
                             'balanced': opening + top_ups - moved_out - spent - fees == closing})
        fx_accounts = [a for a in accounts if is_fx(a)]
        fx_ids = {a.id for a in fx_accounts}
        children = set(s.scalars(select(PaymentSplit.child_request_id)))
        requests = [r for r in s.scalars(select(PaymentRequest).where(PaymentRequest.account_id.in_(fx_ids),
                    PaymentRequest.status.in_(('pending', 'approved', 'paid'))).order_by(PaymentRequest.id)) if r.id not in children] if fx_ids else []
        account_by_id = {a.id: a for a in accounts}
        rows = []
        totals = {}
        for req in requests:
            parent, _, splits, kids = family(s, req)
            conversions = conversions_for(s, req.id)
            ledgers = payment_ledgers(s, [req.id] + [x.child_request_id for x in splits])
            events = [e.date for e in ledgers.values()] + [d for c in conversions for d in (c.block_date, c.credit_date) if d]
            if req.status == 'paid' and not any(start <= d <= end for d in events):
                continue
            requested = req.amount + sum(x.amount for x in splits)
            paid = sum(x.amount for x in splits if kids.get(x.child_request_id) is not None and kids[x.child_request_id].status == 'paid'
                       and ledgers.get(x.child_request_id) is not None and ledgers[x.child_request_id].date <= end)
            if req.status == 'paid' and ledgers.get(req.id) is not None and ledgers[req.id].date <= end:
                paid += req.amount
            blocked = sum(c.block_amount for c in conversions if c.block_date and c.block_date <= end and c.state in ('blocked', 'credited'))
            blocked += sum(c.block_amount for c in conversions if c.block_date and c.block_date <= end and c.state == 'cancelled' and c.block_ledger_id)
            returned = sum(c.returned_uzs or 0 for c in conversions if c.return_date and c.return_date <= end)
            converted = sum(c.credited_amount or 0 for c in conversions if c.credit_date and c.credit_date <= end)
            currency = account_by_id[req.account_id].currency
            free = max(converted - requested, 0)
            status = 'paid' if paid >= requested and paid > 0 else 'partial' if paid > 0 else 'unpaid'
            row = {'request_id': req.id, 'number': number(req.id), 'counterparty': req.counterparty, 'currency': currency,
                   'status': status, 'request_status': req.status, 'requested': money(requested), 'blocked_uzs': money(blocked),
                   'returned_uzs': money(returned), 'converted': money(converted), 'paid': money(paid),
                   'outstanding': money(max(requested - paid, 0)), 'free': money(free)}
            rows.append(row)
            total = totals.setdefault(currency, {'requested': 0, 'blocked_uzs': 0, 'returned_uzs': 0, 'converted': 0, 'paid': 0, 'outstanding': 0, 'free': 0})
            for key, value in (('requested', requested), ('blocked_uzs', blocked), ('returned_uzs', returned), ('converted', converted),
                               ('paid', paid), ('outstanding', max(requested - paid, 0)), ('free', free)):
                total[key] += value
        balances = []
        for account in fx_accounts:
            if account.archived and account_balance(s, account) == 0:
                continue
            state = fx_account_state(s, account)
            balances.append({'account_id': account.id, 'account': account.name, 'currency': account.currency,
                             'balance': money(state['balance']), 'reserved': money(state['reserved']), 'free': money(state['free'])})
        blocks = [{'account_id': a.id, 'account': a.name, 'currency': a.currency, 'balance': money(account_balance(s, a))}
                  for a in accounts if a.kind == 'block']
        return {'date_from': str(start), 'date_to': str(end), 'cards': section1,
                'fx': {'requests': rows, 'totals': {cur: {k: money(v) for k, v in t.items()} for cur, t in totals.items()},
                       'accounts': balances, 'block_accounts': blocks}}
