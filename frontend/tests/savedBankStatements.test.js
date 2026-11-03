import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent, mount, settle, text, find} from './helpers/mountVue.js';
import {lang, setLang} from '../src/i18n/index.js';

const saved = await compileComponent(new URL('../src/SavedBankStatements.vue',import.meta.url));
const history = await compileComponent(new URL('../src/BankStatementHistory.vue',import.meta.url));
const component = await compileComponent(new URL('../src/BankStatements.vue',import.meta.url), {'./SavedBankStatements.vue':saved,'./BankStatementHistory.vue':history});
const accounts = [{id:21,name:'Synthetic bank',kind:'bank',currency:'USD'},{id:22,name:'Synthetic cash',kind:'cash',currency:'UZS'}];
const item = {id:7,status:'preview',filename:'synthetic-saved.xls',account:{id:21,name:'Synthetic bank',currency:'USD'},period_from:'2026-10-01',period_to:'2026-10-31',credit_total:'30.00',debit_total:'10.00',created_at:'2026-11-03T01:00:00'};
const preview = {id:7,status:'preview',preview_token:'fresh-7',full_reconciliation:true,account:item.account,
  statement:{account_number:'SYNTHETIC-21',ready:true,balance_matches:true,declared_totals_match:true,period_from:item.period_from,period_to:item.period_to,opening_balance:'100.00',closing_balance:'120.00',credit_total:'30.00',debit_total:'10.00'},
  rows:[{id:71,date:'2026-10-31',direction:'in',amount:'30.00',status:'income_candidate',correspondent_name:'Synthetic payer',purpose:'Synthetic receipt'}]};
const button = (view,label) => find(view.container,n=>n.tag==='button' && text(n)===label);
const emptyPage = {page:1,has_more:false,items:[]};
function apiWith(calls, extra) {return async (url,options={})=>{
  calls.push([url,options]);
  if (url==='/api/bank-statements/settings') return {fee_category_id:null};
  if (url==='/api/bank-statements/reviews' || url==='/api/bank-statements/reprocess/batches') return {items:[]};
  if (url.includes('/previews?')) return {page:1,has_more:false,items:[item]};
  return extra(url,options);
};}

test('saved checks load lazily using GET, show account and period, paginate, and only emit selected server entries', async t=>{
  const calls=[],opened=[];
  const api=async(url,options={})=>{calls.push([url,options]);return {page:url.includes('page=2')?2:1,has_more:!url.includes('page=2'),items:[item,{...item,id:8,filename:'admin-authorized.xls'}]};};
  const view=mount(saved,{api,companyId:1,canResume:true,onResume:value=>opened.push(value)});t.after(view.unmount);
  await settle();assert.deepEqual(calls,[]);
  await view.state.toggle();await settle();
  assert.equal(calls[0][0],'/api/bank-statements/previews?page=1&page_size=30');
  assert.match(text(view.container),/synthetic-saved.xls/);assert.match(text(view.container),/Synthetic bank · USD/);assert.match(text(view.container),/1 окт. – 31 окт./);
  view.state.resume({...item,id:99});assert.deepEqual(opened,[]);
  view.state.resume(item);view.state.resume(view.state.items[1]);assert.deepEqual(opened.map(value=>value.id),[7,8]);
  assert.ok(calls.every(([,options])=>!options.method || options.method==='GET'));
  await view.state.loadPage(2);assert.equal(view.state.page,2);assert.equal(view.state.hasMore,false);
});

test('saved list access, company changes, close, and refresh discard stale data without automatically reopening',async t=>{
  let finish;const calls=[];
  const api=async(url,options={})=>{calls.push([url,options]);return new Promise(resolve=>finish=resolve);};
  const view=mount(saved,{api,companyId:1,canResume:false});t.after(view.unmount);
  await view.state.toggle();assert.equal(calls.length,0);assert.doesNotMatch(text(view.container),/Сохранённые проверки/);
  view.app._instance.props.canResume=true;await settle();let pending=view.state.toggle();await settle();
  view.app._instance.props.companyId=2;await settle();finish({page:1,has_more:false,items:[item]});await pending;
  assert.equal(view.state.open,false);assert.deepEqual(view.state.items,[]);
  pending=view.state.toggle();await settle();await view.state.toggle();finish({page:1,has_more:false,items:[item]});await pending;assert.deepEqual(view.state.items,[]);
  view.app._instance.props.refreshKey=1;await settle();assert.equal(calls.length,2);
  pending=view.state.toggle();await settle();finish({page:1,has_more:false,items:[item]});await pending;
  view.app._instance.props.canResume=false;await settle();assert.equal(view.state.open,false);assert.deepEqual(view.state.items,[]);
});

test('saved list rejects non-drafts and clears an old page before a failed refresh',async t=>{
  let response={page:1,has_more:false,items:[item]};const view=mount(saved,{api:async()=>response,companyId:1,canResume:true});t.after(view.unmount);
  await view.state.toggle();assert.equal(view.state.items.length,1);
  response={page:1,has_more:false,items:[{...item,status:'committed'}]};await view.state.loadPage(1);await settle();
  assert.deepEqual(view.state.items,[]);assert.match(text(view.container),/Не удалось проверить список сохранённых проверок/);
});

test('open saved check selects its account and currency, refreshes the signed preview, and needs both explicit confirmations',async t=>{
  const calls=[],changed=[];
  const api=apiWith(calls,async url=>{if(url.endsWith('/resume'))return preview;if(url.endsWith('/commit'))return {auto_paid:0,linked_paid:0,review:0,auto_income:1,full_reconciliation:true};throw Error('unexpected');});
  const view=mount(component,{api,companyId:1,accounts,canImport:true,onChanged:()=>changed.push(true)});t.after(view.unmount);await settle();
  assert.equal(calls.filter(([url])=>url.includes('/previews?')).length,0);
  await button(view,'Показать сохранённые проверки').props.onClick();await settle();
  button(view,'Открыть проверку').props.onClick();await settle();
  assert.equal(view.state.accountId,'21');assert.equal(view.state.currency,'USD');assert.equal(view.state.preview.preview_token,'fresh-7');
  assert.equal(view.state.confirmAccount,false);assert.equal(view.state.confirmCurrency,false);assert.equal(view.state.ready,false);
  const resume=calls.find(([url])=>url.endsWith('/resume'));assert.equal(resume[1].method,'POST');assert.deepEqual(JSON.parse(resume[1].body),{fee_category_id:null});
  assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,0);assert.equal(changed.length,0);
  await view.state.commit();view.state.confirmAccount=true;await settle();await view.state.commit();assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,0);
  view.state.confirmCurrency=true;await settle();await view.state.commit();await settle();
  assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,1);assert.equal(changed.length,1);assert.equal(view.state.preview,null);
  assert.deepEqual(JSON.parse(calls.find(([url])=>url.endsWith('/commit'))[1].body),{confirm_account:true,confirm_currency:true,fee_category_id:null,preview_token:'fresh-7'});
});

test('resume waits for the delayed company fee setting and watcher changes do not erase its fresh preview',async t=>{
  let finishSetting;const calls=[];
  const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/settings')return new Promise(resolve=>finishSetting=resolve);if(url.endsWith('/resume'))return preview;throw Error('unexpected');};
  const view=mount(component,{api,companyId:1,accounts,categories:[{id:4,name:'Synthetic fee',type:'outcome'}],canImport:true});t.after(view.unmount);await settle();
  const pending=view.state.resumeSaved(item);await settle();assert.equal(calls.filter(([url])=>url.endsWith('/resume')).length,0);
  finishSetting({fee_category_id:4});await pending;await settle();
  assert.deepEqual(JSON.parse(calls.find(([url])=>url.endsWith('/resume'))[1].body),{fee_category_id:4});
  assert.equal(view.state.preview.id,7);assert.equal(view.state.feeCategoryId,'4');assert.equal(view.state.busy,false);
  view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();await view.state.resumeSaved(item);await settle();
  assert.equal(view.state.confirmAccount,false);assert.equal(view.state.confirmCurrency,false);assert.equal(view.state.preview.preview_token,'fresh-7');
});

test('resume cannot cross a company switch or a revoked permission while fee settings or preview are loading',async t=>{
  let finish;let setting=Promise.resolve({fee_category_id:null});const calls=[];
  const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/settings')return setting;if(url.endsWith('/resume'))return new Promise(resolve=>finish=resolve);return {items:[]};};
  const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();
  let pending=view.state.resumeSaved(item);await settle();view.app._instance.props.companyId=2;await settle();finish(preview);await pending;assert.equal(view.state.preview,null);assert.equal(view.state.accountId,'');
  pending=view.state.resumeSaved(item);await settle();view.app._instance.props.canImport=false;await settle();finish(preview);await pending;assert.equal(view.state.preview,null);assert.equal(view.state.busy,false);
  await view.state.resumeSaved(item);assert.equal(calls.filter(([url])=>url.endsWith('/resume')).length,2);
  view.app._instance.props.canImport=true;setting=new Promise(resolve=>finish=resolve);view.app._instance.props.companyId=3;await settle();
  pending=view.state.resumeSaved(item);await settle();view.app._instance.props.companyId=4;await settle();finish({fee_category_id:null});await pending;
  assert.equal(calls.filter(([url])=>url.endsWith('/resume')).length,2);assert.equal(view.state.preview,null);
});

test('archived or missing account is explained locally without resuming or signing a mismatched preview',async t=>{
  const calls=[];const api=apiWith(calls,async()=>preview);
  const view=mount(component,{api,companyId:1,accounts:[{...accounts[0],archived:true}],canImport:true});t.after(view.unmount);await settle();
  await view.state.resumeSaved(item);await settle();assert.match(text(view.container),/Счёт сохранённой выписки недоступен/);assert.equal(calls.filter(([url])=>url.endsWith('/resume')).length,0);assert.equal(view.state.preview,null);
  view.app._instance.props.accounts=accounts;await settle();await view.state.resumeSaved(item);assert.equal(view.state.preview.id,7);
  view.app._instance.props.accounts=[];await settle();assert.equal(view.state.preview,null);assert.match(text(view.container),/Счёт сохранённой выписки недоступен/);
});

test('resume rejects a stale, malformed, or wrong-account server response before it can be confirmed',async t=>{
  let result={...preview,account:{...preview.account,id:99}};const calls=[];
  const view=mount(component,{api:apiWith(calls,async()=>result),companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();
  for (const bad of [result,{...preview,status:'committed'},{...preview,preview_token:null},{...preview,rows:null},{...preview,full_reconciliation:undefined}]) {
    result=bad;await view.state.resumeSaved(item);view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();await view.state.commit();
    assert.equal(view.state.preview,null);assert.equal(view.state.ready,false);
  }
  assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,0);
});

test('an honest saved source with unconfirmed balances opens for inspection and cannot be committed',async t=>{
  const calls=[];const result={...preview,full_reconciliation:false,statement:{...preview.statement,ready:false,balance_matches:false}};
  const view=mount(component,{api:apiWith(calls,async()=>result),companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();await view.state.resumeSaved(item);
  view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();await view.state.commit();
  assert.equal(view.state.preview.id,7);assert.equal(view.state.ready,false);assert.match(text(view.container),/Остатки или обороты выписки не подтверждены/);assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,0);
});

test('lost resume reply can retry the same saved draft safely, without an upload or automatic commit',async t=>{
  let attempt=0;const calls=[];
  const api=apiWith(calls,async url=>{if(url.endsWith('/resume')){if(++attempt===1)throw Error('lost reply');return preview;}throw Error('unexpected');});
  const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();
  await view.state.resumeSaved(item);await settle();assert.equal(view.state.preview,null);assert.equal(view.state.busy,false);assert.match(text(view.container),/Повторите открытие/);
  await view.state.resumeSaved(item);assert.equal(view.state.preview.id,7);assert.equal(attempt,2);
  assert.equal(calls.filter(([url])=>url.includes('/preview?') || url.endsWith('/commit')).length,0);
});

test('stale or denied saved drafts clear the preview and refresh the list; committed drafts refresh facts without another commit',async t=>{
  const calls=[],changed=[];let status=403;
  const api=apiWith(calls,async()=>{throw Object.assign(Error(status===403?'Нет доступа':'Эта выписка уже обработана. Откройте её в истории выписок.'),{status});});
  const view=mount(component,{api,companyId:1,accounts,canImport:true,onChanged:()=>changed.push(true)});t.after(view.unmount);await settle();
  for(const code of [403,404,409]){status=code;const refresh=view.state.savedRefresh;await view.state.resumeSaved(item);assert.equal(view.state.preview,null);assert.ok(view.state.savedRefresh>refresh);}
  assert.equal(changed.length,2);assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,0);
});

test('successful confirmation removes a saved draft from the visible list and emits the existing refresh event once',async t=>{
  let accepted=false,changed=0;const calls=[];
  const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.includes('/previews?'))return accepted?emptyPage:{...emptyPage,items:[item]};if(url.endsWith('/resume'))return preview;if(url.endsWith('/commit')){accepted=true;return {auto_paid:0,linked_paid:0,review:0,auto_income:1,full_reconciliation:true};}return {items:[]};};
  const view=mount(component,{api,companyId:1,accounts,canImport:true,onChanged:()=>changed++});t.after(view.unmount);await settle();
  await button(view,'Показать сохранённые проверки').props.onClick();await view.state.resumeSaved(item);view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();await view.state.commit();await settle();
  assert.doesNotMatch(text(view.container),/synthetic-saved.xls/);assert.match(text(view.container),/Подтверждённые выписки доступны в истории/);assert.equal(changed,1);await view.state.commit();assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,1);
});

test('uncertain accepted confirmation refreshes its state through resume and never repeats a financial command',async t=>{
  let accepted=false,changed=0;const calls=[];
  const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.includes('/previews?'))return accepted?emptyPage:{...emptyPage,items:[item]};if(url.endsWith('/resume')){if(accepted)throw Object.assign(Error('Эта выписка уже обработана. Откройте её в истории выписок.'),{status:409});return preview;}if(url.endsWith('/commit')){accepted=true;throw Error('lost reply');}return {items:[]};};
  const view=mount(component,{api,companyId:1,accounts,canImport:true,onChanged:()=>changed++});t.after(view.unmount);await settle();await button(view,'Показать сохранённые проверки').props.onClick();
  await view.state.resumeSaved(item);view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();await view.state.commit();await settle();assert.equal(view.state.uncertain,true);assert.ok(button(view,'Обновить состояние выписки'));
  await view.state.commit();await view.state.refreshUncertain();await settle();await view.state.commit();
  assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,1);assert.equal(view.state.preview,null);assert.equal(changed,1);assert.doesNotMatch(text(view.container),/synthetic-saved.xls/);assert.match(text(view.container),/уже обработана/);
});

test('saved check interface and repeatable resume errors are translated to Uzbek',async t=>{
  const prior=lang.value;setLang('uz');t.after(()=>setLang(prior));const calls=[];
  const view=mount(component,{api:apiWith(calls,async()=>{throw Error('lost reply');}),companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();
  await button(view,'Saqlangan tekshiruvlarni ko‘rsatish').props.onClick();await view.state.resumeSaved(item);await settle();
  assert.match(text(view.container),/Saqlangan tekshiruvlar/);assert.match(text(view.container),/Tekshiruvni ochish/);assert.match(text(view.container),/yangi ko‘chirma va operatsiyalar yaratilmaydi/);
});

test('an open confirmed history refreshes after saved confirmation and exposes the accepted statement without reopening',async t=>{
  let accepted=false;const calls=[];
  const api=async(url,options={})=>{calls.push([url,options]);if(url==='/api/bank-statements/settings')return {fee_category_id:null};
    if(url.includes('/history?'))return {...emptyPage,items:accepted?[{...item,status:'committed'}]:[]};
    if(url.endsWith('/history/7'))return {...preview,filename:item.filename,status:'committed'};
    if(url.endsWith('/resume'))return preview;
    if(url.endsWith('/commit')){accepted=true;return {auto_paid:0,linked_paid:0,review:0,auto_income:1,full_reconciliation:true};}
    return {items:[]};
  };
  const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();
  await button(view,'Показать историю выписок').props.onClick();await settle();assert.match(text(view.container),/Подтверждённых выписок нет/);
  await view.state.resumeSaved(item);view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();await view.state.commit();await settle();
  assert.match(text(view.container),/synthetic-saved.xls/);assert.ok(button(view,'Скрыть историю выписок'));assert.doesNotMatch(text(view.container),/Подтверждённых выписок нет/);
  await button(view,'Открыть выписку').props.onClick();await settle();assert.ok(calls.some(([url])=>url.endsWith('/history/7')));assert.equal(calls.filter(([url])=>url.endsWith('/commit')).length,1);
});

test('a failed open-history refresh clears stale items and shows the read error after accepted confirmation',async t=>{
  let accepted=false;
  const api=async url=>{if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.includes('/history?')){if(accepted)throw Error('Synthetic history unavailable');return {...emptyPage,items:[{...item,id:8,filename:'OLD-SYNTHETIC-HISTORY.xls'}]};}
    if(url.endsWith('/resume'))return preview;if(url.endsWith('/commit')){accepted=true;return {auto_paid:0,linked_paid:0,review:0,auto_income:1,full_reconciliation:true};}return {items:[]};};
  const view=mount(component,{api,companyId:1,accounts,canImport:true});t.after(view.unmount);await settle();
  await button(view,'Показать историю выписок').props.onClick();await settle();assert.match(text(view.container),/OLD-SYNTHETIC-HISTORY.xls/);
  await view.state.resumeSaved(item);view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();await view.state.commit();await settle();
  assert.doesNotMatch(text(view.container),/OLD-SYNTHETIC-HISTORY.xls/);assert.match(text(view.container),/Synthetic history unavailable/);assert.match(text(view.container),/Выписка принята/);
});

test('saved preview refresh audit action is translated in the action filter and visible audit event',async t=>{
  const prior=lang.value;setLang('uz');t.after(()=>setLang(prior));
  const audit=await compileComponent(new URL('../src/AuditHistory.vue',import.meta.url));
  const action='Обновлён предпросмотр банковской выписки';
  const api=async()=>({items:[{id:1,user:'Synthetic user',action,entity:'bank_statement',entity_id:'7',date:'03.11.2026',detail:''}],total:1,users:[],actions:[action]});
  const view=mount(audit,{api,refresh:0});t.after(view.unmount);await settle();await view.state.load();await settle();
  assert.match(text(view.container),/Bank ko‘chirmasining oldindan ko‘rishi yangilandi/);assert.doesNotMatch(text(view.container),/Обновлён предпросмотр банковской выписки/);
});
