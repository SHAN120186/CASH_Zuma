// Маршрут заявки на оплату. Сервер решает, какие действия доступны (r.actions), и проверяет
// каждое из них; здесь только подписи, этапы и подсказки для формы.

export const STATUS = {
  draft: 'Черновик', pending: 'На согласовании', approved: 'Утверждена', paid: 'Оплачена',
  returned: 'На доработке', cancelled: 'Закрыта без оплаты', rejected: 'Отклонена (архив)',
};

export const STAGES = { check: 'Проверка бухгалтера', finance: 'Финансовый директор', director: 'Директор' };

export const PRIORITIES = { normal: 'Обычный', high: 'Высокий', urgent: 'Срочный' };
export const LEAD_DAYS = { normal: 7, high: 3, urgent: 1 };
export const CHANNELS = { bank: 'Банк (расчётный счёт)', cash: 'Касса' };

export const DOC_KINDS = {
  indent: 'Внутренняя заявка / Индент',
  contract: 'Договор / Счёт на оплату',
  other: 'Прочие подтверждающие документы',
};
export const REQUIRED_DOCS = ['indent', 'contract'];

// Два отрицательных действия — возврат и закрытие; оба с обязательным комментарием.
export const ACTION_LABELS = {
  check: 'Проверено: реквизиты и комплектность',
  approve: 'Согласовать',
  return: 'Вернуть на доработку',
  return_finance: 'Вернуть финансовому директору',
  close: 'Закрыть без оплаты',
  reschedule: 'Перенести дату',
};
export const DECISIONS = Object.keys(ACTION_LABELS);
export const COMMENT_REQUIRED = ['return', 'return_finance', 'close', 'reschedule'];

export function stageLabel(r) {
  if (r.status !== 'pending') return STATUS[r.status] || r.status;
  return 'Ожидает: ' + (STAGES[r.approval_stage] || 'согласования').toLowerCase();
}

export function approveLabel(r) {
  if (r.approval_stage === 'finance') return r.route && !r.route.director_required ? 'Утвердить (директор не участвует)' : 'Подтвердить проверку бюджета';
  return 'Утвердить оплату';
}

export function decisionOptions(r) {
  return (r.actions || []).filter(a => DECISIONS.includes(a)).map(a => [a, a === 'approve' ? approveLabel(r) : ACTION_LABELS[a]]);
}

export const canDecide = r => (r.actions || []).some(a => a === 'approve' || a === 'check');

// Этапы для трека: Заявитель → Проверка → Фин. директор → Директор (по политике статьи) → Оплата.
export function steps(r) {
  const order = ['check', 'finance', 'director'];
  const director = !r.route || r.route.director_required !== false;
  let author = 'done', check = '', finance = '', dir = director ? '' : 'skip', paid = '';
  if (r.status === 'draft' || r.status === 'returned') author = 'now';
  else if (r.status === 'pending') {
    const at = order.indexOf(r.approval_stage);
    check = at > 0 ? 'done' : 'now';
    finance = at > 1 ? 'done' : at === 1 ? 'now' : '';
    if (director) dir = at === 2 ? 'now' : '';
  } else if (r.status === 'approved' || r.status === 'paid') {
    check = r.checked_by || !r.route?.policy ? 'done' : '';
    finance = 'done';
    if (director) dir = r.approved_by != null && r.approved_by !== r.finance_approved_by ? 'done' : 'now';
    paid = r.status === 'paid' ? 'done' : 'now';
  } else if (r.status === 'rejected') check = 'bad';
  else if (r.status === 'cancelled') author = 'bad';
  return [
    { c: author, l: 'Заявитель' },
    { c: check, l: 'Проверка' },
    { c: finance, l: 'Фин. директор' },
    { c: dir, l: director ? 'Директор' : 'Директор · не участвует' },
    { c: paid, l: 'Оплата' },
  ];
}

export const minDue = (dues, priority) => (dues && dues[priority]) || '';

const HISTORY = {
  submit: 'Отправлена на согласование', check: 'Проверено бухгалтером: реквизиты и комплектность', approve: 'Согласована',
  return: 'Возвращена на доработку', return_finance: 'Возвращена финансовому директору', close: 'Закрыта без оплаты',
  reschedule: 'Перенесена плановая дата',
};
// Журнал хранит «Действие по заявке: <код>»; в карточке показываем понятную подпись и комментарий.
export function historyLabel(action) {
  const code = String(action || '').replace('Действие по заявке: ', '');
  return HISTORY[code] || action;
}
export function historyNote(detail) {
  try { const d = JSON.parse(detail); return typeof d.reason === 'string' ? d.reason : ''; } catch { return ''; }
}

export function accountsFor(accounts, channel, currency) {
  return accounts.filter(a => a.kind === channel && a.currency === currency);
}

export function missingDocs(r) {
  return (r.missing_documents || REQUIRED_DOCS.filter(k => !(r.documents && r.documents[k])));
}

export function budgetRows(card) {
  if (!card || !card.budget_set) return [];
  return [['Лимит', card.limit], ['Использовано', card.used], ['Зарезервировано', card.reserved],
          ['Доступно', card.available], ['Останется после заявки', card.after]];
}

export function budgetNote(card) {
  if (!card) return '';
  if (!card.budget_set) return 'Лимит на этот период не задан.';
  if (card.status === 'soft') return 'Мягкий лимит будет превышен: при согласовании понадобится обоснование.';
  if (card.status === 'hard') return 'Жёсткий лимит будет превышен: заявку не примут.';
  return '';
}

const MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
export function periodLabel(p) {
  const [y, m] = String(p || '').split('-');
  const name = MONTHS[Number(m) - 1];
  return name ? `${name} ${y}` : p;
}

export function fileSize(bytes) {
  if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' МБ';
  return Math.max(1, Math.round(bytes / 1024)) + ' КБ';
}
