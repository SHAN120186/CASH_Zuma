import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,find} from './helpers/mountVue.js';

const progressComponent=await compileComponent(new URL('../src/OperationProgress.vue',import.meta.url));
const appComponent=await compileComponent(new URL('../src/App.vue',import.meta.url),{'./OperationProgress.vue':progressComponent});
const idle={active:false,label:'',completed:0,total:0,error:''};
const user={id:1,username:'synthetic',name:'Synthetic',role:'finance',permissions:['view','export']};
const company={id:2,code:'ZUMA',name:'Synthetic company'};
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};

async function fixture(t,handle) {
  const keys=['window','location','document','fetch','Document','ShadowRoot'];
  const original=keys.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]);
  const originalCreate=Object.getOwnPropertyDescriptor(URL,'createObjectURL');
  const originalRevoke=Object.getOwnPropertyDescriptor(URL,'revokeObjectURL');
  const files={blobs:0,anchors:0,clicks:0},listeners=new Map();
  globalThis.window={addEventListener(){},removeEventListener(){},localStorage:{getItem(){return null;}}};
  globalThis.location={hash:'#home',origin:'https://isolated.test'};
  globalThis.document={hidden:true,activeElement:null,
    addEventListener(type,callback,capture){listeners.set(type+':'+!!capture,callback);},
    removeEventListener(type,callback,capture){if(listeners.get(type+':'+!!capture)===callback)listeners.delete(type+':'+!!capture);},
    querySelector(){return {scrollIntoView(){}};},
    createElement(tag){assert.equal(tag,'a');files.anchors++;return {dataset:{},click(){files.clicks++;},remove(){}};},
    body:{append(){}}};
  URL.createObjectURL=()=>{files.blobs++;return 'blob:synthetic-file';};
  URL.revokeObjectURL=()=>{};
  globalThis.fetch=async(url,options={})=>{
    if(url==='/api/me')return {ok:false,status:401,json:async()=>({detail:'Synthetic unauthenticated startup'})};
    if(url==='/api/logout')return {ok:true,status:200,json:async()=>({ok:true})};
    if(url==='/api/companies')return {ok:true,status:200,json:async()=>({user,csrf:'synthetic',companies:[company]})};
    return handle(url,options);
  };
  let view,unmounted=false;
  const unmount=()=>{if(view&&!unmounted){unmounted=true;view.unmount();}};
  t.after(()=>{
    unmount();
    for(const [key,descriptor] of original)if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];
    if(originalCreate)Object.defineProperty(URL,'createObjectURL',originalCreate);else delete URL.createObjectURL;
    if(originalRevoke)Object.defineProperty(URL,'revokeObjectURL',originalRevoke);else delete URL.revokeObjectURL;
  });
  view=mount(appComponent);await settle();
  Object.assign(view.state,{user,csrf:'synthetic',boot:{accounts:[],categories:[],roles:{},companies:[company]},companyId:2,companyReady:true});
  await settle();
  return {view,files,listeners,unmount};
}

for(const action of ['logout','chooseCompany','unmount']) {
  test(`mounted App cancels a private download on ${action} and ignores its late body`,async t=>{
    const body=deferred();let signal,headers;
    const {view,files,listeners,unmount}=await fixture(t,async(url,options)=>{
      assert.equal(url,'https://isolated.test/api/report-archives/1/files/pdf?company_id=2');
      signal=options.signal;headers=options.headers;
      // Deliberately allow the cached body to resolve after abort, exercising
      // the application's epoch check as well as network cancellation.
      return {ok:true,status:200,redirected:false,
        headers:new Headers({'content-type':'application/pdf','content-disposition':'attachment; filename=synthetic.pdf'}),
        blob:()=>body.promise};
    });
    let prevented=false;
    const link={href:'https://isolated.test/api/report-archives/1/files/pdf?company_id=2',hasAttribute(){return false;}};
    const download=view.state.downloadFile({button:0,target:{closest(){return link;}},preventDefault(){prevented=true;}});
    await settle();
    assert.equal(prevented,true);assert.equal(headers['X-Company-ID'],'2');
    assert.equal(signal.aborted,false);assert.equal(view.state.globalProgress.active,true);
    assert.equal(view.state.globalProgress.completed,1);assert.equal(view.state.globalProgress.total,3);
    if(action==='logout')await view.state.logout();
    else if(action==='chooseCompany'){view.state.chooseCompany();await settle();}
    else unmount();
    assert.equal(signal.aborted,true);
    const afterCancellation={...view.state.globalProgress};
    body.resolve(new Blob(['Synthetic private file']));await download;await settle();
    assert.deepEqual(files,{blobs:0,anchors:0,clicks:0});
    assert.deepEqual(view.state.globalProgress,afterCancellation,'late stages must not revive progress');
    if(action==='logout'){
      assert.equal(view.state.user,null);assert.equal(view.state.companyId,null);
      assert.deepEqual(view.state.globalProgress,idle);
    }
    if(action==='chooseCompany')assert.equal(view.state.companyId,null);
    if(action==='unmount')assert.equal(listeners.size,0);
  });
}

test('mounted App ignores a late checked API response after logout without restoring the session',async t=>{
  const response=deferred();let signal;
  const {view}=await fixture(t,(url,options)=>{assert.equal(url,'/api/accounts');signal=options.signal;return response.promise;});
  const request=view.state.api('/api/accounts');
  const rejected=assert.rejects(request,{name:'AbortError'});
  await view.state.logout();
  assert.equal(signal.aborted,true);assert.equal(view.state.user,null);
  response.resolve({ok:true,status:200,json:async()=>[{id:7,name:'Synthetic cached account'}]});
  await rejected;await settle();
  assert.equal(view.state.user,null);assert.deepEqual(view.state.globalProgress,idle);
});

test('mounted App progresses from zero to full only after the actual API response is checked',async t=>{
  const response=deferred(),body=deferred();
  const {view}=await fixture(t,url=>{assert.equal(url,'/api/accounts');return response.promise;});
  const request=view.state.api('/api/accounts');await settle();
  const bar=()=>find(view.container,node=>node.props?.role==='progressbar');
  assert.equal(view.state.globalProgress.active,true);assert.equal(bar().props['aria-valuenow'],0);
  response.resolve({ok:true,status:200,json:()=>body.promise});await settle();
  assert.equal(view.state.globalProgress.completed,0);assert.equal(bar().props['aria-valuenow'],0);
  body.resolve([{id:7,name:'Synthetic account'}]);assert.deepEqual(await request,[{id:7,name:'Synthetic account'}]);
  await settle();
  assert.equal(view.state.globalProgress.active,false);assert.equal(view.state.globalProgress.error,'');
  assert.equal(view.state.globalProgress.completed,1);assert.equal(bar().props['aria-valuenow'],100);
});
