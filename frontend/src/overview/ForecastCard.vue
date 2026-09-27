<script setup>
import {computed, nextTick, onBeforeUnmount, onMounted, ref, watch} from 'vue';
import {toCents, formatCents, formatCompact, formatShortDate, formatLongDate} from './format.js';
import {makeScale, smoothPath, tickIndexes} from './chart.js';

// Balance forecast from /api/dashboard, day by day, against the minimum
// reserve. Day 0 is today's planned end-of-day balance, as the server
// computes it. The horizon (7/30/90 days) is owned by the page.
const props = defineProps({
  state: {type: String, default: 'loading'}, // loading | ready | error
  error: {type: String, default: ''},
  forecast: {type: Array, default: () => []}, // [{date, balance, incoming, outgoing, risk, reserve_risk}]
  days: {type: Number, default: 30}, // horizon of the data on screen
  reserve: {type: Number, default: null},
  currency: {type: String, required: true},
  horizon: {type: Number, default: 30}, // selected horizon
  hasEvents: Boolean,
  incoming7: {type: Number, default: null},
  outgoing7: {type: Number, default: null},
});
const emit = defineEmits(['horizon', 'retry']);

const points0 = computed(() => props.forecast.map(d => ({...d, cents: toCents(d.balance)})).filter(d => d.cents != null));
const summary = computed(() => {
  const bad = points0.value.find(d => d.risk);
  if (bad) return `Проверьте ликвидность: ${formatLongDate(bad.date)}`;
  if (!props.hasEvents) return 'Будущие события пока не зарегистрированы';
  if (!points0.value.length) return '';
  const min = Math.min(...points0.value.map(d => d.cents));
  return `Минимум ${formatCents(min, props.currency)} ${props.currency}` + (props.reserve ? ' — выше резерва' : '');
});

const plot = ref(null);
const size = ref({width: 640, height: 180});
let observer;
function measure() {
  const el = plot.value;
  if (el && el.clientWidth) size.value = {width: el.clientWidth, height: el.clientHeight || 180};
}
onMounted(() => {
  if (typeof ResizeObserver === 'function') observer = new ResizeObserver(measure);
  watch(plot, el => { observer?.disconnect(); if (el) { observer?.observe(el); measure(); } }, {immediate: true});
});
onBeforeUnmount(() => observer?.disconnect());

// Left gutter for the value scale, as on the previous forecast chart.
const box = computed(() => ({...size.value, top: 14, bottom: 10, left: 58, right: 12}));
const values = computed(() => points0.value.map(d => d.cents));
const scale = computed(() => (values.value.length ? makeScale(values.value, box.value, props.reserve ? [props.reserve] : []) : null));
const points = computed(() => (scale.value ? values.value.map((v, i) => [scale.value.x(i), scale.value.y(v)]) : []));
const line = computed(() => smoothPath(points.value));
const reserveY = computed(() => (scale.value && props.reserve ? scale.value.y(props.reserve) : null));
const ticks = computed(() => tickIndexes(points0.value.length).map(i => ({i, x: points.value[i]?.[0] ?? 0})));
// Four levels between the lowest and highest value (reserve included).
const levels = computed(() => {
  if (!scale.value) return [];
  const all = props.reserve ? [...values.value, props.reserve] : values.value;
  const lo = Math.min(...all), hi = Math.max(...all);
  if (lo === hi) return [{v: lo, y: scale.value.y(lo)}];
  return [0, 1, 2, 3].map(k => lo + ((hi - lo) * k) / 3).map(v => ({v, y: scale.value.y(v)}));
});

const active = ref(null);
const activeDay = computed(() => (active.value == null ? null : points0.value[active.value]));
const current = computed(() => active.value ?? 0);
function nearest(event) {
  const x = event.clientX - event.currentTarget.getBoundingClientRect().left;
  let best = 0;
  points.value.forEach((p, i) => { if (Math.abs(p[0] - x) < Math.abs(points.value[best][0] - x)) best = i; });
  return best;
}
function move(event) { if (points.value.length) active.value = nearest(event); }
function key(event) {
  const n = points.value.length;
  if (!n) return;
  if (event.key === 'Escape') { if (active.value != null) { event.preventDefault(); active.value = null; } return; }
  const home = {Home: 0, End: n - 1}[event.key];
  const step = {ArrowLeft: -1, ArrowRight: 1, ArrowDown: -1, ArrowUp: 1}[event.key];
  if (home === undefined && step === undefined) return;
  event.preventDefault();
  // The first key press shows today; later presses move day by day.
  active.value = home ?? (active.value == null ? 0 : Math.min(n - 1, Math.max(0, active.value + step)));
}
const dayLabel = i => (i === 0 ? 'Сегодня, на конец дня' : 'План (прогноз)');
const valueText = computed(() => {
  const d = points0.value[current.value];
  if (!d) return '';
  return `${formatLongDate(d.date)}, ${dayLabel(current.value).toLowerCase()}: остаток ${formatCents(d.cents, props.currency)} ${props.currency}`
    + (d.reserve_risk ? ', ниже минимального резерва' : d.risk ? ', есть счёт в минусе' : '');
});

const tip = ref(null), tipSize = ref({w: 200, h: 90});
watch(active, () => nextTick(() => { if (tip.value) tipSize.value = {w: tip.value.offsetWidth, h: tip.value.offsetHeight}; }));
// Kept inside the plot: clamped sideways, shown below the point near the top.
const tipStyle = computed(() => {
  if (active.value == null) return {};
  const [x, y] = points.value[active.value];
  const half = Math.min(tipSize.value.w, box.value.width) / 2;
  const below = y - tipSize.value.h - 14 < 0;
  return {left: `${Math.min(Math.max(x, half), box.value.width - half)}px`, top: `${y}px`, transform: below ? 'translate(-50%, 14px)' : undefined};
});
</script>

<template>
 <section class="fc" aria-labelledby="fc-h">
  <header class="fc-head">
   <div class="fc-titles">
    <h2 id="fc-h">Прогноз остатка</h2>
    <p v-if="state === 'ready' && summary" class="fc-sub">{{summary}}</p>
   </div>
   <div class="seg" role="group" aria-label="Горизонт прогноза">
    <button v-for="d in [7, 30, 90]" :key="d" type="button" :class="{on: horizon === d}" :aria-pressed="horizon === d" @click="emit('horizon', d)">{{d}} дн.</button>
   </div>
  </header>

  <div v-if="state === 'loading'" class="fc-sk" aria-hidden="true"></div>
  <div v-else-if="state === 'error'" class="fc-err"><p>Прогноз на {{horizon}} дней не загрузился: {{error}}</p><button type="button" class="secondary tiny" @click="emit('retry')">Повторить</button></div>
  <template v-else-if="points.length">
   <div ref="plot" class="fc-plot" tabindex="0" role="slider" aria-roledescription="график"
        :aria-label="`Прогноз остатка на ${days} дней. ${summary}. Стрелки выбирают день, Escape скрывает подсказку.`"
        :aria-valuemin="0" :aria-valuemax="points.length - 1" :aria-valuenow="current" :aria-valuetext="valueText"
        @pointermove="move" @pointerleave="active = null" @keydown="key" @blur="active = null">
    <svg :viewBox="`0 0 ${box.width} ${box.height}`" :width="box.width" :height="box.height" aria-hidden="true">
     <g v-for="l in levels" :key="'y' + l.v">
      <line :x1="box.left" :x2="box.width" :y1="l.y" :y2="l.y" class="level"/>
      <text :x="box.left - 8" :y="l.y + 4" text-anchor="end" class="level-l">{{formatCompact(l.v)}}</text>
     </g>
     <line v-for="t in ticks" :key="'g' + t.i" :x1="t.x" :x2="t.x" :y1="box.top - 6" :y2="box.height - 1" class="grid"/>
     <line :x1="box.left" :x2="box.width" :y1="box.height - 1" :y2="box.height - 1" class="base"/>
     <line v-if="reserveY != null" :x1="box.left" :x2="box.width" :y1="reserveY" :y2="reserveY" class="reserve"/>
     <path :d="line" class="plan"/>
     <line v-if="activeDay" :x1="points[active][0]" :x2="points[active][0]" :y1="box.top - 6" :y2="box.height - 1" class="cursor"/>
    </svg>
    <span class="fc-dot today" :style="{left: points[0][0] + 'px', top: points[0][1] + 'px'}" aria-hidden="true"></span>
    <span v-if="activeDay && active > 0" class="fc-dot" :style="{left: points[active][0] + 'px', top: points[active][1] + 'px'}" aria-hidden="true"></span>
    <div v-if="activeDay" ref="tip" class="fc-tip" :style="tipStyle" aria-hidden="true">
     <b>{{formatLongDate(activeDay.date)}}</b>
     <small>{{dayLabel(active)}}</small>
     <span>Остаток <em>{{formatCents(activeDay.cents, currency)}} {{currency}}</em></span>
     <span v-if="toCents(activeDay.incoming)">Поступления <em class="in">+{{formatCents(toCents(activeDay.incoming), currency)}}</em></span>
     <span v-if="toCents(activeDay.outgoing)">Выплаты <em class="out">−{{formatCents(toCents(activeDay.outgoing), currency)}}</em></span>
     <small v-if="activeDay.reserve_risk" class="warn">Ниже минимального резерва</small>
     <small v-else-if="activeDay.risk" class="warn">Есть счёт в минусе</small>
    </div>
   </div>
   <div class="fc-axis" aria-hidden="true"><span v-for="t in ticks" :key="'l' + t.i">{{formatShortDate(points0[t.i].date)}}</span></div>
   <ul class="fc-legend" aria-hidden="true">
    <li><i class="k-today"></i>Сегодня, на конец дня</li>
    <li><i class="k-plan"></i>План / прогноз</li>
    <li v-if="reserveY != null"><i class="k-res"></i>Минимальный резерв {{formatCents(reserve, currency)}} {{currency}}</li>
   </ul>
  </template>
  <p v-else class="fc-note">Прогноз появится после первых операций и заявок.</p>

  <p v-if="state === 'ready'" class="fc-week">
   Ближайшие 7 дней · план: поступления <b :class="{in: incoming7}">{{formatCents(incoming7, currency, {sign: true})}}</b> · выплаты <b :class="{out: outgoing7}">{{outgoing7 == null ? '—' : formatCents(-outgoing7, currency)}}</b> {{currency}}
  </p>
 </section>
</template>

<style scoped>
.fc{display:flex;flex-direction:column;gap:12px;min-width:0}
.fc-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap}
.fc-titles{min-width:0}
h2{font-size:15px;font-weight:800;color:var(--text)}
.fc-sub{margin-top:3px;font-size:12.5px;font-weight:600;color:var(--muted)}
.fc-sk{height:200px;border-radius:12px;background:linear-gradient(90deg,#EFEDE7 0%,#F7F6F2 50%,#EFEDE7 100%);background-size:200% 100%;animation:shimmer 1.3s linear infinite}
@keyframes shimmer{from{background-position:200% 0}to{background-position:-200% 0}}
.fc-err{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;color:var(--expense-ink);font-weight:600;background:var(--expense-soft);padding:10px 12px;border-radius:10px}
.fc-plot{position:relative;height:180px;border-radius:12px;outline-offset:4px;touch-action:pan-y}
.fc-plot svg{position:absolute;inset:0;display:block;overflow:visible}
.level{stroke:rgba(23,43,42,.08);stroke-width:1}
.level-l{font-size:11px;font-weight:700;fill:var(--muted);font-variant-numeric:tabular-nums}
.grid{stroke:rgba(23,43,42,.13);stroke-dasharray:3 5;stroke-width:1}
.base{stroke:rgba(23,43,42,.10);stroke-width:1}
.reserve{stroke:var(--expense);stroke-width:1.6;stroke-dasharray:2 5;stroke-linecap:round}
.plan{fill:none;stroke:var(--plan);stroke-width:2.4;stroke-dasharray:7 6;stroke-linecap:round;stroke-linejoin:round}
.cursor{stroke:var(--text);stroke-opacity:.28;stroke-width:1}
.fc-dot{position:absolute;width:11px;height:11px;margin:-5.5px 0 0 -5.5px;border-radius:50%;background:var(--plan);border:2px solid #fff;pointer-events:none}
.fc-dot.today{background:var(--primary);box-shadow:0 0 0 6px rgba(15,118,110,.14)}
.fc-tip{position:absolute;transform:translate(-50%,calc(-100% - 14px));display:flex;flex-direction:column;gap:3px;min-width:min(190px,100%);width:max-content;max-width:min(300px,100%);padding:10px 12px;border-radius:12px;
 background:#fff;border:1px solid var(--line);box-shadow:0 12px 28px -14px rgba(16,45,43,.35);font-size:12.5px;color:var(--muted);font-weight:600;pointer-events:none;z-index:2}
.fc-tip b{color:var(--text);font-size:13px}
.fc-tip span{display:flex;justify-content:space-between;gap:14px}
.fc-tip em{white-space:nowrap;font-style:normal;color:var(--text);font-variant-numeric:tabular-nums;font-weight:700}
.fc-tip em.in{color:var(--income-ink)}.fc-tip em.out{color:var(--expense-ink)}
.fc-tip small{font-size:11.5px}.fc-tip small.warn{color:var(--expense-ink);font-weight:700}
.fc-axis{display:flex;justify-content:space-between;padding-left:52px;font-size:12px;font-weight:600;color:var(--muted);font-variant-numeric:tabular-nums}
.fc-legend{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12px;font-weight:600;color:var(--muted)}
.fc-legend i{display:inline-block;vertical-align:middle;margin-right:6px}
.k-today{width:9px;height:9px;border-radius:50%;background:var(--primary)}
.k-plan{width:20px;border-top:2.4px dashed var(--plan)}
.k-res{width:20px;border-top:2px dotted var(--expense)}
.fc-week{margin-top:auto;padding-top:10px;border-top:1px solid #EEF2F0;font-size:13px;font-weight:600;color:var(--muted);font-variant-numeric:tabular-nums}
.fc-week b{font-weight:800}.fc-week .in{color:var(--income-ink)}.fc-week .out{color:var(--expense-ink)}
.fc-note{color:var(--muted);font-weight:600}
@media (max-width:600px){.fc-plot{height:150px}}
@media (prefers-reduced-motion:reduce){.fc-sk{animation:none}}
</style>
