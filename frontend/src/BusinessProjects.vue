<script setup>
import {ref, computed, watch, nextTick, onMounted, onUnmounted} from 'vue';
import AppIcon from './AppIcon.vue';
import NativeWorkbook from './NativeWorkbook.vue';
import OperationProgress from './OperationProgress.vue';
import {createOperationProgress} from './operationProgress.js';
import {archiveFileUrl, uploadLabel} from './reportArchive.js';
import {formatCents, toCents} from './overview/format.js';
import {SOURCE_ACCEPT, BUSINESS_SCALARS, NARRATIVE_FIELDS, prepareSources, sourceSize, projectUrl, templateUrl,
  formFromInputs, inputsFromForm, monthLabels, resizeValues, createBusinessState, uploadProjectFolder, generateProject,
  assertProject, fieldLabel, issueMessage, projectReview, evidenceRows, sourceLocation, folderProjectTitle, uploadFailureMessage,
  fieldGuidance, missingRequiredFields, missingHeaderFields, headerProblem, originalReportFiles, createProjectDraft, saveProjectDraft} from './businessProjects.js';

const props = defineProps({api: {type: Function, required: true}, company: {type: Object, required: true}, refresh: {type: Number, default: 0}});
const emit = defineEmits(['editing', 'generated']);
const projects = ref([]), canUpload = ref(false), listLoading = ref(false), error = ref(''), notice = ref('');
const state = ref(null), current = ref(null), action = ref(''), step = ref('');
const header = ref({title: '', start: '', months: 36, currency: ''});
const form = ref(formFromInputs()), selected = ref([]), ignored = ref([]), selectionProblems = ref([]), totalSize = ref(0);
const modelForm = ref(null), headerForm = ref(null), validationAttempted = ref(false), headerAttempted = ref(false);
const progress = ref({active:false,label:'',completed:0,total:0,error:''});
const operation = createOperationProgress(value => {progress.value = value;});
const showDeleted = ref(false);
const confirmed = ref(false), loadedSnapshot = ref('');
const projectMode = ref('manual'), flowStage = ref('project');
let active = true, listRequest = 0, detailRequest = 0;
const busy = computed(() => !!action.value), editing = computed(() => !!state.value);
const uploadRequirement = computed(() => projectMode.value === 'manual' || current.value?.native_model ? (!header.value.title.trim() ? 'Укажите название проекта.' : '') : headerProblem(header.value));
const canEdit = computed(() => !!current.value?.can_edit && current.value.status !== 'deleted');
const visibleProjects = computed(() => projects.value.filter(project => showDeleted.value || project.status !== 'deleted'));
const originalReports = computed(() => originalReportFiles(current.value, props.company.id));
const review = computed(() => projectReview(current.value?.extraction, current.value?.validation));
const sourceProblems = computed(() => review.value.checks);
const needsConfirmation = computed(() => sourceProblems.value.some(issue => issue.code !== 'missing_or_invalid' && issue.requires_confirmation));
const blockingSources = computed(() => sourceProblems.value.filter(issue => issue.blocking || ['error','fatal','blocking'].includes(String(issue.severity || '').toLowerCase())));
const validation = computed(() => review.value.fields);
const requiredFields = computed(() => missingRequiredFields(form.value));
const remainingRequirements = computed(() => requiredFields.value);
const headerMissing = computed(() => missingHeaderFields(header.value, projectMode.value, !!current.value?.native_model));
const savedValidation = computed(() => changed.value ? [] : validation.value.filter(issue => !requiredFields.value.some(item => item.field === issue.field)));
const referenceNotes = computed(() => review.value.references);
const evidence = computed(() => evidenceRows(current.value?.extraction));
const narratives = computed(() => current.value?.extraction?.narratives || []);
const labels = computed(() => monthLabels(form.value.start, form.value.months));
const changed = computed(() => !!current.value && JSON.stringify(inputsFromForm(form.value)) !== loadedSnapshot.value);
const generations = computed(() => (current.value?.generations || []).slice().sort((a, b) =>
  Number(b.id === current.value?.current_generation_id) - Number(a.id === current.value?.current_generation_id) || b.id - a.id));
const latest = computed(() => generations.value[0]);
const reportsStale = computed(() => changed.value || !readyReport(current.value));
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
const narrativeGroups = [
  ['project_description', 'initiator', 'strategy', 'location'],
  ['resources', 'organization', 'personnel'],
  ['investment_purpose'],
  [],
  ['technology', 'market'],
  ['insurance', 'risks'],
];
const narrativeFields = group => NARRATIVE_FIELDS.filter(([field]) => narrativeGroups[group].includes(field));
const workingCapitalScalars = computed(() => BUSINESS_SCALARS.filter(([field]) => field !== 'initial_investment'));
const statusLabel = project => project.status === 'deleted' ? 'Удалена · можно восстановить' : project.status === 'ready' ? 'Расчёт сохранён' : project.status === 'needs_data' ? 'Нужны параметры' : 'Папка проекта';
const money = value => value == null || String(value).trim() === '' || !Number.isFinite(Number(value)) ? '—' : formatCents(toCents(value), current.value?.inputs?.currency || form.value.currency || 'USD');
const ratio = value => value == null ? '—' : new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2}).format(Number(value) * 100) + '%';

watch(editing, value => emit('editing', value));
watch(() => props.refresh, loadProjects);
watch(() => props.company.id, () => {operation.reset(); validationAttempted.value=false; headerAttempted.value=false; listRequest++; detailRequest++; state.value = null; current.value = null; projects.value = []; selected.value = []; action.value = ''; error.value = ''; notice.value = ''; loadProjects();});
onMounted(loadProjects);
onUnmounted(() => {active = false; operation.reset(); listRequest++; detailRequest++; emit('editing', false);});

async function loadProjects() {
  const request = ++listRequest, companyId = props.company.id; listLoading.value = true;
  const ticket = busy.value ? null : operation.begin('Загружаем проекты…', 1);
  try {
    const response = await props.api(projectUrl(companyId) + '&include_deleted=true');
    if (!active || request !== listRequest || companyId !== props.company.id) return;
    projects.value = (response.items || []).filter(project => project.company_id === companyId);
    canUpload.value = !!response.can_upload;
    if (ticket) operation.complete(ticket, 'Проекты загружены');
  } catch (e) {if (active && request === listRequest && e.name !== 'AbortError') {error.value = e.message; if(ticket)operation.fail(ticket,e.message);}}
  finally {if (active && request === listRequest) listLoading.value = false;}
}
function setProject(project, replaceForm = false) {
  assertProject(project, props.company.id);
  current.value = project;
  if (state.value) state.value.project = project;
  if (replaceForm) {
    form.value = formFromInputs(project.inputs || project.extraction?.inputs || {}, {title: project.title, ...header.value});
    if (project.can_edit) header.value = {...header.value, title: form.value.title, start: form.value.start, months: form.value.months, currency: form.value.currency};
    loadedSnapshot.value = JSON.stringify(inputsFromForm(form.value)); confirmed.value = false;
  }
}
function readyReport(project) {
  if (project?.company_id !== props.company.id || project.status !== 'ready' || !Number.isSafeInteger(project.revision) || !Number.isSafeInteger(project.current_generation_id)) return false;
  const generation = project.generations?.find(item => item.id === project.current_generation_id);
  const archive = generation?.business_archive;
  return generation?.revision === project.revision &&
    Number.isSafeInteger(archive?.id) && archive.id > 0 && archive.company_id === props.company.id && archive.status === 'ready';
}
function deletedReport(project) {
  const archive = project?.generations?.find(item => item.id === project.current_generation_id)?.business_archive;
  return archive?.company_id === props.company.id && archive.status === 'deleted';
}
function unavailableReportMessage(project) {
  return deletedReport(project) ? 'Отчёт удалён из архива. Включите «Показать удалённые» в архиве отчётов и нажмите «Восстановить».' : 'Для текущей версии нет доступного комплекта PDF и Excel. Проверьте архив отчётов или подготовьте отчёт заново.';
}
function newProject(mode = 'manual') {
  if (busy.value) return;
  operation.reset(); validationAttempted.value=false; headerAttempted.value=false;
  state.value = createBusinessState(); current.value = null;
  projectMode.value = mode; flowStage.value = 'project';
  header.value = {title: '', start: '', months: 36, currency: ''};
  form.value = formFromInputs(); selected.value = []; ignored.value = []; selectionProblems.value = [];
  error.value = ''; notice.value = ''; totalSize.value = 0; confirmed.value = false;
}
async function openProject(project) {
  if (busy.value) return;
  const request = ++detailRequest, companyId=props.company.id; action.value = 'open'; error.value = ''; notice.value = '';
  validationAttempted.value=false; headerAttempted.value=false;
  const ticket=operation.begin('Открываем данные проекта…',1);
  try {
    const detail = await props.api(projectUrl(props.company.id, project.id));
    if (!active || request !== detailRequest) return;
    assertProject(detail, props.company.id, project.id);
    state.value = createBusinessState(detail); selected.value = []; ignored.value = []; selectionProblems.value = []; totalSize.value = 0;
    projectMode.value = detail.mode || 'files'; flowStage.value = readyReport(detail) ? 'report' : 'data';
    const values = detail.inputs || detail.extraction?.inputs || {};
    header.value = {title: detail.title, start: values.start || '', months: values.months ?? 36, currency: values.currency || ''};
    setProject(detail, true);
    if (detail.status === 'ready' && !readyReport(detail)) notice.value = unavailableReportMessage(detail);
    operation.complete(ticket,'Данные проекта загружены');
  } catch (e) {if (active && request===detailRequest && companyId===props.company.id && e.name !== 'AbortError') {error.value = e.message; operation.fail(ticket,e.message);}}
  finally {if (active && request === detailRequest) action.value = '';}
}
function closeProject() {if (busy.value) return; operation.reset(); state.value = null; current.value = null; selected.value = []; error.value = ''; notice.value = ''; loadProjects();}
async function changeProjectState(project, restore = false) {
  if (busy.value || project.company_id !== props.company.id || !(restore ? project.can_restore : project.can_delete)) return;
  const companyId = props.company.id;
  action.value = restore ? 'restore' : 'delete'; error.value = ''; notice.value = '';
  const ticket=operation.begin(restore?'Восстанавливаем проект…':'Удаляем проект из списка…',1);
  try {
    const path = `/api/business-projects/${project.id}${restore ? '/restore' : ''}?company_id=${companyId}&expected_revision=${project.revision}`;
    const detail = await props.api(path, {method: restore ? 'POST' : 'DELETE'});
    if (!active || companyId !== props.company.id) return;
    assertProject(detail, companyId, project.id);
    if (current.value?.id === project.id) {state.value = null; current.value = null; selected.value = [];}
    notice.value = restore ? 'Папка и её отчёты восстановлены.' : 'Папка и её отчёты удалены из списка и бота. Для возврата включите «Показать удалённые» и нажмите «Восстановить».';
    operation.complete(ticket,restore?'Проект восстановлен':'Проект удалён из списка');
    await loadProjects(); emit('generated');
  } catch (e) {if (active && companyId===props.company.id && e.name !== 'AbortError') {error.value = e.message; operation.fail(ticket,e.message); await loadProjects();}}
  finally {if (active && companyId===props.company.id) action.value = '';}
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
  const isCurrent = () => active && state.value === workingState && props.company.id === companyId;
  const preliminary = !current.value && projectMode.value==='manual' ? 2 : current.value&&!current.value.native_model&&changed.value ? 1 : 0;
  const total = preliminary + Number(!current.value&&projectMode.value!=='manual') + selected.value.filter(item=>!workingState.uploaded.has(item.key)).length + 1;
  const ticket=operation.begin('Готовим проект к загрузке…',total); let prepared=0;
  headerAttempted.value=true;
  action.value = 'upload'; error.value = ''; notice.value = '';
  try {
    if (!current.value && projectMode.value === 'manual') {
      operation.stage(ticket,'Создаём проект…');
      const created = await createProjectDraft(props.api, workingState, companyId, header.value.title, 'manual', isCurrent);
      operation.advance(ticket); prepared++;
      setProject(created); form.value = formFromInputs({}, header.value);
      operation.stage(ticket,'Сохраняем данные проекта…');
      const saved = await saveProjectDraft(props.api, created, companyId, inputsFromForm(form.value), isCurrent);
      operation.advance(ticket); prepared++;
      setProject(saved, true);
    }
    if (current.value && !current.value.native_model && changed.value) {
      operation.stage(ticket,'Сохраняем изменения перед загрузкой…');
      const saved = await saveProjectDraft(props.api, current.value, companyId, inputsFromForm(form.value), isCurrent);
      operation.advance(ticket); prepared++;
      setProject(saved, true);
    }
    const project = await uploadProjectFolder(props.api, workingState, {companyId, header: {...header.value}, sources: selected.value,
      mode: projectMode.value, isCurrent,
      onProject: project => setProject(project), onStep: value => {step.value = value;},
      onProgress: value => {if(isCurrent())operation.progress(ticket,{...value,completed:prepared+value.completed,total:prepared+value.total});},
    });
    if (!isCurrent()) return;
    setProject(project, true);
    operation.complete(ticket,'Файлы загружены, источники проверены');
    const ready = readyReport(project);
    flowStage.value = ready ? 'report' : 'data';
    notice.value = ready ? 'Бизнес-план готов. Скачайте PDF и Excel в разделе «Отчёт».' : project.status === 'ready' ? unavailableReportMessage(project) : 'Папка изучена. Проверьте замечания и заполните недостающие параметры ниже.';
    if (ready) emit('generated');
    await loadProjects();
  } catch (e) {if (isCurrent() && e.name !== 'AbortError') {error.value = uploadFailureMessage(e, workingState); operation.fail(ticket,error.value); await recoverConflict(e);}}
  finally {if (active && state.value === workingState && props.company.id === companyId) {action.value = ''; step.value = '';}}
}
async function generate() {
  if (busy.value || !canEdit.value) return;
  validationAttempted.value=true;
  if(requiredFields.value.length){flowStage.value='data';notice.value='Заполните обязательные поля из списка. Черновик можно сохранить без расчёта.';await goToField(requiredFields.value[0].field);return;}
  if(needsConfirmation.value&&!confirmed.value)return;
  const companyId = props.company.id, id = current.value.id;
  const ticket=operation.begin('Сохраняем данные проекта…',2);
  action.value = 'generate'; step.value = 'Сохраняем данные и готовим отчёт…'; error.value = ''; notice.value = '';
  try {
    const isCurrent = () => active && current.value?.id === id && props.company.id === companyId;
    const inputs = inputsFromForm(form.value);
    const saved = await saveProjectDraft(props.api, current.value, companyId, inputs, isCurrent);
    operation.advance(ticket,1,'Рассчитываем модель и формируем PDF и Excel…');
    const detail = await generateProject(props.api, saved, companyId, inputs, confirmed.value, isCurrent);
    if (!isCurrent()) return;
    setProject(detail, true);
    if (detail.validation?.length || !readyReport(detail)) {notice.value = deletedReport(detail) || !detail.validation?.length ? unavailableReportMessage(detail) : 'Проверьте замечания. Новый комплект отчётов появится после заполнения обязательных данных.'; operation.fail(ticket,notice.value);flowStage.value='data';}
    else {operation.complete(ticket,'Бизнес-план PDF и расчётный Excel готовы');notice.value = 'Бизнес-план и расчётный Excel готовы.'; flowStage.value = 'report'; emit('generated');}
    await loadProjects();
  } catch (e) {if (active && current.value?.id===id && companyId===props.company.id && e.name !== 'AbortError') {error.value = e.message; operation.fail(ticket,e.message); await recoverConflict(e);}}
  finally {if (active && current.value?.id === id && props.company.id === companyId) {action.value = ''; step.value = '';}}
}
async function saveDraft(nextStage = null) {
  if (busy.value || current.value && (!canEdit.value || current.value.native_model)) return false;
  const workingState = state.value, companyId = props.company.id;
  const isCurrent = () => active && state.value === workingState && props.company.id === companyId;
  const ticket=operation.begin(current.value?'Сохраняем черновик…':'Создаём проект…',current.value?1:2);
  headerAttempted.value=true;
  action.value = 'save'; error.value = ''; notice.value = '';
  try {
    let project = current.value;
    if (!project) {
      project = await createProjectDraft(props.api, workingState, companyId, header.value.title, projectMode.value, isCurrent);
      operation.advance(ticket,1,'Сохраняем черновик проекта…');
      setProject(project);
      form.value.title = header.value.title.trim();
      form.value.start = header.value.start; form.value.months = header.value.months; form.value.currency = header.value.currency;
    }
    const saved = await saveProjectDraft(props.api, project, companyId, inputsFromForm(form.value), isCurrent);
    const reviewed = confirmed.value; setProject(saved, true); confirmed.value = reviewed;
    operation.complete(ticket,'Черновик сохранён');
    notice.value = 'Черновик сохранён. Недостающие данные можно заполнить позже.';
    if (nextStage) flowStage.value = nextStage;
    await loadProjects(); return true;
  } catch (e) {if (isCurrent() && e.name !== 'AbortError') {error.value = e.message; operation.fail(ticket,e.message); await recoverConflict(e);} return false;}
  finally {if (isCurrent()) action.value = '';}
}
async function goStage(stage) {
  if (busy.value) return;
  if (!current.value && stage === 'project' || current.value?.native_model || current.value && !canEdit.value) {flowStage.value = stage; return;}
  if (!current.value || changed.value) {await saveDraft(stage); return;}
  flowStage.value = stage;
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
    return archive?.company_id === props.company.id && archive.status === 'ready' ? (key === 'business_archive' ? ['pdf', 'xlsx'] : ['pdf']).map(format => ({title: title + ' · ' + (format === 'pdf' ? 'PDF' : 'Excel'), secondary: key === 'teo_archive', url: archiveFileUrl(archive, format)})) : [];
  });
}
function reloadInputs() {if (!busy.value && current.value) {validationAttempted.value=false;setProject(current.value, true);}}
function isMissing(field) {return requiredFields.value.some(item => item.field === field || item.field.startsWith(field + '[') || item.field.startsWith(field + '.'));}
function fieldInvalid(field) {return validationAttempted.value&&isMissing(field);}
function missingId(field) {return 'bp-missing-'+encodeURIComponent(field);}
function nativeWorking(value){action.value=value?'native':'';if(value)operation.reset();}
async function goToField(field) {
  flowStage.value='data';await nextTick();
  if(typeof modelForm.value?.querySelector!=='function')return;
  const root = field.split(/[.\[]/)[0];
  const target = modelForm.value?.querySelector('[data-bp-field="' + field + '"]') || modelForm.value?.querySelector('[data-bp-field="' + root + '"]');
  if (!target) return;
  const nested = target.tagName === 'DETAILS' ? target : target.querySelector?.('details');
  if (nested) nested.open = true;
  let container = target.parentElement;
  while (container) {if (container.tagName === 'DETAILS') container.open = true; container = container.parentElement;}
  target.scrollIntoView({behavior:'smooth', block:'center'});
  const input = ['INPUT','SELECT','TEXTAREA'].includes(target.tagName) ? target : target.querySelector?.('input') || target.querySelector?.('select') || target.querySelector?.('textarea') || target.querySelector?.('button');
  input?.focus?.({preventScroll:true});
}
async function goToHeader(field){flowStage.value='project';await nextTick();const label=headerForm.value?.querySelector?.('[data-bp-header="'+field+'"]');const input=label?.querySelector?.('input')||label?.querySelector?.('select');input?.focus?.({preventScroll:true});input?.scrollIntoView?.({behavior:'smooth',block:'center'});}
function guidance(field) {return fieldGuidance(field);}
function scheduleField(entry, month = null) {
  const product = form.value.products.indexOf(entry.object), loan = form.value.loans.indexOf(entry.object);
  const field = product >= 0 ? `products[${product}].${entry.field}` : loan >= 0 ? `loans[${loan}].${entry.field}` : entry.field;
  return month == null ? field : `${field}[${month}]`;
}
</script>

<template>
  <section class="card business-projects" aria-labelledby="business-projects-title">
    <div class="card-h"><div><h3 id="business-projects-title">Бизнес-план</h3><p class="sub">{{company.name}} · данные проекта и автоматический расчёт</p></div><button v-if="canUpload&&!editing" :disabled="busy||listLoading" @click="newProject()"><AppIcon name="plus"/> Новый проект</button></div>
    <div class="card-b">
      <p class="sub bp-intro">Создайте проект, заполните данные и получите бизнес-план PDF с расчётным Excel. Черновик можно сохранить в любой момент. Исходные документы можно добавить при необходимости.</p>
      <div class="bp-actions"><a class="button secondary" :href="templateUrl(company.id)"><AppIcon name="download"/> Шаблон исходных данных · Excel</a><button class="ghost" :disabled="busy||listLoading" @click="loadProjects"><AppIcon name="refresh"/> Обновить папки</button></div>
      <p v-if="error" class="error" role="alert">{{error}}</p><p v-if="notice" class="notice" role="status">{{notice}}</p>
      <OperationProgress v-if="progress.total&&action!=='native'" v-bind="progress"/>
      <label v-if="canUpload" class="bp-actions"><input v-model="showDeleted" type="checkbox" :disabled="busy"> Показать удалённые папки</label>
      <div v-if="visibleProjects.length" class="bp-project-list"><div v-for="project in visibleProjects" :key="project.id" class="bp-project-row"><button class="secondary bp-project" :class="{selected:current?.id===project.id}" :disabled="busy||editing" @click="openProject(project)"><span><b>{{project.title}}</b><small>{{statusLabel(project)}} · версия исходных данных {{project.revision}}</small></span><AppIcon name="arrow"/></button><button v-if="project.can_delete" class="secondary tiny" :disabled="busy||editing" @click="changeProjectState(project)">Удалить папку и её отчёты</button><button v-if="project.can_restore" class="secondary tiny" :disabled="busy||editing" @click="changeProjectState(project,true)">Восстановить</button></div></div>
      <p v-else-if="!listLoading&&!editing" class="sub">Проектов пока нет. Нажмите «Новый проект» и укажите название.</p>

      <div v-if="editing" class="bp-workspace">
        <div class="section-head"><h3>{{current?current.title:'Новый проект'}}</h3><button class="secondary tiny" :disabled="busy" @click="closeProject">{{changed?'Закрыть без сохранения параметров':'Закрыть проект'}}</button></div>
        <div v-if="current?.can_delete||current?.can_restore" class="bp-actions"><button v-if="current.can_delete" class="secondary tiny" :disabled="busy" @click="changeProjectState(current)">Удалить папку и её отчёты</button><button v-if="current.can_restore" class="secondary tiny" :disabled="busy" @click="changeProjectState(current,true)">Восстановить папку и отчёты</button></div>
        <p v-if="current?.status==='deleted'" class="warning">Папка удалена. Для нового расчёта сначала восстановите её. Исходные файлы сохранены.</p>
        <p v-if="current&&!canEdit" class="sub">Просмотр проекта и готовых отчётов. Изменять исходные данные может автор папки с правом импорта.</p>
        <nav class="bp-actions bp-flow" aria-label="Этапы проекта"><button v-for="[stage,label] in [['project','1. Проект'],['data','2. Данные'],['report','3. Отчёт']]" :key="stage" type="button" :class="flowStage===stage?'':'secondary'" :aria-current="flowStage===stage?'step':undefined" :disabled="busy||!current&&stage==='report'" @click="goStage(stage)">{{label}}</button></nav>
        <div v-if="!current||canEdit" ref="headerForm" :hidden="flowStage!=='project'" class="bp-upload-area">
          <section v-if="headerMissing.length" class="bp-missing-summary" aria-labelledby="bp-header-missing-title"><h4 id="bp-header-missing-title">Не заполнены поля проекта</h4><ul><li v-for="item in headerMissing" :key="item.field"><button type="button" class="ghost tiny" :disabled="busy" @click="goToHeader(item.field)">{{item.label}}</button></li></ul><p class="sub">Для сохранения черновика достаточно названия; остальные поля можно заполнить позже.</p></section>
          <div class="bp-fields">
            <label class="bp-wide" data-bp-header="title">Название проекта <span class="bp-required-tag">Обязательно</span><input v-model="header.title" maxlength="160" :aria-invalid="headerAttempted&&headerMissing.some(item=>item.field==='title')" :disabled="busy||!!state.createFields||!!current" placeholder="Производство лекарственных препаратов"><small>Заполнится из имени папки, если своё название ещё не указано.</small></label>
            <template v-if="!current?.native_model">
              <label data-bp-header="start">Начало прогноза <span class="bp-required-tag">Обязательно</span><input type="month" :value="header.start.slice(0,7)" min="2000-01" max="2100-12" :disabled="busy" @input="setStart(header,$event)"><small>Выберите первый месяц расчёта. Дата загрузки и дата баланса не заменяют начало прогноза.</small></label>
              <label data-bp-header="months">Прогноз, месяцев<input v-model="header.months" type="number" min="1" max="60" step="1" :disabled="busy"><small>От 1 до 60; по умолчанию 36 месяцев</small></label>
              <label data-bp-header="currency">Валюта модели<select v-model="header.currency" :disabled="busy"><option value="">{{projectMode==='manual'?'Выберите валюту':'Определить из источников'}}</option><option>USD</option><option>UZS</option><option>EUR</option></select></label>
            </template>
          </div>
          <div v-if="!current&&projectMode==='manual'" class="bp-actions"><button :disabled="busy||!header.title.trim()" @click="saveDraft('data')"><AppIcon name="check"/> Сохранить и перейти к данным</button><span class="sub">Файлы не обязательны</span></div>
          <details class="bp-details" :open="projectMode==='files'&&!current"><summary>Файлы и импорт оригинала · необязательно</summary>
          <p class="sub">Можно приложить документы к ручному проекту или использовать исходные Word и Excel UZGERMED.</p>
          <button v-if="!current&&projectMode==='manual'&&!state.createFields" type="button" class="secondary tiny" @click="projectMode='files'">Импорт оригинальных Word и Excel</button>
          <p class="sub bp-note">{{current?.native_model?'Оригинальная модель: 36 месяцев, USD. Период для поиска отчётов выбирается ниже, перед формированием.':'Начало прогноза задаётся отдельно от даты загрузки.'}} При добавлении новых источников анализ заново проверит финансовые параметры; готовые версии останутся в истории.</p>
          <div class="bp-actions"><label class="button"><AppIcon name="import"/> Выбрать папку<input type="file" webkitdirectory multiple :accept="SOURCE_ACCEPT" hidden :disabled="busy" @change="chooseSources"></label><label class="button secondary"><AppIcon name="file"/> Выбрать отдельные файлы<input type="file" multiple :accept="SOURCE_ACCEPT" hidden :disabled="busy" @change="chooseSources"></label></div>
          <p class="sub bp-note">PDF, XLSX, XLTX, DOCX, CSV, TXT, JSON, ZIP, PNG и JPEG. До 100 файлов, 20 МБ на файл и 100 МБ на папку. Поддерживаемый оригинальный Excel пересчитывается по своим формулам; изображения и договоры сохраняются как источники.</p>
          <p v-for="problem in selectionProblems" :key="problem" class="error" role="alert">{{problem}}</p>
          <details v-if="ignored.length" class="bp-details"><summary>Пропущены служебные файлы: {{ignored.length}}</summary><ul><li v-for="item in ignored" :key="item.name">{{item.name}} — {{item.reason}}</li></ul></details>
          <details v-if="selected.length" class="bp-details" :open="selected.some(item=>!state.uploaded.has(item.key))"><summary>Выбрано {{selected.length}} файлов · {{sourceSize(totalSize)}}</summary><ul class="bp-files"><li v-for="item in selected" :key="item.key"><span>{{item.relativePath}}</span><small>{{sourceSize(item.size)}} {{state.uploaded.has(item.key)?'· загружен':''}}</small></li></ul></details>
          <p v-if="state.createUnknown" class="warning">Сервер мог создать папку. Повторите загрузку: она продолжится в том же проекте.</p>
          <p v-if="selected.length&&uploadRequirement" class="warning" role="status">Перед загрузкой: {{uploadRequirement}}</p>
          <button :disabled="busy||!selected.length||selectionProblems.length>0||!!uploadRequirement" @click="upload"><AppIcon name="import"/> {{current?'Добавить файлы и выполнить анализ':'Импортировать файлы проекта'}}</button>
          </details>
        </div>

        <template v-if="current">
          <details v-if="originalReports.length" class="bp-details" aria-labelledby="bp-original-title"><summary>Исходные документы · {{originalReports.length}}</summary>
            <h3 id="bp-original-title">Готовый бизнес-план из вашей папки</h3>
            <p class="sub bp-note">Загруженные документы сохраняют свои разделы, таблицы, оформление, формулы и исходные суммы. Скачивание выдаёт исходный файл. Изменения параметров ниже попадут в отдельный новый расчёт.</p>
            <div class="bp-original-list"><article v-for="file in originalReports" :key="file.id" class="bp-required-item"><b>{{file.filename}}</b><a class="button secondary" :href="file.downloadUrl"><AppIcon name="download"/> Скачать исходный {{file.format}}</a></article></div>
            <p v-if="!originalReports.some(file=>file.format==='PDF')" class="sub bp-note">{{current.native_model?'Для нового PDF выполните пересчёт оригинала и нажмите «Сформировать бизнес-план и ТЭО по оригиналу». PDF будет подготовлен по Word из этой папки.':'Для PDF с исходным оформлением добавьте PDF, экспортированный из исходного Word, через «Выбрать отдельные файлы».'}}</p>
          </details>
          <details class="bp-details"><summary>Исходные файлы проекта · {{current.files?.length||0}}</summary><ul class="bp-files"><li v-for="file in current.files||[]" :key="file.relative_path"><span>{{file.relative_path||file.filename}}</span><small>{{sourceSize(file.size||0)}} <a v-if="canEdit&&file.download_url" :href="file.download_url">Скачать исходник</a></small></li></ul></details>
          <p v-if="flowStage==='report'&&!readyReport(current)" class="sub" role="status">{{unavailableReportMessage(current)}}<template v-if="!deletedReport(current)"> {{canEdit ? current.native_model ? 'Нажмите «Подготовить бизнес-план и Excel», чтобы создать файлы.' : 'Нажмите «Рассчитать бизнес-план», чтобы создать файлы.' : 'Попросите автора проекта подготовить отчёт.'}}</template></p>
          <NativeWorkbook v-if="current.native_model" :hidden="flowStage==='project'" :stage="flowStage" :api="api" :company="company" :project="current" @report="goStage('report')" @data="goStage('data')" @updated="setProject($event,true)" @generated="flowStage='report';emit('generated');loadProjects()" @working="nativeWorking"/>
          <template v-if="!current.native_model">
          <section v-if="canEdit&&requiredFields.length" class="bp-missing-summary" :class="{'bp-validation-attempted':validationAttempted}" aria-labelledby="bp-missing-title"><h4 id="bp-missing-title" aria-live="polite">Не заполнены обязательные поля · {{requiredFields.length}}</h4><ul><li v-for="item in requiredFields" :key="item.field"><button type="button" class="ghost tiny" :disabled="busy" @click="goToField(item.field)">{{guidance(item.field).label||fieldLabel(item.field)}}</button></li></ul><p class="sub">Нажмите название, чтобы открыть нужную группу и перейти к полю. Явный ноль считается заполненным значением.</p></section>
          <div :hidden="flowStage!=='data'">
          <section v-if="canEdit&&action!=='upload'" class="bp-required-guide" aria-labelledby="bp-required-title">
            <h3 id="bp-required-title">Обязательные поля · {{remainingRequirements.length ? 'осталось заполнить ' + remainingRequirements.length + ' полей' : 'все поля заполнены'}}</h3>
            <p class="sub bp-note">Заполняйте суммы в выбранной валюте. Пустое поле не означает ноль; ноль указывайте явно. Черновик сохраняется и с неполными данными.</p>
            <details v-if="remainingRequirements.length" class="bp-details"><summary>Как заполнить обязательные поля · {{remainingRequirements.length}}</summary>
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
            </details>
            <p v-else class="notice" role="status">{{current.status==='ready'&&!changed?'Обязательные параметры приняты в расчёт. Готовые PDF и Excel находятся ниже.':'Обязательные поля заполнены. Проверьте источники и выполните расчёт; сервер проверит числа, графики и согласованность модели.'}}</p>
          </section>
          <div v-if="sourceProblems.length||savedValidation.length" class="bp-review">
            <h3>{{savedValidation.length?'Исправьте значения перед расчётом':'Проверьте условия расчёта'}}</h3>
            <p v-if="form.products.length" class="sub bp-note">Распознано {{form.products.length}} позиций продукции. Недостающие параметры находятся в начале формы ниже.</p>
            <p v-if="current.status==='ready'&&current.extraction?.parameters_confirmed" class="sub bp-note">Финансовые параметры проверены и приняты в расчёт. Ниже сохранены замечания к первоначальным источникам.</p>
            <ul v-if="savedValidation.length" class="bp-missing"><li v-for="(issue,index) in savedValidation" :key="'v'+index"><b>{{fieldLabel(issue.field)}}</b>: {{issueMessage(issue)}}</li></ul>
            <p v-for="(issue,index) in blockingSources" :key="'b'+index" class="error" role="alert"><b>{{fieldLabel(issue.field)}}</b>: {{issueMessage(issue)}}</p>
            <details v-if="sourceProblems.length" class="bp-details"><summary>Условия и расхождения, которые нужно проверить · {{sourceProblems.length}}</summary><ul><li v-for="(issue,index) in sourceProblems" :key="'s'+index"><b>{{fieldLabel(issue.field)}}</b>: {{issueMessage(issue)}}<small v-for="(source,sourceIndex) in issue.sources" :key="sourceIndex">{{sourceLocation(source)}}</small></li></ul></details>
          </div>
          <details v-if="referenceNotes.length" class="bp-details"><summary>Справочные заметки по документам · {{referenceNotes.length}}</summary><ul><li v-for="(issue,index) in referenceNotes" :key="index"><b>{{fieldLabel(issue.field)}}</b>: {{issueMessage(issue)}}<small v-for="(source,sourceIndex) in issue.sources" :key="sourceIndex">{{sourceLocation(source)}}</small></li></ul></details>
          <details v-if="evidence.length" class="bp-details"><summary>Параметры, распознанные в источниках · {{evidence.length}}</summary><ul><li v-for="(item,index) in evidence" :key="index"><b>{{fieldLabel(item.field)}}</b><small>{{sourceLocation(item.source)}}<template v-if="item.note"> · {{item.note}}</template></small><span v-if="item.status==='needs_confirmation'" class="pill warn">Проверьте значение</span></li></ul></details>
          <details v-if="narratives.length" class="bp-details"><summary>Описание в исходных документах</summary><details v-for="(item,index) in narratives" :key="index" class="bp-details"><summary>{{item.filename||'Документ'}}</summary><p class="bp-reference">{{item.text}}</p></details></details>

          <template v-if="canEdit">
          <section class="bp-model-form">
          <div class="section-head bp-form-head"><div><h3>Данные проекта</h3><p class="sub">Шесть групп. Сведения из формы попадут в полный бизнес-план.</p></div><button v-if="changed" class="ghost tiny" :disabled="busy" @click="reloadInputs">Вернуть сохранённые параметры</button></div>
          <form ref="modelForm" novalidate @submit.prevent="saveDraft()">
            <fieldset :disabled="busy||!canEdit" class="bp-fieldset">
              <details class="bp-details" data-bp-group="passport" open><summary>1. Проект и инициатор</summary><p class="sub">Название, период, валюта и описание проекта.</p>
              <div class="bp-fields">
                <label class="bp-wide" data-bp-field="title" :class="{'bp-field-error':fieldInvalid('title')}">Название отчётов <span class="bp-required-tag">Обязательно</span><input v-model="form.title" maxlength="160" required :aria-invalid="fieldInvalid('title')" :aria-describedby="fieldInvalid('title') ? missingId('title') : undefined"><small v-if="fieldInvalid('title')" :id="missingId('title')" class="bp-field-help">Заполните поле «{{guidance('title').label || fieldLabel('title')}}».</small></label>
                <label data-bp-field="start" :class="{'bp-field-error':fieldInvalid('start')}">Начало прогноза <span class="bp-required-tag">Обязательно</span><input type="month" :value="form.start.slice(0,7)" min="2000-01" max="2100-12" required :aria-invalid="fieldInvalid('start')" @input="setStart(form,$event)" :aria-describedby="fieldInvalid('start') ? missingId('start') : undefined"><small>Первый месяц финансового прогноза.</small><small v-if="fieldInvalid('start')" :id="missingId('start')" class="bp-field-help">Заполните поле «{{guidance('start').label || fieldLabel('start')}}».</small></label>
                <label data-bp-field="months" :class="{'bp-field-error':fieldInvalid('months')}">Прогноз, месяцев <span class="bp-required-tag">Обязательно</span><input v-model="form.months" type="number" min="1" max="60" step="1" required :aria-describedby="fieldInvalid('months') ? missingId('months') : undefined" :aria-invalid="fieldInvalid('months')"><small>Графики должны покрывать выбранное число месяцев.</small><small v-if="fieldInvalid('months')" :id="missingId('months')" class="bp-field-help">Заполните поле «{{guidance('months').label || fieldLabel('months')}}».</small></label>
                <label data-bp-field="currency" :class="{'bp-field-error':fieldInvalid('currency')}">Валюта <span class="bp-required-tag">Обязательно</span><select v-model="form.currency" required :aria-invalid="fieldInvalid('currency')" :aria-describedby="fieldInvalid('currency') ? missingId('currency') : undefined"><option value="">Выберите валюту</option><option>USD</option><option>UZS</option><option>EUR</option></select><small>Все суммы — в единицах выбранной валюты.</small><small v-if="fieldInvalid('currency')" :id="missingId('currency')" class="bp-field-help">Заполните поле «{{guidance('currency').label || fieldLabel('currency')}}».</small></label>
                <label data-bp-field="tax_rate" :class="{'bp-field-error':fieldInvalid('tax_rate')}">Налог на прибыль, % <span class="bp-required-tag">Обязательно</span><input v-model="form.tax_rate" type="text" inputmode="decimal" required :aria-invalid="fieldInvalid('tax_rate')" :aria-describedby="fieldInvalid('tax_rate') ? missingId('tax_rate') : undefined"><small>Подтверждённая ставка проекта, в процентах.</small><small v-if="fieldInvalid('tax_rate')" :id="missingId('tax_rate')" class="bp-field-help">Заполните поле «{{guidance('tax_rate').label || fieldLabel('tax_rate')}}».</small></label>
                <label data-bp-field="discount_rate" :class="{'bp-field-error':fieldInvalid('discount_rate')}">Годовая ставка дисконтирования, % <span class="bp-required-tag">Обязательно</span><input v-model="form.discount_rate" type="text" inputmode="decimal" required :aria-invalid="fieldInvalid('discount_rate')" :aria-describedby="fieldInvalid('discount_rate') ? missingId('discount_rate') : undefined"><small>Утверждённая годовая ставка для оценки денежных потоков.</small><small v-if="fieldInvalid('discount_rate')" :id="missingId('discount_rate')" class="bp-field-help">Заполните поле «{{guidance('discount_rate').label || fieldLabel('discount_rate')}}».</small></label>
              </div>
              <div class="bp-narratives"><label v-for="[field,label] in narrativeFields(0)" :key="field" :data-bp-field="field">{{label}}<textarea v-model="form[field]" maxlength="16000"></textarea></label></div>
              </details>

              <details class="bp-details" data-bp-group="costs"><summary>2. Расходы и ресурсы</summary><p class="sub">Ежемесячные расходы, снабжение и организация работы.</p>
              <h3 class="bp-form-head">Постоянные расходы</h3><p class="sub">Введите ежемесячную сумму. Для нерегулярных расходов используйте график по месяцам.</p>

              <div v-for="entry in financialSchedules.filter(item=>item.field==='fixed_costs')" :key="entry.key" class="bp-schedule" :data-bp-field="entry.field">
                <template v-if="!Array.isArray(entry.object[entry.field])"><label :class="{'bp-field-error':fieldInvalid(entry.field)}">{{entry.label}} · каждый месяц <span class="bp-required-tag">Обязательно</span><input v-model="entry.object[entry.field]" type="text" inputmode="decimal" required :aria-invalid="fieldInvalid(entry.field)" :aria-describedby="fieldInvalid(entry.field) ? missingId(entry.field) : undefined"><small>Одно значение повторится каждый месяц.</small><small v-if="fieldInvalid(entry.field)" :id="missingId(entry.field)" class="bp-field-help">Заполните поле «{{guidance(entry.field).label || fieldLabel(entry.field)}}».</small></label><button type="button" class="secondary tiny" @click="makeMonthly(entry)">По месяцам</button></template>
                <details v-else class="bp-details"><summary>{{entry.label}} · график: {{entry.object[entry.field].length}} месяцев</summary><p v-if="entry.object[entry.field].length!==Number(form.months)" class="warning">Длина графика отличается от прогноза ({{form.months}} мес.). Уже введённые значения сохраняются до вашего выбора.<button type="button" class="secondary tiny" @click="makeMonthly(entry)">{{entry.object[entry.field].length>Number(form.months)?'Убрать последние '+(entry.object[entry.field].length-Number(form.months))+' значений':'Добавить пустые месяцы'}}</button></p><div class="bp-month-grid"><label v-for="(_,index) in entry.object[entry.field]" :key="index" :class="{'bp-field-error':fieldInvalid(scheduleField(entry,index))}">{{labels[index]||'Месяц '+(index+1)}}<input :data-bp-field="scheduleField(entry,index)" v-model="entry.object[entry.field][index]" type="text" inputmode="decimal" required :aria-invalid="fieldInvalid(scheduleField(entry,index))" :aria-describedby="fieldInvalid(scheduleField(entry,index)) ? missingId(scheduleField(entry,index)) : undefined"><small v-if="fieldInvalid(scheduleField(entry,index))" :id="missingId(scheduleField(entry,index))" class="bp-field-help">Заполните поле «{{guidance(scheduleField(entry,index)).label || fieldLabel(scheduleField(entry,index))}}».</small></label></div><button type="button" class="ghost tiny" @click="entry.object[entry.field]=''">Заменить график одним ежемесячным значением</button></details>
              </div>
              <div class="bp-narratives"><label v-for="[field,label] in narrativeFields(1)" :key="field" :data-bp-field="field">{{label}}<textarea v-model="form[field]" maxlength="16000"></textarea></label></div>
              </details>

              <details class="bp-details" data-bp-group="investment"><summary>3. Инвестиции и основные средства</summary><p class="sub">Вложения до прогноза и покупка активов по месяцам учитываются отдельно.</p>
              <label data-bp-field="initial_investment" :class="{'bp-field-error':fieldInvalid('initial_investment')}">Инвестиции до начала прогноза<input v-model="form.initial_investment" type="text" inputmode="decimal" required :aria-invalid="fieldInvalid('initial_investment')" :aria-describedby="fieldInvalid('initial_investment') ? missingId('initial_investment') : undefined"><small>Не включайте повторно активы, покупаемые в месяцах прогноза.</small><small v-if="fieldInvalid('initial_investment')" :id="missingId('initial_investment')" class="bp-field-help">Заполните поле «{{guidance('initial_investment').label || fieldLabel('initial_investment')}}».</small></label>
              <div class="section-head bp-form-head" data-bp-field="assets"><h3>Основные средства · {{form.assets.length}} <span class="bp-required-tag">Обязательно указать наличие</span></h3><button type="button" class="secondary tiny" :disabled="form.assets.length>=200" @click="addRow('assets')"><AppIcon name="plus"/> Добавить актив</button></div>
              <p class="sub bp-note">Укажите существующие активы и плановые покупки. Если активов нет, отметьте это явно.</p>
              <label v-if="!form.assets.length" class="bp-check" :class="{'bp-field-error':fieldInvalid('assets')}"><input v-model="form.no_assets" type="checkbox" :aria-invalid="fieldInvalid('assets')" :aria-describedby="fieldInvalid('assets')?missingId('assets'):undefined"> Основных средств в этой модели нет</label><small v-if="fieldInvalid('assets')" :id="missingId('assets')" class="bp-field-help">Добавьте актив или явно отметьте отсутствие основных средств.</small>
              <div v-for="(asset,index) in form.assets" :key="index" class="bp-row-card">
                <div class="section-head"><b>Актив №{{index+1}}</b><button type="button" class="ghost tiny" @click="form.assets.splice(index,1);form.no_assets=false">Удалить строку</button></div>
                <div class="bp-fields">
                  <label :data-bp-field="`assets[${index}].name`" :class="{'bp-field-error':fieldInvalid(`assets[${index}].name`)}">Название <span class="bp-required-tag">Обязательно</span><input v-model="asset.name" maxlength="160" required :aria-describedby="fieldInvalid(`assets[${index}].name`) ? missingId(`assets[${index}].name`) : undefined" :aria-invalid="fieldInvalid(`assets[${index}].name`)"><small v-if="fieldInvalid(`assets[${index}].name`)" :id="missingId(`assets[${index}].name`)" class="bp-field-help">Заполните поле «{{guidance(`assets[${index}].name`).label || fieldLabel(`assets[${index}].name`)}}».</small></label>
                  <label :data-bp-field="`assets[${index}].value`" :class="{'bp-field-error':fieldInvalid(`assets[${index}].value`)}">Стоимость <span class="bp-required-tag">Обязательно</span><input v-model="asset.value" type="text" inputmode="decimal" required :aria-describedby="fieldInvalid(`assets[${index}].value`) ? missingId(`assets[${index}].value`) : undefined" :aria-invalid="fieldInvalid(`assets[${index}].value`)"><small>{{guidance(`assets[${index}].value`).how}}</small><small v-if="fieldInvalid(`assets[${index}].value`)" :id="missingId(`assets[${index}].value`)" class="bp-field-help">Заполните поле «{{guidance(`assets[${index}].value`).label || fieldLabel(`assets[${index}].value`)}}».</small></label>
                  <label :data-bp-field="`assets[${index}].life_months`" :class="{'bp-field-error':fieldInvalid(`assets[${index}].life_months`)}">Срок амортизации, месяцев <span class="bp-required-tag">Обязательно</span><input v-model="asset.life_months" type="number" min="1" max="1200" step="1" required :aria-describedby="fieldInvalid(`assets[${index}].life_months`) ? missingId(`assets[${index}].life_months`) : undefined" :aria-invalid="fieldInvalid(`assets[${index}].life_months`)"><small>{{guidance(`assets[${index}].life_months`).how}}</small><small v-if="fieldInvalid(`assets[${index}].life_months`)" :id="missingId(`assets[${index}].life_months`)" class="bp-field-help">Заполните поле «{{guidance(`assets[${index}].life_months`).label || fieldLabel(`assets[${index}].life_months`)}}».</small></label>
                  <label :data-bp-field="`assets[${index}].commissioning_month`" :class="{'bp-field-error':fieldInvalid(`assets[${index}].commissioning_month`)}">Месяц ввода <span class="bp-required-tag">Обязательно</span><input v-model="asset.commissioning_month" type="number" min="0" :max="form.months" step="1" required :aria-describedby="fieldInvalid(`assets[${index}].commissioning_month`) ? missingId(`assets[${index}].commissioning_month`) : undefined" :aria-invalid="fieldInvalid(`assets[${index}].commissioning_month`)"><small>0 — актив на начало; 1 — первый месяц прогноза</small><small v-if="fieldInvalid(`assets[${index}].commissioning_month`)" :id="missingId(`assets[${index}].commissioning_month`)" class="bp-field-help">Заполните поле «{{guidance(`assets[${index}].commissioning_month`).label || fieldLabel(`assets[${index}].commissioning_month`)}}».</small></label>
                </div>
              </div>
              <div class="bp-narratives"><label v-for="[field,label] in narrativeFields(2)" :key="field" :data-bp-field="field">{{label}}<textarea v-model="form[field]" maxlength="16000"></textarea></label></div>
              </details>

              <details class="bp-details" data-bp-group="financing"><summary>4. Финансирование</summary><p class="sub">Собственные взносы, кредиты и графики платежей.</p>
              <div v-for="entry in financialSchedules.filter(item=>item.field==='equity')" :key="entry.key" class="bp-schedule" data-bp-field="equity">
                <template v-if="!Array.isArray(form.equity)"><label :class="{'bp-field-error':fieldInvalid('equity')}">Взносы в капитал · каждый месяц<input v-model="form.equity" type="text" inputmode="decimal" required :aria-invalid="fieldInvalid('equity')" :aria-describedby="fieldInvalid('equity') ? missingId('equity') : undefined"><small v-if="fieldInvalid('equity')" :id="missingId('equity')" class="bp-field-help">Заполните поле «{{guidance('equity').label || fieldLabel('equity')}}».</small></label><button type="button" class="secondary tiny" @click="makeMonthly(entry)">По месяцам</button></template>
                <details v-else class="bp-details"><summary>Взносы в капитал · график: {{form.equity.length}} месяцев</summary><p v-if="form.equity.length!==Number(form.months)" class="warning">Длина графика отличается от прогноза.<button type="button" class="secondary tiny" @click="makeMonthly(entry)">Обновить длину графика</button></p><div class="bp-month-grid"><label v-for="(_,index) in form.equity" :key="index" :class="{'bp-field-error':fieldInvalid(scheduleField(entry,index))}">{{labels[index]||'Месяц '+(index+1)}}<input :data-bp-field="scheduleField(entry,index)" v-model="form.equity[index]" type="text" inputmode="decimal" required :aria-describedby="fieldInvalid(scheduleField(entry,index)) ? missingId(scheduleField(entry,index)) : undefined" :aria-invalid="fieldInvalid(scheduleField(entry,index))"><small v-if="fieldInvalid(scheduleField(entry,index))" :id="missingId(scheduleField(entry,index))" class="bp-field-help">Заполните поле «{{guidance(scheduleField(entry,index)).label || fieldLabel(scheduleField(entry,index))}}».</small></label></div><button type="button" class="ghost tiny" @click="form.equity=''">Заменить график одним ежемесячным значением</button></details>
              </div>
              <div class="section-head bp-form-head" data-bp-field="loans"><h3>Кредиты · {{form.loans.length}} <span class="bp-required-tag">Обязательно указать наличие</span></h3><button type="button" class="secondary tiny" :disabled="form.loans.length>=200" @click="addRow('loans')"><AppIcon name="plus"/> Добавить кредит</button></div>
              <p class="sub bp-note">Укажите остаток, получение кредита и платежи. Если кредитов нет, отметьте это явно.</p>
              <label v-if="!form.loans.length" class="bp-check" :class="{'bp-field-error':fieldInvalid('loans')}"><input v-model="form.no_loans" type="checkbox" :aria-invalid="fieldInvalid('loans')" :aria-describedby="fieldInvalid('loans')?missingId('loans'):undefined"> Кредитов в этой модели нет</label><small v-if="fieldInvalid('loans')" :id="missingId('loans')" class="bp-field-help">Добавьте кредит или явно отметьте отсутствие кредитов.</small>
              <div v-for="(loan,index) in form.loans" :key="index" class="bp-row-card"><div class="section-head"><b>Кредит №{{index+1}}</b><button type="button" class="ghost tiny" @click="form.loans.splice(index,1);form.no_loans=false">Удалить строку</button></div><div class="bp-fields"><label :data-bp-field="`loans[${index}].name`" :class="{'bp-field-error':fieldInvalid(`loans[${index}].name`)}">Название <span class="bp-required-tag">Обязательно</span><input v-model="loan.name" maxlength="160" required :aria-describedby="fieldInvalid(`loans[${index}].name`) ? missingId(`loans[${index}].name`) : undefined" :aria-invalid="fieldInvalid(`loans[${index}].name`)"><small v-if="fieldInvalid(`loans[${index}].name`)" :id="missingId(`loans[${index}].name`)" class="bp-field-help">Заполните поле «{{guidance(`loans[${index}].name`).label || fieldLabel(`loans[${index}].name`)}}».</small></label><label :data-bp-field="`loans[${index}].opening_balance`" :class="{'bp-field-error':fieldInvalid(`loans[${index}].opening_balance`)}">Долг на начало прогноза <span class="bp-required-tag">Обязательно</span><input v-model="loan.opening_balance" type="text" inputmode="decimal" required :aria-describedby="fieldInvalid(`loans[${index}].opening_balance`) ? missingId(`loans[${index}].opening_balance`) : undefined" :aria-invalid="fieldInvalid(`loans[${index}].opening_balance`)"><small>{{guidance(`loans[${index}].opening_balance`).how}}</small><small v-if="fieldInvalid(`loans[${index}].opening_balance`)" :id="missingId(`loans[${index}].opening_balance`)" class="bp-field-help">Заполните поле «{{guidance(`loans[${index}].opening_balance`).label || fieldLabel(`loans[${index}].opening_balance`)}}».</small></label></div><small>Получение, погашение и проценты задайте в графиках ниже. Процентную ставку вместо суммы процентов здесь не вводите.</small></div>

              <h3 v-if="loanSchedules.length" class="bp-form-head">Помесячные графики кредитов</h3><p v-if="loanSchedules.length" class="sub">Одно значение повторяется каждый месяц. Для отдельных выдач и погашений используйте график по месяцам.</p>
              <div v-for="entry in loanSchedules" :key="entry.key" class="bp-schedule" :data-bp-field="scheduleField(entry)">
                <template v-if="!Array.isArray(entry.object[entry.field])"><label :class="{'bp-field-error':fieldInvalid(scheduleField(entry))}">{{entry.label}} · каждый месяц<input :data-bp-field="scheduleField(entry)" v-model="entry.object[entry.field]" type="text" inputmode="decimal" required :aria-invalid="fieldInvalid(scheduleField(entry))" :aria-describedby="fieldInvalid(scheduleField(entry)) ? missingId(scheduleField(entry)) : undefined"><small v-if="fieldInvalid(scheduleField(entry))" :id="missingId(scheduleField(entry))" class="bp-field-help">Заполните поле «{{guidance(scheduleField(entry)).label || fieldLabel(scheduleField(entry))}}».</small></label><button type="button" class="secondary tiny" @click="makeMonthly(entry)">По месяцам</button></template>
                <details v-else class="bp-details"><summary>{{entry.label}} · график: {{entry.object[entry.field].length}} месяцев</summary><p v-if="entry.object[entry.field].length!==Number(form.months)" class="warning">Длина графика отличается от прогноза ({{form.months}} мес.). Уже введённые значения сохраняются до вашего выбора.<button type="button" class="secondary tiny" @click="makeMonthly(entry)">{{entry.object[entry.field].length>Number(form.months)?'Убрать последние '+(entry.object[entry.field].length-Number(form.months))+' значений':'Добавить пустые месяцы'}}</button></p><div class="bp-month-grid"><label v-for="(_,index) in entry.object[entry.field]" :key="index" :class="{'bp-field-error':fieldInvalid(scheduleField(entry,index))}">{{labels[index]||'Месяц '+(index+1)}}<input :data-bp-field="scheduleField(entry,index)" v-model="entry.object[entry.field][index]" type="text" inputmode="decimal" required :aria-invalid="fieldInvalid(scheduleField(entry,index))" :aria-describedby="fieldInvalid(scheduleField(entry,index)) ? missingId(scheduleField(entry,index)) : undefined"><small v-if="fieldInvalid(scheduleField(entry,index))" :id="missingId(scheduleField(entry,index))" class="bp-field-help">Заполните поле «{{guidance(scheduleField(entry,index)).label || fieldLabel(scheduleField(entry,index))}}».</small></label></div><button type="button" class="ghost tiny" @click="entry.object[entry.field]=''">Заменить график одним ежемесячным значением</button></details>
              </div>

              </details>
              <details class="bp-details bp-products" data-bp-group="production" data-bp-field="products" :open="reviewProducts"><summary>5. Продукция, производство и рынок · {{form.products.length}}</summary><p class="sub">Цена и себестоимость без НДС. Объём — в единицах продукции.</p>
              <div class="section-head bp-form-head"><h3>Продукция · {{form.products.length}}</h3><button type="button" class="secondary tiny" :disabled="form.products.length>=200" @click="addRow('products')"><AppIcon name="plus"/> Добавить продукцию</button></div>
              <div v-for="(product,index) in form.products" :key="index" class="bp-row-card"><div class="section-head"><b>Продукция №{{index+1}}</b><button type="button" class="ghost tiny" @click="form.products.splice(index,1)">Удалить строку</button></div><div class="bp-fields"><label :data-bp-field="`products[${index}].name`" :class="{'bp-field-error':fieldInvalid(`products[${index}].name`)}">Название <span class="bp-required-tag">Обязательно</span><input v-model="product.name" maxlength="160" required :aria-describedby="fieldInvalid(`products[${index}].name`) ? missingId(`products[${index}].name`) : undefined" :aria-invalid="fieldInvalid(`products[${index}].name`)"><small v-if="fieldInvalid(`products[${index}].name`)" :id="missingId(`products[${index}].name`)" class="bp-field-help">Заполните поле «{{guidance(`products[${index}].name`).label || fieldLabel(`products[${index}].name`)}}».</small></label><label :data-bp-field="`products[${index}].unit`" :class="{'bp-field-error':fieldInvalid(`products[${index}].unit`)}">Единица измерения <span class="bp-required-tag">Обязательно</span><input v-model="product.unit" maxlength="40" placeholder="упаковка" required :aria-describedby="fieldInvalid(`products[${index}].unit`) ? missingId(`products[${index}].unit`) : undefined" :aria-invalid="fieldInvalid(`products[${index}].unit`)"><small v-if="fieldInvalid(`products[${index}].unit`)" :id="missingId(`products[${index}].unit`)" class="bp-field-help">Заполните поле «{{guidance(`products[${index}].unit`).label || fieldLabel(`products[${index}].unit`)}}».</small></label><label :data-bp-field="`products[${index}].price`" :class="{'bp-field-error':fieldInvalid(`products[${index}].price`)}">Цена реализации <span class="bp-required-tag">Обязательно</span><input v-model="product.price" type="text" inputmode="decimal" required :aria-describedby="fieldInvalid(`products[${index}].price`) ? missingId(`products[${index}].price`) : undefined" :aria-invalid="fieldInvalid(`products[${index}].price`)"><small>{{guidance(`products[${index}].price`).how}}</small><small v-if="fieldInvalid(`products[${index}].price`)" :id="missingId(`products[${index}].price`)" class="bp-field-help">Заполните поле «{{guidance(`products[${index}].price`).label || fieldLabel(`products[${index}].price`)}}».</small></label><label :data-bp-field="`products[${index}].unit_cost`" :class="{'bp-field-error':fieldInvalid(`products[${index}].unit_cost`)}">Себестоимость единицы <span class="bp-required-tag">Обязательно</span><input v-model="product.unit_cost" type="text" inputmode="decimal" required :aria-describedby="fieldInvalid(`products[${index}].unit_cost`) ? missingId(`products[${index}].unit_cost`) : undefined" :aria-invalid="fieldInvalid(`products[${index}].unit_cost`)"><small>{{guidance(`products[${index}].unit_cost`).how}}</small><small v-if="fieldInvalid(`products[${index}].unit_cost`)" :id="missingId(`products[${index}].unit_cost`)" class="bp-field-help">Заполните поле «{{guidance(`products[${index}].unit_cost`).label || fieldLabel(`products[${index}].unit_cost`)}}».</small></label></div><label class="bp-check"><input type="checkbox" :checked="product.capacity!=null" @change="product.capacity=$event.target.checked?'':null"> Указать производственную мощность</label><small>Объём реализации{{product.capacity!=null?' и мощность':''}} — в графиках ниже.</small></div>
              <p v-if="!form.products.length" class="sub">Для расчёта добавьте хотя бы один препарат или другой продукт.</p>
              <h3 v-if="productSchedules.length" class="bp-form-head">Объёмы и помесячные графики</h3><p v-if="productSchedules.length" class="sub">Одно значение повторяется каждый месяц. Для сезонности используйте график по месяцам.</p>
              <div v-for="entry in productSchedules" :key="entry.key" class="bp-schedule" :data-bp-field="scheduleField(entry)">
                <template v-if="!Array.isArray(entry.object[entry.field])"><label :class="{'bp-field-error':fieldInvalid(scheduleField(entry))}">{{entry.label}} · каждый месяц<input :data-bp-field="scheduleField(entry)" v-model="entry.object[entry.field]" type="text" inputmode="decimal" required :aria-invalid="fieldInvalid(scheduleField(entry))" :aria-describedby="fieldInvalid(scheduleField(entry)) ? missingId(scheduleField(entry)) : undefined"><small v-if="fieldInvalid(scheduleField(entry))" :id="missingId(scheduleField(entry))" class="bp-field-help">Заполните поле «{{guidance(scheduleField(entry)).label || fieldLabel(scheduleField(entry))}}».</small></label><button type="button" class="secondary tiny" @click="makeMonthly(entry)">По месяцам</button></template>
                <details v-else class="bp-details"><summary>{{entry.label}} · график: {{entry.object[entry.field].length}} месяцев</summary><p v-if="entry.object[entry.field].length!==Number(form.months)" class="warning">Длина графика отличается от прогноза ({{form.months}} мес.). Уже введённые значения сохраняются до вашего выбора.<button type="button" class="secondary tiny" @click="makeMonthly(entry)">{{entry.object[entry.field].length>Number(form.months)?'Убрать последние '+(entry.object[entry.field].length-Number(form.months))+' значений':'Добавить пустые месяцы'}}</button></p><div class="bp-month-grid"><label v-for="(_,index) in entry.object[entry.field]" :key="index" :class="{'bp-field-error':fieldInvalid(scheduleField(entry,index))}">{{labels[index]||'Месяц '+(index+1)}}<input :data-bp-field="scheduleField(entry,index)" v-model="entry.object[entry.field][index]" type="text" inputmode="decimal" required :aria-invalid="fieldInvalid(scheduleField(entry,index))" :aria-describedby="fieldInvalid(scheduleField(entry,index)) ? missingId(scheduleField(entry,index)) : undefined"><small v-if="fieldInvalid(scheduleField(entry,index))" :id="missingId(scheduleField(entry,index))" class="bp-field-help">Заполните поле «{{guidance(scheduleField(entry,index)).label || fieldLabel(scheduleField(entry,index))}}».</small></label></div><button type="button" class="ghost tiny" @click="entry.object[entry.field]=''">Заменить график одним ежемесячным значением</button></details>
              </div>
              <div class="bp-narratives"><label v-for="[field,label] in narrativeFields(4)" :key="field" :data-bp-field="field">{{label}}<textarea v-model="form[field]" maxlength="16000"></textarea></label></div>
              </details>

              <details class="bp-details" data-bp-group="working-capital"><summary>6. Оборотный капитал и риски</summary><p class="sub">Остатки на начало прогноза и сроки оплаты в днях. Пустое значение не означает ноль.</p><div class="bp-fields"><label v-for="[field,label] in workingCapitalScalars" :key="field" :data-bp-field="field" :class="{'bp-field-error':fieldInvalid(field)}">{{label}} <span class="bp-required-tag">Обязательно</span><input v-model="form[field]" type="text" inputmode="decimal" required :aria-invalid="fieldInvalid(field)" :aria-describedby="fieldInvalid(field) ? missingId(field) : undefined"><small>{{guidance(field).what}}</small><small v-if="fieldInvalid(field)" :id="missingId(field)" class="bp-field-help">Заполните поле «{{guidance(field).label || fieldLabel(field)}}».</small></label></div><div class="bp-narratives"><label v-for="[field,label] in narrativeFields(5)" :key="field" :data-bp-field="field">{{label}}<textarea v-model="form[field]" maxlength="16000"></textarea></label></div></details>
              <label class="bp-check bp-confirm"><input v-model="confirmed" type="checkbox"> Я проверил финансовые параметры и замечания к источникам</label>
              <p v-if="needsConfirmation&&!confirmed" class="sub">Для расчёта по исправленным параметрам подтвердите проверку источников.</p>
              <p v-if="remainingRequirements.length" class="warning" role="status">Перед расчётом заполните {{remainingRequirements.length}} полей. Откройте нужное поле кнопкой «К полю» в списке обязательных данных.</p>
              <div v-if="canEdit" class="bp-actions"><button type="button" class="secondary" :disabled="busy" @click="saveDraft()"><AppIcon name="check"/> Сохранить черновик</button><button type="button" :disabled="busy" @click="goStage('report')">Перейти к отчёту</button><span v-if="changed" class="pill warn">Есть изменения параметров</span></div>
            </fieldset>
          </form>
          </section>
          </template>
          </div>
          </template>
          <section v-if="!current.native_model" :hidden="flowStage!=='report'" class="bp-results"><h3>Подготовить отчёт</h3><p class="sub">Полный бизнес-план и финансовые таблицы формируются из сохранённых данных проекта.</p><p v-if="requiredFields.length" class="warning">Незаполненные поля перечислены выше. Нажмите название, чтобы внести данные.</p><p v-if="needsConfirmation&&!confirmed" class="warning">Перед расчётом подтвердите проверку замечаний в разделе «Данные».</p><div class="bp-actions"><button v-if="canEdit" :disabled="busy||needsConfirmation&&!confirmed" @click="generate"><AppIcon name="report"/> Рассчитать бизнес-план</button><button type="button" class="secondary" :disabled="busy" @click="goStage('data')">Вернуться к данным</button></div></section>
          <component :is="reportsStale?'details':'section'" v-if="latest&&!current.native_model" :hidden="flowStage!=='report'" class="bp-results">
            <summary v-if="reportsStale">Предыдущая сохранённая версия</summary><h3 v-else>Готовые отчёты · версия исходных данных {{latest.revision}}</h3><p class="sub">{{latest.business_archive?.title||current.title}}</p><p class="sub">{{uploadLabel(latest.created_at)}} · суммы в валюте, указанной в файлах этой версии</p>
            <div class="bp-metrics"><div><small>NPV проекта</small><b>{{money(latest.metrics?.npv)}}</b></div><div><small>IRR годовая</small><b>{{ratio(latest.metrics?.irr_annual)}}</b></div><div><small>Окупаемость, мес.</small><b>{{latest.metrics?.payback_months==null?'Не определена':money(latest.metrics.payback_months)}}</b></div><div><small>Минимум денег</small><b>{{money(latest.metrics?.minimum_cash)}}</b></div></div>
            <p v-if="latest.metrics?.irr_reason" class="sub bp-note">{{latest.metrics.irr_reason}}</p><p v-if="String(latest.metrics?.minimum_cash||'').startsWith('-')" class="warning">В прогнозе есть недостаток денег. Проверьте финансирование и сроки расходов в отчётах.</p>
            <p v-if="changed||current.status!=='ready'||latest.revision!==current.revision" class="warning">Файлы относятся к сохранённой версии. Изменённые параметры и новые источники попадут в следующий расчёт.</p>
            <div class="bp-actions"><a v-for="file in downloads(latest).filter(item=>!item.secondary)" :key="file.title" class="button secondary" :href="file.url"><AppIcon name="download"/> {{file.title}}</a></div>
            <details v-if="downloads(latest).some(item=>item.secondary)" class="bp-details"><summary>Дополнительно · ТЭО</summary><div class="bp-actions"><a v-for="file in downloads(latest).filter(item=>item.secondary)" :key="file.title" class="button secondary" :href="file.url">{{file.title}}</a></div></details>
            <details v-if="generations.length>1" class="bp-details"><summary>Предыдущие расчёты · {{generations.length-1}}</summary><div v-for="generation in generations.slice(1)" :key="generation.id" class="bp-generation"><b>{{uploadLabel(generation.created_at)}} · версия исходных данных {{generation.revision}}</b><div class="bp-actions"><a v-for="file in downloads(generation)" :key="file.title" class="button secondary tiny" :href="file.url">{{file.title}}</a></div></div></details>
          </component>
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.bp-missing-summary{margin:16px 0;padding:14px 16px;border:1px solid var(--line);border-radius:12px;background:var(--emx)}.bp-missing-summary h4{margin:0 0 10px}.bp-missing-summary ul{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 16px;margin:0;padding-left:20px;max-height:360px;overflow:auto}.bp-missing-summary button{white-space:normal;text-align:left}.bp-validation-attempted{border-color:#b42318}.bp-field-help{color:#b42318!important;line-height:1.5}.bp-field-error input,.bp-field-error select,.bp-field-error textarea{border-color:#b42318!important;background:var(--paper,#fff)!important}.bp-field-error{color:#b42318}@media(max-width:700px){.bp-missing-summary ul{grid-template-columns:1fr}}
.business-projects [hidden]{display:none!important}.bp-fieldset details>label{display:grid;gap:6px;max-width:460px}.bp-narratives{margin-top:16px}.bp-flow{margin-top:20px}
.bp-original-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}.bp-original-list b{overflow-wrap:anywhere}.bp-original-list .button{margin-top:auto;white-space:normal}
.bp-required-guide{padding:18px;border:1px solid var(--line);border-radius:14px;background:var(--emx);margin:18px 0}.bp-required-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.bp-required-item{display:flex;flex-direction:column;gap:8px;border:1px solid var(--line);border-radius:12px;padding:16px;background:var(--paper,#fff)}.bp-required-item h4{margin:0;font-size:15px}.bp-required-item p{margin:0;line-height:1.55;overflow-wrap:anywhere}.bp-required-item button{align-self:flex-start;margin-top:auto;white-space:normal;text-align:left}.bp-required-tag{color:var(--em);font-size:11px;font-weight:700}.bp-fields input[aria-invalid="true"],.bp-fields select[aria-invalid="true"],.bp-schedule input[aria-invalid="true"]{border-color:var(--warn,#a76500);background:var(--warnl)}@media(max-width:700px){.bp-required-list{grid-template-columns:1fr}.bp-required-guide{padding:14px}}
.bp-intro{line-height:1.7;max-width:960px;margin:4px 0 18px}.bp-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:14px 0}.bp-project-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;margin:16px 0}.bp-project{padding:14px;min-height:70px;justify-content:space-between;white-space:normal;text-align:left}.bp-project.selected{border-color:var(--em);background:var(--eml)}.bp-project small{margin-top:5px}.bp-workspace{margin-top:20px;padding-top:20px;border-top:1px solid var(--line)}.bp-fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.bp-fields label,.bp-narratives label,.bp-month-grid label{display:grid;gap:6px;min-width:0}.bp-fields .bp-wide{grid-column:1/-1}.bp-note{line-height:1.65;margin:12px 0}.bp-upload-area{background:var(--emx);border:1px solid var(--line);border-radius:16px;padding:18px;margin:16px 0}.bp-details{margin:14px 0;border:1px solid var(--line);padding:12px 14px;border-radius:12px}.bp-details summary{cursor:pointer;font-weight:700;color:var(--ink)}.bp-details ul,.bp-review ul{padding-left:20px;margin:12px 0;display:grid;gap:10px;overflow-wrap:anywhere}.bp-files{list-style:none;padding-left:0!important}.bp-files li{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.bp-files small{white-space:nowrap}.bp-review{background:var(--warnl);border:1px solid var(--line);padding:16px;border-radius:14px;margin:16px 0}.bp-progress{margin-top:16px;overflow-wrap:anywhere}.bp-fieldset{border:0;padding:0;margin:0;min-width:0}.bp-form-head{margin-top:25px;margin-bottom:14px}.bp-form-head .sub{margin-top:6px}.bp-row-card{border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0}.bp-check{display:flex;align-items:flex-start;gap:10px;color:var(--ink);margin:14px 0}.bp-check input{flex:none}.bp-schedule{margin:12px 0;padding:14px;border:1px solid var(--line);border-radius:12px;display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap}.bp-schedule>label{display:grid;gap:7px;flex:1;min-width:200px}.bp-schedule .bp-details{width:100%;border:0;padding:0;margin:0}.bp-month-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:18px 0}.bp-month-grid label{font-size:12px}.bp-schedule .warning button{margin:8px 0 0;display:flex}.bp-narratives{display:grid;gap:14px}.bp-confirm{margin-top:22px}.bp-reference{white-space:pre-wrap;overflow-wrap:anywhere;margin-top:12px;line-height:1.7}.bp-results{border-top:1px solid var(--line);margin-top:25px;padding-top:22px}.bp-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}.bp-metrics>div{background:var(--emx);border:1px solid var(--line);border-radius:12px;padding:14px}.bp-metrics b{font-size:19px;display:block;margin-top:7px;overflow-wrap:anywhere}.bp-generation{margin-top:16px}.business-projects .error,.business-projects .notice{margin-top:14px}@media(max-width:850px){.bp-fields{grid-template-columns:repeat(2,minmax(0,1fr))}.bp-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:560px){.bp-fields{grid-template-columns:1fr}.bp-upload-area{padding:13px}.bp-month-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.bp-actions .button,.bp-actions>button{white-space:normal;text-align:center}.bp-files li{display:block}.bp-files small{margin-top:4px}.bp-schedule>label{min-width:0;flex-basis:100%}.bp-metrics b{font-size:16px}}
</style>
