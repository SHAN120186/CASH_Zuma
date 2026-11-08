import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent, mount, settle, text, all, find} from './helpers/mountVue.js';
import {canOpenOverview,canSeeFullOverview} from '../src/overview/rights.js';
import {lang, setLang} from '../src/i18n/index.js';

const overview = await compileComponent(new URL('../src/overview/OverviewPage.vue', import.meta.url));
const today = '2026-11-05';
const companies = [{id: 3, code: 'ZUMA'}, {id: 2, code: 'UZGERMED'}];
const lowerRoles = ['accountant', 'cashier', 'employee', 'auditor', 'operator', 'investor',
  'material_accountant', 'procurement', 'sales'];
const pending = {id: 701, number: 'SYN-PENDING', status: 'pending', date: today, currency: 'UZS',
  amount: '15.00', counterparty: 'Synthetic pending recipient', purpose: 'Synthetic pending purpose', priority: 'normal', approval_stage: 'finance'};
const overdue = {id: 702, number: 'SYN-OVERDUE', status: 'approved', date: '2026-11-04', currency: 'UZS',
  amount: '12.00', counterparty: 'Synthetic overdue recipient', purpose: 'Synthetic overdue purpose', account: 'Synthetic payment bank'};
const future = {id: 703, number: 'SYN-FUTURE', status: 'approved', date: '2026-11-06', currency: 'UZS',
  amount: '9.00', counterparty: 'Synthetic upcoming recipient', purpose: 'Synthetic upcoming purpose', account: 'Synthetic payment bank'};
const upcoming = {kind: 'out', id: future.id, name: future.counterparty, amount: future.amount};
const dash = {pending_count: 1, pending_amount: '15.00', fact_in: '20.00', fact_out: '10.00', reserve: '1.00',
  incoming7: '0.00', outgoing7: '9.00', forecast_has_events: true,
  forecast: [{date: future.date, balance: '100.00', events: [upcoming]}]};
const ownRole = role => ({role, company_role: role, holding_role: null});
const section = (view, id) => find(view.container, node => node.tag === 'section' && node.props.id === id);
const heading = (view, id) => find(view.container, node => node.tag === 'h2' && node.props.id === id);
const h2s = view => all(view.container, node => node.tag === 'h2').map(text);
const requestCalls = calls => calls.filter(url => new URL(url, 'https://synthetic.test').pathname === '/api/requests');
const broadGrants = ['view', 'overview', 'ledger', 'export', 'requests-view', 'approve', 'request_check', 'pay_bank', 'pay_cash'];

test('the dedicated overview right opens every employee home without selecting the full overview', () => {
  for (const role of lowerRoles) {
    for (const sections of [null, {home: 'view', accounts: 'hidden', requests: 'hidden', calendar: 'hidden'}]) {
      const user = {...ownRole(role), permissions: ['home_view'], sections};
      assert.equal(canOpenOverview(user), true, `${role}: home opens`);
      assert.equal(canSeeFullOverview(user), false, `${role}: compact format`);
      assert.deepEqual(user.permissions, ['home_view']);
    }
  }
});

test('the overview entry respects a hidden home and does not mistake other duties for home access', () => {
  assert.equal(canOpenOverview(null), false);
  assert.equal(canOpenOverview({...ownRole('cashier'), permissions: ['request', 'ledger', 'pay_cash']}), false);
  assert.equal(canOpenOverview({...ownRole('cashier'), permissions: ['home_view'], sections: {home: 'hidden'}}), false);
  assert.equal(canOpenOverview({...ownRole('employee'), permissions: []}), false);
  for (const right of ['view', 'overview']) {
    assert.equal(canOpenOverview({...ownRole('sales'), permissions: [right]}), true);
  }
});

function apiFixture(calls, {delayFull, delayRequests} = {}) {
  return async url => {
    calls.push(url);
    const parsed = new URL(url, 'https://synthetic.test');
    let response;
    switch (parsed.pathname) {
      case '/api/requests': {
        const rows = parsed.searchParams.get('state') === 'pending' ? [pending] :
          [overdue, future].filter(row => !parsed.searchParams.has('date_to') || row.date <= parsed.searchParams.get('date_to'));
        response = {items: structuredClone(rows), total: rows.length};
        return delayRequests ? delayRequests(response) : response;
      }
      case '/api/dashboard': response = structuredClone(dash); break;
      case '/api/group-report': response = {day: today, closing: '100.00', reserved: '0.00', available: '100.00'}; break;
      case '/api/accounts': response = parsed.searchParams.get('archived') === 'true' ? [] :
        [{id: 81, name: 'Synthetic balance-only bank', currency: 'UZS', kind: 'bank', balance: '100.00', opening: '100.00', opening_date: '2026-01-01'}]; break;
      case '/api/ledger': response = {items: [], total: 0}; break;
      case '/api/budgets': response = []; break;
      case '/api/report': response = {rows: []}; break;
      default: throw Error(`Unexpected synthetic endpoint: ${url}`);
    }
    return delayFull ? delayFull(response) : response;
  };
}

function propsFor(company, api, user, rights = broadGrants, options = {}) {
  return {api, companyId: company.id, company: {...company, name: company.code}, currency: 'UZS', today,
    showRequestPanels: true, requestsOnly: !canSeeFullOverview(user),
    has: right => rights.includes(right), canDecide: () => rights.includes('approve') || rights.includes('request_check'),
    canOpenRequests: rights.includes('requests-view'), ...options};
}

test('compact payment and upcoming lists exclude funding-only parent totals',async t=>{
 const view=mount(overview,propsFor(companies[0],apiFixture([]),ownRole('cashier')));t.after(view.unmount);await settle();
 view.state.s.approved=[overdue,future,{...overdue,id:900,cash_funding:{mode:'batch'},amount:'990000.00'},{...future,id:901,cash_funding:{mode:'batch'},amount:'990000.00'}];await settle();
 assert.deepEqual(view.state.payments.map(row=>row.id),[overdue.id]);
 assert.deepEqual(view.state.upcoming.map(row=>row.id),[future.id]);
});

function assertCompact(view) {
  assert.deepEqual(h2s(view), ['На согласовании', 'Платежи']);
  assert.equal(section(view, 'ov-risks'), undefined);
  assert.equal(heading(view, 'ov-pf-h'), undefined);
  assert.equal(heading(view, 'ov-ops-h'), undefined);
  assert.equal(heading(view, 'ov-acc-h'), undefined);
  const rendered = text(view.container);
  assert.doesNotMatch(rendered, /Доступный остаток|Показатели месяца|Доходы|Расходы|Чистый поток|Прогноз остатка|Риски|Где находятся деньги|План и факт месяца|Последние операции|Банковская выписка за день/);
  assert.doesNotMatch(rendered, /Synthetic balance-only bank|Synthetic hidden ledger recipient/);
}

test('optional analytics require a click and never reload the working blocks', async t => {
  for (const company of companies) {
    const calls = [];
    const view = mount(overview, propsFor(company, apiFixture(calls), ownRole('finance')));
    t.after(view.unmount); await settle(); await settle();
    assert.equal(calls.filter(url => url.startsWith('/api/report?')).length, 0);
    assert.equal(calls.filter(url => url.startsWith('/api/group-report?')).length, 1);
    assert.equal(calls.filter(url => url.startsWith('/api/ledger?') && url.includes('date_from')).length, 0);
    assert.equal(view.state.reserved, 0);
    assert.equal(view.state.available, 10000);
    const essential = () => calls.filter(url => /\/api\/(requests|budgets|accounts|dashboard)\?/.test(url)).length;
    const initial = essential();
    const button = find(view.container, node => node.tag === 'button' && text(node) === 'Показать график остатка и план-факт');
    assert.ok(button); await button.props.onClick(); await settle();
    assert.equal(essential(), initial);
    assert.equal(calls.filter(url => url.startsWith('/api/report?')).length, 1);
    assert.equal(calls.filter(url => url.startsWith('/api/group-report?')).length, 2);
    assert.equal(calls.filter(url => url.startsWith('/api/ledger?') && url.includes('date_from')).length, 1);
    assert.equal(view.state.reserved, 0);
    assert.equal(view.state.available, 10000);
    const total = calls.length; await view.state.showDetails();
    assert.equal(calls.length, total);
  }
});

test('a slow budget does not block balances, request panels or the report',async t=>{
  let finishBudget;
  const calls=[],baseApi=apiFixture(calls);
  const api=url=>url.startsWith('/api/budgets?')?new Promise(resolve=>{finishBudget=resolve}):baseApi(url);
  const view=mount(overview,propsFor(companies[0],api,ownRole('finance')));t.after(view.unmount);
  await settle();await settle();
  assert.equal(view.state.s.loading,false);
  assert.equal(view.state.s.requestsLoading,false);
  assert.equal(view.state.balanceState,'ready');
  assert.equal(view.state.s.report,null);
  assert.equal(view.state.s.budgetsLoading,true);
  assert.match(text(view.container),/Проверяем бюджеты/);
  assert.doesNotMatch(text(view.container),/Рисков по UZS не видно/);
  finishBudget([{category:'Synthetic over budget',limit:'1.00',remaining:'-3.00'}]);
  await settle();
  assert.equal(view.state.s.budgetsLoading,false);
  assert.match(text(view.container),/Synthetic over budget/);
});

test('a late budget from another company cannot replace the current one',async t=>{
  const finishes=[],baseApi=apiFixture([]);
  const api=url=>url.startsWith('/api/budgets?')?new Promise(resolve=>finishes.push(resolve)):baseApi(url);
  const view=mount(overview,propsFor(companies[0],api,ownRole('finance')));t.after(view.unmount);
  await settle();view.app._instance.props.companyId=companies[1].id;await settle();
  finishes[1]([{category:'Current company',limit:'1.00',remaining:'-2.00'}]);await settle();
  finishes[0]([{category:'Previous company',limit:'1.00',remaining:'-3.00'}]);await settle();
  assert.equal(view.state.s.budgets[0].category,'Current company');
  assert.doesNotMatch(text(view.container),/Previous company/);
});

test('a failed budget leaves risk coverage incomplete while other cards work',async t=>{
  let failBudget;const baseApi=apiFixture([]);
  const api=url=>url.startsWith('/api/budgets?')?new Promise((resolve,reject)=>{failBudget=reject}):baseApi(url);
  const view=mount(overview,propsFor(companies[0],api,ownRole('finance')));t.after(view.unmount);
  await settle();failBudget(Error('Synthetic unavailable'));await settle();
  assert.equal(view.state.s.loading,false);assert.equal(view.state.s.budgetsLoading,false);
  assert.equal(view.state.balanceState,'ready');
  assert.ok(view.state.riskGaps.includes('бюджеты'));
  assert.doesNotMatch(text(view.container),/Рисков по UZS не видно/);
});

test('only finance/director and holding admin/founder keep the full overview', () => {
  for (const role of ['finance', 'director']) assert.equal(canSeeFullOverview(ownRole(role)), true, role);
  for (const role of lowerRoles) {
    assert.equal(canSeeFullOverview({...ownRole(role), permissions: broadGrants,
      roles: [role, 'finance'], acting: [{role: 'finance'}]}), false, role);
  }
  for (const holding_role of ['admin', 'founder']) {
    assert.equal(canSeeFullOverview({holding_role, company_role: 'accountant', role: 'accountant'}), true, holding_role);
  }
  assert.equal(canSeeFullOverview({holding_role: null, company_role: null, role: 'finance'}), false,
    'an absent company assignment cannot inherit a stale global finance role');
  assert.equal(canSeeFullOverview({holding_role: null, company_role: 'accountant', role: 'director'}), false);
  assert.equal(canSeeFullOverview({role: 'finance'}), true, 'legacy role-only records remain supported');
  assert.equal(canSeeFullOverview(null), false);
  assert.equal(canSeeFullOverview({}), false);
});

test('nine lower roles in both companies see exactly approval and payment blocks and fetch only their allowed requests', async t => {
  for (const company of companies) for (const role of lowerRoles) {
    const calls = [];
    const view = mount(overview, propsFor(company, apiFixture(calls), ownRole(role)));
    t.after(view.unmount);
    await settle(); await settle();
    assert.equal(requestCalls(calls).length, 2, `${company.code}/${role}: pending and approved lists`);
    assert.equal(calls.length, 2, `${company.code}/${role}: no hidden financial API reads`);
    assert.ok(requestCalls(calls).some(url => new URL(url, 'https://synthetic.test').searchParams.get('state') === 'pending'));
    const approvedUrl = requestCalls(calls).find(url => new URL(url, 'https://synthetic.test').searchParams.get('state') === 'approved');
    assert.equal(new URL(approvedUrl, 'https://synthetic.test').searchParams.has('date_to'), false,
      'future approved requests come from the same visible-request list, not the financial dashboard');
    assertCompact(view);
    assert.match(text(view.container), /Synthetic pending recipient/);
    assert.match(text(view.container), /Synthetic overdue recipient/);
    assert.match(text(view.container), /Synthetic upcoming recipient/);
    // Stale state from a previously full overview must not put hidden figures back on screen.
    view.state.s = {...view.state.s, dash: structuredClone(dash), accounts: [{name: 'Synthetic balance-only bank', currency: 'UZS', balance: '999.00'}],
      latest: [{date: today, kind: 'in', amount: '999.00', currency: 'UZS', counterparty: 'Synthetic hidden ledger recipient'}]};
    await settle();
    assertCompact(view);
  }
});

test('sales without request access see two unavailable blocks, no financial reads and no request transitions', async t => {
  for (const company of companies) {
    const calls = [], opened = [], destinations = [];
    const view = mount(overview, propsFor(company, apiFixture(calls), ownRole('sales'), ['overview'],
      {onOpenRequest: value => opened.push(value), onGo: value => destinations.push(value)}));
    t.after(view.unmount);
    await settle(); await settle();
    assertCompact(view);
    assert.deepEqual(calls, []);
    assert.equal((text(view.container).match(/Раздел «Заявки» недоступен по вашим настройкам доступа\./g) || []).length, 2);
    const buttons = all(view.container, node => node.tag === 'button').map(text);
    assert.ok(!buttons.some(label => /Все заявки|К оплате|Все утверждённые/.test(label)));
    view.state.showRequest(pending);
    assert.deepEqual(opened, []);
    assert.deepEqual(destinations, []);
  }
});

test('compact overview remains request-only after horizon, scenario, currency and refresh updates', async t => {
  const calls = [];
  const view = mount(overview, propsFor(companies[0], apiFixture(calls), ownRole('accountant')));
  t.after(view.unmount);
  await settle(); await settle();
  view.app._instance.props.days = 7;
  view.state.scenario = 'B';
  await settle(); await settle();
  view.app._instance.props.currency = 'USD';
  view.app._instance.props.refresh += 1;
  await settle(); await settle();
  assert.equal(calls.length, requestCalls(calls).length, 'hidden forecast/report watchers never fetch financial aggregates');
  assert.ok(calls.some(url => new URL(url, 'https://synthetic.test').searchParams.get('currency') === 'USD'));
  assertCompact(view);
});

test('finance, director and admin retain the complete overview and their existing requests in both companies', async t => {
  for (const company of companies) for (const user of [ownRole('finance'), ownRole('director'),
    {holding_role: 'admin', company_role: 'accountant', role: 'accountant'}]) {
    const calls = [];
    const view = mount(overview, propsFor(company, apiFixture(calls), user));
    t.after(view.unmount);
    await settle(); await settle();
    assert.equal(view.app._instance.props.requestsOnly, false);
    assert.equal(requestCalls(calls).length, 2);
    assert.ok(calls.some(url => url.startsWith('/api/dashboard?')));
    assert.ok(calls.some(url => url.startsWith('/api/accounts?')));
    assert.ok(!calls.some(url => url.startsWith('/api/report?')));
    await view.state.showDetails(); await settle();
    assert.ok(section(view, 'ov-decisions'));
    assert.ok(section(view, 'ov-payments'));
    assert.ok(section(view, 'ov-risks'));
    await view.state.showDetails(); await settle();
    assert.ok(heading(view, 'ov-pf-h'));
    assert.ok(heading(view, 'ov-ops-h'));
    assert.match(text(view.container), /Synthetic pending recipient/);
    assert.match(text(view.container), /Synthetic overdue recipient/);
    assert.match(text(view.container), /Synthetic upcoming recipient/);
  }
});

test('founder retains the full read-only overview without gaining approval or payment rights', async t => {
  for (const company of companies) {
    const calls = [], grants = ['view', 'ledger', 'export', 'download', 'audit', 'requests-view'];
    const user = {holding_role: 'founder', company_role: null, role: 'founder'};
    const view = mount(overview, propsFor(company, apiFixture(calls), user, grants));
    t.after(view.unmount);
    await settle(); await settle();
    assert.equal(requestCalls(calls).length, 2);
    assert.equal(section(view, 'ov-decisions'), undefined);
    assert.ok(heading(view, 'ov-pend-h'));
    assert.ok(section(view, 'ov-payments'));
    assert.ok(section(view, 'ov-risks'));
    await view.state.showDetails(); await settle();
    assert.ok(heading(view, 'ov-pf-h'));
    assert.equal(view.state.canPay, false);
    assert.equal(view.state.approver, false);
    const buttons = all(view.container, node => node.tag === 'button').map(text);
    assert.ok(buttons.some(label => label.startsWith('Все утверждённые')));
    assert.ok(!buttons.some(label => /^(К оплате|Оплатить|Подтвердить решение|Отправить на согласование)$/.test(label)));
    assert.deepEqual(grants, ['view', 'ledger', 'export', 'download', 'audit', 'requests-view']);
  }
});

test('late full-overview responses cannot reveal hidden aggregates after switching to request-only mode', async t => {
  const calls = [], finishFull = [];
  const api = apiFixture(calls, {delayFull: response => new Promise(resolve => finishFull.push(() => resolve(response)))});
  const view = mount(overview, propsFor(companies[0], api, ownRole('finance')));
  t.after(view.unmount);
  await settle();
  assert.ok(finishFull.length >= 6);
  const oldFinancialCalls = calls.filter(url => !requestCalls([url]).length).length;
  view.app._instance.props.requestsOnly = true;
  await settle(); await settle();
  for (const finish of finishFull) finish();
  await settle(); await settle();
  assertCompact(view);
  assert.equal(view.state.s.dash, null);
  assert.equal(view.state.s.report, null);
  assert.equal(view.state.s.latest, null);
  assert.equal(calls.filter(url => !requestCalls([url]).length).length, oldFinancialCalls,
    'an obsolete full load cannot start delayed report/history reads');
});

test('closing request access discards slow compact request answers instead of showing old payees', async t => {
  const calls = [], finishRequests = [];
  const api = apiFixture(calls, {delayRequests: response => new Promise(resolve => finishRequests.push(() => resolve(response)))});
  const view = mount(overview, propsFor(companies[0], api, ownRole('accountant')));
  t.after(view.unmount);
  await settle();
  assert.equal(finishRequests.length, 2);
  view.app._instance.props.canOpenRequests = false;
  await settle(); await settle();
  for (const finish of finishRequests) finish();
  await settle(); await settle();
  assertCompact(view);
  assert.doesNotMatch(text(view.container), /Synthetic (?:pending|overdue|upcoming) recipient/);
  assert.equal(view.state.s.pendingAll?.length ?? 0, 0);
  assert.equal(view.state.s.approved?.length ?? 0, 0);
  assert.equal(calls.length, 2, 'revoking access launches no new reads');
});

test('request-only section titles and unavailable state are translated to Uzbek', async t => {
  const previous = lang.value;
  setLang('uz');
  t.after(() => setLang(previous));
  const calls = [];
  const view = mount(overview, propsFor(companies[0], apiFixture(calls), ownRole('sales'), ['overview']));
  t.after(view.unmount);
  await settle(); await settle();
  assert.deepEqual(h2s(view), ['Kelishishda', 'Toʻlovlar']);
  assert.doesNotMatch(text(view.container), /[А-Яа-яЁё]/u);
  assert.deepEqual(calls, []);
});
