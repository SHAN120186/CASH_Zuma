import {test} from 'node:test';
import assert from 'node:assert/strict';
import {toCents, formatCents, formatCompact, formatPercent, addDays, sameDayPreviousMonth} from '../src/overview/format.js';
import {balanceHistory, monthPlanFact} from '../src/overview/history.js';
import {makeScale, scalePoints, smoothPath, tickIndexes} from '../src/overview/chart.js';

const plain = s => s.replace(/[  ]/g, ' ');

test('money keeps every digit and uses non-breaking group separators', () => {
  const text = formatCents(toCents('12480000000.00'), 'UZS');
  assert.equal(plain(text), '12 480 000 000');
  assert.match(text, /^[0-9  ]+$/, 'groups must not break across lines');
  // Largest amount that fits exactly: every digit and tiyin is kept.
  assert.equal(plain(formatCents(toCents('90071992547409.91'), 'USD')), '90 071 992 547 409,91');
  assert.equal(plain(formatCents(toCents('12345678901234.07'), 'UZS')), '12 345 678 901 234,07');
  // Beyond it a dash is shown instead of silently wrong digits.
  assert.equal(formatCents(toCents('100000000000000.00'), 'UZS'), '—');
});

test('money strings are parsed without floating point', () => {
  assert.equal(toCents('0.29'), 29);
  assert.equal(toCents('-1234.5'), -123450);
  assert.equal(toCents('7'), 700);
  assert.equal(toCents('-0.00'), 0);
  assert.equal(toCents('12,34'), 1234);
  assert.equal(toCents('abc'), null);
});

test('UZS drops whole tiyin, USD and EUR keep two decimals, minus is a real minus', () => {
  assert.equal(plain(formatCents(toCents('1500.50'), 'UZS')), '1 500,50');
  assert.equal(plain(formatCents(toCents('1500'), 'USD')), '1 500,00');
  assert.equal(plain(formatCents(toCents('-25.10'), 'EUR')), '−25,10');
  assert.equal(formatCents(null, 'UZS'), '—');
});

test('cents avoid floating point drift over many rows', () => {
  const sum = Array.from({length: 1000}, () => toCents('0.10')).reduce((a, b) => a + b, 0);
  assert.equal(sum, 10000);
});

test('percent change is signed and rounded to one decimal', () => {
  assert.equal(formatPercent(0.0842), '+8,4%');
  assert.equal(formatPercent(-0.031), '−3,1%');
  assert.equal(formatPercent(0), '0%');
  assert.equal(formatPercent(null), '—');
});

test('calendar helpers do not depend on the local time zone', () => {
  assert.equal(addDays('2026-03-01', -1), '2026-02-28');
  assert.equal(addDays('2026-12-31', 1), '2027-01-01');
  assert.equal(sameDayPreviousMonth('2026-03-31'), '2026-02-28');
  assert.equal(sameDayPreviousMonth('2026-01-15'), '2025-12-15');
});

test('history ends at today’s balance and walks back through the ledger', () => {
  const accounts = [{balance: '1000.00', opening: '500.00', opening_date: '2026-01-01'}];
  const entries = [
    {date: '2026-09-27', kind: 'in', amount: '300.00'},
    {date: '2026-09-26', kind: 'out', amount: '100.00'},
    {date: '2026-09-26', kind: 'transfer', amount: '999.00'},
  ];
  const h = balanceHistory({today: '2026-09-27', days: 3, accounts, entries});
  assert.deepEqual(h.map(d => d.date), ['2026-09-25', '2026-09-26', '2026-09-27']);
  assert.deepEqual(h.map(d => d.balance), [80000, 70000, 100000]);
  assert.equal(h[1].expense, 10000);
  assert.equal(h[2].income, 30000);
});

test('an account opened inside the window contributes nothing before its opening date', () => {
  const accounts = [
    {balance: '100.00', opening: '0', opening_date: '2026-01-01'},
    {balance: '250.00', opening: '250.00', opening_date: '2026-09-26'},
  ];
  const h = balanceHistory({today: '2026-09-27', days: 3, accounts, entries: []});
  assert.deepEqual(h.map(d => d.balance), [10000, 35000, 35000]);
});

test('reversals change the balance but not daily income and expense', () => {
  const accounts = [{balance: '0.00', opening: '0', opening_date: '2026-01-01'}];
  const entries = [
    {date: '2026-09-27', kind: 'out', amount: '50.00', reversed: true},
    {date: '2026-09-27', kind: 'in', amount: '50.00', reversal_of: 7},
  ];
  const [day] = balanceHistory({today: '2026-09-27', days: 1, accounts, entries});
  assert.equal(day.balance, 0);
  assert.equal(day.income, 0);
  assert.equal(day.expense, 0);
});

test('plan of a month is undefined while any article has no plan', () => {
  const report = {rows: [
    {kind: 'in', values: ['100.00'], plan: ['120.00']},
    {kind: 'out', values: ['-40.00'], plan: [null]},
  ]};
  const pf = monthPlanFact(report, 0);
  assert.deepEqual(pf.income, {fact: 10000, plan: 12000});
  assert.deepEqual(pf.expense, {fact: 4000, plan: null});
});

test('chart curve stays inside the box and hits every data point', () => {
  const pts = scalePoints([5, 9, 9, 2, 7], {width: 400, height: 100});
  assert.ok(pts.every(([x, y]) => x >= 0 && x <= 400 && y >= 0 && y <= 100));
  const d = smoothPath(pts);
  for (const [x, y] of pts) assert.ok(d.includes(`${Math.round(x * 10) / 10} ${Math.round(y * 10) / 10}`));
  assert.equal(smoothPath([]), '');
  assert.deepEqual(tickIndexes(30), [0, 7, 15, 22, 29]);
});

test('forecast scale keeps a reserve far below the balances inside the box', () => {
  const {y} = makeScale([500, 600, 700], {width: 300, height: 100, top: 10, bottom: 10}, [100]);
  for (const v of [100, 500, 700]) assert.ok(y(v) >= 10 && y(v) <= 90, `value ${v} at ${y(v)}`);
  assert.ok(y(100) > y(500), 'the reserve is drawn below the balances');
});

test('axis labels are compact and signed', () => {
  assert.equal(plain(formatCompact(toCents('22945000000'))), '22,9 млрд');
  assert.equal(plain(formatCompact(toCents('2500000000'))), '2,5 млрд');
  assert.equal(plain(formatCompact(toCents('180000000'))), '180 млн');
  assert.equal(plain(formatCompact(toCents('-42000000'))), '−42 млн');
  assert.equal(formatCompact(toCents('950')), '950');
});
