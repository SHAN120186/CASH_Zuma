import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text,all} from './helpers/mountVue.js';
import {setLang,lang} from '../src/i18n/index.js';
const component=await compileComponent(new URL('../src/DepartmentsPage.vue',import.meta.url));
test('company-scoped catalog saves RU/UZ names with revision and disables without deleting',async t=>{
 const calls=[];let result={version:0,can_edit:true,items:[{code:'qc',name_ru:'QC',name_uz:'QC',active:true}]};
 const api=async(url,options={})=>{calls.push([url,options]);if(options.method){const body=JSON.parse(options.body);assert.equal(body.version,result.version);result={...result,version:result.version+1,items:[{...body,code:'qc'}]};}return structuredClone(result);};
 const view=mount(component,{api,companyId:2,canEdit:true});t.after(view.unmount);await settle();
 view.state.edit(result.items[0]);view.state.draft.name_ru='Synthetic renamed';view.state.draft.name_uz='Synthetic bolim';view.state.draft.active=false;await view.state.save();await settle();
 assert.equal(calls[1][0],'/api/departments/qc?company_id=2');assert.equal(calls[1][1].method,'PUT');assert.equal(result.items.length,1);assert.equal(result.items[0].active,false);assert.match(text(view.container),/Synthetic renamed/);
});
test('read-only catalog never offers save or create and translates labels',async t=>{
 const previous=lang.value;t.after(()=>setLang(previous));setLang('uz');
 const view=mount(component,{api:async()=>({version:0,can_edit:false,items:[]}),companyId:1,canEdit:false});t.after(view.unmount);await settle();
 assert.match(text(view.container),/Kompaniya bo‘limlari/);assert.equal(all(view.container,n=>n.tag==='button'&&/qo‘shish/.test(text(n))).length,0);
});
test('conflicting update preserves the unsaved draft and displays server error',async t=>{
 const view=mount(component,{api:async(url,opts)=>{if(opts)throw Error('Synthetic conflict');return {version:4,can_edit:true,items:[]};},companyId:1,canEdit:true});t.after(view.unmount);await settle();view.state.edit();view.state.draft.name_ru='Keep';view.state.draft.name_uz='Saqlash';await view.state.save();await settle();assert.equal(view.state.draft.name_ru,'Keep');assert.match(text(view.container),/Synthetic conflict/);
});
