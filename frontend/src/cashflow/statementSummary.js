import {N_} from '../i18n/index.js';
import {toCents} from '../overview/format.js';

// These group statement rows, not ledger facts; each debit appears once.
export function statementExpenseGroups(rows) {
 const groups={
  requests:{key:'requests',title:N_('По заявкам'),rows:[]},
  noRequest:{key:'noRequest',title:N_('Расходы без найденной заявки'),rows:[]},
  fees:{key:'fees',title:N_('Банковские комиссии'),rows:[]},
  existing:{key:'existing',title:N_('Ранее внесённые операции'),rows:[]},
  transfer:{key:'transfer',title:N_('Переводы между своими счетами'),rows:[]},
  review:{key:'review',title:N_('Списания на проверке'),rows:[]},
 };
 const requestStatuses=new Set(['auto_pay_candidate','auto_paid','paid_match','manual_paid','manual_match']);
 const feeStatuses=new Set(['fee_candidate','fee_existing','auto_fee_posted','fee_matched']);
 const outgoingStatuses=new Set(['outgoing_candidate','auto_outgoing_posted']);
 const existingStatuses=new Set(['other_matched_candidate','other_matched']);
 for(const row of rows||[]){
  if(row.direction!=='out')continue;
  let key='review';
  if(!row.uncertain&&requestStatuses.has(row.status)&&row.request_id)key='requests';
  else if(!row.uncertain&&(feeStatuses.has(row.status)||(row.status==='reviewed'&&row.classification==='bank_fee')))key='fees';
  else if(!row.uncertain&&outgoingStatuses.has(row.status))key='noRequest';
  else if(!row.uncertain&&existingStatuses.has(row.status))key='existing';
  else if(!row.uncertain&&row.status==='manual_transfer_match')key='transfer';
  groups[key].rows.push(row);
 }
 return Object.values(groups).map(group=>{
  let total=0;
  for(const row of group.rows){const cents=toCents(row.amount);if(cents===null||cents<0||!Number.isSafeInteger(total+cents)){total=null;break;}total+=cents;}
  return {...group,total};
 });
}
