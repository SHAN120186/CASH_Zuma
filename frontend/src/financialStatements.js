import {N_} from './i18n/index.js';

export const MAX_STATEMENT_BYTES = 20 * 1024 * 1024;
const readableStatuses = new Set(['value', 'cached']);

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
  const value=actual-planned;
  // Ratio changes are percentage points. Negative and zero bases have no
  // unambiguous relative percentage; do not show an invented percentage.
  return {value,percent:kind === 'ratio' || planned <= 0 ? null : value/planned*100};
}

export function statementNumber(value, kind = 'amount', locale = 'ru-RU') {
  if (value == null || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat(locale, {minimumFractionDigits:2,maximumFractionDigits:2,
    ...(kind === 'ratio' ? {style:'percent'} : {})}).format(value);
}

export const statementStatuses = {
  value:N_('Значение источника'),cached:N_('Сохранённый результат формулы'),
  missing:N_('Нет данных'),missing_cache:N_('Нет результата формулы'),
  error:N_('Ошибка формулы'),invalid:N_('Некорректное значение'),future:N_('Будущий факт не подтверждён'),
};

export function sourceScopeMatches(payload, companyId) {
  // Company ID is also enforced by the server and the app request epoch.
  // Reject explicit mismatches rather than displaying another company's source.
  return [payload?.company_id,payload?.source?.company_id].every(id => id == null || Number(id) === Number(companyId));
}
