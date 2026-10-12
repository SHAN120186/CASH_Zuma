import {test} from 'node:test';
import assert from 'node:assert/strict';
import {reactive} from 'vue';
import {groupAmount,amountInput} from '../src/amountInput.js';
import {compileComponent,mount,settle,find,text} from './helpers/mountVue.js';
const money=await compileComponent(new URL('../src/MoneyInput.vue',import.meta.url));
const app=await compileComponent(new URL('../src/App.vue',import.meta.url),{'./MoneyInput.vue':money});
test('money entry groups thousands without rounding, accepts pasted spaces and preserves decimal text',async t=>{
 let latest='';const props=reactive({modelValue:'1000.50','onUpdate:modelValue':v=>{latest=v;props.modelValue=v;}});
 const view=mount(money,props);t.after(view.unmount);await settle();
 const input=find(view.container,n=>n.tag==='input');assert.equal(input.value,'1 000.50');
 for(const [raw,canonical,display] of [['1234567','1234567','1 234 567'],['1\u202f234,50','1234.50','1 234.50'],['9999999999999.99','9999999999999.99','9 999 999 999 999.99'],['1000.','1000.','1 000.'],['','','']]){
  input.value=raw;input.selectionStart=raw.length;input.props.onInput({target:input,inputType:'insertText'});await settle();assert.equal(latest,canonical);assert.equal(input.value,display);
 }
 assert.equal(groupAmount('1e3'),'1e3','invalid input is not silently converted into another amount');
});
test('editing in the middle and deleting grouping spaces retains a useful caret',()=>{
 const target={value:'12 934',selectionStart:4,setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b;}};
 assert.equal(amountInput({target,inputType:'insertText'},'1 234'),'12934');assert.equal(target.selectionStart,4);
 Object.assign(target,{value:'1234',selectionStart:1});assert.equal(amountInput({target,inputType:'deleteContentBackward'},'1 234'),'234');assert.equal(target.selectionStart,0);
 Object.assign(target,{value:'1234',selectionStart:1});assert.equal(amountInput({target,inputType:'deleteContentForward'},'1 234'),'134');
});
test('sales receipt form autofills INN, sends exact grouped amount and deletes only after confirmation',async t=>{
 const keys=['window','location','document','fetch','Document','ShadowRoot'],saved=keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]);
 const calls=[],user={id:4,role:'sales',permissions:['overview','receipts','receipt_edit','receipt_delete']};
 const boot={today:'2026-10-12',csrf:'synthetic',user,accounts:[{id:7,name:'Test bank',currency:'UZS',kind:'bank'}],categories:[{id:8,name:'Test income',type:'income'}],counterparties:[{name:'Mikon synthetic',inn:'001234567'}],companies:[{id:1,name:'Synthetic company',code:'ZUMA'}],roles:{}};
 globalThis.window={addEventListener(){},localStorage:{getItem(){return null;}}};globalThis.location={hash:'#calendar'};
 globalThis.document={hidden:true,activeElement:null,addEventListener(){},removeEventListener(){},querySelector(){return{scrollIntoView(){}};}};
 let failRefresh=false;
 globalThis.fetch=async(url,options={})=>{if(url==='/api/me')return{ok:false,status:401,json:async()=>({detail:'Test login'})};calls.push({url,options});if(failRefresh&&(!options.method||options.method==='GET'))return{ok:false,status:503,json:async()=>({detail:'Test refresh failed'})};return{ok:true,status:200,json:async()=>url==='/api/bootstrap'?boot:url==='/api/receipts'&&(!options.method||options.method==='GET')?[]:url.startsWith('/api/dashboard')?{forecast:[],accounts:[]}:{id:21}};};
 const view=mount(app);t.after(()=>{view.unmount();for(const[k,d]of saved){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];}});await settle();
 Object.assign(view.state,{user,boot,companyId:1,companyReady:true,page:'calendar',currency:'UZS',bootstrapLocalDay:new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Tashkent'})});await settle();
 view.state.receipt();await settle();view.state.form.counterparty='Mikon synthetic';view.state.receiptPartyChanged();assert.equal(view.state.form.inn,'001234567');
 const input=find(view.container,n=>n.tag==='input'&&n.props.autocomplete==='off'&&n.props.inputmode==='decimal');
 input.value='1234567.50';input.selectionStart=10;input.props.onInput({target:input,inputType:'insertText'});await settle();
 assert.equal(input.value,'1 234 567.50');await view.state.save();await settle();
 const post=calls.find(c=>c.url==='/api/receipts'&&c.options.method==='POST');assert.equal(JSON.parse(post.options.body).amount,'1234567.50');
 const row={id:21,version:3,account_id:7,category_id:8,counterparty:'Mikon synthetic',inn:'001234567',amount:'1000',date:'2026-10-12',currency:'UZS',status:'expected',can_edit:true,can_delete:true};
 Object.assign(view.state,{receipts:[row],receiptsOpen:true});view.state.receipt(row);await settle();assert.equal(view.state.form.version,3);assert.match(text(view.container),/Редактировать план поступления/);
 view.state.modal=null;view.state.removeReceipt(row);await settle();assert.match(text(view.container),/Mikon synthetic.*1 000.*UZS/);assert.equal(calls.filter(c=>c.options.method==='DELETE').length,0);
 failRefresh=true;await view.state.save();await settle();assert.equal(calls.filter(c=>c.options.method==='DELETE').length,1);assert.equal(calls.find(c=>c.options.method==='DELETE').url,'/api/receipts/21?version=3');assert.equal(view.state.receipts.length,0);assert.equal(view.state.modal,null);assert.match(view.state.error,/Изменения сохранены/);
});
