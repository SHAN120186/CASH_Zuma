<script setup>
import {ref, computed, watch, onMounted, onUnmounted} from 'vue';
import AppIcon from './AppIcon.vue';
import NativeWorkbook from './NativeWorkbook.vue';
import {archiveFileUrl, uploadLabel} from './reportArchive.js';
import {formatCents, toCents} from './overview/format.js';
import {SOURCE_ACCEPT, BUSINESS_SCALARS, NARRATIVE_FIELDS, prepareSources, sourceSize, projectUrl, templateUrl,
  formFromInputs, inputsFromForm, monthLabels, resizeValues, createBusinessState, uploadProjectFolder, generateProject,
  assertProject, fieldLabel, issueMessage, projectReview, evidenceRows, sourceLocation, folderProjectTitle, uploadFailureMessage,
  fieldGuidance, missingRequiredFields, headerProblem, originalReportFiles} from './businessProjects.js';

const props = defineProps({api: {type: Function, required: true}, company: {type: Object, required: true}, refresh: {type: Number, default: 0}});
const emit = defineEmits(['editing', 'generated']);
const projects = ref([]), canUpload = ref(false), listLoading = ref(false), error = ref(''), notice = ref('');
const state = ref(null), current = ref(null), action = ref(''), step = ref('');
const header = ref({title: '', start: '', months: 36, currency: ''});
const form = ref(formFromInputs()), selected = ref([]), ignored = ref([]), selectionProblems = ref([]), totalSize = ref(0);
const modelForm = ref(null);
const showDeleted = ref(false);
const confirmed = ref(false), loadedSnapshot = ref('');
let active = true, listRequest = 0, detailRequest = 0;
const busy = computed(() => !!action.value), editing = computed(() => !!state.value);
const uploadRequirement = computed(() => headerProblem(header.value));
const canEdit = computed(() => !!current.value?.can_edit && current.value.status !== 'deleted');
const visibleProjects = computed(() => projects.value.filter(project => showDeleted.value || project.status !== 'deleted'));
const originalReports = computed(() => originalReportFiles(current.value, props.company.id));
const review = computed(() => projectReview(current.value?.extraction, current.value?.validation));
const sourceProblems = computed(() => review.value.checks);
const needsConfirmation = computed(() => sourceProblems.value.some(issue => issue.code !== 'missing_or_invalid' && issue.requires_confirmation));
const validation = computed(() => review.value.fields);
const requiredFields = computed(() => missingRequiredFields(form.value));
const remainingRequirements = computed(() => {
  const seen = new Set();
  return requiredFields.value.filter(item => {const key = item.field.split(/[.\[]/)[0]; if (seen.has(key)) return false; seen.add(key); return true;});
});
const savedValidation = computed(() => changed.value ? [] : validation.value.filter(issue => !requiredFields.value.some(item => item.field === issue.field)));
const referenceNotes = computed(() => review.value.references);
const evidence = computed(() => evidenceRows(current.value?.extraction));
const narratives = computed(() => current.value?.extraction?.narratives || []);
const labels = computed(() => monthLabels(form.value.start, form.value.months));
const changed = computed(() => !!current.value && JSON.stringify(inputsFromForm(form.value)) !== loadedSnapshot.value);
const generations = computed(() => (current.value?.generations || []).slice().sort((a, b) =>
  Number(b.id === current.value?.current_generation_id) - Number(a.id === current.value?.current_generation_id) || b.id - a.id));
const latest = computed(() => generations.value[0]);
const scheduleEntries = computed(() => {
  const entries = [{key: 'fixed', label: 'Постоянные расходы', object: form.value, field: 'fixed_costs'}, {key: 'equity', label: 'Взносы в капитал', object: form.value, field: 'equity'}];
  form.value.products.forEach((product, index) => {
    const prefix = `Продукция №${index + 1}${product.name ? ' «' + product.name + '»' : ''}`;
    entries.push({key: 'q' + index, label: prefix + ' · объём реализации', object: product, field: 'quantities'});
    if (product.capacity != null) entries.push({key: 'c' + index, label: prefix + ' · мощность', object: product, field: 'capacity'});
  });
  form.value.loans.forEach((loan, index) => {for (const [field, label] of [['drawdowns', 'Получение'], ['principal', 'Погашение основного долга'], ['interest', 'Проценты']]) entries.push({key: 'l' + index + field, label: `Кредит №${index + 1}${loan.name ? ' «' + loan.name + '»' : ''} · ${label}`, object: loan, field});});
  return entries;
});
const financialSchedules = computed(() => scheduleEntries.value.filter(entry => entry.object === form.value));
const loanSchedules = computed(() => scheduleEntries.value.filter(entry => entry.key.startsWith('l')));
const productSchedules = computed(() => scheduleEntries.value.filter(entry => !financialSchedules.value.includes(entry) && !loanSchedules.value.includes(entry)));
const reviewProducts = computed(() => form.value.products.length <= 5 || validation.value.some(issue => String(issue.field || '').startsWith('products')));
const statusLabel = project => project.status === 'deleted' ? 'Удалена · можно восстановить' : project.status === 'ready' ? 'Отчёты готовы' : project.status === 'needs_data' ? 'Нужны параметры' : 'Папка проекта';
const money = value => formatCents(toCents(value), 'USD');
const ratio = value => value == null ? '—' : new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2}).format(Number(value) * 100) + '%';

watch(editing, value => emit('editing', value));
watch(() => props.refresh, loadProjects);
onMounted(loadProjects);
onUnmounted(() => {active = false; listRequest++; detailRequest++; emit('editing', false);});

async function loadProjects() {
  const request = ++listRequest; listLoading.value = true;
  try {
    const response = await props.api(projectUrl(props.company.id) + '&include_deleted=true');
    if (!active || request !== listRequest) return;
    projects.value = (response.items || []).filter(project => project.company_id === props.company.id);
    canUpload.value = !!response.can_upload;
  } catch (e) {if (active && request === listRequest && e.name !== 'AbortError') error.value = e.message;}
  finally {if (active && request === listRequest) listLoading.value = false;}
}
function setProject(project, replaceForm = false) {
  assertProject(project, props.company.id);
  current.value = project;
  if (state.value) state.value.project = project;
  if (replaceForm) {
    form.value = formFromInputs(project.inputs || project.extraction?.inputs || {}, {title: project.title, ...header.value});
    if (project.can_edit) header.value = {...header.value, start: form.value.start, months: form.value.months, currency: form.value.currency};
    loadedSnapshot.value = JSON.stringify(inputsFromForm(form.value)); confirmed.value = false;
  }
}
function newProject() {
  if (busy.value) return;
  state.value = createBusinessState(); current.value = null;
  header.value = {title: '', start: '', months: 36, currency: ''};
  form.value = formFromInputs(); selected.value = []; ignored.value = []; selectionProblems.value = [];
  error.value = ''; notice.value = ''; totalSize.value = 0; confirmed.value = false;
}
async function openProject(project) {
  if (busy.value) return;
  const request = ++detailRequest; action.value = 'open'; error.value = ''; notice.value = '';
  try {
    const detail = await props.api(projectUrl(props.company.id, project.id));
    if (!active || request !== detailRequest) return;
    assertProject(detail, props.company.id, project.id);
    state.value = createBusinessState(detail); selected.value = []; ignored.value = []; selectionProblems.value = []; totalSize.value = 0;
    const values = detail.inputs || detail.extraction?.inputs || {};
    header.value = {title: detail.title, start: values.start || '', months: values.months ?? 36, currency: values.currency || ''};
    setProject(detail, true);
  } catch (e) {if (active && e.name !== 'AbortError') error.value = e.message;}
  finally {if (active && request === detailRequest) action.value = '';}
}
function closeProject() {if (busy.value) return; state.value = null; current.value = null; selected.value = []; error.value = ''; notice.value = ''; loadProjects();}
async function changeProjectState(project, restore = false) {
  if (busy.value || project.company_id !== props.company.id || !(restore ? project.can_restore : project.can_delete)) return;
  const companyId = props.company.id;
  action.value = restore ? 'restore' : 'delete'; error.value = ''; notice.value = '';
  try {
    const path = `/api/business-projects/${project.id}${restore ? '/restore' : ''}?company_id=${companyId}&expected_revision=${project.revision}`;
    const detail = await props.api(path, {method: restore ? 'POST' : 'DELETE'});
    if (!active || companyId !== props.company.id) return;
    assertProject(detail, companyId, project.id);
    if (current.value?.id === project.id) {state.value = null; current.value = null; selected.value = [];}
    notice.value = restore ? 'Папка и её отчёты восстановлены.' : 'Папка и её отчёты удалены из списка и бота. Для возврата включите «Показать удалённые» и нажмите «Восстановить».';
    await loadProjects(); emit('generated');
  } catch (e) {if (active && e.name !== 'AbortError') {error.value = e.message; await loadProjects();}}
  finally {if (active) action.value = '';}
}
function chooseSources(event) {
  const result = prepareSources(event.target.files || []); event.target.value = '';
  selected.value = result.files; ignored.value = result.ignored; selectionProblems.value = result.problems; totalSize.value = result.total;
  if (!String(header.value.title || '').trim()) header.value.title = folderProjectTitle(result.files);
  state.value.uploaded.clear(); error.value = ''; notice.value = '';
}
async function recoverConflict(e) {
  if (e.status !== 409 || !current.value) return;
  try {
    const detail = await props.api(projectUrl(props.company.id, current.value.id));
    if (!active) return;
    setProject(detail); confirmed.value = false;
    error.value = 'Папка уже изменена. Список файлов и версия обновлены. Проверьте параметры и повторите действие; ваши изменения формы сохранены.';
  } catch { /* Keep the original error and local inputs if refreshing also failed. */ }
}
async function upload() {
  if (busy.value || !selected.value.length || selectionProblems.value.length) return;
  const workingState = state.value, companyId = props.company.id;
  action.value = 'upload'; error.value = ''; notice.value = '';
  try {
    const project = await uploadProjectFolder(props.api, workingState, {companyId, header: {...header.value}, sources: selected.value,
      isCurrent: () => active && state.value === workingState && props.company.id === companyId,
      onProject: project => setProject(project), onStep: value => {step.value = value;},
    });
    if (!active) return;
    setProject(project, true);
    notice.value = project.generations?.length && !project.validation?.length ? 'Бизнес-план и ТЭО сформированы. Скачайте PDF и Excel ниже.' : 'Папка изучена. Проверьте замечания и заполните недостающие параметры ниже.';
    if (project.generations?.length) emit('generated');
    await loadProjects();
  } catch (e) {if (active && e.name !== 'AbortError') {error.value = uploadFailureMessage(e, workingState); await recoverConflict(e);}}
  finally {if (active) {action.value = ''; step.value = '';}}
}
async function generate() {
  if (busy.value || !canEdit.value || requiredFields.value.length || needsConfirmation.value && !confirmed.value) return;
  const companyId = props.company.id, id = current.value.id;
  action.value = 'generate'; step.value = 'Рассчитываем финансовую модель и формируем четыре файла…'; error.value = ''; notice.value = '';
  try {
    const detail = await generateProject(props.api, current.value, companyId, inputsFromForm(form.value), confirmed.value,
      () => active && current.value?.id === id && props.company.id === companyId);
    if (!active) return;
    setProject(detail, true);
    if (detail.validation?.length || detail.status !== 'ready') notice.value = 'Проверьте замечания. Новый комплект отчётов появится после заполнения обязательных данных.';
    else {notice.value = 'Бизнес-план и ТЭО сформированы из одной финансовой модели.'; emit('generated');}
    await loadProjects();
  } catch (e) {if (active && e.name !== 'AbortError') {error.value = e.message; await recoverConflict(e);}}
  finally {if (active) {action.value = ''; step.value = '';}}
}
function addRow(collection) {
  if (!canEdit.value || form.value[collection].length >= 200) return;
  if (collection === 'products') form.value.products.push({name: '', unit: '', price: '', unit_cost: '', quantities: '', capacity: null});
  if (collection === 'assets') {form.value.no_assets = false; form.value.assets.push({name: '', value: '', life_months: '', commissioning_month: ''});}
  if (collection === 'loans') {form.value.no_loans = false; form.value.loans.push({name: '', opening_balance: '', drawdowns: '', principal: '', interest: ''});}
}
function setStart(target, event) {target.start = event.target.value ? event.target.value + '-01' : '';}
function makeMonthly(entry) {entry.object[entry.field] = resizeValues(entry.object[entry.field], form.value.months);}
function downloads(generation) {
  return [['business_archive', 'Бизнес-план'], ['teo_archive', 'ТЭО']].flatMap(([key, title]) => {
    const archive = generation[key];
    return archive?.company_id === props.company.id && archive.status === 'ready' ? ['pdf', 'xlsx'].map(format => ({title: title + ' · ' + (format === 'pdf' ? 'PDF' : 'Excel'), url: archiveFileUrl(archive, format)})) : [];
  });
}
function reloadInputs() {if (!busy.value && current.value) setProject(current.value, true);}
function isMissing(field) {return requiredFields.value.some(item => item.field === field || item.field.startsWith(field + '[') || item.field.startsWith(field + '.'));}
function goToField(field) {
  const root = field.split(/[.\[]/)[0];
  const target = modelForm.value?.querySelector('[data-bp-field="' + field + '"]') || modelForm.value?.querySelector('[data-bp-field="' + root + '"]');
  if (!target) return;
  const nested = target.matches('details') ? target : target.querySelector('details');
  if (nested) nested.open = true;
  let container = target.parentElement;
  while (container) {if (container.tagName === 'DETAILS') container.open = true; container = container.parentElement;}
  target.scrollIntoView({behavior:'smooth', block:'center'});
  const input = target.matches('input,select,textarea') ? target : target.querySelector('input:not([type="checkbox"]),select,textarea,button,input');
  input?.focus({preventScroll:true});
}
function guidance(field) {return fieldGuidance(field);}
function scheduleField(entry, month = null) {
  const product = form.value.products.indexOf(entry.object), loan = form.value.loans.indexOf(entry.object);
  const field = product >= 0 ? `products[${product}].${entry.field}` : loan >= 0 ? `loans[${loan}].${entry.field}` : entry.field;
  return month == null ? field : `${field}[${month}]`;
}
</script>

<template>
  <section class="card business-projects" aria-labelledby="business-projects-title">
    <div class="card-h"><div><h3 id="business-projects-title">Бизнес-план и ТЭО из папки</h3><p class="sub">{{company.name}} · один расчёт, два отчёта в PDF и Excel</p></div><button v-if="canUpload&&!editing" :disabled="busy||listLoading" @click="newProject"><AppIcon name="plus"/> Новая папка проекта</button></div>
    <div class="card-b">
      <p class="sub bp-intro">Загрузите папку с исходными таблицами, договорами и описанием проекта. Готовый бизнес-план из папки можно скачать с его исходным оформлением. Для нового расчёта система изучит данные и подготовит отдельный бизнес-план и ТЭО; недостающие параметры появятся ниже.</p>
      <div class="bp-actions"><a class="button secondary" :href="templateUrl(company.id)"><AppIcon name="download"/> Шаблон исходных данных · Excel</a><button class="ghost" :disabled="busy||listLoading" @click="loadProjects"><AppIcon name="refresh"/> Обновить папки</button></div>
      <p v-if="error" class="error" role="alert">{{error}}</p><p v-if="notice" class="notice" role="status">{{notice}}</p>
      <p v-if="listLoading" class="loading" role="status">Загружаем проекты…</p>
      <label v-if="canUpload" class="bp-actions"><input v-model="showDeleted" type="checkbox" :disabled="busy"> Показать удалённые папки</label>
      <div v-if="visibleProjects.length" class="bp-project-list"><div v-for="project in visibleProjects" :key="project.id" class="bp-project-row"><button class="secondary bp-project" :class="{selected:current?.id===project.id}" :disabled="busy||editing" @click="openProject(project)"><span><b>{{project.title}}</b><small>{{statusLabel(project)}} · версия исходных данных {{project.revision}}</small></span><AppIcon name="arrow"/></button><button v-if="project.can_delete" class="secondary tiny" :disabled="busy||editing" @click="changeProjectState(project)">Удалить папку и её отчёты</button><button v-if="project.can_restore" class="secondary tiny" :disabled="busy||editing" @click="changeProjectState(project,true)">Восстановить</button></div></div>
      <p v-else-if="!listLoading&&!editing" class="sub">Папок пока нет. Начните с новой папки или заполненного Excel-шаблона.</p>

      <div v-if="editing" class="bp-workspace">
        <div class="section-head"><h3>{{current?current.title:'Новая папка проекта'}}</h3><button class="secondary tiny" :disabled="busy" @click="closeProject">{{changed?'Закрыть без сохранения параметров':'Закрыть проект'}}</button></div>
        <div v-if="current?.can_delete||current?.can_restore" class="bp-actions"><button v-if="current.can_delete" class="secondary tiny" :disabled="busy" @click="changeProjectState(current)">Удалить папку и её отчёты</button><button v-if="current.can_restore" class="secondary tiny" :disabled="busy" @click="changeProjectState(current,true)">Восстановить папку и отчёты</button></div>
        <p v-if="current?.status==='deleted'" class="warning">Папка удалена. Для нового расчёта сначала восстановите её. Исходные файлы сохранены.</p>
        <p v-if="current&&!canEdit" class="sub">Просмотр проекта и готовых отчётов. Изменять исходные данные может автор папки с правом импорта.</p>
        <div v-if="!current||canEdit" class="bp-upload-area">
          <div class="bp-fields">
            <label class="bp-wide">Название проекта <span class="bp-required-tag">Обязательно</span><input v-model="header.title" maxlength="160" :disabled="busy||!!state.createFields||!!current" placeholder="Производство лекарственных препаратов"><small>Заполнится из имени папки, если своё название ещё не указано.</small></label>
            <template v-if="!current?.native_model">
              <label>Начало прогноза <span class="bp-required-tag">Обязательно</span><input type="month" :value="header.start.slice(0,7)" min="2000-01" max="2100-12" :disabled="busy" @input="setStart(header,$event)"><small>Выберите первый месяц расчёта. Дата загрузки и дата баланса не заменяют начало прогноза.</small></label>
              <label>Прогноз, месяцев<input v-model="header.months" type="number" min="1" max="60" step="1" :disabled="busy"><small>От 1 до 60; по умолчанию 36 месяцев</small></label>
              <label>Валюта модели<select v-model="header.currency" :disabled="busy"><option value="">Определить из источников</option><option>USD</option><option>UZS</option><option>EUR</option></select></label>
            </template>
          </div>
          <p class="sub bp-note">{{current?.native_model?'Оригинальная модель: 36 месяцев, USD. Период для поиска отчётов выбирается ниже, перед формированием.':'Начало прогноза задаётся отдельно от даты загрузки.'}} При добавлении новых источников анализ заново проверит финансовые параметры; готовые версии останутся в истории.</p>
          <div class="bp-actions"><label class="button"><AppIcon name="import"/> Выбрать папку<input type="file" webkitdirectory multiple :accept="SOURCE_ACCEPT" hidden :disabled="busy" @change="chooseSources"></label><label class="button secondary"><AppIcon name="file"/> Выбрать отдельные файлы<input type="file" multiple :accept="SOURCE_ACCEPT" hidden :disabled="busy" @change="chooseSources"></label></div>
          <p class="sub bp-note">PDF, XLSX, XLTX, DOCX, CSV, TXT, JSON, ZIP, PNG и JPEG. До 100 файлов, 20 МБ на файл и 100 МБ на папку. Поддерживаемый оригинальный Excel пересчитывается по своим формулам; изображения и договоры сохраняются как источники.</p>
          <p v-for="problem in selectionProblems" :key="problem" class="error" role="alert">{{problem}}</p>
          <details v-if="ignored.length" class="bp-details"><summary>Пропущены служебные файлы: {{ignored.length}}</summary><ul><li v-for="item in ignored" :key="item.name">{{item.name}} — {{item.reason}}</li></ul></details>
          <details v-if="selected.length" class="bp-details" :open="selected.some(item=>!state.uploaded.has(item.key))"><summary>Выбрано {{selected.length}} файлов · {{sourceSize(totalSize)}}</summary><ul class="bp-files"><li v-for="item in selected" :key="item.key"><span>{{item.relativePath}}</span><small>{{sourceSize(item.size)}} {{state.uploaded.has(item.key)?'· загружен':''}}</small></li></ul></details>
          <p v-if="state.createUnknown" class="warning">Сервер мог создать папку. Повторите загрузку: она продолжится в том же проекте.</p>
          <p v-if="selected.length&&uploadRequirement" class="warning" role="status">Перед загрузкой: {{uploadRequirement}}</p>
          <button :disabled="busy||!selected.length||selectionProblems.length>0||!!uploadRequirement" @click="upload"><AppIcon name="import"/> {{current?'Добавить файлы и выполнить анализ':'Загрузить папку и сформировать отчёты'}}</button>
        </div>
        <p v-if="busy" class="loading bp-progress" role="status">{{step||(action==='native'?'Работаем с оригинальными Word и Excel…':'Загружаем проект…')}}</p>

        <template v-if="current">
          <section v-if="originalReports.length" class="bp-required-guide" aria-labelledby="bp-original-title">
            <h3 id="bp-original-title">Готовый бизнес-план из вашей папки</h3>
            <p class="sub bp-note">Загруженные документы сохраняют свои разделы, таблицы, оформление, формулы и исходные суммы. Скачивание выдаёт исходный файл. Изменения параметров ниже попадут в отдельный новый расчёт.</p>
            <div class="bp-original-list"><article v-for="file in originalReports" :key="file.id" class="bp-required-item"><b>{{file.filename}}</b><a class="button secondary" :href="file.downloadUrl"><AppIcon name="download"/> Скачать исходный {{file.format}}</a></article></div>
            <p v-if="!originalReports.some(file=>file.format==='PDF')" class="sub bp-note">{{current.native_model?'Для нового PDF выполните пересчёт оригинала и нажмите «Сформировать бизнес-план и ТЭО по оригиналу». PDF будет подготовлен по Word из этой папки.':'Для PDF с исходным оформлением добавьте PDF, экспортированный из исходного Word, через «Выбрать отдельные файлы».'}}</p>
          </section>
          <details class="bp-details"><summary>Исходные файлы проекта · {{current.files?.length||0}}</summary><ul class="bp-files"><li v-for="file in current.files||[]" :key="file.relative_path"><span>{{file.relative_path||file.filename}}</span><small>{{sourceSize(file.size||0)}} <a v-if="canEdit&&file.download_url" :href="file.download_url">Скачать исходник</a></small></li></ul></details>
          <NativeWorkbook v-if="current.native_model" :api="api" :company="company" :project="current" @updated="setProject($event,true)" @generated="emit('generated');loadProjects()" @working="action=$event?'native':''"/>
          <template v-if="!current.native_model">
          <section v-if="canEdit&&action!=='upload'" class="bp-required-guide" aria-labelledby="bp-required-title">
            <h3 id="bp-required-title">Обязательные поля · {{remainingRequirements.length ? 'осталось заполнить ' + remainingRequirements.length + ' разделов' : 'все поля заполнены'}}</h3>
            <p class="sub bp-note">1. Выберите месяц начала и валюту. 2. Заполните поля ниже по документам. 3. Проверьте расхождения источников и подтвердите проверку. 4. Нажмите «Рассчитать бизнес-план и ТЭО».</p>
            <p class="sub bp-note">Суммы должны относиться к началу выбранного прогноза и одной валюте. Если документ составлен в тысячах сумов, сначала переведите сумму в единицы валюты модели по подтверждённому курсу. Пустое поле не означает ноль.</p>
            <div v-if="remainingRequirements.length" class="bp-required-list">
              <article v-for="item in remainingRequirements" :key="item.field" class="bp-required-item">
                <h4>{{guidance(item.field).label || fieldLabel(item.field)}}</h4>
                <p>{{guidance(item.field).what}}</p>
                <p v-if="guidance(item.field).source" class="sub"><b>Где взять:</b> {{guidance(item.field).source}}</p>
                <p v-if="guidance(item.field).how" class="sub"><b>Что сделать:</b> {{guidance(item.field).how}}</p>
                <p v-if="guidance(item.field).zero" class="sub">{{guidance(item.field).zero}}</p>
                <button type="button" class="secondary tiny" @click="goToField(item.field)">К полю «{{guidance(item.field).label || fieldLabel(item.field)}}»</button>
              </article>
            </div>
            <p v-else class="notice" role="status">{{current.status==='ready'&&!changed?'Обязательные параметры приняты в расчёт. Готовые PDF и Excel находятся ниже.':'Обязательные поля заполнены. Проверьте источники и выполните расчёт; сервер проверит числа, графики и согласованность модели.'}}</p>
          </section>
          <div v-if="sourceProblems.length||savedValidation.length" class="bp-review">
            <h3>{{savedValidation.length?'Исправьте значения перед расчётом':'Проверьте условия расчёта'}}</h3>
            <p v-if="form.products.length" class="sub bp-note">Распознано {{form.products.length}} позиций продукции. Недостающие параметры находятся в начале формы ниже.</p>
            <p v-if="current.status==='ready'&&current.extraction?.parameters_confirmed" class="sub bp-note">Финансовые параметры проверены и приняты в расчёт. Ниже сохранены замечания к первоначальным источникам.</p>
            <ul v-if="savedValidation.length" class="bp-missing"><li v-for="(issue,index) in savedValidation" :key="'v'+index"><b>{{fieldLabel(issue.field)}}</b>: {{issueMessage(issue)}}</li></ul>
            <details v-if="sourceProblems.length" class="bp-details"><summary>Условия и расхождения, которые нужно проверить · {{sourceProblems.length}}</summary><ul><li v-for="(issue,index) in sourceProblems" :key="'s'+index"><b>{{fieldLabel(issue.field)}}</b>: {{issueMessage(issue)}}<small v-for="(source,sourceIndex) in issue.sources" :key="sourceIndex">{{sourceLocation(source)}}</small></li></ul></details>
          </div>
          <details v-if="referenceNotes.length" class="bp-details"><summary>Справочные заметки по документам · {{referenceNotes.length}}</summary><ul><li v-for="(issue,index) in referenceNotes" :key="index"><b>{{fieldLabel(issue.field)}}</b>: {{issueMessage(issue)}}<small v-for="(source,sourceIndex) in issue.sources" :key="sourceIndex">{{sourceLocation(source)}}</small></li></ul></details>
          <details v-if="evidence.length" class="bp-details"><summary>Параметры, распознанные в источниках · {{evidence.length}}</summary><ul><li v-for="(item,index) in evidence" :key="index"><b>{{fieldLabel(item.field)}}</b><small>{{sourceLocation(item.source)}}<template v-if="item.note"> · {{item.note}}</template></small><span v-if="item.status==='needs_confirmation'" class="pill warn">Проверьте значение</span></li></ul></details>
          <details v-if="narratives.length" class="bp-details"><summary>Описание в исходных документах</summary><details v-for="(item,index) in narratives" :key="index" class="bp-details"><summary>{{item.filename||'Документ'}}</summary><p class="bp-reference">{{item.text}}</p></details></details>

          <template v-if="canEdit">
          <details class="bp-details" :open="current.status!=='ready'"><summary>Параметры финансовой модели · проверить или изменить</summary>
          <div class="section-head bp-form-head"><div><h3>Параметры финансовой модели</h3><p class="sub">Все суммы в одной валюте, без НДС. Отсутствующее значение оставьте пустым; ноль указывайте явно.</p></div><button v-if="changed" class="ghost tiny" :disabled="busy" @click="reloadInputs">Вернуть сохранённые параметры</button></div>
          <form ref="modelForm" @submit.prevent="generate">
            <fieldset :disabled="busy||!canEdit" class="bp-fieldset">
              <div class="bp-fields">
                <label class="bp-wide" data-bp-field="title">Название отчётов <span class="bp-required-tag">Обязательно</span><input v-model="form.title" maxlength="160" required :aria-invalid="isMissing('title')"></label>
                <label data-bp-field="start">Начало прогноза <span class="bp-required-tag">Обязательно</span><input type="month" :value="form.start.slice(0,7)" min="2000-01" max="2100-12" required :aria-invalid="isMissing('start')" @input="setStart(form,$event)"><small>{{guidance('start').how}}</small></label>
                <label data-bp-field="months">Прогноз, месяцев <span class="bp-required-tag">Обязательно</span><input v-model="form.months" type="number" min="1" max="60" step="1" required><small>{{guidance('months').how}}</small></label>
                <label data-bp-field="currency">Валюта <span class="bp-required-tag">Обязательно</span><select v-model="form.currency" required :aria-invalid="isMissing('currency')"><option value="">Выберите валюту</option><option>USD</option><option>UZS</option><option>EUR</option></select><small>{{guidance('currency').how}}</small></label>
                <label data-bp-field="tax_rate">Налог на прибыль, % <span class="bp-required-tag">Обязательно</span><input v-model="form.tax_rate" type="text" inputmode="decimal" required :aria-invalid="isMissing('tax_rate')"><small>{{guidance('tax_rate').how}}</small></label>
                <label data-bp-field="discount_rate">Годовая ставка дисконтирования, % <span class="bp-required-tag">Обязательно</span><input v-model="form.discount_rate" type="text" inputmode="decimal" required :aria-invalid="isMissing('discount_rate')"><small>{{guidance('discount_rate').how}}</small></label>
                <label v-for="[field,label] in BUSINESS_SCALARS" :key="field" :data-bp-field="field">{{label}} <span class="bp-required-tag">Обязательно</span><input v-model="form[field]" type="text" inputmode="decimal" required :aria-invalid="isMissing(field)"><small>{{guidance(field).what}} {{guidance(field).how}}</small></label>
              </div>
              <p class="sub bp-note">Стоимость актива, вводимого в месяце прогноза, учитывается как CAPEX в этом месяце. Такой расход повторно не включайте в инвестиции до начала прогноза.</p>

              <h3 class="bp-form-head">Постоянные расходы и взносы в капитал</h3><p class="sub">Укажите сумму на каждый месяц или отдельный график. Пустая сумма не означает ноль.</p>
              <p class="sub bp-note">{{guidance('fixed_costs').what}} {{guidance('fixed_costs').how}} {{guidance('equity').how}}</p>
              <div v-for="entry in financialSchedules" :key="entry.key" class="bp-schedule" :data-bp-field="entry.field">
                <template v-if="!Array.isArray(entry.object[entry.field])"><label>{{entry.label}} · каждый месяц <span class="bp-required-tag">Обязательно</span><input v-model="entry.object[entry.field]" type="text" inputmode="decimal" required :aria-invalid="isMissing(entry.field)"><small>{{guidance(entry.field).how}}</small></label><button type="button" class="secondary tiny" @click="makeMonthly(entry)">По месяцам</button></template>
                <details v-else class="bp-details"><summary>{{entry.label}} · график: {{entry.object[entry.field].length}} месяцев</summary><p v-if="entry.object[entry.field].length!==Number(form.months)" class="warning">Длина графика отличается от прогноза ({{form.months}} мес.). Уже введённые значения сохраняются до вашего выбора.<button type="button" class="secondary tiny" @click="makeMonthly(entry)">{{entry.object[entry.field].length>Number(form.months)?'Убрать последние '+(entry.object[entry.field].length-Number(form.months))+' значений':'Добавить пустые месяцы'}}</button></p><div class="bp-month-grid"><label v-for="(_,index) in entry.object[entry.field]" :key="index">{{labels[index]||'Месяц '+(index+1)}}<input :data-bp-field="scheduleField(entry,index)" v-model="entry.object[entry.field][index]" type="text" inputmode="decimal" required :aria-invalid="isMissing(scheduleField(entry,index))"></label></div><button type="button" class="ghost tiny" @click="entry.object[entry.field]=''">Заменить график одним ежемесячным значением</button></details>
              </div>

              <div class="section-head bp-form-head" data-bp-field="assets"><h3>Основные средства · {{form.assets.length}} <span class="bp-required-tag">Обязательно указать наличие</span></h3><button type="button" class="secondary tiny" :disabled="form.assets.length>=200" @click="addRow('assets')"><AppIcon name="plus"/> Добавить актив</button></div>
              <p class="sub bp-note">{{guidance('assets').what}} {{guidance('assets').how}} {{guidance('assets').zero}}</p>
              <label v-if="!form.assets.length" class="bp-check"><input v-model="form.no_assets" type="checkbox"> Основных средств в этой модели нет</label>
              <div v-for="(asset,index) in form.assets" :key="index" class="bp-row-card">
                <div class="section-head"><b>Актив №{{index+1}}</b><button type="button" class="ghost tiny" @click="form.assets.splice(index,1);form.no_assets=false">Удалить строку</button></div>
                <div class="bp-fields">
                  <label :data-bp-field="`assets[${index}].name`">Название <span class="bp-required-tag">Обязательно</span><input v-model="asset.name" maxlength="160" required></label>
                  <label :data-bp-field="`assets[${index}].value`">Стоимость <span class="bp-required-tag">Обязательно</span><input v-model="asset.value" type="text" inputmode="decimal" required><small>{{guidance(`assets[${index}].value`).how}}</small></label>
                  <label :data-bp-field="`assets[${index}].life_months`">Срок амортизации, месяцев <span class="bp-required-tag">Обязательно</span><input v-model="asset.life_months" type="number" min="1" max="1200" step="1" required><small>{{guidance(`assets[${index}].life_months`).how}}</small></label>
                  <label :data-bp-field="`assets[${index}].commissioning_month`">Месяц ввода <span class="bp-required-tag">Обязательно</span><input v-model="asset.commissioning_month" type="number" min="0" :max="form.months" step="1" required><small>0 — актив на начало; 1 — первый месяц прогноза</small></label>
                </div>
              </div>

              <div class="section-head bp-form-head" data-bp-field="loans"><h3>Кредиты · {{form.loans.length}} <span class="bp-required-tag">Обязательно указать наличие</span></h3><button type="button" class="secondary tiny" :disabled="form.loans.length>=200" @click="addRow('loans')"><AppIcon name="plus"/> Добавить кредит</button></div>
              <p class="sub bp-note">{{guidance('loans').what}} {{guidance('loans').how}} {{guidance('loans').zero}}</p>
              <label v-if="!form.loans.length" class="bp-check"><input v-model="form.no_loans" type="checkbox"> Кредитов в этой модели нет</label>
              <div v-for="(loan,index) in form.loans" :key="index" class="bp-row-card"><div class="section-head"><b>Кредит №{{index+1}}</b><button type="button" class="ghost tiny" @click="form.loans.splice(index,1);form.no_loans=false">Удалить строку</button></div><div class="bp-fields"><label :data-bp-field="`loans[${index}].name`">Название <span class="bp-required-tag">Обязательно</span><input v-model="loan.name" maxlength="160" required></label><label :data-bp-field="`loans[${index}].opening_balance`">Долг на начало прогноза <span class="bp-required-tag">Обязательно</span><input v-model="loan.opening_balance" type="text" inputmode="decimal" required><small>{{guidance(`loans[${index}].opening_balance`).how}}</small></label></div><small>Получение, погашение и проценты задайте в графиках ниже. Процентную ставку вместо суммы процентов здесь не вводите.</small></div>

              <h3 v-if="loanSchedules.length" class="bp-form-head">Помесячные графики кредитов</h3><p v-if="loanSchedules.length" class="sub">Одно значение повторяется каждый месяц. Для отдельных выдач и погашений используйте график по месяцам.</p>
              <div v-for="entry in loanSchedules" :key="entry.key" class="bp-schedule" :data-bp-field="scheduleField(entry)">
                <template v-if="!Array.isArray(entry.object[entry.field])"><label>{{entry.label}} · каждый месяц<input :data-bp-field="scheduleField(entry)" v-model="entry.object[entry.field]" type="text" inputmode="decimal" required :aria-invalid="isMissing(scheduleField(entry))"></label><button type="button" class="secondary tiny" @click="makeMonthly(entry)">По месяцам</button></template>
                <details v-else class="bp-details"><summary>{{entry.label}} · график: {{entry.object[entry.field].length}} месяцев</summary><p v-if="entry.object[entry.field].length!==Number(form.months)" class="warning">Длина графика отличается от прогноза ({{form.months}} мес.). Уже введённые значения сохраняются до вашего выбора.<button type="button" class="secondary tiny" @click="makeMonthly(entry)">{{entry.object[entry.field].length>Number(form.months)?'Убрать последние '+(entry.object[entry.field].length-Number(form.months))+' значений':'Добавить пустые месяцы'}}</button></p><div class="bp-month-grid"><label v-for="(_,index) in entry.object[entry.field]" :key="index">{{labels[index]||'Месяц '+(index+1)}}<input :data-bp-field="scheduleField(entry,index)" v-model="entry.object[entry.field][index]" type="text" inputmode="decimal" required :aria-invalid="isMissing(scheduleField(entry,index))"></label></div><button type="button" class="ghost tiny" @click="entry.object[entry.field]=''">Заменить график одним ежемесячным значением</button></details>
              </div>

              <details class="bp-details bp-products" data-bp-field="products" :open="reviewProducts"><summary>Продукция · {{form.products.length}} · цены и объёмы</summary>
              <div class="section-head bp-form-head"><h3>Продукция · {{form.products.length}}</h3><button type="button" class="secondary tiny" :disabled="form.products.length>=200" @click="addRow('products')"><AppIcon name="plus"/> Добавить продукцию</button></div>
              <div v-for="(product,index) in form.products" :key="index" class="bp-row-card"><div class="section-head"><b>Продукция №{{index+1}}</b><button type="button" class="ghost tiny" @click="form.products.splice(index,1)">Удалить строку</button></div><div class="bp-fields"><label :data-bp-field="`products[${index}].name`">Название <span class="bp-required-tag">Обязательно</span><input v-model="product.name" maxlength="160" required></label><label :data-bp-field="`products[${index}].unit`">Единица измерения <span class="bp-required-tag">Обязательно</span><input v-model="product.unit" maxlength="40" placeholder="упаковка" required></label><label :data-bp-field="`products[${index}].price`">Цена реализации <span class="bp-required-tag">Обязательно</span><input v-model="product.price" type="text" inputmode="decimal" required><small>{{guidance(`products[${index}].price`).how}}</small></label><label :data-bp-field="`products[${index}].unit_cost`">Себестоимость единицы <span class="bp-required-tag">Обязательно</span><input v-model="product.unit_cost" type="text" inputmode="decimal" required><small>{{guidance(`products[${index}].unit_cost`).how}}</small></label></div><label class="bp-check"><input type="checkbox" :checked="product.capacity!=null" @change="product.capacity=$event.target.checked?'':null"> Указать производственную мощность</label><small>Объём реализации{{product.capacity!=null?' и мощность':''}} — в графиках ниже.</small></div>
              <p v-if="!form.products.length" class="sub">Для расчёта добавьте хотя бы один препарат или другой продукт.</p>
              <h3 v-if="productSchedules.length" class="bp-form-head">Объёмы и помесячные графики</h3><p v-if="productSchedules.length" class="sub">Одно значение повторяется каждый месяц. Для сезонности используйте график по месяцам.</p>
              <div v-for="entry in productSchedules" :key="entry.key" class="bp-schedule" :data-bp-field="scheduleField(entry)">
                <template v-if="!Array.isArray(entry.object[entry.field])"><label>{{entry.label}} · каждый месяц<input :data-bp-field="scheduleField(entry)" v-model="entry.object[entry.field]" type="text" inputmode="decimal" required :aria-invalid="isMissing(scheduleField(entry))"></label><button type="button" class="secondary tiny" @click="makeMonthly(entry)">По месяцам</button></template>
                <details v-else class="bp-details"><summary>{{entry.label}} · график: {{entry.object[entry.field].length}} месяцев</summary><p v-if="entry.object[entry.field].length!==Number(form.months)" class="warning">Длина графика отличается от прогноза ({{form.months}} мес.). Уже введённые значения сохраняются до вашего выбора.<button type="button" class="secondary tiny" @click="makeMonthly(entry)">{{entry.object[entry.field].length>Number(form.months)?'Убрать последние '+(entry.object[entry.field].length-Number(form.months))+' значений':'Добавить пустые месяцы'}}</button></p><div class="bp-month-grid"><label v-for="(_,index) in entry.object[entry.field]" :key="index">{{labels[index]||'Месяц '+(index+1)}}<input :data-bp-field="scheduleField(entry,index)" v-model="entry.object[entry.field][index]" type="text" inputmode="decimal" required :aria-invalid="isMissing(scheduleField(entry,index))"></label></div><button type="button" class="ghost tiny" @click="entry.object[entry.field]=''">Заменить график одним ежемесячным значением</button></details>
              </div>
              </details>

              <details class="bp-details"><summary>Описание проекта для бизнес-плана и ТЭО</summary><p class="sub bp-note">Укажите подтверждённые сведения. Пустые разделы останутся отмечены как требующие дополнения.</p><div class="bp-narratives"><label v-for="[field,label] in NARRATIVE_FIELDS" :key="field">{{label}}<textarea v-model="form[field]" maxlength="12000"></textarea></label></div></details>
              <label class="bp-check bp-confirm"><input v-model="confirmed" type="checkbox"> Я проверил финансовые параметры и замечания к источникам</label>
              <p v-if="needsConfirmation&&!confirmed" class="sub">Для расчёта по исправленным параметрам подтвердите проверку источников.</p>
              <p v-if="remainingRequirements.length" class="warning" role="status">Перед расчётом заполните {{remainingRequirements.length}} разделов. Откройте нужное поле кнопкой «К полю» в списке обязательных данных.</p>
              <div v-if="canEdit" class="bp-actions"><button :disabled="busy||requiredFields.length>0||needsConfirmation&&!confirmed"><AppIcon name="report"/> Рассчитать бизнес-план и ТЭО</button><span v-if="changed" class="pill warn">Есть изменения параметров</span></div>
            </fieldset>
          </form>
          </details>
          </template>

          </template>
          <div v-if="latest&&!current.native_model" class="bp-results">
            <h3>Готовые отчёты · версия исходных данных {{latest.revision}}</h3><p class="sub">{{latest.business_archive?.title||current.title}}</p><p class="sub">{{uploadLabel(latest.created_at)}} · суммы в валюте, указанной в файлах этой версии</p>
            <div class="bp-metrics"><div><small>NPV проекта</small><b>{{money(latest.metrics?.npv)}}</b></div><div><small>IRR годовая</small><b>{{ratio(latest.metrics?.irr_annual)}}</b></div><div><small>Окупаемость, мес.</small><b>{{latest.metrics?.payback_months==null?'Не определена':money(latest.metrics.payback_months)}}</b></div><div><small>Минимум денег</small><b>{{money(latest.metrics?.minimum_cash)}}</b></div></div>
            <p v-if="latest.metrics?.irr_reason" class="sub bp-note">{{latest.metrics.irr_reason}}</p><p v-if="String(latest.metrics?.minimum_cash||'').startsWith('-')" class="warning">В прогнозе есть недостаток денег. Проверьте финансирование и сроки расходов в отчётах.</p>
            <p v-if="changed||current.status!=='ready'||latest.revision!==current.revision" class="warning">Файлы относятся к сохранённой версии. Изменённые параметры и новые источники попадут в следующий расчёт.</p>
            <div class="bp-actions"><a v-for="file in downloads(latest)" :key="file.title" class="button secondary" :href="file.url"><AppIcon name="download"/> {{file.title}}</a></div>
            <details v-if="generations.length>1" class="bp-details"><summary>Предыдущие расчёты · {{generations.length-1}}</summary><div v-for="generation in generations.slice(1)" :key="generation.id" class="bp-generation"><b>{{uploadLabel(generation.created_at)}} · версия исходных данных {{generation.revision}}</b><div class="bp-actions"><a v-for="file in downloads(generation)" :key="file.title" class="button secondary tiny" :href="file.url">{{file.title}}</a></div></div></details>
          </div>
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.bp-original-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}.bp-original-list b{overflow-wrap:anywhere}.bp-original-list .button{margin-top:auto;white-space:normal}
.bp-required-guide{padding:18px;border:1px solid var(--line);border-radius:14px;background:var(--emx);margin:18px 0}.bp-required-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.bp-required-item{display:flex;flex-direction:column;gap:8px;border:1px solid var(--line);border-radius:12px;padding:16px;background:var(--paper,#fff)}.bp-required-item h4{margin:0;font-size:15px}.bp-required-item p{margin:0;line-height:1.55;overflow-wrap:anywhere}.bp-required-item button{align-self:flex-start;margin-top:auto;white-space:normal;text-align:left}.bp-required-tag{color:var(--em);font-size:11px;font-weight:700}.bp-fields input[aria-invalid="true"],.bp-fields select[aria-invalid="true"],.bp-schedule input[aria-invalid="true"]{border-color:var(--warn,#a76500);background:var(--warnl)}@media(max-width:700px){.bp-required-list{grid-template-columns:1fr}.bp-required-guide{padding:14px}}
.bp-intro{line-height:1.7;max-width:960px;margin:4px 0 18px}.bp-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:14px 0}.bp-project-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;margin:16px 0}.bp-project{padding:14px;min-height:70px;justify-content:space-between;white-space:normal;text-align:left}.bp-project.selected{border-color:var(--em);background:var(--eml)}.bp-project small{margin-top:5px}.bp-workspace{margin-top:20px;padding-top:20px;border-top:1px solid var(--line)}.bp-fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.bp-fields label,.bp-narratives label,.bp-month-grid label{display:grid;gap:6px;min-width:0}.bp-fields .bp-wide{grid-column:1/-1}.bp-note{line-height:1.65;margin:12px 0}.bp-upload-area{background:var(--emx);border:1px solid var(--line);border-radius:16px;padding:18px;margin:16px 0}.bp-details{margin:14px 0;border:1px solid var(--line);padding:12px 14px;border-radius:12px}.bp-details summary{cursor:pointer;font-weight:700;color:var(--ink)}.bp-details ul,.bp-review ul{padding-left:20px;margin:12px 0;display:grid;gap:10px;overflow-wrap:anywhere}.bp-files{list-style:none;padding-left:0!important}.bp-files li{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.bp-files small{white-space:nowrap}.bp-review{background:var(--warnl);border:1px solid var(--line);padding:16px;border-radius:14px;margin:16px 0}.bp-progress{margin-top:16px;overflow-wrap:anywhere}.bp-fieldset{border:0;padding:0;margin:0;min-width:0}.bp-form-head{margin-top:25px;margin-bottom:14px}.bp-form-head .sub{margin-top:6px}.bp-row-card{border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0}.bp-check{display:flex;align-items:flex-start;gap:10px;color:var(--ink);margin:14px 0}.bp-check input{flex:none}.bp-schedule{margin:12px 0;padding:14px;border:1px solid var(--line);border-radius:12px;display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap}.bp-schedule>label{display:grid;gap:7px;flex:1;min-width:200px}.bp-schedule .bp-details{width:100%;border:0;padding:0;margin:0}.bp-month-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:18px 0}.bp-month-grid label{font-size:12px}.bp-schedule .warning button{margin:8px 0 0;display:flex}.bp-narratives{display:grid;gap:14px}.bp-confirm{margin-top:22px}.bp-reference{white-space:pre-wrap;overflow-wrap:anywhere;margin-top:12px;line-height:1.7}.bp-results{border-top:1px solid var(--line);margin-top:25px;padding-top:22px}.bp-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}.bp-metrics>div{background:var(--emx);border:1px solid var(--line);border-radius:12px;padding:14px}.bp-metrics b{font-size:19px;display:block;margin-top:7px;overflow-wrap:anywhere}.bp-generation{margin-top:16px}.business-projects .error,.business-projects .notice{margin-top:14px}@media(max-width:850px){.bp-fields{grid-template-columns:repeat(2,minmax(0,1fr))}.bp-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:560px){.bp-fields{grid-template-columns:1fr}.bp-upload-area{padding:13px}.bp-month-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.bp-actions .button,.bp-actions>button{white-space:normal;text-align:center}.bp-files li{display:block}.bp-files small{margin-top:4px}.bp-schedule>label{min-width:0;flex-basis:100%}.bp-metrics b{font-size:16px}}
</style>
