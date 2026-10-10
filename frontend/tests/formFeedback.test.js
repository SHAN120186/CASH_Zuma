import {test} from 'node:test';
import assert from 'node:assert/strict';
import {controlLabel,controlProblem,invalidFormFields,focusInvalidField} from '../src/formFeedback.js';
const control = (label, validity, extra={}) => ({labels:[{textContent:label}],validity,willValidate:true,...extra});

test('blank fields are listed by their visible names and disabled controls are ignored',()=>{
  const a=control('Название отчёта *',{valid:false,valueMissing:true});
  const b=control('Начало периода',{valid:false,valueMissing:true});
  const zero=control('Сумма',{valid:true},{value:'0'});
  const disabled=control('Старое поле',{valid:false,valueMissing:true},{disabled:true});
  const fields=invalidFormFields({elements:[a,b,zero,disabled]});
  assert.deepEqual(fields.map(x=>x.label),['Название отчёта','Начало периода']);
  assert.equal(fields[0].message,'Не заполнено: Название отчёта.');
  assert.equal(fields[0].control,a);
});
test('incorrect numeric input is named without calling it empty or exposing its value',()=>{
  const field=control('Ставка, %',{valid:false,badInput:true},{value:'private-value'});
  assert.equal(controlProblem(field).message,'Проверьте формат поля «Ставка, %».');
  assert.equal(JSON.stringify(controlProblem(field)).includes('private-value'),false);
});
test('radio group appears once and field names have accessible fallbacks',()=>{
  const a=control('Способ оплаты',{valid:false,valueMissing:true},{type:'radio',name:'payment'});
  const b=control('Способ оплаты',{valid:false,valueMissing:true},{type:'radio',name:'payment'});
  assert.equal(invalidFormFields({elements:[a,b]}).length,1);
  assert.equal(controlLabel({name:'username'}),'Логин');
  assert.equal(controlLabel({getAttribute:()=> 'Период отчёта',labels:[]}), 'Период отчёта');
});
test('jump reveals nested collapsed groups before focus and ignores removed forms',()=>{
  const outer={tagName:'DETAILS',open:false,parentElement:null};
  const inner={tagName:'DETAILS',open:false,parentElement:outer};
  let focused=0, scrolled=0;
  const field={isConnected:true,parentElement:inner,focus(){focused++},scrollIntoView(){scrolled++}};
  focusInvalidField(field);
  assert.ok(outer.open&&inner.open);assert.equal(focused,1);assert.equal(scrolled,1);
  field.isConnected=false;focusInvalidField(field);assert.equal(focused,1);
});
