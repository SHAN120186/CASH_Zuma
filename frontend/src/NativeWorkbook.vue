<script setup>
import {ref, computed, watch, nextTick, onUnmounted} from 'vue';
import AppIcon from './AppIcon.vue';
import NativeTemplateInputs from './NativeTemplateInputs.vue';
import OperationProgress from './OperationProgress.vue';
import {createOperationProgress} from './operationProgress.js';
import {archiveFileUrl} from './reportArchive.js';
import {assertProject, decimalShift, numericText} from './businessProjects.js';
import {tx, N_} from './i18n/index.js';
const props = defineProps({api:Function, project:Object, company:Object, stage:{type:String,default:'data'}});
const emit = defineEmits(['updated','generated','working','report','data']);
const parameters = ref({}), busy = ref(false), error = ref(''), reportStart = ref(''), previousGeneration = ref(null);
const nativeForm=ref(null), validationAttempted=ref(false), periodAttempted=ref(false);
const reviewPanel=ref(null), reviewState=ref(null);
const progress=ref({active:false,label:'',completed:0,total:0,error:''});
const operation=createOperationProgress(value=>{progress.value=value;});
let active = true;
const model = computed(() => props.project.native_model);
const canEdit = computed(() => props.project.company_id === props.company.id && props.project.can_edit && props.project.status !== 'deleted');
const isPercent = parameter => parameter.unit === 'доля'; // i18n-ignore
const displayValue = (parameter, value) => value == null ? '' : isPercent(parameter) ? decimalShift(value,2) : value;
watch(() => model.value, value => {if(!value?.input_review)reviewState.value=null;parameters.value = Object.fromEntries((value?.parameters || []).map(p => [p.key,displayValue(p,value.overrides?.[p.key] ?? p.value)]));}, {immediate:true});
watch(() => props.project.id+'|'+props.company.id+'|'+props.project.inputs?.start, () => {reportStart.value = props.project.inputs?.start?.slice(0,7)||'';}, {immediate:true});
watch(busy,value=>emit('working',value));
watch(()=>props.project.id+'|'+props.company.id,()=>{operation.reset();busy.value=false;error.value='';reviewState.value=null;validationAttempted.value=false;periodAttempted.value=false;});
onUnmounted(() => {active=false;operation.reset();emit('working',false);});
const groups = computed(() => {
  const definitions = [
    {id:'financing',title:tx('1. Финансирование'),hint:tx('Курс, новый кредит и годовая ставка. Ставка также влияет на NPV оригинала.'),cells:['B38','D28','AA6']},
    {id:'production',title:tx('2. Производство'),hint:tx('Загрузка первого месяца — в %. Прирост — в процентных пунктах за год.'),cells:['B19','Q19']},
    {id:'tax',title:tx('3. НДС'),hint:tx('Ставка в %. Выручка с НДС и без НДС рассчитывается отдельно.'),cells:['F4']},
    {id:'working-capital',title:tx('4. Оборотный капитал'),hint:tx('Сроки поступлений и покрытия запасов, в днях. Ноль для этих формул не допускается.'),cells:['B5','B6','B7','B10']},
  ];
  for (const p of model.value?.parameters || []) {
    if (p.editable === false) continue;
    const group=definitions.find(group=>group.cells.includes(p.cell)) || definitions[0];
    group.parameters ||= []; group.parameters.push(p);
  }
  return definitions;
});
const locked = computed(() => (model.value?.parameters || []).filter(p=>p.editable===false));
const missingParameters=computed(()=>(model.value?.parameters||[]).filter(p=>p.editable!==false&&!numericText(parameters.value[p.key])).map(p=>({field:p.key,label:p.label})));
const unresolvedReview=computed(()=>reviewState.value?.unresolved||(model.value?.input_review?.items||[]).filter(item=>item.requires_decision&&!item.decision).map(item=>({key:item.key,label:item.label})));
const missingFields=computed(()=>[...missingParameters.value,...unresolvedReview.value.filter(item=>!missingParameters.value.some(parameter=>parameter.field===item.key)).map(item=>({field:item.key,label:item.label})),...(!reportStart.value?[{field:'period',label:N_('Первый месяц периода в архиве')}]:[])]);
const fieldInvalid=field=>field==='period'?periodAttempted.value&&!reportStart.value:validationAttempted.value&&missingParameters.value.some(item=>item.field===field);
const missingId=field=>'native-missing-'+encodeURIComponent(field);
async function goToField(field){emit(field==='period'?'report':'data');await nextTick();if(unresolvedReview.value.some(item=>item.key===field)&&reviewPanel.value?.goToField){await reviewPanel.value.goToField(field);return;}if(typeof nativeForm.value?.querySelectorAll!=='function')return;const target=Array.from(nativeForm.value.querySelectorAll('[data-native-field]')).find(node=>node.getAttribute('data-native-field')===field);if(!target)return;let container=target.parentElement;while(container){if(container.tagName==='DETAILS')container.open=true;container=container.parentElement;}target.focus?.({preventScroll:true});target.scrollIntoView?.({behavior:'smooth',block:'center'});}
const generation = computed(() => (props.project.generations||[]).find(g=>g.native&&g.id===props.project.current_generation_id)
  || (previousGeneration.value?.projectId===props.project.id ? (props.project.generations||[]).find(g=>g.native&&g.id===previousGeneration.value.id) : null)
  || (props.project.current_generation_id==null ? (props.project.generations||[]).filter(g=>g.native).slice().sort((a,b)=>b.id-a.id)[0] : null));
function currentNativeReport(project,companyId) {
  if(project?.company_id!==companyId||project.status!=='ready'||!Number.isSafeInteger(project.revision)||project.revision<1||!Number.isSafeInteger(project.current_generation_id)||project.current_generation_id<1)return false;
  const current=(project.generations||[]).find(item=>item.native&&item.id===project.current_generation_id),archive=current?.business_archive;
  return current?.revision===project.revision&&Number.isSafeInteger(archive?.id)&&archive.id>0&&archive.company_id===companyId&&archive.status==='ready';
}
const files = computed(() => generation.value && props.project.company_id===props.company.id ? [['business_archive',N_('Бизнес-план')],['teo_archive',N_('ТЭО')]].flatMap(([key,title])=>{
  const archive=generation.value[key];return Number.isSafeInteger(archive?.id)&&archive.id>0&&archive.company_id===props.company.id&&archive.status==='ready'?(key==='business_archive'?['pdf','xlsx']:['pdf']).map(format=>({title:`${tx(title)} · ${format==='pdf'?'PDF':'Excel'}`,format,secondary:key==='teo_archive',url:archiveFileUrl(archive,format)})):[];
}):[]);
const changed = computed(() => !!reviewState.value?.changed || (model.value?.parameters || []).some(p => p.editable!==false && numericText(parameters.value[p.key]) !== numericText(displayValue(p,model.value.overrides?.[p.key] ?? p.value))));
const stale = computed(() => changed.value || !currentNativeReport(props.project,props.company.id) || generation.value?.id!==props.project.current_generation_id || reportStart.value+'-01'!==props.project.inputs?.start);
const download = computed(() => `/api/business-projects/${props.project.id}/native.xlsx?company_id=${props.company.id}&revision=${props.project.revision}`);
const kpis = computed(() => [['Стоим_проекта','F34',N_('Стоимость проекта')],['ВНД','D6',N_('NPV за три года')],['ВНД','E6',N_('IRR за три года')]].map(([sheet,cell,label]) => ({label,metric:(model.value?.metrics||[]).find(metric=>metric.sheet===sheet&&metric.cell===cell)}))); // i18n-ignore: sheet names are matched
const history = computed(() => (props.project.generations||[]).filter(g=>g.native&&g.id!==generation.value?.id).slice().sort((a,b)=>b.id-a.id));
function number(value, unit) {
  if (value == null || String(value).trim()==='') return tx('Нет результата');
  if (typeof value === 'string' && value.startsWith('#')) return value;
  if (!Number.isFinite(Number(value))) return tx('Нет результата');
  return new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(Number(value) * (unit === '%' ? 100 : 1)) + (unit ? ' ' + tx(unit) : '');
}
function overrides() {
  return Object.fromEntries((model.value.parameters||[]).filter(p=>p.editable!==false).map(p=>{
    const text=numericText(parameters.value[p.key]);
    if (!text || !Number.isFinite(Number(text))) throw Error(tx('Заполните числовое значение: {label}. Пустое поле не означает ноль.',{label:tx(p.label)}));
    const converted=isPercent(p)?decimalShift(text,-2):text,value=Number(converted);
    if(value===0 && /[1-9]/.test(converted.replace(/[eE][+-]?\d+$/,'')))throw Error(tx('Значение слишком мало для расчёта: {label}.',{label:tx(p.label)}));
    if (p.min!=null&&value<p.min || p.max!=null&&value>p.max) throw Error(tx('Значение вне допустимого диапазона: {label}.',{label:tx(p.label)}));
    return [p.key,value];
  }));
}
const options = body => ({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
function reviewPayload(){if(!model.value?.input_review)return {};return {data_updates:reviewState.value?.data_updates||model.value.data_updates||{},review_decisions:reviewState.value?.review_decisions||model.value.review_decisions||{},confirm_source_basis:reviewState.value?.confirm_source_basis??!!model.value.confirm_source_basis,...(model.value.source_sha256?{source_sha256:model.value.source_sha256}:{})};}
function reviewedOverrides(){return {...overrides(),...(reviewState.value?.scalars||{})};}
function parameterEdited(parameter,event){reviewPanel.value?.setParameter?.(parameter.key,event.target.value);}
function context() {
  const id=props.project.id,companyId=props.company.id;
  return {id,companyId,check:()=>{if(!active||props.project.id!==id||props.company.id!==companyId)throw new DOMException(tx('Раздел или компания изменены'),'AbortError');}};
}
async function preview(c,values) {
  const updated=await props.api(`/api/business-projects/${c.id}/native/preview?company_id=${c.companyId}`,options({revision:props.project.revision,overrides:values,...reviewPayload()}));
  c.check();assertProject(updated,c.companyId,c.id);
  if(!Number.isSafeInteger(updated.revision))throw Error(N_('Сервер не вернул версию пересчёта. Обновите проект.'));
  emit('updated',updated);return updated;
}
async function calculate() {
  if(busy.value||!canEdit.value)return;
  validationAttempted.value=true;
  if(missingParameters.value.length||unresolvedReview.value.length){const missing=[...missingParameters.value,...unresolvedReview.value.map(item=>({field:item.key,label:item.label}))];error.value=tx('Заполните поля: {fields}. Пустое поле не означает ноль.',{fields:missing.map(item=>tx(item.label)).join(', ')});await goToField(missing[0].field);return;}
  const c=context();busy.value=true;error.value='';
  const ticket=operation.begin(N_('Пересчитываем исходные формулы…'),1);
  try {if(generation.value)previousGeneration.value={projectId:c.id,id:generation.value.id};const checked=await preview(c,reviewedOverrides());if(checked.native_model?.preview?.blocked)operation.fail(ticket,N_('Проверка завершена: исправьте ошибки исходных формул.'));else operation.complete(ticket,N_('Исходные формулы проверены'));}
  catch(e){if(active&&e.name!=='AbortError'&&props.project.id===c.id&&props.company.id===c.companyId){error.value=e.message;operation.fail(ticket,e.message);}}
  finally{if(active&&props.project.id===c.id&&props.company.id===c.companyId)busy.value=false;}
}
async function generate() {
  if(busy.value||!canEdit.value)return;
  validationAttempted.value=true;periodAttempted.value=true;
  if(missingFields.value.length){error.value=tx('Заполните поля: {fields}. Пустое поле не означает ноль.',{fields:missingFields.value.map(item=>tx(item.label)).join(', ')});await goToField(missingFields.value[0].field);return;}
  const c=context(),start=reportStart.value+'-01';busy.value=true;error.value='';
  const ticket=operation.begin(N_('Пересчитываем оригинал и проверяем формулы…'),2);
  try {
    if(generation.value)previousGeneration.value={projectId:c.id,id:generation.value.id};
    const values=reviewedOverrides(),reviewValues=reviewPayload(),checked=await preview(c,values);
    operation.advance(ticket,1,N_('Формируем бизнес-план PDF и Excel…'));
    if(!checked.native_model?.preview)throw Error(N_('Сервер не вернул проверку модели. Повторите подготовку отчёта.'));
    if(checked.native_model.preview.blocked){error.value=N_('В оригинале найдены ошибки, влияющие на финансовый результат. Исправьте указанные формулы перед подготовкой отчёта.');operation.fail(ticket,error.value);return;}
    c.check();
    const updated=await props.api(`/api/business-projects/${c.id}/native/generate?company_id=${c.companyId}`,options({revision:checked.revision,start,overrides:values,...reviewValues}));
    c.check();assertProject(updated,c.companyId,c.id);emit('updated',updated);
    if(currentNativeReport(updated,c.companyId)){operation.complete(ticket,N_('Бизнес-план PDF и Excel готовы'));emit('generated');}
    else{
      const current=(updated.generations||[]).find(item=>item.native&&item.id===updated.current_generation_id&&item.revision===updated.revision),archive=current?.business_archive;
      error.value=Number.isSafeInteger(archive?.id)&&archive.id>0&&archive.company_id===c.companyId&&archive.status==='deleted'
        ?N_('Отчёт удалён из архива. Включите «Показать удалённые» в архиве отчётов и нажмите «Восстановить».')
        :updated.status==='ready'?N_('Для текущей версии нет доступного комплекта PDF и Excel. Проверьте архив отчётов или подготовьте отчёт заново.')
        :N_('Отчёт пока не готов. Проверьте замечания к исходным данным.');
      operation.fail(ticket,error.value);
    }
  } catch(e){if(active&&e.name!=='AbortError'&&props.project.id===c.id&&props.company.id===c.companyId){error.value=e.message;operation.fail(ticket,e.message);}}
  finally{if(active&&props.project.id===c.id&&props.company.id===c.companyId)busy.value=false;}
}
</script>
<template>
  <section v-if="model" class="native-workbook" aria-labelledby="native-model-title">
    <h3 id="native-model-title">{{tx('Расчёт по оригиналу')}}</h3>
    <p class="sub">{{tx('Полный бизнес-план PDF и Excel сохраняют структуру и методику ваших Word и Excel.')}}</p>
    <p class="sub" :hidden="stage!=='report'">{{tx('PDF сохраняет разделы и таблицы Word. При замене шрифтов на сервере переносы и количество страниц могут отличаться от оригинала.')}}</p>
    <OperationProgress v-if="progress.total" v-bind="progress"/>
    <section v-if="canEdit&&missingFields.length" class="native-missing-summary" aria-labelledby="native-missing-title"><h4 id="native-missing-title" aria-live="polite">{{tx('Не заполнены обязательные поля · {n}',{n:missingFields.length})}}</h4><ul><li v-for="item in missingFields" :key="item.field"><button type="button" class="ghost tiny" :disabled="busy" @click="goToField(item.field)">{{tx(item.label)}}</button></li></ul><p class="sub">{{tx('Нажмите название, чтобы перейти к полю. Пустое значение не заменяется нулём.')}}</p></section>
    <p v-if="error" class="error" role="alert">{{tx(error)}}</p>
    <NativeTemplateInputs v-if="canEdit&&model.input_review" ref="reviewPanel" :model="model" :parameters="parameters" :disabled="busy" :stage="stage" @change="reviewState=$event" @parameter="parameters[$event.key]=$event.value" @data="emit('data')"/>
    <p v-if="model.preview?.blocked" class="warning" role="alert">{{tx('В оригинале найдены ошибки, влияющие на финансовый результат. Отчёт нельзя публиковать до их исправления.')}}</p>
    <details v-if="model.preview?.blocked" class="native-blockers"><summary>{{tx('Диагностика формул · {n}',{n:model.preview.error_count})}}</summary><ul><li v-for="(issue,index) in model.preview.issues||[]" :key="index"><b>{{issue.sheet}}!{{issue.cell}}</b> — {{tx(issue.message||issue.error)}}</li></ul></details>
    <form v-if="canEdit" ref="nativeForm" novalidate @submit.prevent="generate">
      <details :hidden="stage==='report'" class="native-advanced" :open="!model.input_review"><summary>{{tx('Дополнительно · основные параметры оригинала')}}</summary><details v-for="group in groups" :key="group.id" class="native-group" :data-native-group="group.id" :open="group.id==='financing'"><summary>{{group.title}}</summary><p class="sub">{{group.hint}}</p><div class="native-parameters"><label v-for="parameter in group.parameters||[]" :key="parameter.key" :class="{'native-field-error':fieldInvalid(parameter.key)}">{{tx(parameter.label)}} <span v-if="parameter.unit">({{isPercent(parameter)?parameter.cell==='Q19'?tx('п.п./год'):'%':tx(parameter.unit)}})</span><input v-model="parameters[parameter.key]" @input="parameterEdited(parameter,$event)" :data-native-field="parameter.key" type="text" inputmode="decimal" required :disabled="busy" :aria-invalid="fieldInvalid(parameter.key)" :aria-describedby="fieldInvalid(parameter.key)?missingId(parameter.key):undefined"><small v-if="fieldInvalid(parameter.key)" :id="missingId(parameter.key)" class="native-field-help">{{tx('Заполните поле «{label}».',{label:tx(parameter.label)})}}</small><small v-if="parameter.cell==='AA6'">{{tx('Эта же ставка применяется для дисконтирования NPV.')}}</small></label></div></details></details>
      <button v-if="stage==='data'" type="button" :disabled="busy" @click="emit('report')">{{tx('Перейти к отчёту')}}</button>
      <label :hidden="stage!=='report'" :class="{'native-field-error':fieldInvalid('period')}">{{tx('Первый месяц периода в архиве')}}<input v-model="reportStart" data-native-field="period" type="month" min="2000-01" max="2097-12" required :disabled="busy" :aria-invalid="fieldInvalid('period')" :aria-describedby="fieldInvalid('period')?missingId('period'):undefined"><small v-if="fieldInvalid('period')" :id="missingId('period')" class="native-field-help">{{tx('Выберите первый месяц периода в архиве.')}}</small><small>{{tx('Выберите период для поиска отчёта. Таблицы сохраняют месяцы 1–36; даты договоров не меняются.')}}</small></label>
      <div class="native-downloads" :hidden="stage!=='report'"><button :disabled="busy"><AppIcon name="file"/> {{busy?tx('Проверяем расчёт и готовим отчёт…'):tx('Подготовить бизнес-план и Excel')}}</button><button type="button" class="secondary" :disabled="busy" @click="emit('data')">{{tx('Вернуться к данным')}}</button></div>
      <p class="sub" :hidden="stage!=='report'">{{tx('Сайт пересчитает формулы, проверит результат и сформирует полный бизнес-план.')}}</p>
    </form>
    <div class="native-kpis"><article v-for="item in kpis" :key="item.label"><small>{{tx(item.label)}}</small><b>{{model.preview&&!changed?number(item.metric?.calculated,item.metric?.unit):tx('Ещё не пересчитано')}}</b></article></div>
    <p v-if="changed" class="warning" role="status">{{tx('Параметры изменены. Показатели и файлы предыдущего расчёта требуют обновления.')}}</p>
    <component :is="stale?'details':'section'" v-if="files.length" :hidden="stage!=='report'" class="native-results"><summary v-if="stale">{{tx('Предыдущая сохранённая версия')}}</summary><h3 v-else>{{tx('Готовый бизнес-план')}}</h3><p v-if="stale" class="warning" role="status">{{tx('Эти файлы относятся к предыдущему расчёту. Для текущих параметров подготовьте новый отчёт.')}}</p><div class="native-downloads"><a v-for="file in files.filter(item=>!item.secondary)" :key="file.title" class="button secondary" :href="file.url"><AppIcon name="download"/> {{file.title}}</a></div>
      <details v-if="files.some(item=>item.secondary)"><summary>{{tx('Дополнительно · ТЭО')}}</summary><div class="native-downloads"><a v-for="file in files.filter(item=>item.secondary)" :key="file.title" class="button secondary" :href="file.url">{{file.title}}</a></div></details>
    </component>
    <details class="native-technical"><summary>{{tx('Проверка расчёта и исходные параметры')}}</summary>
      <p class="sub">{{model.source_name}} · {{tx('{n} листов. Сохраняются исходные таблицы, формулы, оформление и параметры печати.',{n:model.sheet_count})}}</p>
      <button v-if="canEdit" type="button" class="secondary" :disabled="busy" @click="calculate"><AppIcon name="refresh"/> {{tx('Пересчитать оригинал и проверить цифры')}}</button>
      <div class="table-scroll"><table><thead><tr><th>{{tx('Показатель')}}</th><th>{{tx('В исходном Excel')}}</th><th>{{tx('После пересчёта')}}</th><th>{{tx('Ячейка')}}</th></tr></thead><tbody><tr v-for="metric in model.metrics" :key="metric.key"><td>{{tx(metric.label)}}</td><td>{{number(metric.original,metric.unit)}}</td><td>{{model.preview&&!changed?number(metric.calculated,metric.unit):tx('Ещё не пересчитано')}}</td><td>{{metric.sheet}}!{{metric.cell}}</td></tr></tbody></table></div>
      <details v-if="locked.length"><summary>{{tx('Условия кредитного графика · справочно')}}</summary><p class="sub">{{tx('Сроки закреплены в графике исходного Excel. Для изменения загрузите исправленную книгу.')}}</p><p v-for="p in locked" :key="p.key">{{tx(p.label)}}: {{number(p.value,p.unit)}}<small>{{p.sheet}}!{{p.cell}} · {{tx(p.help)}}</small></p></details>
      <template v-if="model.preview"><p class="sub">{{tx('Пересчитано формул: {count}. Ошибок: {errors}.',{count:model.preview.formula_count,errors:model.preview.error_count})}}</p><details v-if="model.preview.issues?.length"><summary>{{tx('Диагностика формул · {n}',{n:model.preview.error_count})}}</summary><ul><li v-for="(issue,index) in model.preview.issues" :key="index"><b>{{issue.sheet}}!{{issue.cell}}</b> — {{tx(issue.message||issue.error)}}<small v-if="issue.formula">{{issue.formula}}</small></li></ul></details><a v-if="canEdit&&!changed&&!files.some(item=>!item.secondary&&item.format==='xlsx')" class="button secondary" :href="download"><AppIcon name="download"/> {{model.preview.blocked?tx('Скачать пересчитанный Excel · для исправления ошибок'):tx('Скачать пересчитанный Excel')}}</a></template>
      <details v-if="model.quality_notes?.length"><summary>{{tx('Замечания к методике оригинала')}}</summary><ul><li v-for="note in model.quality_notes" :key="note">{{tx(note)}}</li></ul></details>
    </details>
    <details v-if="history.length"><summary>{{tx('Предыдущие расчёты · {n}',{n:history.length})}}</summary><div v-for="item in history" :key="item.id"><p>{{tx('Версия исходных данных {n}',{n:item.revision})}}</p><a v-if="item.business_archive?.company_id===company.id&&item.business_archive.status==='ready'" class="button secondary" :href="archiveFileUrl(item.business_archive,'pdf')">{{tx('Бизнес-план · PDF')}}</a></div></details>
  </section>
</template>
<style scoped>
.native-missing-summary{margin:16px 0;padding:14px 16px;border:1px solid var(--line);border-radius:12px;background:var(--emx)}.native-missing-summary h4{margin:0 0 10px}.native-missing-summary ul{padding-left:20px;margin:0}.native-missing-summary button{white-space:normal;text-align:left}.native-field-error input{border-color:#b42318}.native-field-error,.native-workbook .native-field-help{color:#b42318}.native-field-help{line-height:1.5}
.native-workbook [hidden]{display:none!important}
.native-workbook{margin:24px 0;border-top:1px solid var(--line);padding-top:20px}.native-parameters{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin:18px 0}.native-parameters label,.native-workbook form>label{display:grid;gap:6px}.native-workbook table{width:100%;margin:20px 0}.native-workbook th,.native-workbook td{padding:10px;text-align:left;border-bottom:1px solid var(--line);white-space:normal}.native-workbook details{margin:16px 0}.native-workbook li{margin:12px 0;overflow-wrap:anywhere}.native-workbook small{display:block;color:var(--muted);margin-top:6px}.native-workbook .sub{line-height:1.6}.native-workbook form{margin:18px 0}.native-downloads{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}.native-group,.native-technical{border:1px solid var(--line);border-radius:12px;padding:14px}.native-workbook summary{cursor:pointer;font-weight:700}.native-kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:18px 0}.native-kpis article{background:var(--emx);border:1px solid var(--line);border-radius:12px;padding:14px}.native-kpis b{display:block;margin-top:8px;font-size:20px;overflow-wrap:anywhere}.native-results{border-top:1px solid var(--line);padding-top:20px;margin-top:20px}@media(max-width:560px){.native-kpis{grid-template-columns:1fr}.native-parameters{grid-template-columns:1fr}}
</style>
