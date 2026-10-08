<script setup>
import {computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch} from 'vue';
import {formatCents, formatAmount, amountTitle, formatPercent, formatShortDate, formatLongDate} from './format.js';
import {scalePoints, smoothPath, tickIndexes} from './chart.js';

const props = defineProps({
  state: {type: String, default: 'loading'}, // loading | ready | empty | error
  error: {type: String, default: ''},
  company: {type: String, default: ''},
  asOf: {type: String, default: ''},
  currency: {type: String, required: true},
  available: {type: Number, default: null},
  onAccounts: {type: Number, default: null},
  reserved: {type: Number, default: null},
  change: {type: Object, default: null}, // {ratio, since}
  history: {type: Array, default: null}, // [{date, balance, income, expense}]
  historyNote: {type: String, default: ''},
  canOpenReport: Boolean,
  canAddAccount: Boolean,
  canOpenDay: Boolean,
});
const emit = defineEmits(['open-report', 'open-day', 'retry', 'add-account']);

// The headline is compact ("5,32 млрд"); every digit is in the tooltip.
const amountText = computed(() => formatAmount(props.available, props.currency));
const direction = computed(() => {
  const r = props.change?.ratio;
  return r == null || !Number.isFinite(r) ? 'flat' : r > 0 ? 'up' : r < 0 ? 'down' : 'flat';
});

// The plot is measured so the curve is drawn at its real size: no stretched
// strokes on narrow screens and no dependency on vector-effect.
const plot = ref(null);
const size = ref({width: 640, height: 150});
let observer;
function measure() {
  const el = plot.value;
  if (el && el.clientWidth) size.value = {width: el.clientWidth, height: el.clientHeight || 150};
}
onMounted(() => {
  if (typeof ResizeObserver === 'function') observer = new ResizeObserver(measure);
  watch(plot, el => { observer?.disconnect(); if (el) { observer?.observe(el); measure(); } }, {immediate: true});
});
onBeforeUnmount(() => observer?.disconnect());
const box = computed(() => ({...size.value, top: 18, bottom: 10, left: 6, right: 14}));
const points = computed(() => (props.history || []).length
  ? scalePoints(props.history.map(d => d.balance), box.value) : []);
const line = computed(() => smoothPath(points.value));
const area = computed(() => {
  const p = points.value;
  if (p.length < 2) return '';
  const floor = box.value.height - 1;
  return `${line.value} L${p.at(-1)[0]} ${floor} L${p[0][0]} ${floor} Z`;
});
const ticks = computed(() => tickIndexes((props.history || []).length).map(i => ({i, x: points.value[i]?.[0] ?? 0})));
// Re-create the drawn path when new data arrives so the entry animation runs once per load.
const drawKey = computed(() => (props.history || []).map(d => d.balance).join(','));

const active = ref(null);
const activeDay = computed(() => (active.value == null ? null : props.history?.[active.value]));
// The tooltip is measured and kept inside the plot: clamped sideways and
// shown below the point when there is no room above it.
const tip = ref(null);
const tipSize = ref({w: 200, h: 110});
watch(active, () => nextTick(() => { if (tip.value) tipSize.value = {w: tip.value.offsetWidth, h: tip.value.offsetHeight}; }));
const tipStyle = computed(() => {
  if (active.value == null) return {};
  const [x, y] = points.value[active.value];
  const half = Math.min(tipSize.value.w, box.value.width) / 2;
  const left = Math.min(Math.max(x, half), box.value.width - half);
  const below = y - tipSize.value.h - 14 < 0;
  return {left: `${left}px`, top: `${y}px`, transform: below ? 'translate(-50%, 14px)' : undefined};
});
const summary = computed(() => {
  const h = props.history || [];
  if (h.length < 2) return '';
  const min = h.reduce((a, d) => (d.balance < a.balance ? d : a), h[0]);
  return `Остаток за ${h.length} дней: с ${formatCents(h[0].balance, props.currency)} до ${formatCents(h.at(-1).balance, props.currency)} ${props.currency}, минимум ${formatCents(min.balance, props.currency)} ${formatShortDate(min.date)}.`;
});
// The plot is a slider over the days: screen readers read this text for the
// selected day (today until another one is chosen).
const current = computed(() => active.value ?? Math.max(0, (props.history || []).length - 1));
const valueText = computed(() => {
  const d = props.history?.[current.value];
  if (!d) return '';
  return `${formatLongDate(d.date)}: остаток ${formatCents(d.balance, props.currency)}, приход ${formatCents(d.income, props.currency)}, расход ${formatCents(d.expense, props.currency)} ${props.currency}`;
});

function nearest(event) {
  const rect = event.currentTarget.getBoundingClientRect();
  const x = event.clientX - rect.left;
  let best = 0;
  points.value.forEach((p, i) => { if (Math.abs(p[0] - x) < Math.abs(points.value[best][0] - x)) best = i; });
  return best;
}
const viaKeys = ref(false);
// Unique gradient ids: two cards on one page must not share url(#…).
const uid = useId();
function move(event) { if (points.value.length) { viaKeys.value = false; active.value = nearest(event); } }
function click(event) {
  if (!props.canOpenDay || !points.value.length) return;
  emit('open-day', props.history[nearest(event)].date);
}
function key(event) {
  const n = points.value.length;
  if (!n) return;
  if (event.key === 'Escape') { if (active.value != null) { event.preventDefault(); active.value = null; } return; }
  viaKeys.value = true;
  const home = {Home: 0, End: n - 1}[event.key];
  const step = {ArrowLeft: -1, ArrowRight: 1, ArrowDown: -1, ArrowUp: 1}[event.key];
  if (home !== undefined || step !== undefined) {
    event.preventDefault();
    // The first key press shows today; later presses move day by day.
    active.value = home ?? (active.value == null ? n - 1 : Math.min(n - 1, Math.max(0, active.value + step)));
    return;
  }
  if ((event.key === 'Enter' || event.key === ' ') && props.canOpenDay) {
    event.preventDefault();
    active.value = current.value;
    emit('open-day', props.history[current.value].date);
  }
}
</script>

<template>
 <article class="bal" :class="'is-' + state" aria-labelledby="bal-title">
  <header class="bal-head">
   <div class="bal-titles">
    <h2 id="bal-title">Доступный остаток</h2>
    <p class="bal-sub"><span class="bal-company">{{company}}</span><span v-if="asOf" class="bal-asof">на {{formatLongDate(asOf)}}</span></p>
   </div>
   <button v-if="canOpenReport" type="button" class="bal-icon" aria-label="Открыть отчёт Cash Flow" title="Отчёт Cash Flow" @click="emit('open-report')">
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M6 19v-5M12 19V9M18 19V5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>
   </button>
  </header>

  <div v-if="state === 'loading'" class="bal-skel" role="status" aria-label="Загружаем доступный остаток">
   <span class="sk sk-amount"></span><span class="sk sk-pill"></span><span class="sk sk-chart"></span>
  </div>

  <div v-else-if="state === 'error'" class="bal-state" role="alert">
   <p class="bal-state-title">Не удалось получить остаток</p>
   <p class="bal-state-text">{{error || 'Сервер не ответил. Данные на экране не изменены.'}}</p>
   <p v-if="onAccounts != null" class="bal-state-text">На счетах сейчас: <b :title="amountTitle(onAccounts, currency)">{{formatAmount(onAccounts, currency)}}</b> {{currency}}</p>
   <button type="button" class="secondary tiny" @click="emit('retry')">Повторить</button>
  </div>

  <div v-else-if="state === 'empty'" class="bal-state">
   <p class="bal-state-title">Нет счетов в {{currency}}</p>
   <p class="bal-state-text">Остаток появится, когда в компании будет счёт или касса в этой валюте.</p>
   <button v-if="canAddAccount" type="button" class="tiny" @click="emit('add-account')">Добавить счёт</button>
  </div>

  <template v-else>
   <p class="bal-amount" :style="{'--chars': amountText.length}" :title="amountTitle(available, currency)">
    <span class="bal-num">{{amountText}}</span><span class="bal-cur">{{currency}}</span>
   </p>
   <div class="bal-meta">
    <span v-if="change && change.ratio != null" class="bal-delta" :class="direction" :title="'По сравнению с ' + formatLongDate(change.since)">
     <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path v-if="direction === 'up'" d="M4 12 12 4M6 4h6v6"/><path v-else-if="direction === 'down'" d="M4 4l8 8M12 6v6H6"/><path v-else d="M3 8h10"/></svg>
     <span>{{formatPercent(change.ratio)}} за месяц</span>
    </span>
    <span v-else class="bal-delta flat">Нет данных для сравнения с прошлым месяцем</span>
   </div>
   <p class="bal-split">
    На счетах <b :title="amountTitle(onAccounts, currency)">{{formatAmount(onAccounts, currency)}}</b> · в резерве утверждённых заявок <b :title="amountTitle(reserved, currency)">{{formatAmount(reserved, currency)}}</b> {{currency}}
   </p>

   <figure v-if="points.length > 1" class="bal-chart">
    <figcaption class="sr">{{summary}}</figcaption>
    <div ref="plot" class="bal-plot" tabindex="0" role="slider" aria-roledescription="график"
         :aria-label="'График остатка на счетах. ' + summary + ' Стрелки выбирают день' + (canOpenDay ? ', Enter открывает операции дня' : '') + ', Escape скрывает подсказку.'"
         :aria-valuemin="0" :aria-valuemax="points.length - 1" :aria-valuenow="current" :aria-valuetext="valueText"
         :class="{clickable: canOpenDay}" @pointermove="move" @pointerleave="active = null" @click="click" @keydown="key" @blur="active = null">
     <svg :viewBox="`0 0 ${box.width} ${box.height}`" :width="box.width" :height="box.height" aria-hidden="true">
      <defs>
       <linearGradient :id="uid + '-fill'" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stop-color="var(--chart-line)" stop-opacity=".22"/>
        <stop offset="1" stop-color="var(--chart-line)" stop-opacity="0"/>
       </linearGradient>
       <!-- userSpaceOnUse: a flat line has a zero-height box and would lose a bounding-box gradient -->
       <linearGradient :id="uid + '-stroke'" gradientUnits="userSpaceOnUse" x1="0" y1="0" :x2="box.width" y2="0">
        <stop offset="0" stop-color="var(--chart-line)"/>
        <stop offset="1" stop-color="var(--primary)"/>
       </linearGradient>
      </defs>
      <line v-for="t in ticks" :key="'g' + t.i" :x1="t.x" :x2="t.x" :y1="box.top - 6" :y2="box.height - 1" class="grid"/>
      <line :x1="0" :x2="box.width" :y1="box.height - 1" :y2="box.height - 1" class="base"/>
      <g :key="drawKey" class="draw">
       <path :d="area" class="area" :fill="`url(#${uid}-fill)`"/>
       <path :d="line" class="line" :stroke="`url(#${uid}-stroke)`" pathLength="1"/>
      </g>
      <line v-if="activeDay" :x1="points[active][0]" :x2="points[active][0]" :y1="box.top - 6" :y2="box.height - 1" class="cursor"/>
     </svg>
     <span class="bal-dot end" :style="{left: points.at(-1)[0] + 'px', top: points.at(-1)[1] + 'px'}" aria-hidden="true"></span>
     <span v-if="activeDay" class="bal-dot" :style="{left: points[active][0] + 'px', top: points[active][1] + 'px'}" aria-hidden="true"></span>
     <div v-if="activeDay" ref="tip" class="bal-tip" :style="tipStyle" aria-hidden="true">
      <b>{{formatLongDate(activeDay.date)}}</b>
      <span>Остаток <em>{{formatCents(activeDay.balance, currency)}} {{currency}}</em></span>
      <span>Приход <em :class="{in: activeDay.income}">{{formatCents(activeDay.income, currency, {sign: true})}}</em></span>
      <span>Расход <em :class="{out: activeDay.expense}">{{activeDay.expense ? '−' + formatCents(activeDay.expense, currency) : formatCents(0, currency)}}</em></span>
      <small v-if="canOpenDay">{{viaKeys ? 'Enter — операции этого дня' : 'Нажмите, чтобы открыть операции дня'}}</small>
     </div>
    </div>
    <div class="bal-axis" aria-hidden="true"><span v-for="t in ticks" :key="'l' + t.i">{{formatShortDate(history[t.i].date)}}</span></div>
   </figure>
   <p v-else class="bal-note">История остатка появится после первых операций.</p>
   <p v-if="historyNote" class="bal-note">{{historyNote}}</p>
  </template>
 </article>
</template>

<style scoped>
.bal{
 container-type:inline-size;position:relative;display:flex;flex-direction:column;gap:14px;min-width:0;
 padding:26px 28px 20px;border-radius:var(--radius-hero);
 background:linear-gradient(155deg,var(--surface) 0%,var(--row-hover) 100%);
 -webkit-backdrop-filter:saturate(150%) blur(16px);backdrop-filter:saturate(150%) blur(16px);
 border:1px solid var(--line2);
 box-shadow:inset 0 1px 0 var(--surface),inset 0 -1px 0 rgba(11,16,36,.04),0 2px 0 rgba(11,16,36,.035),0 1px 2px rgba(11,16,36,.06),0 22px 46px -22px rgba(11,16,36,.30);
 transition:transform var(--dur-2) var(--ease),box-shadow var(--dur-2) var(--ease),border-color var(--dur-2) var(--ease);
}
@media (hover:hover) and (pointer:fine){
 .bal.is-ready:hover{transform:translateY(-3px);border-color:rgba(91,77,242,.28);
  box-shadow:inset 0 1px 0 var(--surface),inset 0 -1px 0 rgba(11,16,36,.04),0 2px 0 rgba(11,16,36,.035),0 2px 4px rgba(11,16,36,.07),0 30px 56px -24px rgba(11,16,36,.36)}
}
.bal-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.bal-titles{min-width:0}
h2{font-size:21px;font-weight:600;letter-spacing:-.012em;color:var(--text)}
.bal-sub{display:flex;flex-wrap:wrap;gap:4px 12px;margin-top:8px;font-size:12.5px;color:var(--muted);font-weight:600}
.bal-company{letter-spacing:.1em;text-transform:uppercase;font-weight:700;font-size:13px}
.bal-icon{flex:none;width:44px;height:44px;min-height:44px;padding:0;border-radius:50%;background:var(--primary-soft);border:0;color:var(--primary)}
.bal-icon:hover:not(:disabled){background:rgba(91,77,242,.18);color:var(--primary-hover)}
.bal-amount{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px;margin:4px 0 0;line-height:1.02;min-width:0}
.bal-num{font-size:clamp(18px,calc(112cqi / var(--chars)),60px);font-weight:800;letter-spacing:-.025em;color:var(--text);font-variant-numeric:tabular-nums;white-space:nowrap}
.bal-cur{font-size:clamp(15px,4.4cqi,30px);font-weight:600;color:var(--muted);letter-spacing:.01em}
.bal-meta{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center}
.bal-delta{display:inline-flex;align-items:center;gap:7px;padding:6px 13px;border-radius:999px;font-size:14px;font-weight:700;font-variant-numeric:tabular-nums}
.bal-delta svg{fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}
.bal-delta.up{background:var(--income-soft);color:var(--income-ink)}
.bal-delta.down{background:var(--expense-soft);color:var(--expense-ink)}
.bal-delta.flat{background:var(--surface-muted);color:var(--muted);font-weight:600;font-size:13px}
.bal-split{font-size:13px;color:var(--muted);font-weight:600;font-variant-numeric:tabular-nums}
.bal-split b{color:var(--text);font-weight:700}
.bal-chart{margin:4px 0 0;min-width:0}
.bal-plot{position:relative;height:150px;border-radius:12px;outline-offset:4px;touch-action:pan-y}
.bal-plot.clickable{cursor:pointer}
.bal-plot svg{position:absolute;inset:0;display:block;overflow:visible}
.grid{stroke:var(--line);stroke-dasharray:3 5;stroke-width:1}
.base{stroke:var(--line);stroke-width:1}
.cursor{stroke:var(--text);stroke-opacity:.28;stroke-width:1}
.line{fill:none;stroke-width:2.6;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1;stroke-dashoffset:0}
.draw .line{animation:draw 520ms var(--ease) both}
.draw .area{animation:fade 520ms var(--ease) both}
@keyframes draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}
@keyframes fade{from{opacity:0}to{opacity:1}}
.bal-dot{position:absolute;width:11px;height:11px;margin:-5.5px 0 0 -5.5px;border-radius:50%;background:var(--primary);border:2px solid var(--surface);pointer-events:none}
.bal-dot.end{box-shadow:0 0 0 8px rgba(91,77,242,.16)}
.bal-tip{position:absolute;transform:translate(-50%,calc(-100% - 14px));display:flex;flex-direction:column;gap:3px;min-width:min(190px,100%);width:max-content;max-width:min(300px,100%);padding:10px 12px;border-radius:12px;
 background:var(--surface);border:1px solid var(--line);box-shadow:0 12px 28px -14px rgba(11,16,36,.35);font-size:12.5px;color:var(--muted);font-weight:600;pointer-events:none;z-index:2}
.bal-tip b{color:var(--text);font-size:13px}
.bal-tip span{display:flex;justify-content:space-between;gap:14px}
.bal-tip em{white-space:nowrap;font-style:normal;color:var(--text);font-variant-numeric:tabular-nums;font-weight:700}
.bal-tip em.in{color:var(--income-ink)}.bal-tip em.out{color:var(--expense-ink)}
.bal-tip small{margin-top:3px;font-size:11.5px;color:var(--muted);font-weight:600}
.bal-axis{display:flex;justify-content:space-between;margin-top:8px;font-size:12px;font-weight:600;color:var(--muted);font-variant-numeric:tabular-nums}
.bal-note{font-size:13px;color:var(--muted);font-weight:600}
.bal-state{display:flex;flex-direction:column;align-items:flex-start;gap:8px;padding:18px 0 8px}
.bal-state-title{font-size:18px;font-weight:700;color:var(--text)}
.bal-state-text{color:var(--muted);font-weight:600;max-width:52ch}
.bal-skel{display:flex;flex-direction:column;gap:14px;padding-top:4px}
.sk{display:block;border-radius:10px;background:linear-gradient(90deg,var(--surface-muted) 0%,var(--bg) 50%,var(--surface-muted) 100%);background-size:200% 100%;animation:shimmer 1.3s linear infinite}
.sk-amount{height:52px;width:min(78%,520px)}.sk-pill{height:30px;width:160px;border-radius:999px}.sk-chart{height:150px}
@keyframes shimmer{from{background-position:200% 0}to{background-position:-200% 0}}
.sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
@container (max-width:440px){
 .bal-tip{min-width:170px}
 .bal-num{font-size:clamp(20px,calc(150cqi / var(--chars)),44px)}
}
@media (max-width:600px){.bal{padding:20px 18px 16px;border-radius:20px}.bal-plot{height:120px}}
@media (prefers-reduced-motion:reduce){
 .bal,.bal.is-ready:hover{transition:none;transform:none}
 .draw .line,.draw .area,.sk{animation:none}
}
</style>
