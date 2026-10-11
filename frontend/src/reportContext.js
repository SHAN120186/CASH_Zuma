// Only presentation choices travel between reports; source amounts and cash
// classifications remain separate. App clears this context on company change.
export function normalizeReportSelection(value = {}, defaults = {}) {
  const integer = (v, lo, hi, fallback) => Number.isInteger(Number(v)) && Number(v) >= lo && Number(v) <= hi ? Number(v) : fallback;
  const year = integer(value.year, 2000, 2100, defaults.year ?? null);
  const period = ['year','q1','q2','q3','q4','custom','month'].includes(value.period) ? value.period : defaults.period ?? 'year';
  let start = integer(value.start, 1, 12, defaults.start ?? 1);
  let end = integer(value.end, 1, 12, defaults.end ?? 12);
  if (period === 'year') { start = 1; end = 12; }
  else if (/^q[1-4]$/.test(period)) { start = (Number(period[1]) - 1) * 3 + 1; end = start + 2; }
  else if (period === 'month') end = start;
  // Reversed custom ranges stay visible for correction, not silently swapped.
  const unit = ['x','k','m'].includes(value.unit) ? value.unit : defaults.unit ?? 'x';
  const mode = ['actual','compare','plan'].includes(value.mode) ? value.mode : defaults.mode ?? 'actual';
  const sourceId = integer(value.sourceId, 1, Number.MAX_SAFE_INTEGER, defaults.sourceId ?? null);
  return {year, period, start, end, unit, mode, sourceId};
}
