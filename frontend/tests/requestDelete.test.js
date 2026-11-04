import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent, mount, settle, find, text} from './helpers/mountVue.js';

const card = await compileComponent(new URL('../src/requests/RequestCard.vue', import.meta.url));
const request = actions => ({id:42,number:'CF-00042',version:7,status:'paid',counterparty:'Synthetic supplier',
  currency:'UZS',amount:'100.00',date:'2026-11-04',priority:'urgent',channel:'bank',account:'Synthetic bank',
  category:'Synthetic expense',purpose:'Synthetic purchase',actions,required_documents:[],documents_list:[],history:[],route:{}});

function browser(t, {reason='Remove duplicate request',confirm=true}={}) {
  const saved=['window','document'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]);
  const prompts=[],confirmations=[];
  globalThis.window={prompt(message){prompts.push(message);return reason;},confirm(message){confirmations.push(message);return confirm;}};
  globalThis.document={activeElement:null,addEventListener(){},removeEventListener(){}};
  const restore=()=>{for(const[key,descriptor]of saved){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}};
  return {prompts,confirmations,restore};
}

test('server deletion action opens the request control and deletes with version and reason, never a payment',async t=>{
  const dialogs=browser(t),calls=[],events=[];
  const api=async(url,options={})=>{calls.push({url,options});
    if(url==='/api/requests/42/delete')return{deleted:true,preserved_payments:1};
    if(url==='/api/request-rules')return{due_minimums:{}};
    return request(['delete']);
  };
  const view=mount(card,{id:42,api,companyUrl:url=>url,money:value=>value,onChanged:()=>events.push('changed'),onClose:()=>events.push('close')});
  t.after(()=>{view.unmount();dialogs.restore();});await settle();
  const button=find(view.container,node=>node.tag==='button'&&text(node)==='Удалить заявку');
  assert.ok(button,'the server may grant deletion to a financial manager without a client role allowlist');
  await button.props.onClick();await settle();
  const writes=calls.filter(call=>call.options.method);
  assert.equal(writes.length,1);
  assert.equal(writes[0].url,'/api/requests/42/delete');
  assert.equal(writes[0].options.method,'POST');
  assert.deepEqual(JSON.parse(writes[0].options.body),{version:7,reason:'Remove duplicate request'});
  assert.deepEqual(events,['changed','close']);
  assert.match(dialogs.confirmations[0],/CF-00042.*Проведённые платежи сохранятся/);
});

test('without the server deletion action the control is hidden; cancelling does not send a write',async t=>{
  const dialogs=browser(t,{confirm:false});const calls=[];
  const api=async(url,options={})=>{calls.push({url,options});return url==='/api/request-rules'?{due_minimums:{}}:request([]);};
  const view=mount(card,{id:42,api,companyUrl:url=>url,money:value=>value});t.after(()=>{view.unmount();dialogs.restore();});await settle();
  assert.equal(find(view.container,node=>node.tag==='button'&&text(node)==='Удалить заявку'),undefined);
  view.state.r=request(['delete']);await settle();
  await find(view.container,node=>node.tag==='button'&&text(node)==='Удалить заявку').props.onClick();await settle();
  assert.equal(calls.filter(call=>call.options.method).length,0);
});

test('stale deletion refreshes the card and its actions without retrying or emitting success',async t=>{
  const dialogs=browser(t);const calls=[],events=[];let reads=0;
  const api=async(url,options={})=>{calls.push({url,options});
    if(url==='/api/requests/42/delete')throw Object.assign(new Error('Заявка изменена.'),{status:409});
    if(url==='/api/request-rules')return{due_minimums:{}};
    const row=request(reads++===0?['delete']:[]);if(reads>1)row.version=8;return row;
  };
  const view=mount(card,{id:42,api,companyUrl:url=>url,money:value=>value,onChanged:()=>events.push('changed'),onClose:()=>events.push('close')});
  t.after(()=>{view.unmount();dialogs.restore();});await settle();
  await find(view.container,node=>node.tag==='button'&&text(node)==='Удалить заявку').props.onClick();await settle();
  assert.equal(calls.filter(call=>call.url.endsWith('/delete')).length,1);
  assert.equal(view.state.r.version,8);
  assert.equal(find(view.container,node=>node.tag==='button'&&text(node)==='Удалить заявку'),undefined);
  assert.match(text(view.container),/Заявка изменена/);
  assert.deepEqual(events,[]);
});
