<script setup>
import {ref, watch, onUnmounted} from 'vue';
import {tx} from '../i18n/index.js';
import {toCents, formatCents} from './format.js';

const props = defineProps({api:{type:Function,required:true},companyId:{type:Number,required:true},currency:{type:String,required:true},today:{type:String,required:true},refresh:{type:Number,default:0}});
const date = ref(props.today), data = ref(null), error = ref(''), loading = ref(false);
const currencies = ['UZS', 'USD', 'EUR'];
const currencyTotals = ref({});
let generation=0;
async function load() {
  const current=++generation, day=date.value, selected=props.currency;
  data.value=null;error.value='';loading.value=false;currencyTotals.value={};
  if (!day || !selected) return;
  loading.value=true;
  currencyTotals.value=Object.fromEntries(currencies.map(currency=>[currency,{loading:true}]));
  // Requests settle independently. A missing or inaccessible currency must not
  // hide the selected currency, nor be presented as a confirmed zero.
  await Promise.all(currencies.map(async currency=>{
    try {
      const params=new URLSearchParams({date:day,currency});
      const result=await props.api(`/api/bank-statements/daily?${params}`);
      if(current!==generation)return;
      if(result.date!==day || result.currency!==currency)throw new Error('mismatched daily statement');
      currencyTotals.value={...currencyTotals.value,[currency]:{data:result}};
      if(currency===selected)data.value=result;
    } catch(e) {
      if(current!==generation)return;
      currencyTotals.value={...currencyTotals.value,[currency]:{error:true}};
      if(currency===selected)error.value=e.message || 'error';
    } finally {
      if(current===generation && currency===selected)loading.value=false;
    }
  }));
}
watch(()=>[props.companyId,props.currency,date.value,props.refresh],load,{immediate:true});
onUnmounted(()=>{generation++;});
const money = value => formatCents(toCents(value),props.currency);
</script>

<template>
  <section class="card daily-statements" aria-labelledby="daily-statements-title">
    <div class="card-h"><h2 id="daily-statements-title">{{tx('Банковская выписка за день')}}</h2><label>{{tx('Дата')}} <input v-model="date" type="date"></label></div>
    <div class="card-b">
      <p class="sub">{{tx('Данные выписки показаны отдельно. Проведённые поступления, комиссии и списания учитываются в остатке и Cash Flow; строки на проверке пока не проведены.')}}</p>
      <div class="currency-summary" :aria-label="tx('Поступления по валютам за день')">
        <h3>{{tx('Поступления по валютам за день')}}</h3>
        <p class="sub">{{tx('Суммы по выпискам могут включать переводы и строки на проверке. В Cash Flow попадают только проведённые операции.')}}</p>
        <div class="daily-totals">
          <div v-for="cur in currencies" :key="cur">
            <span>{{cur}}</span>
            <b v-if="currencyTotals[cur]?.loading">{{tx('Загружаем данные…')}}</b>
            <b v-else-if="currencyTotals[cur]?.error">{{tx('Ошибка загрузки')}}</b>
            <b v-else-if="currencyTotals[cur]?.data?.available === false">{{tx('Нет выписки')}}</b>
            <template v-else-if="currencyTotals[cur]?.data">
              <b>{{formatCents(toCents(currencyTotals[cur].data.credit_total),cur)}} {{cur}}</b>
              <small v-if="currencyTotals[cur].data.uncertain" class="error">{{tx('Требует проверки')}}</small>
            </template>
          </div>
        </div>
      </div>
      <p v-if="loading" role="status">{{tx('Загружаем данные…')}}</p>
      <p v-else-if="error" class="error" role="alert">{{tx('Не удалось загрузить данные выписки.')}}</p>
      <p v-else-if="data && data.available === false" class="sub">{{tx('За выбранный день выписка не загружена.')}}</p>
      <template v-else-if="data">
        <p v-if="data.uncertain" class="error" role="alert">{{tx('Итог выписки требует проверки и может быть неполным: найдены похожие операции.')}}</p>
        <div class="daily-totals"><div><span>{{tx('Поступления по выписке')}}</span><b>{{money(data.credit_total)}} {{currency}}</b></div><div><span>{{tx('Списания по выписке')}}</span><b>{{money(data.debit_total)}} {{currency}}</b></div></div>
        <p class="sub">{{tx('В поступления могут входить переводы между своими счетами.')}}</p>
        <div class="table-scroll"><table><thead><tr><th>{{tx('Счёт')}}</th><th>{{tx('Поступления')}}</th><th>{{tx('Списания')}}</th></tr></thead><tbody><tr v-for="a in data.accounts" :key="a.account_id"><td>{{a.name}}</td><td>{{money(a.credit_total)}} {{currency}}</td><td>{{money(a.debit_total)}} {{currency}}</td></tr></tbody></table></div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.daily-statements{margin:16px 0}.card-h{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.daily-totals{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.daily-totals div{display:grid;gap:4px;padding:10px;background:#f5f6fb;border-radius:8px}.daily-totals b{font-variant-numeric:tabular-nums}
</style>
