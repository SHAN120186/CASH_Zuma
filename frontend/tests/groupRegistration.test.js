import {test} from 'node:test';
import assert from 'node:assert/strict';
import {groupCodePayload, groupCompanyInfo, registrationCommand, issueGroupCode} from '../src/admin/groupRegistration.js';

const companies = [{id: 2, code: 'ZUMA', name: 'Zuma'}, {id: 3, code: 'UZGERMED', name: 'Узгермед'}];
const payload = {chat_id: -1001234567890, company_code: 'ZUMA'};
const response = {code: 'ABCDEFGHJKMNPQRS2345', chat_id: payload.chat_id, company_code: 'ZUMA', expires_at: '2026-10-04T18:30:00Z'};

test('group registration accepts only a precise negative group ID and an available company', () => {
  assert.deepEqual(groupCodePayload('  -1001234567890 ', 'ZUMA', companies), payload);
  assert.equal(groupCodePayload('-222', 'UZGERMED', companies).company_code, 'UZGERMED');
  for (const id of ['1234', '0', '-0', '-1e9', '-12.5', '-9007199254740992', '']) {
    assert.throws(() => groupCodePayload(id, 'ZUMA', companies));
  }
  assert.throws(() => groupCodePayload('-1234', 'UNKNOWN', companies));
  assert.throws(() => groupCodePayload('-1234', 'UNASSIGNED', [{code: 'UNASSIGNED'}]));
});

test('a registration command must match the requested group and company', () => {
  assert.equal(registrationCommand(response, payload), '/register ABCDEFGHJKMNPQRS2345');
  for (const changed of [{chat_id: -999}, {company_code: 'UZGERMED'}, {code: 'bad'}, {expires_at: 'bad'}]) {
    assert.throws(() => registrationCommand({...response, ...changed}, payload));
  }
});

test('group management shows a summary only for exactly one active business company', () => {
  const zuma = {...companies[0], active: true};
  const uzgermed = {...companies[1], active: true};
  assert.deepEqual(groupCompanyInfo({companies: [zuma]}), {label: 'Zuma', summaryEnabled: true});
  assert.deepEqual(groupCompanyInfo({companies: [uzgermed]}), {label: 'Узгермед', summaryEnabled: true});
  assert.deepEqual(groupCompanyInfo({companies: [zuma, uzgermed]}), {label: 'Требуется одна компания', summaryEnabled: false});
  assert.equal(groupCompanyInfo({companies: [zuma, {...uzgermed, active: false}]}).summaryEnabled, false);
  assert.deepEqual(groupCompanyInfo({companies: [{...zuma, active: false}]}), {label: 'Zuma (отключена)', summaryEnabled: false});
  for (const group of [undefined, {companies: []}, {companies: [{code: 'UNASSIGNED', active: true}]}, {companies: [{name: 'Неизвестная', active: true}]}]) {
    assert.deepEqual(groupCompanyInfo(group), {label: 'Компания не выбрана', summaryEnabled: false});
  }
});

test('issuing a group code sends exactly one admin request and does not retry a network failure', async () => {
  const calls = [];
  const api = async (...args) => {calls.push(args); return response;};
  assert.equal((await issueGroupCode(api, payload)).command, '/register ABCDEFGHJKMNPQRS2345');
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/api/admin/telegram-group-codes');
  assert.equal(calls[0][1].method, 'POST');
  assert.deepEqual(JSON.parse(calls[0][1].body), payload);
  let attempts = 0;
  await assert.rejects(issueGroupCode(async () => {attempts++; throw new Error('network');}, payload), /network/);
  assert.equal(attempts, 1);
});
