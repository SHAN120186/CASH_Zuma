<script setup>
// Раздел «Казначейство»: реестр контроля оплат корпоративными картами (бухгалтерия)
// и отчёт «Движение по корп. картам и валютным заявкам». Данные только читаются с сервера;
// отметки сверки проверяет и сохраняет сервер.
import {ref, computed, watch, onMounted} from 'vue';
import {tx, N_} from '../i18n/index.js';
import {CONTROL_PILLS, PAYMENT_STATES, PAYMENT_PILLS, controlLabel, positive, negative, dateRu} from '../requests/treasury.js';

const props = defineProps({api: {type: Function, required: true}, money: {type: Function, required: true}, companyId: {type: Number, default: null},
  today: {type: String, default: ''}, refresh: {type: Number, default: 0}});
const emit = defineEmits(['open-request']);
const tab = ref('control'), status = ref('to_check'), register = ref(null), report = ref(null), busy = ref(false), error = ref(''), notice = ref('');
const monthStart = d => d ? d.slice(0, 8) + '01' : '';
const period = ref({from: monthStart(props.today), to: props.today});
const editing = ref(null), form = ref({statement_checked: false, documents_checked: false, note: ''});
const FILTERS = [['to_check', N_('Ожидает сверки')], ['issue', N_('Расхождение')], ['verified', N_('Сверено')], ['all', N_('Все')]];
let sequence = 0;

async function loadRegister() {
  const current = ++sequence; busy.value = true; error.value = '';
  try {
    const result = await props.api('/api/treasury/card-control?' + new URLSearchParams({status: status.value}));
    if (current === sequence) register.value = result;
  } catch (e) { if (current === sequence) error.value = e.message; }
  finally { if (current === sequence) busy.value = false; }
}
async function loadReport() {
  const current = ++sequence; busy.value = true; error.value = '';
  try {
    const params = new URLSearchParams();
    if (period.value.from) params.set('date_from', period.value.from);
    if (period.value.to) params.set('date_to', period.value.to);
    const result = await props.api('/api/treasury/report?' + params);
    if (current === sequence) report.value = result;
  } catch (e) { if (current === sequence) error.value = e.message; }
  finally { if (current === sequence) busy.value = false; }
}
const load = () => tab.value === 'control' ? loadRegister() : loadReport();
watch([tab, status, () => props.companyId], () => { editing.value = null; notice.value = ''; load(); });
// Действия в карточке заявки, открытой из реестра, обновляют реестр и отчёт.
watch(() => props.refresh, () => load());
onMounted(load);

function edit(row) {
  editing.value = row.id; notice.value = ''; error.value = '';
  form.value = {statement_checked: row.statement_checked, documents_checked: row.documents_checked, note: row.control_note || ''};
}
async function mark(row, next) {
  busy.value = true; error.value = ''; notice.value = '';
  try {
    await props.api(`/api/treasury/card-control/${row.id}`, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({version: row.version, status: next, ...form.value})});
    editing.value = null;
    notice.value = next === 'verified' ? N_('Оплата сверена с выпиской и закрывающими документами.') : N_('Расхождение отмечено.');
    await loadRegister();
  } catch (e) {
    busy.value = false;
    // Отметку уже изменили (другой сотрудник или карточка заявки): реестр перечитывается.
    if ([409, 428].includes(e.status)) { editing.value = null; await loadRegister(); }
    error.value = e.message;
  }
}
const cards = computed(() => report.value?.cards || []);
const fxRows = computed(() => report.value?.fx?.requests || []);
const fxTotals = computed(() => Object.entries(report.value?.fx?.totals || {}));
</script>

<template>
  <div class="treasury-page">
    <div class="tb">
      <div class="seg" role="tablist" :aria-label="tx('Казначейство')">
        <button :class="{on: tab === 'control'}" role="tab" :aria-selected="tab === 'control'" @click="tab = 'control'">{{ tx('Реестр контроля карт') }}</button>
        <button :class="{on: tab === 'report'}" role="tab" :aria-selected="tab === 'report'" @click="tab = 'report'">{{ tx('Движение по картам и валюте') }}</button>
      </div>
      <template v-if="tab === 'control'">
        <div class="seg" role="group" :aria-label="tx('Статус проверки')">
          <button v-for="[key, label] in FILTERS" :key="key" :class="{on: status === key}" @click="status === key ? loadRegister() : (status = key)">{{ tx(label) }}<template v-if="register?.counts?.[key] != null"> · {{ register.counts[key] }}</template></button>
        </div>
      </template>
      <form v-else class="treasury-period" @submit.prevent="loadReport">
        <label>{{ tx('С') }}<input v-model="period.from" type="date" required></label>
        <label>{{ tx('По') }}<input v-model="period.to" type="date" required></label>
        <button type="submit" class="secondary" :disabled="busy">{{ tx('Показать') }}</button>
      </form>
    </div>
    <p v-if="notice" class="notice" role="status">{{ tx(notice) }}</p>
    <p v-if="error" class="error" role="alert">{{ tx(error) }}</p>

    <template v-if="tab === 'control'">
      <p class="sub">{{ tx('Оплаты корпоративными картами после проведения попадают сюда автоматически. Бухгалтер сверяет их с банковской выпиской и закрывающими документами.') }}</p>
      <div class="table-card rsp-wrap">
        <table class="rsp treasury-table">
          <thead><tr><th>{{ tx('Дата') }}</th><th>{{ tx('Заявка') }}</th><th>{{ tx('Карта') }}</th><th>{{ tx('Получатель / назначение') }}</th>
            <th class="num">{{ tx('Оплата') }}</th><th class="num">{{ tx('Комиссия') }}</th><th class="num">{{ tx('Итого') }}</th><th class="num">{{ tx('Остаток после') }}</th>
            <th>{{ tx('Статус проверки') }}</th><th></th></tr></thead>
          <tbody>
            <template v-for="row in register?.items || []" :key="row.id">
              <tr>
                <td class="nowrap" :data-l="tx('Дата')">{{ dateRu(row.date) }}<small>{{ row.reference }}</small></td>
                <td :data-l="tx('Заявка')"><button type="button" class="ghost tiny" @click="emit('open-request', row.request_id)">{{ row.number }}</button></td>
                <td :data-l="tx('Карта')">{{ row.card }}</td>
                <td :data-l="tx('Получатель / назначение')"><b>{{ row.counterparty }}</b><small>{{ row.purpose }}</small></td>
                <td class="num" :data-l="tx('Оплата')">{{ money(row.amount) }}</td>
                <td class="num" :data-l="tx('Комиссия')">{{ money(row.fee) }}</td>
                <td class="num" :data-l="tx('Итого')"><b>{{ money(row.total) }}</b> {{ row.currency }}</td>
                <td class="num" :data-l="tx('Остаток после')">{{ money(row.balance_after) }}</td>
                <td :data-l="tx('Статус проверки')"><span class="pill" :class="CONTROL_PILLS[row.control_status]">{{ controlLabel(row.control_status) }}</span>
                  <small>{{ tx('Выписка: {a} · документы: {b}', {a: row.statement_checked ? tx('да') : tx('нет'), b: row.documents_checked ? tx('да') : tx('нет')}) }}</small>
                  <small v-if="row.controlled_by">{{ row.controlled_by }}</small></td>
                <td><button v-if="row.can_control && editing !== row.id" type="button" class="secondary tiny" :disabled="busy" @click="edit(row)">{{ tx('Сверить') }}</button>
                  <small v-else-if="row.paid_by_you" class="sub">{{ tx('Оплату провели вы — сверяет другой сотрудник') }}</small></td>
              </tr>
              <tr v-if="editing === row.id" class="detail-row"><td colspan="10">
                <form class="treasury-control" @submit.prevent="mark(row, 'verified')">
                  <label class="check-inline"><input v-model="form.statement_checked" type="checkbox"> {{ tx('Сверено с банковской выпиской') }}</label>
                  <label class="check-inline"><input v-model="form.documents_checked" type="checkbox"> {{ tx('Закрывающие документы получены') }}</label>
                  <label>{{ tx('Комментарий (для расхождения — обязательно)') }}<textarea v-model="form.note" maxlength="2000"></textarea></label>
                  <div class="form-actions"><button type="button" class="secondary" @click="editing = null">{{ tx('Отмена') }}</button>
                    <button type="button" class="secondary" :disabled="busy" @click="mark(row, 'issue')">{{ tx('Отметить расхождение') }}</button>
                    <button type="submit" :disabled="busy || !form.statement_checked || !form.documents_checked">{{ tx('Сверено') }}</button></div>
                </form></td></tr>
            </template>
            <tr v-if="register && !register.items.length"><td colspan="10" class="sub">{{ tx('Оплат с таким статусом нет.') }}</td></tr>
          </tbody>
        </table>
      </div>
    </template>

    <template v-else-if="report">
      <h3 class="treasury-h">{{ tx('Раздел 1. Корпоративные карты') }}</h3>
      <div class="table-card rsp-wrap">
        <table class="rsp treasury-table">
          <thead><tr><th>{{ tx('Карта') }}</th><th class="num">{{ tx('Начальный остаток') }}</th><th class="num">{{ tx('Пополнения') }}</th>
            <th class="num">{{ tx('Расход (услуги)') }}</th><th class="num">{{ tx('Комиссии') }}</th><th class="num">{{ tx('Конечный остаток') }}</th><th>{{ tx('Статус проверки бухгалтерией') }}</th></tr></thead>
          <tbody>
            <tr v-for="row in cards" :key="row.account_id">
              <td :data-l="tx('Карта')"><b>{{ row.card }}</b><small>{{ row.currency }}</small></td>
              <td class="num" :data-l="tx('Начальный остаток')">{{ money(row.opening) }}</td>
              <td class="num" :data-l="tx('Пополнения')">{{ money(row.top_ups) }}</td>
              <td class="num" :data-l="tx('Расход (услуги)')">{{ money(row.spent) }}</td>
              <td class="num" :data-l="tx('Комиссии')">{{ money(row.fees) }}</td>
              <td class="num" :data-l="tx('Конечный остаток')"><b>{{ money(row.closing) }}</b></td>
              <td :data-l="tx('Статус проверки бухгалтерией')"><span class="pill" :class="CONTROL_PILLS[row.control_status]">{{ row.control_status === 'none' ? tx('Оплат нет') : controlLabel(row.control_status) }}</span>
                <small>{{ tx('Сверено {v} · ожидает {w} · расхождений {i}', {v: row.control.verified, w: row.control.to_check, i: row.control.issue}) }}</small></td>
            </tr>
            <tr v-if="!cards.length"><td colspan="7" class="sub">{{ tx('Корпоративных карт нет.') }}</td></tr>
          </tbody>
        </table>
      </div>
      <p class="sub">{{ tx('Пополнения — поступления и переводы на карту за вычетом переводов с карты. Остатки — по журналу операций; сторнированные пары в оборотах не показываются.') }}</p>

      <h3 class="treasury-h">{{ tx('Раздел 2. Валютные заявки') }}</h3>
      <div class="table-card rsp-wrap">
        <table class="rsp treasury-table">
          <thead><tr><th>{{ tx('Заявка') }}</th><th>{{ tx('Поставщик') }}</th><th class="num">{{ tx('Заявлено') }}</th><th class="num">{{ tx('Отправлено на блочный счёт, UZS') }}</th>
            <th class="num">{{ tx('Сконвертировано и зачислено') }}</th><th class="num">{{ tx('Оплачено поставщикам') }}</th><th class="num">{{ tx('Свободный остаток') }}</th><th>{{ tx('Статус оплаты') }}</th></tr></thead>
          <tbody>
            <tr v-for="row in fxRows" :key="row.request_id">
              <td :data-l="tx('Заявка')"><button type="button" class="ghost tiny" @click="emit('open-request', row.request_id)">{{ row.number }}</button></td>
              <td :data-l="tx('Поставщик')">{{ row.counterparty }}</td>
              <td class="num" :data-l="tx('Заявлено')">{{ money(row.requested) }} {{ row.currency }}</td>
              <td class="num" :data-l="tx('Отправлено на блочный счёт, UZS')">{{ money(row.blocked_uzs) }}<small v-if="positive(row.returned_uzs)">{{ tx('возвращено {value}', {value: money(row.returned_uzs)}) }}</small></td>
              <td class="num" :data-l="tx('Сконвертировано и зачислено')">{{ money(row.converted) }} {{ row.currency }}</td>
              <td class="num" :data-l="tx('Оплачено поставщикам')">{{ money(row.paid) }} {{ row.currency }}</td>
              <td class="num" :data-l="tx('Свободный остаток')"><b v-if="positive(row.free)">{{ money(row.free) }} {{ row.currency }}</b><span v-else>—</span></td>
              <td :data-l="tx('Статус оплаты')"><span class="pill" :class="PAYMENT_PILLS[row.status]">{{ tx(PAYMENT_STATES[row.status]) }}</span></td>
            </tr>
            <tr v-for="[currency, total] in fxTotals" :key="'total-' + currency" class="treasury-total">
              <td colspan="2"><b>{{ tx('Итого · {currency}', {currency}) }}</b></td>
              <td class="num"><b>{{ money(total.requested) }}</b></td><td class="num"><b>{{ money(total.blocked_uzs) }}</b></td>
              <td class="num"><b>{{ money(total.converted) }}</b></td><td class="num"><b>{{ money(total.paid) }}</b></td><td class="num"><b>{{ money(total.free) }}</b></td><td></td>
            </tr>
            <tr v-if="!fxRows.length"><td colspan="8" class="sub">{{ tx('Валютных заявок за период нет.') }}</td></tr>
          </tbody>
        </table>
      </div>
      <div class="treasury-accounts">
        <section v-for="a in report.fx.accounts" :key="a.account_id" class="card tile">
          <div class="k">{{ a.account }}</div>
          <template v-if="a.free == null"><div class="v num">{{ money(a.balance) }} <small>{{ a.currency }}</small></div>
            <small class="sub">{{ tx('Остаток на {date}', {date: dateRu(report.balances_as_of)}) }}</small></template>
          <template v-else-if="negative(a.free)"><div class="v num treasury-short">−{{ money(a.free.replace('-', '')) }} <small>{{ a.currency }}</small></div>
            <small class="sub">{{ tx('Не хватает для утверждённых заявок · на счёте {balance}, утверждено к оплате {reserved}', {balance: money(a.balance), reserved: money(a.reserved)}) }}</small></template>
          <template v-else><div class="v num">{{ money(a.free) }} <small>{{ a.currency }}</small></div>
            <small class="sub">{{ tx('Свободный остаток · на счёте {balance}, утверждено к оплате {reserved}', {balance: money(a.balance), reserved: money(a.reserved)}) }}</small></template>
        </section>
        <section v-for="a in report.fx.block_accounts" :key="'block-' + a.account_id" class="card tile">
          <div class="k">{{ a.account }}</div><div class="v num">{{ money(a.balance) }} <small>{{ a.currency }}</small></div>
          <small class="sub">{{ tx('Сумы, ожидающие конвертации или возврата · на {date}', {date: dateRu(report.balances_as_of)}) }}</small>
        </section>
      </div>
    </template>
    <p v-else-if="busy" class="sub" role="status">{{ tx('Загружаем отчёт…') }}</p>
  </div>
</template>

<style scoped>
.treasury-period{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap}.treasury-period label{display:grid;gap:4px;font-size:12px}
.treasury-table small{display:block;color:var(--muted);font-size:12px;font-weight:600}
.treasury-table th{white-space:normal;vertical-align:bottom}.treasury-table td.nowrap{white-space:nowrap}
.treasury-short{color:var(--warn)}
.treasury-h{margin:18px 0 8px;font-size:16px}
.treasury-total td{background:var(--surface-muted)}
.treasury-control{display:grid;gap:8px;padding:6px 0}.treasury-control .check-inline{display:flex;gap:8px;align-items:center}
.treasury-accounts{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:14px}
</style>
