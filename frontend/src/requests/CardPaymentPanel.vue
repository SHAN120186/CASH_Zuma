<script setup>
// Оплата заявки корпоративной картой: остаток карты, комиссия, проведение двух операций
// («Оплата корп. картой» и «Комиссия за оплату корп. картой») и отметка реестра контроля бухгалтерии.
import {ref, computed, watch, onUnmounted} from 'vue';
import {tx, N_} from '../i18n/index.js';
import {CONTROL_PILLS, controlLabel, units, decimal, plainAmount, dateRu} from './treasury.js';
import {tashkentToday} from './cashFunding.js';

const props = defineProps({api: {type: Function, required: true}, request: {type: Object, required: true}, card: {type: Object, required: true},
  money: {type: Function, required: true}});
const emit = defineEmits(['busy', 'changed']);
const action = ref(''), busy = ref(false), error = ref(''), notice = ref('');
const pay = ref({date: tashkentToday(), reference: '', fee_amount: '', note: ''});
const control = ref({statement_checked: false, documents_checked: false, note: ''});
const cancelReason = ref('');
let alive = true;
watch(() => props.card, card => {
  pay.value.fee_amount = card.fee_amount;
  control.value = {statement_checked: card.statement_checked, documents_checked: card.documents_checked, note: card.control_note || ''};
}, {immediate: true});

const cur = computed(() => props.card.currency);
const paid = computed(() => props.card.state === 'paid');
const feeChanged = computed(() => units(pay.value.fee_amount) !== units(props.card.fee_amount));
const figures = computed(() => {
  const amount = units(props.card.amount), fee = units(pay.value.fee_amount || '0'), balance = units(props.card.balance);
  if (amount == null || fee == null || balance == null) return null;
  return {total: decimal(amount + fee), after: decimal(balance - amount - fee), enough: balance >= amount + fee};
});
const json = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
function working(value) { busy.value = value; emit('busy', value); }

async function run(path, body, message) {
  if (busy.value) return;
  working(true); error.value = ''; notice.value = '';
  try {
    const result = await props.api(path, json(body));
    if (!alive) return;
    action.value = ''; notice.value = message; emit('changed', result);
  } catch (e) {
    if (!alive) return;
    error.value = e.message;
    // Конфликт версий: карточку перечитываем, чтобы повтор шёл уже с актуальными данными.
    if ([409, 428].includes(e.status)) emit('changed');
  }
  finally { if (alive) working(false); }
}
function submitPayment() {
  if (figures.value && !figures.value.enough) { error.value = N_('Сумма оплаты с комиссией больше остатка карты. Пополните карту и повторите.'); return; }
  run(`/api/treasury/requests/${props.request.id}/card-pay`, {version: props.card.version, request_version: props.request.version,
    date: pay.value.date, reference: pay.value.reference, fee_amount: plainAmount(pay.value.fee_amount || '0'), note: pay.value.note}, N_('Оплата картой и комиссия проведены. Операция передана на сверку бухгалтерии.'));
}
function mark(status) {
  run(`/api/treasury/card-control/${props.card.id}`, {version: props.card.version, status, ...control.value},
    status === 'verified' ? N_('Оплата сверена с выпиской и закрывающими документами.') : status === 'issue' ? N_('Расхождение отмечено.') : N_('Оплата возвращена на сверку.'));
}
function cancelPayment() {
  run(`/api/treasury/requests/${props.request.id}/card-cancel`, {version: props.card.version, reason: cancelReason.value},
    N_('Ошибочная оплата картой отменена: операции сторнированы исходной датой.'));
}
onUnmounted(() => { alive = false; emit('busy', false); });
</script>

<template>
  <section class="treasury-panel" :aria-label="tx('Оплата корпоративной картой')">
    <div class="section-head"><h3>{{ tx('Оплата корпоративной картой') }}</h3>
      <span class="pill" :class="paid ? CONTROL_PILLS[card.control_status] : card.enough ? 'gray' : 'bad'">{{ paid ? controlLabel(card.control_status) : card.enough ? tx('Ожидает оплаты') : tx('Не хватает остатка карты') }}</span></div>
    <div class="treasury-tiles">
      <div class="tile"><div class="k">{{ tx('Остаток по карте сейчас') }}</div><div class="v num">{{ money(card.balance) }} <small>{{ cur }}</small></div>
        <small v-if="!paid" class="sub">{{ tx('Доступно с учётом резервов: {value}', {value: money(card.available)}) }}</small></div>
      <div class="tile"><div class="k">{{ tx('Сумма оплаты') }}</div><div class="v num">{{ money(card.amount) }} <small>{{ cur }}</small></div>
        <small class="sub">{{ tx('Комиссия {percent}%: {fee}', {percent: card.fee_percent, fee: money(card.actual_fee ?? card.fee_amount)}) }}</small></div>
      <div class="tile"><div class="k">{{ tx('Итого списать с карты') }}</div><div class="v num">{{ money(card.total) }} <small>{{ cur }}</small></div>
        <small v-if="card.fee_category" class="sub">{{ tx('Статья комиссии: {name}', {name: card.fee_category}) }}</small></div>
    </div>
    <p v-if="paid && card.control_status !== 'verified'" class="notice">{{ tx('Чеки и закрывающие документы приложите ниже, в блок «Прочие подтверждающие документы», до отметки «Сверено».') }}</p>
    <p v-if="!paid && !card.enough" class="warning" role="status">{{ tx('Сумма оплаты с комиссией больше остатка карты. Оплата будет заблокирована, пока карту не пополнят.') }}</p>
    <dl class="kv treasury-kv">
      <dt>{{ tx('Карта') }}</dt><dd>{{ card.account }}</dd>
      <template v-if="card.balance_on_save != null"><dt>{{ tx('Остаток при сохранении заявки') }}</dt><dd class="num">{{ money(card.balance_on_save) }} {{ cur }} <small class="sub">· {{ tx('доступно {value}', {value: money(card.available_on_save)}) }}</small></dd></template>
      <template v-if="paid">
        <dt>{{ tx('Оплачено') }}</dt><dd>{{ dateRu(card.paid_date) }} · {{ card.paid_reference }}</dd>
        <dt>{{ tx('Остаток до оплаты') }}</dt><dd class="num">{{ money(card.balance_before) }} {{ cur }}</dd>
        <dt>{{ tx('Остаток после оплаты') }}</dt><dd class="num"><b>{{ money(card.balance_after) }} {{ cur }}</b></dd>
        <dt>{{ tx('Операции в журнале') }}</dt><dd>{{ tx('Оплата корп. картой № {id}', {id: card.payment_ledger_id}) }}<template v-if="card.fee_ledger_id"> · {{ tx('Комиссия № {id}', {id: card.fee_ledger_id}) }}</template></dd>
        <dt>{{ tx('Контроль бухгалтерии') }}</dt><dd><span class="pill" :class="CONTROL_PILLS[card.control_status]">{{ controlLabel(card.control_status) }}</span>
          <small v-if="card.controlled_by" class="sub"> · {{ card.controlled_by }}</small><br v-if="card.control_note"><q v-if="card.control_note">{{ card.control_note }}</q></dd>
      </template>
    </dl>
    <p v-if="notice" class="notice" role="status">{{ tx(notice) }}</p>
    <p v-if="error" class="error" role="alert">{{ tx(error) }}</p>
    <div v-if="!action" class="actions">
      <button v-if="card.can_pay" type="button" :disabled="busy" @click="action = 'pay'">{{ tx('Провести оплату картой') }}</button>
      <button v-if="card.can_control" type="button" class="secondary" :disabled="busy" @click="action = 'control'">{{ tx('Сверка бухгалтерии') }}</button>
      <span v-else-if="card.paid_by_you && card.state === 'paid'" class="sub">{{ tx('Сверку отмечает другой сотрудник бухгалтерии: оплату провели вы.') }}</span>
      <button v-if="card.can_cancel" type="button" class="ghost" :disabled="busy" @click="action = 'cancel'">{{ tx('Отменить ошибочную оплату') }}</button>
    </div>
    <form v-if="action === 'pay'" class="treasury-action" @submit.prevent="submitPayment">
      <h4>{{ tx('Факт оплаты корпоративной картой') }}</h4>
      <div class="form-grid">
        <label>{{ tx('Дата оплаты') }}<input v-model="pay.date" type="date" :min="request.approved_on || undefined" :max="tashkentToday()" required :disabled="busy"></label>
        <label>{{ tx('Номер чека, слипа или документа банка') }}<input v-model="pay.reference" maxlength="120" required :disabled="busy"></label>
        <label>{{ tx('Фактическая комиссия банка') }} · {{ cur }}<input v-model="pay.fee_amount" inputmode="decimal" required :disabled="busy"></label>
        <label class="wide">{{ feeChanged ? tx('Пояснение: почему комиссия отличается (не менее 10 символов)') : tx('Комментарий (необязательно)') }}<textarea v-model="pay.note" :required="feeChanged" :minlength="feeChanged ? 10 : undefined" maxlength="1000" :disabled="busy"></textarea></label>
      </div>
      <p v-if="figures" :class="figures.enough ? 'notice' : 'warning'">{{ figures.enough ? tx('Будет списано {total} {currency}: оплата и комиссия отдельными операциями. Останется на карте: {after} {currency}.', {total: money(figures.total), after: money(figures.after), currency: cur}) : tx('Не хватает остатка карты: требуется {total} {currency}, на карте {balance} {currency}. Пополните карту.', {total: money(figures.total), balance: money(card.balance), currency: cur}) }}</p>
      <div class="form-actions"><button type="button" class="secondary" :disabled="busy" @click="action = ''">{{ tx('Отмена') }}</button>
        <button type="submit" :disabled="busy || (figures && !figures.enough)">{{ busy ? tx('Сохраняем…') : tx('Провести оплату и комиссию') }}</button></div>
    </form>
    <form v-if="action === 'control'" class="treasury-action" @submit.prevent="mark('verified')">
      <h4>{{ tx('Реестр контроля бухгалтерии') }}</h4>
      <label class="check-inline"><input v-model="control.statement_checked" type="checkbox" :disabled="busy"> {{ tx('Сверено с банковской выпиской') }}</label>
      <label class="check-inline"><input v-model="control.documents_checked" type="checkbox" :disabled="busy"> {{ tx('Закрывающие документы получены') }}</label>
      <label class="wide">{{ tx('Комментарий (для расхождения — обязательно)') }}<textarea v-model="control.note" maxlength="2000" :disabled="busy"></textarea></label>
      <div class="form-actions"><button type="button" class="secondary" :disabled="busy" @click="action = ''">{{ tx('Отмена') }}</button>
        <button type="button" class="secondary" :disabled="busy" @click="mark('issue')">{{ tx('Отметить расхождение') }}</button>
        <button type="submit" :disabled="busy || !control.statement_checked || !control.documents_checked">{{ tx('Сверено') }}</button></div>
    </form>
    <form v-if="action === 'cancel'" class="treasury-action" @submit.prevent="cancelPayment">
      <p class="notice">{{ tx('Только для ошибки ввода: оплата и комиссия сторнируются исходной датой, заявка снова ждёт оплаты.') }}</p>
      <label class="wide">{{ tx('Причина отмены (не менее 10 символов)') }}<textarea v-model="cancelReason" minlength="10" maxlength="1000" required :disabled="busy"></textarea></label>
      <div class="form-actions"><button type="button" class="secondary" :disabled="busy" @click="action = ''">{{ tx('Назад') }}</button>
        <button type="submit" :disabled="busy">{{ tx('Отменить оплату картой') }}</button></div>
    </form>
  </section>
</template>

<style scoped>
.treasury-panel{padding:16px;border:1px solid var(--line);border-radius:14px;background:var(--surface-muted);margin:16px 0;min-width:0}
.treasury-panel .section-head{flex-wrap:wrap;gap:8px}.treasury-panel h3{margin:0}
.treasury-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:14px 0}
.treasury-tiles .tile{background:var(--surface);border:1px solid var(--line);border-radius:12px}.treasury-tiles .v{font-size:20px}
.treasury-kv{overflow-wrap:anywhere}.treasury-kv dd{min-width:0;white-space:normal}
.treasury-action{margin-top:14px;padding:14px;border:1px solid var(--line);border-radius:12px;background:var(--surface);min-width:0}.treasury-action h4{margin:0 0 12px}
.treasury-action .check-inline{display:flex;gap:8px;align-items:center;margin:6px 0}
.treasury-panel button{max-width:100%;white-space:normal}
@media(max-width:520px){.treasury-panel{padding:12px}.treasury-kv,.treasury-action .form-grid{grid-template-columns:minmax(0,1fr)}.treasury-panel .actions,.treasury-panel .form-actions{flex-wrap:wrap}}
</style>
