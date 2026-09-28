<script setup>
import {ref, computed, onMounted, onUnmounted, nextTick} from 'vue';
import {STATE_CLASS, historyLine, filterUsers, stateCounts, canResolve} from './adminCenter.js';

const props = defineProps({
  api: {type: Function, required: true},
  currentUserId: {type: Number, required: true},
});

const meta = ref(null), users = ref([]), error = ref(''), notice = ref(''), busy = ref(false);
const filters = ref({q: '', company: '', role: '', state: ''});
const card = ref(null), form = ref(null), secret = ref(null), dialog = ref(null);
const journal = ref(null), journalPage = ref(1);

const shown = computed(() => filterUsers(users.value, filters.value));
const counts = computed(() => stateCounts(users.value));
const roleName = r => (meta.value?.company_roles.find(x => x[0] === r) || meta.value?.holding_roles.find(x => x[0] === r) || [r, r])[1];
const json = (method, body) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});

async function load() {
  error.value = '';
  try {
    if (!meta.value) meta.value = await props.api('/api/admin/meta');
    users.value = await props.api('/api/admin/users');
    if (card.value) card.value = await props.api(`/api/admin/users/${card.value.id}`);
  } catch (e) { error.value = e.message; }
}

async function openCard(u) { form.value = null; secret.value = null; card.value = await props.api(`/api/admin/users/${u.id}`); await nextTick(); dialog.value?.querySelector('button')?.focus(); }
function closeCard() { if (!busy.value) { card.value = null; form.value = null; secret.value = null; } }

// Каждое действие — отдельная форма с обязательной причиной; сервер повторно проверяет права.
function start(kind) {
  const c = card.value, firstCompany = meta.value.companies[0]?.id ?? null;
  const base = {reason: ''};
  if (kind === 'name') form.value = {kind, ...base, name: c.name};
  if (kind === 'assign') form.value = {kind, ...base, company_id: c.company_id ?? c.assignments[0]?.company_id ?? firstCompany, role: c.company_role || c.assignments[0]?.role || 'employee'};
  if (kind === 'holding') form.value = {kind, ...base, holding_role: c.holding_role || '', company_id: firstCompany, role: 'employee'};
  if (kind === 'restore') form.value = {kind, ...base, holding_role: '', company_id: firstCompany, role: 'employee'};
  if (['archive', 'password', 'sessions'].includes(kind)) form.value = {kind, ...base};
}
function startCreate() { card.value = null; secret.value = null; form.value = {kind: 'create', username: '', name: '', holding_role: '', company_id: meta.value.companies[0]?.id ?? null, role: 'employee', reason: ''}; }

async function submit() {
  const f = form.value, id = card.value?.id;
  const holding = f.holding_role || null;
  const staff = holding ? {} : {company_id: Number(f.company_id), role: f.role};
  const calls = {
    create: () => props.api('/api/admin/users', json('POST', {username: f.username.trim().toLowerCase(), name: f.name, holding_role: holding, ...staff, reason: f.reason})),
    name: () => props.api(`/api/admin/users/${id}`, json('PUT', {name: f.name, reason: f.reason})),
    assign: () => props.api(`/api/admin/users/${id}/assignment`, json('PUT', {company_id: Number(f.company_id), role: f.role, reason: f.reason})),
    holding: () => props.api(`/api/admin/users/${id}/holding`, json('PUT', {holding_role: holding, ...staff, reason: f.reason})),
    archive: () => props.api(`/api/admin/users/${id}/archive`, json('POST', {reason: f.reason})),
    restore: () => props.api(`/api/admin/users/${id}/restore`, json('POST', {holding_role: holding, ...staff, reason: f.reason})),
    password: () => props.api(`/api/admin/users/${id}/password`, json('POST', {reason: f.reason})),
    sessions: () => props.api(`/api/admin/users/${id}/sessions/end`, json('POST', {reason: f.reason})),
    unassign: () => props.api(`/api/admin/users/${id}/unassign`, json('POST', {company_id: f.company_id, reason: f.reason})),
  };
  busy.value = true; error.value = ''; notice.value = '';
  try {
    const result = await calls[f.kind]();
    if (result?.temporary_password) secret.value = {login: result.user.username, name: result.user.name, password: result.temporary_password};
    if (f.kind === 'create') card.value = {id: result.user.id};
    form.value = null;
    notice.value = 'Сохранено. Изменение записано в журнал.';
    await load();
  } catch (e) { error.value = e.message; } finally { busy.value = false; }
}

function unassign(a) { form.value = {kind: 'unassign', company_id: a.company_id, company: a.company, reason: ''}; }

async function loadJournal(page = 1) {
  error.value = '';
  try { journal.value = await props.api(`/api/admin/audit?page=${page}&page_size=50`); journalPage.value = page; }
  catch (e) { error.value = e.message; }
}

async function copy(text) { try { await navigator.clipboard.writeText(text); notice.value = 'Пароль скопирован.'; } catch { notice.value = 'Скопируйте пароль вручную.'; } }
const when = s => s ? new Date(s.replace(' ', 'T') + 'Z').toLocaleString('ru-RU', {timeZone: 'Asia/Tashkent', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'}) : '—';
function onKey(e) { if (e.key === 'Escape') { if (form.value && !busy.value) form.value = null; else closeCard(); } }
onMounted(() => { load(); document.addEventListener('keydown', onKey); });
onUnmounted(() => document.removeEventListener('keydown', onKey));
</script>

<template>
  <section class="admin-center">
    <div class="tb"><div><h3>Центр администрирования</h3><p class="sub">Все учётные записи холдинга. Действия не зависят от выбранной компании и записываются в журнал.</p></div>
      <div class="grow actions"><button class="secondary" @click="journal ? journal = null : loadJournal()">{{ journal ? 'Скрыть журнал холдинга' : 'Журнал холдинга' }}</button>
        <button :disabled="!meta" @click="startCreate">Новый пользователь</button></div></div>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="error && !card && !form" class="error" role="alert">{{ error }}</p>

    <div class="admin-chips" role="group" aria-label="Состояние доступа">
      <button v-for="(label, key) in meta?.states || {}" :key="key" class="ghost tiny" :class="{on: filters.state === key}" :aria-pressed="filters.state === key"
              @click="filters.state = filters.state === key ? '' : key"><span class="pill" :class="STATE_CLASS[key]">{{ label }} · {{ counts[key] || 0 }}</span></button>
    </div>
    <div class="admin-filters">
      <label class="search"><span class="sr-only">Поиск</span><input v-model="filters.q" type="search" placeholder="Имя или логин" aria-label="Поиск по имени и логину"></label>
      <select v-model="filters.company" aria-label="Компания"><option value="">Все компании</option><option v-for="c in meta?.companies || []" :key="c.id" :value="c.id">{{ c.name }}</option></select>
      <select v-model="filters.role" aria-label="Роль"><option value="">Все роли</option>
        <option v-for="[k, label] in [...(meta?.holding_roles || []), ...(meta?.company_roles || [])]" :key="k" :value="k">{{ label }}</option></select>
    </div>

    <div class="table-card rsp-wrap"><table class="rsp"><thead><tr><th>Сотрудник</th><th>Доступ</th><th>Состояние</th><th></th></tr></thead><tbody>
      <tr v-for="u in shown" :key="u.id">
        <td data-l="Сотрудник"><b>{{ u.name }}</b><small>{{ u.username }}</small></td>
        <td data-l="Доступ">
          <span v-if="u.holding_role" class="pill plan">{{ u.holding_label }} · все компании</span>
          <span v-for="a in u.assignments" :key="a.company_id" class="pill" :class="a.valid ? (u.state === 'conflict' ? 'bad' : 'gray') : 'warn'" style="margin:2px 4px 2px 0">{{ a.company }} · {{ a.role_label }}</span>
          <small v-if="!u.holding_role && !u.assignments.length">Нет назначения</small>
        </td>
        <td data-l="Состояние"><span class="pill" :class="STATE_CLASS[u.state]">{{ u.state_label }}</span>
          <small v-if="u.must_change_password && u.active">Временный пароль</small><small v-if="u.telegram">Telegram привязан</small></td>
        <td data-l=""><button class="secondary tiny" @click="openCard(u)">Открыть</button></td>
      </tr>
      <tr v-if="!shown.length"><td colspan="4" class="sub">Никого не найдено.</td></tr>
    </tbody></table></div>

    <section v-if="journal" class="card admin-journal"><div class="card-h"><h3>Журнал холдинга</h3><span class="sub">Все компании и события без компании: вход, пароли, Telegram, доступ. Время Ташкента.</span></div>
      <div class="card-b"><ol class="admin-history">
        <li v-for="h in journal.items" :key="h.id"><time>{{ when(h.at) }}</time><b>{{ h.action }}</b><span>{{ h.user }} · {{ h.company }}</span>
          <small v-for="(line, i) in historyLine(h.detail, roleName)" :key="i">{{ line }}</small></li>
      </ol>
      <div v-if="journal.total > 50" class="pagination"><span>Страница {{ journalPage }} из {{ Math.ceil(journal.total / 50) }}</span>
        <div class="actions"><button class="secondary tiny" :disabled="journalPage <= 1" @click="loadJournal(journalPage - 1)">← Назад</button>
          <button class="secondary tiny" :disabled="journalPage * 50 >= journal.total" @click="loadJournal(journalPage + 1)">Далее →</button></div></div></div>
    </section>
  </section>

  <div v-if="card || form?.kind === 'create'" class="modal-backdrop">
    <section ref="dialog" class="modal admin-card" role="dialog" aria-modal="true" aria-labelledby="admin-card-title">
      <div class="section-head"><h2 id="admin-card-title">{{ form?.kind === 'create' ? 'Новый пользователь' : card?.name || 'Пользователь' }}</h2>
        <button class="ghost icon-btn" aria-label="Закрыть" :disabled="busy" @click="closeCard(); form = null">✕</button></div>

      <section v-if="secret" class="admin-secret" role="status">
        <b>Временный пароль для {{ secret.login }}</b>
        <code>{{ secret.password }}</code>
        <p class="sub">Показывается один раз. Передайте сотруднику по защищённому каналу: при первом входе он задаст свой пароль.</p>
        <div class="actions"><button class="secondary tiny" @click="copy(secret.password)">Скопировать</button><button class="ghost tiny" @click="secret = null">Скрыть</button></div>
      </section>

      <template v-if="card?.username">
        <dl class="kv admin-kv">
          <dt>Логин</dt><dd>{{ card.username }}</dd>
          <dt>Состояние</dt><dd><span class="pill" :class="STATE_CLASS[card.state]">{{ card.state_label }}</span></dd>
          <dt>Доступ</dt><dd><span v-if="card.holding_role" class="pill plan">{{ card.holding_label }} · все компании</span>
            <span v-for="a in card.assignments" :key="a.company_id" class="admin-assignment"><span class="pill" :class="a.valid ? 'gray' : 'warn'">{{ a.company }} · {{ a.role_label }}</span>
              <button v-if="card.id !== currentUserId && card.active" class="ghost tiny" :disabled="busy" @click="unassign(a)">Снять</button></span>
            <small v-if="!card.holding_role && !card.assignments.length">Нет назначения</small></dd>
          <dt>Сеансы</dt><dd>{{ card.sessions }} · {{ card.telegram ? 'Telegram привязан' : 'Telegram не привязан' }}</dd>
          <dt>Последний вход</dt><dd>{{ when(card.last_login) }}</dd>
        </dl>
        <p v-if="card.state === 'conflict'" class="warning">Сотрудник назначен в нескольких компаниях. Доступ приостановлен, пока вы не выберете одну компанию: «Выбрать компанию».</p>
        <p v-if="card.state === 'no_role'" class="warning">Связь с компанией без действующей роли доступа не даёт. Назначьте роль или уберите связь.</p>

        <div class="actions admin-actions" v-if="!form">
          <template v-if="card.active">
            <button class="secondary tiny" @click="start('name')">Имя</button>
            <button v-if="card.holding_role !== 'founder' && card.id !== currentUserId" class="secondary tiny" @click="start('assign')">{{ canResolve(card) ? 'Выбрать компанию' : 'Назначение' }}</button>
            <button v-if="card.id !== currentUserId" class="secondary tiny" @click="start('holding')">Статус холдинга</button>
            <button class="secondary tiny" @click="start('password')">Сбросить пароль</button>
            <button class="secondary tiny" @click="start('sessions')">Завершить сеансы</button>
            <button v-if="card.id !== currentUserId" class="ghost tiny" @click="start('archive')">Архивировать</button>
          </template>
          <button v-else class="secondary tiny" @click="start('restore')">Восстановить с новым назначением</button>
        </div>
      </template>

      <form v-if="form" class="form-grid admin-form" @submit.prevent="submit">
        <template v-if="form.kind === 'create'">
          <label>Логин<input v-model="form.username" pattern="[a-z0-9_.\-]{3,80}" required placeholder="zuma.aziz" autocomplete="off"></label>
          <label>ФИО<input v-model="form.name" minlength="2" maxlength="160" required></label>
        </template>
        <label v-if="form.kind === 'name'" class="wide">ФИО<input v-model="form.name" minlength="2" maxlength="160" required></label>
        <label v-if="['create', 'holding', 'restore'].includes(form.kind)">Статус<select v-model="form.holding_role">
          <option value="">Сотрудник одной компании</option><option v-for="[k, label] in meta.holding_roles" :key="k" :value="k">{{ label }} · все компании</option></select></label>
        <template v-if="form.kind === 'assign' || (['create', 'holding', 'restore'].includes(form.kind) && !form.holding_role)">
          <label>Компания<select v-model="form.company_id" required><option v-for="c in meta.companies" :key="c.id" :value="c.id">{{ c.name }}</option></select></label>
          <label>Роль<select v-model="form.role" required><option v-for="[k, label] in meta.company_roles" :key="k" :value="k">{{ label }}</option></select></label>
        </template>
        <p v-if="form.kind === 'assign' && !card.holding_role" class="form-note wide">Сотрудник работает в одной компании: назначения в других компаниях будут сняты, прежние сеансы завершены.</p>
        <p v-if="form.kind === 'holding'" class="form-note wide">Все прежние назначения снимаются. Администратор получает финансовые права только по отдельному назначению другого администратора.</p>
        <p v-if="form.kind === 'unassign'" class="form-note wide">Назначение в {{ form.company }} будет снято, сеансы сотрудника завершены.</p>
        <p v-if="form.kind === 'archive'" class="form-note wide">Вход, сеансы, Telegram и все назначения будут отключены. Документы и журнал сохранят автора.</p>
        <p v-if="['password', 'restore', 'create'].includes(form.kind)" class="form-note wide">Будет создан случайный временный пароль (20 символов). При первом входе сотрудник обязан его сменить.</p>
        <label class="wide">Причина<textarea v-model="form.reason" minlength="10" maxlength="1000" required></textarea></label>
        <p v-if="error" class="error wide" role="alert">{{ error }}</p>
        <div class="form-actions wide"><button type="button" class="secondary" :disabled="busy" @click="form = null">Отмена</button><button :disabled="busy">{{ busy ? 'Сохраняем…' : 'Подтвердить' }}</button></div>
      </form>

      <template v-if="card?.history?.length">
        <h3 class="admin-sec">История доступа</h3>
        <ol class="admin-history">
          <li v-for="h in card.history" :key="h.id"><time>{{ when(h.at) }}</time><b>{{ h.action }}</b><span>{{ h.actor }}</span>
            <small v-for="(line, i) in historyLine(h.detail, roleName)" :key="i">{{ line }}</small></li>
        </ol>
      </template>
    </section>
  </div>
</template>
