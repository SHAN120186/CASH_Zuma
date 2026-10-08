import {test} from 'node:test';
import assert from 'node:assert/strict';
import {fileProblem, MAX_REPORT_BYTES, periodProblem, filterProblem, archiveUrl, archiveFileUrl, uploadLabel, createArchiveState, saveArchive} from '../src/reportArchive.js';

const form = {title: 'Бизнес-план', period_start: '2026-01-01', period_end: '2028-12-31'};
const files = {pdf: {name: 'Отчёт.pdf', size: 12}, xlsx: {name: 'Отчёт.xlsx', size: 15}};
const draft = {id: 41, company_id: 2, ...form, formats: [], status: 'draft'};
const loss = () => new TypeError('Failed to fetch');

function server() {
  const calls = [], records = new Map();
  let failCreate = false, failExcel = false, nextId = 41;
  return {calls, records,
    loseCreate: () => {failCreate = true;}, loseExcel: () => {failExcel = true;},
    api: async (url, options = {}) => {
      calls.push({url, ...options});
      if (options.method === 'POST') {
        const body = JSON.parse(options.body);
        if (!records.has(body.request_key)) records.set(body.request_key, {...draft, ...body, id: nextId++, formats: []});
        if (failCreate) {failCreate = false; throw loss();}
        return structuredClone(records.get(body.request_key));
      }
      if (options.method === 'PUT') {
        const [id, format] = url.match(/report-archives\/(\d+)\/files\/(\w+)/).slice(1);
        const record = [...records.values()].find(r => r.id === Number(id));
        if (!record.formats.includes(format)) record.formats.push(format);
        if (record.formats.length === 2) record.status = 'ready';
        if (format === 'xlsx' && failExcel) {failExcel = false; throw loss();}
        return structuredClone(record);
      }
      throw Error('Unexpected request');
    },
  };
}

test('calendar filters keep report period and upload date distinct; downloads stay company scoped', () => {
  const url = new URL(archiveUrl(2, {date_from: '2026-01-01', date_to: '2026-12-31', uploaded_on: '2026-10-07'}), 'https://local.test');
  assert.deepEqual(Object.fromEntries(url.searchParams), {company_id: '2', date_from: '2026-01-01', date_to: '2026-12-31', uploaded_on: '2026-10-07'});
  assert.equal(archiveFileUrl(draft, 'pdf'), '/api/report-archives/41/files/pdf?company_id=2');
  assert.match(uploadLabel('2026-10-06T22:12:00+00:00'), /07\.10\.2026.*03:12/);
  assert.match(periodProblem('2026-02-30', '2026-12-31'), /Укажите/);
  assert.match(periodProblem('2026-12-31', '2026-01-01'), /раньше/);
  assert.match(periodProblem('2026-01-01', '2032-01-01'), /пяти лет/);
  assert.equal(filterProblem({uploaded_on: '2026-10-07'}), '');
  assert.match(filterProblem({date_from: '2026-12-31', date_to: '2026-01-01'}), /раньше/);
});

test('file validation rejects wrong extensions, empty and oversized files before creating anything', async () => {
  assert.match(fileProblem({name: 'report.xls', size: 10}, 'xlsx'), /расширением/);
  assert.match(fileProblem({name: 'report.pdf', size: 0}, 'pdf'), /пустой/);
  assert.match(fileProblem({name: 'report\u202e.pdf', size: 10}, 'pdf'), /недопустимые/);
  assert.match(fileProblem({name: 'report.pdf', size: MAX_REPORT_BYTES + 1}, 'pdf'), /20 МБ/);
  assert.equal(fileProblem({name: 'report.PDF', size: MAX_REPORT_BYTES}, 'pdf'), '');
  const backend = server();
  await assert.rejects(saveArchive(backend.api, createArchiveState(), {companyId: 2, form, files: {pdf: files.pdf}}), /Выберите/);
  assert.equal(backend.calls.length, 0);
});

test('a lost create response retries the same key and frozen metadata without creating a duplicate', async () => {
  const backend = server(), state = createArchiveState();
  backend.loseCreate();
  await assert.rejects(saveArchive(backend.api, state, {companyId: 2, form, files}), /Failed/);
  assert.equal(state.record, null);
  const record = await saveArchive(backend.api, state, {companyId: 2, form: {...form, title: 'Changed after failure'}, files});
  assert.equal(backend.records.size, 1);
  assert.equal(record.title, form.title);
  assert.equal(record.status, 'ready');
  assert.equal(backend.calls[0].body, backend.calls[1].body);
  assert.equal(backend.calls[2].headers['X-Filename'], encodeURIComponent(files.pdf.name));
});

test('lost Excel response resumes only missing file and confirms readiness from server', async () => {
  const backend = server(), state = createArchiveState(), updates = [];
  backend.loseExcel();
  await assert.rejects(saveArchive(backend.api, state, {companyId: 2, form, files, onRecord: record => updates.push(record.status)}), /Failed/);
  assert.deepEqual(state.record.formats, ['pdf']);
  assert.deepEqual(updates, ['draft', 'draft']);
  const result = await saveArchive(backend.api, state, {companyId: 2, form, files: {xlsx: files.xlsx}});
  assert.equal(result.status, 'ready');
  assert.equal(backend.calls.filter(call => call.method === 'POST').length, 1);
  assert.equal(backend.calls.filter(call => call.url.includes('/files/pdf')).length, 1);
  assert.equal(backend.calls.filter(call => call.url.includes('/files/xlsx')).length, 2);
});

test('scope change during creation stops the subsequent uploads and avoids stale state updates', async () => {
  const state = createArchiveState(); let active = true, updates = 0;
  const api = async () => {active = false; return structuredClone(draft);};
  await assert.rejects(saveArchive(api, state, {companyId: 2, form, files, isCurrent: () => active, onRecord: () => updates++}), {name: 'AbortError'});
  assert.equal(state.record, null);
  assert.equal(updates, 0);
  const other = createArchiveState({...draft, company_id: 7});
  await assert.rejects(saveArchive(api, other, {companyId: 2, form, files}), /другой компании/);
});

test('a definite refusal allows fixing the form while an uncertain create keeps its snapshot', async () => {
  const state = createArchiveState(), rejection = Object.assign(Error('Rejected'), {status: 422});
  await assert.rejects(saveArchive(async () => {throw rejection;}, state, {companyId: 2, form, files}), /Rejected/);
  assert.equal(state.fields, null);
  const backend = server();
  const record = await saveArchive(backend.api, state, {companyId: 2, form: {...form, title: 'Corrected'}, files});
  assert.equal(record.title, 'Corrected');
});

test('a wrong company response cannot cause a follow-up upload', async () => {
  const state = createArchiveState(); let calls = 0;
  await assert.rejects(saveArchive(async () => {calls++; return {...draft, company_id: 7};}, state, {companyId: 2, form, files}), /другой компании/);
  assert.equal(calls, 1);
  assert.equal(state.record, null);
});

test('the client never treats uploaded pair as ready without server ready status', async () => {
  const state = createArchiveState({...draft, formats: ['pdf', 'xlsx']});
  await assert.rejects(saveArchive(async () => {throw Error('No call expected');}, state, {companyId: 2, form, files: {}}), /черновик/);
});
