<script setup>
import {ref, computed, watch, onUnmounted} from 'vue';
import {tx, N_} from '../i18n/index.js';
import {fundingActionAvailable, fundingStatus, tashkentToday} from './cashFunding.js';

const props = defineProps({api: {type: Function, required: true}, request: {type: Object, required: true}, money: {type: Function, required: true}});
const emit = defineEmits(['busy', 'changed']);
const action = ref(''), form = ref({date: '', reference: '', note: ''}), preview = ref(null), confirmed = ref(false);
const busy = ref(false), error = ref(''), notice = ref(''), uncertain = ref(false);
const funding = computed(() => props.request.cash_funding);
const batch = computed(() => funding.value?.mode === 'batch');
const canWithdraw = computed(() => fundingActionAvailable(props.request, 'withdraw'));
const canReceive = computed(() => fundingActionAvailable(props.request, 'receive'));
const minimum = computed(() => action.value === 'receive' ? funding.value?.withdraw_date : props.request.approved_on);
const actionAllowed = computed(() => action.value === 'withdraw' ? canWithdraw.value : action.value === 'receive' && canReceive.value);
const statement = computed(() => action.value === 'withdraw'
  ? N_('Подтверждаю фактическое списание из банка, включая комиссию.')
  : N_('Подтверждаю фактическое получение наличных в кассу.'));
const unknownState = computed(() => !['draft', 'returned', 'pending', 'paid', 'cancelled', 'rejected', 'deleted'].includes(props.request.status)
  && (props.request.status !== 'approved' || !['unfunded', 'in_transit', 'received'].includes(funding.value?.state)));
const needsStateCheck = computed(() => uncertain.value || unknownState.value);
const flow = computed(() => {
  if (needsStateCheck.value) return {title: N_('Проверить результат операции'),
    description: N_('Сначала обновите состояние заявки. Повторно подтверждать денежный факт пока не нужно.')};
  const status = props.request.status;
  if (status === 'paid') return {number: 4, done: true, actor: N_('Кассир'), title: N_('Выдача наличных завершена'), description: N_('Оплата заявки подтверждена.')};
  if (['draft', 'returned'].includes(status)) return {number: 1, actor: N_('Автор заявки'), title: N_('Подготовка и согласование'),
    description: N_('Автор отправляет заявку; бухгалтер проверяет реквизиты, затем проходят нужные согласования.'),
    next: N_('После утверждения расчётный бухгалтер фиксирует списание банка.')};
  if (status === 'pending') {
    const stages = {
      check: {actor: N_('Расчётный бухгалтер'), description: N_('Проверьте реквизиты и вложения. Решение укажите внизу карточки заявки.')},
      finance: {actor: N_('Финансовый директор'), description: N_('Финансовый директор проверяет бюджет и дату заявки.')},
      director: {actor: N_('Директор'), description: N_('Директор подтверждает заявку по сохранённому маршруту согласования.')},
    };
    return {number: 1, title: N_('Согласование заявки'), actor: N_('Согласующие заявки'),
      description: N_('Заявка проходит согласование. Денежные этапы начнутся после утверждения.'),
      ...stages[props.request.approval_stage], next: N_('После утверждения расчётный бухгалтер фиксирует списание банка.')};
  }
  if (status !== 'approved') return null;
  if (funding.value.state === 'unfunded') return {number: 2, actor: N_('Расчётный бухгалтер'), title: N_('Списание денег из банка'),
    description: N_('Расчётный бухгалтер фиксирует уже выполненное списание банка вместе с комиссией.'),
    next: N_('Далее кассир подтверждает получение наличных в кассу.')};
  if (funding.value.state === 'in_transit') return {number: 3, actor: N_('Кассир'), title: N_('Получение наличных в кассу'),
    description: canReceive.value ? N_('Списание банка учтено. Подтвердите фактическое получение наличных в кассу.')
      : N_('Списание банка учтено. Передайте этот этап кассиру: он подтверждает фактическое получение наличных.'),
    next: N_('После приёма кассир отдельно подтверждает выдачу денег получателю.')};
  return {number: 4, actor: N_('Кассир'), title: batch.value ? N_('Выдача по заявкам') : N_('Выдача денег получателю'),
    description: batch.value ? N_('Кассир проводит и отслеживает выдачу в блоке «Оплата нескольких заявок» ниже.')
      : N_('Кассир фиксирует фактическую выдачу получателю кнопкой «Факт оплаты» внизу карточки.')};
});
let alive = true, generation = 0;
const json = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
function invalidPreview() { preview.value = null; confirmed.value = false; }
watch(() => [form.value.date, form.value.reference, form.value.note], () => { generation++; invalidPreview(); });
watch(() => [props.request, props.request.id, props.request.company_id, props.request.account_id, props.request.currency,
  props.request.status, props.request.version, props.request.actions?.join('|'), funding.value?.version, funding.value?.state,
  funding.value?.can_withdraw, funding.value?.can_receive], () => {
  generation++; invalidPreview();
  if (!actionAllowed.value) action.value = '';
});
function start(which) {
  if (busy.value || uncertain.value || !fundingActionAvailable(props.request, which)) return;
  action.value = which; form.value = {date: tashkentToday(), reference: '', note: ''};
  error.value = ''; notice.value = ''; invalidPreview();
}
function working(value) { busy.value = value; emit('busy', value); }
function body() { return {version: funding.value.version, date: form.value.date, reference: form.value.reference.trim(), note: form.value.note.trim()}; }
async function refresh() {
  if (busy.value) return;
  working(true); error.value = '';
  try {
    const row = await props.api(`/api/requests/${props.request.id}`);
    if (!alive) return;
    uncertain.value = false; action.value = ''; invalidPreview(); emit('changed', row);
    notice.value = N_('Состояние заявки проверено. Повторная операция не создавалась.');
  } catch (failure) { if (alive) error.value = failure.message; }
  finally { if (alive) working(false); }
}
async function check() {
  if (busy.value || uncertain.value || !actionAllowed.value) return;
  const current = ++generation, id = props.request.id, which = action.value, data = body();
  invalidPreview(); working(true); error.value = ''; notice.value = '';
  try {
    const result = await props.api(`/api/requests/${id}/cash-funding/${which}/preview`, json(data));
    if (!alive || current !== generation || props.request.id !== id || action.value !== which) return;
    if (!result?.token || result.request_id !== id || result.action !== which || result.version !== data.version
      || result.date !== data.date || result.reference !== data.reference || result.currency !== props.request.currency
      || result.gross_amount !== funding.value.gross_amount || result.net_amount !== funding.value.net_amount
      || result.fee_amount !== funding.value.fee_amount) throw new Error(tx('Сервер не вернул подтверждение проверки.'));
    preview.value = result;
  } catch (failure) { if (alive && current === generation) error.value = failure.message; }
  finally { if (alive) working(false); }
}
async function commit() {
  if (busy.value || uncertain.value || !actionAllowed.value || !preview.value?.token || !confirmed.value) return;
  const current = ++generation, id = props.request.id, which = action.value;
  const data = {...body(), token: preview.value.token, confirm: true};
  invalidPreview(); working(true); error.value = ''; notice.value = '';
  try {
    const row = await props.api(`/api/requests/${id}/cash-funding/${which}/commit`, json(data));
    if (!alive || current !== generation || props.request.id !== id) return;
    if (row?.id !== id || row.status !== 'approved' || row.cash_funding?.id !== funding.value.id
      || !(Number.isInteger(row.cash_funding?.version) && row.cash_funding.version > data.version)
      || row.cash_funding.state !== (which === 'withdraw' ? 'in_transit' : 'received')
      || row.cash_funding.gross_amount !== funding.value.gross_amount || row.cash_funding.net_amount !== funding.value.net_amount
      || row.cash_funding.fee_amount !== funding.value.fee_amount) throw new Error(tx('Ответ о проведении не получен. Проверьте состояние заявки перед дальнейшими действиями.'));
    action.value = ''; emit('changed', row);
    notice.value = which === 'withdraw' ? batch.value ? N_('Списание банка и комиссия учтены. Выдача по выбранным заявкам ещё не проведена.') : N_('Списание банка и комиссия учтены. Заявка ещё не оплачена.') : batch.value ? N_('Наличные получены в кассу. Выберите заявки для групповой выдачи.') : N_('Наличные получены в кассу. Заявка готова к оплате по прежним правилам.');
  } catch (failure) {
    if (!alive || current !== generation) return;
    if (failure.status == null || failure.status >= 502) {
      uncertain.value = true; action.value = '';
      error.value = N_('Ответ о проведении не получен. Проверьте состояние заявки перед дальнейшими действиями.');
    } else {
      error.value = failure.message;
      if ([409, 428].includes(failure.status)) { uncertain.value = true; action.value = ''; }
    }
  } finally { if (alive) working(false); }
}
onUnmounted(() => { alive = false; generation++; emit('busy', false); });
</script>

<template>
  <section v-if="funding" class="cash-funding-panel" :aria-label="tx('Наличные через банк')">
    <div class="section-head"><h3>{{ tx('Наличные через банк') }}</h3><span class="pill" :class="!needsStateCheck && funding.state === 'received' ? 'good' : 'warn'">{{ needsStateCheck ? tx('Состояние уточняется') : fundingStatus(request) }}</span></div>
    <p v-if="batch" class="sub"><b>{{ tx('Пополнение кассы для нескольких заявок') }}</b></p>
    <div v-if="flow" class="cash-funding-step" :class="{complete: flow.done}" aria-live="polite">
      <span class="cash-funding-step-number" aria-hidden="true">{{ flow.done ? '✓' : flow.number || '?' }}</span>
      <div>
        <small class="cash-funding-step-owner">{{ flow.number ? tx('Шаг {step} из 4 · {actor}', {step: flow.number, actor: tx(flow.actor)}) : tx('Проверка состояния') }}</small>
        <h4>{{ tx(flow.title) }}</h4>
        <p>{{ tx(flow.description) }}</p>
        <p v-if="flow.next" class="sub cash-funding-next">{{ tx(flow.next) }}</p>
      </div>
    </div>
    <p v-if="notice" class="notice" role="status">{{ tx(notice) }}</p>
    <p v-if="error" class="error" role="alert">{{ tx(error) }}</p>
    <div v-if="needsStateCheck" class="actions"><button type="button" class="secondary" :disabled="busy" @click="refresh">{{ tx('Проверить состояние заявки') }}</button></div>
    <div v-else-if="!action" class="actions">
      <button v-if="canWithdraw" type="button" :disabled="busy" @click="start('withdraw')">{{ tx('Зафиксировать списание банка') }}</button>
      <button v-if="canReceive" type="button" :disabled="busy" @click="start('receive')">{{ tx('Принять наличные в кассу') }}</button>
    </div>
    <form v-if="action && actionAllowed" class="cash-funding-action" :aria-label="action === 'withdraw' ? tx('Списание денег из банка') : tx('Получение наличных в кассу')" @submit.prevent="check">
      <div class="form-grid">
        <label>{{ action === 'withdraw' ? tx('Дата списания банка') : tx('Дата получения в кассу') }}<input v-model="form.date" type="date" :min="minimum || undefined" :max="tashkentToday()" :disabled="busy" required></label>
        <label>{{ action === 'withdraw' ? tx('Номер банковского документа') : tx('Номер приходного кассового документа') }}<input v-model="form.reference" maxlength="120" :disabled="busy" required></label>
        <label class="wide">{{ tx('Комментарий (необязательно)') }}<textarea v-model="form.note" maxlength="1000" :disabled="busy"></textarea></label>
      </div>
      <p v-if="action === 'withdraw'" class="sub">{{ batch ? tx('Учитывается выполненное списание. После получения наличных выдача по выбранным заявкам подтверждается отдельно.') : tx('Учитывается выполненное списание. Команда в банк не отправляется; выдача по заявке остаётся отдельным действием.') }}</p>
      <template v-if="preview">
        <p class="notice">{{ action === 'withdraw' ? tx('Проверено списание банка: {gross} {currency}, комиссия {fee}, наличные {net}.', {gross: money(funding.gross_amount), fee: money(funding.fee_amount), net: money(funding.net_amount), currency: request.currency}) : tx('Проверено получение в кассу: {net} {currency}.', {net: money(funding.net_amount), currency: request.currency}) }}</p>
        <label class="cash-funding-attest"><input v-model="confirmed" type="checkbox" :disabled="busy">{{ tx(statement) }}</label>
      </template>
      <div class="form-actions">
        <button type="button" class="secondary" :disabled="busy" @click="action = ''; invalidPreview()">{{ tx('Отмена') }}</button>
        <button v-if="!preview" type="submit" :disabled="busy">{{ busy ? tx('Проверяем…') : tx('Проверить данные') }}</button>
        <button v-else type="button" :disabled="busy || !confirmed" @click="commit">{{ busy ? tx('Сохраняем…') : action === 'withdraw' ? tx('Подтвердить списание банка') : tx('Подтвердить получение в кассу') }}</button>
      </div>
    </form>
    <dl class="kv cash-funding-details">
      <dt>{{ tx('Банковский счёт') }}</dt><dd>{{ funding.bank_account }}</dd>
      <dt>{{ tx('Касса') }}</dt><dd>{{ request.account }}</dd>
      <dt>{{ tx('Всего списать с банка') }}</dt><dd class="num">{{ money(funding.gross_amount) }} {{ request.currency }}</dd>
      <dt>{{ tx('Комиссия') }} · {{ funding.fee_percent }}%</dt><dd class="num">{{ money(funding.fee_amount) }} {{ request.currency }}<small v-if="funding.fee_category_name"> · {{ funding.fee_category_name }}</small></dd>
      <dt>{{ batch ? tx('Сумма пополнения кассы') : tx('Сумма наличной заявки') }}</dt><dd class="num"><b>{{ money(funding.net_amount) }} {{ request.currency }}</b></dd>
      <template v-if="funding.withdraw_date"><dt>{{ tx('Списание банка') }}</dt><dd>{{ funding.withdraw_date }} · {{ funding.withdraw_reference }}</dd></template>
      <template v-if="funding.receive_date"><dt>{{ tx('Получение в кассу') }}</dt><dd>{{ funding.receive_date }} · {{ funding.receive_reference }}</dd></template>
    </dl>
  </section>
</template>

<style scoped>
.cash-funding-panel{padding:16px;border:1px solid var(--line);border-radius:14px;background:var(--surface-muted);margin:16px 0;min-width:0}
.cash-funding-panel .section-head{flex-wrap:wrap;gap:8px}.cash-funding-panel h3{margin:0}.cash-funding-panel .kv{margin:16px 0 0;overflow-wrap:anywhere}.cash-funding-panel .kv dd{min-width:0;white-space:normal}
.cash-funding-step{display:grid;grid-template-columns:32px minmax(0,1fr);gap:12px;margin:14px 0;padding:14px;border:1px solid var(--line);border-radius:12px;background:var(--surface);overflow-wrap:anywhere}
.cash-funding-step-number{display:flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:50%;background:var(--primary-soft);color:var(--primary);font-weight:800}.cash-funding-step.complete .cash-funding-step-number{background:var(--good-soft);color:var(--good)}
.cash-funding-step-owner{display:block;color:var(--muted);font-size:12px;font-weight:700;line-height:1.5}.cash-funding-step h4{margin:4px 0 6px;font-size:15px;line-height:1.45}.cash-funding-step p{margin:0;font-size:13px;line-height:1.6}.cash-funding-step .cash-funding-next{margin-top:7px}
.cash-funding-action{margin-top:14px;padding:14px;border:1px solid var(--line);border-radius:12px;background:var(--surface);min-width:0}.cash-funding-action h4{margin:0 0 14px;font-size:14px}.cash-funding-action .sub{font-size:12px;line-height:1.6}.cash-funding-panel button{max-width:100%;white-space:normal;overflow-wrap:anywhere;text-align:center}.cash-funding-attest{display:flex;align-items:flex-start;gap:8px;margin:12px 0;font-size:13px;line-height:1.6}.cash-funding-attest input{width:auto;flex:none;margin-top:3px}
@media(max-width:520px){.cash-funding-panel{padding:12px}.cash-funding-step{padding:12px;gap:10px}.cash-funding-action{padding:12px}.cash-funding-panel .kv,.cash-funding-action .form-grid{grid-template-columns:minmax(0,1fr)}.cash-funding-panel .kv{gap:5px}.cash-funding-panel .kv dd{margin-bottom:7px;margin-left:0}.cash-funding-panel .actions,.cash-funding-panel .form-actions{flex-wrap:wrap}.cash-funding-panel .actions button,.cash-funding-panel .form-actions button{width:100%}}
</style>
