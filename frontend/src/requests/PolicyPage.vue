<script setup>
import {ref, computed, onMounted} from 'vue';
import {tx, N_, formatDate} from '../i18n/index.js';

const props = defineProps({
  api: {type: Function, required: true},
  money: {type: Function, required: true},
  canEditCalendar: {type: Boolean, default: false},
});

const POLICIES = {always: N_('Директор утверждает любую сумму'), threshold: N_('Директор — только выше порога'), skip: N_('Директор не участвует')};
const data = ref(null), error = ref(''), notice = ref(''), noticeParams = ref(null), busy = ref(false), editing = ref(null), form = ref({});
const year = ref(new Date().getFullYear()), days = ref([]), dayForm = ref({day: '', kind: 'holiday', name: ''});
const skipForm = ref(null);

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

function edit(c) {
  editing.value = c.id;
  form.value = {policy: c.policy, threshold_uzs: c.thresholds.UZS || '', threshold_usd: c.thresholds.USD || '', threshold_eur: c.thresholds.EUR || '', reason: ''};
}
function save(c) {
  const f = form.value, blank = v => (v === '' || v == null ? null : String(v));
  run(() => props.api(`/api/categories/${c.id}/director-policy`, json('PUT', {policy: f.policy, threshold_uzs: blank(f.threshold_uzs),
    threshold_usd: blank(f.threshold_usd), threshold_eur: blank(f.threshold_eur), reason: f.reason})), N_('Политика статьи «{name}» сохранена.'), {name: c.name})
    .then(() => { if (!error.value) editing.value = null; });
}
function toggleSkip(c) {
  run(() => props.api(`/api/categories/${c.id}/skip-allowed`, json('PUT', {allowed: !c.skip_allowed, reason: skipForm.value.reason})),
      c.skip_allowed ? N_('Статья исключена из перечня.') : N_('Статья добавлена в перечень регулярных платежей.'))
    .then(() => { if (!error.value) skipForm.value = null; });
}
function addDay() { run(() => props.api('/api/calendar-days', json('POST', dayForm.value)), N_('Календарь обновлён.')).then(() => { if (!error.value) dayForm.value = {day: '', kind: 'holiday', name: ''}; }); }
function removeDay(d) { run(() => props.api(`/api/calendar-days/${d.day}`, {method: 'DELETE'}), N_('Запись календаря удалена.')); }

const threshold = (c, cur) => c.policy !== 'threshold' ? '—' : c.thresholds[cur] ? props.money(c.thresholds[cur]) + ' ' + cur : tx('любая сумма');
const dateRu = d => d ? d.split('-').reverse().join('.') : '';
const when = s => s ? formatDate(new Date(s.replace(' ', 'T') + 'Z'), {timeZone: 'Asia/Tashkent', day: '2-digit', month: '2-digit', year: 'numeric'}) : '';
const holidays = computed(() => days.value.filter(d => d.kind === 'holiday').length);
onMounted(load);
</script>

<template>
  <section class="card"><div class="card-b">
    <h3>{{ tx('Порядок согласования и оплаты') }}</h3>
    <ol class="steps-list" style="margin-top:12px">
      <li><span class="n">1</span><div><b>{{ tx('Расчётный бухгалтер проверяет реквизиты и комплектность') }}</b><small>{{ tx('Внутренняя заявка (индент) и договор или счёт обязательны до отправки.') }}</small></div></li>
      <li><span class="n">2</span><div><b>{{ tx('Финансовый директор проверяет бюджет и дату') }}</b><small>{{ tx('Бюджет статьи повторно проверяется при отправке, каждом согласовании и оплате.') }}</small></div></li>
      <li><span class="n">3</span><div><b>{{ tx('Директор — по политике статьи') }}</b><small>{{ tx('Политика фиксируется при отправке заявки: поздняя правка справочника маршрут уже отправленной заявки не меняет. Нет политики или порога валюты — директор утверждает любую сумму.') }}</small></div></li>
      <li><span class="n">4</span><div><b>{{ tx('Оплата') }}</b><small>{{ tx('Банк — расчётный бухгалтер, касса — кассир. Автор, редактор, проверявший бухгалтер и согласующие заявку не оплачивают.') }}</small></div></li>
    </ol>
  </div></section>

  <p v-if="notice" class="notice" role="status">{{ tx(notice, noticeParams) }}</p>
  <p v-if="error" class="error" role="alert">{{ tx(error) }}</p>

  <section v-if="data" class="card"><div class="card-h"><h3>{{ tx('Политика директора по статьям') }}</h3>
    <span class="sub">{{ tx('Порог по умолчанию для обычных статей: {uzs} UZS и {usd} USD; EUR без порога — директор всегда.', {uzs: money(data.defaults.UZS), usd: money(data.defaults.USD)}) }}</span></div>
    <div class="table-card rsp-wrap"><table class="rsp"><thead><tr><th>{{ tx('Статья') }}</th><th>{{ tx('Политика') }}</th><th class="num">UZS</th><th class="num">USD</th><th class="num">EUR</th><th>{{ tx('Изменено') }}</th><th></th></tr></thead><tbody>
      <template v-for="c in data.categories" :key="c.id">
        <tr>
          <td :data-l="tx('Статья')"><b>{{ c.name }}</b><small v-if="c.skip_allowed">{{ tx('Регулярный платёж: разрешено без директора') }}</small></td>
          <td :data-l="tx('Политика')">{{ tx(POLICIES[c.policy]) }}</td>
          <td class="num" data-l="UZS">{{ threshold(c, 'UZS') }}</td><td class="num" data-l="USD">{{ threshold(c, 'USD') }}</td><td class="num" data-l="EUR">{{ threshold(c, 'EUR') }}</td>
          <td :data-l="tx('Изменено')">{{ when(c.changed_at) }}<small>{{ c.changed_by }}</small></td>
          <td data-l=""><div class="actions"><button class="secondary tiny" :disabled="busy" @click="edit(c)">{{ tx('Изменить') }}</button>
            <button v-if="data.can_edit_skip_list" class="ghost tiny" :disabled="busy" @click="skipForm = {id: c.id, reason: ''}">{{ c.skip_allowed ? tx('Убрать из перечня') : tx('В перечень без директора') }}</button></div></td>
        </tr>
        <tr v-if="editing === c.id" class="detail-row"><td colspan="7" class="detail-cell">
          <form class="form-grid" @submit.prevent="save(c)">
            <label>{{ tx('Политика') }}<select v-model="form.policy"><option v-for="(label, k) in POLICIES" :key="k" :value="k" :disabled="k === 'skip' && !c.skip_allowed">{{ tx(label) }}</option></select></label>
            <template v-if="form.policy === 'threshold'">
              <label>{{ tx('Порог {currency}', {currency: 'UZS'}) }}<input v-model="form.threshold_uzs" inputmode="decimal" pattern="[0-9]+([.][0-9]{1,2})?" :placeholder="tx('нет — любая сумма')"></label>
              <label>{{ tx('Порог {currency}', {currency: 'USD'}) }}<input v-model="form.threshold_usd" inputmode="decimal" pattern="[0-9]+([.][0-9]{1,2})?" :placeholder="tx('нет — любая сумма')"></label>
              <label>{{ tx('Порог {currency}', {currency: 'EUR'}) }}<input v-model="form.threshold_eur" inputmode="decimal" pattern="[0-9]+([.][0-9]{1,2})?" :placeholder="tx('нет — любая сумма')"></label>
            </template>
            <label class="wide">{{ tx('Причина изменения') }}<textarea v-model="form.reason" minlength="10" required></textarea></label>
            <div class="form-actions wide"><button type="button" class="secondary" @click="editing = null">{{ tx('Отмена') }}</button><button :disabled="busy">{{ tx('Сохранить') }}</button></div>
          </form>
        </td></tr>
        <tr v-if="skipForm && skipForm.id === c.id" class="detail-row"><td colspan="7" class="detail-cell">
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
