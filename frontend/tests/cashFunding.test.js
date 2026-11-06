import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent, mount, settle, find, text, all} from './helpers/mountVue.js';
import {cashFundingAmounts, fundingBankAccounts, manualAccounts, accountKindLabel, cashFundingBody, fundingActionAvailable, hasCashFundingFee} from '../src/requests/cashFunding.js';
import {requestBody, createSaveState, saveRequest} from '../src/requests/saveFlow.js';
import {setLang, lang} from '../src/i18n/index.js';
import {statementExpenseGroups} from '../src/cashflow/statementSummary.js';

const moneyInput = await compileComponent(new URL('../src/MoneyInput.vue', import.meta.url));
const editor = await compileComponent(new URL('../src/requests/RequestEditor.vue', import.meta.url), {'../MoneyInput.vue':moneyInput});
const panel = await compileComponent(new URL('../src/requests/CashFundingPanel.vue', import.meta.url));
const requestCard = await compileComponent(new URL('../src/requests/RequestCard.vue', import.meta.url));
const overview = await compileComponent(new URL('../src/overview/OverviewPage.vue', import.meta.url));
const money = value => value;
const accounts = [
  {id:1, company_id:3, kind:'bank', currency:'UZS', name:'Synthetic bank'},
  {id:2, company_id:3, kind:'cash', currency:'UZS', name:'Synthetic cash'},
  {id:4, company_id:3, kind:'transit', currency:'UZS', name:'Synthetic transit'},
  {id:5, company_id:9, kind:'bank', currency:'UZS', name:'Another company bank'},
  {id:6, company_id:3, kind:'bank', currency:'USD', name:'Synthetic USD bank'},
];
const boot = {today:'2026-11-04', accounts:accounts.slice(0,3), categories:[{id:10,type:'outcome',name:'Synthetic cost'}, {id:11,type:'outcome',name:'Synthetic fee'}, {id:12,type:'income',name:'Synthetic income'}], due_minimums:{normal:'2026-11-13', high:'2026-11-09', urgent:'2026-11-04'}};
const funding = extra => ({id:7,version:1,bank_account_id:1,bank_account:'Synthetic bank',gross_amount:'1000000.00',fee_percent:'1.00',fee_amount:'10000.00',net_amount:'990000.00',fee_category_id:11,fee_category_name:'Synthetic fee',state:'unfunded',can_withdraw:true,can_receive:false,...extra});
const request = extra => ({id:42,company_id:3,version:9,status:'approved',approved_on:'2026-11-04',account:'Synthetic cash',channel:'cash',currency:'UZS',amount:'990000.00',actions:['cash_withdraw'],cash_funding:funding(),...extra});
const checkedPreview = (body, action = 'withdraw') => ({...body,token:'signed-preview',request_id:42,action,currency:'UZS',gross_amount:'1000000.00',net_amount:'990000.00',fee_amount:'10000.00'});
function mountEditor(t, extra={}) {
  const previous=globalThis.document;
  globalThis.document={activeElement:null, addEventListener(){},removeEventListener(){},querySelector(){return null;}};
  const view=mount(editor,{api:async()=>({due_minimums:boot.due_minimums}),boot,company:{id:3,name:'Synthetic'},money,companyUrl:u=>u,...extra});
  t.after(()=>{view.unmount();globalThis.document=previous;}); return view;
}
function mountPanel(t, api, extra={}) {
  const view=mount(panel,{request:request(),money,api,...extra});t.after(view.unmount);return view;
}
const button=(view,label)=>find(view.container,node=>node.tag==='button'&&text(node)===label);

test('gross includes fee and exact minor-unit rounding avoids float drift',()=>{
  assert.deepEqual(cashFundingAmounts('1 000 000','1'),{gross_amount:'1000000.00',fee_amount:'10000.00',net_amount:'990000.00',fee_percent:'1.00'});
  assert.equal(cashFundingAmounts('1.50','1').fee_amount,'0.02');
  assert.equal(cashFundingAmounts('1,01','1').net_amount,'1.00');
  assert.equal(cashFundingAmounts('900719925474099.99','0.01').fee_amount,'90071992547.41');
  assert.equal(cashFundingAmounts('100','0').net_amount,'100.00');
  for(const [gross,percent] of [['0','1'],['-1','1'],['1','100'],['1','-1'],['1','1.001'],['0.01','99.99']]) assert.ok(cashFundingAmounts(gross,percent).error);
});

test('funding and manual selectors never expose transit, foreign-company, archived or wrong-currency accounts',()=>{
  assert.deepEqual(fundingBankAccounts([...accounts,{...accounts[0],id:8,archived:true}],'UZS',3).map(a=>a.id),[1]);
  assert.deepEqual(manualAccounts(accounts).map(a=>a.id),[1,2,5,6]);
  const previous=lang.value;setLang('ru');assert.equal(accountKindLabel('transit'),'Деньги в пути');setLang('uz');assert.equal(accountKindLabel('transit'),'Yo‘ldagi pul mablag‘lari');setLang(previous);
});

test('request payload carries funding exact strings and explicit removal without changing old clients',()=>{
  const value={bank_account_id:'1',gross_amount:'1 000 000',fee_percent:'1,00',fee_category_id:'11'};
  assert.deepEqual(cashFundingBody(value),{bank_account_id:1,mode:'single',gross_amount:'1000000',fee_percent:'1.00',fee_category_id:11});
  assert.deepEqual(requestBody({cash_funding:value,channel:'cash',amount:'990000.00'},3).cash_funding,cashFundingBody(value));
  assert.equal(requestBody({cash_funding:null},3).cash_funding,null);
  assert.equal(Object.hasOwn(requestBody({},3),'cash_funding'),false);
});

test('editor explicitly chooses batch funding while preserving legacy single requests',async t=>{
  const view=mountEditor(t);await settle();view.state.form.amount='1000000';view.state.paymentMethod='cash_bank';await settle();
  assert.equal(view.state.form.cash_funding.mode,'single');
  view.state.form.cash_funding.mode='batch';view.state.form.cash_funding.fee_category_id=11;await settle();
  assert.equal(view.state.batchFunding,true);assert.equal(view.state.form.amount,'990000.00');
  assert.match(text(view.container),/Пополнение не является отдельным расходом/);
  assert.equal(requestBody(view.state.form,3).cash_funding.mode,'batch');
  assert.equal(cashFundingBody(funding()).mode,'single');
  assert.equal(all(view.container,node=>node.tag==='section'&&String(node.props.class).includes('budget-check')).length,0);
});

test('editor cash-bank mode calculates net, keeps cash documents and clears funding on ordinary channel',async t=>{
  const view=mountEditor(t);await settle();view.state.form.amount='1000000';view.state.paymentMethod='cash_bank';await settle();
  assert.equal(view.state.form.channel,'cash');assert.equal(view.state.form.account_id,2);
  assert.equal(view.state.form.cash_funding.gross_amount,'1000000');assert.equal(view.state.form.amount,'990000.00');assert.deepEqual(view.state.required,['internal']);
  assert.match(text(view.container),/Наличные через банк/);assert.ok(view.state.fundingProblem,'fee article must be explicitly selected');
  view.state.form.cash_funding.fee_category_id=11;await settle();assert.equal(view.state.fundingProblem,'');
  view.state.form.cash_funding.fee_percent='2';await settle();assert.equal(view.state.form.amount,'980000.00');
  const amount=all(view.container,node=>node.tag==='input'&&Object.hasOwn(node.props,'readonly')&&node.props.readonly!==false)[0];assert.ok(amount,'request net cannot be edited independently');assert.equal(amount.props.value,'980 000.00');
  view.state.paymentMethod='bank';await settle();assert.equal(view.state.form.cash_funding,null);assert.equal(view.state.form.account_id,1);assert.deepEqual(view.state.required,['internal','contract']);assert.equal(view.state.form.amount,'1000000');
  view.state.paymentMethod='cash_bank';await settle();assert.equal(view.state.form.cash_funding.gross_amount,'1000000');assert.equal(view.state.form.amount,'990000.00','switching modes does not compound the fee');
});

test('saved funding opens with original values; changing company blocks all writes',async t=>{
  const calls=[];const row=request({status:'draft',priority:'urgent',date:'2026-11-04',account_id:2,category_id:10,counterparty:'Synthetic',purpose:'Synthetic purpose',documents_list:[]});
  const view=mountEditor(t,{request:row,api:async(url,options)=>{calls.push({url,options});return {due_minimums:boot.due_minimums};}});await settle();
  assert.equal(view.state.paymentMethod,'cash_bank');assert.equal(view.state.form.amount,'990000.00');assert.equal(view.state.form.cash_funding.gross_amount,'1000000.00');
  view.app._instance.props.company={id:9,name:'Other'};await settle();await view.state.save({submitter:{dataset:{send:'0'}}});
  assert.equal(view.state.blocked,'company_changed');assert.equal(calls.filter(call=>call.options?.method).length,0);
});

test('changed funding terms force a PUT instead of a false unchanged-save result',async()=>{
  const row={id:42,version:1,status:'draft',number:'SYN-42',counterparty:'Synthetic',counterparty_inn:''};
  const state=createSaveState(row), fields={company_id:3,channel:'cash',account_id:2,amount:'990000.00',counterparty:'Synthetic',counterparty_inn:'',cash_funding:{bank_account_id:1,gross_amount:'1000000',fee_percent:'1',fee_category_id:11}};
  let stored={...row}, writes=0;
  const api=async(url,options={})=>{if(options.method==='PUT'){writes++;stored={...stored,...JSON.parse(options.body),version:stored.version+1};}return stored;};
  await saveRequest(api,state,{fields,reason:'Synthetic reason'});
  await saveRequest(api,state,{fields:{...fields,cash_funding:{...fields.cash_funding,fee_category_id:10}},reason:'Changed fee article'});
  assert.equal(writes,2);
});

test('funding actions require both server action and capability flags',async t=>{
  assert.equal(fundingActionAvailable(request(),'withdraw'),true);
  assert.equal(fundingActionAvailable(request({actions:[]}),'withdraw'),false);
  assert.equal(fundingActionAvailable(request({cash_funding:funding({can_withdraw:false})}),'withdraw'),false);
  const view=mountPanel(t,async()=>{}, {request:request({actions:[]})});await settle();
  assert.equal(button(view,'Зафиксировать списание банка'),undefined);assert.match(text(view.container),/990000.00/);
});

test('signed withdrawal preview and commit send one write even on double click',async t=>{
  const calls=[],events=[];let finish;
  const api=async(url,options)=>{const data=JSON.parse(options.body);calls.push({url,data});if(url.endsWith('/preview'))return checkedPreview(data);return new Promise(resolve=>finish=resolve);};
  const view=mountPanel(t,api,{onChanged:row=>events.push(row)});await settle();await button(view,'Зафиксировать списание банка').props.onClick();await settle();
  view.state.form.reference='SYN-BANK-1';await settle();await view.state.check();await settle();
  assert.equal(calls[0].url,'/api/requests/42/cash-funding/withdraw/preview');assert.equal(calls[0].data.version,1);
  assert.ok(view.state.preview);view.state.confirmed=true;const one=view.state.commit(),two=view.state.commit();await settle();
  assert.equal(calls.filter(call=>call.url.endsWith('/commit')).length,1);assert.equal(calls[1].data.confirm,true);assert.equal(calls[1].data.token,'signed-preview');
  finish(request({cash_funding:funding({state:'in_transit',version:2,can_withdraw:false})}));await Promise.all([one,two]);await settle();
  assert.equal(events.length,1);assert.equal(view.state.notice,'Списание банка и комиссия учтены. Заявка ещё не оплачена.');
});

test('lost commit reply locks financial actions until a read-only refresh',async t=>{
  const calls=[],events=[];const after=request({actions:['cash_receive'],cash_funding:funding({state:'in_transit',version:2,can_withdraw:false,can_receive:true})});
  const api=async(url,options={})=>{calls.push({url,options});if(url.endsWith('/preview'))return checkedPreview(JSON.parse(options.body));if(url.endsWith('/commit'))throw new Error('Lost response');return after;};
  const view=mountPanel(t,api,{onChanged:row=>events.push(row)});await settle();view.state.start('withdraw');view.state.form.reference='SYN-BANK-2';await settle();await view.state.check();view.state.confirmed=true;
  await view.state.commit();await settle();assert.equal(view.state.uncertain,true);assert.ok(button(view,'Проверить состояние заявки'));assert.equal(button(view,'Зафиксировать списание банка'),undefined);
  await view.state.commit();assert.equal(calls.filter(call=>call.url.endsWith('/commit')).length,1);
  await view.state.refresh();await settle();assert.equal(calls.at(-1).options.method,undefined);assert.equal(events[0].cash_funding.state,'in_transit');assert.equal(view.state.uncertain,false);
});

test('changed funding version discards late signed previews without success',async t=>{
  let finish;const view=mountPanel(t,()=>new Promise(resolve=>finish=resolve));await settle();view.state.start('withdraw');view.state.form.reference='SYN-BANK-3';await settle();const pending=view.state.check();await settle();
  view.app._instance.props.request=request({version:10,cash_funding:funding({version:2})});await settle();finish({token:'stale'});await pending;await settle();assert.equal(view.state.preview,null);
});

test('cash receipt is a distinct signed action and paid/read-only requests hide funding actions in both languages',async t=>{
  const previous=lang.value;t.after(()=>setLang(previous));setLang('uz');const calls=[];
  const row=request({actions:['cash_receive'],cash_funding:funding({state:'in_transit',can_withdraw:false,can_receive:true,withdraw_date:'2026-11-04'})});
  const view=mountPanel(t,async(url,options)=>{calls.push({url,options});return checkedPreview(JSON.parse(options.body),'receive');},{request:row});await settle();
  assert.ok(button(view,'Kassaga olinganini tasdiqlash'));view.state.start('receive');view.state.form.reference='SYN-CASH-1';await settle();await view.state.check();assert.match(calls[0].url,/\/receive\/preview$/);
  view.app._instance.props.request=request({status:'paid',actions:[],cash_funding:funding({state:'received',can_withdraw:false,can_receive:false})});await settle();
  assert.match(text(view.container),/Naqd pul bilan to‘langan/);assert.equal(all(view.container,node=>node.tag==='form').length,0);
});

test('bank cash-funding matches display existing facts and fees exactly once',()=>{
  const groups=statementExpenseGroups([
    {id:1,direction:'out',status:'cash_funding_candidate',classification:'internal_transfer',amount:'990000.00'},
    {id:2,direction:'out',status:'cash_funding_matched',classification:'bank_fee',amount:'10000.00'},
  ]);
  assert.equal(groups.find(group=>group.key==='existing').total,99000000);
  assert.equal(groups.find(group=>group.key==='fees').total,1000000);
  assert.equal(groups.find(group=>group.key==='review').rows.length,0);
  assert.equal(groups.reduce((sum,group)=>sum+group.rows.length,0),2);
});

test('foreign signed preview is rejected before any financial commit',async t=>{
  const calls=[];const view=mountPanel(t,async(url,options)=>{calls.push(url);return {...checkedPreview(JSON.parse(options.body)),request_id:99};});await settle();
  view.state.start('withdraw');view.state.form.reference='SYN-BANK-4';await settle();await view.state.check();await settle();
  assert.equal(view.state.preview,null);assert.match(view.state.error,/не вернул подтверждение/);view.state.confirmed=true;await view.state.commit();assert.equal(calls.length,1);
});

test('unverifiable commit response requires a state read rather than reporting false success',async t=>{
  const events=[],calls=[];const view=mountPanel(t,async(url,options)=>{calls.push(url);return url.endsWith('/preview')?checkedPreview(JSON.parse(options.body)):request({id:99});},{onChanged:row=>events.push(row)});await settle();
  view.state.start('withdraw');view.state.form.reference='SYN-BANK-5';await settle();await view.state.check();view.state.confirmed=true;await view.state.commit();await settle();
  assert.equal(events.length,0);assert.equal(view.state.uncertain,true);assert.equal(calls.filter(url=>url.endsWith('/commit')).length,1);assert.equal(view.state.notice,'');
});

test('accepted funding commit reloads full detail instead of losing documents, people and history',async t=>{
  const previous=globalThis.document;globalThis.document={activeElement:null,addEventListener(){},removeEventListener(){},querySelector(){return null;}};
  const detailed=request({number:'SYN-42',category:'Synthetic cost',priority:'urgent',date:'2026-11-04',people:{creator_id:'Creator'},history:[{id:1,action:'Created',at:'2026-11-04 00:00:00'}],documents_list:[{id:1,kind:'internal',current:true,filename:'synthetic.pdf',version:1}]});
  let reads=0;const view=mount(requestCard,{id:42,api:async url=>{if(url==='/api/request-rules')return {due_minimums:{}};reads++;return detailed;},money,companyUrl:u=>u});
  t.after(()=>{view.unmount();globalThis.document=previous;});await settle();assert.equal(view.state.r.documents_list.length,1);
  await view.state.fundingChanged(request({cash_funding:funding({state:'in_transit',version:2})}));await settle();
  assert.ok(reads>=2);assert.equal(view.state.r.documents_list[0].filename,'synthetic.pdf');assert.equal(view.state.r.people.creator_id,'Creator');assert.equal(view.state.r.history.length,1);
});

test('forecast includes fee in one original request event and removes the caption after withdrawal',async t=>{
  const previous=lang.value;setLang('ru');t.after(()=>setLang(previous));
  const view=mount(overview,{showRequestPanels:true,companyId:3,company:{id:3,name:'Synthetic'},currency:'UZS',today:'2026-11-04',has:()=>false,canDecide:()=>false,api:()=>new Promise(()=>{})});t.after(view.unmount);await settle();
  const event={kind:'out',id:42,name:'Synthetic payee',amount:'1000000.00',cash_amount:'990000.00',cash_funding_fee:'10000.00'};
  view.state.s={...view.state.s,loading:false,dashLoading:false,requestsLoading:false,approved:[],pending:[],pendingAll:[],dash:{forecast:[{date:'2026-11-05',events:[event]}]}};await settle();
  assert.equal(view.state.upcoming.length,1);assert.equal(view.state.upcoming[0].id,42);assert.equal(view.state.upcoming[0].amount,'1000000.00');
  assert.match(text(view.container).replace(/[\u00a0\u202f]/g,' '),/Включая комиссию 10 000 UZS/);
  setLang('uz');await settle();assert.match(text(view.container),/Shu jumladan komissiya/);
  view.state.s.dash.forecast[0].events=[{...event,amount:'990000.00',cash_funding_fee:'0.00'}];await settle();
  assert.equal(view.state.upcoming.length,1);assert.equal(view.state.upcoming[0].id,42);assert.doesNotMatch(text(view.container),/Shu jumladan komissiya/);
  assert.equal(hasCashFundingFee({}),false);assert.equal(hasCashFundingFee({cash_funding_fee:'0.00'}),false);
});
