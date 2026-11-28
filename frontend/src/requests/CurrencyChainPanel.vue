<script setup>
// Валютная заявка («Заявка $»): цепочка документов
// перевод на блочный счёт → конвертация сум => валюта → приход Банк $ → расход поставщику,
// статус оплаты (не оплачена / частично / оплачена) и остатки валюты. Права и этапы решает сервер (can_*).
import {ref, computed, onUnmounted} from 'vue';
import {tx, N_} from '../i18n/index.js';
import {PAYMENT_STATES, PAYMENT_PILLS, CONVERSION_STATES, CONVERSION_PILLS, conversionAmounts, creditAmounts, defaultSupplierPayment,
        supplierPaymentKind, positive, negative, units, decimal, dateRu} from './treasury.js';
import {tashkentToday} from './cashFunding.js';

const props = defineProps({api: {type: Function, required: true}, request: {type: Object, required: true}, fx: {type: Object, required: true},
  money: {type: Function, required: true}});
const emit = defineEmits(['busy', 'changed', 'open-request']);
const busy = ref(false), error = ref(''), notice = ref(''), open = ref(''), step = ref({});
const plan = ref(null), payment = ref(null);
let alive = true;
const cur = computed(() => props.fx.currency);
const json = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
function working(value) { busy.value = value; emit('busy', value); }

const planFigures = computed(() => plan.value ? conversionAmounts(plan.value.amount, plan.value.rate, plan.value.fee_percent) : null);
const creditFigures = computed(() => {
  if (!open.value.startsWith('credit:')) return null;
  const conversion = props.fx.conversions.find(c => 'credit:' + c.id === open.value);
  if (!conversion) return null;
  const blocked = units(conversion.block_amount), returned = units(conversion.returned_uzs) || 0n;
  return creditAmounts(step.value.amount, step.value.rate, step.value.fee, blocked == null ? null : decimal(blocked - returned));
});
const paymentKind = computed(() => payment.value ? supplierPaymentKind(props.fx, payment.value.amount) : '');

function startPlan() {
  error.value = ''; notice.value = ''; open.value = 'plan';
  const need = positive(props.fx.need_to_convert) ? props.fx.need_to_convert : props.fx.outstanding;
  plan.value = {source_account_id: props.fx.source_accounts[0]?.id ?? null, amount: need, rate: '', fee_percent: '0',
    fee_category_id: props.fx.fee_categories.find(c => /комисс|конверт/i.test(c.name))?.id ?? props.fx.fee_categories[0]?.id ?? null, note: ''};
}
function startStep(conversion, action) {
  error.value = ''; notice.value = ''; open.value = action + ':' + conversion.id;
  step.value = {date: tashkentToday(), reference: '', note: '',
    amount: action === 'block' ? conversion.block_amount : action === 'credit' ? conversion.planned_amount : '',
    rate: action === 'credit' ? conversion.planned_rate : '', fee: action === 'credit' ? conversion.planned_fee : ''};
}
function startPayment() {
  error.value = ''; notice.value = ''; open.value = 'pay';
  payment.value = {amount: defaultSupplierPayment(props.fx), date: tashkentToday(), reference: '', note: ''};
}

async function run(path, body, message) {
  if (busy.value) return;
  working(true); error.value = ''; notice.value = '';
  try {
    const result = await props.api(path, json(body));
    if (!alive) return;
    open.value = ''; notice.value = message; emit('changed', result);
  } catch (e) { if (alive) error.value = e.message; }
  finally { if (alive) working(false); }
}
function submitPlan() {
  if (planFigures.value?.error) { error.value = planFigures.value.error; return; }
  run(`/api/treasury/requests/${props.fx.request_id}/conversions`, {request_version: props.request.version, ...plan.value,
    fee_percent: String(plan.value.fee_percent || '0').replace(',', '.')}, N_('Конвертация запланирована. Следующий шаг — перевод сумов на блочный счёт.'));
}
function submitStep(conversion, action) {
  const body = {version: conversion.version, date: step.value.date, reference: step.value.reference, note: step.value.note};
  if (action === 'block') body.amount = step.value.amount;
  if (action === 'credit') Object.assign(body, {amount: step.value.amount, rate: step.value.rate, fee: step.value.fee || '0'});
  if (action === 'cancel') delete body.date;
  const messages = {block: N_('Сумы переведены на блочный счёт. После зачисления валюты учтите «Приход Банк».'),
    credit: N_('Валюта зачислена на валютный счёт. Можно оплачивать поставщику.'), return: N_('Неиспользованные сумы возвращены на расчётный счёт.'),
    cancel: N_('План конвертации отменён.')};
  run(`/api/treasury/conversions/${conversion.id}/${action}`, body, messages[action]);
}
function submitPayment() {
  if (paymentKind.value === 'excess') { error.value = N_('Сумма больше остатка заявки к оплате.'); return; }
  run(`/api/treasury/requests/${props.fx.request_id}/supplier-pay`, {request_version: props.request.version, ...payment.value},
    paymentKind.value === 'partial' ? N_('Частичная оплата проведена: оплаченная часть выделена в связанную заявку, остаток ждёт оплаты.') : N_('Заявка оплачена полностью.'));
}
const stepClass = done => done ? 'done' : '';
onUnmounted(() => { alive = false; emit('busy', false); });
</script>

<template>
  <section class="treasury-panel" :aria-label="tx('Валютная заявка')">
    <div class="section-head"><h3>{{ tx('Валютная заявка · {currency}', {currency: cur}) }}</h3>
      <span class="pill" :class="PAYMENT_PILLS[fx.payment_status]">{{ tx(PAYMENT_STATES[fx.payment_status]) }}</span></div>
    <p v-if="fx.parent" class="notice">{{ tx('Это часть {n} оплаты заявки {number}. Остатки и цепочка конвертации ведутся в исходной заявке.', {n: fx.sequence, number: fx.parent.number}) }}
      <button type="button" class="secondary tiny" @click="emit('open-request', fx.parent.id)">{{ tx('Открыть {number}', {number: fx.parent.number}) }}</button></p>
    <div class="treasury-tiles">
      <div class="tile"><div class="k">{{ tx('Заявлено') }}</div><div class="v num">{{ money(fx.requested) }} <small>{{ cur }}</small></div></div>
      <div class="tile"><div class="k">{{ tx('Оплачено поставщику') }}</div><div class="v num">{{ money(fx.paid) }} <small>{{ cur }}</small></div></div>
      <div class="tile"><div class="k">{{ tx('Осталось оплатить') }}</div><div class="v num">{{ money(fx.outstanding) }} <small>{{ cur }}</small></div></div>
      <div class="tile"><div class="k">{{ tx('Сконвертировано и зачислено') }}</div><div class="v num">{{ money(fx.converted) }} <small>{{ cur }}</small></div>
        <small v-if="positive(fx.in_progress)" class="sub">{{ tx('В работе: {value} {currency}', {value: money(fx.in_progress), currency: cur}) }}</small>
        <small v-if="positive(fx.earmarked) && positive(fx.outstanding)" class="sub">{{ tx('Куплено и ждёт оплаты поставщику: {value} {currency}', {value: money(fx.earmarked), currency: cur}) }}</small></div>
    </div>
    <div v-if="positive(fx.unused_converted) || positive(fx.account.free) || positive(fx.need_to_convert)" class="treasury-balances">
      <p v-if="positive(fx.unused_converted)" class="notice"><b>{{ tx('Остаток {currency} по заявке: {value}', {currency: cur, value: money(fx.unused_converted)}) }}</b> — {{ tx('куплено больше заявленной суммы; остаток свободен для следующих заявок.') }}</p>
      <p v-if="positive(fx.account.free)" class="notice"><b>{{ tx('Свободный остаток на счёте «{account}»: {value} {currency}', {account: fx.account.name, value: money(fx.account.free), currency: cur}) }}</b> — {{ tx('можно использовать для следующих заявок без новой конвертации.') }}</p>
      <p v-if="positive(fx.need_to_convert)" class="warning">{{ tx('Не хватает валюты: нужно сконвертировать {value} {currency}.', {value: money(fx.need_to_convert), currency: cur}) }}</p>
    </div>
    <p class="sub">{{ negative(fx.account.free)
      ? tx('Счёт «{account}»: остаток {balance} {currency}, утверждено к оплате {reserved} {currency}; по всем утверждённым заявкам не хватает {short} {currency}.', {account: fx.account.name, balance: money(fx.account.balance), reserved: money(fx.account.reserved), short: money(fx.account.free.replace('-', '')), currency: cur})
      : tx('Счёт «{account}»: остаток {balance} {currency}, утверждено к оплате {reserved} {currency}, свободно {free} {currency}.', {account: fx.account.name, balance: money(fx.account.balance), reserved: money(fx.account.reserved), free: money(fx.account.free), currency: cur}) }}</p>

    <h4 class="treasury-sub">{{ tx('Цепочка документов') }}</h4>
    <p v-if="!fx.conversions.length" class="sub">{{ tx('Конвертаций по заявке нет.') }}</p>
    <article v-for="c in fx.conversions" :key="c.id" class="fx-doc">
      <div class="section-head"><b>{{ tx('Конвертация сум → {currency} № {id}', {currency: cur, id: c.id}) }}</b>
        <span class="pill" :class="CONVERSION_PILLS[c.state]">{{ tx(CONVERSION_STATES[c.state]) }}</span></div>
      <p class="sub">{{ tx('План: {amount} {currency} по курсу {rate} = {uzs} UZS, комиссия {fee} UZS ({percent}%). На блочный счёт: {block} UZS.', {amount: money(c.planned_amount), currency: cur, rate: money(c.planned_rate), uzs: money(c.planned_uzs), fee: money(c.planned_fee), percent: c.fee_percent, block: money(c.block_amount)}) }}</p>
      <ol class="fx-steps">
        <li :class="stepClass(c.documents.block)"><b>{{ tx('1. Перевод на блочный счёт') }}</b>
          <span v-if="c.documents.block">{{ dateRu(c.block_date) }} · {{ tx('документ {ref}', {ref: c.block_reference}) }} · <b class="num">{{ money(c.documents.block.amount) }} UZS</b> · {{ c.source_account }} → {{ c.block_account }} · {{ tx('операция № {id}', {id: c.documents.block.ledger_id}) }}</span>
          <span v-else class="sub">{{ tx('Ожидает перевода с сумового счёта «{account}».', {account: c.source_account}) }}</span></li>
        <li :class="stepClass(c.documents.sell)"><b>{{ tx('2. Конвертация сум → {currency}', {currency: cur}) }}</b>
          <span v-if="c.documents.sell">{{ tx('Курс {rate}; списано {sold} UZS (операция № {id})', {rate: money(c.rate), sold: money(c.sold_uzs), id: c.documents.sell.ledger_id}) }}<template v-if="c.documents.fee"> · {{ tx('комиссия {fee} UZS (операция № {id})', {fee: money(c.actual_fee), id: c.documents.fee.ledger_id}) }}</template></span>
          <span v-else class="sub">{{ tx('Ожидает исполнения банком.') }}</span></li>
        <li :class="stepClass(c.documents.buy)"><b>{{ tx('3. Приход Банк {currency}', {currency: cur}) }}</b>
          <span v-if="c.documents.buy">{{ dateRu(c.credit_date) }} · {{ tx('документ {ref}', {ref: c.credit_reference}) }} · <b class="num">{{ money(c.credited_amount) }} {{ cur }}</b> → {{ c.target_account }} · {{ tx('операция № {id}', {id: c.documents.buy.ledger_id}) }}</span>
          <span v-else class="sub">{{ tx('Ожидает зачисления на валютный счёт.') }}</span></li>
        <li v-if="c.documents.return" class="done"><b>{{ tx('Возврат неиспользованных сумов') }}</b>
          <span>{{ dateRu(c.return_date) }} · {{ tx('документ {ref}', {ref: c.return_reference}) }} · <b class="num">{{ money(c.documents.return.amount) }} UZS</b> · {{ tx('операция № {id}', {id: c.documents.return.ledger_id}) }}</span></li>
      </ol>
      <p v-if="positive(c.leftover_uzs)" class="warning">{{ tx('Остаток на блочном счёте по конвертации: {value} UZS.', {value: money(c.leftover_uzs)}) }}</p>
      <div v-if="!open.endsWith(':' + c.id)" class="actions">
        <button v-if="c.can_block" type="button" :disabled="busy" @click="startStep(c, 'block')">{{ tx('Перевести на блочный счёт') }}</button>
        <button v-if="c.can_credit" type="button" :disabled="busy" @click="startStep(c, 'credit')">{{ tx('Учесть приход валюты') }}</button>
        <button v-if="c.can_return" type="button" class="secondary" :disabled="busy" @click="startStep(c, 'return')">{{ tx('Вернуть остаток на расчётный счёт') }}</button>
        <button v-if="c.can_cancel" type="button" class="ghost" :disabled="busy" @click="startStep(c, 'cancel')">{{ tx('Отменить план') }}</button>
      </div>
      <form v-else class="treasury-action" @submit.prevent="submitStep(c, open.split(':')[0])">
        <div class="form-grid">
          <template v-if="!open.startsWith('cancel:')">
            <label>{{ tx('Дата документа банка') }}<input v-model="step.date" type="date" :max="tashkentToday()" required :disabled="busy"></label>
            <label>{{ tx('Номер документа банка') }}<input v-model="step.reference" maxlength="120" required :disabled="busy"></label>
          </template>
          <label v-if="open.startsWith('block:')">{{ tx('Сумма перевода, UZS') }}<input v-model="step.amount" inputmode="decimal" required :disabled="busy"></label>
          <template v-if="open.startsWith('credit:')">
            <label>{{ tx('Зачислено, {currency}', {currency: cur}) }}<input v-model="step.amount" inputmode="decimal" required :disabled="busy"></label>
            <label>{{ tx('Курс конвертации, UZS за 1 {currency}', {currency: cur}) }}<input v-model="step.rate" inputmode="decimal" required :disabled="busy"></label>
            <label>{{ tx('Комиссия за конвертацию, UZS') }}<input v-model="step.fee" inputmode="decimal" :disabled="busy"></label>
          </template>
          <label class="wide">{{ open.startsWith('cancel:') ? tx('Причина отмены (не менее 10 символов)') : tx('Комментарий (необязательно)') }}<textarea v-model="step.note" :required="open.startsWith('cancel:')" :minlength="open.startsWith('cancel:') ? 10 : undefined" maxlength="1000" :disabled="busy"></textarea></label>
        </div>
        <p v-if="creditFigures && !creditFigures.error" :class="creditFigures.enough ? 'notice' : 'warning'">{{ creditFigures.enough ? tx('Будет списано с блочного счёта {sold} UZS и комиссия {fee} UZS. Останется на блочном счёте: {leftover} UZS.', {sold: money(creditFigures.sold), fee: money(creditFigures.fee), leftover: money(creditFigures.leftover)}) : tx('На блочном счёте не хватает сумов для этой покупки: проверьте курс и сумму.') }}</p>
        <p v-if="open.startsWith('return:')" class="sub">{{ tx('Вернётся {value} UZS на счёт «{account}».', {value: money(c.leftover_uzs), account: c.source_account}) }}</p>
        <div class="form-actions"><button type="button" class="secondary" :disabled="busy" @click="open = ''">{{ tx('Отмена') }}</button>
          <button type="submit" :disabled="busy || (creditFigures && (!!creditFigures.error || !creditFigures.enough))">{{ busy ? tx('Сохраняем…') : tx('Подтвердить') }}</button></div>
      </form>
    </article>
    <div v-if="fx.can_plan && open !== 'plan'" class="actions"><button type="button" class="secondary" :disabled="busy" @click="startPlan">{{ tx('Запланировать конвертацию') }}</button></div>
    <form v-if="open === 'plan' && plan" class="treasury-action" @submit.prevent="submitPlan">
      <h4>{{ tx('Конвертация сум → {currency}', {currency: cur}) }}</h4>
      <div class="form-grid">
        <label>{{ tx('Сумовой расчётный счёт') }}<select v-model="plan.source_account_id" required :disabled="busy"><option v-for="a in fx.source_accounts" :key="a.id" :value="a.id">{{ a.name }}</option></select></label>
        <label>{{ tx('Купить, {currency}', {currency: cur}) }}<input v-model="plan.amount" inputmode="decimal" required :disabled="busy"></label>
        <label>{{ tx('Курс (план), UZS за 1 {currency}', {currency: cur}) }}<input v-model="plan.rate" inputmode="decimal" required :disabled="busy"></label>
        <label>{{ tx('Комиссия за конвертацию, %') }}<input v-model="plan.fee_percent" inputmode="decimal" :disabled="busy"></label>
        <label>{{ tx('Статья комиссии') }}<select v-model="plan.fee_category_id" required :disabled="busy"><option v-for="cat in fx.fee_categories" :key="cat.id" :value="cat.id">{{ cat.name }}</option></select></label>
        <label class="wide">{{ tx('Комментарий (необязательно)') }}<textarea v-model="plan.note" maxlength="1000" :disabled="busy"></textarea></label>
      </div>
      <p v-if="planFigures && !planFigures.error" class="notice">{{ tx('Покупка: {uzs} UZS, комиссия {fee} UZS. Перевести на блочный счёт: {block} UZS.', {uzs: money(planFigures.uzs), fee: money(planFigures.fee), block: money(planFigures.block)}) }}</p>
      <p v-else-if="planFigures?.error && plan.rate" class="field-error">{{ tx(planFigures.error) }}</p>
      <div class="form-actions"><button type="button" class="secondary" :disabled="busy" @click="open = ''">{{ tx('Отмена') }}</button>
        <button type="submit" :disabled="busy || !planFigures || !!planFigures.error">{{ tx('Запланировать') }}</button></div>
    </form>

    <h4 class="treasury-sub">{{ tx('Расход → Поставщик') }}</h4>
    <ul v-if="fx.parts.length" class="fx-parts">
      <li v-for="p in fx.parts" :key="p.request_id + ':' + p.sequence"><span>{{ tx('Оплата {n}', {n: p.sequence}) }} · <button type="button" class="ghost tiny" @click="emit('open-request', p.request_id)">{{ p.number }}</button></span>
        <span>{{ p.date ? dateRu(p.date) : '—' }} · {{ p.reference }}</span><b class="num">{{ money(p.amount) }} {{ cur }}</b></li>
    </ul>
    <p v-else class="sub">{{ tx('Оплат поставщику пока нет.') }}</p>
    <div v-if="fx.can_pay && open !== 'pay'" class="actions"><button type="button" :disabled="busy" @click="startPayment">{{ tx('Оплатить поставщику') }}</button></div>
    <form v-if="open === 'pay' && payment" class="treasury-action" @submit.prevent="submitPayment">
      <div class="form-grid">
        <label>{{ tx('Сумма, {currency}', {currency: cur}) }}<input v-model="payment.amount" inputmode="decimal" required :disabled="busy"></label>
        <label>{{ tx('Дата списания') }}<input v-model="payment.date" type="date" :min="request.approved_on || undefined" :max="tashkentToday()" required :disabled="busy"></label>
        <label>{{ tx('Номер платёжного документа (SWIFT)') }}<input v-model="payment.reference" minlength="2" maxlength="160" required :disabled="busy"></label>
        <label class="wide">{{ tx('Комментарий (необязательно)') }}<textarea v-model="payment.note" maxlength="3000" :disabled="busy"></textarea></label>
      </div>
      <p v-if="paymentKind === 'partial'" class="notice">{{ tx('Частичная оплата: оплаченная часть выделится в связанную заявку, остаток {rest} {currency} останется утверждённым к оплате.', {rest: money(decimal(units(fx.outstanding) - units(payment.amount))), currency: cur}) }}</p>
      <p v-else-if="paymentKind === 'full'" class="notice">{{ tx('Оплата закроет заявку полностью.') }}</p>
      <p v-else-if="paymentKind === 'excess'" class="warning">{{ tx('Сумма больше остатка заявки к оплате.') }}</p>
      <div class="form-actions"><button type="button" class="secondary" :disabled="busy" @click="open = ''">{{ tx('Отмена') }}</button>
        <button type="submit" :disabled="busy || !paymentKind || paymentKind === 'excess'">{{ busy ? tx('Сохраняем…') : tx('Провести оплату') }}</button></div>
    </form>
    <p v-if="notice" class="notice" role="status">{{ tx(notice) }}</p>
    <p v-if="error" class="error" role="alert">{{ tx(error) }}</p>
  </section>
</template>

<style scoped>
.treasury-panel{padding:16px;border:1px solid var(--line);border-radius:14px;background:var(--surface-muted);margin:16px 0;min-width:0;overflow-wrap:anywhere}
.treasury-panel .section-head{flex-wrap:wrap;gap:8px}.treasury-panel h3{margin:0}
.treasury-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.treasury-tiles .tile{background:var(--surface);border:1px solid var(--line);border-radius:12px}.treasury-tiles .v{font-size:19px}
.treasury-balances p{margin:6px 0}.treasury-sub{margin:18px 0 8px;font-size:14px}
.fx-doc{padding:12px 14px;margin:10px 0;border:1px solid var(--line);border-radius:12px;background:var(--surface)}
.fx-steps{list-style:none;margin:10px 0 6px;padding:0;display:grid;gap:8px}
.fx-steps li{display:grid;gap:3px;padding:8px 10px 8px 14px;border-left:3px solid var(--line-strong);border-radius:4px;background:var(--surface-muted);font-size:13px}
.fx-steps li.done{border-left-color:var(--good)}
.fx-parts{list-style:none;margin:0;padding:0;display:grid;gap:6px}
.fx-parts li{display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px 12px;padding:8px 10px;border:1px solid var(--line);border-radius:10px;background:var(--surface);font-size:13px}
.treasury-action{margin-top:12px;padding:14px;border:1px solid var(--line);border-radius:12px;background:var(--surface);min-width:0}.treasury-action h4{margin:0 0 12px}
.treasury-panel button{max-width:100%;white-space:normal}
@media(max-width:520px){.treasury-panel{padding:12px}.treasury-action .form-grid{grid-template-columns:minmax(0,1fr)}.treasury-panel .actions,.treasury-panel .form-actions{flex-wrap:wrap}}
</style>
