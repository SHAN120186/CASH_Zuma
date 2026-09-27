<script setup>
import {ref, computed, watch, onMounted, onUnmounted, nextTick} from 'vue';
import {PRIORITIES, LEAD_DAYS, CHANNELS, accountsFor, minDue, budgetRows, budgetNote, periodLabel} from './workflow.js';

const props = defineProps({
  api: {type: Function, required: true},
  boot: {type: Object, required: true},
  company: {type: Object, default: null},
  request: {type: Object, default: null},
  currency: {type: String, default: 'UZS'},
  money: {type: Function, required: true},
});
const emit = defineEmits(['close', 'saved']);

const r = props.request;
const firstAccount = props.boot.accounts.find(a => a.currency === props.currency) || props.boot.accounts[0];
const form = ref(r ? {
  channel: r.channel || r.account_kind, currency: r.currency, account_id: r.account_id, category_id: r.category_id,
  amount: r.amount, counterparty: r.counterparty, purpose: r.purpose, priority: r.priority, date: r.date, project: r.project || '', reason: '',
} : {
  channel: firstAccount?.kind || 'bank', currency: firstAccount?.currency || props.currency, account_id: firstAccount?.id ?? null,
  category_id: props.boot.categories.find(c => c.type === 'outcome')?.id ?? null, amount: '', counterparty: '', purpose: '',
  priority: 'normal', date: minDue(props.boot.due_minimums, 'normal'), project: '',
});
const error = ref(''), saving = ref(false), card = ref(null), dialog = ref(null);
// Сроки считает сервер по календарю; при открытии формы берём свежие (календарь мог измениться).
const dues = ref(props.boot.due_minimums || {});

const accountOptions = computed(() => accountsFor(props.boot.accounts, form.value.channel, form.value.currency));
const outcomeCategories = computed(() => props.boot.categories.filter(c => c.type === 'outcome'));
const earliest = computed(() => minDue(dues.value, form.value.priority));
const earliestLabel = computed(() => earliest.value ? earliest.value.split('-').reverse().join('.') : '');

// Счёт подбирается под канал и валюту; дата не раньше срока для приоритета.
watch(() => [form.value.channel, form.value.currency], () => {
  if (!accountOptions.value.some(a => a.id === form.value.account_id)) form.value.account_id = accountOptions.value[0]?.id ?? null;
});
watch(() => form.value.priority, () => {
  if (earliest.value && (!form.value.date || form.value.date < earliest.value)) form.value.date = earliest.value;
});

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

async function save() {
  saving.value = true; error.value = '';
  const body = {
    company_id: props.company?.id, account_id: form.value.account_id, channel: form.value.channel, currency: form.value.currency,
    category_id: form.value.category_id, amount: String(form.value.amount), counterparty: form.value.counterparty,
    purpose: form.value.purpose, priority: form.value.priority, date: form.value.date, project: form.value.project || '', status: 'draft',
  };
  try {
    const saved = r
      ? await props.api(`/api/requests/${r.id}`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...body, version: r.version, reason: form.value.reason})})
      : await props.api('/api/requests', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    emit('saved', saved);
  } catch (e) { error.value = e.message; } finally { saving.value = false; }
}

function onKey(e) { if (e.key === 'Escape' && !saving.value) emit('close'); }
onMounted(async () => {
  document.addEventListener('keydown', onKey); await nextTick(); dialog.value?.querySelector('select,input')?.focus();
  try {
    dues.value = (await props.api('/api/request-rules')).due_minimums;
    if (!r && (!form.value.date || form.value.date < earliest.value)) form.value.date = earliest.value;
  } catch { /* остаются сроки из загрузки страницы; сервер всё равно проверит дату */ }
});
onUnmounted(() => { document.removeEventListener('keydown', onKey); clearTimeout(timer); });
</script>

<template>
  <div class="modal-backdrop">
    <form ref="dialog" class="modal rq-editor" role="dialog" aria-modal="true" aria-labelledby="rq-editor-title" @submit.prevent="save">
      <div class="section-head"><h2 id="rq-editor-title">{{ r ? 'Изменить ' + r.number : 'Новая заявка на оплату' }}</h2>
        <button type="button" class="ghost icon-btn" aria-label="Закрыть" :disabled="saving" @click="emit('close')">✕</button></div>
      <p class="form-note">Заявка сохраняется черновиком. Затем в карточке заявки приложите внутреннюю заявку (индент) и договор или счёт и отправьте её на согласование.</p>
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
        <label v-if="r" class="wide">Причина изменения<textarea v-model="form.reason" minlength="10" maxlength="1000" required></textarea></label>
      </div>
      <section v-if="card" class="budget-check" :class="card.status" aria-live="polite">
        <strong>Бюджет статьи · {{ periodLabel(card.period) }}</strong>
        <p v-if="card.error">{{ card.error }}</p>
        <dl v-else-if="card.budget_set" class="budget-grid">
          <template v-for="[label, value] in budgetRows(card)" :key="label"><dt>{{ label }}</dt><dd><b>{{ money(value) }}</b> {{ card.currency }}</dd></template>
        </dl>
        <p v-if="!card.error && budgetNote(card)" class="sub">{{ budgetNote(card) }}</p>
      </section>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <div class="form-actions"><button type="button" class="secondary" :disabled="saving" @click="emit('close')">Отмена</button>
        <button type="submit" :disabled="saving || !accountOptions.length">{{ saving ? 'Сохраняем…' : 'Сохранить черновик' }}</button></div>
    </form>
  </div>
</template>
