import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent, mount, settle, all, text} from './helpers/mountVue.js';
import {lang, setLang, tx} from '../src/i18n/index.js';

const component = await compileComponent(new URL('../src/requests/PolicyPage.vue', import.meta.url));
const category = {id: 17, code: '17', name: 'Synthetic equipment', policy: 'always', thresholds: {},
  director_limits: {UZS: '100000000.00', USD: null, EUR: '0.00'}};
const policy = {categories: [category], defaults: {UZS: '5000000', USD: '500'}, can_edit_director_limits: true};
const money = value => String(value);

test('the limit editor distinguishes zero from unlimited and does not change routing policy', async t => {
  const calls = [];
  const view = mount(component, {money, api: async (url, options) => {
    calls.push([url, options]);return url.includes('calendar-days') ? [] : policy;
  }});t.after(view.unmount);await settle();
  assert.match(text(view.container), /100000000.00 UZS/);
  assert.match(text(view.container), /USD: Без лимита/);
  assert.match(text(view.container), /EUR: 0.00 EUR/);
  view.state.editLimit(category);
  assert.equal(view.state.limitForm.limit_eur, '0.00');
  view.state.limitForm.limit_uzs = 0;view.state.limitForm.limit_usd = '';
  view.state.limitForm.reason = 'Owner confirms zero cap';await view.state.saveLimit(category);await settle();
  const writes = calls.filter(([, options]) => options);
  assert.equal(writes.length, 1);assert.equal(writes[0][0], '/api/categories/17/director-limits');
  assert.deepEqual(JSON.parse(writes[0][1].body), {limit_uzs: '0', limit_usd: null, limit_eur: '0.00', reason: 'Owner confirms zero cap'});
  assert.equal(view.state.limitEditing, null);assert.match(text(view.container), /Лимит директора по статье/);
});

test('director and read-only views have no button for editing authority caps', async t => {
  for (const [canEdit, admin] of [[true, false], [false, true]]) {
    const view = mount(component, {money, canEdit, api: async url => url.includes('calendar-days') ? [] : {...policy, can_edit_director_limits: admin}});
    t.after(view.unmount);await settle();
    assert.equal(all(view.container, node => node.tag === 'button' && text(node) === 'Лимит директора').length, 0);
    assert.match(text(view.container), /100000000.00 UZS/);
  }
});

test('an API rejection keeps the limit form and does not report success', async t => {
  const view = mount(component, {money, api: async (url, options) => {
    if (options) throw new Error('Synthetic conflict');
    return url.includes('calendar-days') ? [] : policy;
  }});t.after(view.unmount);await settle();
  view.state.editLimit(category);view.state.limitForm.limit_uzs = '250';
  await view.state.saveLimit(category);await settle();
  assert.equal(view.state.limitEditing, 17);assert.equal(view.state.limitForm.limit_uzs, '250');
  assert.equal(view.state.notice, '');assert.match(text(view.container), /Synthetic conflict/);
});

test('a limit refusal translates to Uzbek while preserving category, amounts and currency', () => {
  const previous = lang.value;
  try {
    setLang('uz');
    const message = tx('Лимит директора по статье «Synthetic equipment» — 100 000 000 UZS; сумма — 100 000 000,01 UZS. Окончательное согласование недоступно. Измените сумму заявки или обратитесь к администратору холдинга для пересмотра лимита.');
    assert.doesNotMatch(message, /[А-Яа-яЁё]/);
    assert.match(message, /Synthetic equipment/);assert.match(message, /100 000 000,01 UZS/);
  } finally {setLang(previous);}
});
