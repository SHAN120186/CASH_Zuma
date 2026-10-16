<script setup>
import {ref,computed,watch,onMounted,onUnmounted,nextTick} from 'vue';
import {tx,N_,intlLocale,formatDate} from './i18n/index.js';
import AppIcon from './AppIcon.vue';
import OperationProgress from './OperationProgress.vue';
import {createOperationProgress} from './operationProgress.js';
import {normalizeReportSelection} from './reportContext.js';
import {statementUrl,statementFileProblem,statementDeviation,statementNumber,statementStatuses,sourceScopeMatches,aggregateStatementRow,statementScaledNumber,statementScaledDeviation,statementReasonLabels,statementDisplayLabels,cellNumber} from './financialStatements.js';

const props=defineProps({api:{type:Function,required:true},company:{type:Object,required:true},
  type:{type:String,default:'pnl'},canImport:{type:Boolean,default:false},canDownload:{type:Boolean,default:false},refresh:{type:Number,default:0},selection:{type:Object,default:null}});
const emit=defineEmits(['editing','selection-change']);
const payload=ref(null),selected=ref(''),month=ref(null),loading=ref(false),uploading=ref(false),error=ref('');
const year=ref(null),per=ref('month'),start=ref(1),end=ref(1),mode=ref('compare'),unit=ref('x'),showMonths=ref(false),collapsed=ref([]);
const helpOpen=ref(false),helpPanel=ref(null),uploadInput=ref(null),managementHelpPanel=ref(null);
const revenueBasis=ref('unconfirmed'),savingRevenueBasis=ref(false),revenuePolicyError=ref(''),revenuePolicyMessage=ref('');
const confirmedRevenueBasis='gross_excluding_vat_before_returns_discounts';
const helpId=computed(()=>'statement-help-'+props.company.id+'-'+props.type);
const monthNames=[N_('Январь'),N_('Февраль'),N_('Март'),N_('Апрель'),N_('Май'),N_('Июнь'),N_('Июль'),N_('Август'),N_('Сентябрь'),N_('Октябрь'),N_('Ноябрь'),N_('Декабрь')];
const periodOptions=[['year',N_('Год')],['q1',N_('I кв.')],['q2',N_('II кв.')],['q3',N_('III кв.')],['q4',N_('IV кв.')],['custom',N_('Свой период')],['month',N_('Месяц')]];
const units={x:{label:N_('Единицы'),scale:1},k:{label:N_('Тыс.'),scale:1000},m:{label:N_('Млн'),scale:1000000}};
const progress=ref({active:false,completed:0,total:0,label:'',error:''});
const progressController=createOperationProgress(value=>progress.value=value);
let active=true,requestId=0,lastSelection='';
const source=computed(()=>payload.value?.source);
const revenuePolicy=computed(()=>source.value?.revenue_policy || {basis:'unconfirmed',status:'unconfirmed',revision:0});
const confirmedRevenuePolicy=computed(()=>revenuePolicy.value.status==='confirmed'&&revenuePolicy.value.basis===confirmedRevenueBasis&&source.value?.source_sha256&&revenuePolicy.value.source_sha256===source.value.source_sha256);
const canConfirmRevenueBasis=computed(()=>props.canImport&&payload.value?.can_confirm_revenue_basis===true&&friendlyProfile.value&&Boolean(source.value?.source_sha256));
const revenuePolicyChanged=computed(()=>revenueBasis.value!==revenuePolicy.value.basis);
const report=computed(()=>source.value?.reports?.[props.type]);
const profileUnrecognized=computed(()=>source.value?.calculation?.reason==='profile_unrecognized'||report.value?.calculation?.reason==='profile_unrecognized');
const friendlyProfile=computed(()=>!profileUnrecognized.value&&(source.value?.calculation?.profile==='uzbek_grouped_chart_finmodel_v1'||report.value?.calculation?.profile==='uzbek_grouped_chart_finmodel_v1'));
const reportTitle=computed(()=>props.type==='balance'?N_('Баланс'):N_('Прибыли и убытки (P&L)'));
const periods=computed(()=>report.value?.periods || []);
const years=computed(()=>[...new Set([...periods.value.map(item=>/^\d{4}-\d{2}$/.test(item.period||'')?Number(item.period.slice(0,4)):null),source.value?.year].filter(value=>Number.isInteger(value)&&value>=2000&&value<=2100))].sort((a,b)=>a-b));
const rangeError=computed(()=>per.value==='custom'&&Number(start.value)>Number(end.value)?N_('Начало периода должно быть не позже конца. Исправьте диапазон месяцев.'):year.value!=null&&!years.value.includes(Number(year.value))?N_('Выбранный год отсутствует в исходной книге.'):'');
const indices=computed(()=>Array.from({length:Math.max(0,Number(per.value==='month'?month.value:end.value)-Number(per.value==='month'?month.value:start.value)+1)},(_,i)=>i+Number(per.value==='month'?month.value:start.value)).filter(value=>value>=1&&value<=12));
const range=computed(()=>({startMonth:Number(per.value==='month'?month.value:start.value),endMonth:Number(per.value==='month'?month.value:end.value),year:year.value,type:props.type,coverage:source.value?.coverage,pnlReport:source.value?.reports?.pnl,profileUnrecognized:profileUnrecognized.value}));
const scale=computed(()=>units[unit.value].scale);
const unitCaption=computed(()=>source.value?.unit_status==='unknown'?tx(unit.value==='m'?N_('млн исходных единиц'):unit.value==='k'?N_('тыс. исходных единиц'):N_('Исходные единицы')):(unit.value==='m'?tx('млн')+' ':unit.value==='k'?tx('тыс.')+' ':'')+tx(source.value?.unit||source.value?.currency||N_('Не указана')));
const selectedPeriodLabel=computed(()=>rangeError.value?tx('Период не определён'):per.value==='year'?tx('{year} год',{year:year.value??tx('Не указан')}):per.value.startsWith('q')?tx('{quarter} квартал {year}',{quarter:per.value.slice(1),year:year.value??tx('Не указан')}):indices.value.length===1?periodLabel(periods.value.find(item=>Number(item.month)===indices.value[0])||{month:indices.value[0]}):tx('{from}–{to} · {year}',{from:tx(monthNames[indices.value[0]-1]),to:tx(monthNames[indices.value.at(-1)-1]),year:year.value??tx('Не указан')}));
const subcolumns=computed(()=>mode.value==='compare'?['plan','fact','deviation','percent']:[mode.value==='plan'?'plan':'fact']);
const columns=computed(()=>{
 const total={key:'total',label:indices.value.length>1?(props.type==='pnl'?tx('За период'):tx('На конец периода')):selectedPeriodLabel.value};
 if(!showMonths.value||indices.value.length<=1)return [total];
 return [...indices.value.map(value=>({key:'month-'+value,month:value,label:periodLabel(periods.value.find(item=>Number(item.month)===value)||{month:value})})),total];
});
const columnLabels={plan:N_('План'),fact:N_('Факт'),deviation:N_('Отклонение'),percent:N_('Отклонение, %')};
const normValue=row=>typeof row.norm==='object'?row.norm?.status==='author_guideline'?row.norm.value:null:row.norm;
const normSource=row=>row.norm?.source || row.norm_source;
const hasNorms=computed(()=>report.value?.rows?.some(row=>row.norm!=null));
const sourceItems=computed(()=>payload.value?.sources || []);
const requirements=computed(()=>source.value?.requirements || payload.value?.requirements || []);
const issues=computed(()=>(source.value?.issues || []).filter(item=>item.resolved_in_calculation!==true));
const correctedIssues=computed(()=>(source.value?.issues || []).filter(item=>item.resolved_in_calculation===true));
const requirementLabels={found:N_('Найдено в источнике'),needs_clarification:N_('Нужно уточнить')};
const foundRequirements=computed(()=>requirements.value.filter(item=>item.status==='found'));
const clarificationRequirements=computed(()=>requirements.value.filter(item=>item.status==='needs_clarification'));
const pendingCoverage=computed(()=>(source.value?.coverage||[]).filter(item=>['missing','future'].includes(item.status)));
const independentCapital=computed(()=>{
 if(profileUnrecognized.value)return false;
 const capital=report.value?.rows?.find(row=>Number(row.row)===36);
 return Boolean(capital && aggregateStatementRow(report.value,capital,range.value)[mode.value==='plan'?'plan':'fact']?.status==='calculated');
});
const selectedCoverage=computed(()=>(source.value?.coverage||[]).filter(item=>indices.value.includes(Number(item.month))));
const coverageLabels={missing:N_('Ожидается ОСВ за месяц'),available:N_('ОСВ загружена'),future:N_('Будущий период'),invalid:N_('Проверьте даты операций')};
const amount=value=>statementNumber(value,'amount',intlLocale.value);
const formatCell=(row,cell)=>statementScaledNumber(cell,row,scale.value,intlLocale.value);
const stateLabel=cell=>tx(statementStatuses[cell?.status] || N_('Нет данных'));
const reasonLabel=cell=>cell?.reason?tx(statementReasonLabels[cell.reason]||cell.reason):'';
const factIsCalculated=row=>{
 const cell=rowCells(row).fact;
 return cell?.status==='calculated'||cell?.status==='aggregated'&&cell.provenance?.length>0&&cell.provenance.every(origin=>origin.status==='calculated');
};
const managementMethod=computed(()=>report.value?.calculation?.management_template);
const managementRow=row=>friendlyProfile.value&&props.type==='pnl'&&[16,18,21].includes(Number(row?.row))&&
  managementMethod.value?.status==='recognized'&&managementMethod.value?.profile==='finmodel_actual_operating_subtotal_v1';
const managementCaption=row=>{
 if(mode.value==='plan'||!managementRow(row)||!factIsCalculated(row))return false;
 const cell=rowCells(row).fact;
 return cellNumber(cell)!==null&&cell.calculation_basis==='management_template'&&cell.template_profile==='finmodel_actual_operating_subtotal_v1';
};
const managementMethodAvailable=computed(()=>(report.value?.rows||[]).some(managementRow));
const rowLabel=row=>!profileUnrecognized.value && row?.fact_label && mode.value!=='plan' && factIsCalculated(row) ? row.fact_label : row?.source_label || row?.label;
const displayRowLabel=row=>{
 if(!friendlyProfile.value)return rowLabel(row);
 if(props.type==='pnl'&&mode.value!=='plan'&&factIsCalculated(row)){
  if(Number(row.row)===8)return confirmedRevenuePolicy.value?N_('Выручка без НДС'):N_('Оборот выручки');
  if(Number(row.row)===17)return N_('Оборот амортизационного счёта');
  if(Number(row.row)===20)return N_('Финансовые расходы');
  if(Number(row.row)===23&&mappingProfit.value)return N_('Финансовый результат');
  if(Number(row.row)===24&&mappingProfit.value)return N_('Рентабельность финансового результата');
 }
 if(props.type==='balance'&&rowCells(row).fact?.status==='calculated'&&mode.value!=='plan'){
  if(Number(row.row)===52)return N_('Нераспределённая прибыль прошлых лет');
  if(Number(row.row)===53)return N_('Сальдо финансового результата (9900)');
  if(Number(row.row)===56)return N_('Остаточное расхождение баланса');
 }
 return statementDisplayLabels[props.type]?.[Number(row.row)]||rowLabel(row);
};
const visibleReasons=row=>{
 const reasons=(mode.value==='compare'?['plan','fact']:[primarySide.value]).map(side=>{
  const cell=rowCells(row)[side];return cellNumber(cell)===null?reasonLabel(cell):'';
 });
 if(friendlyProfile.value&&props.type==='pnl'&&mode.value!=='plan'&&factIsCalculated(row)){
  if(Number(row.row)===8)reasons.push(tx(confirmedRevenuePolicy.value?N_('До возвратов и скидок. НДС повторно не вычитается.'):N_('Состав выручки уточняется.')));
  if(Number(row.row)===17)reasons.push(tx('Распределение амортизации уточняется.'));
  if(Number(row.row)===20)reasons.push(tx('Включены все финансовые расходы.'));
 }
 return [...new Set(reasons.filter(Boolean))];
};
const periodLabel=period=>period.period || tx('Месяц {n} · дата не подтверждена',{n:period.month});
const dateLabel=value=>formatDate(value,{timeZone:'Asia/Tashkent',year:'numeric',month:'2-digit',day:'2-digit'}) || '';
const rowCells=row=>aggregateStatementRow(report.value,row,range.value);
const columnCells=(row,column)=>column.month==null?rowCells(row):aggregateStatementRow(report.value,row,{...range.value,startMonth:column.month,endMonth:column.month});
const deviation=(row,cells=rowCells(row))=>statementDeviation(cells.plan,cells.fact,row.kind);
const displayDeviation=(row,cells=rowCells(row))=>statementScaledDeviation(cells.plan,cells.fact,row,scale.value,intlLocale.value);
const displayPercent=(row,cells=rowCells(row))=>deviation(row,cells).percent===null?'—':amount(deviation(row,cells).percent)+'%';
const primarySide=computed(()=>mode.value==='plan'?'plan':'fact');
const mappingProfit=computed(()=>!profileUnrecognized.value&&props.type==='pnl'&&report.value?.rows?.some(row=>Number(row.row)===23&&row.fact_label?.includes('9900')&&factIsCalculated(row)));
const cardDefinitions=computed(()=>profileUnrecognized.value?[]:props.type==='pnl'?[{row:7,title:N_('Чистая выручка')},{row:12,title:N_('Валовая прибыль')},{row:16,title:'EBITDA'},{row:18,title:'EBIT'},{row:23,title:mappingProfit.value&&mode.value!=='plan'?N_('Финансовый результат'):N_('Чистая прибыль')}]:[{row:19,title:N_('Активы')},{row:35,title:N_('Обязательства')},{row:36,title:N_('Капитал'),review:!independentCapital.value},{row:8,title:N_('Деньги')}]);
const cards=computed(()=>cardDefinitions.value.map(item=>{const row=report.value?.rows?.find(row=>Number(row.row)===item.row);return {...item,sourceRow:row,cells:row?rowCells(row):{plan:null,fact:null}};}));
const groups=computed(()=>{
 if(profileUnrecognized.value)return [{key:'source',title:N_('Строки исходного отчёта'),totals:[],rows:report.value?.rows||[]}];
 const definitions=props.type==='pnl'?[['gross',N_('Выручка и валовая прибыль'),7,13,[12,13]],['operating',N_('Операционная прибыль'),14,18,[16,18]],['net',N_('Финансовые расходы и чистая прибыль'),19,24,[23,24]]]:[['assets',N_('Активы'),7,19,[19]],['liabilities',N_('Обязательства'),20,35,[35]],['capital',N_('Капитал и контроль баланса'),36,38,[36,37,38]]];
 return definitions.map(([key,title,from,to,totals])=>({key,title,totals,rows:(report.value?.rows||[]).filter(row=>{const value=Number(row.row);return key==='capital'?value>=32&&value<=38&&value!==35:key==='liabilities'?value>=20&&value<=35&&!(value>=32&&value<=34):value>=from&&value<=to;})})).filter(group=>group.rows.length);
});
const auditRows=computed(()=>!profileUnrecognized.value&&props.type==='balance'?(report.value?.rows||[]).filter(row=>Number(row.row)>=39&&Number(row.row)<=61):[]);
const noFactData=computed(()=>source.value&&mode.value!=='plan'&&selectedCoverage.value.length>0&&selectedCoverage.value.every(item=>item.status!=='available'));
const kpiRows=computed(()=>!profileUnrecognized.value&&props.type==='balance'?(report.value?.rows||[]).filter(row=>Number(row.row)>=62&&Number(row.row)<=81):[]);
const groupRows=group=>collapsed.value.includes(group.key)?group.rows.filter(row=>group.totals.includes(Number(row.row))):group.rows;
const sourcesOf=cell=>cell?.provenance?.length?cell.provenance:cell?[cell]:[];
const sourceDetails=origin=>origin?.provenance?.length?origin.provenance:[];
const hasOriginalCache=origin=>origin && ['calculated','requires_allocation','requires_assumptions','unavailable'].includes(origin.status) && Object.hasOwn(origin,'source_cache');
const originalCache=origin=>({value:origin.source_cache,status:origin.source_status||'cached'});
const balanceResidual=computed(()=>props.type==='balance'&&independentCapital.value?report.value?.rows?.find(row=>Number(row.row)===38):null);
const requirementEvidence=(requirement,value)=>{
 if(value.startsWith(N_('Счета Mapping:')))return tx('Счета Mapping: {accounts}',{accounts:value.slice(value.indexOf(':')+1).trim()});
 if(requirement.key==='plan'&&source.value?.plan_source?.status==='cached_snapshot')return tx('{sheet}: сохранённых результатов — {n}; снимок {date}',{sheet:source.value.plan_source.sheet,n:source.value.plan_source.cached_cell_count,date:dateLabel(source.value.plan_source.as_of)});
 return tx(value);
};
const missingFieldLabel=field=>field.key==='pending_periods'?tx('Ещё не загружены: {periods}',{periods:(field.title||'').split(':').slice(1).join(':').trim()}):tx(field.title||N_('Дополнительные исходные данные'));
function toggleGroup(key){collapsed.value=collapsed.value.includes(key)?collapsed.value.filter(value=>value!==key):[...collapsed.value,key];}
async function openHelp(){helpOpen.value=true;await nextTick();helpPanel.value?.scrollIntoView({block:'start',behavior:'smooth'});}
async function openManagementHelp(){helpOpen.value=true;await nextTick();managementHelpPanel.value?.scrollIntoView({block:'start',behavior:'smooth'});}
function toggleHelp(){if(helpOpen.value)helpOpen.value=false;else openHelp();}
function collapseAll(){collapsed.value=[...groups.value.map(group=>group.key),...(kpiRows.value.length?['kpi']:[])];}
function setPer(value){const previous=[...indices.value];per.value=value;if(value==='year'){start.value=1;end.value=12;}else if(value.startsWith('q')){const quarter=Number(value.slice(1));start.value=(quarter-1)*3+1;end.value=quarter*3;}else if(value==='custom'){start.value=previous[0]||Number(month.value)||1;end.value=previous.at(-1)||start.value;}}
function currentSelection(){return normalizeReportSelection({year:year.value,period:per.value,start:per.value==='month'?month.value:start.value,end:per.value==='month'?month.value:end.value,unit:unit.value,mode:mode.value,sourceId:Number(selected.value)||null});}
function publishSelection(){if(source.value){const value=currentSelection();lastSelection=JSON.stringify(value);emit('selection-change',value);}}
function applySelection(value=props.selection){
 if(!value||!source.value||!years.value.includes(Number(value.year)))return;
 const next=normalizeReportSelection(value,currentSelection());
 year.value=next.year;per.value=next.period;start.value=next.start;end.value=next.end;
 if(next.period==='month')month.value=next.start;
 unit.value=next.unit;mode.value=next.mode;
 lastSelection=JSON.stringify(currentSelection());
}
watch(()=>props.selection,value=>{
 const id=value?.sourceId;
 if(id!=null&&source.value&&String(id)!==selected.value&&sourceItems.value.some(item=>String(item.id)===String(id))){load(id);return;}
 applySelection(value);
},{deep:true});
watch([year,per,start,end,month,unit,mode],()=>{
 if(!source.value||loading.value||uploading.value)return;
 const value=currentSelection(),key=JSON.stringify(value);
 if(key!==lastSelection){lastSelection=key;emit('selection-change',value);}
});
let sourceScope='';
function resetMonth() {
  const nextScope=props.company.id+':'+source.value?.id;
  if(sourceScope!==nextScope){sourceScope=nextScope;year.value=years.value.includes(source.value?.year)?source.value.year:years.value[0]??null;per.value='month';collapsed.value=[];showMonths.value=false;helpOpen.value=false;revenuePolicyError.value='';revenuePolicyMessage.value='';}
  if(!years.value.includes(Number(year.value)))year.value=years.value[0]??null;
  if(!periods.value.some(item=>Number(item.month)===Number(month.value)))month.value=periods.value[0]?.month??null;
  applySelection();
  lastSelection=JSON.stringify(currentSelection());
}
function assertScope(value,companyId) {
  if(!sourceScopeMatches(value,companyId)) throw Error(N_('Источник относится к другой компании. Обновите страницу.'));
}
async function load(sourceId=selected.value) {
  if(savingRevenueBasis.value)return;
  const id=++requestId,companyId=props.company.id;
  loading.value=true;error.value='';payload.value=null;
  const ticket=progressController.begin(N_('Загружаем проверку исходных данных'),1);
  try{
    const value=await props.api(statementUrl(companyId,sourceId));
    if(!active || id!==requestId || props.company.id!==companyId)return;
    assertScope(value,companyId);payload.value=value;selected.value=String(value.selected ?? value.source?.id ?? '');resetMonth();publishSelection();
    progressController.complete(ticket,N_('Исходные данные загружены'));
  }catch(e){if(active&&id===requestId&&props.company.id===companyId&&e.name!=='AbortError'){error.value=e.message;progressController.fail(ticket,e);}}
  finally{if(active&&id===requestId&&props.company.id===companyId)loading.value=false;}
}
async function upload(event) {
  const file=event.target.files?.[0];event.target.value='';
  if(!file || uploading.value || savingRevenueBasis.value || !props.canImport)return;
  const problem=statementFileProblem(file);if(problem){error.value=problem;return;}
  const id=++requestId,companyId=props.company.id;
  uploading.value=true;loading.value=false;error.value='';
  const ticket=progressController.begin(N_('Читаем исходную книгу и проверяем данные'),2);
  try{
    const result=await props.api('/api/financial-statements/upload?company_id='+encodeURIComponent(companyId),{
      method:'POST',headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(file.name)},body:file});
    if(!active||id!==requestId||props.company.id!==companyId)return;
    assertScope(result,companyId);
    const sourceId=result.id ?? result.source_id ?? result.source?.id;
    if(sourceId==null)throw Error(N_('Сервер не подтвердил сохранение исходной книги. Обновите список источников.'));
    progressController.advance(ticket,1,N_('Книга сохранена. Получаем результаты проверки'));
    const value=await props.api(statementUrl(companyId,sourceId));
    if(!active||id!==requestId||props.company.id!==companyId)return;
    assertScope(value,companyId);payload.value=value;selected.value=String(value.selected ?? sourceId);resetMonth();publishSelection();
    progressController.complete(ticket,N_('Проверка исходной книги загружена'));
  }catch(e){if(active&&id===requestId&&props.company.id===companyId&&e.name!=='AbortError'){error.value=e.message;progressController.fail(ticket,e);}}
  finally{if(active&&id===requestId&&props.company.id===companyId)uploading.value=false;}
}
async function saveRevenuePolicy() {
  if(!canConfirmRevenueBasis.value||!revenuePolicyChanged.value||savingRevenueBasis.value||loading.value||uploading.value)return;
  const id=++requestId,companyId=props.company.id,sourceId=source.value.id,sha256=source.value.source_sha256;
  const basis=revenueBasis.value,revision=revenuePolicy.value.revision;
  savingRevenueBasis.value=true;revenuePolicyError.value='';revenuePolicyMessage.value='';
  const ticket=progressController.begin(N_('Сохраняем правило выручки'),2);
  let saved=false;
  try{
    const result=await props.api('/api/financial-statements/'+encodeURIComponent(sourceId)+'/revenue-policy?company_id='+encodeURIComponent(companyId),{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({basis,expected_revision:revision,source_sha256:sha256})});
    if(!active||id!==requestId||props.company.id!==companyId)return;
    assertScope(result,companyId);
    if(String(result.source_id)!==String(sourceId))throw Error(N_('Источник относится к другой компании. Обновите страницу.'));
    saved=true;progressController.advance(ticket,1,N_('Правило сохранено. Обновляем расчёт'));
    const value=await props.api(statementUrl(companyId,sourceId));
    if(!active||id!==requestId||props.company.id!==companyId)return;
    assertScope(value,companyId);
    if(String(value.source?.id)!==String(sourceId)||value.source?.source_sha256!==sha256)throw Error(N_('Источник изменился. Обновите страницу и проверьте выбранную книгу.'));
    payload.value=value;selected.value=String(sourceId);resetMonth();publishSelection();
    revenuePolicyMessage.value=N_('Правило выручки сохранено. Отчёт пересчитан из Mapping.');
    progressController.complete(ticket,N_('Расчёт выручки обновлён'));
  }catch(e){if(active&&id===requestId&&props.company.id===companyId&&e.name!=='AbortError'){revenuePolicyError.value=saved?N_('Правило сохранено, но отчёт не обновлён. Обновите страницу.'):e.message;progressController.fail(ticket,e);}}
  finally{if(active&&id===requestId&&props.company.id===companyId)savingRevenueBasis.value=false;}
}
watch(()=>[source.value?.id,source.value?.source_sha256,revenuePolicy.value.basis,revenuePolicy.value.revision],()=>{revenueBasis.value=revenuePolicy.value.basis;});
watch([uploading,savingRevenueBasis],()=>emit('editing',uploading.value||savingRevenueBasis.value));
watch(()=>props.company.id,()=>{requestId++;selected.value='';month.value=null;payload.value=null;helpOpen.value=false;uploading.value=false;savingRevenueBasis.value=false;revenuePolicyError.value='';revenuePolicyMessage.value='';progressController.reset();load('');});
watch(()=>props.type,resetMonth);
watch(()=>props.refresh,()=>{if(!uploading.value&&!savingRevenueBasis.value)load();});
onMounted(()=>load(props.selection?.sourceId??''));
onUnmounted(()=>{active=false;requestId++;progressController.reset();emit('editing',false);});
</script>

<template>
  <div class="financial-statements">
    <div class="card tools statement-tools">
      <div class="tl-row">
        <span class="pill statement-company">{{company.name}}</span>
        <label class="fld">{{tx('Год')}} <select v-model="year" :aria-label="tx('Год отчёта')" :disabled="!years.length"><option v-if="!years.length" :value="null">{{tx('Не указан')}}</option><option v-for="value in years" :key="value" :value="value">{{value}}</option></select></label>
        <div class="seg" role="group" :aria-label="tx('Вид отчёта')"><button v-for="item in [['actual',tx('Факт')],['plan',tx('План')],['compare',tx('План-факт')]]" :key="item[0]" :class="{on:mode===item[0]}" :aria-pressed="mode===item[0]" @click="mode=item[0]">{{item[1]}}</button></div>
        <div class="seg" role="group" :aria-label="tx('Единицы измерения')"><button v-for="(value,key) in units" :key="key" :class="{on:unit===key}" :aria-pressed="unit===key" @click="unit=key">{{tx(value.label)}}</button></div>
        <div class="grow"><button type="button" class="secondary tiny" :aria-controls="helpId" :aria-expanded="helpOpen" @click="toggleHelp">{{tx('Справка')}}</button><button class="secondary tiny" :disabled="loading||uploading" @click="load(selected)"><AppIcon name="refresh"/> {{tx('Обновить')}}</button></div>
      </div>
      <div class="tl-row">
        <div class="chips" role="group" :aria-label="tx('Период')"><button v-for="item in periodOptions" :key="item[0]" class="chip" :class="{on:per===item[0]}" :aria-pressed="per===item[0]" @click="setPer(item[0])">{{tx(item[1])}}</button></div>
        <label v-if="per==='month'" class="fld">{{type==='balance'?tx('Месяц баланса'):tx('Месяц P&L')}} <select v-model="month"><option v-for="period in periods" :key="period.month" :value="period.month">{{periodLabel(period)}}</option></select></label>
        <template v-if="per==='custom'"><label class="fld">{{tx('с')}} <select v-model="start" :aria-label="tx('Первый месяц периода')"><option v-for="(name,index) in monthNames" :key="index" :value="index+1">{{tx(name)}}</option></select></label><label class="fld">{{tx('по')}} <select v-model="end" :aria-label="tx('Последний месяц периода')"><option v-for="(name,index) in monthNames" :key="index" :value="index+1">{{tx(name)}}</option></select></label></template>
        <button type="button" class="secondary tiny" :disabled="indices.length<=1" :aria-pressed="showMonths" @click="showMonths=!showMonths">{{showMonths?tx('Скрыть месяцы'):tx('Показать месяцы')}}</button>
        <div class="grow"><button class="secondary tiny" @click="collapseAll">{{tx('Свернуть разделы')}}</button><button class="secondary tiny" @click="collapsed=[]">{{tx('Развернуть')}}</button></div>
      </div>
    </div>
    <p v-if="rangeError" class="error" role="alert">{{tx(rangeError)}}</p>
    <OperationProgress v-bind="progress"/>
    <p v-if="error" class="error" role="alert">{{tx(error)}}</p>
    <div v-if="!loading&&!error&&!source" class="card statement-empty">
      <p>{{tx('Нет данных для этой компании.')}}</p>
      <button type="button" class="secondary" :aria-controls="helpId" @click="openHelp">{{tx('Настроить данные')}}</button>
    </div>
    <p v-if="profileUnrecognized" class="sub statement-profile-note" role="note">{{tx('Формат модели не распознан: показаны строки исходника без расчётных карточек и KPI.')}}</p>
    <div v-if="noFactData&&!loading" class="card statement-empty"><p>{{tx('Нет фактических данных за этот период.')}}</p><button type="button" class="secondary" :aria-controls="helpId" @click="openHelp">{{tx('Проверить данные')}}</button></div>
    <template v-if="source&&report?.rows?.length">
      <div v-if="cards.length" class="sumrow statement-cards"><div v-for="card in cards" :key="card.row" class="card tile statement-kpi-card" :class="{'needs-review':card.review}" :data-source-row="card.row"><div class="k"><span>{{tx(card.title)}}</span><span class="pill" :class="mode==='plan'?'plan':'fact'">{{mode==='compare'?tx('План-факт'):mode==='plan'?tx('План'):tx('Факт')}}</span></div><div class="v num">{{formatCell(card.sourceRow||{kind:'amount'},card.cells[primarySide])}}<small>{{unitCaption}}</small></div><button v-if="managementCaption(card.sourceRow)" type="button" class="statement-method-link" :aria-controls="helpId" :aria-label="tx('Методика расчёта EBITDA и EBIT')" @click="openManagementHelp">{{tx('По образцу Excel')}}</button><div v-if="mode==='compare'" class="d">{{(card.row===36&&independentCapital)||(card.row===23&&mappingProfit)?tx('План из исходной модели'):tx('План')}}: {{formatCell(card.sourceRow||{kind:'amount'},card.cells.plan)}} · {{tx('Отклонение')}}: {{card.sourceRow?displayDeviation(card.sourceRow,card.cells):'—'}}</div><p v-if="card.row===23&&mappingProfit&&mode==='compare'" class="sub statement-card-reason">{{tx('Факт: финансовый результат. План: прибыль исходной модели; базы отличаются.')}}</p><div v-if="card.review" class="d">{{tx('Требует проверки')}}</div><p v-if="cellNumber(card.cells[primarySide])===null&&reasonLabel(card.cells[primarySide])" class="sub statement-card-reason">{{reasonLabel(card.cells[primarySide])}}</p></div></div>
      <div v-if="balanceResidual" class="statement-balance-check" role="note"><b>{{tx('Расхождение баланса')}}</b> · {{formatCell(balanceResidual,rowCells(balanceResidual)[primarySide])}} {{unitCaption}}<span class="sub"> · {{tx('Остаток ОСВ показан отдельно; капитал не подбирается для равенства.')}}</span></div>
      <div class="cf-cap"><h3>{{tx(reportTitle)}} · {{selectedPeriodLabel}}</h3><span class="sub">{{unitCaption}} · {{type==='balance'?tx('Остатки на конец периода'):tx('Суммы за выбранный период')}}</span></div>
      <div class="table-scroll statement-table-wrap" role="region" :aria-label="tx('Финансовый отчёт')" tabindex="0"><table class="statement-table"><caption>{{tx(reportTitle)}} · {{selectedPeriodLabel}}</caption><thead><tr><th scope="col" rowspan="2">{{tx('Показатель')}}</th><th v-for="column in columns" :key="column.key" scope="colgroup" :colspan="subcolumns.length" :class="{'statement-period-total':column.key==='total'}">{{column.label}}</th><th scope="col" rowspan="2">{{tx('Пояснение')}}</th></tr><tr><template v-for="column in columns" :key="column.key"><th v-for="field in subcolumns" :key="field" scope="col" :class="{'statement-period-total':column.key==='total'}">{{tx(columnLabels[field])}}</th></template></tr></thead>
        <tbody><template v-for="group in groups" :key="group.key"><tr class="statement-group"><th :colspan="2+columns.length*subcolumns.length"><button type="button" :aria-expanded="!collapsed.includes(group.key)" @click="toggleGroup(group.key)"><AppIcon name="chev" :class="{closed:collapsed.includes(group.key)}"/> {{tx(group.title)}}</button></th></tr><tr v-for="row in groupRows(group)" :key="row.key||row.row" :class="{'statement-section':row.kind==='section','statement-total-row':group.totals.includes(Number(row.row))}" :data-source-row="row.row"><th scope="row">{{tx(displayRowLabel(row))}}</th><template v-for="column in columns" :key="column.key"><td v-for="field in subcolumns" :key="field" class="statement-value" :class="{'statement-period-total':column.key==='total'}">{{row.kind==='section'?'':field==='plan'||field==='fact'?formatCell(row,columnCells(row,column)[field]):field==='deviation'?displayDeviation(row,columnCells(row,column)):displayPercent(row,columnCells(row,column))}}</td></template><td><p v-for="reason in visibleReasons(row)" :key="reason" class="statement-row-reason">{{reason}}</p><details v-if="row.kind!=='section'"><summary>{{tx('Подробнее')}}</summary><p class="sub">{{tx(rowLabel(row))}}</p><p v-if="managementRow(row)" class="sub statement-management-detail">{{tx('Управленческий показатель по образцу Excel. EBITDA и EBIT в образце равны; амортизация показана отдельно.')}}</p><div v-for="side in mode==='compare'?['plan','fact']:[primarySide]" :key="side" class="statement-provenance"><b>{{side==='plan'?tx('План'):tx('Факт')}}</b>: {{stateLabel(rowCells(row)[side])}}<small v-if="rowCells(row)[side]?.reason">{{tx(statementReasonLabels[rowCells(row)[side].reason]||rowCells(row)[side].reason)}}</small><div v-for="(origin,index) in sourcesOf(rowCells(row)[side])" :key="index"><small>{{origin.source||report.sheet}}</small><code v-if="origin.formula">{{origin.formula}}</code><code v-if="origin.template_formula&&origin.template_formula!==origin.formula">{{origin.template_formula}}</code><div v-for="(detail,detailIndex) in sourceDetails(origin)" :key="detailIndex"><small>{{detail.source}}<span v-if="detail.account"> · {{detail.account}}</span></small></div><div v-if="hasOriginalCache(origin)" class="statement-original-cache"><small>{{tx('Сохранённое значение до исправления')}}: {{formatCell(row,originalCache(origin))}}</small><code v-if="origin.source_formula">{{origin.source_formula}}</code></div></div></div></details></td></tr></template></tbody>
      </table></div>
      <section v-if="kpiRows.length" class="card statement-kpi-section"><div class="card-h"><div><h3>{{tx('Ключевые показатели')}}</h3><p class="sub">{{tx(indices.length>1?N_('Коэффициенты за период и остатки на его конец'):N_('Показатели на конец диапазона'))}} · {{periodLabel(periods.find(item=>Number(item.month)===indices.at(-1))||{month:indices.at(-1)})}}</p></div><button type="button" class="secondary tiny" :aria-expanded="!collapsed.includes('kpi')" @click="toggleGroup('kpi')">{{collapsed.includes('kpi')?tx('Развернуть'):tx('Свернуть')}}</button></div><div v-if="!collapsed.includes('kpi')" class="card-b"><div class="table-scroll" role="region" :aria-label="tx('KPI и нормативы источника')" tabindex="0"><table class="statement-table statement-kpi-table"><thead><tr><th scope="col">{{tx('Показатель')}}</th><th v-for="field in subcolumns" :key="field" scope="col">{{tx(columnLabels[field])}}</th><th scope="col">{{tx('Ориентир автора')}}</th><th scope="col">{{tx('Пояснение')}}</th></tr></thead><tbody><tr v-for="row in kpiRows" :key="row.key||row.row" :class="{'statement-section':row.kind==='section'}" :data-source-row="row.row"><th scope="row">{{tx(displayRowLabel(row))}}</th><td v-for="field in subcolumns" :key="field" class="statement-value">{{row.kind==='section'?'':field==='plan'||field==='fact'?formatCell(row,rowCells(row)[field]):field==='deviation'?displayDeviation(row):displayPercent(row)}}</td><td class="statement-norm">{{row.kind==='section'?'':normValue(row)??'—'}}</td><td><p v-for="reason in visibleReasons(row)" :key="reason" class="statement-row-reason">{{reason}}</p><details v-if="row.kind!=='section'"><summary>{{tx('Подробнее')}}</summary><p class="sub">{{tx(rowLabel(row))}}</p><p v-if="managementRow(row)" class="sub statement-management-detail">{{tx('Управленческий показатель по образцу Excel. EBITDA и EBIT в образце равны; амортизация показана отдельно.')}}</p><div v-for="side in mode==='compare'?['plan','fact']:[primarySide]" :key="side" class="statement-provenance"><b>{{side==='plan'?tx('План'):tx('Факт')}}</b>: {{stateLabel(rowCells(row)[side])}}<small v-if="reasonLabel(rowCells(row)[side])">{{reasonLabel(rowCells(row)[side])}}</small><div v-for="(origin,index) in sourcesOf(rowCells(row)[side])" :key="index"><small>{{origin.source||report.sheet}}</small><code v-if="origin.formula">{{origin.formula}}</code><code v-if="origin.template_formula&&origin.template_formula!==origin.formula">{{origin.template_formula}}</code><div v-for="(detail,detailIndex) in sourceDetails(origin)" :key="detailIndex"><small>{{detail.source}}<span v-if="detail.account"> · {{detail.account}}</span></small></div><div v-if="hasOriginalCache(origin)" class="statement-original-cache"><small>{{tx('Сохранённое значение до исправления')}}: {{formatCell(row,originalCache(origin))}}</small><code v-if="origin.source_formula">{{origin.source_formula}}</code></div></div></div><div v-if="row.norm!=null" class="statement-provenance"><b>{{tx('Ориентир автора')}}</b>: {{normValue(row)??'—'}}<small v-if="row.norm.status&&row.norm.status!=='author_guideline'">{{stateLabel(row.norm)}}</small><small>{{normSource(row)}}</small></div></details></td></tr></tbody></table></div></div></section>
    </template>
    <p v-else-if="source&&!loading" class="empty">{{tx('Нет данных за этот период. Откройте справку, чтобы проверить источник.')}}</p>
    <details ref="helpPanel" :id="helpId" class="card statement-disclosure statement-help" :open="helpOpen" @toggle="helpOpen=$event.target.open">
      <summary>{{tx('Справка и проверка')}}</summary>
      <div v-if="helpOpen" class="card-b statement-help-body">
        <section class="statement-help-section"><h4>{{tx('Как читать отчёт')}}</h4>
          <p class="sub">{{tx('Источник требует проверки; соответствие МСФО не подтверждено.')}}</p>
          <p v-if="source?.unit_status==='unknown'" class="sub statement-unit-note">{{tx('Масштаб исходной книги не подтверждён. Выбранная единица меняет только отображение исходных чисел, а не их валюту или смысл.')}}</p>
      <p class="sub statement-method">{{type==='balance'?tx('Баланс берётся строго на последний месяц диапазона; предыдущий месяц не подставляется вместо отсутствующего.'):tx('Суммы P&L складываются только при полном выбранном периоде. Маржи пересчитываются из сумм прибыли и чистой выручки, а не усредняются.')}} {{tx('Отклонение = факт − план. Для маржи показаны процентные пункты. Для нулевого или отрицательного плана относительный процент не определён. Прочерк означает, что значение не подтверждено; явный ноль показан числом.')}}</p>
          <p v-if="hasNorms" class="sub">{{tx('Ориентиры приведены из исходной книги. Их методика и пороги требуют согласования; это не оценка соответствия МСФО.')}}</p>
          <p v-if="type==='balance'&&!profileUnrecognized" class="sub">{{tx('ROA, ROE и оборачиваемость используют суммы за период и средние начальные и конечные остатки. Дни считаются по календарю. Если базы нет, показан прочерк с причиной.')}}</p>
        </section>
        <section v-if="managementMethodAvailable" ref="managementHelpPanel" class="statement-help-section statement-management-method"><h4>{{tx('Методика расчёта EBITDA и EBIT')}}</h4>
          <p class="sub">{{tx('Фактические EBITDA и EBIT — управленческие показатели по образцу Excel, а не подтверждённый расчёт по МСФО.')}}</p>
          <p class="sub">{{tx('EBITDA = чистая выручка − себестоимость + прочие операционные доходы 9300 − операционные расходы 9400. В образце EBIT = EBITDA; амортизация 0200 показана отдельно и повторно не вычитается.')}}</p>
          <p class="sub">{{tx('Прибыль до налога по образцу = EBIT + финансовые доходы 9500 − финансовые расходы 9600. Финансовый результат по счёту 9900 сохраняется отдельно; результат выбытия активов 9200 не входит в эту методику.')}}</p>
          <p class="sub">{{tx('Для независимого EBITDA с добавлением амортизации нужно распределить амортизацию, уже включённую в себестоимость и расходы.')}}</p>
          <details><summary>{{tx('Допущения и источники')}}</summary><p class="sub">{{tx('Пустые суммы зарегистрированных счетов равны нулю. Отсутствующие необязательные строки 9040 и 9300 считаются нулём только в полном распознанном образце; это отмечено в основаниях расчёта. Недостающие основные счета и месяцы не заменяются нулями.')}}</p><p class="sub">{{tx('Точные формулы и счета каждого месяца доступны в «Подробнее» у показателя. За период складываются только рассчитанные месяцы. План из сохранённой книги не меняется.')}}</p></details>
        </section>
        <section v-if="source?.revenue_policy&&friendlyProfile" class="statement-help-section statement-revenue-policy"><h4>{{tx('Правило выручки')}}</h4>
          <p v-if="confirmedRevenuePolicy" class="sub"><span class="pill fact">{{tx('Подтверждено')}}</span> {{tx('Выручка 9000 без НДС, до возвратов и скидок.')}} <span v-if="revenuePolicy.confirmed_at">{{dateLabel(revenuePolicy.confirmed_at)}}</span></p>
          <p v-else class="sub">{{tx('Состав выручки не подтверждён.')}}</p>
          <p v-if="confirmedRevenuePolicy||revenueBasis===confirmedRevenueBasis" class="sub">{{tx('Чистая выручка = выручка 9000 − возвраты 9040 − скидки 9050. Каждое уменьшение учитывается один раз; НДС повторно не вычитается.')}}</p>
          <p class="sub">{{tx('Правило действует только для выбранной компании и этой версии книги.')}}</p>
          <form v-if="canConfirmRevenueBasis" class="statement-revenue-form" @submit.prevent="saveRevenuePolicy">
            <label>{{tx('Состав выручки в Mapping')}}<select v-model="revenueBasis" :disabled="savingRevenueBasis||loading||uploading">
              <option value="unconfirmed">{{tx('Не подтверждён')}}</option>
              <option :value="confirmedRevenueBasis">{{tx('Без НДС, до возвратов и скидок')}}</option>
            </select></label>
            <button type="submit" :disabled="!revenuePolicyChanged||savingRevenueBasis||loading||uploading">{{tx('Сохранить правило выручки')}}</button>
          </form>
          <p v-if="revenuePolicyError" class="error" role="alert">{{tx(revenuePolicyError)}}</p>
          <p v-if="revenuePolicyMessage" class="sub" role="status" aria-live="polite">{{tx(revenuePolicyMessage)}}</p>
        </section>
        <section v-if="auditRows.length" class="statement-help-section"><h4>{{tx('Сверки исходной модели')}}</h4>
          <div class="table-scroll" tabindex="0" role="region" :aria-label="tx('Сверки исходной модели')"><table class="statement-table"><thead><tr><th>{{tx('Показатель')}}</th><th v-for="field in subcolumns" :key="field">{{tx(columnLabels[field])}}</th><th>{{tx('Источник и статус')}}</th></tr></thead><tbody><tr v-for="row in auditRows" :key="row.row"><th scope="row">{{tx(rowLabel(row))}}</th><td v-for="field in subcolumns" :key="field" class="statement-value">{{row.kind==='section'?'':field==='plan'||field==='fact'?formatCell(row,rowCells(row)[field]):field==='deviation'?displayDeviation(row):displayPercent(row)}}</td><td><div v-for="side in mode==='compare'?['plan','fact']:[primarySide]" :key="side" class="statement-provenance"><b>{{tx(columnLabels[side])}}</b>: {{stateLabel(rowCells(row)[side])}}<small>{{reasonLabel(rowCells(row)[side])}}</small><div v-for="(origin,index) in sourcesOf(rowCells(row)[side])" :key="index"><small>{{origin.source||report.sheet}}</small><code v-if="origin.formula">{{origin.formula}}</code><div v-if="hasOriginalCache(origin)" class="statement-original-cache"><small>{{tx('Сохранённое значение до исправления')}}: {{formatCell(row,originalCache(origin))}}</small><code v-if="origin.source_formula">{{origin.source_formula}}</code></div></div></div></td></tr></tbody></table></div>
        </section>
        <section v-if="report?.source_actual?.rows?.length" class="statement-help-section"><h4>{{tx('Факт исходной модели для сверки')}}</h4>
          <p class="sub">{{tx('Это сохранённые значения исходника. На основном экране показан расчёт по подтверждённым счетам; спорные суммы не подставляются.')}}</p>
          <div class="table-scroll" tabindex="0" role="region" :aria-label="tx('Факт исходной модели для сверки')"><table class="statement-table"><thead><tr><th>{{tx('Показатель')}}</th><th v-for="monthIndex in indices" :key="monthIndex">{{tx(monthNames[monthIndex-1])}}</th></tr></thead><tbody><tr v-for="row in report.source_actual.rows" :key="row.row"><th scope="row">{{tx(row.label)}}</th><td v-for="monthIndex in indices" :key="monthIndex" class="statement-value">{{formatCell(report.rows.find(item=>item.row===row.row)||{kind:'amount'},row.cells.find(item=>Number(item.month)===monthIndex)?.fact)}}</td></tr></tbody></table></div>
        </section>
        <section class="statement-help-section"><h4>{{tx('Данные и обновление')}}</h4>
      <div class="statement-controls"><label v-if="sourceItems.length">{{tx('Исходная книга')}}<select v-model="selected" :disabled="loading||uploading||savingRevenueBasis" @change="load(selected)"><option v-for="item in sourceItems" :key="item.id" :value="String(item.id)">{{item.filename}} · {{dateLabel(item.created_at)}}</option></select></label><template v-if="canImport"><button type="button" class="secondary statement-upload" :disabled="uploading||loading||savingRevenueBasis" @click="uploadInput?.click()"><AppIcon name="import"/> {{source?tx('Обновить данные'):tx('Загрузить данные')}}</button><input ref="uploadInput" type="file" accept=".xlsx" hidden :disabled="uploading||loading||savingRevenueBasis" :aria-label="source?tx('Обновить данные'):tx('Загрузить данные')" @change="upload"></template></div>
      <p v-if="source" class="sub">{{tx('Данные уже сохранены. Повторно загружать файл нужно только после появления новых данных.')}}</p>
      <p v-if="!canImport" class="sub">{{tx('Пользователь с правом импорта должен добавить исходную книгу.')}}</p>
      <template v-if="source"><p class="sub statement-source">{{source.filename}} · {{tx('Год')}}: {{source.year??tx('Не указан')}} · {{tx('Валюта источника')}}: {{source.currency||tx('Не указана')}} · {{tx('Единицы источника')}}: {{tx(source.unit||'')}} <span v-if="source.unit_status==='unknown'">· {{tx('Нужно подтвердить единицы')}}</span><span v-if="source.reviewed_as_of"> · {{tx('Дата проверки / среза')}}: {{dateLabel(source.reviewed_as_of)}}</span></p><p v-if="source.reviewed_as_of" class="sub">{{tx('Проверка обновляется из сохранённой книги. Для новых операций и месяцев загрузите новую версию источника; бюджет сохранённого снимка не меняется.')}}</p>
        <ul class="statement-coverage"><li v-for="item in selectedCoverage" :key="item.month">{{periodLabel(periods.find(period=>Number(period.month)===Number(item.month))||{month:item.month})}}: {{tx(coverageLabels[item.status]||'')}} · {{tx('Записей: {n}',{n:item.record_count??'—'})}}</li></ul></template>
        </section>
    <section v-if="source" class="statement-data-summary" :aria-label="tx('Данные отчёта')"><h4>{{tx('Источники и полнота данных')}}</h4>
      <div class="statement-summary-status"><span class="pill">{{tx('Найдено: {n}',{n:foundRequirements.length})}}</span><span class="pill" :class="clarificationRequirements.length?'warn':''">{{tx('Уточнить: {n}',{n:clarificationRequirements.length})}}</span><span class="pill">{{tx('Ожидают ОСВ: {n}',{n:pendingCoverage.length})}}</span></div>
      <p v-if="source.calculation?.profile" class="sub">{{tx('Факт: Mapping — ОСВ и счета выбранной компании.')}}</p>
      <p v-if="source.plan_source?.status==='cached_snapshot'" class="sub">{{tx('План: сохранённый снимок Macro из исходной книги. Обновление проверки не заменяет бюджет.')}} <span v-if="source.plan_source.as_of">{{dateLabel(source.plan_source.as_of)}}</span></p>
      <p v-if="pendingCoverage.length" class="sub">{{tx('ОСВ ещё не загружена')}}: {{pendingCoverage.map(item=>periodLabel(periods.find(period=>Number(period.month)===Number(item.month))||{month:item.month})).join(', ')}}. {{tx('Эти месяцы ожидают загрузки и не считаются ошибками расчёта.')}}</p>
    </section>
    <section v-if="issues.length||correctedIssues.length" class="statement-help-section"><h4>{{tx('Проверка и исправления')}} <span v-if="issues.length" class="pill warn">{{tx('Уточнить: {n}',{n:issues.length})}}</span><span v-if="correctedIssues.length" class="pill">{{tx('Исправлено: {n}',{n:correctedIssues.length})}}</span></h4><div class="statement-issues"><article v-for="(issue,index) in issues" :key="issue.code+'-'+index" class="statement-issue" :class="issue.severity"><strong>{{tx(issue.title)}}</strong><p>{{tx(issue.detail)}}</p><small>{{issue.source}}</small></article><article v-for="(issue,index) in correctedIssues" :key="'corrected-'+issue.code+'-'+index" class="statement-issue info"><span class="pill">{{tx('Исправлено при расчёте')}}</span> <strong>{{tx(issue.title)}}</strong><p>{{tx(issue.detail)}}</p><small>{{issue.source}}</small></article></div></section>
    <section v-if="requirements.length" class="statement-help-section"><h4>{{tx('Исходные данные для регулярного отчёта')}}</h4><div><p class="sub">{{tx('Найденные суммы берутся из Mapping. Ниже перечислены только необходимые уточнения и подтверждающие данные; повторно загружать найденные суммы не нужно.')}}</p><div class="statement-requirements"><article v-for="requirement in requirements" :key="requirement.key"><h4>{{tx(requirement.title)}}</h4><span v-if="requirement.status" class="pill" :class="requirement.status==='found'?'fact':'warn'">{{tx(requirementLabels[requirement.status]||N_('Нужно уточнить'))}}</span><ul v-if="requirement.evidence?.length" class="statement-evidence"><li v-for="field in requirement.evidence" :key="field">{{requirementEvidence(requirement,field)}}</li></ul><template v-if="requirement.missing_fields?.length"><p><b>{{tx('Нужно заполнить или подтвердить')}}</b></p><ul><li v-for="field in requirement.missing_fields" :key="field.key"><b>{{missingFieldLabel(field)}}</b><span v-if="field.reason">: {{tx(field.reason)}}</span></li></ul></template><ul v-else-if="!requirement.status"><li v-for="field in requirement.fields" :key="field">{{tx(field)}}</li></ul><p><b>{{tx('Когда обновлять')}}:</b> {{tx(requirement.frequency)}}</p><p class="sub">{{tx(requirement.reason)}}</p></article></div></div></section>
      </div>
    </details>
  </div>
</template>
<style scoped>
.financial-statements{display:grid;gap:14px}.statement-tools{padding:16px}.tl-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.tl-row+.tl-row{margin-top:12px}.fld{display:flex;align-items:center;gap:7px;font-size:13px}.fld select{min-width:85px}.grow{margin-left:auto;display:flex;gap:7px;flex-wrap:wrap}.seg,.chips{display:flex;flex-wrap:wrap;gap:4px}.seg button{padding:8px 12px;font-size:13px;border:1px solid var(--line);background:var(--surface);color:var(--ink);border-radius:7px}.seg button.on{background:var(--emx);color:var(--em);border-color:var(--em)}.statement-company{max-width:230px;overflow-wrap:anywhere;background:var(--emx);color:var(--em)}.statement-review{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 12px;border:1px solid var(--warn,#c58b30);border-radius:9px;background:var(--warnl,#fff8e7);font-size:12px;line-height:1.5}.statement-review button{white-space:nowrap}.statement-unit-note{margin:0}.statement-disclosure>summary{padding:13px 16px;cursor:pointer;font-weight:650;font-size:13px;overflow-wrap:anywhere}.statement-disclosure>summary .sub{font-weight:400;margin-left:8px}.statement-disclosure>summary .pill{margin-left:8px}.statement-disclosure[open]>summary{border-bottom:1px solid var(--line)}.statement-controls{display:flex;flex-wrap:wrap;align-items:end;gap:12px;margin:0 0 12px}.statement-controls>label:first-child{flex:1;min-width:190px;max-width:660px}.statement-controls label select{width:100%}.statement-upload{flex:none!important;min-width:0!important}.statement-source{overflow-wrap:anywhere;margin:12px 0}.statement-coverage{font-size:12px;line-height:1.7;margin:12px 0 0;padding-left:18px}.statement-cards{grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}.statement-kpi-card{min-width:0}.statement-kpi-card .v{font-size:clamp(18px,1.7vw,25px);line-height:1.3;margin:12px 0 5px;min-width:0;max-width:100%;white-space:normal;overflow-wrap:anywhere}.statement-kpi-card .v small{display:block;overflow-wrap:anywhere;font-size:11px;font-weight:400;color:var(--muted);margin-top:4px}.statement-card-source{font-size:10px!important;line-height:1.5;overflow-wrap:anywhere;margin:7px 0}.statement-kpi-card .d{font-size:13px;line-height:1.6}.statement-kpi-card.needs-review{border-color:var(--warn,#c58b30)}.cf-cap{display:flex;justify-content:space-between;gap:12px;align-items:end;flex-wrap:wrap}.cf-cap h3{margin:0;font-size:15px}.statement-table-wrap{border:1px solid var(--line);border-radius:10px;background:var(--surface)}.statement-table{width:100%;border-collapse:separate;border-spacing:0;min-width:720px;font-size:14px}.statement-table caption{text-align:left;color:var(--muted);font-size:12px;padding:10px 12px}.statement-table th,.statement-table td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}.statement-table thead th{text-align:center;background:var(--emx);white-space:nowrap}.statement-table thead th:first-child,.statement-table tbody th[scope=row]{position:sticky;left:0;z-index:2;min-width:225px;max-width:300px;text-align:left;background:var(--surface);box-shadow:2px 0 0 var(--line)}.statement-table thead th:first-child{z-index:3;background:var(--emx)}.statement-table tbody th[scope=row]{font-weight:500;overflow-wrap:anywhere}.statement-value{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;min-width:100px}.statement-period-total{background:var(--emx);border-left:1px solid var(--line)}.statement-group th{padding:0;background:var(--emx);text-align:left}.statement-group button{display:flex;align-items:center;gap:8px;width:100%;text-align:left;background:transparent;border:0;color:var(--ink);font-size:14px;font-weight:700;padding:13px}.statement-group .app-icon.closed{transform:rotate(-90deg)}.statement-total-row th,.statement-total-row td{font-weight:700!important}.statement-total-row th[scope=row]{background:var(--emx)}.statement-table .statement-section{background:var(--emx)}.statement-section th{font-weight:700!important;background:var(--emx)!important}.statement-provenance{margin:10px 0;min-width:170px;font-size:11px}.statement-provenance small,.statement-provenance code{display:block;overflow-wrap:anywhere;white-space:normal;margin-top:4px}.statement-table summary{cursor:pointer;color:var(--em)}.statement-method{margin:0;line-height:1.6}.statement-kpi-section .card-h{align-items:center;gap:12px}.statement-issues{display:grid;gap:10px}.statement-issue{border-left:3px solid var(--warn,#c58b30);padding:10px 12px;background:var(--warnl,#fff8e7);border-radius:6px;overflow-wrap:anywhere}.statement-issue.error{border-color:var(--bad,#b42318)}.statement-issue.info{border-color:var(--em);background:var(--emx)}.statement-issue p{font-size:12px;line-height:1.6;margin:5px 0}.statement-issue small{color:var(--muted)}.statement-requirements{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:16px;margin-top:16px}.statement-requirements article{border:1px solid var(--line);border-radius:10px;padding:14px;overflow-wrap:anywhere}.statement-requirements h4{font-size:13px;margin:0 0 10px}.statement-requirements p,.statement-requirements ul{font-size:12px;line-height:1.6}.statement-requirements ul{padding-left:18px}@media(max-width:600px){.statement-tools{padding:12px}.tl-row{align-items:flex-start;gap:8px}.grow{margin-left:0;width:100%}.statement-tools .chips{width:100%}.statement-review{align-items:flex-start}.statement-controls>label:first-child{max-width:100%;min-width:0;width:100%}.statement-controls{align-items:stretch}.statement-controls .statement-upload{width:100%}.statement-requirements{grid-template-columns:1fr}.statement-cards{grid-template-columns:repeat(2,minmax(0,1fr))}.statement-kpi-card .v{font-size:20px}.statement-kpi-card .k{flex-wrap:wrap}.statement-table thead th:first-child,.statement-table tbody th[scope=row]{min-width:170px;max-width:190px}.statement-value{min-width:95px}.statement-kpi-section .card-h{align-items:flex-start}}
/* These cards have more than the three rows used by the shared Cash Flow subgrid. */
.statement-cards>div.statement-kpi-card{display:flex;flex-direction:column;grid-row:auto;grid-template-rows:none}.statement-kpi-card .k{flex-wrap:wrap;min-height:70px;align-content:flex-start;gap:6px}.statement-company{white-space:normal;min-width:0}
.statement-table tbody th[scope=row]{white-space:normal}
.statement-data-summary{padding:12px 16px}.statement-summary-status{display:flex;gap:8px;flex-wrap:wrap}.statement-data-summary p{margin:8px 0 0;line-height:1.6;overflow-wrap:anywhere}.statement-balance-check{padding:12px 16px;border:1px solid var(--line);border-radius:9px;line-height:1.6;font-size:12px;overflow-wrap:anywhere}.statement-card-reason{font-size:11px;line-height:1.5;margin:5px 0 0}.statement-original-cache{border-top:1px solid var(--line);margin-top:8px;padding-top:5px;color:var(--muted)}.statement-evidence{color:var(--muted)}
.statement-help-body{display:grid;gap:22px}.statement-help-section h4,.statement-data-summary h4{font-size:15px;margin:0 0 12px}.statement-help-section p,.statement-data-summary p{line-height:1.65}.statement-help-section+.statement-help-section{border-top:1px solid var(--line);padding-top:20px}.statement-empty{padding:22px;display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.statement-empty p{margin:0}.statement-row-reason{font-size:12px;line-height:1.5;max-width:320px;min-width:180px;white-space:normal;margin:0 0 8px;color:var(--muted)}.statement-kpi-card .k{font-size:14px}.statement-card-reason{font-size:12px}.statement-kpi-card .v small{font-size:12px}.statement-table :is(button,summary):focus-visible,.statement-help>summary:focus-visible{outline:2px solid var(--em);outline-offset:3px}.statement-help-section .statement-method{font-size:13px}.statement-controls .statement-upload{white-space:normal}.statement-help h4 .pill{margin-left:8px}@media(max-width:600px){.statement-table{font-size:13px}.statement-empty{padding:16px}}
.statement-revenue-form{display:flex;align-items:end;flex-wrap:wrap;gap:12px}.statement-revenue-form label{display:grid;gap:7px;min-width:0;max-width:100%;font-size:13px}.statement-revenue-form select{max-width:100%;width:100%}.statement-revenue-form button{white-space:normal}@media(max-width:600px){.statement-revenue-form label,.statement-revenue-form button{width:100%}}
.statement-cards{grid-template-columns:repeat(auto-fit,minmax(min(100%,135px),1fr));gap:10px}.statement-kpi-card .v{font-size:clamp(17px,1.6vw,24px)}.statement-method-link{padding:0;border:0;background:transparent;color:var(--em);font-size:12px;font-weight:600;text-align:left;justify-content:flex-start;white-space:normal;line-height:1.5}.statement-method-link:focus-visible{outline:2px solid var(--em);outline-offset:3px}.statement-group button{justify-content:flex-start}.statement-management-method details{margin-top:10px;line-height:1.65}.statement-management-method summary{cursor:pointer;color:var(--em)}@media(max-width:600px){.statement-cards{grid-template-columns:repeat(2,minmax(0,1fr))}.statement-kpi-card .v{font-size:20px}}
</style>
