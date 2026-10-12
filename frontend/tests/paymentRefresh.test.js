import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text,all} from './helpers/mountVue.js';

const component=await compileComponent(new URL('../src/App.vue',import.meta.url));
const reply=data=>({ok:true,status:200,json:async()=>data});
const denied=(status,message)=>({ok:false,status,json:async()=>({detail:message})});
const day=()=>new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Tashkent'});
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};
const paymentWarning='Оплата записана. Не удалось обновить данные. Нажмите «Обновить данные», оплату повторять не нужно.';
const request={id:41,version:6,status:'approved',channel:'bank',account_kind:'bank',creator_id:2,last_editor_id:2,
 checked_by:3,finance_approved_by:4,approved_by:5,account_id:7,category_id:8,amount:'500.00',
 date:'2026-10-12',approved_on:'2026-10-12',counterparty:'Synthetic supplier',purpose:'Synthetic expense',
 number:'CF-00041',currency:'UZS',actions:['pay']};
const user={id:9,role:'accountant',permissions:['view','ledger','pay_bank']};
const boot=()=>({today:day(),csrf:'synthetic',user,
 accounts:[{id:7,name:'Synthetic bank',currency:'UZS',kind:'bank'}],
 categories:[{id:8,name:'Synthetic expense',type:'outcome'}],
 companies:[{id:1,name:'Synthetic Zuma',code:'ZUMA'},{id:2,name:'Other synthetic company',code:'UZGERMED'}],roles:{}});

async function fixture(t,handle){
 const keys=['window','location','document','fetch','Document','ShadowRoot'];
 const originals=keys.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]);
 const calls=[];
 globalThis.window={addEventListener(){},localStorage:{getItem(){return null;}}};
 globalThis.location={hash:'#requests'};
 globalThis.document={hidden:true,activeElement:null,addEventListener(){},removeEventListener(){},querySelector(){return {scrollIntoView(){}};}};
 globalThis.fetch=async(url,options={})=>{
  if(url==='/api/me')return denied(401,'Synthetic unauthenticated startup');
  calls.push({url,method:options.method||'GET',body:options.body?JSON.parse(options.body):null});
  return handle(url,options);
 };
 const view=mount(component);
 t.after(()=>{view.unmount();for(const [key,descriptor] of originals){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}});
 await settle();
 Object.assign(view.state,{user,boot:boot(),companyId:1,companyReady:true,page:'requests',currency:'UZS',bootstrapLocalDay:day(),rows:[{...request}],listTotal:1});
 await settle();
 view.state.cardPay(request);view.state.form.reference='SYN-PAY-41';await settle();
 return {view,calls};
}
const posted=calls=>calls.filter(call=>call.method==='POST'&&call.url==='/api/ledger');
const visibleErrors=view=>all(view.container,node=>node.tag==='p'&&String(node.props.class||'').split(' ').includes('error')).map(text);

test('linked payment completes once and disappears from the approved filter',async t=>{
 let paid=false;
 const {view,calls}=await fixture(t,async(url,options)=>{
  if(url==='/api/ledger'){const body=JSON.parse(options.body);assert.equal(body.request_id,41);assert.equal(body.request_version,6);paid=true;return reply({id:83});}
  if(url==='/api/bootstrap')return reply(boot());
  assert.match(url,/^\/api\/requests\?/);
  const state=new URL(url,'https://isolated.test').searchParams.get('state');
  const row={...request,status:paid?'paid':'approved',actions:paid?[]:['pay']};
  return reply({items:!state||state===row.status?[row]:[],total:!state||state===row.status?1:0});
 });
 view.state.requestStatus='approved';
 await view.state.save();await settle();
 assert.equal(view.state.modal,null);assert.equal(view.state.rows.length,0);assert.equal(view.state.listTotal,0);
 assert.equal(view.state.notice,'Заявка оплачена.');assert.equal(view.state.error,'');
 view.state.requestStatus='';await view.state.load();await settle();
 assert.equal(view.state.rows[0].status,'paid');assert.equal(view.state.canPayRequest(view.state.rows[0]),false);
 assert.match(text(view.container),/Оплачена/);assert.equal(posted(calls).length,1);
});

test('accepted payment stays paid with a visible warning when bootstrap and list refresh fail; retry reads only',async t=>{
 let failRefresh=true,paid=false;
 const {view,calls}=await fixture(t,async(url)=>{
  if(url==='/api/ledger'){paid=true;return reply({id:83});}
  if(url==='/api/bootstrap')return failRefresh?denied(503,'Synthetic bootstrap unavailable'):reply(boot());
  assert.match(url,/^\/api\/requests\?/);
  return failRefresh?denied(503,'Synthetic list unavailable'):reply({items:[{...request,status:'paid',actions:[]}],total:1});
 });
 await view.state.save();await settle();
 assert.equal(paid,true);assert.equal(view.state.modal,null);assert.equal(view.state.formError,'');
 assert.equal(view.state.rows[0].status,'paid');assert.equal(view.state.canPayRequest(view.state.rows[0]),false);
 assert.equal(view.state.error,paymentWarning);assert.ok(visibleErrors(view).includes(paymentWarning));
 failRefresh=false;const before=calls.length;await view.state.load();await settle();
 assert.equal(view.state.error,'');assert.equal(view.state.rows[0].status,'paid');
 assert.ok(calls.slice(before).every(call=>call.method==='GET'),'refresh must not resubmit a recorded payment');
 assert.equal(posted(calls).length,1);
});

test('bootstrap failure still attempts an independent list refresh and reports the accepted payment',async t=>{
 const {view,calls}=await fixture(t,async(url)=>{
  if(url==='/api/ledger')return reply({id:83});
  if(url==='/api/bootstrap')return denied(503,'Synthetic bootstrap unavailable');
  assert.match(url,/^\/api\/requests\?/);return reply({items:[],total:0});
 });
 view.state.requestStatus='approved';await view.state.save();await settle();
 assert.equal(view.state.rows.length,0);assert.equal(view.state.error,paymentWarning);
 assert.ok(calls.some(call=>call.url.startsWith('/api/requests?')));assert.equal(posted(calls).length,1);
});

test('a read started before payment cannot restore approved status while the following bootstrap fails',async t=>{
 const oldRead=deferred(),bootstrapRead=deferred();let listReads=0;
 const {view,calls}=await fixture(t,async(url)=>{
  if(url==='/api/ledger')return reply({id:83});
  if(url==='/api/bootstrap')return bootstrapRead.promise;
  assert.match(url,/^\/api\/requests\?/);
  return ++listReads===1?oldRead.promise:denied(503,'Synthetic current list unavailable');
 });
 const reading=view.state.load();await settle();
 const saving=view.state.save();await settle();
 assert.ok(calls.some(call=>call.url==='/api/bootstrap'));assert.equal(view.state.rows[0].status,'paid');
 oldRead.resolve(reply({items:[request],total:1}));await reading;await settle();
 assert.equal(view.state.rows[0].status,'paid');assert.equal(view.state.canPayRequest(view.state.rows[0]),false);
 bootstrapRead.resolve(denied(503,'Synthetic bootstrap unavailable'));await saving;await settle();
 assert.equal(view.state.rows[0].status,'paid');assert.equal(view.state.error,paymentWarning);
 assert.equal(posted(calls).length,1);
});

test('midnight bootstrap failure cannot retain the old pay action after an accepted payment',async t=>{
 let failRefresh=true;
 const {view,calls}=await fixture(t,async(url)=>{
  if(url==='/api/ledger')return reply({id:83});
  if(url==='/api/bootstrap')return failRefresh?denied(503,'Synthetic next-day bootstrap unavailable'):reply(boot());
  assert.match(url,/^\/api\/requests\?/);return reply({items:[{...request,status:'paid',actions:[]}],total:1});
 });
 view.state.bootstrapLocalDay='2000-01-01';
 await view.state.save();await settle();
 assert.equal(calls.filter(call=>call.url==='/api/bootstrap').length,2,'both explicit refresh and the business-day refresh failed');
 assert.equal(view.state.rows[0].status,'paid');assert.equal(view.state.canPayRequest(view.state.rows[0]),false);
 assert.equal(view.state.error,paymentWarning);assert.ok(visibleErrors(view).includes(paymentWarning));
 failRefresh=false;const before=calls.length;await view.state.load();await settle();
 assert.equal(view.state.bootstrapLocalDay,day());assert.equal(view.state.error,'');
 assert.ok(calls.slice(before).every(call=>call.method==='GET'));assert.equal(posted(calls).length,1);
});

test('rejected payment keeps the form and approved state without a false success message',async t=>{
 const {view,calls}=await fixture(t,async(url)=>{
  assert.equal(url,'/api/ledger');return denied(409,'Synthetic payment not allowed');
 });
 await view.state.save();await settle();
 assert.equal(view.state.modal.paymentRequestId,41);assert.equal(view.state.formError,'Synthetic payment not allowed');
 assert.equal(view.state.rows[0].status,'approved');assert.equal(view.state.notice,'');assert.equal(view.state.error,'');
 assert.equal(calls.length,1);assert.equal(posted(calls).length,1);
});

for(const stage of ['write','refresh'])test(`late payment ${stage} cannot change another company after cancellation`,async t=>{
 const late=deferred();
 const {view,calls}=await fixture(t,async(url)=>{
  if(url==='/api/ledger')return stage==='write'?late.promise:reply({id:83});
  assert.equal(url,'/api/bootstrap');assert.equal(stage,'refresh');return late.promise;
 });
 const saving=view.state.save();await settle();
 assert.ok(calls.some(call=>call.url===(stage==='write'?'/api/ledger':'/api/bootstrap')));
 view.state.cancelPending();
 const other={...request,id:99,number:'OTHER-99'};
 Object.assign(view.state,{companyId:2,boot:{...boot(),today:'2026-10-13'},rows:[other],modal:null,error:'Other company error',notice:'Other company notice'});
 late.resolve(stage==='write'?reply({id:83}):reply(boot()));await saving;await settle();
 assert.equal(view.state.companyId,2);assert.equal(view.state.boot.today,'2026-10-13');
 assert.equal(view.state.rows[0].id,99);assert.equal(view.state.rows[0].status,'approved');
 assert.equal(view.state.error,'Other company error');assert.equal(view.state.notice,'Other company notice');
 assert.equal(calls.filter(call=>call.url.startsWith('/api/requests?')).length,0);
 assert.equal(posted(calls).length,1);
});
