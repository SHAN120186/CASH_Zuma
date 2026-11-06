import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text,all} from './helpers/mountVue.js';
import {fundingStatus} from '../src/requests/cashFunding.js';
import {setLang,lang} from '../src/i18n/index.js';

const fundingPanel=await compileComponent(new URL('../src/requests/CashFundingPanel.vue',import.meta.url));
const batchPanel=await compileComponent(new URL('../src/requests/CashFundingBatchPanel.vue',import.meta.url));
const card=await compileComponent(new URL('../src/requests/RequestCard.vue',import.meta.url),{'./CashFundingPanel.vue':fundingPanel,'./CashFundingBatchPanel.vue':batchPanel});
const funding=extra=>({id:7,version:1,mode:'single',state:'unfunded',bank_account_id:1,bank_account:'Synthetic bank',gross_amount:'1000000.00',net_amount:'990000.00',fee_amount:'10000.00',fee_percent:'1.00',fee_category_id:11,fee_category_name:'Synthetic commission',can_withdraw:false,can_receive:false,...extra});
const source=extra=>({id:133,company_id:3,version:9,status:'pending',approval_stage:'check',number:'SYN-133',account_id:2,account:'Synthetic cash',account_kind:'cash',channel:'cash',currency:'UZS',amount:'990000.00',date:'2026-11-06',approved_on:null,counterparty:'Synthetic finance request',purpose:'Synthetic cash funding',priority:'normal',creator_id:4,last_editor_id:4,checked_by:null,finance_approved_by:null,approved_by:null,route:{policy:'threshold',finance_author:true,finance_required:false,director_required:true,reason:''},people:{creator_id:'Synthetic finance author'},documents_list:[],history:[],actions:[],cash_funding:funding(),...extra});
const buttons=view=>all(view.container,node=>node.tag==='button').map(text);
async function fixture(t,initial,historyState=null){
  const previous=globalThis.document;globalThis.document={activeElement:null,addEventListener(){},removeEventListener(){},querySelector(){return null;}};
  let current=initial;const calls=[];
  const view=mount(card,{id:133,money:value=>value,companyUrl:url=>url,api:async(url,options={})=>{calls.push({url,method:options.method||'GET'});if(url==='/api/request-rules')return{due_minimums:{}};if(url.endsWith('/cash-funding/payments'))return historyState;return current;}});
  t.after(()=>{view.unmount();globalThis.document=previous;});await settle();
  return {view,calls,async replace(next){current=next;await view.state.load();await settle();}};
}

test('the actual card displays accountant then director without a fictitious finance approval',async t=>{
  const checking=source({actions:['check']});const {view,replace}=await fixture(t,checking);
  assert.match(text(view.container),/Ожидает: проверка бухгалтера/);
  assert.match(text(view.container),/Маршрут: проверка бухгалтера → директор → оплата/);
  assert.match(text(view.container),/Фин. директор · автор заявки/);
  assert.match(text(view.container),/Автор заявки · не согласует/);
  assert.doesNotMatch(text(view.container),/Ожидает снятия из банка/);
  const waiting=source({checked_by:8,approval_stage:'director',actions:['approve'],people:{creator_id:'Synthetic finance author',checked_by:'Synthetic checking accountant'}});
  await replace(waiting);assert.match(text(view.container),/Ожидает: директор/);assert.equal(view.state.r.finance_approved_by,null);
  const approved=source({status:'approved',approval_stage:null,checked_by:8,approved_by:9,approved_on:'2026-11-06',people:{creator_id:'Synthetic finance author',checked_by:'Synthetic checking accountant',approved_by:'Synthetic director'}});
  await replace(approved);assert.match(text(view.container),/Synthetic director/);assert.match(text(view.container),/Ожидает снятия из банка/);
  assert.equal(view.state.r.finance_approved_by,null);assert.equal(view.state.financeAuthor,true);
});

test('funding state labels respect draft, return, approval stages and settled facts in RU and UZ',()=>{
  const previous=lang.value;try{
    for(const language of ['ru','uz']){setLang(language);
      for(const status of ['draft','returned'])assert.doesNotMatch(fundingStatus(source({status})),/Ожидает снятия|Bankdan yechib olish kutilmoqda/);
      for(const approval_stage of ['check','finance','director'])assert.doesNotMatch(fundingStatus(source({approval_stage})),/Ожидает снятия|Bankdan yechib olish kutilmoqda/);
      assert.match(fundingStatus(source({status:'approved'})),language==='ru'?/Ожидает снятия/:/Bankdan yechib olish/);
      assert.match(fundingStatus(source({status:'approved',cash_funding:funding({state:'in_transit'})})),language==='ru'?/Деньги в пути/:/Yo‘ldagi pul/);
      assert.match(fundingStatus(source({status:'paid'})),language==='ru'?/Оплачена наличными/:/Naqd pul bilan to‘langan/);
    }
  }finally{setLang(previous);}
});

test('administrative card keeps permitted data actions without any approval or cash payment control',async t=>{
  const row=source({approval_stage:'director',checked_by:8,actions:['edit','documents','delete','reschedule'],cash_funding:funding({mode:'batch'})});
  const historyState={request_id:133,company_id:3,cash_account_id:2,funding_id:7,version:1,request_version:9,mode:'batch',currency:'UZS',received_amount:'0.00',allocated_amount:'0.00',remaining_amount:'0.00',cash_balance:'1000000.00',can_pay:false,blocked_reason:'Synthetic administrative oversight',candidates:[],groups:[]};
  const {view,calls}=await fixture(t,row,historyState);
  const labels=buttons(view);assert.ok(labels.includes('Изменить'));assert.ok(labels.includes('Удалить заявку'));
  for(const label of ['Факт оплаты','Зафиксировать списание банка','Подтвердить получение в кассу','Оплатить несколько заявок','Подтвердить выдачу и оплатить заявки'])assert.equal(labels.includes(label),false,label);
  assert.deepEqual(view.state.options.map(option=>option[0]),['reschedule']);
  assert.equal(all(view.container,node=>node.tag==='input'&&node.props.type==='file').length>0,true,'document administration stays available');
  assert.equal(calls.some(call=>call.method==='POST'),false);
});

test('same-version card refresh removes stale operational capabilities while preserving CRUD',async t=>{
  const original=source({status:'approved',approval_stage:null,approved_on:'2026-11-06',checked_by:8,approved_by:9,actions:['cash_withdraw'],cash_funding:funding({can_withdraw:true})});
  const {view,replace}=await fixture(t,original);assert.ok(buttons(view).includes('Зафиксировать списание банка'));
  await replace({...original,actions:['edit','delete'],cash_funding:funding({can_withdraw:false})});
  assert.equal(buttons(view).includes('Зафиксировать списание банка'),false);assert.ok(buttons(view).includes('Изменить'));assert.ok(buttons(view).includes('Удалить заявку'));
});

test('finance-author labels translate on the real card',async t=>{
  const previous=lang.value;setLang('uz');t.after(()=>setLang(previous));
  const {view}=await fixture(t,source({approval_stage:'director',checked_by:8,actions:[]}));
  assert.match(text(view.container),/Moliyaviy direktor · ariza muallifi/);
  assert.match(text(view.container),/Jarayon: buxgalter tekshiruvi → direktor → to‘lov/);
  assert.doesNotMatch(text(view.container),/Bankdan yechib olish kutilmoqda/);
});
