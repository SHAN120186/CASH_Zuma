<script setup>
import {ref,computed,watch,onMounted,onUnmounted} from 'vue';
import {tx,N_,intlLocale,formatDate} from './i18n/index.js';
import AppIcon from './AppIcon.vue';
import OperationProgress from './OperationProgress.vue';
import {createOperationProgress} from './operationProgress.js';
import {OSV_SCALES,OSV_MONTHS,OSV_UNITS,fileBase64,sourceYears,periodMonths,sumAmounts,osvExportUrl} from './cashflow/osvView.js';

const props=defineProps({api:{type:Function,required:true},companyId:{type:Number,required:true},companyName:{type:String,default:''},currency:{type:String,default:'UZS'},canEdit:{type:Boolean,default:false},canDownload:{type:Boolean,default:false}});
const sources=ref([]),sourceId=ref(''),serverEdit=ref(false),serverDownload=ref(false),loaded=ref(false);
const bankFile=ref(null),templateFile=ref(null),preview=ref(null),showUpload=ref(false),uploadCurrency=ref(props.currency),scale=ref(1),reason=ref('');
const report=ref(null),year=ref(new Date().getFullYear()),scenario=ref('B'),mode=ref('compare'),unit=ref('m'),period=ref('available'),start=ref(1),end=ref(12),collapsed=ref([]);
const error=ref(''),notice=ref(''),working=ref(false),noteEdit=ref(null),daily=ref(null),dailyMonth=ref(''),dailyBank=ref(''),dailyQuery=ref(''),dailyPage=ref(1);
const progress=ref({active:false,label:'',completed:0,total:0,error:''});
const controller=createOperationProgress(value=>progress.value=value);
let active=true,listTicket=0,reportTicket=0,dailyTicket=0;
const canWrite=computed(()=>props.canEdit&&serverEdit.value);
const canDownload=computed(()=>props.canDownload&&serverDownload.value);
const selectedSource=computed(()=>sources.value.find(source=>String(source.id)===String(sourceId.value)));
const years=computed(()=>sourceYears(selectedSource.value));
const indices=computed(()=>periodMonths(start.value,end.value));
const months=computed(()=>indices.value.map(month=>report.value?.months?.find(item=>Number(item.month)===month)||{month,available:false}));
const actualMonths=computed(()=>months.value.filter(month=>month.available));
const grouped=computed(()=>{
  const result=[];
  for(const row of report.value?.rows||[]){const key=row.group||N_('Прочие статьи');let group=result.find(item=>item.key===key);if(!group){group={key,rows:[]};result.push(group);}group.rows.push(row);}
  return result;
});
const columns=computed(()=>mode.value==='compare'?5:1);
const currencyCaption=computed(()=>`${tx(OSV_UNITS[unit.value].label)} ${report.value?.source?.currency||selectedSource.value?.currency||props.currency}`);
const coverage=computed(()=>actualMonths.value.length?actualMonths.value.at(-1).through||'':'');
const summary=computed(()=>{
  const entries=actualMonths.value;
  if(!entries.length)return [];
  return [
    {label:N_('Остаток на начало периода'),value:entries[0].opening},
    {label:N_('Внешние поступления'),value:sumAmounts(entries.map(item=>item.external_inflow))},
    {label:N_('Внешние выплаты'),value:sumAmounts(entries.map(item=>item.external_outflow))},
    {label:N_('Чистое изменение денег'),value:sumAmounts(entries.map(item=>item.net))},
    {label:N_('Остаток на конец периода'),value:entries.at(-1).closing},
  ];
});
const filteredDaily=computed(()=>{
  const query=dailyQuery.value.trim().toLocaleLowerCase();
  return (daily.value?.rows||[]).filter(row=>(!dailyBank.value||row.bank_label===dailyBank.value)&&(!query||`${row.code} ${row.name}`.toLocaleLowerCase().includes(query)));
});
const dailyBanks=computed(()=>[...new Set((daily.value?.rows||[]).map(row=>row.bank_label).filter(Boolean))]);
const dailyPages=computed(()=>Math.max(1,Math.ceil(filteredDaily.value.length/50)));
const dailyRows=computed(()=>filteredDaily.value.slice((dailyPage.value-1)*50,dailyPage.value*50));
watch([dailyQuery,dailyBank],()=>dailyPage.value=1);
const dayLabel=value=>value?formatDate(value+'T12:00:00',{day:'2-digit',month:'2-digit',year:'numeric'}):'—';
function amount(value){
  if(value==null||value===''||!Number.isFinite(Number(value)))return '—';
  const config=OSV_UNITS[unit.value];
  return new Intl.NumberFormat(intlLocale.value,{minimumFractionDigits:config.digits,maximumFractionDigits:config.digits}).format(Number(value)/config.divisor);
}
function exact(value){return value==null?'':new Intl.NumberFormat(intlLocale.value,{minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(value));}
function percent(fact,plan){return fact==null||plan==null||Number(plan)===0?'—':new Intl.NumberFormat(intlLocale.value,{style:'percent',maximumFractionDigits:1}).format(Number(fact)/Number(plan));}
function delta(row,index){const fact=row.fact?.[index],plan=row.plan?.[index];return fact==null||plan==null?null:Number(plan)-Number(fact);}
function toggleGroup(key){collapsed.value=collapsed.value.includes(key)?collapsed.value.filter(value=>value!==key):[...collapsed.value,key];}
function setPeriod(value){
  period.value=value;
  if(value==='available'){const available=report.value?.months?.filter(month=>month.available)||[];start.value=available[0]?.month||1;end.value=available.at(-1)?.month||12;}
  else if(value==='year'){start.value=1;end.value=12;}
  else if(value!=='custom'){const quarter=Number(value.slice(1));start.value=(quarter-1)*3+1;end.value=quarter*3;}
}
function requireScope(value){if(value?.company_id!=null&&Number(value.company_id)!==props.companyId)throw Error(N_('Источник относится к другой компании. Обновите страницу.'));}
async function loadSources(preferred){
  const ticket=++listTicket;error.value='';controller.reset();const operation=controller.begin(N_('Загружаем источники Cash Flow'),1);
  try{
    const result=await props.api('/api/cashflow-osv/sources');
    if(!active||ticket!==listTicket)return;
    requireScope(result);sources.value=result.sources||[];serverEdit.value=!!result.can_edit;serverDownload.value=!!result.can_download;loaded.value=true;
    const selected=sources.value.find(item=>String(item.id)===String(preferred||sourceId.value))||sources.value[0];
    controller.complete(operation,N_('Источники Cash Flow загружены'));
    if(selected){sourceId.value=String(selected.id);const available=sourceYears(selected);if(!available.includes(Number(year.value)))year.value=available.at(-1);await loadReport();}
    else {sourceId.value='';report.value=null;showUpload.value=canWrite.value;}
  }catch(e){if(active&&ticket===listTicket&&e.name!=='AbortError'){error.value=e.message;controller.fail(operation,e);}}
}
async function chooseSource(event){
  if(event?.target)sourceId.value=String(event.target.value);
  report.value=null;daily.value=null;noteEdit.value=null;preview.value=null;showUpload.value=false;
  const available=years.value;if(!available.includes(Number(year.value)))year.value=available.at(-1);
  await loadReport();
}
function changeYear(event){if(event?.target)year.value=Number(event.target.value);return loadReport();}
function changeScenario(event){if(event?.target)scenario.value=String(event.target.value);return loadReport();}
function changeDailyMonth(event){if(event?.target)dailyMonth.value=String(event.target.value);return loadDaily();}
async function loadReport(){
  const ticket=++reportTicket,id=sourceId.value,y=Number(year.value),choice=scenario.value;
  dailyTicket++;daily.value=null;noteEdit.value=null;report.value=null;error.value='';
  if(!id||!y)return;
  controller.reset();const operation=controller.begin(N_('Рассчитываем Cash Flow по ОСВ'),1);
  try{
    const result=await props.api(`/api/cashflow-osv/sources/${id}/report?${new URLSearchParams({year:String(y),scenario:choice})}`);
    if(!active||ticket!==reportTicket||sourceId.value!==id||Number(year.value)!==y||scenario.value!==choice)return;
    requireScope(result);requireScope(result.source);
    if(result.source?.id!=null&&String(result.source.id)!==id)throw Error(N_('Источник изменился. Обновите страницу и проверьте выбранную книгу.'));
    report.value=result;
    if(period.value==='available')setPeriod('available');
    controller.complete(operation,N_('Cash Flow по ОСВ рассчитан'));
  }catch(e){if(active&&ticket===reportTicket&&e.name!=='AbortError'){error.value=e.message;controller.fail(operation,e);}}
}
function selectFile(event,type){
  const file=event.target.files?.[0]||null;preview.value=null;error.value='';
  if(type==='bank')bankFile.value=file;else templateFile.value=file;
}
async function checkFiles(){
  if(!canWrite.value||working.value)return;
  if(!bankFile.value){error.value=N_('Добавьте файл ОСВ счёта 5110.');return;}
  for(const file of [bankFile.value,templateFile.value].filter(Boolean))if(!/\.xlsx$/i.test(file.name)||file.size>8*1024*1024){error.value=N_('Нужен XLSX размером до 8 МБ.');return;}
  working.value=true;error.value='';preview.value=null;notice.value='';
  controller.reset();const operation=controller.begin(N_('Читаем файлы Cash Flow'),3);
  try{
    const payload={bank_filename:bankFile.value.name,bank_base64:await fileBase64(bankFile.value)};
    if(templateFile.value){payload.template_filename=templateFile.value.name;payload.template_base64=await fileBase64(templateFile.value);}
    controller.advance(operation,1,N_('Проверяем статьи и остатки'));
    const result=await props.api('/api/cashflow-osv/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(!active)return;requireScope(result);preview.value=result;reason.value='';controller.complete(operation,N_('Проверка файлов завершена'));
  }catch(e){if(active&&e.name!=='AbortError'){error.value=e.message;controller.fail(operation,e);}}
  finally{if(active)working.value=false;}
}
async function commit(){
  if(!canWrite.value||working.value||!preview.value?.can_commit||reason.value.trim().length<10)return;
  working.value=true;error.value='';
  controller.reset();const operation=controller.begin(N_('Сохраняем источник Cash Flow'),2);
  try{
    const result=await props.api(`/api/cashflow-osv/preview/${preview.value.id}/commit`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:preview.value.version,currency:uploadCurrency.value,scale:Number(scale.value),reason:reason.value.trim()})});
    if(!active)return;requireScope(result);requireScope(result.source);const id=result.source?.id;
    preview.value=null;showUpload.value=false;bankFile.value=null;templateFile.value=null;
    notice.value=result.idempotent?N_('Этот источник уже сохранён. Открыта существующая версия.'):N_('Источник сохранён. Денежные операции и остатки сайта не изменены.');
    controller.advance(operation,1,N_('Открываем сохранённый отчёт'));await loadSources(id);controller.complete(operation,N_('Источник Cash Flow сохранён'));
  }catch(e){if(active&&e.name!=='AbortError'){error.value=e.message;controller.fail(operation,e);}}
  finally{if(active)working.value=false;}
}
function editNote(row,month){if(!canWrite.value)return;noteEdit.value={code:row.code,name:row.name,month:`${year.value}-${String(month).padStart(2,'0')}`,text:row.notes?.[month-1]||''};}
async function saveNote(){
  if(!canWrite.value||!noteEdit.value||working.value)return;working.value=true;error.value='';
  try{
    const value={...noteEdit.value};await props.api(`/api/cashflow-osv/sources/${sourceId.value}/notes`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:report.value.notes_version,month:value.month,code:value.code,text:value.text})});
    if(!active)return;noteEdit.value=null;notice.value=N_('Примечание сохранено');await loadReport();
  }catch(e){if(active&&e.name!=='AbortError')error.value=e.message;}finally{if(active)working.value=false;}
}
async function loadDaily(){
  if(!dailyMonth.value||!sourceId.value)return;
  const ticket=++dailyTicket,id=sourceId.value,month=dailyMonth.value;daily.value=null;error.value='';
  controller.reset();const operation=controller.begin(N_('Загружаем дневные обороты'),1);
  try{
    const result=await props.api(`/api/cashflow-osv/sources/${id}/daily?month=${month}`);
    if(!active||ticket!==dailyTicket||sourceId.value!==id||dailyMonth.value!==month)return;
    requireScope(result);daily.value=result;dailyPage.value=1;dailyBank.value='';dailyQuery.value='';controller.complete(operation,N_('Дневные обороты загружены'));
  }catch(e){if(active&&ticket===dailyTicket&&e.name!=='AbortError'){error.value=e.message;controller.fail(operation,e);}}
}
const exportUrl=computed(()=>osvExportUrl(sourceId.value,year.value,scenario.value,props.companyId,start.value,end.value));
watch(()=>props.canEdit,value=>{if(!value){showUpload.value=false;preview.value=null;noteEdit.value=null;}});
onMounted(()=>loadSources());
onUnmounted(()=>{active=false;listTicket++;reportTicket++;dailyTicket++;controller.reset();});
</script>

<template>
 <div class="osv-cashflow">
  <OperationProgress v-bind="progress"/>
  <p v-if="error" class="error" role="alert">{{tx(error)}}</p>
  <p v-if="notice" class="notice" role="status">{{tx(notice)}}</p>
  <section class="card source-panel">
   <div class="section-head"><div><h2>{{tx('Cash Flow по ОСВ 5110')}}</h2><p class="sub">{{companyName}} · {{tx('Отдельный источник факта для отчёта')}}</p></div><button v-if="canWrite" type="button" class="secondary" :disabled="working" @click="showUpload=!showUpload;preview=null">{{showUpload?tx('Закрыть загрузку'):tx('Обновить исходные данные')}}</button></div>
   <div v-if="sources.length" class="filter-bar"><label class="source-choice">{{tx('Сохранённый источник')}}<select v-model="sourceId" :disabled="working" @change="chooseSource"><option v-for="source in sources" :key="source.id" :value="String(source.id)">{{source.filename}} · {{dayLabel(source.last_date)}} · {{source.currency}}</option></select></label><span class="sub">{{tx('Повторная загрузка не нужна: выберите сохранённую версию.')}}</span></div>
   <p v-else-if="loaded&&!canWrite" class="sub">{{tx('Источник пока не загружен. Его может добавить сотрудник с правом изменения Cash Flow.')}}</p>
   <p v-if="showUpload||!sources.length" class="sub">{{tx('ОСВ используется для отчёта. Загрузка не создаёт платежи, заявки и доступные к оплате деньги.')}}</p>
   <form v-if="showUpload&&canWrite" class="upload-form" @submit.prevent="checkFiles">
    <div class="file-grid"><label>{{tx('ОСВ счёта 5110')}} <span class="required-mark">*</span><input type="file" accept=".xlsx" :disabled="working" @change="selectFile($event,'bank')"><small>{{tx('Экспорт из 1С с банковскими счетами, статьями ДДС и дневными оборотами.')}}</small></label><label>{{tx('Образец Cash Flow')}}<input type="file" accept=".xlsx" :disabled="working" @change="selectFile($event,'template')"><small>{{tx('Необязательно. Даёт статьи, планы А / Б / В и оформление план-факта.')}}</small></label></div>
    <div class="filter-bar"><label>{{tx('Валюта источника')}}<select v-model="uploadCurrency" :disabled="working"><option>UZS</option><option>USD</option><option>EUR</option></select></label><label>{{tx('Суммы в исходном файле')}}<select v-model="scale" :disabled="working"><option v-for="option in OSV_SCALES" :key="option.value" :value="option.value">{{tx(option.label)}}</option></select></label><button :disabled="working||!bankFile" type="submit">{{tx('Проверить файлы')}}</button></div>
    <p class="sub">{{tx('Единицы исходника задаются до сохранения. Масштаб таблицы можно менять отдельно.')}}</p>
   </form>
   <div v-if="preview&&canWrite" class="preview-panel">
    <h3>{{tx('Проверка перед сохранением')}}</h3><p>{{tx('Банков: {banks}. Дневных строк: {rows}. Статей: {categories}.',{banks:preview.metadata?.bank_count??preview.metadata?.banks?.length??'—',rows:preview.metadata?.record_count??'—',categories:preview.categories_count??'—'})}}</p><p class="sub">{{dayLabel(preview.metadata?.first_date)}} — {{dayLabel(preview.metadata?.last_date)}} · {{tx('Суммы ниже показаны в единицах исходного файла.')}}</p>
    <dl class="preview-totals"><dt>{{tx('Остаток на начало')}}</dt><dd>{{exact(preview.metadata?.opening)}}</dd><dt>{{tx('Поступления по банкам')}}</dt><dd>{{exact(preview.metadata?.inflow)}}</dd><dt>{{tx('Выплаты по банкам')}}</dt><dd>{{exact(preview.metadata?.outflow)}}</dd><dt>{{tx('Остаток на конец')}}</dt><dd>{{exact(preview.metadata?.closing)}}</dd><dt>{{tx('Внутренние переводы: приход / расход')}}</dt><dd>{{exact(preview.metadata?.internal_inflow)}} / {{exact(preview.metadata?.internal_outflow)}}</dd></dl>
    <details v-if="preview.issues?.length"><summary>{{tx('Замечаний: {n}',{n:preview.issues.length})}}</summary><ul class="issues"><li v-for="(issue,index) in preview.issues" :key="index" :class="{error:issue.level==='error'}">{{tx(issue.message)}}<small v-if="issue.cell">{{issue.cell}}</small></li></ul></details>
    <label>{{tx('Основание сохранения')}}<textarea v-model="reason" minlength="10" maxlength="1000" :disabled="working" :placeholder="tx('Например: обновление факта по выписке за выбранный период')"></textarea></label>
    <div class="form-actions"><button type="button" class="secondary" :disabled="working" @click="preview=null">{{tx('Отменить')}}</button><button type="button" :disabled="working||!preview.can_commit||reason.trim().length<10" @click="commit">{{tx('Сохранить источник и открыть отчёт')}}</button></div>
   </div>
  </section>

  <template v-if="selectedSource">
   <div class="filter-bar report-controls"><label>{{tx('Год')}}<select v-model="year" :disabled="working" @change="changeYear"><option v-for="value in years" :key="value" :value="value">{{value}}</option></select></label><label>{{tx('Сценарий')}}<select v-model="scenario" :disabled="working" @change="changeScenario"><option value="A">{{tx('А')}}</option><option value="B">{{tx('Б')}}</option><option value="V">{{tx('В')}}</option></select></label><div class="seg" role="group" :aria-label="tx('Вид отчёта')"><button type="button" :aria-pressed="mode==='actual'" :class="{on:mode==='actual'}" @click="mode='actual'">{{tx('Факт')}}</button><button type="button" :aria-pressed="mode==='compare'" :class="{on:mode==='compare'}" @click="mode='compare'">{{tx('План-факт')}}</button></div><label>{{tx('Масштаб')}}<select v-model="unit"><option v-for="(option,key) in OSV_UNITS" :key="key" :value="key">{{tx(option.label)}}</option></select></label><a v-if="canDownload&&report&&indices.length" class="button secondary" :href="exportUrl"><AppIcon name="download"/> Excel</a></div>
   <div class="filter-bar"><label>{{tx('Период')}}<select :value="period" @change="setPeriod($event.target.value)"><option value="available">{{tx('Загруженный период')}}</option><option value="year">{{tx('Весь год')}}</option><option v-for="quarter in 4" :key="quarter" :value="'q'+quarter">{{tx('{quarter} квартал',{quarter})}}</option><option value="custom">{{tx('Свой период')}}</option></select></label><template v-if="period==='custom'"><label>{{tx('С месяца')}}<select v-model="start"><option v-for="(name,index) in OSV_MONTHS" :key="index" :value="index+1">{{tx(name)}}</option></select></label><label>{{tx('По месяц')}}<select v-model="end"><option v-for="(name,index) in OSV_MONTHS" :key="index" :value="index+1">{{tx(name)}}</option></select></label></template><p class="sub">{{currencyCaption}}</p></div>
   <p v-if="!indices.length" class="error" role="alert">{{tx('Начальный месяц не может быть позже конечного.')}}</p>
  </template>
  <template v-if="report&&indices.length">
   <p v-if="coverage" class="sub">{{tx('Факт по {date}. Будущие и незагруженные месяцы оставлены пустыми.',{date:dayLabel(coverage)})}}</p>
   <div class="summary-grid"><div v-for="item in summary" :key="item.label" class="card tile"><span class="sub">{{tx(item.label)}}</span><strong :title="exact(item.value)">{{amount(item.value)}}</strong><small>{{currencyCaption}}</small></div></div>
   <div class="section-head"><h3>{{tx('Движение денежных средств по статьям')}}</h3><div class="actions"><button type="button" class="ghost tiny" @click="collapsed=[]">{{tx('Развернуть всё')}}</button><button type="button" class="ghost tiny" @click="collapsed=grouped.map(group=>group.key)">{{tx('Свернуть всё')}}</button></div></div>
   <div class="osv-table-scroll" tabindex="0" role="region" :aria-label="tx('Cash Flow по статьям и месяцам')"><table class="osv-table"><thead><tr><th class="article" :rowspan="mode==='compare'?2:1">{{tx('Код / статья')}}</th><th v-for="item in months" :key="item.month" :colspan="columns" class="month"><span>{{tx(OSV_MONTHS[item.month-1])}} {{year}}</span><small v-if="!item.available">{{tx('Нет данных')}}</small><small v-else-if="item.partial">{{tx('по {date}',{date:dayLabel(item.through)})}}</small></th></tr><tr v-if="mode==='compare'"><template v-for="item in months" :key="item.month"><th>{{tx('План')}}</th><th>{{tx('Факт')}}</th><th>{{tx('План − факт')}}</th><th>{{tx('Факт (%)')}}</th><th>{{tx('Примечание')}}</th></template></tr></thead><tbody>
    <template v-for="group in grouped" :key="group.key"><tr class="group-row"><th :colspan="1+months.length*columns"><button type="button" :aria-expanded="!collapsed.includes(group.key)" @click="toggleGroup(group.key)"><span aria-hidden="true">{{collapsed.includes(group.key)?'+':'−'}}</span> {{group.key}}</button></th></tr><template v-if="!collapsed.includes(group.key)"><tr v-for="row in group.rows" :key="row.code" :class="{receipt:row.kind==='receipt',total:row.kind==='net'}"><th class="article"><small>{{row.code}}</small><span>{{row.name}}</span></th><template v-for="item in months" :key="item.month"><template v-if="mode==='compare'"><td class="num" :title="row.plan_status?.[item.month-1]==='error'?tx('В исходном плане ошибка формулы'):exact(row.plan?.[item.month-1])">{{amount(row.plan?.[item.month-1])}}</td><td class="num" :title="exact(row.fact?.[item.month-1])">{{item.available?amount(row.fact?.[item.month-1]):'—'}}</td><td class="num" :class="{negative:Number(delta(row,item.month-1))<0}" :title="exact(delta(row,item.month-1))">{{item.available?amount(delta(row,item.month-1)):'—'}}</td><td class="num">{{item.available?percent(row.fact?.[item.month-1],row.plan?.[item.month-1]):'—'}}</td><td class="note-cell"><span>{{row.notes?.[item.month-1]}}</span><button v-if="canWrite" type="button" class="ghost tiny" :aria-label="tx('Изменить примечание: {article}, {month}',{article:row.name,month:tx(OSV_MONTHS[item.month-1])})" @click="editNote(row,item.month)"><AppIcon name="edit"/></button></td></template><td v-else class="num" :title="exact(row.fact?.[item.month-1])">{{item.available?amount(row.fact?.[item.month-1]):'—'}}</td></template></tr></template></template>
   </tbody></table></div>
   <form v-if="noteEdit&&canWrite" class="card note-editor" @submit.prevent="saveNote"><h3>{{tx('Примечание')}} · {{noteEdit.name}} · {{noteEdit.month}}</h3><textarea v-model="noteEdit.text" maxlength="2000" :disabled="working" :aria-label="tx('Примечание к статье')"></textarea><div class="form-actions"><button type="button" class="secondary" :disabled="working" @click="noteEdit=null">{{tx('Отменить')}}</button><button :disabled="working">{{tx('Сохранить примечание')}}</button></div></form>
   <details class="card report-help"><summary>{{tx('Методика и проверка источника')}}</summary><p>{{tx('Расход по статье = кредит − дебет; поступление = дебет − кредит. Возвраты учитываются один раз.')}}</p><p>{{tx('Отклонение = план − факт, как в образце. Процент = факт / план; при нулевом плане процент не рассчитывается.')}}</p><p>{{tx('Внутренние переводы показаны в детализации и исключены из внешних поступлений и выплат.')}}</p><p>{{tx('Исходник')}}: {{selectedSource.filename}}<template v-if="selectedSource.template_filename"> · {{selectedSource.template_filename}}</template></p><ul v-if="report.issues?.length" class="issues"><li v-for="(issue,index) in report.issues" :key="index">{{tx(issue.message)}}<small v-if="issue.cell">{{issue.cell}}</small></li></ul></details>
   <section class="card daily-panel"><div class="section-head"><h3>{{tx('Дневная детализация')}}</h3><button v-if="daily" type="button" class="ghost tiny" @click="daily=null;dailyMonth='';dailyTicket++">{{tx('Закрыть')}}</button></div><div class="filter-bar"><label>{{tx('Месяц')}}<select v-model="dailyMonth" @change="changeDailyMonth"><option value="">{{tx('Выберите месяц')}}</option><option v-for="item in report.months.filter(value=>value.available)" :key="item.key" :value="item.key">{{tx(OSV_MONTHS[item.month-1])}} {{year}}</option></select></label><template v-if="daily"><label>{{tx('Банк')}}<select v-model="dailyBank"><option value="">{{tx('Все банки')}}</option><option v-for="bank in dailyBanks" :key="bank">{{bank}}</option></select></label><label>{{tx('Код или название статьи')}}<input v-model="dailyQuery" type="search"></label></template></div><template v-if="daily"><p class="sub">{{tx('Остаток на начало')}}: {{amount(daily.opening)}} · {{tx('Остаток на конец')}}: {{amount(daily.closing)}} · {{currencyCaption}}</p><div class="table-scroll"><table><thead><tr><th>{{tx('Дата')}}</th><th>{{tx('Банк')}}</th><th>{{tx('Статья')}}</th><th class="num">{{tx('Дебет · поступление')}}</th><th class="num">{{tx('Кредит · выплата')}}</th><th>{{tx('Строка исходника')}}</th></tr></thead><tbody><tr v-for="row in dailyRows" :key="row.source_row"><td>{{dayLabel(row.date)}}</td><td>{{row.bank_label}}</td><td><b>{{row.code}}</b> {{row.name}}<small v-if="row.internal">{{tx('Внутренний перевод')}}</small></td><td class="num">{{amount(row.debit)}}</td><td class="num">{{amount(row.credit)}}</td><td>{{row.source_row}}</td></tr></tbody></table></div><div class="daily-pages"><span>{{tx('Строк: {count}',{count:filteredDaily.length})}}</span><button type="button" class="secondary tiny" :disabled="dailyPage<=1" @click="dailyPage--">{{tx('Назад')}}</button><span>{{dailyPage}} / {{dailyPages}}</span><button type="button" class="secondary tiny" :disabled="dailyPage>=dailyPages" @click="dailyPage++">{{tx('Далее')}}</button></div></template></section>
  </template>
 </div>
</template>

<style scoped>
.osv-cashflow{display:grid;gap:16px}.source-panel,.preview-panel,.note-editor,.daily-panel,.report-help{padding:20px}.section-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.section-head h2,.section-head h3{margin:0}.source-choice{flex:1;min-width:220px}.source-choice select{width:100%}.upload-form{border-top:1px solid var(--line);margin-top:16px;padding-top:16px}.file-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.file-grid label{display:grid;gap:8px;min-width:0}.file-grid input{max-width:100%;font-size:13px}.file-grid small,.issues small{display:block;color:var(--muted);line-height:1.5}.required-mark{color:var(--bad)}.filter-bar{display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin:14px 0}.filter-bar>label{display:grid;gap:6px}.filter-bar input,.filter-bar select{max-width:100%}.preview-panel{margin-top:16px;border:1px solid var(--line);border-radius:12px}.preview-totals{display:grid;grid-template-columns:minmax(160px,1fr) auto;gap:8px;max-width:700px}.preview-totals dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}.preview-panel textarea,.note-editor textarea{display:block;width:100%;min-height:80px;margin-top:8px}.form-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:14px;flex-wrap:wrap}.issues{padding-left:20px;line-height:1.6}.summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.summary-grid .tile{display:grid;gap:8px;padding:16px;min-width:0}.summary-grid strong{font-size:24px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.summary-grid small{color:var(--muted)}.osv-table-scroll{overflow:auto;max-height:72vh;border:1px solid var(--line);border-radius:12px;background:var(--bg2,#fff)}.osv-table{border-collapse:separate;border-spacing:0;width:max-content;min-width:100%;font-size:13px}.osv-table th,.osv-table td{padding:11px 12px;border-bottom:1px solid var(--line)}.osv-table thead th{background:var(--dark,#182139);color:#fff;text-align:center;position:sticky;top:0;z-index:3;white-space:nowrap;min-width:135px}.osv-table thead tr:nth-child(2) th{top:53px;font-weight:500}.osv-table .article{position:sticky;left:0;z-index:2;min-width:310px;max-width:380px;width:350px;white-space:normal;text-align:left;background:var(--bg2,#fff)}.osv-table thead .article{z-index:5;background:var(--dark,#182139)}.osv-table .article span,.osv-table .article small,.month small,.note-cell span{display:block}.osv-table .article small{font-weight:400;color:var(--muted);margin-bottom:5px}.osv-table .month small{font-weight:400;font-size:11px;line-height:1.5}.osv-table td.num{font-variant-numeric:tabular-nums;white-space:nowrap;text-align:right;min-width:135px}.osv-table .note-cell{min-width:180px;max-width:260px;white-space:normal;vertical-align:top}.note-cell button{margin-top:5px}.osv-table .group-row th{padding:0;background:var(--emx,#eef1ff);text-align:left}.group-row button{width:100%;text-align:left;border:0;border-radius:0;background:transparent;color:var(--text,#15203a);padding:12px 14px;font-weight:700;position:sticky;left:0;max-width:calc(100vw - 420px);white-space:normal}.osv-table .total th,.osv-table .total td{font-weight:700;background:var(--emx,#eef1ff)}.negative{color:var(--bad,#b42318)}.report-help summary{cursor:pointer;font-weight:700}.report-help p{line-height:1.6}.daily-panel td{vertical-align:top}.daily-panel small{display:block;color:var(--muted)}.daily-pages{display:flex;align-items:center;justify-content:flex-end;gap:12px;flex-wrap:wrap;margin-top:14px}
@media(max-width:900px){.summary-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.file-grid{grid-template-columns:1fr}.group-row button{max-width:80vw}.osv-table .article{min-width:210px;max-width:260px}.source-panel,.daily-panel,.report-help{padding:14px}.preview-panel{padding:12px}}
@media(max-width:520px){.summary-grid{grid-template-columns:1fr}.summary-grid strong{font-size:23px}.preview-totals{grid-template-columns:1fr}.preview-totals dd{text-align:left;margin-bottom:8px}.osv-table .article{min-width:170px;max-width:210px}.section-head>button{width:100%}.form-actions>button{width:100%}}
</style>
