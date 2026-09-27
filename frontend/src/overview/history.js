import { addDays, toCents } from './format.js';

// End-of-day balance of one currency for the last `days` days ending `today`,
// rebuilt backwards from today's balances and the ledger of the window.
//
// The server computes an account balance as opening + every ledger entry up to
// the date, and 0 before the opening date (app/services.py account_balance).
// Walking back from today therefore removes every later entry and every opening
// balance that did not exist yet. Transfers move money between two accounts of
// the same currency, so they do not change the total. Income and expense for a
// day exclude reversed entries and their reversals, as the Cash Flow report does.
export function balanceHistory({ today, days = 30, accounts, entries }) {
  const start = addDays(today, -(days - 1));
  const current = accounts.reduce((sum, a) => sum + (toCents(a.balance) ?? 0), 0);
  const dates = Array.from({ length: days }, (_, i) => addDays(start, i));

  const signed = e => {
    const cents = toCents(e.amount) ?? 0;
    return e.kind === 'in' ? cents : e.kind === 'out' ? -cents : 0;
  };
  const effective = e => e.reversal_of == null && !e.reversed;

  return dates.map(date => {
    const later = entries.reduce((sum, e) => (e.date > date ? sum + signed(e) : sum), 0);
    const notYetOpened = accounts.reduce((sum, a) => (
      a.opening_date > date && a.opening_date <= today ? sum + (toCents(a.opening) ?? 0) : sum
    ), 0);
    let income = 0, expense = 0;
    for (const e of entries) {
      if (e.date !== date || !effective(e)) continue;
      if (e.kind === 'in') income += toCents(e.amount) ?? 0;
      if (e.kind === 'out') expense += toCents(e.amount) ?? 0;
    }
    return { date, balance: current - later - notYetOpened, income, expense };
  });
}

// Plan and fact of one month from the Cash Flow report. A plan with any empty
// article is undefined, exactly as the report shows «Не задан».
export function monthPlanFact(report, monthIndex) {
  if (!report || !Array.isArray(report.rows)) return null;
  const pick = kind => {
    const rows = report.rows.filter(r => r.kind === kind);
    const fact = rows.reduce((s, r) => s + Math.abs(toCents(r.values[monthIndex]) ?? 0), 0);
    const plans = rows.map(r => r.plan[monthIndex]);
    const plan = rows.length && plans.every(p => p !== null && p !== undefined)
      ? plans.reduce((s, p) => s + Math.abs(toCents(p) ?? 0), 0)
      : null;
    return { fact, plan };
  };
  return { income: pick('in'), expense: pick('out') };
}
