<script setup>
// ВрИО выбранной компании. Список сотрудников компании — /api/users (члены компании, администраторы холдинга
// и действующие ВрИО); сервер повторно проверяет, что замещающий работает именно в этой компании.
import {ref, computed, onMounted} from 'vue';

const props = defineProps({
  api: {type: Function, required: true},
  roles: {type: Object, default: () => ({})},
  company: {type: Object, default: null},
  today: {type: String, default: ''},
  currentUserId: {type: Number, default: null},
});

const rows = ref([]), users = ref([]), error = ref(''), notice = ref(''), busy = ref(false), creating = ref(false), revoking = ref(null), reason = ref('');
const form = ref({user_id: null, replaced_user_id: null, role: 'finance', starts_on: props.today, ends_on: props.today, reason: ''});
const STATE = {active: ['Действует', 'good'], planned: ['Запланировано', 'plan'], expired: ['Истекло', 'gray'], revoked: ['Отменено', 'gray']};
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
async function create() { if (await run(() => props.api('/api/delegations', json(form.value)), 'ВрИО назначен.')) creating.value = false; }
async function revoke(d) { if (await run(() => props.api(`/api/delegations/${d.id}/revoke`, json({reason: reason.value})), 'Замещение отменено.')) { revoking.value = null; reason.value = ''; } }
const dateRu = d => d ? d.split('-').reverse().join('.') : '';
onMounted(load);
</script>

<template>
  <section class="card delegations">
    <div class="card-h"><h3>Временное замещение (ВрИО) · {{ company?.name }}</h3>
      <button class="secondary tiny" :disabled="busy" @click="creating = !creating">{{ creating ? 'Скрыть форму' : 'Назначить ВрИО' }}</button></div>
    <div class="card-b">
      <p class="sub">ВрИО работает под своим логином и на срок замещения получает роль отсутствующего сотрудника в этой компании. После окончания срока права прекращаются автоматически. Собственную заявку ВрИО не согласует; в журнале сохраняются оба сотрудника.</p>
      <p class="sub">ВрИО назначается только из сотрудников {{ company?.name || 'этой компании' }} (или из администраторов холдинга). Сотрудника другой компании сначала переведите сюда в центре администрирования. Смена назначения или архив сотрудника завершают его замещения.</p>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <form v-if="creating" class="form-grid" @submit.prevent="create">
        <label>Роль<select v-model="form.role" required><option v-for="[k, label] in companyRoles" :key="k" :value="k">{{ label }}</option></select></label>
        <label>Кого заменяет<select v-model="form.replaced_user_id" required><option v-for="u in replaceable" :key="u.id" :value="u.id">{{ u.name }}</option></select>
          <small v-if="!replaceable.length" class="sub">В компании нет сотрудника с этой ролью.</small></label>
        <label>ВрИО<select v-model="form.user_id" required><option v-for="u in candidates" :key="u.id" :value="u.id">{{ u.name }} · {{ u.role_label }}</option></select>
          <small v-if="!candidates.length" class="sub">В компании нет другого сотрудника, который может замещать.</small></label>
        <label>С<input v-model="form.starts_on" type="date" required></label>
        <label>По (включительно)<input v-model="form.ends_on" type="date" :min="form.starts_on" required></label>
        <label class="wide">Основание<textarea v-model="form.reason" minlength="10" required placeholder="Например, отпуск по приказу №…"></textarea></label>
        <div class="form-actions wide"><button :disabled="busy">Назначить</button></div>
      </form>
      <div v-if="rows.length" class="table-card rsp-wrap"><table class="rsp"><thead><tr><th>ВрИО</th><th>Заменяет</th><th>Роль</th><th>Срок</th><th>Статус</th><th></th></tr></thead><tbody>
        <template v-for="d in rows" :key="d.id">
          <tr><td data-l="ВрИО"><b>{{ d.user }}</b></td><td data-l="Заменяет">{{ d.replaced }}</td><td data-l="Роль">{{ d.role_label }}</td>
            <td data-l="Срок">{{ dateRu(d.starts_on) }} — {{ dateRu(d.ends_on) }}<small>{{ d.reason }}</small></td>
            <td data-l="Статус"><span class="pill" :class="STATE[d.state][1]">{{ STATE[d.state][0] }}</span></td>
            <td data-l=""><button v-if="['active', 'planned'].includes(d.state)" class="ghost tiny" :disabled="busy" @click="revoking = d.id">Отменить</button></td></tr>
          <tr v-if="revoking === d.id" class="detail-row"><td colspan="6" class="detail-cell">
            <form class="rq-inline" @submit.prevent="revoke(d)"><input v-model="reason" minlength="10" required aria-label="Причина отмены" placeholder="Причина, не короче 10 символов">
              <button class="tiny" :disabled="busy">Отменить замещение</button><button type="button" class="ghost tiny" @click="revoking = null">Назад</button></form>
          </td></tr>
        </template>
      </tbody></table></div>
      <p v-else class="sub">Замещений в этой компании нет.</p>
    </div>
  </section>
</template>
