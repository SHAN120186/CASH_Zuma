import {test} from 'node:test';
import assert from 'node:assert/strict';
import {reactive} from 'vue';
import {setLang,lang} from '../src/i18n/index.js';
import {statementUrl,statementFileProblem,MAX_STATEMENT_BYTES,cellNumber,statementCell,statementDeviation,sourceScopeMatches} from '../src/financialStatements.js';
import {compileComponent,mount,settle,text,find,all} from './helpers/mountVue.js';

const progressComponent=await compileComponent(new URL('../src/OperationProgress.vue',import.meta.url));
const component=await compileComponent(new URL('../src/FinancialStatements.vue',import.meta.url),{'./OperationProgress.vue':progressComponent});
const company={id:2,name:'Synthetic company'};
const cell=(value,source='09_Statements!D7',status='cached')=>({value,source,status,formula:'=SUM(01_Data!F2:F5)'});
const row=(key,label,kind,plan,fact,measure=kind==='ratio'?'percent':'amount')=>({key,row:7,label,kind,measure,cells:[{month:1,plan,fact},{month:2,plan:cell('10'),fact:cell(null,'09_Statements!G7','missing')}]});
function sourcePayload(id=1){return{
 company_id:2,selected:id,sources:[{id,filename:'Synthetic.xlsx',created_at:'2026-10-11'}],
 source:{id,company_id:2,filename:'Synthetic.xlsx',year:2026,currency:null,unit:'Не указана',unit_status:'unknown',review_status:'needs_review',
 coverage:[{month:1,record_count:4,status:'available'},{month:2,record_count:0,status:'missing'}],
 issues:[{code:'equity',severity:'error',title:'Synthetic equity review',source:'09b_Balance!E42',detail:'Synthetic review detail'}],
 requirements:[{key:'ledger',title:'Synthetic trial balance',fields:['Synthetic debit','Synthetic credit'],frequency:'Every month',reason:'Synthetic reconciliation'}],
 reports:{pnl:{sheet:'09_Statements',title:'Synthetic PNL',periods:[{month:1,period:'2026-01'},{month:2,period:'2026-02'}],rows:[
 row('revenue','Synthetic revenue','amount',cell('100'),cell('125')),
 row('zero','Synthetic explicit zero','amount',cell('0'),cell('0')),
 row('future','Synthetic missing fact','amount',cell('0'),{...cell(null,'09_Statements!X7','future'),source_cache:'987654.32'}),
 row('margin','Synthetic margin','ratio',cell('0.20'),cell('0.25')),
 ]},balance:{sheet:'09b_Balance',title:'Synthetic balance',periods:[{month:1,period:'2026-01'},{month:2,period:'2026-02'}],rows:[
 row('capital','Synthetic total assets','amount',cell('200'),cell('250')),
 row('days','Synthetic inventory days','ratio',cell('36'),cell('40'),'days'),
 row('coefficient','Synthetic liquidity coefficient','ratio',cell('1.2'),cell('1.5'),'coefficient'),
 ]}}},
 };}

test('source review distinguishes available zero from absent, future, errors and saved-only caches',()=>{
 assert.equal(cellNumber(cell('0')),0);assert.equal(cellNumber(cell('-2.5')), -2.5);
 for(const status of ['missing','future','missing_cache','error','invalid'])assert.equal(cellNumber({...cell('999',undefined,status),source_cache:'999'}),null,status);
 for(const value of ['',null,'#REF!','NaN','Infinity','1,000','20 USD'])assert.equal(cellNumber(cell(value)),null);
 assert.equal(statementCell({cells:[{month:1,plan:cell('20')},{month:2,plan:cell('30')}]},'2').plan.value,'30');
 assert.equal(statementCell({cells:[]},1).fact,null);
});

test('deviations handle missing and zero plans, ratios are percentage points rather than summed margins',()=>{
 assert.deepEqual(statementDeviation(cell('100'),cell('125')),{value:25,percent:25});
 assert.deepEqual(statementDeviation(cell('0'),cell('50')),{value:50,percent:null});
 assert.deepEqual(statementDeviation(cell('-100'),cell('-50')),{value:50,percent:null});
 assert.equal(statementDeviation(cell('0.2'),cell('0.25'),'ratio').percent,null);
 assert.equal(Math.round(statementDeviation(cell('0.2'),cell('0.25'),'ratio').value*100),5);
 assert.deepEqual(statementDeviation(cell('10'),cell(null)),{value:null,percent:null});
});

test('requests are company scoped and uploads reject empty, wrong extension, oversized and misleading filenames',()=>{
 assert.equal(statementUrl(2,8),'/api/financial-statements?company_id=2&source_id=8');
 assert.equal(sourceScopeMatches({company_id:2,source:{company_id:2}},2),true);
 assert.equal(sourceScopeMatches({company_id:7},2),false);
 assert.equal(sourceScopeMatches({source:{company_id:7}},2),false);
 assert.equal(statementFileProblem({name:'Synthetic.XLSX',size:MAX_STATEMENT_BYTES}), '');
 for(const file of [{name:'Synthetic.pdf',size:10},{name:'Synthetic.xlsx',size:0},{name:'Synthetic.xlsx',size:MAX_STATEMENT_BYTES+1},{name:'Synthetic\u202e.xlsx',size:10}])assert.ok(statementFileProblem(file));
});

test('PNL view preserves zero, hides missing/future caches, displays source provenance and no downloads',async t=>{
 const calls=[];const view=mount(component,{company,type:'pnl',canDownload:true,api:async url=>{calls.push(url);return sourcePayload();}});t.after(view.unmount);await settle();
 assert.deepEqual(calls,['/api/financial-statements?company_id=2']);
 const table=find(view.container,node=>node.tag==='table');
 assert.match(text(table),/Synthetic revenue100,00125,0025,0025,00%/);
 assert.match(text(table),/Synthetic explicit zero0,000,000,00—/);
 assert.match(text(table),/Synthetic missing fact0,00———/);
 assert.match(text(table),/Synthetic margin20,00\s*%25,00\s*%5,00 п\. п\.—/);
 assert.doesNotMatch(text(view.container),/987654\.32/);
 assert.match(text(view.container),/09_Statements!D7/);assert.match(text(view.container),/Synthetic trial balance/);
 assert.match(text(view.container),/Synthetic equity review/);assert.match(text(view.container),/не подтверждает соответствие МСФО/);
 assert.equal(all(view.container,node=>node.tag==='a').length,0);
});

test('balance shows a single month, days and coefficients keep their units instead of percent scaling',async t=>{
 const view=mount(component,{company,type:'balance',api:async()=>sourcePayload()});t.after(view.unmount);await settle();
 const table=find(view.container,node=>node.tag==='table');
 assert.match(text(table),/Synthetic inventory days36,00 дн\.40,00 дн\.4,00—/);
 assert.match(text(table),/Synthetic liquidity coefficient1,201,500,30—/);
 assert.match(text(view.container),/не суммируются/);
 view.state.month=2;await settle();
 assert.match(text(table),/Synthetic total assets10,00———/);
 assert.doesNotMatch(text(table),/250,00/);
});

test('an unconfirmed source date never inherits a year or period from the page',async t=>{
 const payload=sourcePayload();payload.source.reports.pnl.periods[0].period=null;
 const view=mount(component,{company,type:'pnl',api:async()=>payload});t.after(view.unmount);await settle();
 const table=find(view.container,node=>node.tag==='table');
 assert.match(text(table),/Месяц 1 · дата не подтверждена/);
 assert.doesNotMatch(text(table),/2026-01/);
});

test('the source review cutoff is shown from its immutable snapshot instead of the browser date',async t=>{
 const previous=lang.value;t.after(()=>setLang(previous));setLang('ru');
 const payload=sourcePayload();payload.source.reviewed_as_of='2026-10-11';
 const view=mount(component,{company,type:'pnl',api:async()=>payload});t.after(view.unmount);await settle();
 const metadata=find(view.container,node=>String(node.props?.class||'').includes('statement-source'));
 assert.match(text(metadata),/Дата проверки \/ среза: 11\.10\.2026/);
 assert.match(text(view.container),/Для обновления среза загрузите обновлённый источник/);
 setLang('uz');await settle();assert.match(text(metadata),/Tekshirish \/ kesim sanasi: 11\.10\.2026/);
});

test('author thresholds preserve their source text and never claim an IFRS pass or compare invalid source values',async t=>{
 const payload=sourcePayload();const coefficient=payload.source.reports.balance.rows[2];coefficient.norm={value:'≥ 1.5 (author)',source:'09b_Balance!A74',status:'author_guideline'};
 payload.source.reports.balance.rows[1].norm={value:'#REF!',source:'09b_Balance!A65',status:'error'};
 const view=mount(component,{company,type:'balance',api:async()=>payload});t.after(view.unmount);await settle();
 const table=find(view.container,node=>node.tag==='table');
 assert.match(text(table),/Ориентир автора/);assert.match(text(table),/≥ 1\.5 \(author\)/);assert.match(text(table),/09b_Balance!A74/);
 assert.match(text(table),/09b_Balance!A65/);assert.match(text(table),/Ошибка формулы/);assert.doesNotMatch(text(table),/#REF!/);
 assert.match(text(view.container),/не оценка соответствия МСФО/);assert.equal(all(view.container,node=>node.props?.class==='good'||node.props?.class==='pass').length,0);
});

test('an empty source lists named requirements without fabricating amounts or upload rights',async t=>{
 const view=mount(component,{company,canImport:false,api:async()=>({sources:[],source:null,requirements:[{key:'data',title:'Synthetic source data',fields:['Synthetic account codes'],frequency:'Monthly',reason:'Synthetic check'}]})});t.after(view.unmount);await settle();
 assert.match(text(view.container),/пока не загружена/);assert.match(text(view.container),/Synthetic account codes/);
 assert.equal(all(view.container,node=>node.tag==='input'&&node.props.type==='file').length,0);
 assert.equal(all(view.container,node=>node.tag==='table').length,0);
});

test('a source response explicitly belonging to another company is rejected before rendering',async t=>{
 const wrong=sourcePayload();wrong.source.company_id=7;
 const view=mount(component,{company,api:async()=>wrong});t.after(view.unmount);await settle();
 assert.equal(view.state.payload,null);assert.match(view.state.error,/другой компании/);
 assert.doesNotMatch(text(view.container),/Synthetic revenue/);
});

test('a later source selection wins over an earlier slow response; stale errors cannot replace current source',async t=>{
 let rejectOld;const old=new Promise((_,reject)=>rejectOld=reject);const calls=[];
 const view=mount(component,{company,api:url=>{calls.push(url);return calls.length===1?old:Promise.resolve(sourcePayload(8));}});t.after(view.unmount);
 const next=view.state.load('8');await next;await settle();assert.equal(view.state.selected,'8');
 rejectOld(Error('Synthetic old response'));await settle();
 assert.equal(view.state.selected,'8');assert.equal(view.state.error,'');assert.equal(view.state.loading,false);
});

test('company change clears prior source while loading and late former-company responses are ignored',async t=>{
 let resolveLater;let calls=0;const props=reactive({company,api:async url=>{calls++;if(calls===2)return new Promise(resolve=>resolveLater=resolve);return sourcePayload();}});
 const view=mount(component,props);t.after(view.unmount);await settle();assert.equal(view.state.source.id,1);
 const pending=view.state.load(8);await settle();assert.equal(view.state.payload,null);
 view.app._instance.props.company={id:7,name:'Synthetic other company'};await settle();
 assert.equal(view.state.payload,null);assert.match(view.state.error,/другой компании/);
 resolveLater(sourcePayload(8));await pending;await settle();assert.equal(view.state.payload,null);
});

test('upload requires import rights, sends raw Excel and counts only confirmed upload and checked-read stages',async t=>{
 const calls=[];let resolveUpload;const uploaded=new Promise(resolve=>resolveUpload=resolve);
 const view=mount(component,{company,canImport:true,api:async(url,opts)=>{calls.push({url,opts});if(opts?.method==='POST')return uploaded;return sourcePayload(opts?1:8);}});t.after(view.unmount);await settle();
 const file={name:'Synthetic source.xlsx',size:10};const event={target:{files:[file],value:'file'}};const task=view.state.upload(event);await settle();
 assert.equal(event.target.value,'');assert.equal(view.state.progress.completed,0);assert.equal(view.state.progress.total,2);assert.equal(view.state.uploading,true);
 resolveUpload({id:8,created:true});await task;await settle();
 assert.equal(view.state.progress.completed,2);assert.equal(view.state.progress.active,false);
 const call=calls.find(item=>item.opts?.method==='POST');assert.equal(call.url,'/api/financial-statements/upload?company_id=2');assert.equal(call.opts.body,file);assert.equal(call.opts.headers['X-Filename'],'Synthetic%20source.xlsx');
 assert.equal(calls.at(-1).url,'/api/financial-statements?company_id=2&source_id=8');
 view.app._instance.props.canImport=false;await settle();const count=calls.length;await view.state.upload({target:{files:[file],value:'file'}});assert.equal(calls.length,count);
});

test('RU/UZ changes static source-review instructions and units without altering raw source labels',async t=>{
 const previous=lang.value;t.after(()=>setLang(previous));setLang('uz');
 const view=mount(component,{company,type:'balance',api:async()=>sourcePayload()});t.after(view.unmount);await settle();
 assert.match(text(view.container),/Manba ma’lumotlarini tekshirish/);assert.match(text(view.container),/36,00 kun/);
 assert.match(text(view.container),/Synthetic inventory days/);assert.doesNotMatch(text(view.container),/Проверка исходных данных/);
});
