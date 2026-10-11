import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text,find} from './helpers/mountVue.js';
const component=await compileComponent(new URL('../src/ReportLinks.vue',import.meta.url));
const choice={year:2026,period:'q1',start:1,end:3,unit:'x',mode:'actual',sourceId:5};
test('report links are closed, lazy, scoped to source and period, and discard stale requests',async t=>{
 const calls=[],pending=[];
 const view=mount(component,{companyId:2,selection:choice,api:url=>{calls.push(url);return new Promise(resolve=>pending.push(resolve));}});
 t.after(view.unmount);await settle();assert.equal(calls.length,0);
 await find(view.container,node=>node.tag==='button').props.onClick();await settle();
 assert.match(calls[0],/company_id=2/);assert.match(calls[0],/source_id=5/);assert.match(calls[0],/start_month=1&end_month=3/);
 view.app._instance.props.companyId=3;await settle();
 pending[0]({company_id:2,status:'available',period:'FOREIGN',bridges:{}});await settle();
 assert.doesNotMatch(text(view.container),/FOREIGN/);
 pending[1]({company_id:3,status:'missing'});await settle();
 assert.match(text(view.container),/Для этой фирмы пока нет данных Mapping/);
});
test('an explicit foreign-company reply is rejected and does not render financial amounts',async t=>{
 const view=mount(component,{companyId:2,selection:choice,api:async()=>({company_id:3,status:'available',period:'FOREIGN'})});
 t.after(view.unmount);await find(view.container,node=>node.tag==='button').props.onClick();await settle();
 assert.match(text(view.container),/Источник относится к другой компании/);assert.doesNotMatch(text(view.container),/FOREIGN/);
});
test('ready Mapping bridges show the real period, raw units and separate capital movement',async t=>{
 const view=mount(component,{companyId:2,selection:choice,api:async()=>({company_id:2,status:'ready',
  period:{start:'2026-01',end:'2026-03'},source:{unit_status:'unknown',currency:'UZS'},required_fields:[],
  bridges:{profit_to_equity:{title:'Прибыль и изменение капитала',opening_equity:'100',net_income:'40',other_equity_change:'10',closing_equity:'150',provenance:{}},
   cash_movement:{title:'Изменение денежных остатков по ОСВ',opening_cash:'20',net_change:'8',closing_cash:'28',provenance:{}}}})});
 t.after(view.unmount);await find(view.container,node=>node.tag==='button').props.onClick();await settle();
 assert.match(text(view.container),/2026-01 — 2026-03/);assert.match(text(view.container),/Исходные единицы/);
 assert.match(text(view.container),/Прочее изменение капитала/);assert.match(text(view.container),/10,00/);
 assert.doesNotMatch(text(view.container),/\[object Object\]/);
 assert.match(text(view.container),/Движение по видам деятельности по ОСВ не определяется/);
});
test('missing months in a saved source do not claim that the company has no Mapping data',async t=>{
 const view=mount(component,{companyId:2,selection:choice,api:async()=>({company_id:2,source_id:5,status:'missing',
  period:{start:'2026-01',end:'2026-03'},source:{unit_status:'unknown'},bridges:{},
  coverage:[{period:'2026-02',ready:false,reason:'mapping_month_missing'}]})});
 t.after(view.unmount);await find(view.container,node=>node.tag==='button').props.onClick();await settle();
 assert.match(text(view.container),/Ожидается ОСВ за месяцы: 2026-02/);
 assert.doesNotMatch(text(view.container),/Для этой фирмы пока нет данных Mapping/);
});
