import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,all,text} from './helpers/mountVue.js';
const component=await compileComponent(new URL('../src/admin/UserSections.vue',import.meta.url));
const policy={version:2,company_id:2,locked:false,sections:{accounts:'hidden'},modules:[{key:'accounts',label:'Банк и касса',default:'view'},{key:'pnl',label:'P&L',default:'hidden'},{key:'balance',label:'Баланс',default:'hidden'}]};
const companies=[{id:2,name:'A'},{id:3,name:'B'}];
test('section changes save selected user company, version and independent report modes',async t=>{
 const calls=[];const view=mount(component,{user:{id:7,company_id:2},companies,api:async(url,options)=>{calls.push([url,options]);return policy;}});t.after(view.unmount);await settle();
 assert.equal(view.state.choices.accounts,'hidden');
 view.state.choices.pnl='view';view.state.choices.balance='hidden';view.state.reason='Owner confirms these section rights';
 await view.state.save();await settle();
 const saved=calls.find(([,o])=>o?.method==='PUT');assert.equal(saved[0],'/api/admin/users/7/sections');
 const body=JSON.parse(saved[1].body);assert.equal(body.company_id,2);assert.equal(body.version,2);assert.equal(body.sections.pnl,'view');assert.equal(body.sections.balance,'hidden');
 assert.match(text(view.container),/Пароль остаётся прежним/);
});
test('a stale company response cannot overwrite the current access matrix',async t=>{
 let oldResolve;const view=mount(component,{user:{id:8,holding_role:'founder'},companies,api:url=>url.endsWith('=2')?new Promise(r=>oldResolve=r):Promise.resolve({...policy,company_id:3,sections:{pnl:'hidden'}})});t.after(view.unmount);await settle();
 view.state.company=3;await settle();assert.equal(view.state.policy.company_id,3);
 oldResolve(policy);await settle();assert.equal(view.state.policy.company_id,3);
 assert.equal(all(view.container,n=>n.tag==='option'&&n.props.value==='edit').length,0);
});

test('the employee sees one unambiguous choice and a pending before/after until saved',async t=>{
 let saved={...policy,sections:{}};const calls=[];
 const view=mount(component,{user:{id:7,company_id:2},companies,api:async(url,options)=>{calls.push([url,options]);if(options){saved={...saved,version:saved.version+1,sections:JSON.parse(options.body).sections};return saved;}return saved;}});t.after(view.unmount);await settle();
 assert.doesNotMatch(text(view.container),/По роли|Режим доступа/);
 assert.match(text(view.container),/Стандартный: Только просмотр/);
 assert.equal(view.state.pending.length,0);
 view.state.choices.accounts='edit';await settle();
 assert.match(text(view.container),/Не сохранено: Стандартный: Только просмотр → Можно работать/);
 assert.equal(view.state.canSave,false,'a reason is required before saving');
 view.state.reason='Allow accounting work';await settle();assert.equal(view.state.canSave,true);
 await view.state.save();await settle();
 assert.equal(view.state.pending.length,0);assert.equal(view.state.choices.accounts,'edit');
 assert.doesNotMatch(text(view.container),/Не сохранено: Стандартный: Только просмотр → Можно работать/);
 assert.match(text(view.container),/Все настройки сохранены/);
});

test('bulk reports never changes bank access and reset never writes to the API',async t=>{
 const calls=[];const view=mount(component,{user:{id:7,company_id:2},companies,api:async(url,options)=>{calls.push([url,options]);return policy;}});t.after(view.unmount);await settle();
 view.state.reportMode='edit';await settle();
 assert.equal(view.state.choices.pnl,'edit');assert.equal(view.state.choices.balance,'edit');assert.equal(view.state.choices.accounts,'hidden');
 assert.equal(view.state.pending.length,2);assert.equal(calls.filter(([,o])=>o).length,0);
 view.state.reset();await settle();assert.equal(view.state.choices.pnl,'default');assert.equal(view.state.choices.balance,'default');assert.equal(view.state.pending.length,0);
 assert.equal(calls.filter(([,o])=>o).length,0);
});

test('failed save keeps the selected pending changes instead of claiming success',async t=>{
 const view=mount(component,{user:{id:7,company_id:2},companies,api:async(url,options)=>{if(options)throw new Error('Conflict');return policy;}});t.after(view.unmount);await settle();
 view.state.choices.accounts='view';view.state.reason='Correct the section access';await view.state.save();await settle();
 assert.equal(view.state.pending.length,1);assert.equal(view.state.choices.accounts,'view');assert.equal(view.state.notice,'');assert.match(text(view.container),/Conflict/);
});
