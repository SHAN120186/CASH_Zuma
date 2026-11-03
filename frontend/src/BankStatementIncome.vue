<script setup>
import {computed,ref,watch} from 'vue';
import {tx,N_} from './i18n/index.js';
import {formatCents,formatShortDate,toCents} from './overview/format.js';
import {statementIncomeSummary} from './cashflow/statementIncome.js';
const props=defineProps({statement:{type:Object,required:true},rows:{type:Array,required:true},currency:{type:String,default:''}});
const expanded=ref(false);
const income=computed(()=>statementIncomeSummary(props.statement,props.rows));
watch(()=>props.statement,()=>{expanded.value=false;});
const money=value=>formatCents(value,props.currency);
</script>

<template>
 <section class="statement-income" :aria-label="tx('Все поступления по выписке')">
  <div class="income-head">
   <div><p class="income-label">{{tx('Поступления — всего за период выписки')}}</p>
    <p v-if="statement.period_from && statement.period_to" class="income-period">{{formatShortDate(statement.period_from)}} — {{formatShortDate(statement.period_to)}} · {{currency || tx('Валюта не указана')}}</p>
   </div>
   <strong class="income-total">{{money(income.total)}} <span>{{currency}}</span></strong>
  </div>
  <p class="income-explanation">{{tx('Сумма всех кредитовых строк этой выписки, включая ещё не проведённые поступления. Списания из неё не вычитаются.')}}</p>
  <p v-if="income.matches" class="income-check">{{tx('Поступлений: {n}. Итог совпадает со строками выписки.',{n:income.count})}}</p>
  <p v-else class="error" role="alert">{{tx('Итог поступлений и строки выписки требуют проверки.')}} <span v-if="income.calculated !== null">{{tx('Сумма строк')}}: {{money(income.calculated)}} {{currency}}.</span></p>
  <button v-if="income.count" type="button" class="secondary tiny" :aria-expanded="expanded" @click="expanded=!expanded">{{tx(expanded?N_('Скрыть поступления'):N_('Показать поступления'))}}</button>
  <div v-if="expanded" class="table-scroll">
   <table><thead><tr><th>{{tx('Дата')}}</th><th>{{tx('Контрагент / документ')}}</th><th>{{tx('ИНН')}}</th><th class="amount">{{tx('Поступление')}}</th></tr></thead>
    <tbody><tr v-for="(row,index) in income.rows" :key="row.id ?? index"><td>{{formatShortDate(row.date)}}</td><td>{{row.correspondent_name || '—'}}<small>{{row.document_number}}</small></td><td>{{row.correspondent_inn || '—'}}</td><td class="amount">{{money(toCents(row.amount))}} {{currency}}</td></tr></tbody>
    <tfoot><tr><th colspan="3">{{tx('Итого поступлений по выписке')}}</th><th class="amount">{{money(income.total)}} {{currency}}</th></tr></tfoot>
   </table>
  </div>
 </section>
</template>

<style scoped>
.statement-income{margin:16px 0;padding:20px;border:1px solid #bce6d0;border-radius:14px;background:#eef9f3;color:#123d2a}.income-head{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:14px}.income-label{margin:0 0 5px;font-weight:700}.income-period{margin:0;color:#49695b;font-size:.85rem}.income-total{font-size:clamp(1.35rem,3vw,2rem);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.income-total span{font-size:.65em;white-space:nowrap}.income-explanation{font-size:.85rem;color:#49695b;margin:12px 0 6px;max-width:850px}.income-check{font-size:.85rem;margin:6px 0 12px}.table-scroll{margin-top:12px}.table-scroll td{max-width:340px;white-space:normal;overflow-wrap:anywhere}.table-scroll small{display:block}.amount{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}tfoot{border-top:2px solid #bce6d0}button{background:white}@media(max-width:600px){.statement-income{padding:14px}.income-head{display:grid}.income-total{font-size:1.4rem}}
</style>
