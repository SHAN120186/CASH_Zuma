<script setup>
import {computed} from 'vue';
import {tx,N_} from './i18n/index.js';
import {toCents,formatCents} from './overview/format.js';

const props=defineProps({summary:{type:Object,default:null},currency:{type:String,default:''},result:{type:Boolean,default:false},fullReconciliation:{type:Boolean,default:false}});
const valid=computed(()=>props.summary&&['ready','posted','pending','linked'].every(key=>{
 const group=props.summary[key];return group&&Number.isInteger(group.count)&&group.count>=0&&typeof group.credit_total==='string'&&typeof group.debit_total==='string';
}));
const groups=computed(()=>props.result?[
 {key:'linked',label:N_('Всего учтено по выписке')},
 {key:'posted',label:props.summary?.scope==='history'?N_('Из них создано этой выпиской'):N_('Из них новые операции за это подтверждение')},
 {key:'pending',label:N_('Остаётся на проверке')},
]:[
 {key:'ready',label:N_('Проверенные строки к проведению')},
 {key:'linked',label:N_('Уже учтено — новая операция не нужна')},
 {key:'pending',label:N_('Остаётся на проверке')},
]);
const money=value=>formatCents(toCents(value),props.currency);
const pendingDebit=computed(()=>toCents(props.summary?.pending?.debit_total));
</script>

<template>
 <section v-if="valid" class="posting-summary" :aria-label="tx(result?N_('Результат проведения проверенных строк'):N_('Проверенные строки и строки на проверке'))">
  <h3>{{tx(result?N_('Результат проведения проверенных строк'):N_('Проверенные строки и строки на проверке'))}}</h3>
  <p class="sub">{{tx(result?N_('Новые операции входят в учтённый итог. Эти строки не складываются между собой и повторно в факт не добавляются.'):N_('Это результат проверки перед подтверждением. Сервер повторно проверит права, остатки и отсутствие повторов при проведении.'))}}</p>
  <div class="table-scroll"><table><thead><tr><th>{{tx('Состояние')}}</th><th>{{tx('Строк')}}</th><th>{{tx('Поступления')}}</th><th>{{tx('Списания')}}</th></tr></thead><tbody>
   <tr v-for="group in groups" :key="group.key" :data-posting-state="group.key"><th>{{tx(group.label)}}</th><td>{{summary[group.key].count}}</td><td>{{money(summary[group.key].credit_total)}} {{currency}}</td><td>{{money(summary[group.key].debit_total)}} {{currency}}</td></tr>
  </tbody></table></div>
  <dl class="posting-balances">
   <div><dt>{{tx('Остаток по выписке')}}</dt><dd>{{money(summary.bank_closing)}} {{currency}}</dd></div>
   <div><dt>{{tx('Учтённый остаток на сайте')}}</dt><dd>{{money(summary.ledger_closing)}} {{currency}}</dd></div>
   <div v-if="!result"><dt>{{tx('Остаток после проверенных строк')}}</dt><dd>{{money(summary.projected_closing)}} {{currency}}</dd></div>
   <div><dt>{{tx('Остаток за вычетом спорных списаний')}}</dt><dd>{{money(summary.conservative_closing)}} {{currency}}</dd></div>
  </dl>
  <p v-if="pendingDebit!==null && pendingDebit>0" class="posting-hold">{{tx(result?N_('Из доступных средств удерживается {amount} {currency} спорных списаний. Поступления на проверке не дают денег для оплаты.'):N_('После проведения из доступных средств будет удерживаться {amount} {currency} спорных списаний. Поступления на проверке не дают денег для оплаты.'),{amount:money(summary.pending.debit_total),currency})}}</p>
  <p v-if="summary.blocking_reason" class="error" role="alert">{{tx(summary.blocking_reason)}}</p>
  <p v-if="result && !fullReconciliation" class="error" role="alert">{{tx('Счёт остаётся несверенным. Новые ручные оплаты заявок недоступны до полной сверки.')}}</p>
 </section>
</template>

<style scoped>
.posting-summary{margin:18px 0;padding:16px;border:1px solid var(--line,#dce0e8);border-radius:12px;background:#f8fafc}.posting-summary h3{margin:0 0 8px}.posting-summary table{width:100%;font-variant-numeric:tabular-nums}.posting-summary td,.posting-summary th{white-space:normal}.posting-summary tbody th{text-align:left}.posting-balances{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:16px 0 0}.posting-balances dt{font-size:.8rem;color:var(--mut,#657084)}.posting-balances dd{margin:5px 0 0;font-weight:700;font-variant-numeric:tabular-nums}.posting-hold{font-size:.85rem;margin-bottom:0}
</style>
