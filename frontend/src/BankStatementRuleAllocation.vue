<script setup>
import {ref, watch, onUnmounted} from 'vue';
import {tx, N_} from './i18n/index.js';
import {toCents, formatCents, formatShortDate} from './overview/format.js';

const props=defineProps({api:{type:Function,required:true},companyId:{type:Number,required:true},batchId:{type:Number,required:true},canApply:{type:Boolean,default:false}});
const emit=defineEmits(['changed']);
const preview=ref(null), busy=ref(false), uncertain=ref(false), confirmed=ref(false), error=ref(''), notice=ref('');
let generation=0;
function invalidate(){generation++;preview.value=null;confirmed.value=false;busy.value=false;uncertain.value=false;error.value='';notice.value='';}
watch(()=>[props.companyId,props.batchId,props.canApply],invalidate);onUnmounted(invalidate);
const labels={ready:N_('Готово к распределению'),budget:N_('Остаётся в «Не распределено»: бюджет'),conflict:N_('Конфликт правил'),invalid:N_('Статья правила недоступна'),none:N_('Подходящее правило не найдено.')};
async function check(){
 if(!props.canApply||busy.value)return;
 const current=++generation;busy.value=true;preview.value=null;confirmed.value=false;error.value='';notice.value='';
 try{
  const result=await props.api(`/api/bank-statements/${props.batchId}/category-rules/preview`,{method:'POST',body:'{}'});
  if(current!==generation||!props.canApply)return;
  if(result.batch_id!==props.batchId||!Array.isArray(result.rows)||!Number.isInteger(result.ready_count)||typeof result.token!=='string')throw Error(tx('Не удалось проверить распределение.'));
  preview.value=result;uncertain.value=false;
 }catch(e){if(current===generation)error.value=e.message||tx('Не удалось проверить распределение.');}
 finally{if(current===generation)busy.value=false;}
}
async function apply(){
 if(!props.canApply||busy.value||uncertain.value||!preview.value?.token||preview.value.ready_count<1||!confirmed.value)return;
 const current=++generation;busy.value=true;error.value='';notice.value='';
 try{
  const result=await props.api(`/api/bank-statements/${props.batchId}/category-rules/commit`,{method:'POST',body:JSON.stringify({token:preview.value.token,confirm:true})});
  if(current!==generation||!props.canApply)return;
  if(result.batch_id!==props.batchId||!Number.isInteger(result.allocated)||result.allocated<1)throw Error(tx('Не удалось подтвердить результат распределения.'));
  notice.value=tx('Распределено операций: {count}. Денежные суммы сохранены.',{count:result.allocated});
  preview.value=null;confirmed.value=false;emit('changed');
 }catch(e){
  if(current!==generation)return;
  preview.value=null;confirmed.value=false;
  if(!Number.isInteger(e.status)||e.status>=500){uncertain.value=true;error.value=tx('Ответ о распределении не получен. Проверьте состояние перед повтором.');}
  else error.value=e.message||tx('Не удалось подтвердить результат распределения.');
 }finally{if(current===generation)busy.value=false;}
}
</script>

<template>
 <section v-if="canApply" class="rule-allocation" :aria-label="tx('Распределение сохранённой выписки')">
  <button type="button" class="secondary tiny" :disabled="busy" @click="check">{{tx(uncertain?N_('Проверить состояние распределения'):N_('Применить правила к нераспределённым'))}}</button>
  <p v-if="error" class="error" role="alert">{{tx(error)}}</p><p v-if="notice" role="status">{{notice}}</p>
  <template v-if="preview">
   <p>{{tx('Готово к распределению: {ready}. Останется без статьи: {remaining}.',{ready:preview.ready_count,remaining:preview.remaining_count})}}</p>
   <details v-if="preview.rows.length"><summary>{{tx('Посмотреть распределение')}}</summary><div class="table-scroll"><table><thead><tr><th>{{tx('Дата')}}</th><th>{{tx('Контрагент / документ')}}</th><th>{{tx('Сумма')}}</th><th>{{tx('Статья')}}</th><th>{{tx('Состояние')}}</th></tr></thead><tbody>
    <tr v-for="row in preview.rows" :key="row.ledger_id"><td>{{formatShortDate(row.date)}}</td><td>{{row.counterparty}}<small>{{row.document_number}}</small></td><td>{{formatCents(toCents(row.amount),preview.currency)}} {{preview.currency}}</td><td>{{row.category_name||tx('Не распределено')}}</td><td>{{tx(labels[row.status]||N_('Не распределено'))}}</td></tr>
   </tbody></table></div></details>
   <template v-if="preview.ready_count>0"><label class="confirm"><input v-model="confirmed" type="checkbox" :disabled="busy||uncertain"> {{tx('Подтверждаю статьи по правилам для уже учтённых операций.')}}</label>
   <button type="button" :disabled="busy||uncertain||!confirmed" @click="apply">{{tx('Подтвердить распределение')}}</button></template>
  </template>
 </section>
</template>

<style scoped>
.rule-allocation{margin:14px 0}.rule-allocation .confirm{display:block;margin:12px 0}.rule-allocation .table-scroll{margin-top:8px}.rule-allocation td{white-space:normal}.rule-allocation small{display:block}
</style>
