<script setup>
import {computed, ref, watch, onUnmounted} from 'vue';
import ForecastCard from './ForecastCard.vue';
import {toCents, formatAmount, amountTitle} from './format.js';
import {tx} from '../i18n/index.js';

// Sales receives only the shared dashboard summary. The full treasury overview
// reads accounts, ledger, budgets and reports, which sales cannot open.
const props=defineProps({api:{type:Function,required:true},companyId:{type:Number,required:true},currency:{type:String,required:true},refresh:{type:Number,default:0},days:{type:Number,default:30}});
const emit=defineEmits(['go','update:days']);
const dashboard=ref(null),loading=ref(true),error=ref(''),loadedDays=ref(props.days);
let request=0,active=true;
async function load(){
 const mine=++request;loading.value=true;error.value='';dashboard.value=null;
 const company=props.companyId,days=props.days;
 try{
  const result=await props.api(`/api/dashboard?currency=${props.currency}&days=${days}`);
  if(!active||mine!==request||company!==props.companyId)return;
  dashboard.value=result;loadedDays.value=days;
 }catch(e){if(active&&mine===request&&e.name!=='AbortError')error.value=e.message}
 finally{if(active&&mine===request)loading.value=false}
}
watch(()=>[props.companyId,props.currency,props.refresh,props.days],load,{immediate:true});
onUnmounted(()=>{active=false;request++});
const tiles=computed(()=>[
 {label:tx('Доходы'),value:dashboard.value?.fact_in},
 {label:tx('Расходы'),value:dashboard.value?.fact_out},
 {label:tx('План поступлений на 7 дней'),value:dashboard.value?.incoming7},
]);
</script>

<template>
 <div class="sales-overview">
  <section class="card"><div class="card-h"><div><h3>{{tx('План поступлений')}}</h3><p class="sub">{{tx('Добавляйте ожидаемые поступления в календаре. Факт получения подтверждает бухгалтер.')}}</p></div><button @click="emit('go','calendar')">{{tx('Открыть календарь')}}</button></div></section>
  <div class="sumrow"><section v-for="tile in tiles" :key="tile.label" class="card tile"><div class="k">{{tile.label}}</div><div class="v num" :title="amountTitle(toCents(tile.value),currency)">{{loading?'…':formatAmount(toCents(tile.value),currency)}}</div><small>{{currency}}</small></section></div>
  <ForecastCard :state="loading?'loading':error?'error':'ready'" :error="error" :forecast="dashboard?.forecast||[]" :days="loadedDays" :reserve="toCents(dashboard?.reserve)" :currency="currency" :horizon="days" :has-events="!!dashboard?.forecast_has_events" :incoming7="toCents(dashboard?.incoming7)" :outgoing7="toCents(dashboard?.outgoing7)" @horizon="emit('update:days',$event)" @retry="load"/>
 </div>
</template>

<style scoped>
.sales-overview{display:grid;gap:18px}.sales-overview .card-h{flex-wrap:wrap;gap:14px}.sales-overview .card-h p{margin:8px 0 0}.sales-overview .sumrow{margin:0}
</style>
