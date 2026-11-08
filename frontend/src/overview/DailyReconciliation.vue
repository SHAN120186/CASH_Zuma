<script setup>
import {ref,watch,onUnmounted} from 'vue';
import {tx} from '../i18n/index.js';
import {toCents,formatCents} from './format.js';
const props=defineProps({api:Function,companyId:Number,currency:String,today:String,refresh:Number});
const emit=defineEmits(['open-request']);
const opened=ref(false),day=ref(props.today),data=ref(null),loading=ref(false),error=ref('');
let generation=0;
const money=v=>v==null?'—':formatCents(toCents(v),props.currency);
async function load(){
 const current=++generation;data.value=null;error.value='';loading.value=false;
 if(!opened.value||!day.value||!props.companyId)return;
 loading.value=true;const selected=day.value,currency=props.currency;
 try{const result=await props.api(`/api/bank-statements/reconciliation-day?${new URLSearchParams({date:selected,currency})}`);
 if(current!==generation)return;
 if(result.date!==selected||result.currency!==currency)throw new Error(tx('Ошибка загрузки'));
 data.value=result;
 }catch(e){if(current===generation)error.value=e.message||tx('Ошибка загрузки');}
 finally{if(current===generation)loading.value=false;}
}
watch(()=>[opened.value,day.value,props.companyId,props.currency,props.refresh],load);
onUnmounted(()=>generation++);
</script>
<template>
 <section class="card reconciliation">
  <div class="card-h"><h2>{{tx('Ежедневная сверка')}}</h2><button class="secondary" @click="opened=!opened">{{opened?tx('Скрыть'):tx('Открыть')}}</button></div>
  <div v-if="opened" class="card-b">
   <div class="actions"><label>{{tx('Дата')}} <input v-model="day" type="date"></label><b>{{currency}}</b><button class="secondary" :disabled="loading" @click="load">{{tx('Обновить')}}</button></div>
   <p class="sub">{{tx('Сводка читает проведённые операции и не создаёт оплат. Исправления прошлых дней учитываются при обновлении.')}}</p>
   <p v-if="loading" role="status">{{tx('Загружаем данные…')}}</p><p v-else-if="error" role="alert" class="error">{{error}}</p>
   <template v-else-if="data">
    <div class="summary">
     <div>{{tx('Заявки к оплате за день')}}<b>{{data.requests.planned_count}} · {{money(data.requests.planned_amount)}}</b></div>
     <div>{{tx('Фактически оплачено за день')}}<b>{{data.requests.paid_count}} · {{money(data.requests.paid_amount)}}</b><small>{{tx('Из них с другой плановой датой')}}: {{data.requests.other_date_paid_count}}</small></div>
     <div>{{tx('Банковские комиссии')}}<b>{{money(data.bank_fees)}}</b></div>
     <div>{{tx('Удержания посредников')}}<b>{{money(data.partner_fees)}}</b></div>
     <div>{{tx('Проценты')}}<b>{{money(data.interest)}}</b></div>
    </div>
    <h3>{{tx('Банк и выписка')}}</h3><p class="sub">{{tx('Для проверки дневного остатка нужна выписка за один день. Прочерк означает, что подтверждённых данных нет.')}}</p>
    <div class="table-scroll"><table><thead><tr><th>{{tx('Счёт')}}</th><th>{{tx('Начало дня')}}</th><th>{{tx('Поступления')}}</th><th>{{tx('Списания')}}</th><th>{{tx('Остаток на сайте')}}</th><th>{{tx('Остаток по выписке')}}</th><th>{{tx('Разница')}}</th><th>{{tx('Статус')}}</th></tr></thead><tbody>
     <tr v-for="bank in data.banks" :key="bank.id"><td>{{bank.name}}</td><td>{{money(bank.opening)}}</td><td>{{money(bank.credit)}}</td><td>{{money(bank.debit)}}</td><td>{{money(bank.closing)}}</td><td>{{money(bank.bank_closing)}}</td><td>{{money(bank.difference)}}</td><td>{{bank.reconciled?tx('Сверено'):bank.available?tx('Требует проверки'):tx('Нет выписки')}}</td></tr>
    </tbody></table></div>
    <h3>{{tx('Касса')}}</h3><div class="summary"><div v-for="cash in data.cash" :key="cash.id">{{cash.name}}<b>{{money(cash.closing)}} {{currency}}</b><small>{{tx('Поступления')}}: {{money(cash.incoming)}} · {{tx('Списания')}}: {{money(cash.outgoing)}}</small></div></div>
    <details><summary>{{tx('Наличные в пути и выдача по заявкам')}} · {{data.fundings.length}}</summary><div class="table-scroll"><table><thead><tr><th>{{tx('Заявка')}}</th><th>{{tx('Контрагент')}}</th><th>{{tx('Деньги в пути')}}</th><th>{{tx('Получено в кассу')}}</th><th>{{tx('Выдано по заявкам')}}</th><th>{{tx('Осталось от пополнения')}}</th></tr></thead><tbody><tr v-for="item in data.fundings" :key="item.request_id"><td><button class="secondary tiny" @click="emit('open-request',{id:item.request_id})">{{item.request_id}}</button></td><td>{{item.counterparty}}</td><td>{{money(item.in_transit)}}</td><td>{{money(item.received?item.net:'0')}}</td><td>{{money(item.issued)}}</td><td>{{money(item.remaining)}}</td></tr></tbody></table></div></details>
    <details><summary>{{tx('Строки на проверке')}} · {{data.review.length}}</summary><p class="sub">{{tx('Проверьте спорные строки в сохранённой выписке. Сводка не подтверждает их автоматически.')}}</p><div v-for="line in data.review" :key="line.id" class="review-row"><b>{{line.counterparty}} · {{money(line.amount)}} {{currency}}</b><p>{{line.purpose}}</p><small>{{line.reason}}</small></div></details>
    <details><summary>{{tx('Банковские выписки')}} · {{data.statement_rows?.length||0}}</summary><div class="table-scroll"><table><thead><tr><th>{{tx('Контрагент')}}</th><th>{{tx('Сумма')}}</th><th>{{tx('Заявка')}}</th><th>{{tx('Статус')}}</th></tr></thead><tbody><tr v-for="line in data.statement_rows" :key="line.id"><td>{{line.counterparty}}<small>{{line.purpose}}</small></td><td>{{line.direction==='out'?'−':'+'}}{{money(line.amount)}}</td><td><button v-if="line.request_id" class="secondary tiny" @click="emit('open-request',{id:line.request_id})">{{line.request_id}}</button><span v-else>—</span></td><td>{{line.accounted?tx('Сверено'):tx('Требует проверки')}}</td></tr></tbody></table></div></details>
   </template>
  </div>
 </section>
</template>
<style scoped>
.reconciliation{margin-bottom:16px}.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:18px 0}.summary>div{padding:14px;border:1px solid #d8dcec;border-radius:10px}.summary b{display:block;margin:6px 0;font-size:18px}.table-scroll{overflow:auto}th,td{text-align:left;padding:10px;white-space:nowrap}table{width:100%;border-collapse:collapse}td{border-top:1px solid #e6e9f3}details{margin-top:20px}summary{cursor:pointer;font-weight:700}.review-row{padding:12px 0;border-bottom:1px solid #e6e9f3}
</style>
