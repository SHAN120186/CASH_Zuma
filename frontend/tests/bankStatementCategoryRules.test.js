import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text,all,find} from './helpers/mountVue.js';
import {lang,setLang} from '../src/i18n/index.js';

const component=await compileComponent(new URL('../src/BankStatementCategoryRules.vue',import.meta.url));
const bank=await compileComponent(new URL('../src/BankStatements.vue',import.meta.url),{'./BankStatementCategoryRules.vue':component});
const endpoint='/api/bank-statements/category-rules';
const revision='a'.repeat(64),nextRevision='b'.repeat(64);
const categories=[{id:1,name:'Synthetic income',type:'income'},{id:2,name:'Synthetic expense',type:'outcome'},{id:3,name:'Retired expense',type:'outcome',retired:true},{id:4,name:'Technical fallback',type:'outcome',activity:'unclassified'},{id:5,name:'Second expense',type:'outcome'}];
const rule=(extra={})=>({id:'rule-1',direction:'out',currency:null,inn:'123456789',counterparty:'',purpose_contains:'',contract_contains:'',category_id:2,category_name:'Synthetic expense',category_valid:true,enabled:true,...extra});
const snapshot=(extra={})=>({revision,can_manage:true,items:[rule()],warning:'',...extra});
function setup(t,api,extra={}){const view=mount(component,{api,companyId:1,categories,canView:true,canManage:true,...extra});t.after(view.unmount);return view;}
const button=(view,label)=>find(view.container,node=>node.tag==='button'&&text(node)===label);
async function click(view,label){const node=button(view,label);assert.ok(node,`Missing button ${label}`);assert.ok(!node.props.disabled,`Disabled button ${label}`);const result=node.props.onClick();await settle();return result;}
async function set(view,label,value){const wrapper=find(view.container,node=>node.tag==='label'&&text(node).startsWith(label));assert.ok(wrapper,`Missing label ${label}`);const input=find(wrapper,node=>['input','select'].includes(node.tag));input.props['onUpdate:modelValue'](value);await settle();}
async function stage(view){const form=find(view.container,node=>node.tag==='form');assert.ok(form);assert.ok(!find(form,node=>node.tag==='button'&&node.props.type==='submit').props.disabled);form.props.onSubmit({preventDefault(){}});await settle();}

test('rules stay closed and perform only a GET when explicitly opened',async t=>{
 const calls=[];const view=setup(t,async(...args)=>{calls.push(args);return snapshot()});await settle();
 assert.equal(text(view.container),'Правила статей');assert.equal(all(view.container,node=>node.tag==='input').length,0);assert.deepEqual(calls,[]);
 await click(view,'Правила статей');assert.deepEqual(calls,[[endpoint]]);assert.match(text(view.container),/Synthetic expense/);
 const help=find(view.container,node=>node.tag==='details');assert.ok(help);assert.ok(!help.props.open);
 await click(view,'Правила статей');await click(view,'Правила статей');assert.equal(calls.length,1);
});

test('both server and local permissions are required to modify rules',async t=>{
 for(const [canManage,serverCanManage] of [[true,false],[false,true]]){
  const calls=[];const view=setup(t,async(url,options)=>{calls.push([url,options]);return snapshot({can_manage:serverCanManage})},{canManage});await click(view,'Правила статей');
  assert.match(text(view.container),/только для просмотра/);assert.equal(button(view,'Добавить правило'),undefined);assert.equal(button(view,'Сохранить правила'),undefined);
  view.state.edit();view.state.toggleEnabled(rule());view.state.remove(rule());await view.state.save([]);assert.equal(view.state.editor,null);assert.equal(view.state.items.length,1);assert.equal(calls.length,1);
 }
 const hidden=setup(t,async()=>{throw Error('Hidden panel must not load')},{canView:false});await settle();assert.equal(text(hidden.container),'');await hidden.state.toggle();assert.equal(hidden.state.open,false);
});

test('an explicit local draft and one final CAS save persist all AND conditions without guessing',async t=>{
 const calls=[];let resolveSave;
 const view=setup(t,async(url,options)=>{calls.push([url,options]);if(!options)return snapshot();return new Promise(resolve=>resolveSave=resolve)});await click(view,'Правила статей');await click(view,'Добавить правило');
 assert.deepEqual(['inn','counterparty','purpose_contains','contract_contains'].map(key=>view.state.editor[key]),['','','','']);
 assert.deepEqual(all(find(view.container,node=>node.tag==='label'&&text(node).startsWith('Статья')),node=>node.tag==='option').map(node=>text(node)),['Выберите статью','Synthetic expense','Second expense']);
 await set(view,'Движение','in');await set(view,'Валюта','USD');await set(view,'ИНН контрагента','987654321');await set(view,'Точное название контрагента','Synthetic payer');await set(view,'Назначение содержит','Delivery');await set(view,'Договор / счёт содержит','12/345');await set(view,'Статья','1');await stage(view);
 assert.equal(calls.length,1);assert.match(text(view.container),/несохранённые изменения/);assert.equal(button(view,'Обновить правила').props.disabled,true);
 const save=button(view,'Сохранить правила');const pending=save.props.onClick();const double=save.props.onClick();await settle();assert.equal(calls.length,2);
 const payload=JSON.parse(calls[1][1].body);assert.equal(calls[1][1].method,'PUT');assert.equal(payload.revision,revision);assert.deepEqual(payload.items,[{id:'rule-1',direction:'out',currency:null,inn:'123456789',counterparty:'',purpose_contains:'',contract_contains:'',category_id:2,enabled:true},{direction:'in',currency:'USD',inn:'987654321',counterparty:'Synthetic payer',purpose_contains:'Delivery',contract_contains:'12/345',category_id:1,enabled:true}]);
 resolveSave(snapshot({revision:nextRevision,items:[rule(),rule({...payload.items[1],id:'server-generated',category_name:'Synthetic income'})]}));await Promise.all([pending,double]);await settle();
 assert.equal(view.state.dirty,false);assert.equal(view.state.items[1].id,'server-generated');assert.match(text(view.container),/Правила статей сохранены/);
});

test('criterion validation blocks broad rules but accepts explicit contract numbers',async t=>{
 const view=setup(t,async()=>snapshot({items:[]}));await click(view,'Правила статей');await click(view,'Добавить правило');await set(view,'Статья','2');
 const valid=()=>!find(view.container,node=>node.tag==='button'&&node.props.type==='submit').props.disabled;
 assert.equal(valid(),false);await set(view,'Назначение содержит','12345');assert.equal(valid(),false);await set(view,'Назначение содержит','abc');assert.equal(valid(),false);await set(view,'Назначение содержит','');await set(view,'Договор / счёт содержит','12/345');assert.equal(valid(),true);
 await set(view,'ИНН контрагента','12');assert.equal(valid(),false);await set(view,'ИНН контрагента','123456789');assert.equal(valid(),true);await set(view,'Назначение содержит','12345');assert.equal(valid(),true);
 await stage(view);assert.equal(view.state.items.length,1);assert.equal(view.state.items[0].contract_contains,'12/345');
});

test('editing direction clears an incompatible category and disable or delete remain reversible drafts',async t=>{
 const calls=[];const view=setup(t,async(...args)=>{calls.push(args);return snapshot()});await click(view,'Правила статей');await click(view,'Изменить');await set(view,'Движение','in');assert.equal(view.state.editor.category_id,'');await set(view,'Статья','1');await stage(view);
 await click(view,'Отключить');assert.equal(view.state.items[0].enabled,false);await click(view,'Удалить');assert.equal(view.state.items.length,0);assert.equal(calls.length,1);
 await click(view,'Отменить изменения');assert.equal(view.state.items[0].direction,'out');assert.equal(view.state.items[0].enabled,true);assert.equal(view.state.dirty,false);assert.equal(calls.length,1);
});

test('multiple unavailable article targets can be repaired before the single replacement save',async t=>{
 const calls=[];const invalid=[rule({category_id:30,category_name:'Retired A',category_valid:false}),rule({id:'rule-2',category_id:31,category_name:'Retired B',category_valid:false})];
 const view=setup(t,async(url,options)=>{calls.push([url,options]);return options?snapshot({revision:nextRevision,items:JSON.parse(options.body).items.map(item=>rule({...item,category_name:'Synthetic expense'}))}):snapshot({items:invalid})});await click(view,'Правила статей');assert.match(text(view.container),/Статья недоступна/);
 for(let index=0;index<2;index++){all(view.container,node=>node.tag==='button'&&text(node)==='Изменить')[index].props.onClick();await settle();await set(view,'Статья','2');await stage(view)}
 assert.equal(calls.length,1);assert.doesNotMatch(text(view.container),/Статья недоступна/);await click(view,'Сохранить правила');assert.deepEqual(JSON.parse(calls[1][1].body).items.map(item=>item.category_id),[2,2]);assert.doesNotMatch(text(view.container),/Статья недоступна/);
});

test('lost save responses require an authoritative refresh before another PUT',async t=>{
 let gets=0,puts=0,changed=0;
 const view=setup(t,async(url,options)=>{if(options){puts++;throw Error('lost response')}return ++gets===1?snapshot():snapshot({revision:nextRevision,items:[rule({enabled:false})]})},{onChanged:()=>changed++});await click(view,'Правила статей');await click(view,'Отключить');await click(view,'Сохранить правила');
 assert.equal(puts,1);assert.match(text(view.container),/Ответ о сохранении правил не получен/);assert.equal(button(view,'Сохранить правила').props.disabled,true);await view.state.save(view.state.items);assert.equal(puts,1);assert.equal(changed,0);
 await click(view,'Обновить правила');assert.equal(gets,2);assert.equal(puts,1);assert.equal(changed,1);assert.equal(view.state.needsRefresh,false);assert.equal(view.state.dirty,false);assert.equal(view.state.items[0].enabled,false);
});

test('stale CAS saves cannot be retried against the same snapshot',async t=>{
 let gets=0,puts=0;const view=setup(t,async(url,options)=>{if(options){puts++;throw Object.assign(Error('Правила статей изменились. Обновите список перед сохранением.'),{status:409})}return snapshot({revision:++gets===1?revision:nextRevision})});await click(view,'Правила статей');await click(view,'Отключить');await click(view,'Сохранить правила');assert.equal(puts,1);assert.equal(view.state.items[0].enabled,false);assert.equal(view.state.needsRefresh,true);
 await view.state.save(view.state.items);assert.equal(puts,1);await click(view,'Обновить правила');assert.equal(view.state.items[0].enabled,true);assert.equal(view.state.revision,nextRevision);assert.equal(puts,1);
});

test('old company and closed-panel GET replies cannot restore stale settings',async t=>{
 let resolveLoad;const calls=[];const view=setup(t,(url,options)=>{calls.push([url,options]);return new Promise(resolve=>resolveLoad=resolve)});
 const first=button(view,'Правила статей').props.onClick();await settle();await click(view,'Правила статей');resolveLoad(snapshot());await first;await settle();assert.equal(view.state.loaded,false);assert.equal(view.state.items.length,0);
 const second=button(view,'Правила статей').props.onClick();await settle();view.app._instance.props.companyId=2;await settle();resolveLoad(snapshot());await second;await settle();assert.equal(view.state.open,false);assert.equal(view.state.items.length,0);
 const third=button(view,'Правила статей').props.onClick();await settle();resolveLoad(snapshot({revision:nextRevision,items:[rule({id:'company-2',category_name:'Company two'})]}));await third;await settle();assert.match(text(view.container),/Company two/);assert.doesNotMatch(text(view.container),/Synthetic expense/);assert.equal(calls.length,3);
});

test('company or access changes discard delayed save acknowledgements and local drafts',async t=>{
 for(const change of ['company','access']){
  let resolveSave,changed=0;const view=setup(t,async(url,options)=>options?new Promise(resolve=>resolveSave=resolve):snapshot(),{onChanged:()=>changed++});await click(view,'Правила статей');await click(view,'Отключить');const pending=button(view,'Сохранить правила').props.onClick();await settle();
  if(change==='company')view.app._instance.props.companyId=2;else view.app._instance.props.canManage=false;await settle();resolveSave(snapshot({revision:nextRevision,items:[rule({enabled:false})]}));await pending;await settle();assert.equal(changed,0);assert.equal(view.state.open,false);assert.equal(view.state.items.length,0);assert.equal(view.state.notice,'');
 }
});

test('malformed rule snapshots stay noneditable and explicit server warnings are translated',async t=>{
 const view=setup(t,async()=>snapshot({items:[rule(),rule()]}));await click(view,'Правила статей');assert.match(text(view.container),/Не удалось загрузить правила/);assert.equal(button(view,'Добавить правило'),undefined);
 const previous=lang.value;setLang('uz');t.after(()=>setLang(previous));const uz=setup(t,async()=>snapshot({warning:'Сохранённые правила статей повреждены. До исправления применяется «Не распределено».',items:[]}));await click(uz,'Modda qoidalari');assert.match(text(uz.container),/Saqlangan modda qoidalari buzilgan/);assert.match(text(uz.container),/Taqsimlanmagan/);assert.doesNotMatch(text(uz.container),/[А-Яа-яЁё]/);
});

test('saving rules through the real child clears import and reprocess signatures without posting facts',async t=>{
 const calls=[];let factsChanged=0;
 const accounts=[{id:21,name:'Synthetic bank',kind:'bank',currency:'UZS'}];
 const statement={id:7,status:'preview',preview_token:'signed-old-rules',full_reconciliation:true,account:accounts[0],statement:{account_number:'TEST-BANK',ready:true,period_from:'2026-11-01',period_to:'2026-11-01',opening_balance:'100',closing_balance:'90',credit_total:'0',debit_total:'10',balance_matches:true,declared_totals_match:true},rows:[{id:11,date:'2026-11-01',direction:'out',amount:'10',status:'outgoing_candidate',category_id:2,category_name:'Synthetic expense',category_source:'bank_rule',category_rule_status:'matched'}]};
 const api=async(url,options)=>{calls.push([url,options]);if(url===endpoint)return options?snapshot({revision:nextRevision,items:[rule({enabled:false})]}):snapshot();if(url==='/api/bank-statements/settings')return {fee_category_id:null};if(url.includes('/preview?'))return statement;if(url.endsWith('/reprocess/preview'))return {...statement,batch_id:9,status:'committed'};return {items:[{id:9,status:'committed',filename:'Synthetic saved',currency:'UZS'}]}};
 const view=mount(bank,{api,companyId:1,categories,accounts,canImport:true,canPost:true,canManageFee:true,onChanged:()=>factsChanged++});t.after(view.unmount);await settle();view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload({target:{files:[{name:'synthetic.xls',size:10}],value:'file'}});await settle();view.state.confirmAccount=true;view.state.confirmCurrency=true;await settle();
 assert.match(text(view.container),/Статья: Synthetic expense/);assert.match(text(view.container),/Статья определена правилом/);assert.match(text(view.container),/Списание будет проведено/);
 view.state.selectedOldBatch='9';await settle();await view.state.previewOldBatch();await settle();assert.ok(view.state.preview?.preview_token);assert.ok(view.state.reprocessPreview?.preview_token);const refresh=view.state.savedRefresh;
 await click(view,'Правила статей');await click(view,'Отключить');await click(view,'Сохранить правила');
 assert.equal(view.state.preview,null);assert.equal(view.state.reprocessPreview,null);assert.equal(view.state.confirmAccount,false);assert.equal(view.state.confirmCurrency,false);assert.ok(view.state.savedRefresh>refresh);assert.equal(factsChanged,0);assert.match(text(view.container),/Откройте проверку выписки заново перед подтверждением/);assert.equal(calls.filter(([url,options])=>options&&url!==endpoint&&options.method!=='GET').length,2);assert.ok(!calls.some(([url])=>url.endsWith('/commit')));
});

test('source-row rule explanations distinguish conflicts, limits and existing facts in Uzbek',async t=>{
 const previous=lang.value;setLang('uz');t.after(()=>setLang(previous));
 const accounts=[{id:21,name:'Synthetic bank',kind:'bank',currency:'UZS'}];
 const metadata=[{category_rule_status:'conflict'},{category_rule_status:'invalid'},{category_rule_budget_over:true},{category_source:'existing'},{category_source:'existing',category_rule_status:'budget_fallback'}];
 const statement={id:7,status:'preview',preview_token:'signed',full_reconciliation:true,account:accounts[0],statement:{account_number:'TEST',ready:true,period_from:'2026-11-01',period_to:'2026-11-01',opening_balance:'100',closing_balance:'50',credit_total:'0',debit_total:'50',balance_matches:true,declared_totals_match:true},rows:metadata.map((values,index)=>({id:index+1,date:'2026-11-01',direction:'out',amount:'10',status:'other_matched',category_name:'User catalog name',...values}))};
 const view=mount(bank,{companyId:1,accounts,canImport:true,api:async url=>url==='/api/bank-statements/settings'?{fee_category_id:null}:url.includes('/preview?')?statement:{items:[]}});t.after(view.unmount);await settle();view.state.accountId='21';view.state.currency='UZS';await settle();await view.state.upload({target:{files:[{name:'synthetic.xls',size:10}],value:'file'}});await settle();
 const sourceRows=all(view.container,node=>node.tag==='tr'&&text(node).includes('User catalog name'));
 assert.equal(sourceRows.length,5);assert.match(text(sourceRows[0]),/turli moddalarni/);assert.match(text(sourceRows[1]),/moddasi mavjud emas/);assert.match(text(sourceRows[2]),/limiti oshgan/);assert.match(text(sourceRows[3]),/Mavjud operatsiyaning moddasi/);assert.match(text(sourceRows[4]),/budjeti oshgan/);assert.match(text(sourceRows[4]),/Taqsimlanmagan/);
 for(const row of sourceRows){assert.match(text(row),/Mavjud operatsiyaga bog‘landi/);assert.doesNotMatch(text(row),/[А-Яа-яЁё]/)}
});
