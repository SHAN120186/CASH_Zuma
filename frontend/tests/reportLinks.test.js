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
test('website cash keeps its own currency and scale, shows actual basis in plan mode and does not invent a comparison',async t=>{
 const calls=[];
 const view=mount(component,{companyId:2,currency:'USD',selection:{...choice,unit:'k',mode:'plan'},api:async url=>{calls.push(url);return {company_id:2,source_id:5,status:'ready',
  period:{start:'2026-01',end:'2026-03'},source:{unit_status:'unknown'},required_fields:[],bridges:{},
  website_cash:{status:'ready',company_id:2,currency:'USD',as_of:'2026-03-31',opening_cash:'2000',net_change:'500',opening_adjustments:'100',closing_cash:'2600',account_count:2,operation_count:3},
  cash_comparison:{status:'needs_clarification',difference:null}};}});
 t.after(view.unmount);await find(view.container,node=>node.tag==='button').props.onClick();await settle();
 assert.match(calls[0],/currency=USD/);assert.match(text(view.container),/Сверка фактических данных/);
 assert.match(text(view.container),/Сверка использует факт выбранного периода/);
 assert.match(text(view.container),/Денежные операции сайта/);assert.match(text(view.container),/Тыс\. USD/);
 assert.match(text(view.container),/2,60/);assert.match(text(view.container),/Счета: 2\. Операции за период: 3\./);
 assert.match(text(view.container),/Разница между Cash Flow сайта и Mapping не рассчитана/);
});
test('an empty website cash register is explained without claiming that unknown balances are zero',async t=>{
 const view=mount(component,{companyId:2,selection:choice,api:async()=>({company_id:2,source_id:5,status:'ready',bridges:{},required_fields:[],
  website_cash:{status:'missing',reason:'no_accounts',company_id:2,currency:'UZS',account_count:0,opening_cash:null,net_change:null,opening_adjustments:null,closing_cash:null}})});
 t.after(view.unmount);await find(view.container,node=>node.tag==='button').props.onClick();await settle();
 assert.match(text(view.container),/За выбранный период нет денежных счетов сайта/);
 assert.match(text(view.container),/Добавьте счета с начальными остатками и проведённые операции/);
 assert.doesNotMatch(text(view.container),/0,00/);
});
test('website cash from another company or currency and a stale source are rejected',async t=>{
 for(const extra of [
  {website_cash:{company_id:3,currency:'UZS',closing_cash:'123456'}},
  {website_cash:{company_id:2,currency:'USD',closing_cash:'123456'}},
  {source_id:9},
 ]){
  const view=mount(component,{companyId:2,selection:choice,api:async()=>({company_id:2,source_id:5,status:'ready',bridges:{},...extra})});
  t.after(view.unmount);await find(view.container,node=>node.tag==='button').props.onClick();await settle();
  assert.match(text(view.container),/Источник/);assert.doesNotMatch(text(view.container),/123\s?456/);
 }
});
test('currency changes reload an open reconciliation and discard the prior currency response',async t=>{
 const calls=[],pending=[];
 const view=mount(component,{companyId:2,currency:'UZS',selection:choice,api:url=>{calls.push(url);return new Promise(resolve=>pending.push(resolve));}});
 t.after(view.unmount);await find(view.container,node=>node.tag==='button').props.onClick();await settle();
 view.app._instance.props.currency='EUR';await settle();assert.match(calls[1],/currency=EUR/);
 pending[0]({company_id:2,source_id:5,status:'ready',bridges:{},website_cash:{company_id:2,currency:'UZS',closing_cash:'987654'}});await settle();
 assert.doesNotMatch(text(view.container),/987\s?654/);
 pending[1]({company_id:2,source_id:5,status:'ready',bridges:{},website_cash:{company_id:2,currency:'EUR',status:'ready',account_count:1,operation_count:0,closing_cash:'20'}});await settle();
 assert.match(text(view.container),/Единицы EUR/);assert.match(text(view.container),/20,00/);
});
test('the shared Cash Flow cut-off travels to reconciliation and reloads when changed',async t=>{
 const calls=[];
 const view=mount(component,{companyId:2,asOf:'2026-02-15',selection:choice,api:async url=>{calls.push(url);return {company_id:2,source_id:5,status:'ready',bridges:{}};}});
 t.after(view.unmount);await find(view.container,node=>node.tag==='button').props.onClick();await settle();
 assert.match(calls[0],/as_of=2026-02-15/);
 view.app._instance.props.asOf='2026-03-31';await settle();assert.match(calls[1],/as_of=2026-03-31/);
 view.app._instance.props.selection={...choice,year:2025};await settle();assert.doesNotMatch(calls[2],/as_of=/);
});
