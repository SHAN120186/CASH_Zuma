import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle} from './helpers/mountVue.js';
import {lang,setLang} from '../src/i18n/index.js';
const component=await compileComponent(new URL('../src/BankStatementHistory.vue',import.meta.url));

test('history distinguishes created, linked and duplicate operations and prioritizes current state',async t=>{
 const view=mount(component,{api:async()=>({items:[]}),companyId:1,canView:true});t.after(view.unmount);await settle();
 for(const status of ['auto_paid','manual_paid','auto_income_posted','auto_fee_posted','auto_outgoing_posted'])
  assert.equal(view.state.stateLabel({status,operation_state:'active'}),'Проведена');
 for(const status of ['paid_match','manual_match','income_matched','fee_matched','other_matched','manual_transfer_match'])
  assert.equal(view.state.stateLabel({status,operation_state:'active'}),'Связана с существующей операцией');
 assert.equal(view.state.stateLabel({status:'duplicate',operation_state:'active'}),'Повтор связан с операцией');
 assert.equal(view.state.stateLabel({status:'unknown',operation_state:'active'}),'Состояние не определено');
 for(const status of ['auto_paid','paid_match','duplicate']){
  assert.equal(view.state.stateLabel({status,operation_state:'reversed'}),'Сторнирована');
  assert.equal(view.state.stateLabel({status,operation_state:'changed'}),'Операция изменена');
  assert.equal(view.state.stateLabel({status,operation_state:'missing'}),'Операция не найдена');
  assert.equal(view.state.stateLabel({status,operation_state:'unposted'}),'Не проведена');
 }
});

test('linked and duplicate history states are translated in Uzbek',async t=>{
 const prior=lang.value;setLang('uz');t.after(()=>setLang(prior));
 const view=mount(component,{api:async()=>({items:[]}),companyId:1,canView:true});t.after(view.unmount);await settle();
 assert.equal(view.state.stateLabel({status:'paid_match',operation_state:'active'}),'Mavjud operatsiyaga bog‘langan');
 assert.equal(view.state.stateLabel({status:'duplicate',operation_state:'active'}),'Takror operatsiyaga bog‘langan');
});
