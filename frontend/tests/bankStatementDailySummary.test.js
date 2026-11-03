import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text} from './helpers/mountVue.js';
import {lang,setLang} from '../src/i18n/index.js';

const component=await compileComponent(new URL('../src/overview/BankStatementDaily.vue',import.meta.url));
const result=(date,currency,credit,extras={})=>({date,currency,credit_total:credit,debit_total:'0.00',accounts:[],available:true,uncertain:false,...extras});

test('daily statement totals stay separate by currency, show uncertainty and distinguish absent from zero',async t=>{
 const calls=[];
 const api=async url=>{calls.push(url);const q=new URL(url,'https://example.invalid').searchParams;const cur=q.get('currency'),date=q.get('date');
   if(cur==='USD')return result(date,cur,'0.00',{available:false});
   if(cur==='EUR')return result(date,cur,'0.00');
   return result(date,cur,'1250.50',{uncertain:true});};
 const view=mount(component,{api,companyId:1,currency:'UZS',today:'2026-10-30'});t.after(view.unmount);await settle();
 assert.deepEqual(calls.map(url=>new URL(url,'https://example.invalid').searchParams.get('currency')),['UZS','USD','EUR']);
 assert.equal(view.state.currencyTotals.UZS.data.credit_total,'1250.50');
 assert.equal(view.state.currencyTotals.USD.data.available,false);
 assert.equal(view.state.currencyTotals.EUR.data.available,true);
 const content=text(view.container);assert.match(content,/Поступления по валютам за день/);
 assert.match(content,/1\s*250,50 UZS/);assert.match(content,/Нет выписки/);assert.match(content,/0,00 EUR/);
 assert.match(content,/Требует проверки/);assert.match(content,/В Cash Flow попадают только проведённые операции/);
});

test('an error in one currency does not suppress the others or become a zero',async t=>{
 const api=async url=>{const q=new URL(url,'https://example.invalid').searchParams;const c=q.get('currency');if(c==='USD')throw Error('network');return result(q.get('date'),c,c==='EUR'?'5.75':'100.00');};
 const view=mount(component,{api,companyId:1,currency:'UZS',today:'2026-10-30'});t.after(view.unmount);await settle();
 assert.equal(view.state.currencyTotals.USD.error,true);assert.equal(view.state.currencyTotals.EUR.data.credit_total,'5.75');
 assert.match(text(view.container),/Ошибка загрузки/);assert.match(text(view.container),/5,75 EUR/);
 assert.match(text(view.container),/100 UZS/);
});

test('late results cannot cross a company or a date',async t=>{
 let pending=[];
 const api=url=>new Promise(resolve=>{const q=new URL(url,'https://example.invalid').searchParams;pending.push({currency:q.get('currency'),date:q.get('date'),resolve});});
 const view=mount(component,{api,companyId:1,currency:'UZS',today:'2026-10-30'});t.after(view.unmount);await settle();assert.equal(pending.length,3);
 view.app._instance.props.companyId=2;await settle();assert.equal(pending.length,6);
 for(const item of pending.slice(3,6))item.resolve(result(item.date,item.currency,'11.00'));
 await settle();for(const item of pending.slice(0,3))item.resolve(result(item.date,item.currency,'999.00'));
 await settle();assert.equal(view.state.currencyTotals.UZS.data.credit_total,'11.00');
 view.state.date='2026-10-31';await settle();assert.equal(pending.length,9);
 for(const item of pending.slice(6,9))item.resolve(result(item.date,item.currency,'22.00'));
 await settle();assert.equal(view.state.currencyTotals.UZS.data.date,'2026-10-31');assert.equal(view.state.currencyTotals.UZS.data.credit_total,'22.00');
});

test('currency totals and availability are translated in Uzbek',async t=>{
 const previous=lang.value;setLang('uz');t.after(()=>setLang(previous));
 const view=mount(component,{api:async url=>{const q=new URL(url,'https://example.invalid').searchParams;return result(q.get('date'),q.get('currency'),'0.00',{available:false});},companyId:1,currency:'UZS',today:'2026-10-30'});t.after(view.unmount);await settle();
 assert.match(text(view.container),/Kunlik tushumlar valyutalar bo‘yicha/);assert.match(text(view.container),/Ko‘chirma yo‘q/);
});
