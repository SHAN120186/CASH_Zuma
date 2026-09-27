<script setup>
import {computed, ref, watch} from 'vue';
import BalanceCard from './BalanceCard.vue';
import ForecastCard from './ForecastCard.vue';
import AppIcon from '../AppIcon.vue';
import {toCents, formatCents, formatShortDate, addDays, sameDayPreviousMonth} from './format.js';
import {balanceHistory, monthPlanFact} from './history.js';
import {canPayAny} from './rights.js';

const props = defineProps({
  api: {type: Function, required: true},
  companyId: {type: Number, required: true},
  company: {type: Object, default: null},
  currency: {type: String, required: true},
  today: {type: String, required: true},
  refresh: {type: Number, default: 0},
  has: {type: Function, required: true},
  canDecide: {type: Function, required: true},
  // Forecast horizon, shared with the Calendar page through App.
  days: {type: Number, default: 30},
  // Approval track of a request and its step marks, the same as in «Заявки».
  track: {type: Function, default: null},
  marks: {type: Object, default: () => ({})},
});
const emit = defineEmits(['go', 'edit-reserve', 'update:days']);

const HISTORY_DAYS = 30, PAGE = 100, MAX_PAGES = 30, MAX_REQUEST_PAGES = 10, SHOWN_ROWS = 6, PENDING_PREVIEW = 4;
const HORIZONS = [7, 30, 90];
const scenario = ref('A');
const horizon = computed(() => (HORIZONS.includes(props.days) ? props.days : 30));
const s = ref(fresh());
function fresh() {
  return {
    dash: null, dashDays: null, now: null, prev: null, accounts: null, activeAccounts: null, history: null, historyNote: '',
    latest: null, pending: null, pendingAll: null, pendingTotal: 0, pendingLoaded: 0, approved: null, approvedTotal: 0, approvedLoaded: 0,
    budgets: null, report: null, errors: {}, loading: true, requestsLoading: true, dashLoading: false,
  };
}

// Every block loads independently; one failing endpoint never blanks the screen.
const settle = p => p.then(v => ({ok: true, v}), e => ({ok: false, e}));
const aborted = r => !r.ok && r.e?.name === 'AbortError';
const q = obj => new URLSearchParams(obj).toString();
let generation = 0, dashGeneration = 0;

// After the first render each step writes only the fields it owns, so a slow
// step never puts back data that a newer one has already replaced.
function patch(gen, fields, errors = {}) {
  if (gen !== generation) return;
  s.value = {...s.value, ...fields, errors: {...s.value.errors, ...errors}};
}

// Reads a paginated list to the end (at most `maxPages`), so the caller can
// sort it itself and knows how much was left out.
async function allPages(path, params, maxPages, gen) {
  const items = [];
  let page = 1, total = Infinity;
  while (items.length < total && page <= maxPages) {
    const data = await props.api(`${path}?${q({...params, paginated: 'true', page, page_size: PAGE})}`);
    if (gen !== generation) break;
    items.push(...data.items);
    total = data.total;
    if (!data.items.length) break;
    page += 1;
  }
  return {items, total: Number.isFinite(total) ? total : items.length};
}

async function load() {
  const gen = ++generation, dashStart = dashGeneration;
  s.value = fresh();
  const c = props.currency, cid = props.companyId, today = props.today, days = horizon.value;
  const month = today.slice(0, 7), prevDay = sameDayPreviousMonth(today);
  const start = addDays(today, -(HISTORY_DAYS - 1));
  const report = day => props.api(`/api/group-report?${q({company_id: cid, currency: c, scenario: 'A', month: day.slice(0, 7), day})}`);

  // Request lists may take several pages; they fill in after the balance and
  // KPIs are already on screen. The API returns the latest due dates first, so
  // the urgent requests are at the end of the list: read it whole and sort here.
  const requests = Promise.all([
    settle(allPages('/api/requests', {state: 'pending', currency: c}, MAX_REQUEST_PAGES, gen)),
    settle(allPages('/api/requests', {state: 'approved', currency: c, date_to: today}, MAX_REQUEST_PAGES, gen)),
  ]);
  const [dash, now, prev, active, archived, latest, budgets] = await Promise.all([
    settle(props.api(`/api/dashboard?${q({currency: c, days})}`)),
    settle(report(today)),
    settle(report(prevDay)),
    settle(props.api('/api/accounts?archived=false')),
    settle(props.api('/api/accounts?archived=true')),
    settle(props.api(`/api/ledger?${q({paginated: 'true', page: 1, page_size: 6, currency: c})}`)),
    settle(props.api(`/api/budgets?${q({currency: c, month})}`)),
  ]);
  if (gen !== generation || [dash, now, active].some(aborted)) return;

  const errors = {};
  const take = (key, r) => { if (!r.ok) errors[key] = r.e.message; return r.ok ? r.v : null; };
  const first = {
    now: take('now', now),
    prev: prev.ok ? prev.v : null,
    latest: take('latest', latest)?.items ?? null,
    budgets: take('budgets', budgets),
    activeAccounts: active.ok ? active.v.filter(x => x.currency === c) : null,
    // Balances and history need archived accounts too: they may still hold money.
    accounts: active.ok && archived.ok ? [...active.v, ...archived.v].filter(x => x.currency === c) : null,
    loading: false,
  };
  if (!active.ok) errors.accounts = active.e.message;
  else if (!archived.ok) errors.archived = archived.e.message;
  // A horizon switched during this load has already brought newer forecast data.
  if (dashGeneration === dashStart) Object.assign(first, {dash: take('dash', dash), dashDays: dash.ok ? days : null});
  patch(gen, first, errors);
  loadReport(gen);

  requests.then(([pending, approved]) => {
    if (gen !== generation || [pending, approved].some(aborted)) return;
    const fields = {requestsLoading: false}, errs = {};
    if (pending.ok) {
      Object.assign(fields, {pendingAll: pending.v.items, pending: pending.v.items.filter(props.canDecide),
        pendingTotal: pending.v.total, pendingLoaded: pending.v.items.length});
    } else errs.pending = pending.e.message;
    if (approved.ok) Object.assign(fields, {approved: approved.v.items, approvedTotal: approved.v.total, approvedLoaded: approved.v.items.length});
    else errs.approved = approved.e.message;
    patch(gen, fields, errs);
  });

  // The balance history needs every ledger entry of the window; page through it.
  if (first.accounts) {
    try {
      const entries = await allPages('/api/ledger', {currency: c, date_from: start, date_to: today}, MAX_PAGES, gen);
      if (gen !== generation) return;
      if (entries.items.length < entries.total) {
        patch(gen, {historyNote: 'За 30 дней слишком много операций для графика на этом экране. Полная история — в разделе «Операции».'});
      } else {
        patch(gen, {history: balanceHistory({today, days: HISTORY_DAYS, accounts: first.accounts, entries: entries.items})});
      }
    } catch (e) {
      if (e.name !== 'AbortError') patch(gen, {}, {history: e.message});
    }
  }
}

async function loadReport(gen = generation) {
  const year = props.today.slice(0, 4), wanted = scenario.value;
  // A slower answer for another scenario must not land under the selected button.
  const current = () => gen === generation && wanted === scenario.value;
  try {
    const r = await props.api(`/api/report?${q({currency: props.currency, year, company_id: props.companyId, scenario: wanted, as_of: props.today})}`);
    if (current()) s.value = {...s.value, report: r, errors: {...s.value.errors, report: ''}};
  } catch (e) {
    if (e.name !== 'AbortError' && current()) s.value = {...s.value, errors: {...s.value.errors, report: e.message}};
  }
}

// A horizon switch reloads only the dashboard. Labels follow the horizon of the
// data on screen (dashDays), so a slow or failed switch never mislabels it.
async function reloadForecast() {
  const gen = generation, mine = ++dashGeneration, days = horizon.value;
  const current = () => gen === generation && mine === dashGeneration;
  s.value = {...s.value, dashLoading: true};
  try {
    const d = await props.api(`/api/dashboard?${q({currency: props.currency, days})}`);
    if (current()) s.value = {...s.value, dash: d, dashDays: days, dashLoading: false, errors: {...s.value.errors, dash: ''}};
  } catch (e) {
    if (e.name !== 'AbortError' && current()) s.value = {...s.value, dashLoading: false, errors: {...s.value.errors, dash: e.message}};
  }
}

watch(() => [props.refresh, props.currency, props.companyId], load, {immediate: true});
watch(horizon, (h, old) => { if (h !== old) reloadForecast(); });
watch(scenario, () => { s.value = {...s.value, report: null, errors: {...s.value.errors, report: ''}}; loadReport(); });

// ---- derived figures ----
const c = computed(() => props.currency);
const plural = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
};
const sumCents = (list, pick) => list.reduce((sum, x) => {
  const v = toCents(pick(x));
  return sum == null || v == null ? null : sum + v;
}, 0);

const balanceState = computed(() => {
  if (s.value.loading) return 'loading';
  if (!s.value.now) return 'error';
  if (s.value.accounts && !s.value.accounts.length) return 'empty';
  return 'ready';
});
// «Доступно» (CASHFLOW_RULES_RU.md) comes from the server's daily group report:
// balance on accounts at the end of today minus approved, unpaid requests due
// by today. The split line uses the same answer, so the three figures reconcile.
const onAccounts = computed(() => toCents(s.value.now?.closing) ?? (s.value.accounts ? sumCents(s.value.accounts, a => a.balance) : null));
const reserved = computed(() => toCents(s.value.now?.reserved));
const available = computed(() => toCents(s.value.now?.available));
const change = computed(() => {
  const now = available.value, before = toCents(s.value.prev?.available);
  if (now == null || before == null || before === 0) return null;
  return {ratio: (now - before) / Math.abs(before), since: s.value.prev.day};
});

const kpi = computed(() => {
  const d = s.value.dash;
  if (!d) return null;
  const inflow = toCents(d.fact_in), outflow = toCents(d.fact_out);
  const f = d.forecast || [];
  const end = f.length ? toCents(f.at(-1).balance) : null;
  const low = f.reduce((a, x) => (a == null || toCents(x.balance) < toCents(a.balance) ? x : a), null);
  return {inflow, outflow, net: inflow == null || outflow == null ? null : inflow - outflow, end, low};
});
const monthStart = computed(() => props.today.slice(0, 8) + '01');
const kpiCards = computed(() => {
  const k = kpi.value, cur = props.currency, since = `с ${formatShortDate(monthStart.value)} · факт`;
  if (!k) return [];
  return [
    {label: 'Доходы', value: formatCents(k.inflow, cur), tone: 'in', note: since},
    {label: 'Расходы', value: formatCents(k.outflow, cur), tone: 'out', note: since},
    {label: 'Чистый поток', value: formatCents(k.net, cur, {sign: true}), tone: k.net >= 0 ? 'in' : 'out', note: 'доходы − расходы месяца'},
    {label: `Прогноз на ${s.value.dashDays ?? horizon.value} дней`, value: formatCents(k.end, cur), tone: '',
     note: k.low ? `минимум ${formatCents(toCents(k.low.balance), cur)} · ${formatShortDate(k.low.date)}` : ''},
  ];
});

const forecastState = computed(() => (s.value.loading || s.value.dashLoading ? 'loading' : s.value.errors.dash || !s.value.dash ? 'error' : 'ready'));
// Approved outgoing payments after today, in forecast order (the old «Ближайшие выплаты»).
const upcoming = computed(() => {
  const out = [];
  for (const d of s.value.dash?.forecast || []) {
    if (d.date <= props.today) continue;
    for (const e of d.events || []) if (e.kind === 'out') out.push({date: d.date, name: e.name, amount: e.amount, id: e.id});
    if (out.length >= 5) break;
  }
  return out.slice(0, 5);
});
const accountList = computed(() => s.value.activeAccounts || []);
// Requests at every stage, nearest due date first (the old home list).
const pendingPreview = computed(() => [...(s.value.pendingAll || [])].sort(byDue).slice(0, PENDING_PREVIEW));

const byDue = (a, b) => a.date.localeCompare(b.date) || a.id - b.id;
const payments = computed(() => (s.value.approved || []).filter(r => r.date <= props.today).sort(byDue));
const decisions = computed(() => [...(s.value.pending || [])].sort(byDue));
const approver = computed(() => props.has('approve'));
// Paying a request needs a payment right; the general right to write never pays.
const canPay = computed(() => canPayAny(props.has));

const risks = computed(() => {
  const out = [], d = s.value.dash, cur = c.value;
  const forecast = d?.forecast || [];
  const reserveDay = forecast.find(x => x.reserve_risk);
  if (reserveDay) {
    out.push({level: 'bad', title: 'Остаток опустится ниже резерва',
      text: `${formatShortDate(reserveDay.date)}: ${formatCents(toCents(reserveDay.balance), cur)} ${cur} при резерве ${formatCents(toCents(d.reserve), cur)}`,
      go: ['calendar']});
  }
  // Every account that goes negative within the forecast, with its first such day.
  const short = new Map();
  for (const day of forecast) {
    for (const a of day.account_shortfalls || []) if (!short.has(a.account_id)) short.set(a.account_id, {...a, date: day.date});
  }
  if (short.size) {
    const list = [...short.values()];
    out.push({level: list.every(a => a.allow_overdraft) ? 'warn' : 'bad',
      title: list.length === 1 ? 'Счёт уйдёт в минус' : `${list.length} ${plural(list.length, 'счёт уйдёт', 'счёта уйдут', 'счетов уйдут')} в минус`,
      text: list.slice(0, 3)
        .map(a => `${a.account}, ${formatShortDate(a.date)}: ${formatCents(toCents(a.balance), cur)}${a.allow_overdraft ? ' (овердрафт разрешён)' : ''}`).join(' · ')
        + (list.length > 3 ? ` и ещё ${list.length - 3}` : ''),
      go: ['calendar']});
  }
  const reserve = toCents(d?.reserve);
  if (reserve && available.value != null && available.value < reserve) {
    out.push({level: 'bad', title: 'Доступно меньше минимального резерва', text: `Резерв ${formatCents(reserve, cur)} ${cur}`, go: ['accounts']});
  }
  const over = (s.value.budgets || []).filter(b => b.limit != null && (toCents(b.remaining) ?? 0) < 0);
  if (over.length) {
    out.push({level: 'warn', title: over.length === 1 ? 'Бюджет статьи превышен' : `Превышен бюджет ${over.length} ${plural(over.length, 'статьи', 'статей', 'статей')}`,
      text: over.slice(0, 3).map(b => `${b.category}: перерасход ${formatCents(-toCents(b.remaining), cur)}`).join(' · '), go: ['budgets']});
  }
  const overdue = payments.value.filter(r => r.date < props.today);
  if (overdue.length) {
    out.push({level: 'warn', title: overdue.length === 1 ? 'Просрочена утверждённая выплата' : `Просрочено утверждённых выплат: ${overdue.length}`,
      text: overdue.slice(0, 2).map(r => `${r.number} · ${r.counterparty}`).join(' · '), go: ['requests', {status: 'approved'}]});
  }
  return out;
});
// Sources that failed: the risk list is incomplete, never «всё в порядке».
const riskGaps = computed(() => {
  const e = s.value.errors, gaps = [];
  if (e.now || e.accounts) gaps.push('доступный остаток');
  if (e.dash) gaps.push('прогноз');
  if (e.budgets) gaps.push('бюджеты');
  if (e.approved) gaps.push('утверждённые заявки');
  return gaps;
});

const chips = computed(() => {
  const e = s.value.errors, out = [];
  const count = (n, partial) => (s.value.requestsLoading ? '…' : n == null ? '—' : n + (partial ? '+' : ''));
  if (approver.value) {
    const n = e.pending ? null : decisions.value.length;
    out.push({target: 'ov-decisions', text: count(n, s.value.pendingLoaded < s.value.pendingTotal),
      words: `${plural(n ?? 0, 'заявка ждёт', 'заявки ждут', 'заявок ждут')} вашего решения`, tone: n ? 'hot' : ''});
  }
  const pay = e.approved ? null : payments.value.length;
  out.push({target: 'ov-payments', text: count(pay, s.value.approvedLoaded < s.value.approvedTotal),
    words: `${plural(pay ?? 0, 'платёж', 'платежа', 'платежей')} к оплате`, tone: pay ? 'hot' : ''});
  const gaps = riskGaps.value.length, n = risks.value.length;
  out.push({target: 'ov-risks', text: gaps && !n && !s.value.requestsLoading ? '—' : count(n, gaps > 0),
    words: plural(n, 'риск', 'риска', 'рисков'), tone: n ? 'bad' : ''});
  return out;
});

const planFact = computed(() => monthPlanFact(s.value.report, Number(props.today.slice(5, 7)) - 1));
const pct = (fact, plan) => (plan ? Math.round((fact / plan) * 100) : null);
const pfLabel = (label, v) => (v.plan == null ? `${label}: план не задан` : v.plan === 0 ? `${label}: план равен нулю` : `${label}: выполнено ${pct(v.fact, v.plan)}% плана`);

function scrollTo(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({behavior: reduce ? 'auto' : 'smooth', block: 'start'});
  el.focus({preventScroll: true});
}
const stage = r => (r.approval_stage === 'director' ? 'Решение директора' : 'Финансовая проверка');
const signedLedger = t => (t.kind === 'out' ? -1 : t.kind === 'in' ? 1 : 0) * (toCents(t.amount) ?? 0);
</script>

<template>
 <div class="ov">
  <section class="ov-today" aria-label="Что требует внимания сегодня">
   <span class="ov-today-label">Сегодня</span>
   <template v-if="s.loading"><span class="ov-chip sk-chip" aria-hidden="true"></span><span class="ov-chip sk-chip" aria-hidden="true"></span></template>
   <template v-else>
    <button v-for="chip in chips" :key="chip.target" type="button" class="ov-chip" :class="chip.tone" @click="scrollTo(chip.target)">
     <b>{{chip.text}}</b> {{chip.words}}
    </button>
   </template>
  </section>

  <div class="ov-top">
   <BalanceCard class="ov-main" :state="balanceState" :error="s.errors.now" :company="company?.name || ''" :as-of="s.now?.day || today"
    :currency="currency" :available="available" :on-accounts="onAccounts" :reserved="reserved" :change="change"
    :history="s.history" :history-note="s.errors.history ? 'График не загрузился: ' + s.errors.history : s.errors.accounts ? 'Счета не загрузились: ' + s.errors.accounts : s.errors.archived ? 'Архивные счета не загрузились, история остатка недоступна: ' + s.errors.archived : s.historyNote"
    :can-open-report="has('export')" :can-add-account="has('write')" :can-open-day="has('ledger')"
    @retry="load" @open-report="emit('go', 'report')" @add-account="emit('go', 'accounts')" @open-day="d => emit('go', 'ledger', {from: d, to: d})"/>

   <div class="ov-kpis" role="group" aria-label="Показатели месяца">
    <template v-if="kpi && balanceState !== 'empty'">
     <div v-for="k in kpiCards" :key="k.label" class="ov-kpi">
      <span class="kpi-l">{{k.label}}</span>
      <span class="kpi-v" :class="k.tone" :style="{'--chars': k.value.length}">{{k.value}}</span>
      <span v-if="k.note" class="kpi-s">{{k.note}}</span>
     </div>
    </template>
    <template v-else-if="s.loading"><div v-for="n in 4" :key="n" class="ov-kpi" aria-hidden="true"><span class="sk sk-l"></span><span class="sk sk-v"></span><span class="sk sk-l"></span></div></template>
    <div v-else-if="balanceState === 'empty'" class="ov-kpi kpi-err"><span class="kpi-l">Показатели месяца</span><span class="kpi-s">Появятся вместе со счётом в {{currency}}.</span></div>
    <div v-else class="ov-kpi kpi-err"><span class="kpi-l">Показатели месяца не загрузились</span><span class="kpi-s">{{s.errors.dash || 'Нет данных'}}</span><button type="button" class="secondary tiny" @click="load">Повторить</button></div>
   </div>
  </div>

  <div class="ov-grid">
   <section v-if="approver" id="ov-decisions" class="panel span-7" tabindex="-1" aria-labelledby="ov-dec-h">
    <header class="panel-h"><h2 id="ov-dec-h">Ждут вашего решения</h2><button type="button" class="ghost tiny" @click="emit('go', 'requests', {status: 'pending'})">Все заявки <AppIcon name="arrow"/></button></header>
    <div v-if="s.loading || s.requestsLoading" class="panel-sk" aria-hidden="true"><span class="sk"></span><span class="sk"></span></div>
    <p v-else-if="s.errors.pending" class="panel-err">Заявки не загрузились: {{s.errors.pending}}</p>
    <ul v-else-if="decisions.length" class="rows">
     <li v-for="r in decisions.slice(0, SHOWN_ROWS)" :key="r.id">
      <button type="button" class="row" @click="emit('go', 'requests', {status: 'pending'})">
       <span class="row-main"><b>{{r.number}} · {{r.counterparty}}</b><small>{{r.purpose}}</small><small>{{stage(r)}} · до {{formatShortDate(r.date)}}<template v-if="r.priority !== 'normal'"> · <em class="prio">{{r.priority === 'urgent' ? 'срочно' : 'высокий приоритет'}}</em></template></small>
        <span v-if="track" class="trk"><template v-for="(st, i) in track(r)" :key="i"><span v-if="i" class="ln"></span><span class="st" :class="st.c === 'skip' ? '' : st.c"><u>{{marks[st.c]}}</u>{{st.l}}</span></template></span></span>
       <span class="row-amt">{{formatCents(toCents(r.amount), r.currency)}} <small>{{r.currency}}</small></span>
      </button>
     </li>
    </ul>
    <div v-else class="ov-empty"><span class="empty-ic" aria-hidden="true">✓</span><p>Решений от вас по {{currency}} сейчас не требуется.</p></div>
    <p v-if="decisions.length > SHOWN_ROWS" class="panel-note">Ещё {{decisions.length - SHOWN_ROWS}} — в разделе «Заявки».</p>
    <p v-if="s.pendingLoaded < s.pendingTotal" class="panel-note">Прочитаны {{s.pendingLoaded}} из {{s.pendingTotal}} заявок на согласовании; остальные — в разделе «Заявки».</p>
    <p v-if="s.dash?.pending_count" class="ov-pend-total">Всего на согласовании: <b>{{s.dash.pending_count}}</b> {{plural(s.dash.pending_count, 'заявка', 'заявки', 'заявок')}} на сумму <b>{{formatCents(toCents(s.dash.pending_amount), currency)}}</b> {{currency}}</p>
   </section>
   <section v-else class="panel span-7" aria-labelledby="ov-pend-h">
    <header class="panel-h"><h2 id="ov-pend-h">На согласовании</h2><button v-if="has('requests-view')" type="button" class="ghost tiny" @click="emit('go', 'requests', {status: 'pending'})">Все заявки <AppIcon name="arrow"/></button></header>
    <div v-if="s.loading" class="panel-sk" aria-hidden="true"><span class="sk"></span></div>
    <p v-else-if="s.dash?.pending_count" class="ov-pend"><b>{{s.dash.pending_count}}</b> {{plural(s.dash.pending_count, 'заявка', 'заявки', 'заявок')}} на сумму <b>{{formatCents(toCents(s.dash.pending_amount), currency)}}</b> {{currency}}</p>
    <p v-else-if="!s.dash && s.errors.dash" class="panel-err">Сводка не загрузилась: {{s.errors.dash}}</p>
    <div v-if="s.requestsLoading && !s.loading" class="panel-sk" aria-hidden="true"><span class="sk"></span><span class="sk"></span></div>
    <p v-else-if="s.errors.pending" class="panel-err">Заявки не загрузились: {{s.errors.pending}}</p>
    <ul v-else-if="pendingPreview.length" class="rows">
     <li v-for="r in pendingPreview" :key="r.id">
      <div class="row static">
       <span class="row-main"><b>{{r.number}} · {{r.counterparty}}</b><small>{{r.purpose}}</small><small>до {{formatShortDate(r.date)}}</small>
        <span v-if="track" class="trk"><template v-for="(st, i) in track(r)" :key="i"><span v-if="i" class="ln"></span><span class="st" :class="st.c === 'skip' ? '' : st.c"><u>{{marks[st.c]}}</u>{{st.l}}</span></template></span></span>
       <span class="row-amt">{{formatCents(toCents(r.amount), r.currency)}} <small>{{r.currency}}</small></span>
      </div>
     </li>
    </ul>
    <div v-else-if="!s.loading" class="ov-empty"><span class="empty-ic ok" aria-hidden="true">✓</span><p>Заявок на согласовании в {{currency}} нет.</p></div>
   </section>

   <section id="ov-risks" class="panel span-5" tabindex="-1" aria-labelledby="ov-risk-h">
    <header class="panel-h"><h2 id="ov-risk-h">Риски</h2></header>
    <div v-if="s.loading" class="panel-sk" aria-hidden="true"><span class="sk"></span><span class="sk"></span></div>
    <template v-else>
     <p v-if="riskGaps.length" class="panel-err">Не удалось проверить: {{riskGaps.join(', ')}}. Список рисков может быть неполным.</p>
     <ul v-if="risks.length" class="risks">
      <li v-for="(r, i) in risks" :key="i" :class="r.level">
       <button type="button" class="risk" @click="emit('go', ...r.go)"><b>{{r.title}}</b><small>{{r.text}}</small></button>
      </li>
     </ul>
     <p v-else-if="s.requestsLoading" class="panel-note">Проверяем утверждённые выплаты…</p>
     <div v-else-if="!riskGaps.length" class="ov-empty"><span class="empty-ic ok" aria-hidden="true">✓</span><p>Рисков по {{currency}} не видно: остаток выше резерва, бюджеты в пределах лимитов, просрочек нет.</p></div>
    </template>
    <p v-if="s.dash" class="ov-reserve">
     <span>Минимальный резерв: <b>{{formatCents(toCents(s.dash.reserve), currency)}}</b> {{currency}}</span>
     <button v-if="has('budget')" type="button" class="ghost tiny" @click="emit('edit-reserve', s.dash.reserve)">Изменить</button>
    </p>
   </section>

   <section v-if="balanceState === 'empty'" class="panel span-8" aria-labelledby="ov-start-h">
    <header class="panel-h"><h2 id="ov-start-h">С чего начать</h2></header>
    <p class="panel-note">Три шага, чтобы прогноз и отчёты заработали.</p>
    <ol class="ov-steps">
     <li><span class="n" aria-hidden="true">1</span><div><b>Добавьте счёт или кассу</b><small>Укажите валюту и подтверждённый остаток на начало учёта.</small><button v-if="has('write')" type="button" class="tiny" @click="emit('go', 'accounts')">К счетам</button></div></li>
     <li><span class="n" aria-hidden="true">2</span><div><b>Задайте минимальный резерв</b><small>Ниже этого остатка прогноз покажет предупреждение.</small></div></li>
     <li><span class="n" aria-hidden="true">3</span><div><b>Загрузите операции или создайте заявки</b><small>Банковскую выписку можно загрузить в разделе «Импорт».</small></div></li>
    </ol>
   </section>
   <ForecastCard v-else class="panel span-8" :state="forecastState" :error="s.errors.dash" :forecast="s.dash?.forecast || []" :days="s.dashDays ?? horizon"
    :reserve="toCents(s.dash?.reserve)" :currency="currency" :horizon="horizon" :has-events="!!s.dash?.forecast_has_events"
    :incoming7="toCents(s.dash?.incoming7)" :outgoing7="toCents(s.dash?.outgoing7)" @horizon="d => emit('update:days', d)" @retry="reloadForecast"/>

   <section class="panel span-4" aria-labelledby="ov-acc-h">
    <header class="panel-h"><h2 id="ov-acc-h">Где находятся деньги</h2><span class="pill fact">Факт</span></header>
    <p v-if="accountList.length" class="panel-note">{{accountList.length}} {{plural(accountList.length, 'счёт', 'счёта', 'счетов')}} в {{currency}} · <button type="button" class="linkish" @click="emit('go', 'accounts')">Банк и касса</button></p>
    <div v-if="s.loading" class="panel-sk" aria-hidden="true"><span class="sk"></span><span class="sk"></span></div>
    <p v-else-if="s.errors.accounts" class="panel-err">Счета не загрузились: {{s.errors.accounts}}</p>
    <ul v-else-if="accountList.length" class="ov-acc">
     <li v-for="a in accountList" :key="a.id">
      <span class="ov-acc-ic" aria-hidden="true"><AppIcon :name="a.kind === 'bank' ? 'accounts' : 'cash'"/></span>
      <span class="ov-acc-main"><b>{{a.name}}</b><small>{{a.kind === 'bank' ? 'Банковский счёт' : 'Касса'}}</small></span>
      <span class="ov-acc-amt">{{formatCents(toCents(a.balance), a.currency)}} <small>{{a.currency}}</small></span>
     </li>
    </ul>
    <div v-else class="ov-empty"><span class="empty-ic" aria-hidden="true"><AppIcon name="accounts"/></span><p>Нет счетов в {{currency}}.</p></div>
   </section>

   <section id="ov-payments" class="panel span-7" tabindex="-1" aria-labelledby="ov-pay-h">
    <header class="panel-h"><h2 id="ov-pay-h">Платежи</h2><button v-if="has('requests-view')" type="button" class="ghost tiny" @click="emit('go', 'requests', {status: 'approved'})">{{canPay ? 'К оплате' : 'Все утверждённые'}} <AppIcon name="arrow"/></button></header>
    <h3 class="ov-sub">Сегодня и просроченные</h3>
    <div v-if="s.loading || s.requestsLoading" class="panel-sk" aria-hidden="true"><span class="sk"></span><span class="sk"></span></div>
    <p v-else-if="s.errors.approved" class="panel-err">Платежи не загрузились: {{s.errors.approved}}</p>
    <div v-else-if="payments.length" class="tbl-wrap">
     <table class="tbl" role="table">
      <thead role="rowgroup"><tr role="row"><th role="columnheader" scope="col">Срок</th><th role="columnheader" scope="col">Получатель</th><th role="columnheader" scope="col">Счёт</th><th role="columnheader" scope="col" class="r">Сумма</th></tr></thead>
      <tbody role="rowgroup">
       <tr v-for="r in payments.slice(0, SHOWN_ROWS)" :key="r.id" role="row" :class="{late: r.date < today}">
        <td role="cell" class="nowrap">{{formatShortDate(r.date)}}<small v-if="r.date < today" class="late-l">просрочено</small></td>
        <td role="cell"><b>{{r.counterparty}}</b><small>{{r.number}} · {{r.purpose}}</small></td>
        <td role="cell" class="nowrap acc">{{r.account}}</td>
        <td role="cell" class="r nowrap num">{{formatCents(toCents(r.amount), r.currency)}} <small class="cur">{{r.currency}}</small></td>
       </tr>
      </tbody>
     </table>
    </div>
    <div v-else class="ov-empty"><span class="empty-ic ok" aria-hidden="true">✓</span><p>Утверждённых платежей в {{currency}} со сроком по сегодня нет.</p></div>
    <p v-if="payments.length > SHOWN_ROWS" class="panel-note">Ещё {{payments.length - SHOWN_ROWS}} — в разделе «Заявки».</p>
    <p v-if="s.approvedLoaded < s.approvedTotal" class="panel-note">Прочитаны {{s.approvedLoaded}} из {{s.approvedTotal}} утверждённых заявок; остальные — в разделе «Заявки».</p>
    <h3 class="ov-sub">Ближайшие выплаты · план</h3>
    <div v-if="s.loading || s.dashLoading" class="panel-sk" aria-hidden="true"><span class="sk"></span></div>
    <p v-else-if="s.errors.dash" class="panel-err">Прогноз выплат не загрузился: {{s.errors.dash}}</p>
    <ul v-else-if="upcoming.length" class="ov-upc">
     <li v-for="(p, i) in upcoming" :key="(p.id ?? 'x') + '-' + i">
      <span class="ov-upc-d">{{formatShortDate(p.date)}}</span>
      <span class="ov-upc-n">{{p.name}}<small>утверждено</small></span>
      <span class="ov-upc-a">{{formatCents(toCents(p.amount), currency)}} <small>{{currency}}</small></span>
     </li>
    </ul>
    <p v-else class="panel-note">Нет утверждённых выплат на ближайшие {{s.dashDays ?? horizon}} дней.</p>
   </section>

   <section class="panel span-5" aria-labelledby="ov-pf-h">
    <header class="panel-h">
     <h2 id="ov-pf-h">План и факт месяца</h2>
     <div class="seg" role="group" aria-label="Сценарий плана">
      <button v-for="[k, l] in [['A','А'],['B','Б'],['V','В']]" :key="k" type="button" :class="{on: scenario === k}" :aria-pressed="scenario === k" @click="scenario = k">{{l}}</button>
     </div>
    </header>
    <p v-if="s.errors.report" class="panel-err">План не загрузился: {{s.errors.report}}</p>
    <div v-else-if="!planFact" class="panel-sk" aria-hidden="true"><span class="sk"></span><span class="sk"></span></div>
    <div v-else class="ov-pf">
     <div v-for="[key, label, cls] in [['income','Поступления','in'],['expense','Выплаты','out']]" :key="key" class="ov-pf-row">
      <div class="ov-pf-top"><span>{{label}}</span><span class="ov-pf-num"><b>{{formatCents(planFact[key].fact, currency)}}</b> <template v-if="planFact[key].plan != null">из {{formatCents(planFact[key].plan, currency)}}</template><template v-else>· план не задан</template></span></div>
      <div class="bar" :class="cls" role="img" :aria-label="pfLabel(label, planFact[key])">
       <span :style="{width: planFact[key].plan ? Math.min(100, pct(planFact[key].fact, planFact[key].plan)) + '%' : '0%'}"></span>
      </div>
      <small v-if="planFact[key].plan === 0" class="ov-pf-pct">План равен нулю</small>
      <small v-else-if="planFact[key].plan != null" class="ov-pf-pct">{{pct(planFact[key].fact, planFact[key].plan)}}% плана</small>
     </div>
     <p class="panel-note">План задаётся по всем статьям; пока хотя бы одна статья без плана, итог плана не определён.</p>
    </div>
   </section>

   <section class="panel span-12" aria-labelledby="ov-ops-h">
    <header class="panel-h"><h2 id="ov-ops-h">Последние операции</h2><button v-if="has('ledger')" type="button" class="ghost tiny" @click="emit('go', 'ledger')">Все операции <AppIcon name="arrow"/></button></header>
    <div v-if="s.loading" class="panel-sk" aria-hidden="true"><span class="sk"></span><span class="sk"></span><span class="sk"></span></div>
    <p v-else-if="s.errors.latest" class="panel-err">Операции не загрузились: {{s.errors.latest}}</p>
    <div v-else-if="s.latest && s.latest.length" class="tbl-wrap">
     <table class="tbl" role="table">
      <thead role="rowgroup"><tr role="row"><th role="columnheader" scope="col">Дата</th><th role="columnheader" scope="col">Операция</th><th role="columnheader" scope="col" class="cat">Статья</th><th role="columnheader" scope="col">Счёт</th><th role="columnheader" scope="col" class="r">Сумма</th></tr></thead>
      <tbody role="rowgroup">
       <tr v-for="t in s.latest" :key="t.id" role="row">
        <td role="cell" class="nowrap">{{formatShortDate(t.date)}}</td>
        <td role="cell"><b>{{t.counterparty || (t.kind === 'transfer' ? 'Внутренний перевод' : '—')}}</b><small>{{t.reference}}<template v-if="t.reversal_of"> · сторно</template><template v-else-if="t.reversed"> · отменена сторно</template></small></td>
        <td role="cell" class="cat">{{t.category}}</td>
        <td role="cell" class="nowrap acc">{{t.account}}<template v-if="t.to_account"> → {{t.to_account}}</template></td>
        <td role="cell" class="r nowrap num" :class="t.kind === 'in' ? 'in' : t.kind === 'out' ? 'out' : ''">{{t.kind === 'transfer' ? formatCents(toCents(t.amount), t.currency) : formatCents(signedLedger(t), t.currency, {sign: true})}} <small class="cur">{{t.currency}}</small></td>
       </tr>
      </tbody>
     </table>
    </div>
    <div v-else class="ov-empty"><span class="empty-ic" aria-hidden="true">⇄</span><p>Операций в {{currency}} пока нет.</p></div>
   </section>
  </div>
 </div>
</template>

<style scoped>
.ov{display:flex;flex-direction:column;gap:18px;min-width:0}
.ov-today{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.ov-today-label{font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-right:4px}
.ov-chip{max-width:100%;white-space:normal;text-align:left;min-height:34px;padding:0 14px;border-radius:999px;background:var(--surface);border:1px solid var(--line);color:var(--text);font-weight:600;font-size:13px;gap:6px}
.ov-chip b{font-variant-numeric:tabular-nums}
.ov-chip.hot{background:var(--primary-soft);border-color:rgba(15,118,110,.25);color:#0B4F49}
.ov-chip.bad{background:var(--expense-soft);border-color:rgba(193,59,41,.22);color:var(--expense-ink)}
.ov-chip:hover:not(:disabled){border-color:var(--primary);color:var(--primary-hover);background:var(--surface)}
.sk-chip{width:180px;background:linear-gradient(90deg,#EFEDE7,#F7F6F2,#EFEDE7);background-size:200% 100%;animation:shimmer 1.3s linear infinite;border:0}

.ov-top{position:relative;display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:18px;align-items:stretch}
.ov-top::before{content:"";position:absolute;inset:-40px 20% -30px -40px;background:radial-gradient(closest-side,rgba(15,118,110,.10),transparent 70%);pointer-events:none;z-index:0}
.ov-main{grid-column:span 8;z-index:1}
.ov-kpis{grid-column:span 4;display:grid;grid-template-columns:minmax(0,1fr);grid-auto-rows:1fr;gap:12px;z-index:1}
.ov-kpi{container-type:inline-size;display:flex;flex-direction:column;justify-content:center;gap:4px;min-width:0;padding:14px 18px;border-radius:var(--radius-card);
 background:rgba(255,255,255,.9);border:1px solid rgba(23,43,42,.07);box-shadow:var(--shadow-1);
 transition:transform var(--dur-2) var(--ease),box-shadow var(--dur-2) var(--ease)}
@media (hover:hover) and (pointer:fine){.ov-kpi:hover{transform:translateY(-2px);box-shadow:var(--shadow-2)}}
.kpi-l{font-size:12.5px;font-weight:700;color:var(--muted)}
.kpi-v{font-size:clamp(14px,calc(150cqi / var(--chars, 12)),24px);font-weight:800;letter-spacing:-.015em;color:var(--text);font-variant-numeric:tabular-nums;white-space:nowrap;line-height:1.15}
.kpi-v.in{color:var(--income-ink)}.kpi-v.out{color:var(--expense-ink)}
.kpi-s{font-size:12px;font-weight:600;color:var(--muted)}
.kpi-err{grid-column:1/-1;align-items:flex-start;justify-content:flex-start;padding-top:22px}

.ov-grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:18px}
.span-4{grid-column:span 4}.span-5{grid-column:span 5}.span-7{grid-column:span 7}.span-8{grid-column:span 8}.span-12{grid-column:span 12}
.panel{display:flex;flex-direction:column;gap:10px;min-width:0;padding:18px 20px;border-radius:var(--radius-card);background:var(--surface);border:1px solid rgba(23,43,42,.07);box-shadow:var(--shadow-1);outline-offset:3px;scroll-margin-top:76px}
.panel-h{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
.panel-h h2{font-size:15px;font-weight:800;color:var(--text)}
.ov-sub{font-size:11.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-top:4px}
.ov-acc,.ov-upc,.ov-steps{list-style:none;margin:0;padding:0;display:flex;flex-direction:column}
.ov-acc li{display:flex;flex-wrap:wrap;align-items:center;gap:4px 12px;padding:10px 0;border-bottom:1px solid #EEF2F0;min-width:0}
.ov-acc li:last-child{border-bottom:0}
.ov-acc-ic{flex:none;width:36px;height:36px;border-radius:11px;display:grid;place-items:center;background:var(--primary-soft);color:var(--primary)}
.ov-acc-main{flex:1 1 12ch;min-width:12ch;display:flex;flex-direction:column}
.ov-acc-amt{margin-left:auto}
.ov-acc-main b{font-size:13.5px;color:var(--text);overflow-wrap:break-word}
.ov-acc-main small,.ov-steps small{font-size:12px;color:var(--muted);font-weight:600}
.ov-acc-amt,.ov-upc-a{font-weight:800;color:var(--text);font-variant-numeric:tabular-nums;white-space:nowrap;text-align:right}
.ov-acc-amt small,.ov-upc-a small{display:inline;font-size:11.5px;color:var(--muted);font-weight:700}
.ov-upc li{display:grid;grid-template-columns:64px minmax(0,1fr) auto;gap:12px;align-items:baseline;padding:8px 0;border-bottom:1px solid #EEF2F0;font-size:13px;font-weight:600;color:var(--text)}
.ov-upc li:last-child{border-bottom:0}
.ov-upc-d{color:var(--muted)}
.ov-upc-n{min-width:0;overflow-wrap:break-word}
.ov-upc-n small{display:block;font-size:11.5px;color:var(--muted);font-weight:600}
.ov-pend-total{margin-top:auto;padding-top:10px;border-top:1px solid #EEF2F0;font-size:13px;color:var(--muted);font-weight:600;font-variant-numeric:tabular-nums}
.ov-pend-total b{color:var(--text);font-weight:800}
.row.static{cursor:default}.row.static:hover{background:transparent}
.row-main .trk{margin-top:6px}
.linkish{min-height:0;padding:0;border:0;background:none;color:var(--primary);font-weight:700;text-decoration:underline;text-underline-offset:2px}
.linkish:hover:not(:disabled){background:none;color:var(--primary-hover)}
.ov-steps{gap:14px}
.ov-steps li{display:flex;gap:12px;align-items:flex-start}
.ov-steps .n{flex:none;width:28px;height:28px;border-radius:50%;display:grid;place-items:center;background:var(--primary-soft);color:var(--primary);font-weight:800}
.ov-steps b{display:block;color:var(--text);font-size:13.5px}
.ov-steps button{margin-top:8px}
.ov-reserve{margin:auto 0 0;padding-top:12px;border-top:1px solid #EEF2F0;display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:6px 10px;font-size:13px;color:var(--muted);font-weight:600}
.ov-reserve b{color:var(--text);font-weight:800;font-variant-numeric:tabular-nums}
.ov-pend{font-size:14px;color:var(--muted);font-weight:600;font-variant-numeric:tabular-nums}
.ov-pend b{color:var(--text);font-weight:800}.ov-pend b:first-child{font-size:24px;margin-right:2px}
.panel-note{font-size:12px;color:var(--muted);font-weight:600}
.panel-err{color:var(--expense-ink);font-weight:600;background:var(--expense-soft);padding:10px 12px;border-radius:10px}
.panel-sk{display:flex;flex-direction:column;gap:10px}
.sk{display:block;height:42px;border-radius:10px;background:linear-gradient(90deg,#EFEDE7 0%,#F7F6F2 50%,#EFEDE7 100%);background-size:200% 100%;animation:shimmer 1.3s linear infinite}
.sk-l{height:12px;width:60%}.sk-v{height:22px;width:85%}
@keyframes shimmer{from{background-position:200% 0}to{background-position:-200% 0}}

.rows,.risks{list-style:none;margin:0;padding:0;display:flex;flex-direction:column}
.row{width:100%;display:flex;justify-content:space-between;align-items:center;gap:12px;min-height:54px;padding:8px 10px;margin:0 -10px;width:calc(100% + 20px);
 border:0;border-radius:12px;background:transparent;color:var(--text);text-align:left;font-weight:600}
.rows li+li .row{border-top:1px solid #EEF2F0;border-radius:0}
.row:hover:not(:disabled){background:#F3F8F6;color:var(--text);border-radius:12px}
.row-main{display:flex;flex-direction:column;gap:2px;min-width:0}
.row-main b{font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row-main small,.tbl small{font-size:12px;color:var(--muted);font-weight:600;display:block;white-space:normal}
.prio{font-style:normal;color:var(--warning-ink);font-weight:700}
.row-amt{font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap;text-align:right}
.row-amt small{display:inline;font-size:11.5px;color:var(--muted);font-weight:700}

.risks{gap:8px}
.risk{width:100%;display:flex;flex-direction:column;align-items:flex-start;gap:2px;padding:10px 12px 10px 14px;border-radius:12px;border:0;text-align:left;font-weight:600;
 background:var(--surface-muted);color:var(--text);position:relative}
.risk::before{content:"";position:absolute;left:0;top:10px;bottom:10px;width:3px;border-radius:0 3px 3px 0;background:var(--muted)}
.bad .risk{background:var(--expense-soft)}.bad .risk::before{background:var(--expense)}
.warn .risk{background:var(--warning-soft)}.warn .risk::before{background:var(--warning)}
.risk b{font-size:13.5px;color:var(--text);white-space:normal}
.risk small{font-size:12px;color:var(--muted);font-weight:600;white-space:normal}
.risk:hover:not(:disabled){filter:brightness(.97);background:inherit;color:var(--text)}
.bad .risk:hover:not(:disabled){background:var(--expense-soft)}.warn .risk:hover:not(:disabled){background:var(--warning-soft)}

.ov-empty{display:flex;align-items:center;gap:12px;padding:10px 2px;color:var(--muted);font-weight:600}
.empty-ic{flex:none;width:40px;height:40px;border-radius:12px;display:grid;place-items:center;font-weight:800;color:var(--primary);
 background:linear-gradient(145deg,#FFFFFF,#E6F2EF);box-shadow:0 1px 0 #fff inset,0 6px 14px -8px rgba(15,118,110,.45),0 0 0 1px rgba(15,118,110,.10)}
.empty-ic.ok{color:var(--income-ink)}

.tbl-wrap{overflow-x:auto;margin:0 -4px}
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th{position:sticky;top:0;background:var(--surface);text-align:left;font-size:11.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);padding:8px 8px;border-bottom:1px solid var(--line)}
.tbl td{padding:9px 8px;border-bottom:1px solid #EEF2F0;vertical-align:top;color:var(--text);font-weight:600}
.tbl tbody tr{transition:background-color var(--dur-1) var(--ease)}
.tbl tbody tr:hover{background:#F7FAF9}
.tbl tr:last-child td{border-bottom:0}
.tbl .r{text-align:right}.nowrap{white-space:nowrap}
.tbl .num{font-variant-numeric:tabular-nums;font-weight:800}
.tbl .in{color:var(--income-ink)}.tbl .out{color:var(--expense-ink)}
.tbl small.cur{display:inline;font-size:11px}
.late td:first-child{color:var(--expense-ink)}
.late-l{color:var(--expense-ink)!important}

.ov-pf{display:flex;flex-direction:column;gap:14px}
.ov-pf-row{display:flex;flex-direction:column;gap:6px}
.ov-pf-top{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-weight:700;color:var(--text);font-size:13.5px}
.ov-pf-top .ov-pf-num{font-weight:600;color:var(--muted);font-variant-numeric:tabular-nums;text-align:right;white-space:normal}
.ov-pf-top .ov-pf-num b{color:var(--text);font-weight:800}
.bar{height:10px;border-radius:999px;background:#EDF1EF;overflow:hidden}
.bar span{display:block;height:100%;border-radius:inherit;transition:width 480ms var(--ease)}
.bar.in span{background:var(--income)}.bar.out span{background:var(--expense)}
.ov-pf-pct{font-size:12px;color:var(--muted);font-weight:700}

@media (max-width:1180px){
 .ov-main,.ov-kpis{grid-column:span 12}
 .ov-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
 .span-8,.span-4{grid-column:span 12}
}
@media (max-width:920px){
 .ov-kpis{grid-template-columns:1fr 1fr}
 .span-4,.span-5,.span-7,.span-8{grid-column:span 12}
}
@media (max-width:600px){
 .ov{gap:14px}.ov-top,.ov-grid{gap:14px}
 .panel{padding:16px 14px}
 .tbl-wrap{overflow:visible;margin:0}
 .tbl,.tbl tbody,.tbl tr,.tbl td{display:block}
 .tbl thead{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
 .tbl tr{display:grid;grid-template-columns:minmax(0,1fr) auto;column-gap:12px;row-gap:2px;padding:10px 0;border-bottom:1px solid #EEF2F0}
 .tbl tr:last-child{border-bottom:0}
 .tbl td{padding:0;border:0;white-space:normal}
 .tbl td:first-child{grid-column:1;font-size:12px;color:var(--muted)}
 .tbl td.r{grid-column:2;grid-row:1 / span 3;align-self:center;white-space:nowrap}
 .tbl td:not(:first-child):not(.r){grid-column:1}
 .tbl td.acc{font-size:12px;color:var(--muted)}
 .tbl .cat{display:none}
 .tbl .late-l{display:inline;margin-left:6px}
}
@media (max-width:480px){.ov-kpis{grid-template-columns:minmax(0,1fr);gap:10px}.ov-kpi{padding:12px 16px}}
@media (prefers-reduced-motion:reduce){
 .ov-kpi,.ov-kpi:hover,.bar span,.tbl tbody tr{transition:none;transform:none}
 .sk,.sk-chip{animation:none}
}
</style>
