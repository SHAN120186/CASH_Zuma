import {N_,tx} from './i18n/index.js';

export const MAX_STATEMENT_BYTES = 20 * 1024 * 1024;
const readableStatuses = new Set(['value', 'cached', 'aggregated', 'calculated']);

// Friendly labels are safe only after the backend has recognised the account
// profile and the position of every report row. Original labels stay in audit.
export const statementDisplayLabels = {
  pnl:{7:N_('Чистая выручка'),8:N_('Выручка'),9:N_('Возвраты'),10:N_('Скидки'),
    11:N_('Себестоимость продаж'),12:N_('Валовая прибыль'),13:N_('Валовая маржа'),
    14:N_('Прочие операционные доходы'),15:N_('Операционные расходы'),16:'EBITDA',
    17:N_('Амортизация'),18:'EBIT',19:N_('Финансовые доходы'),20:N_('Процентные расходы'),
    21:N_('Прибыль до налога'),22:N_('Налог на прибыль'),23:N_('Чистая прибыль'),24:N_('Чистая маржа')},
  balance:{7:N_('Активы'),8:N_('Денежные средства'),9:N_('Дебиторская задолженность'),
    10:N_('Прочая дебиторская задолженность'),11:N_('Авансы по налогам'),
    12:N_('Авансы по социальным платежам'),13:N_('Запасы'),14:N_('Расходы будущих периодов'),
    15:N_('Авансы поставщикам'),16:N_('Авансы сотрудникам'),17:N_('Депозиты и залоги'),
    18:N_('Основные средства, нетто'),19:N_('Всего активов'),21:N_('Обязательства и капитал'),
    22:N_('Кредиторская задолженность'),23:N_('Авансы покупателей'),24:N_('Отложенные доходы'),
    25:N_('Налоги к уплате'),26:N_('Заёмные средства'),27:N_('Задолженность перед учредителями'),
    28:N_('Зарплата к выплате'),29:N_('Прочие кредиторы'),30:N_('Краткосрочные кредиты'),
    31:N_('Долгосрочные кредиты'),32:N_('Уставный капитал'),33:N_('Резервный капитал'),
    34:N_('Нераспределённая прибыль'),35:N_('Всего обязательств'),36:N_('Капитал'),
    37:N_('Обязательства и капитал, итого'),38:N_('Расхождение баланса'),
    41:N_('Дополнительные обязательства'),42:N_('Социальные обязательства'),
    43:N_('Задолженность перед учредителями'),44:N_('Зарплата к выплате'),45:N_('Прочие кредиторы'),
    46:N_('Прочие обязательства, итого'),49:N_('Расшифровка капитала'),50:N_('Уставный капитал'),
    51:N_('Резервный капитал'),52:N_('Нераспределённая прибыль на начало'),
    53:N_('Финансовый результат года'),54:N_('Нераспределённая прибыль на конец'),
    55:N_('Капитал по счетам'),56:N_('Корректировка капитала'),60:'KPI',
    62:N_('Рентабельность продаж'),63:N_('Рентабельность активов (ROA)'),
    64:N_('Рентабельность капитала (ROE)'),65:N_('Срок погашения дебиторской задолженности (DSO)'),
    66:N_('Срок оборачиваемости запасов'),67:N_('Дебиторская задолженность / выручка'),
    68:N_('Деньги / выручка'),69:N_('Накопленная амортизация / основные средства'),
    70:N_('Оборачиваемость основных средств'),72:N_('Рабочий капитал'),73:N_('Текущая ликвидность'),
    74:N_('Быстрая ликвидность'),75:N_('Доля собственного капитала'),76:N_('Обязательства / капитал'),
    77:N_('Дебиторская задолженность'),78:N_('Денежные средства'),79:N_('Запасы'),
    80:N_('Основные средства, нетто'),81:N_('Кредиторская задолженность')},
};

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
  actual_revenue_classification_required:N_('Нужно подтвердить состав выручки, возвратов и скидок. До этого показатель не рассчитан.'),
  actual_tax_classification_required:N_('Нужно подтвердить налог на прибыль отдельно от прочих налогов.'),
  counterpart_classification_required:N_('Нужно классифицировать результат выбытия активов по счёту 9200.'),
  accounting_result_mismatch:N_('Результат по счетам не совпадает с движением финансового результата. Нужна сверка ОСВ.'),
  absent_profit_account:N_('В ОСВ не найден необходимый счёт.'),
  missing_account:N_('В ОСВ не найден необходимый счёт.'),
  profit_account_closing_required:N_('Обороты финансового результата не согласованы с сальдо. Нужна проверка закрытия счёта.'),
  invalid_period:N_('Даты выбранного периода не подтверждены.'),
  incomplete_period:N_('Не все месяцы выбранного периода подтверждены; полный итог не рассчитан.'),
  ratio_method_required:N_('Для этого коэффициента не задана методика расчёта за период.'),
  ratio_base_missing:N_('Не найдены подтверждённые исходные строки для расчёта маржи.'),
  zero_base:N_('Маржа за период не определена: сумма выручки равна нулю.'),
  nonfinite_result:N_('Результат расчёта выходит за допустимый числовой диапазон.'),
  depreciation_allocation_required:N_('Для EBITDA нужно распределение амортизации в себестоимость и расходы. Повторно вычитать амортизацию нельзя.'),
  ratio_period_method_required:N_('Этот KPI рассчитан за месяц. Для выбранного диапазона нужна методика с прибылью за период и средними остатками; месячное значение не подставляется.'),
  zero_denominator:N_('Коэффициент не определён: его расчётная база равна нулю.'),
  mapping_amount_invalid:N_('В Mapping есть некорректное числовое значение; расчёт не подтверждён.'),
  mapping_period_missing:N_('ОСВ за этот месяц ещё не загружена.'),
  plan_assumptions_required:N_('Нужно уточнить учёт НДС и процентных расходов в плане.'),
  opening_balance_missing:N_('Нет подтверждённого остатка на начало выбранного периода.'),
  plan_basis_unconfirmed:N_('Расчётная база планового показателя требует уточнения.'),
  nonpositive_flow:N_('Оборачиваемость не определена: выручка или себестоимость за период должна быть положительной.'),
  profile_unrecognized:N_('Структура счетов не соответствует поддерживаемой модели. Показаны значения источника.'),
  incomplete_account_register:N_('Для расчёта не подтверждён полный набор счетов ОСВ.'),
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
    return {...original,value:null,status,provenance,reason:original.reason || 'incomplete_period',missingMonths};
  }
  return {...original,provenance};
}

function sumSide(row, months, side, options, context, role=null) {
  const provenance=provenanceFor(row,months,side,context,role);
  if (context.invalidMonths.length) return unavailable('invalid_period',provenance,context.invalidMonths,'invalid');
  const missingMonths=unconfirmedMonths(provenance,side,options,context);
  if (missingMonths.length) {
    const blocked=provenance.find(item=>item.reason && ['requires_allocation','requires_assumptions','unavailable','invalid'].includes(item.status));
    return unavailable(blocked?.reason || 'incomplete_period',provenance,missingMonths,blocked?.status || 'incomplete');
  }
  const values=provenance.map(item=>decimal(item.value));
  if (values.some(value=>value == null)) return unavailable('nonfinite_result',provenance,[],'invalid');
  const value=decimalText(sumDecimals(values));
  return Number.isFinite(Number(value)) ? {value,status:'aggregated',provenance} :
    unavailable('nonfinite_result',provenance,[],'invalid');
}

function ratioResult(top,bottom,provenance,denominatorReason='zero_denominator') {
  if (!top || !bottom) return unavailable('nonfinite_result',provenance,[],'invalid');
  if (bottom.coefficient === 0n) return unavailable(denominatorReason,provenance,[],'invalid');
  const value=decimalText(divideDecimals(top,bottom));
  return Number.isFinite(Number(value)) ? {value,status:'calculated',provenance} :
    unavailable('nonfinite_result',provenance,[],'invalid');
}

function balancePeriodSide(report,row,months,side,options,context) {
  if (context.invalidMonths.length) return unavailable('invalid_period',[],context.invalidMonths,'invalid');
  const methods={62:{flow:23,base:7},63:{flow:23,stock:19},64:{flow:23,stock:36},
    65:{flow:7,stock:9,days:true,inverse:true,positive:true},66:{flow:11,stock:13,days:true,inverse:true,positive:true},
    67:{flow:7,stock:9,inverse:true,ending:true},68:{flow:7,stock:8,inverse:true,ending:true},
    70:{flow:7,stock:18}};
  const method=methods[Number(row.row)],pnl=options.pnlReport;
  if (!method || !pnl) return unavailable('ratio_period_method_required',provenanceFor(row,months,side,context),[],'unsupported');
  const flowRow=(pnl.rows || []).find(item=>Number(item.row) === method.flow);
  if (!flowRow) return unavailable('ratio_base_missing',[],[],'unsupported');
  const flow=sumSide(flowRow,months,side,options,periodContext(pnl,months,options.year),'flow');
  if (cellNumber(flow) == null) return flow;
  const provenance=[...flow.provenance];
  let flowDecimal=decimal(flow.value);
  if (method.positive && flowDecimal.coefficient < 0n) return unavailable('nonpositive_flow',provenance,[],'invalid');
  if (method.base) {
    const baseRow=(pnl.rows || []).find(item=>Number(item.row) === method.base);
    if (!baseRow) return unavailable('ratio_base_missing',provenance,[],'unsupported');
    const base=sumSide(baseRow,months,side,options,periodContext(pnl,months,options.year),'denominator');
    provenance.push(...base.provenance);
    if (cellNumber(base) == null) return {...base,provenance};
    return ratioResult(flowDecimal,decimal(base.value),provenance,'zero_base');
  }
  const stockRow=(report.rows || []).find(item=>Number(item.row) === method.stock);
  if (!stockRow) return unavailable('ratio_base_missing',provenance,[],'unsupported');
  // Every selected factual month must be present, even for a stock/flow ratio.
  const missing=months.filter(month=>side === 'fact' && Array.isArray(options.coverage) &&
    !options.coverage.some(period=>Number(period.month) === month && period.status === 'available'));
  if (missing.length) return unavailable('incomplete_period',provenance,missing);
  const ending=sourceSide(stockRow,months.at(-1),side,options,context);
  provenance.push(...ending.provenance.map(item=>({...item,role:'closing_balance'})));
  if (cellNumber(ending) == null) return {...ending,provenance};
  let stockDecimal=decimal(ending.value);
  if (!method.ending) {
    const first=statementCell(stockRow,months[0])[side];
    const opening=first?.opening_balance;
    if (cellNumber(opening) == null) return unavailable('opening_balance_missing',provenance,[months[0]],'unsupported');
    provenance.push({...opening,month:months[0],period:context.periods.get(months[0]),row:stockRow.row,role:'opening_balance'});
    stockDecimal=divideDecimals(sumDecimals([decimal(opening.value),stockDecimal]),decimal('2'));
  }
  if (method.days) {
    const year=context.year;
    if (!Number.isInteger(year)) return unavailable('invalid_period',provenance,months,'invalid');
    const days=(Date.UTC(year,months.at(-1),1)-Date.UTC(year,months[0]-1,1))/86400000;
    stockDecimal={...stockDecimal,coefficient:stockDecimal.coefficient*BigInt(days)};
  }
  return method.inverse ? ratioResult(stockDecimal,flowDecimal,provenance,'zero_base') :
    ratioResult(flowDecimal,stockDecimal,provenance);
}

/** Source-period presentation only: cash-ledger data and immutable source cells are untouched. */
export function aggregateStatementRow(report, row, options={}) {
  const months=selectedMonths(options);
  const balance=options.type === 'balance' || report?.sheet === '09b_Balance';
  const profileUnrecognized=options.profileUnrecognized || report?.calculation?.reason === 'profile_unrecognized';
  // Profit and turnover ratios combine flows with stocks. An ending monthly
  // ratio is not a quarterly/yearly ratio, even when its cache is numeric.
  const periodKpi=!profileUnrecognized && balance && months.length > 1 && row?.kind === 'ratio' &&
    [62,63,64,65,66,67,68,70].includes(Number(row.row));
  const checkedMonths=balance && months.length && !periodKpi ? [months.at(-1)] : months;
  const context=periodContext(report,checkedMonths,options.year);
  const mode=periodKpi ? 'ratio_period' : balance ? 'end_month' : months.length === 1 ? 'source' : row?.kind === 'ratio' ? 'ratio' : 'sum';
  const result={months,mode,year:context.year,reason:null};
  if (!months.length) {
    result.plan=unavailable('invalid_period',[],[],'invalid');
    result.fact=unavailable('invalid_period',[],[],'invalid');
  } else if (row?.kind === 'section') {
    result.plan={value:null,status:'missing',provenance:[]};
    result.fact={value:null,status:'missing',provenance:[]};
  } else if (periodKpi) {
    for (const side of ['plan','fact']) result[side]=balancePeriodSide(report,row,months,side,options,context);
  } else if (balance || months.length === 1) {
    for (const side of ['plan','fact']) result[side]=sourceSide(row,months.at(-1),side,options,context);
  } else if (rowMeasure(row) === 'amount') {
    for (const side of ['plan','fact']) result[side]=sumSide(row,months,side,options,context);
  } else if (profileUnrecognized) {
    for (const side of ['plan','fact']) result[side]=unavailable('profile_unrecognized',provenanceFor(row,months,side,context),[],'unsupported');
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
  aggregated:N_('Сумма подтверждённых месяцев'),calculated:N_('Рассчитано из исходных данных'),
  incomplete:N_('Неполный период'),unsupported:N_('Методика периода не задана'),
  requires_allocation:N_('Нужно распределение амортизации'),
  requires_assumptions:N_('Нужно уточнить параметры плана'),
  pending:N_('Ожидается загрузка месяца'),
  unavailable:N_('Расчётная база не подтверждена'),
};

export function sourceScopeMatches(payload, companyId) {
  // Company ID is also enforced by the server and the app request epoch.
  // Reject explicit mismatches rather than displaying another company's source.
  return [payload?.company_id,payload?.source?.company_id].every(id => id == null || Number(id) === Number(companyId));
}
