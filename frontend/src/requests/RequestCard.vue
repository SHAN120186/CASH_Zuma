<script setup>
// Карточка заявки: маршрут, бюджет статьи, документы с версиями, решения из r.actions и история.
import {ref, computed, onMounted, onUnmounted, nextTick} from 'vue';
import {PRIORITIES, CHANNELS, DOC_KINDS, REQUIRED_DOCS, SINGLE_DOCS, DOC_ACCEPT, COMMENT_REQUIRED, stageLabel, steps, decisionOptions,
        missingDocs, budgetRows, budgetNote, periodLabel, minDue, historyLabel, historyNote, historyFile, fileProblem} from './workflow.js';

const props = defineProps({
  api: {type: Function, required: true},
  id: {type: Number, required: true},
  companyUrl: {type: Function, required: true},
  money: {type: Function, required: true},
  dues: {type: Object, default: () => ({})},
});
const emit = defineEmits(['close', 'changed', 'edit', 'record-payment']);

const r = ref(null), error = ref(''), busy = ref(false), notice = ref(''), dialog = ref(null), rules = ref(props.dues);
const decision = ref({action: '', note: '', date: ''}), showHistory = ref({}), removing = ref(null), removeReason = ref('');
const MK = {done: '✓', now: '●', bad: '✕', skip: '–', '': ''};
const PILL = {pending: 'warn', approved: 'good', paid: 'fact', returned: 'warn', cancelled: 'gray', draft: 'gray', rejected: 'bad'};

const can = a => (r.value?.actions || []).includes(a);
const options = computed(() => r.value ? decisionOptions(r.value) : []);
const needsComment = computed(() => COMMENT_REQUIRED.includes(decision.value.action));
const byKind = computed(() => {
  const all = r.value?.documents_list || [];
  return Object.fromEntries(Object.keys(DOC_KINDS).map(k => [k, {
    current: all.filter(d => d.kind === k && d.current),
    history: all.filter(d => d.kind === k && !d.current).sort((a, b) => b.version - a.version || b.id - a.id),
  }]));
});
const missing = computed(() => r.value ? missingDocs(r.value) : []);
// До отправки файл убирается сразу; после отправки — только прочий документ и с причиной
// (обязательный заменяется новой версией).
const draftLike = computed(() => ['draft', 'returned'].includes(r.value?.status));
const canRemove = kind => can('documents') && (draftLike.value || !REQUIRED_DOCS.includes(kind));

async function load(afterAction = false) {
  try {
    r.value = await props.api(`/api/requests/${props.id}`);
    if (!options.value.some(o => o[0] === decision.value.action)) decision.value = {action: options.value[0]?.[0] || '', note: '', date: r.value.date};
  } catch (e) {
    // Действие закрыло заявку для этого сотрудника (например, плательщик вернул её финансовому директору):
    // карточка закрывается, список обновляется, прежние кнопки не остаются.
    if (afterAction && e.status === 404) { emit('changed'); emit('close'); return; }
    error.value = e.message;
  }
}

// После действия карточка перечитывается; при конфликте версий — тоже, с сообщением сервера.
// fn может вернуть строку — уточнённое сообщение вместо общего.
async function run(fn, message) {
  busy.value = true; error.value = ''; notice.value = '';
  try {
    const extra = await fn();
    await load(true); notice.value = typeof extra === 'string' && extra ? extra : message; emit('changed'); return true;
  } catch (e) {
    error.value = e.message + ([409, 428].includes(e.status) ? ' Карточка обновлена.' : '');
    if ([409, 428].includes(e.status)) { await load(true); emit('changed'); }
    return false;
  } finally { busy.value = false; }
}

const json = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});

function submit() {
  run(() => props.api(`/api/requests/${r.value.id}/decision`, json({action: 'submit', version: r.value.version})), 'Заявка отправлена на согласование.');
}

async function decide() {
  const {action, note, date} = decision.value;
  const ok = await run(() => props.api(`/api/requests/${r.value.id}/decision`, json({action, note, date: action === 'reschedule' ? date : null, version: r.value.version})),
    action === 'close' ? 'Заявка закрыта без оплаты.' : action === 'return' ? 'Заявка возвращена на доработку.' : 'Решение сохранено.');
  if (ok) decision.value = {action: options.value[0]?.[0] || '', note: '', date: r.value?.date || ''};
}

function upload(kind, event) {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file) return;
  const problem = fileProblem(file);
  if (problem) { error.value = problem; notice.value = ''; return; }
  const replaces = SINGLE_DOCS.includes(kind) && byKind.value[kind].current.length > 0;
  run(async () => {
    // Версия заявки обязательна после отправки: сервер отклонит загрузку по устаревшей карточке.
    const res = await props.api(`/api/requests/${r.value.id}/documents?kind=${kind}`, {method: 'POST', body: file,
      headers: {'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name), 'X-Request-Version': String(r.value.version)}});
    return res.approval_reset ? 'Документ изменён после отправки: проверка и согласования начаты заново.' : '';
  }, replaces ? 'Загружена новая версия документа; прежняя осталась в истории.' : 'Документ приложен.');
}

function remove(doc) {
  if (draftLike.value) {
    run(() => props.api(`/api/request-documents/${doc.id}`, {method: 'DELETE', headers: {'X-Request-Version': String(r.value.version)}}), 'Документ убран.');
    return;
  }
  run(async () => {
    const res = await props.api(`/api/request-documents/${doc.id}/remove`, json({version: r.value.version, reason: removeReason.value}));
    return res.approval_reset ? 'Документ убран: проверка и согласования начаты заново.' : '';
  }, 'Документ убран.').then(ok => { if (ok) { removing.value = null; removeReason.value = ''; } });
}

const when = s => s ? new Date(s.replace(' ', 'T') + (/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? '' : 'Z')).toLocaleString('ru-RU', {timeZone: 'Asia/Tashkent', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'}) : '';
const dateRu = d => d ? d.split('-').reverse().join('.') : '';

function close() { if (!busy.value) emit('close'); }
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
  document.addEventListener('keydown', onKey); await load(); await nextTick(); dialog.value?.querySelector('button')?.focus();
  try { rules.value = (await props.api('/api/request-rules')).due_minimums; } catch { /* сервер проверит дату переноса */ }
});
onUnmounted(() => document.removeEventListener('keydown', onKey));
</script>

<template>
  <div class="modal-backdrop">
    <section ref="dialog" class="modal rq-sheet" role="dialog" aria-modal="true" aria-labelledby="rq-card-title">
      <div class="section-head"><h2 id="rq-card-title">{{ r ? r.number + ' · ' + r.counterparty : 'Заявка' }}</h2>
        <button type="button" class="ghost icon-btn" aria-label="Закрыть" :disabled="busy" @click="close">✕</button></div>
      <p v-if="!r && !error" class="sub" role="status">Загрузка заявки…</p>
      <template v-if="r">
        <div class="rq-head">
          <span class="pill" :class="PILL[r.status]">{{ stageLabel(r) }}</span>
          <span v-if="r.overdue" class="pill bad">Срок оплаты прошёл</span>
          <b class="num rq-amount">{{ money(r.amount) }} <small>{{ r.currency }}</small></b>
        </div>
        <div class="trk" aria-label="Этапы заявки"><template v-for="(s, i) in steps(r)" :key="i"><span v-if="i" class="ln"></span>
          <span class="st" :class="s.c === 'skip' ? 'skip' : s.c"><u>{{ MK[s.c] }}</u>{{ s.l }}</span></template></div>
        <p class="sub rq-route">Маршрут: проверка бухгалтера → финансовый директор → директор по политике статьи → оплата. {{ r.route?.reason }}</p>

        <dl class="kv rq-kv">
          <dt>Статья</dt><dd>{{ r.category }}</dd>
          <dt>Канал и счёт</dt><dd>{{ CHANNELS[r.channel] || r.channel }} · {{ r.account }}</dd>
          <dt>Плановая дата</dt><dd>{{ dateRu(r.date) }} · {{ PRIORITIES[r.priority] }} приоритет</dd>
          <dt>Назначение</dt><dd>{{ r.purpose }}</dd>
          <template v-if="r.project"><dt>Проект</dt><dd>{{ r.project }}</dd></template>
          <dt>Автор</dt><dd>{{ r.people?.creator_id }}</dd>
          <template v-if="r.people?.last_editor_id && r.last_editor_id !== r.creator_id"><dt>Последний редактор</dt><dd>{{ r.people.last_editor_id }}</dd></template>
          <template v-if="r.people?.checked_by"><dt>Проверил</dt><dd>{{ r.people.checked_by }}</dd></template>
          <template v-if="r.people?.finance_approved_by"><dt>Фин. директор</dt><dd>{{ r.people.finance_approved_by }}</dd></template>
          <template v-if="r.people?.approved_by && r.approved_by !== r.finance_approved_by"><dt>Директор</dt><dd>{{ r.people.approved_by }}</dd></template>
          <template v-if="r.decision_note"><dt>Комментарий</dt><dd>{{ r.decision_note }}</dd></template>
        </dl>

        <section v-if="r.budget_card" class="budget-check" :class="r.budget_card.status" aria-label="Бюджет статьи">
          <strong>Бюджет статьи «{{ r.category }}» · {{ periodLabel(r.budget_card.period) }}</strong>
          <dl v-if="r.budget_card.budget_set" class="budget-grid">
            <template v-for="[label, value] in budgetRows(r.budget_card)" :key="label"><dt>{{ label }}</dt><dd><b>{{ money(value) }}</b> {{ r.currency }}</dd></template>
          </dl>
          <p v-if="budgetNote(r.budget_card)" class="sub">{{ budgetNote(r.budget_card) }}</p>
        </section>

        <h3 class="rq-sec">Документы</h3>
        <p v-if="missing.length && draftLike" class="warning">Для отправки не хватает: {{ missing.map(k => DOC_KINDS[k]).join(', ') }}.</p>
        <div class="rq-docs">
          <section v-for="(label, kind) in DOC_KINDS" :key="kind" class="rq-doc">
            <div class="rq-doc-h"><b>{{ label }}</b><span class="pill" :class="REQUIRED_DOCS.includes(kind) ? (byKind[kind].current.length ? 'good' : 'warn') : 'gray'">{{ REQUIRED_DOCS.includes(kind) ? 'Обязательно' : 'Необязательно' }}</span></div>
            <ul v-if="byKind[kind].current.length" class="rq-files">
              <li v-for="d in byKind[kind].current" :key="d.id">
                <a :href="companyUrl(d.url)">{{ d.filename }}</a><small>версия {{ d.version }} · {{ d.created_by }} · {{ when(d.created_at) }}</small>
                <span v-if="canRemove(kind)" class="actions">
                  <button type="button" class="ghost tiny" :disabled="busy" @click="draftLike ? remove(d) : (removing = d.id)">Убрать</button>
                </span>
                <form v-if="removing === d.id && !draftLike" class="rq-inline" @submit.prevent="remove(d)"><input v-model="removeReason" minlength="10" maxlength="1000" required placeholder="Причина, не короче 10 символов" aria-label="Причина">
                  <button class="tiny" :disabled="busy">Убрать</button><button type="button" class="ghost tiny" @click="removing = null">Назад</button></form>
              </li>
            </ul>
            <p v-else class="sub">Файл не приложен.</p>
            <label v-if="can('documents')" class="button secondary tiny file-btn">{{ SINGLE_DOCS.includes(kind) && byKind[kind].current.length ? 'Заменить файл' : 'Приложить файл' }}<input type="file" :accept="DOC_ACCEPT" :disabled="busy" @change="upload(kind, $event)"></label>
            <button v-if="byKind[kind].history.length" type="button" class="ghost tiny" :aria-expanded="!!showHistory[kind]" @click="showHistory[kind] = !showHistory[kind]">{{ showHistory[kind] ? 'Скрыть прежние версии' : 'Прежние версии: ' + byKind[kind].history.length }}</button>
            <ul v-if="showHistory[kind]" class="rq-files old">
              <li v-for="d in byKind[kind].history" :key="d.id"><a :href="companyUrl(d.url)">{{ d.filename }}</a><small>версия {{ d.version }} · {{ SINGLE_DOCS.includes(kind) ? 'заменена или убрана' : 'убран' }} · {{ d.created_by }} · {{ when(d.created_at) }}</small></li>
            </ul>
          </section>
        </div>
        <p class="sub">PDF, PNG, JPEG, DOCX или XLSX до 5 МБ. После отправки новая версия документа или удаление прочего документа начинают проверку и согласования заново.</p>

        <div class="form-actions rq-actions">
          <button v-if="can('edit')" type="button" class="secondary" :disabled="busy" @click="emit('edit', r)">Изменить</button>
          <button v-if="can('submit')" type="button" :disabled="busy || missing.length > 0" @click="submit">Отправить на согласование</button>
          <button v-if="can('pay')" type="button" :disabled="busy" @click="emit('record-payment', r)">Факт оплаты</button>
        </div>

        <form v-if="options.length" class="rq-decision card" @submit.prevent="decide">
          <h3>Решение</h3>
          <div class="form-grid">
            <label>Действие<select v-model="decision.action" required><option v-for="o in options" :key="o[0]" :value="o[0]">{{ o[1] }}</option></select></label>
            <label v-if="decision.action === 'reschedule'">Новая дата<input v-model="decision.date" type="date" :min="minDue(rules, r.priority)" required></label>
            <label class="wide">{{ needsComment ? 'Комментарий (обязательно, от 10 символов)' : 'Комментарий' }}<textarea v-model="decision.note" :minlength="needsComment ? 10 : 0" :required="needsComment" maxlength="2000"></textarea></label>
          </div>
          <p v-if="decision.action === 'close'" class="sub">Заявка завершится без оплаты и останется в истории со статусом «Закрыта без оплаты».</p>
          <div class="form-actions"><button type="submit" :disabled="busy">{{ busy ? 'Сохраняем…' : 'Подтвердить решение' }}</button></div>
        </form>

        <p v-if="notice" class="notice" role="status">{{ notice }}</p>
        <p v-if="error" class="error" role="alert">{{ error }}</p>

        <h3 class="rq-sec">История</h3>
        <ol class="rq-history">
          <li v-for="h in r.history" :key="h.id"><time>{{ when(h.at) }}</time><b>{{ historyLabel(h.action) }}</b>
            <span>{{ h.user }}{{ h.role ? ' · ' + h.role : '' }}{{ h.acting_for ? ' · ВрИО за ' + h.acting_for : '' }}{{ h.ip ? ' · IP ' + h.ip : '' }}</span>
            <small v-if="historyFile(h.detail)">{{ historyFile(h.detail) }}</small>
            <q v-if="historyNote(h.detail)">{{ historyNote(h.detail) }}</q></li>
        </ol>
      </template>
      <p v-else-if="error" class="error" role="alert">{{ error }}</p>
    </section>
  </div>
</template>
