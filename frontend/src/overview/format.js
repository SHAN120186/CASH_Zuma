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
