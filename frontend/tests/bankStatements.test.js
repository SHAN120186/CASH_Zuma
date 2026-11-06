import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text,all,find} from './helpers/mountVue.js';
import {lang,setLang} from '../src/i18n/index.js';

const component=await compileComponent(new URL('../src/BankStatements.vue',import.meta.url));
const expenseComponent=await compileComponent(new URL('../src/BankStatementExpenses.vue',import.meta.url));
const withExpenses=await compileComponent(new URL('../src/BankStatements.vue',import.meta.url),{'./BankStatementExpenses.vue':expenseComponent});
const daily=await compileComponent(new URL('../src/overview/BankStatementDaily.vue',import.meta.url));
const overview=await compileComponent(new URL('../src/overview/OverviewPage.vue',import.meta.url));
const accounts=[{id:21,name:'Synthetic bank',kind:'bank',currency:'UZS'}, {id:22,name:'Synthetic cash',kind:'cash',currency:'UZS'}];
const statement={id:7,status:'preview',preview_token:'snapshot-7',full_reconciliation:true,account:{id:21,name:'Synthetic bank',currency:'UZS'},statement:{account_number:'TEST-BANK-21',ready:true,period_from:'2026-10-30',period_to:'2026-10-30',opening_balance:'100.00',closing_balance:'90.00',credit_total:'10.00',debit_total:'20.00',balance_matches:true,declared_totals_match:true},rows:[{id:1,date:'2026-10-30',document_number:'D1',correspondent_name:'Synthetic',correspondent_inn:'123456789',purpose:'Synthetic payment',direction:'out',amount:'20.00',status:'auto_pay_candidate',request_id:9}]};
const file={name:'statement.xls',size:100};
function event(f=file){return {target:{files:[f],value:'file'}}}

test('statement expense groups keep unclassified debits, fees, transfers and uncertain rows separate',async t=>{
 const lines=[
  {id:1,direction:'out',amount:'1000.10',status:'auto_pay_candidate',request_id:44},
  {id:2,direction:'out',amount:'2000.20',status:'outgoing_candidate',correspondent_name:'Synthetic supplier',correspondent_inn:'123456789'},
  {id:3,direction:'out',amount:'50.05',status:'fee_candidate'},
  {id:4,direction:'out',amount:'70.00',status:'review',reason:'Uncertain debit'},
  {id:5,direction:'out',amount:'80.00',status:'manual_transfer_match'},
  {id:6,direction:'out',amount:'90.00',status:'outgoing_candidate',uncertain:true},
  {id:7,direction:'in',amount:'600.00',status:'income_candidate'},
  {id:8,direction:'out',amount:'25.00',status:'other_matched_candidate'},
 ];
 const api=async url=>url==='/api/bank-statements/settings'?{fee_category_id:null}:url.includes('/preview?')?{...statement,full_reconciliation:false,statement:{...statement.statement,debit_total:'3315.35'},rows:lines.map(r=>({...r,date:'2026-10-30',expense_kind:({1:'request',2:'no_request',3:'bank_fee',4:'unresolved',5:'internal_transfer',6:'unresolved',8:'no_request'})[r.id],expense_existing_operation:r.id===8}))}:{items:[]};
 const view=mount(withExpenses,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();
 view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload(event());await settle();
 assert.match(text(view.container),/1\s*000,10 UZS/);assert.match(text(view.container),/2\s*025,20 UZS/);assert.match(text(view.container),/50,05 UZS/);
 assert.match(text(view.container),/160 UZS/);assert.match(text(view.container),/3\s*315,35 UZS/);
 assert.match(text(view.container),/Synthetic supplier/);assert.match(text(view.container),/123456789/);
 assert.match(text(view.container),/Остаётся на проверке/);assert.match(text(view.container),/Автоматическая сверка списаний/);
});

test('statement summaries keep exact cents, refuse unsafe totals, and translate labels to Uzbek',async t=>{
 const previous=lang.value;setLang('uz');t.after(()=>setLang(previous));
 const api=async url=>url==='/api/bank-statements/settings'?{fee_category_id:null}:url.includes('/preview?')?{...statement,rows:[
  {id:1,date:'2026-10-30',direction:'out',amount:'90071992547409.90',status:'outgoing_candidate',expense_kind:'no_request'},
  {id:2,date:'2026-10-30',direction:'out',amount:'0.10',status:'outgoing_candidate',expense_kind:'no_request'},
  {id:3,date:'2026-10-30',direction:'out',amount:'1.25',status:'fee_candidate',expense_kind:'bank_fee'},
 ]}:{items:[]};
 const view=mount(withExpenses,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();
 view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload(event());await settle();
 assert.match(text(view.container),/— UZS/);assert.match(text(view.container),/1,25 UZS/);
 assert.match(text(view.container),/Ariza topilmagan xarajatlar/);assert.match(text(view.container),/Bank komissiyalari/);
});

test('Cash Flow shortcut is only offered when the current user can open the report',async t=>{
 const api=async url=>url==='/api/bank-statements/settings'?{fee_category_id:null}:url.includes('/preview?')?statement:{items:[]};let opened=0;
 const view=mount(component,{api,companyId:1,accounts,canImport:true,canViewCashFlow:false,onCashFlow:()=>{opened++}});t.after(view.unmount);await settle();
 view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload(event());await settle();
 assert.equal(find(view.container,n=>n.tag==='button'&&text(n).includes('Открыть Cash Flow')),undefined);
 view.app._instance.props.canViewCashFlow=true;await settle();
 const button=find(view.container,n=>n.tag==='button'&&text(n).includes('Открыть Cash Flow'));assert.ok(button);button.props.onClick();assert.equal(opened,1);
});

test('select account and currency explicitly, inspect bank account then confirm each before commit',async t=>{
 const calls=[];
 const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/reviews')return {items:[]};if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.includes('/preview?'))return statement;if(url.endsWith('/commit'))return {auto_paid:1,linked_paid:0,review:0,full_reconciliation:true};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();
 assert.equal(view.state.validSelection,false);assert.equal(view.state.bankAccounts.length,1);
 view.state.accountId='21';view.state.currency='USD';await settle();assert.equal(view.state.validSelection,false);
 await view.state.upload(event());assert.equal(calls.filter(([url])=>url.includes('/preview?')).length,0);
 view.state.currency='UZS';await settle();await view.state.upload(event());await settle();
 assert.match(calls.find(([url])=>url.includes('/preview?'))[0],/account_id=21&currency=UZS/);
 assert.equal(view.state.ready,false);assert.match(text(view.container),/TEST-BANK-21/);
 view.state.confirmAccount=true;await settle();assert.equal(view.state.ready,false);
 view.state.confirmCurrency=true;await settle();assert.equal(view.state.ready,true);
 await view.state.commit();await settle();const call=calls.find(([url])=>url.endsWith('/commit'));
 assert.deepEqual(JSON.parse(call[1].body),{confirm_account:true,confirm_currency:true,fee_category_id:null,preview_token:'snapshot-7'});
 assert.match(text(view.container),/Автоматически оплачено: 1/);assert.equal(view.state.preview,null);
});

test('a failed balance or unexpected scope cannot be confirmed, and no automatic action occurs on upload',async t=>{
 let commits=0;
 const api=async(url)=>{if(url==='/api/bank-statements/reviews')return {items:[]};if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.includes('/preview?'))return {...statement,statement:{...statement.statement,balance_matches:false}};commits++;return {}};
 const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();
 view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload(event());
 view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();assert.equal(view.state.ready,false);await view.state.commit();assert.equal(commits,0);
 assert.match(text(view.container),/не подтверждены/);
});

test('uncertain commit result blocks duplicate submission and old company replies are ignored',async t=>{
 let commits=0,resolvePreview;
 const api=async(url)=>{if(url==='/api/bank-statements/reviews')return {items:[]};if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.includes('/preview?'))return new Promise(resolve=>resolvePreview=resolve);commits++;throw Error('network')};
 const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();
 view.state.accountId='21';view.state.currency='UZS';await settle();
 const pending=view.state.upload(event());await settle();view.state.currency='USD';await settle();resolvePreview(statement);await pending;await settle();assert.equal(view.state.preview,null);
 view.state.currency='UZS';await settle();const next=view.state.upload(event());await settle();resolvePreview(statement);await next;
 view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();await view.state.commit();assert.equal(commits,1);assert.equal(view.state.uncertain,true);
 await view.state.commit();assert.equal(commits,1);assert.match(text(view.container),/Ответ о загрузке не получен/);
});

test('accountant review queue remains visible in Uzbek without granting import to a viewer',async t=>{
 const previous=lang.value;setLang('uz');t.after(()=>setLang(previous));let calls=[];
 const view=mount(component,{companyId:1,accounts,canImport:false,api:async url=>{calls.push(url);return {items:[{id:1,date:'2026-10-30',document_number:'D1',correspondent_name:'Synthetic',correspondent_inn:'',purpose:'Synthetic purpose',direction:'out',amount:'1.00',currency:'UZS',status:'review'}]}}});t.after(view.unmount);await settle();
 assert.match(text(view.container),/Buxgalter tekshiradigan operatsiyalar/);assert.match(text(view.container),/Buxgalter tekshirishi kerak/);
 assert.equal(all(view.container,n=>n.tag==='input'&&n.props?.type==='file').length,0);
 await view.state.upload(event());assert.deepEqual(calls,['/api/bank-statements/reviews']);
});

test('daily bank statement totals are separate, scoped by date and currency, and never assume missing data is zero',async t=>{
 const calls=[];
 const view=mount(daily,{companyId:1,currency:'UZS',today:'2026-10-30',api:async url=>{calls.push(url);return {date:'2026-10-30',currency:'UZS',credit_total:'3500459877.85',debit_total:'4801316304.69',accounts:[{account_id:21,name:'Synthetic bank',credit_total:'3500459877.85',debit_total:'4801316304.69'}]}}});t.after(view.unmount);await settle();
 assert.match(calls[0],/date=2026-10-30&currency=UZS/);assert.match(text(view.container),/3\s*500\s*459\s*877,85/);
 assert.match(text(view.container),/Проведённые поступления, комиссии и списания учитываются в остатке и Cash Flow/);
});


test('review classification only records a decision and refreshes the queue',async t=>{
 const calls=[];
 const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/reviews')return {items:[{id:31,date:'2026-10-30',currency:'UZS',amount:'2',direction:'out',status:'review'}]};if(url.endsWith('/review'))return {};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,canReview:true});t.after(view.unmount);await settle();
 view.state.reviewActions={31:{classification:'bank_fee',note:'Commission'}};await view.state.saveReview({id:31});await settle();
 const action=calls.find(([url])=>url==='/api/bank-statements/lines/31/review');
 assert.deepEqual(JSON.parse(action[1].body),{classification:'bank_fee',note:'Commission'});
 assert.equal(calls.filter(([url])=>url==='/api/bank-statements/reviews').length,2);
 assert.match(text(view.container),/не создаёт операцию/);
});

test('daily unavailable status is never presented as zero',async t=>{
 const view=mount(daily,{companyId:1,currency:'UZS',today:'2026-10-30',api:async()=>({date:'2026-10-30',currency:'UZS',credit_total:'0',debit_total:'0',accounts:[{account_id:21,name:'Synthetic bank'}],available:false})});t.after(view.unmount);await settle();
 assert.match(text(view.container),/выписка не загружена/);assert.doesNotMatch(text(view.container),/Synthetic bank/);
});


test('statement direction and unready source do not mislead',async t=>{
 const api=async(url)=>url==='/api/bank-statements/reviews'?{items:[]}:{...statement,statement:{...statement.statement,ready:false},rows:[...statement.rows,{id:2,date:'2026-10-30',direction:'in',amount:'10',status:'review'}]};
 const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload(event());view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();
 assert.match(text(view.container),/Списание/);assert.match(text(view.container),/Поступление/);assert.equal(view.state.ready,false);
});
test('daily warns about uncertain duplicates',async t=>{
 const view=mount(daily,{companyId:1,currency:'UZS',today:'2026-10-30',api:async()=>({date:'2026-10-30',currency:'UZS',credit_total:'10',debit_total:'0',accounts:[{account_id:21,name:'Synthetic bank',credit_total:'10',debit_total:'0'}],available:true,uncertain:true})});t.after(view.unmount);await settle();
 assert.match(text(view.container),/Итог выписки требует проверки и может быть неполным/);
});


test('manual matching requires explicit confirmation and meaningful reason',async t=>{
 const calls=[];const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/reviews')return {items:[]};if(url.endsWith('/candidates'))return {items:[{id:42,number:'CF-42',counterparty:'Synthetic',counterparty_inn:'123',amount:'20',date:'2026-10-30',status:'approved',version:3}]};if(url.endsWith('/match'))return {status:'manual_paid'};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,canReview:true});t.after(view.unmount);await settle();const row={id:31,status:'review',direction:'out',currency:'UZS'};await view.state.findCandidates(row);view.state.matchActions[31].selected='42';view.state.matchActions[31].note='short';view.state.matchActions[31].confirm=true;await view.state.matchRequest(row);assert.equal(calls.filter(([url])=>url.endsWith('/match')).length,0);view.state.matchActions[31].note='Confirmed by document';await view.state.matchRequest(row);assert.deepEqual(JSON.parse(calls.find(([url])=>url.endsWith('/match'))[1].body),{request_id:42,request_version:3,confirm:true,note:'Confirmed by document'});
});


test('uploaded empty day is a valid zero and is not called absent',async t=>{
 const view=mount(daily,{companyId:1,currency:'UZS',today:'2026-10-30',api:async()=>({date:'2026-10-30',currency:'UZS',credit_total:'0.00',debit_total:'0.00',accounts:[],available:true,uncertain:false})});t.after(view.unmount);await settle();assert.doesNotMatch(text(view.container),/выписка не загружена/);assert.match(text(view.container),/0 UZS/);
});



test('importer without payment rights sees automatic payment depends on authorization and checks',async t=>{
 const api=async(url)=>url==='/api/bank-statements/reviews'?{items:[]}:statement;
 const view=mount(component,{api,companyId:1,accounts,canImport:true,canReview:false});t.after(view.unmount);await settle();view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload(event());await settle();
 assert.match(text(view.container),/только при наличии права оплаты и успешном прохождении проверок/);assert.match(text(view.container),/строка останется бухгалтеру на проверку/);
});

test('fee category is company-scoped, explicitly previewed, and opening mismatch is visible',async t=>{
 const calls=[];
 const categories=[{id:4,name:'Synthetic fee',type:'outcome'},{id:5,name:'Old fee',type:'outcome',retired:true},{id:6,name:'Synthetic income',type:'income'},{id:7,name:'Unclassified',type:'outcome',activity:'unclassified'}];
 const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/reviews'||url==='/api/bank-statements/reprocess/batches')return {items:[]};if(url==='/api/bank-statements/settings')return {fee_category_id:4};if(url.includes('/preview?'))return {...statement,full_reconciliation:false,opening_matches:false,site_opening:'99.00',rows:[{id:2,date:'2026-10-30',amount:'10',direction:'in',status:'income_candidate',category_source:'unclassified'},{id:3,date:'2026-10-30',amount:'1',direction:'out',status:'fee_candidate',category_id:4}]};if(url.endsWith('/commit'))return {auto_paid:0,linked_paid:0,review:2,full_reconciliation:false};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,categories,canImport:true,canPost:true});t.after(view.unmount);await settle();
 assert.deepEqual(view.state.feeCategories.map(c=>c.id),[4]);assert.equal(view.state.feeCategoryId,'4');
 view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload(event());await settle();
 assert.match(calls.find(([url])=>url.includes('/preview?'))[0],/fee_category_id=4/);
 assert.match(text(view.container),/Начальный остаток не совпадает/);assert.match(text(view.container),/Не распределено/);assert.match(text(view.container),/Synthetic fee/);assert.match(text(view.container),/Остаётся на проверке/);assert.doesNotMatch(text(view.container),/Поступление будет проведено|Комиссия будет проведена/);
 view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();await view.state.commit();
 const body=JSON.parse(calls.find(([url])=>url.endsWith('/commit'))[1].body);
 assert.deepEqual(body,{confirm_account:true,confirm_currency:true,fee_category_id:4,preview_token:'snapshot-7'});
 assert.match(text(view.container),/полная сверка счёта не завершена/);assert.doesNotMatch(text(view.container),/Поступления, комиссии и прочие списания отложены/);
});

test('final partial result stays explicit even when the preview was initially complete',async t=>{
 const api=async(url)=>{if(url==='/api/bank-statements/reviews')return {items:[]};if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.includes('/preview?'))return statement;if(url.endsWith('/commit'))return {auto_paid:0,linked_paid:0,review:1,auto_income:0,auto_fee:0,full_reconciliation:false};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload(event());
 assert.match(text(view.container),/Все строки готовы к полной сверке/);view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();await view.state.commit();await settle();
 assert.match(text(view.container),/Спорные строки остаются на проверке/);assert.match(text(view.container),/полная сверка счёта не завершена/);
});

test('incomplete reconciliation keeps pending candidates honest and reports actual request payments',async t=>{
 const api=async(url)=>{if(url==='/api/bank-statements/reviews')return {items:[]};if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.includes('/preview?'))return {...statement,full_reconciliation:false,rows:[...statement.rows,{id:2,date:'2026-10-30',direction:'in',amount:'10',status:'income_candidate'},{id:3,date:'2026-10-30',direction:'out',amount:'5',status:'other_matched_candidate'}]};if(url.endsWith('/commit'))return {auto_paid:1,linked_paid:0,review:2,auto_income:0,auto_fee:0,full_reconciliation:false};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload(event());await settle();
 assert.match(text(view.container),/Точное совпадение с заявкой/);assert.match(text(view.container),/Остаётся на проверке/);assert.doesNotMatch(text(view.container),/Найдена существующая операция/);assert.match(text(view.container),/сервер проведёт доступные проверенные строки/);
 view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();await view.state.commit();await settle();
 assert.match(text(view.container),/Автоматически оплачено: 1/);assert.match(text(view.container),/Спорные строки остаются на проверке/);assert.doesNotMatch(text(view.container),/Поступления, комиссии и прочие списания отложены/);
});

test('existing independent bank operations show as links rather than new entries',async t=>{
 const api=async(url)=>{if(url==='/api/bank-statements/reviews')return {items:[]};if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.includes('/preview?'))return {...statement,rows:[{id:3,date:'2026-10-30',direction:'out',amount:'5',status:'other_matched_candidate'}]};if(url.endsWith('/commit'))return {auto_paid:0,linked_paid:0,review:0,other_linked:1,full_reconciliation:true};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload(event());
 assert.match(text(view.container),/Найдена существующая операция/);view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();await view.state.commit();await settle();
 assert.match(text(view.container),/Других существующих операций связано: 1/);
});

test('a response without a full-reconciliation result cannot be confirmed',async t=>{
 let writes=0;const api=async(url,options={})=>{if(url==='/api/bank-statements/reviews')return {items:[]};if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.includes('/preview?')){const {full_reconciliation,...old}=statement;return old}if(options.method==='POST')writes++;return {}};
 const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload(event());view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();
 assert.equal(view.state.ready,false);await view.state.commit();assert.equal(writes,0);assert.match(text(view.container),/Сервер не подтвердил полную сверку/);
});

test('allocating a posted receipt requires a current token, allowed category and explanation',async t=>{
 const calls=[],row={id:44,date:'2026-10-30',amount:'100',currency:'UZS',account_name:'Bank',correspondent_name:'Synthetic',document_number:'1',category_id:8,edit_token:'current-snapshot'};
 const categories=[{id:8,name:'Unallocated',type:'income',activity:'unclassified'},{id:9,name:'Sales',type:'income'},{id:10,name:'Retired',type:'income',retired:true},{id:11,name:'Expense',type:'outcome'}];
 const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/reviews'||url==='/api/bank-statements/outgoing-unallocated')return {items:[]};if(url==='/api/bank-statements/income-unallocated')return {items:[row]};if(url.endsWith('/income-category'))return {};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,categories,canAllocate:true});t.after(view.unmount);await settle();
 assert.deepEqual(view.state.incomeCategories.map(c=>c.id),[9]);
 view.state.allocationActions={44:{categoryId:'9',reason:'short'}};await view.state.assignIncome(row);assert.equal(calls.filter(([url])=>url.endsWith('/income-category')).length,0);
 view.state.allocationActions={44:{categoryId:'11',reason:'Valid explanation'}};await view.state.assignIncome(row);assert.equal(calls.filter(([url])=>url.endsWith('/income-category')).length,0);
 view.state.allocationActions={44:{categoryId:'9',reason:'Verified bank receipt'}};await view.state.assignIncome(row);
 const call=calls.find(([url])=>url.endsWith('/income-category'));assert.deepEqual(JSON.parse(call[1].body),{category_id:9,edit_token:'current-snapshot',reason:'Verified bank receipt'});
 assert.match(text(view.container),/Поступления без статьи/);
});

test('allocating a posted external debit requires an expense article, a current token and an explanation',async t=>{
 const calls=[],row={id:90,date:'2026-10-30',amount:'35',currency:'UZS',account_name:'Bank',correspondent_name:'Synthetic',document_number:'3',category_id:8,edit_token:'snapshot'};
 const categories=[{id:8,name:'Unallocated',type:'outcome',activity:'unclassified'},{id:9,name:'Expense',type:'outcome'},{id:10,name:'Retired',type:'outcome',retired:true},{id:11,name:'Income',type:'income'}];
 const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/reviews'||url==='/api/bank-statements/income-unallocated')return {items:[]};if(url==='/api/bank-statements/outgoing-unallocated')return {items:[row]};if(url.endsWith('/outgoing-category'))return {};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,categories,canAllocate:true});t.after(view.unmount);await settle();
 assert.deepEqual(view.state.feeCategories.map(c=>c.id),[9]);assert.match(text(view.container),/изменится фактическое использование бюджета/);assert.match(text(view.container),/жёсткого лимита сначала пересмотрите бюджет/);
 view.state.outgoingActions={90:{categoryId:'11',reason:'Correct reason'}};await view.state.assignOutgoing(row);assert.equal(calls.filter(([url])=>url.endsWith('/outgoing-category')).length,0);
 view.state.outgoingActions={90:{categoryId:'9',reason:'short'}};await view.state.assignOutgoing(row);assert.equal(calls.filter(([url])=>url.endsWith('/outgoing-category')).length,0);
 view.state.outgoingActions={90:{categoryId:'9',reason:'Checked bank expense'}};await view.state.assignOutgoing(row);
 const call=calls.find(([url])=>url.endsWith('/outgoing-category'));assert.deepEqual(JSON.parse(call[1].body),{category_id:9,edit_token:'snapshot',reason:'Checked bank expense'});
});

test('a hard-budget rejection stays visible and does not report an expense as classified',async t=>{
 const row={id:90,date:'2026-10-30',amount:'35',currency:'UZS',account_name:'Bank',correspondent_name:'Synthetic',document_number:'3',category_id:8,edit_token:'snapshot'};
 const api=async(url)=>{if(url==='/api/bank-statements/reviews'||url==='/api/bank-statements/income-unallocated')return {items:[]};if(url==='/api/bank-statements/outgoing-unallocated')return {items:[row]};if(url.endsWith('/outgoing-category'))throw Object.assign(Error('Жёсткий лимит превышен. Сначала пересмотрите утверждённый бюджет.'),{status:409});throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,categories:[{id:9,name:'Expense',type:'outcome'}],canAllocate:true});t.after(view.unmount);await settle();view.state.outgoingActions={90:{categoryId:'9',reason:'Checked bank expense'}};await view.state.assignOutgoing(row);await settle();
 assert.match(text(view.container),/Жёсткий лимит превышен/);assert.equal(view.state.outgoingUnallocated.length,1);
});

test('outgoing candidates without signed server readiness remain pending during partial reconciliation',async t=>{
 const api=async(url)=>{if(url==='/api/bank-statements/reviews')return {items:[]};if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.includes('/preview?'))return {...statement,full_reconciliation:false,rows:[{id:3,date:'2026-10-30',direction:'out',amount:'5',status:'outgoing_candidate',category_source:'unclassified_out'}]};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload(event());await settle();
 assert.match(text(view.container),/Остаётся на проверке/);assert.match(text(view.container),/Не распределено/);assert.doesNotMatch(text(view.container),/Списание будет проведено/);
});

test('successful statement confirms how many unallocated external debits were posted',async t=>{
 const api=async(url)=>{if(url==='/api/bank-statements/reviews')return {items:[]};if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.includes('/preview?'))return {...statement,rows:[{id:3,date:'2026-10-30',direction:'out',amount:'5',status:'outgoing_candidate',category_source:'unclassified_out'}]};if(url.endsWith('/commit'))return {auto_paid:0,linked_paid:0,review:0,auto_outgoing:1,full_reconciliation:true};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload(event());
 assert.match(text(view.container),/Списание будет проведено/);view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();await view.state.commit();await settle();
 assert.match(text(view.container),/Внешних списаний проведено: 1/);
});

test('reprocessing requires a current server preview and invalidates it when article changes',async t=>{
 const calls=[],batches=[{id:7,account_name:'Bank',currency:'UZS',period_from:'2026-10-30',period_to:'2026-10-30',filename:'synthetic.xls'}];
 const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/reviews'||url==='/api/bank-statements/income-unallocated')return {items:[]};if(url==='/api/bank-statements/reprocess/batches')return {items:batches};if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.endsWith('/reprocess/preview'))return {batch_id:7,preview_token:'snapshot-old',full_reconciliation:true,opening_matches:true,statement_opening:'100.00',site_opening:'100.00',rows:[{id:3,date:'2026-10-30',direction:'in',status:'income_candidate',amount:'10.00'},{id:4,date:'2026-10-30',direction:'out',status:'auto_paid',amount:'20.00'}]};if(url.endsWith('/reprocess/commit'))return {posted:1,linked:0,review:0,full_reconciliation:true};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,categories:[{id:4,name:'Fee',type:'outcome'}],canPost:true});t.after(view.unmount);await settle();
 view.state.selectedOldBatch='7';await settle();await view.state.previewOldBatch();assert.match(text(view.container),/Поступление будет проведено/);assert.match(text(view.container),/Заявка оплачена/);assert.match(text(view.container),/Остаток на начало по выписке/);
 view.state.feeCategoryId='4';await settle();assert.equal(view.state.reprocessPreview,null);await view.state.commitOldBatch();assert.equal(calls.filter(([url])=>url.endsWith('/reprocess/commit')).length,0);
 await view.state.previewOldBatch();await view.state.commitOldBatch();
 assert.deepEqual(JSON.parse(calls.find(([url])=>url.endsWith('/reprocess/commit'))[1].body),{fee_category_id:4,preview_token:'snapshot-old',confirm:true});
 assert.match(text(view.container),/Повторная проверка завершена/);
});

test('reprocess access changes discard stale previews and load eligible statements when restored',async t=>{
 let resolveOld; let requests=0;
 const api=async url=>{
  if(url==='/api/bank-statements/settings')return {fee_category_id:null};
  if(url==='/api/bank-statements/reprocess/batches'){
   requests++; if(requests===1)return new Promise(resolve=>{resolveOld=resolve});
   return {items:[{id:8,filename:'current.xls',currency:'UZS',period_from:'2026-10-30',period_to:'2026-10-30'}]};
  }
  return {items:[]};
 };
 const view=mount(component,{api,companyId:1,accounts,canPost:true});t.after(view.unmount);await settle();
 assert.equal(requests,1);view.app._instance.props.canPost=false;await settle();
 resolveOld({items:[{id:7,filename:'stale.xls'}]});await settle();assert.equal(view.state.oldBatches.length,0);
 view.app._instance.props.canPost=true;await settle();assert.equal(requests,2);
 assert.equal(view.state.oldBatches[0].id,8);assert.match(text(view.container),/Повторная проверка загруженной выписки/);
});

test('a stale preview must be reloaded before attempting another commit',async t=>{
 let attempts=0;const api=async(url)=>{if(url==='/api/bank-statements/reviews')return {items:[]};if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.includes('/preview?'))return statement;if(url.endsWith('/commit')){attempts++;throw Object.assign(Error('changed'),{status:409})}throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload(event());view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();await view.state.commit();await view.state.commit();
 assert.equal(attempts,1);assert.equal(view.state.preview,null);assert.match(text(view.container),/Данные изменились/);
});

test('a delayed company setting cannot replace a category explicitly selected by the user',async t=>{
 let resolveSetting;const api=async url=>{if(url==='/api/bank-statements/reviews')return {items:[]};if(url==='/api/bank-statements/settings')return new Promise(resolve=>{resolveSetting=resolve});throw Error('unexpected')};
 const categories=[{id:4,name:'Fee 1',type:'outcome'},{id:5,name:'Fee 2',type:'outcome'}];
 const view=mount(component,{api,companyId:1,accounts,categories,canImport:true});t.after(view.unmount);await settle();
 view.state.feeCategoryId='4';view.state.feeTouched=true;resolveSetting({fee_category_id:5});await settle();
 assert.equal(view.state.feeCategoryId,'4');
});

test('an earlier company response cannot restore its fee article after a company switch',async t=>{
 const settings=[];const api=async url=>{if(url==='/api/bank-statements/reviews')return {items:[]};if(url==='/api/bank-statements/settings')return new Promise(resolve=>{settings.push(resolve)});throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,categories:[{id:4,name:'Fee A',type:'outcome'},{id:5,name:'Fee B',type:'outcome'}],canImport:true});t.after(view.unmount);await settle();
 view.app._instance.props.companyId=2;await settle();assert.equal(settings.length,2);settings[1]({fee_category_id:5});await settle();settings[0]({fee_category_id:4});await settle();
 assert.equal(view.state.feeCategoryId,'5');
});

test('review-only permissions allow assigning an unallocated receipt but not reprocessing',async t=>{
 const calls=[],row={id:71,date:'2026-10-30',amount:'15',currency:'UZS',account_name:'Bank',correspondent_name:'Synthetic',document_number:'doc',category_id:8,edit_token:'token'};
 const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/reviews'||url==='/api/bank-statements/outgoing-unallocated')return {items:[]};if(url==='/api/bank-statements/income-unallocated')return {items:[row]};if(url.endsWith('/income-category'))return {};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,categories:[{id:9,name:'Income',type:'income'}],canReview:true,canAllocate:true,canPost:false,canImport:false});t.after(view.unmount);await settle();
 assert.match(text(view.container),/Поступления без статьи/);assert.doesNotMatch(text(view.container),/Повторная проверка загруженной выписки/);
 view.state.allocationActions={71:{categoryId:'9',reason:'Bank entry checked'}};await view.state.assignIncome(row);
 assert.equal(calls.filter(([url])=>url.endsWith('/income-category')).length,1);assert.equal(calls.filter(([url])=>url.includes('/reprocess/')).length,0);
});

test('read-only ledger user cannot submit an income allocation even when method is called directly',async t=>{
 let writes=0;const api=async(url,options={})=>{if(options.method==='POST')writes++;return {items:[]}};
 const view=mount(component,{api,companyId:1,accounts,categories:[{id:9,name:'Income',type:'income'}],canReview:false,canAllocate:false,canPost:false});t.after(view.unmount);await settle();
 view.state.allocationActions={71:{categoryId:'9',reason:'Bank entry checked'}};await view.state.assignIncome({id:71,category_id:8,edit_token:'token'});
 assert.equal(writes,0);assert.doesNotMatch(text(view.container),/Поступления без статьи/);
});

test('duplicate resolution requires a previously posted candidate, token, reason and explicit confirmation',async t=>{
 const calls=[],row={id:88,status:'ambiguous_duplicate',date:'2026-10-30',direction:'in',amount:'10.00',currency:'UZS'};
 const previous={line_id:45,batch_id:3,date:'2026-10-30',document_number:'D1',account_name:'Synthetic bank',amount:'10.00',currency:'UZS',ledger_id:21,token:'candidate-token'};
 const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/reviews')return {items:[row]};if(url.endsWith('/duplicate-candidates'))return {items:[previous]};if(url.endsWith('/duplicate-resolve'))return {ok:true};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,canReview:true});t.after(view.unmount);await settle();await view.state.findDuplicateCandidates(row);
 view.state.duplicateActions[88].selected='45';view.state.duplicateActions[88].note='short';view.state.duplicateActions[88].confirm=true;await view.state.resolveDuplicate(row);assert.equal(calls.filter(([url])=>url.endsWith('/duplicate-resolve')).length,0);
 view.state.duplicateActions[88].note='Checked bank duplicate';view.state.duplicateActions[88].confirm=false;await view.state.resolveDuplicate(row);assert.equal(calls.filter(([url])=>url.endsWith('/duplicate-resolve')).length,0);
 view.state.duplicateActions[88].confirm=true;view.state.duplicateActions[88].items[0].token='';await view.state.resolveDuplicate(row);assert.equal(calls.filter(([url])=>url.endsWith('/duplicate-resolve')).length,0);view.state.duplicateActions[88].items[0].token='candidate-token';
 view.state.duplicateActions[88].confirm=true;await view.state.resolveDuplicate(row);const call=calls.find(([url])=>url.endsWith('/duplicate-resolve'));
 assert.deepEqual(JSON.parse(call[1].body),{prior_line_id:45,token:'candidate-token',reason:'Checked bank duplicate',confirm:true});
});

test('duplicate resolution instructions are available in Uzbek',async t=>{
 const previous=lang.value;setLang('uz');t.after(()=>setLang(previous));
 const row={id:88,status:'ambiguous_duplicate',date:'2026-10-30',direction:'out',amount:'10.00',currency:'UZS'};
 const api=async url=>url==='/api/bank-statements/reviews'?{items:[row]}:{items:[]};
 const view=mount(component,{api,companyId:1,accounts,canReview:true});t.after(view.unmount);await settle();
 assert.match(text(view.container),/Avval o‘tkazilgan operatsiyani topish/);await view.state.findDuplicateCandidates(row);await settle();
 assert.match(text(view.container),/takror tekshiruvda qoladi/i);
});

test('duplicate tools are never shown or executed without review permission',async t=>{
 const calls=[],row={id:88,status:'ambiguous_duplicate',date:'2026-10-30',direction:'out',amount:'10.00',currency:'UZS'};
 const api=async(url,options={})=>{calls.push([url,options]);return {items:[row]}};
 const view=mount(component,{api,companyId:1,accounts,canReview:false});t.after(view.unmount);await settle();
 assert.doesNotMatch(text(view.container),/Найти ранее проведённую операцию/);await view.state.findDuplicateCandidates(row);await view.state.resolveDuplicate(row);
 assert.deepEqual(calls.map(([url])=>url),['/api/bank-statements/reviews']);
});

test('admin existing-fact correction links duplicates and transfers without enabling payment or classification',async t=>{
 const calls=[],duplicate={id:88,status:'ambiguous_duplicate',date:'2026-10-30',direction:'out',amount:'10.00',currency:'UZS'},transfer={id:91,status:'review',date:'2026-10-30',direction:'out',amount:'10.00',currency:'UZS'};
 const prior={line_id:45,ledger_id:21,token:'duplicate-token'},existing={ledger_id:51,token:'transfer-token',from_account:'Bank A',to_account:'Bank B',currency:'UZS',amount:'10.00'};
 const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/reviews')return {items:[duplicate,transfer]};if(url.endsWith('/duplicate-candidates'))return {items:[prior]};if(url.endsWith('/transfer-candidates'))return {items:[existing]};if(url.endsWith('/duplicate-resolve')||url.endsWith('/transfer-link'))return {ok:true};throw Error('unexpected endpoint '+url)};
 const view=mount(component,{api,companyId:1,accounts,canLinkExistingBankFacts:true});t.after(view.unmount);await settle();
 assert.match(text(view.container),/Новая оплата не создаётся/);assert.match(text(view.container),/Найти ранее проведённую операцию/);assert.match(text(view.container),/Найти оформленный перевод/);assert.doesNotMatch(text(view.container),/Найти заявку для сопоставления|Подтвердить сопоставление/);
 await view.state.findDuplicateCandidates(duplicate);Object.assign(view.state.duplicateActions[88],{selected:'45',note:'Existing duplicate checked',confirm:true});await view.state.resolveDuplicate(duplicate);
 await view.state.findTransferCandidates(transfer);Object.assign(view.state.transferActions[91],{selected:'51',note:'Existing transfer checked',confirm:true});await view.state.linkTransfer(transfer);
 view.state.matchActions[91]={items:[{id:17,version:3}],selected:'17',note:'Must not pay request',confirm:true};view.state.reviewActions[91]={classification:'bank_fee',note:'Must not classify'};
 await view.state.findCandidates(transfer);await view.state.matchRequest(transfer);await view.state.saveReview(transfer);
 const posts=calls.filter(([,options])=>options.method==='POST');assert.deepEqual(posts.map(([url])=>url),['/api/bank-statements/lines/88/duplicate-resolve','/api/bank-statements/lines/91/transfer-link']);
 assert.deepEqual(JSON.parse(posts[0][1].body),{prior_line_id:45,token:'duplicate-token',reason:'Existing duplicate checked',confirm:true});assert.deepEqual(JSON.parse(posts[1][1].body),{ledger_id:51,token:'transfer-token',reason:'Existing transfer checked',confirm:true});
});

test('revoking existing-fact correction clears signed choices and discards an in-flight candidate response',async t=>{
 let finish;const calls=[],duplicate={id:88,status:'ambiguous_duplicate'},transfer={id:91,status:'review'};
 const api=async(url,options={})=>{calls.push([url,options]);if(url.endsWith('/duplicate-candidates'))return new Promise(resolve=>{finish=resolve});return {items:[]}};
 const view=mount(component,{api,companyId:1,accounts,canLinkExistingBankFacts:true});t.after(view.unmount);await settle();
 view.state.transferActions[91]={items:[{ledger_id:51,token:'old-token'}],selected:'51',note:'Previously checked transfer',confirm:true};
 const pending=view.state.findDuplicateCandidates(duplicate);await settle();view.app._instance.props.canLinkExistingBankFacts=false;await settle();
 assert.deepEqual(Object.keys(view.state.transferActions),[]);assert.deepEqual(Object.keys(view.state.duplicateActions),[]);
 finish({items:[{line_id:45,ledger_id:21,token:'late-token'}]});await pending;await settle();assert.equal(view.state.duplicateActions[88],undefined);assert.equal(view.state.reviewBusy,null);
 await view.state.resolveDuplicate(duplicate);await view.state.findTransferCandidates(transfer);await view.state.linkTransfer(transfer);assert.equal(calls.some(([,options])=>options.method==='POST'),false);assert.doesNotMatch(text(view.container),/Найти ранее проведённую операцию|Найти оформленный перевод/);
});

test('admin existing-fact correction explanation is translated into Uzbek',async t=>{
 const previous=lang.value;setLang('uz');t.after(()=>setLang(previous));
 const view=mount(component,{api:async()=>({items:[]}),companyId:1,accounts,canLinkExistingBankFacts:true});t.after(view.unmount);await settle();
 assert.match(text(view.container),/Administrator takror yoki o‘tkazmani avval qayd etilgan operatsiya bilan bog‘lashi mumkin/);assert.match(text(view.container),/Yangi to‘lov yaratilmaydi/);assert.doesNotMatch(text(view.container),/Новая оплата/);
});

test('a late duplicate-candidate response cannot leak across a company change',async t=>{
 let finish;const row={id:88,status:'ambiguous_duplicate'};
 const api=async(url)=>{if(url==='/api/bank-statements/reviews')return {items:[]};if(url.endsWith('/duplicate-candidates'))return new Promise(resolve=>{finish=resolve});throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,canReview:true});t.after(view.unmount);await settle();const pending=view.state.findDuplicateCandidates(row);await settle();
 view.app._instance.props.companyId=2;await settle();finish({items:[{line_id:45,ledger_id:21,token:'token'}]});await pending;await settle();
 assert.equal(view.state.duplicateActions[88],undefined);assert.equal(view.state.reviewBusy,null);
});

test('transfer link shows both accounts and requires the selected token, explanation and confirmation',async t=>{
 const calls=[],row={id:91,status:'review',date:'2026-10-30',direction:'out',amount:'10',currency:'UZS'};
 const candidate={ledger_id:51,date:'2026-10-30',amount:'10',currency:'UZS',from_account:'Bank A',to_account:'Bank B',reference:'D123',counterparty_account_confirmed:false,token:'transfer-token'};
 const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/reviews')return {items:[row]};if(url.endsWith('/transfer-candidates'))return {items:[candidate]};if(url.endsWith('/transfer-link'))return {ok:true};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,canReview:true});t.after(view.unmount);await settle();await view.state.findTransferCandidates(row);await settle();
 assert.match(text(view.container),/Bank A.*Bank B.*D123/);let action=view.state.transferActions[91];action.selected='51';await settle();assert.match(text(view.container),/Счёт другой стороны не подтверждён/);
 action.note='short';action.confirm=true;await view.state.linkTransfer(row);action.note='Checked bank transfer';action.confirm=false;await view.state.linkTransfer(row);action.confirm=true;action.items[0].token='';await view.state.linkTransfer(row);assert.equal(calls.filter(([url])=>url.endsWith('/transfer-link')).length,0);
 action.items[0].token='transfer-token';await view.state.linkTransfer(row);const call=calls.find(([url])=>url.endsWith('/transfer-link'));assert.deepEqual(JSON.parse(call[1].body),{ledger_id:51,token:'transfer-token',reason:'Checked bank transfer',confirm:true});
});

test('transfer candidate selection resets confirmation and displays confirmed counterparty correctly',async t=>{
 const row={id:92,status:'reviewed',date:'2026-10-30',direction:'in',amount:'10',currency:'UZS'};
 const candidate={ledger_id:52,date:'2026-10-30',amount:'10',currency:'UZS',from_account:'Bank A',to_account:'Bank B',reference:'D124',counterparty_account_confirmed:true,token:'token'};
 const api=async url=>url.endsWith('/transfer-candidates')?{items:[candidate]}:{items:[row]};
 const view=mount(component,{api,companyId:1,accounts,canReview:true});t.after(view.unmount);await settle();await view.state.findTransferCandidates(row);view.state.transferActions[92].selected='52';await settle();
 assert.match(text(view.container),/Счёт другой стороны подтверждён выпиской/);assert.doesNotMatch(text(view.container),/Счёт другой стороны не подтверждён/);
 view.state.transferActions[92].note='Previously confirmed';view.state.transferActions[92].confirm=true;view.state.resetSelectionConfirmation(view.state.transferActions,row);assert.equal(view.state.transferActions[92].note,'');assert.equal(view.state.transferActions[92].confirm,false);
});

test('transfer tools require review rights and eligible status',async t=>{
 const calls=[],row={id:93,status:'review',date:'2026-10-30',direction:'out',amount:'10',currency:'UZS'};const api=async url=>{calls.push(url);return {items:[row]}};
 const view=mount(component,{api,companyId:1,accounts,canReview:false});t.after(view.unmount);await settle();assert.doesNotMatch(text(view.container),/Найти оформленный перевод/);await view.state.findTransferCandidates(row);await view.state.linkTransfer(row);assert.deepEqual(calls,['/api/bank-statements/reviews']);
});

test('empty transfer candidates remain under review, and stale responses cannot cross companies',async t=>{
 let finish;const row={id:94,status:'review',date:'2026-10-30',direction:'out',amount:'10',currency:'UZS'};let delayed=false;
 const api=async url=>{if(url==='/api/bank-statements/reviews')return {items:[row]};if(url.endsWith('/transfer-candidates'))return delayed?new Promise(resolve=>{finish=resolve}):{items:[]};throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,accounts,canReview:true});t.after(view.unmount);await settle();await view.state.findTransferCandidates(row);await settle();assert.match(text(view.container),/Подходящий оформленный перевод не найден/);
 delayed=true;const pending=view.state.findTransferCandidates(row);await settle();view.app._instance.props.companyId=2;await settle();finish({items:[{ledger_id:52,token:'token'}]});await pending;await settle();assert.equal(view.state.transferActions[94],undefined);assert.equal(view.state.reviewBusy,null);
});

test('unreconciled bank accounts warn authorized staff, and zero or cash accounts do not',async t=>{
 const scoped=[{id:21,name:'Blocked bank',kind:'bank',currency:'UZS',unreconciled_statements:2},{id:22,name:'Cash',kind:'cash',currency:'UZS',unreconciled_statements:1},{id:23,name:'Clear bank',kind:'bank',currency:'UZS',unreconciled_statements:0}];
 const api=async()=>({items:[]});const view=mount(component,{api,companyId:1,accounts:scoped,canReview:true});t.after(view.unmount);await settle();
 const warning=find(view.container,node=>node.props?.role==='alert'&&text(node).includes('Несверенных выписок'));
 assert.ok(warning);assert.match(text(warning),/Ручная оплата заявок.*временно недоступна/);
 assert.deepEqual(all(warning,node=>node.tag==='li').map(text),['Blocked bank · UZS — Несверенных выписок: 2']);
 const readOnly=mount(component,{api,companyId:1,accounts:scoped});t.after(readOnly.unmount);await settle();assert.doesNotMatch(text(readOnly.container),/Несверенных выписок/);
});

test('overview warns on unreconciled accounts in selected currency and links to operations',async t=>{
 const api=async url=>url.startsWith('/api/accounts')||url.startsWith('/api/budgets')?[]:url.startsWith('/api/requests')||url.startsWith('/api/ledger')?{items:[],total:0}:{};let destination;
 const view=mount(overview,{api,companyId:1,currency:'UZS',today:'2026-10-30',has:p=>['view','ledger'].includes(p),canDecide:()=>false,onGo:value=>{destination=value}});t.after(view.unmount);await settle();
 view.state.s.activeAccounts=[{id:1,currency:'USD',unreconciled_statements:2},{id:2,currency:'UZS',unreconciled_statements:0}];await settle();assert.doesNotMatch(text(view.container),/Остаток сайта не сверен/);
 view.state.s.activeAccounts[1].unreconciled_statements=1;await settle();assert.match(text(view.container),/Остаток сайта не сверен с банком/);assert.match(text(view.container),/ручные оплаты.*временно заблокированы/);
 const button=find(view.container,node=>node.tag==='button'&&text(node).includes('Перейти к операциям'));assert.ok(button);button.props.onClick();assert.equal(destination,'ledger');
 view.state.s.activeAccounts[1].unreconciled_statements=0;await settle();assert.doesNotMatch(text(view.container),/Остаток сайта не сверен/);
});

test('only holding admin can review and delete a statement with an explicit reason and confirmation',async t=>{
 const calls=[];let changed=0;
 const batch={id:73,filename:'synthetic.xls',account_name:'Synthetic bank',currency:'UZS',period_from:'2026-10-30',period_to:'2026-10-30',operation_count:2,linked_count:1,delete_token:'snapshot-token'};
 let items=[batch];
 const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/admin/batches')return {items};if(url==='/api/bank-statements/73/delete-preview')return batch;if(url==='/api/bank-statements/73/delete'){items=[];return {ok:true}};return {items:[]}};
 const viewer=mount(component,{api,companyId:1,accounts,canDeleteStatements:false});t.after(viewer.unmount);await settle();
 assert.doesNotMatch(text(viewer.container),/Удаление выписки администратором/);await viewer.state.selectDeleteBatch(batch);await viewer.state.deleteStatement();assert.equal(calls.some(([url])=>url.includes('/admin/batches')||url.endsWith('/delete')),false);
 const view=mount(component,{api,companyId:1,accounts,canDeleteStatements:true,onChanged:()=>changed++});t.after(view.unmount);await settle();
 assert.match(text(view.container),/synthetic.xls/);assert.match(text(view.container),/Создано выпиской: 2/);assert.match(text(view.container),/Ранее существовало: 1/);
 await view.state.selectDeleteBatch(batch);view.state.deleteConfirmed=true;view.state.deleteReason='коротко';await view.state.deleteStatement();assert.equal(calls.filter(([url])=>url.endsWith('/delete')).length,0);
 view.state.deleteReason='Тестовое обоснование';view.state.deleteConfirmed=false;await view.state.deleteStatement();assert.equal(calls.filter(([url])=>url.endsWith('/delete')).length,0);
 view.state.deleteConfirmed=true;await view.state.deleteStatement();await settle();
 assert.deepEqual(JSON.parse(calls.find(([url])=>url.endsWith('/delete'))[1].body),{confirm:true,reason:'Тестовое обоснование',expected:'snapshot-token'});
 assert.equal(changed,1);assert.match(text(view.container),/Выписка удалена, её операции отменены/);assert.doesNotMatch(text(view.container),/synthetic.xls/);
});

test('statement deletion after an uncertain response is not repeated before a fresh read',async t=>{
 let attempts=0;const batch={id:73,filename:'synthetic.xls',account_name:'Synthetic',currency:'UZS',period_from:'2026-10-30',period_to:'2026-10-30',operation_count:1,linked_count:0,delete_token:'version'};
 const api=async(url)=>{if(url==='/api/bank-statements/admin/batches')return {items:[batch]};if(url.endsWith('/delete-preview'))return batch;if(url.endsWith('/delete')){attempts++;throw Error('network')};return {items:[]}};
 const view=mount(component,{api,companyId:1,accounts,canDeleteStatements:true});t.after(view.unmount);await settle();
 await view.state.selectDeleteBatch(batch);view.state.deleteReason='Тестовая причина удаления';view.state.deleteConfirmed=true;
 await view.state.deleteStatement();await view.state.deleteStatement();assert.equal(attempts,1);assert.match(text(view.container),/Ответ об удалении не получен/);
 await view.state.loadAdminBatches();assert.equal(view.state.deleteUncertain,false);assert.equal(view.state.deleteBatchId,null);
});

test('admin statement list and deletion completion are scoped to the current company',async t=>{
 let resolveFirst,resolveDelete;const calls=[];const batch={id:73,filename:'one.xls',account_name:'Synthetic',currency:'UZS',period_from:'2026-10-30',period_to:'2026-10-30',operation_count:1,linked_count:0,delete_token:'v'};
 let read=0,changed=0;const api=async(url)=>{calls.push(url);if(url==='/api/bank-statements/admin/batches'){read++;if(read===1)return new Promise(resolve=>resolveFirst=resolve);return {items:[{...batch,id:74,filename:'two.xls'}]}}if(url.endsWith('/delete-preview'))return {...batch,id:74,filename:'two.xls'};if(url.endsWith('/delete'))return new Promise(resolve=>resolveDelete=resolve);return {items:[]}};
 const view=mount(component,{api,companyId:1,accounts,canDeleteStatements:true,onChanged:()=>changed++});t.after(view.unmount);await settle();
 view.app._instance.props.companyId=2;await settle();resolveFirst({items:[batch]});await settle();assert.match(text(view.container),/two.xls/);assert.doesNotMatch(text(view.container),/one.xls/);
 await view.state.selectDeleteBatch({...batch,id:74,filename:'two.xls'});view.state.deleteReason='Подробная причина';view.state.deleteConfirmed=true;const pending=view.state.deleteStatement();await settle();view.app._instance.props.companyId=3;await settle();resolveDelete({ok:true});await pending;await settle();assert.equal(changed,0);assert.doesNotMatch(text(view.container),/Выписка удалена/);
});

test('statement deletion actions are translated to Uzbek',async t=>{
 const previous=lang.value;setLang('uz');t.after(()=>setLang(previous));
 const api=async url=>url==='/api/bank-statements/admin/batches'?{items:[]}:{items:[]};const view=mount(component,{api,companyId:1,accounts,canDeleteStatements:true});t.after(view.unmount);await settle();assert.match(text(view.container),/Administrator tomonidan bank ko‘chirmasini o‘chirish/);assert.match(text(view.container),/Yuklangan ko‘chirmalar yo‘q/);
});

test('revoking admin access discards statement deletion controls and prevents submission',async t=>{
 const calls=[];const batch={id:73,filename:'synthetic.xls',account_name:'Synthetic',currency:'UZS',period_from:'2026-10-30',period_to:'2026-10-30',operation_count:1,linked_count:0,delete_token:'version'};
 const api=async(url)=>{calls.push(url);return url==='/api/bank-statements/admin/batches'?{items:[batch]}:url.endsWith('/delete-preview')?batch:{items:[]}};
 const view=mount(component,{api,companyId:1,accounts,canDeleteStatements:true});t.after(view.unmount);await settle();await view.state.selectDeleteBatch(batch);view.state.deleteReason='Подробная причина';view.state.deleteConfirmed=true;
 view.app._instance.props.canDeleteStatements=false;await settle();assert.doesNotMatch(text(view.container),/Удаление выписки администратором/);await view.state.deleteStatement();assert.equal(calls.some(url=>url.endsWith('/delete')),false);
});

test('a server error during deletion requires a refreshed list before retry',async t=>{
 let attempts=0;const batch={id:73,filename:'synthetic.xls',status:'committed',account_name:'Synthetic',currency:'UZS',period_from:'2026-10-30',period_to:'2026-10-30',operation_count:1,linked_count:0,delete_token:'version'};
 const api=async url=>{if(url==='/api/bank-statements/admin/batches')return {items:[batch]};if(url.endsWith('/delete-preview'))return batch;if(url.endsWith('/delete')){attempts++;throw Object.assign(Error('server'),{status:500})}return {items:[]}};
 const view=mount(component,{api,companyId:1,accounts,canDeleteStatements:true});t.after(view.unmount);await settle();await view.state.selectDeleteBatch(batch);view.state.deleteReason='Подробная причина';view.state.deleteConfirmed=true;
 await view.state.deleteStatement();await view.state.deleteStatement();assert.equal(attempts,1);assert.equal(view.state.deleteUncertain,true);
});

test('late deletion preview cannot be applied to another company',async t=>{
 let finish;const batch={id:73,filename:'one.xls',status:'committed',account_name:'Synthetic',currency:'UZS',period_from:'2026-10-30',period_to:'2026-10-30',operation_count:1,linked_count:0};
 const api=async url=>url==='/api/bank-statements/admin/batches'?{items:[batch]}:url.endsWith('/delete-preview')?new Promise(resolve=>finish=resolve):{items:[]};
 const view=mount(component,{api,companyId:1,accounts,canDeleteStatements:true});t.after(view.unmount);await settle();const pending=view.state.selectDeleteBatch(batch);await settle();view.app._instance.props.companyId=2;await settle();finish({...batch,delete_token:'old-company'});await pending;await settle();assert.equal(view.state.deletePreview,null);assert.equal(view.state.deleteBatchId,null);assert.doesNotMatch(text(view.container),/Подтвердите удаление выписки/);
});
