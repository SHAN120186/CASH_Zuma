import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text,find,all} from './helpers/mountVue.js';
import {lang,setLang} from '../src/i18n/index.js';

const component=await compileComponent(new URL('../src/requests/CashFundingBatchPanel.vue',import.meta.url));
const row=(id,amount,extra={})=>({id,version:2,company_id:3,account_id:2,status:'approved',channel:'cash',currency:'UZS',amount,number:`CF-${id}`,counterparty:`Synthetic ${id}`,category:'Synthetic expense',date:'2026-11-06',approved_on:'2026-11-05',actions:['pay'],cash_funding:null,...extra});
const parent=extra=>({id:42,company_id:3,account_id:2,version:9,status:'approved',currency:'UZS',approved_on:'2026-11-05',cash_funding:{id:7,version:2,mode:'batch',state:'received',net_amount:'990000.00',receive_date:'2026-11-05'},...extra});
const state=extra=>({request_id:42,company_id:3,cash_account_id:2,funding_id:7,version:2,request_version:9,mode:'batch',currency:'UZS',received_amount:'990000.00',allocated_amount:'0.00',remaining_amount:'990000.00',cash_balance:'990000.00',can_pay:true,blocked_reason:'',candidates:[row(10,'500000.00'),row(11,'410000.00')],groups:[],...extra});
const decimal=n=>`${n/100n}.${String(n%100n).padStart(2,'0')}`;
const cents=value=>{const [whole,fraction='']=value.split('.');return BigInt(whole)*100n+BigInt(fraction.padEnd(2,'0'));};
const preview=(body,extra={})=>{
  const total=body.items.reduce((sum,item)=>sum+cents(state().candidates.find(r=>r.id===item.id).amount),0n);
  return {...body,request_id:42,company_id:3,cash_account_id:2,funding_id:7,mode:'batch',currency:'UZS',token:'synthetic-signed',total_amount:decimal(total),remaining_amount:decimal(99000000n-total),cash_balance:decimal(99000000n-total),...extra};
};
const committed=(body,extra={})=>{const p=preview(body);return {...p,version:3,request_version:10,paid_request_ids:body.items.map(item=>item.id),group:{id:1,key:body.key,date:body.date,reference:body.reference,total_amount:p.total_amount,request_ids:body.items.map(item=>item.id),ledger_ids:body.items.map(item=>item.id+100)},...extra};};
const button=(view,label)=>find(view.container,node=>node.tag==='button'&&text(node)===label);
async function fixture(t,handle,request=parent(),afterPaid=null){
  const calls=[],events=[];const api=async(url,options={})=>{const body=options.body?JSON.parse(options.body):null;calls.push({url,method:options.method||'GET',body});return handle?handle(url,body,calls):body?(url.endsWith('/preview')?preview(body):committed(body)):state();};
  const view=mount(component,{request,money:v=>v,api,onPaid:result=>{events.push(result);afterPaid?.(result);}});t.after(view.unmount);await settle();return {view,calls,events};
}
async function selectBoth(view){view.state.start();view.state.choose(view.state.snapshot.candidates[0],true);view.state.choose(view.state.snapshot.candidates[1],true);view.state.form.reference='SYN-CASH-1';await settle();}

test('batch selection posts exact approved amounts once and leaves the remaining cash',async t=>{
  const {view,calls,events}=await fixture(t);await selectBoth(view);
  assert.equal(view.state.total,91000000n);assert.equal(view.state.remaining,8000000n);
  assert.match(text(view.container),/80000.00/);assert.equal(calls.filter(c=>c.method==='POST').length,0);
  await view.state.check();await settle();assert.ok(view.state.preview);
  view.state.confirmed=true;await view.state.commit();await settle();
  assert.deepEqual(events[0].paid_request_ids,[10,11]);assert.equal(view.state.snapshot.remaining_amount,'80000.00');
  assert.equal(view.state.snapshot.candidates.length,0);assert.equal(view.state.snapshot.version,3);
  await view.state.commit();assert.equal(calls.filter(c=>c.url.endsWith('/commit')).length,1);
  assert.equal(calls.some(c=>c.url==='/api/ledger'),false);
});

test('changing selected rows, date or reference invalidates the signed preview and attestation',async t=>{
  const {view}=await fixture(t);await selectBoth(view);await view.state.check();view.state.confirmed=true;
  view.state.choose(view.state.snapshot.candidates[1],false);await settle();assert.equal(view.state.preview,null);assert.equal(view.state.confirmed,false);
  await view.state.check();assert.ok(view.state.preview);view.state.form.reference='SYN-CHANGED';await settle();assert.equal(view.state.preview,null);
  await view.state.check();
  const changedDate=new Date(view.state.form.date+'T00:00:00Z');changedDate.setUTCDate(changedDate.getUTCDate()+1);
  view.state.form.date=changedDate.toISOString().slice(0,10);await settle();assert.equal(view.state.preview,null);
});

test('lost commit response is recovered by a group-key GET without a repeated POST',async t=>{
  let result;
  const {view,calls,events}=await fixture(t,async(url,body)=>{
    if(url.endsWith('/preview'))return preview(body);
    if(url.endsWith('/commit')){result=committed(body);throw new Error('Synthetic lost reply');}
    return result?state({version:3,request_version:10,allocated_amount:result.total_amount,remaining_amount:result.remaining_amount,cash_balance:result.cash_balance,candidates:[],groups:[result.group]}):state();
  });
  await selectBoth(view);await view.state.check();view.state.confirmed=true;await view.state.commit();await settle();
  assert.equal(view.state.uncertain,true);assert.ok(button(view,'Проверить состояние группы'));
  await view.state.commit();assert.equal(calls.filter(c=>c.url.endsWith('/commit')).length,1);
  await view.state.refresh();await settle();assert.equal(view.state.uncertain,false);assert.deepEqual(events[0].paid_request_ids,[10,11]);
  assert.equal(calls.at(-1).method,'GET');assert.equal(calls.filter(c=>c.url.endsWith('/commit')).length,1);
  await view.state.refresh();assert.equal(events.length,1,'a second read cannot report a second payout');
});

test('wrong-company candidates and wrong preview metadata cannot become a payout',async t=>{
  const bad=await fixture(t,()=>state({candidates:[row(10,'500000.00',{company_id:9})]}));
  assert.equal(bad.view.state.snapshot,null);assert.ok(bad.view.state.error);bad.view.state.start();await bad.view.state.check();assert.equal(bad.calls.filter(c=>c.method==='POST').length,0);
  const {view,calls}=await fixture(t,(url,body)=>body?preview(body,{cash_account_id:99}):state());
  await selectBoth(view);await view.state.check();assert.equal(view.state.preview,null);assert.ok(view.state.error);view.state.confirmed=true;await view.state.commit();assert.equal(calls.filter(c=>c.url.endsWith('/commit')).length,0);
});

test('read-only and unreceived financing never expose a group payment action',async t=>{
  const readonly=await fixture(t,()=>state({can_pay:false,allocated_amount:null,remaining_amount:null,cash_balance:null,candidates:[],groups:[],blocked_reason:'Synthetic read only'}));
  assert.equal(button(readonly.view,'Оплатить несколько заявок'),undefined);readonly.view.state.choose(row(10,'500000.00'),true);await readonly.view.state.check();assert.equal(readonly.calls.filter(c=>c.method==='POST').length,0);
  assert.doesNotMatch(text(readonly.view.container),/Фактический остаток кассы/);
  assert.equal(readonly.view.state.error,'');assert.ok(readonly.view.state.snapshot);
  assert.doesNotMatch(text(readonly.view.container),/Выдано ранее|Выбрано к оплате|Останется от пополнения/);
  const waiting=await fixture(t,()=>state({received_amount:'0.00',remaining_amount:'0.00'}),parent({cash_funding:{...parent().cash_funding,state:'in_transit'}}));
  assert.equal(button(waiting.view,'Оплатить несколько заявок'),undefined);
  const legacy=await fixture(t,()=>{throw new Error('legacy must not load batch API');},parent({cash_funding:{...parent().cash_funding,mode:'single'}}));
  assert.equal(legacy.calls.length,0);assert.equal(text(legacy.view.container),'');
});

test('privacy-masked draft ownership is valid and never invents financial zeroes',async t=>{
  const source=parent({status:'draft',cash_funding:{...parent().cash_funding,state:'unfunded'}});
  const {view}=await fixture(t,()=>state({received_amount:'0.00',allocated_amount:null,remaining_amount:null,cash_balance:null,can_pay:false,blocked_reason:'Synthetic own-only access',candidates:[],groups:[]}),source);
  assert.equal(view.state.error,'');assert.ok(view.state.snapshot);assert.equal(view.state.hasTotals,false);
  assert.doesNotMatch(text(view.container),/Выдано ранее|Выбрано к оплате|Останется от пополнения|Фактический остаток кассы/);
  assert.equal(button(view,'Оплатить несколько заявок'),undefined);
});

test('a fresh card object revalidates payment capability even when financial versions stay the same',async t=>{
  let allowed=true;
  const {view,calls}=await fixture(t,()=>state({can_pay:allowed,candidates:allowed?state().candidates:[],blocked_reason:allowed?'':'Synthetic administrative oversight'}));
  await selectBoth(view);assert.equal(view.state.canPay,true);
  allowed=false;view.app._instance.props.request=parent();await settle();
  assert.equal(view.state.canPay,false);assert.equal(view.state.preview,null);assert.equal(view.state.opened,false);
  assert.equal(button(view,'Оплатить несколько заявок'),undefined);assert.equal(calls.filter(call=>call.method==='POST').length,0);
  assert.equal(calls.filter(call=>call.method==='GET').length,2,'one fresh capability read follows the replacement');
});

test('permission refresh after a lost payout keeps the attempt until readable history resolves it',async t=>{
  let paid=false,masked=false;
  const {view,calls,events}=await fixture(t,(url,body)=>{
    if(url.endsWith('/preview'))return preview(body);
    if(url.endsWith('/commit')){paid=true;throw new Error('Synthetic lost response');}
    return masked?state({can_pay:false,allocated_amount:null,remaining_amount:null,cash_balance:null,candidates:[],groups:[]}):state();
  });
  await selectBoth(view);await view.state.check();view.state.confirmed=true;await view.state.commit();
  const originalKey=view.state.attempt.key;assert.equal(paid,true);
  masked=true;view.app._instance.props.request=parent();await settle();
  assert.equal(view.state.uncertain,true);assert.equal(view.state.attempt.key,originalKey);assert.equal(view.state.canPay,false);
  assert.equal(events.length,0);assert.equal(calls.filter(call=>call.url.endsWith('/commit')).length,1);
});

test('masked history cannot declare a lost committed group absent or unlock another payment',async t=>{
  let result;
  const {view,calls,events}=await fixture(t,(url,body)=>{
    if(url.endsWith('/preview'))return preview(body);
    if(url.endsWith('/commit')){result=committed(body);throw new Error('Synthetic lost response');}
    return result?state({version:3,request_version:10,allocated_amount:null,remaining_amount:null,cash_balance:null,can_pay:false,candidates:[],groups:[]}):state();
  });
  await selectBoth(view);await view.state.check();view.state.confirmed=true;await view.state.commit();
  const attemptKey=view.state.attempt.key;await view.state.refresh();await settle();
  assert.equal(view.state.uncertain,true);assert.equal(view.state.attempt.key,attemptKey);assert.equal(events.length,0);
  assert.doesNotMatch(text(view.container),/выбранная группа не проведена/);assert.equal(view.state.hasTotals,false);
  await view.state.commit();assert.equal(calls.filter(call=>call.url.endsWith('/commit')).length,1);
});

test('own parent version refresh reloads totals after commit despite queued form cleanup',async t=>{
  let result,resolveRead,viewRef;
  const {view,events}=await fixture(t,(url,body)=>{
    if(url.endsWith('/preview'))return preview(body);
    if(url.endsWith('/commit')){result=committed(body);return result;}
    if(!result)return state();
    return new Promise(resolve=>{resolveRead=()=>resolve(state({version:3,request_version:10,allocated_amount:result.total_amount,remaining_amount:result.remaining_amount,cash_balance:result.cash_balance,candidates:[],groups:[result.group]}));});
  },parent(),()=>{viewRef.app._instance.props.request=parent({version:10,cash_funding:{...parent().cash_funding,version:3}});});
  viewRef=view;await selectBoth(view);await view.state.check();view.state.confirmed=true;await view.state.commit();await settle();
  assert.ok(resolveRead,'new authoritative parent versions start a fresh GET');
  view.state.form.note='';view.state.form.reference='';await settle();resolveRead();await settle();
  assert.ok(view.state.snapshot,'form cleanup cannot invalidate the independent status read');
  assert.equal(view.state.snapshot.remaining_amount,'80000.00');assert.equal(view.state.snapshot.allocated_amount,'910000.00');
  assert.equal(view.state.snapshot.groups.length,1);assert.equal(events.length,1);assert.equal(view.state.busy,false);
  assert.match(text(view.container),/80000.00/);assert.match(text(view.container),/Проведённые группы выплат/);
});

test('limits are exact in minor units and neither excessive selection nor stale preview can commit',async t=>{
  let resolve;
  const {view,calls}=await fixture(t,(url,body)=>body?new Promise(done=>resolve=()=>done(preview(body))):state({cash_balance:'900000.00'}));
  await selectBoth(view);assert.ok(view.state.selectionProblem);await view.state.check();assert.equal(calls.filter(c=>c.method==='POST').length,0);
  view.state.choose(view.state.snapshot.candidates[1],false);await settle();const checking=view.state.check();await settle();
  view.state.form.reference='SYN-NEW';await settle();resolve();await checking;assert.equal(view.state.preview,null);
});

test('a malformed accepted response locks the UI until status is checked',async t=>{
  const {view,events}=await fixture(t,(url,body)=>!body?state():url.endsWith('/preview')?preview(body):committed(body,{paid_request_ids:[10]}));
  await selectBoth(view);await view.state.check();view.state.confirmed=true;await view.state.commit();
  assert.equal(view.state.uncertain,true);assert.equal(events.length,0);assert.equal(view.state.preview,null);
});

test('search preserves selected rows and decimal totals use exact minor units',async t=>{
  const {view}=await fixture(t,()=>state({candidates:[row(10,'0.10'),row(11,'0.20')]}));
  await selectBoth(view);view.state.search='unmatched synthetic search';await settle();
  assert.equal(view.state.filtered.length,0);assert.equal(view.state.selectedRows.length,2);assert.equal(view.state.total,30n);
  view.state.search='Synthetic 10';await settle();assert.equal(view.state.filtered.length,1);assert.equal(view.state.total,30n);
});

test('selection stops at one hundred distinct requests',async t=>{
  const {view}=await fixture(t,()=>state({candidates:Array.from({length:101},(_,index)=>row(1000+index,'1.00'))}));
  view.state.start();for(const item of view.state.snapshot.candidates)view.state.choose(item,true);await settle();
  assert.equal(view.state.selected.length,100);assert.equal(new Set(view.state.selected).size,100);assert.equal(view.state.body().items.length,100);
});

test('late preview from a changed parent scope is discarded and labels translate to Uzbek',async t=>{
  const previous=lang.value;t.after(()=>setLang(previous));setLang('uz');
  let resolve;
  const {view,events}=await fixture(t,(url,body)=>body?new Promise(done=>resolve=()=>done(preview(body))):state());
  assert.match(text(view.container),/Bir nechta arizani to‘lash/);await selectBoth(view);const pending=view.state.check();await settle();
  view.app._instance.props.request=parent({version:10});await settle();resolve();await pending;await settle();
  assert.equal(view.state.preview,null);assert.equal(events.length,0);
});
