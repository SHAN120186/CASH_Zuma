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
 assert.match(text(view.container),/пароли не меняются/);
});
test('a stale company response cannot overwrite the current access matrix',async t=>{
 let oldResolve;const view=mount(component,{user:{id:8,holding_role:'founder'},companies,api:url=>url.endsWith('=2')?new Promise(r=>oldResolve=r):Promise.resolve({...policy,company_id:3,sections:{pnl:'hidden'}})});t.after(view.unmount);await settle();
 view.state.company=3;await settle();assert.equal(view.state.policy.company_id,3);
 oldResolve(policy);await settle();assert.equal(view.state.policy.company_id,3);
 assert.equal(all(view.container,n=>n.tag==='option'&&n.props.value==='edit').length,0);
});
