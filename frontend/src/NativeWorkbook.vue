<script setup>
import {ref, computed, watch, onUnmounted} from 'vue';
import AppIcon from './AppIcon.vue';
import {archiveFileUrl} from './reportArchive.js';
import {assertProject, decimalShift, numericText} from './businessProjects.js';
const props = defineProps({api:Function, project:Object, company:Object, stage:{type:String,default:'data'}});
const emit = defineEmits(['updated','generated','working','report','data']);
const parameters = ref({}), busy = ref(false), error = ref(''), reportStart = ref(''), previousGeneration = ref(null);
let active = true;
const model = computed(() => props.project.native_model);
const canEdit = computed(() => props.project.company_id === props.company.id && props.project.can_edit && props.project.status !== 'deleted');
const isPercent = parameter => parameter.unit === 'доля';
const displayValue = (parameter, value) => value == null ? '' : isPercent(parameter) ? decimalShift(value,2) : value;
watch(() => model.value, value => {parameters.value = Object.fromEntries((value?.parameters || []).map(p => [p.key,displayValue(p,value.overrides?.[p.key] ?? p.value)]));}, {immediate:true});
watch(() => props.project.id+'|'+props.company.id+'|'+props.project.inputs?.start, () => {reportStart.value = props.project.inputs?.start?.slice(0,7)||'';}, {immediate:true});
watch(busy,value=>emit('working',value));
onUnmounted(() => {active=false;emit('working',false);});
const groups = computed(() => {
  const definitions = [
    {id:'financing',title:'1. Финансирование',hint:'Курс, новый кредит и годовая ставка. Ставка также влияет на NPV оригинала.',cells:['B38','D28','AA6']},
    {id:'production',title:'2. Производство',hint:'Загрузка первого месяца — в %. Прирост — в процентных пунктах за год.',cells:['B19','Q19']},
    {id:'tax',title:'3. НДС',hint:'Ставка в %. Выручка с НДС и без НДС рассчитывается отдельно.',cells:['F4']},
    {id:'working-capital',title:'4. Оборотный капитал',hint:'Сроки поступлений и покрытия запасов, в днях. Ноль для этих формул не допускается.',cells:['B5','B6','B7','B10']},
  ];
  for (const p of model.value?.parameters || []) {
    if (p.editable === false) continue;
    const group=definitions.find(group=>group.cells.includes(p.cell)) || definitions[0];
    group.parameters ||= []; group.parameters.push(p);
  }
  return definitions;
});
const locked = computed(() => (model.value?.parameters || []).filter(p=>p.editable===false));
const generation = computed(() => (props.project.generations||[]).find(g=>g.native&&g.id===props.project.current_generation_id)
  || (previousGeneration.value?.projectId===props.project.id ? (props.project.generations||[]).find(g=>g.native&&g.id===previousGeneration.value.id) : null)
  || (props.project.current_generation_id==null ? (props.project.generations||[]).filter(g=>g.native).slice().sort((a,b)=>b.id-a.id)[0] : null));
const files = computed(() => generation.value && props.project.company_id===props.company.id ? [['business_archive','Бизнес-план'],['teo_archive','ТЭО']].flatMap(([key,title])=>{
  const archive=generation.value[key];return archive?.company_id===props.company.id&&archive.status==='ready'?(key==='business_archive'?['pdf','xlsx']:['pdf']).map(format=>({title:`${title} · ${format==='pdf'?'PDF':'Excel'}`,secondary:key==='teo_archive',url:archiveFileUrl(archive,format)})):[];
}):[]);
const changed = computed(() => (model.value?.parameters || []).some(p => p.editable!==false && numericText(parameters.value[p.key]) !== numericText(displayValue(p,model.value.overrides?.[p.key] ?? p.value))));
const stale = computed(() => changed.value || !generation.value || props.project.status!=='ready' || generation.value.id!==props.project.current_generation_id || generation.value.revision!==props.project.revision || reportStart.value+'-01'!==props.project.inputs?.start);
const download = computed(() => `/api/business-projects/${props.project.id}/native.xlsx?company_id=${props.company.id}&revision=${props.project.revision}`);
const kpis = computed(() => [['Стоим_проекта','F34','Стоимость проекта'],['ВНД','D6','NPV за три года'],['ВНД','E6','IRR за три года']].map(([sheet,cell,label]) => ({label,metric:(model.value?.metrics||[]).find(metric=>metric.sheet===sheet&&metric.cell===cell)})));
const history = computed(() => (props.project.generations||[]).filter(g=>g.native&&g.id!==generation.value?.id).slice().sort((a,b)=>b.id-a.id));
function number(value, unit) {
  if (value == null || String(value).trim()==='') return 'Нет результата';
  if (typeof value === 'string' && value.startsWith('#')) return value;
  if (!Number.isFinite(Number(value))) return 'Нет результата';
  return new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(Number(value) * (unit === '%' ? 100 : 1)) + (unit ? ' ' + unit : '');
}
function overrides() {
  return Object.fromEntries((model.value.parameters||[]).filter(p=>p.editable!==false).map(p=>{
    const text=numericText(parameters.value[p.key]);
    if (!text || !Number.isFinite(Number(text))) throw Error('Заполните числовое значение: '+p.label+'. Пустое поле не означает ноль.');
    const converted=isPercent(p)?decimalShift(text,-2):text,value=Number(converted);
    if(value===0 && /[1-9]/.test(converted.replace(/[eE][+-]?\d+$/,'')))throw Error('Значение слишком мало для расчёта: '+p.label+'.');
    if (p.min!=null&&value<p.min || p.max!=null&&value>p.max) throw Error('Значение вне допустимого диапазона: '+p.label+'.');
    return [p.key,value];
  }));
}
const options = body => ({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
function context() {
  const id=props.project.id,companyId=props.company.id;
  return {id,companyId,check:()=>{if(!active||props.project.id!==id||props.company.id!==companyId)throw new DOMException('Раздел или компания изменены','AbortError');}};
}
async function preview(c,values) {
  const updated=await props.api(`/api/business-projects/${c.id}/native/preview?company_id=${c.companyId}`,options({revision:props.project.revision,overrides:values}));
  c.check();assertProject(updated,c.companyId,c.id);
  if(!Number.isSafeInteger(updated.revision))throw Error('Сервер не вернул версию пересчёта. Обновите проект.');
  emit('updated',updated);return updated;
}
async function calculate() {
  if(busy.value||!canEdit.value)return;
  const c=context();busy.value=true;error.value='';
  try {if(generation.value)previousGeneration.value={projectId:c.id,id:generation.value.id};await preview(c,overrides());}
  catch(e){if(active&&e.name!=='AbortError'&&props.project.id===c.id&&props.company.id===c.companyId)error.value=e.message;}
  finally{if(active)busy.value=false;}
}
async function generate() {
  if(busy.value||!canEdit.value||!reportStart.value)return;
  const c=context(),start=reportStart.value+'-01';busy.value=true;error.value='';
  try {
    if(generation.value)previousGeneration.value={projectId:c.id,id:generation.value.id};
    const values=overrides(),checked=await preview(c,values);
    if(!checked.native_model?.preview)throw Error('Сервер не вернул проверку модели. Повторите подготовку отчёта.');
    if(checked.native_model.preview.blocked){error.value='В оригинале найдены ошибки, влияющие на финансовый результат. Исправьте указанные формулы перед подготовкой отчёта.';return;}
    c.check();
    const updated=await props.api(`/api/business-projects/${c.id}/native/generate?company_id=${c.companyId}`,options({revision:checked.revision,start,overrides:values}));
    c.check();assertProject(updated,c.companyId,c.id);emit('updated',updated);emit('generated');
  } catch(e){if(active&&e.name!=='AbortError'&&props.project.id===c.id&&props.company.id===c.companyId)error.value=e.message;}
  finally{if(active)busy.value=false;}
}
</script>
<template>
  <section v-if="model" class="native-workbook" aria-labelledby="native-model-title">
    <h3 id="native-model-title">Расчёт по оригиналу</h3>
    <p class="sub">Полный бизнес-план PDF и Excel сохраняют структуру и методику ваших Word и Excel.</p>
    <p v-if="error" class="error" role="alert">{{error}}</p>
    <p v-if="model.preview?.blocked" class="warning" role="alert">В оригинале найдены ошибки, влияющие на финансовый результат. Отчёт нельзя публиковать до их исправления.</p>
    <ul v-if="model.preview?.blocked" class="native-blockers"><li v-for="(issue,index) in model.preview.issues||[]" :key="index"><b>{{issue.sheet}}!{{issue.cell}}</b> — {{issue.message||issue.error}}</li></ul>
    <form v-if="canEdit" novalidate @submit.prevent="generate">
      <details v-for="group in groups" :key="group.id" :hidden="stage==='report'" class="native-group" :data-native-group="group.id" :open="group.id==='financing'"><summary>{{group.title}}</summary><p class="sub">{{group.hint}}</p><div class="native-parameters"><label v-for="parameter in group.parameters||[]" :key="parameter.key">{{parameter.label}} <span v-if="parameter.unit">({{isPercent(parameter)?parameter.cell==='Q19'?'п.п./год':'%':parameter.unit}})</span><input v-model="parameters[parameter.key]" type="text" inputmode="decimal" required :disabled="busy"><small v-if="parameter.cell==='AA6'">Эта же ставка применяется для дисконтирования NPV.</small></label></div></details>
      <button v-if="stage==='data'" type="button" :disabled="busy" @click="emit('report')">Перейти к отчёту</button>
      <label :hidden="stage!=='report'">Первый месяц периода в архиве<input v-model="reportStart" type="month" min="2000-01" max="2097-12" required :disabled="busy"><small>Выберите период для поиска отчёта. Таблицы сохраняют месяцы 1–36; даты договоров не меняются.</small></label>
      <div class="native-downloads" :hidden="stage!=='report'"><button :disabled="busy||!reportStart"><AppIcon name="file"/> {{busy?'Проверяем расчёт и готовим отчёт…':'Подготовить бизнес-план и Excel'}}</button><button type="button" class="secondary" :disabled="busy" @click="emit('data')">Вернуться к данным</button></div>
      <p class="sub" :hidden="stage!=='report'">Сайт пересчитает формулы, проверит результат и сформирует полный бизнес-план.</p>
    </form>
    <div class="native-kpis"><article v-for="item in kpis" :key="item.label"><small>{{item.label}}</small><b>{{model.preview&&!changed?number(item.metric?.calculated,item.metric?.unit):'Ещё не пересчитано'}}</b></article></div>
    <p v-if="changed" class="warning" role="status">Параметры изменены. Показатели и файлы предыдущего расчёта требуют обновления.</p>
    <component :is="stale?'details':'section'" v-if="files.length" :hidden="stage!=='report'" class="native-results"><summary v-if="stale">Предыдущая сохранённая версия</summary><h3 v-else>Готовый бизнес-план</h3><p v-if="stale" class="warning" role="status">Эти файлы относятся к предыдущему расчёту. Для текущих параметров подготовьте новый отчёт.</p><div class="native-downloads"><a v-for="file in files.filter(item=>!item.secondary)" :key="file.title" class="button secondary" :href="file.url"><AppIcon name="download"/> {{file.title}}</a></div>
      <details v-if="files.some(item=>item.secondary)"><summary>Дополнительно · ТЭО</summary><div class="native-downloads"><a v-for="file in files.filter(item=>item.secondary)" :key="file.title" class="button secondary" :href="file.url">{{file.title}}</a></div></details>
    </component>
    <details class="native-technical"><summary>Проверка расчёта и исходные параметры</summary>
      <p class="sub">{{model.source_name}} · {{model.sheet_count}} листов. Сохраняются исходные таблицы, формулы, оформление и параметры печати.</p>
      <button v-if="canEdit" type="button" class="secondary" :disabled="busy" @click="calculate"><AppIcon name="refresh"/> Пересчитать оригинал и проверить цифры</button>
      <div class="table-scroll"><table><thead><tr><th>Показатель</th><th>В исходном Excel</th><th>После пересчёта</th><th>Ячейка</th></tr></thead><tbody><tr v-for="metric in model.metrics" :key="metric.key"><td>{{metric.label}}</td><td>{{number(metric.original,metric.unit)}}</td><td>{{model.preview&&!changed?number(metric.calculated,metric.unit):'Ещё не пересчитано'}}</td><td>{{metric.sheet}}!{{metric.cell}}</td></tr></tbody></table></div>
      <details v-if="locked.length"><summary>Условия кредитного графика · справочно</summary><p class="sub">Сроки закреплены в графике исходного Excel. Для изменения загрузите исправленную книгу.</p><p v-for="p in locked" :key="p.key">{{p.label}}: {{number(p.value,p.unit)}}<small>{{p.sheet}}!{{p.cell}} · {{p.help}}</small></p></details>
      <template v-if="model.preview"><p class="sub">Пересчитано формул: {{model.preview.formula_count}}. Ошибок: {{model.preview.error_count}}.</p><details v-if="model.preview.issues?.length"><summary>Диагностика формул · {{model.preview.error_count}}</summary><ul><li v-for="(issue,index) in model.preview.issues" :key="index"><b>{{issue.sheet}}!{{issue.cell}}</b> — {{issue.message||issue.error}}<small v-if="issue.formula">{{issue.formula}}</small></li></ul></details><a v-if="canEdit&&!changed&&!files.some(item=>!item.secondary&&item.title.endsWith('Excel'))" class="button secondary" :href="download"><AppIcon name="download"/> Скачать пересчитанный Excel{{model.preview.blocked?' · для исправления ошибок':''}}</a></template>
      <details v-if="model.quality_notes?.length"><summary>Замечания к методике оригинала</summary><ul><li v-for="note in model.quality_notes" :key="note">{{note}}</li></ul></details>
    </details>
    <details v-if="history.length"><summary>Предыдущие расчёты · {{history.length}}</summary><div v-for="item in history" :key="item.id"><p>Версия исходных данных {{item.revision}}</p><a v-if="item.business_archive?.company_id===company.id&&item.business_archive.status==='ready'" class="button secondary" :href="archiveFileUrl(item.business_archive,'pdf')">Бизнес-план · PDF</a></div></details>
  </section>
</template>
<style scoped>
.native-workbook [hidden]{display:none!important}
.native-workbook{margin:24px 0;border-top:1px solid var(--line);padding-top:20px}.native-parameters{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin:18px 0}.native-parameters label,.native-workbook form>label{display:grid;gap:6px}.native-workbook table{width:100%;margin:20px 0}.native-workbook th,.native-workbook td{padding:10px;text-align:left;border-bottom:1px solid var(--line);white-space:normal}.native-workbook details{margin:16px 0}.native-workbook li{margin:12px 0;overflow-wrap:anywhere}.native-workbook small{display:block;color:var(--muted);margin-top:6px}.native-workbook .sub{line-height:1.6}.native-workbook form{margin:18px 0}.native-downloads{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}.native-group,.native-technical{border:1px solid var(--line);border-radius:12px;padding:14px}.native-workbook summary{cursor:pointer;font-weight:700}.native-kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:18px 0}.native-kpis article{background:var(--emx);border:1px solid var(--line);border-radius:12px;padding:14px}.native-kpis b{display:block;margin-top:8px;font-size:20px;overflow-wrap:anywhere}.native-results{border-top:1px solid var(--line);padding-top:20px;margin-top:20px}@media(max-width:560px){.native-kpis{grid-template-columns:1fr}.native-parameters{grid-template-columns:1fr}}
</style>
