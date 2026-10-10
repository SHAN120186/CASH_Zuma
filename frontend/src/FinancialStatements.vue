<script setup>
import {ref,computed,watch,onMounted,onUnmounted} from 'vue';
import {tx,N_,intlLocale,formatDate} from './i18n/index.js';
import AppIcon from './AppIcon.vue';
import OperationProgress from './OperationProgress.vue';
import {createOperationProgress} from './operationProgress.js';
import {statementUrl,statementFileProblem,cellNumber,statementCell,statementDeviation,statementNumber,statementStatuses,sourceScopeMatches} from './financialStatements.js';

const props=defineProps({api:{type:Function,required:true},company:{type:Object,required:true},
  type:{type:String,default:'pnl'},canImport:{type:Boolean,default:false},canDownload:{type:Boolean,default:false},refresh:{type:Number,default:0}});
const emit=defineEmits(['editing']);
const payload=ref(null),selected=ref(''),month=ref(null),loading=ref(false),uploading=ref(false),error=ref('');
const progress=ref({active:false,completed:0,total:0,label:'',error:''});
const progressController=createOperationProgress(value=>progress.value=value);
let active=true,requestId=0;
const source=computed(()=>payload.value?.source);
const report=computed(()=>source.value?.reports?.[props.type]);
const periods=computed(()=>report.value?.periods || []);
const normValue=row=>typeof row.norm==='object'?row.norm?.status==='author_guideline'?row.norm.value:null:row.norm;
const normSource=row=>row.norm?.source || row.norm_source;
const hasNorms=computed(()=>report.value?.rows?.some(row=>row.norm!=null));
const sourceItems=computed(()=>payload.value?.sources || []);
const requirements=computed(()=>source.value?.requirements || payload.value?.requirements || []);
const issues=computed(()=>source.value?.issues || []);
const coverage=computed(()=>source.value?.coverage?.find(item=>Number(item.month)===Number(month.value)));
const coverageLabels={missing:N_('Нет исходной ОСВ за месяц'),available:N_('Операции в источнике есть'),future:N_('Будущий период'),invalid:N_('Проверьте даты операций')};
const amount=value=>statementNumber(value,'amount',intlLocale.value);
const rowMeasure=row=>row.measure || (row.kind==='ratio'?'percent':row.kind);
const formatCell=(row,cell)=>statementNumber(cellNumber(cell),rowMeasure(row)==='percent'?'ratio':'amount',intlLocale.value)+(cellNumber(cell)!==null&&rowMeasure(row)==='days'?' '+tx('дн.'):'');
const stateLabel=cell=>tx(statementStatuses[cell?.status] || N_('Нет данных'));
const periodLabel=period=>period.period || tx('Месяц {n} · дата не подтверждена',{n:period.month});
const dateLabel=value=>formatDate(value,{timeZone:'Asia/Tashkent',year:'numeric',month:'2-digit',day:'2-digit'}) || '';
const rowCells=row=>statementCell(row,month.value);
const deviation=row=>statementDeviation(rowCells(row).plan,rowCells(row).fact,row.kind);
function resetMonth() {
  if(!periods.value.some(item=>Number(item.month)===Number(month.value))) month.value=periods.value[0]?.month ?? null;
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
    <section class="card">
      <div class="card-h"><div><h3>{{type==='balance'?tx('Баланс · проверка источника'):tx('P&L · план и факт источника')}}</h3><p class="sub">{{company.name}} · {{tx('Проверка исходных данных')}}</p></div></div>
      <div class="card-b">
        <p class="statement-review" role="note">{{tx('Значения из исходной книги требуют проверки. Эта форма не подтверждает соответствие МСФО; найденные расхождения и недостающие документы показаны ниже.')}}</p>
        <div class="statement-controls">
          <label v-if="sourceItems.length">{{tx('Исходная книга')}}<select v-model="selected" :disabled="loading||uploading" @change="load(selected)"><option v-for="item in sourceItems" :key="item.id" :value="String(item.id)">{{item.filename}} · {{dateLabel(item.created_at)}}</option></select></label>
          <label v-if="canImport" class="button secondary statement-upload"><AppIcon name="import"/> {{tx('Добавить исходный Excel')}}<input type="file" accept=".xlsx" hidden :disabled="uploading||loading" :aria-label="tx('Добавить исходный Excel')" @change="upload"></label>
          <button type="button" class="ghost" :disabled="uploading||loading" @click="load(selected)"><AppIcon name="refresh"/> {{tx('Обновить')}}</button>
        </div>
        <OperationProgress v-bind="progress"/>
        <p v-if="error" class="error" role="alert">{{tx(error)}}</p>
        <p v-if="!loading&&!error&&!source" class="empty">{{tx('Исходная книга для P&L и баланса пока не загружена. Нужны листы отчётов, операции и мэппинг счетов.')}}</p>
        <p v-if="!loading&&!error&&!source&&!canImport" class="sub">{{tx('Пользователь с правом импорта должен добавить исходную книгу.')}}</p>
        <template v-if="source">
          <p class="sub statement-source">{{source.filename}} · {{tx('Год')}}: {{source.year??tx('Не указан')}} · {{tx('Валюта источника')}}: {{source.currency||tx('Не указана')}} · {{tx('Единицы источника')}}: {{tx(source.unit||'')}} <span v-if="source.unit_status==='unknown'">· {{tx('Нужно подтвердить единицы')}}</span><span v-if="source.reviewed_as_of"> · {{tx('Дата проверки / среза')}}: {{dateLabel(source.reviewed_as_of)}}</span></p>
          <p v-if="source.reviewed_as_of" class="sub">{{tx('Покрытие факта и будущие периоды определены на дату проверки сохранённой книги. Для обновления среза загрузите обновлённый источник.')}}</p>
          <div v-if="periods.length" class="statement-month">
            <label>{{type==='balance'?tx('Месяц баланса'):tx('Месяц P&L')}}<select v-model="month"><option v-for="period in periods" :key="period.month" :value="period.month">{{periodLabel(period)}}</option></select></label>
            <p class="sub">{{type==='balance'?tx('Остатки показаны за выбранный месяц и не суммируются.'):tx('Показан один месяц. Маржи взяты из источника и не усредняются.')}}</p>
          </div>
          <p v-if="coverage" class="statement-coverage">{{tx('Покрытие факта')}}: {{tx(coverageLabels[coverage.status]||'')}} · {{tx('Записей: {n}',{n:coverage.record_count??'—'})}}</p>
          <div v-if="report?.rows?.length" class="table-scroll statement-table-wrap">
            <table class="statement-table"><caption>{{tx(report.title)}} · {{periodLabel(periods.find(item=>Number(item.month)===Number(month))||{month})}}</caption><thead><tr><th scope="col">{{tx('Показатель')}}</th><th scope="col">{{tx('План')}}</th><th scope="col">{{tx('Факт')}}</th><th scope="col">{{tx('Отклонение')}}</th><th scope="col">{{tx('Отклонение, %')}}</th><th v-if="hasNorms" scope="col">{{tx('Ориентир автора')}}</th><th scope="col">{{tx('Источник и статус')}}</th></tr></thead>
              <tbody><tr v-for="row in report.rows" :key="row.key||row.row" :class="{'statement-section':row.kind==='section'}">
                <th scope="row">{{tx(row.label)}}</th>
                <td>{{row.kind==='section'?'':formatCell(row,rowCells(row).plan)}}</td><td>{{row.kind==='section'?'':formatCell(row,rowCells(row).fact)}}</td>
                <td>{{row.kind==='section'?'':rowMeasure(row)==='percent'&&deviation(row).value!==null?tx('{value} п. п.',{value:amount(deviation(row).value*100)}):amount(deviation(row).value)}}</td>
                <td>{{row.kind==='section'?'':deviation(row).percent===null?'—':amount(deviation(row).percent)+'%'}}</td>
                <td v-if="hasNorms" class="statement-norm">{{row.kind==='section'?'':normValue(row)??'—'}}</td>
                <td><details v-if="row.kind!=='section'"><summary>{{tx('Проверить источник')}}</summary><div v-for="side in ['plan','fact']" :key="side" class="statement-provenance"><b>{{side==='plan'?tx('План'):tx('Факт')}}</b>: {{stateLabel(rowCells(row)[side])}}<small>{{rowCells(row)[side]?.source || report.sheet}}</small><code v-if="rowCells(row)[side]?.formula">{{rowCells(row)[side].formula}}</code></div><div v-if="row.norm!=null" class="statement-provenance"><b>{{tx('Ориентир автора')}}</b>: {{normValue(row)??'—'}}<small v-if="row.norm.status&&row.norm.status!=='author_guideline'">{{stateLabel(row.norm)}}</small><small>{{normSource(row)}}</small></div></details></td>
              </tr></tbody>
            </table>
          </div>
          <p v-else class="empty">{{tx('В исходной книге не найдена форма этого отчёта. Проверьте список расхождений.')}}</p>
          <p v-if="report?.rows?.length" class="sub">{{tx('Отклонение = факт − план. Для маржи показаны процентные пункты. Для нулевого или отрицательного плана относительный процент не определён. Прочерк означает, что значение не подтверждено; явный ноль показан числом.')}}</p>
          <p v-if="hasNorms" class="sub">{{tx('Ориентиры приведены из исходной книги. Их методика и пороги требуют согласования; это не оценка соответствия МСФО.')}}</p>
        </template>
      </div>
    </section>
    <section v-if="issues.length" class="card"><div class="card-h"><h3>{{tx('Что нужно проверить')}}</h3></div><div class="card-b statement-issues"><article v-for="(issue,index) in issues" :key="issue.code+'-'+index" class="statement-issue" :class="issue.severity"><strong>{{tx(issue.title)}}</strong><p>{{tx(issue.detail)}}</p><small>{{issue.source}}</small></article></div></section>
    <section v-if="requirements.length" class="card"><div class="card-h"><h3>{{tx('Исходные данные для регулярного отчёта')}}</h3></div><div class="card-b"><p class="sub">{{tx('Этот список показывает необходимые данные. Загрузка книги сама по себе не подтверждает их полноту.')}}</p><div class="statement-requirements"><article v-for="requirement in requirements" :key="requirement.key"><h4>{{tx(requirement.title)}}</h4><ul><li v-for="field in requirement.fields" :key="field">{{tx(field)}}</li></ul><p><b>{{tx('Когда обновлять')}}:</b> {{tx(requirement.frequency)}}</p><p class="sub">{{tx(requirement.reason)}}</p></article></div></div></section>
  </div>
</template>

<style scoped>
.financial-statements{display:grid;gap:18px}.statement-review{padding:12px 14px;border:1px solid var(--warn,#c58b30);border-radius:10px;background:var(--warnl,#fff8e7);font-size:13px;line-height:1.6}.statement-controls{display:flex;flex-wrap:wrap;align-items:end;gap:12px;margin:16px 0}.statement-controls>label:first-child{flex:1;min-width:190px;max-width:660px}.statement-controls label select{width:100%}.statement-upload{flex:none!important;min-width:0!important}.statement-source{overflow-wrap:anywhere;margin:16px 0}.statement-month{display:flex;align-items:end;gap:18px;flex-wrap:wrap}.statement-month label{min-width:150px}.statement-coverage{font-size:12px;padding:9px 12px;border-radius:8px;background:var(--emx);margin:12px 0}.statement-table{width:100%;border-collapse:collapse;min-width:780px;font-size:12px}.statement-table caption{text-align:left;color:var(--muted);font-size:12px;padding:10px 0}.statement-table th,.statement-table td{padding:11px 12px;border-bottom:1px solid var(--line);vertical-align:top}.statement-table thead th{text-align:left;background:var(--emx)}.statement-table tbody th{text-align:left;min-width:230px;font-weight:500}.statement-table td:nth-child(n+2):nth-child(-n+5){text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}.statement-table .statement-section{background:var(--emx)}.statement-section th{font-weight:700!important}.statement-provenance{margin:10px 0;min-width:170px;font-size:11px}.statement-provenance small,.statement-provenance code{display:block;overflow-wrap:anywhere;white-space:normal;margin-top:4px}.statement-table summary{cursor:pointer;color:var(--em)}.statement-issues{display:grid;gap:10px}.statement-issue{border-left:3px solid var(--warn,#c58b30);padding:10px 12px;background:var(--warnl,#fff8e7);border-radius:6px;overflow-wrap:anywhere}.statement-issue.error{border-color:var(--bad,#b42318)}.statement-issue.info{border-color:var(--em);background:var(--emx)}.statement-issue p{font-size:12px;line-height:1.6;margin:5px 0}.statement-issue small{color:var(--muted)}.statement-requirements{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:16px;margin-top:16px}.statement-requirements article{border:1px solid var(--line);border-radius:10px;padding:14px;overflow-wrap:anywhere}.statement-requirements h4{font-size:13px;margin:0 0 10px}.statement-requirements p,.statement-requirements ul{font-size:12px;line-height:1.6}.statement-requirements ul{padding-left:18px}@media(max-width:600px){.statement-controls>label:first-child{max-width:100%;min-width:0;width:100%}.statement-controls{align-items:stretch}.statement-controls .statement-upload{width:100%}.statement-requirements{grid-template-columns:1fr}.statement-month{align-items:stretch}.statement-month label{width:100%}.statement-month select{width:100%}}
</style>
