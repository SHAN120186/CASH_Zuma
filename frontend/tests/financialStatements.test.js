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
const visibleText=node=>node.props?.hidden?'':node.tag==='details'&&!node.props.open?(node.children||[]).filter(child=>child.tag==='summary').map(visibleText).join(''):(node.text||'')+(node.children||[]).map(visibleText).join('');
const openHelp=async view=>{await view.state.openHelp();await settle();};
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
 const calls=[];const view=mount(component,{company,type:'pnl',canDownload:true,api:async url=>{calls.push(url);return sourcePayload();}});t.after(view.unmount);await settle();await openHelp(view);
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
 const view=mount(component,{company,type:'balance',api:async()=>sourcePayload()});t.after(view.unmount);await settle();await openHelp(view);
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
 const view=mount(component,{company,type:'pnl',api:async()=>payload});t.after(view.unmount);await settle();await openHelp(view);
 const metadata=find(view.container,node=>String(node.props?.class||'').includes('statement-source'));
 assert.match(text(metadata),/Дата проверки \/ среза: 11\.10\.2026/);
 assert.match(text(view.container),/Для новых операций и месяцев загрузите новую версию источника/);
 setLang('uz');await settle();assert.match(text(metadata),/Tekshirish \/ kesim sanasi: 11\.10\.2026/);
});

test('author thresholds preserve their source text and never claim an IFRS pass or compare invalid source values',async t=>{
 const payload=sourcePayload();const coefficient=payload.source.reports.balance.rows[2];coefficient.norm={value:'≥ 1.5 (author)',source:'09b_Balance!A74',status:'author_guideline'};
 payload.source.reports.balance.rows[1].norm={value:'#REF!',source:'09b_Balance!A65',status:'error'};
 const view=mount(component,{company,type:'balance',api:async()=>payload});t.after(view.unmount);await settle();await openHelp(view);
 const table=find(view.container,node=>node.tag==='table'&&String(node.props.class||'').includes('statement-kpi-table'));
 assert.match(text(table),/Ориентир автора/);assert.match(text(table),/≥ 1\.5 \(author\)/);assert.match(text(table),/09b_Balance!A74/);
 assert.match(text(table),/09b_Balance!A65/);assert.match(text(table),/Ошибка формулы/);assert.doesNotMatch(text(table),/#REF!/);
 assert.match(text(view.container),/не оценка соответствия МСФО/);assert.equal(all(view.container,node=>node.props?.class==='good'||node.props?.class==='pass').length,0);
});

test('an empty source lists named requirements without fabricating amounts or upload rights',async t=>{
 const view=mount(component,{company,canImport:false,api:async()=>({sources:[],source:null,requirements:[{key:'data',title:'Synthetic source data',fields:['Synthetic account codes'],frequency:'Monthly',reason:'Synthetic check'}]})});t.after(view.unmount);await settle();
 assert.match(visibleText(view.container),/Нет данных для этой компании/);assert.doesNotMatch(text(view.container),/Synthetic account codes/);
 await openHelp(view);assert.match(text(view.container),/Synthetic account codes/);
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
 const view=mount(component,{company,type:'balance',api:async()=>sourcePayload()});t.after(view.unmount);await settle();await openHelp(view);
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
 assert.match(text(metricCard(view,19)),/6\s?000,00/);assert.match(text(metricCard(view,36)),/Капитал/);
 assert.doesNotMatch(text(metricCard(view,36)),/Synthetic equity balancing/);assert.match(text(metricCard(view,36)),/Требует проверки/);
 assert.match(text(metricTable(view)),/Synthetic inventory days/);assert.doesNotMatch(text(mainTable(view)),/Synthetic inventory days/);
 assert.match(text(view.container),/Коэффициенты за период и остатки на его конец · 2026-03/);
 view.state.payload.source.reports.balance.rows.find(row=>row.row===19).cells[2].fact={value:null,status:'missing'};await settle();
 assert.match(text(find(metricCard(view,19),node=>String(node.props?.class||'').includes('v num'))),/^—/);
 assert.doesNotMatch(text(metricCard(view,19)),/4\s?000,00/);
});

test('money scales alone while percentage, days, coefficient and original author norms stay unchanged',async t=>{
 const view=mount(component,{company,type:'balance',api:async()=>periodPayload()});t.after(view.unmount);await settle();await openHelp(view);view.state.month=3;view.state.unit='k';await settle();
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
 assert.equal(disclosures.length,1);assert.ok(disclosures.every(node=>node.props.open===false));
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
 const view=mount(component,{company,type:'balance',api:async()=>payload});t.after(view.unmount);await settle();await openHelp(view);
 assert.match(text(metricCard(view,36)),/Капитал/);assert.doesNotMatch(text(metricCard(view,36)),/Капитал как разница|Требует проверки/);
 assert.match(text(view.container),/Расхождение баланса/);
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
 assert.match(text(card),/нужно распределение амортизации/);
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
 const view=mount(component,{company,type:'balance',api:async()=>payload});t.after(view.unmount);await settle();await openHelp(view);
 assert.match(text(view.container),/Aniqlashtirish: 1/);
 assert.match(text(metricCard(view,36)),/Kapital/);
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
 const view=mount(component,{company,type:'balance',api:async()=>payload});t.after(view.unmount);await settle();await openHelp(view);
 assert.equal(view.state.issues.length,1);assert.equal(view.state.correctedIssues.length,1);
 assert.match(text(metricCard(view,36)),/Капитал/);
 assert.match(text(view.container),/Исправлено: 1/);assert.match(text(view.container),/Уточнить: 1/);
 view.state.month=2;await settle();
 assert.match(text(metricCard(view,36)),/Капитал/);
 assert.match(text(metricCard(view,36)),/Требует проверки/);
 view.state.month=1;view.state.mode='plan';await settle();
 assert.match(text(metricCard(view,36)),/Капитал/);
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


test('working reports keep source checks and file controls in one closed accessible help panel',async t=>{
 const payload=periodPayload();payload.source.reviewed_as_of='2026-10-11';
 payload.source.calculation={profile:'uzbek_grouped_chart_finmodel_v1'};
 const view=mount(component,{company,canImport:true,type:'pnl',api:async()=>payload});t.after(view.unmount);await settle();
 const help=find(view.container,node=>node.tag==='details'&&String(node.props.class||'').includes('statement-help'));
 const button=find(view.container,node=>node.tag==='button'&&text(node)==='Справка');
 assert.equal(help.props.open,false);assert.equal(button.props['aria-expanded'],false);
 assert.equal(button.props['aria-controls'],help.props.id);
 assert.equal(all(view.container,node=>node.tag==='input'&&node.props.type==='file').length,0);
 assert.doesNotMatch(visibleText(view.container),/Synthetic.xlsx|Замечаний:|Найдено:|Ожидают ОСВ|среза|МСФО|Macro|Mapping|09_Statements|Synthetic equity review|Маржи пересчитываются/);
 assert.match(visibleText(view.container),/Валовая прибыль/);assert.match(visibleText(view.container),/Чистая выручка/);
 button.props.onClick();await settle();
 assert.equal(button.props['aria-expanded'],true);assert.equal(help.props.open,true);assert.equal(help.scrolled,true);
 assert.equal(all(view.container,node=>node.tag==='input'&&node.props.type==='file').length,1);
 assert.match(text(help),/Справка и проверка.*Как читать отчёт.*Данные и обновление.*Источники и полнота данных.*Проверка и исправления/);
 assert.match(text(help),/Synthetic.xlsx/);assert.match(text(help),/соответствие МСФО не подтверждено/);
 assert.match(text(help),/Повторно загружать файл нужно только после появления новых данных/);
 assert.ok(find(help,node=>node.tag==='button'&&text(node).trim()==='Обновить данные'));
 button.props.onClick();await settle();assert.equal(help.props.open,false);
 assert.doesNotMatch(text(help),/Synthetic.xlsx/);assert.equal(all(view.container,node=>node.tag==='input'&&node.props.type==='file').length,0);
});

test('empty-company setup opens help without forcing a technical workbook template or granting import rights',async t=>{
 const view=mount(component,{company,canImport:false,api:async()=>({company_id:2,source:null,sources:[],requirements:[]})});t.after(view.unmount);await settle();
 assert.match(visibleText(view.container),/Нет данных для этой компании/);
 assert.doesNotMatch(visibleText(view.container),/Excel|лист|шаблон|мэппинг/);
 const setup=find(view.container,node=>node.tag==='button'&&text(node)==='Настроить данные');assert.ok(setup);
 setup.props.onClick();await settle();assert.equal(view.state.helpOpen,true);
 assert.equal(all(view.container,node=>node.tag==='input'&&node.props.type==='file').length,0);
 assert.doesNotMatch(text(view.container),/Обновить данные|Загрузить данные/);
 const calls=[];view.app._instance.props.api=async(...args)=>calls.push(args);await settle();
 await view.state.upload({target:{files:[{name:'Synthetic.xlsx',size:10}],value:'file'}});assert.equal(calls.length,0);
});

test('help and ordinary recognized labels follow RU/UZ while missing reasons and balance residual remain visible',async t=>{
 const previous=lang.value;t.after(()=>setLang(previous));setLang('ru');
 const payload=periodPayload();payload.source.calculation={profile:'uzbek_grouped_chart_finmodel_v1'};
 for(const pair of payload.source.reports.balance.rows.find(row=>row.row===36).cells)pair.fact.status='calculated';
 payload.source.reports.balance.rows.find(row=>row.row===38).cells[0].fact=cell('12.34','Mapping residual','calculated');
 const view=mount(component,{company,type:'balance',canImport:true,api:async()=>payload});t.after(view.unmount);await settle();
 assert.match(visibleText(view.container),/Расхождение баланса · 12,34/);
 assert.doesNotMatch(visibleText(view.container),/Synthetic source cash metric|09b_Balance|соответствие МСФО/);
 setLang('uz');await settle();assert.match(visibleText(view.container),/Balans farqi · 12,34/);
 assert.match(visibleText(view.container),/Yordam va tekshirish/);assert.match(visibleText(view.container),/Asosiy ko‘rsatkichlar/);
 await openHelp(view);assert.match(visibleText(view.container),/Maʼlumotlarni yangilash/);
 assert.match(visibleText(view.container),/Ma’lumotlar allaqachon saqlangan/);
});

test('Mapping-only profit uses an honest result label and leaves uncertain revenue blank; source-only comparison is in help',async t=>{
 const payload=periodPayload();payload.source.calculation={profile:'uzbek_grouped_chart_finmodel_v1'};
 const pnl=payload.source.reports.pnl;const revenue=pnl.rows.find(row=>row.row===7),profit=pnl.rows.find(row=>row.row===23);
 const reported=structuredClone(revenue);reported.label='Original reported revenue 9000';reported.cells[0].fact=cell('999999.99');
 pnl.source_actual={status:'reported_source',classification_pending:true,rows:[reported]};
 revenue.cells[0].fact={value:null,status:'requires_assumptions',reason:'actual_revenue_classification_required',source_cache:'999999.99',source_status:'cached'};
 profit.fact_label='Финансовый результат периода по 9900';for(const pair of profit.cells)pair.fact.status='calculated';
 const view=mount(component,{company,type:'pnl',api:async()=>payload});t.after(view.unmount);await settle();
 assert.match(text(metricCard(view,23)),/Финансовый результат/);assert.doesNotMatch(text(metricCard(view,23)),/Чистая прибыль|9900/);
 assert.match(visibleText(view.container),/Нужно подтвердить состав выручки, возвратов и скидок/);
 assert.doesNotMatch(visibleText(view.container),/999.?999,99|Original reported revenue/);
 assert.match(text(metricCard(view,23)),/базы отличаются/);
 await openHelp(view);assert.match(text(find(view.container,node=>node.tag==='details'&&String(node.props.class||'').includes('statement-help'))),/Original reported revenue 9000.*999.?999,99/);
 view.state.setPer('q1');await settle();assert.match(text(metricCard(view,23)),/Финансовый результат/);
 view.state.mode='plan';await settle();assert.match(text(metricCard(view,23)),/Чистая прибыль/);assert.doesNotMatch(text(metricCard(view,23)),/базы отличаются/);
});

test('incoming report selection applies only an available year and never echoes itself; user controls emit the shared context',async t=>{
 const changes=[],calls=[];
 const selection={year:2026,period:'q2',start:4,end:6,unit:'m',mode:'plan',sourceId:8};
 const view=mount(component,{company,type:'pnl',selection,onSelectionChange:value=>changes.push(value),api:async url=>{calls.push(url);return periodPayloadWithId(8);}});t.after(view.unmount);await settle();
 assert.equal(calls[0],'/api/financial-statements?company_id=2&source_id=8');
 assert.equal(view.state.year,2026);assert.equal(view.state.per,'q2');assert.equal(view.state.start,4);assert.equal(view.state.end,6);assert.equal(view.state.unit,'m');assert.equal(view.state.mode,'plan');
 assert.equal(changes.length,1);
 view.state.setPer('q1');await settle();assert.equal(changes.length,2);
 assert.deepEqual(changes[1],{year:2026,period:'q1',start:1,end:3,unit:'m',mode:'plan',sourceId:8});
 view.app._instance.props.selection=structuredClone(changes[1]);await settle();assert.equal(changes.length,2);
 view.app._instance.props.selection={...selection,year:2025,period:'year'};await settle();assert.equal(view.state.year,2026);assert.equal(view.state.per,'q1');assert.equal(changes.length,2);
 view.app._instance.props.selection={...selection,period:'month',start:7,end:7,mode:'actual'};await settle();
 assert.equal(view.state.month,7);assert.equal(view.state.per,'month');assert.equal(view.state.mode,'actual');assert.equal(changes.length,2);
 view.state.unit='k';await settle();assert.deepEqual(changes.at(-1),{year:2026,period:'month',start:7,end:7,unit:'k',mode:'actual',sourceId:8});
});
function periodPayloadWithId(id){const payload=periodPayload();payload.selected=id;payload.source.id=id;payload.sources[0].id=id;return payload;}

const revenuePolicyBasis='gross_excluding_vat_before_returns_discounts';
function revenuePolicyPayload({confirmed=false,allowed=true,revision=0}={}){
 const payload=periodPayload();
 payload.can_confirm_revenue_basis=allowed;
 payload.source.source_sha256='a'.repeat(64);
 payload.source.calculation={profile:'uzbek_grouped_chart_finmodel_v1'};
 payload.source.revenue_policy={basis:confirmed?revenuePolicyBasis:'unconfirmed',status:confirmed?'confirmed':'unconfirmed',revision,
  source_sha256:payload.source.source_sha256,confirmed_at:confirmed?'2026-10-11T12:00:00Z':null};
 const pnl=payload.source.reports.pnl;
 pnl.rows.find(row=>row.row===23).fact_label='Финансовый результат по счетам (9900)';
 for(const number of [7,8,12,13,23,24])for(const pair of pnl.rows.find(row=>row.row===number).cells){
  pair.fact.status='calculated';
  if(!confirmed&&[7,12,13,24].includes(number))pair.fact={value:null,status:'requires_assumptions',reason:'actual_revenue_classification_required'};
 }
 return payload;
}

test('revenue basis starts unconfirmed and can only be explicitly changed inside closed help',async t=>{
 const calls=[],payload=revenuePolicyPayload();
 const view=mount(component,{company,canImport:true,type:'pnl',api:async(...args)=>{calls.push(args);return payload;}});t.after(view.unmount);await settle();
 assert.equal(view.state.revenueBasis,'unconfirmed');assert.equal(view.state.confirmedRevenuePolicy,false);
 assert.doesNotMatch(visibleText(view.container),/Правило выручки|Сохранить правило выручки/);
 await openHelp(view);
 const form=find(view.container,node=>node.tag==='form'&&String(node.props.class||'').includes('statement-revenue-form'));
 assert.ok(form);assert.equal(find(form,node=>node.tag==='button').props.disabled,true);
 assert.match(text(view.container),/Состав выручки не подтверждён/);
 assert.doesNotMatch(text(view.container),/Чистая выручка = выручка 9000/);
 await view.state.saveRevenuePolicy();assert.equal(calls.length,1);
});

test('explicit revenue confirmation uses current company, source hash and revision, reloads reports without reupload and preserves period controls',async t=>{
 const calls=[],editing=[],initial=revenuePolicyPayload({revision:3}),updated=revenuePolicyPayload({confirmed:true,revision:4});
 let reads=0;
 const view=mount(component,{company,canImport:true,type:'pnl',onEditing:value=>editing.push(value),api:async(url,options)=>{
  calls.push({url,options});
  if(options?.method==='POST')return{company_id:2,source_id:1,changed:true,policy:updated.source.revenue_policy};
  return reads++===0?initial:updated;
 }});t.after(view.unmount);await settle();view.state.setPer('q1');view.state.unit='m';view.state.mode='actual';await openHelp(view);
 view.state.revenueBasis=revenuePolicyBasis;await settle();
 await view.state.saveRevenuePolicy();await settle();
 assert.deepEqual(calls.map(item=>item.url),['/api/financial-statements?company_id=2','/api/financial-statements/1/revenue-policy?company_id=2','/api/financial-statements?company_id=2&source_id=1']);
 assert.deepEqual(JSON.parse(calls[1].options.body),{basis:revenuePolicyBasis,expected_revision:3,source_sha256:'a'.repeat(64)});
 assert.deepEqual(calls[1].options.headers,{'Content-Type':'application/json'});
 assert.equal(view.state.confirmedRevenuePolicy,true);assert.equal(view.state.helpOpen,true);assert.equal(view.state.revenuePolicyChanged,false);
 assert.equal(view.state.per,'q1');assert.equal(view.state.unit,'m');assert.equal(view.state.mode,'actual');
 assert.match(text(view.container),/Правило выручки сохранено\. Отчёт пересчитан из Mapping/);
 assert.match(text(mainTable(view)),/Выручка без НДС/);assert.match(text(mainTable(view)),/Рентабельность финансового результата/);
 assert.doesNotMatch(text(mainTable(view)),/Состав выручки уточняется|Чистая маржа/);
 assert.deepEqual(editing,[true,false]);assert.equal(view.state.progress.completed,2);assert.equal(view.state.progress.total,2);
});

test('revenue policy editing requires both returned permission and import rights; another company is never confirmed automatically',async t=>{
 for(const [canImport,allowed] of [[false,true],[true,false],[false,false]]){
  const calls=[],payload=revenuePolicyPayload({allowed});
  const view=mount(component,{company,canImport,type:'pnl',api:async(...args)=>{calls.push(args);return payload;}});t.after(view.unmount);await settle();await openHelp(view);
  assert.equal(view.state.canConfirmRevenueBasis,false);assert.equal(all(view.container,node=>node.tag==='form').length,0);
  view.state.revenueBasis=revenuePolicyBasis;await view.state.saveRevenuePolicy();assert.equal(calls.length,1);assert.equal(view.state.confirmedRevenuePolicy,false);
 }
});

test('a stale revenue revision error leaves the old source and calculations untouched',async t=>{
 const payload=revenuePolicyPayload({revision:7}),calls=[];
 const view=mount(component,{company,canImport:true,type:'pnl',api:async(url,options)=>{calls.push(url);if(options?.method==='POST')throw Error('Synthetic conflict: reload required');return payload;}});t.after(view.unmount);await settle();await openHelp(view);
 view.state.revenueBasis=revenuePolicyBasis;await view.state.saveRevenuePolicy();await settle();
 assert.equal(calls.length,2);assert.equal(view.state.revenuePolicy.revision,7);assert.equal(view.state.confirmedRevenuePolicy,false);
 assert.equal(view.state.source.reports.pnl.rows.find(row=>row.row===7).cells[0].fact.value,null);
 const policySection=find(view.container,node=>String(node.props?.class||'').includes('statement-revenue-policy'));
 assert.match(text(find(policySection,node=>node.props?.role==='alert')),/Synthetic conflict/);assert.equal(view.state.savingRevenueBasis,false);
});

test('a company switch during revenue saving discards late success without loading or showing the previous company',async t=>{
 const payload=revenuePolicyPayload(),other=revenuePolicyPayload();other.company_id=7;other.source.company_id=7;other.can_confirm_revenue_basis=false;
 let complete;const calls=[];
 const view=mount(component,{company,canImport:true,type:'pnl',api:async(url,options)=>{
  calls.push(url);if(options?.method==='POST')return new Promise(resolve=>complete=resolve);
  return url.includes('company_id=7')?other:payload;
 }});t.after(view.unmount);await settle();await openHelp(view);view.state.revenueBasis=revenuePolicyBasis;
 const saving=view.state.saveRevenuePolicy();await settle();assert.equal(view.state.savingRevenueBasis,true);
 view.app._instance.props.company={id:7,name:'Other synthetic company'};await settle();
 complete({company_id:2,source_id:1,changed:true,policy:{basis:revenuePolicyBasis}});await saving;await settle();
 assert.equal(view.state.source.company_id,7);assert.equal(view.state.confirmedRevenuePolicy,false);assert.equal(view.state.revenueBasis,'unconfirmed');
 assert.equal(view.state.revenuePolicyMessage,'');assert.equal(view.state.revenuePolicyError,'');assert.equal(view.state.helpOpen,false);
 assert.deepEqual(calls,['/api/financial-statements?company_id=2','/api/financial-statements/1/revenue-policy?company_id=2','/api/financial-statements?company_id=7']);
});

test('saved revenue policy cannot apply a reloaded different source and the UI explains that refresh failed',async t=>{
 const payload=revenuePolicyPayload(),other=revenuePolicyPayload({confirmed:true,revision:1});other.source.source_sha256='b'.repeat(64);
 let reads=0;
 const view=mount(component,{company,canImport:true,type:'pnl',api:async(url,options)=>options?.method==='POST'?{company_id:2,source_id:1,changed:true}:reads++===0?payload:other});t.after(view.unmount);await settle();await openHelp(view);
 view.state.revenueBasis=revenuePolicyBasis;await view.state.saveRevenuePolicy();await settle();
 assert.equal(view.state.source.source_sha256,'a'.repeat(64));assert.equal(view.state.confirmedRevenuePolicy,false);
 const policySection=find(view.container,node=>String(node.props?.class||'').includes('statement-revenue-policy'));
 assert.match(text(find(policySection,node=>node.props?.role==='alert')),/Правило сохранено, но отчёт не обновлён/);
});

test('RU/UZ translates confirmed revenue basis and financial-result margin without changing the plan or masking unresolved disposal classification',async t=>{
 const previous=lang.value;t.after(()=>setLang(previous));setLang('ru');
 const payload=revenuePolicyPayload({confirmed:true,revision:1}),pnl=payload.source.reports.pnl;
 pnl.rows.find(row=>row.row===18).cells[0].fact={value:null,status:'requires_assumptions',reason:'counterpart_classification_required'};
 const planSnapshot=JSON.stringify(pnl.rows.map(row=>row.cells.map(pair=>pair.plan)));
 const view=mount(component,{company,canImport:true,type:'pnl',api:async()=>payload});t.after(view.unmount);await settle();await openHelp(view);
 assert.match(text(mainTable(view)),/Рентабельность финансового результата/);assert.match(text(mainTable(view)),/Нужно классифицировать результат выбытия активов по счёту 9200/);
 assert.match(text(view.container),/Подтверждено.*Выручка 9000 без НДС, до возвратов и скидок/);
 setLang('uz');await settle();
 assert.match(text(mainTable(view)),/QQSsiz tushum/);assert.match(text(mainTable(view)),/Moliyaviy natija rentabelligi/);
 assert.match(text(mainTable(view)),/9200 hisobvarag‘i/);assert.doesNotMatch(text(mainTable(view)),/counterpart_classification_required|Состав выручки уточняется/);
 assert.match(text(view.container),/Tushum qoidasi/);assert.match(text(view.container),/QQS qayta chegirilmaydi/);
 view.state.mode='plan';await settle();assert.match(text(mainTable(view)),/Чистая маржа|Sof marja/);
 assert.equal(JSON.stringify(pnl.rows.map(row=>row.cells.map(pair=>pair.plan))),planSnapshot);
});

test('changing company resets open help and applies no year from the previous company',async t=>{
 const source=periodPayload();const next=periodPayload();next.company_id=7;next.source.company_id=7;next.source.year=2027;
 for(const report of Object.values(next.source.reports))for(const period of report.periods)period.period=period.period.replace('2026','2027');
 const view=mount(component,{company,selection:{year:2026,period:'q1',start:1,end:3,unit:'k',mode:'actual'},api:async url=>url.includes('company_id=7')?next:source});t.after(view.unmount);await settle();await openHelp(view);
 view.app._instance.props.company={id:7,name:'Different company'};await settle();
 assert.equal(view.state.helpOpen,false);assert.equal(view.state.year,2027);assert.equal(view.state.per,'month');
 assert.doesNotMatch(visibleText(view.container),/Synthetic.xlsx/);
});


test('additional balance reconciliations stay inside help while the amount residual remains on the report',async t=>{
 const payload=periodPayload();payload.source.calculation={profile:'uzbek_grouped_chart_finmodel_v1'};
 for(const pair of payload.source.reports.balance.rows.find(row=>row.row===36).cells)pair.fact.status='calculated';
 const view=mount(component,{company,type:'balance',api:async()=>payload});t.after(view.unmount);await settle();
 assert.equal(all(mainTable(view),node=>node.tag==='tr'&&Number(node.props['data-source-row'])===56).length,0);
 assert.equal(all(mainTable(view),node=>node.tag==='tr'&&Number(node.props['data-source-row'])===38).length,1);
 assert.match(visibleText(view.container),/Расхождение баланса/);assert.doesNotMatch(visibleText(view.container),/Сверки исходной модели/);
 await openHelp(view);assert.match(text(find(view.container,node=>node.tag==='details'&&String(node.props.class||'').includes('statement-help'))),/Сверки исходной модели.*Synthetic equity reconciliation/);
});

test('a period awaiting all facts has a plain data message and leaves budget values and the import ACL intact',async t=>{
 const payload=periodPayload();payload.source.coverage.slice(3,6).forEach(item=>item.status='missing');
 const view=mount(component,{company,type:'pnl',api:async()=>payload});t.after(view.unmount);await settle();view.state.setPer('q2');await settle();
 assert.match(visibleText(view.container),/Нет фактических данных за этот период/);
 assert.match(text(metricCard(view,7)),/План: 300,00/);assert.match(text(find(metricCard(view,7),node=>String(node.props?.class||'').includes('v num'))),/^—/);
 assert.equal(all(view.container,node=>node.tag==='input'&&node.props.type==='file').length,0);
 view.state.mode='plan';await settle();assert.doesNotMatch(visibleText(view.container),/Нет фактических данных за этот период/);
});


test('initial and explicitly selected source loads publish their authoritative context once, while restoration never loops',async t=>{
 const calls=[],changes=[];
 const payload=id=>{const value=periodPayloadWithId(id);value.sources=[{id:1,filename:'A.xlsx'},{id:8,filename:'B.xlsx'}];return value;};
 const view=mount(component,{company,type:'pnl',api:async url=>{calls.push(url);return payload(url.includes('source_id=8')?8:1);},onSelectionChange:value=>changes.push(value)});t.after(view.unmount);await settle();
 assert.equal(changes.length,1);assert.equal(changes[0].sourceId,1);assert.equal(changes[0].period,'month');
 view.app._instance.props.selection=structuredClone(changes.at(-1));await settle();assert.equal(calls.length,1);assert.equal(changes.length,1);
 await view.state.load(8);await settle();assert.equal(changes.length,2);assert.equal(changes.at(-1).sourceId,8);assert.equal(view.state.selected,'8');
 view.app._instance.props.selection=structuredClone(changes.at(-1));await settle();assert.equal(calls.length,2);assert.equal(changes.length,2);
 view.app._instance.props.selection={...changes.at(-1),sourceId:1};await settle();assert.equal(view.state.selected,'1');assert.equal(calls.length,3);assert.equal(changes.length,3);assert.equal(changes.at(-1).sourceId,1);
});


test('derived PNL turnover rows do not advertise unconfirmed net revenue, allocated depreciation or interest-only expense',async t=>{
 const payload=periodPayload();payload.source.calculation={profile:'uzbek_grouped_chart_finmodel_v1'};
 const pnl=payload.source.reports.pnl;
 for(const [number,label] of [[8,'Оборот счёта 9000'],[17,'Кредитовый оборот счёта 0200'],[20,'Дебетовый оборот счёта 9600']]){
  let row=pnl.rows.find(item=>item.row===number);
  if(!row){row=structuredClone(pnl.rows[0]);row.row=number;row.key='turnover-'+number;pnl.rows.push(row);}
  row.label=number===8?'Выручка':number===17?'Амортизация':'Процентные расходы';row.fact_label=label;
  for(const pair of row.cells)pair.fact.status='calculated';
 }
 const view=mount(component,{company,type:'pnl',api:async()=>payload});t.after(view.unmount);await settle();view.state.setPer('q1');await settle();
 const body=visibleText(mainTable(view));assert.match(body,/Оборот выручки/);assert.match(body,/Оборот амортизационного счёта/);assert.match(body,/Финансовые расходы/);
 assert.match(body,/Состав выручки уточняется/);assert.match(body,/Распределение амортизации уточняется/);assert.match(body,/Включены все финансовые расходы/);
 assert.doesNotMatch(body,/счёта (?:9000|0200|9600)|Процентные расходы/);
 assert.match(text(mainTable(view)),/Кредитовый оборот счёта 0200/);
 view.state.mode='plan';await settle();assert.match(visibleText(mainTable(view)),/Процентные расходы/);assert.doesNotMatch(visibleText(mainTable(view)),/Оборот выручки|Распределение амортизации уточняется/);
});


test('missing required account reasons do not claim a missing profit account when the financial result is calculated, in RU and UZ',async t=>{
 const previous=lang.value;t.after(()=>setLang(previous));setLang('ru');
 const payload=periodPayload();payload.source.calculation={profile:'uzbek_grouped_chart_finmodel_v1'};
 const pnl=payload.source.reports.pnl,other=pnl.rows.find(row=>row.row===14),profit=pnl.rows.find(row=>row.row===23);
 other.label='Прочие операционные доходы';
 const tax=structuredClone(other);tax.row=22;tax.key='missing-tax-account';tax.label='Налог на прибыль';pnl.rows.push(tax);
 for(const pair of other.cells)pair.fact={value:null,status:'unavailable',reason:'absent_profit_account'};
 for(const pair of tax.cells)pair.fact={value:null,status:'unavailable',reason:'missing_account'};
 profit.fact_label='Финансовый результат периода по 9900';for(const pair of profit.cells)pair.fact.status='calculated';
 const original=structuredClone(payload);
 const view=mount(component,{company,type:'pnl',api:async()=>payload});t.after(view.unmount);await settle();
 const affected=number=>find(mainTable(view),node=>node.tag==='tr'&&Number(node.props['data-source-row'])===number);
 for(const number of [14,22])assert.match(visibleText(affected(number)),/В ОСВ не найден необходимый счёт\./);
 assert.doesNotMatch(visibleText(mainTable(view)),/не найден счёт финансового результата/);
 assert.match(text(metricCard(view,23)),/Финансовый результат.*10,00/);
 setLang('uz');await settle();
 for(const number of [14,22])assert.match(visibleText(affected(number)),/Aylanma-saldo qaydnomasida zarur hisobvaraq topilmadi\./);
 assert.doesNotMatch(visibleText(mainTable(view)),/moliyaviy natija hisobvarag‘i topilmadi/);
 assert.deepEqual(payload,original);
});
