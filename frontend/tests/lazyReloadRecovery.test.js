import {test} from 'node:test';
import assert from 'node:assert/strict';
import {lazyPage} from '../src/lazyPage.js';
import {compileComponent,mount,settle,find,text} from './helpers/mountVue.js';
import {lang,setLang} from '../src/i18n/index.js';

const appComponent=await compileComponent(new URL('../src/App.vue',import.meta.url));
const button=(view,label)=>find(view.container,node=>node.tag==='button'&&text(node)===label);
const staleChunk=()=>Promise.reject(new TypeError('Synthetic stale chunk /static/erp/assets/old-release.js returned 404'));

async function fixture(t,handler){
  const keys=['window','location','document','fetch'],originals=keys.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]);
  let reloads=0;
  globalThis.window={addEventListener(){},removeEventListener(){},localStorage:{getItem:()=>null},matchMedia:()=>({matches:false})};
  globalThis.location={hash:'#home',origin:'https://isolated.test',reload(){reloads++;}};
  globalThis.document={hidden:true,activeElement:null,addEventListener(){},removeEventListener(){},querySelector:()=>({scrollIntoView(){}})};
  globalThis.fetch=async(url,options)=>url==='/api/me'?{ok:false,status:401,json:async()=>({detail:'Synthetic unauthenticated startup'})}:handler(url,options);
  const view=mount(appComponent);await settle();
  t.after(()=>{view.unmount();for(const [key,descriptor] of originals)if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];});
  return{view,reloads:()=>reloads};
}

test('stale hashed code keeps retry but never reloads until the user explicitly chooses it',async t=>{
  let attempts=0,reloads=0;
  const view=mount(lazyPage(()=>{attempts++;return staleChunk();},{reload:()=>reloads++}));
  view.app.config.errorHandler=()=>{};t.after(view.unmount);await settle();
  assert.equal(reloads,0);assert.match(text(view.container),/Раздел мог обновиться/);
  button(view,'Повторить').props.onClick();await settle();
  assert.equal(attempts,2);assert.equal(reloads,0);assert.ok(button(view,'Обновить страницу'));
  button(view,'Обновить страницу').props.onClick();assert.equal(reloads,1);
});

test('the actual App lazy-page reload callback blocks every tracked form and editing state',async t=>{
  const {view,reloads}=await fixture(t,()=>{throw Error('No additional API reads or writes expected');});
  const failedPage=mount(view.state.lazySection(staleChunk));failedPage.app.config.errorHandler=()=>{};t.after(failedPage.unmount);await settle();
  const reloadButton=button(failedPage,'Обновить страницу');assert.ok(reloadButton);
  for(const [key,value] of [['modal',{}],['documents',{}],['editor',{}],['cardId',42],['reportEditing',true]]){
    view.state[key]=value;reloadButton.props.onClick();
    assert.equal(reloads(),0,key);assert.equal(view.state.notice,'Сохраните или закройте текущую форму перед обновлением.');
    view.state[key]=key==='reportEditing'?false:null;
  }
  for(const key of ['busy','saving']){
    view.state[key]=true;reloadButton.props.onClick();assert.equal(reloads(),0,key);
    assert.equal(view.state.notice,'Дождитесь завершения текущей операции перед обновлением.');view.state[key]=false;
  }
  reloadButton.props.onClick();assert.equal(reloads(),1,'explicit reload succeeds after all guards clear');
});

test('an in-flight API operation blocks explicit reload even when the outer busy flag is false',async t=>{
  let finish;const pendingResponse=new Promise(resolve=>finish=resolve);const calls=[];
  const {view,reloads}=await fixture(t,(url,options)=>{calls.push({url,method:options.method});return pendingResponse;});
  const pending=view.state.api('/api/synthetic-pending',{method:'POST'});assert.equal(view.state.busy,false);
  view.state.refreshVersion();assert.equal(reloads(),0);assert.equal(calls.length,1);
  finish({ok:true,status:200,json:async()=>({ok:true})});await pending;await settle();
  assert.equal(reloads(),0,'finishing an operation never triggers an automatic reload');
  view.state.refreshVersion();assert.equal(reloads(),1);assert.equal(calls.length,1,'reload does not replay the API operation');
});

test('the explicit reload explanation and action are available in Uzbek',async t=>{
  const previous=lang.value;setLang('uz');t.after(()=>setLang(previous));
  const view=mount(lazyPage(staleChunk,{reload(){}}));view.app.config.errorHandler=()=>{};t.after(view.unmount);await settle();
  assert.match(text(view.container),/Bo‘lim yangilangan bo‘lishi mumkin/);assert.ok(button(view,'Sahifani yangilash'));
});
