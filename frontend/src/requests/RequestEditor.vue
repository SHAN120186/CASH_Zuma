<script setup>
// Форма заявки на оплату — одно окно: поля, три блока вложений, «Сохранить черновик» и «Отправить на согласование».
// Порядок сохранения (черновик → файлы → отправка) и повтор после ошибки — в saveFlow.js.
import {ref, computed, watch, onMounted, onUnmounted, nextTick} from 'vue';
import {PRIORITIES, LEAD_DAYS, CHANNELS, DOC_KINDS, REQUIRED_DOCS, SINGLE_DOCS, DOC_ACCEPT, accountsFor, minDue, budgetRows, budgetNote,
        periodLabel, fileProblem, fileSize} from './workflow.js';
import {createSaveState, requestBody, saveRequest, removeSavedDocument, missingRequired} from './saveFlow.js';

const props = defineProps({
  api: {type: Function, required: true},
  boot: {type: Object, required: true},
  company: {type: Object, default: null},
  request: {type: Object, default: null},
  currency: {type: String, default: 'UZS'},
  money: {type: Function, required: true},
  companyUrl: {type: Function, required: true},
});
const emit = defineEmits(['close', 'saved', 'open-card']);

const r = props.request;
const state = createSaveState(r);
const sentMode = state.mode === 'sent';
// По умолчанию — банковский счёт выбранной валюты, затем касса, затем любой счёт.
const firstAccount = props.boot.accounts.find(a => a.currency === props.currency && a.kind === 'bank')
  || props.boot.accounts.find(a => a.currency === props.currency) || props.boot.accounts[0];
const form = ref(r ? {
  channel: r.channel || r.account_kind, currency: r.currency, account_id: r.account_id, category_id: r.category_id,
  amount: r.amount, counterparty: r.counterparty, purpose: r.purpose, priority: r.priority, date: r.date, project: r.project || '', reason: '',
} : {
  channel: firstAccount?.kind || 'bank', currency: firstAccount?.currency || props.currency, account_id: firstAccount?.id ?? null,
  category_id: props.boot.categories.find(c => c.type === 'outcome')?.id ?? null, amount: '', counterparty: '', purpose: '',
  priority: 'normal', date: minDue(props.boot.due_minimums, 'normal'), project: '',
});
const error = ref(''), notice = ref(''), saving = ref(false), step = ref(''), blocked = ref(''), card = ref(null), dialog = ref(null);
// Номер появляется, когда форма создала черновик (например, до ошибки загрузки файла).
const number = ref(r?.number || null), status = ref(r?.status || null);
// Сохранённые (действующие) файлы заявки и выбранные, ещё не загруженные.
const saved = ref((r?.documents_list || []).filter(d => d.current));
const files = ref([]);
let fileKey = 0;
// Сроки считает сервер по календарю; при открытии формы берём свежие (календарь мог измениться).
const dues = ref(props.boot.due_minimums || {});

const accountOptions = computed(() => accountsFor(props.boot.accounts, form.value.channel, form.value.currency));
const outcomeCategories = computed(() => props.boot.categories.filter(c => c.type === 'outcome'));
const earliest = computed(() => minDue(dues.value, form.value.priority));
const earliestLabel = computed(() => earliest.value ? earliest.value.split('-').reverse().join('.') : '');
const title = computed(() => r ? 'Изменить ' + r.number : number.value ? 'Черновик ' + number.value : 'Новая заявка на оплату');
const needsReason = computed(() => !!r);
const missing = computed(() => missingRequired(saved.value, files.value));
// Убрать сохранённый файл можно до отправки; после — в карточке заявки с причиной.
const canRemoveSaved = computed(() => !sentMode && state.id != null && ['draft', 'returned'].includes(status.value));
const savedOf = kind => saved.value.filter(d => d.kind === kind);
const pendingOf = kind => files.value.filter(f => f.kind === kind);
const replacing = kind => SINGLE_DOCS.includes(kind) && pendingOf(kind).length > 0;
const pickLabel = kind => kind === 'other' ? 'Добавить файлы' : (savedOf(kind).length || pendingOf(kind).length ? 'Заменить файл' : 'Выбрать файл');
const STEPS = {save: 'Сохраняем заявку…', upload: 'Загружаем файлы…', submit: 'Отправляем на согласование…'};

// Счёт подбирается под канал и валюту; дата не раньше срока для приоритета.
watch(() => [form.value.channel, form.value.currency], () => {
  if (!accountOptions.value.some(a => a.id === form.value.account_id)) form.value.account_id = accountOptions.value[0]?.id ?? null;
});
watch(() => form.value.priority, () => {
  if (earliest.value && (!form.value.date || form.value.date < earliest.value)) form.value.date = earliest.value;
});

// Бюджет выбранной статьи на период: лимит, использовано, резерв, доступно и остаток после заявки.
let timer = null, key = '';
watch(() => [form.value.account_id, form.value.category_id, form.value.amount, form.value.date].join('|'), value => {
  clearTimeout(timer); key = value; card.value = null;
  const {account_id, category_id, amount, date} = form.value;
  if (!account_id || !category_id || !(Number(amount) > 0) || !date) return;
  timer = setTimeout(async () => {
    try {
      const result = await props.api('/api/budget-check?' + new URLSearchParams({account_id, category_id, amount: String(amount), date}));
      if (key === value) card.value = result;
    } catch (e) { if (key === value) card.value = {error: e.message}; }
  }, 300);
}, {immediate: true});

async function digest(item) {
  // Отпечаток файла нужен, чтобы после потерянного ответа не загрузить тот же файл второй раз.
  try {
    if (!globalThis.crypto?.subtle) return;
    const hash = await crypto.subtle.digest('SHA-256', await item.file.arrayBuffer());
    item.sha256 = [...new Uint8Array(hash)].map(b => b.toString(16).padStart(2, '0')).join('');
  } catch { /* без отпечатка файл просто загрузится */ }
}

function choose(kind, event) {
  const picked = [...(event.target.files || [])];
  event.target.value = '';
  if (!picked.length) return;
  const problems = picked.map(fileProblem).filter(Boolean);
  if (problems.length) { error.value = problems.join(' '); return; }
  error.value = '';
  const items = (SINGLE_DOCS.includes(kind) ? picked.slice(0, 1) : picked)
    .map(file => ({key: 'f' + (++fileKey), kind, name: file.name, size: file.size, file, sha256: ''}));
  files.value = SINGLE_DOCS.includes(kind) ? [...files.value.filter(f => f.kind !== kind), ...items] : [...files.value, ...items];
  items.forEach(item => digest(files.value.find(f => f.key === item.key)));
}

function removePending(item) { files.value = files.value.filter(f => f.key !== item.key); }

async function removeSaved(doc) {
  error.value = ''; notice.value = '';
  try {
    await removeSavedDocument(props.api, state, doc);
    saved.value = saved.value.filter(d => d.id !== doc.id);
    notice.value = `Файл «${doc.filename}» убран из черновика.`;
  } catch (e) { error.value = e.message; }
}

function onUploaded(item, res) {
  files.value = files.value.filter(f => f.key !== item.key);
  if (!res) return;
  const doc = {id: res.id, kind: res.kind, filename: res.filename, version: res.version, url: res.url, current: true};
  saved.value = [...saved.value.filter(d => !(SINGLE_DOCS.includes(res.kind) && d.kind === res.kind)), doc];
}

function onServer(server) {
  saved.value = (server.documents_list || []).filter(d => d.current);
  status.value = server.status;
}

// Какая кнопка отправила форму: submitter, а в старых браузерах — последний клик (Enter нажимает кнопку по умолчанию).
const sendIntent = ref(false);
async function save(event) {
  if (saving.value || blocked.value) return;
  const send = event?.submitter?.dataset?.send ? event.submitter.dataset.send === '1' : sendIntent.value;
  error.value = ''; notice.value = '';
  saving.value = true; step.value = 'save';
  try {
    const result = await saveRequest(props.api, state, {
      fields: requestBody(form.value, props.company?.id), files: files.value, saved: saved.value,
      submit: send && !sentMode, reason: form.value.reason, onUploaded, onServer,
      onStep: s => { step.value = s; },
    });
    number.value = state.number; status.value = state.status;
    if (result.ok) { emit('saved', {id: result.id, number: result.number, submitted: result.submitted, resent: sentMode}); return; }
    error.value = result.message;
    if (['already_sent', 'closed', 'status_changed'].includes(result.code)) blocked.value = result.code;
    else if (state.created && result.step !== 'create') notice.value = `Черновик ${state.number} сохранён. Уже загруженные файлы показаны в блоках ниже.`;
  } finally { saving.value = false; step.value = ''; }
}

function close() { if (!saving.value) emit('close'); }

function onKey(e) {
  if (e.key === 'Escape') { close(); return; }
  if (e.key !== 'Tab' || !dialog.value) return;
  const controls = [...dialog.value.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]')]
    .filter(n => n.getClientRects().length);
  const first = controls[0], last = controls.at(-1);
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
}
onMounted(async () => {
  document.addEventListener('keydown', onKey); await nextTick(); dialog.value?.querySelector('select,input:not(:disabled)')?.focus();
  try {
    dues.value = (await props.api('/api/request-rules')).due_minimums;
    if (!r && (!form.value.date || form.value.date < earliest.value)) form.value.date = earliest.value;
  } catch { /* остаются сроки из загрузки страницы; сервер всё равно проверит дату */ }
  if (r && !r.documents_list) {
    try { saved.value = ((await props.api(`/api/requests/${r.id}`)).documents_list || []).filter(d => d.current); } catch { /* покажем без файлов */ }
  }
});
onUnmounted(() => { document.removeEventListener('keydown', onKey); clearTimeout(timer); });
</script>

<template>
  <div class="modal-backdrop">
    <form ref="dialog" class="modal rq-editor" role="dialog" aria-modal="true" aria-labelledby="rq-editor-title" @submit.prevent="save">
      <div class="section-head"><h2 id="rq-editor-title">{{ title }}</h2>
        <button type="button" class="ghost icon-btn" aria-label="Закрыть" :disabled="saving" @click="close">✕</button></div>
      <p v-if="sentMode" class="form-note">Заявка на согласовании. После сохранения она останется на согласовании, а проверка и согласования начнутся заново. Новый файл заменит прежний (прежняя версия останется в истории); убрать файл можно в карточке заявки с причиной.</p>
      <p v-else class="form-note">«Сохранить черновик» — без отправки, файлы можно приложить позже. «Отправить на согласование» — сохранить, загрузить файлы и отправить: нужны внутренняя заявка (индент) и договор или счёт. Автор и последний редактор не согласуют свою заявку.</p>
      <div class="form-grid">
        <label>Компания<input :value="company?.name" disabled></label>
        <label>Канал оплаты<select v-model="form.channel" required><option v-for="(label, k) in CHANNELS" :key="k" :value="k">{{ label }}</option></select></label>
        <label>Валюта<select v-model="form.currency" required><option v-for="c in ['UZS','USD','EUR']" :key="c" :value="c">{{ c }}</option></select></label>
        <label>Счёт списания<select v-model="form.account_id" required><option v-for="a in accountOptions" :key="a.id" :value="a.id">{{ a.name }}</option></select>
          <small v-if="!accountOptions.length" class="field-error">Нет счёта для выбранного канала и валюты.</small></label>
        <label>Статья расходов<select v-model="form.category_id" required><option v-for="c in outcomeCategories" :key="c.id" :value="c.id">{{ c.name }}</option></select></label>
        <label>Сумма<input v-model="form.amount" type="text" inputmode="decimal" pattern="[0-9]+([.][0-9]{1,2})?" required autocomplete="off"></label>
        <label>Контрагент<input v-model="form.counterparty" minlength="2" maxlength="160" required></label>
        <label>Приоритет<select v-model="form.priority" required><option v-for="(label, k) in PRIORITIES" :key="k" :value="k">{{ label }} · от {{ LEAD_DAYS[k] }} раб. дн.</option></select></label>
        <label>Плановая дата оплаты<input v-model="form.date" type="date" :min="earliest" required aria-describedby="rq-date-hint">
          <small id="rq-date-hint" class="sub">Не раньше {{ earliestLabel }}: сегодняшняя дата не допускается.</small></label>
        <label class="wide">Назначение платежа<textarea v-model="form.purpose" minlength="5" maxlength="3000" required></textarea></label>
        <label v-if="needsReason" class="wide">Причина изменения<textarea v-model="form.reason" minlength="10" maxlength="1000" required></textarea></label>
      </div>

      <section v-if="card" class="budget-check" :class="card.status" aria-live="polite">
        <strong>Бюджет статьи · {{ periodLabel(card.period) }}</strong>
        <p v-if="card.error">{{ card.error }}</p>
        <dl v-else-if="card.budget_set" class="budget-grid">
          <template v-for="[label, value] in budgetRows(card)" :key="label"><dt>{{ label }}</dt><dd><b>{{ money(value) }}</b> {{ card.currency }}</dd></template>
        </dl>
        <p v-if="!card.error && budgetNote(card)" class="sub">{{ budgetNote(card) }}</p>
      </section>

      <section class="request-documents-placeholder" aria-labelledby="rq-docs-title">
        <strong id="rq-docs-title">Документы к заявке</strong>
        <p>{{ sentMode ? 'Первые два раздела обязательны.' : 'Черновик можно сохранить без файлов. Для отправки обязательны первые два раздела.' }} PDF, PNG, JPEG, DOCX или XLSX, до 5 МБ на файл.</p>
        <div class="request-document-grid">
          <div v-for="(label, kind) in DOC_KINDS" :key="kind" class="request-document-slot">
            <b>{{ label }} <span v-if="REQUIRED_DOCS.includes(kind)" class="required-mark" aria-label="обязательно">*</span></b>
            <span v-for="doc in savedOf(kind)" :key="doc.id" class="saved-file">
              <a :href="companyUrl(doc.url)">{{ doc.filename }}</a><small>версия {{ doc.version }}{{ replacing(kind) ? ' · будет заменён' : '' }}</small>
              <button v-if="canRemoveSaved" type="button" class="ghost tiny" :disabled="saving" @click="removeSaved(doc)">Убрать</button>
            </span>
            <span v-for="f in pendingOf(kind)" :key="f.key" class="pending-file">{{ f.name }}<small>{{ fileSize(f.size) }} · не загружен</small>
              <button type="button" class="ghost tiny" :disabled="saving" @click="removePending(f)">Убрать</button></span>
            <label class="button secondary tiny file-btn">{{ pickLabel(kind) }}<input type="file" :accept="DOC_ACCEPT" :multiple="kind === 'other'" :disabled="saving || !!blocked" @change="choose(kind, $event)"></label>
          </div>
        </div>
      </section>

      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <p v-if="saving && step" class="sub" role="status">{{ STEPS[step] }}</p>
      <div class="form-actions">
        <button type="button" class="secondary" :disabled="saving" @click="close">{{ blocked ? 'Закрыть' : 'Отмена' }}</button>
        <button v-if="blocked" type="button" @click="emit('open-card', state.id)">Открыть карточку заявки</button>
        <template v-else-if="sentMode">
          <button type="submit" data-send="1" :disabled="saving || !accountOptions.length" @click="sendIntent = true">{{ saving ? 'Сохраняем…' : 'Сохранить изменения' }}</button>
        </template>
        <template v-else>
          <button type="submit" class="secondary" data-send="0" :disabled="saving || !accountOptions.length" @click="sendIntent = false">Сохранить черновик</button>
          <button type="submit" data-send="1" :disabled="saving || !accountOptions.length" @click="sendIntent = true" :aria-describedby="missing.length ? 'rq-missing' : undefined">{{ saving ? 'Сохраняем…' : 'Отправить на согласование' }}</button>
        </template>
      </div>
      <p v-if="!blocked && missing.length" id="rq-missing" class="sub rq-missing">Для отправки не хватает: {{ missing.map(k => DOC_KINDS[k]).join(', ') }}.</p>
    </form>
  </div>
</template>
