import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text} from './helpers/mountVue.js';
const component=await compileComponent(new URL('../src/overview/DailyReconciliation.vue',import.meta.url));
const result=extras=>({date:'2026-11-08',currency:'UZS',requests:{planned_count:2,planned_amount:'900',paid_count:0,paid_amount:'0',other_date_paid_count:0},banks:[],cash:[],fundings:[],review:[],bank_fees:'0',partner_fees:'0',interest:'0',...extras});
test('reconciliation does no background reading and only reads selected currency after opening',async t=>{
 const calls=[];const view=mount(component,{api:async url=>{calls.push(url);return result();},companyId:1,currency:'UZS',today:'2026-11-08'});t.after(view.unmount);
 await settle();assert.equal(calls.length,0);
 view.state.opened=true;await settle();assert.equal(calls.length,1);assert.match(calls[0],/currency=UZS/);
 assert.match(text(view.container),/Фактически оплачено/);
 view.state.opened=false;await settle();view.app._instance.props.refresh=1;await settle();assert.equal(calls.length,1);
});
test('late reconciliation data cannot cross companies',async t=>{
 const pending=[];const view=mount(component,{api:()=>new Promise(resolve=>pending.push(resolve)),companyId:1,currency:'UZS',today:'2026-11-08'});t.after(view.unmount);
 view.state.opened=true;await settle();view.app._instance.props.companyId=2;await settle();assert.equal(pending.length,2);
 pending[1](result({partner_fees:'123'}));await settle();pending[0](result({partner_fees:'999'}));await settle();assert.equal(view.state.data.partner_fees,'123');
});
test('a failed refresh removes stale financial totals',async t=>{
 let fail=false;const view=mount(component,{api:async()=>{if(fail)throw Error('Synthetic error');return result();},companyId:1,currency:'UZS',today:'2026-11-08'});t.after(view.unmount);
 view.state.opened=true;await settle();assert.ok(view.state.data);fail=true;await view.state.load();await settle();assert.equal(view.state.data,null);assert.match(text(view.container),/Synthetic error/);
});
