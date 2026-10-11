<script setup>
import {ref,computed,watch,onMounted,onUnmounted} from 'vue';
import {tx,N_,intlLocale,formatDate} from './i18n/index.js';
import AppIcon from './AppIcon.vue';
import OperationProgress from './OperationProgress.vue';
import {createOperationProgress} from './operationProgress.js';
import {statementUrl,statementFileProblem,statementDeviation,statementNumber,statementStatuses,sourceScopeMatches,aggregateStatementRow,statementScaledNumber,statementScaledDeviation,statementReasonLabels} from './financialStatements.js';

const props=defineProps({api:{type:Function,required:true},company:{type:Object,required:true},
  type:{type:String,default:'pnl'},canImport:{type:Boolean,default:false},canDownload:{type:Boolean,default:false},refresh:{type:Number,default:0}});
const emit=defineEmits(['editing']);
const payload=ref(null),selected=ref(''),month=ref(null),loading=ref(false),uploading=ref(false),error=ref('');
const year=ref(null),per=ref('month'),start=ref(1),end=ref(1),mode=ref('compare'),unit=ref('x'),showMonths=ref(false),collapsed=ref([]);
const sourceOpen=ref(false),issuesOpen=ref(false),requirementsOpen=ref(false),uploadInput=ref(null);
const monthNames=[N_('Январь'),N_('Февраль'),N_('Март'),N_('Апрель'),N_('Май'),N_('Июнь'),N_('Июль'),N_('Август'),N_('Сентябрь'),N_('Октябрь'),N_('Ноябрь'),N_('Декабрь')];
const periodOptions=[['year',N_('Год')],['q1',N_('I кв.')],['q2',N_('II кв.')],['q3',N_('III кв.')],['q4',N_('IV кв.')],['custom',N_('Свой период')],['month',N_('Месяц')]];
const units={x:{label:N_('Единицы'),scale:1},k:{label:N_('Тыс.'),scale:1000},m:{label:N_('Млн'),scale:1000000}};
const progress=ref({active:false,completed:0,total:0,label:'',error:''});
const progressController=createOperationProgress(value=>progress.value=value);
let active=true,requestId=0;
const source=computed(()=>payload.value?.source);
const report=computed(()=>source.value?.reports?.[props.type]);
const periods=computed(()=>report.value?.periods || []);
const years=computed(()=>[...new Set([...periods.value.map(item=>/^\d{4}-\d{2}$/.test(item.period||'')?Number(item.period.slice(0,4)):null),source.value?.year].filter(value=>Number.isInteger(value)&&value>=2000&&value<=2100))].sort((a,b)=>a-b));
const rangeError=computed(()=>per.value==='custom'&&Number(start.value)>Number(end.value)?N_('Начало периода должно быть не позже конца. Исправьте диапазон месяцев.'):year.value!=null&&!years.value.includes(Number(year.value))?N_('Выбранный год отсутствует в исходной книге.'):'');
const indices=computed(()=>Array.from({length:Math.max(0,Number(per.value==='month'?month.value:end.value)-Number(per.value==='month'?month.value:start.value)+1)},(_,i)=>i+Number(per.value==='month'?month.value:start.value)).filter(value=>value>=1&&value<=12));
const range=computed(()=>({startMonth:Number(per.value==='month'?month.value:start.value),endMonth:Number(per.value==='month'?month.value:end.value),year:year.value,type:props.type,coverage:source.value?.coverage}));
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
const issues=computed(()=>source.value?.issues || []);
const selectedCoverage=computed(()=>(source.value?.coverage||[]).filter(item=>indices.value.includes(Number(item.month))));
const coverageLabels={missing:N_('Нет исходной ОСВ за месяц'),available:N_('Операции в источнике есть'),future:N_('Будущий период'),invalid:N_('Проверьте даты операций')};
const amount=value=>statementNumber(value,'amount',intlLocale.value);
const formatCell=(row,cell)=>statementScaledNumber(cell,row,scale.value,intlLocale.value);
const stateLabel=cell=>tx(statementStatuses[cell?.status] || N_('Нет данных'));
const periodLabel=period=>period.period || tx('Месяц {n} · дата не подтверждена',{n:period.month});
const dateLabel=value=>formatDate(value,{timeZone:'Asia/Tashkent',year:'numeric',month:'2-digit',day:'2-digit'}) || '';
const rowCells=row=>aggregateStatementRow(report.value,row,range.value);
const columnCells=(row,column)=>column.month==null?rowCells(row):aggregateStatementRow(report.value,row,{...range.value,startMonth:column.month,endMonth:column.month});
const deviation=(row,cells=rowCells(row))=>statementDeviation(cells.plan,cells.fact,row.kind);
const displayDeviation=(row,cells=rowCells(row))=>statementScaledDeviation(cells.plan,cells.fact,row,scale.value,intlLocale.value);
const displayPercent=(row,cells=rowCells(row))=>deviation(row,cells).percent===null?'—':amount(deviation(row,cells).percent)+'%';
const primarySide=computed(()=>mode.value==='plan'?'plan':'fact');
const cardDefinitions=computed(()=>props.type==='pnl'?[{row:7,title:N_('Чистая выручка')},{row:12,title:'Gross Profit'},{row:16,title:'EBITDA'},{row:18,title:'EBIT'},{row:23,title:N_('Чистая прибыль')}]:[{row:19,title:N_('Активы')},{row:35,title:N_('Обязательства')},{row:36,title:N_('Капитал как разница'),review:true},{row:8,title:N_('Деньги')}]);
const cards=computed(()=>cardDefinitions.value.map(item=>{const row=report.value?.rows?.find(row=>Number(row.row)===item.row);return {...item,sourceRow:row,cells:row?rowCells(row):{plan:null,fact:null}};}));
const groups=computed(()=>{
 const definitions=props.type==='pnl'?[['gross',N_('Выручка и валовая прибыль'),7,13,[12,13]],['operating',N_('Операционная прибыль'),14,18,[16,18]],['net',N_('Финансовые расходы и чистая прибыль'),19,24,[23,24]]]:[['assets',N_('Активы'),7,19,[19]],['liabilities',N_('Обязательства'),20,35,[35]],['capital',N_('Капитал и контроль баланса'),36,38,[36,37,38]],['checks',N_('Сверки баланса'),39,61,[56]]];
 return definitions.map(([key,title,from,to,totals])=>({key,title,totals,rows:(report.value?.rows||[]).filter(row=>{const value=Number(row.row);return key==='capital'?value>=32&&value<=38&&value!==35:key==='liabilities'?value>=20&&value<=35&&!(value>=32&&value<=34):value>=from&&value<=to;})})).filter(group=>group.rows.length);
});
const kpiRows=computed(()=>props.type==='balance'?(report.value?.rows||[]).filter(row=>Number(row.row)>=62&&Number(row.row)<=81):[]);
const groupRows=group=>collapsed.value.includes(group.key)?group.rows.filter(row=>group.totals.includes(Number(row.row))):group.rows;
const sourcesOf=cell=>cell?.provenance?.length?cell.provenance:cell?[cell]:[];
function toggleGroup(key){collapsed.value=collapsed.value.includes(key)?collapsed.value.filter(value=>value!==key):[...collapsed.value,key];}
function collapseAll(){collapsed.value=[...groups.value.map(group=>group.key),...(kpiRows.value.length?['kpi']:[])];}
function setPer(value){const previous=[...indices.value];per.value=value;if(value==='year'){start.value=1;end.value=12;}else if(value.startsWith('q')){const quarter=Number(value.slice(1));start.value=(quarter-1)*3+1;end.value=quarter*3;}else if(value==='custom'){start.value=previous[0]||Number(month.value)||1;end.value=previous.at(-1)||start.value;}}
let sourceScope='';
function resetMonth() {
  const nextScope=props.company.id+':'+source.value?.id;
  if(sourceScope!==nextScope){sourceScope=nextScope;year.value=years.value.includes(source.value?.year)?source.value.year:years.value[0]??null;per.value='month';collapsed.value=[];showMonths.value=false;sourceOpen.value=false;issuesOpen.value=false;requirementsOpen.value=!source.value;}
  if(!years.value.includes(Number(year.value)))year.value=years.value[0]??null;
  if(!periods.value.some(item=>Number(item.month)===Number(month.value)))month.value=periods.value[0]?.month??null;
}
function assertScope(value,companyId) {
  if(!sourceScopeMatches(value,companyId)) throw Error(N_('Источник относится к другой компании. Обновите страницу.'));
}
async function load(sourceId=selected.value) {
  const id=++requestId,companyId=props.company.id;
  loading.value=true;error.value='';payload.value=null;
  const ticket=progressController.begin(N_('Загружаем проверку исходных данных'),1);
  try{
    const value=await props.api(statementUrl(companyId,sourceId));
    if(!active || id!==requestId || props.company.id!==companyId)return;
    assertScope(value,companyId);payload.value=value;selected.value=String(value.selected ?? value.source?.id ?? '');resetMonth();
    progressController.complete(ticket,N_('Исходные данные загружены'));
  }catch(e){if(active&&id===requestId&&props.company.id===companyId&&e.name!=='AbortError'){error.value=e.message;progressController.fail(ticket,e);}}
  finally{if(active&&id===requestId&&props.company.id===companyId)loading.value=false;}
}
async function upload(event) {
  const file=event.target.files?.[0];event.target.value='';
  if(!file || uploading.value || !props.canImport)return;
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
    assertScope(value,companyId);payload.value=value;selected.value=String(value.selected ?? sourceId);resetMonth();
    progressController.complete(ticket,N_('Проверка исходной книги загружена'));
  }catch(e){if(active&&id===requestId&&props.company.id===companyId&&e.name!=='AbortError'){error.value=e.message;progressController.fail(ticket,e);}}
  finally{if(active&&id===requestId&&props.company.id===companyId)uploading.value=false;}
}
watch(uploading,value=>emit('editing',value));
watch(()=>props.company.id,()=>{requestId++;selected.value='';month.value=null;payload.value=null;uploading.value=false;progressController.reset();load('');});
watch(()=>props.type,resetMonth);
watch(()=>props.refresh,()=>{if(!uploading.value)load();});
onMounted(()=>load());
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
        <div class="grow"><button class="secondary tiny" :disabled="loading||uploading" @click="load(selected)"><AppIcon name="refresh"/> {{tx('Обновить')}}</button></div>
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
    <div class="statement-review" role="note"><span>{{tx('Источник требует проверки; соответствие МСФО не подтверждено.')}}</span><button v-if="issues.length" class="ghost tiny" type="button" :aria-expanded="issuesOpen" @click="issuesOpen=!issuesOpen">{{tx('Замечаний: {n}',{n:issues.length})}}</button></div>
    <p v-if="source?.unit_status==='unknown'" class="sub statement-unit-note">{{tx('Масштаб исходной книги не подтверждён. Выбранная единица меняет только отображение исходных чисел, а не их валюту или смысл.')}}</p>
    <OperationProgress v-bind="progress"/>
    <p v-if="error" class="error" role="alert">{{tx(error)}}</p>
    <p v-if="!loading&&!error&&!source" class="empty">{{tx('Исходная книга для P&L и баланса пока не загружена. Нужны листы отчётов, операции и мэппинг счетов.')}}</p>
    <p v-if="!loading&&!error&&!source&&!canImport" class="sub">{{tx('Пользователь с правом импорта должен добавить исходную книгу.')}}</p>
    <details class="card statement-disclosure" :open="sourceOpen||!source" @toggle="sourceOpen=$event.target.open"><summary>{{tx('Исходник и дата проверки')}} <span class="sub">{{source?.filename}}</span></summary><div class="card-b">
      <div class="statement-controls"><label v-if="sourceItems.length">{{tx('Исходная книга')}}<select v-model="selected" :disabled="loading||uploading" @change="load(selected)"><option v-for="item in sourceItems" :key="item.id" :value="String(item.id)">{{item.filename}} · {{dateLabel(item.created_at)}}</option></select></label><template v-if="canImport"><button type="button" class="secondary statement-upload" :disabled="uploading||loading" @click="uploadInput?.click()"><AppIcon name="import"/> {{tx('Добавить исходный Excel')}}</button><input ref="uploadInput" type="file" accept=".xlsx" hidden :disabled="uploading||loading" :aria-label="tx('Добавить исходный Excel')" @change="upload"></template></div>
      <template v-if="source"><p class="sub statement-source">{{source.filename}} · {{tx('Год')}}: {{source.year??tx('Не указан')}} · {{tx('Валюта источника')}}: {{source.currency||tx('Не указана')}} · {{tx('Единицы источника')}}: {{tx(source.unit||'')}} <span v-if="source.unit_status==='unknown'">· {{tx('Нужно подтвердить единицы')}}</span><span v-if="source.reviewed_as_of"> · {{tx('Дата проверки / среза')}}: {{dateLabel(source.reviewed_as_of)}}</span></p><p v-if="source.reviewed_as_of" class="sub">{{tx('Покрытие факта и будущие периоды определены на дату проверки сохранённой книги. Для обновления среза загрузите обновлённый источник.')}}</p>
        <ul class="statement-coverage"><li v-for="item in selectedCoverage" :key="item.month">{{periodLabel(periods.find(period=>Number(period.month)===Number(item.month))||{month:item.month})}}: {{tx(coverageLabels[item.status]||'')}} · {{tx('Записей: {n}',{n:item.record_count??'—'})}}</li></ul></template>
    </div></details>
    <template v-if="source&&report?.rows?.length">
      <div class="sumrow statement-cards"><div v-for="card in cards" :key="card.row" class="card tile statement-kpi-card" :class="{'needs-review':card.review}" :data-source-row="card.row"><div class="k"><span>{{tx(card.title)}}</span><span class="pill" :class="mode==='plan'?'plan':'fact'">{{mode==='compare'?tx('План-факт'):mode==='plan'?tx('План'):tx('Факт')}}</span></div><div class="v num">{{formatCell(card.sourceRow||{kind:'amount'},card.cells[primarySide])}}<small>{{unitCaption}}</small></div><p class="sub statement-card-source">{{card.sourceRow?.label||tx('Строка источника не найдена')}}</p><div v-if="mode==='compare'" class="d">{{tx('План')}}: {{formatCell(card.sourceRow||{kind:'amount'},card.cells.plan)}} · {{tx('Отклонение')}}: {{card.sourceRow?displayDeviation(card.sourceRow,card.cells):'—'}}</div><div class="d">{{stateLabel(card.cells[primarySide])}}<span v-if="card.review"> · {{tx('Требует проверки')}}</span></div></div></div>
      <div class="cf-cap"><h3>{{tx(report.title)}} · {{selectedPeriodLabel}}</h3><span class="sub">{{unitCaption}} · {{type==='balance'?tx('Остатки на конец периода'):tx('Суммы за выбранный период')}}</span></div>
      <div class="table-scroll statement-table-wrap" role="region" :aria-label="tx('Финансовый отчёт')" tabindex="0"><table class="statement-table"><caption>{{type==='balance'?tx('Баланс · проверка источника'):tx('P&L · план и факт источника')}} · {{selectedPeriodLabel}}</caption><thead><tr><th scope="col" rowspan="2">{{tx('Показатель')}}</th><th v-for="column in columns" :key="column.key" scope="colgroup" :colspan="subcolumns.length" :class="{'statement-period-total':column.key==='total'}">{{column.label}}</th><th scope="col" rowspan="2">{{tx('Источник и статус')}}</th></tr><tr><template v-for="column in columns" :key="column.key"><th v-for="field in subcolumns" :key="field" scope="col" :class="{'statement-period-total':column.key==='total'}">{{tx(columnLabels[field])}}</th></template></tr></thead>
        <tbody><template v-for="group in groups" :key="group.key"><tr class="statement-group"><th :colspan="2+columns.length*subcolumns.length"><button type="button" :aria-expanded="!collapsed.includes(group.key)" @click="toggleGroup(group.key)"><AppIcon name="chev" :class="{closed:collapsed.includes(group.key)}"/> {{tx(group.title)}}</button></th></tr><tr v-for="row in groupRows(group)" :key="row.key||row.row" :class="{'statement-section':row.kind==='section','statement-total-row':group.totals.includes(Number(row.row))}" :data-source-row="row.row"><th scope="row">{{tx(row.label)}}</th><template v-for="column in columns" :key="column.key"><td v-for="field in subcolumns" :key="field" class="statement-value" :class="{'statement-period-total':column.key==='total'}">{{row.kind==='section'?'':field==='plan'||field==='fact'?formatCell(row,columnCells(row,column)[field]):field==='deviation'?displayDeviation(row,columnCells(row,column)):displayPercent(row,columnCells(row,column))}}</td></template><td><details v-if="row.kind!=='section'"><summary>{{tx('Проверить источник')}}</summary><div v-for="side in mode==='compare'?['plan','fact']:[primarySide]" :key="side" class="statement-provenance"><b>{{side==='plan'?tx('План'):tx('Факт')}}</b>: {{stateLabel(rowCells(row)[side])}}<small v-if="rowCells(row)[side]?.reason">{{tx(statementReasonLabels[rowCells(row)[side].reason]||rowCells(row)[side].reason)}}</small><div v-for="(origin,index) in sourcesOf(rowCells(row)[side])" :key="index"><small>{{origin.source||report.sheet}}</small><code v-if="origin.formula">{{origin.formula}}</code></div></div></details></td></tr></template></tbody>
      </table></div>
      <p class="sub statement-method">{{type==='balance'?tx('Баланс берётся строго на последний месяц диапазона; предыдущий месяц не подставляется вместо отсутствующего.'):tx('Суммы P&L складываются только при полном выбранном периоде. Маржи пересчитываются из сумм прибыли и чистой выручки, а не усредняются.')}} {{tx('Отклонение = факт − план. Для маржи показаны процентные пункты. Для нулевого или отрицательного плана относительный процент не определён. Прочерк означает, что значение не подтверждено; явный ноль показан числом.')}}</p>
      <section v-if="kpiRows.length" class="card statement-kpi-section"><div class="card-h"><div><h3>{{tx('KPI и нормативы источника')}}</h3><p class="sub">{{tx('Показатели на конец диапазона')}} · {{periodLabel(periods.find(item=>Number(item.month)===indices.at(-1))||{month:indices.at(-1)})}}</p></div><button type="button" class="secondary tiny" :aria-expanded="!collapsed.includes('kpi')" @click="toggleGroup('kpi')">{{collapsed.includes('kpi')?tx('Развернуть'):tx('Свернуть')}}</button></div><div v-if="!collapsed.includes('kpi')" class="card-b"><p class="sub">{{tx('Ориентиры приведены из исходной книги. Их методика и пороги требуют согласования; это не оценка соответствия МСФО.')}}</p><div class="table-scroll" role="region" :aria-label="tx('KPI и нормативы источника')" tabindex="0"><table class="statement-table statement-kpi-table"><thead><tr><th scope="col">{{tx('Показатель')}}</th><th v-for="field in subcolumns" :key="field" scope="col">{{tx(columnLabels[field])}}</th><th scope="col">{{tx('Ориентир автора')}}</th><th scope="col">{{tx('Источник и статус')}}</th></tr></thead><tbody><tr v-for="row in kpiRows" :key="row.key||row.row" :class="{'statement-section':row.kind==='section'}" :data-source-row="row.row"><th scope="row">{{tx(row.label)}}</th><td v-for="field in subcolumns" :key="field" class="statement-value">{{row.kind==='section'?'':field==='plan'||field==='fact'?formatCell(row,rowCells(row)[field]):field==='deviation'?displayDeviation(row):displayPercent(row)}}</td><td class="statement-norm">{{row.kind==='section'?'':normValue(row)??'—'}}</td><td><details v-if="row.kind!=='section'"><summary>{{tx('Проверить источник')}}</summary><div v-for="side in mode==='compare'?['plan','fact']:[primarySide]" :key="side" class="statement-provenance"><b>{{side==='plan'?tx('План'):tx('Факт')}}</b>: {{stateLabel(rowCells(row)[side])}}<div v-for="(origin,index) in sourcesOf(rowCells(row)[side])" :key="index"><small>{{origin.source||report.sheet}}</small><code v-if="origin.formula">{{origin.formula}}</code></div></div><div v-if="row.norm!=null" class="statement-provenance"><b>{{tx('Ориентир автора')}}</b>: {{normValue(row)??'—'}}<small v-if="row.norm.status&&row.norm.status!=='author_guideline'">{{stateLabel(row.norm)}}</small><small>{{normSource(row)}}</small></div></details></td></tr></tbody></table></div></div></section>
    </template>
    <p v-else-if="source&&!loading" class="empty">{{tx('В исходной книге не найдена форма этого отчёта. Проверьте список расхождений.')}}</p>
    <details v-if="issues.length" class="card statement-disclosure" :open="issuesOpen" @toggle="issuesOpen=$event.target.open"><summary>{{tx('Что нужно проверить')}} <span class="pill warn">{{issues.length}}</span></summary><div class="card-b statement-issues"><article v-for="(issue,index) in issues" :key="issue.code+'-'+index" class="statement-issue" :class="issue.severity"><strong>{{tx(issue.title)}}</strong><p>{{tx(issue.detail)}}</p><small>{{issue.source}}</small></article></div></details>
    <details v-if="requirements.length" class="card statement-disclosure" :open="requirementsOpen||!source" @toggle="requirementsOpen=$event.target.open"><summary>{{tx('Исходные данные для регулярного отчёта')}}</summary><div class="card-b"><p class="sub">{{tx('Этот список показывает необходимые данные. Загрузка книги сама по себе не подтверждает их полноту.')}}</p><div class="statement-requirements"><article v-for="requirement in requirements" :key="requirement.key"><h4>{{tx(requirement.title)}}</h4><ul><li v-for="field in requirement.fields" :key="field">{{tx(field)}}</li></ul><p><b>{{tx('Когда обновлять')}}:</b> {{tx(requirement.frequency)}}</p><p class="sub">{{tx(requirement.reason)}}</p></article></div></div></details>
  </div>
</template>
<style scoped>
.financial-statements{display:grid;gap:14px}.statement-tools{padding:16px}.tl-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.tl-row+.tl-row{margin-top:12px}.fld{display:flex;align-items:center;gap:7px;font-size:12px}.fld select{min-width:85px}.grow{margin-left:auto;display:flex;gap:7px;flex-wrap:wrap}.seg,.chips{display:flex;flex-wrap:wrap;gap:4px}.seg button{padding:7px 11px;font-size:12px;border:1px solid var(--line);background:var(--surface);color:var(--ink);border-radius:7px}.seg button.on{background:var(--emx);color:var(--em);border-color:var(--em)}.statement-company{max-width:230px;overflow-wrap:anywhere;background:var(--emx);color:var(--em)}.statement-review{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 12px;border:1px solid var(--warn,#c58b30);border-radius:9px;background:var(--warnl,#fff8e7);font-size:12px;line-height:1.5}.statement-review button{white-space:nowrap}.statement-unit-note{margin:0}.statement-disclosure>summary{padding:13px 16px;cursor:pointer;font-weight:650;font-size:13px;overflow-wrap:anywhere}.statement-disclosure>summary .sub{font-weight:400;margin-left:8px}.statement-disclosure>summary .pill{margin-left:8px}.statement-disclosure[open]>summary{border-bottom:1px solid var(--line)}.statement-controls{display:flex;flex-wrap:wrap;align-items:end;gap:12px;margin:0 0 12px}.statement-controls>label:first-child{flex:1;min-width:190px;max-width:660px}.statement-controls label select{width:100%}.statement-upload{flex:none!important;min-width:0!important}.statement-source{overflow-wrap:anywhere;margin:12px 0}.statement-coverage{font-size:12px;line-height:1.7;margin:12px 0 0;padding-left:18px}.statement-cards{grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}.statement-kpi-card{min-width:0}.statement-kpi-card .v{font-size:clamp(18px,1.7vw,25px);line-height:1.3;margin:12px 0 5px;min-width:0;max-width:100%;white-space:normal;overflow-wrap:anywhere}.statement-kpi-card .v small{display:block;overflow-wrap:anywhere;font-size:11px;font-weight:400;color:var(--muted);margin-top:4px}.statement-card-source{font-size:10px!important;line-height:1.5;overflow-wrap:anywhere;margin:7px 0}.statement-kpi-card .d{font-size:11px;line-height:1.6}.statement-kpi-card.needs-review{border-color:var(--warn,#c58b30)}.cf-cap{display:flex;justify-content:space-between;gap:12px;align-items:end;flex-wrap:wrap}.cf-cap h3{margin:0;font-size:15px}.statement-table-wrap{border:1px solid var(--line);border-radius:10px;background:var(--surface)}.statement-table{width:100%;border-collapse:separate;border-spacing:0;min-width:720px;font-size:12px}.statement-table caption{text-align:left;color:var(--muted);font-size:12px;padding:10px 12px}.statement-table th,.statement-table td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}.statement-table thead th{text-align:center;background:var(--emx);white-space:nowrap}.statement-table thead th:first-child,.statement-table tbody th[scope=row]{position:sticky;left:0;z-index:2;min-width:225px;max-width:300px;text-align:left;background:var(--surface);box-shadow:2px 0 0 var(--line)}.statement-table thead th:first-child{z-index:3;background:var(--emx)}.statement-table tbody th[scope=row]{font-weight:500;overflow-wrap:anywhere}.statement-value{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;min-width:100px}.statement-period-total{background:var(--emx);border-left:1px solid var(--line)}.statement-group th{padding:0;background:var(--emx);text-align:left}.statement-group button{display:flex;align-items:center;gap:8px;width:100%;text-align:left;background:transparent;border:0;color:var(--ink);font-size:12px;font-weight:700;padding:12px}.statement-group .app-icon.closed{transform:rotate(-90deg)}.statement-total-row th,.statement-total-row td{font-weight:700!important}.statement-total-row th[scope=row]{background:var(--emx)}.statement-table .statement-section{background:var(--emx)}.statement-section th{font-weight:700!important;background:var(--emx)!important}.statement-provenance{margin:10px 0;min-width:170px;font-size:11px}.statement-provenance small,.statement-provenance code{display:block;overflow-wrap:anywhere;white-space:normal;margin-top:4px}.statement-table summary{cursor:pointer;color:var(--em)}.statement-method{margin:0;line-height:1.6}.statement-kpi-section .card-h{align-items:center;gap:12px}.statement-issues{display:grid;gap:10px}.statement-issue{border-left:3px solid var(--warn,#c58b30);padding:10px 12px;background:var(--warnl,#fff8e7);border-radius:6px;overflow-wrap:anywhere}.statement-issue.error{border-color:var(--bad,#b42318)}.statement-issue.info{border-color:var(--em);background:var(--emx)}.statement-issue p{font-size:12px;line-height:1.6;margin:5px 0}.statement-issue small{color:var(--muted)}.statement-requirements{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:16px;margin-top:16px}.statement-requirements article{border:1px solid var(--line);border-radius:10px;padding:14px;overflow-wrap:anywhere}.statement-requirements h4{font-size:13px;margin:0 0 10px}.statement-requirements p,.statement-requirements ul{font-size:12px;line-height:1.6}.statement-requirements ul{padding-left:18px}@media(max-width:600px){.statement-tools{padding:12px}.tl-row{align-items:flex-start;gap:8px}.grow{margin-left:0;width:100%}.statement-tools .chips{width:100%}.statement-review{align-items:flex-start}.statement-controls>label:first-child{max-width:100%;min-width:0;width:100%}.statement-controls{align-items:stretch}.statement-controls .statement-upload{width:100%}.statement-requirements{grid-template-columns:1fr}.statement-cards{grid-template-columns:repeat(2,minmax(0,1fr))}.statement-kpi-card .v{font-size:20px}.statement-kpi-card .k{flex-wrap:wrap}.statement-table thead th:first-child,.statement-table tbody th[scope=row]{min-width:170px;max-width:190px}.statement-value{min-width:95px}.statement-kpi-section .card-h{align-items:flex-start}}
/* These cards have more than the three rows used by the shared Cash Flow subgrid. */
.statement-cards>div.statement-kpi-card{display:flex;flex-direction:column;grid-row:auto;grid-template-rows:none}.statement-kpi-card .k{flex-wrap:wrap}.statement-company{white-space:normal;min-width:0}
.statement-table tbody th[scope=row]{white-space:normal}
</style>
