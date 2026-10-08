// Formatting for the overview: Russian digit grouping with non-breaking
// separators, tabular figures are applied in CSS.

const cache = new Map();
function numberFormat(digits, sign) {
  const key = digits + ':' + sign;
  if (!cache.has(key)) {
    cache.set(key, new Intl.NumberFormat('ru-RU', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
      signDisplay: sign ? 'exceptZero' : 'auto',
    }));
  }
  return cache.get(key);
}

// API money values are decimal strings ("12480000000.00"). They are parsed
// digit by digit into integer cents: no floating point step, so every tiyin
// survives. Amounts beyond the safe integer range (about 90 trillion) come
// back as null and are shown as a dash rather than with wrong digits.
const MONEY = /^\s*([+-]?)(\d+)(?:[.,](\d*))?\s*$/;
export function toCents(value) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number') return Number.isFinite(value) ? Math.round(value * 100) : null;
  const m = MONEY.exec(String(value));
  if (!m) return null;
  const frac = (m[3] || '').padEnd(3, '0');
  const cents = Number(m[2]) * 100 + Number(frac.slice(0, 2)) + (Number(frac[2]) >= 5 ? 1 : 0);
  if (!Number.isSafeInteger(cents)) return null;
  return m[1] === '-' && cents ? -cents : cents;
}

const grouping = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

// Whole part and tiyin are formatted separately, so large sums keep exact digits.
export function formatCents(cents, currency, { sign = false } = {}) {
  if (cents === null || cents === undefined || !Number.isSafeInteger(cents)) return '—';
  const abs = Math.abs(cents), frac = abs % 100, whole = (abs - frac) / 100;
  // Whole UZS amounts are shown without tiyin, like the rest of the product.
  const tail = currency === 'UZS' && frac === 0 ? '' : ',' + String(frac).padStart(2, '0');
  const prefix = cents < 0 ? '−' : sign && cents > 0 ? '+' : '';
  return prefix + grouping.format(whole) + tail;
}

// Unit names for compact amounts; uz is ready for the Uzbek interface.
export const AMOUNT_UNITS = {
  ru: {k: 'тыс.', m: 'млн', b: 'млрд', t: 'трлн'},
  uz: {k: 'ming', m: 'mln', b: 'mlrd', t: 'trln'},
};
// [size of one unit in cents, unit key], smallest first. Thresholds are on the
// whole-currency value: 1e4 → тыс., 1e6 → млн, 1e9 → млрд, 1e12 → трлн.
const STEPS = [[1e5, 'k'], [1e8, 'm'], [1e11, 'b'], [1e14, 't']];
const COMPACT_FROM = 1e6; // 10 000 in cents

// Headline amounts: "5,32 млрд", "182 млн", "25,4 тыс.". Below 10 000 the
// exact amount is shown. Two decimals under 10 units, one under 100, none
// above; trailing zeros are dropped. The unit is kept on the same line.
// Ledgers, tables and forms keep formatCents with every digit.
export function formatAmount(cents, currency, { sign = false, lang = 'ru' } = {}) {
  if (cents === null || cents === undefined || !Number.isSafeInteger(cents)) return '—';
  const abs = Math.abs(cents);
  if (abs < COMPACT_FROM) return formatCents(cents, currency, { sign });
  const units = AMOUNT_UNITS[lang] || AMOUNT_UNITS.ru;
  let i = STEPS.length - 1;
  while (i > 0 && abs < STEPS[i][0]) i -= 1;
  let digits, value;
  for (;;) {
    const size = STEPS[i][0], n = abs / size;
    digits = n < 10 ? 2 : n < 100 ? 1 : 0;
    // Rounded in whole steps of the last shown digit, so no float drift.
    const step = size / 10 ** digits;
    value = Math.round(abs / step) / 10 ** digits;
    // 999 960 rounds to "1 000 тыс."; show it as "1 млн" instead.
    if (value < 1000 || i === STEPS.length - 1) break;
    i += 1;
  }
  const text = numberFormat(digits, false).format(value).replace(/(,\d*?)0+$/, '$1').replace(/,$/, '');
  const prefix = cents < 0 ? '−' : sign && cents > 0 ? '+' : '';
  return prefix + text + '\u00a0' + units[STEPS[i][1]];
}

// Tooltip for a compact amount: every digit and the currency.
export function amountTitle(cents, currency, { sign = false } = {}) {
  if (cents === null || cents === undefined || !Number.isSafeInteger(cents)) return '';
  return formatCents(cents, currency, { sign }) + ' ' + currency;
}

export function formatPercent(ratio) {
  if (ratio === null || !Number.isFinite(ratio)) return '—';
  const value = Math.round(ratio * 1000) / 10;
  const body = numberFormat(1, false).format(Math.abs(value)).replace(/,0$/, '');
  return (value > 0 ? '+' : value < 0 ? '−' : '') + body + '%';
}

// Axis labels only: a chart scale, never a stated amount (those keep every digit).
export function formatCompact(cents) {
  if (cents === null || cents === undefined || !Number.isFinite(cents)) return '—';
  const value = cents / 100, abs = Math.abs(value);
  const [div, unit] = abs >= 1e12 ? [1e12, 'трлн'] : abs >= 1e9 ? [1e9, 'млрд'] : abs >= 1e6 ? [1e6, 'млн'] : abs >= 1e3 ? [1e3, 'тыс.'] : [1, ''];
  const n = abs / div;
  const text = numberFormat(n >= 100 || div === 1 ? 0 : 1, false).format(n).replace(/,0$/, '');
  return (value < 0 ? '−' : '') + text + (unit ? ' ' + unit : '');
}

// Labels for the levels of one chart scale: "52 млн", "52,7 млн", "53,3 млн".
// One unit for every level, chosen by the largest as formatAmount would, with its
// precision (two decimals under 10, one under 100); more decimals are added while
// neighbouring levels would read alike, then whole amounts. Zero is a plain "0".
export function formatAxisLabels(values) {
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return values.map(() => '—');
  const top = Math.max(...finite.map(Math.abs));
  const step = top >= COMPACT_FROM ? STEPS.slice().reverse().find(([size]) => top >= size) : null;
  const scales = [[100, '']];
  if (step) scales.unshift([step[0], ' ' + AMOUNT_UNITS.ru[step[1]]]);
  let labels = [];
  for (const [size, unit] of scales) {
    const n = top / size;
    for (let digits = unit ? (n < 10 ? 2 : n < 100 ? 1 : 0) : 0; digits <= 2; digits += 1) {
      labels = values.map(v => {
        if (!Number.isFinite(v)) return '—';
        const text = numberFormat(digits, false).format(Math.abs(v) / size).replace(/(,\d*?)0+$/, '$1').replace(/,$/, '');
        return text === '0' ? '0' : (v < 0 ? '−' : '') + text + unit;
      });
      if (new Set(labels).size === labels.length) return labels;
    }
  }
  return labels;
}

const shortDate = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', timeZone: 'UTC' });
const longDate = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
const parse = iso => new Date(iso + 'T00:00:00Z');

export const formatShortDate = iso => shortDate.format(parse(iso));
export const formatLongDate = iso => longDate.format(parse(iso));

// Calendar arithmetic on ISO dates without local time zones.
export function addDays(iso, n) {
  const d = parse(iso);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

// Same day one month earlier, clamped to that month's last day.
export function sameDayPreviousMonth(iso) {
  const [y, m, day] = iso.split('-').map(Number);
  const py = m === 1 ? y - 1 : y, pm = m === 1 ? 12 : m - 1;
  const last = new Date(Date.UTC(py, pm, 0)).getUTCDate();
  return `${py}-${String(pm).padStart(2, '0')}-${String(Math.min(day, last)).padStart(2, '0')}`;
}
