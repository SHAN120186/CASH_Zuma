import {N_, tx} from '../i18n/index.js';
import {ungroupAmount} from '../amountInput.js';
import {stageLabel} from './workflow.js';

export const CASH_FUNDING_STATES = {
  unfunded: N_('Ожидает снятия из банка'),
  in_transit: N_('Деньги в пути'),
  received: N_('Наличные получены в кассу'),
};

function units(value) {
  const raw = ungroupAmount(value);
  if (!/^\d{1,15}(?:\.\d{1,2})?$/.test(raw)) return null;
  const [whole, fraction = ''] = raw.split('.');
  return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
}
const decimal = value => `${value / 100n}.${String(value % 100n).padStart(2, '0')}`;

// Gross is the complete bank debit, including the fee. Round half up exactly
// once in currency minor units; never calculate financial inputs with floats.
export function cashFundingAmounts(grossAmount, feePercent) {
  const gross = units(grossAmount), percent = units(feePercent);
  if (gross == null || gross <= 0n) return {error: N_('Укажите положительную сумму списания с банка с точностью до двух знаков.')};
  if (percent == null || percent >= 10000n) return {error: N_('Комиссия должна быть от 0 до 99,99%.')};
  const fee = (gross * percent + 5000n) / 10000n;
  if (gross - fee <= 0n) return {error: N_('После комиссии должна остаться сумма наличной заявки.')};
  return {gross_amount: decimal(gross), fee_amount: decimal(fee), net_amount: decimal(gross - fee), fee_percent: decimal(percent)};
}

export function fundingBankAccounts(accounts, currency, companyId) {
  return (accounts || []).filter(account => account.kind === 'bank' && account.currency === currency && !account.archived
    && (account.company_id == null || Number(account.company_id) === Number(companyId)));
}

export const manualAccounts = accounts => (accounts || []).filter(account => ['bank', 'cash'].includes(account.kind));
export const accountKindLabel = kind => kind === 'transit' ? tx('Деньги в пути') : kind === 'bank' ? tx('Банковский счёт') : tx('Касса');
export const hasCashFundingFee = event => {const fee=units(event?.cash_funding_fee);return fee!==null&&fee>0n;};

export function cashFundingBody(value) {
  if (!value) return null;
  return {bank_account_id: value.bank_account_id == null ? null : Number(value.bank_account_id),
    mode: value.mode || 'single',
    gross_amount: ungroupAmount(value.gross_amount), fee_percent: ungroupAmount(value.fee_percent),
    fee_category_id: value.fee_category_id == null ? null : Number(value.fee_category_id)};
}

export function fundingActionAvailable(request, action) {
  const funding = request?.cash_funding;
  return !!funding && request.status === 'approved' && (request.actions || []).includes(`cash_${action}`)
    && funding[`can_${action}`] === true;
}

export function fundingStatus(request) {
  if (request?.status === 'paid') return tx('Оплачена наличными');
  if (['draft', 'returned'].includes(request?.status)) return tx('Документ ещё не согласован.');
  if (request?.status && request.status !== 'approved') return stageLabel(request);
  return tx(CASH_FUNDING_STATES[request?.cash_funding?.state] || N_('Наличные через банк'));
}

export function tashkentToday() {
  const parts = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Tashkent', year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}
