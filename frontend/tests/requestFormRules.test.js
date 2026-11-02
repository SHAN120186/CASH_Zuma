import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,all,text} from './helpers/mountVue.js';
import {DEPARTMENTS,requiredDocs} from '../src/requests/workflow.js';
import {requestBody,missingRequired,saveRequest,createSaveState} from '../src/requests/saveFlow.js';
import {setLang,lang} from '../src/i18n/index.js';
const editor=await compileComponent(new URL('../src/requests/RequestEditor.vue',import.meta.url));
const dues={normal:'2026-11-03',high:'2026-10-28',urgent:'2026-10-24'};
const boot={accounts:[{id:1,kind:'bank',currency:'UZS',name:'Synthetic bank'},{id:2,kind:'cash',currency:'UZS',name:'Synthetic cash'}],categories:[{id:3,type:'outcome',name:'Synthetic cost'}],due_minimums:dues};
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function form(t,options={}){
 const previous=globalThis.document;
 globalThis.document={activeElement:null,addEventListener(){},removeEventListener(){},querySelector(){return null;}};
 const view=mount(editor,{boot,company:{id:2,name:'Synthetic company'},money:v=>v,companyUrl:u=>u,api:async()=>({due_minimums:dues}),...options});
 t.after(()=>{view.unmount();globalThis.document=previous;});return view;
}

test('priority selection always replaces the auto date in both directions',async t=>{
 const view=form(t);await settle();assert.equal(view.state.form.date,dues.normal);
 for(const priority of ['high','urgent','normal']){view.state.form.priority=priority;await settle();assert.equal(view.state.form.date,dues[priority]);}
});

test('fresh working-day rules replace a stale auto date even when the new date is earlier',async t=>{
 const view=form(t,{boot:{...boot,due_minimums:{...dues,normal:'2026-11-04'}}});await settle();
 assert.equal(view.state.form.date,dues.normal);
});

test('a late rule response preserves the later date the user entered manually',async t=>{
 let resolve;const view=form(t,{api:()=>new Promise(r=>resolve=r)});await settle();
 view.state.form.date='2026-11-30';resolve({due_minimums:dues});await settle();assert.equal(view.state.form.date,'2026-11-30');
});

test('opening an existing request preserves its date and department until the priority changes',async t=>{
 const request={id:7,version:1,status:'draft',priority:'normal',date:'2026-11-30',channel:'bank',currency:'UZS',account_id:1,category_id:3,department:'warehouse',documents_list:[]};
 const view=form(t,{request});await settle();assert.equal(view.state.form.date,'2026-11-30');assert.equal(view.state.form.department,'warehouse');
 view.state.form.priority='high';await settle();assert.equal(view.state.form.date,dues.high);
});

test('cash keeps the indent mandatory and the other two blocks optional without losing selected files',async t=>{
 const view=form(t);await settle();assert.deepEqual(view.state.missing,['internal','contract']);
 view.state.saved=[{id:1,kind:'internal',current:true},{id:2,kind:'contract',current:true}];
 view.state.form.channel='cash';await settle();assert.equal(view.state.form.account_id,2);assert.deepEqual(view.state.required,['internal']);assert.deepEqual(view.state.missing,[]);
 assert.equal(all(view.container,n=>String(n.props?.class).includes('required-mark')).length,1);
 assert.equal(view.state.saved.length,2,'switching channel never deletes a document');
 view.state.saved=[{id:1,kind:'internal',current:true}];view.state.form.channel='bank';await settle();assert.equal(view.state.form.account_id,1);assert.deepEqual(view.state.missing,['contract']);
 assert.equal(all(view.container,n=>String(n.props?.class).includes('required-mark')).length,2);
});

test('all seven departments appear in Russian and Uzbek and the code is sent with the form',async t=>{
 const previous=lang.value;t.after(()=>setLang(previous));setLang('ru');const view=form(t);await settle();
 assert.deepEqual(Object.keys(DEPARTMENTS),['qc','ml','engineering','warehouse','production','office','facilities']);
 assert.match(text(view.container),/Инженерный отдел \(ИТО\)/);assert.match(text(view.container),/Хозяйственная часть/);
 setLang('uz');await settle();assert.match(text(view.container),/Ombor/);assert.match(text(view.container),/Ishlab chiqarish/);
 assert.match(text(view.container),/QC/);assert.match(text(view.container),/ML/);
 view.state.form.department='warehouse';assert.equal(requestBody(view.state.form,2).department,'warehouse');
});

test('save flow sends a cash request with its indent and refuses a bank request without its contract',async()=>{
 const fields={account_id:2,channel:'cash',currency:'UZS',category_id:3,amount:'100',counterparty:'Synthetic',purpose:'Synthetic expense',priority:'urgent',date:dues.urgent,department:'office'};
 const saved=[{id:1,kind:'internal',current:true}],calls=[];
 let request={id:7,version:1,status:'draft',number:'Synthetic',documents_list:saved};
 const api=async(url,options={})=>{calls.push([url,options.method||'GET']);if(options.method==='PUT')request={...request,...JSON.parse(options.body),version:2};if(url.endsWith('/decision'))request={...request,status:'pending',version:3};return request;};
 const result=await saveRequest(api,createSaveState(request),{fields,saved,submit:true});assert.equal(result.ok,true);assert.ok(calls.some(([url])=>url.endsWith('/decision')));
 calls.length=0;const bank=await saveRequest(api,createSaveState({...request,status:'draft'}),{fields:{...fields,account_id:1,channel:'bank'},saved,submit:true});assert.equal(bank.code,'missing_documents');assert.equal(calls.length,0);
 assert.deepEqual(requiredDocs('cash'),['internal']);assert.deepEqual(missingRequired(saved,[],'bank'),['contract']);
});

test('custom company departments use fresh rules and retain an archived selection',async t=>{
 const previous=lang.value;t.after(()=>setLang(previous));setLang('ru');
 const items=[{code:'dept_custom',name_ru:'Новый отдел',name_uz:'Yangi bolim',active:true},{code:'qc',name_ru:'Old QC',name_uz:'Old QC',active:false}];
 const view=form(t,{api:async()=>({due_minimums:dues,department_catalog:{items}})});await settle();
 assert.deepEqual(view.state.departmentOptions.map(x=>x.code),['dept_custom']);assert.match(text(view.container),/Новый отдел/);
 setLang('uz');await settle();assert.match(text(view.container),/Yangi bolim/);
 view.state.form.department='qc';await settle();assert.deepEqual(view.state.departmentOptions.map(x=>x.code),['dept_custom','qc']);
});

test('ИНН accepts short identifiers, exact match fills company, edited ИНН clears selected name',async t=>{
 const calls=[];const view=form(t,{api:async url=>{calls.push(url);return url.startsWith('/api/request-counterparties')?{items:[{id:12,name:'Example supplier',inn:'123456789'}]}:{due_minimums:dues};}});
 await settle();view.state.partyInn='x';await settle();await delay(320);assert.equal(calls.filter(x=>x.includes('request-counterparties')).length,0);
 view.state.partyInn='3';await settle();await delay(320);await settle();assert.equal(calls.filter(x=>x.includes('request-counterparties')).length,1);
 view.state.partyInn='123456789';await settle();await delay(320);await settle();
 assert.equal(view.state.form.counterparty,'Example supplier');assert.equal(view.state.partyStatus,'selected');
 assert.equal(calls.filter(x=>x.includes('request-counterparties')).length,2);
 assert.equal(requestBody({...view.state.form,counterparty_inn:view.state.partyInn},2).counterparty_inn,'123456789');
 view.state.partyInn='987';await settle();assert.equal(view.state.form.counterparty,'');
});

test('ИНН partial matches require choosing and obsolete responses cannot overwrite current input',async t=>{
 const pending=[];const view=form(t,{api:url=>url.startsWith('/api/request-counterparties')?new Promise(resolve=>pending.push({url,resolve})):Promise.resolve({due_minimums:dues})});
 await settle();view.state.partyInn='123';await settle();await delay(320);
 view.state.partyInn='456';await settle();await delay(320);assert.equal(pending.length,2);
 pending[0].resolve({items:[{id:1,name:'Old supplier',inn:'123'}]});await settle();assert.equal(view.state.form.counterparty,'');
 pending[1].resolve({items:[{id:2,name:'Supplier A',inn:'456000001'},{id:3,name:'Supplier B',inn:'456000002'}]});await settle();
 assert.equal(view.state.partyMatches.length,2);assert.equal(view.state.form.counterparty,'');
 view.state.chooseParty(view.state.partyMatches[1]);await settle();assert.equal(view.state.form.counterparty,'Supplier B');assert.equal(view.state.partyInn,'456000002');
});

test('existing name survives opening editor and no-match/error permits manual entry',async t=>{
 const request={id:7,version:1,status:'draft',counterparty:'Historical supplier',priority:'normal',date:'2026-11-30',channel:'bank',currency:'UZS',account_id:1,category_id:3,documents_list:[]};
 const view=form(t,{request,api:async url=>url.startsWith('/api/request-counterparties')?{items:[]}:{due_minimums:dues}});await settle();
 assert.equal(view.state.form.counterparty,'Historical supplier');view.state.partyInn='999';await settle();await delay(320);await settle();
 assert.equal(view.state.partyStatus,'empty');assert.equal(view.state.form.counterparty,'Historical supplier');assert.match(text(view.container),/контрагент не найден/i);
});

test('late exact ИНН result does not overwrite a manually edited name',async t=>{
 let resolve;const view=form(t,{api:url=>url.startsWith('/api/request-counterparties')?new Promise(r=>resolve=r):Promise.resolve({due_minimums:dues})});
 await settle();view.state.partyInn='123456789';await settle();await delay(320);
 view.state.form.counterparty='Manually entered supplier';await settle();
 resolve({items:[{id:4,name:'Supplier from directory',inn:'123456789'}]});await settle();
 assert.equal(view.state.form.counterparty,'Manually entered supplier');assert.equal(view.state.partyMatches.length,1);
 view.state.chooseParty(view.state.partyMatches[0]);await settle();assert.equal(view.state.form.counterparty,'Supplier from directory');
});

test('request editing retains stored ИНН and manual change of company name clears it',async t=>{
 const request={id:7,version:1,status:'draft',counterparty:'Saved supplier',counterparty_inn:'009876543',priority:'normal',date:'2026-11-30',channel:'bank',currency:'UZS',account_id:1,category_id:3,documents_list:[]};
 const view=form(t,{request});await settle();assert.equal(view.state.partyInn,'009876543');
 view.state.form.counterparty='Changed supplier';view.state.manualPartyChanged();await settle();assert.equal(view.state.partyInn,'');
});
