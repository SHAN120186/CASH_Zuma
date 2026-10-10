import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,find,text,all} from './helpers/mountVue.js';

const progressComponent=await compileComponent(new URL('../src/OperationProgress.vue',import.meta.url));
const periodComponent=await compileComponent(new URL('../src/PeriodFilter.vue',import.meta.url));
const appComponent=await compileComponent(new URL('../src/App.vue',import.meta.url),{'./OperationProgress.vue':progressComponent,'./PeriodFilter.vue':periodComponent});
const companyCard=await compileComponent(new URL('../src/CompanyCard.vue',import.meta.url));
const businessProjects=await compileComponent(new URL('../src/BusinessProjects.vue',import.meta.url));
const reportArchive=await compileComponent(new URL('../src/ReportArchive.vue',import.meta.url));
const reportEntryApp=await compileComponent(new URL('../src/App.vue',import.meta.url),{'./CompanyCard.vue':companyCard,'./BusinessProjects.vue':businessProjects,'./ReportArchive.vue':reportArchive,'./OperationProgress.vue':progressComponent});
const idle={active:false,label:'',completed:0,total:0,error:''};
const user={id:1,username:'synthetic',name:'Synthetic',role:'director',permissions:['view','export','download']};
const company={id:2,code:'ZUMA',name:'Synthetic company'};
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
const otherCompany={id:3,code:'UZGERMED',name:'Other synthetic company'};
const bootstrapBody=(selectedUser=user)=>({accounts:[],categories:[],counterparties:[],roles:{},companies:[company,otherCompany],today:'2026-10-10',user:selectedUser,csrf:'synthetic'});

async function fixture(t,handle,component=appComponent) {
  const keys=['window','location','document','fetch','Document','ShadowRoot'];
  const original=keys.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]);
  const originalCreate=Object.getOwnPropertyDescriptor(URL,'createObjectURL');
  const originalRevoke=Object.getOwnPropertyDescriptor(URL,'revokeObjectURL');
  const files={blobs:0,anchors:0,clicks:0},listeners=new Map();
  globalThis.window={addEventListener(){},removeEventListener(){},localStorage:{getItem(){return null;}},matchMedia(){return {matches:false};}};
  let hash='#home';globalThis.location={get hash(){return hash;},set hash(value){hash=value?'#'+String(value).replace(/^#/,''):'';},origin:'https://isolated.test'};
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
  let view,unmounted=false,cleaned=false;
  const unmount=()=>{if(view&&!unmounted){unmounted=true;view.unmount();}};
  const cleanup=()=>{
    if(cleaned)return;cleaned=true;
    unmount();
    for(const [key,descriptor] of original)if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];
    if(originalCreate)Object.defineProperty(URL,'createObjectURL',originalCreate);else delete URL.createObjectURL;
    if(originalRevoke)Object.defineProperty(URL,'revokeObjectURL',originalRevoke);else delete URL.revokeObjectURL;
  };t.after(cleanup);
  view=mount(component);await settle();
  Object.assign(view.state,{user,csrf:'synthetic',boot:{accounts:[],categories:[],roles:{},companies:[company]},companyId:2,companyReady:true});
  await settle();
  return {view,files,listeners,unmount,cleanup};
}

for(const [page,label,endpoint] of [['business','Бизнес-планы','/api/business-projects?'],['reports','Архив отчётов','/api/report-archives?']]){
  test(`ZUMA opens ${page} from its internal menu after ordinary scoped company selection`,async t=>{
    const calls=[];const {view}=await fixture(t,async(url,options)=>{
      calls.push({url,companyId:options.headers['X-Company-ID']});
      if(url==='/api/bootstrap'){assert.equal(view.state.companyId,2);assert.equal(view.state.companyReady,false);return {ok:true,status:200,json:async()=>bootstrapBody()};}
      assert.ok(url.startsWith(endpoint),url);assert.match(url,/company_id=2(?:&|$)/);assert.equal(view.state.companyId,2);assert.equal(view.state.companyReady,true);assert.equal(view.state.page,page);assert.equal(options.headers['X-Company-ID'],'2');
      return {ok:true,status:200,json:async()=>({items:[],can_upload:false})};
    },reportEntryApp);
    view.state.chooseCompany();await settle();
    assert.equal(find(view.container,node=>node.props?.class==='company-report-actions'),undefined);
    const card=find(view.container,node=>node.props?.class==='company-card');assert.ok(card);card.props.onClick();await settle();
    assert.equal(view.state.companyReady,true);assert.equal(view.state.page,'home');assert.equal(calls.length,1);
    const button=find(view.container,node=>node.tag==='button'&&text(node).trim()===label);assert.ok(button);button.props.onClick();await settle();
    assert.equal(view.state.companyId,2);assert.equal(view.state.page,page);assert.equal(location.hash,'#'+page);
    assert.equal(calls[0].url,'/api/bootstrap');assert.ok(calls.some(call=>call.url.startsWith(endpoint)));assert.ok(calls.every(call=>call.companyId==='2'));
  });
}

test('ordinary selection never loads reports after denied permission or failed bootstrap',async t=>{
  for(const failure of ['no_export','bootstrap_rejected']){
    const calls=[];const {view,cleanup}=await fixture(t,async(url,options)=>{
      calls.push({url,companyId:options.headers['X-Company-ID']});
      if(url==='/api/bootstrap')return failure==='bootstrap_rejected'?{ok:false,status:403,json:async()=>({detail:'Synthetic company denied'})}:{ok:true,status:200,json:async()=>bootstrapBody({...user,role:'employee',permissions:['request']})};
      return {ok:true,status:200,json:async()=>({items:[],total:0})};
    },reportEntryApp);
    view.state.chooseCompany();await settle();await view.state.selectCompany(2);await settle();
    assert.equal(calls.filter(call=>/^\/api\/(?:business-projects|report-archives)/.test(call.url)).length,0);
    assert.notEqual(view.state.page,'reports');assert.equal(location.hash,'#home');assert.ok(calls.every(call=>call.companyId==='2'));
    if(failure==='no_export'){
      assert.equal(view.state.companyReady,true);assert.equal(find(view.container,node=>node.tag==='button'&&text(node).trim()==='Архив отчётов'),undefined);
    }else {assert.equal(view.state.companyReady,false);assert.equal(view.state.error,'Synthetic company denied');}cleanup();
  }
});

test('unfinished forms still prevent ordinary company selection',async t=>{
  const calls=[];const {view}=await fixture(t,async(url,options)=>{calls.push({url,options});return {ok:true,status:200,json:async()=>bootstrapBody()};});
  view.state.saving=true;await view.state.selectCompany(3);view.state.saving=false;
  view.state.reportEditing=true;view.state.chooseCompany();await settle();view.state.reportEditing=false;
  assert.equal(calls.length,0);assert.equal(view.state.companyId,2);assert.equal(view.state.page,'home');
});

test('a late ordinary company bootstrap cannot restore selection after logout, unmount or a newer company',async t=>{
  for(const cancellation of ['logout','unmount','company']){
    const response=deferred(),calls=[];let pendingSignal;
    const {view,unmount,cleanup}=await fixture(t,async(url,options)=>{
      calls.push({url,companyId:options.headers['X-Company-ID']});
      if(url==='/api/bootstrap'&&options.headers['X-Company-ID']==='2'){pendingSignal=options.signal;return {ok:true,status:200,json:()=>response.promise};}
      return {ok:true,status:200,json:async()=>bootstrapBody()};
    },reportEntryApp);
    view.state.chooseCompany();await settle();const pending=view.state.selectCompany(2);await settle();
    assert.equal(view.state.companyReady,false);assert.equal(view.state.page,'home');
    if(cancellation==='logout')await view.state.logout();else if(cancellation==='unmount')unmount();else await view.state.selectCompany(3);
    assert.equal(pendingSignal.aborted,true);response.resolve(bootstrapBody());await pending;await settle();
    assert.equal(calls.filter(call=>/^\/api\/(?:business-projects|report-archives)/.test(call.url)).length,0);assert.notEqual(view.state.page,'business');assert.equal(location.hash,'#home');
    if(cancellation==='company')assert.equal(view.state.companyId,3);else if(cancellation==='logout')assert.equal(view.state.companyId,null);cleanup();
  }
});

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

test('sales has only overview and calendar, saves expected income and cannot confirm actual income',async t=>{
  const sales={...user,role:'sales',company_role:'sales',role_label:'Отдел продаж',permissions:['overview','receipts']};
  const income={id:8,type:'income',name:'Synthetic income'},account={id:7,kind:'bank',name:'Synthetic bank',currency:'UZS'};
  const planned={id:9,account_id:7,category_id:8,counterparty:'Synthetic buyer',amount:'1200',currency:'UZS',date:'2026-10-11',status:'expected'};
  const calls=[];
  const {view}=await fixture(t,async(url,options)=>{
    calls.push({url,options});
    if(url==='/api/receipts')return {ok:true,status:200,json:async()=>options.method==='POST'?{ok:true,id:9}:[planned]};
    if(url==='/api/bootstrap')return {ok:true,status:200,json:async()=>({...bootstrapBody(sales),accounts:[account],categories:[income]})};
    assert.match(url,/^\/api\/dashboard\?/);return {ok:true,status:200,json:async()=>({forecast:[],reserve:'0'})};
  });
  Object.assign(view.state,{user:sales,boot:{...bootstrapBody(sales),accounts:[account],categories:[income]}});await settle();
  assert.deepEqual(view.state.nav.map(item=>item[0]),['home','calendar','profile']);
  assert.deepEqual(view.state.navSections.flatMap(group=>group.items.map(item=>item[0])),['home','calendar']);
  const businessNav=all(view.container,node=>node.tag==='button'&&/^nav-i(?: |$)/.test(node.props?.class||'')).map(text);
  assert.ok(businessNav.some(label=>label.includes('Обзор')));assert.ok(businessNav.some(label=>label.includes('Календарь')));
  for(const forbidden of ['accounts','report','business','reports','requests','ledger','budgets','users']){await view.state.go(forbidden);await view.state.goWith(forbidden);assert.equal(view.state.page,'home');}
  assert.equal(calls.length,0);
  const calendar=find(view.container,node=>node.tag==='button'&&text(node).trim()==='Календарь');calendar.props.onClick();await settle();
  assert.equal(view.state.page,'calendar');
  await view.state.showReceipts();await settle();
  assert.equal(find(view.container,node=>node.tag==='button'&&text(node)==='Подтвердить приход'),undefined);
  view.state.transaction(planned,true);assert.equal(view.state.modal,null);
  find(view.container,node=>node.tag==='button'&&text(node).trim()==='План поступления').props.onClick();await settle();
  assert.equal(view.state.modal.title,'Ожидаемое поступление');
  Object.assign(view.state.form,{account_id:7,category_id:8,amount:'1200',date:'2026-10-11',counterparty:'Synthetic buyer'});
  await view.state.save();await settle();
  const writes=calls.filter(call=>call.options.method==='POST');assert.equal(writes.length,1);assert.equal(writes[0].url,'/api/receipts');
  assert.deepEqual(JSON.parse(writes[0].options.body),{account_id:7,category_id:8,amount:'1200',date:'2026-10-11',counterparty:'Synthetic buyer'});
  assert.equal(view.state.modal,null);assert.equal(view.state.page,'calendar');
  assert.equal(all(view.container,node=>node.tag==='a'&&/^\/api\//.test(node.props.href||'')).length,0);
});

test('sales assignment stays restricted even with stale acting director rights',async t=>{
  const calls=[];const {view}=await fixture(t,async(...args)=>{calls.push(args);return {ok:true,status:200,json:async()=>({})};});
  view.state.user={...user,role:'director',company_role:'sales',permissions:['view','overview','receipts','export','download','write','users','schedule']};await settle();
  assert.deepEqual(view.state.nav.map(item=>item[0]),['home','calendar','profile']);
  assert.equal(view.state.has('download'),false);assert.equal(view.state.has('write'),false);assert.equal(view.state.has('export'),false);
  await view.state.goWith('business');view.state.openRequest({id:99});assert.equal(view.state.page,'home');assert.equal(view.state.cardId,null);
  assert.equal(calls.length,0);
  view.state.user={...view.state.user,holding_role:'admin'};await settle();
  assert.ok(view.state.nav.some(item=>item[0]==='users'),'holding management remains available');
});

test('file guard blocks ordinary and modified stale private links without starting a network download',async t=>{
  const calls=[];const {view,files}=await fixture(t,async(...args)=>{calls.push(args);throw Error('No file request expected');});
  view.state.user={...user,role:'finance',permissions:['view','export']};await settle();
  for(const modified of [{},{ctrlKey:true},{metaKey:true},{shiftKey:true}]){
    let prevented=false;const link={href:'https://isolated.test/api/report-archives/1/files/pdf?company_id=2',hasAttribute(){return false;}};
    await view.state.downloadFile({button:0,...modified,target:{closest(){return link;}},preventDefault(){prevented=true;}});
    assert.equal(prevented,true);
  }
  assert.equal(calls.length,0);assert.deepEqual(files,{blobs:0,anchors:0,clicks:0});
  assert.match(view.state.notice,/Скачивание файлов недоступно/);
});

test('treasury viewing preserves report tabs but CSV templates, ledger exports and attachments require download permission',async t=>{
  const {view}=await fixture(t,async()=>{throw Error('No file request expected');});
  for(const canDownload of [false,true]){
    view.state.user={...user,role:canDownload?'director':'finance',permissions:['view','ledger','import','export',...(canDownload?['download']:[])]};
    view.state.page='ledger';await settle();
    assert.deepEqual(view.state.reportTabs.map(item=>item[0]),['report','business','reports']);
    assert.equal(all(view.container,node=>node.tag==='a'&&node.props.href?.includes('/api/export/ledger.csv')).length,canDownload?1:0);
    view.state.page='import';await settle();
    assert.equal(all(view.container,node=>node.tag==='a'&&node.props.href?.includes('/api/import/template.csv')).length,canDownload?1:0);
    view.state.documents={ledger:{id:1,reference:'Synthetic document'},rows:[{filename:'Synthetic ledger.pdf',url:'/api/documents/7'}]};await settle();
    assert.match(text(view.container),/Synthetic ledger.pdf/);
    assert.equal(all(view.container,node=>node.tag==='a'&&node.props.href?.includes('/api/documents/7')).length,canDownload?1:0);
    view.state.documents=null;
  }
});
