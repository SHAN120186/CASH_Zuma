<script setup>
import {ref, computed, onMounted} from 'vue';
import {tx, N_, formatDate} from '../i18n/index.js';

const props = defineProps({
  api: {type: Function, required: true},
  money: {type: Function, required: true},
  canEdit:{type:Boolean,default:true},
  canEditCalendar: {type: Boolean, default: false},
});

const POLICIES = {always: N_('Директор участвует всегда'), threshold: N_('Директор — только выше порога'), skip: N_('Директор не участвует')};
const data = ref(null), error = ref(''), notice = ref(''), noticeParams = ref(null), busy = ref(false), editing = ref(null), form = ref({});
const year = ref(new Date().getFullYear()), days = ref([]), dayForm = ref({day: '', kind: 'holiday', name: ''});
const skipForm = ref(null);
const currencies = ['UZS', 'USD', 'EUR'];

async function load() {
  error.value = '';
  try { data.value = await props.api('/api/approval-policy'); days.value = await props.api(`/api/calendar-days?year=${year.value}`); }
  catch (e) { error.value = e.message; }
}
// The notice keeps its Russian key and parameters, so it follows a later language switch.
async function run(fn, message, params = null) {
  busy.value = true; error.value = ''; notice.value = ''; noticeParams.value = null;
  try { await fn(); await load(); notice.value = message; noticeParams.value = params; } catch (e) { error.value = e.message; } finally { busy.value = false; }
}
const json = (method, body) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});

function effective(c, cur) {
  if (c.effective?.[cur]) return c.effective[cur];
  const policy = c.policy || 'always', threshold = c.thresholds?.[cur] ?? null;
  return {policy: policy === 'threshold' && threshold == null ? 'always' : policy, threshold};
}
function edit(c) {
  editing.value = c.id;
  const values = {reason: ''};
  for (const cur of currencies) {
    const rule = effective(c, cur), suffix = cur.toLowerCase();
    values['policy_' + suffix] = rule.policy;
    values['threshold_' + suffix] = rule.threshold ?? '';
  }
  form.value = values;
}
async function save(c) {
  const body = {reason: form.value.reason};
  for (const cur of currencies) {
    const suffix = cur.toLowerCase(), policy = form.value['policy_' + suffix], value = form.value['threshold_' + suffix];
    body['policy_' + suffix] = policy;
    body['threshold_' + suffix] = policy === 'threshold' && value !== '' && value != null ? String(value) : null;
  }
  await run(() => props.api(`/api/categories/${c.id}/director-policy`, json('PUT', body)), N_('Политика статьи «{name}» сохранена.'), {name: c.name});
  if (!error.value) editing.value = null;
}
function toggleSkip(c) {
  run(() => props.api(`/api/categories/${c.id}/skip-allowed`, json('PUT', {allowed: !c.skip_allowed, reason: skipForm.value.reason})),
      c.skip_allowed ? N_('Статья исключена из перечня.') : N_('Статья добавлена в перечень регулярных платежей.'))
    .then(() => { if (!error.value) skipForm.value = null; });
}
function addDay() { run(() => props.api('/api/calendar-days', json('POST', dayForm.value)), N_('Календарь обновлён.')).then(() => { if (!error.value) dayForm.value = {day: '', kind: 'holiday', name: ''}; }); }
function removeDay(d) { run(() => props.api(`/api/calendar-days/${d.day}`, {method: 'DELETE'}), N_('Запись календаря удалена.')); }

const participation = (c, cur) => {
  const rule = effective(c, cur);
  return rule.policy === 'threshold' && rule.threshold != null
    ? tx('Выше {amount} {currency}', {amount: props.money(rule.threshold), currency: cur})
    : tx(POLICIES[rule.policy] || POLICIES.always);
};
const dateRu = d => d ? d.split('-').reverse().join('.') : '';
const when = s => s ? formatDate(new Date(s.replace(' ', 'T') + 'Z'), {timeZone: 'Asia/Tashkent', day: '2-digit', month: '2-digit', year: 'numeric'}) : '';
const holidays = computed(() => days.value.filter(d => d.kind === 'holiday').length);
onMounted(load);
</script>

<template>
  <section class="card"><div class="card-b">
    <h3>{{ tx('Порядок согласования и оплаты') }}</h3>
    <ol class="steps-list" style="margin-top:12px">
      <li><span class="n">1</span><div><b>{{ tx('Расчётный бухгалтер проверяет реквизиты и комплектность') }}</b><small>{{ tx('Индент обязателен всегда. Для банка нужен также договор или счёт; для кассы остальные документы — при наличии.') }}</small></div></li>
      <li><span class="n">2</span><div><b>{{ tx('Финансовый директор проверяет бюджет и дату') }}</b><small>{{ tx('Бюджет статьи повторно проверяется при отправке, каждом согласовании и оплате.') }}</small></div></li>
      <li><span class="n">3</span><div><b>{{ tx('Директор — по политике статьи') }}</b><small>{{ tx('Политика фиксируется при отправке заявки: поздняя правка справочника маршрут уже отправленной заявки не меняет. Нет политики или порога валюты — директор утверждает любую сумму.') }}</small></div></li>
      <li><span class="n">4</span><div><b>{{ tx('Оплата') }}</b><small>{{ tx('Банк — расчётный бухгалтер, в том числе проверявший заявку; касса — кассир. Автор, редакторы, финансовый директор и директор заявку не оплачивают.') }}</small></div></li>
    </ol>
  </div></section>

  <p v-if="notice" class="notice" role="status">{{ tx(notice, noticeParams) }}</p>
  <p v-if="error" class="error" role="alert">{{ tx(error) }}</p>

  <section v-if="data" class="card"><div class="card-h"><h3>{{ tx('Пороги участия директора по статьям') }}</h3>
    <span class="sub">{{ tx('Порог по умолчанию для обычных статей: {uzs} UZS и {usd} USD; EUR без порога — директор всегда.', {uzs: money(data.defaults.UZS), usd: money(data.defaults.USD)}) }}</span></div>
    <div class="card-b"><p class="sub">{{ tx('Директор согласует заявку только при сумме строго выше порога. Ровно порог — согласование завершает финансовый директор. Порог 0 — директор участвует при любой положительной сумме. «Директор не участвует» — согласование завершает финансовый директор. Верхнего ограничения суммы для директора нет.') }}</p></div>
    <div class="table-card rsp-wrap"><table class="rsp policy-table"><thead><tr><th>{{ tx('Статья') }}</th><th>UZS</th><th>USD</th><th>EUR</th><th>{{ tx('Изменено') }}</th><th></th></tr></thead><tbody>
      <template v-for="c in data.categories" :key="c.id">
        <tr>
          <td :data-l="tx('Статья')"><b>{{ c.name }}</b><small v-if="c.skip_allowed">{{ tx('Регулярный платёж: разрешено без директора') }}</small></td>
          <td v-for="cur in currencies" :key="cur" :data-l="cur">{{ participation(c, cur) }}</td>
          <td :data-l="tx('Изменено')">{{ when(c.changed_at) }}<small>{{ c.changed_by }}</small></td>
          <td data-l=""><div class="actions"><button v-if="canEdit" class="secondary tiny" :disabled="busy" @click="edit(c)">{{ tx('Изменить') }}</button>
            <button v-if="canEdit&&data.can_edit_skip_list" class="ghost tiny" :disabled="busy" @click="skipForm = {id: c.id, reason: ''}">{{ c.skip_allowed ? tx('Убрать из перечня') : tx('В перечень без директора') }}</button></div></td>
        </tr>
        <tr v-if="editing === c.id" class="detail-row"><td colspan="6" class="detail-cell">
          <form class="form-grid" @submit.prevent="save(c)">
            <template v-for="cur in currencies" :key="cur">
              <label>{{ tx('Участие директора {currency}', {currency: cur}) }}<select v-model="form['policy_' + cur.toLowerCase()]"><option v-for="(label, k) in POLICIES" :key="k" :value="k" :disabled="k === 'skip' && !c.skip_allowed">{{ tx(label) }}</option></select></label>
              <label v-if="form['policy_' + cur.toLowerCase()] === 'threshold'">{{ tx('Порог участия {currency}', {currency: cur}) }}<input v-model="form['threshold_' + cur.toLowerCase()]" inputmode="decimal" pattern="[0-9]+([.][0-9]{1,2})?" required></label>
            </template>
            <label class="wide">{{ tx('Причина изменения') }}<textarea v-model="form.reason" minlength="10" required></textarea></label>
            <div class="form-actions wide"><button type="button" class="secondary" @click="editing = null">{{ tx('Отмена') }}</button><button :disabled="busy">{{ tx('Сохранить') }}</button></div>
          </form>
        </td></tr>
        <tr v-if="skipForm && skipForm.id === c.id" class="detail-row"><td colspan="6" class="detail-cell">
          <form class="rq-inline" @submit.prevent="toggleSkip(c)"><input v-model="skipForm.reason" minlength="10" required :aria-label="tx('Причина')" :placeholder="tx('Причина, не короче 10 символов')">
            <button class="tiny" :disabled="busy">{{ tx('Подтвердить') }}</button><button type="button" class="ghost tiny" @click="skipForm = null">{{ tx('Отмена') }}</button></form>
        </td></tr>
      </template>
    </tbody></table></div>
  </section>

  <section class="card"><div class="card-h"><h3>{{ tx('Календарь рабочих дней') }}</h3>
    <label class="fld">{{ tx('Год') }} <input v-model.number="year" type="number" min="2024" max="2100" @change="load"></label></div>
    <div class="card-b">
      <p class="sub">{{ tx('Суббота и воскресенье — выходные. Праздники с постоянной датой уже внесены; Рамазан и Курбан хайит и переносы выходных добавляются по постановлению. Сроки заявок: обычная — от 7, высокая — от 3, срочная — от 1 рабочего дня. Праздников в {year}: {n}.', {year, n: holidays}) }}</p>
      <ul class="cal-days">
        <li v-for="d in days" :key="d.day"><span class="pill" :class="d.kind === 'holiday' ? 'bad' : 'good'">{{ d.kind === 'holiday' ? tx('Выходной') : tx('Рабочий') }}</span>
          <b>{{ dateRu(d.day) }}</b> {{ d.name }}
          <button v-if="canEditCalendar" class="ghost tiny" :disabled="busy" @click="removeDay(d)">{{ tx('Удалить') }}</button></li>
      </ul>
      <form v-if="canEditCalendar" class="form-grid" @submit.prevent="addDay">
        <label>{{ tx('Дата') }}<input v-model="dayForm.day" type="date" required></label>
        <label>{{ tx('Тип дня') }}<select v-model="dayForm.kind"><option value="holiday">{{ tx('Праздник / выходной') }}</option><option value="workday">{{ tx('Рабочий выходной день') }}</option></select></label>
        <label class="wide">{{ tx('Название') }}<input v-model="dayForm.name" minlength="2" maxlength="160" required></label>
        <div class="form-actions wide"><button :disabled="busy">{{ tx('Добавить в календарь') }}</button></div>
      </form>
    </div>
  </section>
</template>

<style scoped>
@media (min-width:701px) {
  .policy-table {table-layout:fixed}
  .policy-table th:nth-child(1) {width:28%}
  .policy-table th:nth-child(2),.policy-table th:nth-child(3),.policy-table th:nth-child(4) {width:16%}
  .policy-table th:nth-child(5) {width:10%}
  .policy-table th:nth-child(6) {width:14%}
  .policy-table td {overflow-wrap:anywhere}
  .policy-table th,.policy-table td {padding-left:8px;padding-right:8px}
  .policy-table th,.policy-table td.num {white-space:normal}
  .policy-table .actions button {max-width:100%;white-space:normal;text-align:left}
}
</style>
