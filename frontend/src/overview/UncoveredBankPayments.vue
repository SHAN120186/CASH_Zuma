<script setup>
import {computed, onUnmounted, ref, watch} from 'vue';
import {tx} from '../i18n/index.js';
import {formatCents, toCents} from './format.js';

const props = defineProps({api:Function, account:Object, today:String, refresh:Number});
const emit = defineEmits(['open-request']);
const opened = ref(false), from = ref(props.today), to = ref(props.today);
const rows = ref(null), loading = ref(false), error = ref(''), limit = ref(30);
const days = computed(() => (Date.parse(to.value) - Date.parse(from.value)) / 86400000 + 1);
const valid = computed(() => Number.isInteger(days.value) && days.value >= 1 && days.value <= 92);
const amount = value => formatCents(toCents(value), props.account.currency);
let epoch = 0;
function clear() {
  epoch++;
  rows.value = null;
  loading.value = false;
  limit.value = 30;
  error.value = '';
}
watch([opened, from, to, () => props.account.id, () => props.account.currency, () => props.refresh],clear,{flush:'sync'});
onUnmounted(clear);
async function load() {
  if (!opened.value || !valid.value || loading.value) return;
  clear();
  const current = epoch;
  loading.value = true;
  const params = new URLSearchParams({account_id:props.account.id,currency:props.account.currency,date_from:from.value,date_to:to.value});
  try {
    const result = await props.api('/api/bank-statements/uncovered-payments?' + params);
    if (epoch !== current) return;
    if (result.account?.id !== props.account.id || result.account?.currency !== props.account.currency ||
        result.date_from !== from.value || result.date_to !== to.value || !Array.isArray(result.rows)) throw Error(tx('Некорректный ответ сервера'));
    rows.value = result.rows;
  } catch (e) {
    if (epoch === current) error.value = e.message || tx('Ошибка загрузки');
  } finally {
    if (epoch === current) loading.value = false;
  }
}
</script>
<template>
 <section class="uncovered">
  <button type="button" class="secondary" :aria-expanded="opened" @click="opened=!opened">{{tx('Оплаты за дни без выписки')}}</button>
  <template v-if="opened">
   <p class="sub">{{tx('Здесь показаны оплаты, внесённые в Cash Zuma за дни без подтверждённой выписки на сайте.')}}</p>
   <div class="range">
    <label>{{tx('С даты')}}<input type="date" v-model="from"></label>
    <label>{{tx('По дату')}}<input type="date" v-model="to"></label>
    <button type="button" :disabled="!valid || loading" @click="load">{{tx('Показать оплаты')}}</button>
   </div>
   <p v-if="from && to && !valid" class="error" role="alert">{{tx('Выберите от 1 до 92 дней.')}}</p>
   <p v-else-if="loading" role="status">{{tx('Загружаем данные…')}}</p>
   <p v-else-if="error" class="error" role="alert">{{tx(error)}}</p>
   <template v-else-if="rows">
    <p v-if="!rows.length" class="sub">{{tx('За выбранные дни таких оплат нет.')}}</p>
    <template v-else>
     <p>{{tx('Оплат за дни без выписки: {count}',{count:rows.length})}}</p>
     <ul><li v-for="row in rows.slice(0,limit)" :key="row.request_id">
      <span><b>{{row.counterparty}}</b><small>{{row.date}}<template v-if="row.document_number"> · {{tx('Документ сайта №{number}',{number:row.document_number})}}</template></small></span>
      <strong>{{amount(row.amount)}} {{account.currency}}</strong>
      <button type="button" class="secondary tiny" @click="emit('open-request',{id:row.request_id})">{{'CF-'+String(row.request_id).padStart(5,'0')}}</button>
     </li></ul>
     <button v-if="rows.length>limit" type="button" class="secondary tiny" @click="limit+=30">{{tx('Показать ещё')}}</button>
    </template>
   </template>
  </template>
 </section>
</template>
<style scoped>
.uncovered{margin-top:18px;padding-top:16px;border-top:1px solid var(--line)}.range{display:flex;flex-wrap:wrap;align-items:end;gap:10px}.uncovered ul{list-style:none;margin:12px 0;padding:0}.uncovered li{display:grid;grid-template-columns:minmax(0,1fr) auto auto;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--line)}.uncovered li span{overflow-wrap:anywhere}.uncovered li small{display:block;color:var(--mut)}.uncovered li strong{font-variant-numeric:tabular-nums}
@media(max-width:600px){.range{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}.range input{width:100%;min-width:0}.range button{grid-column:1 / -1}.uncovered li{grid-template-columns:minmax(0,1fr) auto}.uncovered li button{justify-self:start}}
</style>
