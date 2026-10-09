import {test} from 'node:test';
import assert from 'node:assert/strict';
import {reactive} from 'vue';
import {compileComponent,mount,settle,text,find,all} from './helpers/mountVue.js';
const inputsComponent=await compileComponent(new URL('../src/NativeTemplateInputs.vue',import.meta.url));
const workbookComponent=await compileComponent(new URL('../src/NativeWorkbook.vue',import.meta.url),{'./NativeTemplateInputs.vue':inputsComponent});
const company={id:2};
const item=(key,extra={})=>({key,group:'Prices',label:'Base price',value:12000,original_value:12000,unit:'UZS/pack',sheet:'Input',cell:'B2',editable:true,min:0,max:1e15,proposals:[],requires_decision:false,...extra});
const proposal=(id,value,extra={})=>({id,value,source:'New prices.xlsx',sheet:'Prices',cell:'C2',unit:'UZS/pack',...extra});
const model=items=>({source_sha256:'a'.repeat(64),parameters:[],metrics:[],input_review:{items,warnings:[],decisions:{}},data_updates:{},review_decisions:{}});
const event=value=>({target:{value}});

test('source decisions are explicit, select a duplicate proposal and retain raw currency units',async t=>{
  const row=item('Input!B2',{requires_decision:true,proposals:[proposal('one',12500),proposal('two',13000)],help:'The base price keeps its original coefficient.'});
  const view=mount(inputsComponent,{model:model([row]),parameters:{}});t.after(view.unmount);await settle();
  assert.deepEqual(view.state.submission.unresolved.map(entry=>entry.key),['Input!B2']);
  assert.match(text(view.container),/12000/);assert.match(text(view.container),/UZS\/pack/);assert.match(text(view.container),/original coefficient/);
  view.state.setChoice(row,event('source'));await settle();assert.equal(view.state.submission.unresolved.length,1);
  view.state.selectProposal(row,event('two'));await settle();
  assert.deepEqual(view.state.submission.data_updates,{'Input!B2':13000});
  assert.deepEqual(view.state.submission.review_decisions,{'Input!B2':{choice:'source',proposal_id:'two'}});
  assert.deepEqual(view.state.submission.scalars,{});assert.equal(row.value,12000);
  view.state.setChoice(row,event('keep'));await settle();
  assert.deepEqual(view.state.submission.data_updates,{});assert.deepEqual(view.state.submission.review_decisions,{'Input!B2':{choice:'keep'}});
});

test('blank manual values and blank original inputs never become zero; fractions and integers validate before submission',async t=>{
  const rate=item('Tax!C3',{label:'Tax rate',unit:'доля',value:0.15,original_value:0.15,max:1,requires_decision:true});
  const count=item('Payroll!B2',{label:'Staff count',unit:'people',integer:true,value:null,original_value:null,required:true,requires_decision:true});
  const view=mount(inputsComponent,{model:model([rate,count]),parameters:{}});t.after(view.unmount);await settle();
  view.state.setChoice(count,event('keep'));await settle();assert.equal(view.state.submission.unresolved.length,2);assert.deepEqual(view.state.submission.data_updates,{});
  view.state.setChoice(rate,event('manual'));view.state.setManual(rate,event('18,5'));
  view.state.setChoice(count,event('manual'));view.state.setManual(count,event(''));await settle();
  assert.deepEqual(view.state.submission.data_updates,{'Tax!C3':0.185});assert.equal(view.state.submission.unresolved[0].key,'Payroll!B2');
  view.state.setManual(count,event('2.5'));await settle();assert.match(view.state.submission.unresolved[0].message,/целое число/);
  view.state.setManual(count,event('0'));await settle();assert.deepEqual(view.state.submission.data_updates,{'Tax!C3':0.185,'Payroll!B2':0});assert.equal(view.state.submission.unresolved.length,0);
  view.state.setManual(count,event('1e-999'));await settle();assert.equal(view.state.submission.unresolved.length,1);
});

test('unused blank staffing rows stay optional but choosing a new role names its missing count or salary',async t=>{
  const count=item('Труд!B16',{sheet:'Труд',cell:'B16',label:'New staff count',group:'Personnel',integer:true,value:null,original_value:null,proposals:[proposal('count',2)]});
  const salary=item('Труд!C16',{sheet:'Труд',cell:'C16',label:'New staff salary',group:'Personnel',value:null,original_value:null});
  const view=mount(inputsComponent,{model:model([count,salary]),parameters:{}});t.after(view.unmount);await settle();assert.equal(view.state.submission.unresolved.length,0);
  view.state.setChoice(count,event('keep'));await settle();assert.equal(view.state.submission.unresolved.length,0);assert.deepEqual(view.state.submission.data_updates,{});
  view.state.setChoice(count,event('source'));await settle();assert.deepEqual(view.state.submission.unresolved.map(problem=>problem.label),['New staff salary']);
  assert.ok(find(view.container,node=>node.props?.['data-review-key']===salary.key));
  view.state.setChoice(salary,event('manual'));view.state.setManual(salary,event('0'));await settle();assert.equal(view.state.submission.unresolved.length,0);assert.deepEqual(view.state.submission.data_updates,{[count.key]:2,[salary.key]:0});
});

test('default review shows differences only and browsing searches named groups with formula references read only',async t=>{
  const rows=Array.from({length:70},(_,index)=>item('Input!B'+index,{label:'Price '+index,cell:'B'+index}));
  rows.push(item('Labor!D4',{label:'Payroll formula',group:'Personnel',editable:false,value:null,original_value:null}));
  const view=mount(inputsComponent,{model:model(rows),parameters:{}});t.after(view.unmount);await settle();
  assert.equal(all(view.container,node=>node.props?.['data-review-key']).length,0);assert.match(text(view.container),/Спорных значений нет/);
  view.state.browseGroup='Personnel';await settle();assert.equal(all(view.container,node=>node.props?.['data-review-key']).length,1);
  const reference=find(view.container,node=>node.props?.['data-review-key']==='Labor!D4');
  assert.match(text(reference),/Рассчитывается по формуле Excel/);assert.equal(find(reference,node=>node.tag==='select'||node.tag==='input'),undefined);
  view.state.browseGroup='Prices';await settle();assert.equal(all(view.container,node=>node.props?.['data-review-key']).length,25);
  assert.match(text(view.container),/Показать ещё 25 параметров/);
  view.state.search='Price 69';await settle();assert.equal(all(view.container,node=>node.props?.['data-review-key']).length,1);assert.match(text(view.container),/Price 69/);
});

test('named missing-field navigation clears filters, includes a later page and focuses its resolution control',async t=>{
  const rows=Array.from({length:70},(_,index)=>item('Input!B'+index,{label:'Price '+index,cell:'B'+index}));
  const target=item('Labor!B80',{sheet:'Labor',cell:'B80',label:'Named missing salary',group:'Personnel',requires_decision:true});rows.push(target);let navigated=0;
  const view=mount(inputsComponent,{model:model(rows),parameters:{},onData:()=>{navigated++;}});t.after(view.unmount);await settle();
  view.state.browseGroup='Prices';view.state.search='Price 1';await settle();assert.equal(find(view.container,node=>node.props?.['data-review-key']===target.key),undefined);
  view.state.panel.querySelectorAll=()=>all(view.state.panel,node=>node.props?.['data-review-key']).map(node=>{const select=find(node,child=>child.tag==='select');if(select)select.focus=()=>{select.focused=true;};return node;});
  await view.state.goToField(target.key);await settle();const card=find(view.container,node=>node.props?.['data-review-key']===target.key);
  assert.equal(navigated,1);assert.equal(view.state.search,'');assert.equal(view.state.browseGroup,'');assert.equal(find(card,node=>node.tag==='select').focused,true);assert.equal(card.scrolled,true);
});

test('saved selections survive reload; editable native inputs with locked legacy parameters use data_updates',async t=>{
  const row=item('Input!B2',{value:14000,requires_decision:true,decision:{choice:'source',proposal_id:'new'},proposals:[proposal('new',14000)]});
  const value={...model([row]),parameters:[{key:row.key,editable:false}],data_updates:{[row.key]:14000},review_decisions:{[row.key]:{choice:'source',proposal_id:'new'}}};
  const view=mount(inputsComponent,{model:value,parameters:{[row.key]:'14000'}});t.after(view.unmount);await settle();
  assert.equal(view.state.submission.unresolved.length,0);assert.equal(view.state.submission.changed,false);
  assert.deepEqual(view.state.submission.data_updates,{[row.key]:14000});assert.deepEqual(view.state.submission.scalars,{});
  view.state.setChoice(row,event('manual'));view.state.setManual(row,event('15000'));await settle();assert.equal(view.state.submission.changed,true);
  view.state.setChoice(row,event(''));await settle();assert.equal(view.state.submission.unresolved.length,1);assert.equal(view.state.submission.changed,true);
});

test('the real review choice handler flows into original preview and generation across a saved-model replacement',async t=>{
  const row=item('Input!B2',{requires_decision:true,proposals:[proposal('new',13500,{basis_confirmation:true})]}),calls=[];
  let props;
  const project={id:7,company_id:2,title:'Synthetic',status:'needs_data',revision:4,can_edit:true,inputs:{start:'2028-04-01'},native_model:model([row]),generations:[]};
  props=reactive({company,project,stage:'data',onUpdated:updated=>{Object.assign(props.project,updated);},api:async(url,options)=>{
    const body=JSON.parse(options.body);calls.push({url,body});return{...props.project,revision:5,native_model:{...props.project.native_model,data_updates:body.data_updates,review_decisions:body.review_decisions,confirm_source_basis:body.confirm_source_basis,input_review:{items:[{...row,value:13500,decision:body.review_decisions[row.key]}],decisions:body.review_decisions},preview:{blocked:false,formula_count:10,error_count:0,issues:[]}}};
  }});
  const view=mount(workbookComponent,props);t.after(view.unmount);await settle();
  const card=find(view.container,node=>node.props?.['data-review-key']===row.key),selector=find(card,node=>node.tag==='select');
  selector.props.onChange(event('source'));await settle();
  assert.equal(view.state.reviewState.unresolved.length,1);assert.deepEqual(view.state.reviewState.data_updates,{[row.key]:13500});assert.match(text(view.container),/не подтверждены валюта/);
  await view.state.generate();assert.equal(calls.length,0);
  const checkbox=find(view.container,node=>node.tag==='input'&&node.props.type==='checkbox');checkbox.props['onUpdate:modelValue'](true);await settle();
  assert.equal(view.state.reviewState.unresolved.length,0);assert.equal(view.state.reviewState.confirm_source_basis,true);
  await view.state.generate();await settle();assert.equal(calls.length,2);assert.equal(calls[0].body.data_updates[row.key],13500);assert.deepEqual(calls[1].body.review_decisions,calls[0].body.review_decisions);assert.equal(calls[1].body.confirm_source_basis,true);
  assert.equal(view.state.reviewState.unresolved.length,0);assert.equal(view.state.reviewState.changed,false);
});

test('document basis confirmation is limited to selected ambiguous sources, restored on reload and reset after a source change',async t=>{
  const row=item('Input!B2',{requires_decision:true,proposals:[proposal('old',13000,{basis_confirmation:true}),proposal('new',14000,{basis_confirmation:true})]});
  const value={...model([row]),data_updates:{[row.key]:13000},review_decisions:{[row.key]:{choice:'source',proposal_id:'old'}},confirm_source_basis:true};
  const view=mount(inputsComponent,{model:value,parameters:{}});t.after(view.unmount);await settle();
  assert.equal(view.state.basisChecked,true);assert.equal(view.state.submission.unresolved.length,0);assert.equal(view.state.submission.confirm_source_basis,true);
  view.state.selectProposal(row,event('new'));await settle();assert.equal(view.state.basisChecked,false);assert.equal(view.state.submission.unresolved.length,1);assert.equal(view.state.submission.confirm_source_basis,false);
  view.state.basisChecked=true;await settle();assert.equal(view.state.submission.unresolved.length,0);
  view.state.setChoice(row,event('keep'));await settle();assert.equal(view.state.sourceBasisItems.length,0);assert.equal(view.state.submission.unresolved.length,0);assert.equal(find(view.container,node=>node.tag==='input'&&node.props.type==='checkbox'),undefined);
  view.state.setChoice(row,event('source'));view.state.selectProposal(row,event('old'));await settle();assert.equal(view.state.basisChecked,false);assert.equal(view.state.submission.unresolved.length,1);
  view.state.setChoice(row,event('manual'));view.state.setManual(row,event('14500'));await settle();assert.equal(view.state.submission.unresolved.length,0);assert.equal(view.state.submission.confirm_source_basis,false);
});

test('disabled and formula-only fields cannot be changed through handlers',async t=>{
  const row=item('Input!B2'),readonly=item('Input!C3',{editable:false});
  const props=reactive({model:model([row,readonly]),parameters:{},disabled:true});
  const view=mount(inputsComponent,props);t.after(view.unmount);await settle();
  view.state.setChoice(row,event('manual'));view.state.setManual(row,event('42'));view.state.setParameter(row.key,'42');await settle();assert.deepEqual(view.state.submission.review_decisions,{});
  props.disabled=false;await settle();view.state.setChoice(readonly,event('manual'));view.state.setManual(readonly,event('42'));await settle();assert.deepEqual(view.state.submission.review_decisions,{});
});

test('unresolved named decisions stop original report generation and submitted selections accompany preview and export',async t=>{
  const row=item('Input!B2',{requires_decision:true,proposals:[proposal('new',13500)]}),calls=[];
  const project={id:7,company_id:2,title:'Synthetic',status:'needs_data',revision:4,can_edit:true,inputs:{start:'2028-04-01'},native_model:model([row]),generations:[]};
  const view=mount(workbookComponent,{company,project,stage:'report',api:async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return{...project,revision:5,native_model:{...project.native_model,preview:{blocked:false,formula_count:10,error_count:0,issues:[]}}};}});t.after(view.unmount);await settle();
  await view.state.generate();assert.equal(calls.length,0);assert.match(text(view.container),/Base price/);
  view.state.reviewState={data_updates:{'Input!B2':13500},review_decisions:{'Input!B2':{choice:'source',proposal_id:'new'}},scalars:{},unresolved:[],changed:true};
  await view.state.generate();await settle();assert.equal(calls.length,2);
  assert.deepEqual(calls[0].body,{revision:4,overrides:{},data_updates:{'Input!B2':13500},review_decisions:{'Input!B2':{choice:'source',proposal_id:'new'}},confirm_source_basis:false,source_sha256:'a'.repeat(64)});
  assert.deepEqual(calls[1].body,{...calls[0].body,revision:5,start:'2028-04-01'});
});

test('keeping a disputed scalar resets its override to the original and editing the advanced parameter chooses manual',async t=>{
  const row=item('Input!B2',{requires_decision:true,value:17000,proposals:[proposal('new',17000)]});
  const parameters=reactive({[row.key]:'17000'}),value={...model([row]),parameters:[{key:row.key,editable:true}],overrides:{[row.key]:17000}};
  const view=mount(inputsComponent,{model:value,parameters,onParameter:event=>{parameters[event.key]=event.value;}});t.after(view.unmount);await settle();
  view.state.setChoice(row,event('keep'));await settle();assert.equal(parameters[row.key],'12000');assert.deepEqual(view.state.submission.scalars,{[row.key]:12000});
  parameters[row.key]='18000';view.state.setParameter(row.key,'18000');await settle();
  assert.deepEqual(view.state.submission.review_decisions,{[row.key]:{choice:'manual'}});assert.deepEqual(view.state.submission.scalars,{[row.key]:18000});
});
