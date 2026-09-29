import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {filterUsers, stateCounts, historyLine, canResolve} from '../src/admin/adminCenter.js';

const users = [
  {id: 1, name: 'Азиз Каримов', username: 'zuma.aziz', holding_role: null, state: 'ok', assignments: [{company_id: 2, role: 'procurement'}]},
  {id: 2, name: 'Нодира', username: 'uzgermed.nodira', holding_role: null, state: 'conflict', assignments: [{company_id: 1, role: 'finance'}, {company_id: 2, role: 'finance'}]},
  {id: 3, name: 'Владелец', username: 'owner', holding_role: 'founder', state: 'ok', assignments: []},
  {id: 4, name: 'Старый логин', username: 'old', holding_role: null, state: 'archived', assignments: []},
];

test('search, company, role and state filters combine', () => {
  assert.deepEqual(filterUsers(users, {q: 'ZUMA'}).map(u => u.id), [1]);
  assert.deepEqual(filterUsers(users, {company: '1'}).map(u => u.id), [2]);
  assert.deepEqual(filterUsers(users, {role: 'founder'}).map(u => u.id), [3]);
  assert.deepEqual(filterUsers(users, {state: 'conflict', role: 'finance'}).map(u => u.id), [2]);
  assert.deepEqual(stateCounts(users), {ok: 2, conflict: 1, archived: 1});
  assert.equal(canResolve(users[1]), true);
  assert.equal(canResolve(users[0]), false);
});

test('access history shows before, after and the reason', () => {
  const detail = JSON.stringify({before: {active: true, holding_role: null, assignments: [['ZUMA', 'finance'], ['UZGERMED', 'finance']]},
                                 after: {active: true, holding_role: null, assignments: [['ZUMA', 'finance']]}, reason: 'Работает только в Zuma'});
  const names = {finance: 'Финансовый директор'};
  assert.deepEqual(historyLine(detail, r => names[r] || r), [
    'Было: ZUMA · Финансовый директор, UZGERMED · Финансовый директор',
    'Стало: ZUMA · Финансовый директор',
    'Причина: Работает только в Zuma']);
  assert.equal(historyLine(JSON.stringify({before: null, after: {assignments: [['ZUMA', 'finance']]}, reason: 'Приём на работу'}), r => names[r] || r)[0],
               'Было: учётной записи не было');
  assert.deepEqual(historyLine('обычный текст'), ['обычный текст']);
  assert.deepEqual(historyLine(''), []);
});

test('the admin center never shows or stores a password beyond the one-time notice', () => {
  const source = readFileSync(new URL('../src/admin/AdminCenter.vue', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /localStorage|sessionStorage/, 'temporary passwords stay in memory only');
  assert.match(source, /Показывается один раз/);
  assert.doesNotMatch(source, /\/api\/users\//, 'the registry uses the holding admin API only');
});
