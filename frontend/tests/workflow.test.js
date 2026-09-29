import {test} from 'node:test';
import assert from 'node:assert/strict';
import {STATUS, ACTION_LABELS, COMMENT_REQUIRED, DOC_KINDS, REQUIRED_DOCS, LEAD_DAYS, steps, stageLabel, decisionOptions,
        canDecide, accountsFor, budgetRows, budgetNote, minDue, missingDocs, periodLabel, fileProblem, MAX_DOC_BYTES, DOC_ACCEPT} from '../src/requests/workflow.js';

const pending = (stage, extra) => ({status: 'pending', approval_stage: stage, route: {policy: 'always', director_required: true}, ...extra});

test('the track shows the accountant check, finance, the director by policy and payment', () => {
  assert.deepEqual(steps(pending('check')).map(s => [s.l, s.c]),
    [['Заявитель', 'done'], ['Проверка', 'now'], ['Фин. директор', ''], ['Директор', ''], ['Оплата', '']]);
  assert.deepEqual(steps(pending('director')).map(s => s.c), ['done', 'done', 'done', 'now', '']);
  const skipped = steps(pending('finance', {route: {policy: 'threshold', director_required: false}}));
  assert.deepEqual(skipped[3], {c: 'skip', l: 'Директор · не участвует'});
  const paid = steps({status: 'paid', checked_by: 7, finance_approved_by: 3, approved_by: 4, route: {policy: 'always', director_required: true}});
  assert.deepEqual(paid.map(s => s.c), ['done', 'done', 'done', 'done', 'done']);
  assert.equal(steps({status: 'cancelled', route: {}})[0].c, 'bad');
  assert.equal(steps({status: 'returned', route: {}})[0].c, 'now');
});

test('only return and close remain as negative actions, and both need a comment', () => {
  assert.equal(STATUS.cancelled, 'Закрыта без оплаты');
  for (const banned of ['reject', 'stop', 'cancel']) assert.ok(!(banned in ACTION_LABELS), banned);
  assert.ok(COMMENT_REQUIRED.includes('return') && COMMENT_REQUIRED.includes('close'));
  assert.ok(!COMMENT_REQUIRED.includes('approve') && !COMMENT_REQUIRED.includes('check'));
});

test('decisions come from the server-allowed actions only', () => {
  const r = pending('finance', {actions: ['edit', 'approve', 'return', 'close', 'pay', 'documents'],
                                route: {policy: 'threshold', director_required: false}});
  assert.deepEqual(decisionOptions(r).map(o => o[0]), ['approve', 'return', 'close']);
  assert.match(decisionOptions(r)[0][1], /директор не участвует/);
  assert.equal(canDecide(r), true);
  assert.equal(canDecide(pending('check', {actions: ['check']})), true);
  assert.equal(canDecide(pending('check', {actions: ['edit']})), false);
  assert.equal(stageLabel(pending('check')), 'Ожидает: проверка бухгалтера');
});

test('the account list follows the chosen channel and currency', () => {
  const accounts = [{id: 1, kind: 'bank', currency: 'UZS'}, {id: 2, kind: 'cash', currency: 'UZS'}, {id: 3, kind: 'bank', currency: 'USD'}];
  assert.deepEqual(accountsFor(accounts, 'bank', 'UZS').map(a => a.id), [1]);
  assert.deepEqual(accountsFor(accounts, 'cash', 'UZS').map(a => a.id), [2]);
  assert.deepEqual(accountsFor(accounts, 'cash', 'EUR'), []);
});

test('the budget card shows limit, used, reserved, available and the rest after the request', () => {
  const card = {budget_set: true, limit: '1000.00', used: '100.00', reserved: '600.00', available: '300.00', after: '-100.00', status: 'soft'};
  assert.deepEqual(budgetRows(card).map(r => r[0]), ['Лимит', 'Использовано', 'Зарезервировано', 'Доступно', 'Останется после заявки']);
  assert.match(budgetNote(card), /Мягкий лимит/);
  assert.equal(budgetNote({budget_set: false}), 'Лимит на этот период не задан.');
  assert.deepEqual(budgetRows({budget_set: false}), []);
  assert.equal(periodLabel('2026-09'), 'сентябрь 2026');
});

test('lead times and required documents match the server rules', () => {
  assert.deepEqual(LEAD_DAYS, {normal: 7, high: 3, urgent: 1});
  assert.equal(minDue({urgent: '2026-09-17'}, 'urgent'), '2026-09-17');
  assert.deepEqual(REQUIRED_DOCS, ['internal', 'contract']);
  assert.deepEqual(Object.keys(DOC_KINDS), ['internal', 'contract', 'other']);
  assert.equal(DOC_KINDS.internal, 'Внутренняя заявка / Индент');
  assert.deepEqual(missingDocs({documents: {internal: 1, contract: 0}}), ['contract']);
  assert.deepEqual(missingDocs({missing_documents: []}), []);
  assert.deepEqual(missingDocs({missing_documents: ['internal']}), ['internal']);
});

test('files are checked by type and size before upload', () => {
  assert.equal(fileProblem({name: 'Счёт.PDF', size: 2048}), '');
  assert.equal(fileProblem({name: 'contract.docx', size: MAX_DOC_BYTES}), '');
  assert.match(fileProblem({name: 'scan.xlsx', size: MAX_DOC_BYTES + 1}), /больше 5 МБ/);
  assert.match(fileProblem({name: 'notes.txt', size: 10}), /PDF, PNG, JPEG, DOCX или XLSX/);
  assert.match(fileProblem({name: 'empty.pdf', size: 0}), /пустой/);
  assert.equal(DOC_ACCEPT, '.pdf,.png,.jpg,.jpeg,.docx,.xlsx');
});

test('the request history reads as plain actions with their comments', async () => {
  const {historyLabel, historyNote, historyFile} = await import('../src/requests/workflow.js');
  assert.equal(historyLabel('Действие по заявке: close'), 'Закрыта без оплаты');
  assert.equal(historyFile(JSON.stringify({kind: 'contract', filename: 'invoice.pdf', version: 2, approval_reset: true})),
    'Договор / Счёт на оплату: invoice.pdf · версия 2 · согласование начато заново');
  assert.equal(historyFile(JSON.stringify({reason: 'x'})), '');
  assert.equal(historyLabel('Добавлен документ заявки'), 'Добавлен документ заявки');
  assert.equal(historyNote(JSON.stringify({reason: 'Счёт выписан на другое юрлицо'})), 'Счёт выписан на другое юрлицо');
  assert.equal(historyNote('не JSON'), '');
});

test('a request sent before 2.14.0 shows no accountant check in its route', () => {
  const legacy = steps({status: 'pending', approval_stage: 'director', finance_approved_by: 3, route: {policy: null, director_required: true}});
  assert.deepEqual(legacy.map(s => s.c), ['done', 'skip', 'done', 'now', '']);
  assert.equal(legacy[1].l, 'Проверка · не требовалась');
  const paid = steps({status: 'paid', finance_approved_by: 3, approved_by: 4, route: {policy: null, director_required: true}});
  assert.deepEqual(paid.map(s => s.c), ['done', 'skip', 'done', 'done', 'done']);
  assert.equal(steps({status: 'draft', route: {policy: null}})[1].l, 'Проверка');
});
