import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent, mount, settle, text, all, find} from './helpers/mountVue.js';
import {canSeeRequestPanels} from '../src/overview/rights.js';

const overview = await compileComponent(new URL('../src/overview/OverviewPage.vue', import.meta.url));
const today = '2026-11-05';
const companies = [{id: 3, code: 'ZUMA'}, {id: 2, code: 'UZGERMED'}];
const lowerRoles = ['accountant', 'cashier', 'employee', 'auditor', 'operator', 'investor',
  'material_accountant', 'procurement', 'sales'];
const pending = {id: 701, number: 'SYN-PENDING', status: 'pending', date: today, currency: 'UZS',
  amount: '15.00', counterparty: 'Synthetic pending recipient', purpose: 'Synthetic pending purpose', approval_stage: 'finance'};
const overdue = {id: 702, number: 'SYN-OVERDUE', status: 'approved', date: '2026-11-04', currency: 'UZS',
  amount: '12.00', counterparty: 'Synthetic overdue recipient', purpose: 'Synthetic overdue purpose', account: 'Synthetic bank'};
const upcoming = {kind: 'out', id: 703, name: 'Synthetic upcoming recipient', amount: '9.00'};
const dash = {pending_count: 1, pending_amount: '15.00', fact_in: '20.00', fact_out: '10.00', reserve: '1.00',
  incoming7: '0.00', outgoing7: '9.00', forecast_has_events: true,
  forecast: [{date: '2026-11-06', balance: '100.00', events: [upcoming]}]};
const ownRole = role => ({role, company_role: role, holding_role: null});
const section = (view, id) => find(view.container, node => node.tag === 'section' && node.props.id === id);
const heading = (view, id) => find(view.container, node => node.tag === 'h2' && node.props.id === id);
const requestCalls = calls => calls.filter(url => url.startsWith('/api/requests?'));

function apiFixture(calls, delayed = null) {
  return async url => {
    calls.push(url);
    const parsed = new URL(url, 'https://synthetic.test');
    switch (parsed.pathname) {
      case '/api/requests': {
        const row = parsed.searchParams.get('state') === 'pending' ? pending : overdue;
        return delayed ? delayed(row) : {items: [row], total: 1};
      }
      case '/api/dashboard': return structuredClone(dash);
      case '/api/group-report': return {day: today, closing: '100.00', reserved: '0.00', available: '100.00'};
      case '/api/accounts': return parsed.searchParams.get('archived') === 'true' ? [] :
        [{id: 81, name: 'Synthetic bank', currency: 'UZS', kind: 'bank', balance: '100.00', opening: '100.00', opening_date: '2026-01-01'}];
      case '/api/ledger': return {items: [], total: 0};
      case '/api/budgets': return [];
      case '/api/report': return {rows: []};
      default: throw Error(`Unexpected synthetic endpoint: ${url}`);
    }
  };
}

function propsFor(company, api, showRequestPanels, rights = []) {
  return {api, companyId: company.id, company: {...company, name: company.code}, currency: 'UZS', today,
    showRequestPanels, has: right => rights.includes(right), canDecide: () => rights.includes('approve') || rights.includes('request_check'),
    canOpenRequests: rights.includes('requests-view')};
}

function assertHidden(view) {
  assert.equal(section(view, 'ov-decisions'), undefined);
  assert.equal(heading(view, 'ov-pend-h'), undefined);
  assert.equal(section(view, 'ov-payments'), undefined);
  const rendered = text(view.container);
  assert.doesNotMatch(rendered, /Ждут вашего решения|На согласовании|Сегодня и просроченные|Ближайшие выплаты/);
  assert.doesNotMatch(rendered, /Synthetic (?:pending|overdue|upcoming) recipient|SYN-PENDING|SYN-OVERDUE/);
  assert.doesNotMatch(rendered, /плат[её]ж(?:а|ей|) к оплате|заявк[аи]? жд[её]т|Просрочена утвержд[её]нная выплата/);
  assert.ok(section(view, 'ov-risks'), 'other overview blocks stay available');
  assert.ok(heading(view, 'ov-pf-h'), 'plan/fact is unchanged');
}

test('overview request panels are limited to finance/director and holding admin/founder', () => {
  for (const role of ['finance', 'director']) assert.equal(canSeeRequestPanels(ownRole(role)), true, role);
  for (const role of lowerRoles) {
    assert.equal(canSeeRequestPanels({...ownRole(role), permissions: ['approve', 'request_check', 'pay_bank', 'pay_cash'],
      roles: [role, 'finance'], acting: [{role: 'finance'}]}), false, role);
  }
  for (const holding_role of ['admin', 'founder']) {
    assert.equal(canSeeRequestPanels({holding_role, company_role: 'accountant', role: 'accountant'}), true, holding_role);
  }
  assert.equal(canSeeRequestPanels({holding_role: null, company_role: null, role: 'finance'}), false,
    'a stale global finance role is not a company assignment');
  assert.equal(canSeeRequestPanels({holding_role: null, company_role: 'accountant', role: 'director'}), false,
    'the selected-company role takes precedence over the profile role');
  assert.equal(canSeeRequestPanels({role: 'finance'}), true, 'legacy role-only records remain supported');
  assert.equal(canSeeRequestPanels(null), false);
  assert.equal(canSeeRequestPanels({}), false);
});

test('all lower roles in both companies get no request panels or request-list fetches, even with extra grants', async t => {
  for (const company of companies) for (const role of lowerRoles) {
    const calls = [];
    const view = mount(overview, propsFor(company, apiFixture(calls), canSeeRequestPanels(ownRole(role)),
      ['view', 'requests-view', 'approve', 'request_check', 'pay_bank', 'pay_cash']));
    t.after(view.unmount);
    await settle(); await settle();
    assert.deepEqual(requestCalls(calls), [], `${company.code}/${role}: no request list was read`);
    assert.equal(view.state.s.requestsLoading, false);
    assertHidden(view);
    // Retained data must not expose a card, chip, or payee through the risks block.
    view.state.s = {...view.state.s, pending: [pending], pendingAll: [pending], approved: [overdue],
      pendingTotal: 1, pendingLoaded: 1, approvedTotal: 1, approvedLoaded: 1};
    await settle();
    assertHidden(view);
  }
});

test('finance, director and admin keep approval and payment previews in both companies', async t => {
  for (const company of companies) for (const user of [ownRole('finance'), ownRole('director'),
    {holding_role: 'admin', company_role: 'accountant', role: 'accountant'}]) {
    const calls = [];
    const view = mount(overview, propsFor(company, apiFixture(calls), canSeeRequestPanels(user), ['view', 'requests-view', 'approve']));
    t.after(view.unmount);
    await settle(); await settle();
    assert.equal(requestCalls(calls).length, 2);
    assert.ok(section(view, 'ov-decisions'));
    assert.ok(section(view, 'ov-payments'));
    const rendered = text(view.container);
    assert.match(rendered, /Synthetic pending recipient/);
    assert.match(rendered, /Synthetic overdue recipient/);
    assert.match(rendered, /Synthetic upcoming recipient/);
    const payee = find(view.container, node => node.tag === 'button' && text(node).includes('Synthetic pending recipient'));
    assert.ok(payee);
  }
});

test('founder keeps read-only pending and payment summaries without approval or payment privileges', async t => {
  for (const company of companies) {
    const calls = [], grants = ['view', 'ledger', 'export', 'download', 'audit', 'requests-view'];
    const user = {holding_role: 'founder', company_role: null, role: 'founder'};
    const view = mount(overview, propsFor(company, apiFixture(calls), canSeeRequestPanels(user), grants));
    t.after(view.unmount);
    await settle(); await settle();
    assert.equal(requestCalls(calls).length, 2);
    assert.equal(section(view, 'ov-decisions'), undefined);
    assert.ok(heading(view, 'ov-pend-h'));
    assert.ok(section(view, 'ov-payments'));
    assert.equal(view.state.canPay, false);
    assert.equal(view.state.approver, false);
    const buttons = all(view.container, node => node.tag === 'button').map(text);
    assert.ok(buttons.some(label => label.startsWith('Все утверждённые')));
    assert.ok(!buttons.some(label => /^(К оплате|Оплатить|Подтвердить решение|Отправить на согласование)$/.test(label)));
    assert.deepEqual(grants, ['view', 'ledger', 'export', 'download', 'audit', 'requests-view']);
  }
});

test('revoking overview request panels discards slow old request answers and hides payee risks', async t => {
  const calls = [], resolveRequests = [];
  const api = apiFixture(calls, row => new Promise(resolve => resolveRequests.push(() => resolve({items: [row], total: 1}))));
  const view = mount(overview, propsFor(companies[0], api, true, ['view', 'requests-view', 'approve', 'pay_bank']));
  t.after(view.unmount);
  await settle();
  assert.equal(resolveRequests.length, 2);
  view.app._instance.props.showRequestPanels = false;
  await settle(); await settle();
  for (const resolve of resolveRequests) resolve();
  await settle(); await settle();
  assertHidden(view);
  assert.equal(view.state.s.pendingAll?.length ?? 0, 0);
  assert.equal(view.state.s.approved?.length ?? 0, 0);
  assert.equal(view.state.s.requestsLoading, false);
  assert.equal(requestCalls(calls).length, 2, 'revoking access does not launch replacement requests');
});
