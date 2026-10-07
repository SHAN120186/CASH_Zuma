import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent, mount, settle, text, all, find} from './helpers/mountVue.js';
import {prepareSources, MAX_SOURCE_SIZE, MAX_FOLDER_SIZE, decimalShift, formFromInputs, inputsFromForm, resizeValues, monthLabels,
  createBusinessState, uploadProjectFolder, generateProject, projectUrl, templateUrl, fieldLabel, sourceLocation, headerProblem} from '../src/businessProjects.js';

const header = {title: 'Synthetic project', start: '2027-01-01', months: 36, currency: ''};
const file = (name, size = 12, path = 'Project/' + name, value = 'contents') => ({name, size, webkitRelativePath: path, value});
const lost = () => new TypeError('Failed to fetch');
const component = await compileComponent(new URL('../src/BusinessProjects.vue', import.meta.url));
const company = {id: 2, name: 'Synthetic company'};
const button = (root, phrase) => find(root, node => node.tag === 'button' && text(node).includes(phrase));
const completeInputs = () => ({title: 'Synthetic project', start: '2027-01-01', months: 3, currency: 'USD', tax_rate: '0.15', discount_rate: '0.12',
  opening_cash: '0', opening_receivables: '0', opening_inventory: '0', opening_payables: '0', initial_investment: '1000',
  receivable_days: '0', inventory_days: '0', payable_days: '0', fixed_costs: '0', equity: '0',
  products: [{name: 'A', unit: 'pack', price: '10', unit_cost: '2', quantities: ['1', '2', '3']}], assets: [], loans: []});

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

test('mounted folder chooser exposes the 36-month choice and generates all four protected download links', async t => {
  const service = backend(); let generated = 0;
  const archive = id => ({id, company_id: 2, status: 'ready', formats: ['pdf', 'xlsx']});
  const api = async (url, options) => {
    if (!options) return {items: [], can_upload: true};
    const response = await service.api(url, options);
    return url.includes('/analyse') ? {...response, inputs: completeInputs(), validation: [], extraction: {}, generations: [{id: 1, revision: response.revision, business_archive: archive(10), teo_archive: archive(11), metrics: {npv: '12.3'}}]} : response;
  };
  const view = mount(component, {api, company, onGenerated: () => generated++}); t.after(view.unmount);
  await settle(); await button(view.container, 'Новая папка проекта').props.onClick(); await settle();
  assert.equal(view.state.header.months, 36);
  assert.equal(view.state.header.currency, '');
  assert.ok(find(view.container, node => node.tag === 'input' && 'webkitdirectory' in node.props));
  view.state.header.start = '2027-01-01'; view.state.header.title = header.title;
  view.state.chooseSources({target: {files: [file('a.xlsx')], value: 'selected'}});
  await view.state.upload(); await settle();
  assert.equal(generated, 1);
  const downloads = all(view.container, node => node.tag === 'a' && node.props.href?.includes('/files/'));
  assert.equal(downloads.length, 4);
  assert.deepEqual(downloads.map(node => node.props.href), ['/api/report-archives/10/files/pdf?company_id=2', '/api/report-archives/10/files/xlsx?company_id=2', '/api/report-archives/11/files/pdf?company_id=2', '/api/report-archives/11/files/xlsx?company_id=2']);
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
  assert.equal(posts, 1);
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
