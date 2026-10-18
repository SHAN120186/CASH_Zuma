import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle} from './helpers/mountVue.js';

const component=await compileComponent(new URL('../src/App.vue',import.meta.url));
const reply=data=>({ok:true,json:async()=>data});
const budget=(id,month)=>({id,currency:'UZS',month,cost_group:'fixed',category:'Synthetic budget',amount:'100.00'});

async function fixture(t,fetcher){
  const originals=Object.fromEntries(['window','location','document','fetch'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  globalThis.window={addEventListener(){},localStorage:{getItem(){return null;}}};
  globalThis.location={hash:'#budgets'};
  globalThis.document={hidden:true,activeElement:null,addEventListener(){},removeEventListener(){},querySelector(){return {scrollIntoView(){}};}};
  globalThis.fetch=async url=>url==='/api/me'?{ok:false,status:401,json:async()=>({detail:'Synthetic unauthenticated startup'})}:fetcher(url);
  const view=mount(component);
  t.after(()=>{view.unmount();for(const [key,descriptor] of Object.entries(originals)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}});
  await settle();
  Object.assign(view.state,{user:{id:1,role:'finance',permissions:['view','ledger']},
    boot:{today:'2026-10-12',accounts:[],categories:[],companies:[{id:2,name:'Synthetic Zuma',code:'ZUMA'}],roles:{}},
    companyId:2,companyReady:true,page:'budgets',currency:'UZS',month:'2026-10'});
  await settle();
  return view;
}

test('mounted navigation ignores a late budget response after ledger has loaded',async t=>{
  let resolveOld;
  const view=await fixture(t,async url=>{
    if(url.startsWith('/api/budgets?'))return await new Promise(resolve=>{resolveOld=()=>resolve(reply([budget('budget-old','2026-10')]));});
    assert.ok(url.startsWith('/api/ledger?'),'only synthetic reads are permitted');
    return reply({items:[{id:'ledger-new',kind:'in',amount:'10.00',date:'2026-10-12',account:'Synthetic bank',currency:'UZS'}],total:1});
  });
  const old=view.state.load();await settle();assert.ok(resolveOld);
  await view.state.go('ledger');await settle();assert.equal(view.state.rows[0].id,'ledger-new');
  resolveOld();await old;await settle();
  assert.equal(view.state.page,'ledger');
  assert.equal(view.state.rows[0].id,'ledger-new','the old budget must not overwrite the active ledger');
});

test('mounted budgets ignore a late response for an earlier selected month',async t=>{
  let resolveOctober;
  const view=await fixture(t,async url=>{
    assert.ok(url.startsWith('/api/budgets?'),'only synthetic budget reads are permitted');
    const month=new URL(url,'http://isolated.test').searchParams.get('month');
    if(month==='2026-10')return await new Promise(resolve=>{resolveOctober=()=>resolve(reply([budget('budget-october','2026-10')]));});
    assert.equal(month,'2026-11');
    return reply([budget('budget-november','2026-11')]);
  });
  const october=view.state.load();await settle();assert.ok(resolveOctober);
  view.state.month='2026-11';await view.state.load();await settle();
  assert.equal(view.state.rows[0].id,'budget-november');
  resolveOctober();await october;await settle();
  assert.equal(view.state.month,'2026-11');
  assert.equal(view.state.rows[0].id,'budget-november','October must not replace the selected November budget');
});
