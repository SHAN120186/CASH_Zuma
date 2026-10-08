export const FORMATS = ['pdf', 'xlsx'];
export const MAX_REPORT_BYTES = 20 * 1024 * 1024;
const TYPES = {pdf: 'application/pdf', xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'};

export function fileProblem(file, format) {
  if (!file) return `Выберите файл ${format === 'pdf' ? 'PDF' : 'Excel (.xlsx)'}.`;
  if (!String(file.name || '').toLowerCase().endsWith('.' + format)) return `Нужен файл с расширением .${format}.`;
  if (file.name.length > 220 || /[\\/:\p{C}]/u.test(file.name)) return 'Имя файла слишком длинное или содержит недопустимые символы.';
  if (!file.size) return 'Файл пустой.';
  if (file.size > MAX_REPORT_BYTES) return 'Размер одного файла — не больше 20 МБ.';
  return '';
}

export function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '') || value < '2000-01-01' || value > '2100-12-31') return false;
  const date = new Date(value + 'T00:00:00Z');
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function periodProblem(start, end) {
  if (!validDate(start) || !validDate(end)) return 'Укажите начало и конец периода: даты от 2000 до 2100 года.';
  if (end < start) return 'Конец периода не может быть раньше начала.';
  if ((new Date(end + 'T00:00:00Z') - new Date(start + 'T00:00:00Z')) / 86400000 > 5 * 366) return 'Период одного отчёта — не больше пяти лет.';
  return '';
}

export function filterProblem(filters) {
  for (const key of ['date_from', 'date_to', 'uploaded_on']) {
    if (filters[key] && !validDate(filters[key])) return 'Проверьте даты фильтра: от 2000 до 2100 года.';
  }
  if (filters.date_from && filters.date_to && filters.date_from > filters.date_to) return 'Конец периода поиска не может быть раньше начала.';
  return '';
}

export function archiveUrl(companyId, filters = {}) {
  const params = new URLSearchParams({company_id: String(companyId)});
  for (const key of ['date_from', 'date_to', 'uploaded_on']) if (filters[key]) params.set(key, filters[key]);
  if (filters.include_deleted) params.set('include_deleted', 'true');
  return '/api/report-archives?' + params;
}

export const archiveFileUrl = (record, format) => `/api/report-archives/${record.id}/files/${format}?company_id=${record.company_id}`;
export const dateLabel = value => validDate(value) ? value.split('-').reverse().join('.') : '—';
export function uploadLabel(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat('ru-RU', {
    timeZone: 'Asia/Tashkent', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date);
}

function requestKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

// A request key and metadata snapshot survive a lost POST response; retry creates the same archive.
export function createArchiveState(record = null) {
  return {record, requestKey: requestKey(), fields: null, createUnknown: false};
}

export function archiveRequirements(form = {}, files = {}, savedFormats = []) {
  const issues = [], title = String(form.title ?? '').trim();
  if (!title) issues.push({field:'title',label:'Название',message:'Укажите название отчёта.'});
  else if (title.length > 160) issues.push({field:'title',label:'Название',message:'Название — до 160 символов.'});
  else if (/\p{C}/u.test(title)) issues.push({field:'title',label:'Название',message:'Название содержит недопустимые символы.'});
  for (const [field,label] of [['period_start','Начало периода'],['period_end','Конец периода']]) {
    if (!form[field]) issues.push({field,label,message:'Укажите дату.'});
    else if (!validDate(form[field])) issues.push({field,label,message:'Укажите действительную дату от 2000 до 2100 года.'});
  }
  if (validDate(form.period_start) && validDate(form.period_end)) {
    const problem = periodProblem(form.period_start,form.period_end);
    if (problem) issues.push({field:'period_end',label:'Конец периода',message:problem});
  }
  for (const format of FORMATS.filter(value=>!savedFormats.includes(value))) {
    const problem = fileProblem(files[format],format);
    if (problem) issues.push({field:format,label:format==='pdf'?'Файл PDF':'Файл Excel (.xlsx)',message:problem});
  }
  return issues;
}

export async function saveArchive(api, state, {companyId, form, files, isCurrent = () => true, onRecord, onStep, onProgress} = {}) {
  const check = () => { if (!isCurrent()) throw new DOMException('Раздел или компания изменены', 'AbortError'); };
  check();
  if (state.record && state.record.company_id !== companyId) throw Error('Отчёт относится к другой компании.');
  const missing = FORMATS.filter(format => !state.record?.formats?.includes(format));
  const issues = archiveRequirements(form,files,state.record?.formats || []);
  if (issues.length) throw Error(issues.map(issue=>issue.label+': '+issue.message).join(' '));
  const title = String(form.title ?? '').trim();
  const total = (state.record ? 0 : 1) + missing.length + 1;
  let completed = 0;
  const stage = label => {onStep?.(label);onProgress?.({completed,total,label});};
  const confirmed = label => {completed++;stage(label);};
  if (!state.record) {
    state.fields ||= {company_id: companyId, title, period_start: form.period_start, period_end: form.period_end, request_key: state.requestKey};
    if (state.fields.company_id !== companyId) throw Error('Форма относится к другой компании. Откройте новую загрузку.');
    stage('Сохраняем версию отчёта…');
    check();
    let record;
    try {
      record = await api(archiveUrl(companyId), {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(state.fields)});
    } catch (e) {
      // A definite refusal on the first attempt allows correcting the form. An uncertain
      // response keeps its metadata and key, so the same version can be recovered safely.
      if (e.status >= 400 && e.status < 500 && !state.createUnknown) state.fields = null;
      else state.createUnknown = true;
      throw e;
    }
    check();
    if (record.company_id !== companyId) throw Error('Сервер вернул отчёт другой компании. Обновите страницу.');
    state.record = record;
    state.createUnknown = false;
    onRecord?.(record);
    confirmed('Версия отчёта сохранена');
  }
  for (const format of FORMATS) {
    check();
    if (state.record.formats?.includes(format)) continue;
    stage(`Загружаем ${format === 'pdf' ? 'PDF' : 'Excel'}…`);
    const record = await api(archiveFileUrl(state.record, format), {
      method: 'PUT', headers: {'Content-Type': TYPES[format], 'X-Filename': encodeURIComponent(files[format].name)}, body: files[format],
    });
    check();
    if (record.company_id !== companyId || record.id !== state.record.id) throw Error('Сервер вернул другую версию отчёта. Обновите страницу.');
    state.record = record;
    onRecord?.(record);
    confirmed(`${format === 'pdf' ? 'PDF' : 'Excel'} сохранён`);
  }
  stage('Проверяем готовность версии…');
  if (state.record.status !== 'ready' || !FORMATS.every(format => state.record.formats?.includes(format))) {
    throw Error('Версия сохранена как черновик. Обновите список и завершите загрузку недостающих файлов.');
  }
  confirmed('PDF и Excel готовы');
  return state.record;
}
