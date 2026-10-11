import {N_,tx} from './i18n/index.js';

export const MAX_STATEMENT_BYTES = 20 * 1024 * 1024;
const readableStatuses = new Set(['value', 'cached', 'aggregated', 'calculated']);

export function statementUrl(companyId, sourceId = null) {
  const query = new URLSearchParams({company_id:String(companyId)});
  if (sourceId != null && sourceId !== '') query.set('source_id',String(sourceId));
  return '/api/financial-statements?' + query;
}

export function statementFileProblem(file) {
  if (!file || !/\.xlsx$/i.test(file.name || '')) return N_('Выберите исходную книгу Excel (.xlsx).');
  if (/[\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/.test(file.name)) return N_('Имя файла содержит недопустимые символы.');
  if (!Number.isFinite(file.size) || file.size <= 0) return N_('Выбранный файл пустой.');
  if (file.size > MAX_STATEMENT_BYTES) return N_('Размер исходной книги не должен превышать 20 МБ.');
  return '';
}

export function cellNumber(cell) {
  if (!cell || !readableStatuses.has(cell.status) || cell.value == null || cell.value === '') return null;
  const raw=String(cell.value).trim();
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(raw)) return null;
  const value=Number(raw);
  return Number.isFinite(value) ? value : null;
}

export function statementCell(row, month) {
  return (row.cells || []).find(cell => Number(cell.month) === Number(month)) || {plan:null,fact:null};
}

export function statementDeviation(plan, fact, kind = 'amount') {
  const planned=cellNumber(plan), actual=cellNumber(fact);
  if (planned == null || actual == null || kind === 'section') return {value:null,percent:null};
  const plannedDecimal=decimal(plan.value),actualDecimal=decimal(fact.value);
  if (!plannedDecimal || !actualDecimal) return {value:null,percent:null};
  const difference=sumDecimals([actualDecimal,{...plannedDecimal,coefficient:-plannedDecimal.coefficient}]);
  const value=Number(decimalText(difference));
  if (!Number.isFinite(value)) return {value:null,percent:null};
  // Ratio changes are percentage points. Negative and zero bases have no
  // unambiguous relative percentage; do not show an invented percentage.
  const percent=kind === 'ratio' || plannedDecimal.coefficient <= 0n ? null :
    Number(decimalText(divideDecimals({...difference,coefficient:difference.coefficient*100n},plannedDecimal)));
  return {value,percent:percent != null && Number.isFinite(percent) ? percent : null};
}

export function statementNumber(value, kind = 'amount', locale = 'ru-RU') {
  if (value == null || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat(locale, {minimumFractionDigits:2,maximumFractionDigits:2,
    ...(kind === 'ratio' ? {style:'percent'} : {})}).format(value);
}

export const statementReasonLabels = {
  invalid_period:N_('Даты выбранного периода не подтверждены.'),
  incomplete_period:N_('Не все месяцы выбранного периода подтверждены; полный итог не рассчитан.'),
  ratio_method_required:N_('Для этого коэффициента не задана методика расчёта за период.'),
  ratio_base_missing:N_('Не найдены подтверждённые исходные строки для расчёта маржи.'),
  zero_base:N_('Маржа за период не определена: сумма выручки равна нулю.'),
  nonfinite_result:N_('Результат расчёта выходит за допустимый числовой диапазон.'),
};

function rowMeasure(row) {
  return row?.measure || (row?.kind === 'ratio' ? 'percent' : row?.kind || 'amount');
}

// Decimal source caches can have more than two fractional digits. Keep their
// coefficient and exponent intact until presentation; these are not cash cents.
function decimal(value) {
  const match=/^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:e([+-]?\d+))?$/i.exec(String(value).trim());
  if (!match) return null;
  const exponent=Number(match[5] || 0);
  if (!Number.isInteger(exponent) || Math.abs(exponent) > 1000) return null;
  const fraction=match[3] ?? match[4] ?? '';
  const digits=(match[2] || '0')+fraction;
  if (digits.length > 2000) return null;
  return {coefficient:BigInt(digits)*(match[1] === '-' ? -1n : 1n),scale:fraction.length-exponent};
}

function decimalText(value) {
  let {coefficient,scale}=value;
  if (coefficient === 0n) return '0';
  while (scale > 0 && coefficient % 10n === 0n) {coefficient/=10n;scale--;}
  const negative=coefficient < 0n;
  let digits=(negative ? -coefficient : coefficient).toString();
  if (scale <= 0) digits+='0'.repeat(-scale);
  else {digits=digits.padStart(scale+1,'0');digits=digits.slice(0,-scale)+'.'+digits.slice(-scale);}
  return (negative ? '-' : '')+digits;
}

function sumDecimals(values) {
  const scale=Math.max(...values.map(value=>value.scale));
  return {coefficient:values.reduce((total,value)=>total+value.coefficient*10n**BigInt(scale-value.scale),0n),scale};
}

function divideDecimals(top,bottom) {
  const precision=24, exponent=bottom.scale-top.scale+precision;
  const numerator=top.coefficient*10n**BigInt(Math.max(exponent,0));
  const denominator=bottom.coefficient*10n**BigInt(Math.max(-exponent,0));
  let coefficient=numerator/denominator;
  const remainder=numerator%denominator;
  const absolute=value=>value < 0n ? -value : value;
  if (absolute(remainder)*2n >= absolute(denominator)) coefficient+=(numerator < 0n) !== (denominator < 0n) ? -1n : 1n;
  return {coefficient,scale:precision};
}

function selectedMonths(options) {
  const first=Number(options.startMonth ?? options.month ?? 1);
  const last=Number(options.endMonth ?? first);
  if (!Number.isInteger(first) || !Number.isInteger(last) || first < 1 || last > 12 || first > last) return [];
  return Array.from({length:last-first+1}, (_,index)=>first+index);
}

function periodContext(report, months, requestedYear) {
  const periods=new Map(), invalidMonths=[];
  let year=requestedYear == null ? null : Number(requestedYear);
  if (year != null && (!Number.isInteger(year) || year < 1000 || year > 9999)) {
    return {year:null,periods,invalidMonths:[...months]};
  }
  for (const month of months) {
    const matches=(report?.periods || []).filter(item=>Number(item.month) === month);
    const text=matches.length === 1 ? matches[0].period : null;
    const parsed=typeof text === 'string' ? /^(\d{4})-(\d{2})$/.exec(text) : null;
    if (!parsed || Number(parsed[2]) !== month || Number(parsed[1]) < 1000) {
      invalidMonths.push(month);
      continue;
    }
    if (year == null) year=Number(parsed[1]);
    if (Number(parsed[1]) !== year) invalidMonths.push(month);
    periods.set(month,text);
  }
  return {year,periods,invalidMonths};
}

function provenanceFor(row, months, side, context, role=null) {
  return months.map(month=>({
    ...(statementCell(row,month)[side] || {value:null,status:'missing'}),
    month,period:context.periods.get(month) ?? null,row:row?.row ?? null,
    ...(role ? {role} : {}),
  }));
}

function unconfirmedMonths(provenance, side, options, context) {
  const invalid=new Set(context.invalidMonths);
  return provenance.filter(item=>invalid.has(item.month) || cellNumber(item) == null ||
    side === 'fact' && Array.isArray(options.coverage) &&
      !options.coverage.some(period=>Number(period.month) === item.month && period.status === 'available'))
    .map(item=>item.month);
}

function unavailable(reason, provenance=[], missingMonths=[], status='incomplete') {
  return {value:null,status,reason,provenance,missingMonths:[...new Set(missingMonths)]};
}

function sourceSide(row, month, side, options, context) {
  const provenance=provenanceFor(row,[month],side,context);
  const original=statementCell(row,month)[side] || {value:null,status:'missing'};
  if (context.invalidMonths.length) return unavailable('invalid_period',provenance,[month],'invalid');
  const missingMonths=unconfirmedMonths(provenance,side,options,context);
  if (missingMonths.length) {
    const status=cellNumber(original) == null ? original.status || 'missing' :
      options.coverage?.find(item=>Number(item.month) === month)?.status === 'future' ? 'future' : 'missing';
    return {...original,value:null,status,provenance,reason:'incomplete_period',missingMonths};
  }
  return {...original,provenance};
}

function sumSide(row, months, side, options, context, role=null) {
  const provenance=provenanceFor(row,months,side,context,role);
  if (context.invalidMonths.length) return unavailable('invalid_period',provenance,context.invalidMonths,'invalid');
  const missingMonths=unconfirmedMonths(provenance,side,options,context);
  if (missingMonths.length) return unavailable('incomplete_period',provenance,missingMonths);
  const values=provenance.map(item=>decimal(item.value));
  if (values.some(value=>value == null)) return unavailable('nonfinite_result',provenance,[],'invalid');
  const value=decimalText(sumDecimals(values));
  return Number.isFinite(Number(value)) ? {value,status:'aggregated',provenance} :
    unavailable('nonfinite_result',provenance,[],'invalid');
}

/** Source-period presentation only: cash-ledger data and immutable source cells are untouched. */
export function aggregateStatementRow(report, row, options={}) {
  const months=selectedMonths(options);
  const balance=options.type === 'balance' || report?.sheet === '09b_Balance';
  const checkedMonths=balance && months.length ? [months.at(-1)] : months;
  const context=periodContext(report,checkedMonths,options.year);
  const mode=balance ? 'end_month' : months.length === 1 ? 'source' : row?.kind === 'ratio' ? 'ratio' : 'sum';
  const result={months,mode,year:context.year,reason:null};
  if (!months.length) {
    result.plan=unavailable('invalid_period',[],[],'invalid');
    result.fact=unavailable('invalid_period',[],[],'invalid');
  } else if (row?.kind === 'section') {
    result.plan={value:null,status:'missing',provenance:[]};
    result.fact={value:null,status:'missing',provenance:[]};
  } else if (balance || months.length === 1) {
    for (const side of ['plan','fact']) result[side]=sourceSide(row,months.at(-1),side,options,context);
  } else if (rowMeasure(row) === 'amount') {
    for (const side of ['plan','fact']) result[side]=sumSide(row,months,side,options,context);
  } else {
    const numeratorRow={13:12,24:23}[Number(row?.row)];
    const numerator=(report?.rows || []).find(item=>Number(item.row) === numeratorRow);
    const revenue=(report?.rows || []).find(item=>Number(item.row) === 7);
    for (const side of ['plan','fact']) {
      if (!numeratorRow) {
        result[side]=unavailable('ratio_method_required',provenanceFor(row,months,side,context),[],'unsupported');
        continue;
      }
      if (!numerator || !revenue) {
        result[side]=unavailable('ratio_base_missing',provenanceFor(row,months,side,context),[],'unsupported');
        continue;
      }
      const top=sumSide(numerator,months,side,options,context,'numerator');
      const bottom=sumSide(revenue,months,side,options,context,'denominator');
      const provenance=[...top.provenance,...bottom.provenance];
      const missingMonths=[...(top.missingMonths || []),...(bottom.missingMonths || [])];
      if (cellNumber(top) == null || cellNumber(bottom) == null) {
        result[side]=unavailable(top.reason || bottom.reason || 'incomplete_period',provenance,missingMonths,
          top.status === 'invalid' || bottom.status === 'invalid' ? 'invalid' : 'incomplete');
        continue;
      }
      const topDecimal=decimal(top.value),bottomDecimal=decimal(bottom.value);
      if (!topDecimal || !bottomDecimal) {
        result[side]=unavailable('nonfinite_result',provenance,[],'invalid');
        continue;
      }
      if (bottomDecimal.coefficient === 0n) {
        result[side]=unavailable('zero_base',provenance,[],'invalid');
        continue;
      }
      const value=decimalText(divideDecimals(topDecimal,bottomDecimal));
      result[side]=Number.isFinite(Number(value)) ? {value,status:'calculated',provenance} :
        unavailable('nonfinite_result',provenance,[],'invalid');
    }
  }
  result.reason=result.plan.reason || result.fact.reason || null;
  return result;
}

/** Divisors change the display of original source amounts, not their accounting unit. */
export function statementScaledValue(value, row, scale=1) {
  if (value == null || !Number.isFinite(value)) return null;
  if (rowMeasure(row) !== 'amount') return value;
  const divisor=Number(scale);
  return [1,1000,1000000].includes(divisor) ? value/divisor : null;
}

export function statementScaledNumber(cell, row, scale=1, locale='ru-RU') {
  const measure=rowMeasure(row);
  const value=cellNumber(cell) == null ? null : decimal(cell.value);
  const divisor=measure === 'amount' ? Number(scale) : 1;
  if (!value || ![1,1000,1000000].includes(divisor)) return '—';
  const displayed=decimalText({...value,scale:value.scale+Math.log10(divisor)});
  return new Intl.NumberFormat(locale,{minimumFractionDigits:2,maximumFractionDigits:2,
    ...(measure === 'percent' ? {style:'percent'} : {})}).format(displayed)+
    (measure === 'days' ? ' '+tx('дн.') : '');
}

/** Format the exact difference directly; the legacy numeric API is for calculations. */
export function statementScaledDeviation(plan, fact, row, scale=1, locale='ru-RU') {
  if (row?.kind === 'section' || cellNumber(plan) == null || cellNumber(fact) == null) return '—';
  const planned=decimal(plan.value),actual=decimal(fact.value);
  if (!planned || !actual) return '—';
  const difference=sumDecimals([actual,{...planned,coefficient:-planned.coefficient}]);
  const text=decimalText(difference);
  if (!Number.isFinite(Number(text))) return '—';
  const measure=rowMeasure(row);
  if (measure === 'percent') {
    const points=decimalText({...difference,coefficient:difference.coefficient*100n});
    if (!Number.isFinite(Number(points))) return '—';
    return tx('{value} п. п.',{value:statementScaledNumber({value:points,status:'calculated'},
      {kind:'ratio',measure:'coefficient'},1,locale)});
  }
  return statementScaledNumber({value:text,status:'calculated'},
    {...row,measure:measure === 'days' ? 'coefficient' : measure},scale,locale);
}

export const statementStatuses = {
  value:N_('Значение источника'),cached:N_('Сохранённый результат формулы'),
  missing:N_('Нет данных'),missing_cache:N_('Нет результата формулы'),
  error:N_('Ошибка формулы'),invalid:N_('Некорректное значение'),future:N_('Будущий факт не подтверждён'),
  aggregated:N_('Сумма подтверждённых месяцев'),calculated:N_('Маржа из сумм за период'),
  incomplete:N_('Неполный период'),unsupported:N_('Методика периода не задана'),
};

export function sourceScopeMatches(payload, companyId) {
  // Company ID is also enforced by the server and the app request epoch.
  // Reject explicit mismatches rather than displaying another company's source.
  return [payload?.company_id,payload?.source?.company_id].every(id => id == null || Number(id) === Number(companyId));
}
