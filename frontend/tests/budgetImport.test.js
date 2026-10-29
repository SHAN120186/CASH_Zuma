import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text} from './helpers/mountVue.js';
const component=await compileComponent(new URL('../src/BudgetImport.vue',import.meta.url));
const preview={id:9,month:'2026-11',currency:'UZS',can_create:false,issues:[],rows:[{line:2,code:'001',name:'Synthetic',amount:'100.00',suggestion:'21'},{line:3,code:'002',name:'Blank',amount:null,suggestion:''}],categories:[{id:21,code:'001',name:'Synthetic',old:'80.00'}]};
test('budget import previews exact scope and commits only mappings after review',async t=>{
 const calls=[];const view=mount(component,{month:'2026-11',currency:'UZS',api:async(url,options)=>{calls.push([url,options]);return url.includes('/preview?')?preview:{updated:1}}});t.after(view.unmount);
 await view.state.upload({target:{files:[{name:'budget.xlsx'}],value:'file'}});await settle();
 assert.match(calls[0][0],/month=2026-11&currency=UZS&scale=1/);assert.equal(view.state.ready,true);assert.match(text(view.container),/100/);
 assert.equal(view.state.previous(preview.rows[0]),'80.00');await view.state.commit();await settle();
 assert.equal(calls[1][0],'/api/budgets/import/9/commit');assert.deepEqual(JSON.parse(calls[1][1].body),{mappings:{'001':'21','002':''}});assert.equal(view.state.preview,null);assert.match(text(view.container),/Загружено статей бюджета: 1/);
});
test('unmatched amounts and validation errors cannot commit; failed commit retains preview',async t=>{
 let commits=0;const view=mount(component,{month:'2026-11',currency:'UZS',api:async(url)=>{if(url.includes('/preview?'))return {...preview,rows:[{...preview.rows[0],suggestion:''}]};commits++;throw Error('Conflict')}});t.after(view.unmount);
 await view.state.upload({target:{files:[{name:'budget.xlsx'}],value:''}});await settle();assert.equal(view.state.ready,false);await view.state.commit();assert.equal(commits,0);
 view.state.choices['001']='21';await settle();assert.equal(view.state.ready,true);await view.state.commit();assert.equal(commits,1);assert.ok(view.state.preview);assert.equal(view.state.error,'Conflict');
});
