import {createArchiveState} from './reportArchive.js';

export const SOURCE_ACCEPT = '.pdf,.xlsx,.xltx,.docx,.csv,.txt,.json,.zip,.png,.jpg,.jpeg';
export const MAX_SOURCE_SIZE = 20 * 1024 * 1024;
export const MAX_FOLDER_SIZE = 100 * 1024 * 1024;
export const MAX_SOURCE_COUNT = 100;
const EXTENSIONS = new Set(SOURCE_ACCEPT.split(',').map(value => value.slice(1)));
export const BUSINESS_SCALARS = [
  ['opening_cash', 'Деньги на начало'], ['opening_receivables', 'Дебиторская задолженность на начало'],
  ['opening_inventory', 'Запасы на начало'], ['opening_payables', 'Кредиторская задолженность на начало'],
  ['initial_investment', 'Инвестиции до начала прогноза (без CAPEX следующих месяцев)'],
  ['receivable_days', 'Срок оплаты покупателей, дней'], ['inventory_days', 'Запасы, дней'], ['payable_days', 'Срок оплаты поставщикам, дней'],
];
export const NARRATIVE_FIELDS = [
  ['project_description', 'Описание проекта'], ['technology', 'Технология и производственный процесс'],
  ['market', 'Рынок и сбыт'], ['location', 'Место реализации'], ['investment_purpose', 'Назначение инвестиций'],
];
const LABELS = Object.fromEntries([...BUSINESS_SCALARS, ...NARRATIVE_FIELDS, ['title', 'Название'], ['start', 'Начало прогноза'],
  ['months', 'Количество месяцев'], ['currency', 'Валюта'], ['tax_rate', 'Налог на прибыль'], ['discount_rate', 'Ставка дисконтирования'],
  ['fixed_costs', 'Постоянные расходы'], ['equity', 'Взносы в капитал'], ['name', 'Название'], ['unit', 'Единица измерения'],
  ['price', 'Цена реализации'], ['unit_cost', 'Себестоимость единицы'], ['quantities', 'Объём реализации'], ['capacity', 'Мощность'],
  ['value', 'Стоимость'], ['life_months', 'Срок амортизации'], ['commissioning_month', 'Месяц ввода'],
  ['opening_balance', 'Остаток кредита на начало'], ['drawdowns', 'Получение кредита'], ['principal', 'Погашение основного долга'], ['interest', 'Проценты'],
  ['sources', 'Исходные файлы'], ['documents', 'Документы'], ['narratives', 'Описание проекта'], ['source_issues', 'Проверка источников'],
  ['model', 'Исходные данные'], ['products', 'Продукция'], ['assets', 'Активы'], ['loans', 'Кредиты']]);

export function fieldLabel(field = '') {
  return String(field).split('.').map(part => {
    const match = /^(\w+)\[(\d+)\]$/.exec(part);
    return match ? `${LABELS[match[1]] || 'Параметр'} ${['products', 'assets', 'loans'].includes(match[1]) ? '№' : '· месяц '}${Number(match[2]) + 1}` : LABELS[part] || 'Параметр проекта';
  }).join(' · ');
}
export const sourceSize = size => size >= 1024 * 1024 ? (size / 1024 / 1024).toFixed(1) + ' МБ' : Math.ceil(size / 1024) + ' КБ';

export function prepareSources(selected = []) {
  const files = [], ignored = [], problems = [], paths = new Set();
  let total = 0;
  for (const [index, file] of [...selected].entries()) {
    const relativePath = String(file.webkitRelativePath || file.name || '');
    const parts = relativePath.split('/'), name = String(file.name || parts.at(-1) || '');
    if (/^(?:\.DS_Store|Thumbs\.db|desktop\.ini|\._.*)$/i.test(name) || parts.includes('__MACOSX')) {
      ignored.push({name: relativePath, reason: 'Служебный файл операционной системы'}); continue;
    }
    const extension = name.toLowerCase().split('.').at(-1);
    if (!EXTENSIONS.has(extension)) {
      problems.push(`${relativePath}: формат не поддерживается. Допустимы PDF, XLSX, XLTX, DOCX, CSV, TXT, JSON, ZIP, PNG и JPEG.`); continue;
    }
    if (!relativePath || relativePath.length > 500 || parts.some(part => !part || part === '.' || part === '..') || /[\\:\p{C}]/u.test(relativePath) || /[\\/:\p{C}]/u.test(name) || name.length > 220) {
      problems.push(`${name}: небезопасное имя или путь файла.`); continue;
    }
    if (paths.has(relativePath)) {problems.push(`${relativePath}: путь выбран дважды.`); continue;}
    if (!file.size || file.size > MAX_SOURCE_SIZE) {problems.push(`${relativePath}: файл должен быть непустым и не больше 20 МБ.`); continue;}
    paths.add(relativePath); total += file.size;
    files.push({key: `${index}:${relativePath}`, file, name, relativePath, size: file.size});
  }
  if (files.length > MAX_SOURCE_COUNT) problems.push('В одной папке допускается не больше 100 исходных файлов.');
  if (total > MAX_FOLDER_SIZE) problems.push('Общий размер исходных файлов — не больше 100 МБ.');
  if (!files.length && !problems.length) problems.push('Выберите хотя бы один исходный файл.');
  return {files, ignored, problems, total};
}

export function folderProjectTitle(sources = []) {
  const roots = sources.map(item => String(item.relativePath || item.webkitRelativePath || item.file?.webkitRelativePath || '').split('/'));
  if (!roots.length || roots.some(parts => parts.length < 2 || !parts[0])) return '';
  return roots.every(parts => parts[0] === roots[0][0]) ? roots[0][0].slice(0, 160) : '';
}

export const projectUrl = (companyId, id = null, action = '') => `/api/business-projects${id == null ? '' : '/' + id}${action ? '/' + action : ''}?company_id=${companyId}`;
export const templateUrl = companyId => `/api/business-projects/template.xlsx?company_id=${companyId}`;
const json = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
const clone = value => value == null ? '' : Array.isArray(value) ? value.map(clone) : String(value);
export const numericText = value => value == null ? '' : String(value).trim().replace(/[\s\u00a0]/g, '').replace(',', '.');

// Shift decimal strings without a floating-point round-trip, including small tax rates.
export function decimalShift(value, power) {
  const text = numericText(value);
  if (!text) return '';
  const match = /^([+-]?)(\d+)(?:\.(\d*))?$/.exec(text);
  if (!match) return text;
  const digits = match[2] + (match[3] || ''), position = match[2].length + power;
  let whole = position <= 0 ? '0' : digits.slice(0, position).padEnd(position, '0');
  let fraction = position <= 0 ? '0'.repeat(-position) + digits : digits.slice(position);
  whole = whole.replace(/^0+(?=\d)/, ''); fraction = fraction.replace(/0+$/, '');
  return (match[1] === '-' ? '-' : '') + whole + (fraction ? '.' + fraction : '');
}
const schedule = value => Array.isArray(value) ? value.map(numericText) : numericText(value);
const integer = value => value === '' || value == null ? null : Number(value);

export function formFromInputs(inputs = {}, fallback = {}) {
  inputs = inputs && typeof inputs === 'object' && !Array.isArray(inputs) ? inputs : {};
  const form = {title: String(inputs.title || fallback.title || ''), currency: String(inputs.currency || fallback.currency || ''),
    start: String(inputs.start || fallback.start || ''), months: inputs.months ?? fallback.months ?? 36,
    tax_rate: decimalShift(inputs.tax_rate, 2), discount_rate: decimalShift(inputs.discount_rate, 2),
    fixed_costs: clone(inputs.fixed_costs), equity: clone(inputs.equity), no_assets: Array.isArray(inputs.assets) && !inputs.assets.length,
    no_loans: Array.isArray(inputs.loans) && !inputs.loans.length};
  for (const [field] of [...BUSINESS_SCALARS, ...NARRATIVE_FIELDS]) form[field] = clone(inputs[field]);
  const objects = entries => (Array.isArray(entries) ? entries : []).map(entry => entry && typeof entry === 'object' ? entry : {});
  form.products = objects(inputs.products).map(product => ({name: String(product.name || ''), unit: String(product.unit || ''),
    price: clone(product.price), unit_cost: clone(product.unit_cost), quantities: clone(product.quantities), capacity: product.capacity == null ? null : clone(product.capacity)}));
  form.assets = objects(inputs.assets).map(asset => ({name: String(asset.name || ''), value: clone(asset.value), life_months: asset.life_months ?? '', commissioning_month: asset.commissioning_month ?? ''}));
  form.loans = objects(inputs.loans).map(loan => ({name: String(loan.name || ''), opening_balance: clone(loan.opening_balance),
    drawdowns: clone(loan.drawdowns), principal: clone(loan.principal), interest: clone(loan.interest)}));
  return form;
}

export function inputsFromForm(form) {
  const inputs = {title: String(form.title || '').trim(), currency: form.currency, start: form.start, months: integer(form.months),
    tax_rate: decimalShift(form.tax_rate, -2), discount_rate: decimalShift(form.discount_rate, -2), fixed_costs: schedule(form.fixed_costs), equity: schedule(form.equity)};
  for (const [field] of BUSINESS_SCALARS) inputs[field] = numericText(form[field]);
  for (const [field] of NARRATIVE_FIELDS) inputs[field] = String(form[field] || '').trim();
  inputs.products = form.products.map(product => ({name: product.name.trim(), unit: product.unit.trim(), price: numericText(product.price),
    unit_cost: numericText(product.unit_cost), quantities: schedule(product.quantities), capacity: product.capacity == null ? null : schedule(product.capacity)}));
  inputs.assets = form.assets.length || form.no_assets ? form.assets.map(asset => ({name: asset.name.trim(), value: numericText(asset.value),
    life_months: integer(asset.life_months), commissioning_month: integer(asset.commissioning_month)})) : null;
  inputs.loans = form.loans.length || form.no_loans ? form.loans.map(loan => ({name: loan.name.trim(), opening_balance: numericText(loan.opening_balance),
    drawdowns: schedule(loan.drawdowns), principal: schedule(loan.principal), interest: schedule(loan.interest)})) : null;
  return inputs;
}

export function monthLabels(start, months) {
  const count = Math.min(60, Math.max(0, Number(months) || 0));
  const match = /^(\d{4})-(\d{2})-01$/.exec(start || '');
  return Array.from({length: count}, (_, index) => {
    if (!match) return `Месяц ${index + 1}`;
    const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1 + index, 1));
    return new Intl.DateTimeFormat('ru-RU', {timeZone: 'UTC', month: 'short', year: 'numeric'}).format(date);
  });
}
export function resizeValues(values, months) {
  const count = Math.min(60, Math.max(1, Number(months) || 1));
  return Array.from({length: count}, (_, index) => Array.isArray(values) ? values[index] ?? '' : values ?? '');
}
export function headerProblem(header) {
  if (!String(header.title || '').trim() || String(header.title).trim().length > 160) return 'Укажите название проекта: до 160 символов.';
  const match = /^(\d{4})-(\d{2})-01$/.exec(header.start || '');
  const months = Number(header.months);
  if (!match || Number(match[1]) < 2000 || Number(match[1]) > 2100 || Number(match[2]) < 1 || Number(match[2]) > 12) return 'Выберите первый месяц прогноза.';
  if (!Number.isInteger(months) || months < 1 || months > 60) return 'Прогноз: от 1 до 60 месяцев.';
  if (Number(match[1]) * 12 + Number(match[2]) - 1 + months - 1 > 2100 * 12 + 11) return 'Весь прогноз должен заканчиваться не позже декабря 2100 года.';
  if (header.currency && !['USD', 'UZS', 'EUR'].includes(header.currency)) return 'Выберите USD, UZS или EUR.';
  return '';
}

export function createBusinessState(project = null) {
  return {project, requestKey: createArchiveState().requestKey, createFields: null, createUnknown: false, uploaded: new Set(), sourceUploadStarted: false};
}
export function uploadFailureMessage(error, state) {
  const message = error?.message || 'Загрузка не завершена.';
  if (state?.uploaded?.size) return message + ' Уже загруженные файлы сохранены. Повторная попытка продолжит этот проект.';
  if (state?.sourceUploadStarted) return message + ' Повторите загрузку: проверим файл и продолжим этот проект.';
  return message;
}
export function assertProject(project, companyId, id = null) {
  if (!project || project.company_id !== companyId || (id != null && project.id !== id)) throw Error('Сервер вернул проект другой компании. Обновите список.');
  return project;
}

export async function uploadProjectFolder(api, state, {companyId, header, sources, isCurrent = () => true, onProject, onStep} = {}) {
  const check = () => {if (!isCurrent()) throw new DOMException('Раздел или компания изменены', 'AbortError');};
  const remember = project => {check(); assertProject(project, companyId, state.project?.id); state.project = project; onProject?.(project);};
  check();
  const problem = headerProblem(header);
  if (problem) throw Error(problem);
  const picked = prepareSources(sources.map(item => item.file));
  if (picked.problems.length) throw Error(picked.problems.join(' '));
  if (!state.project) {
    state.createFields ||= {company_id: companyId, title: header.title.trim(), request_key: state.requestKey};
    if (state.createFields.company_id !== companyId) throw Error('Форма относится к другой компании.');
    onStep?.('Создаём папку проекта…');
    let project;
    try {project = await api(projectUrl(companyId), json(state.createFields));}
    catch (e) {if (e.status >= 400 && e.status < 500 && !state.createUnknown) state.createFields = null; else state.createUnknown = true; throw e;}
    remember(project); state.createUnknown = false;
  }
  assertProject(state.project, companyId);
  if (state.project.can_edit === false) throw Error('Изменение этой папки недоступно.');
  for (const [index, item] of sources.entries()) {
    check(); if (state.uploaded.has(item.key)) continue;
    onStep?.(`Загружаем ${index + 1} из ${sources.length}: ${item.relativePath}`);
    state.sourceUploadStarted = true;
    const project = await api(projectUrl(companyId, state.project.id, 'sources') + '&expected_revision=' + state.project.revision, {
      method: 'PUT', body: item.file, headers: {'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(item.name), 'X-Relative-Path': encodeURIComponent(item.relativePath)},
    });
    remember(project); state.uploaded.add(item.key);
  }
  check(); onStep?.('Изучаем источники и рассчитываем бизнес-план и ТЭО…');
  const overrides = {title: header.title.trim(), start: header.start, months: Number(header.months)};
  if (header.currency) overrides.currency = header.currency;
  const project = await api(projectUrl(companyId, state.project.id, 'analyse'), json({revision: state.project.revision, overrides}));
  remember(project); return project;
}

export async function generateProject(api, project, companyId, inputs, confirmSources, isCurrent = () => true) {
  assertProject(project, companyId);
  if (!project.can_edit) throw Error('Изменение этого проекта недоступно.');
  if (!isCurrent()) throw new DOMException('Раздел или компания изменены', 'AbortError');
  const result = await api(projectUrl(companyId, project.id, 'generate'), json({revision: project.revision, inputs, confirm_sources: !!confirmSources}));
  if (!isCurrent()) throw new DOMException('Раздел или компания изменены', 'AbortError');
  return assertProject(result, companyId, project.id);
}

export function issueMessage(issue) {
  if (typeof issue === 'string') return issue;
  return issue?.message || issue?.detail || 'Проверьте исходный файл и параметры проекта.';
}
export function sourceLocation(source) {
  if (typeof source === 'string') return source;
  if (!source) return '';
  return [source.filename, source.sheet ? 'лист «' + source.sheet + '»' : '', source.cell, source.page ? 'стр. ' + source.page : ''].filter(Boolean).join(' · ');
}
export function sourceIssues(extraction) {
  return Array.isArray(extraction?.issues) ? extraction.issues : [];
}
export function deduplicateIssues(issues = []) {
  const byKey = new Map();
  for (const entry of issues) {
    const issue = typeof entry === 'string' ? {message: entry} : entry;
    if (!issue || typeof issue !== 'object') continue;
    const key = `${issue.field || ''}\u0000${issueMessage(issue)}`;
    const item = byKey.get(key) || {...issue, sources: []};
    const blocking = issue.blocking === true || ['error', 'fatal', 'blocking'].includes(String(issue.severity || '').toLowerCase());
    item.requires_confirmation ||= !!issue.requires_confirmation || blocking;
    for (const source of [...(Array.isArray(issue.sources) ? issue.sources : []), ...(issue.source ? [issue.source] : [])]) {
      if (!item.sources.some(value => sourceLocation(value) === sourceLocation(source))) item.sources.push(source);
    }
    byKey.set(key, item);
  }
  return [...byKey.values()];
}
export function projectReview(extraction, validation = []) {
  const current = deduplicateIssues(validation);
  const fields = current.filter(issue => issue.code !== 'source_issues');
  const source = deduplicateIssues(sourceIssues(extraction).filter(issue => issue?.code !== 'missing_or_invalid'));
  const checks = deduplicateIssues([...source.filter(issue => issue.requires_confirmation),
    ...current.filter(issue => issue.code === 'source_issues').map(issue => ({...issue, requires_confirmation: true}))]);
  const checkKeys = new Set(checks.map(issue => `${issue.field || ''}\u0000${issueMessage(issue)}`));
  const references = source.filter(issue => !issue.requires_confirmation && !checkKeys.has(`${issue.field || ''}\u0000${issueMessage(issue)}`));
  return {fields, checks, references};
}
export function evidenceRows(extraction) {
  const evidence = extraction?.evidence;
  if (Array.isArray(evidence)) return evidence;
  if (!evidence || typeof evidence !== 'object') return [];
  return Object.entries(evidence).flatMap(([field, entries]) => (Array.isArray(entries) ? entries : [entries]).map(entry => ({field, ...(typeof entry === 'object' && entry ? entry : {source: String(entry)})})));
}
