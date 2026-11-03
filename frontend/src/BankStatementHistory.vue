<script setup>
import {ref, computed, watch, onUnmounted} from 'vue';
import {tx, N_} from './i18n/index.js';
import {toCents, formatCents, formatShortDate} from './overview/format.js';
import BankStatementExpenses from './BankStatementExpenses.vue';
import BankStatementIncome from './BankStatementIncome.vue';

const props = defineProps({api:{type:Function,required:true},companyId:{type:Number,required:true},
  canView:{type:Boolean,default:false},canViewCashFlow:{type:Boolean,default:false},refreshKey:{type:Number,default:0}});
const emit = defineEmits(['cash-flow']);
const open = ref(false), page = ref(1), hasMore = ref(false), items = ref([]), loaded = ref(false), loading = ref(false), error = ref('');
const detail = ref(null), detailLoading = ref(false), detailError = ref('');
let generation = 0, detailGeneration = 0;
const states = Object.freeze({active:N_('Проведена'),reversed:N_('Сторнирована'),changed:N_('Операция изменена'),missing:N_('Операция не найдена'),unposted:N_('Не проведена')});
const createdStatuses = new Set(['auto_paid','manual_paid','auto_income_posted','auto_fee_posted','auto_outgoing_posted']);
const linkedStatuses = new Set(['paid_match','manual_match','income_matched','fee_matched','other_matched','manual_transfer_match']);
const stateLabel = row => {
 if(row.operation_state !== 'active')return tx(states[row.operation_state] || N_('Состояние не определено'));
 if(row.status === 'duplicate')return tx('Повтор связан с операцией');
 if(linkedStatuses.has(row.status))return tx('Связана с существующей операцией');
 return tx(createdStatuses.has(row.status) ? N_('Проведена') : N_('Состояние не определено'));
};
const direction = value => tx(value === 'in' ? N_('Поступление') : value === 'out' ? N_('Списание') : N_('Не определено'));
const money = (value,currency) => formatCents(toCents(value),currency);
function invalidate(){generation++;detailGeneration++;items.value=[];detail.value=null;loaded.value=false;loading.value=false;detailLoading.value=false;error.value='';detailError.value='';hasMore.value=false;page.value=1;}
watch(() => [props.companyId,props.canView],()=>{invalidate();open.value=false;});
watch(() => props.refreshKey,()=>{if(open.value)loadPage(1);else invalidate();});
onUnmounted(invalidate);
async function loadPage(number){
 if(!open.value||!props.canView||!Number.isInteger(number)||number<1)return;
 const current=++generation;detailGeneration++;items.value=[];detail.value=null;loaded.value=false;detailError.value='';detailLoading.value=false;error.value='';loading.value=true;
 try{
  const result=await props.api(`/api/bank-statements/history?page=${number}&page_size=30`);
  if(current!==generation||!open.value||!props.canView)return;
  if(result.page!==number||!Array.isArray(result.items)||typeof result.has_more!=='boolean')throw new Error(tx('Не удалось проверить список выписок.'));
  page.value=number;hasMore.value=result.has_more;items.value=result.items;loaded.value=true;
 }catch(e){if(current===generation&&open.value)error.value=e.message||tx('Не удалось загрузить историю выписок.');}
 finally{if(current===generation)loading.value=false;}
}
async function toggle(){
 if(!props.canView)return;
 if(open.value){open.value=false;invalidate();return;}
 open.value=true;await loadPage(1);
}
async function openDetail(item){
 if(!props.canView||!open.value||!items.value.some(row=>row.id===item.id))return;
 const current=++detailGeneration,scope=generation;detail.value=null;detailError.value='';detailLoading.value=true;
 try{
  const result=await props.api(`/api/bank-statements/history/${encodeURIComponent(item.id)}`);
  if(current!==detailGeneration||scope!==generation||!open.value||!props.canView)return;
  if(result?.id!==item.id||result.status!=='committed'||!Array.isArray(result.rows))throw new Error(tx('Не удалось проверить выбранную выписку.'));
  detail.value=result;
 }catch(e){if(current===detailGeneration&&scope===generation&&open.value)detailError.value=e.message||tx('Не удалось открыть выписку.');}
 finally{if(current===detailGeneration)detailLoading.value=false;}
}
function openCashFlow(){
 if(!props.canViewCashFlow||!detail.value)return;
 const {currency}=detail.value.account||{}, {period_from,period_to}=detail.value.statement||{};
 if(!currency||!period_from||!period_to)return;
 emit('cash-flow',{currency,period_from,period_to});
}
</script>

<template>
 <section class="statement-history" v-if="canView" :aria-label="tx('История банковских выписок')">
  <h3>{{tx('История банковских выписок')}}</h3>
  <button type="button" class="secondary tiny" :aria-expanded="open" @click="toggle"><template v-if="open">{{tx('Скрыть историю выписок')}}</template><template v-else>{{tx('Показать историю выписок')}}</template></button>
  <template v-if="open">
   <p class="sub">{{tx('Здесь показаны подтверждённые выписки. Просмотр ничего не проводит и не изменяет.')}}</p>
   <p v-if="loading" role="status">{{tx('Загружаем данные…')}}</p><p v-if="error" class="error" role="alert">{{error}}</p>
   <p v-if="loaded && !items.length" class="sub">{{tx('Подтверждённых выписок нет.')}}</p>
   <div v-if="items.length" class="table-scroll"><table><thead><tr><th>{{tx('Выписка')}}</th><th>{{tx('Счёт')}}</th><th>{{tx('Период')}}</th><th>{{tx('Поступления по выписке')}}</th><th>{{tx('Списания по выписке')}}</th><th></th></tr></thead><tbody>
    <tr v-for="item in items" :key="item.id"><td>{{item.filename}}</td><td>{{item.account?.name}} · {{item.account?.currency}}</td><td>{{formatShortDate(item.period_from)}} – {{formatShortDate(item.period_to)}}</td><td>{{money(item.credit_total,item.account?.currency)}} {{item.account?.currency}}</td><td>{{money(item.debit_total,item.account?.currency)}} {{item.account?.currency}}</td><td><button type="button" class="secondary tiny" :disabled="detailLoading" @click="openDetail(item)">{{tx('Открыть выписку')}}</button></td></tr>
   </tbody></table></div>
   <div v-if="loaded && (page>1 || hasMore)" class="form-actions"><button type="button" class="secondary tiny" :disabled="loading || page<=1" @click="loadPage(page-1)">{{tx('Назад')}}</button><span>{{tx('Страница {n}',{n:page})}}</span><button type="button" class="secondary tiny" :disabled="loading || !hasMore" @click="loadPage(page+1)">{{tx('Далее')}}</button></div>
   <p v-if="detailLoading" role="status">{{tx('Загружаем данные…')}}</p><p v-if="detailError" class="error" role="alert">{{detailError}}</p>
   <div v-if="detail" class="statement-history-detail">
    <h4>{{detail.filename}} · {{detail.account?.name}} · {{detail.account?.currency}}</h4>
    <p>{{tx('Период')}}: {{formatShortDate(detail.statement.period_from)}} – {{formatShortDate(detail.statement.period_to)}}</p>
    <p class="sub">{{tx('Суммы ниже — из файла выписки. Состояние показывает текущую операцию на сайте.')}}</p>
    <BankStatementIncome :statement="detail.statement" :rows="detail.rows" :currency="detail.account?.currency"/>
    <BankStatementExpenses :statement="detail.statement" :rows="detail.rows" :currency="detail.account?.currency"/>
    <p v-if="canViewCashFlow" class="sub">{{tx('Cash Flow показывает месяцы с первого числа начального месяца до даты окончания выписки. Если выписка охватывает разные годы, предыдущий год откройте отдельно.')}}</p>
    <button v-if="canViewCashFlow" type="button" class="secondary tiny" @click="openCashFlow">{{tx('Открыть Cash Flow за период выписки')}}</button>
    <details class="statement-source"><summary>{{tx('Исходные строки и статусы проведения')}}</summary>
    <div class="table-scroll"><table><thead><tr><th>{{tx('Дата')}}</th><th>{{tx('Контрагент / документ')}}</th><th>{{tx('Назначение')}}</th><th>{{tx('Движение')}}</th><th>{{tx('Состояние операции')}}</th></tr></thead><tbody>
     <tr v-for="row in detail.rows" :key="row.id"><td>{{formatShortDate(row.date)}}</td><td>{{row.correspondent_name || '—'}}<small>{{row.correspondent_inn || '—'}} · {{row.document_number}}</small></td><td>{{row.purpose}}<small v-if="row.request_id">{{tx('Заявка №{id}',{id:row.request_id})}}</small></td><td>{{direction(row.direction)}}<small>{{money(row.amount,detail.account?.currency)}} {{detail.account?.currency}}</small></td><td>{{stateLabel(row)}}<small v-if="row.reason">{{tx(row.reason)}}</small></td></tr>
    </tbody></table></div>
    </details>
   </div>
  </template>
 </section>
</template>

<style scoped>
.statement-history{margin-top:20px}.statement-history-detail{margin-top:16px}.history-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.history-summary div{display:grid;gap:4px;padding:10px;background:#f5f6fb;border-radius:8px}.table-scroll td{max-width:340px;white-space:normal;overflow-wrap:anywhere}.table-scroll small{display:block}.form-actions{display:flex;gap:10px;align-items:center;margin-top:10px}
</style>
