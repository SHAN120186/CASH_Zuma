// Чистая логика центра администрирования: фильтры, счётчики и чтение журнала изменений доступа.
import {tx} from '../i18n/index.js';

export const STATE_CLASS = {ok: 'good', archived: 'gray', no_company: 'warn', conflict: 'bad', no_role: 'warn'};

export function filterUsers(users, {q = '', company = '', role = '', state = ''} = {}) {
  const needle = q.trim().toLowerCase();
  return users.filter(u =>
    (!needle || u.name.toLowerCase().includes(needle) || u.username.toLowerCase().includes(needle)) &&
    (company === '' || u.assignments.some(a => a.company_id === Number(company))) &&
    (!role || u.holding_role === role || u.assignments.some(a => a.role === role)) &&
    (!state || u.state === state));
}

export function stateCounts(users) {
  return users.reduce((acc, u) => ({...acc, [u.state]: (acc[u.state] || 0) + 1}), {});
}

// Конфликт разрешается только явным выбором одной компании.
export const canResolve = u => u.state === 'conflict';

function describe(snapshot, roleName) {
  if (!snapshot) return '—';
  const parts = [];
  if (snapshot.holding_role) parts.push(tx('{role} · все компании', {role: roleName(snapshot.holding_role)}));
  for (const [company, role] of snapshot.assignments || []) parts.push(`${company} · ${role ? roleName(role) : tx('без роли')}`);
  if (snapshot.active === false) parts.push(tx('в архиве'));
  return parts.join(', ') || tx('нет доступа');
}

// Журнал хранит «было / стало / причина» в JSON; старые записи — обычным текстом.
export function historyLine(detail, roleName = r => r) {
  let d;
  try { d = JSON.parse(detail); } catch { return detail ? [detail] : []; }
  if (!d || typeof d !== 'object' || !('after' in d)) return detail ? [detail] : [];
  const lines = [d.before ? tx('Было: {value}', {value: describe(d.before, roleName)}) : tx('Было: учётной записи не было'),
    tx('Стало: {value}', {value: describe(d.after, roleName)})];
  if (d.reason) lines.push(tx('Причина: {reason}', {reason: d.reason}));
  return lines;
}
