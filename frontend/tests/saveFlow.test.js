import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createSaveState, requestBody, saveRequest, removeSavedDocument, missingRequired, RETRY_REASON} from '../src/requests/saveFlow.js';

// A small in-memory server with the same rules as the backend for the calls the form makes:
// versions, 409 for a stale version, 428 without a version after submit, one active internal/contract
// file, approval reset after submit, and submit only with both required documents.
const httpError = (status, message) => Object.assign(new Error(message), {status});
const noReply = () => new TypeError('Failed to fetch');
const FIELDS = ['company_id', 'account_id', 'channel', 'currency', 'category_id', 'amount', 'counterparty', 'purpose', 'priority', 'date', 'project'];

function fakeServer() {
  const calls = [], requests = new Map(), docs = [], faults = [];
  let nextId = 41, nextDoc = 100;
  const pick = body => Object.fromEntries(FIELDS.map(k => [k, body[k]]));
  const view = r => ({...r, number: `CF-${String(r.id).padStart(5, '0')}`, actions: [],
    documents_list: docs.filter(d => d.request_id === r.id).map(d => ({id: d.id, kind: d.kind, version: d.version, current: d.active,
      filename: d.filename, sha256: d.sha256, url: `/api/request-documents/${d.id}`}))});
  const version = (r, v, required) => {
    if (v == null) { if (required) throw httpError(428, 'Обновите список заявок: для действия нужна версия документа.'); return; }
    if (v !== r.version) throw httpError(409, 'Заявка уже изменена другим пользователем. Обновите список и проверьте изменения.');
  };
  const sent = r => ['pending', 'approved'].includes(r.status);
  function handle({method, url, body, headers}) {
    let m;
    if (method === 'POST' && url === '/api/requests') {
      if (body.status !== 'draft') throw httpError(422, 'Сначала сохраните черновик и приложите документы.');
      const r = {id: nextId++, ...pick(body), status: 'draft', version: 1};
      requests.set(r.id, r);
      return view(r);
    }
    if ((m = url.match(/^\/api\/requests\/(\d+)$/))) {
      const r = requests.get(Number(m[1]));
      if (method === 'GET') return view(r);
      if (method === 'PUT') {
        version(r, body.version, true);
        if (!['draft', 'pending', 'returned'].includes(r.status)) throw httpError(409, 'Редактирование доступно до утверждения.');
        if (String(body.reason || '').length < 10) throw httpError(422, 'reason: String should have at least 10 characters');
        if (body.date < '2026-10-08') throw httpError(422, 'Для обычного приоритета нужно не меньше 7 рабочих дней: ближайшая допустимая дата 08.10.2026.');
        Object.assign(r, pick(body), {status: body.status, version: r.version + 1});
        return view(r);
      }
    }
    if ((m = url.match(/^\/api\/requests\/(\d+)\/documents\?kind=(\w+)$/)) && method === 'POST') {
      const r = requests.get(Number(m[1])), kind = m[2];
      const v = headers['X-Request-Version'] == null ? null : Number(headers['X-Request-Version']);
      version(r, v, sent(r));
      const previous = docs.filter(d => d.request_id === r.id && d.kind === kind);
      if (kind !== 'other') previous.forEach(d => { d.active = false; });
      const doc = {id: nextDoc++, request_id: r.id, kind, filename: decodeURIComponent(headers['X-Filename']), version: previous.length + 1,
                   sha256: body.sha, active: true};
      docs.push(doc);
      const reset = sent(r);
      if (reset) Object.assign(r, {status: 'pending', version: r.version + 1});
      return {id: doc.id, kind, label: kind, filename: doc.filename, version: doc.version, url: `/api/request-documents/${doc.id}`,
              request_version: r.version, status: r.status, approval_reset: reset};
    }
    if ((m = url.match(/^\/api\/requests\/(\d+)\/decision$/)) && method === 'POST' && body.action === 'submit') {
      const r = requests.get(Number(m[1]));
      version(r, body.version, true);
      if (!['draft', 'returned'].includes(r.status)) throw httpError(403, 'Отправить можно свой черновик или возвращённую заявку.');
      const kinds = new Set(docs.filter(d => d.request_id === r.id && d.active).map(d => d.kind));
      if (!kinds.has('internal') || !kinds.has('contract')) throw httpError(409, 'Перед отправкой прикрепите: «Договор / Счёт на оплату».');
      Object.assign(r, {status: 'pending', version: r.version + 1});
      return view(r);
    }
    if ((m = url.match(/^\/api\/request-documents\/(\d+)$/)) && method === 'DELETE') {
      const d = docs.find(x => x.id === Number(m[1])), r = requests.get(d.request_id);
      if (!['draft', 'returned'].includes(r.status)) throw httpError(409, 'После отправки документ убирают с указанием причины в карточке заявки.');
      d.active = false;
      return {ok: true, request_version: r.version, status: r.status, approval_reset: false};
    }
    throw httpError(404, `Нет маршрута ${method} ${url}`);
  }
  async function api(url, opts = {}) {
    const call = {method: opts.method || 'GET', url, headers: {...(opts.headers || {})},
                  body: typeof opts.body === 'string' ? JSON.parse(opts.body) : opts.body};
    calls.push(call);
    const i = faults.findIndex(f => f.method === call.method && f.url.test(url));
    if (i >= 0) {
      const [f] = faults.splice(i, 1);
      if (f.before) f.before();
      else if (f.lose) { handle(call); throw noReply(); }
      else throw f.error;
    }
    return structuredClone(handle(call));
  }
  return {
    api, calls, requests, docs,
    fail: (method, url, error) => faults.push({method, url, error}),
    lose: (method, url) => faults.push({method, url, lose: true}),
    before: (method, url, before) => faults.push({method, url, before}),
    trail: from => calls.slice(from).map(c => `${c.method} ${c.url}`),
    creates: () => calls.filter(c => c.method === 'POST' && c.url === '/api/requests').length,
    add: fields => { const r = {id: nextId++, ...fields}; requests.set(r.id, r); return view(r); },
    attach: (id, kind, sha) => docs.push({id: nextDoc++, request_id: id, kind, filename: kind + '.pdf', version: 1, sha256: sha, active: true}),
  };
}

let fileNo = 0;
const file = (kind, name, sha = '') => ({key: 'f' + (++fileNo), kind, name, file: {name, size: 1000, sha}, sha256: sha});
const FORM = {channel: 'bank', currency: 'UZS', account_id: 1, category_id: 5, amount: '100', counterparty: 'ООО Поставщик',
              purpose: 'Оплата по счёту поставщика', priority: 'normal', date: '2026-10-09', project: ''};
const body = form => requestBody(form, 7);

// The form removes a file from its pending list as soon as it is uploaded (as RequestEditor does).
function editorFiles(list) {
  const files = {list: [...list]};
  files.onUploaded = item => { files.list = files.list.filter(f => f.key !== item.key); };
  return files;
}

test('reported case: the contract upload fails, the user changes the amount and fields, the retry saves them', async () => {
  const s = fakeServer(), state = createSaveState();
  const internal = file('internal', 'indent.pdf'), contract = file('contract', 'Договор №17.pdf');
  const files = editorFiles([internal, contract]);
  s.fail('POST', /documents\?kind=contract$/, httpError(422, 'Документ заявки: PDF, PNG, JPEG, DOCX или XLSX, не больше 5 МБ.'));

  const first = await saveRequest(s.api, state, {fields: body(FORM), files: files.list, submit: true, onUploaded: files.onUploaded});
  assert.equal(first.ok, false);
  assert.equal(first.code, 'upload_failed');
  assert.match(first.message, /Договор №17\.pdf/);
  assert.deepEqual(s.trail(0), ['POST /api/requests', 'POST /api/requests/41/documents?kind=internal', 'POST /api/requests/41/documents?kind=contract']);
  assert.equal(s.calls[0].body.status, 'draft');
  assert.equal(s.calls[0].body.amount, '100');
  assert.ok(state.uploaded.has(internal.key), 'the internal file is marked uploaded right after success');
  assert.ok(!state.uploaded.has(contract.key));
  assert.deepEqual(files.list.map(f => f.key), [contract.key]);

  // New amount, date, purpose, account and category, then «Отправить на согласование» again.
  const changed = {...FORM, amount: '200', date: '2026-10-12', purpose: 'Оплата по счёту №17 с доставкой', account_id: 2, category_id: 6};
  const from = s.calls.length, serverVersion = s.requests.get(41).version;
  const second = await saveRequest(s.api, state, {fields: body(changed), files: files.list, saved: [{kind: 'internal', current: true}],
                                                  submit: true, onUploaded: files.onUploaded});
  assert.equal(second.ok, true, second.message);
  assert.equal(second.submitted, true);
  assert.deepEqual(s.trail(from), ['GET /api/requests/41', 'PUT /api/requests/41', 'POST /api/requests/41/documents?kind=contract',
                                   'POST /api/requests/41/decision']);
  const put = s.calls[from + 1].body;
  assert.deepEqual({amount: put.amount, date: put.date, purpose: put.purpose, account_id: put.account_id, category_id: put.category_id},
                   {amount: '200', date: '2026-10-12', purpose: 'Оплата по счёту №17 с доставкой', account_id: 2, category_id: 6});
  assert.equal(put.status, 'draft');
  assert.equal(put.version, serverVersion);
  assert.equal(put.reason, RETRY_REASON);
  const upload = s.calls[from + 2];
  assert.equal(upload.headers['X-Request-Version'], String(serverVersion + 1));
  const submit = s.calls[from + 3].body;
  assert.deepEqual(submit, {action: 'submit', version: serverVersion + 1});

  assert.equal(s.creates(), 1, 'exactly one POST /api/requests for the form');
  const saved = s.requests.get(41);
  assert.equal(saved.status, 'pending');
  assert.equal(saved.amount, '200');
  assert.equal(s.requests.size, 1);
  assert.deepEqual(s.docs.filter(d => d.active).map(d => d.kind).sort(), ['contract', 'internal']);
});

test('a lost submit response never turns the sent request back into a draft or creates a second one', async () => {
  const s = fakeServer(), state = createSaveState();
  const files = editorFiles([file('internal', 'indent.pdf'), file('contract', 'invoice.pdf')]);
  s.lose('POST', /\/decision$/);

  const first = await saveRequest(s.api, state, {fields: body(FORM), files: files.list, submit: true, onUploaded: files.onUploaded});
  assert.equal(first.ok, false);
  assert.equal(first.step, 'submit');
  assert.equal(s.requests.get(41).status, 'pending', 'the server did submit it');

  for (const submit of [true, false]) {
    const from = s.calls.length;
    const again = await saveRequest(s.api, state, {fields: body({...FORM, amount: '999'}), files: files.list, submit,
                                                   saved: [{kind: 'internal'}, {kind: 'contract'}]});
    assert.equal(again.ok, false);
    assert.equal(again.code, 'already_sent');
    assert.match(again.message, /уже отправлена на согласование/);
    assert.match(again.message, /карточке заявки с указанием причины/);
    assert.deepEqual(s.trail(from), ['GET /api/requests/41'], 'no PUT with status draft, no new request');
  }
  assert.equal(s.creates(), 1);
  assert.equal(s.requests.get(41).status, 'pending');
  assert.equal(s.requests.get(41).amount, '100');
});

test('a stale version refetches the request, explains it and creates nothing new', async () => {
  const s = fakeServer();
  const opened = s.add({...body(FORM), status: 'draft', version: 3});
  const state = createSaveState(opened);
  // Another editor saves the draft between our read and our write.
  s.before('PUT', /^\/api\/requests\/41$/, () => { s.requests.get(41).version = 4; });

  const first = await saveRequest(s.api, state, {fields: body({...FORM, amount: '150'}), reason: 'Уточнена сумма по счёту'});
  assert.equal(first.ok, false);
  assert.equal(first.code, 'stale');
  assert.match(first.message, /изменена другим пользователем/);
  assert.match(first.message, /версия 4/);
  assert.deepEqual(s.trail(0), ['GET /api/requests/41', 'PUT /api/requests/41', 'GET /api/requests/41']);
  assert.equal(state.version, 4, 'the version was refetched');
  assert.equal(s.creates(), 0);

  const from = s.calls.length;
  const second = await saveRequest(s.api, state, {fields: body({...FORM, amount: '150'}), reason: 'Уточнена сумма по счёту'});
  assert.equal(second.ok, true, second.message);
  assert.deepEqual(s.trail(from), ['GET /api/requests/41', 'PUT /api/requests/41']);
  assert.equal(s.calls[from + 1].body.version, 4);
  assert.equal(s.requests.get(41).amount, '150');
  assert.equal(s.creates(), 0);
});

test('editing a request on approval keeps it pending and refuses a stale version', async () => {
  const s = fakeServer();
  const opened = s.add({...body(FORM), status: 'pending', version: 5});
  s.attach(41, 'internal', 'aa'); s.attach(41, 'contract', 'bb');
  const state = createSaveState(opened);
  assert.equal(state.mode, 'sent');
  s.requests.get(41).version = 6; // the accountant checked it meanwhile
  const saved = [{kind: 'internal', current: true}, {kind: 'contract', current: true}];

  const first = await saveRequest(s.api, state, {fields: body({...FORM, amount: '120'}), saved, reason: 'Поставщик выставил новый счёт'});
  assert.equal(first.code, 'stale');
  assert.equal(s.requests.get(41).amount, '100');

  const files = editorFiles([file('contract', 'invoice-v2.pdf')]);
  const from = s.calls.length;
  const second = await saveRequest(s.api, state, {fields: body({...FORM, amount: '120'}), saved, files: files.list, onUploaded: files.onUploaded,
                                                  reason: 'Поставщик выставил новый счёт'});
  assert.equal(second.ok, true, second.message);
  assert.deepEqual(s.trail(from), ['GET /api/requests/41', 'PUT /api/requests/41', 'POST /api/requests/41/documents?kind=contract']);
  assert.equal(s.calls[from + 1].body.status, 'pending', 'never written back as a draft');
  assert.equal(s.calls[from + 2].headers['X-Request-Version'], '7');
  assert.equal(s.requests.get(41).status, 'pending');
  assert.equal(s.creates(), 0);
});

test('a draft is saved without files by one POST; unchanged repeats do not write again', async () => {
  const s = fakeServer(), state = createSaveState();
  const first = await saveRequest(s.api, state, {fields: body(FORM)});
  assert.equal(first.ok, true, first.message);
  assert.equal(first.submitted, false);
  assert.equal(first.number, 'CF-00041');
  assert.deepEqual(s.trail(0), ['POST /api/requests']);
  assert.deepEqual(s.calls[0].body, {...body(FORM), status: 'draft'});

  let from = s.calls.length;
  await saveRequest(s.api, state, {fields: body(FORM)});
  assert.deepEqual(s.trail(from), ['GET /api/requests/41'], 'nothing changed: no PUT');

  from = s.calls.length;
  await saveRequest(s.api, state, {fields: body({...FORM, counterparty: 'ООО Новый поставщик'})});
  assert.deepEqual(s.trail(from), ['GET /api/requests/41', 'PUT /api/requests/41']);
  assert.equal(s.calls[from + 1].body.status, 'draft');
  assert.equal(s.creates(), 1);
});

test('after the PUT itself fails the retry sends the current fields, the missing file and submits once', async () => {
  const s = fakeServer(), state = createSaveState();
  const files = editorFiles([file('internal', 'indent.pdf'), file('contract', 'invoice.pdf')]);
  s.fail('POST', /documents\?kind=contract$/, noReply());
  const first = await saveRequest(s.api, state, {fields: body(FORM), files: files.list, submit: true, onUploaded: files.onUploaded});
  assert.equal(first.code, 'upload_failed');
  assert.match(first.message, /сервер не ответил/);

  // The retry with a date that is too early: the PUT is refused and nothing else is sent.
  let from = s.calls.length;
  const second = await saveRequest(s.api, state, {fields: body({...FORM, amount: '300', date: '2026-10-01'}), files: files.list, submit: true,
                                                  saved: [{kind: 'internal'}], onUploaded: files.onUploaded});
  assert.equal(second.ok, false);
  assert.equal(second.step, 'update');
  assert.match(second.message, /7 рабочих дней/);
  assert.deepEqual(s.trail(from), ['GET /api/requests/41', 'PUT /api/requests/41']);

  from = s.calls.length;
  const third = await saveRequest(s.api, state, {fields: body({...FORM, amount: '300', date: '2026-10-14'}), files: files.list, submit: true,
                                                 saved: [{kind: 'internal'}], onUploaded: files.onUploaded});
  assert.equal(third.ok, true, third.message);
  assert.deepEqual(s.trail(from), ['GET /api/requests/41', 'PUT /api/requests/41', 'POST /api/requests/41/documents?kind=contract',
                                   'POST /api/requests/41/decision']);
  const put = s.calls[from + 1].body;
  assert.equal(put.amount, '300');
  assert.equal(put.date, '2026-10-14');
  assert.equal(put.version, 1, 'the refused PUT did not change the version');
  assert.equal(s.calls[from + 3].body.version, 2);
  assert.equal(s.creates(), 1);
  assert.equal(s.docs.filter(d => d.kind === 'internal').length, 1, 'the internal file was uploaded once');
  assert.equal(s.requests.get(41).status, 'pending');
});

test('submit is refused before any call when a required block is empty', async () => {
  const s = fakeServer(), state = createSaveState();
  const result = await saveRequest(s.api, state, {fields: body(FORM), files: [file('internal', 'indent.pdf'), file('other', 'photo.png')], submit: true});
  assert.equal(result.code, 'missing_documents');
  assert.match(result.message, /Договор \/ Счёт на оплату/);
  assert.equal(s.calls.length, 0);
  assert.deepEqual(missingRequired([{kind: 'contract', current: true}], []), ['internal']);
  assert.deepEqual(missingRequired([{kind: 'internal', current: false}], [{kind: 'contract'}]), ['internal']);
});

test('an unanswered create is never repeated by the same form', async () => {
  const s = fakeServer(), state = createSaveState();
  s.lose('POST', /^\/api\/requests$/);
  const first = await saveRequest(s.api, state, {fields: body(FORM)});
  assert.equal(first.code, 'unknown_create');
  const second = await saveRequest(s.api, state, {fields: body(FORM)});
  assert.equal(second.code, 'unknown_create');
  assert.equal(s.creates(), 1);
  assert.equal(s.requests.size, 1);
});

test('a file whose upload reply was lost is not uploaded twice', async () => {
  const s = fakeServer(), state = createSaveState();
  const files = editorFiles([file('internal', 'indent.pdf', 'sha-indent'), file('contract', 'invoice.pdf', 'sha-invoice')]);
  s.lose('POST', /documents\?kind=contract$/);
  const first = await saveRequest(s.api, state, {fields: body(FORM), files: files.list, submit: true, onUploaded: files.onUploaded});
  assert.equal(first.code, 'upload_failed');
  const from = s.calls.length;
  const second = await saveRequest(s.api, state, {fields: body(FORM), files: files.list, submit: true, onUploaded: files.onUploaded,
                                                  saved: [{kind: 'internal'}]});
  assert.equal(second.ok, true, second.message);
  assert.deepEqual(s.trail(from), ['GET /api/requests/41', 'POST /api/requests/41/decision']);
  assert.equal(s.docs.filter(d => d.kind === 'contract').length, 1);
  assert.deepEqual(files.list, []);
});

test('a saved draft file is removed with DELETE; the request body keeps numbers and a string amount', async () => {
  const s = fakeServer();
  const opened = s.add({...body(FORM), status: 'draft', version: 2});
  s.attach(41, 'other', 'x');
  const state = createSaveState(opened);
  const res = await removeSavedDocument(s.api, state, {id: 100});
  assert.equal(res.ok, true);
  assert.deepEqual(s.trail(0), ['DELETE /api/request-documents/100']);
  assert.equal(s.docs[0].active, false);
  assert.deepEqual(requestBody({...FORM, account_id: '3', category_id: '9', amount: 1500.5, counterparty: '  ООО Альфа '}, 7),
    {company_id: 7, account_id: 3, channel: 'bank', currency: 'UZS', category_id: 9, amount: '1500.5', counterparty: 'ООО Альфа',
     purpose: 'Оплата по счёту поставщика', priority: 'normal', date: '2026-10-09', project: ''});
  assert.equal(createSaveState({id: 5, status: 'returned', version: 2}).mode, 'draft');
  assert.equal(createSaveState({id: 5, status: 'pending', version: 2}).mode, 'sent');
});
