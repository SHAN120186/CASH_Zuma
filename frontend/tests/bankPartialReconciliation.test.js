import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text,find} from './helpers/mountVue.js';
import {lang,setLang} from '../src/i18n/index.js';

const summaryComponent=await compileComponent(new URL('../src/BankStatementPostingSummary.vue',import.meta.url));
const expenses=await compileComponent(new URL('../src/BankStatementExpenses.vue',import.meta.url));
const income=await compileComponent(new URL('../src/BankStatementIncome.vue',import.meta.url));
const bank=await compileComponent(new URL('../src/BankStatements.vue',import.meta.url),{'./BankStatementPostingSummary.vue':summaryComponent,'./BankStatementExpenses.vue':expenses,'./BankStatementIncome.vue':income});
const history=await compileComponent(new URL('../src/BankStatementHistory.vue',import.meta.url),{'./BankStatementPostingSummary.vue':summaryComponent});
const overview=await compileComponent(new URL('../src/overview/OverviewPage.vue',import.meta.url));
const group=(count,credit_total,debit_total)=>({count,credit_total,debit_total});
const summary={scope:'preview',ready:group(4,'30.00','15.00'),posted:group(0,'0.00','0.00'),linked:group(0,'0.00','0.00'),pending:group(2,'40.00','20.00'),bank_closing:'135.00',ledger_closing:'100.00',projected_closing:'115.00',conservative_closing:'95.00',closing_matches:true,partial_allowed:true,blocking_reason:''};
const resultSummary={...summary,scope:'result',ready:group(0,'0.00','0.00'),posted:group(4,'30.00','15.00'),linked:group(4,'30.00','15.00'),ledger_closing:'115.00'};
const account={id:21,name:'Synthetic bank',kind:'bank',currency:'UZS',unreconciled_statements:1};
const source={account_number:'SYNTHETIC-21',ready:true,balance_matches:true,declared_totals_match:true,period_from:'2026-11-01',period_to:'2026-11-03',opening_balance:'100.00',credit_total:'70.00',debit_total:'35.00',closing_balance:'135.00'};
const rows=[
 {id:1,direction:'in',amount:'30.00',status:'income_candidate',posting_state:'ready'},
 {id:2,direction:'out',amount:'10.00',status:'outgoing_candidate',expense_kind:'no_request',posting_state:'ready'},
 {id:3,direction:'out',amount:'2.00',status:'fee_candidate',expense_kind:'bank_fee',posting_state:'ready',category_name:'Банковские комиссии (выписки)'},
 {id:4,direction:'out',amount:'3.00',status:'interest_candidate',expense_kind:'bank_interest',posting_state:'ready',category_name:'Проценты по кредитам (выписки)'},
 {id:5,direction:'in',amount:'40.00',status:'review',posting_state:'pending'},
 {id:6,direction:'out',amount:'20.00',status:'ambiguous_duplicate',expense_kind:'unresolved',posting_state:'pending'},
].map(row=>({...row,date:'2026-11-03',correspondent_name:`Synthetic ${row.id}`,document_number:`SYN-${row.id}`}));
const preview={id:7,status:'preview',preview_token:'partial-signed-7',account,statement:source,full_reconciliation:false,opening_matches:true,rows,posting_summary:summary};
const result={id:7,status:'committed',auto_paid:0,linked_paid:0,auto_income:1,auto_outgoing:1,auto_fee:1,auto_interest:1,interest_linked:0,review:2,full_reconciliation:false,partial_reconciliation:true,posting_summary:resultSummary};
const stateRow=(view,key)=>find(view.container,node=>node.tag==='tr'&&node.props?.['data-posting-state']===key);
function apiFor(calls,extra){return async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.includes('/preview?')||url.endsWith('/resume'))return preview;if(url.endsWith('/commit')&&!url.includes('/reprocess/'))return result;return extra?extra(url,options):{items:[]};};}

test('server ready and pending count plus incoming/outgoing amounts remain separate from accounted and bank closing',async t=>{
 const view=mount(summaryComponent,{summary,currency:'UZS'});t.after(view.unmount);await settle();
 assert.match(text(stateRow(view,'ready')),/Проверенные строки к проведению430 UZS15 UZS/);
 assert.match(text(stateRow(view,'pending')),/Остаётся на проверке240 UZS20 UZS/);
 assert.match(text(view.container),/Остаток по выписке135 UZS/);assert.match(text(view.container),/Учтённый остаток на сайте100 UZS/);assert.match(text(view.container),/Остаток после проверенных строк115 UZS/);assert.match(text(view.container),/Остаток за вычетом спорных списаний95 UZS/);
 assert.match(text(view.container),/После проведения из доступных средств будет удерживаться 20 UZS спорных списаний/);assert.match(text(view.container),/Поступления на проверке не дают денег для оплаты/);
 assert.equal(find(view.container,node=>['button','input','form'].includes(node.tag)),undefined);
});

test('result distinguishes newly posted rows inside accounted totals and history uses its own cumulative label',async t=>{
 const view=mount(summaryComponent,{summary:resultSummary,currency:'USD',result:true,fullReconciliation:false});t.after(view.unmount);await settle();
 assert.match(text(stateRow(view,'linked')),/Всего учтено по выписке430,00 USD15,00 USD/);assert.match(text(stateRow(view,'posted')),/Из них новые операции за это подтверждение430,00 USD15,00 USD/);
 assert.equal(stateRow(view,'ready'),undefined);assert.match(text(view.container),/Эти строки не складываются между собой/);assert.match(text(view.container),/Счёт остаётся несверенным/);assert.doesNotMatch(text(view.container),/Остаток после проверенных строк/);
 view.app._instance.props.summary={...resultSummary,scope:'history'};await settle();assert.match(text(stateRow(view,'posted')),/Из них создано этой выпиской/);assert.doesNotMatch(text(view.container),/за это подтверждение/);
});

test('missing summaries never become zeros and invalid or unsafe money is displayed as unknown',async t=>{
 const view=mount(summaryComponent,{summary:null,currency:'UZS'});t.after(view.unmount);await settle();assert.equal(text(view.container),'');
 view.app._instance.props.summary={...summary,ready:group(1,'bad','9007199254740992.00')};await settle();assert.match(text(stateRow(view,'ready')),/1— UZS— UZS/);
 view.app._instance.props.summary={...summary,pending:{count:undefined}};await settle();assert.equal(text(view.container),'');
});

test('signed verified subset can be confirmed with unresolved rows while raw totals and actual posted fact stay distinct',async t=>{
 const calls=[];let changed=0;const view=mount(bank,{api:apiFor(calls),companyId:1,accounts:[account],canImport:true,canPost:true,onChanged:()=>changed++});t.after(view.unmount);await settle();
 await view.state.resumeSaved({id:7,status:'preview',account});await settle();
 assert.match(text(view.container),/Поступления — всего за период выписки.*70 UZS/);assert.match(text(view.container),/Списано всего по выписке35 UZS/);
 assert.match(text(view.container),/Поступление будет проведено/);assert.match(text(view.container),/Комиссия будет проведена/);assert.match(text(view.container),/Проценты по кредиту будут проведены/);assert.match(text(view.container),/На проверку: 2/);assert.match(text(view.container),/Банковские комиссии \(выписки\)/);assert.match(text(view.container),/Проценты по кредитам \(выписки\)/);
 await view.state.commit();assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,0);
 view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();assert.equal(view.state.ready,true);await view.state.commit();await settle();
 assert.equal(changed,1);assert.equal(view.state.preview,null);assert.match(text(stateRow(view,'posted')),/Из них новые операции за это подтверждение430 UZS15 UZS/);assert.match(text(stateRow(view,'pending')),/240 UZS20 UZS/);
 assert.match(text(view.container),/Поступлений проведено: 1/);assert.match(text(view.container),/комиссий проведено: 1/);assert.match(text(view.container),/Процентов по кредитам проведено: 1/);assert.match(text(view.container),/полная сверка счёта не завершена/);
 assert.doesNotMatch(text(view.container),/Поступления, комиссии и прочие списания отложены|Выписка принята/);
 await view.state.commit();assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,1);assert.deepEqual(JSON.parse(calls.find(([url])=>url.endsWith('/commit'))[1].body),{confirm_account:true,confirm_currency:true,fee_category_id:null,preview_token:'partial-signed-7'});
});

test('server-blocked candidate rows are pending and a closing mismatch is explained instead of promised as posted',async t=>{
 const blocked={...preview,posting_summary:{...summary,ready:group(0,'0.00','0.00'),pending:group(6,'70.00','35.00'),partial_allowed:false,closing_matches:false,blocking_reason:'Движения сайта не согласуются с учтённой частью банковской выписки.'},rows:rows.map(row=>({...row,posting_state:'pending'}))};
 const calls=[];const view=mount(bank,{api:async(url,opts={})=>{calls.push([url,opts]);if(url.endsWith('/resume'))return blocked;if(url.endsWith('/settings'))return {fee_category_id:null};return {items:[]};},companyId:1,accounts:[account],canImport:true});t.after(view.unmount);await settle();await view.state.resumeSaved({id:7,status:'preview',account});await settle();
 assert.match(text(view.container),/На проверку: 6/);assert.match(text(stateRow(view,'ready')),/00 UZS0 UZS/);assert.match(text(view.container),/Движения сайта не согласуются/);assert.doesNotMatch(text(view.container),/Проценты по кредиту будут проведены|Комиссия будет проведена|Поступление будет проведено/);
});

test('repeat processing uses signed readiness and reports only its new result without clearing the unresolved-account warning',async t=>{
 const calls=[];const api=apiFor(calls,async url=>url==='/api/bank-statements/reprocess/batches'?{items:[{id:7,account_name:account.name,currency:'UZS',filename:'synthetic.xls',period_from:source.period_from,period_to:source.period_to}]}:url.endsWith('/reprocess/preview')?{...preview,batch_id:7,preview_token:'reprocess-partial'}:url.endsWith('/reprocess/commit')?{...result,posted:4,linked:0,review:2}:{items:[]});
 const view=mount(bank,{api,companyId:1,accounts:[account],canPost:true,canReview:true});t.after(view.unmount);await settle();view.state.selectedOldBatch='7';await settle();await view.state.previewOldBatch();await settle();
 assert.match(text(stateRow(view,'ready')),/430 UZS15 UZS/);assert.match(text(view.container),/Проценты по кредиту будут проведены/);await view.state.commitOldBatch();await settle();
 assert.match(text(stateRow(view,'posted')),/Из них новые операции за это подтверждение/);assert.match(text(view.container),/Ручная оплата заявок.*временно недоступна/);assert.match(text(view.container),/Спорные строки остаются на проверке/);assert.doesNotMatch(text(view.container),/поступления, комиссии и прочие списания не проводились/);
 assert.deepEqual(JSON.parse(calls.find(([url])=>url.endsWith('/reprocess/commit'))[1].body),{fee_category_id:null,preview_token:'reprocess-partial',confirm:true});
});

async function renderedReprocess(t,commitResponse,posting=summary,overrides={}){
 const calls=[];
 const api=apiFor(calls,async url=>url==='/api/bank-statements/reprocess/batches'?{items:[{id:7,account_name:account.name,currency:'UZS',filename:'synthetic.xls',period_from:source.period_from,period_to:source.period_to}]}:url.endsWith('/reprocess/preview')?{...preview,batch_id:7,preview_token:'reprocess-click-signed',posting_summary:posting,...overrides}:url.endsWith('/reprocess/commit')?commitResponse():{items:[]});
 const view=mount(bank,{api,companyId:1,accounts:[account],canPost:true});t.after(view.unmount);await settle();
 const select=find(view.container,node=>node.tag==='select'&&node.children.some(option=>option.tag==='option'&&option.props.value==='7'));
 assert.ok(select);select.props['onUpdate:modelValue']('7');await settle();
 const check=find(view.container,node=>node.tag==='button'&&text(node)==='Проверить выписку');assert.ok(check);await check.props.onClick();await settle();
 return {view,calls};
}

test('the visible counted confirm action before reprocess rows posts once even on a rapid double click and displays actual result',async t=>{
 let finish;
 const {view,calls}=await renderedReprocess(t,()=>new Promise(resolve=>finish=resolve));
 const action=find(view.container,node=>node.tag==='button'&&text(node)==='Провести проверенные строки (4)');assert.ok(action);assert.equal(action.props.disabled,false);
 const summaryPanel=find(view.container,node=>node.tag==='section'&&String(node.props.class||'').includes('posting-summary'));
 assert.equal(action.parent,summaryPanel.parent);assert.ok(action.parent.children.indexOf(action)>action.parent.children.indexOf(summaryPanel));
 const sourceTable=find(action.parent,node=>node.tag==='tbody'&&node.children.some(row=>row.tag==='tr'&&text(row).includes('Synthetic 1')));
 assert.ok(sourceTable);assert.ok(action.parent.children.indexOf(action)<action.parent.children.indexOf(sourceTable.parent.parent));
 const first=action.props.onClick(),second=action.props.onClick();await settle();assert.equal(calls.filter(([url])=>url.endsWith('/reprocess/commit')).length,1);assert.equal(action.props.disabled,true);
 finish({...result,posted:4,linked:0,review:2});await Promise.all([first,second]);await settle();
 assert.deepEqual(JSON.parse(calls.find(([url])=>url.endsWith('/reprocess/commit'))[1].body),{fee_category_id:null,preview_token:'reprocess-click-signed',confirm:true});
 assert.match(text(view.container),/Повторная проверка завершена. Проведено: 4; связано: 0; на проверке: 2/);assert.match(text(stateRow(view,'posted')),/Из них новые операции за это подтверждение430 UZS15 UZS/);assert.equal(calls.filter(([url])=>url.endsWith('/reprocess/commit')).length,1);
});

test('the counted confirm action cannot claim posted amounts after a server conflict',async t=>{
 const {view,calls}=await renderedReprocess(t,()=>{throw Object.assign(Error('Synthetic preview changed. Check again.'),{status:409});});
 const action=find(view.container,node=>node.tag==='button'&&text(node)==='Провести проверенные строки (4)');await action.props.onClick();await settle();
 assert.match(text(view.container),/Synthetic preview changed. Check again./);assert.doesNotMatch(text(view.container),/Повторная проверка завершена|Проведено: 4/);assert.equal(stateRow(view,'posted'),undefined);assert.equal(calls.filter(([url])=>url.endsWith('/reprocess/commit')).length,1);
});

test('both reprocess confirmation actions are disabled when the signed summary has no ready or linked rows',async t=>{
 const empty={...summary,ready:group(0,'0.00','0.00'),linked:group(0,'0.00','0.00')};
 const {view,calls}=await renderedReprocess(t,()=>result,empty);
 for(const label of ['Провести проверенные строки (0)','Подтвердить повторную обработку']){
  const action=find(view.container,node=>node.tag==='button'&&text(node)===label);assert.ok(action);assert.equal(action.props.disabled,true);await action.props.onClick();
 }
 assert.equal(calls.filter(([url])=>url.endsWith('/reprocess/commit')).length,0);assert.doesNotMatch(text(view.container),/Повторная проверка завершена/);
});

test('already accounted rows do not enable a no-op reprocess while other rows remain disputed',async t=>{
 const accountedRows=Array.from({length:74},(_,index)=>({...rows[3],id:index+1,status:'auto_interest_posted',posting_state:'linked'}));
 const pendingRows=Array.from({length:9},(_,index)=>({...rows[5],id:index+75}));
 const posting={...summary,ready:group(0,'0.00','0.00'),linked:group(74,'0.00','222.00'),pending:group(9,'0.00','180.00')};
 const {view,calls}=await renderedReprocess(t,()=>result,posting,{rows:[...accountedRows,...pendingRows],full_reconciliation:false});
 for(const label of ['Провести проверенные строки (0)','Подтвердить повторную обработку']){
  const action=find(view.container,node=>node.tag==='button'&&text(node)===label);assert.ok(action);assert.equal(action.props.disabled,true);await action.props.onClick();
 }
 assert.equal(calls.filter(([url])=>url.endsWith('/reprocess/commit')).length,0);assert.doesNotMatch(text(view.container),/Повторная проверка завершена/);
});

test('new signed link candidates enable linking even when there are no new operations to post',async t=>{
 const candidates=['income_matched_candidate','fee_existing','interest_existing','other_matched_candidate'].map((status,index)=>({...rows[index],status,posting_state:'linked'}));
 const posting={...summary,ready:group(0,'0.00','0.00'),linked:group(4,'30.00','15.00')};
 const {view,calls}=await renderedReprocess(t,()=>({...result,posted:0,linked:4}),posting,{rows:candidates});
 const action=find(view.container,node=>node.tag==='button'&&text(node)==='Связать проверенные строки (4)');assert.ok(action);assert.equal(action.props.disabled,false);await action.props.onClick();await settle();
 assert.equal(calls.filter(([url])=>url.endsWith('/reprocess/commit')).length,1);assert.match(text(view.container),/Проведено: 0; связано: 4/);
});

test('fully reconciled accounted rows can finish reconciliation without a misleading post-zero action',async t=>{
 const posting={...resultSummary,ready:group(0,'0.00','0.00'),posted:group(0,'0.00','0.00'),pending:group(0,'0.00','0.00')};
 const {view,calls}=await renderedReprocess(t,()=>({...result,posted:0,linked:0,review:0,full_reconciliation:true,posting_summary:posting}),posting,{rows:rows.slice(0,4).map(row=>({...row,status:'auto_fee_posted',posting_state:'linked'})),full_reconciliation:true});
 assert.equal(find(view.container,node=>node.tag==='button'&&text(node)==='Провести проверенные строки (0)'),undefined);
 const bottom=find(view.container,node=>node.tag==='button'&&text(node)==='Подтвердить повторную обработку');assert.equal(bottom.props.disabled,false);
 const prior=lang.value;t.after(()=>setLang(prior));setLang('uz');await settle();
 const finish=find(view.container,node=>node.tag==='button'&&text(node)==='Solishtirishni yakunlash');assert.ok(finish);assert.equal(finish.props.disabled,false);await finish.props.onClick();await settle();
 assert.equal(calls.filter(([url])=>url.endsWith('/reprocess/commit')).length,1);assert.equal(stateRow(view,'posted')!==undefined,true);
});

test('read-only history shows active posted interest and partial statement summary without creating fact again',async t=>{
 const calls=[];const item={id:7,filename:'synthetic.xls',account,period_from:source.period_from,period_to:source.period_to,credit_total:source.credit_total,debit_total:source.debit_total};
 const detail={...item,status:'committed',statement:source,rows:rows.map(row=>({...row,status:row.id===4?'auto_interest_posted':row.status,ledger_id:row.id===4?44:null,operation_state:row.id===4?'active':'unposted'})),posting_summary:{...resultSummary,scope:'history'},full_reconciliation:false};
 const view=mount(history,{api:async(url,opts={})=>{calls.push([url,opts]);return url.includes('/history?')?{page:1,has_more:false,items:[item]}:detail;},companyId:1,canView:true});t.after(view.unmount);await view.state.toggle();await view.state.openDetail(item);await settle();
 assert.match(text(stateRow(view,'posted')),/Из них создано этой выпиской/);assert.match(text(view.container),/Проведена/);assert.match(text(view.container),/Строки без действующей операции остаются на проверке/);assert.match(text(view.container),/Счёт остаётся несверенным/);assert.ok(calls.every(([,options])=>!options.method||options.method==='GET'));
});

test('overview explains the server hold without subtracting it a second time and clears the notice on currency refresh',async t=>{
 const api=async url=>url.startsWith('/api/accounts')||url.startsWith('/api/budgets')?[]:url.startsWith('/api/requests')||url.startsWith('/api/ledger')?{items:[],total:0}:{};
 const view=mount(overview,{api,companyId:1,currency:'UZS',today:'2026-11-03',has:()=>true,canDecide:()=>false});t.after(view.unmount);await settle();
 view.state.s.now={closing:'115.00',reserved:'5.00',available:'90.00',bank_pending_debits:'20.00'};await settle();assert.equal(view.state.available,9000);assert.match(text(view.container),/Доступные средства уменьшены на 20 UZS/);assert.match(text(view.container),/Факт отражает только проведённые операции/);
 view.app._instance.props.currency='USD';await settle();assert.doesNotMatch(text(view.container),/Доступные средства уменьшены/);assert.equal(view.state.bankPendingDebits,null);
 view.state.s.now={bank_pending_debits:'0.00'};await settle();assert.doesNotMatch(text(view.container),/Доступные средства уменьшены/);
});

test('partial summary, conservative hold and separate interest status are available in Uzbek',async t=>{
 const prior=lang.value;setLang('uz');t.after(()=>setLang(prior));
 const calls=[];const view=mount(bank,{api:apiFor(calls),companyId:1,accounts:[account],canImport:true});t.after(view.unmount);await settle();await view.state.resumeSaved({id:7,status:'preview',account});await settle();
 assert.match(text(view.container),/O‘tkazishga tayyor tekshirilgan satrlar/);assert.match(text(view.container),/Tekshiruvda qoladi/);assert.match(text(view.container),/Kredit foizlari o‘tkaziladi/);assert.match(text(view.container),/Bank komissiyalari \(ko‘chirmalar\)/);assert.match(text(view.container),/Kreditlar bo‘yicha foizlar \(ko‘chirmalar\)/);assert.match(text(view.container),/O‘tkazilgandan keyin mavjud mablag‘dan 20 UZS bahsli chiqimlar ushlab qolinadi/);
});
