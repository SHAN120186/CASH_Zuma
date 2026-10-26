import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,all,text} from './helpers/mountVue.js';
import {fileBase64,sourceYears,periodMonths,sumAmounts,osvExportUrl} from '../src/cashflow/osvView.js';
import {setLang,lang} from '../src/i18n/index.js';
const component=await compileComponent(new URL('../src/ImportedCashFlow.vue',import.meta.url));
const source={id:7,filename:'Synthetic 5110.xlsx',template_filename:'Synthetic sample.xlsx',company_id:2,currency:'UZS',scale:1,first_date:'2026-01-05',last_date:'2026-02-13',year_from:2026,year_to:2026,record_count:4,version:1};
const array=(...values)=>[...values,...Array(12-values.length).fill(null)];
function reportFor(value=source){return {source:value,year:2026,scenario:'B',notes_version:3,
 months:Array.from({length:12},(_,index)=>({month:index+1,key:`2026-${String(index+1).padStart(2,'0')}`,available:index<2,partial:index===1,through:index===0?'2026-01-31':index===1?'2026-02-13':null,opening:index===0?'100.00':index===1?'80.00':null,closing:index===0?'80.00':index===1?'70.00':null,external_inflow:index===0?'10.00':index===1?'5.00':null,external_outflow:index===0?'30.00':index===1?'15.00':null})),
 rows:[{code:'5',name:'Synthetic expense',group:'Synthetic group',kind:'expense',plan:array('40.00','20.00'),fact:array('30.00','15.00'),plan_status:array('numeric','numeric'),notes:Array(12).fill('')},{code:'1',name:'Synthetic receipt',group:'Synthetic income',kind:'receipt',plan:array('12.00','4.00'),fact:array('10.00','5.00'),plan_status:array('numeric','numeric'),notes:Array(12).fill('')}],issues:[]};}
function view(t,{api,canEdit=true,canDownload=true,...rest}={}){
 const calls=[];
 const fallback=async(url,options={})=>{calls.push([url,options]);if(url==='/api/cashflow-osv/sources')return {sources:[source],can_edit:true,can_download:true};if(url.includes('/report?'))return reportFor();throw Error('Unexpected synthetic endpoint '+url);};
 const mounted=mount(component,{api:api||fallback,companyId:2,companyName:'Synthetic company',currency:'UZS',canEdit,canDownload,...rest});
 t.after(()=>mounted.unmount());return {...mounted,calls};
}

test('source periods preserve missing coverage and currency amounts sum without a floating-point cent loss',()=>{
 assert.deepEqual(sourceYears(source),[2026]);assert.deepEqual(sourceYears({first_date:'2025-12-01',last_date:'2026-01-01'}),[2025,2026]);
 assert.deepEqual(periodMonths(4,3),[]);assert.deepEqual(periodMonths(1,2),[1,2]);
 assert.equal(sumAmounts(['78543776885.22','0.08']), '78543776885.30');assert.equal(sumAmounts(['10.01','-10.00']), '0.01');
 assert.equal(sumAmounts(['1.00',null]),null);assert.equal(sumAmounts([]),null);
});

test('base64 upload preserves every byte including chunk boundaries',async()=>{
 const bytes=Uint8Array.from({length:65539},(_,index)=>index%256),file={arrayBuffer:async()=>bytes.buffer};
 const encoded=await fileBase64(file);assert.deepEqual(Buffer.from(encoded,'base64'),Buffer.from(bytes));
});

test('a saved source opens without uploading again and the default range ends at the last loaded month',async t=>{
 const mounted=view(t);await settle();assert.equal(mounted.state.sourceId,'7');assert.equal(mounted.state.year,2026);
 assert.equal(mounted.state.start,1);assert.equal(mounted.state.end,2);assert.equal(mounted.state.coverage,'2026-02-13');
 assert.equal(mounted.calls.filter(([,options])=>options.method==='POST').length,0);
 assert.match(text(mounted.container),/Повторная загрузка не нужна/);assert.match(text(mounted.container),/13.02.2026/);
});

test('plan minus fact follows the sample for both expenses and receipts; zero plan has no percentage',async t=>{
 const mounted=view(t);await settle();const [expense,receipt]=mounted.state.report.rows;
 assert.equal(mounted.state.delta(expense,0),10);assert.equal(mounted.state.delta(receipt,0),2);assert.equal(mounted.state.delta(receipt,1),-1);
 assert.equal(mounted.state.percent('1.00','0.00'),'—');assert.equal(mounted.state.percent(null,'10.00'),'—');
 mounted.state.setPeriod('year');await settle();assert.equal(mounted.state.months.length,12);assert.equal(mounted.state.months[2].available,false);
 assert.match(text(mounted.container),/Нет данных/);assert.equal(mounted.state.summary[1].value,'15.00');assert.equal(mounted.state.summary.at(-1).value,'70.00');
});

test('read-only users cannot upload, commit or edit notes even when the source server lists edit capability',async t=>{
 const mounted=view(t,{canEdit:false});await settle();assert.equal(mounted.state.canWrite,false);
 mounted.state.showUpload=true;mounted.state.preview={can_commit:true,id:1};mounted.state.reason='Synthetic import reason';
 await mounted.state.commit();mounted.state.editNote(mounted.state.report.rows[0],1);await settle();
 assert.equal(mounted.state.noteEdit,null);assert.equal(all(mounted.container,node=>node.tag==='input'&&node.props.type==='file').length,0);
 assert.equal(mounted.calls.filter(([,options])=>options.method==='POST'||options.method==='PUT').length,0);
});

test('server read-only capability also prevents writes and restricted roles see no export link',async t=>{
 const mounted=view(t,{canDownload:false,api:async url=>url.endsWith('/sources')?{sources:[source],can_edit:false,can_download:true}:reportFor()});await settle();
 assert.equal(mounted.state.canWrite,false);assert.equal(mounted.state.canDownload,false);
 assert.equal(all(mounted.container,node=>node.tag==='a'&&String(node.props.href).includes('/export.xlsx')).length,0);
});

test('preview sends both optional sample and source bytes; save uses explicitly selected units and currency',async t=>{
 const calls=[];
 const api=async(url,options={})=>{calls.push([url,options]);if(url.endsWith('/sources'))return {sources:[],can_edit:true,can_download:true};if(url.endsWith('/preview'))return {id:8,version:2,can_commit:true,metadata:{bank_count:2,record_count:4,first_date:'2026-01-05',last_date:'2026-02-13'},categories_count:3,issues:[]};if(url.endsWith('/commit'))return {source:{...source,id:8},idempotent:false};throw Error('Unexpected URL');};
 const mounted=view(t,{api});await settle();
 const fake=name=>({name,size:3,arrayBuffer:async()=>new Uint8Array([0x50,0x4b,3]).buffer});
 mounted.state.bankFile=fake('Synthetic bank.xlsx');mounted.state.templateFile=fake('Synthetic sample.xlsx');
 await mounted.state.checkFiles();const payload=JSON.parse(calls.find(([url])=>url.endsWith('/preview'))[1].body);
 assert.equal(payload.bank_filename,'Synthetic bank.xlsx');assert.equal(payload.template_filename,'Synthetic sample.xlsx');assert.equal(payload.bank_base64,'UEsD');
 assert.equal(calls.some(([url])=>url.endsWith('/commit')),false,'preview never commits financial data');
 mounted.state.scale=1000;mounted.state.uploadCurrency='USD';mounted.state.reason='Synthetic review confirmed';await mounted.state.commit();
 const saved=JSON.parse(calls.find(([url])=>url.endsWith('/commit'))[1].body);assert.deepEqual(saved,{version:2,currency:'USD',scale:1000,reason:'Synthetic review confirmed'});
});

test('missing file and oversized uploads are rejected before an API mutation',async t=>{
 const mounted=view(t);await settle();await mounted.state.checkFiles();assert.match(mounted.state.error,/Добавьте файл/);
 mounted.state.bankFile={name:'Synthetic.xlsx',size:8*1024*1024+1};await mounted.state.checkFiles();assert.match(mounted.state.error,/до 8 МБ/);
 assert.equal(mounted.calls.some(([,options])=>options.method==='POST'),false);
});

test('a stale source response cannot replace a more recently selected company-scoped source',async t=>{
 const second={...source,id:9,filename:'Synthetic second.xlsx'};let resolve;
 const api=async url=>{if(url.endsWith('/sources'))return {sources:[source,second],can_edit:true,can_download:true};if(url.includes('/7/report'))return new Promise(done=>resolve=done);if(url.includes('/9/report'))return reportFor(second);throw Error('Unexpected URL');};
 const mounted=view(t,{api});await settle();mounted.state.sourceId='9';await mounted.state.chooseSource();resolve(reportFor(source));await settle();
 assert.equal(mounted.state.report.source.id,9);assert.equal(mounted.state.progress.active,false);
});

test('a source from another company is rejected before its data can be displayed',async t=>{
 const mounted=view(t,{api:async url=>url.endsWith('/sources')?{sources:[source],can_edit:true,can_download:true}:reportFor({...source,company_id:3})});await settle();
 assert.equal(mounted.state.report,null);assert.match(mounted.state.error,/другой компании/);
});

test('article note save uses the current notes version and preserves source identity',async t=>{
 const calls=[];const api=async(url,options={})=>{calls.push([url,options]);if(url.endsWith('/sources'))return {sources:[source],can_edit:true,can_download:true};if(url.includes('/report?'))return reportFor();if(url.endsWith('/notes'))return {version:4};throw Error('Unexpected URL');};
 const mounted=view(t,{api});await settle();mounted.state.editNote(mounted.state.report.rows[0],2);mounted.state.noteEdit.text='Synthetic review note';await mounted.state.saveNote();
 const saved=calls.find(([url])=>url.endsWith('/notes'));assert.equal(saved[1].method,'PUT');assert.deepEqual(JSON.parse(saved[1].body),{version:3,month:'2026-02',code:'5',text:'Synthetic review note'});assert.equal(mounted.state.sourceId,'7');
});

test('daily view distinguishes banks and internal transfers and paginates rather than rendering all records',async t=>{
 const rows=Array.from({length:121},(_,index)=>({source_row:index+12,date:'2026-01-05',code:index%2?'5':'00-000010',name:index%2?'Synthetic expense':'Synthetic transfer',debit:'10.00',credit:'0.00',internal:index%2===0,bank_label:index%2?'Bank 1':'Bank 2'}));
 const mounted=view(t,{api:async url=>url.endsWith('/sources')?{sources:[source],can_edit:true,can_download:true}:url.includes('/daily?')?{rows,opening:'100.00',closing:'80.00'}:reportFor()});await settle();
 mounted.state.dailyMonth='2026-01';await mounted.state.loadDaily();assert.equal(mounted.state.dailyRows.length,50);assert.equal(mounted.state.dailyPages,3);
 mounted.state.dailyBank='Bank 1';await settle();assert.equal(mounted.state.filteredDaily.length,60);mounted.state.dailyQuery='missing';await settle();assert.equal(mounted.state.filteredDaily.length,0);assert.equal(mounted.state.dailyPage,1);
});

test('Russian and Uzbek source controls translate while imported financial names stay unchanged',async t=>{
 const previous=lang.value;t.after(()=>setLang(previous));setLang('ru');const mounted=view(t);await settle();assert.match(text(mounted.container),/Cash Flow по ОСВ 5110/);
 setLang('uz');await settle();assert.match(text(mounted.container),/5110 qaydnomasi bo‘yicha Cash Flow/);assert.match(text(mounted.container),/Synthetic expense/);
});

test('export is scoped to the chosen source, company, year, scenario and month range',()=>{
 const url=new URL(osvExportUrl(7,2026,'V',2,2,4),'http://local.test');assert.equal(url.pathname,'/api/cashflow-osv/sources/7/export.xlsx');
 assert.equal(url.searchParams.get('company_id'),'2');assert.equal(url.searchParams.get('scenario'),'V');assert.equal(url.searchParams.get('start_month'),'2');assert.equal(url.searchParams.get('end_month'),'4');
});
