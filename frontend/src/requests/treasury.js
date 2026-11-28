// Казначейство в заявке: канал «Корпоративная карта» и валютная заявка с конвертацией через блочный счёт.
// Денежные расчёты — в сотых долях BigInt, без float, с округлением половины вверх один раз;
// сервер повторяет их и решает окончательно (app/treasury.py).
import {N_, tx} from '../i18n/index.js';
import {ungroupAmount} from '../amountInput.js';

export const CONTROL_STATES = {
  none: N_('Не оплачено'), to_check: N_('Ожидает сверки'), verified: N_('Сверено'), issue: N_('Расхождение'),
};
export const CONTROL_PILLS = {none: 'gray', to_check: 'warn', verified: 'good', issue: 'bad'};
export const PAYMENT_STATES = {unpaid: N_('Не оплачена'), partial: N_('Частично оплачена'), paid: N_('Оплачена'),
  closed: N_('Оплачена частично, остаток снят')};
export const PAYMENT_PILLS = {unpaid: 'warn', partial: 'plan', paid: 'good', closed: 'gray'};
export const CONVERSION_STATES = {
  planned: N_('Запланирована'), blocked: N_('Сумы на блочном счёте'), credited: N_('Валюта зачислена'), cancelled: N_('Отменена'),
};
export const CONVERSION_PILLS = {planned: 'gray', blocked: 'warn', credited: 'good', cancelled: 'gray'};

// Значение в сотых долях (scale=2) или в десятитысячных (scale=4, курс).
export function scaled(value, scale = 2) {
  const raw = ungroupAmount(value);
  const pattern = scale === 4 ? /^\d{1,15}(?:\.\d{1,4})?$/ : /^\d{1,15}(?:\.\d{1,2})?$/;
  if (!pattern.test(raw)) return null;
  const [whole, fraction = ''] = raw.split('.');
  return BigInt(whole) * 10n ** BigInt(scale) + BigInt(fraction.padEnd(scale, '0') || '0');
}
export const units = value => scaled(value, 2);
export const decimal = value => {
  const sign = value < 0n ? '-' : '', abs = value < 0n ? -value : value;
  return `${sign}${abs / 100n}.${String(abs % 100n).padStart(2, '0')}`;
};
const roundDiv = (value, divisor) => (value + divisor / 2n) / divisor;

// Комиссия от суммы: процент с двумя знаками (1,25% = 125 сотых процента).
export function feeOf(base, percent) {
  return roundDiv(base * percent, 10000n);
}

export function cardAmounts(amount, feePercent) {
  const value = units(amount), percent = units(feePercent === '' || feePercent == null ? '0' : feePercent);
  if (value == null || value <= 0n) return {error: N_('Укажите положительную сумму оплаты с точностью до двух знаков.')};
  if (percent == null || percent >= 10000n) return {error: N_('Комиссия должна быть от 0 до 99,99%.')};
  const fee = feeOf(value, percent);
  return {amount: decimal(value), fee_amount: decimal(fee), total: decimal(value + fee)};
}

// Сумы (тийины) за валюту (центы) по курсу «сумов за единицу» с четырьмя знаками.
export function conversionAmounts(amount, rate, feePercent) {
  const value = units(amount), price = scaled(rate, 4), percent = units(feePercent === '' || feePercent == null ? '0' : feePercent);
  if (value == null || value <= 0n) return {error: N_('Укажите сумму валюты с точностью до двух знаков.')};
  if (price == null || price <= 0n) return {error: N_('Укажите курс: сумов за единицу валюты, до четырёх знаков после запятой.')};
  if (percent == null || percent >= 10000n) return {error: N_('Комиссия должна быть от 0 до 99,99%.')};
  const uzs = roundDiv(value * price, 10000n), fee = feeOf(uzs, percent);
  return {amount: decimal(value), uzs: decimal(uzs), fee: decimal(fee), block: decimal(uzs + fee)};
}

// Зачисление валюты: покупка по фактическому курсу, комиссия и остаток на блочном счёте.
// Пустая комиссия считается, как на сервере: процент плана от фактической покупки.
export function creditAmounts(amount, rate, fee, blocked, feePercent = '0') {
  const value = units(amount), price = scaled(rate, 4), available = units(blocked), automatic = fee === '' || fee == null;
  const typed = automatic ? 0n : units(fee), percent = units(feePercent === '' || feePercent == null ? '0' : feePercent);
  if (value == null || value <= 0n) return {error: N_('Укажите зачисленную сумму валюты.')};
  if (price == null || price <= 0n) return {error: N_('Укажите курс: сумов за единицу валюты, до четырёх знаков после запятой.')};
  if (typed == null || (automatic && percent == null)) return {error: N_('Комиссия указывается суммой в сумах с точностью до двух знаков.')};
  const sold = roundDiv(value * price, 10000n), charge = automatic ? feeOf(sold, percent) : typed;
  const leftover = available == null ? null : available - sold - charge;
  return {sold: decimal(sold), fee: decimal(charge), automatic, leftover: leftover == null ? null : decimal(leftover), enough: leftover == null || leftover >= 0n};
}

export const isCardAccount = account => account?.kind === 'card';
export const isFxRequest = request => !!request && (request.channel || request.account_kind) === 'bank' && request.currency && request.currency !== 'UZS';
export const treasuryKind = request => (request?.channel || request?.account_kind) === 'card' ? 'card' : isFxRequest(request) ? 'fx' : null;

export function cardAccounts(accounts, companyId) {
  return (accounts || []).filter(account => account.kind === 'card' && !account.archived
    && (account.company_id == null || Number(account.company_id) === Number(companyId)));
}

// Сумма к оплате поставщику по умолчанию: остаток заявки, но не больше остатка валютного счёта.
export function defaultSupplierPayment(fx) {
  const outstanding = units(fx?.outstanding), limit = units(fx?.max_payment);
  if (outstanding == null) return '';
  if (limit == null || limit <= 0n) return decimal(outstanding);
  return decimal(outstanding < limit ? outstanding : limit);
}

export function supplierPaymentKind(fx, amount) {
  const value = units(amount), outstanding = units(fx?.outstanding);
  if (value == null || value <= 0n || outstanding == null) return '';
  if (value > outstanding) return 'excess';
  return value === outstanding ? 'full' : 'partial';
}

// Сумма для сервера: «10 500,50» → «10500.50». Нераспознанное значение уходит как есть: сервер ответит понятной ошибкой.
export const plainAmount = value => {const n = units(value); return n == null ? value : decimal(n);};
export const positive = value => {const n = units(value); return n != null && n > 0n;};
export const negative = value => {const raw = String(value ?? '').trim(); return raw.startsWith('-') && units(raw.slice(1)) > 0n;};
// Дата документа: 2026-11-28 → 28.11.2026.
export const dateRu = value => /^\d{4}-\d{2}-\d{2}/.test(String(value || '')) ? String(value).slice(0, 10).split('-').reverse().join('.') : (value || '');

export function controlLabel(status) {
  return tx(CONTROL_STATES[status] || status || '');
}
