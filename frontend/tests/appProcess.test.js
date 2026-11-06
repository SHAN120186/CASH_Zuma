import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,find,text,all} from './helpers/mountVue.js';
import {lang,setLang} from '../src/i18n/index.js';

const progressComponent=await compileComponent(new URL('../src/OperationProgress.vue',import.meta.url));
const periodComponent=await compileComponent(new URL('../src/PeriodFilter.vue',import.meta.url));
const appComponent=await compileComponent(new URL('../src/App.vue',import.meta.url),{'./OperationProgress.vue':progressComponent,'./PeriodFilter.vue':periodComponent});
const companyCard=await compileComponent(new URL('../src/CompanyCard.vue',import.meta.url));
const businessProjects=await compileComponent(new URL('../src/BusinessProjects.vue',import.meta.url));
const reportArchive=await compileComponent(new URL('../src/ReportArchive.vue',import.meta.url));
const financialStatements=await compileComponent(new URL('../src/FinancialStatements.vue',import.meta.url),{'./OperationProgress.vue':progressComponent});
const reportEntryApp=await compileComponent(new URL('../src/App.vue',import.meta.url),{'./CompanyCard.vue':companyCard,'./BusinessProjects.vue':businessProjects,'./ReportArchive.vue':reportArchive,'./FinancialStatements.vue':financialStatements,'./OperationProgress.vue':progressComponent});
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
  Object.assign(view.state,{user,csrf:'synthetic',boot:{accounts:[],categories:[],roles:{},companies:[company],today:'2026-10-10'},companyId:2,companyReady:true});
  await settle();
  return {view,files,listeners,unmount,cleanup};
}

test('account card warns finance about un reconciled statements but never the sales role',async t=>{
 const {view}=await fixture(t,async()=>({ok:true,status:200,json:async()=>([])}));
 const account={id:21,name:'Synthetic bank',kind:'bank',currency:'UZS',opening:'0',balance:'10',opening_date:'2026-10-01',unreconciled_statements:2};
 view.state.user={...user,role:'finance',permissions:['view','ledger','pay_bank']};view.state.boot={...view.state.boot,accounts:[account]};view.state.rows=[account];view.state.currency='UZS';view.state.page='accounts';await settle();
 assert.match(text(view.container),/Есть несверенные выписки: 2/);assert.match(text(view.container),/Ручная оплата заявок с этого счёта временно недоступна/);
 view.state.user={...user,role:'sales',permissions:['view','ledger','pay_bank']};await settle();assert.doesNotMatch(text(view.container),/Есть несверенные выписки/);
});

test('request cards and paginated table retain the server due-date order in each company and explain it in RU and UZ',async t=>{
 const prior=lang.value;t.after(()=>setLang(prior));setLang('ru');
 const request=(id,date)=>({id,number:`CF-${String(id).padStart(5,'0')}`,date,status:'approved',currency:'UZS',amount:'10.00',counterparty:`Synthetic supplier ${id}`,purpose:'Synthetic expense',account:'Synthetic bank',priority:'normal',actions:[]});
 const cards=[request(91,'2026-10-05'),request(3,'2026-10-10'),request(70,'2026-10-13')];
 const table=Array.from({length:16},(_,index)=>request(100-index,`2026-10-${String(index+1).padStart(2,'0')}`));
 const calls=[];let mode='cards';
 const {view}=await fixture(t,async(url,options)=>{
  assert.ok(url.startsWith('/api/requests?'));const params=new URL(url,'https://isolated.test').searchParams,page=Number(params.get('page')),size=Number(params.get('page_size'));
  assert.equal(size,15);
  calls.push({company:options.headers['X-Company-ID'],params});
  const ordered=mode==='cards'?cards:table;return {ok:true,status:200,json:async()=>({items:ordered.slice((page-1)*size,page*size),total:ordered.length})};
 });
 Object.assign(view.state,{page:'requests',user:{...user,permissions:['view','ledger','request']},boot:bootstrapBody(),currency:'UZS'});await settle();
 const cardNumbers=()=>all(view.container,node=>node.tag==='section'&&String(node.props.class||'').split(/\s+/).includes('rq-card')).map(node=>text(node).match(/CF-\d+/)?.[0]);
 const tableNumbers=()=>all(find(view.container,node=>node.tag==='tbody'),node=>node.tag==='tr').map(node=>text(node).match(/CF-\d+/)?.[0]);
 for(const companyId of [2,3]){
  view.state.companyId=companyId;view.state.listPage=1;await view.state.load();await settle();
  assert.deepEqual(cardNumbers(),cards.map(row=>row.number));assert.match(text(view.container),/Сначала ранние сроки оплаты: просроченные, затем сегодняшние и ближайшие/);assert.equal(calls.at(-1).company,String(companyId));
 }
 mode='table';view.state.requestArchive='archived';view.state.requestStatus='approved';await view.state.load();await settle();assert.deepEqual(tableNumbers(),table.slice(0,15).map(row=>row.number));
 assert.equal(view.state.listPages,2);
 const next=()=>find(view.container,node=>node.tag==='button'&&text(node).trim()==='Далее →');
 assert.ok(next());assert.equal(next().props.disabled,false);
 await next().props.onClick();await settle();assert.deepEqual(tableNumbers(),[table[15].number]);assert.equal(view.state.listPage,2);assert.equal(next().props.disabled,true);assert.equal(calls.at(-1).params.get('page_size'),'15');assert.equal(calls.at(-1).params.get('archive'),'archived');assert.equal(calls.at(-1).params.get('state'),'approved');
 assert.equal(calls.some(call=>call.params.has('sort')||call.params.has('order')),false);
 setLang('uz');await settle();assert.match(text(view.container),/Avval eng erta to‘lov muddatlari: muddati o‘tgan, so‘ng bugungi va yaqin kunlardagi arizalar/);
});

for(const [page,label,endpoint] of [['business','Бизнес-планы','/api/business-projects?'],['reports','Архив отчётов','/api/report-archives?'],['pnl','P&L','/api/financial-statements?'],['balance','Баланс','/api/financial-statements?']]){
  test(`ZUMA opens ${page} from its internal menu after ordinary scoped company selection`,async t=>{
    const calls=[];const {view}=await fixture(t,async(url,options)=>{
      calls.push({url,companyId:options.headers['X-Company-ID']});
      if(url==='/api/bootstrap'){assert.equal(view.state.companyId,2);assert.equal(view.state.companyReady,false);return {ok:true,status:200,json:async()=>bootstrapBody()};}
      assert.ok(url.startsWith(endpoint),url);assert.match(url,/company_id=2(?:&|$)/);assert.equal(view.state.companyId,2);assert.equal(view.state.companyReady,true);assert.equal(view.state.page,page);assert.equal(options.headers['X-Company-ID'],'2');
      return {ok:true,status:200,json:async()=>({items:[],sources:[],source:null,can_upload:false})};
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
    assert.match(url,/^\/api\/dashboard\?/);return {ok:true,status:200,json:async()=>({forecast:[{date:'2026-10-12',opening:'0',incoming:'1200',outgoing:'0',balance:'1200',account_shortfalls:[],events:[{...planned,kind:'in',name:planned.counterparty,overdue:true}]}],reserve:'0'})};
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
  assert.equal(find(view.container,node=>node.tag==='button'&&text(node)==='Подтвердить приход'),undefined);
  view.state.transaction(planned,true);assert.equal(view.state.modal,null);
  find(view.container,node=>node.tag==='button'&&text(node).trim()==='План поступления').props.onClick();await settle();
  assert.equal(view.state.modal.title,'Ожидаемое поступление');
  Object.assign(view.state.form,{account_id:7,category_id:8,amount:'1200',date:'2026-10-11',counterparty:'Synthetic buyer'});
  await view.state.save();await settle();
  const writes=calls.filter(call=>call.options.method==='POST');assert.equal(writes.length,1);assert.equal(writes[0].url,'/api/receipts');
  assert.deepEqual(JSON.parse(writes[0].options.body),{account_id:7,category_id:8,amount:'1200',date:'2026-10-11',counterparty:'Synthetic buyer',inn:'',note:'',version:null});
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

test('procurement edits an expected receipt with INN and note without actual-payment rights',async t=>{
  const buyer={...user,role:'procurement',company_role:'procurement',permissions:['request','calendar','counterparties','receipt_edit']};
  const account={id:7,kind:'bank',name:'Synthetic bank',currency:'UZS'},income={id:8,type:'income',name:'Synthetic income'};
  const planned={id:9,version:2,account_id:7,category_id:8,counterparty:'Synthetic buyer',inn:'001234567',note:'Invoice expected',amount:'1200',currency:'UZS',date:'2026-10-11',status:'expected',can_edit:true};
  const boot={...bootstrapBody(buyer),accounts:[account],categories:[income],counterparties:[{name:'Imported buyer',inn:'009876543'}]};
  const calls=[];const {view}=await fixture(t,async(url,options)=>{
    calls.push({url,options});
    if(url==='/api/bootstrap')return {ok:true,status:200,json:async()=>boot};
    if(url.startsWith('/api/receipt-counterparties?'))return {ok:true,status:200,json:async()=>({items:[{id:1,name:'Imported buyer',inn:'009876543'}]})};
    if(url==='/api/receipts/9')return {ok:true,status:200,json:async()=>({id:9,version:3})};
    if(url==='/api/receipts')return {ok:true,status:200,json:async()=>[planned]};
    assert.match(url,/^\/api\/dashboard\?/);return {ok:true,status:200,json:async()=>({forecast:[{date:'2026-10-12',opening:'0',incoming:'1200',outgoing:'0',balance:'1200',account_shortfalls:[],events:[{...planned,kind:'in',name:planned.counterparty,overdue:true}]}],reserve:'0'})};
  });
  Object.assign(view.state,{user:buyer,boot});await settle();
  assert.deepEqual(view.state.nav.map(n=>n[0]),['requests','calendar','counterparties','profile']);
  assert.equal(view.state.has('write'),false);assert.equal(view.state.has('download'),false);
  await view.state.go('calendar');await settle();
  assert.equal(find(view.container,n=>n.tag==='button'&&text(n).trim()==='Подтвердить приход'),undefined);
  find(view.container,n=>n.tag==='button'&&text(n).trim()==='Редактировать').props.onClick();await settle();
  const fields=view.state.modal.fields.map(f=>f.key);
  assert.ok(fields.indexOf('counterparty')<fields.indexOf('amount'));assert.ok(fields.includes('inn'));assert.ok(fields.includes('note'));
  view.state.form.counterparty='Imported buyer';view.state.receiptPartyChanged();await new Promise(resolve=>setTimeout(resolve,320));await settle();assert.equal(view.state.form.inn,'009876543');
  view.state.form.note='Updated expected date';await view.state.save();await settle();
  const writes=calls.filter(c=>c.options.method==='PUT');assert.equal(writes.length,1);assert.equal(writes[0].url,'/api/receipts/9');
  const body=JSON.parse(writes[0].options.body);assert.equal(body.version,2);assert.equal(body.inn,'009876543');assert.equal(body.note,'Updated expected date');
  assert.equal(calls.some(c=>c.url==='/api/ledger'),false);
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

test('finance downloads an available request attachment without general download rights',async t=>{
  const revokeTimers=[],setTimeoutBefore=globalThis.setTimeout;
  globalThis.setTimeout=(callback,delay,...args)=>{const timer=setTimeoutBefore(callback,delay,...args);if(delay===60000)revokeTimers.push(timer);return timer;};
  t.after(()=>{globalThis.setTimeout=setTimeoutBefore;for(const timer of revokeTimers)clearTimeout(timer);});
  const calls=[];
  const {view,files}=await fixture(t,async(url,options)=>{
    calls.push({url,options});
    return {ok:true,status:200,redirected:false,
      headers:new Headers({'content-type':'application/pdf','content-disposition':'attachment; filename=invoice.pdf'}),
      blob:async()=>new Blob(['Synthetic request invoice'])};
  });
  view.state.user={...user,role:'finance',permissions:['view','export']};await settle();
  assert.equal(view.state.has('download'),false);
  let prevented=false;const link={href:'https://isolated.test/api/request-documents/9?company_id=2',hasAttribute(){return false;}};
  await view.state.downloadFile({button:0,target:{closest(){return link;}},preventDefault(){prevented=true;}});await settle();
  assert.equal(prevented,true);assert.equal(calls.length,1);assert.equal(calls[0].url,link.href);
  assert.equal(calls[0].options.headers['X-Company-ID'],'2');assert.equal(calls[0].options.credentials,'same-origin');
  assert.deepEqual(files,{blobs:1,anchors:1,clicks:1});
  assert.equal(view.state.globalProgress.active,false);assert.equal(view.state.globalProgress.error,'');
  assert.equal(view.state.globalProgress.completed,3);assert.equal(view.state.globalProgress.total,3);
});

for(const status of [403,404]) {
  test(`finance request attachment denied with ${status} never creates a browser download`,async t=>{
    const calls=[];let bodies=0;const message=`Synthetic attachment access ${status}`;
    const {view,files}=await fixture(t,async(url,options)=>{
      calls.push({url,options});return {ok:false,status,json:async()=>({detail:message}),blob:async()=>{bodies++;return new Blob(['Forbidden file']);}};
    });
    view.state.user={...user,role:'finance',permissions:['view','export']};await settle();
    assert.equal(view.state.has('download'),false);
    const link={href:'https://isolated.test/api/request-documents/9?company_id=2',hasAttribute(){return false;}};
    await view.state.downloadFile({button:0,target:{closest(){return link;}},preventDefault(){}});await settle();
    assert.equal(calls.length,1);assert.equal(calls[0].options.headers['X-Company-ID'],'2');assert.equal(bodies,0);
    assert.deepEqual(files,{blobs:0,anchors:0,clicks:0});assert.equal(view.state.globalProgress.active,false);
    assert.equal(view.state.globalProgress.error,message);
  });
}

test('finance request attachment is cancelled when the company changes and ignores its late body',async t=>{
  const body=deferred();let signal;
  const {view,files}=await fixture(t,async(url,options)=>{
    assert.equal(url,'https://isolated.test/api/request-documents/9?company_id=2');assert.equal(options.headers['X-Company-ID'],'2');signal=options.signal;
    return {ok:true,status:200,redirected:false,
      headers:new Headers({'content-type':'application/pdf','content-disposition':'attachment; filename=invoice.pdf'}),blob:()=>body.promise};
  });
  view.state.user={...user,role:'finance',permissions:['view','export']};await settle();
  assert.equal(view.state.has('download'),false);
  const link={href:'https://isolated.test/api/request-documents/9?company_id=2',hasAttribute(){return false;}};
  const download=view.state.downloadFile({button:0,target:{closest(){return link;}},preventDefault(){}});await settle();
  assert.equal(signal.aborted,false);assert.equal(view.state.globalProgress.active,true);
  view.state.chooseCompany();await settle();assert.equal(signal.aborted,true);assert.equal(view.state.companyId,null);
  const cancelled={...view.state.globalProgress};body.resolve(new Blob(['Late request invoice']));await download;await settle();
  assert.deepEqual(files,{blobs:0,anchors:0,clicks:0});assert.deepEqual(view.state.globalProgress,cancelled);
});

test('treasury viewing preserves report tabs but CSV templates, ledger exports and attachments require download permission',async t=>{
  const {view}=await fixture(t,async()=>{throw Error('No file request expected');});
  for(const canDownload of [false,true]){
    view.state.user={...user,role:canDownload?'director':'finance',permissions:['view','ledger','import','export',...(canDownload?['download']:[])]};
    view.state.page='ledger';await settle();
    assert.deepEqual(view.state.reportTabs.map(item=>item[0]),['report','pnl','balance','rating','business','reports']);
    assert.equal(all(view.container,node=>node.tag==='a'&&node.props.href?.includes('/api/export/ledger.csv')).length,canDownload?1:0);
    view.state.page='import';await settle();
    assert.equal(all(view.container,node=>node.tag==='a'&&node.props.href?.includes('/api/import/template.csv')).length,canDownload?1:0);
    view.state.documents={ledger:{id:1,reference:'Synthetic document'},rows:[{filename:'Synthetic ledger.pdf',url:'/api/documents/7'}]};await settle();
    assert.match(text(view.container),/Synthetic ledger.pdf/);
    assert.equal(all(view.container,node=>node.tag==='a'&&node.props.href?.includes('/api/documents/7')).length,canDownload?1:0);
    view.state.documents=null;
  }
});


test('personal section modes hide exact report tabs and block navigation to hidden modules',async t=>{
 const {view}=await fixture(t,async()=>({ok:true,status:200,json:async()=>({items:[]})}));
 view.state.user={...user,sections:{home:'view',accounts:'hidden',ledger:'view',pnl:'hidden',balance:'view',reports:'hidden'}};
 await settle();
 assert.deepEqual(view.state.nav.map(n=>n[0]).sort(),['home','ledger','balance','profile'].sort());
 assert.equal(view.state.canNavigate('pnl'),false);assert.equal(view.state.canNavigate('accounts'),false);
});
test('read-only section mode hides write controls without removing read access',async t=>{
 const {view}=await fixture(t,async()=>({ok:true,status:200,json:async()=>({items:[]})}));
 view.state.user={...user,permissions:['view','ledger','write','import'],sections:{home:'view',ledger:'view'}};
 view.state.page='ledger';await settle();
 assert.equal(view.state.has('ledger'),true);assert.equal(view.state.has('write'),false);assert.equal(view.state.has('import'),false);
 assert.equal(all(view.container,n=>n.tag==='button'&&text(n).includes('Новая операция')).length,0);
});

test('administration catalogs drill down and obey the existing catalog section visibility',async t=>{
 const {view}=await fixture(t,async()=>({ok:true,status:200,json:async()=>({})}));
 view.state.user={...user,holding_role:'admin',permissions:['view','catalog','users','audit','counterparties']};await settle();
 const admin=find(view.container,n=>n.tag==='button'&&text(n).trim()==='Администрирование');assert.ok(admin);
 assert.ok(find(view.container,n=>n.tag==='button'&&text(n).trim()==='Отделы'));
 const catalogs=find(view.container,n=>n.tag==='button'&&text(n).trim()==='Справочники');catalogs.props.onClick();await settle();
 assert.equal(find(view.container,n=>n.tag==='button'&&text(n).trim()==='Отделы'),undefined);
 catalogs.props.onClick();await settle();
 const sectionNames=view.state.allNav.map(n=>n[0]);view.state.user={...user,sections:Object.fromEntries(sectionNames.map(k=>[k,k==='categories'?'hidden':'view']))};await settle();
 assert.equal(find(view.container,n=>n.tag==='button'&&text(n).trim()==='Отделы'),undefined);
 assert.ok(find(view.container,n=>n.tag==='button'&&text(n).trim()==='Пользователи'));
});

test('holding admin confirms a versioned operation deletion in the selected company',async t=>{
 const calls=[];const admin={...user,holding_role:'admin',role:'admin',permissions:['view','ledger','write','download']};
 const {view}=await fixture(t,async(url,options)=>{calls.push({url,options});return {ok:true,status:200,json:async()=>url==='/api/bootstrap'?bootstrapBody(admin):url.startsWith('/api/ledger?')?{items:[],total:0}:{id:8,deleted:true}};});
 view.state.user=admin;view.state.page='ledger';view.state.rows=[{id:8,date:'2026-10-10',kind:'in',amount:'100',currency:'UZS',account:'Synthetic',reference:'SYNTHETIC-DELETE',edit_token:'a'.repeat(64),can_delete:true}];await settle();
 const button=find(view.container,n=>n.tag==='button'&&text(n).trim()==='Удалить');assert.ok(button);button.props.onClick();await settle();
 assert.equal(calls.length,0,'opening confirmation never deletes');assert.equal(view.state.modal.saveLabel,'Удалить');assert.match(text(view.container),/SYNTHETIC-DELETE/);
 view.state.form.reason='Synthetic mistaken movement';await view.state.save();await settle();
 const call=calls.find(c=>c.options.method==='DELETE');assert.ok(call);assert.equal(call.url,'/api/ledger/8');assert.equal(call.options.headers['X-Company-ID'],'2');assert.equal(call.options.headers['Content-Type'],'application/json');assert.equal(JSON.parse(call.options.body).expected,'a'.repeat(64));assert.equal(view.state.modal,null);
 view.state.showDeletedOperations=true;await view.state.load();assert.ok(calls.some(c=>c.url.includes('include_deleted=true')));
});
test('company director has no operation delete button and a failed delete keeps its reason',async t=>{
 const {view}=await fixture(t,async()=>({ok:false,status:409,json:async()=>({detail:'Synthetic changed operation'})}));
 const row={id:9,date:'2026-10-10',kind:'out',amount:'1',currency:'UZS',account:'Synthetic',reference:'SYNTHETIC-CONFLICT',edit_token:'b'.repeat(64),can_delete:true};
 view.state.user={...user,permissions:['view','ledger','write']};view.state.page='ledger';view.state.rows=[row];await settle();
 assert.equal(find(view.container,n=>n.tag==='button'&&text(n).trim()==='Удалить'),undefined);
 view.state.user={...user,holding_role:'admin',permissions:['view','ledger','write']};view.state.deleteOperation(row);view.state.form.reason='Keep reason on conflict';await view.state.save();await settle();assert.equal(view.state.form.reason,'Keep reason on conflict');assert.match(text(view.container),/Synthetic changed operation/);
});


const bankStatementsComponent=await compileComponent(new URL('../src/BankStatements.vue',import.meta.url));
const bankDailyComponent=await compileComponent(new URL('../src/overview/BankStatementDaily.vue',import.meta.url));
const bankApp=await compileComponent(new URL('../src/App.vue',import.meta.url),{'./OperationProgress.vue':progressComponent,'./PeriodFilter.vue':periodComponent,'./BankStatements.vue':bankStatementsComponent,'./overview/BankStatementDaily.vue':bankDailyComponent});

function mountedComponent(vnode,type){
 if(!vnode)return null;
 if(vnode.component){if(vnode.component.type===type)return vnode.component;return mountedComponent(vnode.component.subTree,type);}
 if(Array.isArray(vnode.children))for(const child of vnode.children){const match=mountedComponent(child,type);if(match)return match;}
 return null;
}

test('bank preview shortcut opens Cash Flow from operations without posting the statement',async t=>{
 const calls=[];const account={id:21,name:'Synthetic bank',kind:'bank',currency:'UZS'};
 const statement={id:7,status:'preview',preview_token:'synthetic-token',full_reconciliation:true,account:{id:21,name:'Synthetic bank',currency:'UZS'},statement:{account_number:'SYNTHETIC',ready:true,balance_matches:true,declared_totals_match:true,credit_total:'10.00',debit_total:'20.00',opening_balance:'100.00',closing_balance:'90.00',period_from:'2026-10-10',period_to:'2026-10-10'},rows:[{id:1,direction:'out',amount:'20.00',status:'outgoing_candidate',date:'2026-10-10'}]};
 const {view}=await fixture(t,async(url,options)=>{calls.push({url,method:options.method||'GET'});return {ok:true,status:200,json:async()=>url.includes('/preview?')?statement:url.startsWith('/api/report?')?{currency:'UZS',year:2026,company_id:2,scenario:'A',as_of:'2026-10-10'}:{items:[],total:0}};},bankApp);
 const accountant={...user,role:'accountant',company_role:'accountant',permissions:['ledger','pay_bank']};
 Object.assign(view.state,{user:accountant,page:'ledger',boot:{...bootstrapBody(accountant),accounts:[account]},cashFlowBasis:'osv'});await settle();
 const child=mountedComponent(view.app._instance.subTree,bankStatementsComponent);assert.ok(child);child.setupState.accountId='21';child.setupState.currency='UZS';await settle();
 await child.setupState.upload({target:{files:[{name:'synthetic.xls',size:100}],value:'file'}});await settle();
 assert.ok(calls.some(c=>c.url.includes('/api/bank-statements/preview?')));assert.equal(find(view.container,n=>n.tag==='button'&&text(n).includes('Открыть Cash Flow')),undefined);
 view.state.user={...accountant,permissions:['ledger','pay_bank','export']};await settle();
 const shortcut=find(view.container,n=>n.tag==='button'&&text(n).includes('Открыть Cash Flow'));assert.ok(shortcut);shortcut.props.onClick();await settle();
 assert.equal(view.state.page,'report');assert.equal(view.state.cashFlowBasis,'operations');assert.equal(location.hash,'#report');
 assert.ok(calls.some(c=>c.url.startsWith('/api/report?')));assert.equal(calls.some(c=>/bank-statements\/\d+\/commit/.test(c.url)),false);
});

test('bank reconciliation in Operations respects accountant rights, section read-only, and current company',async t=>{
 const calls=[];const accountant={...user,role:'accountant',company_role:'accountant',permissions:['ledger','pay_bank'],sections:{ledger:'edit',home:'hidden'}};
 const {view}=await fixture(t,async(url,options)=>{calls.push({url,company:options.headers['X-Company-ID']});return {ok:true,status:200,json:async()=>url.includes('/reviews')?{items:[{id:123,date:'2026-10-30',direction:'out',amount:'10',currency:'UZS',status:'review'}]}:url.startsWith('/api/ledger?')?{items:[],total:0}:{}}},bankApp);
 Object.assign(view.state,{user:accountant,boot:{...bootstrapBody(accountant),accounts:[{id:21,name:'Synthetic account ZUMA',kind:'bank',currency:'UZS'}]},page:'ledger'});await settle();
 assert.match(text(view.container),/Операции для проверки бухгалтера/);assert.ok(find(view.container,n=>n.tag==='input'&&n.props?.type==='file'));
 assert.match(text(view.container),/Повторная проверка загруженной выписки/);
 assert.match(text(view.container),/Выбор статьи комиссии не меняет общую настройку компании/);
 assert.doesNotMatch(text(view.container),/Для проведения поступлений и комиссий требуется право записи операций/);
 assert.equal(find(view.container,n=>n.tag==='button'&&text(n).trim()==='Новая операция'),undefined);
 assert.ok(calls.some(c=>c.url==='/api/bank-statements/reprocess/batches'&&c.company==='2'));
 assert.ok(calls.some(c=>c.url==='/api/bank-statements/reviews'&&c.company==='2'));
 view.state.user={...accountant,sections:{ledger:'view',home:'hidden'}};await settle();
 assert.equal(find(view.container,n=>n.tag==='input'&&n.props?.type==='file'),undefined);
 assert.doesNotMatch(text(view.container),/Повторная проверка загруженной выписки/);
 assert.doesNotMatch(text(view.container),/Найти заявку для сопоставления/);
 assert.match(text(view.container),/Операции для проверки бухгалтера/);
 view.state.companyId=3;view.state.boot={...bootstrapBody(accountant),accounts:[{id:22,name:'Synthetic account UZGERMED',kind:'bank',currency:'UZS'}]};view.state.user=accountant;await settle();
 assert.ok(calls.some(c=>c.url==='/api/bank-statements/reviews'&&c.company==='3'));
 assert.doesNotMatch(text(view.container),/Synthetic account ZUMA/);assert.match(text(view.container),/Synthetic account UZGERMED/);
});

test('sales overview does not fetch or show bank statement totals',async t=>{
 const calls=[];const sales={...user,role:'sales',company_role:'sales',permissions:['overview','calendar','receipts'],sections:null};
 const {view}=await fixture(t,async(url,options)=>{calls.push(url);return {ok:true,status:200,json:async()=>({})}},bankApp);
 calls.length=0;Object.assign(view.state,{user:sales,boot:{...bootstrapBody(sales),accounts:[{id:1,currency:'UZS',kind:'bank',unreconciled_statements:1}]},page:'home'});await settle();
 assert.equal(calls.filter(url=>url.startsWith('/api/bank-statements')).length,0);assert.doesNotMatch(text(view.container),/Банковская выписка за день|Остаток сайта не сверен/);
});

test('accepted bank statement refreshes ledger independently after bootstrap fails and removes stale balances',async t=>{
 const calls=[],accountant={...user,role:'accountant',permissions:['view','ledger','pay_bank']};
 const {view}=await fixture(t,async url=>{calls.push(url);if(url==='/api/bootstrap')return {ok:false,status:503,json:async()=>({detail:'offline'})};if(url.startsWith('/api/ledger?'))return {ok:true,status:200,json:async()=>({items:[{id:999,kind:'in',date:'2026-10-10',currency:'UZS',amount:'42',reference:'NEW'}],total:1})};return {ok:true,status:200,json:async()=>({items:[]})}},bankApp);
 Object.assign(view.state,{user:accountant,boot:{...bootstrapBody(accountant),accounts:[{id:21,name:'Old balance',kind:'bank',currency:'UZS'}]},page:'ledger',rows:[{id:2,reference:'STALE'}]});await settle();await view.state.bankStatementsChanged();await settle();
 assert.ok(calls.includes('/api/bootstrap'));assert.ok(calls.some(url=>url.startsWith('/api/ledger?')));assert.equal(view.state.boot.accounts.length,0);assert.equal(view.state.rows[0].reference,'NEW');assert.match(text(view.container),/Изменения выписки сохранены/);assert.match(text(view.container),/повторно подтверждать выписку не нужно/);
});

test('a failed statement ledger refresh clears stale operations while retaining refreshed accounts',async t=>{
 const accountant={...user,role:'accountant',permissions:['view','ledger','pay_bank']};
 const {view}=await fixture(t,async url=>url==='/api/bootstrap'?{ok:true,status:200,json:async()=>({...bootstrapBody(accountant),accounts:[{id:21,name:'Fresh bank',kind:'bank',currency:'UZS'}]})}:url.startsWith('/api/ledger?')?{ok:false,status:503,json:async()=>({detail:'offline'})}:{ok:true,status:200,json:async()=>({items:[]})},bankApp);
 Object.assign(view.state,{user:accountant,page:'ledger',rows:[{id:2,reference:'STALE'}]});await settle();await view.state.bankStatementsChanged();await settle();assert.equal(view.state.rows.length,0);assert.equal(view.state.boot.accounts[0].name,'Fresh bank');assert.match(text(view.container),/Изменения выписки сохранены/);
});

test('late statement refresh does not overwrite a different company',async t=>{
 const waiting=deferred(),accountant={...user,role:'accountant',permissions:['view','ledger','pay_bank']},calls=[];
 const {view}=await fixture(t,async url=>{calls.push(url);if(url==='/api/bootstrap')return waiting.promise;return {ok:true,status:200,json:async()=>({items:[],total:0})}},bankApp);
 Object.assign(view.state,{user:accountant,page:'ledger',boot:{...bootstrapBody(accountant),accounts:[{id:21,name:'Old company bank',kind:'bank',currency:'UZS'}]}});await settle();const pending=view.state.bankStatementsChanged();await settle();view.state.companyId=3;view.state.boot={...bootstrapBody(accountant),accounts:[{id:22,name:'New company bank',kind:'bank',currency:'UZS'}]};view.state.rows=[{id:12,reference:'NEW COMPANY'}];
 waiting.resolve({ok:true,status:200,json:async()=>({...bootstrapBody(accountant),accounts:[{id:21,name:'Wrong company bank',kind:'bank',currency:'UZS'}]})});await pending;await settle();
 assert.equal(view.state.boot.accounts[0].name,'New company bank');assert.equal(view.state.rows[0].reference,'NEW COMPANY');assert.doesNotMatch(text(view.container),/Изменения выписки сохранены/);
});


test('hidden Operations rejects navigation and never loads statement reviews',async t=>{
 const calls=[];const restricted={...user,role:'accountant',company_role:'accountant',permissions:['view','ledger','pay_bank'],sections:{ledger:'hidden',home:'view'}};
 const {view}=await fixture(t,async(url)=>{calls.push(url);return {ok:true,status:200,json:async()=>url.includes('/daily?')?{date:'2026-10-10',currency:'UZS',accounts:[],available:false}:{items:[],total:0}}},bankApp);
 Object.assign(view.state,{user:restricted,boot:bootstrapBody(restricted),page:'home'});await settle();
 await view.state.go('ledger');await settle();assert.equal(view.state.page,'home');assert.ok(!calls.some(url=>url==='/api/bank-statements/reviews'));assert.doesNotMatch(text(view.container),/Операции для проверки бухгалтера/);
});

test('statement deletion controls are shown only to holding administrators',async t=>{
 const calls=[];const {view}=await fixture(t,async url=>{calls.push(url);return {ok:true,status:200,json:async()=>({items:[]})}},bankApp);
 const accountant={...user,role:'accountant',company_role:'accountant',permissions:['view','ledger','pay_bank']};
 Object.assign(view.state,{user:accountant,page:'ledger'});await settle();assert.doesNotMatch(text(view.container),/Удаление выписки администратором/);assert.equal(calls.some(url=>url.includes('/admin/batches')),false);
 view.state.user={...accountant,holding_role:'admin',role:'admin',permissions:['view','ledger','write','pay_bank']};await settle();assert.match(text(view.container),/Удаление выписки администратором/);assert.ok(calls.some(url=>url.includes('/admin/batches')));
});

test('bank exception resolution is visible only for a holding admin with editable ledger and both selected-company payment and write rights',async t=>{
 const resolution=await compileComponent(new URL('../src/BankStatementResolution.vue',import.meta.url));
 const statements=await compileComponent(new URL('../src/BankStatements.vue',import.meta.url),{'./BankStatementResolution.vue':resolution});
 const app=await compileComponent(new URL('../src/App.vue',import.meta.url),{'./BankStatements.vue':statements});
 const calls=[],row={id:31,batch_id:7,account_id:21,date:'2026-10-10',direction:'out',amount:'50.00',currency:'UZS',status:'review',document_number:'SYNTHETIC-31'};
 const {view}=await fixture(t,async(url,options)=>{calls.push([url,options]);return {ok:true,status:200,json:async()=>url==='/api/bank-statements/reviews'?{items:[row]}:{items:[]}};},app);
 const full={...user,role:'accountant',company_role:'accountant',permissions:['view','ledger','pay_bank','write']};
 Object.assign(view.state,{page:'ledger',user:full,boot:bootstrapBody(full)});await settle();
 const action=()=>find(view.container,node=>node.tag==='button'&&text(node)==='Разобрать спорную строку');
 assert.equal(action(),undefined);assert.ok(find(view.container,node=>node.tag==='button'&&text(node)==='Найти заявку для сопоставления'));
 view.state.user={...full,holding_role:'admin',permissions:['view','ledger','pay_bank']};await settle();assert.equal(action(),undefined);
 view.state.user={...full,holding_role:'admin',sections:{ledger:'view'}};await settle();assert.equal(action(),undefined);
 view.state.user={...full,holding_role:'founder'};await settle();assert.equal(action(),undefined);
 view.state.user={...full,holding_role:'admin',sections:{ledger:'edit'}};await settle();assert.ok(action());
 view.state.user={...full,holding_role:'admin',permissions:['view','ledger','write']};await settle();assert.equal(action(),undefined);
 assert.ok(calls.every(([,options])=>!options.method||options.method==='GET'));assert.equal(calls.some(([url])=>url.includes('/historical-')||url.includes('/distinct-income/')),false);
});

test('holding admin without payment rights can link existing bank facts only with editable ledger',async t=>{
 const resolution=await compileComponent(new URL('../src/BankStatementResolution.vue',import.meta.url));
 const statements=await compileComponent(new URL('../src/BankStatements.vue',import.meta.url),{'./BankStatementResolution.vue':resolution});
 const app=await compileComponent(new URL('../src/App.vue',import.meta.url),{'./BankStatements.vue':statements});
 const calls=[],rows=[{id:31,status:'ambiguous_duplicate',date:'2026-10-10',direction:'out',amount:'50.00',currency:'UZS'},{id:32,status:'review',date:'2026-10-10',direction:'out',amount:'10.00',currency:'UZS'}];
 const {view}=await fixture(t,async(url,options)=>{calls.push([url,options]);return {ok:true,status:200,json:async()=>url==='/api/bank-statements/reviews'?{items:rows}:{items:[],total:0}};},app);
 const admin={...user,role:'admin',holding_role:'admin',company_role:null,permissions:['view','ledger','write'],sections:{ledger:'edit'}};
 Object.assign(view.state,{page:'ledger',user:admin,boot:bootstrapBody(admin)});await settle();
 const duplicate=()=>find(view.container,n=>n.tag==='button'&&text(n)==='Найти ранее проведённую операцию'),transfer=()=>find(view.container,n=>n.tag==='button'&&text(n)==='Найти оформленный перевод');
 assert.ok(duplicate());assert.ok(transfer());assert.doesNotMatch(text(view.container),/Найти заявку для сопоставления|Разобрать спорную строку/);
 const child=mountedComponent(view.app._instance.subTree,statements);assert.ok(child);assert.equal(child.props.canReview,false);assert.equal(child.props.canResolveExceptions,false);assert.equal(child.props.canLinkExistingBankFacts,true);
 view.state.user={...admin,sections:{ledger:'view'}};await settle();assert.equal(duplicate(),undefined);assert.equal(transfer(),undefined);
 view.state.user={...admin,permissions:['view','ledger']};await settle();assert.equal(duplicate(),undefined);assert.equal(transfer(),undefined);
 view.state.user={...admin,role:'director',holding_role:null,company_role:'director'};await settle();assert.equal(duplicate(),undefined);assert.equal(transfer(),undefined);
 view.state.user={...admin,role:'founder',holding_role:'founder'};await settle();assert.equal(duplicate(),undefined);assert.equal(transfer(),undefined);
 view.state.user=admin;await settle();assert.ok(duplicate());assert.ok(transfer());assert.doesNotMatch(text(view.container),/Найти заявку для сопоставления|Разобрать спорную строку/);assert.ok(calls.every(([,options])=>!options.method||options.method==='GET'));
});

test('statement link opens actual Cash Flow in the statement currency and month range',async t=>{
 const calls=[];const director={...user,role:'director',permissions:['view','ledger','export']};
 const {view}=await fixture(t,async(url,options)=>{calls.push({url,company:options.headers['X-Company-ID']});return {ok:true,status:200,json:async()=>url.startsWith('/api/report?')?{currency:'USD',year:2026,company_id:2,scenario:'A',as_of:'2026-06-20'}:{items:[],total:0}}},bankApp);
 Object.assign(view.state,{user:director,page:'ledger',boot:bootstrapBody(director),cashFlowBasis:'osv',currency:'UZS'});await settle();
 await view.state.openStatementCashFlow({currency:'USD',period_from:'2026-05-10',period_to:'2026-06-20'});await settle();
 assert.equal(view.state.page,'report');assert.equal(view.state.cashFlowBasis,'operations');assert.equal(view.state.currency,'USD');assert.equal(view.state.year,2026);assert.equal(view.state.reportAsOf,'2026-06-20');assert.equal(view.state.reportSelection.start,5);assert.equal(view.state.reportSelection.end,6);assert.equal(view.state.reportSelection.period,'custom');assert.ok(calls.some(c=>c.url.includes('currency=USD')&&c.url.includes('as_of=2026-06-20')&&c.company==='2'));
});

test('a saved statement history opens Cash Flow with that statement period and currency',async t=>{
 const history=await compileComponent(new URL('../src/BankStatementHistory.vue',import.meta.url));
 const bank=await compileComponent(new URL('../src/BankStatements.vue',import.meta.url),{'./BankStatementHistory.vue':history});
 const app=await compileComponent(new URL('../src/App.vue',import.meta.url),{'./OperationProgress.vue':progressComponent,'./PeriodFilter.vue':periodComponent,'./BankStatements.vue':bank});
 const account={id:21,name:'Synthetic bank',currency:'USD',kind:'bank'},item={id:9,filename:'synthetic.xls',account,period_from:'2026-05-10',period_to:'2026-06-20',credit_total:'100.00',debit_total:'25.00'};
 const detail={...item,status:'committed',statement:{period_from:item.period_from,period_to:item.period_to},rows:[]},calls=[];
 const director={...user,role:'director',permissions:['view','ledger','export','import']};
 const {view}=await fixture(t,async(url,opts)=>{calls.push({url,method:opts.method||'GET'});return {ok:true,status:200,json:async()=>url.includes('/history?')?{page:1,has_more:false,items:[item]}:url.endsWith('/history/9')?detail:url.startsWith('/api/report?')?{currency:'USD',year:2026,company_id:2,scenario:'A',as_of:'2026-06-20'}:{items:[],total:0}}},app);
 Object.assign(view.state,{user:director,boot:{...bootstrapBody(director),accounts:[account]},page:'ledger',currency:'UZS'});await settle();
 const child=mountedComponent(view.app._instance.subTree,history);assert.ok(child);await child.setupState.toggle();await child.setupState.openDetail(item);await settle();
 child.setupState.openCashFlow();await settle();assert.equal(view.state.page,'report');assert.equal(view.state.currency,'USD');assert.equal(view.state.reportAsOf,'2026-06-20');assert.deepEqual([view.state.reportSelection.start,view.state.reportSelection.end],[5,6]);assert.equal(calls.some(c=>c.method!=='GET'),false);
});

test('accountant with read-only ledger can view history but cannot upload or post a statement',async t=>{
 const history=await compileComponent(new URL('../src/BankStatementHistory.vue',import.meta.url));
 const bank=await compileComponent(new URL('../src/BankStatements.vue',import.meta.url),{'./BankStatementHistory.vue':history});
 const app=await compileComponent(new URL('../src/App.vue',import.meta.url),{'./OperationProgress.vue':progressComponent,'./PeriodFilter.vue':periodComponent,'./BankStatements.vue':bank});
 const accountant={...user,role:'accountant',company_role:'accountant',permissions:['ledger','pay_bank'],sections:{ledger:'view'}};const calls=[];
 const {view}=await fixture(t,async(url,opts)=>{calls.push({url,method:opts.method||'GET'});return {ok:true,status:200,json:async()=>url.includes('/history?')?{page:1,has_more:false,items:[]}:{items:[],total:0}}},app);
 Object.assign(view.state,{user:accountant,page:'ledger',boot:bootstrapBody(accountant)});await settle();const child=mountedComponent(view.app._instance.subTree,history);assert.ok(child);assert.match(text(view.container),/История банковских выписок/);assert.equal(find(view.container,n=>n.tag==='input'&&n.props?.type==='file'),undefined);
 await child.setupState.toggle();await settle();assert.ok(calls.some(c=>c.url.includes('/history?')));assert.equal(calls.some(c=>c.method!=='GET'),false);
});

test('accepted saved statement clears cached Cash Flow before returning to the same report, including delayed and failed reads',async t=>{
 const reportComponent=await compileComponent(new URL('../src/CashFlowReport.vue',import.meta.url));
 const app=await compileComponent(new URL('../src/App.vue',import.meta.url),{'./OperationProgress.vue':progressComponent,'./PeriodFilter.vue':periodComponent,'./BankStatements.vue':bankStatementsComponent,'./CashFlowReport.vue':reportComponent});
 const accountant={...user,role:'accountant',company_role:'accountant',permissions:['view','ledger','pay_bank','export']};
 const account={id:21,name:'Synthetic bank',kind:'bank',currency:'UZS'};
 const preview={id:7,status:'preview',preview_token:'saved-synthetic',full_reconciliation:true,account,
  statement:{account_number:'SYNTHETIC',ready:true,balance_matches:true,declared_totals_match:true,credit_total:'30.00',debit_total:'0.00',opening_balance:'100.00',closing_balance:'130.00',period_from:'2026-10-10',period_to:'2026-10-10'},rows:[]};
 const report=(label,amount)=>{const zero=()=>Array(12).fill('0.00'),empty=()=>Array(12).fill(null),values=zero();values[9]=amount;return {
  company_id:2,currency:'UZS',year:2026,scenario:'A',as_of:'2026-10-10',notes:{},
  rows:[{key:'9:in',category_id:9,category:label,kind:'in',activity:'operating',values,plan:empty()}],groups:[{activity:'operating',name:'Операционная деятельность',values,plan:empty()}],
  totals:values,plan_totals:empty(),opening:zero(),closing:values,opening_adjustments:zero(),plan_opening:empty(),plan_closing:empty(),plan_versions:Array(12).fill(1),plan_opening_input:empty()};};
 for(const fail of [false,true]){
  const waiting=deferred(),calls=[];
  const {view,cleanup}=await fixture(t,async(url,options)=>{calls.push({url,method:options.method||'GET'});
   if(url==='/api/bootstrap')return {ok:true,status:200,json:async()=>({...bootstrapBody(accountant),accounts:[account]})};
   if(url==='/api/bank-statements/settings')return {ok:true,status:200,json:async()=>({fee_category_id:null})};
   if(url.endsWith('/resume'))return {ok:true,status:200,json:async()=>preview};
   if(url.endsWith('/commit'))return {ok:true,status:200,json:async()=>({auto_paid:0,linked_paid:0,review:0,auto_income:1,full_reconciliation:true})};
   if(url.startsWith('/api/report?'))return waiting.promise;
   return {ok:true,status:200,json:async()=>({items:[],total:0})};
  },app);
  Object.assign(view.state,{user:accountant,boot:{...bootstrapBody(accountant),accounts:[account]},page:'report',currency:'UZS',year:2026,reportAsOf:'2026-10-10',cashFlowBasis:'operations',report:report('STALE-SYNTHETIC-FACT','10.00')});await settle();
  assert.match(text(view.container),/STALE-SYNTHETIC-FACT/);
  await view.state.go('ledger');await settle();
  const child=mountedComponent(view.app._instance.subTree,bankStatementsComponent);assert.ok(child);
  await child.setupState.resumeSaved({id:7,status:'preview',account});child.setupState.confirmAccount=true;child.setupState.confirmCurrency=true;await settle();await child.setupState.commit();await settle();
  const opening=view.state.go('report');await settle();
  assert.equal(mountedComponent(view.app._instance.subTree,reportComponent),null);assert.doesNotMatch(text(view.container),/STALE-SYNTHETIC-FACT/);
  waiting.resolve(fail?{ok:false,status:503,json:async()=>({detail:'Synthetic report unavailable'})}:{ok:true,status:200,json:async()=>report('FRESH-SYNTHETIC-FACT','40.00')});
  await opening;await settle();
  assert.doesNotMatch(text(view.container),/STALE-SYNTHETIC-FACT/);assert.equal(calls.filter(call=>call.url.endsWith('/commit')).length,1);
  if(fail){assert.equal(mountedComponent(view.app._instance.subTree,reportComponent),null);assert.match(text(view.container),/Synthetic report unavailable/);}
  else assert.match(text(view.container),/FRESH-SYNTHETIC-FACT/);
  cleanup();
 }
});

test('operations Cash Flow warns about an unreconciled bank in the selected currency without applying the warning to OSV',async t=>{
 const {view}=await fixture(t,async()=>({ok:true,status:200,json:async()=>({})}));
 Object.assign(view.state,{page:'report',cashFlowBasis:'operations',currency:'UZS',boot:{...bootstrapBody(),accounts:[{id:1,kind:'bank',currency:'UZS',unreconciled_statements:1},{id:2,kind:'cash',currency:'USD',unreconciled_statements:1}]}});await settle();
 assert.match(text(view.container),/Факт Cash Flow включает только проведённые операции/);assert.match(text(view.container),/остаток сайта ещё не подтверждает полную сверку с банком/);
 view.state.currency='USD';await settle();assert.doesNotMatch(text(view.container),/Часть банковских строк остаётся на проверке/);
 view.state.currency='UZS';view.state.cashFlowBasis='osv';await settle();assert.doesNotMatch(text(view.container),/Часть банковских строк остаётся на проверке/);
 view.state.cashFlowBasis='operations';view.state.boot.accounts[0].unreconciled_statements=0;await settle();assert.doesNotMatch(text(view.container),/Часть банковских строк остаётся на проверке/);
});
