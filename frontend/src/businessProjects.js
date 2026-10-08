import {createArchiveState} from './reportArchive.js';
import {tx, N_, formatDate} from './i18n/index.js';

export const SOURCE_ACCEPT = '.pdf,.xlsx,.xltx,.docx,.csv,.txt,.json,.zip,.png,.jpg,.jpeg';
export const MAX_SOURCE_SIZE = 20 * 1024 * 1024;
export const MAX_FOLDER_SIZE = 100 * 1024 * 1024;
export const MAX_SOURCE_COUNT = 100;
// Original reports are downloaded as uploaded. This view preserves the supplied
// document, and must never label it as a newly calculated forecast.
export function originalReportFiles(project, companyId) {
  if (!project?.can_edit || !Number.isSafeInteger(project.id) || project.id <= 0 || project.company_id !== companyId) return [];
  const formats = {xlsx: 'Excel', docx: 'Word', pdf: 'PDF'};
  const reportName = /бизнес[\s_-]*план|business[\s_-]*plan|(?:^|[\s_-])тео(?:[.\s_-]|$)|uzgermed_pharm_36m|лекарство_производство/i;
  return (project.files || []).flatMap(file => {
    const name = String(file.filename || '').normalize('NFKC');
    const extension = name.split('.').pop().toLowerCase();
    if (!formats[extension] || !reportName.test(name) || !Number.isSafeInteger(file.id) || file.id <= 0) return [];
    const url = `/api/business-projects/${project.id}/sources/${file.id}/download?company_id=${companyId}`;
    if (file.download_url !== url) return [];
    return [{...file, format: formats[extension], downloadUrl: url}];
  });
}
const EXTENSIONS = new Set(SOURCE_ACCEPT.split(',').map(value => value.slice(1)));
// Labels stay Russian in these lists (they are also keys of the dictionary);
// the form shows them with tx(label).
export const BUSINESS_SCALARS = [
  ['opening_cash', N_('Деньги на начало')], ['opening_receivables', N_('Дебиторская задолженность на начало')],
  ['opening_inventory', N_('Запасы на начало')], ['opening_payables', N_('Кредиторская задолженность на начало')],
  ['initial_investment', N_('Инвестиции до начала прогноза (без CAPEX следующих месяцев)')],
  ['receivable_days', N_('Срок оплаты покупателей, дней')], ['inventory_days', N_('Запасы, дней')], ['payable_days', N_('Срок оплаты поставщикам, дней')],
];
export const NARRATIVE_FIELDS = [
  ['project_description', N_('Описание проекта')], ['initiator', N_('Инициатор проекта')], ['strategy', N_('Стратегия развития')],
  ['market', N_('Рынок и сбыт')], ['resources', N_('Сырьё и ресурсы')], ['location', N_('Место реализации')],
  ['technology', N_('Технология и производственный процесс')], ['organization', N_('Организация работы')],
  ['personnel', N_('Персонал')], ['investment_purpose', N_('Назначение инвестиций')],
  ['insurance', N_('Страхование')], ['risks', N_('Риски и меры снижения')],
];
const LABELS = Object.fromEntries([...BUSINESS_SCALARS, ...NARRATIVE_FIELDS, ['title', N_('Название')], ['start', N_('Начало прогноза')],
  ['months', N_('Количество месяцев')], ['currency', N_('Валюта')], ['tax_rate', N_('Налог на прибыль')], ['discount_rate', N_('Ставка дисконтирования')],
  ['fixed_costs', N_('Постоянные расходы')], ['equity', N_('Взносы в капитал')], ['name', N_('Название')], ['unit', N_('Единица измерения')],
  ['price', N_('Цена реализации')], ['unit_cost', N_('Себестоимость единицы')], ['quantities', N_('Объём реализации')], ['capacity', N_('Мощность')],
  ['value', N_('Стоимость')], ['life_months', N_('Срок амортизации')], ['commissioning_month', N_('Месяц ввода')],
  ['opening_balance', N_('Остаток кредита на начало')], ['drawdowns', N_('Получение кредита')], ['principal', N_('Погашение основного долга')], ['interest', N_('Проценты')],
  ['sources', N_('Исходные файлы')], ['documents', N_('Документы')], ['narratives', N_('Описание проекта')], ['source_issues', N_('Проверка источников')],
  ['model', N_('Исходные данные')], ['products', N_('Продукция')], ['assets', N_('Активы')], ['loans', N_('Кредиты')]]);
const labelOf = key => Object.hasOwn(LABELS, key) ? LABELS[key] : '';

// Readable name of a model field in the current language: «Продукция №1 · Объём реализации · месяц 3».
export function fieldLabel(field = '') {
  return String(field).split('.').map(part => {
    const match = /^(\w+)\[(\d+)\]$/.exec(part);
    if (!match) return tx(labelOf(part) || N_('Параметр проекта'));
    const label = tx(labelOf(match[1]) || N_('Параметр')), n = Number(match[2]) + 1;
    return ['products', 'assets', 'loans'].includes(match[1]) ? tx('{label} №{n}', {label, n}) : tx('{label} · месяц {n}', {label, n});
  }).join(' · ');
}
export const sourceSize = size => size >= 1024 * 1024 ? tx('{n} МБ', {n: (size / 1024 / 1024).toFixed(1)}) : tx('{n} КБ', {n: Math.ceil(size / 1024)});

export function prepareSources(selected = []) {
  const files = [], ignored = [], problems = [], paths = new Set();
  let total = 0;
  for (const [index, file] of [...selected].entries()) {
    const relativePath = String(file.webkitRelativePath || file.name || '');
    const parts = relativePath.split('/'), name = String(file.name || parts.at(-1) || '');
    if (/^(?:\.DS_Store|Thumbs\.db|desktop\.ini|\._.*)$/i.test(name) || parts.includes('__MACOSX')) {
      // The reason is shown with tx(item.reason), so it follows a later language switch.
      ignored.push({name: relativePath, reason: N_('Служебный файл операционной системы')}); continue;
    }
    const extension = name.toLowerCase().split('.').at(-1);
    if (!EXTENSIONS.has(extension)) {
      problems.push(tx('{path}: формат не поддерживается. Допустимы PDF, XLSX, XLTX, DOCX, CSV, TXT, JSON, ZIP, PNG и JPEG.', {path: relativePath})); continue;
    }
    if (!relativePath || relativePath.length > 500 || parts.some(part => !part || part === '.' || part === '..') || /[\\:\p{C}]/u.test(relativePath) || /[\\/:\p{C}]/u.test(name) || name.length > 220) {
      problems.push(tx('{name}: небезопасное имя или путь файла.', {name})); continue;
    }
    if (paths.has(relativePath)) {problems.push(tx('{path}: путь выбран дважды.', {path: relativePath})); continue;}
    if (!file.size || file.size > MAX_SOURCE_SIZE) {problems.push(tx('{path}: файл должен быть непустым и не больше 20 МБ.', {path: relativePath})); continue;}
    paths.add(relativePath); total += file.size;
    files.push({key: `${index}:${relativePath}`, file, name, relativePath, size: file.size});
  }
  if (files.length > MAX_SOURCE_COUNT) problems.push(tx('В одной папке допускается не больше 100 исходных файлов.'));
  if (total > MAX_FOLDER_SIZE) problems.push(tx('Общий размер исходных файлов — не больше 100 МБ.'));
  if (!files.length && !problems.length) problems.push(tx('Выберите хотя бы один исходный файл.'));
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
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(text);
  if (!match) return text;
  const exponent = Number(match[4] || 0);
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 1000) return text;
  const digits = match[2] + (match[3] || ''), position = match[2].length + power + exponent;
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

// Sheet and file names of the UZGERMED source folder are quoted exactly as they
// appear in the documents, in every language; the guidance fills them in.
const SOURCE_NAMES = {costSheet: 'Стоим_проекта', laborSheet: 'Труд', workshopSheet: 'Цех_стоим', capitalSheet: 'Раб_капит', assetsFile: 'ОС.расш.PDF'}; // i18n-ignore
// What each field means, where to find the value and how to fill it. The texts
// are translated by fieldGuidance() when they are shown.
const GUIDANCE = {
  title: {label: N_('Название проекта'), what: N_('Название, которое будет напечатано в бизнес-плане и ТЭО.'),
    source: N_('Название папки или паспорт проекта.'), how: N_('Укажите понятное название проекта; предложенное название папки можно изменить.')},
  start: {label: N_('Начало прогноза'), what: N_('Первый месяц, за который нужно рассчитать деятельность проекта.'),
    source: N_('План запуска проекта и дата подтверждённых начальных остатков.'),
    how: N_('Выберите месяц и год. Начальные остатки должны относиться к началу этого месяца; между датой отчётности и началом прогноза потребуется сверка движений.')},
  months: {label: N_('Количество месяцев'), what: N_('Продолжительность финансового прогноза.'),
    source: N_('Задание на бизнес-план или ТЭО.'), how: N_('Укажите число месяцев. Каждый помесячный график должен покрывать этот период.')},
  currency: {label: N_('Валюта и единицы сумм'), what: N_('Единая валюта и масштаб всех денежных параметров модели.'),
    source: N_('Заголовки исходных таблиц и утверждённые условия пересчёта.'),
    how: N_('Выберите валюту и приведите суммы к её денежным единицам. Форма-1 и Форма-2 из папки UZGERMED содержат тысячи сумов. Дату и курс пересчёта нужно подтвердить отдельно.')},
  tax_rate: {label: N_('Налог на прибыль'), what: N_('Подтверждённая ставка налога на прибыль для этого проекта.'),
    source: N_('Налоговые условия организации и согласованные параметры проекта.'),
    how: N_('Введите ставку в процентах. Ставку из старой финансовой модели нужно проверить для выбранного проекта и периода.'),
    zero: N_('Явный 0 допустим только при подтверждённой нулевой ставке в расчёте.')},
  discount_rate: {label: N_('Годовая ставка дисконтирования'), what: N_('Согласованная ставка для оценки будущих денежных потоков.'),
    source: N_('Утверждённые предпосылки расчёта проекта.'),
    how: N_('Введите годовую ставку в процентах. В исходной книге UZGERMED она связана со ставкой кредита; эту связь нужно подтвердить.'),
    zero: N_('Явный 0 означает расчёт без дисконтирования и требует осознанного выбора.')},
  opening_cash: {label: N_('Деньги на начало'), what: N_('Доступные деньги проекта на начало первого месяца прогноза.'),
    source: N_('Остатки банковских счетов и кассы. Для UZGERMED: Форма-1, list02!E46, строка 320, конец II квартала 2026 года, тыс. сумов.'),
    how: N_('Сверьте остаток с выбранной датой начала и валютой проекта. Укажите общую подтверждённую сумму; будущие поступления учитываются в своих месяцах.'),
    zero: N_('Укажите 0, если на начало прогноза денег действительно нет.')},
  opening_receivables: {label: N_('Дебиторская задолженность на начало'), what: N_('Сумма, которую покупатели уже должны проекту на начало прогноза.'),
    source: N_('Расшифровка расчётов с покупателями. Для UZGERMED: Форма-1, list02!E36, строка 220, конец II квартала 2026 года, тыс. сумов.'),
    how: N_('Выделите задолженность покупателей и подтвердите её остаток на выбранную дату в валюте проекта. Общая дебиторская задолженность отчётности может включать другие расчёты.'),
    zero: N_('Укажите 0, если задолженности покупателей на начало действительно нет.')},
  opening_inventory: {label: N_('Запасы на начало'), what: N_('Стоимость сырья, незавершённого производства и готовой продукции на начало прогноза.'),
    source: N_('Инвентаризация и расшифровка запасов. Для UZGERMED: Форма-1, list02!E27, строка 140, компоненты E28:E30; тыс. сумов.'),
    how: N_('Укажите стоимость запасов в валюте проекта. Сверьте дату остатка; количество упаковок само по себе не является стоимостью запасов.'),
    zero: N_('Укажите 0, если запасов на начало действительно нет.')},
  opening_payables: {label: N_('Кредиторская задолженность на начало'), what: N_('Долг поставщикам за уже полученные товары и услуги на начало прогноза.'),
    source: N_('Расшифровка расчётов с поставщиками. Для UZGERMED: Форма-1, list02!E81, строка 610, оставлена пустой; нужен подтверждённый остаток.'),
    how: N_('Уточните именно задолженность поставщикам на выбранную дату. Сверьте её состав и валюту; общий итог обязательств включает также кредиты и другие долги.'),
    zero: N_('Укажите 0 только после подтверждения отсутствия долга поставщикам. Пустая строка отчёта такого подтверждения не даёт.')},
  initial_investment: {label: N_('Инвестиции до начала прогноза'), what: N_('Отдельная сумма инвестиций до первого месяца расчёта для оценки денежных потоков проекта.'),
    source: N_('Смета и подтверждённый план вложений. Лист «{costSheet}» UZGERMED содержит также существующие активы и рабочий капитал.'),
    how: N_('Укажите согласованную сумму до начала прогноза. Покупки активов в следующих месяцах задайте в разделе основных средств; один расход учитывается один раз.'),
    zero: N_('Укажите 0, если в выбранном расчёте инвестиций до начала прогноза нет.')},
  fixed_costs: {label: N_('Постоянные расходы'), what: N_('Регулярные операционные расходы, которые не включены в затраты на единицу продукции.'),
    source: N_('Смета постоянных затрат, штатное расписание и коммунальные условия. В UZGERMED: листы «{laborSheet}» и «{workshopSheet}».'),
    how: N_('Сложите подтверждённые постоянные расходы и укажите месячную сумму или график. Амортизация, проценты и затраты на единицу продукции рассчитываются отдельно.'),
    zero: N_('Укажите 0 только для месяца, в котором постоянных расходов действительно нет.')},
  equity: {label: N_('Взносы в капитал'), what: N_('Деньги, которые собственники внесут в проект в течение прогноза.'),
    source: N_('План финансирования и подтверждённые решения собственников.'),
    how: N_('Задайте сумму или график будущих взносов по месяцам. Уже имеющийся уставный капитал сам по себе не означает новое денежное поступление.'),
    zero: N_('Если будущих денежных взносов нет, укажите 0 явно.')},
  receivable_days: {label: N_('Срок оплаты покупателей'), what: N_('Согласованное число дней, за которое покупатели оплачивают реализацию.'),
    source: N_('Условия продаж и договоры с покупателями.'), how: N_('Введите подтверждённый срок в днях. При разных условиях продаж согласуйте общий срок для этой модели.'),
    zero: N_('Укажите 0, если в модели покупатели оплачивают реализацию сразу.')},
  inventory_days: {label: N_('Запасы, дней'), what: N_('Согласованный срок хранения запасов для расчёта оборотного капитала.'),
    source: N_('Производственный и закупочный план. В UZGERMED сырьё и готовая продукция имеют отдельные сроки на листе «{capitalSheet}».'),
    how: N_('Подтвердите единый срок запасов в днях для этой модели.'), zero: N_('Укажите 0 только при согласованном расчёте без остатка запасов.')},
  payable_days: {label: N_('Срок оплаты поставщикам'), what: N_('Согласованный срок оплаты закупок и возникновения задолженности поставщикам.'),
    source: N_('Договоры поставок и план расчётов.'), how: N_('Введите подтверждённый срок в днях. Условия разных поставщиков нужно согласовать для общего расчёта.'),
    zero: N_('Укажите 0, если в модели задолженность поставщикам не возникает.')},
  products: {label: N_('Продукция'), what: N_('Продукты или услуги, из которых складываются выручка и переменные затраты.'),
    source: N_('Производственный план, прайс-лист и калькуляции.'), how: N_('Добавьте хотя бы одну позицию и заполните название, единицу, цену, затраты на единицу и объём реализации.')},
  'products.name': {what: N_('Однозначное название продукции или услуги.'), source: N_('Ассортимент и производственный план.'), how: N_('Укажите название, дозировку и упаковку, если они различают позиции.')},
  'products.unit': {what: N_('Единица, к которой относятся цена, затраты и объём.'), source: N_('Прайс-лист и производственный план.'), how: N_('Укажите единицу, например упаковка или килограмм; все три показателя должны использовать эту единицу.')},
  'products.price': {what: N_('Цена реализации одной единицы без НДС в валюте проекта.'), source: N_('Подтверждённый действующий прайс-лист. В UZGERMED между основным файлом и дополнительными ценами есть расхождения.'), how: N_('Введите выбранную цену за указанную единицу и подтвердите её валюту и учёт НДС.'), zero: N_('Явный 0 означает реализацию без выручки; указывайте его только при таком плане.')},
  'products.unit_cost': {what: N_('Переменные затраты на одну единицу продукции.'), source: N_('Калькуляция материалов и других переменных затрат. Основная книга UZGERMED выделяет материальную себестоимость.'), how: N_('Уточните полный состав переменных затрат на единицу; постоянные расходы отражаются отдельно.'), zero: N_('Укажите 0 только при подтверждённом отсутствии переменных затрат на единицу.')},
  'products.quantities': {what: N_('Количество единиц реализации в каждом месяце прогноза.'), source: N_('План продаж и производства. В UZGERMED годовые объёмы преобразованы в месячные по правилу исходной модели.'), how: N_('Укажите одно количество для каждого месяца или заполните все месяцы графика; подтвердите правило распределения годового объёма.'), zero: N_('Укажите 0 для месяца, в котором реализации действительно не будет.')},
  'products.capacity': {what: N_('Производственная мощность в тех же единицах, что и объём реализации.'), source: N_('Технические данные производства.'), how: N_('Если мощность включена, заполните значение или каждый месяц графика. Если ограничение мощности не используется, снимите отметку «Указать производственную мощность».'), zero: N_('Явный 0 означает отсутствие мощности в соответствующем месяце.')},
  assets: {label: N_('Основные средства'), what: N_('Активы, по которым модель рассчитывает амортизацию и покупки в периоде.'),
    source: N_('Ведомость основных средств и план покупок. {assetsFile} UZGERMED описывает существующие активы на конец II квартала 2026 года.'),
    how: N_('Если активы есть, нажмите «Добавить актив» и укажите его стоимость, срок и месяц ввода. Для существующего актива нужны остаточная стоимость и оставшийся срок амортизации.'),
    zero: N_('Отметьте «Основных средств в этой модели нет» только при их действительном отсутствии в выбранной модели.')},
  'assets.name': {what: N_('Название отдельного актива или обоснованной группы активов.'), source: N_('Ведомость основных средств или смета закупки.'), how: N_('Укажите понятное уникальное название.')},
  'assets.value': {what: N_('Стоимость актива в валюте проекта.'), source: N_('Остаточная стоимость существующего актива или подтверждённая стоимость покупки нового.'), how: N_('Введите стоимость, соответствующую выбранному месяцу ввода и составу актива.'), zero: N_('Явный 0 допустим только для подтверждённой нулевой стоимости в этом расчёте.')},
  'assets.life_months': {what: N_('Срок амортизации актива в месяцах.'), source: N_('Данные об учёте актива и согласованные параметры амортизации.'), how: N_('Для существующего актива укажите оставшийся срок, для нового — его срок амортизации. Годовая ставка сама по себе не подтверждает оставшийся срок.')},
  'assets.commissioning_month': {what: N_('Месяц, с которого актив участвует в расчёте.'), source: N_('Дата начала прогноза и план ввода актива.'), how: N_('0 — существующий актив на начало прогноза; 1 — первый месяц. Для новой покупки укажите соответствующий месяц прогноза.')},
  loans: {label: N_('Кредиты'), what: N_('Действующие и планируемые кредиты с денежными графиками.'), source: N_('Кредитные договоры, графики платежей и подтверждённые остатки. В UZGERMED есть разные валюты и версии графиков.'), how: N_('Если кредиты есть, нажмите «Добавить кредит» и заполните долг на начало, выдачи, погашения и суммы процентов. Сверьте каждый график с договором и выбранной датой.'), zero: N_('Отметьте «Кредитов в этой модели нет» только если в выбранной модели действительно нет кредитов.')},
  'loans.name': {what: N_('Название, позволяющее отличить кредит от других.'), source: N_('Кредитный договор.'), how: N_('Укажите уникальное название или идентификатор договора.')},
  'loans.opening_balance': {what: N_('Непогашенный основной долг на начало прогноза.'), source: N_('Выписка кредитора или сверенный график на выбранную дату.'), how: N_('Введите остаток основного долга в валюте проекта и проверьте, что график относится к действующему договору.'), zero: N_('Укажите 0 для нового кредита, который будет получен в периоде прогноза, если до его начала долга действительно нет.')},
  'loans.drawdowns': {what: N_('Суммы получения кредита в каждом месяце.'), source: N_('План выдач и траншей кредитора.'), how: N_('Задайте месячный график получения денег. Одно значение в форме повторяется каждый месяц.'), zero: N_('Укажите 0 во всех месяцах, когда выдачи кредита не будет.')},
  'loans.principal': {what: N_('Суммы погашения основного долга в каждом месяце.'), source: N_('График погашения кредитора.'), how: N_('Введите суммы погашения по месяцам; проценты указываются отдельным графиком.'), zero: N_('Укажите 0 в месяце без погашения основного долга.')},
  'loans.interest': {what: N_('Денежные суммы процентов в каждом месяце.'), source: N_('Проверенный график начислений кредитора.'), how: N_('Введите суммы процентов в валюте проекта. Процентная ставка вместо суммы не заполнит этот график.'), zero: N_('Укажите 0 только для месяца без процентов.')},
};

// Hints shown while a field is still empty: an example for the placeholder and
// a short "what to write". Examples are illustrations only and are never saved.
const EXAMPLES = {
  title: N_('Производство таблеток в Ташкенте'), start: '', months: '36', tax_rate: '15', discount_rate: '18',
  fixed_costs: '45000', equity: '0', initial_investment: '250000',
  opening_cash: '120000', opening_receivables: '0', opening_inventory: '35000', opening_payables: '0',
  receivable_days: '30', inventory_days: '45', payable_days: '30',
  'products.name': N_('Парацетамол 500 мг, 20 таблеток'), 'products.unit': N_('упаковка'), 'products.price': '1.25', 'products.unit_cost': '0.70',
  'products.quantities': '40000', 'products.capacity': '60000',
  'assets.name': N_('Таблеточный пресс'), 'assets.value': '180000', 'assets.life_months': '120', 'assets.commissioning_month': '1',
  'loans.name': N_('Кредит банка, договор № 12/26'), 'loans.opening_balance': '0', 'loans.drawdowns': '0', 'loans.principal': '5000', 'loans.interest': '1200',
};
const NARRATIVE_HINTS = {
  project_description: [N_('Что производит или продаёт проект, для кого и зачем; срок и масштаб.'), N_('Выпуск таблетированных препаратов на арендованной площадке; 3 позиции, 1,2 млн упаковок в год')],
  initiator: [N_('Кто реализует проект: компания, опыт, собственники, контакты ответственного.'), N_('ООО «…», 8 лет на фармацевтическом рынке, собственный склад и дистрибуция')],
  strategy: [N_('Цели на 3–5 лет: рост продаж, новые продукты и рынки, этапы развития.'), N_('Первый год — запуск 3 позиций, второй — экспорт в Казахстан')],
  market: [N_('Кто покупатели, объём рынка, конкуренты, цены и каналы сбыта.'), N_('Аптечные сети Ташкента и областей; основные конкуренты — импортные аналоги')],
  resources: [N_('Какое сырьё и материалы нужны, где их закупать, сроки поставки.'), N_('Субстанция из Индии, упаковка — местный поставщик, поставка 45 дней')],
  location: [N_('Где будет работать проект: адрес, площадь, своя или аренда, коммуникации.'), N_('Ташкент, Сергелийский район, 1 200 м² в аренде, электричество и вода подведены')],
  technology: [N_('Как устроено производство: этапы, оборудование, мощность, контроль качества.'), N_('Смешивание → таблетирование → блистеры; линия до 5 000 упаковок в смену')],
  organization: [N_('Как организована работа: структура управления, смены, подрядчики.'), N_('Директор, производство, ОТК, склад; работа в 2 смены')],
  personnel: [N_('Сколько сотрудников, какие должности, зарплаты и обучение.'), N_('25 человек: 15 на производстве, 4 в ОТК, 6 в управлении')],
  investment_purpose: [N_('На что пойдут деньги: оборудование, ремонт, оборотный капитал — с суммами.'), N_('Пресс 180 000, ремонт цеха 40 000, оборотные средства 30 000')],
  insurance: [N_('Что и у кого страхуется: имущество, ответственность, груз.'), N_('Оборудование и склад — страховая компания «…», ежегодно')],
  risks: [N_('Основные риски проекта и как их снижать.'), N_('Рост цен на сырьё — договоры на год; задержка поставки — запас на 45 дней')],
};
function hintKey(field) {return String(field || '').replace(/\[\d+\]/g, '').replace(/\.\d+$/, '');}

const example = text => tx('Например: {example}', {example: tx(text)});

// {placeholder, text} for an empty field in the current language: what to write and an example value.
export function emptyFieldHint(field = '') {
  const key = hintKey(field);
  if (Object.hasOwn(NARRATIVE_HINTS, key)) {
    const [text, sample] = NARRATIVE_HINTS[key];
    return {placeholder: example(sample), text: tx(text)};
  }
  const guide = fieldGuidance(key), sample = Object.hasOwn(EXAMPLES, key) ? EXAMPLES[key] : '';
  const zeroHint = guide.zero ? ' ' + guide.zero : '';
  return {placeholder: sample ? example(sample) : '', text: (guide.what || '') + zeroHint};
}

export const isBlankValue = value => value == null || typeof value === 'string' && !value.trim() || Array.isArray(value) && !value.length;

const DEFAULT_GUIDANCE = {what: N_('Обязательное значение для выбранного расчёта.'), source: N_('Подтверждённый документ или согласованный параметр проекта.'),
  how: N_('Заполните значение по исходным данным; пустое поле не означает ноль.')};
// {label, what, source, how, zero?} for a field, translated into the current language.
export function fieldGuidance(field = '') {
  const key = String(field).replace(/\[\d+\]/g, '');
  const guide = Object.fromEntries(Object.entries(Object.hasOwn(GUIDANCE, key) ? GUIDANCE[key] : DEFAULT_GUIDANCE)
    .map(([name, text]) => [name, tx(text, SOURCE_NAMES)]));
  return {...guide, label: /\[\d+\]/.test(field) ? fieldLabel(field) : guide.label || fieldLabel(field)};
}

export function missingRequiredFields(form = formFromInputs()) {
  const inputs = inputsFromForm(form), fields = new Set();
  const blank = value => value == null || typeof value === 'string' && !value.trim();
  const require = (field, value) => {if (blank(value)) fields.add(field);};
  const series = (field, value) => {
    if (!Array.isArray(value)) {require(field, value); return;}
    if (!value.length || Number.isInteger(inputs.months) && inputs.months > 0 && value.length !== inputs.months) fields.add(field);
    for (let index = 0; index < value.length; index++) require(`${field}[${index}]`, value[index]);
  };
  for (const field of ['title', 'currency', 'start', 'tax_rate', 'discount_rate', ...BUSINESS_SCALARS.map(([key]) => key)]) require(field, inputs[field]);
  require('months', form.months);
  for (const field of ['fixed_costs', 'equity']) series(field, inputs[field]);
  for (const collection of ['products', 'assets', 'loans']) {
    const entries = inputs[collection];
    if (!Array.isArray(entries) || collection === 'products' && !entries.length) {fields.add(collection); continue;}
    entries.forEach((entry, index) => {
      const prefix = `${collection}[${index}]`;
      require(prefix + '.name', entry.name);
      if (collection === 'products') {
        for (const field of ['unit', 'price', 'unit_cost']) require(`${prefix}.${field}`, entry[field]);
        series(prefix + '.quantities', entry.quantities);
        if (entry.capacity != null) series(prefix + '.capacity', entry.capacity);
      } else if (collection === 'assets') {
        require(prefix + '.value', entry.value);
        for (const field of ['life_months', 'commissioning_month']) require(`${prefix}.${field}`, form.assets[index][field]);
      } else {
        require(prefix + '.opening_balance', entry.opening_balance);
        for (const field of ['drawdowns', 'principal', 'interest']) series(`${prefix}.${field}`, entry[field]);
      }
    });
  }
  return [...fields].map(field => ({field}));
}

export function monthLabels(start, months) {
  const count = Math.min(60, Math.max(0, Number(months) || 0));
  const match = /^(\d{4})-(\d{2})-01$/.exec(start || '');
  // Month names follow the interface language chosen at the time of the call.
  return Array.from({length: count}, (_, index) => {
    if (!match) return tx('Месяц {n}', {n: index + 1});
    return formatDate(new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1 + index, 1)), {timeZone: 'UTC', month: 'short', year: 'numeric'});
  });
}
export function resizeValues(values, months) {
  const count = Math.min(60, Math.max(1, Number(months) || 1));
  return Array.from({length: count}, (_, index) => Array.isArray(values) ? values[index] ?? '' : values ?? '');
}
// Messages and labels below are returned in the current interface language.
export function headerProblem(header) {
  if (!String(header.title || '').trim() || String(header.title).trim().length > 160) return tx('Укажите название проекта: до 160 символов.');
  const match = /^(\d{4})-(\d{2})-01$/.exec(header.start || '');
  const months = Number(header.months);
  if (!match || Number(match[1]) < 2000 || Number(match[1]) > 2100 || Number(match[2]) < 1 || Number(match[2]) > 12) return tx('Выберите первый месяц прогноза.');
  if (!Number.isInteger(months) || months < 1 || months > 60) return tx('Прогноз: от 1 до 60 месяцев.');
  if (Number(match[1]) * 12 + Number(match[2]) - 1 + months - 1 > 2100 * 12 + 11) return tx('Весь прогноз должен заканчиваться не позже декабря 2100 года.');
  if (header.currency && !['USD', 'UZS', 'EUR'].includes(header.currency)) return tx('Выберите USD, UZS или EUR.');
  return '';
}

export function missingHeaderFields(header = {}, mode = 'manual', native = false) {
  const blank = value => value == null || !String(value).trim();
  return [['title', N_('Название проекта')], ...(!native ? [['start', N_('Начало прогноза')], ['months', N_('Прогноз, месяцев')],
    ...(mode === 'manual' ? [['currency', N_('Валюта модели')]] : [])] : [])]
    .filter(([field]) => blank(header[field])).map(([field, label]) => ({field, label: tx(label)}));
}

export function createBusinessState(project = null) {
  return {project, requestKey: createArchiveState().requestKey, createFields: null, createUnknown: false, uploaded: new Set(), sourceUploadStarted: false};
}
// The server message (or the fallback) is translated here, so the whole text is in one language.
export function uploadFailureMessage(error, state) {
  const message = tx(error?.message || N_('Загрузка не завершена.'));
  if (state?.uploaded?.size) return tx('{message} Уже загруженные файлы сохранены. Повторная попытка продолжит этот проект.', {message});
  if (state?.sourceUploadStarted) return tx('{message} Повторите загрузку: проверим файл и продолжим этот проект.', {message});
  return message;
}
export function assertProject(project, companyId, id = null) {
  if (!project || project.company_id !== companyId || (id != null && project.id !== id)) throw Error(tx('Сервер вернул проект другой компании. Обновите список.'));
  return project;
}
const changed = () => new DOMException(tx('Раздел или компания изменены'), 'AbortError');

export async function createProjectDraft(api, state, companyId, title, mode = 'manual', isCurrent = () => true) {
  const check = () => {if (!isCurrent()) throw changed();};
  check();
  if (state.project) return assertProject(state.project, companyId);
  if (!String(title || '').trim() || String(title).trim().length > 160) throw Error(tx('Укажите название проекта: до 160 символов.'));
  state.createFields ||= {company_id: companyId, title: String(title).trim(), request_key: state.requestKey, mode};
  if (state.createFields.company_id !== companyId || state.createFields.mode !== mode) throw Error(tx('Форма относится к другому проекту или компании.'));
  let result;
  try {result = await api(projectUrl(companyId), json(state.createFields));}
  catch (e) {if (e.status >= 400 && e.status < 500 && !state.createUnknown) state.createFields = null; else state.createUnknown = true; throw e;}
  check(); state.project = assertProject(result, companyId); state.createUnknown = false;
  return state.project;
}

export async function saveProjectDraft(api, project, companyId, inputs, isCurrent = () => true) {
  assertProject(project, companyId);
  if (!project.can_edit) throw Error(tx('Изменение этого проекта недоступно.'));
  if (!isCurrent()) throw changed();
  const result = await api(projectUrl(companyId, project.id, 'inputs'), {...json({revision: project.revision, inputs}), method: 'PUT'});
  if (!isCurrent()) throw changed();
  return assertProject(result, companyId, project.id);
}

// Progress labels (onStep, onProgress) are returned in the current interface language.
export async function uploadProjectFolder(api, state, {companyId, header, sources, mode = state.project?.mode || 'files', isCurrent = () => true, onProject, onStep, onProgress} = {}) {
  const check = () => {if (!isCurrent()) throw changed();};
  const remember = project => {check(); assertProject(project, companyId, state.project?.id); state.project = project; onProject?.(project);};
  check();
  const problem = mode === 'manual' ? (!String(header.title || '').trim() ? tx('Укажите название проекта.') : '') : headerProblem(header);
  if (problem) throw Error(problem);
  const picked = prepareSources(sources.map(item => item.file));
  if (picked.problems.length) throw Error(picked.problems.join(' '));
  const total = Number(!state.project) + sources.filter(item => !state.uploaded.has(item.key)).length + 1;
  let completed = 0;
  const phase = (label, advance = false) => {check(); if (advance) completed++; onStep?.(label); onProgress?.({completed, total, label});};
  if (!state.project) {
    state.createFields ||= {company_id: companyId, title: header.title.trim(), request_key: state.requestKey, mode};
    if (state.createFields.company_id !== companyId) throw Error(tx('Форма относится к другой компании.'));
    phase(tx('Создаём папку проекта…'));
    let project;
    try {project = await api(projectUrl(companyId), json(state.createFields));}
    catch (e) {if (e.status >= 400 && e.status < 500 && !state.createUnknown) state.createFields = null; else state.createUnknown = true; throw e;}
    remember(project); state.createUnknown = false; phase(tx('Папка проекта создана'), true);
  }
  assertProject(state.project, companyId);
  if (state.project.can_edit === false) throw Error(tx('Изменение этой папки недоступно.'));
  for (const [index, item] of sources.entries()) {
    check(); if (state.uploaded.has(item.key)) continue;
    phase(tx('Загружаем {n} из {total}: {path}', {n: index + 1, total: sources.length, path: item.relativePath}));
    state.sourceUploadStarted = true;
    const project = await api(projectUrl(companyId, state.project.id, 'sources') + '&expected_revision=' + state.project.revision, {
      method: 'PUT', body: item.file, headers: {'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(item.name), 'X-Relative-Path': encodeURIComponent(item.relativePath)},
    });
    remember(project); state.uploaded.add(item.key); phase(tx('Файл загружен: {path}', {path: item.relativePath}), true);
  }
  phase(tx('Проверяем источники и параметры отчёта…'));
  const overrides = mode === 'manual' ? {} : {title: header.title.trim(), start: header.start, months: Number(header.months)};
  if (mode !== 'manual' && header.currency) overrides.currency = header.currency;
  const project = await api(projectUrl(companyId, state.project.id, 'analyse'), json({revision: state.project.revision, overrides}));
  remember(project); phase(tx('Источники проверены'), true); return project;
}

export async function generateProject(api, project, companyId, inputs, confirmSources, isCurrent = () => true) {
  assertProject(project, companyId);
  if (!project.can_edit) throw Error(tx('Изменение этого проекта недоступно.'));
  if (!isCurrent()) throw changed();
  const result = await api(projectUrl(companyId, project.id, 'generate'), json({revision: project.revision, inputs, confirm_sources: !!confirmSources}));
  if (!isCurrent()) throw changed();
  return assertProject(result, companyId, project.id);
}

// The server text of an issue as received; duplicates are found by this text in any language.
function issueText(issue) {
  if (typeof issue === 'string') return issue;
  return issue?.message || issue?.detail || N_('Проверьте исходный файл и параметры проекта.');
}
export const issueMessage = issue => tx(issueText(issue));
export function sourceLocation(source) {
  if (typeof source === 'string') return source;
  if (!source) return '';
  return [source.filename, source.sheet ? tx('лист «{sheet}»', {sheet: source.sheet}) : '', source.cell, source.page ? tx('стр. {page}', {page: source.page}) : ''].filter(Boolean).join(' · ');
}
export function sourceIssues(extraction) {
  return Array.isArray(extraction?.issues) ? extraction.issues : [];
}
export function deduplicateIssues(issues = []) {
  const byKey = new Map();
  for (const entry of issues) {
    const issue = typeof entry === 'string' ? {message: entry} : entry;
    if (!issue || typeof issue !== 'object') continue;
    const key = `${issue.field || ''}\u0000${issueText(issue)}`;
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
  const checkKeys = new Set(checks.map(issue => `${issue.field || ''}\u0000${issueText(issue)}`));
  const references = source.filter(issue => !issue.requires_confirmation && !checkKeys.has(`${issue.field || ''}\u0000${issueText(issue)}`));
  return {fields, checks, references};
}
export function evidenceRows(extraction) {
  const evidence = extraction?.evidence;
  if (Array.isArray(evidence)) return evidence;
  if (!evidence || typeof evidence !== 'object') return [];
  return Object.entries(evidence).flatMap(([field, entries]) => (Array.isArray(entries) ? entries : [entries]).map(entry => ({field, ...(typeof entry === 'object' && entry ? entry : {source: String(entry)})})));
}
