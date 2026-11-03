import {toCents} from '../overview/format.js';

// One source file, all credit rows. Matching and posting status do not change
// the bank's receipt total; ledger entries are never added to these amounts.
export function statementIncomeSummary(statement, rows) {
 const incoming=(Array.isArray(rows)?rows:[]).filter(row=>row.direction==='in');
 const source=toCents(statement?.credit_total);
 let calculated=Array.isArray(rows)?0:null;
 for(const row of incoming){
  const value=toCents(row.amount);
  if(value===null||value<0||calculated===null||!Number.isSafeInteger(calculated+value)){calculated=null;break;}
  calculated+=value;
 }
 const total=source!==null&&source>=0?source:null;
 return {total,calculated,rows:incoming,count:incoming.length,
  matches:total!==null&&calculated!==null&&total===calculated};
}
