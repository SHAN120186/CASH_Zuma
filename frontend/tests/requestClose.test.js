import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,find,text} from './helpers/mountVue.js';
import {lang,setLang} from '../src/i18n/index.js';

const component=await compileComponent(new URL('../src/requests/RequestCard.vue',import.meta.url));
const reason='Synthetic duplicate without payment';
const request={id:42,number:'CF-00042',version:7,status:'pending',counterparty:'Synthetic supplier',
 currency:'UZS',amount:'100.00',date:'2026-11-04',priority:'urgent',channel:'bank',account:'Synthetic bank',
 category:'Synthetic expense',purpose:'Synthetic purchase',actions:['close','return'],required_documents:[],documents_list:[],history:[],route:{}};
const decisionForm=view=>find(view.container,node=>node.tag==='form'&&String(node.props.class||'').includes('rq-decision'));

async function fixture(t,{confirm=true,response={deleted:true,permanent:true,id:42},failure,locale='ru'}={}){
 const saved=['window','document'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]),previousLang=lang.value;
 const confirmations=[],calls=[],events=[];
 setLang(locale);
 globalThis.window={confirm(message){confirmations.push(message);return confirm;}};
 globalThis.document={activeElement:null,addEventListener(){},removeEventListener(){}};
 const api=async(url,options={})=>{
  calls.push({url,options});
  if(options.method){if(failure)throw Object.assign(new Error(failure.message),{status:failure.status});return response;}
  if(url==='/api/request-rules')return{due_minimums:{}};
  const reads=calls.filter(call=>call.url==='/api/requests/42'&&!call.options.method).length;
  return{...request,version:reads>1?8:7};
 };
 const view=mount(component,{id:42,api,companyUrl:url=>url,money:value=>value,onChanged:()=>events.push('changed'),onClose:()=>events.push('close')});
 t.after(()=>{view.unmount();setLang(previousLang);for(const[key,descriptor]of saved){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}});
 await settle();view.state.decision={action:'close',note:reason,date:request.date};await settle();
 const submit=()=>decisionForm(view).props.onSubmit({preventDefault(){}});
 return{view,calls,events,confirmations,submit};
}

test('confirmed close deletes the request, files and history and closes without reading a deleted card',async t=>{
 const{view,calls,events,confirmations,submit}=await fixture(t);
 assert.match(text(view.container),/безвозвратно удалит заявку, все её файлы и историю.*Восстановление невозможно/);
 const input=find(decisionForm(view),node=>node.tag==='textarea');
 assert.equal(input.props.required,true);assert.equal(input.props.minlength,10);
 await submit();await settle();
 assert.match(confirmations[0],/Безвозвратно удалить заявку CF-00042.*все её файлы и история.*невозможно/);
 const writes=calls.filter(call=>call.options.method);
 assert.equal(writes.length,1);assert.equal(writes[0].url,'/api/requests/42/decision');
 assert.deepEqual(JSON.parse(writes[0].options.body),{action:'close',note:reason,date:null,version:7});
 assert.equal(calls.filter(call=>call.url==='/api/requests/42').length,1,'only the initial card read exists');
 assert.deepEqual(events,['changed','close']);assert.equal(view.state.notice,'');assert.equal(view.state.error,'');
});

test('declining irreversible close preserves the reason and sends no POST or card reload',async t=>{
 const{view,calls,events,confirmations,submit}=await fixture(t,{confirm:false});const initialCalls=calls.length;
 await submit();await settle();
 assert.equal(confirmations.length,1);assert.equal(calls.length,initialCalls);
 assert.equal(view.state.decision.note,reason);assert.equal(view.state.busy,false);assert.deepEqual(events,[]);
});

test('repeated submit while permanent close is pending sends exactly one deletion',async t=>{
 let finish;const response=new Promise(resolve=>{finish=resolve;});
 const{view,calls,events,confirmations,submit}=await fixture(t,{response});
 const pending=submit();await settle();assert.equal(view.state.busy,true);
 await submit();await settle();assert.equal(calls.filter(call=>call.options.method).length,1);assert.equal(confirmations.length,1);
 finish({deleted:true,permanent:true,id:42});await pending;await settle();assert.deepEqual(events,['changed','close']);assert.equal(view.state.busy,false);
});

test('missing and whitespace-only reasons never open confirmation or send a write',async t=>{
 const{view,calls,events,confirmations,submit}=await fixture(t);
 for(const note of['','short','             ']){
  view.state.decision.note=note;await submit();await settle();
  assert.match(view.state.error,/Укажите причину закрытия не короче 10 символов/);
 }
 assert.equal(confirmations.length,0);assert.equal(calls.filter(call=>call.options.method).length,0);assert.deepEqual(events,[]);
});

for(const status of[409,403,503])test(`close HTTP ${status} preserves the dialog and never emits deletion success`,async t=>{
 const{view,calls,events,submit}=await fixture(t,{failure:{status,message:'Synthetic close rejected'}});
 await submit();await settle();
 assert.match(view.state.error,/Synthetic close rejected/);assert.equal(view.state.notice,'');assert.equal(view.state.busy,false);
 assert.equal(view.state.decision.note,reason);assert.ok(decisionForm(view));assert.deepEqual(events,[]);
 assert.equal(calls.filter(call=>call.options.method).length,1);
 assert.equal(calls.filter(call=>call.url==='/api/requests/42').length,status===409?2:1);
 assert.equal(view.state.r.version,status===409?8:7);
});

for(const response of[{deleted:true,id:42},{deleted:true,permanent:true,id:99},{status:'cancelled',id:42}])test(`close requires the permanent deletion marker for its own request: ${JSON.stringify(response)}`,async t=>{
 const{view,calls,events,submit}=await fixture(t,{response});await submit();await settle();
 assert.match(view.state.error,/Сервер не подтвердил удаление заявки/);assert.equal(view.state.notice,'');assert.deepEqual(events,[]);
 assert.equal(calls.filter(call=>call.url==='/api/requests/42').length,1);assert.ok(decisionForm(view));
});

test('close confirmation and irreversible warning are translated to Uzbek',async t=>{
 const{view,confirmations,submit}=await fixture(t,{locale:'uz',confirm:false});
 assert.match(text(view.container),/Tiklashning iloji yoʻq/);await submit();await settle();
 assert.match(confirmations[0],/CF-00042.*barcha fayllari va tarixi/);assert.doesNotMatch(confirmations[0],/[А-Яа-яЁё]/);
});

test('returning for correction closes the card without a redundant reload or deletion confirmation',async t=>{
 const{view,calls,events,confirmations,submit}=await fixture(t,{response:request});
 view.state.decision.action='return';await submit();await settle();
 assert.equal(confirmations.length,0);assert.equal(calls.filter(call=>call.url==='/api/requests/42').length,1);
 assert.deepEqual(events,['changed','close']);assert.equal(view.state.notice,'Заявка возвращена на доработку.');
});

for (const action of ['check','approve','reschedule']) test(`successful ${action} closes the card after confirmation`,async t=>{
 const {view,events,submit}=await fixture(t,{response:{ok:true}});
 view.state.decision={action,note:'',date:''};await settle();
 await submit();await settle();assert.deepEqual(events,['changed','close']);
});

for (const action of ['submit','return','approve']) test(`failed ${action} keeps the card open`,async t=>{
 const {view,events}=await fixture(t,{failure:{status:503,message:'Save failed'}});
 view.state.decision={action,note:reason,date:request.date};
 if(action==='submit')await view.state.submit();else await view.state.decide();await settle();
 assert.deepEqual(events,[]);assert.match(view.state.error,/Save failed/);
});
