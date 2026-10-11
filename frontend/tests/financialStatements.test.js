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
const row=(key,label,kind,plan,fact,measure=kind==='ratio'?'percent':'amount',rowNumber=7)=>({key,row:rowNumber,label,kind,measure,cells:[{month:1,plan,fact},{month:2,plan:cell('10'),fact:cell(null,'09_Statements!G7','missing')}]});
function sourcePayload(id=1){return{
 company_id:2,selected:id,sources:[{id,filename:'Synthetic.xlsx',created_at:'2026-10-11'}],
 source:{id,company_id:2,filename:'Synthetic.xlsx',year:2026,currency:null,unit:'Не указана',unit_status:'unknown',review_status:'needs_review',
 coverage:[{month:1,record_count:4,status:'available'},{month:2,record_count:0,status:'missing'}],
 issues:[{code:'equity',severity:'error',title:'Synthetic equity review',source:'09b_Balance!E42',detail:'Synthetic review detail'}],
 requirements:[{key:'ledger',title:'Synthetic trial balance',fields:['Synthetic debit','Synthetic credit'],frequency:'Every month',reason:'Synthetic reconciliation'}],
 reports:{pnl:{sheet:'09_Statements',title:'Synthetic PNL',periods:[{month:1,period:'2026-01'},{month:2,period:'2026-02'}],rows:[
 row('revenue','Synthetic revenue','amount',cell('100'),cell('125')),
 row('zero','Synthetic explicit zero','amount',cell('0'),cell('0'),'amount',8),
 row('future','Synthetic missing fact','amount',cell('0'),{...cell(null,'09_Statements!X7','future'),source_cache:'987654.32'},'amount',9),
 row('margin','Synthetic margin','ratio',cell('0.20'),cell('0.25'),'percent',13),
 ]},balance:{sheet:'09b_Balance',title:'Synthetic balance',periods:[{month:1,period:'2026-01'},{month:2,period:'2026-02'}],rows:[
 row('capital','Synthetic total assets','amount',cell('200'),cell('250'),'amount',19),
 row('days','Synthetic inventory days','ratio',cell('36'),cell('40'),'days',65),
 row('coefficient','Synthetic liquidity coefficient','ratio',cell('1.2'),cell('1.5'),'coefficient',74),
 ]}}},
 };}

function periodPayload(){
 const value=sourcePayload();
 const periods=Array.from({length:12},(_,index)=>({month:index+1,period:'2026-'+String(index+1).padStart(2,'0')}));
 const makeRow=(number,label,measure,plans,facts)=>({key:'synthetic-'+number,row:number,label,kind:['percent','days','coefficient'].includes(measure)?'ratio':'amount',measure,
   cells:periods.map((period,index)=>({month:period.month,plan:cell(String(plans[index]??plans[0]),'Synthetic!P'+period.month+'R'+number),fact:cell(String(facts[index]??facts[0]),'Synthetic!F'+period.month+'R'+number)}))});
 value.source.coverage=periods.map(period=>({month:period.month,record_count:5,status:'available'}));
 value.source.reports.pnl={sheet:'09_Statements',title:'Synthetic PNL',periods,rows:[
   makeRow(7,'Synthetic net revenue','amount',[100,200,300],[200,100,300]),
   makeRow(8,'Synthetic detailed sales','amount',[100],[200]),
   makeRow(12,'Synthetic Gross Profit','amount',[20,40,60],[50,20,30]),
   makeRow(13,'Synthetic gross margin','percent',[.2],[.25,.2,.1]),
   makeRow(14,'Synthetic OPEX','amount',[5],[8]),
   makeRow(16,'Synthetic EBITDA','amount',[15,35,55],[42,12,22]),
   makeRow(18,'Synthetic EBIT','amount',[12,32,52],[38,8,18]),
   makeRow(19,'Synthetic finance costs','amount',[2],[3]),
   makeRow(23,'Synthetic net profit','amount',[5,10,15],[10,5,30]),
   makeRow(24,'Synthetic net margin','percent',[.05],[.05,.05,.1]),
 ]};
 const plan=periods.map(period=>period.month*1000),fact=periods.map(period=>period.month*2000);
 value.source.reports.balance={sheet:'09b_Balance',title:'Synthetic balance',periods,rows:[
   makeRow(8,'Synthetic cash','amount',plan,fact),makeRow(19,'Synthetic total assets','amount',plan,fact),
   makeRow(20,'Synthetic payable','amount',plan,fact),makeRow(32,'Synthetic share capital','amount',plan,fact),
   makeRow(35,'Synthetic total liabilities','amount',plan,fact),makeRow(36,'Synthetic equity balancing','amount',plan,fact),
   makeRow(38,'Synthetic balance control','amount',[0],[0]),makeRow(56,'Synthetic equity reconciliation','amount',[0],[0]),
   {...makeRow(62,'Synthetic debt ratio','percent',[.3],[.4]),norm:{value:'< 0.5 (source)',source:'09b_Balance!A62',status:'author_guideline'}},
   makeRow(65,'Synthetic inventory days','days',[30],[40]),makeRow(72,'Synthetic working capital','amount',plan,fact),
   makeRow(74,'Synthetic liquidity coefficient','coefficient',[1.2],[1.5]),makeRow(78,'Synthetic source cash metric','amount',plan,fact),
 ]};
 return value;
}

const mainTable=view=>find(view.container,node=>node.tag==='table'&&!String(node.props.class||'').includes('statement-kpi-table'));
const metricTable=view=>find(view.container,node=>node.tag==='table'&&String(node.props.class||'').includes('statement-kpi-table'));
const metricCard=(view,number)=>find(view.container,node=>String(node.props?.class||'').includes('statement-kpi-card')&&Number(node.props['data-source-row'])===number);

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
 assert.match(text(view.container),/Synthetic equity review/);assert.match(text(view.container),/соответствие МСФО не подтверждено/);
 assert.equal(all(view.container,node=>node.tag==='a').length,0);
});

test('balance shows a single month, days and coefficients keep their units instead of percent scaling',async t=>{
 const view=mount(component,{company,type:'balance',api:async()=>sourcePayload()});t.after(view.unmount);await settle();
 const table=find(view.container,node=>node.tag==='table');
 assert.match(text(view.container),/Synthetic inventory days36,00 дн\.40,00 дн\.4,00—/);
 assert.match(text(view.container),/Synthetic liquidity coefficient1,201,500,30—/);
 assert.match(text(view.container),/строго на последний месяц диапазона/);
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
 assert.match(text(view.container),/Для новых операций и месяцев загрузите новую версию источника/);
 setLang('uz');await settle();assert.match(text(metadata),/Tekshirish \/ kesim sanasi: 11\.10\.2026/);
});

test('author thresholds preserve their source text and never claim an IFRS pass or compare invalid source values',async t=>{
 const payload=sourcePayload();const coefficient=payload.source.reports.balance.rows[2];coefficient.norm={value:'≥ 1.5 (author)',source:'09b_Balance!A74',status:'author_guideline'};
 payload.source.reports.balance.rows[1].norm={value:'#REF!',source:'09b_Balance!A65',status:'error'};
 const view=mount(component,{company,type:'balance',api:async()=>payload});t.after(view.unmount);await settle();
 const table=find(view.container,node=>node.tag==='table'&&String(node.props.class||'').includes('statement-kpi-table'));
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
 assert.match(text(view.container),/Manba tekshirishni talab qiladi/);assert.match(text(view.container),/36,00 kun/);
 assert.match(text(view.container),/Synthetic inventory days/);assert.doesNotMatch(text(view.container),/Проверка исходных данных/);
});

test('quarter controls calculate source PNL cards, weighted margins and every source month without mutating the book',async t=>{
 const payload=periodPayload(),original=structuredClone(payload);
 const view=mount(component,{company,type:'pnl',api:async()=>payload});t.after(view.unmount);await settle();
 view.state.setPer('q1');await settle();
 assert.equal(view.state.range.startMonth,1);assert.equal(view.state.range.endMonth,3);
 assert.match(text(metricCard(view,7)),/600,00/);assert.match(text(metricCard(view,12)),/100,00/);assert.match(text(metricCard(view,23)),/45,00/);
 assert.match(text(mainTable(view)),/Synthetic gross margin20,00\s*%16,67\s*%/);
 assert.match(text(mainTable(view)),/Synthetic!F1R7/);assert.match(text(mainTable(view)),/Synthetic!F2R7/);assert.match(text(mainTable(view)),/Synthetic!F3R7/);
 assert.deepEqual(payload,original);
});

test('an incomplete quarter suppresses the fact total and cards instead of silently showing a subtotal',async t=>{
 const payload=periodPayload();payload.source.coverage[1].status='missing';
 const view=mount(component,{company,type:'pnl',api:async()=>payload});t.after(view.unmount);await settle();view.state.setPer('q1');await settle();
 const card=metricCard(view,7);const headline=find(card,node=>String(node.props?.class||'').includes('v num'));
 assert.match(text(headline),/^—/);assert.match(text(card),/План: 600,00/);
 assert.match(text(mainTable(view)),/Synthetic net revenue600,00———/);
 assert.match(text(mainTable(view)),/Не все месяцы выбранного периода подтверждены/);
});

test('a reversed custom period stays visible as an error and never falls back to January totals',async t=>{
 const view=mount(component,{company,type:'pnl',api:async()=>periodPayload()});t.after(view.unmount);await settle();
 view.state.setPer('custom');view.state.start=3;view.state.end=1;await settle();
 assert.equal(Number(view.state.start),3);assert.equal(Number(view.state.end),1);
 assert.match(text(view.container),/Начало периода должно быть не позже конца/);
 assert.match(text(mainTable(view)),/Synthetic net revenue————/);
 assert.match(text(find(metricCard(view,7),node=>String(node.props?.class||'').includes('v num'))),/^—/);
});

test('switching from one month to a custom period preserves the selected month and source capital stays in its chapter',async t=>{
 const view=mount(component,{company,type:'balance',api:async()=>periodPayload()});t.after(view.unmount);await settle();
 view.state.month=7;await settle();view.state.setPer('custom');await settle();
 assert.equal(Number(view.state.start),7);assert.equal(Number(view.state.end),7);
 assert.ok(view.state.groups.find(group=>group.key==='capital').rows.some(row=>row.row===32));
 assert.ok(!view.state.groups.find(group=>group.key==='liabilities').rows.some(row=>row.row===32));
});

test('source year choices cannot invent years, and changing display modes controls the financial and KPI columns',async t=>{
 const view=mount(component,{company,type:'balance',api:async()=>periodPayload()});t.after(view.unmount);await settle();
 const yearSelect=find(view.container,node=>node.tag==='select'&&node.props['aria-label']==='Год отчёта');
 assert.deepEqual(yearSelect.children.filter(node=>node.tag==='option').map(node=>node.props.value),[2026]);
 view.state.mode='actual';await settle();assert.deepEqual(view.state.subcolumns,['fact']);
 assert.doesNotMatch(text(find(mainTable(view),node=>node.tag==='thead')),/План/);
 assert.doesNotMatch(text(find(metricTable(view),node=>node.tag==='thead')),/План/);
 view.state.mode='plan';await settle();assert.deepEqual(view.state.subcolumns,['plan']);
 view.state.mode='compare';await settle();assert.deepEqual(view.state.subcolumns,['plan','fact','deviation','percent']);
});

test('monthly view exposes source columns plus one selected-period total and can return to the compact table',async t=>{
 const view=mount(component,{company,type:'pnl',api:async()=>periodPayload()});t.after(view.unmount);await settle();view.state.setPer('q1');await settle();
 assert.equal(view.state.columns.length,1);
 const toggle=find(view.container,node=>node.tag==='button'&&text(node)==='Показать месяцы');toggle.props.onClick();await settle();
 assert.deepEqual(view.state.columns.map(column=>column.month??'total'),[1,2,3,'total']);
 assert.match(text(find(mainTable(view),node=>node.tag==='thead')),/2026-01.*2026-02.*2026-03.*За период/);
 assert.equal(toggle.props['aria-pressed'],true);
 toggle.props.onClick();await settle();assert.equal(view.state.columns.length,1);
});

test('balance quarters take only the final month, preserve a missing ending snapshot and place all KPIs below',async t=>{
 const payload=periodPayload();
 const view=mount(component,{company,type:'balance',api:async()=>payload});t.after(view.unmount);await settle();view.state.setPer('q1');await settle();
 assert.match(text(metricCard(view,19)),/6\s?000,00/);assert.match(text(metricCard(view,36)),/Капитал как разница/);
 assert.match(text(metricCard(view,36)),/Synthetic equity balancing/);assert.match(text(metricCard(view,36)),/Требует проверки/);
 assert.match(text(metricTable(view)),/Synthetic inventory days/);assert.doesNotMatch(text(mainTable(view)),/Synthetic inventory days/);
 assert.match(text(view.container),/Коэффициенты за период и остатки на его конец · 2026-03/);
 view.state.payload.source.reports.balance.rows.find(row=>row.row===19).cells[2].fact={value:null,status:'missing'};await settle();
 assert.match(text(find(metricCard(view,19),node=>String(node.props?.class||'').includes('v num'))),/^—/);
 assert.doesNotMatch(text(metricCard(view,19)),/4\s?000,00/);
});

test('money scales alone while percentage, days, coefficient and original author norms stay unchanged',async t=>{
 const view=mount(component,{company,type:'balance',api:async()=>periodPayload()});t.after(view.unmount);await settle();view.state.month=3;view.state.unit='k';await settle();
 assert.match(text(metricCard(view,19)),/6,00/);assert.match(text(metricCard(view,19)),/тыс\. исходных единиц/);
 assert.match(text(metricTable(view)),/Synthetic debt ratio30,00\s*%40,00\s*%/);
 assert.match(text(metricTable(view)),/Synthetic inventory days30,00 дн\.40,00 дн\./);
 assert.match(text(metricTable(view)),/Synthetic liquidity coefficient1,201,50/);
 assert.match(text(metricTable(view)),/Synthetic working capital3,006,00/);
 assert.match(text(metricTable(view)),/< 0\.5 \(source\)/);
 view.state.unit='m';await settle();assert.match(text(metricCard(view,19)),/млн исходных единиц/);
 assert.match(text(metricTable(view)),/Synthetic liquidity coefficient1,201,50/);
 assert.match(text(view.container),/не их валюту или смысл/);
});

test('cards and monetary deviations preserve large decimal amounts and negative differences before and after scaling',async t=>{
 const payload=periodPayload(),revenue=payload.source.reports.pnl.rows.find(row=>row.row===7);
 revenue.cells[0].plan=cell('0');revenue.cells[0].fact=cell('9007199254740993.25');
 const view=mount(component,{company,type:'pnl',api:async()=>payload});t.after(view.unmount);await settle();
 const compact=value=>value.replace(/[\s\u00a0\u202f]/g,'');
 assert.match(compact(text(metricCard(view,7))),/9007199254740993,25/);
 assert.match(compact(text(mainTable(view))),/Syntheticnetrevenue0,009007199254740993,259007199254740993,25—/);
 view.state.unit='k';await settle();assert.match(compact(text(mainTable(view))),/Syntheticnetrevenue0,009007199254740,999007199254740,99—/);
 view.state.payload.source.reports.pnl.rows.find(row=>row.row===7).cells[0].fact=cell('-9007199254740993.25');view.state.unit='x';await settle();
 assert.match(compact(text(mainTable(view))),/Syntheticnetrevenue0,00-9007199254740993,25-9007199254740993,25—/);
});

test('collapse preserves chapter headers, key totals and cards; source requirements and findings start folded',async t=>{
 const view=mount(component,{company,type:'pnl',api:async()=>periodPayload()});t.after(view.unmount);await settle();
 view.state.collapseAll();await settle();
 const table=mainTable(view);assert.equal(all(table,node=>node.tag==='tr'&&Number(node.props['data-source-row'])===8).length,0);
 assert.equal(all(table,node=>node.tag==='tr'&&Number(node.props['data-source-row'])===12).length,1);
 assert.equal(all(view.container,node=>String(node.props?.class||'').includes('statement-kpi-card')).length,5);
 assert.equal(all(table,node=>node.tag==='button'&&node.props['aria-expanded']===false).length,3);
 assert.match(text(table),/Выручка и валовая прибыль/);
 view.state.toggleGroup('gross');await settle();assert.equal(all(table,node=>node.tag==='tr'&&Number(node.props['data-source-row'])===8).length,1);
 const disclosures=all(view.container,node=>node.tag==='details'&&String(node.props.class||'').includes('statement-disclosure'));
 assert.equal(disclosures.length,3);assert.ok(disclosures.every(node=>node.props.open===false));
});

test('corrected sources show independent capital, residual and named clarifications without repeating found fields',async t=>{
 const payload=periodPayload();
 payload.source.calculation={profile:'uzbek_grouped_chart_finmodel_v1'};
 payload.source.reports.balance.calculation={basis:'mapping'};
 for(const period of payload.source.reports.balance.rows.find(row=>row.row===36).cells)period.fact.status='calculated';
 payload.source.plan_source={status:'cached_snapshot',as_of:'2026-10-11',live:false};
 payload.source.coverage[7].status='missing';payload.source.coverage[8].status='future';
 payload.source.requirements=[
  {key:'sales',title:'Synthetic sales',status:'found',evidence:['Mapping 9010 / 9040 / 9050'],fields:['Synthetic duplicate sales field'],missing_fields:[]},
  {key:'plan',title:'Synthetic plan',status:'needs_clarification',evidence:['98_CashMacro_Import'],missing_fields:[{key:'vat',title:'Synthetic VAT basis',reason:'Synthetic gross versus net'}]},
 ];
 const view=mount(component,{company,type:'balance',api:async()=>payload});t.after(view.unmount);await settle();
 assert.match(text(metricCard(view,36)),/Капитал по счетам/);assert.doesNotMatch(text(metricCard(view,36)),/Капитал как разница|Требует проверки/);
 assert.match(text(view.container),/Контроль баланса: активы − обязательства − капитал/);
 assert.match(text(view.container),/Найдено: 1.*Уточнить: 1.*Ожидают ОСВ: 2/);
 assert.match(text(view.container),/Mapping 9010 \/ 9040 \/ 9050/);
 assert.match(text(view.container),/Synthetic VAT basis.*Synthetic gross versus net/);
 assert.doesNotMatch(text(view.container),/Synthetic duplicate sales field/);
 assert.match(text(view.container),/сохранённый снимок Macro.*Обновление проверки не заменяет бюджет/);
 assert.match(text(view.container),/2026-08, 2026-09.*не считаются ошибками расчёта/);
});

test('unconfirmed EBITDA stays blank, states its allocation reason and preserves original cache only in source details',async t=>{
 const payload=periodPayload(),ebitda=payload.source.reports.pnl.rows.find(row=>row.row===16);
 ebitda.cells[0].fact={value:null,status:'requires_allocation',reason:'depreciation_allocation_required',
  source:'09_Statements!F16',source_cache:'42',source_status:'cached',source_formula:'Synthetic original EBITDA',
  provenance:[{source:'Mapping!H55',account:'0200',measure:'turnover_debit'}]};
 const view=mount(component,{company,type:'pnl',api:async()=>payload});t.after(view.unmount);await settle();
 const card=metricCard(view,16);
 assert.match(text(find(card,node=>String(node.props?.class||'').includes('v num'))),/^—/);
 assert.match(text(card),/Нужно распределение амортизации/);
 assert.match(text(card),/Повторно вычитать амортизацию нельзя/);
 assert.match(text(mainTable(view)),/Сохранённое значение до исправления: 42,00/);
 assert.match(text(mainTable(view)),/Mapping!H55/);
});

test('balance range KPI sums quarter profits and uses opening and ending assets, while end liquidity remains unchanged',async t=>{
 const payload=periodPayload(),balance=payload.source.reports.balance;
 const assets=balance.rows.find(row=>row.row===19);
 assets.cells[0].fact.opening_balance={value:'1000',status:'calculated',source:'Mapping synthetic opening assets'};
 const roe={...structuredClone(balance.rows.find(row=>row.row===62)),row:63,label:'Synthetic ROA',key:'synthetic-roa'};
 balance.rows.push(roe);
 const view=mount(component,{company,type:'balance',api:async()=>payload});t.after(view.unmount);await settle();
 view.state.setPer('q1');await settle();
 const ratio=find(metricTable(view),node=>node.tag==='tr'&&Number(node.props['data-source-row'])===63);
 assert.match(text(ratio),/Synthetic ROA—1,29\s*%/); // 45 / average(1000, 6000).
 assert.match(text(ratio),/Mapping synthetic opening assets/);
 assert.match(text(metricTable(view)),/Synthetic liquidity coefficient1,201,50/);
 assert.match(text(metricTable(view)),/Нет подтверждённого остатка на начало выбранного периода/);
});

test('RU/UZ translates found and missing-field summary, corrected capital and allocation instructions',async t=>{
 const previous=lang.value;t.after(()=>setLang(previous));setLang('uz');
 const payload=periodPayload();payload.source.reports.balance.calculation={basis:'mapping'};
 for(const period of payload.source.reports.balance.rows.find(row=>row.row===36).cells)period.fact.status='calculated';
 payload.source.requirements=[{key:'unit',title:'Synthetic units',status:'needs_clarification',missing_fields:[{key:'unit',title:'Нужно подтвердить единицы'}]}];
 const view=mount(component,{company,type:'balance',api:async()=>payload});t.after(view.unmount);await settle();
 assert.match(text(view.container),/Aniqlashtirish: 1/);
 assert.match(text(view.container),/Hisobvaraqlar bo‘yicha kapital/);
 assert.match(text(view.container),/To‘ldirish yoki tasdiqlash kerak/);
 assert.match(text(view.container),/Birliklarni tasdiqlash kerak/);
});

test('resolved source defects leave the current issue count and capital labels follow the selected calculated month and side',async t=>{
 const payload=periodPayload(),capital=payload.source.reports.balance.rows.find(row=>row.row===36);
 payload.source.reports.balance.calculation={profile:'uzbek_grouped_chart_finmodel_v1',status:'derived_provisional'};
 capital.fact_label='Капитал по счетам (8300, 8500, 8700, 9900)';
 capital.cells[0].fact.status='calculated';
 payload.source.issues=[
  {code:'equity_plug',severity:'error',title:'Synthetic corrected source defect',detail:'Synthetic corrected basis',resolved_in_calculation:true},
  {code:'unit_missing',severity:'warning',title:'Synthetic current clarification',detail:'Synthetic unit basis'},
 ];
 const view=mount(component,{company,type:'balance',api:async()=>payload});t.after(view.unmount);await settle();
 assert.equal(view.state.issues.length,1);assert.equal(view.state.correctedIssues.length,1);
 assert.match(text(metricCard(view,36)),/Капитал по счетам/);
 assert.match(text(view.container),/Исправлено: 1/);assert.match(text(view.container),/Замечаний: 1/);
 view.state.month=2;await settle();
 assert.match(text(metricCard(view,36)),/Капитал как разница/);
 assert.doesNotMatch(text(metricCard(view,36)),/Капитал по счетам/);
 view.state.month=1;view.state.mode='plan';await settle();
 assert.match(text(metricCard(view,36)),/Капитал как разница/);
 assert.doesNotMatch(text(mainTable(view)),/Капитал по счетам \(8300/);
});

test('an explicitly unrecognized relocated model shows original rows without standard cards or recalculated ROE',async t=>{
 const payload=periodPayload(),balance=payload.source.reports.balance;
 payload.source.calculation={status:'source_only',profile:null,reason:'profile_unrecognized'};
 const cash=balance.rows.find(row=>row.row===8);
 const tax=structuredClone(cash);tax.key='relocated-tax';tax.row=8;tax.label='Original tax advance at row8';
 for(const pair of tax.cells){pair.plan=cell('123');pair.fact=cell('321');}
 cash.row=11;cash.key='relocated-cash';cash.label='Original cash at row11';balance.rows.push(tax);
 const roe=structuredClone(balance.rows.find(row=>row.row===62));roe.row=64;roe.key='original-roe';roe.label='Original source ROE';
 for(const pair of roe.cells){pair.plan=cell('0.82');pair.fact=cell('0.91');}
 balance.rows.push(roe);
 const view=mount(component,{company,type:'balance',api:async()=>payload});t.after(view.unmount);await settle();
 view.state.setPer('q1');await settle();
 assert.equal(view.state.cards.length,0);assert.equal(view.state.kpiRows.length,0);
 assert.equal(all(view.container,node=>String(node.props?.class||'').includes('statement-kpi-card')).length,0);
 assert.equal(metricTable(view),undefined);assert.equal(view.state.independentCapital,false);
 assert.deepEqual(view.state.groups.map(group=>group.key),['source']);
 assert.match(text(mainTable(view)),/Строки исходного отчёта/);
 assert.match(text(mainTable(view)),/Original tax advance at row8123,00321,00/);
 assert.match(text(mainTable(view)),/Original cash at row113\s?000,006\s?000,00/);
 assert.match(text(mainTable(view)),/Original source ROE82,00\s*%91,00\s*%/);
 assert.equal(view.state.rowCells(roe).fact.status,'cached');assert.equal(view.state.rowCells(roe).fact.value,'0.91');
 assert.match(text(view.container),/Формат модели не распознан: показаны строки исходника без расчётных карточек и KPI/);
 const previous=lang.value;t.after(()=>setLang(previous));setLang('uz');await settle();
 assert.match(text(view.container),/Model formati aniqlanmadi/);
});

test('a recognized profile with a source-only month retains its ordinary report controls and KPI section',async t=>{
 const payload=periodPayload();
 payload.source.calculation={status:'derived_provisional',profile:'uzbek_grouped_chart_finmodel_v1',months:[{month:1,status:'source_only',reason:'incomplete_account_register'}]};
 const view=mount(component,{company,type:'balance',api:async()=>payload});t.after(view.unmount);await settle();
 assert.equal(view.state.profileUnrecognized,false);assert.equal(view.state.cards.length,4);
 assert.ok(view.state.kpiRows.length>0);assert.ok(view.state.groups.some(group=>group.key==='assets'));
});
