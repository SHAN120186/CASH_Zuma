<script setup>
import {ref, computed, onMounted, onUnmounted, nextTick} from 'vue';
import {STATUS, PRIORITIES, CHANNELS, DOC_KINDS, REQUIRED_DOCS, COMMENT_REQUIRED, stageLabel, steps, decisionOptions,
        missingDocs, budgetRows, budgetNote, periodLabel, fileSize, minDue, historyLabel, historyNote} from './workflow.js';

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

const can = a => (r.value?.actions || []).includes(a);
const options = computed(() => r.value ? decisionOptions(r.value) : []);
const needsComment = computed(() => COMMENT_REQUIRED.includes(decision.value.action));
const byKind = computed(() => {
  const all = r.value?.documents_list || [];
  return Object.fromEntries(Object.keys(DOC_KINDS).map(k => [k, {
    current: all.filter(d => d.kind === k && d.current),
    history: all.filter(d => d.kind === k && !d.current),
  }]));
});
const missing = computed(() => r.value ? missingDocs(r.value) : []);

async function load() {
  error.value = '';
  try {
    r.value = await props.api(`/api/requests/${props.id}`);
    if (!options.value.some(o => o[0] === decision.value.action)) decision.value = {action: options.value[0]?.[0] || '', note: '', date: r.value.date};
  } catch (e) { error.value = e.message; }
}

async function run(fn, message) {
  busy.value = true; error.value = ''; notice.value = '';
  try { await fn(); await load(); notice.value = message; emit('changed'); } catch (e) { error.value = e.message; } finally { busy.value = false; }
}

const json = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});

function submit() {
  run(() => props.api(`/api/requests/${r.value.id}/decision`, json({action: 'submit', version: r.value.version})), 'Заявка отправлена на согласование.');
}

function decide() {
  const {action, note, date} = decision.value;
  run(() => props.api(`/api/requests/${r.value.id}/decision`, json({action, note, date: action === 'reschedule' ? date : null, version: r.value.version})),
      'Решение сохранено.');
}

function upload(kind, event, replaces = null) {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file) return;
  const query = `kind=${kind}` + (replaces ? `&replaces=${replaces}` : '');
  run(async () => {
    const res = await props.api(`/api/requests/${r.value.id}/document?${query}`, {method: 'POST',
      headers: {'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name), 'X-Request-Version': String(r.value.version)}, body: file});
    if (res.approval_reset) notice.value = 'Документ изменён после отправки: согласование начато заново.';
  }, replaces ? 'Загружена новая версия документа.' : 'Документ приложен.');
}

function remove(doc) {
  run(() => props.api(`/api/request-documents/${doc.id}/remove`, json({version: r.value.version, reason: removeReason.value})), 'Документ убран.')
    .then(() => { removing.value = null; removeReason.value = ''; });
}

const when = s => s ? new Date(s.replace(' ', 'T') + 'Z').toLocaleString('ru-RU', {timeZone: 'Asia/Tashkent', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'}) : '';
const dateRu = d => d ? d.split('-').reverse().join('.') : '';

function onKey(e) { if (e.key === 'Escape' && !busy.value) emit('close'); }
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
        <button type="button" class="ghost icon-btn" aria-label="Закрыть" :disabled="busy" @click="emit('close')">✕</button></div>
      <p v-if="!r && !error" class="sub" role="status">Загрузка заявки…</p>
      <template v-if="r">
        <div class="rq-head">
          <span class="pill" :class="{pending: 'warn', approved: 'good', paid: 'fact', returned: 'warn', cancelled: 'gray', draft: 'gray', rejected: 'bad'}[r.status]">{{ stageLabel(r) }}</span>
          <b class="num rq-amount">{{ money(r.amount) }} <small>{{ r.currency }}</small></b>
        </div>
        <div class="trk" aria-label="Этапы заявки"><template v-for="(s, i) in steps(r)" :key="i"><span v-if="i" class="ln"></span>
          <span class="st" :class="s.c === 'skip' ? 'skip' : s.c"><u>{{ MK[s.c] }}</u>{{ s.l }}</span></template></div>
        <p class="sub rq-route">{{ r.route.reason }}</p>

        <dl class="kv rq-kv">
          <dt>Статья</dt><dd>{{ r.category }}</dd>
          <dt>Канал и счёт</dt><dd>{{ CHANNELS[r.channel] }} · {{ r.account }}</dd>
          <dt>Плановая дата</dt><dd>{{ dateRu(r.date) }} · {{ PRIORITIES[r.priority] }} приоритет</dd>
          <dt>Назначение</dt><dd>{{ r.purpose }}</dd>
          <dt>Автор</dt><dd>{{ r.people.creator_id }}</dd>
          <dt v-if="r.people.checked_by">Проверил</dt><dd v-if="r.people.checked_by">{{ r.people.checked_by }}</dd>
          <dt v-if="r.people.finance_approved_by">Фин. директор</dt><dd v-if="r.people.finance_approved_by">{{ r.people.finance_approved_by }}</dd>
          <dt v-if="r.people.approved_by">Директор</dt><dd v-if="r.people.approved_by">{{ r.people.approved_by }}</dd>
          <dt v-if="r.decision_note">Комментарий</dt><dd v-if="r.decision_note">{{ r.decision_note }}</dd>
        </dl>

        <section class="budget-check" :class="r.budget_card.status" aria-label="Бюджет статьи">
          <strong>Бюджет статьи «{{ r.category }}» · {{ periodLabel(r.budget_card.period) }}</strong>
          <dl v-if="r.budget_card.budget_set" class="budget-grid">
            <template v-for="[label, value] in budgetRows(r.budget_card)" :key="label"><dt>{{ label }}</dt><dd><b>{{ money(value) }}</b> {{ r.currency }}</dd></template>
          </dl>
          <p v-if="budgetNote(r.budget_card)" class="sub">{{ budgetNote(r.budget_card) }}</p>
        </section>

        <h3 class="rq-sec">Документы</h3>
        <p v-if="missing.length && ['draft', 'returned'].includes(r.status)" class="warning">Для отправки не хватает: {{ missing.map(k => DOC_KINDS[k]).join(', ') }}.</p>
        <div class="rq-docs">
          <section v-for="(label, kind) in DOC_KINDS" :key="kind" class="rq-doc">
            <div class="rq-doc-h"><b>{{ label }}</b><span class="pill" :class="REQUIRED_DOCS.includes(kind) ? (byKind[kind].current.length ? 'good' : 'warn') : 'gray'">{{ REQUIRED_DOCS.includes(kind) ? 'Обязательно' : 'Необязательно' }}</span></div>
            <ul v-if="byKind[kind].current.length" class="rq-files">
              <li v-for="d in byKind[kind].current" :key="d.id">
                <a :href="companyUrl(d.url)">{{ d.filename }}</a><small>версия {{ d.version }} · {{ fileSize(d.size) }} · {{ d.created_by }}</small>
                <span v-if="can('documents')" class="actions">
                  <label class="button secondary tiny file-btn">Заменить<input type="file" accept=".pdf,.png,.jpg,.jpeg,.docx,.xlsx" :disabled="busy" @change="upload(kind, $event, d.id)"></label>
                  <button v-if="kind === 'other'" type="button" class="ghost tiny" :disabled="busy" @click="removing = d.id">Убрать</button>
                </span>
                <form v-if="removing === d.id" class="rq-inline" @submit.prevent="remove(d)"><input v-model="removeReason" minlength="10" required placeholder="Причина, не короче 10 символов" aria-label="Причина">
                  <button class="tiny" :disabled="busy">Убрать</button><button type="button" class="ghost tiny" @click="removing = null">Отмена</button></form>
              </li>
            </ul>
            <p v-else class="sub">Файл не приложен.</p>
            <label v-if="can('documents') && (kind === 'other' || !byKind[kind].current.length)" class="button secondary tiny file-btn">Приложить файл<input type="file" accept=".pdf,.png,.jpg,.jpeg,.docx,.xlsx" :disabled="busy" @change="upload(kind, $event)"></label>
            <button v-if="byKind[kind].history.length" type="button" class="ghost tiny" @click="showHistory[kind] = !showHistory[kind]">{{ showHistory[kind] ? 'Скрыть прежние версии' : 'Прежние версии: ' + byKind[kind].history.length }}</button>
            <ul v-if="showHistory[kind]" class="rq-files old">
              <li v-for="d in byKind[kind].history" :key="d.id"><a :href="companyUrl(d.url)">{{ d.filename }}</a><small>версия {{ d.version }}{{ d.removed ? ' · убран' : '' }} · {{ when(d.created_at) }}</small></li>
            </ul>
          </section>
        </div>
        <p class="sub">PDF, PNG, JPEG, DOCX или XLSX до 5 МБ. После отправки замена файла сбрасывает согласование и начинает его заново.</p>

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
            <label class="wide">{{ needsComment ? 'Комментарий (обязательно)' : 'Комментарий' }}<textarea v-model="decision.note" :minlength="needsComment ? 10 : 0" :required="needsComment" maxlength="2000"></textarea></label>
          </div>
          <div class="form-actions"><button type="submit" :disabled="busy">{{ busy ? 'Сохраняем…' : 'Подтвердить решение' }}</button></div>
        </form>

        <p v-if="notice" class="notice" role="status">{{ notice }}</p>
        <p v-if="error" class="error" role="alert">{{ error }}</p>

        <h3 class="rq-sec">История</h3>
        <ol class="rq-history">
          <li v-for="h in r.history" :key="h.id"><time>{{ when(h.at) }}</time><b>{{ historyLabel(h.action) }}</b>
            <span>{{ h.user }}{{ h.role ? ' · ' + h.role : '' }}{{ h.acting_for ? ' · ВрИО за ' + h.acting_for : '' }}{{ h.ip ? ' · IP ' + h.ip : '' }}</span>
            <q v-if="historyNote(h.detail)">{{ historyNote(h.detail) }}</q></li>
        </ol>
      </template>
      <p v-else-if="error" class="error" role="alert">{{ error }}</p>
    </section>
  </div>
</template>
