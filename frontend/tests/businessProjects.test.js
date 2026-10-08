import {test} from 'node:test';
import assert from 'node:assert/strict';
import {reactive} from 'vue';
import {compileComponent, mount, settle, text, all, find} from './helpers/mountVue.js';
import {prepareSources, MAX_SOURCE_SIZE, MAX_FOLDER_SIZE, decimalShift, formFromInputs, inputsFromForm, resizeValues, monthLabels,
  createBusinessState, uploadProjectFolder, generateProject, projectUrl, templateUrl, fieldLabel, sourceLocation, headerProblem,
  folderProjectTitle, projectReview, uploadFailureMessage, fieldGuidance, missingRequiredFields, missingHeaderFields, originalReportFiles, NARRATIVE_FIELDS, createProjectDraft, saveProjectDraft} from '../src/businessProjects.js';

const header = {title: 'Synthetic project', start: '2027-01-01', months: 36, currency: ''};
const file = (name, size = 12, path = 'Project/' + name, value = 'contents') => ({name, size, webkitRelativePath: path, value});
const lost = () => new TypeError('Failed to fetch');
const component = await compileComponent(new URL('../src/BusinessProjects.vue', import.meta.url));
const progressComponent=await compileComponent(new URL('../src/OperationProgress.vue',import.meta.url));
const progressView=await compileComponent(new URL('../src/BusinessProjects.vue',import.meta.url),{'./OperationProgress.vue':progressComponent});
const deferred=()=>{let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail;});return{promise,resolve,reject};};
const company = {id: 2, name: 'Synthetic company'};
const button = (root, phrase) => find(root, node => node.tag === 'button' && text(node).includes(phrase));
const completeInputs = () => ({title: 'Synthetic project', start: '2027-01-01', months: 3, currency: 'USD', tax_rate: '0.15', discount_rate: '0.12',
  opening_cash: '0', opening_receivables: '0', opening_inventory: '0', opening_payables: '0', initial_investment: '1000',
  receivable_days: '0', inventory_days: '0', payable_days: '0', fixed_costs: '0', equity: '0',
  products: [{name: 'A', unit: 'pack', price: '10', unit_cost: '2', quantities: ['1', '2', '3']}], assets: [], loans: []});

test('folder trash deletes the selected company revision and exposes reversible recovery',async t=>{
  let project={id:1,company_id:2,revision:3,title:'Synthetic folder',status:'draft',can_delete:true,can_edit:true};const calls=[];
  const view=mount(component,{company,api:async(url,options={})=>{calls.push({url,...options});if(options.method){project={...project,status:options.method==='DELETE'?'deleted':'draft',can_restore:options.method==='DELETE',can_delete:options.method!=='DELETE'};return project;}return{items:[project],can_upload:true};}});t.after(view.unmount);
  await settle();assert.ok(button(view.container,'Удалить папку'));await view.state.changeProjectState(project);await settle();
  assert.equal(calls.find(call=>call.method==='DELETE').url,'/api/business-projects/1?company_id=2&expected_revision=3');
  assert.equal(button(view.container,'Восстановить'),undefined);view.state.showDeleted=true;await settle();assert.ok(button(view.container,'Восстановить'));
  await view.state.changeProjectState(project,true);await settle();assert.equal(calls.find(call=>call.method==='POST').url,'/api/business-projects/1/restore?company_id=2&expected_revision=3');assert.ok(button(view.container,'Удалить папку'));
});
test('native model hides the generic calculation form and misleading generic generation metrics',async t=>{
  const project={id:1,company_id:2,revision:1,can_edit:true,title:'Native folder',status:'needs_data',files:[],inputs:{},extraction:{},validation:[],native_model:{supported:true,parameters:[],metrics:[]},generations:[{id:1,metrics:{npv:'999'}}]};
  const view=mount(component,{company,api:async url=>url.includes('/1?')?project:{items:[project],can_upload:true}});t.after(view.unmount);await settle();await view.state.openProject(project);await settle();
  assert.equal(find(view.container,n=>n.tag==='form'),undefined);assert.doesNotMatch(text(view.container),/NPV проекта|осталось заполнить/);
});

test('original report downloads retain the supplied document and reject another company or an unrelated URL', () => {
  const doc = (id, filename) => ({id, filename, download_url: `/api/business-projects/1/sources/${id}/download?company_id=2`});
  const project = {id: 1, company_id: 2, can_edit: true, files: [doc(3, 'Лекарство_производство_ООО_UZGERMED_PHARM_36м_Долл_.xlsx'), doc(4, 'Бизнес-план.docx'), doc(5, 'UZGERMED_PHARM_36m.pdf'), doc(6, 'Договор.pdf')]};
  const before = structuredClone(project);
  assert.deepEqual(originalReportFiles(project, 2).map(file => file.format), ['Excel', 'Word', 'PDF']);
  assert.deepEqual(project, before);
  assert.deepEqual(originalReportFiles({...project, can_edit: false}, 2), []);
  assert.deepEqual(originalReportFiles(project, 9), []);
  assert.deepEqual(originalReportFiles({...project, files: [{...doc(3, 'Бизнес-план.pdf'), download_url: 'https://other.test/fake.pdf'}]}, 2), []);
});

test('mounted original template is available before missing financial inputs are completed', async t => {
  const project = {id: 1, company_id: 2, revision: 1, can_edit: true, title: 'Original folder', status: 'needs_data', generations: [], inputs: {}, extraction: {}, validation: [],
    files: [{id: 3, filename: 'Бизнес-план.xlsx', relative_path: 'Folder/Бизнес-план.xlsx', size: 100, download_url: '/api/business-projects/1/sources/3/download?company_id=2'}]};
  const view = mount(component, {company, api: async url => url.includes('/1?') ? project : {items: [project], can_upload: true}});
  t.after(view.unmount);
  await settle(); await view.state.openProject(project); await settle();
  assert.match(text(view.container), /Готовый бизнес-план из вашей папки/);
  assert.match(text(view.container), /свои разделы, таблицы, оформление, формулы и исходные суммы/);
  const download = find(view.container, node => node.tag === 'a' && text(node).includes('Скачать исходный Excel'));
  assert.equal(download.props.href, '/api/business-projects/1/sources/3/download?company_id=2');
  assert.equal(button(view.container, 'Рассчитать бизнес-план').props.disabled, false);
  await view.state.generate();await settle();
  assert.equal(view.state.validationAttempted,true);
  assert.ok(view.state.requiredFields.length);
});

test('folder validation preserves nested paths and explicitly distinguishes system junk from unsupported business files', () => {
  const result = prepareSources([file('input.XLSX', 12, 'Project/Models/input.XLSX'), file('contract.zip'), file('picture.jpg'), file('.DS_Store'), file('old.xls')]);
  assert.equal(result.files[0].relativePath, 'Project/Models/input.XLSX');
  assert.equal(result.files.length, 3);
  assert.equal(result.ignored.length, 1);
  assert.equal(result.ignored[0].name, 'Project/.DS_Store');
  assert.match(result.problems[0], /old\.xls: формат не поддерживается/);
  assert.match(prepareSources([file('model.xlsx', 12, '../model.xlsx')]).problems[0], /небезопасное/);
  assert.match(prepareSources([file('model.xlsx', 12, 'Project/model.xlsx'), file('model.xlsx')]).problems[0], /дважды/);
  assert.match(prepareSources([file('model.xlsx', MAX_SOURCE_SIZE + 1)]).problems[0], /20 МБ/);
  assert.match(prepareSources(Array.from({length: 101}, (_, index) => file(index + '.txt'))).problems[0], /100 исходных/);
  assert.match(prepareSources(Array.from({length: 6}, (_, index) => file(index + '.xlsx', MAX_FOLDER_SIZE / 5))).problems[0], /100 МБ/);
});

test('editing preserves missing values and exact decimal amounts; absent loans and assets require an explicit choice', () => {
  const form = formFromInputs({tax_rate: '0.000000000001', discount_rate: '0.125', opening_cash: '999999999999999.12', products: [{name: 'A', unit: 'pack', price: '10.5', unit_cost: '0', quantities: ['1', null, '0']}], loans: undefined});
  assert.equal(form.tax_rate, '0.0000000001');
  assert.equal(form.discount_rate, '12.5');
  const inputs = inputsFromForm(form);
  assert.equal(inputs.tax_rate, '0.000000000001');
  assert.equal(inputs.opening_cash, '999999999999999.12');
  assert.equal(inputs.opening_inventory, '');
  assert.equal(inputs.fixed_costs, '');
  assert.equal(inputs.products[0].unit_cost, '0');
  assert.deepEqual(inputs.products[0].quantities, ['1', '', '0']);
  assert.equal(inputs.products[0].capacity, null);
  assert.equal(inputs.assets, null);
  assert.equal(inputs.loans, null);
  form.no_assets = true; form.no_loans = true;
  assert.deepEqual(inputsFromForm(form).assets, []);
  assert.deepEqual(inputsFromForm(form).loans, []);
  assert.equal(decimalShift(' 1 000,25 ', -2), '10.0025');
  assert.equal(decimalShift('1e-7', 2), '0.00001');
  assert.equal(decimalShift('1e2', -2), '1');
});

test('manual project starts without files, saves missing values as a draft and exposes all six data groups and twelve narratives',async t=>{
  let project=null;const calls=[];
  const api=async(url,options)=>{
    calls.push({url,...options});
    if(!options)return{items:project?[project]:[],can_upload:true};
    const body=JSON.parse(options.body);
    if(options.method==='POST'){project={id:5,company_id:2,revision:0,title:body.title,mode:body.mode,can_edit:true,status:'draft',inputs:{},files:[],generations:[],validation:[]};return structuredClone(project);}
    assert.equal(url,projectUrl(2,5,'inputs'));
    assert.equal(body.revision,project.revision);
    project={...project,revision:project.revision+1,status:'needs_data',inputs:body.inputs,validation:[{field:'opening_cash',message:'Заполните остаток.'}]};
    return structuredClone(project);
  };
  const view=mount(component,{api,company});t.after(view.unmount);await settle();
  await button(view.container,'Новый проект').props.onClick();await settle();
  assert.equal(view.state.projectMode,'manual');assert.equal(view.state.selected.length,0);
  view.state.header.title='Manual synthetic';
  await button(view.container,'Сохранить и перейти к данным').props.onClick();await settle();
  const writes=calls.filter(call=>call.method);
  assert.equal(writes.length,2);assert.equal(JSON.parse(writes[0].body).mode,'manual');
  assert.equal(JSON.parse(writes[1].body).inputs.opening_cash,'');
  assert.equal(JSON.parse(writes[1].body).inputs.assets,null);
  assert.equal(JSON.parse(writes[1].body).inputs.loans,null);
  assert.equal(view.state.flowStage,'data');assert.equal(view.state.current.revision,1);
  const groups=all(view.container,node=>Object.hasOwn(node.props||{},'data-bp-group'));
  assert.equal(groups.length,6);
  assert.equal(all(view.container,node=>node.tag==='textarea').length,12);
  assert.ok(all(view.container,node=>node.tag==='textarea').every(node=>node.props.maxlength==='16000'));
  for(const [field] of NARRATIVE_FIELDS)view.state.form[field]='Synthetic '+field;
  view.state.form.opening_cash='0';
  await view.state.saveDraft();await settle();
  assert.equal(view.state.current.revision,2);assert.equal(view.state.form.opening_cash,'0');
  for(const [field] of NARRATIVE_FIELDS)assert.equal(view.state.form[field],'Synthetic '+field);
  assert.equal(view.state.form.opening_inventory,'');
  assert.match(text(view.container),/Черновик сохранён/);
});

test('manual generation saves canonical data first then submits the returned revision and keeps reports stale during edits',async t=>{
  const archive=id=>({id,company_id:2,status:'ready'});
  const older={id:1,revision:1,business_archive:archive(10),teo_archive:archive(11),metrics:{npv:'10'}};
  let project={id:5,company_id:2,revision:1,mode:'manual',title:'Synthetic',can_edit:true,status:'ready',inputs:completeInputs(),files:[],generations:[older],current_generation_id:1,validation:[],extraction:{}};
  const calls=[];
  const view=mount(component,{company,api:async(url,options)=>{
    if(!options)return url.includes('/5?')?structuredClone(project):{items:[project],can_upload:true};
    calls.push({url,...options});const body=JSON.parse(options.body);
    if(options.method==='PUT'){assert.equal(body.revision,1);project={...project,revision:2,status:'needs_data',inputs:body.inputs,current_generation_id:null};return structuredClone(project);}
    assert.equal(body.revision,2);project={...project,status:'ready',current_generation_id:2,generations:[{...older,id:2,revision:2,business_archive:archive(20),teo_archive:archive(21)},older]};return structuredClone(project);
  }});t.after(view.unmount);await settle();await view.state.openProject(project);await settle();
  view.state.form.opening_cash='25';view.state.form.initiator='Synthetic initiator';await settle();
  assert.match(text(view.container),/Файлы относятся к сохранённой версии/);
  await view.state.generate();await settle();
  assert.deepEqual(calls.map(call=>[call.url,call.method]),[[projectUrl(2,5,'inputs'),'PUT'],[projectUrl(2,5,'generate'),'POST']]);
  assert.equal(JSON.parse(calls[1].body).inputs.initiator,'Synthetic initiator');
  assert.equal(view.state.current.revision,2);assert.equal(view.state.flowStage,'report');
  assert.equal(view.state.latest.id,2);assert.equal(view.state.changed,false);
});

test('manual draft helper reuses an uncertain creation key and stops between save and generation after a company change',async()=>{
  const state=createBusinessState(),calls=[];let attempts=0;
  const api=async(url,options)=>{calls.push({url,...options});if(attempts++===0)throw lost();return{id:5,company_id:2,revision:0,mode:'manual',can_edit:true};};
  await assert.rejects(createProjectDraft(api,state,2,'Synthetic'),/Failed/);
  const project=await createProjectDraft(api,state,2,'Changed local title');
  assert.equal(calls[0].body,calls[1].body);assert.equal(state.createUnknown,false);
  let current=true;
  await assert.rejects(saveProjectDraft(async()=>{current=false;return project;},project,2,{opening_cash:''},()=>current),{name:'AbortError'});
  await assert.rejects(saveProjectDraft(async()=>({...project,company_id:9}),project,2,{}),/другой компании/);
});

test('monthly resizing keeps entered values and adds blanks rather than inventing zero', () => {
  assert.deepEqual(resizeValues(['1', '0'], 4), ['1', '0', '', '']);
  assert.deepEqual(resizeValues('', 2), ['', '']);
  assert.deepEqual(resizeValues('0', 2), ['0', '0']);
  assert.deepEqual(resizeValues(['1', '2', '3'], 2), ['1', '2']);
  assert.match(monthLabels('2027-12-01', 2)[1], /2028/);
  assert.equal(headerProblem({...header, start: ''}), 'Выберите первый месяц прогноза.');
  assert.match(headerProblem({...header, start: '2100-12-01', months: 2}), /декабря 2100/);
});

function backend() {
  const projects = new Map(), calls = [], sources = new Map(); let createLoss = false, sourceLoss = false;
  return {calls, projects, sources,
    loseCreate: () => {createLoss = true;}, loseSource: () => {sourceLoss = true;},
    api: async (url, options) => {
      calls.push({url, ...options});
      if (options.method === 'POST' && !url.includes('/analyse')) {
        const body = JSON.parse(options.body);
        if (!projects.has(body.request_key)) projects.set(body.request_key, {id: 1, company_id: body.company_id, title: body.title, revision: 0, can_edit: true, files: [], status: 'draft'});
        if (createLoss) {createLoss = false; throw lost();}
        return structuredClone(projects.get(body.request_key));
      }
      const project = [...projects.values()][0];
      if (options.method === 'PUT') {
        const path = decodeURIComponent(options.headers['X-Relative-Path']);
        if (sources.get(path) !== options.body.value) {
          assert.equal(new URL(url, 'https://local.test').searchParams.get('expected_revision'), String(project.revision));
          sources.set(path, options.body.value); project.revision++;
        }
        if (sourceLoss) {sourceLoss = false; throw lost();}
        return structuredClone(project);
      }
      if (url.includes('/analyse')) {project.status = 'ready'; project.generations = [{id: 1}]; return structuredClone(project);}
      throw Error('Unexpected call');
    },
  };
}

test('a lost folder create response reuses the project key; files upload sequentially with exact revision and safe path headers', async () => {
  const service = backend(), state = createBusinessState(), sources = prepareSources([file('a.xlsx', 12, 'Folder/Sub/a.xlsx'), file('b.pdf')]).files;
  service.loseCreate();
  await assert.rejects(uploadProjectFolder(service.api, state, {companyId: 2, header, sources}), /Failed/);
  assert.equal(state.createUnknown, true);
  const detail = await uploadProjectFolder(service.api, state, {companyId: 2, header, sources});
  assert.equal(service.projects.size, 1);
  assert.equal(service.calls[0].body, service.calls[1].body);
  assert.equal(detail.revision, 2);
  assert.equal(service.calls[2].headers['X-Relative-Path'], encodeURIComponent('Folder/Sub/a.xlsx'));
  assert.equal(service.calls[3].url.endsWith('&expected_revision=1'), true);
  assert.deepEqual(JSON.parse(service.calls.at(-1).body).overrides, {title: header.title, start: header.start, months: 36});
});

test('unknown source upload retries the same bytes, resumes the same folder and never skips a file by size alone', async () => {
  const service = backend(), state = createBusinessState(), sources = prepareSources([file('a.xlsx')]).files;
  service.loseSource();
  await assert.rejects(uploadProjectFolder(service.api, state, {companyId: 2, header, sources}), /Failed/);
  assert.equal(state.project.id, 1);
  assert.equal(state.uploaded.size, 0);
  const detail = await uploadProjectFolder(service.api, state, {companyId: 2, header, sources});
  assert.equal(detail.revision, 1);
  assert.equal(service.calls.filter(call => call.method === 'POST' && !call.url.includes('/analyse')).length, 1);
  assert.equal(service.calls.filter(call => call.method === 'PUT').length, 2);
  assert.equal(service.calls.filter(call => call.method === 'PUT')[0].body, service.calls.filter(call => call.method === 'PUT')[1].body);
});

test('invalid files, readonly folders and a stale revision never proceed to analysis or generation', async () => {
  const service = backend(), sources = prepareSources([file('bad.exe')]);
  await assert.rejects(uploadProjectFolder(service.api, createBusinessState(), {companyId: 2, header, sources: [{key: 'bad', file: file('bad.exe')}]}), /не поддерживается/);
  assert.equal(service.calls.length, 0);
  const readonly = {id: 1, company_id: 2, revision: 0, can_edit: false};
  await assert.rejects(uploadProjectFolder(service.api, createBusinessState(readonly), {companyId: 2, header, sources: prepareSources([file('a.xlsx')]).files}), /недоступно/);
  await assert.rejects(generateProject(service.api, readonly, 2, {}, false), /недоступно/);
  const calls = [], conflict = Object.assign(Error('Changed'), {status: 409});
  const api = async (url, options) => {calls.push({url, ...options}); throw conflict;};
  await assert.rejects(uploadProjectFolder(api, createBusinessState({...readonly, can_edit: true}), {companyId: 2, header, sources: prepareSources([file('a.xlsx')]).files}), /Changed/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'PUT');
});

test('company switch cancels the remaining writes and generate rejects mismatched company responses', async () => {
  let active = true, calls = 0;
  const api = async () => {calls++; active = false; return {id: 1, company_id: 2, revision: 0, can_edit: true};};
  await assert.rejects(uploadProjectFolder(api, createBusinessState(), {companyId: 2, header, sources: prepareSources([file('a.xlsx')]).files, isCurrent: () => active}), {name: 'AbortError'});
  assert.equal(calls, 1);
  await assert.rejects(generateProject(async () => ({id: 1, company_id: 7}), {id: 1, company_id: 2, revision: 3, can_edit: true}, 2, {}, true), /другой компании/);
});

test('explicit generation submits full canonical inputs, confirmation and current revision; missing-data detail is returned for editing', async () => {
  let sent;
  const project = {id: 1, company_id: 2, revision: 4, can_edit: true};
  const inputs = inputsFromForm(formFromInputs({title: 'Project', assets: [], loans: []}));
  const detail = await generateProject(async (url, options) => {sent = {url, body: JSON.parse(options.body)}; return {...project, inputs, status: 'needs_data', validation: [{field: 'opening_cash', message: 'Missing'}]};}, project, 2, inputs, true);
  assert.equal(sent.url, projectUrl(2, 1, 'generate'));
  assert.deepEqual(sent.body, {revision: 4, inputs, confirm_sources: true});
  assert.equal(detail.validation.length, 1);
  assert.equal(detail.inputs.opening_cash, '');
  assert.equal(templateUrl(2), '/api/business-projects/template.xlsx?company_id=2');
  assert.equal(fieldLabel('products[0].quantities[2]'), 'Продукция №1 · Объём реализации · месяц 3');
  assert.equal(sourceLocation({filename: 'a.xlsx', sheet: 'Input', cell: 'B2'}), 'a.xlsx · лист «Input» · B2');
});

test('mounted optional original import keeps the 36-month choice and exposes business PDF, one Excel and secondary TEO', async t => {
  const service = backend(); let generated = 0;
  const archive = id => ({id, company_id: 2, status: 'ready', formats: ['pdf', 'xlsx']});
  const api = async (url, options) => {
    if (!options) return {items: [], can_upload: true};
    const response = await service.api(url, options);
    return url.includes('/analyse') ? {...response, inputs: completeInputs(), validation: [], extraction: {}, generations: [{id: 1, revision: response.revision, business_archive: archive(10), teo_archive: archive(11), metrics: {npv: '12.3'}}]} : response;
  };
  const view = mount(component, {api, company, onGenerated: () => generated++}); t.after(view.unmount);
  await settle(); await button(view.container, 'Новый проект').props.onClick(); await settle();
  assert.equal(view.state.projectMode, 'manual');
  await button(view.container, 'Импорт оригинальных Word и Excel').props.onClick(); await settle();
  assert.equal(view.state.header.months, 36);
  assert.equal(view.state.header.currency, '');
  assert.ok(find(view.container, node => node.tag === 'input' && 'webkitdirectory' in node.props));
  view.state.header.start = '2027-01-01'; view.state.header.title = header.title;
  view.state.chooseSources({target: {files: [file('a.xlsx')], value: 'selected'}});
  await view.state.upload(); await settle();
  assert.equal(generated, 1);
  const downloads = all(view.container, node => node.tag === 'a' && node.props.href?.includes('/files/'));
  assert.equal(downloads.length, 3);
  assert.deepEqual(downloads.map(node => node.props.href), ['/api/report-archives/10/files/pdf?company_id=2', '/api/report-archives/10/files/xlsx?company_id=2', '/api/report-archives/11/files/pdf?company_id=2']);
  const teo = find(view.container,node=>node.tag==='details'&&node.children.some(child=>child.tag==='summary'&&text(child).includes('Дополнительно')));
  assert.notEqual(teo.props.open,true);
  assert.match(text(view.container), /Бизнес-план и ТЭО сформированы/);
});

test('mounted missing-data response keeps submitted financial values; source ambiguity requires review', async t => {
  let posts = 0;
  const project = {id: 1, company_id: 2, revision: 3, can_edit: true, title: header.title, status: 'needs_data', files: [], generations: [], inputs: completeInputs(),
    extraction: {issues: [{field: 'opening_cash', message: 'Проверьте исходный остаток.', requires_confirmation: true}]}, validation: []};
  const view = mount(component, {company, api: async (url, options) => {
    if (!options) return url.includes('/1?') ? structuredClone(project) : {items: [project], can_upload: true};
    posts++; const body = JSON.parse(options.body);
    return {...project, inputs: body.inputs, validation: [{field: 'tax_rate', message: 'Заполните налог.'}]};
  }}); t.after(view.unmount);
  await settle(); await view.state.openProject(project); await settle();
  view.state.form.opening_cash = '31';
  await view.state.generate(); assert.equal(posts, 0);
  view.state.confirmed = true; await view.state.generate(); await settle();
  assert.equal(posts, 2);
  assert.equal(view.state.form.opening_cash, '31');
  assert.match(text(view.container), /Заполните налог/);
  assert.ok(!text(view.container).includes('Бизнес-план и ТЭО сформированы из одной финансовой модели.'));
});

test('mounted source revision conflict preserves local edits; readonly projects provide no financial editor', async t => {
  const project = {id: 1, company_id: 2, revision: 3, can_edit: true, title: header.title, status: 'needs_data', files: [], generations: [], inputs: completeInputs(), extraction: {}, validation: []};
  let latest = structuredClone(project);
  const view = mount(component, {company, api: async (url, options) => {
    if (options) {latest.revision = 4; latest.inputs.opening_cash = '999'; throw Object.assign(Error('Changed'), {status: 409});}
    return url.includes('/1?') ? structuredClone(latest) : {items: [project], can_upload: true};
  }}); t.after(view.unmount);
  await settle(); await view.state.openProject(project); view.state.form.opening_cash = '321';
  await view.state.generate(); await settle();
  assert.equal(view.state.current.revision, 4);
  assert.equal(view.state.form.opening_cash, '321');
  assert.match(text(view.container), /ваши изменения формы сохранены/);
  view.state.form.months = 5; await settle();
  assert.deepEqual([...view.state.form.products[0].quantities], ['1', '2', '3']);
  view.state.makeMonthly(view.state.scheduleEntries.find(entry => entry.key === 'q0'));
  assert.deepEqual([...view.state.form.products[0].quantities], ['1', '2', '3', '', '']);
  const readonly = {...project, can_edit: false, inputs: {}};
  const viewer = mount(component, {company, api: async url => url.includes('/1?') ? readonly : {items: [readonly], can_upload: false}}); t.after(viewer.unmount);
  await settle(); await viewer.state.openProject(readonly); await settle();
  assert.equal(button(viewer.container, 'Рассчитать бизнес-план'), undefined);
  assert.equal(all(viewer.container, node => node.tag === 'input' && node.props.type === 'file').length, 0);
  assert.equal(all(viewer.container, node => node.tag === 'fieldset').length, 0);
});

test('mounted reports follow the current calculation after restoring older parameters', async t => {
  const archive = id => ({id, company_id: 2, status: 'ready', formats: ['pdf', 'xlsx']});
  const generation = (id, npv) => ({id, revision: 3, created_at: '2027-01-01T00:00:00Z',
    business_archive: archive(id * 10), teo_archive: archive(id * 10 + 1), metrics: {npv}});
  const project = {id: 1, company_id: 2, revision: 3, current_generation_id: 1, can_edit: true,
    title: header.title, status: 'ready', files: [], inputs: completeInputs(), validation: [],
    extraction: {issues: [{code: 'missing_or_invalid', requires_confirmation: true}]},
    generations: [generation(2, '200'), generation(1, '100')]};
  const view = mount(component, {company, api: async url => url.includes('/1?') ? project : {items: [project], can_upload: true}});
  t.after(view.unmount);
  await settle(); await view.state.openProject(project); await settle();
  assert.equal(view.state.latest.id, 1);
  assert.equal(view.state.needsConfirmation, false);
  const downloads = all(view.container, node => node.tag === 'a' && node.props.href?.includes('/files/'));
  assert.equal(downloads[0].props.href, '/api/report-archives/10/files/pdf?company_id=2');
});

test('folder title is suggested only from a common selected folder and preflight errors never claim files were uploaded', async t => {
  assert.equal(folderProjectTitle(prepareSources([file('a.xlsx', 12, 'Sample Folder/a.xlsx'), file('b.pdf', 12, 'Sample Folder/sub/b.pdf')]).files), 'Sample Folder');
  assert.equal(folderProjectTitle([{relativePath: 'a.xlsx'}]), '');
  assert.equal(folderProjectTitle([{relativePath: 'One/a.xlsx'}, {relativePath: 'Two/b.pdf'}]), '');
  const service = backend();
  const api = async (url, options) => options ? service.api(url, options) : {items: [], can_upload: true};
  const view = mount(component, {company, api}); t.after(view.unmount);
  await settle(); view.state.newProject('files');
  view.state.chooseSources({target: {files: [file('a.xlsx', 12, 'Sample Folder/a.xlsx')], value: 'selected'}});
  assert.equal(view.state.header.title, 'Sample Folder');
  assert.equal(view.state.header.start, '');
  await view.state.upload(); await settle();
  assert.equal(service.calls.length, 0);
  assert.equal(view.state.error, 'Выберите первый месяц прогноза.');
  assert.doesNotMatch(view.state.error, /файлы.*(?:остаются|сохранены)/i);
  view.state.header.title = 'Chosen title';
  view.state.chooseSources({target: {files: [file('b.pdf', 12, 'Other Folder/b.pdf')], value: 'selected'}});
  assert.equal(view.state.header.title, 'Chosen title');
  const pending = createBusinessState(); pending.sourceUploadStarted = true;
  assert.doesNotMatch(uploadFailureMessage(lost(), pending), /файлы.*(?:остаются|сохранены)/i);
  pending.uploaded.add('source');
  assert.match(uploadFailureMessage(lost(), pending), /Уже загруженные файлы сохранены/);
});

test('a forty-product source response keeps nine missing fields first, deduplicates history and collapses document notes', async t => {
  const missing = ['opening_cash', 'opening_receivables', 'opening_inventory', 'opening_payables', 'initial_investment', 'fixed_costs', 'equity', 'assets', 'loans'];
  const inputs = {...completeInputs(), months: 36, products: Array.from({length: 40}, (_, index) => ({name: `Sample product ${index + 1}`, unit: 'pack', price: '10', unit_cost: '2', quantities: Array(36).fill('5')}))};
  for (const field of missing) delete inputs[field];
  const checks = [{field: 'products.price', code: 'price_factor_vat', message: 'Подтвердите коэффициент и НДС.', requires_confirmation: true},
    {field: 'products.quantities', code: 'volume_policy', message: 'Подтвердите объёмы.', requires_confirmation: true},
    {field: 'fixed_costs', code: 'overhead_scope', message: 'Подтвердите состав расходов.', requires_confirmation: true}];
  const extraction = {issues: [
    ...Array.from({length: 4}, () => missing.map(field => ({field, code: 'missing_or_invalid', message: 'Историческое поле не заполнено.', requires_confirmation: true}))).flat(),
    ...Array.from({length: 5}, () => checks).flat(),
    ...Array.from({length: 12}, (_, index) => ({field: 'documents', code: 'pdf_financial_reconciliation', message: 'PDF используется как справочный источник.', requires_confirmation: false, source: {filename: `source-${index + 1}.pdf`}})),
    ...Array.from({length: 7}, (_, index) => ({field: 'documents', code: 'supporting_workbook', message: 'Таблица используется для сверки.', requires_confirmation: false, source: {filename: `sheet-${index + 1}.xlsx`}})),
  ], evidence: [], narratives: []};
  const validation = [...missing.map(field => ({field, message: 'Заполните значение; пустое поле не равно нулю.'})),
    ...checks.map(issue => ({field: issue.field, message: issue.message, code: 'source_issues', source_code: issue.code}))];
  const project = {id: 1, company_id: 2, revision: 22, can_edit: true, title: 'Sample folder', status: 'needs_data', files: [], generations: [], inputs, extraction, validation};
  const grouped = projectReview(extraction, validation);
  assert.equal(grouped.fields.length, 9);
  assert.equal(grouped.checks.length, 3);
  assert.equal(grouped.references.length, 2);
  assert.equal(grouped.references[0].sources.length, 12);
  const view = mount(component, {company, api: async url => url.includes('/1?') ? structuredClone(project) : {items: [project], can_upload: true}});
  t.after(view.unmount);
  await settle(); await view.state.openProject(project); await settle();
  assert.equal(view.state.validation.length, 9);
  assert.equal(view.state.sourceProblems.length, 3);
  assert.equal(view.state.needsConfirmation, true);
  const requiredGuide = find(view.container, node => node.tag === 'section' && node.props.class === 'bp-required-guide');
  assert.equal(all(requiredGuide, node => node.tag === 'article').length, 9);
  assert.equal(view.state.requiredFields.length, 9);
  assert.equal(button(view.container, 'Рассчитать бизнес-план').props.disabled, true);
  assert.doesNotMatch(text(view.container), /Историческое поле не заполнено/);
  const products = find(view.container, node => node.tag === 'details' && String(node.props.class || '').includes('bp-products'));
  assert.equal(products.props.open, false);
  const notes = find(view.container, node => node.tag === 'details' && node.children.some(child => child.tag === 'summary' && text(child).includes('Справочные заметки')));
  assert.notEqual(notes.props.open, true);
  const content = text(view.container), productPosition = content.indexOf('5. Продукция, производство и рынок · 40');
  assert.ok(content.indexOf('2. Расходы и ресурсы') < productPosition);
  assert.ok(content.indexOf('Основные средства · 0') < productPosition);
  assert.ok(content.indexOf('Кредиты · 0') < productPosition);
  assert.equal(view.state.form.fixed_costs, '');
  assert.equal(view.state.form.equity, '');
  assert.equal(view.state.form.no_assets, false);
  assert.equal(view.state.form.no_loans, false);
  assert.deepEqual([...view.state.form.products[39].quantities], Array(36).fill('5'));
  for (const field of missing.filter(field => !['assets', 'loans'].includes(field))) view.state.form[field] = '0';
  view.state.form.no_assets = true; view.state.form.no_loans = true;
  await settle();
  assert.equal(view.state.requiredFields.length, 0);
  assert.equal(view.state.remainingRequirements.length, 0);
  assert.equal(all(requiredGuide, node => node.tag === 'article').length, 0);
  assert.equal(view.state.savedValidation.length, 0);
  assert.equal(button(view.container, 'Рассчитать бизнес-план').props.disabled, true);
  view.state.confirmed = true; await settle();
  assert.equal(button(view.container, 'Рассчитать бизнес-план').props.disabled, false);
  assert.match(text(requiredGuide), /Обязательные поля заполнены/);
  const confirmationOnly = projectReview({}, [{field: 'sources', code: 'source_issues', message: 'Проверьте источники.'}]);
  assert.equal(confirmationOnly.fields.length, 0);
  assert.equal(confirmationOnly.checks[0].requires_confirmation, true);
});

test('fatal or blocking source errors stay in required checks even without the confirmation flag', async t => {
  for (const flags of [{severity: 'error'}, {severity: 'fatal'}, {severity: 'blocking'}, {blocking: true}]) {
    const grouped = projectReview({issues: [{field: 'documents', code: 'parse_error', message: 'Не удалось прочитать источник.', requires_confirmation: false, ...flags}]});
    assert.equal(grouped.checks.length, 1);
    assert.equal(grouped.checks[0].requires_confirmation, true);
    assert.equal(grouped.references.length, 0);
  }
  const fatal = {field: 'documents', code: 'parse_error', severity: 'fatal', message: 'Не удалось прочитать источник.', requires_confirmation: false};
  const reference = {field: 'documents', code: 'narrative_reference', message: 'Справочный текст сохранён.', requires_confirmation: false};
  const project = {id: 1, company_id: 2, revision: 1, can_edit: true, title: 'Sample project', status: 'needs_data', files: [], generations: [], inputs: completeInputs(),
    extraction: {issues: [fatal, reference]}, validation: [{field: fatal.field, message: fatal.message, code: 'source_issues', source_code: fatal.code}]};
  const view = mount(component, {company, api: async url => url.includes('/1?') ? structuredClone(project) : {items: [project], can_upload: true}});
  t.after(view.unmount);
  await settle(); await view.state.openProject(project); await settle();
  assert.equal(view.state.validation.length, 0);
  assert.equal(view.state.sourceProblems.length, 1);
  assert.equal(view.state.referenceNotes.length, 1);
  assert.equal(view.state.needsConfirmation, true);
  assert.equal(button(view.container, 'Рассчитать бизнес-план').props.disabled, true);
  assert.match(text(find(view.container, node => node.tag === 'div' && node.props.class === 'bp-review')), /Не удалось прочитать источник/);
});

test('mandatory-field guidance explains the source and action including truthful absent-assets and absent-loans choices', () => {
  const fields = ['title', 'start', 'months', 'currency', 'tax_rate', 'discount_rate', 'opening_cash', 'opening_receivables', 'opening_inventory', 'opening_payables',
    'initial_investment', 'fixed_costs', 'equity', 'receivable_days', 'inventory_days', 'payable_days', 'products', 'assets', 'loans',
    'products[0].name', 'products[0].unit', 'products[0].price', 'products[0].unit_cost', 'products[0].quantities[2]', 'products[0].capacity',
    'assets[0].name', 'assets[0].value', 'assets[0].life_months', 'assets[0].commissioning_month',
    'loans[0].name', 'loans[0].opening_balance', 'loans[0].drawdowns[1]', 'loans[0].principal', 'loans[0].interest'];
  for (const field of fields) {
    const guide = fieldGuidance(field);
    for (const key of ['label', 'what', 'source', 'how']) assert.ok(guide[key]?.trim(), `${field}.${key}`);
    assert.doesNotMatch(Object.values(guide).join(' '), /\[\]|JSON/);
  }
  assert.match(fieldGuidance('opening_cash').source, /E46.*320/);
  assert.match(fieldGuidance('opening_payables').source, /E81.*пустой/);
  assert.match(fieldGuidance('currency').how, /тысячи сумов/);
  assert.match(fieldGuidance('assets').zero, /только при их действительном отсутствии/);
  assert.match(fieldGuidance('loans').zero, /только если.*действительно нет кредитов/);
  assert.equal(fieldGuidance('products[0].quantities[2]').label, 'Продукция №1 · Объём реализации · месяц 3');
});

test('forty-product current-form requirements change from nine to zero only after explicit user entries and choices', () => {
  const missing = ['opening_cash', 'opening_receivables', 'opening_inventory', 'opening_payables', 'initial_investment', 'fixed_costs', 'equity', 'assets', 'loans'];
  const inputs = {...completeInputs(), months: 36, products: Array.from({length: 40}, (_, index) => ({name: `Sample ${index + 1}`, unit: 'pack', price: '10', unit_cost: '2', quantities: Array(36).fill('5')}))};
  for (const field of missing) delete inputs[field];
  const form = formFromInputs(inputs), before = JSON.stringify(form);
  assert.deepEqual(missingRequiredFields(form).map(item => item.field).sort(), [...missing].sort());
  assert.equal(JSON.stringify(form), before);
  for (const field of missing.filter(field => !['assets', 'loans'].includes(field))) form[field] = '0';
  assert.deepEqual(missingRequiredFields(form).map(item => item.field).sort(), ['assets', 'loans']);
  form.no_assets = true; form.no_loans = true;
  assert.deepEqual(missingRequiredFields(form), []);
  form.opening_cash = '   ';
  assert.deepEqual(missingRequiredFields(form), [{field: 'opening_cash'}]);
  form.opening_cash = '0'; form.no_loans = false;
  assert.deepEqual(missingRequiredFields(form), [{field: 'loans'}]);
  assert.deepEqual(form.products[39].quantities, Array(36).fill('5'));
  assert.equal(form.start, inputs.start);
});

test('current requirements identify missing row values, missing monthly items and mismatched schedule length by field path', () => {
  const form = formFromInputs(completeInputs());
  form.products[0].price = '';
  form.products[0].quantities = ['0', ' ', null];
  form.products[0].capacity = '';
  form.fixed_costs = ['0', '0'];
  form.equity = ['0', '0', '0'];
  form.assets = [{name: 'Sample asset', value: '', life_months: '', commissioning_month: '0'}];
  form.loans = [{name: 'Sample loan', opening_balance: '0', drawdowns: ['0', '', '0'], principal: '0', interest: ''}];
  const actual = missingRequiredFields(form).map(item => item.field).sort();
  assert.deepEqual(actual, ['products[0].price', 'products[0].quantities[1]', 'products[0].quantities[2]', 'products[0].capacity', 'fixed_costs',
    'assets[0].value', 'assets[0].life_months', 'loans[0].drawdowns[1]', 'loans[0].interest'].sort());
  form.products[0].quantities = new Array(3);
  assert.deepEqual(missingRequiredFields(form).filter(item => item.field.includes('.quantities')).map(item => item.field),
    ['products[0].quantities[0]', 'products[0].quantities[1]', 'products[0].quantities[2]']);
});

test('current requirements check absence without changing amounts or duplicating backend numeric and range validation', () => {
  const form = formFromInputs(completeInputs());
  form.products[0].price = '0.12345678901234567';
  form.fixed_costs = '-1';
  form.opening_cash = '0';
  const before = JSON.stringify(form);
  assert.deepEqual(missingRequiredFields(form), []);
  assert.equal(JSON.stringify(form), before);
  const blank = formFromInputs();
  assert.ok(missingRequiredFields(blank).some(item => item.field === 'start'));
  assert.equal(blank.start, '');
  assert.equal(blank.opening_cash, '');
  assert.equal(blank.no_assets, false);
  assert.equal(blank.no_loans, false);
});

test('project headers name missing fields and create/save progress advances only on confirmed responses',async t=>{
  assert.deepEqual(missingHeaderFields({title:'',start:'',months:36,currency:''}).map(item=>item.label),['Название проекта','Начало прогноза','Валюта модели']);
  assert.deepEqual(missingHeaderFields({title:'A',start:'',months:36,currency:''},'files').map(item=>item.field),['start']);
  assert.deepEqual(missingHeaderFields({title:'A'},'files',true),[]);
  const create=deferred(),save=deferred();let sent;
  const project={id:10,company_id:2,revision:0,title:'Draft',mode:'manual',can_edit:true,inputs:{title:'Draft'},extraction:{},validation:[],generations:[],files:[]};
  const view=mount(progressView,{company,api:async(url,options)=>{if(!options)return{items:[],can_upload:true};if(options.method==='POST')return create.promise;sent=JSON.parse(options.body).inputs;return save.promise;}});t.after(view.unmount);
  await settle();view.state.newProject();await settle();assert.match(text(view.container),/Не заполнены поля проекта/);assert.match(text(view.container),/Валюта модели/);
  view.state.header.title='Draft';const pending=view.state.saveDraft('data');await settle();
  assert.equal(view.state.progress.completed,0);assert.equal(view.state.progress.total,2);assert.equal(find(view.container,n=>n.props?.role==='progressbar').props['aria-valuenow'],0);
  create.resolve(project);await settle();assert.equal(view.state.progress.completed,1);assert.equal(find(view.container,n=>n.props?.role==='progressbar').props['aria-valuenow'],50);assert.equal(sent.start,'');assert.equal(sent.currency,'');
  save.resolve({...project,revision:1,inputs:sent});await pending;await settle();assert.equal(view.state.progress.completed,2);assert.equal(view.state.progress.active,false);assert.equal(view.state.flowStage,'data');
});

test('missing summaries identify each row and month; attempts expose accessible red helpers while drafts stay saveable',async t=>{
  const inputs=completeInputs();inputs.products=[{name:'A',unit:'pack',price:'',unit_cost:'0',quantities:['','0','1']},{name:'B',unit:'pack',price:'',unit_cost:'0',quantities:'0'}];
  let project={id:11,company_id:2,revision:0,title:inputs.title,mode:'manual',can_edit:true,inputs,extraction:{},validation:[],generations:[],files:[]};const calls=[];
  const view=mount(component,{company,api:async(url,options)=>{calls.push({url,options});if(options){project={...project,revision:project.revision+1,inputs:JSON.parse(options.body).inputs};return project;}return url.includes('/11?')?project:{items:[project],can_upload:true};}});t.after(view.unmount);
  await settle();await view.state.openProject(project);view.state.flowStage='report';await settle();
  const summary=find(view.container,n=>n.props?.['aria-labelledby']==='bp-missing-title');assert.equal(summary.props.hidden,undefined);assert.match(text(summary),/Продукция №1/);assert.match(text(summary),/Продукция №2/);assert.match(text(summary),/месяц 1/);
  const price=()=>find(view.container,n=>n.tag==='label'&&n.props?.['data-bp-field']==='products[0].price');
  assert.equal(find(price(),n=>n.tag==='input').props['aria-invalid'],false);const previous=calls.length;
  await view.state.generate();await settle();assert.equal(calls.length,previous);assert.equal(view.state.flowStage,'data');
  assert.match(price().props.class,/bp-field-error/);const input=find(price(),n=>n.tag==='input');assert.equal(input.props['aria-invalid'],true);const helper=find(price(),n=>n.props?.id===input.props['aria-describedby']);assert.match(text(helper),/Цена/);
  view.state.form.products[0].price='0';await settle();assert.equal(find(price(),n=>n.tag==='input').props['aria-invalid'],false);
  await view.state.saveDraft();const saved=calls.find(call=>call.options?.method==='PUT');assert.equal(JSON.parse(saved.options.body).inputs.products[0].quantities[0],'');
});

test('field navigation opens its data group and focuses the actual input',async t=>{
  const project={id:12,company_id:2,revision:0,title:'A',mode:'manual',can_edit:true,inputs:completeInputs(),extraction:{},validation:[],generations:[],files:[]};
  const view=mount(component,{company,api:async url=>url.includes('/12?')?project:{items:[project],can_upload:true}});t.after(view.unmount);await settle();await view.state.openProject(project);await settle();
  const group={tagName:'DETAILS',open:false,parentElement:null};let focused=false,scrolled=false;
  const control={focus:()=>{focused=true;}};const target={tagName:'LABEL',parentElement:group,querySelector:tag=>tag==='input'?control:null,scrollIntoView:()=>{scrolled=true;}};
  view.state.modelForm={querySelector:()=>target};await view.state.goToField('products[0].price');assert.equal(group.open,true);assert.equal(focused,true);assert.equal(scrolled,true);
});

test('save plus generation shows actual zero/fifty/complete stages and a failed export stays at fifty',async t=>{
  for(const fail of [false,true]){
    const save=deferred(),generate=deferred();const project={id:13,company_id:2,revision:0,title:'A',mode:'manual',can_edit:true,inputs:completeInputs(),extraction:{},validation:[],generations:[],files:[]};
    const view=mount(progressView,{company,api:async(url,options)=>options?(options.method==='PUT'?save.promise:generate.promise):url.includes('/13?')?project:{items:[project],can_upload:true}});t.after(view.unmount);await settle();await view.state.openProject(project);await settle();const pending=view.state.generate();await settle();
    assert.equal(view.state.progress.completed,0);assert.equal(view.state.progress.total,2);await settle();assert.equal(view.state.progress.completed,0);
    save.resolve({...project,revision:1});await settle();assert.equal(view.state.progress.completed,1);assert.equal(find(view.container,n=>n.props?.role==='progressbar').props['aria-valuenow'],50);
    if(fail)generate.reject(Error('Export unavailable'));else generate.resolve({...project,revision:2,status:'ready'});await pending;await settle();
    assert.equal(view.state.progress.active,false);assert.equal(view.state.progress.completed,fail?1:2);assert.equal(find(view.container,n=>n.props?.role==='progressbar').props['aria-valuenow'],fail?50:100);if(fail)assert.match(view.state.progress.error,/Export unavailable/);
  }
});

test('folder progress counts confirmed files and analysis; stale company responses do not advance',async()=>{
  const sources=prepareSources([file('a.txt'),file('b.txt')]).files;const files=[deferred(),deferred()],analyse=deferred(),events=[];
  const project={id:14,company_id:2,revision:0,can_edit:true,mode:'files'};const state=createBusinessState(project);let index=0;
  const pending=uploadProjectFolder(async url=>url.includes('/analyse?')?analyse.promise:files[index++].promise,state,{companyId:2,header,sources,onProgress:value=>events.push(value)});await settle();assert.equal(events.at(-1).completed,0);assert.equal(events.at(-1).total,3);
  files[0].resolve({...project,revision:1});await settle();assert.equal(events.at(-1).completed,1);files[1].resolve({...project,revision:2});await settle();assert.equal(events.at(-1).completed,2);analyse.resolve({...project,revision:3,status:'needs_data'});await pending;assert.equal(events.at(-1).completed,3);
  const late=deferred(),staleEvents=[];let current=true;const stale=uploadProjectFolder(async()=>late.promise,createBusinessState(project),{companyId:2,header,sources:sources.slice(0,1),isCurrent:()=>current,onProgress:value=>staleEvents.push(value)});current=false;late.resolve({...project,revision:1});await assert.rejects(stale,{name:'AbortError'});assert.equal(staleEvents.at(-1).completed,0);
});

test('company switches and unmount invalidate manual operation progress and prevent subsequent generation',async t=>{
  for(const unmount of [false,true]){
    const save=deferred(),activeCompany=reactive({...company}),calls=[];const project={id:15,company_id:2,revision:0,title:'A',mode:'manual',can_edit:true,inputs:completeInputs(),extraction:{},validation:[],generations:[],files:[]};
    const view=mount(progressView,{company:activeCompany,api:async(url,options)=>{calls.push({url,options});if(options)return save.promise;return url.includes('/15?')?project:{items:[],can_upload:true};}});if(!unmount)t.after(view.unmount);
    await settle();await view.state.openProject(project);await settle();const pending=view.state.generate();await settle();if(unmount)view.unmount();else activeCompany.id=9;await settle();save.resolve({...project,revision:1});await pending;await settle();
    assert.equal(calls.filter(call=>call.url.includes('/generate?')).length,0);assert.notEqual(view.state.progress.completed,2);assert.equal(view.state.error,'');
  }
});
