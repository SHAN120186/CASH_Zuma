<script setup>
import {ref, computed, onMounted, onUnmounted, watch} from 'vue';
import AppIcon from './AppIcon.vue';
import {FORMATS, fileProblem, filterProblem, archiveUrl, archiveFileUrl, dateLabel, uploadLabel, createArchiveState, saveArchive} from './reportArchive.js';

const props = defineProps({api: {type: Function, required: true}, company: {type: Object, required: true}, refresh: {type: Number, default: 0}});
const emit = defineEmits(['editing']);
const items = ref([]), canUpload = ref(false), loading = ref(false), error = ref(''), notice = ref('');
const filters = ref({date_from: '', date_to: '', uploaded_on: ''});
const editor = ref(null), form = ref({title: '', period_start: '', period_end: ''}), files = ref({pdf: null, xlsx: null});
const saving = ref(false), step = ref(''), formError = ref('');
let active = true, listRequest = 0;
const savedFormats = computed(() => editor.value?.record?.formats || []);
const metadataSaved = computed(() => !!editor.value?.record);
const metadataFrozen = computed(() => metadataSaved.value || !!editor.value?.fields);
const missingFormats = computed(() => FORMATS.filter(format => !savedFormats.value.includes(format)));

watch(() => !!editor.value, value => emit('editing', value));
watch(() => props.refresh, load);
onUnmounted(() => {active = false; listRequest++; emit('editing', false);});
onMounted(load);

async function load() {
  const problem = filterProblem(filters.value);
  if (problem) {error.value = problem; return;}
  const request = ++listRequest;
  loading.value = true; error.value = ''; items.value = []; canUpload.value = false;
  try {
    const result = await props.api(archiveUrl(props.company.id, filters.value));
    if (!active || request !== listRequest) return;
    // The server also checks scope. A different company's response is never rendered.
    items.value = (result.items || []).filter(item => item.company_id === props.company.id);
    canUpload.value = !!result.can_upload;
  } catch (e) {if (active && request === listRequest && e.name !== 'AbortError') error.value = e.message;}
  finally {if (active && request === listRequest) loading.value = false;}
}
function clearFilters() {filters.value = {date_from: '', date_to: '', uploaded_on: ''}; load();}
function open(record = null) {
  editor.value = createArchiveState(record);
  form.value = {title: record?.title || '', period_start: record?.period_start || '', period_end: record?.period_end || ''};
  files.value = {pdf: null, xlsx: null}; formError.value = ''; notice.value = '';
}
function close() {if (saving.value) return; const changed = metadataFrozen.value; editor.value = null; if (changed) load();}
function choose(format, event) {
  const file = event.target.files?.[0]; event.target.value = '';
  if (!file) return;
  const problem = fileProblem(file, format);
  if (problem) {formError.value = problem; return;}
  files.value = {...files.value, [format]: file}; formError.value = '';
}
async function save() {
  if (saving.value) return;
  saving.value = true; formError.value = '';
  const state = editor.value, companyId = props.company.id;
  try {
    await saveArchive(props.api, state, {companyId, form: {...form.value}, files: {...files.value},
      isCurrent: () => active && editor.value === state && props.company.id === companyId,
      onRecord: record => {editor.value.record = record;}, onStep: value => {step.value = value;},
    });
    if (!active) return;
    editor.value = null;
    notice.value = 'Версия отчёта опубликована: PDF и Excel доступны на сайте и в Telegram-боте.';
    filters.value = {date_from: '', date_to: '', uploaded_on: ''};
    await load();
  } catch (e) {
    if (active && e.name !== 'AbortError') formError.value = e.message + (state.record ? ' Уже загруженные файлы сохранены. Повторите загрузку недостающего файла.' : ' Можно повторить загрузку: повторная версия не создаётся.');
  } finally {if (active) {saving.value = false; step.value = '';}}
}
</script>

<template>
  <div class="report-archive">
    <p v-if="notice" class="notice" role="status">{{notice}}</p>
    <section class="card">
      <div class="card-h">
        <div><h3>Бизнес-планы · PDF и Excel</h3><p class="sub">{{company.name}} · история загруженных версий</p></div>
        <button v-if="canUpload&&!editor" @click="open()" :disabled="loading"><AppIcon name="plus"/> Загрузить отчёт</button>
      </div>
      <div class="card-b">
        <p class="sub archive-explanation">Каждая версия хранит готовые файлы и свой период. Дата загрузки сохраняется автоматически. Фильтр выбирает отчёты с пересекающимся периодом; содержимое файлов остаётся за весь указанный период.</p>
        <form class="archive-filters" @submit.prevent="load">
          <label>Период отчёта: с<input v-model="filters.date_from" type="date" min="2000-01-01" max="2100-12-31" :disabled="saving"></label>
          <label>По<input v-model="filters.date_to" type="date" min="2000-01-01" max="2100-12-31" :disabled="saving"></label>
          <label>Дата загрузки<input v-model="filters.uploaded_on" type="date" min="2000-01-01" max="2100-12-31" :disabled="saving"><small>Ташкент · UTC+5</small></label>
          <button class="secondary" :disabled="loading||saving"><AppIcon name="search"/> Найти</button>
          <button class="ghost" type="button" :disabled="saving" @click="clearFilters">Сбросить</button>
        </form>
        <p v-if="error" class="error" role="alert">{{error}}</p>
        <p v-if="loading" class="loading" role="status">Загружаем отчёты…</p>
        <p v-else-if="!error&&!items.length" class="empty">Отчётов по выбранным датам пока нет.</p>
        <div v-if="items.length" class="table-scroll rsp-wrap">
          <table class="rsp"><thead><tr><th>Отчёт</th><th>Период в файлах</th><th>Загружен · Ташкент</th><th>Состояние</th><th>Файлы</th></tr></thead><tbody>
            <tr v-for="record in items" :key="record.id">
              <td data-l="Отчёт"><span><b>{{record.title}}</b><small>Версия №{{record.version??record.id}}</small></span></td>
              <td data-l="Период в файлах">{{dateLabel(record.period_start)}} — {{dateLabel(record.period_end)}}</td>
              <td data-l="Загружен">{{uploadLabel(record.uploaded_at)}}</td>
              <td data-l="Состояние"><span class="pill" :class="record.status==='ready'?'good':'warn'">{{record.status==='ready'?'Готов':'Черновик'}}</span></td>
              <td data-l="Файлы"><div class="archive-downloads">
                <template v-if="record.status==='ready'"><a v-for="format in FORMATS" :key="format" class="button secondary tiny" :href="archiveFileUrl(record,format)"><AppIcon name="download"/> {{format==='pdf'?'PDF':'Excel'}}</a></template>
                <template v-else><span class="sub">Не хватает: {{FORMATS.filter(format=>!record.formats.includes(format)).map(format=>format==='pdf'?'PDF':'Excel').join(', ')}}</span><button v-if="record.can_upload" class="secondary tiny" :disabled="saving||!!editor" @click="open(record)">Завершить загрузку</button></template>
              </div></td>
            </tr>
          </tbody></table>
        </div>
      </div>
    </section>

    <section v-if="editor" class="card archive-editor" aria-labelledby="archive-upload-title">
      <div class="card-h"><h3 id="archive-upload-title">{{editor.record?'Завершить загрузку версии №'+(editor.record.version??editor.record.id):'Новая версия отчёта'}}</h3></div>
      <form class="card-b" @submit.prevent="save">
        <p class="sub archive-explanation">Выберите PDF и Excel одного отчёта. Готовая версия сохраняется в истории; для обновления загрузите новую пару файлов.</p>
        <div class="archive-fields">
          <label class="archive-title">Название<input v-model="form.title" required minlength="1" maxlength="160" placeholder="Бизнес-план — 36 месяцев" :disabled="saving||metadataFrozen"></label>
          <label>Начало периода<input v-model="form.period_start" required type="date" min="2000-01-01" max="2100-12-31" :disabled="saving||metadataFrozen"></label>
          <label>Конец периода<input v-model="form.period_end" required type="date" :min="form.period_start||'2000-01-01'" max="2100-12-31" :disabled="saving||metadataFrozen"></label>
        </div>
        <div class="archive-files">
          <div v-for="format in FORMATS" :key="format" class="archive-file"><b>{{format==='pdf'?'PDF':'Excel (.xlsx)'}}</b>
            <span v-if="savedFormats.includes(format)" class="pill good"><AppIcon name="check"/> Загружен</span>
            <template v-else><label class="button secondary"><AppIcon name="file"/> {{files[format]?'Выбрать другой файл':'Выбрать файл'}}<input type="file" :accept="'.'+format" :disabled="saving" hidden @change="choose(format,$event)"></label><small>{{files[format]?.name||'Файл не выбран'}}</small></template>
          </div>
        </div>
        <p class="sub archive-limit">До 20 МБ на файл. Период относится к содержимому отчёта, а не к дате загрузки.</p>
        <p v-if="metadataSaved&&editor.record?.status!=='ready'" class="warning">Параметры версии уже сохранены. Добавьте недостающие файлы, чтобы отчёт стал доступен в боте.</p>
        <p v-else-if="editor.createUnknown" class="warning">Сервер мог сохранить черновик. Повторите загрузку или закройте форму и проверьте историю отчётов.</p>
        <p v-if="formError" class="error" role="alert">{{formError}}</p>
        <p v-if="saving" class="loading" role="status">{{step}}</p>
        <div class="form-actions"><button type="button" class="secondary" :disabled="saving" @click="close">{{metadataFrozen?'Закрыть':'Отмена'}}</button><button :disabled="saving||missingFormats.some(format=>!files[format])"><AppIcon name="import"/> {{saving?'Загружаем…':metadataFrozen?'Завершить загрузку':'Загрузить PDF и Excel'}}</button></div>
      </form>
    </section>
  </div>
</template>

<style scoped>
.report-archive{display:grid;gap:18px}.archive-explanation{max-width:920px;line-height:1.65;margin:4px 0 18px}.archive-filters{display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;margin-bottom:20px}.archive-filters label,.archive-fields label{display:grid;gap:6px;flex:1;min-width:160px}.archive-filters button{margin-top:26px}.archive-fields{display:grid;grid-template-columns:minmax(240px,2fr) 1fr 1fr;gap:14px}.archive-files{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:18px}.archive-file{border:1px solid var(--line);border-radius:14px;padding:16px;display:flex;flex-wrap:wrap;align-items:center;gap:12px}.archive-file small{width:100%;overflow-wrap:anywhere}.archive-file .button{margin-left:auto}.archive-limit{margin:12px 0 18px}.archive-downloads{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.archive-editor .warning{margin-top:14px}.report-archive td{vertical-align:top}.archive-editor .form-actions{margin-top:18px}@media(max-width:760px){.archive-fields,.archive-files{grid-template-columns:1fr}.archive-filters label{flex-basis:100%;min-width:0}.archive-filters button{margin-top:0}.archive-file .button{margin-left:0}.report-archive .card-h{align-items:flex-start}}
</style>
