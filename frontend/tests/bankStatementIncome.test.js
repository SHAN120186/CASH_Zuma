import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text,find} from './helpers/mountVue.js';
import {statementIncomeSummary} from '../src/cashflow/statementIncome.js';
import {lang,setLang} from '../src/i18n/index.js';
const component=await compileComponent(new URL('../src/BankStatementIncome.vue',import.meta.url));
const statement={credit_total:'300.15',period_from:'2026-10-01',period_to:'2026-10-31'};
const rows=[
 {id:1,date:'2026-10-05',direction:'in',amount:'100.10',status:'review',uncertain:true,correspondent_name:'Synthetic buyer A',correspondent_inn:'300000001',document_number:'IN-A'},
 {id:2,date:'2026-10-10',direction:'in',amount:'200.05',status:'income_matched',ledger_id:44,correspondent_name:'Synthetic buyer B',correspondent_inn:'300000002',document_number:'IN-B'},
 {id:3,date:'2026-10-10',direction:'out',amount:'1000.00',status:'auto_paid',request_id:88,correspondent_name:'Synthetic supplier'},
];

test('full bank credits include review and existing payments once and never subtract expenses',()=>{
 const result=statementIncomeSummary(statement,rows);
 assert.equal(result.total,30015);assert.equal(result.calculated,30015);assert.equal(result.count,2);assert.equal(result.matches,true);
 assert.deepEqual(result.rows.map(row=>row.id),[1,2]);
});

test('source bank total remains visible when details are incomplete; missing and invalid totals are not zero',()=>{
 const result=statementIncomeSummary(statement,rows.slice(0,1));
 assert.equal(result.total,30015);assert.equal(result.calculated,10010);assert.equal(result.matches,false);
 for(const value of [null,undefined,'','bad','-1.00','9007199254740992'])assert.equal(statementIncomeSummary({credit_total:value},[]).total,null);
 assert.equal(statementIncomeSummary({credit_total:'0.00'},[]).matches,true);
 assert.equal(statementIncomeSummary({credit_total:'0.00'},undefined).matches,false);
 assert.equal(statementIncomeSummary({credit_total:'0.00'},[{direction:'in',amount:'bad'}]).calculated,null);
});

test('large grouped receipt amounts preserve each fractional unit',()=>{
 const result=statementIncomeSummary({credit_total:'3600123456.78'},[{direction:'in',amount:'3600000000.00'},{direction:'in',amount:'123456.78'}]);
 assert.equal(result.total,360012345678);assert.equal(result.matches,true);
});

test('card shows a full-period total first and expands only receipts without API or financial actions',async t=>{
 const view=mount(component,{statement,rows,currency:'UZS'});t.after(view.unmount);await settle();
 assert.match(text(view.container),/Поступления — всего за период выписки/);assert.match(text(view.container),/300,15 UZS/);
 assert.match(text(view.container),/Поступлений: 2/);assert.doesNotMatch(text(view.container),/Synthetic buyer/);
 const button=find(view.container,node=>node.tag==='button');assert.equal(button.props['aria-expanded'],false);button.props.onClick();await settle();
 assert.match(text(view.container),/Synthetic buyer A/);assert.match(text(view.container),/300000002/);assert.doesNotMatch(text(view.container),/Synthetic supplier/);
 assert.match(text(view.container),/Итого поступлений по выписке/);
 assert.equal(find(view.container,node=>node.tag==='input'||node.tag==='form'),undefined);
 view.app._instance.props.statement={...statement,credit_total:'0.00'};view.app._instance.props.rows=[];await settle();
 assert.equal(view.state.expanded,false);assert.doesNotMatch(text(view.container),/Synthetic buyer/);assert.match(text(view.container),/0 UZS/);
});

test('mismatched source totals carry an explicit warning beside the full bank amount',async t=>{
 const view=mount(component,{statement,rows:rows.slice(0,1),currency:'USD'});t.after(view.unmount);await settle();
 assert.match(text(view.container),/300,15 USD/);assert.match(text(view.container),/Сумма строк: 100,10 USD/);
 assert.ok(find(view.container,node=>node.props?.role==='alert'));
});

test('receipt card translates total, detail toggle and reconciliation into Uzbek',async t=>{
 const prior=lang.value;setLang('uz');t.after(()=>setLang(prior));
 const view=mount(component,{statement,rows,currency:'UZS'});t.after(view.unmount);await settle();
 assert.match(text(view.container),/Ko‘chirma davri uchun jami tushum/);assert.match(text(view.container),/Tushumlarni ko‘rsatish/);assert.match(text(view.container),/Tushumlar: 2/);
});
