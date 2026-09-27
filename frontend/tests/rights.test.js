import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {PAY_RIGHTS, payRight, canPayAny, canPayRequest} from '../src/overview/rights.js';

const has = list => right => list.includes(right);
const request = extra => ({status: 'approved', account_kind: 'bank', creator_id: 1, last_editor_id: 2, finance_approved_by: 3, approved_by: 4, ...extra});

test('only pay_bank and pay_cash open a payment; generic pay and write never do', () => {
  assert.deepEqual(PAY_RIGHTS, ['pay_bank', 'pay_cash']);
  assert.equal(canPayAny(has(['pay', 'write', 'approve'])), false);
  assert.equal(canPayAny(has(['pay_bank'])), true);
  assert.equal(canPayAny(has(['pay_cash'])), true);
});

test('bank requests need pay_bank and cash requests need pay_cash', () => {
  assert.equal(payRight(request()), 'pay_bank');
  assert.equal(payRight(request({account_kind: 'cash'})), 'pay_cash');
  assert.equal(canPayRequest(request(), 9, has(['pay_bank'])), true);
  assert.equal(canPayRequest(request(), 9, has(['pay_cash'])), false);
  assert.equal(canPayRequest(request({account_kind: 'cash'}), 9, has(['pay_bank'])), false);
  assert.equal(canPayRequest(request({account_kind: 'cash'}), 9, has(['pay_cash'])), true);
  assert.equal(canPayRequest(request({status: 'pending'}), 9, has(['pay_bank'])), false);
});

test('the author, last editor, finance director and director of a request never pay it', () => {
  for (const participant of [1, 2, 3, 4]) {
    assert.equal(canPayRequest(request(), participant, has(['pay_bank', 'pay_cash'])), false, `user ${participant}`);
  }
});

test('the Overview and the request screens carry no reject, no threshold and no generic pay', () => {
  const files = ['../src/overview/OverviewPage.vue', '../src/overview/BalanceCard.vue', '../src/overview/ForecastCard.vue',
                 '../src/overview/rights.js', '../src/App.vue'];
  for (const file of files) {
    const source = readFileSync(new URL(file, import.meta.url), 'utf8');
    assert.doesNotMatch(source, /['"]reject['"]/, `${file}: reject action`);
    assert.doesNotMatch(source, /по порогу|Директор не требовался|Порог директора|Настроить порог/, `${file}: director threshold`);
    assert.doesNotMatch(source, /has\(\s*['"]pay['"]\s*\)/, `${file}: generic pay right`);
    assert.doesNotMatch(source, /['"]pay['"]\s*[,\]]/, `${file}: generic pay right in a list`);
  }
});
