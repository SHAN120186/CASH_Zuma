import {test} from 'node:test';
import assert from 'node:assert/strict';
import {toCents, formatCents, formatAmount, amountTitle, formatCompact, formatAxisLabels, formatPercent, addDays, sameDayPreviousMonth} from '../src/overview/format.js';
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

test('balance chart levels share one unit and never read as the same number', () => {
  const levels = (...v) => formatAxisLabels(v.map(toCents)).map(plain);
  // The reviewed case: 52 → 53,3 млн is a small rise, the scale says so in money.
  assert.deepEqual(levels('52000000', '52650000', '53300000'), ['52 млн', '52,7 млн', '53,3 млн']);
  assert.deepEqual(levels('50000000', '75000000', '100000000'), ['50 млн', '75 млн', '100 млн']);
  // A narrow range gets more decimals instead of three equal labels.
  assert.deepEqual(levels('52950000', '52970000', '52990000'), ['52,95 млн', '52,97 млн', '52,99 млн']);
  // When even two decimals of the unit are equal, whole amounts are shown.
  assert.deepEqual(levels('52950000', '52950500', '52951000'), ['52 950 000', '52 950 500', '52 951 000']);
  // The unit follows the largest level; small sums stay exact, minus is a real minus.
  assert.deepEqual(levels('900000', '1200000'), ['0,9 млн', '1,2 млн']);
  assert.deepEqual(levels('-2000000', '0', '2000000'), ['−2 млн', '0', '2 млн']);
  assert.deepEqual(levels('1500', '9500'), ['1 500', '9 500']);
  assert.deepEqual(levels('25400', '30000'), ['25,4 тыс.', '30 тыс.']);
  assert.deepEqual(formatAxisLabels([]), []);
  assert.match(formatAxisLabels([toCents('53300000')])[0], /^53,3 млн$/);
});

test('headline amounts pick тыс., млн, млрд or трлн automatically', () => {
  const short = (v, cur = 'UZS', opts) => plain(formatAmount(toCents(v), cur, opts));
  assert.equal(short('5318400000'), '5,32 млрд');
  assert.equal(short('182000000'), '182 млн');
  assert.equal(short('75000000'), '75 млн');
  assert.equal(short('25400'), '25,4 тыс.');
  assert.equal(short('10000'), '10 тыс.');
  assert.equal(short('1000000'), '1 млн');
  assert.equal(short('1000000000'), '1 млрд');
  assert.equal(short('2500000000000'), '2,5 трлн');
  assert.equal(short('90000000000000'), '90 трлн');
  // Below 10 000 the exact amount is shown, with tiyin when there are any.
  assert.equal(short('9999'), '9 999');
  assert.equal(short('1500.50'), '1 500,50');
  assert.equal(short('1500', 'USD'), '1 500,00');
  // The unit stays on the same line as the number.
  assert.match(formatAmount(toCents('182000000'), 'UZS'), /^182\u00a0млн$/);
});

test('compact amounts: two decimals under 10, one under 100, none above, no trailing zeros', () => {
  const short = v => plain(formatAmount(toCents(v), 'UZS'));
  assert.equal(short('1234567'), '1,23 млн');
  assert.equal(short('1500000'), '1,5 млн');
  assert.equal(short('12345678'), '12,3 млн');
  assert.equal(short('123456789'), '123 млн');
  assert.equal(short('40000000000'), '40 млрд');
  // Rounding that reaches 1 000 moves to the next unit.
  assert.equal(short('999600'), '1 млн');
  assert.equal(short('999999999'), '1 млрд');
  assert.equal(short('9996000'), '10 млн');
});

test('compact amounts keep the sign rules of exact amounts', () => {
  assert.equal(plain(formatAmount(toCents('-42000000'), 'UZS')), '−42 млн');
  assert.equal(plain(formatAmount(toCents('-25.10'), 'EUR')), '−25,10');
  assert.equal(plain(formatAmount(toCents('3200000'), 'UZS', {sign: true})), '+3,2 млн');
  assert.equal(plain(formatAmount(toCents('-3200000'), 'UZS', {sign: true})), '−3,2 млн');
  assert.equal(plain(formatAmount(0, 'UZS', {sign: true})), '0');
  assert.equal(formatAmount(null, 'UZS'), '—');
  assert.equal(formatAmount(undefined, 'UZS'), '—');
  assert.equal(formatAmount(toCents('100000000000000.00'), 'UZS'), '—');
});

test('compact amounts have Uzbek unit names', () => {
  const uz = v => plain(formatAmount(toCents(v), 'UZS', {lang: 'uz'}));
  assert.equal(uz('25400'), '25,4 ming');
  assert.equal(uz('75000000'), '75 mln');
  assert.equal(uz('5318400000'), '5,32 mlrd');
  assert.equal(uz('2500000000000'), '2,5 trln');
});

test('the tooltip of a compact amount keeps every digit and the currency', () => {
  assert.equal(plain(amountTitle(toCents('5318400000'), 'UZS')), '5 318 400 000 UZS');
  assert.equal(plain(amountTitle(toCents('1234567.89'), 'USD')), '1 234 567,89 USD');
  assert.equal(plain(amountTitle(toCents('-42000000'), 'UZS')), '−42 000 000 UZS');
  assert.equal(plain(amountTitle(toCents('3200000'), 'UZS', {sign: true})), '+3 200 000 UZS');
  assert.equal(amountTitle(null, 'UZS'), '');
});
