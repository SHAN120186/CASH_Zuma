import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text,find,all} from './helpers/mountVue.js';
import {lang,setLang} from '../src/i18n/index.js';

const component=await compileComponent(new URL('../src/BankStatementResolution.vue',import.meta.url));
const bank=await compileComponent(new URL('../src/BankStatements.vue',import.meta.url),{'./BankStatementResolution.vue':component});
const source={id:31,batch_id:7,date:'2026-11-03',amount:'50.00',currency:'UZS',direction:'out',status:'review',account_id:21,document_number:'SYN-31',correspondent_name:'Synthetic company',purpose:'Synthetic bank evidence'};
const account={id:21,name:'Synthetic bank',currency:'UZS'},destination={id:22,name:'Synthetic own cash',currency:'UZS',correspondence_confirmed:false};
const reason='Confirmed using separate bank documents';
const cases={
 'historical-transfer':{target:'22',action:'Учесть выполненный перевод',status:'historical_transfer_posted',options:{line:{...source},source_account:account,accounts:[destination]},preview:{line_id:31,batch_id:7,source_account:account,destination_account:destination,operation:{kind:'transfer',date:source.date,amount:'50.00',currency:'UZS',reference:'SYN',correspondence_confirmed:false},reconciliation:{bank_closing:'100.00',projected_closing:'100.00',projected_pending_debit_total:'0.00'},preview_token:'signed-transfer'}},
 'historical-request':{target:'45',action:'Учесть историческую оплату',status:'historical_request_paid',options:{available:true,reason:'',items:[{request_id:45,number:'CF-00045',counterparty:'Synthetic supplier',counterparty_inn:'123',amount:'50.00',currency:'UZS',date:'2026-10-01',status:'cancelled',version:3}]},preview:{line_id:31,request_id:45,amount:'50.00',currency:'UZS',date:source.date,counterparty:'Synthetic supplier',category_name:'Synthetic expense',previous_status:'cancelled',effect:'historical_request_payment',token:'signed-request'}},
 'distinct-income':{target:'32',action:'Учесть отдельное поступление',status:'manual_distinct_income',options:{available:true,reason:'',items:[{line_id:32,document_number:'SYN-31',date:source.date,amount:'75.00',currency:'UZS',counterparty:'Synthetic buyer',purpose:'Separate synthetic bank receipt',status:'ambiguous_duplicate'}]},preview:{line_id:31,distinct_from_line_id:32,amount:'50.00',currency:'UZS',date:source.date,counterparty:'Synthetic buyer',category_name:'Synthetic income',effect:'distinct_income',token:'signed-income'}},
};
const button=(view,label)=>find(view.container,node=>node.tag==='button'&&text(node)===label);
const set=(node,value)=>{assert.ok(node);node.props['onUpdate:modelValue'](value);};
function fixture(t,scenario,handle,props={}){
 const calls=[],changed=[],working=[];const data=cases[scenario],row={...source,direction:scenario==='distinct-income'?'in':'out',status:scenario==='distinct-income'?'ambiguous_duplicate':'review'};
 const api=async(url,options={})=>{calls.push([url,options]);if(handle)return handle(url,options);if(url.endsWith('/options'))return data.options;if(url.endsWith('/preview'))return data.preview;if(url.endsWith('/commit'))return {line_id:31,ledger_id:90,status:data.status};throw Error('unexpected');};
 const view=mount(component,{api,companyId:1,row,canResolve:true,onChanged:value=>changed.push(value),onWorking:value=>working.push(value),...props});t.after(view.unmount);
 return {view,calls,changed,working,data};
}
async function open(view,scenario){
 await button(view,'Разобрать спорную строку').props.onClick();await settle();
 if(scenario!=='distinct-income')set(find(view.container,node=>node.tag==='select'&&node.children.some(option=>option.props?.value===scenario)),scenario);
 await settle();
}
async function prepare(view,scenario){
 await open(view,scenario);set(find(view.container,node=>node.tag==='select'&&node.children.some(option=>option.props?.value===cases[scenario].target)),cases[scenario].target);
 set(find(view.container,node=>node.tag==='textarea'),reason);set(find(view.container,node=>node.tag==='input'&&node.props.type==='checkbox'),true);await settle();
 await button(view,'Проверить решение').props.onClick();await settle();
}

test('ordinary accountants cannot see or invoke admin exception actions',async t=>{
 const {view,calls}=fixture(t,'historical-transfer',null,{canResolve:false});await settle();assert.equal(text(view.container),'');await view.state.toggle();await view.state.loadOptions();await view.state.check();await view.state.commit();assert.deepEqual(calls,[]);
});

for(const scenario of Object.keys(cases))test(`${scenario} uses a lazy read, explicit bank evidence, signed preview and a separate final confirmation`,async t=>{
 const {view,calls,changed,data}=fixture(t,scenario);await settle();assert.deepEqual(calls,[]);
 await open(view,scenario);assert.equal(calls.length,1);assert.equal(calls[0][1].method,undefined);assert.match(calls[0][0],new RegExp(`/${scenario}/options$`));assert.equal(button(view,'Проверить решение').props.disabled,true);
 set(find(view.container,node=>node.tag==='select'&&node.children.some(option=>option.props?.value===data.target)),data.target);set(find(view.container,node=>node.tag==='textarea'),reason);await settle();assert.equal(button(view,'Проверить решение').props.disabled,true);
 set(find(view.container,node=>node.tag==='input'&&node.props.type==='checkbox'),true);await settle();await button(view,'Проверить решение').props.onClick();await settle();assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,0);
 const action=button(view,data.action);assert.ok(action);assert.equal(action.props.disabled,true);await action.props.onClick();assert.equal(changed.length,0);
 const confirmations=all(view.container,node=>node.tag==='input'&&node.props.type==='checkbox');set(confirmations[1],true);await settle();await action.props.onClick();await settle();
 assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,1);assert.equal(changed.length,1);assert.match(text(view.container),/Учёт банковского факта сохранён/);assert.match(text(view.container),/Новая платёжная команда в банк не отправляется/);
 const body=JSON.parse(calls.find(([url])=>url.endsWith('/commit'))[1].body);
 if(scenario==='historical-transfer')assert.deepEqual(body,{reason,to_account_id:22,preview_token:'signed-transfer',confirm_bank_fact:true,confirm_ownership:true});
 else assert.deepEqual(body,{reason,[scenario==='historical-request'?'request_id':'distinct_from_line_id']:Number(data.target),attest_bank_fact:true,token:data.preview.token,confirm:true});
});

test('changing reason, selected record or attestation invalidates the signature and resets the final checkbox',async t=>{
 const {view,calls}=fixture(t,'historical-transfer');await prepare(view,'historical-transfer');set(all(view.container,node=>node.tag==='input'&&node.props.type==='checkbox')[1],true);await settle();
 set(find(view.container,node=>node.tag==='textarea'),'Updated bank document evidence');await settle();assert.equal(button(view,'Учесть выполненный перевод'),undefined);assert.equal(view.state.confirm,false);await view.state.commit();assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,0);
 await button(view,'Проверить решение').props.onClick();await settle();set(find(view.container,node=>node.tag==='input'&&node.props.type==='checkbox'),false);await settle();assert.equal(view.state.preview,null);assert.equal(button(view,'Проверить решение').props.disabled,true);
});

test('rapid double click sends one signed financial request and a conflict never claims success',async t=>{
 let finish;const scenario='historical-request',data=cases[scenario];
 const {view,calls,changed}=fixture(t,scenario,async url=>url.endsWith('/options')?data.options:url.endsWith('/preview')?data.preview:new Promise(resolve=>finish=resolve));await prepare(view,scenario);set(all(view.container,node=>node.tag==='input'&&node.props.type==='checkbox')[1],true);await settle();
 const action=button(view,data.action),first=action.props.onClick(),second=action.props.onClick();await settle();assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,1);finish({line_id:31,ledger_id:90,status:data.status});await Promise.all([first,second]);await settle();assert.equal(changed.length,1);
 const denied=fixture(t,scenario,async url=>{if(url.endsWith('/options'))return data.options;if(url.endsWith('/preview'))return data.preview;throw Object.assign(Error('Synthetic stale source'),{status:409});});await prepare(denied.view,scenario);set(all(denied.view.container,node=>node.tag==='input'&&node.props.type==='checkbox')[1],true);await settle();await button(denied.view,data.action).props.onClick();await settle();assert.match(text(denied.view.container),/Synthetic stale source/);assert.doesNotMatch(text(denied.view.container),/Учёт банковского факта сохранён/);assert.equal(denied.changed.length,0);assert.equal(denied.view.state.preview,null);
});

test('late options and previews cannot cross company or access changes',async t=>{
 const data=cases['historical-transfer'];let finish;let delay='options';
 const {view,changed}=fixture(t,'historical-transfer',async url=>url.endsWith(`/${delay}`)?new Promise(resolve=>finish=resolve):url.endsWith('/options')?data.options:data.preview);
 await open(view,'historical-transfer');assert.equal(view.state.busy,true);view.app._instance.props.companyId=2;await settle();finish(data.options);await settle();assert.equal(view.state.options,null);assert.equal(view.state.open,false);assert.equal(view.state.busy,false);
 delay='preview';await open(view,'historical-transfer');set(find(view.container,node=>node.tag==='select'&&node.children.some(option=>option.props?.value==='22')),'22');set(find(view.container,node=>node.tag==='textarea'),reason);set(find(view.container,node=>node.tag==='input'&&node.props.type==='checkbox'),true);await settle();const pending=button(view,'Проверить решение').props.onClick();await settle();
 view.app._instance.props.canResolve=false;await settle();finish(data.preview);await pending;await settle();assert.equal(view.state.preview,null);assert.equal(text(view.container),'');assert.equal(changed.length,0);
});

test('an edited input or replaced source row cannot restore an earlier signed preview',async t=>{
 const data=cases['historical-request'];let finish;
 const {view,calls}=fixture(t,'historical-request',async url=>url.endsWith('/options')?data.options:new Promise(resolve=>finish=resolve));
 await open(view,'historical-request');set(find(view.container,node=>node.tag==='select'&&node.children.some(option=>option.props?.value==='45')),'45');set(find(view.container,node=>node.tag==='textarea'),reason);set(find(view.container,node=>node.tag==='input'&&node.props.type==='checkbox'),true);await settle();
 let pending=button(view,'Проверить решение').props.onClick();await settle();set(find(view.container,node=>node.tag==='textarea'),'Changed evidence while a reply was pending');finish(data.preview);await pending;await settle();assert.equal(view.state.preview,null);assert.equal(view.state.busy,false);
 pending=button(view,'Проверить решение').props.onClick();await settle();view.app._instance.props.row={...source,id:33};await settle();finish(data.preview);await pending;assert.equal(view.state.preview,null);assert.equal(view.state.open,false);assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,0);
});

test('foreign-currency options and a self peer cannot become selectable resolution records',async t=>{
 const transfer=fixture(t,'historical-transfer',async()=>({...cases['historical-transfer'].options,accounts:[{...destination,currency:'USD'}]}));await open(transfer.view,'historical-transfer');assert.equal(transfer.view.state.options,null);assert.match(text(transfer.view.container),/Не удалось получить варианты решения/);
 const income=fixture(t,'distinct-income',async()=>({items:[{...cases['distinct-income'].options.items[0],line_id:31}],available:true}));await open(income.view,'distinct-income');assert.equal(income.view.state.options,null);assert.equal(income.calls.filter(([,options])=>options.method==='POST').length,0);
});

test('wrong signed amount or selected record is rejected before confirmation',async t=>{
 const data=cases['historical-request'];const {view,calls}=fixture(t,'historical-request',async url=>url.endsWith('/options')?data.options:{...data.preview,amount:'999.00'});await prepare(view,'historical-request');assert.equal(view.state.preview,null);assert.equal(button(view,data.action),undefined);assert.match(text(view.container),/Не удалось проверить выбранное решение/);assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,0);
});

test('lost accepted response is checked through saved source without sending a second financial command',async t=>{
 const data=cases['distinct-income'];let accepted=false;
 const {view,calls,changed}=fixture(t,'distinct-income',async url=>{if(url.endsWith('/options'))return data.options;if(url.endsWith('/preview'))return data.preview;if(url.endsWith('/commit')){accepted=true;throw Error('lost reply');}assert.equal(url,'/api/bank-statements/7');return {id:7,rows:[{...source,status:data.status,ledger_id:90}]};});
 await prepare(view,'distinct-income');set(all(view.container,node=>node.tag==='input'&&node.props.type==='checkbox')[1],true);await settle();await button(view,data.action).props.onClick();await settle();assert.equal(accepted,true);assert.equal(view.state.uncertain,true);assert.equal(changed.length,0);await view.state.commit();assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,1);
 await button(view,'Проверить результат учёта').props.onClick();await settle();assert.equal(changed.length,1);assert.equal(view.state.uncertain,false);assert.match(text(view.container),/подтверждён сохранённой строкой/);assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,1);
});

test('a verified still-pending source clears uncertainty but requires a fresh preview and both confirmations',async t=>{
 const data=cases['historical-transfer'];const {view,calls,changed}=fixture(t,'historical-transfer',async url=>{if(url.endsWith('/options'))return data.options;if(url.endsWith('/preview'))return data.preview;if(url.endsWith('/commit'))throw Error('lost reply');return {id:7,rows:[source]};});await prepare(view,'historical-transfer');set(all(view.container,node=>node.tag==='input'&&node.props.type==='checkbox')[1],true);await settle();await button(view,data.action).props.onClick();await settle();await button(view,'Проверить результат учёта').props.onClick();await settle();assert.equal(view.state.preview,null);assert.equal(view.state.uncertain,false);assert.equal(view.state.attest,false);assert.match(text(view.container),/Строка остаётся на проверке/);assert.equal(changed.length,0);assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,1);
});

test('admin row resolution refreshes the pending source and history key and emits the existing fact refresh once',async t=>{
 let accepted=false,changed=0;const calls=[],data=cases['historical-transfer'];
 const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/reviews')return {items:accepted?[]:[source]};if(url.endsWith('/options'))return data.options;if(url.endsWith('/preview'))return data.preview;if(url.endsWith('/commit')){accepted=true;return {line_id:31,ledger_id:90,status:data.status};}return {items:[]};};
 const view=mount(bank,{api,companyId:1,accounts:[{...account,kind:'bank'}],canReview:true,canResolveExceptions:true,onChanged:()=>changed++});t.after(view.unmount);await settle();await prepare(view,'historical-transfer');set(all(view.container,node=>node.tag==='input'&&node.props.type==='checkbox')[1],true);await settle();await button(view,data.action).props.onClick();await settle();assert.equal(changed,1);assert.equal(view.state.reviews.length,0);assert.equal(view.state.savedRefresh,1);assert.match(text(view.container),/Учёт банковского факта сохранён/);
});

test('resolution wording and bank evidence acknowledgements are available in Uzbek',async t=>{
 const prior=lang.value;setLang('uz');t.after(()=>setLang(prior));const {view}=fixture(t,'distinct-income');await button(view,'Bahsli satrni ko‘rib chiqish').props.onClick();await settle();assert.match(text(view.container),/Bankka yangi to‘lov buyrug‘i yuborilmaydi/);assert.match(text(view.container),/alohida amalda olingan tushum/);assert.match(text(view.container),/Yechimni tekshirish/);
});
