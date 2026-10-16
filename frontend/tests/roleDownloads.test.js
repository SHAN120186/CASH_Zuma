import {test} from 'node:test';
import assert from 'node:assert/strict';
import {reactive} from 'vue';
import {compileComponent,mount,settle,find,text,all} from './helpers/mountVue.js';

const archiveComponent=await compileComponent(new URL('../src/ReportArchive.vue',import.meta.url));
const businessComponent=await compileComponent(new URL('../src/BusinessProjects.vue',import.meta.url));
const requestCard=await compileComponent(new URL('../src/requests/RequestCard.vue',import.meta.url));
const requestEditor=await compileComponent(new URL('../src/requests/RequestEditor.vue',import.meta.url));
const salesOverview=await compileComponent(new URL('../src/overview/SalesOverview.vue',import.meta.url));
const company={id:2,code:'ZUMA',name:'Synthetic company'};
const archive={id:11,version:1,company_id:2,status:'ready',formats:['pdf','xlsx'],title:'Synthetic ready business plan',period_start:'2026-10-01',period_end:'2029-09-30'};
const generation={id:1,revision:2,business_archive:archive,teo_archive:{...archive,id:12,formats:['pdf']}};
const project={id:7,company_id:2,title:'Synthetic business plan',currency:'USD',status:'ready',revision:2,can_edit:true,files:[{id:9,filename:'Synthetic source.xlsx',relative_path:'Synthetic source.xlsx',download_url:'/api/business-projects/7/sources/9/download?company_id=2'}],inputs:{title:'Synthetic business plan',start:'2026-10-01',months:36,currency:'USD'},generations:[generation],latest_report:{...generation,current:true},current_generation_id:1};
const fileLinks=view=>all(view.container,node=>node.tag==='a'&&/^\/api\//.test(node.props.href||''));

test('archive records stay visible while download rights govern both PDF and Excel links',async t=>{
 for(const canDownload of [false,true]){
  const view=mount(archiveComponent,{company,canDownload,api:async()=>({items:[archive],can_upload:false})});t.after(view.unmount);await settle();
  assert.match(text(view.container),/Synthetic ready business plan/);assert.equal(fileLinks(view).length,canDownload?2:0);
 }
});

test('business folders, input templates and sources expose no file links without download rights',async t=>{
 for(const canDownload of [false,true]){
  const view=mount(businessComponent,{company,canDownload,api:async url=>url.includes('/7?')?project:{items:[project],can_upload:true}});t.after(view.unmount);await settle();
  assert.match(text(view.container),/Synthetic business plan/);
  assert.equal(fileLinks(view).length,canDownload?4:0,'template and ready business PDF/Excel/TEO');
  await view.state.openProject(project);await settle();
  assert.match(text(view.container),/Synthetic source.xlsx/);
  assert.equal(fileLinks(view).some(link=>String(link.props.href).includes('/sources/')),canDownload);
 }
});

test('visible request documents, previous versions and saved editor files download without general download rights',async t=>{
 const previousDocument=globalThis.document,views=[];
 globalThis.document={activeElement:null,addEventListener(){},removeEventListener(){},querySelector(){return null;}};
 t.after(()=>{for(const view of views)view.unmount();globalThis.document=previousDocument;});
 const document={id:9,kind:'contract',filename:'Synthetic.pdf',version:2,current:true,url:'/api/request-documents/9',created_at:'2026-10-11',created_by:'Synthetic'};
 const request={id:1,version:1,status:'draft',number:'Synthetic request',date:'2026-10-20',priority:'normal',account_kind:'bank',channel:'bank',account_id:7,category_id:8,currency:'UZS',amount:'100',counterparty:'Synthetic buyer',purpose:'Synthetic purpose',actions:['documents'],documents_list:[document,{...document,id:10,current:false,version:1,url:'/api/request-documents/10'}],history:[]};
 const boot={accounts:[{id:7,kind:'bank',currency:'UZS',name:'Synthetic bank'}],categories:[{id:8,type:'outcome',name:'Synthetic cost'}],due_minimums:{normal:'2026-10-20',high:'2026-10-15',urgent:'2026-10-12'}};
 const card=mount(requestCard,{id:1,companyUrl:url=>url+'?company_id=2',money:v=>v,api:async()=>request});views.push(card);await settle();
 assert.match(text(card.container),/Synthetic.pdf/);
 assert.deepEqual(fileLinks(card).map(link=>link.props.href),['/api/request-documents/9?company_id=2']);
 const history=find(card.container,node=>node.tag==='button'&&text(node).includes('Прежние версии:'));history.props.onClick();await settle();
 assert.deepEqual(fileLinks(card).map(link=>link.props.href),['/api/request-documents/9?company_id=2','/api/request-documents/10?company_id=2']);
 const editor=mount(requestEditor,{boot,company,request,currency:'UZS',money:v=>v,companyUrl:url=>url+'?company_id=2',api:async()=>({due_minimums:boot.due_minimums})});views.push(editor);await settle();
 assert.match(text(editor.container),/Synthetic.pdf/);
 assert.deepEqual(fileLinks(editor).map(link=>link.props.href),['/api/request-documents/9?company_id=2']);
});

test('sales overview loads only the permitted dashboard and links to calendar',async t=>{
 const calls=[],routes=[];
 const view=mount(salesOverview,{companyId:2,currency:'UZS',days:30,onGo:name=>routes.push(name),api:async url=>{calls.push(url);return{fact_in:'1200',fact_out:'100',incoming7:'500',forecast:[],reserve:'0'};}});t.after(view.unmount);await settle();
 assert.deepEqual(calls,['/api/dashboard?currency=UZS&days=30']);
 assert.match(text(view.container),/План поступлений/);assert.equal(fileLinks(view).length,0);
 find(view.container,node=>node.tag==='button'&&text(node)==='Открыть календарь').props.onClick();
 assert.deepEqual(routes,['calendar']);
});

test('sales overview cannot show previous currency amounts when a new currency request fails',async t=>{
 const props=reactive({companyId:2,currency:'USD',days:30,api:async url=>{
  if(url.includes('currency=EUR'))throw Error('Synthetic denied request');
  return{fact_in:'123456.78',fact_out:'900',incoming7:'500',forecast:[],reserve:'0'};
 }});
 const view=mount(salesOverview,props);t.after(view.unmount);await settle();
 assert.equal(view.state.dashboard.fact_in,'123456.78');
 view.app._instance.props.currency='EUR';await settle();
 assert.equal(view.state.dashboard,null);assert.equal(view.state.error,'Synthetic denied request');
 assert.equal(all(view.container,node=>node.props?.title&&String(node.props.title).includes('123')).length,0);
});
