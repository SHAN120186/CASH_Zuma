<script setup>
// ВрИО выбранной компании. Список сотрудников компании — /api/users (члены компании, администраторы холдинга
// и действующие ВрИО); сервер повторно проверяет, что замещающий работает именно в этой компании.
import {ref, computed, onMounted} from 'vue';
import {tx, N_} from '../i18n/index.js';

const props = defineProps({
  api: {type: Function, required: true},
  roles: {type: Object, default: () => ({})},
  company: {type: Object, default: null},
  today: {type: String, default: ''},
  currentUserId: {type: Number, default: null},
});

const rows = ref([]), users = ref([]), error = ref(''), notice = ref(''), busy = ref(false), creating = ref(false), revoking = ref(null), reason = ref('');
const form = ref({user_id: null, replaced_user_id: null, role: 'finance', starts_on: props.today, ends_on: props.today, reason: ''});
const STATE = {active: [N_('Действует'), 'good'], planned: [N_('Запланировано'), 'plan'], expired: [N_('Истекло'), 'gray'], revoked: [N_('Отменено'), 'gray'],
  stale: [N_('Без основания'), 'warn']};
const companyRoles = computed(() => Object.entries(props.roles).filter(([k]) => !['admin', 'founder'].includes(k)));
// Заменить можно сотрудника с этой ролью в компании. ВрИО — сотрудник этой же компании (с назначением здесь)
// или администратор холдинга; не учредитель, не сам назначающий и не заменяемый.
const replaceable = computed(() => users.value.filter(u => u.active && u.company_role === form.value.role && !u.substitute));
const candidates = computed(() => users.value.filter(u => u.active && u.holding_role !== 'founder' && u.id !== props.currentUserId
  && u.id !== form.value.replaced_user_id && (u.holding_role === 'admin' || (u.company_role && !u.substitute))));

async function load() {
  error.value = '';
  try { [rows.value, users.value] = await Promise.all([props.api('/api/delegations'), props.api('/api/users')]); } catch (e) { error.value = e.message; }
}
async function run(fn, message) {
  busy.value = true; error.value = ''; notice.value = '';
  try { await fn(); await load(); notice.value = message; return true; } catch (e) { error.value = e.message; return false; } finally { busy.value = false; }
}
const json = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
async function create() { if (await run(() => props.api('/api/delegations', json(form.value)), N_('ВрИО назначен.'))) creating.value = false; }
async function revoke(d) { if (await run(() => props.api(`/api/delegations/${d.id}/revoke`, json({reason: reason.value})), N_('Замещение отменено.'))) { revoking.value = null; reason.value = ''; } }
const dateRu = d => d ? d.split('-').reverse().join('.') : '';
onMounted(load);
</script>

<template>
  <section class="card delegations">
    <div class="card-h"><h3>{{ tx('Временное замещение (ВрИО) · {company}', {company: company?.name ?? ''}) }}</h3>
      <button class="secondary tiny" :disabled="busy" @click="creating = !creating">{{ creating ? tx('Скрыть форму') : tx('Назначить ВрИО') }}</button></div>
    <div class="card-b">
      <p class="sub">{{ tx('ВрИО работает под своим логином и на срок замещения получает роль отсутствующего сотрудника в этой компании. После окончания срока права прекращаются автоматически. Собственную заявку ВрИО не согласует; в журнале сохраняются оба сотрудника.') }}</p>
      <p class="sub">{{ tx('ВрИО назначается только из сотрудников {company} (или из администраторов холдинга). Сотрудника другой компании сначала переведите сюда в центре администрирования. Смена назначения или архив сотрудника завершают его замещения.', {company: company?.name || tx('этой компании')}) }}</p>
      <p v-if="notice" class="notice" role="status">{{ tx(notice) }}</p>
      <p v-if="error" class="error" role="alert">{{ tx(error) }}</p>
      <form v-if="creating" class="form-grid" @submit.prevent="create">
        <label>{{ tx('Роль') }}<select v-model="form.role" required><option v-for="[k, label] in companyRoles" :key="k" :value="k">{{ tx(label) }}</option></select></label>
        <label>{{ tx('Кого заменяет') }}<select v-model="form.replaced_user_id" required><option v-for="u in replaceable" :key="u.id" :value="u.id">{{ u.name }}</option></select>
          <small v-if="!replaceable.length" class="sub">{{ tx('В компании нет сотрудника с этой ролью.') }}</small></label>
        <label>{{ tx('ВрИО') }}<select v-model="form.user_id" required><option v-for="u in candidates" :key="u.id" :value="u.id">{{ u.name }} · {{ tx(u.role_label) }}</option></select>
          <small v-if="!candidates.length" class="sub">{{ tx('В компании нет другого сотрудника, который может замещать.') }}</small></label>
        <label>{{ tx('С') }}<input v-model="form.starts_on" type="date" required></label>
        <label>{{ tx('По (включительно)') }}<input v-model="form.ends_on" type="date" :min="form.starts_on" required></label>
        <label class="wide">{{ tx('Основание') }}<textarea v-model="form.reason" minlength="10" required :placeholder="tx('Например, отпуск по приказу №…')"></textarea></label>
        <div class="form-actions wide"><button :disabled="busy">{{ tx('Назначить') }}</button></div>
      </form>
      <div v-if="rows.length" class="table-card rsp-wrap"><table class="rsp"><thead><tr><th>{{ tx('ВрИО') }}</th><th>{{ tx('Заменяет') }}</th><th>{{ tx('Роль') }}</th><th>{{ tx('Срок') }}</th><th>{{ tx('Статус') }}</th><th></th></tr></thead><tbody>
        <template v-for="d in rows" :key="d.id">
          <tr><td :data-l="tx('ВрИО')"><b>{{ d.user }}</b></td><td :data-l="tx('Заменяет')">{{ d.replaced }}</td><td :data-l="tx('Роль')">{{ tx(d.role_label) }}</td>
            <td :data-l="tx('Срок')">{{ dateRu(d.starts_on) }} — {{ dateRu(d.ends_on) }}<small>{{ d.reason }}</small></td>
            <td :data-l="tx('Статус')"><span class="pill" :class="STATE[d.state][1]">{{ tx(STATE[d.state][0]) }}</span></td>
            <td data-l=""><button v-if="['active', 'planned', 'stale'].includes(d.state)" class="ghost tiny" :disabled="busy" @click="revoking = d.id">{{ tx('Отменить') }}</button></td></tr>
          <tr v-if="revoking === d.id" class="detail-row"><td colspan="6" class="detail-cell">
            <form class="rq-inline" @submit.prevent="revoke(d)"><input v-model="reason" minlength="10" required :aria-label="tx('Причина отмены')" :placeholder="tx('Причина, не короче 10 символов')">
              <button class="tiny" :disabled="busy">{{ tx('Отменить замещение') }}</button><button type="button" class="ghost tiny" @click="revoking = null">{{ tx('Назад') }}</button></form>
          </td></tr>
        </template>
      </tbody></table></div>
      <p v-else class="sub">{{ tx('Замещений в этой компании нет.') }}</p>
    </div>
  </section>
</template>
