import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cardAmounts, conversionAmounts, creditAmounts, defaultSupplierPayment, supplierPaymentKind, treasuryKind, cardAccounts,
        scaled, positive} from '../src/requests/treasury.js';
import {CHANNELS, requiredDocs} from '../src/requests/workflow.js';
import {manualAccounts, accountKindLabel} from '../src/requests/cashFunding.js';
import {cardPlanBody, requestBody} from '../src/requests/saveFlow.js';
import {setLang, tx} from '../src/i18n/index.js';

test('card payment adds the fee once, rounding half up in minor units', () => {
  assert.deepEqual(cardAmounts('1000000', '1'), {amount: '1000000.00', fee_amount: '10000.00', total: '1010000.00'});
  assert.deepEqual(cardAmounts('99 500', '1'), {amount: '99500.00', fee_amount: '995.00', total: '100495.00'});
  assert.equal(cardAmounts('0.50', '1').fee_amount, '0.01');
  assert.equal(cardAmounts('0.49', '1').fee_amount, '0.00');
  assert.equal(cardAmounts('1000', '').fee_amount, '0.00');
  assert.ok(cardAmounts('1000', '100').error);
  assert.ok(cardAmounts('-5', '1').error);
});

test('conversion plan converts cents by a four-digit rate and adds the bank fee to the block transfer', () => {
  assert.deepEqual(conversionAmounts('10000', '12850.50', '0.5'),
    {amount: '10000.00', uzs: '128505000.00', fee: '642525.00', block: '129147525.00'});
  assert.equal(conversionAmounts('0.01', '12850.5555', '0').uzs, '128.51');
  assert.equal(scaled('12850.5555', 4), 128505555n);
  assert.ok(conversionAmounts('100', '12850.55555', '0').error);
  assert.ok(conversionAmounts('100', '0', '0').error);
});

test('currency credit reports the block account leftover and a shortage', () => {
  assert.deepEqual(creditAmounts('10050', '12780', '642000', '129147525.00'),
    {sold: '128439000.00', fee: '642000.00', automatic: false, leftover: '66525.00', enough: true});
  assert.equal(creditAmounts('10050', '12900', '0', '129147525.00').enough, false);
});

test('an empty conversion fee is the planned percentage of the actual purchase, as on the server', () => {
  // 0,5% of 10 050 USD at 12 780 = 0,5% of 128 439 000 = 642 195 UZS, not the planned 642 525.
  assert.deepEqual(creditAmounts('10050', '12780', '', '129147525.00', '0.50'),
    {sold: '128439000.00', fee: '642195.00', automatic: true, leftover: '66330.00', enough: true});
  assert.equal(creditAmounts('10050', '12780', '0', '129147525.00', '0.50').fee, '0.00');
});

test('supplier payment defaults to the smaller of the outstanding amount and the account balance', () => {
  assert.equal(defaultSupplierPayment({outstanding: '10000.00', max_payment: '6000.00'}), '6000.00');
  assert.equal(defaultSupplierPayment({outstanding: '4000.00', max_payment: '4050.00'}), '4000.00');
  assert.equal(defaultSupplierPayment({outstanding: '4000.00', max_payment: '0.00'}), '4000.00');
  assert.equal(supplierPaymentKind({outstanding: '4000.00'}, '1000'), 'partial');
  assert.equal(supplierPaymentKind({outstanding: '4000.00'}, '4 000'), 'full');
  assert.equal(supplierPaymentKind({outstanding: '4000.00'}, '4000.01'), 'excess');
  assert.equal(supplierPaymentKind({outstanding: '4000.00'}, ''), '');
  assert.equal(positive('0.00'), false);
  assert.equal(positive('0.01'), true);
});

test('card channel needs only the indent and is a manual money account; the block account is not', () => {
  assert.equal(CHANNELS.card, 'Корпоративная карта');
  assert.deepEqual(requiredDocs('card'), ['internal']);
  assert.deepEqual(requiredDocs('bank'), ['internal', 'contract']);
  const accounts = [{id: 1, kind: 'bank'}, {id: 2, kind: 'card', company_id: 5}, {id: 3, kind: 'block'}, {id: 4, kind: 'transit'}, {id: 5, kind: 'card', company_id: 6}];
  assert.deepEqual(manualAccounts(accounts).map(a => a.id), [1, 2, 5]);
  assert.deepEqual(cardAccounts(accounts, 5).map(a => a.id), [2]);
  assert.equal(treasuryKind({channel: 'card', currency: 'UZS'}), 'card');
  assert.equal(treasuryKind({channel: 'bank', currency: 'USD'}), 'fx');
  assert.equal(treasuryKind({channel: 'bank', currency: 'UZS'}), null);
  assert.equal(treasuryKind({channel: 'cash', currency: 'USD'}), null);
});

test('card plan is sent only when the form has it and normalizes the percent', () => {
  assert.deepEqual(cardPlanBody({fee_percent: '1,5', fee_category_id: '7'}), {fee_percent: '1.5', fee_category_id: 7});
  assert.deepEqual(cardPlanBody({fee_percent: '', fee_category_id: null}), {fee_percent: '0', fee_category_id: null});
  assert.equal(cardPlanBody(null), null);
  const base = {account_id: 2, channel: 'card', currency: 'UZS', category_id: 3, amount: '10', counterparty: 'Shop', purpose: 'Покупка картой',
    priority: 'normal', date: '2026-11-30'};
  assert.equal(Object.hasOwn(requestBody(base, 1), 'card'), false);
  assert.deepEqual(requestBody({...base, card: {fee_percent: '2', fee_category_id: 4}}, 1).card, {fee_percent: '2', fee_category_id: 4});
});

test('treasury labels are translated in Uzbek', () => {
  try {
    setLang('uz');
    assert.equal(tx('Корпоративная карта'), 'Korporativ karta');
    assert.equal(accountKindLabel('block'), 'Konvertatsiya uchun blok hisob');
    assert.equal(tx('Частично оплачена'), 'Qisman toʻlangan');
    assert.match(tx('Недостаточно средств на корпоративной карте «Humo 4417» на 2026-11-28: остаток 100000.00 UZS, требуется 100495.00 UZS (сумма оплаты 99500.00 + комиссия 995.00). Оплата не проведена: пополните карту и повторите.'),
      /^«Humo 4417» korporativ kartasida 2026-11-28 sanaga/);
  } finally { setLang('ru'); }
});
