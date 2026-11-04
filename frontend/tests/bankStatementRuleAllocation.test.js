import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text} from './helpers/mountVue.js';

const component=await compileComponent(new URL('../src/BankStatementRuleAllocation.vue',import.meta.url));
const snapshot={batch_id:7,currency:'UZS',token:'a'.repeat(64),ready_count:1,remaining_count:1,
 rows:[{line_id:1,ledger_id:11,date:'2026-10-30',document_number:'B1',counterparty:'Synthetic',direction:'out',amount:'75.00',category_name:'Synthetic article',status:'ready'}]};

test('read-only staff never requests or commits existing allocation',async t=>{
 const calls=[];const view=mount(component,{api:async url=>calls.push(url),companyId:1,batchId:7,canApply:false});t.after(view.unmount);
 await view.state.check();view.state.preview=snapshot;view.state.confirmed=true;await view.state.apply();
 assert.deepEqual(calls,[]);assert.equal(text(view.container),'');
});

test('existing allocation needs explicit confirmation and double clicks send one commit',async t=>{
 const calls=[];let finish,changed=0;
 const api=async(url,options)=>{calls.push([url,options]);return url.endsWith('/preview')?snapshot:new Promise(resolve=>{finish=resolve;});};
 const view=mount(component,{api,companyId:1,batchId:7,canApply:true,onChanged:()=>changed++});t.after(view.unmount);
 await view.state.check();await view.state.apply();assert.equal(calls.length,1);
 view.state.confirmed=true;const pending=view.state.apply();await settle();await view.state.apply();
 assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,1);
 assert.deepEqual(JSON.parse(calls.at(-1)[1].body),{token:snapshot.token,confirm:true});
 finish({batch_id:7,allocated:1,remaining_count:1});await pending;await settle();
 assert.equal(changed,1);assert.equal(view.state.preview,null);assert.match(text(view.container),/Распределено операций: 1/);
});

test('lost allocation reply requires a fresh read and cannot create a second commit',async t=>{
 let reads=0,commits=0;
 const api=async url=>{if(url.endsWith('/preview'))return ++reads===1?snapshot:{...snapshot,rows:[],ready_count:0};commits++;throw Error('network');};
 const view=mount(component,{api,companyId:1,batchId:7,canApply:true});t.after(view.unmount);
 await view.state.check();view.state.confirmed=true;await view.state.apply();await view.state.apply();
 assert.equal(commits,1);assert.equal(view.state.uncertain,true);assert.match(text(view.container),/Проверьте состояние перед повтором/);
 await view.state.check();view.state.confirmed=true;await view.state.apply();
 assert.equal(reads,2);assert.equal(commits,1);assert.equal(view.state.uncertain,false);
});

test('company change discards delayed previews and old confirmation',async t=>{
 let finish;const view=mount(component,{api:()=>new Promise(resolve=>{finish=resolve;}),companyId:1,batchId:7,canApply:true});t.after(view.unmount);
 const pending=view.state.check();await settle();view.app._instance.props.companyId=2;await settle();
 finish(snapshot);await pending;await settle();assert.equal(view.state.preview,null);assert.equal(view.state.confirmed,false);assert.equal(view.state.busy,false);
});
