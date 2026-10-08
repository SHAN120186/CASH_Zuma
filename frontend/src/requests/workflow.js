// Маршрут заявки на оплату. Сервер решает, какие действия доступны (r.actions), и проверяет
// каждое из них; здесь только подписи, этапы и подсказки для формы.
// Подписи хранятся по-русски (N_) и переводятся при показе (tx).
import {tx, N_} from '../i18n/index.js';

export const STATUS = {
  draft: N_('Черновик'), pending: N_('На согласовании'), approved: N_('Утверждена'), paid: N_('Оплачена'),
  returned: N_('На доработке'), cancelled: N_('Закрыта без оплаты'), rejected: N_('Отклонена (архив)'),
};

export const STAGES = { check: N_('Проверка бухгалтера'), finance: N_('Финансовый директор'), director: N_('Директор') };
// «Ожидает: <этап>» — целой фразой, чтобы перевод не зависел от падежа и регистра.
const WAITING = {
  check: N_('Ожидает: проверка бухгалтера'), finance: N_('Ожидает: финансовый директор'), director: N_('Ожидает: директор'),
};

export const PRIORITIES = { normal: N_('Обычный'), high: N_('Высокий'), urgent: N_('Срочный') };
export const LEAD_DAYS = { normal: 7, high: 3, urgent: 1 };
export const CHANNELS = { bank: N_('Банк (расчётный счёт)'), cash: N_('Касса') };

// Разделы вложений заявки — как в таблице request_documents на сервере.
export const DOC_KINDS = {
  internal: N_('Внутренняя заявка / Индент'),
  contract: N_('Договор / Счёт на оплату'),
  other: N_('Прочие подтверждающие документы'),
};
export const REQUIRED_DOCS = ['internal', 'contract'];
// Внутренняя заявка и договор — по одному действующему файлу (новый файл — новая версия); прочих может быть несколько.
export const SINGLE_DOCS = ['internal', 'contract'];
export const DOC_EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg', '.docx', '.xlsx'];
export const DOC_ACCEPT = DOC_EXTENSIONS.join(',');
export const MAX_DOC_BYTES = 5 * 1024 * 1024;

// Проверка файла до загрузки; сервер повторяет её по содержимому (подпись файла, структура DOCX/XLSX).
export function fileProblem(file) {
  const name = String(file?.name || '');
  const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')).toLowerCase() : '';
  if (!DOC_EXTENSIONS.includes(ext)) return tx('«{name}»: допустимы PDF, PNG, JPEG, DOCX или XLSX.', {name});
  if (!(file.size > 0)) return tx('«{name}»: пустой файл.', {name});
  if (file.size > MAX_DOC_BYTES) return tx('«{name}»: файл больше 5 МБ.', {name});
  return '';
}

// Два отрицательных действия — возврат и закрытие; оба с обязательным комментарием.
export const ACTION_LABELS = {
  check: N_('Проверено: реквизиты и комплектность'),
  approve: N_('Согласовать'),
  return: N_('Вернуть на доработку'),
  return_finance: N_('Вернуть финансовому директору'),
  close: N_('Закрыть без оплаты'),
  reschedule: N_('Перенести дату'),
};
export const DECISIONS = Object.keys(ACTION_LABELS);
export const COMMENT_REQUIRED = ['return', 'return_finance', 'close', 'reschedule'];

export function stageLabel(r) {
  if (r.status !== 'pending') return tx(STATUS[r.status] || r.status);
  return tx(WAITING[r.approval_stage] || N_('Ожидает: согласования'));
}

export function approveLabel(r) {
  if (r.approval_stage === 'finance') return r.route && !r.route.director_required ? tx('Утвердить (директор не участвует)') : tx('Подтвердить проверку бюджета');
  return tx('Утвердить оплату');
}

export function decisionOptions(r) {
  return (r.actions || []).filter(a => DECISIONS.includes(a)).map(a => [a, a === 'approve' ? approveLabel(r) : tx(ACTION_LABELS[a])]);
}

export const canDecide = r => (r.actions || []).some(a => a === 'approve' || a === 'check');

// Этапы для трека: Заявитель → Проверка → Фин. директор → Директор (по политике статьи) → Оплата.
export function steps(r) {
  const order = ['check', 'finance', 'director'];
  const director = !r.route || r.route.director_required !== false;
  // Заявка, отправленная до 2.14.0 (без снимка политики), идёт прежним маршрутом без проверки бухгалтера.
  const legacy = ['pending', 'approved', 'paid'].includes(r.status) && !!r.route && !r.route.policy;
  let author = 'done', check = '', finance = '', dir = director ? '' : 'skip', paid = '';
  if (r.status === 'draft' || r.status === 'returned') author = 'now';
  else if (r.status === 'pending') {
    const at = order.indexOf(r.approval_stage);
    check = legacy ? 'skip' : at > 0 ? 'done' : 'now';
    finance = at > 1 ? 'done' : at === 1 ? 'now' : '';
    if (director) dir = at === 2 ? 'now' : '';
  } else if (r.status === 'approved' || r.status === 'paid') {
    check = legacy ? 'skip' : r.checked_by ? 'done' : '';
    finance = 'done';
    if (director) dir = r.approved_by != null && r.approved_by !== r.finance_approved_by ? 'done' : 'now';
    paid = r.status === 'paid' ? 'done' : 'now';
  } else if (r.status === 'rejected') check = 'bad';
  else if (r.status === 'cancelled') author = 'bad';
  return [
    { c: author, l: tx('Заявитель') },
    { c: check, l: legacy ? tx('Проверка · не требовалась') : tx('Проверка') },
    { c: finance, l: tx('Фин. директор') },
    { c: dir, l: director ? tx('Директор') : tx('Директор · не участвует') },
    { c: paid, l: tx('Оплата') },
  ];
}

export const minDue = (dues, priority) => (dues && dues[priority]) || '';

const HISTORY = {
  submit: N_('Отправлена на согласование'), check: N_('Проверено бухгалтером: реквизиты и комплектность'), approve: N_('Согласована'),
  return: N_('Возвращена на доработку'), return_finance: N_('Возвращена финансовому директору'), close: N_('Закрыта без оплаты'),
  reschedule: N_('Перенесена плановая дата'), reject: N_('Отклонена (прежний порядок)'), cancel: N_('Отменена (прежний порядок)'),
};
// Журнал хранит «Действие по заявке: <код>»; в карточке показываем понятную подпись и комментарий.
// Прочие записи журнала — подписи сервера, они переводятся словарём сервера.
export function historyLabel(action) {
  const code = String(action || '').replace('Действие по заявке: ', ''); // i18n-ignore
  return tx(HISTORY[code] || action);
}
export function historyNote(detail) {
  try { const d = JSON.parse(detail); return typeof d.reason === 'string' ? d.reason : ''; } catch { return ''; }
}
// Для действий с документами — какой файл и какая версия.
export function historyFile(detail) {
  try {
    const d = JSON.parse(detail);
    if (typeof d.filename !== 'string') return '';
    return (DOC_KINDS[d.kind] ? tx(DOC_KINDS[d.kind]) + ': ' : '') + d.filename + (d.version ? ' · ' + tx('версия {n}', {n: d.version}) : '')
      + (d.approval_reset ? ' · ' + tx('согласование начато заново') : '');
  } catch { return ''; }
}

export function accountsFor(accounts, channel, currency) {
  return accounts.filter(a => a.kind === channel && a.currency === currency);
}

export function missingDocs(r) {
  return (r.missing_documents || REQUIRED_DOCS.filter(k => !(r.documents && r.documents[k])));
}

export function budgetRows(card) {
  if (!card || !card.budget_set) return [];
  return [[tx('Лимит'), card.limit], [tx('Использовано'), card.used], [tx('Зарезервировано'), card.reserved],
          [tx('Доступно'), card.available], [tx('Останется после заявки'), card.after]];
}

export function budgetNote(card) {
  if (!card) return '';
  if (!card.budget_set) return tx('Лимит на этот период не задан.');
  if (card.status === 'soft') return tx('Мягкий лимит будет превышен: при согласовании понадобится обоснование.');
  if (card.status === 'hard') return tx('Жёсткий лимит будет превышен: заявку не примут.');
  return '';
}

const MONTHS = [N_('январь'), N_('февраль'), N_('март'), N_('апрель'), N_('май'), N_('июнь'), N_('июль'), N_('август'),
  N_('сентябрь'), N_('октябрь'), N_('ноябрь'), N_('декабрь')];
export function periodLabel(p) {
  const [y, m] = String(p || '').split('-');
  const name = MONTHS[Number(m) - 1];
  return name ? `${tx(name)} ${y}` : p;
}

export function fileSize(bytes) {
  if (!(bytes > 0)) return '';
  if (bytes >= 1024 * 1024) return tx('{n} МБ', {n: (bytes / 1024 / 1024).toFixed(1)});
  return tx('{n} КБ', {n: Math.max(1, Math.round(bytes / 1024))});
}
