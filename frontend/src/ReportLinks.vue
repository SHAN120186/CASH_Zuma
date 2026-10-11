<script setup>
import {ref,watch,onUnmounted,computed} from 'vue';
import {tx,N_,intlLocale} from './i18n/index.js';
import {statementScaledNumber} from './financialStatements.js';
import {normalizeReportSelection} from './reportContext.js';
import OperationProgress from './OperationProgress.vue';
import {createOperationProgress} from './operationProgress.js';
const props=defineProps({api:{type:Function,required:true},companyId:{type:Number,required:true},selection:{type:Object,required:true}});
const open=ref(false),result=ref(null),error=ref(''),loading=ref(false);
const progress=ref({active:false,completed:0,total:0,label:'',error:''});
const controller=createOperationProgress(value=>progress.value=value);
let requestId=0,active=true;
const selection=computed(()=>normalizeReportSelection(props.selection));
const scale=computed(()=>({x:1,k:1000,m:1000000}[selection.value.unit]));
const caption=computed(()=>result.value?.source?.unit_status==='unknown'?N_('Исходные единицы'):result.value?.source?.unit||result.value?.source?.currency||'');
const money=value=>statementScaledNumber({value,status:value==null?'unavailable':'calculated'},{kind:'amount'},scale.value,intlLocale.value);
const rawMoney=value=>statementScaledNumber({value,status:value==null?'unavailable':'calculated'},{kind:'amount'},1,intlLocale.value);
const equity=computed(()=>result.value?.bridges?.profit_to_equity);
const cash=computed(()=>result.value?.bridges?.cash_movement);
const provenance=bridge=>Object.values(bridge?.provenance||{}).flat();
const periodCaption=computed(()=>result.value?.period?result.value.period.start+' — '+result.value.period.end:'');
const pendingMonths=computed(()=>(result.value?.coverage||[]).filter(item=>!item.ready&&item.reason!=='month_not_closed').map(item=>item.period).join(', '));
const unclosedMonths=computed(()=>(result.value?.coverage||[]).filter(item=>!item.ready&&item.reason==='month_not_closed').map(item=>item.period).join(', '));
async function load(){
 const id=++requestId,companyId=props.companyId,choice={...selection.value};
 result.value=null;error.value='';
 if(!choice.year||choice.start>choice.end)return;
 loading.value=true;const ticket=controller.begin(N_('Загружаем сверку отчётов'),1);
 try{
  const query=new URLSearchParams({company_id:String(companyId),year:String(choice.year),start_month:String(choice.start),end_month:String(choice.end)});
  if(choice.sourceId)query.set('source_id',String(choice.sourceId));
  const value=await props.api('/api/financial-statements/reconciliation?'+query);
  if(!active||id!==requestId||props.companyId!==companyId)return;
  if(value.company_id!==companyId)throw Error(N_('Источник относится к другой компании. Обновите страницу.'));
  result.value=value;controller.complete(ticket,N_('Сверка отчётов загружена'));
 }catch(e){if(active&&id===requestId&&e.name!=='AbortError'){error.value=e.message;controller.fail(ticket,e);}}
 finally{if(active&&id===requestId)loading.value=false;}
}
watch(()=>[open.value,props.companyId,selection.value.year,selection.value.start,selection.value.end,selection.value.sourceId],()=>{requestId++;result.value=null;loading.value=false;controller.reset();if(open.value)load();});
onUnmounted(()=>{active=false;requestId++;controller.reset();});
</script>
<template>
 <section class="card report-links">
  <button class="secondary" :aria-expanded="open" :aria-controls="'report-links-'+companyId" @click="open=!open">{{tx('Связь P&L, баланса и Cash Flow')}}</button>
  <div v-if="open" :id="'report-links-'+companyId" class="links-body">
   <OperationProgress v-bind="progress"/>
   <p v-if="error" class="error" role="alert">{{tx(error)}}</p>
   <template v-if="result?.bridges&&(result.source_id!=null||result.status!=='missing')">
    <p class="muted">{{periodCaption}} · {{tx(caption)}} · {{tx(selection.unit==='m'?N_('Млн'):selection.unit==='k'?N_('Тыс.'):N_('Единицы'))}}</p>
    <div class="bridges">
     <section><h3>{{tx('Прибыль и капитал')}}</h3>
      <dl><dt>{{tx('Капитал на начало периода')}}</dt><dd>{{money(equity?.opening_equity)}}</dd>
       <dt>{{tx('Финансовый результат')}}</dt><dd>{{money(equity?.net_income)}}</dd>
       <dt>{{tx('Прочее изменение капитала')}}</dt><dd>{{money(equity?.other_equity_change)}}</dd>
       <dt>{{tx('Капитал на конец периода')}}</dt><dd>{{money(equity?.closing_equity)}}</dd></dl>
     </section>
     <section><h3>{{tx('Движение денег по Mapping')}}</h3>
      <dl><dt>{{tx('Деньги на начало периода')}}</dt><dd>{{money(cash?.opening_cash)}}</dd>
       <dt>{{tx('Чистое изменение денег')}}</dt><dd>{{money(cash?.net_change)}}</dd>
       <dt>{{tx('Деньги на конец периода')}}</dt><dd>{{money(cash?.closing_cash)}}</dd></dl>
     </section>
    </div>
    <p v-if="result.status!=='ready'" class="muted">{{tx('Сверка доступна после расчёта всех месяцев выбранного периода.')}}</p>
    <p v-if="pendingMonths" class="muted">{{tx('Ожидается ОСВ за месяцы: {periods}',{periods:pendingMonths})}}</p>
    <p v-if="unclosedMonths" class="muted">{{tx('Месяцы ещё не закрыты: {periods}',{periods:unclosedMonths})}}</p>
    <p class="muted">{{tx('Прибыль и движение денег различаются из-за начислений, оборотного капитала и других операций. Прочее изменение капитала требует расшифровки.')}}</p>
    <p class="muted">{{tx('Cash Flow на сайте берётся из проведённых денежных операций. Для сопоставления с Mapping нужно подтвердить масштаб, состав денежных счетов и дату. Движение по видам деятельности по ОСВ не определяется.')}}</p>
    <details><summary>{{tx('Основания сверки')}}</summary><ul><li v-for="field in result.required_fields" :key="field.key">{{tx(field.title)}}</li></ul>
     <p v-if="equity?.rounding_check?.status==='available'">{{tx('Разница округления результата: {value} исходных единиц',{value:rawMoney(equity.rounding_check.difference)})}}</p>
     <p v-for="bridge in result.bridges" :key="bridge.title">{{tx(bridge.title)}}: {{[...new Set(provenance(bridge).map(item=>item.source).filter(Boolean))].join(', ')}}</p>
     <template v-for="bridge in result.bridges" :key="bridge.title+'continuity'">
      <p v-for="difference in bridge.continuity?.differences||[]" :key="difference.period">{{tx('Разрыв между месяцами')}} · {{difference.period}}: {{money(difference.difference)}}</p>
     </template>
    </details>
   </template>
   <p v-else-if="!loading&&!error">{{tx('Для этой фирмы пока нет данных Mapping для сверки отчётов.')}}</p>
  </div>
 </section>
</template>
<style scoped>
.report-links{margin-top:16px;padding:16px}.links-body{margin-top:16px}.bridges{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:20px}.bridges h3{font-size:16px;line-height:1.4}.bridges dl{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;font-size:14px}.bridges dt{overflow-wrap:anywhere}.bridges dd{margin:0;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.report-links p{line-height:1.6}.report-links details{overflow-wrap:anywhere}
</style>
