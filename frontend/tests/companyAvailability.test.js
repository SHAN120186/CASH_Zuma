import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,find,all,text} from './helpers/mountVue.js';
import {lang,setLang} from '../src/i18n/index.js';
const component=await compileComponent(new URL('../src/admin/CompanyAvailability.vue',import.meta.url));
const companies=[{id:2,code:'UZGERMED',name:'UZGERMED',active:true},{id:3,code:'ZUMA',name:'ZUMA',active:false},
 {id:6,code:'UZYARN',name:'UZYARN',active:true},{id:1,code:'UNASSIGNED',name:'Service',active:true}];
async function fixture(t,reply){
 const calls=[],changed=[];const view=mount(component,{companies,api:async(...args)=>{calls.push(args);return reply(...args);},onChanged:c=>changed.push(c)});
 t.after(view.unmount);await settle();return {...view,calls,changed};
}

test('company availability requires explicit confirmation and reason before a precise update',async t=>{
 const view=await fixture(t,async()=>({company:{...companies[0],active:false}}));
 assert.doesNotMatch(text(view.container),/Service/);
 assert.equal(all(view.container,n=>n.tag==='li').length,3);
 const on=find(view.container,n=>n.props?.role==='switch'&&n.props['aria-label']==='Доступ к компании UZGERMED');
 const off=find(view.container,n=>n.props?.role==='switch'&&n.props['aria-label']==='Доступ к компании ZUMA');
 assert.equal(on.props['aria-checked'],true);assert.equal(off.props['aria-checked'],false);
 assert.match(text(on),/Вкл/);assert.match(text(off),/Выкл/);
 on.props.onClick();await settle();
 assert.equal(view.calls.length,0);assert.equal(view.state.canSave,false);
 view.state.reason='Owner confirms temporary closure';await view.state.save();assert.equal(view.calls.length,0);
 view.state.confirmed=true;await view.state.save();await settle();
 assert.equal(view.calls.length,1);assert.equal(view.calls[0][0],'/api/admin/companies/2/availability');
 assert.equal(view.calls[0][1].method,'PUT');
 assert.deepEqual(JSON.parse(view.calls[0][1].body),{active:false,expected_active:true,reason:'Owner confirms temporary closure'});
 assert.equal(view.changed.length,1);assert.equal(view.changed[0].active,false);assert.equal(view.state.selected,null);
 assert.match(text(view.container),/Компания отключена\. Данные и назначения сохранены/);
});

test('reactivation is explicit and cancel never sends a request',async t=>{
 const view=await fixture(t,async()=>({company:{...companies[1],active:true}}));
 view.state.choose(companies[1]);await settle();assert.match(text(view.container),/Включить компанию ZUMA/);
 view.state.cancel();assert.equal(view.calls.length,0);assert.equal(view.state.selected,null);
 view.state.choose(companies[1]);view.state.confirmed=true;view.state.reason='Resume company work after review';await view.state.save();
 assert.deepEqual(JSON.parse(view.calls[0][1].body),{active:true,expected_active:false,reason:'Resume company work after review'});
 assert.equal(view.changed[0].active,true);
});

test('failed save keeps the company state and reason and never reports success',async t=>{
 const view=await fixture(t,async()=>{throw new Error('Synthetic stale state; reload required');});
 view.state.choose(companies[2]);view.state.confirmed=true;view.state.reason='Explicit synthetic availability change';await view.state.save();await settle();
 assert.equal(view.changed.length,0);assert.equal(view.state.selected.id,6);assert.equal(view.state.selected.active,true);
 assert.equal(view.state.reason,'Explicit synthetic availability change');assert.equal(view.state.notice,'');
 assert.match(text(view.container),/Synthetic stale state/);
});

test('busy confirmation cannot send twice and an invalid response cannot change the list',async t=>{
 let resolve;const view=await fixture(t,()=>new Promise(done=>{resolve=done;}));
 view.state.choose(companies[0]);view.state.confirmed=true;view.state.reason='Explicit synthetic change';
 const saving=view.state.save();await settle();await view.state.save();assert.equal(view.calls.length,1);
 resolve({company:{...companies[2],active:false}});await saving;await settle();assert.equal(view.changed.length,0);
 assert.match(text(view.container),/Не удалось подтвердить состояние компании/);
});

test('company changes elsewhere clear a stale confirmation instead of submitting a new intent',async t=>{
 const view=await fixture(t,async()=>{throw new Error('No update expected');});
 view.state.choose(companies[0]);view.state.confirmed=true;view.state.reason='A previous company state was selected';
 view.app._instance.props.companies=companies.map(c=>c.id===2?{...c,active:false}:c);await settle();
 assert.equal(view.state.selected,null);assert.equal(view.calls.length,0);
});

test('company controls retain localized actions and unchanged company names',async t=>{
 const previous=lang.value;t.after(()=>setLang(previous));setLang('uz');
 const view=await fixture(t,async()=>({}));view.state.choose(companies[0]);await settle();
 assert.match(text(view.container),/Kompaniyalarni boshqarish/);assert.match(text(view.container),/UZGERMED kompaniyasini o‘chirish/);
 assert.doesNotMatch(text(view.container),/[А-Яа-яЁё]/);
});
