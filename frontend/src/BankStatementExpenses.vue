<script setup>
import {computed,ref,watch} from 'vue';
import {tx,N_} from './i18n/index.js';
import {formatCents,formatShortDate,toCents} from './overview/format.js';
import {statementExpenses} from './cashflow/statementExpenses.js';
const props=defineProps({statement:{type:Object,required:true},rows:{type:Array,required:true},currency:{type:String,default:''}});
const selected=ref('all');
const summary=computed(()=>statementExpenses(props.statement,props.rows));
const groups=computed(()=>summary.value.groups.filter(g=>g.rows.length||['request','bank_fee','no_request'].includes(g.key)));
const shown=computed(()=>selected.value==='all'?summary.value.rows:summary.value.groups.find(g=>g.key===selected.value)?.rows||[]);
watch(()=>props.statement,()=>{selected.value='all';});
const money=value=>formatCents(value,props.currency);
const kinds=computed(()=>Object.fromEntries(summary.value.groups.map(g=>[g.key,g.title])));
const label=row=>tx(kinds.value[row.expense_kind]||N_('Неоднозначные списания'));
const posted=new Set(['auto_paid','manual_paid','auto_fee_posted','auto_outgoing_posted']);
const linked=new Set(['paid_match','manual_match','fee_matched','other_matched','manual_transfer_match','duplicate']);
const reasons={
 duplicate_or_overlap:N_('Возможный повтор строки или пересечение выписок'),
 request_reference_conflict:N_('Номер заявки в назначении не совпадает с реквизитами'),
 request_not_unique:N_('Есть несколько подходящих заявок или списаний'),
 request_not_approved:N_('Похожая заявка ещё не утверждена'),
 request_not_available:N_('Связь с заявкой недоступна для просмотра'),
 request_category_unavailable:N_('Статья связанной заявки требует проверки'),
 possible_request_different_date:N_('Есть похожая заявка на другую дату'),
 classification_conflict:N_('Назначение операции противоречит найденной связи'),
 own_inn_does_not_prove_transfer:N_('Свой ИНН: тип движения ещё не подтверждён'),
 own_account_not_unique:N_('Собственный счёт не определён однозначно'),
 linked_operation_changed:N_('Связанная операция изменилась или отменена'),
 missing_counterparty_details:N_('В выписке не хватает реквизитов контрагента'),
};
const reason=row=>tx(reasons[row.expense_reason]||N_('Назначение списания требует проверки'));
function state(row){
 if(row.operation_state&&row.operation_state!=='active')return tx(N_('Связь с операцией требует проверки'));
 if(row.expense_existing_operation)return tx(N_('Уже учтено — повторно не добавляется'));
 if(row.ledger_id&&(linked.has(row.status)||row.operation_state==='active'))return tx(N_('Уже учтено — повторно не добавляется'));
 if(row.ledger_id&&posted.has(row.status))return tx(N_('Проведено по выписке'));
 return tx(N_('Сверка показана; проведение — после подтверждения'));
}
</script>

<template>
 <section class="statement-expenses" :aria-label="tx('Автоматическая сверка списаний')">
  <div class="expense-head"><div><h3>{{tx('Автоматическая сверка списаний')}}</h3><p class="sub">{{tx('Статья берётся из найденной заявки. Комиссии выделяются отдельно, остальные внешние расходы показываются с фирмой и суммой из банка.')}}</p></div>
   <div class="expense-total"><span>{{tx('Списано всего по выписке')}}</span><b>{{money(summary.declared)}} {{currency}}</b></div>
  </div>
  <p v-if="summary.matches" class="expense-check">{{tx('Все списания включены в итог один раз.')}}</p>
  <p v-else class="error" role="alert">{{tx('Итог списаний и строки выписки требуют проверки.')}} {{tx('Сумма строк')}}: {{money(summary.total)}} {{currency}}.</p>
  <div class="expense-groups" :aria-label="tx('Группы списаний')">
   <button type="button" class="expense-group" :aria-pressed="selected==='all'" @click="selected='all'"><span>{{tx('Все списания')}} · {{summary.rows.length}}</span><b>{{money(summary.total)}} {{currency}}</b></button>
   <button v-for="group in groups" :key="group.key" type="button" class="expense-group" :class="group.key" :aria-pressed="selected===group.key" @click="selected=group.key"><span>{{tx(group.title)}} · {{group.rows.length}}</span><b>{{money(group.total)}} {{currency}}</b></button>
  </div>
  <p v-if="selected==='fee_possible'" class="sub">{{tx('Банк указал удержание за исполнение платежа или пополнение карты. Оно выделено по шаблону назначения; просмотр не создаёт комиссию повторно.')}}</p>
  <p v-if="selected==='no_request'" class="sub">{{tx('Для этих внешних списаний подходящая заявка не найдена. Фирма, ИНН и сумма взяты из выписки; вводить их вручную не нужно.')}}</p>
  <p v-if="selected==='unresolved'" class="sub">{{tx('Показаны только неоднозначные связи, возможные повторы и движения с неподтверждённым назначением. Они не скрыты из общего итога.')}}</p>
  <div v-if="shown.length" class="table-scroll"><table><thead><tr><th>{{tx('Дата')}}</th><th>{{tx('Фирма / ИНН')}}</th><th class="amount">{{tx('Сумма')}}</th><th>{{tx('Сопоставление')}}</th></tr></thead><tbody>
   <tr v-for="(row,index) in shown" :key="row.id ?? index"><td>{{formatShortDate(row.date)}}<small>{{row.document_number}}</small></td><td>{{row.correspondent_name || tx('Название в выписке отсутствует')}}<small>{{row.correspondent_inn || '—'}}</small></td><td class="amount">{{money(toCents(row.amount))}} {{currency}}</td><td><strong>{{label(row)}}</strong><small v-if="row.expense_request_id">{{tx('Заявка №{id}',{id:row.expense_request_id})}}</small><small v-if="row.expense_category_name">{{tx('Статья')}}: {{row.expense_category_name}}</small><small v-if="row.expense_reason">{{reason(row)}}</small><small class="operation-state">{{state(row)}}</small></td></tr>
  </tbody><tfoot><tr><th colspan="2">{{tx(selected==='all'?N_('Итого списаний по выписке'):N_('Итого по выбранной группе'))}}</th><th class="amount">{{money(selected==='all'?summary.total:summary.groups.find(g=>g.key===selected)?.total)}} {{currency}}</th><th></th></tr></tfoot></table></div>
  <p v-else class="sub">{{tx('В этой группе списаний нет.')}}</p>
 </section>
</template>

<style scoped>
.statement-expenses{margin:20px 0;padding:20px;background:#f8f9fd;border:1px solid #dde1ee;border-radius:14px}.expense-head{display:flex;justify-content:space-between;align-items:start;gap:20px;flex-wrap:wrap}.expense-head h3{margin:0}.expense-head .sub{max-width:720px;line-height:1.5}.expense-total{display:grid;gap:6px}.expense-total span{font-size:.85rem;color:#647087}.expense-total b{font-size:1.5rem;font-variant-numeric:tabular-nums}.expense-check{font-size:.85rem;color:#20744b}.expense-groups{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:8px;margin:14px 0}.expense-group{display:grid;gap:9px;text-align:left;padding:12px;background:white;color:#25304a;border:1px solid #dce0eb;border-radius:10px;min-width:0;white-space:normal;cursor:pointer}.expense-group span{white-space:normal;overflow-wrap:anywhere;min-width:0;font-size:.8rem;line-height:1.4}.expense-group b{font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.expense-group[aria-pressed=true]{border-color:#7354dd;box-shadow:inset 0 0 0 1px #7354dd;background:#f3efff}.expense-group.bank_fee{border-top:3px solid #4c87cf}.expense-group.no_request{border-top:3px solid #e5aa41}.expense-group.request{border-top:3px solid #48a57b}.table-scroll{overflow-x:auto}.table-scroll td{max-width:360px;white-space:normal;overflow-wrap:anywhere}.table-scroll small{display:block;margin-top:5px;font-size:.76rem}.operation-state{color:#6b7488}.amount{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.sub{font-size:.85rem;color:#677185}.table-scroll tfoot{border-top:2px solid #dce0eb}@media(max-width:600px){.statement-expenses{padding:12px}.expense-groups{grid-template-columns:repeat(2,minmax(0,1fr))}.expense-total b{font-size:1.25rem}}
</style>
