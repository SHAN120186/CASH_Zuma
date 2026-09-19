import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadRecentLogins, rememberLogin, matchingLogins, offerPasswordSave} from '../src/loginPreferences.js';

function storage(initial = null) {
  let saved = initial;
  return {getItem: () => saved, setItem: (_key, value) => {saved = value;}};
}

test('only the last three unique successful usernames survive a reload', () => {
  const local = storage();
  let recent = [];
  for (const username of ['director', 'cashier', 'accountant', 'admin', 'cashier']) {
    recent = rememberLogin(username, recent, () => local);
  }
  assert.deepEqual(loadRecentLogins(() => local), ['cashier', 'admin', 'accountant']);
  assert.deepEqual(JSON.parse(local.getItem()), ['cashier', 'admin', 'accountant']);
});

test('corrupt or unexpected stored values do not break the login screen', () => {
  for (const value of ['{', '{}', 'null', '42']) {
    assert.deepEqual(loadRecentLogins(() => storage(value)), []);
  }
  const value = JSON.stringify([' admin ', '', null, {password: 'not-a-username'}, 'admin', 'a'.repeat(81), 'cashier']);
  assert.deepEqual(loadRecentLogins(() => storage(value)), ['admin', 'cashier']);
});

test('denied storage access and quota errors do not prevent login', () => {
  const denied = () => {throw new Error('Storage access denied');};
  assert.deepEqual(loadRecentLogins(denied), []);
  assert.deepEqual(rememberLogin('admin', ['cashier'], denied), ['admin', 'cashier']);
  assert.deepEqual(rememberLogin('admin', [], () => ({setItem() {throw new Error('Quota exceeded');}})), ['admin']);
});

test('suggestions filter three recent usernames as the user types', () => {
  const recent = ['admin', 'cashier', 'accountant'];
  assert.deepEqual(matchingLogins(recent, ''), recent);
  assert.deepEqual(matchingLogins(recent, '  CA '), ['cashier']);
  assert.deepEqual(matchingLogins(recent, 'unknown'), []);
});

test('native password storage is never requested on unsupported or insecure browsers', () => {
  let calls = 0;
  const browser = {isSecureContext: false, PasswordCredential: class {}, navigator: {credentials: {store() {calls++;}}}};
  offerPasswordSave({username: 'test', password: 'synthetic-test-password'}, browser);
  offerPasswordSave({username: 'test', password: 'synthetic-test-password'}, {isSecureContext: true, navigator: {}});
  assert.equal(calls, 0);
});

test('a pending browser prompt does not hold up the application', () => {
  let saved;
  const browser = {isSecureContext: true, PasswordCredential: class {constructor(data) {Object.assign(this, data);}},
    navigator: {credentials: {store(credential) {saved = credential; return new Promise(() => {});}}}};
  assert.equal(offerPasswordSave({username: 'test', password: 'synthetic-test-password'}, browser), undefined);
  assert.equal(saved.id, 'test');
  assert.equal(saved.password, 'synthetic-test-password');
});

test('password-manager rejection or constructor errors do not break sign-in', async () => {
  const browser = {isSecureContext: true, PasswordCredential: class {}, navigator: {credentials: {store() {return Promise.reject(new Error('Declined'));}}}};
  offerPasswordSave({username: 'test', password: 'synthetic-test-password'}, browser);
  browser.PasswordCredential = class {constructor() {throw new Error('Unavailable');}};
  assert.doesNotThrow(() => offerPasswordSave({username: 'test', password: 'synthetic-test-password'}, browser));
  await new Promise(resolve => setImmediate(resolve));
});
