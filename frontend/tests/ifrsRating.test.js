import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text,find} from './helpers/mountVue.js';
const component=await compileComponent(new URL('../src/IfrsRating.vue',import.meta.url));
const company={id:2,name:'Zuma',code:'ZUMA'};
const data=()=>({company_id:2,can_sync:true,binding:{synced_at:'2026-10-14T06:00:00Z'},source_url:'https://docs.google.com',source:{filename:'synthetic.xlsx',sha256:'test'},report:{
 year:2026,end_month:3,available_months:[1,2,3],period:{start:'2026-01-01',end:'2026-03-31',months:3},
 score:'75',grade:'C',capped:true,coverage_weight:100,status:'provisional',method_version:'test',categories:[],issues:[],monthly:[],data_blocks:[],provenance:{},
 scorecard:['plan_execution','ebitda_margin','cash_ratio','net_debt_ebitda'].map(key=>({key,title:key,value:'1',score:'8',weight:5,points:'4',status:'good',unit:'x',bands:[]}))}});

test('rating renders saved score without Drive refresh despite legacy sync capability',async t=>{
 const calls=[];const view=mount(component,{company,api:async(url,opts)=>{calls.push([url,opts]);return data();}});t.after(view.unmount);await settle();
 assert.match(text(view.container),/75,0/);assert.equal(find(view.container,n=>n.tag==='button'&&text(n)==='Обновить из Google Drive'),undefined);
 await view.state.load(true);await settle();assert.equal(calls.length,2);assert.ok(calls.every(([url,opts])=>!opts?.method&&url.startsWith('/api/ifrs-rating?')));
});

test('late responses cannot reveal another company data',async t=>{
 const pending=[];const view=mount(component,{company,api:()=>new Promise(resolve=>pending.push(resolve))});
 t.after(view.unmount);await settle();
 view.app._instance.props.company={id:3,name:'Other'};await settle();
 pending[0](data());await settle();assert.doesNotMatch(text(view.container),/75,0/);
 pending[1](data());await settle();assert.match(text(view.container),/Источник относится к другой компании/);
});

test('saved rating stale marker remains readable without any source mutation action',async t=>{
 const value=data();value.stale=true;const view=mount(component,{company,api:async()=>value});t.after(view.unmount);await settle();
 assert.match(text(view.container),/последняя сохранённая версия/);assert.match(text(view.container),/75,0/);assert.equal(find(view.container,n=>n.tag==='button'&&text(n)==='Обновить из Google Drive'),undefined);
});

test('view-only users have no source mutation button',async t=>{
 const value=data();value.can_sync=false;value.report.score=null;value.report.grade=null;value.report.status='incomplete';
 const view=mount(component,{company,api:async()=>value});t.after(view.unmount);await settle();
 assert.doesNotMatch(text(view.container),/Обновить из Google Drive/);
 assert.match(text(view.container),/часть показателей недоступна/);
});
