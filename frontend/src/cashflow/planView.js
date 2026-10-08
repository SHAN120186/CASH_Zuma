// A date cut-off describes actual money, so it can never be in a future year.
export function reportDateForYear(year, today) {
  const y = Number(year), current = Number(String(today).slice(0, 4));
  if (!Number.isInteger(y) || y < 2000 || y > 2100 || y > current || !validReportDate(today, current, today)) return null;
  return y === current ? today : `${y}-12-31`;
}

export function validReportDate(value, year, today) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '') || !today) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value &&
    Number(value.slice(0, 4)) === Number(year) && value <= today;
}

export function editorMonth(asOf, year, start, end) {
  const month = Number(String(asOf).slice(5, 7));
  return Number(String(asOf).slice(0, 4)) === Number(year) && month >= Number(start) && month <= Number(end)
    ? month : Number(start);
}

const cents = value => {
  const [whole, fraction = ''] = String(value).split('.');
  return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0')) * (whole.startsWith('-') ? -1n : 1n);
};
const money = value => `${value / 100n}.${String(value % 100n).padStart(2, '0')}`;

// Known amounts are a subtotal, never the full plan while any article is empty.
// Explicit zero is known. Count affected rows separately from missing monthly cells.
export function planCompleteness(rows, indices) {
  let income = 0n, expense = 0n, incomeCells = 0, expenseCells = 0, missingRows = 0, missingCells = 0, knownCells = 0;
  for (const row of rows) {
    let missing = false;
    for (const month of indices) {
      const value = row.plan[month];
      if (value == null) { missing = true; missingCells++; continue; }
      const amount = cents(value), absolute = amount < 0n ? -amount : amount;
      if (row.kind === 'in') {income += absolute; incomeCells++;}
      else {expense += absolute; expenseCells++;}
      knownCells++;
    }
    if (missing) missingRows++;
  }
  return {income: incomeCells ? money(income) : null, expense: expenseCells ? money(expense) : null, missingRows, missingCells, knownCells};
}
