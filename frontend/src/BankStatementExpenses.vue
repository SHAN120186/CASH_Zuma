<script setup>
import {computed,ref,watch} from 'vue';
import {tx,N_} from './i18n/index.js';
import {formatCents,formatShortDate,formatLongDate,toCents} from './overview/format.js';
import {statementExpenses} from './cashflow/statementExpenses.js';
import {statementCategoryName} from './cashflow/statementCategory.js';
const props=defineProps({statement:{type:Object,required:true},rows:{type:Array,required:true},currency:{type:String,default:''}});
const selected=ref('all');
const summary=computed(()=>statementExpenses(props.statement,props.rows));
const groups=computed(()=>summary.value.groups.filter(g=>g.rows.length||['request','bank_fee','no_request'].includes(g.key)));
const shown=computed(()=>selected.value==='all'?summary.value.rows:summary.value.groups.find(g=>g.key===selected.value)?.rows||[]);
const currentGroup=computed(()=>selected.value==='all'?{title:N_('Все списания'),total:summary.value.total,rows:summary.value.rows}:summary.value.groups.find(g=>g.key===selected.value));
const hasState=row=>!!(row.expense_existing_operation||row.ledger_id||row.operation_state&&row.operation_state!=='unposted');
const showDetails=computed(()=>selected.value==='all'||shown.value.some(row=>row.expense_request_id||row.expense_reason||hasState(row)));
watch(()=>props.statement,()=>{selected.value='all';});
const money=value=>formatCents(value,props.currency);
const kinds=computed(()=>Object.fromEntries(summary.value.groups.map(g=>[g.key,g.title])));
const label=row=>tx(kinds.value[row.expense_kind]||N_('Неоднозначные списания'));
const posted=new Set(['auto_paid','manual_paid','auto_fee_posted','auto_interest_posted','auto_outgoing_posted']);
const linked=new Set(['paid_match','manual_match','fee_matched','interest_matched','other_matched','manual_transfer_match','duplicate']);
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
 if(row.ledger_id&&posted.has(row.status))return tx(N_('Проведено по выписке'));
 if(row.expense_existing_operation)return tx(N_('Уже учтено — повторно не добавляется'));
 if(row.ledger_id&&(linked.has(row.status)||row.operation_state==='active'))return tx(N_('Уже учтено — повторно не добавляется'));
 return tx(N_('Сверка показана; проведение — после подтверждения'));
}
</script>

<template>
 <section class="statement-expenses" :aria-label="tx('Автоматическая сверка списаний')">
  <header class="expense-head">
   <div class="expense-heading"><span class="expense-eyebrow">{{tx('Банковская выписка')}}</span><h3>{{tx('Автоматическая сверка списаний')}}</h3><p v-if="statement.period_from && statement.period_to" class="expense-period">{{formatLongDate(statement.period_from)}}<template v-if="statement.period_to!==statement.period_from"> — {{formatLongDate(statement.period_to)}}</template></p></div>
   <div class="expense-total"><span>{{tx('Списано всего по выписке')}}</span><b>{{money(summary.declared)}} <small>{{currency}}</small></b><p v-if="summary.matches" class="expense-check"><span aria-hidden="true">✓</span> {{tx('Все списания включены в итог один раз.')}}</p></div>
  </header>
  <p v-if="!summary.matches" class="expense-alert error" role="alert">{{tx('Итог списаний и строки выписки требуют проверки.')}} {{tx('Сумма строк')}}: {{money(summary.total)}} {{currency}}.</p>
  <div class="expense-workspace">
   <aside class="expense-navigation" :aria-label="tx('Группы списаний')">
    <span class="expense-nav-title">{{tx('Группы списаний')}}</span>
    <div class="expense-groups">
     <button type="button" class="expense-group all" :aria-pressed="selected==='all'" @click="selected='all'"><span class="group-title"><i class="group-dot" aria-hidden="true"></i>{{tx('Все списания')}}<em>{{summary.rows.length}}</em></span><b>{{money(summary.total)}} <small>{{currency}}</small></b></button>
     <button v-for="group in groups" :key="group.key" type="button" class="expense-group" :class="group.key" :aria-pressed="selected===group.key" @click="selected=group.key"><span class="group-title"><i class="group-dot" aria-hidden="true"></i>{{tx(group.title)}}<em>{{group.rows.length}}</em></span><b>{{money(group.total)}} <small>{{currency}}</small></b></button>
    </div>
    <label class="expense-mobile-select">{{tx('Группа списаний')}}<select v-model="selected"><option value="all">{{tx('Все списания')}} · {{summary.rows.length}}</option><option v-for="group in groups" :key="group.key" :value="group.key">{{tx(group.title)}} · {{group.rows.length}}</option></select></label>
   </aside>
   <div class="expense-results">
    <header class="expense-results-head"><div><h4>{{tx(currentGroup?.title || N_('Все списания'))}}</h4><span class="expense-row-count">{{tx('Операций: {n}',{n:shown.length})}}</span></div><b>{{money(currentGroup?.total)}} <small>{{currency}}</small></b></header>
    <p v-if="selected==='fee_possible'" class="expense-hint">{{tx('Банк указал удержание за исполнение платежа или пополнение карты. Оно выделено по шаблону назначения; просмотр не создаёт комиссию повторно.')}}</p>
    <p v-else-if="selected==='no_request'" class="expense-hint">{{tx('Для этих внешних списаний подходящая заявка не найдена. Фирма, ИНН и сумма взяты из выписки; вводить их вручную не нужно.')}}</p>
    <p v-else-if="selected==='unresolved'" class="expense-hint">{{tx('Показаны только неоднозначные связи, возможные повторы и движения с неподтверждённым назначением. Они не скрыты из общего итога.')}}</p>
    <p v-else class="expense-hint">{{tx('Статья берётся из найденной заявки. Комиссии выделяются отдельно, остальные внешние расходы показываются с фирмой и суммой из банка.')}}</p>
    <div v-if="shown.length" class="table-scroll"><table><thead><tr><th class="date-column">{{tx('Дата')}}</th><th>{{tx('Фирма / ИНН')}}</th><th class="amount">{{tx('Сумма')}}</th><th v-if="showDetails">{{tx('Сопоставление')}}</th></tr></thead><tbody>
     <tr v-for="(row,index) in shown" :key="row.id ?? index"><td class="date-column">{{formatShortDate(row.date)}}<small>{{row.document_number}}</small></td><td class="counterparty">{{row.correspondent_name || tx('Название в выписке отсутствует')}}<small>{{row.correspondent_inn || '—'}}</small></td><td class="amount">{{money(toCents(row.amount))}} <small>{{currency}}</small></td><td v-if="showDetails" class="match-column"><strong v-if="selected==='all'" class="match-label" :class="row.expense_kind">{{label(row)}}</strong><span v-if="row.expense_request_id" class="request-reference">{{tx('Заявка №{id}',{id:row.expense_request_id})}}</span><small v-if="row.expense_category_name">{{tx('Статья')}}: {{statementCategoryName(row.expense_category_name)}}</small><small v-if="row.expense_reason" class="match-reason">{{reason(row)}}</small><small v-if="hasState(row)" class="operation-state">{{state(row)}}</small></td></tr>
    </tbody><tfoot><tr><th colspan="2">{{tx(selected==='all'?N_('Итого списаний по выписке'):N_('Итого по выбранной группе'))}}</th><th class="amount">{{money(currentGroup?.total)}} <small>{{currency}}</small></th><th v-if="showDetails"></th></tr></tfoot></table></div>
    <p v-else class="expense-empty">{{tx('В этой группе списаний нет.')}}</p>
    <p class="expense-footnote">{{tx('Итог взят из файла банка. В факт Cash Flow входят только действующие проведённые операции; строки на проверке в факт не добавляются.')}}</p>
   </div>
  </div>
 </section>
</template>

<style scoped>
.statement-expenses{margin:24px 0;background:var(--surface,#fff);border:1px solid var(--line,#dedee8);border-radius:16px;color:var(--text,#182034);overflow:hidden;box-shadow:0 4px 22px -20px #1c214f}.expense-head{display:flex;justify-content:space-between;align-items:center;gap:24px;padding:26px 28px;border-bottom:1px solid var(--line2,#edf0f5)}.expense-eyebrow{display:block;color:var(--primary,#6755d4);font-size:11px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;margin-bottom:8px}.expense-heading{min-width:0}.expense-heading h3{font-size:18px;line-height:1.4;margin:0;font-weight:800}.expense-period{margin:6px 0 0;color:var(--muted-2,#677185);font-size:12px;line-height:1.5}.expense-total{text-align:right;flex-shrink:0}.expense-total>span{font-size:12px;color:var(--muted-2,#677185)}.expense-total>b{display:block;font-size:28px;line-height:1.45;font-weight:800;letter-spacing:-.035em;font-variant-numeric:tabular-nums}.expense-total>b small{display:inline;font-size:12px;letter-spacing:.025em;color:var(--muted-2,#677185);font-weight:600}.expense-check{margin:5px 0 0;color:var(--good,#26734b);font-size:11px;line-height:1.4}.expense-check>span{font-weight:800;margin-right:3px}.expense-alert{margin:0;padding:12px 28px;font-size:12px;border-bottom:1px solid var(--line2,#edf0f5)}.expense-workspace{display:grid;grid-template-columns:260px minmax(0,1fr);align-items:start}.expense-navigation{padding:22px 14px;background:#fafbfe;min-width:0;border-right:1px solid var(--line2,#edf0f5);align-self:stretch}.expense-nav-title{display:block;padding:0 12px 12px;font-size:10px;font-weight:800;letter-spacing:.08em;color:var(--muted-2,#677185);text-transform:uppercase}.expense-groups{display:grid;gap:3px}.expense-group{all:unset;box-sizing:border-box;display:grid;gap:7px;padding:12px;border-radius:9px;cursor:pointer;color:var(--text,#182034);min-width:0;font:inherit;transition:background .15s}.expense-group:hover{background:#eff1f8}.expense-group:focus-visible{outline:2px solid var(--primary,#6755d4);outline-offset:1px}.group-title{display:flex;align-items:baseline;gap:7px;font-size:12px;line-height:1.5;font-weight:600}.group-title em{font-style:normal;font-size:10px;font-weight:500;margin-left:auto;align-self:center;background:#e9edf4;color:#606b81;border-radius:12px;padding:1px 6px;flex-shrink:0}.group-dot{width:6px;height:6px;flex:0 0 6px;border-radius:50%;background:#929bae;align-self:center}.expense-group b{padding-left:13px;font-size:13px;font-weight:750;line-height:1.4;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.expense-group small{display:inline;font-size:10px;font-weight:500;color:var(--muted-2,#677185)}.expense-group[aria-pressed=true]{background:var(--primary-soft,#efebfc);color:var(--primary,#6755d4)}.expense-group[aria-pressed=true] em{background:#e0dafa;color:#6552c5}.expense-group.request .group-dot{background:#39a576}.expense-group.bank_fee .group-dot{background:#4d88ce}.expense-group.fee_possible .group-dot{background:#8a79c3}.expense-group.no_request .group-dot{background:#dc9a38}.expense-group.unresolved .group-dot{background:#d17749}.expense-results{min-width:0;padding:24px 24px 16px}.expense-results-head{display:flex;justify-content:space-between;align-items:start;gap:16px;margin-bottom:10px}.expense-results-head h4{font-size:15px;line-height:1.5;margin:0 0 4px;font-weight:750}.expense-row-count{font-size:11px;color:var(--muted-2,#677185)}.expense-results-head>b{font-size:17px;line-height:1.5;letter-spacing:-.02em;font-variant-numeric:tabular-nums;white-space:nowrap}.expense-results-head>b small{display:inline;font-size:10px;letter-spacing:0;color:var(--muted-2,#677185);font-weight:500}.expense-hint{font-size:11px;line-height:1.65;color:var(--muted-2,#677185);margin:0 0 18px;max-width:680px}.table-scroll{overflow-x:auto;border:1px solid var(--line2,#edf0f5);border-radius:9px}.table-scroll table{width:100%;border-collapse:collapse;font-size:12px;margin:0}.table-scroll th{background:#f7f8fc;padding:12px 10px;font-size:10px;letter-spacing:.02em;color:var(--muted-2,#677185);font-weight:700;text-align:left;border:0}.table-scroll td{padding:15px 10px;vertical-align:top;border-bottom:1px solid var(--line2,#edf0f5);white-space:normal;overflow-wrap:anywhere;line-height:1.55}.table-scroll tbody tr:last-child td{border-bottom:0}.table-scroll tbody tr:hover{background:#fcfcff}.table-scroll small{display:block;margin-top:4px;font-size:10px;line-height:1.5;color:var(--muted-2,#677185);font-weight:400}.table-scroll .date-column{white-space:nowrap;min-width:56px;font-size:11px}.counterparty{font-weight:650;min-width:140px}.counterparty small{font-variant-numeric:tabular-nums;letter-spacing:.02em}.table-scroll .amount{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;font-weight:700;min-width:120px}.table-scroll .amount small{display:inline;font-size:9px;font-weight:500}.match-column{min-width:130px;font-size:10px}.match-label{display:inline-block;font-size:9px;line-height:1.4;background:#f0f2f8;color:#59657e;border-radius:4px;padding:3px 5px;font-weight:600}.match-label.request{color:#21774c;background:#eaf7ef}.match-label.unresolved{color:#a46c27;background:#fff6e7}.request-reference{display:block;font-size:11px;font-weight:700;margin:4px 0}.table-scroll .match-reason{color:#a16e2e}.operation-state{color:var(--muted-2,#677185)}.table-scroll tfoot th{border-top:1px solid var(--line,#dedee8);background:#fafbfe;padding-top:14px;padding-bottom:14px;color:var(--text,#182034);font-size:11px}.expense-footnote{margin:14px 0 0;font-size:10px;line-height:1.5;color:var(--muted-2,#677185)}.expense-empty{padding:36px 12px;text-align:center;font-size:12px;color:var(--muted-2,#677185);background:#fafbfe;border-radius:9px}.expense-mobile-select{display:none}
@media(max-width:1100px){.expense-workspace{grid-template-columns:230px minmax(0,1fr)}.expense-head{padding:22px}.expense-results{padding:22px 18px 16px}.expense-total>b{font-size:25px}}
@media(max-width:850px){.expense-head{align-items:start;gap:18px}.expense-heading h3{font-size:16px}.expense-total>b{font-size:22px}.expense-workspace{grid-template-columns:1fr}.expense-navigation{border-right:0;border-bottom:1px solid var(--line2,#edf0f5);padding:16px 20px;background:#fafbfe}.expense-nav-title,.expense-groups{display:none}.expense-mobile-select{display:grid;gap:7px;font-size:11px;color:var(--muted-2,#677185);font-weight:650}.expense-mobile-select select{box-sizing:border-box;width:100%;max-width:none;min-width:0;border:1px solid var(--line,#dedee8);border-radius:8px;background:white;padding:11px 12px;color:var(--text,#182034);font:inherit;font-size:12px}.expense-results{padding:20px}.expense-results-head h4{font-size:14px}}
@media(max-width:520px){.statement-expenses{border-radius:12px;margin:16px 0}.expense-head{display:grid;padding:20px;gap:20px}.expense-total{text-align:left;min-width:0}.expense-total>b{font-size:27px;overflow-wrap:anywhere}.expense-total>b small{display:inline;font-size:11px}.expense-check{font-size:10px}.expense-results{padding:18px 12px 14px}.expense-results-head{gap:12px}.expense-results-head>b{font-size:14px}.expense-results-head h4{font-size:13px}.expense-hint{font-size:10px}.table-scroll td{padding:12px 9px}.expense-alert{padding:12px 20px}.counterparty{min-width:150px}.table-scroll .amount{min-width:110px}.expense-row-count{font-size:10px}}
</style>
