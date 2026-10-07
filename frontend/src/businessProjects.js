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

const GUIDANCE = {
  title: {label: 'Название проекта', what: 'Название, которое будет напечатано в бизнес-плане и ТЭО.',
    source: 'Название папки или паспорт проекта.', how: 'Укажите понятное название проекта; предложенное название папки можно изменить.'},
  start: {label: 'Начало прогноза', what: 'Первый месяц, за который нужно рассчитать деятельность проекта.',
    source: 'План запуска проекта и дата подтверждённых начальных остатков.',
    how: 'Выберите месяц и год. Начальные остатки должны относиться к началу этого месяца; между датой отчётности и началом прогноза потребуется сверка движений.'},
  months: {label: 'Количество месяцев', what: 'Продолжительность финансового прогноза.',
    source: 'Задание на бизнес-план или ТЭО.', how: 'Укажите число месяцев. Каждый помесячный график должен покрывать этот период.'},
  currency: {label: 'Валюта и единицы сумм', what: 'Единая валюта и масштаб всех денежных параметров модели.',
    source: 'Заголовки исходных таблиц и утверждённые условия пересчёта.',
    how: 'Выберите валюту и приведите суммы к её денежным единицам. Форма-1 и Форма-2 из папки UZGERMED содержат тысячи сумов. Дату и курс пересчёта нужно подтвердить отдельно.'},
  tax_rate: {label: 'Налог на прибыль', what: 'Подтверждённая ставка налога на прибыль для этого проекта.',
    source: 'Налоговые условия организации и согласованные параметры проекта.',
    how: 'Введите ставку в процентах. Ставку из старой финансовой модели нужно проверить для выбранного проекта и периода.',
    zero: 'Явный 0 допустим только при подтверждённой нулевой ставке в расчёте.'},
  discount_rate: {label: 'Годовая ставка дисконтирования', what: 'Согласованная ставка для оценки будущих денежных потоков.',
    source: 'Утверждённые предпосылки расчёта проекта.',
    how: 'Введите годовую ставку в процентах. В исходной книге UZGERMED она связана со ставкой кредита; эту связь нужно подтвердить.',
    zero: 'Явный 0 означает расчёт без дисконтирования и требует осознанного выбора.'},
  opening_cash: {label: 'Деньги на начало', what: 'Доступные деньги проекта на начало первого месяца прогноза.',
    source: 'Остатки банковских счетов и кассы. Для UZGERMED: Форма-1, list02!E46, строка 320, конец II квартала 2026 года, тыс. сумов.',
    how: 'Сверьте остаток с выбранной датой начала и валютой проекта. Укажите общую подтверждённую сумму; будущие поступления учитываются в своих месяцах.',
    zero: 'Укажите 0, если на начало прогноза денег действительно нет.'},
  opening_receivables: {label: 'Дебиторская задолженность на начало', what: 'Сумма, которую покупатели уже должны проекту на начало прогноза.',
    source: 'Расшифровка расчётов с покупателями. Для UZGERMED: Форма-1, list02!E36, строка 220, конец II квартала 2026 года, тыс. сумов.',
    how: 'Выделите задолженность покупателей и подтвердите её остаток на выбранную дату в валюте проекта. Общая дебиторская задолженность отчётности может включать другие расчёты.',
    zero: 'Укажите 0, если задолженности покупателей на начало действительно нет.'},
  opening_inventory: {label: 'Запасы на начало', what: 'Стоимость сырья, незавершённого производства и готовой продукции на начало прогноза.',
    source: 'Инвентаризация и расшифровка запасов. Для UZGERMED: Форма-1, list02!E27, строка 140, компоненты E28:E30; тыс. сумов.',
    how: 'Укажите стоимость запасов в валюте проекта. Сверьте дату остатка; количество упаковок само по себе не является стоимостью запасов.',
    zero: 'Укажите 0, если запасов на начало действительно нет.'},
  opening_payables: {label: 'Кредиторская задолженность на начало', what: 'Долг поставщикам за уже полученные товары и услуги на начало прогноза.',
    source: 'Расшифровка расчётов с поставщиками. Для UZGERMED: Форма-1, list02!E81, строка 610, оставлена пустой; нужен подтверждённый остаток.',
    how: 'Уточните именно задолженность поставщикам на выбранную дату. Сверьте её состав и валюту; общий итог обязательств включает также кредиты и другие долги.',
    zero: 'Укажите 0 только после подтверждения отсутствия долга поставщикам. Пустая строка отчёта такого подтверждения не даёт.'},
  initial_investment: {label: 'Инвестиции до начала прогноза', what: 'Отдельная сумма инвестиций до первого месяца расчёта для оценки денежных потоков проекта.',
    source: 'Смета и подтверждённый план вложений. Лист «Стоим_проекта» UZGERMED содержит также существующие активы и рабочий капитал.',
    how: 'Укажите согласованную сумму до начала прогноза. Покупки активов в следующих месяцах задайте в разделе основных средств; один расход учитывается один раз.',
    zero: 'Укажите 0, если в выбранном расчёте инвестиций до начала прогноза нет.'},
  fixed_costs: {label: 'Постоянные расходы', what: 'Регулярные операционные расходы, которые не включены в затраты на единицу продукции.',
    source: 'Смета постоянных затрат, штатное расписание и коммунальные условия. В UZGERMED: листы «Труд» и «Цех_стоим».',
    how: 'Сложите подтверждённые постоянные расходы и укажите месячную сумму или график. Амортизация, проценты и затраты на единицу продукции рассчитываются отдельно.',
    zero: 'Укажите 0 только для месяца, в котором постоянных расходов действительно нет.'},
  equity: {label: 'Взносы в капитал', what: 'Деньги, которые собственники внесут в проект в течение прогноза.',
    source: 'План финансирования и подтверждённые решения собственников.',
    how: 'Задайте сумму или график будущих взносов по месяцам. Уже имеющийся уставный капитал сам по себе не означает новое денежное поступление.',
    zero: 'Если будущих денежных взносов нет, укажите 0 явно.'},
  receivable_days: {label: 'Срок оплаты покупателей', what: 'Согласованное число дней, за которое покупатели оплачивают реализацию.',
    source: 'Условия продаж и договоры с покупателями.', how: 'Введите подтверждённый срок в днях. При разных условиях продаж согласуйте общий срок для этой модели.',
    zero: 'Укажите 0, если в модели покупатели оплачивают реализацию сразу.'},
  inventory_days: {label: 'Запасы, дней', what: 'Согласованный срок хранения запасов для расчёта оборотного капитала.',
    source: 'Производственный и закупочный план. В UZGERMED сырьё и готовая продукция имеют отдельные сроки на листе «Раб_капит».',
    how: 'Подтвердите единый срок запасов в днях для этой модели.', zero: 'Укажите 0 только при согласованном расчёте без остатка запасов.'},
  payable_days: {label: 'Срок оплаты поставщикам', what: 'Согласованный срок оплаты закупок и возникновения задолженности поставщикам.',
    source: 'Договоры поставок и план расчётов.', how: 'Введите подтверждённый срок в днях. Условия разных поставщиков нужно согласовать для общего расчёта.',
    zero: 'Укажите 0, если в модели задолженность поставщикам не возникает.'},
  products: {label: 'Продукция', what: 'Продукты или услуги, из которых складываются выручка и переменные затраты.',
    source: 'Производственный план, прайс-лист и калькуляции.', how: 'Добавьте хотя бы одну позицию и заполните название, единицу, цену, затраты на единицу и объём реализации.'},
  'products.name': {what: 'Однозначное название продукции или услуги.', source: 'Ассортимент и производственный план.', how: 'Укажите название, дозировку и упаковку, если они различают позиции.'},
  'products.unit': {what: 'Единица, к которой относятся цена, затраты и объём.', source: 'Прайс-лист и производственный план.', how: 'Укажите единицу, например упаковка или килограмм; все три показателя должны использовать эту единицу.'},
  'products.price': {what: 'Цена реализации одной единицы без НДС в валюте проекта.', source: 'Подтверждённый действующий прайс-лист. В UZGERMED между основным файлом и дополнительными ценами есть расхождения.', how: 'Введите выбранную цену за указанную единицу и подтвердите её валюту и учёт НДС.', zero: 'Явный 0 означает реализацию без выручки; указывайте его только при таком плане.'},
  'products.unit_cost': {what: 'Переменные затраты на одну единицу продукции.', source: 'Калькуляция материалов и других переменных затрат. Основная книга UZGERMED выделяет материальную себестоимость.', how: 'Уточните полный состав переменных затрат на единицу; постоянные расходы отражаются отдельно.', zero: 'Укажите 0 только при подтверждённом отсутствии переменных затрат на единицу.'},
  'products.quantities': {what: 'Количество единиц реализации в каждом месяце прогноза.', source: 'План продаж и производства. В UZGERMED годовые объёмы преобразованы в месячные по правилу исходной модели.', how: 'Укажите одно количество для каждого месяца или заполните все месяцы графика; подтвердите правило распределения годового объёма.', zero: 'Укажите 0 для месяца, в котором реализации действительно не будет.'},
  'products.capacity': {what: 'Производственная мощность в тех же единицах, что и объём реализации.', source: 'Технические данные производства.', how: 'Если мощность включена, заполните значение или каждый месяц графика. Если ограничение мощности не используется, снимите отметку «Указать производственную мощность».', zero: 'Явный 0 означает отсутствие мощности в соответствующем месяце.'},
  assets: {label: 'Основные средства', what: 'Активы, по которым модель рассчитывает амортизацию и покупки в периоде.',
    source: 'Ведомость основных средств и план покупок. ОС.расш.PDF UZGERMED описывает существующие активы на конец II квартала 2026 года.',
    how: 'Если активы есть, нажмите «Добавить актив» и укажите его стоимость, срок и месяц ввода. Для существующего актива нужны остаточная стоимость и оставшийся срок амортизации.',
    zero: 'Отметьте «Основных средств в этой модели нет» только при их действительном отсутствии в выбранной модели.'},
  'assets.name': {what: 'Название отдельного актива или обоснованной группы активов.', source: 'Ведомость основных средств или смета закупки.', how: 'Укажите понятное уникальное название.'},
  'assets.value': {what: 'Стоимость актива в валюте проекта.', source: 'Остаточная стоимость существующего актива или подтверждённая стоимость покупки нового.', how: 'Введите стоимость, соответствующую выбранному месяцу ввода и составу актива.', zero: 'Явный 0 допустим только для подтверждённой нулевой стоимости в этом расчёте.'},
  'assets.life_months': {what: 'Срок амортизации актива в месяцах.', source: 'Данные об учёте актива и согласованные параметры амортизации.', how: 'Для существующего актива укажите оставшийся срок, для нового — его срок амортизации. Годовая ставка сама по себе не подтверждает оставшийся срок.'},
  'assets.commissioning_month': {what: 'Месяц, с которого актив участвует в расчёте.', source: 'Дата начала прогноза и план ввода актива.', how: '0 — существующий актив на начало прогноза; 1 — первый месяц. Для новой покупки укажите соответствующий месяц прогноза.'},
  loans: {label: 'Кредиты', what: 'Действующие и планируемые кредиты с денежными графиками.', source: 'Кредитные договоры, графики платежей и подтверждённые остатки. В UZGERMED есть разные валюты и версии графиков.', how: 'Если кредиты есть, нажмите «Добавить кредит» и заполните долг на начало, выдачи, погашения и суммы процентов. Сверьте каждый график с договором и выбранной датой.', zero: 'Отметьте «Кредитов в этой модели нет» только если в выбранной модели действительно нет кредитов.'},
  'loans.name': {what: 'Название, позволяющее отличить кредит от других.', source: 'Кредитный договор.', how: 'Укажите уникальное название или идентификатор договора.'},
  'loans.opening_balance': {what: 'Непогашенный основной долг на начало прогноза.', source: 'Выписка кредитора или сверенный график на выбранную дату.', how: 'Введите остаток основного долга в валюте проекта и проверьте, что график относится к действующему договору.', zero: 'Укажите 0 для нового кредита, который будет получен в периоде прогноза, если до его начала долга действительно нет.'},
  'loans.drawdowns': {what: 'Суммы получения кредита в каждом месяце.', source: 'План выдач и траншей кредитора.', how: 'Задайте месячный график получения денег. Одно значение в форме повторяется каждый месяц.', zero: 'Укажите 0 во всех месяцах, когда выдачи кредита не будет.'},
  'loans.principal': {what: 'Суммы погашения основного долга в каждом месяце.', source: 'График погашения кредитора.', how: 'Введите суммы погашения по месяцам; проценты указываются отдельным графиком.', zero: 'Укажите 0 в месяце без погашения основного долга.'},
  'loans.interest': {what: 'Денежные суммы процентов в каждом месяце.', source: 'Проверенный график начислений кредитора.', how: 'Введите суммы процентов в валюте проекта. Процентная ставка вместо суммы не заполнит этот график.', zero: 'Укажите 0 только для месяца без процентов.'},
};

export function fieldGuidance(field = '') {
  const key = String(field).replace(/\[\d+\]/g, '');
  const guide = GUIDANCE[key] || {what: 'Обязательное значение для выбранного расчёта.', source: 'Подтверждённый документ или согласованный параметр проекта.', how: 'Заполните значение по исходным данным; пустое поле не означает ноль.'};
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
