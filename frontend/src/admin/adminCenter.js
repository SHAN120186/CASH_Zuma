// Чистая логика центра администрирования: фильтры, счётчики и чтение журнала изменений доступа.
import {tx} from '../i18n/index.js';

export const STATE_CLASS = {ok: 'good', archived: 'gray', no_company: 'warn', conflict: 'bad', no_role: 'warn',company_suspended:'warn'};

// A role belongs to the selected company. Holding roles apply across companies;
// invalid/archived assignments stay visible with their actual access state.
export function companyAccessRows(users, companyId, {q = '', role = '', state = ''} = {}) {
  const needle = q.trim().toLowerCase();
  return users.flatMap(user => {
    const assignment = user.assignments.find(a => a.company_id === Number(companyId));
    if (!user.holding_role && !assignment) return [];
    const selectedRole = user.holding_role || assignment?.role;
    if (needle && !user.name.toLowerCase().includes(needle) && !user.username.toLowerCase().includes(needle)) return [];
    if (role && selectedRole !== role || state && user.state !== state) return [];
    return [{user, assignment, role: selectedRole}];
  });
}

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
  if (typeof d.before === 'boolean' && typeof d.after === 'boolean') {
    const label = on => on ? tx('Включена') : tx('Отключена');
    const lines = [tx('Было: {value}', {value: label(d.before)}), tx('Стало: {value}', {value: label(d.after)})];
    if (d.reason) lines.push(tx('Причина: {reason}', {reason: d.reason}));
    return lines;
  }
  const lines = [d.before ? tx('Было: {value}', {value: describe(d.before, roleName)}) : tx('Было: учётной записи не было'),
    tx('Стало: {value}', {value: describe(d.after, roleName)})];
  if (d.reason) lines.push(tx('Причина: {reason}', {reason: d.reason}));
  return lines;
}
