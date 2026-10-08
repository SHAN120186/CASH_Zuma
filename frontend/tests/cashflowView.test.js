import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent, mount, settle, text, find, all} from './helpers/mountVue.js';
import {planCompleteness, reportDateForYear, validReportDate} from '../src/cashflow/planView.js';

const reportComponent = await compileComponent(new URL('../src/CashFlowReport.vue', import.meta.url));
const appComponent = await compileComponent(new URL('../src/App.vue', import.meta.url), {'./CashFlowReport.vue': reportComponent});
const zero = () => Array(12).fill('0.00'), empty = () => Array(12).fill(null);
function report({year = 2026, currency = 'UZS', scenario = 'A', completeZero = false} = {}) {
  const plans = () => completeZero ? zero() : empty();
  return {company_id: 2, currency, year, scenario, as_of: `${year}-${year === 2026 ? '10-06' : '12-31'}`, notes: {},
    rows: [
      {key:'7:out', category_id:7, category:'Упаковка и материалы', kind:'out', activity:'operating', values:zero(), plan:plans()},
      {key:'8:out', category_id:8, category:'Коммунальные услуги', kind:'out', activity:'operating', values:zero(), plan:plans()},
      {key:'9:in', category_id:9, category:'Продажи', kind:'in', activity:'operating', values:zero(), plan:plans()},
    ],
    groups:[{activity:'operating', name:'Операционная деятельность', values:zero(), plan:plans()}],
    totals:zero(), plan_totals:plans(), opening:zero(), closing:zero(), opening_adjustments:zero(),
    plan_opening:plans(), plan_closing:plans(), plan_versions:Array(12).fill(3), plan_opening_input:empty()};
}
function mountReport(extra = {}) {
  return mount(reportComponent, {report:report(), currency:'UZS', year:2026, api:async () => {}, canPlan:true,
    companies:[{id:2, name:'Zuma'}], companyId:2, scenario:'A', asOf:'2026-10-06', today:'2026-10-06', ...extra});
}
const button = (root, label) => find(root, node => node.tag === 'button' && text(node) === label);

test('mounted Cash Flow reveals the cut-off month and keeps the right-edge cue for hidden months', async t => {
  const view = mountReport(); t.after(view.unmount);
  const wrap = find(view.container, node => node.props?.['aria-label'] === 'Cash Flow по месяцам');
  Object.assign(wrap, {layout:{left:100, width:638, height:300}, clientLeft:1, clientTop:1,
    clientWidth:620, clientHeight:282, offsetWidth:639, offsetHeight:300, scrollWidth:1600});
  const headers = all(wrap, node => node.tag === 'th' && 'data-m' in node.props);
  assert.equal(headers.length, 12);
  for (const header of headers) header.layout = {left:221 + Number(header.props['data-m']) * 120, width:120, height:32};
  assert.equal(wrap.querySelector('th[data-m="9"]'), headers[9], 'selector finds the rendered October header');
  await settle();
  const visibleRight = wrap.getBoundingClientRect().left + wrap.clientLeft + wrap.clientWidth;
  assert.equal(wrap.scrollLeft, 732);
  assert.equal(headers[9].getBoundingClientRect().right, visibleRight - 32);
  assert.equal(view.state.moreRight, true);
  assert.deepEqual(view.state.bars, {x:17, y:16});
  const frame = find(view.container, node => node.props?.class?.includes?.('cf-frame'));
  assert.match(frame.props.class, /more/);
  assert.equal(frame.props.style['--cf-bar-x'], '17px');
  view.app._instance.props.asOf = '2026-03-15'; await settle();
  assert.equal(wrap.scrollLeft, 0, 'earlier month returns to the leftmost visible position');
  assert.equal(headers[2].getBoundingClientRect().right, 581);
  view.state.setPer('q1');
  view.app._instance.props.asOf = '2026-10-06'; await settle();
  assert.deepEqual(view.state.indices, [0,1,2]);
  assert.equal(wrap.querySelector('th[data-m="9"]'), null, 'the hidden October column is not fabricated');
  assert.equal(wrap.scrollLeft, 0, 'cut-off outside the period uses the last rendered month');
});

test('scenario comparison labels partial, explicit-zero and missing plans without claiming a full subtotal', async t => {
  const replies = {
    A:{expense_plan:'100.00', expense_plan_complete:false, expense_plan_defined_rows:1, expense_plan_missing_rows:11},
    B:{expense_plan:'0.00', expense_plan_complete:true, expense_plan_defined_rows:12, expense_plan_missing_rows:0},
    V:{expense_plan:'0.00', expense_plan_complete:false, expense_plan_defined_rows:0, expense_plan_missing_rows:12},
  };
  const view = mountReport({api:async url => {
    const q = new URL(url, 'http://isolated.test').searchParams;
    return {...replies[q.get('scenario')], bank_income_mtd:'100.00', available:'100.00'};
  }}); t.after(view.unmount);
  await button(view.container, 'Сравнить А / Б / В').props.onClick(); await settle();
  const statuses = all(view.container, node => node.props?.role === 'status').map(text);
  assert.ok(statuses.includes('Частичный план выплат · незаполненных строк: 11'));
  assert.ok(statuses.includes('Полный план выплат'));
  assert.ok(statuses.includes('План выплат не задан · незаполненных строк: 12'));
  const comparison = find(view.container, node => node.tag === 'section' && text(node).includes('Сравнение сценариев'));
  assert.equal(all(comparison, node => node.props?.class?.includes?.('v num')).length, 3);
  assert.match(text(comparison), /Не задан/);
});

test('mounted plan editor chooses cut-off month, or first visible month outside that period', async t => {
  const view = mountReport(); t.after(view.unmount);
  await button(view.container, 'Задать план').props.onClick(); await settle();
  assert.equal(view.state.editMonth, 10);
  view.state.setPer('q1'); await view.state.edit(); await settle();
  assert.equal(view.state.editMonth, 1);
  view.state.setPer('custom'); view.state.start = 6; view.state.end = 8;
  await view.state.edit(); await settle();
  assert.equal(view.state.editMonth, 6);
});

test('mounted October save shows the updated amount, partial subtotal and month in Plan-fact', async t => {
  let view, refreshed, refreshes = 0;
  const calls = [];
  view = mountReport({currency:'USD', scenario:'B', report:report({currency:'USD', scenario:'B'}),
    api:async (url, options) => {
      assert.equal(url, '/api/cash-plan');
      const body = JSON.parse(options.body); calls.push(body);
      refreshed = report({currency:'USD', scenario:'B'});
      refreshed.rows[0].plan[9] = '-10000000.00'; refreshed.plan_versions[9] = 4;
      return {version:4};
    }, onRefresh:() => {refreshes++; view.app._instance.props.report = refreshed;}});
  t.after(view.unmount);
  await view.state.edit(); view.state.draft[0].amount = '10000000'; view.state.reason = 'Synthetic month planning test';
  await view.state.save(); await settle();
  assert.deepEqual(calls[0], {company_id:2, scenario:'B', month:'2026-10', currency:'USD', version:3,
    opening:null, reason:'Synthetic month planning test', items:[
      {category_id:7, kind:'out', amount:'10000000'}, {category_id:8, kind:'out', amount:null}, {category_id:9, kind:'in', amount:null},
    ]});
  assert.equal(refreshes, 1);
  assert.equal(view.state.mode, 'plan'); assert.equal(view.state.editor, false);
  assert.deepEqual(view.state.indices, [9]); assert.deepEqual(view.state.columns, ['plan','values','delta']);
  const table = find(view.container, node => node.tag === 'table');
  assert.ok(text(table).includes('Окт 2026')); assert.ok(!text(table).includes('Янв 2026'));
  assert.ok(text(table).includes('−10 000,0'), 'USD thousands display must show the saved 10 million');
  const status = find(view.container, node => node.props?.role === 'status');
  assert.match(text(status), /Октябрь 2026 · USD · сценарий Б/);
  const partial = find(view.container, node => node.props?.['aria-label'] === 'Полнота плана');
  assert.match(text(partial), /Частичный план/);
  assert.match(text(partial), /выплаты 10 000,0 тыс. USD/);
  assert.match(text(partial), /поступления не заданы/);
  assert.match(text(partial), /Незаполненные строки: 2/);
  assert.equal(view.app._instance.props.report.plan_totals[9], null, 'subtotal cannot become the full plan');
  assert.equal(view.app._instance.props.report.rows[0].values[9], '0.00', 'saving a plan cannot create an actual payment');
  assert.equal(find(view.container, node => node.props?.['aria-label'] === 'Cash Flow по месяцам').scrolled, true);
});

test('mounted stale-version refusal keeps edited values and original period, without reporting success', async t => {
  const calls = [];
  const view = mountReport({api:async (url, options) => {
    calls.push(JSON.parse(options.body)); throw Object.assign(new Error('План уже изменён другим пользователем.'), {status:409});
  }}); t.after(view.unmount);
  await view.state.edit(); view.state.draft[0].amount = '10000000'; view.state.reason = 'Synthetic version conflict test';
  await view.state.save(); await settle();
  assert.equal(calls[0].version, 3); assert.equal(calls[0].month, '2026-10');
  assert.equal(view.state.editor, true); assert.equal(view.state.draft[0].amount, '10000000');
  assert.equal(view.state.saved, false); assert.equal(view.state.mode, 'actual'); assert.equal(view.state.per, 'year');
  assert.match(text(find(view.container, node => node.props?.role === 'alert')), /План уже изменён/);
});

test('mounted Plan-fact distinguishes a missing plan from a complete explicit-zero plan', async t => {
  const missing = mountReport(); t.after(missing.unmount);
  await button(missing.container, 'План-факт').props.onClick(); await settle();
  assert.match(text(find(missing.container, node => node.props?.['aria-label'] === 'Полнота плана')), /План не задан/);
  assert.equal(missing.state.planStatus.income, null); assert.equal(missing.state.planStatus.expense, null);
  const filled = mountReport({report:report({completeZero:true})}); t.after(filled.unmount);
  await button(filled.container, 'План-факт').props.onClick(); await settle();
  assert.equal(find(filled.container, node => node.props?.['aria-label'] === 'Полнота плана'), undefined);
  assert.equal(filled.state.planStatus.missingRows, 0); assert.equal(filled.app._instance.props.report.plan_totals[9], '0.00');
  assert.equal(filled.state.planStatus.income, '0.00'); assert.equal(filled.state.planStatus.expense, '0.00');
  assert.ok(!text(find(filled.container, node => node.tag === 'table')).includes('Не задан'));
});

test('known-plan subtotal keeps cents and counts rows rather than silently filling missing monthly cells', () => {
  assert.deepEqual(planCompleteness([
    {kind:'in', plan:['0.10','0.20']}, {kind:'out', plan:['-10000000.01', null]}, {kind:'out', plan:['0.00',null]},
  ], [0,1]), {income:'0.30', expense:'10000000.01', missingRows:2, missingCells:2, knownCells:4});
});

test('read-only report roles have no plan editor button', t => {
  const view = mountReport({canPlan:false}); t.after(view.unmount);
  assert.equal(button(view.container, 'Задать план'), undefined);
});

test('mounted date input refuses a future cut-off before emitting and keeps its last valid value', async t => {
  const emitted = [], view = mountReport({onAsOf:value => emitted.push(value)}); t.after(view.unmount);
  const input = find(view.container, node => node.tag === 'input' && node.props.type === 'date');
  const target = {value:'2026-10-07'}; input.props.onChange({target}); await settle();
  assert.equal(target.value, '2026-10-06'); assert.deepEqual(emitted, []);
  assert.match(text(find(view.container, node => node.props?.role === 'alert')), /не позже сегодня/);
  input.props.onChange({target:{value:'2026-10-05'}}); await settle();
  assert.deepEqual(emitted, ['2026-10-05']); assert.equal(view.state.dateError, '');
});

test('mounted App returns from a past year to server today and rejects future or wrong-year cut-offs', async t => {
  globalThis.window = {addEventListener() {}, localStorage:{getItem:() => null}};
  globalThis.location = {hash:'#report'};
  globalThis.document = {hidden:true, activeElement:null, addEventListener() {}, removeEventListener() {}, querySelector:() => ({scrollIntoView() {}})};
  const previousFetch = globalThis.fetch, calls = [];
  globalThis.fetch = async url => {
    if (url === '/api/me') return {ok:false, status:401, json:async () => ({detail:'Synthetic unauthenticated startup'})};
    assert.ok(url.startsWith('/api/report?'), 'tests never call financial write or external APIs');
    const query = new URL(url, 'http://isolated.test').searchParams; calls.push(Object.fromEntries(query));
    return {ok:true, json:async () => report({year:Number(query.get('year')), currency:query.get('currency'), scenario:query.get('scenario')})};
  };
  const view = mount(appComponent); t.after(() => {view.unmount(); globalThis.fetch = previousFetch;});
  await settle();
  Object.assign(view.state, {user:{id:1, username:'synthetic.finance', name:'Synthetic', role:'finance', permissions:['view','export','plan']},
    boot:{today:'2026-10-06', accounts:[], categories:[], companies:[{id:2, name:'Zuma', code:'ZUMA'}], roles:{}},
    companyId:2, companyReady:true, year:2025, currency:'USD', reportScenario:'B', reportAsOf:'2025-12-31',
    report:report({year:2025, currency:'USD', scenario:'B'})});
  await settle();
  const yearSelect = () => find(view.container, node => node.props?.['aria-label'] === 'Год отчёта');
  yearSelect().props.onChange({target:{value:'2026'}}); await settle();
  assert.equal(view.state.year, 2026); assert.equal(view.state.reportAsOf, '2026-10-06');
  assert.deepEqual(calls.at(-1), {currency:'USD', year:'2026', company_id:'2', scenario:'B', as_of:'2026-10-06'});
  assert.ok(all(yearSelect(), node => node.tag === 'option').every(node => Number(node._value) <= 2026));
  const dateInput = () => find(view.container, node => node.tag === 'input' && node.props.type === 'date');
  assert.equal(dateInput().props.max, '2026-10-06');
  const count = calls.length;
  const target = {value:'2026-10-07'}; dateInput().props.onChange({target}); await settle();
  assert.equal(target.value, '2026-10-06'); assert.equal(view.state.reportAsOf, '2026-10-06'); assert.equal(calls.length, count);
  dateInput().props.onChange({target:{value:'2025-12-31'}}); await settle();
  assert.equal(calls.length, count); assert.equal(view.state.year, 2026);
  yearSelect().props.onChange({target:{value:'2027'}}); await settle();
  assert.equal(view.state.year, 2026); assert.equal(calls.length, count);
  yearSelect().props.onChange({target:{value:'2025'}}); await settle();
  assert.equal(view.state.reportAsOf, '2025-12-31'); assert.equal(calls.at(-1).as_of, '2025-12-31');
});

test('date helpers enforce server day and calendar validity without shifting actuals between years', () => {
  assert.equal(reportDateForYear(2026,'2026-10-06'), '2026-10-06');
  assert.equal(reportDateForYear(2025,'2026-10-06'), '2025-12-31');
  assert.equal(reportDateForYear(2027,'2026-10-06'), null);
  assert.equal(reportDateForYear(2026,undefined), null);
  assert.equal(reportDateForYear(2026,'2026-02-30'), null);
  assert.equal(validReportDate('2026-02-30',2026,'2026-10-06'), false);
  assert.equal(validReportDate('2026-10-07',2026,'2026-10-06'), false);
  assert.equal(validReportDate('2025-12-31',2026,'2026-10-06'), false);
  assert.equal(validReportDate('2024-02-29',2024,'2026-10-06'), true);
});
