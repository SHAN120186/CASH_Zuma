import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent, mount, settle, text, find} from './helpers/mountVue.js';

const component = await compileComponent(new URL('../src/overview/StatementPeriod.vue', import.meta.url));
const uncovered = await compileComponent(new URL('../src/overview/UncoveredBankPayments.vue', import.meta.url));
const account = {id:11, kind:'bank', currency:'UZS', name:'Test Bank'};
const props = api => ({api, companyId:2, accounts:[account], currency:'UZS'});
const source = {id:7, filename:'test.xls', period_from:'2026-11-01', period_to:'2026-11-21'};
const report = () => ({
  account, binding:'confirmed',
  statement:{...source, owner_name:'Test company', account_number:'0011', opening_balance:'300.01', credit_total:'100.02',
    debit_total:'150.00', closing_balance:'250.03', ready:true, balance_matches:true, declared_totals_match:true},
  site:{opening_balance:'300.01', closing_balance:'200.00'},
  rows:[{line:3,date:'2026-11-10',document_number:'178',direction:'out',correspondent_name:'Bank supplier',
    status:'candidate',request_ids:[42],amount:'150.00'}],
  unmatched_payments:[{request_id:17,date:'2026-11-11',amount:'15.00',document_number:'370',counterparty:'Site supplier',status:'app_only'}],
});
test('period comparison fetches saved sources only when opened, then reads the selected period on demand', async t => {
  const calls = [];
  const view = mount(component, props(async (url, opts) => {
    calls.push({url,opts}); return url.includes('/list?') ? {items:[source]} : report();
  }));
  t.after(view.unmount); await settle();
  assert.equal(calls.length,0);
  view.state.opened = true; await settle();
  assert.match(calls[0].url,/period-sources\/list\?account_id=11&currency=UZS$/);
  assert.equal(calls.length,1);
  await view.state.compare(); await settle();
  assert.match(calls[1].url,/period-sources\/7\?account_id=11&currency=UZS$/);
  assert.equal(calls[1].opts,undefined);
  assert.match(text(view.container),/150.*250,03/s);
  assert.match(text(view.container),/\+50,03/);
  assert.match(text(view.container),/CF-00042/);
  view.state.tab = 'site'; await settle();
  assert.match(text(view.container),/Нет связи с выпиской/);
  assert.match(text(view.container),/CF-00017/);
  assert.doesNotMatch(text(view.container),/Bank supplier/);
  assert.equal(calls.length,2);
});
test('uploaded file only goes to read-only comparison and a new file clears evidence', async t => {
  const calls=[];
  const view=mount(component,props(async (url,opts) => {
    calls.push({url,opts}); return url.includes('/list?') ? {items:[]} : report();
  }));
  t.after(view.unmount); view.state.opened=true; await settle();
  const file = new Blob(['synthetic']); file.name='Тест.xlsx';
  view.state.chooseFile({target:{files:[file]}}); await settle();
  assert.equal(calls.length,1);
  await view.state.compare(); await settle();
  assert.match(calls[1].url,/\/compare-period\?account_id=11&currency=UZS$/);
  assert.equal(calls[1].opts.method,'POST');
  assert.equal(calls[1].opts.body,file);
  assert.equal(decodeURIComponent(calls[1].opts.headers['X-Filename']),file.name);
  view.state.chooseFile({target:{files:[]}}); await settle();
  assert.equal(view.state.data,null);
  assert.equal(view.state.ready,false);
});
test('a late response cannot restore rows across a change of company; mismatched responses are rejected', async t => {
  let finish;
  const view=mount(component,props(async url => url.includes('/list?') ? {items:[source]} : new Promise(resolve => {finish=resolve;})));
  t.after(view.unmount); view.state.opened=true; await settle();
  const request=view.state.compare(); await settle();
  view.app._instance.props.companyId=3; await settle();
  finish(report()); await request; await settle();
  assert.equal(view.state.data,null);
  const next=view.state.compare(); await settle();
  finish({...report(),account:{...account,id:22}}); await next; await settle();
  assert.equal(view.state.data,null);
  assert.ok(find(view.container,n=>n.props?.role==='alert'));
});
test('unbound file shows bank totals, withholding site rows until the selected account and currency are acknowledged', async t => {
  const calls=[];
  const view=mount(component,props(async url => {
    calls.push(url);
    if (url.includes('/list?')) return {items:[]};
    const result=report();
    return url.includes('confirm_account=true') ? result : {...result,requires_confirmation:true,binding:'owner_only'};
  }));
  t.after(view.unmount); view.state.opened=true; await settle();
  const file=new Blob(['test']); file.name='test.xlsx';
  view.state.chooseFile({target:{files:[file]}}); await view.state.compare(true); await settle();
  assert.doesNotMatch(calls[1],/confirm_account/);
  assert.match(text(view.container),/250,03/);
  assert.doesNotMatch(text(view.container),/CF-00042|На конец в Cash Zuma/);
  const confirm=find(view.container,n=>n.tag==='button'&&text(n).includes('Подтвердить счёт и валюту'));
  await confirm.props.onClick(); await settle();
  assert.match(calls[2],/confirm_account=true/);
  assert.match(text(view.container),/CF-00042/);
});
test('a failed saved-source lookup still permits file comparison; retry never retains old totals', async t => {
  let fail=false;
  const view=mount(component,props(async url => {
    if (url.includes('/list?')) throw Error('source unavailable');
    if (fail) throw Error('comparison unavailable');
    return report();
  }));
  t.after(view.unmount); view.state.opened=true; await settle();
  assert.equal(view.state.source,'file');
  const file=new Blob(['test']); file.name='statement.xls';
  view.state.chooseFile({target:{files:[file]}}); await view.state.compare(); await settle();
  assert.ok(view.state.data);
  fail=true; await view.state.compare(); await settle();
  assert.equal(view.state.data,null);
  assert.match(text(view.container),/comparison unavailable/);
});
test('uncovered payment lookup works without a file, is explicit, paginates and pins dates to site today', async t => {
  const calls=[];
  const rows=Array.from({length:31},(_,i)=>({request_id:i+1,counterparty:'Fixture supplier',document_number:'T'+(i+1),amount:'1.20',date:'2026-11-22'}));
  const view=mount(uncovered,{account,today:'2026-11-22',api:async url=>{
    calls.push(url);return {account,date_from:'2026-11-22',date_to:'2026-11-22',rows};
  }});
  t.after(view.unmount);
  view.state.opened=true; await settle(); assert.deepEqual(calls,[]);
  await view.state.load(); await settle();
  assert.match(calls[0],/uncovered-payments\?account_id=11&currency=UZS&date_from=2026-11-22&date_to=2026-11-22$/);
  assert.match(text(view.container),/CF-00030/); assert.doesNotMatch(text(view.container),/CF-00031/);
  find(view.container,n=>n.tag==='button'&&text(n)==='Показать ещё').props.onClick(); await settle();
  assert.match(text(view.container),/CF-00031/); assert.equal(calls.length,1);
  view.state.from='2026-08-22'; await settle(); await view.state.load();
  assert.equal(view.state.rows,null);assert.equal(calls.length,1);assert.match(text(view.container),/92 дней/);
  view.state.from='2026-08-23'; await settle(); assert.equal(view.state.valid,true);
});
test('uncovered payments do not display an earlier query or mismatched account', async t => {
  let respond;
  const view=mount(uncovered,{account,today:'2026-11-22',api:()=>new Promise(resolve=>{respond=resolve;})});
  t.after(view.unmount);view.state.opened=true;await settle();
  const pending=view.state.load();view.state.from='2026-11-19';
  respond({account,date_from:'2026-11-22',date_to:'2026-11-22',rows:[{request_id:1}]});await pending;
  assert.equal(view.state.rows,null);assert.equal(view.state.error,'');
  const next=view.state.load();respond({account:{...account,id:23},date_from:'2026-11-19',date_to:'2026-11-22',rows:[]});await next;await settle();
  assert.equal(view.state.rows,null);assert.ok(find(view.container,n=>n.props?.role==='alert'));
});
