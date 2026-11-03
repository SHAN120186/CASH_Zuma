import {N_} from '../i18n/index.js';
import {toCents} from '../overview/format.js';

// A bank-file reconciliation, not a second ledger. Every debit belongs to one
// display group, including unresolved or already posted rows, and is added once.
export function statementExpenses(statement,rows){
 const definitions=[
  ['request',N_('По заявкам')],['bank_fee',N_('Банковские комиссии')],
  ['fee_possible',N_('Удержания банка по шаблону')],['no_request',N_('Расходы без найденной заявки')],
  ['bank_interest',N_('Проценты по кредитам')],['internal_transfer',N_('Переводы между своими счетами')],
  ['unresolved',N_('Неоднозначные списания')],
 ];
 const groups=Object.fromEntries(definitions.map(([key,title])=>[key,{key,title,rows:[],total:0}]));
 const debits=(Array.isArray(rows)?rows:[]).filter(row=>row.direction==='out');
 let total=Array.isArray(rows)?0:null;
 for(const row of debits){
  const group=groups[row.expense_kind]||groups.unresolved;
  group.rows.push(row);
  const cents=toCents(row.amount);
  if(cents===null||cents<0||group.total===null||!Number.isSafeInteger(group.total+cents))group.total=null;
  else group.total+=cents;
  if(cents===null||cents<0||total===null||!Number.isSafeInteger(total+cents))total=null;
  else total+=cents;
 }
 const declared=toCents(statement?.debit_total);
 return {groups:Object.values(groups),rows:debits,total,declared:declared!==null&&declared>=0?declared:null,
  matches:declared!==null&&declared>=0&&total!==null&&declared===total};
}
