<script setup>
import {ref, computed, watch} from 'vue';
import AppIcon from './AppIcon.vue';
import {archiveFileUrl} from './reportArchive.js';
const props = defineProps({api:Function, project:Object, company:Object});
const emit = defineEmits(['updated','generated','working']);
const parameters = ref({}), busy = ref(false), error = ref('');
const reportStart = ref('');
const model = computed(() => props.project.native_model);
watch(() => model.value, value => {parameters.value = Object.fromEntries((value?.parameters || []).map(p => [p.key, value.overrides?.[p.key] ?? p.value]));reportStart.value = props.project.inputs?.start?.slice(0,7)||'';}, {immediate:true});
watch(busy,value=>emit('working',value));
const generation = computed(() => (props.project.generations||[]).find(g=>g.native&&g.id===props.project.current_generation_id));
const files = computed(() => generation.value ? [['business_archive','Бизнес-план'],['teo_archive','ТЭО']].flatMap(([key,title])=>{
  const archive=generation.value[key];return archive?.company_id===props.company.id&&archive.status==='ready'?['pdf','xlsx'].map(format=>({title:`${title} · ${format==='pdf'?'PDF':'Excel'}`,url:archiveFileUrl(archive,format)})):[];
}):[]);
const changed = computed(() => (model.value?.parameters || []).some(p => Number(parameters.value[p.key]) !== Number(model.value.overrides?.[p.key] ?? p.value)));
const download = computed(() => `/api/business-projects/${props.project.id}/native.xlsx?company_id=${props.company.id}&revision=${props.project.revision}`);
function number(value, unit) {if (value == null || typeof value === 'string' && value.startsWith('#')) return value || 'Нет результата'; return new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(Number(value) * (unit === '%' ? 100 : 1)) + (unit ? ' ' + unit : '');}
async function calculate() {
  if (busy.value || !props.project.can_edit) return;
  busy.value = true; error.value = '';
  const id = props.project.id, companyId = props.company.id;
  try {
    const editable = new Set(model.value.parameters.filter(p => p.editable !== false).map(p => p.key));
    const overrides = Object.fromEntries(Object.entries(parameters.value).filter(([key]) => editable.has(key)).map(([key,value]) => [key, value === '' ? null : Number(value)]));
    const updated = await props.api(`/api/business-projects/${id}/native/preview?company_id=${companyId}`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:props.project.revision,overrides})});
    if (props.project.id === id && props.company.id === companyId) emit('updated',updated);
  } catch (e) {if(e.name !== 'AbortError') error.value = e.message;}
  finally {busy.value = false;}
}
async function generate() {
  if(busy.value||!props.project.can_edit||!reportStart.value||changed.value||model.value.preview?.blocked)return;
  busy.value=true;error.value='';
  const id=props.project.id,companyId=props.company.id;
  try {
    const editable=new Set(model.value.parameters.filter(p=>p.editable!==false).map(p=>p.key));
    const overrides=Object.fromEntries(Object.entries(parameters.value).filter(([key])=>editable.has(key)).map(([key,value])=>[key,value===''?null:Number(value)]));
    const updated=await props.api(`/api/business-projects/${id}/native/generate?company_id=${companyId}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:props.project.revision,start:reportStart.value+'-01',overrides})});
    if(props.project.id===id&&props.company.id===companyId){emit('updated',updated);emit('generated');}
  } catch(e){if(e.name!=='AbortError')error.value=e.message;}
  finally{busy.value=false;}
}
</script>
<template>
  <section v-if="model" class="native-workbook" aria-labelledby="native-model-title">
    <h3 id="native-model-title">Расчёт по оригинальному Excel</h3>
    <p class="sub">{{model.source_name}} · {{model.sheet_count}} листов. Сохраняются исходные таблицы, формулы, оформление и параметры печати. Значения ниже взяты из вашего файла.</p>
    <p v-if="!model.preview" class="notice">Нажмите «Пересчитать оригинал и проверить цифры». Сайт проверит формулы и сравнит результат с сохранёнными суммами в Excel.</p>
    <form v-if="project.can_edit" @submit.prevent="calculate">
      <div class="native-parameters"><label v-for="parameter in model.parameters" :key="parameter.key">{{parameter.label}} <span v-if="parameter.unit">({{parameter.unit}})</span><input v-model="parameters[parameter.key]" type="number" :min="parameter.min" :max="parameter.max" :step="parameter.step||'any'" required :disabled="busy||parameter.editable===false"><small>{{parameter.sheet}}!{{parameter.cell}} · {{parameter.help}}</small></label></div>
      <p v-if="error" class="error" role="alert">{{error}}</p>
      <button :disabled="busy"><AppIcon name="refresh"/> {{busy?'Пересчитываем формулы…':'Пересчитать оригинал и проверить цифры'}}</button>
    </form>
    <div class="table-scroll"><table><thead><tr><th>Показатель</th><th>В исходном Excel</th><th>После пересчёта</th><th>Ячейка</th></tr></thead><tbody><tr v-for="metric in model.metrics" :key="metric.key"><td>{{metric.label}}</td><td>{{number(metric.original,metric.unit)}}</td><td>{{model.preview?number(metric.calculated,metric.unit):'Ещё не пересчитано'}}</td><td>{{metric.sheet}}!{{metric.cell}}</td></tr></tbody></table></div>
    <template v-if="model.preview">
      <p v-if="model.preview.blocked" class="warning" role="status">В оригинале найдены ошибки, влияющие на финансовый результат. Готовый бизнес-план и ТЭО нельзя публиковать до их исправления. Ниже указано, какие формулы нужно восстановить.</p>
      <p v-else class="notice" role="status">Контрольные показатели пересчитаны по формулам оригинала. {{model.preview.error_count?'В других строках книги остались ошибки, указанные ниже.':''}}</p>
      <p class="sub">Пересчитано формул: {{model.preview.formula_count}}. Ошибок: {{model.preview.error_count}}. Сохранённые в оригинале цифры показаны отдельно от нового расчёта.</p>
      <details v-if="model.preview.issues?.length" :open="model.preview.blocked"><summary>Ошибки в исходном файле · {{model.preview.error_count}}</summary><ul><li v-for="(issue,index) in model.preview.issues" :key="index"><b>{{issue.sheet}}!{{issue.cell}}</b> — {{issue.message||issue.error}}<small v-if="issue.formula">{{issue.formula}}</small></li></ul></details>
      <p v-if="model.preview.error_count>(model.preview.issues?.length||0)" class="sub">Показаны первые проблемные ячейки. Полный результат пересчёта сохранён в Excel.</p>
      <a v-if="project.can_edit&&!changed" class="button secondary" :href="download"><AppIcon name="download"/> Скачать пересчитанный Excel{{model.preview.blocked?' · для исправления ошибок':''}}</a>
      <p v-if="changed" class="sub">Параметры изменены. Перед скачиванием выполните пересчёт.</p>
      <details v-if="model.quality_notes?.length" open><summary>Замечания к методике оригинала</summary><ul><li v-for="note in model.quality_notes" :key="note">{{note}}</li></ul></details>
      <form v-if="project.can_edit&&!model.preview.blocked" @submit.prevent="generate">
        <label>Первый месяц периода в архиве<input v-model="reportStart" type="month" min="2000-01" max="2097-12" required :disabled="busy"><small>Выберите период для поиска отчёта. Таблицы оригинала сохраняют месяцы 1–36 и годы 1–3; даты договоров не меняются.</small></label>
        <p class="sub">Бизнес-план PDF формируется из Word вашей папки с обновлёнными финансовыми таблицами. ТЭО содержит результаты той же модели и замечания к исходным формулам. Excel для обоих отчётов — полная исходная модель после пересчёта.</p>
        <button :disabled="busy||!reportStart||changed"><AppIcon name="file"/> {{busy?'Готовим Word, PDF и Excel…':'Сформировать бизнес-план и ТЭО по оригиналу'}}</button>
      </form>
      <div v-if="files.length" class="native-downloads"><a v-for="file in files" :key="file.title" class="button secondary" :href="file.url"><AppIcon name="download"/> {{file.title}}</a></div>
    </template>
  </section>
</template>
<style scoped>
.native-workbook{margin:24px 0;border-top:1px solid var(--line);padding-top:20px}.native-parameters{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin:18px 0}.native-parameters label,.native-workbook form>label{display:grid;gap:6px}.native-workbook table{width:100%;margin:20px 0}.native-workbook th,.native-workbook td{padding:10px;text-align:left;border-bottom:1px solid var(--line);white-space:normal}.native-workbook details{margin:16px 0}.native-workbook li{margin:12px 0;overflow-wrap:anywhere}.native-workbook small{display:block;color:var(--muted);margin-top:6px}.native-workbook .sub{line-height:1.6}.native-workbook form{margin:18px 0}.native-downloads{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}
</style>
