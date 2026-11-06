<script setup>
import {ref, computed, watch, onMounted, onUnmounted} from 'vue';
import {tx, N_} from '../i18n/index.js';
import {tashkentToday} from './cashFunding.js';

const props = defineProps({api: {type: Function, required: true}, request: {type: Object, required: true}, money: {type: Function, required: true}});
const emit = defineEmits(['busy', 'paid']);
const snapshot = ref(null), opened = ref(false), selected = ref([]), search = ref('');
const form = ref({date: tashkentToday(), reference: '', note: ''});
const preview = ref(null), confirmed = ref(false), busy = ref(false), uncertain = ref(false);
const error = ref(''), notice = ref(''), attempt = ref(null), key = ref('');
const funding = computed(() => props.request.cash_funding);
let alive = true, generation = 0, readGeneration = 0, reloadNeeded = false;
const json = data => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
const url = () => `/api/requests/${props.request.id}/cash-funding/payments`;
const identity = () => [props.request.id, props.request.company_id, props.request.account_id, props.request.currency, funding.value?.id, funding.value?.mode].join('|');
const scope = () => [props.request.id, props.request.company_id, props.request.account_id, props.request.currency, props.request.version, props.request.status, funding.value?.id, funding.value?.version, funding.value?.mode].join('|');
const units = value => typeof value === 'string' && /^-?\d{1,15}(?:\.\d{1,2})?$/.test(value)
  ? (value.startsWith('-') ? -1n : 1n) * (() => {const [whole, fraction = ''] = value.replace('-', '').split('.'); return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));})() : null;
const decimal = value => `${value < 0n ? '-' : ''}${(value < 0n ? -value : value) / 100n}.${String((value < 0n ? -value : value) % 100n).padStart(2, '0')}`;
const sameIds = (a, b) => Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((id, index) => Number.isInteger(id) && id === b[index]);
function failResponse() { throw new Error(tx('Ответ о групповой выплате не соответствует выбранным заявкам. Проверьте состояние.')); }
function metadata(data) {
  return data && data.request_id === props.request.id && data.company_id === props.request.company_id
    && data.cash_account_id === props.request.account_id && data.funding_id === funding.value?.id
    && data.currency === props.request.currency && data.mode === 'batch';
}
function candidate(row) {
  return row && Number.isInteger(row.id) && row.id > 0 && Number.isInteger(row.version) && row.version > 0
    && row.id !== props.request.id && row.company_id === props.request.company_id
    && row.account_id === props.request.account_id && row.currency === props.request.currency
    && row.status === 'approved' && (row.channel || row.account_kind) === 'cash' && !row.cash_funding
    && units(row.amount) != null && units(row.amount) > 0n && Array.isArray(row.actions);
}
function validateSnapshot(data) {
  if (!metadata(data) || !Number.isInteger(data.version) || data.version < funding.value.version
    || !Number.isInteger(data.request_version) || data.request_version < props.request.version
    || typeof data.can_pay !== 'boolean' || !Array.isArray(data.candidates) || !Array.isArray(data.groups)
    || !data.candidates.every(candidate) || new Set(data.candidates.map(row => row.id)).size !== data.candidates.length) failResponse();
  const received = units(data.received_amount), allocated = units(data.allocated_amount), remaining = units(data.remaining_amount);
  const redacted = data.can_pay === false && data.allocated_amount === null && data.remaining_amount === null && data.cash_balance === null;
  if (received == null || received < 0n
    || (!redacted && (allocated == null || remaining == null || units(data.cash_balance) == null || allocated < 0n || remaining < 0n || allocated + remaining !== received))
    || (redacted && (data.groups.length || data.candidates.length))
    || (funding.value.state === 'received' && received !== units(funding.value.net_amount))) failResponse();
  for (const group of data.groups) {
    if (!group || typeof group.key !== 'string' || !Array.isArray(group.request_ids) || !group.request_ids.length
      || !group.request_ids.every(Number.isInteger) || new Set(group.request_ids).size !== group.request_ids.length
      || !Array.isArray(group.ledger_ids) || group.ledger_ids.length !== group.request_ids.length
      || !group.ledger_ids.every(Number.isInteger) || new Set(group.ledger_ids).size !== group.ledger_ids.length
      || units(group.total_amount) == null || units(group.total_amount) <= 0n) failResponse();
  }
  return data;
}
const canPay = computed(() => snapshot.value?.can_pay === true && props.request.status === 'approved' && funding.value?.state === 'received');
const hasTotals = computed(() => units(snapshot.value?.allocated_amount) != null && units(snapshot.value?.remaining_amount) != null && units(snapshot.value?.cash_balance) != null);
const selectedRows = computed(() => selected.value.map(id => snapshot.value?.candidates.find(row => row.id === id)).filter(Boolean));
const total = computed(() => selectedRows.value.reduce((sum, row) => sum + units(row.amount), 0n));
const available = computed(() => units(snapshot.value?.remaining_amount) ?? 0n);
const remaining = computed(() => available.value - total.value);
const cashBalance = computed(() => units(snapshot.value?.cash_balance) ?? 0n);
const minimum = computed(() => [funding.value?.receive_date, props.request.approved_on, ...selectedRows.value.map(row => row.approved_on)].filter(Boolean).sort().at(-1));
const selectionProblem = computed(() => selected.value.length > 100 ? N_('За один раз можно выбрать не более 100 заявок.')
  : total.value > available.value ? N_('Сумма выбранных заявок превышает остаток этого пополнения.')
  : total.value > cashBalance.value ? N_('В кассе недостаточно фактических денег для выбранных заявок.') : '');
const filtered = computed(() => {
  const query = search.value.trim().toLocaleLowerCase();
  return (snapshot.value?.candidates || []).filter(row => !query || [row.number, row.counterparty, row.category, row.purpose].some(value => String(value || '').toLocaleLowerCase().includes(query)));
});
function working(value) { busy.value = value; emit('busy', value); if (!value && alive && reloadNeeded) { reloadNeeded = false; refresh(); } }
function invalidate() { preview.value = null; confirmed.value = false; }
function newKey() { return crypto.randomUUID(); }
watch(() => [form.value.date, form.value.reference, form.value.note, selected.value.join('|')], () => {
  generation++; invalidate();
  if (!uncertain.value) key.value = '';
});
function choose(row, checked) {
  if (busy.value || uncertain.value || !canPay.value || !row.actions.includes('pay')) return;
  if (checked && !selected.value.includes(row.id) && selected.value.length < 100) selected.value = [...selected.value, row.id];
  if (!checked) selected.value = selected.value.filter(id => id !== row.id);
}
function start() {
  if (busy.value || uncertain.value || !canPay.value) return;
  opened.value = true; error.value = ''; notice.value = '';
  form.value.date = tashkentToday();
}
function finish(group) {
  const ids = [...group.request_ids];
  selected.value = []; invalidate(); uncertain.value = false; attempt.value = null; key.value = ''; opened.value = false;
  notice.value = N_('Выбранные заявки оплачены. Повторная выдача не требуется.');
  emit('paid', {paid_request_ids: ids, group});
}
async function refresh() {
  if (busy.value || funding.value?.mode !== 'batch') return;
  const current = ++readGeneration, context = scope();
  generation++;
  working(true); error.value = ''; invalidate();
  try {
    const result = validateSnapshot(await props.api(url()));
    if (!alive || current !== readGeneration || context !== scope()) return;
    snapshot.value = result;
    if (attempt.value) {
      // A privacy-masked history cannot prove that a lost payout was absent.
      if (result.allocated_amount === null) return;
      const previous = attempt.value, group = result.groups.find(item => item.key === previous.key);
      if (group) {
        if (!sameIds(group.request_ids, previous.ids) || group.total_amount !== previous.total_amount) failResponse();
        finish(group);
      } else {
        uncertain.value = false; key.value = previous.key; attempt.value = null;
        selected.value = selected.value.filter(id => result.candidates.some(row => row.id === id && row.actions.includes('pay')));
        notice.value = N_('Состояние проверено: выбранная группа не проведена. Проверьте заявки перед подтверждением.');
      }
    } else {
      selected.value = selected.value.filter(id => result.candidates.some(row => row.id === id && row.actions.includes('pay')));
    }
  } catch (failure) { if (alive && current === readGeneration && context === scope()) error.value = failure.message; }
  finally { if (alive) working(false); }
}
function body() {
  if (!key.value) key.value = newKey();
  return {version: snapshot.value.version, request_version: snapshot.value.request_version,
    items: selectedRows.value.map(row => ({id: row.id, version: row.version})), date: form.value.date,
    reference: form.value.reference.trim(), note: form.value.note.trim(), key: key.value};
}
async function check() {
  if (busy.value || uncertain.value || !canPay.value || !selectedRows.value.length || selectionProblem.value) return;
  const current = ++generation, context = scope(), data = body(), wantedTotal = decimal(total.value), wantedRemaining = decimal(remaining.value);
  working(true); error.value = ''; notice.value = ''; invalidate();
  try {
    const result = await props.api(url() + '/preview', json(data));
    if (!alive || current !== generation || context !== scope()) return;
    if (!metadata(result) || !result.token || result.version !== data.version || result.request_version !== data.request_version
      || result.key !== data.key || result.date !== data.date || result.reference !== data.reference || result.note !== data.note
      || !Array.isArray(result.items) || result.items.length !== data.items.length
      || !result.items.every((item, index) => item.id === data.items[index].id && item.version === data.items[index].version)
      || result.total_amount !== wantedTotal || result.remaining_amount !== wantedRemaining || units(result.cash_balance) == null) failResponse();
    preview.value = result;
  } catch (failure) { if (alive && current === generation && context === scope()) error.value = failure.message; }
  finally { if (alive) working(false); }
}
async function commit() {
  if (busy.value || uncertain.value || !canPay.value || !preview.value?.token || !confirmed.value) return;
  const current = ++generation, context = scope(), checked = preview.value;
  const data = {...body(), token: checked.token, confirm: true}, ids = data.items.map(item => item.id);
  attempt.value = {key: data.key, ids, total_amount: checked.total_amount, identity: identity()};
  invalidate(); working(true); error.value = ''; notice.value = '';
  try {
    const result = await props.api(url() + '/commit', json(data));
    if (!alive || current !== generation || context !== scope()) return;
    if (!metadata(result) || !Number.isInteger(result.version) || result.version <= data.version
      || !Number.isInteger(result.request_version) || result.request_version <= data.request_version
      || !sameIds(result.paid_request_ids, ids) || result.group?.key !== data.key
      || !sameIds(result.group.request_ids, ids) || result.group.total_amount !== checked.total_amount
      || !Array.isArray(result.group.ledger_ids) || result.group.ledger_ids.length !== ids.length
      || !result.group.ledger_ids.every(Number.isInteger) || new Set(result.group.ledger_ids).size !== ids.length
      || result.group.date !== data.date || result.group.reference !== data.reference
      || result.remaining_amount !== checked.remaining_amount || units(result.cash_balance) == null) failResponse();
    if (snapshot.value) snapshot.value = {...snapshot.value, version: result.version, request_version: result.request_version, remaining_amount: result.remaining_amount, cash_balance: result.cash_balance,
      allocated_amount: decimal((units(snapshot.value.received_amount) ?? 0n) - units(result.remaining_amount)),
      candidates: snapshot.value.candidates.filter(row => !ids.includes(row.id)), groups: [...snapshot.value.groups, result.group]};
    finish(result.group);
  } catch (failure) {
    if (!alive || current !== generation || context !== scope()) return;
    error.value = failure.message;
    if (failure.status == null || failure.status >= 500 || [409, 428].includes(failure.status)) {
      uncertain.value = true; opened.value = false;
      error.value = N_('Ответ о групповой выплате не получен. Проверьте состояние; повторно выдавать деньги не нужно.');
    } else attempt.value = null;
  } finally { if (alive) working(false); }
}
// A refreshed request can carry new effective permissions without a financial
// version change. Revalidate the API capability on that replacement as well.
watch(() => [scope(), props.request, props.request.actions?.join('|')], () => {
  generation++; readGeneration++; snapshot.value = null; selected.value = []; invalidate(); opened.value = false;
  const recovering = attempt.value?.identity === identity();
  uncertain.value = recovering;
  if (!recovering) { attempt.value = null; key.value = ''; }
  reloadNeeded = true; if (!busy.value) { reloadNeeded = false; refresh(); }
});
onMounted(refresh);
onUnmounted(() => { alive = false; generation++; readGeneration++; emit('busy', false); });
</script>

<template>
  <section v-if="funding?.mode === 'batch'" class="cash-batch-panel" :aria-label="tx('Групповая выдача наличных')">
    <div class="section-head"><h3>{{ tx('Оплата нескольких заявок') }}</h3><button type="button" class="ghost tiny" :disabled="busy" @click="refresh">{{ tx('Обновить') }}</button></div>
    <p class="sub">{{ tx('Здесь оплачиваются выбранные согласованные заявки. Пополнение кассы второй раз в расходы не включается.') }}</p>
    <p v-if="busy" class="sub" role="status">{{ tx('Проверяем…') }}</p>
    <p v-if="notice" class="notice" role="status">{{ tx(notice) }}</p>
    <p v-if="error" class="error" role="alert">{{ tx(error) }}</p>
    <template v-if="snapshot">
      <dl class="batch-summary">
        <div><dt>{{ tx('Получено по пополнению') }}</dt><dd>{{ money(snapshot.received_amount) }} {{ request.currency }}</dd></div>
        <template v-if="hasTotals"><div><dt>{{ tx('Выдано ранее') }}</dt><dd>{{ money(snapshot.allocated_amount) }} {{ request.currency }}</dd></div>
        <div><dt>{{ tx('Выбрано к оплате') }}</dt><dd>{{ money(decimal(total)) }} {{ request.currency }}</dd></div>
        <div><dt>{{ tx('Останется от пополнения') }}</dt><dd>{{ money(decimal(remaining)) }} {{ request.currency }}</dd></div></template>
      </dl>
      <p v-if="snapshot.cash_balance != null" class="sub">{{ tx('Фактический остаток кассы: {amount} {currency}.', {amount: money(snapshot.cash_balance), currency: request.currency}) }}</p>
      <p v-if="!canPay" class="sub">{{ tx(snapshot.blocked_reason || N_('Групповая выдача доступна после согласования и получения наличных пользователю с правом кассовой оплаты.')) }}</p>
      <button v-if="canPay && !opened && !uncertain" type="button" :disabled="busy || !snapshot.candidates.length" @click="start">{{ tx('Оплатить несколько заявок') }}</button>
      <p v-if="canPay && !snapshot.candidates.length" class="sub">{{ tx('Нет подходящих согласованных заявок для этой кассы.') }}</p>
      <form v-if="opened && canPay && !uncertain" class="batch-form" @submit.prevent="check">
        <label>{{ tx('Поиск заявки') }}<input v-model="search" type="search" :disabled="busy" autocomplete="off"></label>
        <div class="batch-table-wrap"><table>
          <thead><tr><th>{{ tx('Выбрать') }}</th><th>{{ tx('Заявка и получатель') }}</th><th>{{ tx('Статья') }}</th><th>{{ tx('Оплатить до') }}</th><th class="num">{{ tx('Сумма') }}</th></tr></thead>
          <tbody><tr v-for="row in filtered" :key="row.id"><td><input type="checkbox" :checked="selected.includes(row.id)" :aria-label="tx('Выбрать заявку {number}', {number: row.number})" :disabled="busy || !row.actions.includes('pay') || (!selected.includes(row.id) && selected.length >= 100)" @change="choose(row, $event.target.checked)"></td><td><b>{{ row.number }}</b><small>{{ row.counterparty }}</small></td><td>{{ row.category }}</td><td>{{ row.date }}</td><td class="num">{{ money(row.amount) }} {{ request.currency }}</td></tr></tbody>
        </table></div>
        <p v-if="!filtered.length" class="sub">{{ tx('По этому запросу заявки не найдены.') }}</p>
        <p class="sub">{{ tx('Выбрано заявок: {count}. За один раз — не более 100.', {count: selected.length}) }}</p>
        <p v-if="selectionProblem" class="error" role="alert">{{ tx(selectionProblem) }}</p>
        <div class="form-grid">
          <label>{{ tx('Дата факта') }}<input v-model="form.date" type="date" :min="minimum || undefined" :max="tashkentToday()" :disabled="busy" required></label>
          <label>{{ tx('Номер документа') }}<input v-model="form.reference" minlength="2" maxlength="120" :disabled="busy" required></label>
          <label class="wide">{{ tx('Комментарий к оплате') }}<textarea v-model="form.note" maxlength="1000" :disabled="busy"></textarea></label>
        </div>
        <template v-if="preview">
          <p class="notice">{{ tx('Проверено: {count} заявок на {amount} {currency}.', {count: selected.length, amount: money(preview.total_amount), currency: request.currency}) }}</p>
          <label class="batch-attest"><input v-model="confirmed" type="checkbox" :disabled="busy">{{ tx('Подтверждаю фактическую выдачу денег по всем выбранным заявкам.') }}</label>
        </template>
        <div class="form-actions"><button type="button" class="secondary" :disabled="busy" @click="opened = false; invalidate()">{{ tx('Отмена') }}</button><button v-if="!preview" type="submit" :disabled="busy || !selected.length || !!selectionProblem">{{ tx('Проверить выбранные заявки') }}</button><button v-else type="button" :disabled="busy || !confirmed" @click="commit">{{ tx('Подтвердить выдачу и оплатить заявки') }}</button></div>
      </form>
      <section v-if="snapshot.groups.length" class="batch-history" :aria-label="tx('Проведённые группы выплат')"><h4>{{ tx('Проведённые группы выплат') }}</h4><ul><li v-for="group in snapshot.groups" :key="group.key">{{ group.date }} · {{ group.reference }} · {{ tx('Заявок: {count}', {count: group.request_ids.length}) }} · <b>{{ money(group.total_amount) }} {{ request.currency }}</b></li></ul></section>
    </template>
    <button v-if="uncertain" type="button" class="secondary" :disabled="busy" @click="refresh">{{ tx('Проверить состояние группы') }}</button>
  </section>
</template>

<style scoped>
.cash-batch-panel{border:1px solid var(--line);border-radius:14px;padding:16px;margin:16px 0;min-width:0;background:var(--surface-muted)}
.cash-batch-panel .section-head{flex-wrap:wrap;gap:8px}.cash-batch-panel h3,.cash-batch-panel h4{margin:0}.batch-summary{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:14px 0}.batch-summary>div{border:1px solid var(--line);border-radius:10px;padding:10px;background:var(--surface)}.batch-summary dt{font-size:12px;color:var(--muted)}.batch-summary dd{margin:5px 0 0;font-weight:700;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.batch-form{margin-top:14px}.batch-table-wrap{overflow:auto;max-height:380px;margin:12px 0;max-width:100%}.batch-table-wrap table{min-width:580px;width:100%}.batch-table-wrap th{position:sticky;top:0;background:var(--surface-muted);z-index:1}.batch-table-wrap small{display:block;margin-top:4px;max-width:190px;white-space:normal;overflow-wrap:anywhere}.batch-table-wrap input[type=checkbox],.batch-attest input{width:auto;flex:none}.batch-attest{display:flex;align-items:flex-start;gap:8px;margin:12px 0}.batch-attest input{margin-top:3px}.batch-history{margin-top:16px}.batch-history ul{padding-left:18px;overflow-wrap:anywhere}.batch-history li{margin:7px 0}
@media(max-width:520px){.cash-batch-panel{padding:12px}.batch-summary{grid-template-columns:1fr}.batch-form .form-actions{flex-wrap:wrap}}
</style>
