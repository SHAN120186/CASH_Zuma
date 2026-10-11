// Сохранение формы заявки на оплату: черновик → вложения → отправка на согласование.
//
// Модуль не зависит от Vue: функция api(url, opts) передаётся снаружи (та же, что в App.vue),
// ошибки HTTP несут поле status. Состояние формы явное: id и версия заявки на сервере, её статус,
// режим формы и множество уже загруженных файлов. Правила:
//  - на одну форму создаётся не больше одной заявки (один успешный POST /api/requests);
//  - если черновик уже существует (создан этой формой или открыт для правки), сначала читается
//    его состояние на сервере, затем PUT текущих полей формы с версией сервера;
//  - файлы загружаются по одному, каждый отмечается загруженным сразу после успеха,
//    версия заявки берётся из ответа; при повторе загружаются только незагруженные;
//  - отправка идёт с последней версией;
//  - если заявка уже на согласовании (например, потерялся ответ на отправку), форма не пишет
//    в неё статус «черновик» и не создаёт вторую заявку: дальнейшие изменения — в карточке с причиной.

import {DOC_KINDS, REQUIRED_DOCS, STATUS} from './workflow.js';
import {tx, N_} from '../i18n/index.js';

// Причина уходит на сервер по-русски (её видно в истории заявки); при показе она переводится.
export const RETRY_REASON = N_('Повторное сохранение формы после ошибки загрузки файла');
export const KIND_ORDER = ['internal', 'contract', 'other'];
const EDITABLE = ['draft', 'returned'];
const FIELDS = ['company_id', 'account_id', 'channel', 'currency', 'category_id', 'amount', 'counterparty', 'purpose', 'priority', 'date', 'project'];

// Постоянные сообщения хранятся по-русски (N_) и переводятся формой при показе;
// сообщения с номером, статусом или текстом сервера собираются на текущем языке (tx).
export const MESSAGES = {
  unknownCreate: N_('Сервер не ответил при создании черновика: он мог сохраниться. Чтобы не создать вторую заявку, закройте форму и найдите черновик в списке заявок.'),
  alreadySent: n => tx('Заявка {n} уже отправлена на согласование. Дальнейшие изменения — в карточке заявки с указанием причины.', {n}),
  closed: (n, status) => tx('Заявка {n} уже в статусе «{status}»: форма её не меняет. Откройте карточку заявки.', {n, status: tx(STATUS[status] || status)}),
  statusChanged: (n, status) => tx('Статус заявки {n} изменился: «{status}». Закройте форму и откройте карточку заявки.', {n, status: tx(STATUS[status] || status)}),
  stale: v => (v ? tx('Состояние заявки обновлено с сервера (версия {v}). Проверьте поля и нажмите кнопку ещё раз.', {v})
    : tx('Состояние заявки обновлено с сервера. Проверьте поля и нажмите кнопку ещё раз.')),
  noReply: N_('Сервер не ответил. Нажмите кнопку ещё раз: форма сначала проверит состояние заявки на сервере.'),
};

export function createSaveState(request = null) {
  return {
    id: request?.id ?? null,
    number: request?.number ?? null,
    version: request?.version ?? null,
    status: request?.status ?? null,
    // 'draft' — новая заявка, черновик или возвращённая; 'sent' — правка заявки на согласовании
    // (она остаётся на согласовании, согласования начинаются заново).
    mode: request?.status === 'pending' ? 'sent' : 'draft',
    created: false,
    createUnknown: false,
    lastSent: null,
    uploaded: new Set(),
  };
}

// Поля формы → тело запроса. Сумма всегда строкой; проект необязателен.
export function requestBody(form, companyId) {
  return {
    company_id: companyId, account_id: form.account_id == null ? null : Number(form.account_id), channel: form.channel,
    currency: form.currency, category_id: form.category_id == null ? null : Number(form.category_id),
    amount: String(form.amount ?? '').trim(), counterparty: String(form.counterparty ?? '').trim(),
    purpose: String(form.purpose ?? '').trim(), priority: form.priority, date: form.date, project: String(form.project ?? '').trim(),
  };
}

const snapshot = fields => JSON.stringify(FIELDS.map(k => fields[k] ?? null));

// Обязательные разделы, которых нет ни среди сохранённых файлов, ни среди выбранных.
export function missingRequired(saved = [], files = []) {
  const kinds = new Set([...saved.filter(d => d.current !== false).map(d => d.kind), ...files.map(f => f.kind)]);
  return REQUIRED_DOCS.filter(k => !kinds.has(k));
}

export function missingMessage(missing) {
  return tx('Для отправки прикрепите: {list}. Черновик можно сохранить без файлов.', {list: missing.map(k => '«' + tx(DOC_KINDS[k]) + '»').join(', ')});
}

// Ответ сервера не получен (сеть, прерванный запрос, шлюз): исход операции неизвестен.
export const unknownOutcome = e => e?.status == null || e.status >= 502;

const json = (method, body) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
// Сообщение сервера внутри фразы: без точки в конце.
const clause = text => String(text || '').trim().replace(/[.。]+$/, '');

function remember(state, request) {
  if (!request) return;
  if (request.id != null) state.id = request.id;
  if (request.number) state.number = request.number;
  if (request.version != null) state.version = request.version;
  if (request.status) state.status = request.status;
}

async function refetch(api, state, onServer) {
  try { const server = await api(`/api/requests/${state.id}`); remember(state, server); onServer?.(server); return server; }
  catch { return null; }
}

/**
 * Сохраняет форму. Возвращает {ok:true, id, number, submitted, request} или
 * {ok:false, code, message, step, request?}; исключений не бросает.
 * files — выбранные, ещё не сохранённые файлы [{key, kind, name, file, sha256?}];
 * saved — действующие файлы заявки на сервере (для проверки обязательных разделов).
 * onUploaded(item, res), onServer(card), onStep('save'|'upload'|'submit') — необязательные уведомления формы.
 */
export async function saveRequest(api, state, {fields, files = [], saved = [], submit = false, reason = '', onUploaded, onServer, onStep} = {}) {
  const fail = (code, message, extra = {}) => ({ok: false, code, message, ...extra});
  const sending = submit || state.mode === 'sent';
  if (sending) {
    const missing = missingRequired(saved, files);
    if (missing.length) return fail('missing_documents', missingMessage(missing), {step: 'check'});
  }
  if (state.createUnknown) return fail('unknown_create', MESSAGES.unknownCreate, {step: 'create'});

  onStep?.('save');
  let request = null, server = null;
  if (state.id == null) {
    // 1а. Новая заявка: один POST на форму, всегда черновиком.
    try { request = await api('/api/requests', json('POST', {...fields, status: 'draft'})); }
    catch (e) {
      if (unknownOutcome(e)) { state.createUnknown = true; return fail('unknown_create', MESSAGES.unknownCreate, {step: 'create'}); }
      return fail('failed', e.message, {step: 'create'});
    }
    remember(state, request);
    state.created = true;
    state.lastSent = snapshot(fields);
  } else {
    // 1б. Заявка уже есть: сначала её состояние на сервере.
    try { server = await api(`/api/requests/${state.id}`); }
    catch (e) { return fail('failed', unknownOutcome(e) ? MESSAGES.noReply : e.message, {step: 'refresh'}); }
    onServer?.(server);
    const known = state.version;
    state.number = server.number || state.number;
    if (state.mode === 'draft') {
      if (!EDITABLE.includes(server.status)) {
        remember(state, server);
        const sent = ['pending', 'approved'].includes(server.status);
        return fail(sent ? 'already_sent' : 'closed', sent ? MESSAGES.alreadySent(state.number) : MESSAGES.closed(state.number, server.status), {step: 'refresh', request: server});
      }
      // Черновик правит автор (или финансовый руководитель): сохраняются текущие поля формы с версией сервера.
      const unchanged = state.lastSent === snapshot(fields) && server.version === known;
      remember(state, server);
      if (!unchanged) {
        const why = String(reason || '').trim() || (state.created ? RETRY_REASON : '');
        try { request = await api(`/api/requests/${state.id}`, json('PUT', {...fields, status: 'draft', version: state.version, reason: why})); }
        catch (e) { return conflictOr(api, state, e, 'update', onServer); }
        remember(state, request);
        state.lastSent = snapshot(fields);
      } else request = server;
    } else {
      // Правка отправленной заявки: она остаётся на согласовании, версия — та, с которой открыта форма.
      if (server.status !== 'pending') {
        remember(state, server);
        return fail('status_changed', MESSAGES.statusChanged(state.number, server.status), {step: 'refresh', request: server});
      }
      if (state.lastSent !== snapshot(fields) || server.version !== known) {
        try { request = await api(`/api/requests/${state.id}`, json('PUT', {...fields, status: 'pending', version: state.version, reason: String(reason || '').trim()})); }
        catch (e) { return conflictOr(api, state, e, 'update', onServer); }
        remember(state, request);
        state.lastSent = snapshot(fields);
      } else request = server;
    }
  }

  // 2. Вложения: по одному, только ещё не загруженные.
  const current = (server?.documents_list || []).filter(d => d.current);
  if (files.some(f => !state.uploaded.has(f.key))) onStep?.('upload');
  for (const kind of KIND_ORDER) {
    for (const item of files.filter(f => f.kind === kind)) {
      if (state.uploaded.has(item.key)) continue;
      // Ответ на прошлую загрузку мог потеряться: тот же файл уже есть на сервере — не дублируем.
      if (item.sha256 && current.some(d => d.kind === kind && d.sha256 === item.sha256)) {
        state.uploaded.add(item.key);
        onUploaded?.(item, null);
        continue;
      }
      let res;
      try {
        res = await api(`/api/requests/${state.id}/documents?kind=${kind}`, {method: 'POST', body: item.file, headers: {
          'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(item.name), 'X-Request-Version': String(state.version)}});
      } catch (e) {
        if (e.status === 409 || e.status === 428) return conflictOr(api, state, e, 'upload', onServer);
        const why = unknownOutcome(e) ? tx('сервер не ответил') : clause(tx(e.message));
        const message = tx('Файл «{name}» не загружен: {why}. Черновик {number} сохранён; исправьте и нажмите кнопку ещё раз — уже загруженные файлы повторно не отправляются.',
          {name: item.name, why, number: state.number || ''});
        return fail('upload_failed', message, {step: 'upload', file: item.key});
      }
      state.uploaded.add(item.key);
      if (res?.request_version != null) state.version = res.request_version;
      if (res?.status) state.status = res.status;
      onUploaded?.(item, res);
    }
  }

  // 3. Отправка с последней версией.
  if (submit && state.mode === 'draft') {
    onStep?.('submit');
    try { request = await api(`/api/requests/${state.id}/decision`, json('POST', {action: 'submit', version: state.version})); }
    catch (e) {
      if (unknownOutcome(e)) return fail('failed', MESSAGES.noReply, {step: 'submit'});
      const server = await refetch(api, state, onServer);
      return fail('submit_failed', tx('Черновик {number} сохранён, но не отправлен: {reason}.', {number: state.number || '', reason: clause(tx(e.message))}),
        {step: 'submit', request: server});
    }
    remember(state, request);
  }
  return {ok: true, id: state.id, number: state.number, submitted: sending, request};
}

async function conflictOr(api, state, e, step, onServer) {
  if (e.status === 409 || e.status === 428) {
    const server = await refetch(api, state, onServer);
    return {ok: false, code: 'stale', message: `${tx(e.message)} ${MESSAGES.stale(server?.version)}`, step, request: server};
  }
  return {ok: false, code: 'failed', message: unknownOutcome(e) ? MESSAGES.noReply : e.message, step};
}

// Убрать сохранённый файл черновика (до отправки); после отправки файлы убирают в карточке с причиной.
export async function removeSavedDocument(api, state, doc) {
  const res = await api(`/api/request-documents/${doc.id}`, {method: 'DELETE'});
  if (res?.request_version != null) state.version = res.request_version;
  if (res?.status) state.status = res.status;
  return res;
}
