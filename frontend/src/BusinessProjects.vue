<script setup>
import {ref, computed, watch, onMounted, onUnmounted} from 'vue';
import AppIcon from './AppIcon.vue';
import {archiveFileUrl, uploadLabel} from './reportArchive.js';
import {formatCents, toCents} from './overview/format.js';
import {SOURCE_ACCEPT, BUSINESS_SCALARS, NARRATIVE_FIELDS, prepareSources, sourceSize, projectUrl, templateUrl,
  formFromInputs, inputsFromForm, monthLabels, resizeValues, createBusinessState, uploadProjectFolder, generateProject,
  assertProject, fieldLabel, issueMessage, sourceIssues, evidenceRows, sourceLocation} from './businessProjects.js';

const props = defineProps({api: {type: Function, required: true}, company: {type: Object, required: true}, refresh: {type: Number, default: 0}});
const emit = defineEmits(['editing', 'generated']);
const projects = ref([]), canUpload = ref(false), listLoading = ref(false), error = ref(''), notice = ref('');
const state = ref(null), current = ref(null), action = ref(''), step = ref('');
const header = ref({title: '', start: '', months: 36, currency: ''});
const form = ref(formFromInputs()), selected = ref([]), ignored = ref([]), selectionProblems = ref([]), totalSize = ref(0);
const confirmed = ref(false), loadedSnapshot = ref('');
let active = true, listRequest = 0, detailRequest = 0;
const busy = computed(() => !!action.value), editing = computed(() => !!state.value);
const canEdit = computed(() => !!current.value?.can_edit);
const sourceProblems = computed(() => sourceIssues(current.value?.extraction));
const needsConfirmation = computed(() => sourceProblems.value.some(issue => issue.code !== 'missing_or_invalid' && issue.requires_confirmation));
const validation = computed(() => current.value?.validation || []);
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
const statusLabel = project => project.status === 'ready' ? 'Отчёты готовы' : project.status === 'needs_data' ? 'Нужны параметры' : 'Папка проекта';
const money = value => formatCents(toCents(value), 'USD');
const ratio = value => value == null ? '—' : new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2}).format(Number(value) * 100) + '%';

watch(editing, value => emit('editing', value));
watch(() => props.refresh, loadProjects);
onMounted(loadProjects);
onUnmounted(() => {active = false; listRequest++; detailRequest++; emit('editing', false);});

async function loadProjects() {
  const request = ++listRequest; listLoading.value = true;
  try {
    const response = await props.api(projectUrl(props.company.id));
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
function chooseSources(event) {
  const result = prepareSources(event.target.files || []); event.target.value = '';
  selected.value = result.files; ignored.value = result.ignored; selectionProblems.value = result.problems; totalSize.value = result.total;
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
  } catch (e) {if (active && e.name !== 'AbortError') {error.value = e.message + ' Загруженные файлы остаются в папке. Повторная попытка продолжит этот проект.'; await recoverConflict(e);}}
  finally {if (active) {action.value = ''; step.value = '';}}
}
async function generate() {
  if (busy.value || !canEdit.value || needsConfirmation.value && !confirmed.value) return;
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
</script>

<template>
  <section class="card business-projects" aria-labelledby="business-projects-title">
    <div class="card-h"><div><h3 id="business-projects-title">Бизнес-план и ТЭО из папки</h3><p class="sub">{{company.name}} · один расчёт, два отчёта в PDF и Excel</p></div><button v-if="canUpload&&!editing" :disabled="busy||listLoading" @click="newProject"><AppIcon name="plus"/> Новая папка проекта</button></div>
    <div class="card-b">
      <p class="sub bp-intro">Загрузите папку с исходными таблицами, договорами и описанием проекта. Система изучит файлы, рассчитает модель и подготовит бизнес-план и ТЭО. Если данных недостаточно, здесь появятся поля для заполнения.</p>
      <div class="bp-actions"><a class="button secondary" :href="templateUrl(company.id)"><AppIcon name="download"/> Шаблон исходных данных · Excel</a><button class="ghost" :disabled="busy||listLoading" @click="loadProjects"><AppIcon name="refresh"/> Обновить папки</button></div>
      <p v-if="error" class="error" role="alert">{{error}}</p><p v-if="notice" class="notice" role="status">{{notice}}</p>
      <p v-if="listLoading" class="loading" role="status">Загружаем проекты…</p>
      <div v-if="projects.length" class="bp-project-list"><button v-for="project in projects" :key="project.id" class="secondary bp-project" :class="{selected:current?.id===project.id}" :disabled="busy||editing" @click="openProject(project)"><span><b>{{project.title}}</b><small>{{statusLabel(project)}} · версия исходных данных {{project.revision}}</small></span><AppIcon name="arrow"/></button></div>
      <p v-else-if="!listLoading&&!editing" class="sub">Папок пока нет. Начните с новой папки или заполненного Excel-шаблона.</p>

      <div v-if="editing" class="bp-workspace">
        <div class="section-head"><h3>{{current?current.title:'Новая папка проекта'}}</h3><button class="secondary tiny" :disabled="busy" @click="closeProject">{{changed?'Закрыть без сохранения параметров':'Закрыть проект'}}</button></div>
        <p v-if="current&&!canEdit" class="sub">Просмотр проекта и готовых отчётов. Изменять исходные данные может автор папки с правом импорта.</p>
        <div v-if="!current||canEdit" class="bp-upload-area">
          <div class="bp-fields">
            <label class="bp-wide">Название проекта<input v-model="header.title" maxlength="160" :disabled="busy||!!state.createFields||!!current" placeholder="Производство лекарственных препаратов"></label>
            <label>Начало прогноза<input type="month" :value="header.start.slice(0,7)" min="2000-01" max="2100-12" :disabled="busy" @input="setStart(header,$event)"></label>
            <label>Прогноз, месяцев<input v-model="header.months" type="number" min="1" max="60" step="1" :disabled="busy"><small>От 1 до 60; по умолчанию 36 месяцев</small></label>
            <label>Валюта модели<select v-model="header.currency" :disabled="busy"><option value="">Определить из источников</option><option>USD</option><option>UZS</option><option>EUR</option></select></label>
          </div>
          <p class="sub bp-note">Начало прогноза задаётся отдельно от даты загрузки. При добавлении новых источников анализ заново проверит финансовые параметры; готовые версии останутся в истории.</p>
          <div class="bp-actions"><label class="button"><AppIcon name="import"/> Выбрать папку<input type="file" webkitdirectory multiple :accept="SOURCE_ACCEPT" hidden :disabled="busy" @change="chooseSources"></label><label class="button secondary"><AppIcon name="file"/> Выбрать отдельные файлы<input type="file" multiple :accept="SOURCE_ACCEPT" hidden :disabled="busy" @change="chooseSources"></label></div>
          <p class="sub bp-note">PDF, XLSX, XLTX, DOCX, CSV, TXT, JSON, ZIP, PNG и JPEG. До 100 файлов, 20 МБ на файл и 100 МБ на папку. Формулы Excel не выполняются; изображения и договоры сохраняются как источники.</p>
          <p v-for="problem in selectionProblems" :key="problem" class="error" role="alert">{{problem}}</p>
          <details v-if="ignored.length" class="bp-details"><summary>Пропущены служебные файлы: {{ignored.length}}</summary><ul><li v-for="item in ignored" :key="item.name">{{item.name}} — {{item.reason}}</li></ul></details>
          <details v-if="selected.length" class="bp-details" open><summary>Выбрано {{selected.length}} файлов · {{sourceSize(totalSize)}}</summary><ul class="bp-files"><li v-for="item in selected" :key="item.key"><span>{{item.relativePath}}</span><small>{{sourceSize(item.size)}} {{state.uploaded.has(item.key)?'· загружен':''}}</small></li></ul></details>
          <p v-if="state.createUnknown" class="warning">Сервер мог создать папку. Повторите загрузку: она продолжится в том же проекте.</p>
          <button :disabled="busy||!selected.length||selectionProblems.length>0" @click="upload"><AppIcon name="import"/> {{current?'Добавить файлы и выполнить анализ':'Загрузить папку и сформировать отчёты'}}</button>
        </div>
        <p v-if="busy" class="loading bp-progress" role="status">{{step||'Загружаем проект…'}}</p>

        <template v-if="current">
          <details class="bp-details"><summary>Исходные файлы проекта · {{current.files?.length||0}}</summary><ul class="bp-files"><li v-for="file in current.files||[]" :key="file.relative_path"><span>{{file.relative_path||file.filename}}</span><small>{{sourceSize(file.size||0)}}</small></li></ul></details>
          <div v-if="sourceProblems.length||validation.length" class="bp-review">
            <h3>{{validation.length?'Что нужно заполнить и проверить':'Замечания к исходным файлам'}}</h3>
            <p v-if="current.status==='ready'&&current.extraction?.parameters_confirmed" class="sub bp-note">Финансовые параметры проверены и приняты в расчёт. Ниже сохранены замечания к первоначальным источникам.</p>
            <ul><li v-for="(issue,index) in sourceProblems" :key="'s'+index"><b>{{fieldLabel(issue.field)}}</b>: {{issueMessage(issue)}}<small v-if="issue.source">{{sourceLocation(issue.source)}}</small></li><li v-for="(issue,index) in validation" :key="'v'+index"><b>{{fieldLabel(issue.field)}}</b>: {{issueMessage(issue)}}</li></ul>
          </div>
          <details v-if="evidence.length" class="bp-details"><summary>Параметры, распознанные в источниках · {{evidence.length}}</summary><ul><li v-for="(item,index) in evidence" :key="index"><b>{{fieldLabel(item.field)}}</b><small>{{sourceLocation(item.source)}}<template v-if="item.note"> · {{item.note}}</template></small><span v-if="item.status==='needs_confirmation'" class="pill warn">Проверьте значение</span></li></ul></details>
          <details v-if="narratives.length" class="bp-details"><summary>Описание в исходных документах</summary><details v-for="(item,index) in narratives" :key="index" class="bp-details"><summary>{{item.filename||'Документ'}}</summary><p class="bp-reference">{{item.text}}</p></details></details>

          <template v-if="canEdit">
          <details class="bp-details" :open="current.status!=='ready'"><summary>Параметры финансовой модели · проверить или изменить</summary>
          <div class="section-head bp-form-head"><div><h3>Параметры финансовой модели</h3><p class="sub">Все суммы в одной валюте, без НДС. Отсутствующее значение оставьте пустым; ноль указывайте явно.</p></div><button v-if="changed" class="ghost tiny" :disabled="busy" @click="reloadInputs">Вернуть сохранённые параметры</button></div>
          <form @submit.prevent="generate">
            <fieldset :disabled="busy||!canEdit" class="bp-fieldset">
              <div class="bp-fields">
                <label class="bp-wide">Название отчётов<input v-model="form.title" maxlength="160"></label>
                <label>Начало прогноза<input type="month" :value="form.start.slice(0,7)" min="2000-01" max="2100-12" @input="setStart(form,$event)"></label>
                <label>Прогноз, месяцев<input v-model="form.months" type="number" min="1" max="60" step="1"></label>
                <label>Валюта<select v-model="form.currency"><option value="">Выберите валюту</option><option>USD</option><option>UZS</option><option>EUR</option></select></label>
                <label>Налог на прибыль, %<input v-model="form.tax_rate" type="text" inputmode="decimal" placeholder="Например, 15"></label>
                <label>Годовая ставка дисконтирования, %<input v-model="form.discount_rate" type="text" inputmode="decimal" placeholder="Например, 12"></label>
                <label v-for="[field,label] in BUSINESS_SCALARS" :key="field">{{label}}<input v-model="form[field]" type="text" inputmode="decimal"></label>
              </div>
              <p class="sub bp-note">Стоимость актива, вводимого в месяце прогноза, учитывается как CAPEX в этом месяце. Такой расход повторно не включайте в инвестиции до начала прогноза.</p>

              <div class="section-head bp-form-head"><h3>Продукция · {{form.products.length}}</h3><button type="button" class="secondary tiny" :disabled="form.products.length>=200" @click="addRow('products')"><AppIcon name="plus"/> Добавить продукцию</button></div>
              <div v-for="(product,index) in form.products" :key="index" class="bp-row-card"><div class="section-head"><b>Продукция №{{index+1}}</b><button type="button" class="ghost tiny" @click="form.products.splice(index,1)">Удалить строку</button></div><div class="bp-fields"><label>Название<input v-model="product.name" maxlength="160"></label><label>Единица измерения<input v-model="product.unit" maxlength="40" placeholder="упаковка"></label><label>Цена реализации<input v-model="product.price" type="text" inputmode="decimal"></label><label>Себестоимость единицы<input v-model="product.unit_cost" type="text" inputmode="decimal"></label></div><label class="bp-check"><input type="checkbox" :checked="product.capacity!=null" @change="product.capacity=$event.target.checked?'':null"> Указать производственную мощность</label><small>Объём реализации{{product.capacity!=null?' и мощность':''}} — в графиках ниже.</small></div>
              <p v-if="!form.products.length" class="sub">Для расчёта добавьте хотя бы один препарат или другой продукт.</p>

              <div class="section-head bp-form-head"><h3>Основные средства · {{form.assets.length}}</h3><button type="button" class="secondary tiny" :disabled="form.assets.length>=200" @click="addRow('assets')"><AppIcon name="plus"/> Добавить актив</button></div>
              <label v-if="!form.assets.length" class="bp-check"><input v-model="form.no_assets" type="checkbox"> Основных средств в этой модели нет</label>
              <div v-for="(asset,index) in form.assets" :key="index" class="bp-row-card"><div class="section-head"><b>Актив №{{index+1}}</b><button type="button" class="ghost tiny" @click="form.assets.splice(index,1);form.no_assets=false">Удалить строку</button></div><div class="bp-fields"><label>Название<input v-model="asset.name" maxlength="160"></label><label>Стоимость<input v-model="asset.value" type="text" inputmode="decimal"></label><label>Срок амортизации, месяцев<input v-model="asset.life_months" type="number" min="1" max="1200" step="1"></label><label>Месяц ввода<input v-model="asset.commissioning_month" type="number" min="0" :max="form.months" step="1"><small>0 — актив на начало; 1 — первый месяц прогноза</small></label></div></div>

              <div class="section-head bp-form-head"><h3>Кредиты · {{form.loans.length}}</h3><button type="button" class="secondary tiny" :disabled="form.loans.length>=200" @click="addRow('loans')"><AppIcon name="plus"/> Добавить кредит</button></div>
              <label v-if="!form.loans.length" class="bp-check"><input v-model="form.no_loans" type="checkbox"> Кредитов в этой модели нет</label>
              <div v-for="(loan,index) in form.loans" :key="index" class="bp-row-card"><div class="section-head"><b>Кредит №{{index+1}}</b><button type="button" class="ghost tiny" @click="form.loans.splice(index,1);form.no_loans=false">Удалить строку</button></div><div class="bp-fields"><label>Название<input v-model="loan.name" maxlength="160"></label><label>Долг на начало прогноза<input v-model="loan.opening_balance" type="text" inputmode="decimal"></label></div><small>Получение, погашение и проценты задайте в графиках ниже. Процентную ставку вместо суммы процентов здесь не вводите.</small></div>

              <h3 class="bp-form-head">Объёмы и помесячные графики</h3><p class="sub">Одно значение повторяется каждый месяц. Для сезонности, разовых взносов и кредитов используйте график по месяцам.</p>
              <div v-for="entry in scheduleEntries" :key="entry.key" class="bp-schedule">
                <template v-if="!Array.isArray(entry.object[entry.field])"><label>{{entry.label}} · каждый месяц<input v-model="entry.object[entry.field]" type="text" inputmode="decimal"></label><button type="button" class="secondary tiny" @click="makeMonthly(entry)">По месяцам</button></template>
                <details v-else class="bp-details"><summary>{{entry.label}} · график: {{entry.object[entry.field].length}} месяцев</summary><p v-if="entry.object[entry.field].length!==Number(form.months)" class="warning">Длина графика отличается от прогноза ({{form.months}} мес.). Уже введённые значения сохраняются до вашего выбора.<button type="button" class="secondary tiny" @click="makeMonthly(entry)">{{entry.object[entry.field].length>Number(form.months)?'Убрать последние '+(entry.object[entry.field].length-Number(form.months))+' значений':'Добавить пустые месяцы'}}</button></p><div class="bp-month-grid"><label v-for="(_,index) in entry.object[entry.field]" :key="index">{{labels[index]||'Месяц '+(index+1)}}<input v-model="entry.object[entry.field][index]" type="text" inputmode="decimal"></label></div><button type="button" class="ghost tiny" @click="entry.object[entry.field]=''">Заменить график одним ежемесячным значением</button></details>
              </div>

              <details class="bp-details"><summary>Описание проекта для бизнес-плана и ТЭО</summary><p class="sub bp-note">Укажите подтверждённые сведения. Пустые разделы останутся отмечены как требующие дополнения.</p><div class="bp-narratives"><label v-for="[field,label] in NARRATIVE_FIELDS" :key="field">{{label}}<textarea v-model="form[field]" maxlength="12000"></textarea></label></div></details>
              <label class="bp-check bp-confirm"><input v-model="confirmed" type="checkbox"> Я проверил финансовые параметры и замечания к источникам</label>
              <p v-if="needsConfirmation&&!confirmed" class="sub">Для расчёта по исправленным параметрам подтвердите проверку источников.</p>
              <div v-if="canEdit" class="bp-actions"><button :disabled="busy||needsConfirmation&&!confirmed"><AppIcon name="report"/> Рассчитать бизнес-план и ТЭО</button><span v-if="changed" class="pill warn">Есть изменения параметров</span></div>
            </fieldset>
          </form>
          </details>
          </template>

          <div v-if="latest" class="bp-results">
            <h3>Готовые отчёты · версия исходных данных {{latest.revision}}</h3><p class="sub">{{uploadLabel(latest.created_at)}} · суммы в валюте, указанной в файлах этой версии</p>
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
.bp-intro{line-height:1.7;max-width:960px;margin:4px 0 18px}.bp-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:14px 0}.bp-project-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;margin:16px 0}.bp-project{padding:14px;min-height:70px;justify-content:space-between;white-space:normal;text-align:left}.bp-project.selected{border-color:var(--em);background:var(--eml)}.bp-project small{margin-top:5px}.bp-workspace{margin-top:20px;padding-top:20px;border-top:1px solid var(--line)}.bp-fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.bp-fields label,.bp-narratives label,.bp-month-grid label{display:grid;gap:6px;min-width:0}.bp-fields .bp-wide{grid-column:1/-1}.bp-note{line-height:1.65;margin:12px 0}.bp-upload-area{background:var(--emx);border:1px solid var(--line);border-radius:16px;padding:18px;margin:16px 0}.bp-details{margin:14px 0;border:1px solid var(--line);padding:12px 14px;border-radius:12px}.bp-details summary{cursor:pointer;font-weight:700;color:var(--ink)}.bp-details ul,.bp-review ul{padding-left:20px;margin:12px 0;display:grid;gap:10px;overflow-wrap:anywhere}.bp-files{list-style:none;padding-left:0!important}.bp-files li{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.bp-files small{white-space:nowrap}.bp-review{background:var(--warnl);border:1px solid var(--line);padding:16px;border-radius:14px;margin:16px 0}.bp-progress{margin-top:16px;overflow-wrap:anywhere}.bp-fieldset{border:0;padding:0;margin:0;min-width:0}.bp-form-head{margin-top:25px;margin-bottom:14px}.bp-form-head .sub{margin-top:6px}.bp-row-card{border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0}.bp-check{display:flex;align-items:flex-start;gap:10px;color:var(--ink);margin:14px 0}.bp-check input{flex:none}.bp-schedule{margin:12px 0;padding:14px;border:1px solid var(--line);border-radius:12px;display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap}.bp-schedule>label{display:grid;gap:7px;flex:1;min-width:200px}.bp-schedule .bp-details{width:100%;border:0;padding:0;margin:0}.bp-month-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:18px 0}.bp-month-grid label{font-size:12px}.bp-schedule .warning button{margin:8px 0 0;display:flex}.bp-narratives{display:grid;gap:14px}.bp-confirm{margin-top:22px}.bp-reference{white-space:pre-wrap;overflow-wrap:anywhere;margin-top:12px;line-height:1.7}.bp-results{border-top:1px solid var(--line);margin-top:25px;padding-top:22px}.bp-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}.bp-metrics>div{background:var(--emx);border:1px solid var(--line);border-radius:12px;padding:14px}.bp-metrics b{font-size:19px;display:block;margin-top:7px;overflow-wrap:anywhere}.bp-generation{margin-top:16px}.business-projects .error,.business-projects .notice{margin-top:14px}@media(max-width:850px){.bp-fields{grid-template-columns:repeat(2,minmax(0,1fr))}.bp-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:560px){.bp-fields{grid-template-columns:1fr}.bp-upload-area{padding:13px}.bp-month-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.bp-actions .button,.bp-actions>button{white-space:normal;text-align:center}.bp-files li{display:block}.bp-files small{margin-top:4px}.bp-schedule>label{min-width:0;flex-basis:100%}.bp-metrics b{font-size:16px}}
</style>
