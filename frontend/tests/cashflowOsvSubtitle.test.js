import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text,find} from './helpers/mountVue.js';
import {lang,setLang} from '../src/i18n/index.js';
const app=await compileComponent(new URL('../src/App.vue',import.meta.url));

test('the mounted Cash Flow page describes the selected factual basis in Russian and Uzbek without claiming IAS 7 for bank OSV',async t=>{
 const globals=Object.fromEntries(['window','location','document','fetch'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 const previous=lang.value;setLang('ru');
 globalThis.window={addEventListener(){},localStorage:{getItem(){return null;}}};
 globalThis.location={hash:'#report'};
 globalThis.document={hidden:true,activeElement:null,addEventListener(){},removeEventListener(){},querySelector(){return null;}};
 globalThis.fetch=async()=>({ok:false,status:401,json:async()=>({detail:'Synthetic unauthenticated startup'})});
 const view=mount(app);
 t.after(()=>{view.unmount();setLang(previous);for(const [key,descriptor] of Object.entries(globals)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}});
 await settle();
 Object.assign(view.state,{user:{id:1,role:'finance',permissions:['view','export'],sections:{report:'view',accounts:'view'}},boot:{today:'2026-10-26',accounts:[],categories:[],companies:[{id:2,name:'Synthetic company',code:'UZGERMED'}],roles:{}},companyId:2,companyReady:true,page:'report'});
 await settle();
 const heading=()=>text(find(view.container,node=>node.props?.class==='pg-head'));
 assert.match(heading(),/Управленческая форма по прямому методу · структура IAS 7/);
 view.state.cashFlowBasis='osv';await settle();
 assert.match(heading(),/Отчёт по банковской ОСВ 5110 и выбранному образцу/);assert.doesNotMatch(heading(),/IAS 7/);
 setLang('uz');await settle();assert.match(heading(),/Bankning 5110 aylanma-saldo qaydnomasi/);assert.doesNotMatch(heading(),/IAS 7/);
 view.state.cashFlowBasis='operations';await settle();assert.match(heading(),/IAS 7/);
 view.state.page='accounts';await settle();assert.doesNotMatch(heading(),/5110/);assert.match(heading(),/Bank va kassa/);
});
