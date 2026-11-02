import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text,all} from './helpers/mountVue.js';
import {lang,setLang} from '../src/i18n/index.js';

const component=await compileComponent(new URL('../src/BankStatements.vue',import.meta.url));
const daily=await compileComponent(new URL('../src/overview/BankStatementDaily.vue',import.meta.url));
const accounts=[{id:21,name:'Synthetic bank',kind:'bank',currency:'UZS'}, {id:22,name:'Synthetic cash',kind:'cash',currency:'UZS'}];
const statement={id:7,status:'preview',account:{id:21,name:'Synthetic bank',currency:'UZS'},statement:{account_number:'TEST-BANK-21',ready:true,period_from:'2026-10-30',period_to:'2026-10-30',opening_balance:'100.00',closing_balance:'90.00',credit_total:'10.00',debit_total:'20.00',balance_matches:true,declared_totals_match:true},rows:[{id:1,date:'2026-10-30',document_number:'D1',correspondent_name:'Synthetic',correspondent_inn:'123456789',purpose:'Synthetic payment',direction:'out',amount:'20.00',status:'auto_pay_candidate',request_id:9}]};
const file={name:'statement.xls',size:100};
function event(f=file){return {target:{files:[f],value:'file'}}}

test('select account and currency explicitly, inspect bank account then confirm each before commit',async t=>{
 const calls=[];
 const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/reviews')return {items:[]};if(url.includes('/preview?'))return statement;if(url.endsWith('/commit'))return {auto_paid:1,linked_paid:0,review:0};throw Error('unexpected')};
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
 assert.deepEqual(JSON.parse(call[1].body),{confirm_account:true,confirm_currency:true});
 assert.match(text(view.container),/Автоматически оплачено: 1/);assert.equal(view.state.preview,null);
});

test('a failed balance or unexpected scope cannot be confirmed, and no automatic action occurs on upload',async t=>{
 let commits=0;
 const api=async(url)=>{if(url==='/api/bank-statements/reviews')return {items:[]};if(url.includes('/preview?'))return {...statement,statement:{...statement.statement,balance_matches:false}};commits++;return {}};
 const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();
 view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload(event());
 view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();assert.equal(view.state.ready,false);await view.state.commit();assert.equal(commits,0);
 assert.match(text(view.container),/не подтверждены/);
});

test('uncertain commit result blocks duplicate submission and old company replies are ignored',async t=>{
 let commits=0,resolvePreview;
 const api=async(url)=>{if(url==='/api/bank-statements/reviews')return {items:[]};if(url.includes('/preview?'))return new Promise(resolve=>resolvePreview=resolve);commits++;throw Error('network')};
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
 assert.match(text(view.container),/не добавляются автоматически в остаток и Cash Flow/);
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
