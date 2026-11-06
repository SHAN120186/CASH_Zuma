import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent, mount, settle, all, text} from './helpers/mountVue.js';
import {lang, setLang, tx} from '../src/i18n/index.js';

const component = await compileComponent(new URL('../src/requests/PolicyPage.vue', import.meta.url));
const category = {id: 17, code: '17', name: 'Synthetic equipment', policy: 'always', skip_allowed: false,
  effective: {UZS: {policy: 'threshold', threshold: '20000000.00'}, USD: {policy: 'threshold', threshold: '500.00'}, EUR: {policy: 'always', threshold: null}},
  director_limits: {UZS: '100000000.00', USD: null, EUR: '0.00'}};
const policy = {categories: [category], defaults: {UZS: '5000000', USD: '500'}, can_edit_skip_list: true};
const money = value => String(value);

function apiFor(value = policy, calls = []) {
  return async (url, options) => {calls.push([url, options]);return url.includes('calendar-days') ? [] : value;};
}

test('each currency shows its effective participation rule without an upper cap', async t => {
  const view = mount(component, {money, api: apiFor()});t.after(view.unmount);await settle();
  const table = all(view.container, node => node.tag === 'table')[0];
  assert.match(text(table), /Выше 20000000.00 UZS/);
  assert.match(text(table), /Выше 500.00 USD/);
  assert.match(text(table), /Директор участвует всегда/);
  assert.doesNotMatch(text(table), /Лимит директора|100000000.00|согласование запрещено/);
  assert.equal(all(table, node => node.tag === 'th').length, 6);
});

test('editing a zero threshold preserves the other currency rules in the new API payload', async t => {
  const calls = [], view = mount(component, {money, api: apiFor(policy, calls)});
  t.after(view.unmount);await settle();view.state.edit(category);await settle();
  assert.equal(all(view.container, node => node.tag === 'select').length, 3);
  view.state.form.threshold_uzs = 0;
  view.state.form.reason = 'Synthetic owner threshold update';await view.state.save(category);await settle();
  const writes = calls.filter(([, options]) => options);
  assert.equal(writes.length, 1);assert.equal(writes[0][0], '/api/categories/17/director-policy');
  assert.deepEqual(JSON.parse(writes[0][1].body), {reason: 'Synthetic owner threshold update',
    policy_uzs: 'threshold', threshold_uzs: '0', policy_usd: 'threshold', threshold_usd: '500.00', policy_eur: 'always', threshold_eur: null});
  assert.equal(view.state.editing, null);assert.match(text(view.container), /Политика статьи/);
});

test('skip is explicit per currency and unavailable outside the approved list', async t => {
  const zero = {...category, effective: {...category.effective, UZS: {policy: 'threshold', threshold: 0}}};
  const view = mount(component, {money, api: apiFor({...policy, categories: [zero]})});t.after(view.unmount);await settle();
  assert.match(text(view.container), /Выше 0 UZS/);view.state.edit(zero);await settle();
  assert.equal(view.state.form.threshold_uzs, 0);
  const skipOptions = all(view.container, node => node.tag === 'option' && node.props.value === 'skip');
  assert.equal(skipOptions.length, 3);assert.ok(skipOptions.every(node => node.props.disabled));
  const allowed = {...category, skip_allowed: true, effective: {...category.effective, UZS: {policy: 'skip', threshold: null}}};
  const calls = [], editable = mount(component, {money, api: apiFor({...policy, categories: [allowed]}, calls)});
  t.after(editable.unmount);await settle();editable.state.edit(allowed);editable.state.form.reason = 'Synthetic director participation policy';
  await editable.state.save(allowed);await settle();
  const body = JSON.parse(calls.find(([, options]) => options)[1].body);
  assert.equal(body.policy_uzs, 'skip');assert.equal(body.threshold_uzs, null);assert.equal(body.policy_usd, 'threshold');
});

test('read-only view has no policy editor and a rejection preserves the form', async t => {
  const readonly = mount(component, {money, canEdit: false, api: apiFor()});t.after(readonly.unmount);await settle();
  assert.equal(all(readonly.container, node => node.tag === 'button' && text(node) === 'Изменить').length, 0);
  const view = mount(component, {money, api: async (url, options) => {
    if (options) throw new Error('Synthetic conflict');
    return url.includes('calendar-days') ? [] : policy;
  }});t.after(view.unmount);await settle();view.state.edit(category);view.state.form.threshold_uzs = '250';
  await view.state.save(category);await settle();
  assert.equal(view.state.editing, 17);assert.equal(view.state.form.threshold_uzs, '250');
  assert.equal(view.state.notice, '');assert.match(text(view.container), /Synthetic conflict/);
});

test('threshold labels and the retired cap message translate to Uzbek', async t => {
  const previous = lang.value;t.after(() => setLang(previous));setLang('uz');
  assert.equal(tx('Выше {amount} {currency}', {amount: '0', currency: 'UZS'}), '0 UZS dan yuqori');
  assert.doesNotMatch(tx('Верхний лимит директора больше не используется. Настройте порог участия.'), /[А-Яа-яЁё]/);
  const view = mount(component, {money, api: apiFor()});t.after(view.unmount);await settle();
  const table = all(view.container, node => node.tag === 'table')[0];
  assert.doesNotMatch(text(table), /[А-Яа-яЁё]/);assert.match(text(table), /20000000.00 UZS dan yuqori/);
});
