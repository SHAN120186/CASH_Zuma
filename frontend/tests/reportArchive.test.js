import {test} from 'node:test';
import assert from 'node:assert/strict';
import {fileProblem, MAX_REPORT_BYTES, periodProblem, filterProblem, archiveUrl, archiveFileUrl, uploadLabel, createArchiveState, saveArchive, archiveRequirements} from '../src/reportArchive.js';
import {compileComponent,mount,settle,text,find,all} from './helpers/mountVue.js';
const progressComponent=await compileComponent(new URL('../src/OperationProgress.vue',import.meta.url));
const component=await compileComponent(new URL('../src/ReportArchive.vue',import.meta.url),{'./OperationProgress.vue':progressComponent});
const company={id:2,name:'Synthetic company'};

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

test('named archive requirements cover each blank field, calendar errors and only the files not already saved',()=>{
  assert.deepEqual(archiveRequirements().map(issue=>issue.label),['Название','Начало периода','Конец периода','Файл PDF','Файл Excel (.xlsx)']);
  assert.deepEqual(archiveRequirements(form,{xlsx:files.xlsx},['pdf']),[]);
  assert.deepEqual(archiveRequirements(form,{},['pdf','xlsx']),[]);
  const invalid=archiveRequirements({...form,period_start:'2026-02-30',period_end:'2026-03-01'},files);
  assert.deepEqual(invalid.map(issue=>issue.field),['period_start']);
  const reversed=archiveRequirements({...form,period_end:'2025-01-01'},files);
  assert.deepEqual(reversed.map(issue=>issue.field),['period_end']);assert.match(reversed[0].message,/раньше/);
});

test('archive progress counts confirmed metadata, each uploaded file and ready validation and keeps a failed file incomplete',async()=>{
  const backend=server(),state=createArchiveState(),stages=[],labels=[];
  backend.loseExcel();
  await assert.rejects(saveArchive(backend.api,state,{companyId:2,form,files,onStep:label=>labels.push(label),onProgress:value=>stages.push(value)}),/Failed/);
  assert.deepEqual(stages.map(value=>[value.completed,value.total]),[[0,4],[1,4],[1,4],[2,4],[2,4]]);
  assert.equal(stages.at(-1).label,'Загружаем Excel…');assert.ok(labels.includes('Загружаем PDF…'));
  const retry=[];
  const result=await saveArchive(backend.api,state,{companyId:2,form,files:{xlsx:files.xlsx},onProgress:value=>retry.push(value)});
  assert.equal(result.status,'ready');
  assert.deepEqual(retry.map(value=>[value.completed,value.total]),[[0,2],[1,2],[1,2],[2,2]]);
  assert.equal(retry.at(-1).label,'PDF и Excel готовы');
});

test('mounted archive load waits at 0% then reports confirmed completion and filters foreign company rows',async t=>{
  let resolve;
  const view=mount(component,{company,api:()=>new Promise(done=>{resolve=done;})});t.after(view.unmount);await settle();
  assert.equal(view.state.listProgress.active,true);assert.match(text(view.container),/0%/);
  resolve({can_upload:true,items:[{...draft,title:'Local record'},{...draft,id:42,company_id:9,title:'Foreign record'}]});
  await settle();
  assert.equal(view.state.listProgress.active,false);assert.match(text(view.container),/100%/);
  assert.match(text(view.container),/Local record/);assert.doesNotMatch(text(view.container),/Foreign record/);
});

test('mounted archive form names blank fields inline and retry needs only a missing Excel after a PDF is already saved',async t=>{
  const record={...draft,can_upload:true,formats:['pdf']},calls=[];
  const api=async(url,options)=>{
    if(!options)return{items:[record],can_upload:true};
    calls.push({url,...options});
    return{...record,formats:['pdf','xlsx'],status:'ready'};
  };
  const view=mount(component,{company,api});t.after(view.unmount);await settle();
  view.state.open();await settle();
  const required=find(view.container,node=>node.props?.class==='archive-requirements');
  assert.equal(all(required,node=>node.tag==='li').length,5);
  for(const label of ['Название','Начало периода','Конец периода','Файл PDF','Файл Excel (.xlsx)'])assert.match(text(required),new RegExp(label.replace(/[().]/g,'\\$&')));
  assert.equal(all(view.container,node=>node.tag==='input'&&node.props['aria-invalid']===true).length,3);
  await view.state.save();assert.equal(calls.length,0);assert.match(view.state.formError,/Название.*Начало периода.*Конец периода.*Файл PDF.*Файл Excel/);
  view.state.open(record);await settle();
  assert.deepEqual(view.state.requirements.map(issue=>issue.label),['Файл Excel (.xlsx)']);
  view.state.choose('xlsx',{target:{files:[files.xlsx],value:'chosen'}});await settle();
  assert.equal(view.state.requirements.length,0);
  await view.state.save();await settle();
  assert.equal(calls.length,1);assert.equal(calls[0].url,archiveFileUrl(record,'xlsx'));
  assert.equal(view.state.actionProgress.active,false);assert.equal(view.state.actionProgress.completed,2);assert.equal(view.state.actionProgress.total,2);
  assert.equal(view.state.editor,null);assert.match(text(view.container),/PDF и Excel сохранены/);
});

test('mounted archive deletion and restore retain scoped versions and finish progress only after checked responses',async t=>{
  let record={...draft,status:'ready',formats:['pdf','xlsx'],version:0,can_delete:true,can_upload:true};
  const calls=[];
  const view=mount(component,{company,api:async(url,options)=>{
    if(!options)return{items:[record],can_upload:true};
    calls.push({url,...options});record={...record,status:options.method==='DELETE'?'deleted':'ready',can_restore:options.method==='DELETE',can_delete:options.method!=='DELETE'};return structuredClone(record);
  }});t.after(view.unmount);await settle();
  await view.state.changeArchiveState(record);await settle();
  assert.equal(calls[0].url,'/api/report-archives/41?company_id=2&expected_version=0');
  assert.equal(calls[0].method,'DELETE');assert.equal(view.state.actionProgress.completed,1);assert.equal(view.state.actionProgress.total,1);
  await view.state.changeArchiveState(record,true);await settle();
  assert.equal(calls[1].url,'/api/report-archives/41/restore?company_id=2&expected_version=0');
  assert.equal(calls[1].method,'POST');assert.equal(view.state.actionProgress.error,'');
});
