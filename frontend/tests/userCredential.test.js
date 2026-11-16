import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent, mount, settle, find, all, text} from './helpers/mountVue.js';
import {lang, setLang} from '../src/i18n/index.js';

const component = await compileComponent(new URL('../src/admin/UserCredential.vue', import.meta.url));
const user = (changes = {}) => ({id: 7, username: 'synthetic.accountant', active: true, credential_state: 'stored', ...changes});
const secret = '  Synthetic-Pass-2026  ';
const button = (view, label) => find(view.container, node => node.tag === 'button' && text(node) === label);
const revealedField = view => find(view.container, node => node.tag === 'input' && 'readonly' in node.props);
const deferred = () => {let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject};};

function fixture(t, api, props = {}) {
  t.mock.timers.enable({apis: ['setTimeout']});
  const keys = ['document', 'navigator'], originals = keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]);
  const listeners = new Set(), copied = [];
  const page = {hidden: false, visibilityState: 'visible', activeElement: null,
    addEventListener(event, listener) {assert.equal(event, 'visibilitychange'); listeners.add(listener);},
    removeEventListener(event, listener) {assert.equal(event, 'visibilitychange'); listeners.delete(listener);}};
  Object.defineProperty(globalThis, 'document', {value: page, configurable: true, writable: true});
  Object.defineProperty(globalThis, 'navigator', {value: {clipboard: {writeText: async value => copied.push(value)}}, configurable: true, writable: true});
  const updated = [], view = mount(component, {api, user: user(), enabled: true, onUpdated: (...args) => updated.push(args), ...props});
  t.after(() => {view.unmount(); for (const [key, descriptor] of originals) if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];});
  return {view, copied, updated, listeners, hidePage() {page.hidden = true; page.visibilityState = 'hidden'; for (const listener of listeners) listener();}, showPage() {page.hidden = false; page.visibilityState = 'visible'; for (const listener of listeners) listener();}};
}

test('credential panel starts masked and makes no automatic request or polling request', async t => {
  const calls = [], {view} = fixture(t, async (...args) => {calls.push(args); return {};});
  await settle(); t.mock.timers.tick(180000); await settle();
  assert.deepEqual(calls, []);
  assert.equal(revealedField(view).props.type, 'password');
  assert.equal(revealedField(view).value, '');
  assert.equal(button(view, 'Показать пароль').props.disabled, false);
  assert.equal(button(view, 'Сохранить известный пароль'), undefined);
});

test('explicit reveal sends one audited POST, copies exact spaces and hide erases the value', async t => {
  const calls = [], {view, copied} = fixture(t, async (...args) => {calls.push(args); return {username: user().username, password: secret};});
  await button(view, 'Показать пароль').props.onClick(); await settle();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/api/admin/users/7/credential/reveal');
  assert.equal(calls[0][1].method, 'POST');
  assert.deepEqual(JSON.parse(calls[0][1].body), {reason: 'Восстановление доступа сотрудника'});
  assert.equal(revealedField(view).props.type, 'text');
  assert.equal(revealedField(view).value, secret);
  await button(view, 'Скопировать').props.onClick(); await settle();
  assert.deepEqual(copied, [secret]); assert.match(text(view.container), /Пароль скопирован/);
  button(view, 'Скрыть').props.onClick(); await settle();
  assert.equal(view.state.password, ''); assert.equal(revealedField(view).value, '');
  assert.equal(revealedField(view).props.type, 'password');
  await view.state.copy(); assert.equal(copied.length, 1, 'hidden values cannot be copied again');
});

test('revealed password is erased at sixty seconds and copying never extends its lifetime', async t => {
  const {view} = fixture(t, async () => ({username: user().username, password: secret}));
  await view.state.reveal(); await settle();
  t.mock.timers.tick(59999); await settle(); assert.equal(revealedField(view).value, secret);
  await view.state.copy(); t.mock.timers.tick(1); await settle();
  assert.equal(view.state.password, ''); assert.equal(revealedField(view).value, '');
  assert.equal(button(view, 'Скопировать'), undefined); assert.match(text(view.container), /скрыт через 60 секунд/);
});

test('a late reveal from a previous user cannot appear on the new card', async t => {
  const first = deferred(), second = deferred(), calls = [];
  const {view} = fixture(t, (...args) => {calls.push(args); return calls.length === 1 ? first.promise : second.promise;});
  const old = view.state.reveal(); await settle(); assert.equal(view.state.busy, true);
  view.app._instance.props.user = user({id: 9, username: 'other.synthetic'}); await settle();
  assert.equal(view.state.busy, false); assert.equal(view.state.password, '');
  const current = view.state.reveal();
  first.resolve({username: user().username, password: secret}); await old; await settle();
  assert.equal(view.state.password, ''); assert.equal(view.state.busy, true);
  second.resolve({username: 'other.synthetic', password: 'Other-synthetic-value'}); await current; await settle();
  assert.equal(revealedField(view).value, 'Other-synthetic-value');
  assert.equal(calls[1][0], '/api/admin/users/9/credential/reveal');
});

test('switching to a hidden tab erases visible and entered passwords and discards pending replies', async t => {
  const pending = deferred(), {view, hidePage, showPage} = fixture(t, () => pending.promise);
  const request = view.state.reveal(); hidePage(); await settle();
  pending.resolve({username: user().username, password: secret}); await request; await settle();
  assert.equal(view.state.password, ''); assert.equal(view.state.busy, false);
  showPage(); view.app._instance.props.user = user({credential_state: 'missing'}); await settle();
  button(view, 'Сохранить известный пароль').props.onClick(); await settle();
  view.state.knownPassword = secret; view.state.reason = 'Synthetic recovery reason';
  hidePage(); await settle();
  assert.equal(view.state.knownPassword, ''); assert.equal(view.state.reason, ''); assert.equal(view.state.importing, false);
});

test('a target change erases already revealed and imported input without sending either to the next account', async t => {
  const calls = [], {view} = fixture(t, async (...args) => {calls.push(args); return {username: user().username, password: secret};});
  await view.state.reveal();
  view.app._instance.props.user = user({id: 9, username: 'other.synthetic', credential_state: 'missing'}); await settle();
  assert.equal(revealedField(view).value, '');
  view.state.openImport(); view.state.knownPassword = secret; view.state.reason = 'Synthetic recovery reason';
  view.app._instance.props.user = user({id: 10, username: 'third.synthetic', credential_state: 'missing'}); await settle();
  assert.equal(view.state.knownPassword, ''); assert.equal(view.state.reason, ''); assert.equal(view.state.importing, false);
  await view.state.save(); assert.equal(calls.length, 1, 'changing cards never saves or reveals on behalf of the previous card');
});

test('unmount erases component secrets, unregisters visibility handling and ignores a late response', async t => {
  const pending = deferred(), {view, listeners} = fixture(t, () => pending.promise);
  assert.equal(listeners.size, 1);
  const response = view.state.reveal(); view.unmount();
  pending.resolve({username: user().username, password: secret}); await response; await settle();
  assert.equal(view.state.password, ''); assert.equal(view.state.knownPassword, ''); assert.equal(listeners.size, 0);
});

test('archiving or losing vault permission clears the password and disables all credential actions', async t => {
  const calls = [], {view} = fixture(t, async (...args) => {calls.push(args); return {username: user().username, password: secret};});
  await view.state.reveal(); view.app._instance.props.enabled = false; await settle();
  assert.equal(view.state.password, ''); assert.equal(button(view, 'Показать пароль').props.disabled, true);
  await view.state.reveal(); assert.equal(calls.length, 1);
  view.app._instance.props.enabled = true; view.app._instance.props.user = user({active: false, credential_state: 'archived'}); await settle();
  assert.match(text(view.container), /в архиве/); assert.equal(button(view, 'Показать пароль').props.disabled, true);
  view.state.openImport(); await view.state.save(); assert.equal(calls.length, 1);
  assert.equal(button(view, 'Сохранить известный пароль'), undefined);
});

test('missing and stale passwords offer explicit verified import, requiring a reason and preserving password spaces', async t => {
  const calls = [], {view, updated} = fixture(t, async (...args) => {calls.push(args); return {ok: true, credential_state: 'stored'};}, {user: user({credential_state: 'missing'})});
  assert.match(text(view.container), /пароль не сохранён/);
  assert.equal(button(view, 'Показать пароль').props.disabled, true); assert.equal(all(view.container, n => n.tag === 'form').length, 0);
  button(view, 'Сохранить известный пароль').props.onClick(); await settle();
  view.state.knownPassword = secret; view.state.reason = 'short'; await settle();
  assert.equal(view.state.canSave, false); await view.state.save(); assert.equal(calls.length, 0);
  view.state.reason = '  Подтверждённый пароль сотрудника  '; await view.state.save(); await settle();
  assert.equal(calls.length, 1); assert.equal(calls[0][0], '/api/admin/users/7/credential/import'); assert.equal(calls[0][1].method, 'POST');
  assert.deepEqual(JSON.parse(calls[0][1].body), {password: secret, reason: 'Подтверждённый пароль сотрудника'});
  assert.equal(updated.length, 1); assert.equal(view.state.knownPassword, ''); assert.equal(view.state.importing, false);
  assert.match(text(view.container), /Пароль сотрудника не изменён/);
  assert.equal(view.state.password, '', 'import never reveals the password automatically');
  view.app._instance.props.user = user({credential_state: 'stale'}); await settle();
  assert.match(text(view.container), /больше не соответствует/); assert.ok(button(view, 'Сохранить известный пароль'));
});

test('wrong known password keeps the import form and reason without showing a success or resetting the user password', async t => {
  const calls = [], {view, updated} = fixture(t, async (...args) => {calls.push(args); throw Error('Пароль не соответствует учётной записи.');}, {user: user({credential_state: 'stale'})});
  view.state.openImport(); view.state.knownPassword = secret; view.state.reason = 'Synthetic credential recovery';
  await view.state.save(); await settle();
  assert.equal(view.state.importing, true); assert.equal(view.state.knownPassword, secret); assert.equal(view.state.reason, 'Synthetic credential recovery');
  assert.equal(view.state.password, ''); assert.equal(view.state.busy, false); assert.equal(view.state.notice, ''); assert.equal(updated.length, 0);
  assert.match(text(view.container), /Пароль не соответствует/); assert.equal(calls.length, 1);
  assert.match(calls[0][0], /\/credential\/import$/);
  t.mock.timers.tick(60000); await settle(); assert.equal(view.state.knownPassword, '', 'failed input still has a bounded memory lifetime');
});

test('import permits an existing short legacy password but never an empty password', async t => {
  const calls = [], {view} = fixture(t, async (...args) => {calls.push(args); return {ok: true, credential_state: 'stored'};}, {user: user({credential_state: 'missing'})});
  view.state.openImport(); view.state.reason = 'Synthetic legacy recovery';
  assert.equal(view.state.canSave, false); await view.state.save(); assert.equal(calls.length, 0);
  view.state.knownPassword = 'L'; await settle(); assert.equal(view.state.canSave, true);
  await view.state.save(); assert.equal(JSON.parse(calls[0][1].body).password, 'L');
});

test('locked, unknown and disabled states cannot reveal or import and a server error never reveals a value', async t => {
  let count = 0; const {view} = fixture(t, async () => {count++; throw Error('Сохранённый пароль сейчас недоступен.');}, {user: user({credential_state: 'locked'})});
  assert.match(text(view.container), /сейчас недоступен/); assert.equal(button(view, 'Сохранить известный пароль'), undefined);
  await view.state.reveal(); view.state.openImport(); assert.equal(count, 0);
  view.app._instance.props.user = user({credential_state: 'unexpected'}); await settle();
  assert.match(text(view.container), /Состояние пароля неизвестно/); assert.equal(button(view, 'Показать пароль').props.disabled, true);
  view.app._instance.props.user = user(); await settle(); await view.state.reveal(); await settle();
  assert.equal(count, 1); assert.equal(revealedField(view).value, ''); assert.match(text(view.container), /сейчас недоступен/);
});

test('a mismatched response is rejected and Uzbek labels explain the same explicit workflow', async t => {
  const previous = lang.value; setLang('uz'); t.after(() => setLang(previous));
  const {view} = fixture(t, async () => ({username: 'other.synthetic', password: secret}));
  assert.ok(button(view, 'Parolni ko‘rsatish'));
  await view.state.reveal(); await settle(); assert.equal(view.state.password, ''); assert.match(text(view.container), /to‘liq qaytarmadi/);
  view.app._instance.props.user = user({credential_state: 'missing'}); await settle();
  assert.match(text(view.container), /Amaldagi parol saqlanmagan/); assert.ok(button(view, 'Ma’lum parolni saqlash'));
});
