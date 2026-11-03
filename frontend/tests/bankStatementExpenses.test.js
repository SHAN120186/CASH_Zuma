import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text,find,all} from './helpers/mountVue.js';
import {statementExpenses} from '../src/cashflow/statementExpenses.js';
import {lang,setLang} from '../src/i18n/index.js';

const component=await compileComponent(new URL('../src/BankStatementExpenses.vue',import.meta.url));
const history=await compileComponent(new URL('../src/BankStatementHistory.vue',import.meta.url),{'./BankStatementExpenses.vue':component});
const statement={debit_total:'236.05',credit_total:'1000.00',period_from:'2026-10-01',period_to:'2026-10-31'};
const debit=(id,kind,amount,extras={})=>({id,date:'2026-10-20',direction:'out',amount,
 correspondent_name:`Synthetic firm ${id} Ltd`,correspondent_inn:`30000000${id}`,document_number:`SYNTH-${id}`,
 expense_kind:kind,expense_request_id:null,expense_category_name:'',status:'review',...extras});
const rows=[
 debit(1,'request','100.10',{expense_request_id:71,expense_category_name:'Synthetic materials',status:'auto_pay_candidate'}),
 debit(2,'bank_fee','2.15',{status:'fee_candidate'}),
 debit(3,'fee_possible','3.20'),
 debit(4,'no_request','40.50'),
 debit(5,'bank_interest','5.25'),
 debit(6,'internal_transfer','60.00',{ledger_id:66,status:'manual_transfer_match',operation_state:'active'}),
 debit(7,'unresolved','7.35',{status:'ambiguous_duplicate',uncertain:true,include_in_daily:false}),
 debit(8,'future_unknown_kind','8.45',{status:'duplicate',uncertain:true}),
 debit(9,'request','9.05',{expense_request_id:79,expense_category_name:'Synthetic services',ledger_id:99,status:'paid_match',operation_state:'active'}),
 {id:10,date:'2026-10-20',direction:'in',amount:'1000.00',expense_kind:'request',correspondent_name:'Synthetic buyer'},
];
const groupButton=(view,key)=>find(view.container,node=>node.tag==='button'&&
 String(node.props.class||'').split(/\s+/).includes(key));
const bodyRows=view=>all(find(view.container,node=>node.tag==='tbody'),node=>node.tag==='tr');

test('every debit is grouped exactly once regardless of status, uncertainty, prior posting or daily flag',()=>{
 const frozenRows=rows.map(row=>Object.freeze({...row}));Object.freeze(frozenRows);
 const before=JSON.stringify(frozenRows),summary=statementExpenses(Object.freeze({...statement}),frozenRows);
 assert.equal(summary.declared,23605);assert.equal(summary.total,23605);assert.equal(summary.matches,true);
 assert.deepEqual(summary.rows.map(row=>row.id),[1,2,3,4,5,6,7,8,9]);
 const grouped=summary.groups.flatMap(group=>group.rows.map(row=>row.id));
 assert.equal(grouped.length,9);assert.equal(new Set(grouped).size,9);
 assert.equal(summary.groups.reduce((sum,group)=>sum+group.total,0),23605);
 assert.deepEqual(summary.groups.find(group=>group.key==='request').rows.map(row=>row.id),[1,9]);
 assert.deepEqual(summary.groups.find(group=>group.key==='unresolved').rows.map(row=>row.id),[7,8]);
 assert.equal(JSON.stringify(frozenRows),before);
});

test('bank fee, possible fee, loan interest and internal transfer remain different groups',()=>{
 const summary=statementExpenses(statement,rows);
 for(const [key,ids,total] of [
  ['bank_fee',[2],215],['fee_possible',[3],320],['bank_interest',[5],525],
  ['internal_transfer',[6],6000],['no_request',[4],4050],['unresolved',[7,8],1580],
 ]){
  const group=summary.groups.find(group=>group.key===key);
  assert.deepEqual(group.rows.map(row=>row.id),ids);assert.equal(group.total,total);
 }
});

test('missing or invalid amounts cannot become a zero or a false reconciled total',()=>{
 for(const value of [null,undefined,'','bad','-1.00','9007199254740992']){
  const summary=statementExpenses({debit_total:value},[]);
  assert.equal(summary.declared,null);assert.equal(summary.matches,false);
 }
 for(const value of [null,'bad','-1.00','9007199254740992']){
  const summary=statementExpenses({debit_total:'0.00'},[debit(1,'no_request',value)]);
  assert.equal(summary.total,null);assert.equal(summary.matches,false);
  assert.equal(summary.groups.find(group=>group.key==='no_request').total,null);
 }
 assert.equal(statementExpenses({debit_total:'0.00'},undefined).matches,false);
 assert.equal(statementExpenses({debit_total:'0.00'},[]).matches,true);
 const large=statementExpenses({debit_total:'123456789.30'},[debit(1,'request','123456789.13'),debit(2,'no_request','0.17')]);
 assert.equal(large.total,12345678930);assert.equal(large.matches,true);
});

test('the whole-file bank debit remains first and visible while a group shows firm, INN and amount',async t=>{
 const view=mount(component,{statement:{...statement},rows,currency:'UZS'});t.after(view.unmount);await settle();
 assert.match(text(view.container),/Списано всего по выписке/);assert.match(text(view.container),/236,05 UZS/);
 assert.match(text(view.container),/Все списания включены в итог один раз/);assert.equal(bodyRows(view).length,9);
 groupButton(view,'no_request').props.onClick();await settle();
 assert.equal(bodyRows(view).length,1);const content=text(bodyRows(view)[0]);
 assert.match(content,/Synthetic firm 4/);assert.match(content,/300000004/);assert.match(content,/40,50 UZS/);
 assert.doesNotMatch(text(find(view.container,node=>node.tag==='tbody')),/Synthetic firm [12356789]|Synthetic buyer/);
 assert.match(text(view.container),/вводить их вручную не нужно/);
 assert.match(text(find(view.container,node=>node.tag==='tfoot')),/Итого по выбранной группе40,50 UZS/);
 assert.match(text(view.container),/Списано всего по выписке236,05 UZS/);
 assert.equal(groupButton(view,'no_request').props['aria-pressed'],true);
});

test('filters keep possible fees, interest, own transfers and ambiguous rows separate without hiding gross debit',async t=>{
 const view=mount(component,{statement:{...statement},rows,currency:'USD'});t.after(view.unmount);await settle();
 for(const [key,ids] of [['bank_fee',[2]],['fee_possible',[3]],['bank_interest',[5]],['internal_transfer',[6]],['unresolved',[7,8]]]){
  groupButton(view,key).props.onClick();await settle();
  assert.equal(bodyRows(view).length,ids.length);
  assert.deepEqual(bodyRows(view).map(row=>text(row).match(/Synthetic firm (\d+)/)?.[1]),ids.map(String));
  assert.match(text(view.container),/Списано всего по выписке236,05 USD/);
 }
 assert.match(text(view.container),/Они не скрыты из общего итога/);
});

test('existing active paid requests are explanations rather than a second amount or payment action',async t=>{
 let calls=0;const view=mount(component,{statement:{...statement},rows,currency:'UZS',onChanged:()=>calls++});
 t.after(view.unmount);await settle();
 groupButton(view,'request').props.onClick();await settle();
 assert.equal(bodyRows(view).length,2);
 const already=bodyRows(view).find(row=>/Synthetic firm 9/.test(text(row)));
 assert.match(text(already),/Уже учтено — повторно не добавляется/);
 assert.match(text(already),/Заявка №79/);assert.match(text(already),/Статья: Synthetic services/);
 assert.match(text(find(view.container,node=>node.tag==='tfoot')),/109,15 UZS/);
 assert.equal(view.state.summary.total,23605);
 for(const button of all(view.container,node=>node.tag==='button')){
  assert.equal(button.props.type,'button');button.props.onClick();await settle();
 }
 assert.equal(find(view.container,node=>['form','input','textarea'].includes(node.tag)),undefined);
 assert.equal(calls,0);assert.equal(view.state.summary.total,23605);
});

test('mismatch keeps the declared file amount and makes incomplete row totals explicit',async t=>{
 const view=mount(component,{statement:{...statement},rows:[rows[0]],currency:'USD'});t.after(view.unmount);await settle();
 assert.match(text(view.container),/Списано всего по выписке236,05 USD/);
 assert.match(text(view.container),/Сумма строк: 100,10 USD/);
 assert.ok(find(view.container,node=>node.props?.role==='alert'));
 assert.doesNotMatch(text(view.container),/Все списания включены в итог один раз/);
});

test('changing the selected statement clears the prior group and all prior rows',async t=>{
 const view=mount(component,{statement:{...statement},rows,currency:'UZS'});t.after(view.unmount);await settle();
 groupButton(view,'no_request').props.onClick();await settle();assert.equal(view.state.selected,'no_request');
 view.app._instance.props.statement={...statement,debit_total:'12.00',period_from:'2026-11-01',period_to:'2026-11-02'};
 view.app._instance.props.rows=[debit(11,'bank_fee','12.00',{correspondent_name:'Synthetic new statement bank'})];
 await settle();assert.equal(view.state.selected,'all');assert.equal(bodyRows(view).length,1);
 assert.match(text(view.container),/Synthetic new statement bank/);assert.doesNotMatch(text(view.container),/Synthetic firm 4/);
 assert.equal(view.state.summary.total,1200);
});

test('company changes remove the expense panel and reopen it without the previous filter; history only reads',async t=>{
 const calls=[];let company=1;
 const item=()=>({id:company,filename:`synthetic-company-${company}.xls`,account:{id:company,name:'Synthetic bank',currency:'UZS'},
  period_from:statement.period_from,period_to:statement.period_to,credit_total:statement.credit_total,debit_total:statement.debit_total});
 const api=async(url,options={})=>{calls.push([url,options]);return url.includes('/history?')?
  {page:1,has_more:false,items:[item()]}:{...item(),status:'committed',statement:{...statement},rows:rows.map(row=>({...row}))};};
 const view=mount(history,{api,companyId:1,canView:true});t.after(view.unmount);await settle();
 await view.state.toggle();await view.state.openDetail(view.state.items[0]);await settle();
 let panel=find(view.container,node=>node.tag==='section'&&String(node.props.class||'').includes('statement-expenses'));
 assert.ok(panel);find(panel,node=>node.tag==='button'&&String(node.props.class||'').split(/\s+/).includes('no_request')).props.onClick();
 await settle();assert.equal(all(find(panel,node=>node.tag==='tbody'),node=>node.tag==='tr').length,1);
 company=2;view.app._instance.props.companyId=2;await settle();
 assert.equal(find(view.container,node=>node.tag==='section'&&String(node.props.class||'').includes('statement-expenses')),undefined);
 assert.equal(view.state.detail,null);assert.equal(view.state.open,false);
 await view.state.toggle();await view.state.openDetail(view.state.items[0]);await settle();
 panel=find(view.container,node=>node.tag==='section'&&String(node.props.class||'').includes('statement-expenses'));
 assert.equal(all(find(panel,node=>node.tag==='tbody'),node=>node.tag==='tr').length,9);
 assert.ok(calls.every(([,options])=>!options.method||options.method==='GET'));
});

test('expense labels, classifications, explanations and existing-payment state translate into Uzbek',async t=>{
 const previous=lang.value;setLang('uz');t.after(()=>setLang(previous));
 const view=mount(component,{statement:{...statement},rows,currency:'UZS'});t.after(view.unmount);await settle();
 const content=text(view.container);
 for(const phrase of ['Chiqimlarni avtomatik solishtirish','Ko‘chirma bo‘yicha jami chiqim',
  'Arizalar bo‘yicha','Bank komissiyalari','Andoza bo‘yicha bank ushlanmalari',
  'Kreditlar bo‘yicha foizlar','O‘z hisobvaraqlari orasidagi o‘tkazmalar','Noaniq chiqimlar',
  'Hisobga olingan — qayta qo‘shilmaydi'])assert.ok(content.includes(phrase),phrase);
 groupButton(view,'no_request').props.onClick();await settle();
 assert.match(text(view.container),/Kompaniya \/ STIR/);assert.match(text(view.container),/ularni qo‘lda kiritish kerak emas/);
 assert.match(text(view.container),/Tanlangan guruh bo‘yicha jami/);
});
