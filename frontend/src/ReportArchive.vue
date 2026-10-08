<script setup>
import {ref, computed, onMounted, onUnmounted, watch} from 'vue';
import AppIcon from './AppIcon.vue';
import BusinessProjects from './BusinessProjects.vue';
import OperationProgress from './OperationProgress.vue';
import {createOperationProgress} from './operationProgress.js';
import {FORMATS, fileProblem, filterProblem, archiveUrl, archiveFileUrl, dateLabel, uploadLabel, createArchiveState, saveArchive, archiveRequirements} from './reportArchive.js';

const props = defineProps({api: {type: Function, required: true}, company: {type: Object, required: true}, refresh: {type: Number, default: 0}});
const emit = defineEmits(['editing']);
const items = ref([]), canUpload = ref(false), loading = ref(false), error = ref(''), notice = ref('');
const filters = ref({date_from: '', date_to: '', uploaded_on: ''});
const editor = ref(null), form = ref({title: '', period_start: '', period_end: ''}), files = ref({pdf: null, xlsx: null});
const saving = ref(false), step = ref(''), formError = ref('');
const businessEditing = ref(false);
const showDeleted = ref(false), changingState = ref(false);
const listProgress = ref({}), actionProgress = ref({});
const listController = createOperationProgress(value=>{listProgress.value=value;});
const actionController = createOperationProgress(value=>{actionProgress.value=value;});
let active = true, listRequest = 0;
const savedFormats = computed(() => editor.value?.record?.formats || []);
const metadataSaved = computed(() => !!editor.value?.record);
const metadataFrozen = computed(() => metadataSaved.value || !!editor.value?.fields);
const missingFormats = computed(() => FORMATS.filter(format => !savedFormats.value.includes(format)));
const requirements = computed(() => archiveRequirements(form.value,files.value,savedFormats.value));
const problemFor = field => requirements.value.find(issue=>issue.field===field)?.message || '';

watch(() => !!editor.value || businessEditing.value, value => emit('editing', value));
watch(() => props.refresh, load);
watch(() => props.company.id, () => {listRequest++;editor.value=null;files.value={pdf:null,xlsx:null};saving.value=false;changingState.value=false;items.value=[];canUpload.value=false;error.value='';formError.value='';notice.value='';listController.reset();actionController.reset();load();});
onUnmounted(() => {active = false; listRequest++; listController.reset();actionController.reset();emit('editing', false);});
onMounted(load);

async function load() {
  const problem = filterProblem(filters.value);
  if (problem) {error.value = problem; return;}
  const request = ++listRequest, companyId = props.company.id;
  listController.reset();
  const ticket = listController.begin('Загружаем архив отчётов',1);
  loading.value = true; error.value = ''; items.value = []; canUpload.value = false;
  try {
    const result = await props.api(archiveUrl(companyId, {...filters.value, include_deleted:showDeleted.value}));
    if (!active || request !== listRequest || companyId !== props.company.id) return;
    // The server also checks scope. A different company's response is never rendered.
    items.value = (result.items || []).filter(item => item.company_id === companyId);
    canUpload.value = !!result.can_upload;
    listController.complete(ticket,'Архив отчётов загружен');
  } catch (e) {if (active && request === listRequest && companyId === props.company.id && e.name !== 'AbortError') {error.value = e.message;listController.fail(ticket,e);}}
  finally {if (active && request === listRequest) loading.value = false;}
}
function clearFilters() {filters.value = {date_from: '', date_to: '', uploaded_on: ''}; load();}
async function changeArchiveState(record, restore = false) {
  if (saving.value || changingState.value || record.company_id !== props.company.id || !(restore ? record.can_restore : record.can_delete)) return;
  const companyId = props.company.id;
  const ticket = actionController.begin(restore?'Восстанавливаем отчёт':'Удаляем отчёт',1);
  changingState.value = true; error.value = ''; notice.value = '';
  try {
    const path = `/api/report-archives/${record.id}${restore ? '/restore' : ''}?company_id=${companyId}&expected_version=${record.version ?? record.id}`;
    const updated = await props.api(path, {method:restore ? 'POST' : 'DELETE'});
    if (!active || companyId !== props.company.id) return;
    if (updated.company_id !== companyId || updated.id !== record.id) throw Error('Сервер вернул другую версию отчёта. Обновите страницу.');
    actionController.complete(ticket,restore?'Отчёт восстановлен':'Отчёт удалён');
    notice.value = restore ? 'Отчёт восстановлен и снова доступен в боте.' : 'Отчёт удалён из списка и бота. Его можно восстановить через «Показать удалённые».';
    await load();
  } catch (e) {if (active && companyId === props.company.id && e.name !== 'AbortError') {error.value = e.message;actionController.fail(ticket,e);}}
  finally {if (active && companyId === props.company.id) changingState.value = false;}
}
function open(record = null) {
  if (saving.value || changingState.value || !canUpload.value || record && (record.company_id !== props.company.id || !record.can_upload)) return;
  actionController.reset();
  editor.value = createArchiveState(record);
  form.value = {title: record?.title || '', period_start: record?.period_start || '', period_end: record?.period_end || ''};
  files.value = {pdf: null, xlsx: null}; formError.value = ''; notice.value = '';
}
function close() {if (saving.value) return; const changed = metadataFrozen.value; editor.value = null; if (changed) load();}
function choose(format, event) {
  if (!editor.value || saving.value) return;
  const file = event.target.files?.[0]; event.target.value = '';
  if (!file) return;
  const problem = fileProblem(file, format);
  if (problem) {formError.value = problem; return;}
  files.value = {...files.value, [format]: file}; formError.value = '';
}
async function save() {
  if (saving.value || changingState.value || !editor.value) return;
  if (requirements.value.length) {formError.value='Проверьте обязательные поля: '+requirements.value.map(issue=>issue.label).join(', ')+'.';return;}
  saving.value = true; formError.value = '';
  const state = editor.value, companyId = props.company.id;
  const ticket = actionController.begin('Сохраняем PDF и Excel',0);
  const isCurrent = () => active && editor.value === state && props.company.id === companyId;
  try {
    await saveArchive(props.api, state, {companyId, form: {...form.value}, files: {...files.value},
      isCurrent,
      onRecord: record => {editor.value.record = record;}, onStep: value => {step.value = value;},
      onProgress: value => {actionController.progress(ticket,value);},
    });
    if (!isCurrent()) return;
    actionController.complete(ticket,'PDF и Excel сохранены');
    editor.value = null;
    notice.value = 'Версия отчёта опубликована: PDF и Excel доступны на сайте и в Telegram-боте.';
    filters.value = {date_from: '', date_to: '', uploaded_on: ''};
    await load();
  } catch (e) {
    if (isCurrent() && e.name !== 'AbortError') {actionController.fail(ticket,e);formError.value = e.message + (state.record ? ' Уже загруженные файлы сохранены. Повторите загрузку недостающего файла.' : ' Можно повторить загрузку: повторная версия не создаётся.');}
  } finally {if (active && props.company.id === companyId) {saving.value = false; step.value = '';}}
}
</script>

<template>
  <div class="report-archive">
    <BusinessProjects :api="api" :company="company" :refresh="refresh" @editing="businessEditing=$event" @generated="load"/>
    <OperationProgress v-bind="actionProgress"/>
    <p v-if="notice" class="notice" role="status">{{notice}}</p>
    <section class="card">
      <div class="card-h">
        <div><h3>Архив готовых отчётов · PDF и Excel</h3><p class="sub">{{company.name}} · история загруженных и рассчитанных версий</p></div>
        <button v-if="canUpload&&!editor&&!businessEditing" @click="open()" :disabled="loading"><AppIcon name="plus"/> Загрузить готовый отчёт</button>
      </div>
      <div class="card-b">
        <p class="sub archive-explanation">Каждая версия хранит готовые файлы и свой период. Дата загрузки сохраняется автоматически. Фильтр выбирает отчёты с пересекающимся периодом; содержимое файлов остаётся за весь указанный период.</p>
        <form class="archive-filters" @submit.prevent="load">
          <label>Период отчёта: с<input v-model="filters.date_from" type="date" min="2000-01-01" max="2100-12-31" :disabled="saving"></label>
          <label>По<input v-model="filters.date_to" type="date" min="2000-01-01" max="2100-12-31" :disabled="saving"></label>
          <label>Дата загрузки<input v-model="filters.uploaded_on" type="date" min="2000-01-01" max="2100-12-31" :disabled="saving"><small>Ташкент · UTC+5</small></label>
          <button class="secondary" :disabled="loading||saving"><AppIcon name="search"/> Найти</button>
          <button class="ghost" type="button" :disabled="saving" @click="clearFilters">Сбросить</button>
          <label v-if="canUpload" class="archive-trash"><span>Удалённые отчёты</span><span><input v-model="showDeleted" type="checkbox" :disabled="saving||changingState" @change="load"> Показать удалённые</span></label>
        </form>
        <p v-if="error" class="error" role="alert">{{error}}</p>
        <OperationProgress v-bind="listProgress"/>
        <p v-if="!loading&&!error&&!items.length" class="empty">Отчётов по выбранным датам пока нет.</p>
        <div v-if="items.length" class="table-scroll rsp-wrap">
          <table class="rsp"><thead><tr><th>Отчёт</th><th>Период в файлах</th><th>Загружен · Ташкент</th><th>Состояние</th><th>Файлы</th></tr></thead><tbody>
            <tr v-for="record in items" :key="record.id">
              <td data-l="Отчёт"><span><b>{{record.title}}</b><small>Версия №{{record.version??record.id}}</small></span></td>
              <td data-l="Период в файлах">{{dateLabel(record.period_start)}} — {{dateLabel(record.period_end)}}</td>
              <td data-l="Загружен">{{uploadLabel(record.uploaded_at)}}</td>
              <td data-l="Состояние"><span class="pill" :class="record.status==='ready'?'good':'warn'">{{record.status==='deleted'?'Удалён':record.status==='ready'?'Готов':'Черновик'}}</span></td>
              <td data-l="Файлы"><div class="archive-downloads">
                <template v-if="record.status==='ready'"><a v-for="format in FORMATS" :key="format" class="button secondary tiny" :href="archiveFileUrl(record,format)"><AppIcon name="download"/> {{format==='pdf'?'PDF':'Excel'}}</a></template>
                <template v-else-if="record.status!=='deleted'"><span class="sub">Не хватает: {{FORMATS.filter(format=>!record.formats.includes(format)).map(format=>format==='pdf'?'PDF':'Excel').join(', ')}}</span><button v-if="record.can_upload" class="secondary tiny" :disabled="saving||!!editor" @click="open(record)">Завершить загрузку</button></template>
                <button v-if="record.can_delete" class="secondary tiny" :disabled="saving||changingState||!!editor" @click="changeArchiveState(record)">Удалить отчёт</button><button v-if="record.can_restore" class="secondary tiny" :disabled="saving||changingState||!!editor" @click="changeArchiveState(record,true)">Восстановить</button><small v-if="record.deleted_with_project">Для возврата восстановите папку проекта №{{record.restore_project_id}}.</small>
              </div></td>
            </tr>
          </tbody></table>
        </div>
      </div>
    </section>

    <section v-if="editor" class="card archive-editor" aria-labelledby="archive-upload-title">
      <div class="card-h"><h3 id="archive-upload-title">{{editor.record?'Завершить загрузку версии №'+(editor.record.version??editor.record.id):'Новая версия отчёта'}}</h3></div>
      <form class="card-b" novalidate @submit.prevent="save">
        <p class="sub archive-explanation">Выберите PDF и Excel одного отчёта. Готовая версия сохраняется в истории; для обновления загрузите новую пару файлов.</p>
        <div class="archive-fields">
          <label class="archive-title" data-archive-field="title">Название<input v-model="form.title" required minlength="1" maxlength="160" placeholder="Бизнес-план — 36 месяцев" :disabled="saving||metadataFrozen" :aria-invalid="!!problemFor('title')" :aria-describedby="problemFor('title')?'archive-title-error':undefined"><small v-if="problemFor('title')" id="archive-title-error" class="archive-field-error">{{problemFor('title')}}</small></label>
          <label data-archive-field="period_start">Начало периода<input v-model="form.period_start" required type="date" min="2000-01-01" max="2100-12-31" :disabled="saving||metadataFrozen" :aria-invalid="!!problemFor('period_start')" :aria-describedby="problemFor('period_start')?'archive-start-error':undefined"><small v-if="problemFor('period_start')" id="archive-start-error" class="archive-field-error">{{problemFor('period_start')}}</small></label>
          <label data-archive-field="period_end">Конец периода<input v-model="form.period_end" required type="date" :min="form.period_start||'2000-01-01'" max="2100-12-31" :disabled="saving||metadataFrozen" :aria-invalid="!!problemFor('period_end')" :aria-describedby="problemFor('period_end')?'archive-end-error':undefined"><small v-if="problemFor('period_end')" id="archive-end-error" class="archive-field-error">{{problemFor('period_end')}}</small></label>
        </div>
        <div class="archive-files">
          <div v-for="format in FORMATS" :key="format" class="archive-file" :data-archive-field="format"><b>{{format==='pdf'?'PDF':'Excel (.xlsx)'}}</b>
            <span v-if="savedFormats.includes(format)" class="pill good"><AppIcon name="check"/> Загружен</span>
            <template v-else><label class="button secondary"><AppIcon name="file"/> {{files[format]?'Выбрать другой файл':'Выбрать файл'}}<input type="file" :accept="'.'+format" :disabled="saving" :aria-label="format==='pdf'?'Выбрать файл PDF':'Выбрать файл Excel (.xlsx)'" hidden @change="choose(format,$event)"></label><small>{{files[format]?.name||'Файл не выбран'}}</small><small v-if="problemFor(format)" class="archive-field-error">{{problemFor(format)}}</small></template>
          </div>
        </div>
        <div v-if="requirements.length" class="archive-requirements" aria-labelledby="archive-requirements-title"><b id="archive-requirements-title">Проверьте обязательные поля:</b><ul><li v-for="issue in requirements" :key="issue.field"><b>{{issue.label}}</b> — {{issue.message}}</li></ul></div>
        <p class="sub archive-limit">До 20 МБ на файл. Период относится к содержимому отчёта, а не к дате загрузки.</p>
        <p v-if="metadataSaved&&editor.record?.status!=='ready'" class="warning">Параметры версии уже сохранены. Добавьте недостающие файлы, чтобы отчёт стал доступен в боте.</p>
        <p v-else-if="editor.createUnknown" class="warning">Сервер мог сохранить черновик. Повторите загрузку или закройте форму и проверьте историю отчётов.</p>
        <p v-if="formError" class="error" role="alert">{{formError}}</p>
        <div class="form-actions"><button type="button" class="secondary" :disabled="saving" @click="close">{{metadataFrozen?'Закрыть':'Отмена'}}</button><button :disabled="saving||requirements.length>0"><AppIcon name="import"/> {{saving?'Загружаем…':metadataFrozen?'Завершить загрузку':'Загрузить PDF и Excel'}}</button></div>
      </form>
    </section>
  </div>
</template>

<style scoped>
.archive-field-error{color:var(--warn,#a76500);line-height:1.4}.archive-fields input[aria-invalid="true"]{border-color:var(--warn,#a76500)}.archive-requirements{padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:var(--warnl);margin-top:16px;font-size:12px;line-height:1.5}.archive-requirements ul{margin:8px 0 0;padding-left:20px;display:grid;gap:4px}
.report-archive{display:grid;gap:18px}.archive-explanation{max-width:920px;line-height:1.65;margin:4px 0 18px}.archive-filters{display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;margin-bottom:20px}.archive-filters label,.archive-fields label{display:grid;gap:6px;flex:1;min-width:160px}.archive-filters button{margin-top:26px}.archive-fields{display:grid;grid-template-columns:minmax(240px,2fr) 1fr 1fr;gap:14px}.archive-files{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:18px}.archive-file{border:1px solid var(--line);border-radius:14px;padding:16px;display:flex;flex-wrap:wrap;align-items:center;gap:12px}.archive-file small{width:100%;overflow-wrap:anywhere}.archive-file .button{margin-left:auto}.archive-limit{margin:12px 0 18px}.archive-downloads{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.archive-editor .warning{margin-top:14px}.report-archive td{vertical-align:top}.archive-editor .form-actions{margin-top:18px}@media(max-width:760px){.archive-fields,.archive-files{grid-template-columns:1fr}.archive-filters label{flex-basis:100%;min-width:0}.archive-filters button{margin-top:0}.archive-file .button{margin-left:0}.report-archive .card-h{align-items:flex-start}}
</style>
