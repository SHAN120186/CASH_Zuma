<script setup>
// Bank statement data stays separate from the existing cash ledger until the
// server has validated the source and the operator confirms the account.
import {ref, computed, watch, onMounted, onUnmounted} from 'vue';
import {tx, N_} from './i18n/index.js';
import {toCents, formatCents, formatShortDate} from './overview/format.js';

const props = defineProps({
  api: {type: Function, required: true},
  companyId: {type: Number, required: true},
  accounts: {type: Array, default: () => []},
  canImport: {type: Boolean, default: false},
  canReview: {type: Boolean, default: false},
});
const emit = defineEmits(['changed']);
const accountId = ref(''), currency = ref(''), preview = ref(null), busy = ref(false), error = ref(''), notice = ref('');
const confirmAccount = ref(false), confirmCurrency = ref(false), reviews = ref([]), reviewError = ref('');
const reviewActions = ref({}), matchActions = ref({}), reviewBusy = ref(null);
const reviewsLoaded = ref(false), uncertain = ref(false), fileInput = ref(null);
const bankAccounts = computed(() => props.accounts.filter(a => a.kind === 'bank' && !a.archived));
const account = computed(() => bankAccounts.value.find(a => String(a.id) === String(accountId.value)));
const validSelection = computed(() => !!account.value && !!currency.value && account.value.currency === currency.value);
const rows = computed(() => Array.isArray(preview.value?.rows) ? preview.value.rows : []);
const count = status => rows.value.filter(row => row.status === status).length;
const ready = computed(() => !!preview.value && preview.value.status === 'preview' && !!preview.value.id && !!preview.value.statement?.account_number && validSelection.value && confirmAccount.value && confirmCurrency.value
  && preview.value.statement?.ready === true && preview.value.statement?.balance_matches === true && preview.value.statement?.declared_totals_match !== false && !uncertain.value);
const money = (value, cur = currency.value) => formatCents(toCents(value), cur || 'UZS');
const labels = Object.freeze({
  auto_pay_candidate: N_('Точное совпадение с заявкой'),
  paid_match: N_('Оплата уже внесена на сайте'),
  review: N_('Нужна проверка бухгалтера'),
  duplicate: N_('Уже загружено'),
  ambiguous_duplicate: N_('Возможный повтор — нужна проверка'),
});
const label = value => tx(labels[value] || N_('Нужна проверка бухгалтера'));
const candidateStatus = value => tx(value === 'approved' ? N_('Утверждена') : value === 'paid' ? N_('Оплачена') : N_('Не определено'));
const directionLabel = value => tx(value === 'in' ? N_('Поступление') : value === 'out' ? N_('Списание') : N_('Не определено'));
let generation = 0, reviewGeneration = 0;

function invalidate() {
  generation += 1;
  preview.value = null; confirmAccount.value = false; confirmCurrency.value = false;
  error.value = ''; notice.value = ''; uncertain.value = false; busy.value = false;
}
watch(() => [accountId.value, currency.value], invalidate);
watch(() => props.companyId, () => { invalidate(); reviewGeneration += 1; reviews.value = []; reviewsLoaded.value = false; loadReviews(); });

async function loadReviews() {
  const current = ++reviewGeneration;
  try {
    const result = await props.api('/api/bank-statements/reviews');
    if (current !== reviewGeneration) return;
    reviews.value = Array.isArray(result.items) ? result.items : [];
    reviewsLoaded.value = true; reviewError.value = '';
  } catch (e) {
    if (current === reviewGeneration) {reviewsLoaded.value = false; reviewError.value = e.message || '';}
  }
}
async function findCandidates(row) {
  if (!props.canReview || reviewBusy.value !== null || row.status !== 'review' || row.direction !== 'out') return;
  const current = reviewGeneration;
  reviewBusy.value = row.id; reviewError.value = '';
  try {
    const result = await props.api(`/api/bank-statements/lines/${encodeURIComponent(row.id)}/candidates`);
    if (current !== reviewGeneration) return;
    matchActions.value[row.id] = {items: Array.isArray(result.items) ? result.items : [], selected: '', note: '', confirm: false};
  } catch (e) {if (current === reviewGeneration) reviewError.value = e.message || 'error';}
  finally {reviewBusy.value = null;}
}
async function matchRequest(row) {
  const action = matchActions.value[row.id];
  const candidate = action?.items.find(item => String(item.id) === String(action.selected));
  if (!props.canReview || reviewBusy.value !== null || !candidate || !action.confirm || (action.note || '').trim().length < 10) return;
  const current = reviewGeneration;
  reviewBusy.value = row.id; reviewError.value = '';
  try {
    await props.api(`/api/bank-statements/lines/${encodeURIComponent(row.id)}/match`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({request_id: candidate.id, request_version: candidate.version, confirm: true, note: action.note.trim()}),
    });
    if (current !== reviewGeneration) return;
    delete matchActions.value[row.id];
    await loadReviews();
    emit('changed');
  } catch (e) {if (current === reviewGeneration) reviewError.value = e.message || 'error';}
  finally {reviewBusy.value = null;}
}
async function saveReview(row) {
  if (!props.canReview || reviewBusy.value !== null) return;
  const action = reviewActions.value[row.id] || {};
  if (!action.classification) return;
  const current = reviewGeneration;
  reviewBusy.value = row.id; reviewError.value = '';
  try {
    await props.api(`/api/bank-statements/lines/${encodeURIComponent(row.id)}/review`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({classification: action.classification, note: action.note || ''}),
    });
    if (current !== reviewGeneration) return;
    await loadReviews();
    emit('changed');
  } catch (e) { if (current === reviewGeneration) reviewError.value = e.message || 'error'; }
  finally { reviewBusy.value = null; }
}
onMounted(loadReviews);
onUnmounted(() => {generation += 1; reviewGeneration += 1;});

async function upload(event) {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (!file || busy.value || !props.canImport || !validSelection.value) return;
  invalidate();
  const current = generation;
  if (!/\.(xls|html|htm)$/i.test(file.name) || !file.size) {
    error.value = tx('Выберите непустую банковскую выписку в формате XLS или HTML.');
    return;
  }
  busy.value = true;
  try {
    const params = new URLSearchParams({account_id: String(accountId.value), currency: currency.value});
    const result = await props.api(`/api/bank-statements/preview?${params}`, {
      method: 'POST', headers: {'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name)}, body: file,
    });
    if (current !== generation) return;
    if (String(result.account?.id) !== String(accountId.value) || result.account?.currency !== currency.value) {
      throw new Error(N_('Выбранный счёт или валюта изменились. Загрузите выписку снова.'));
    }
    preview.value = result;
  } catch (e) {if (current === generation) error.value = tx(e.message);}
  finally {if (current === generation) busy.value = false;}
}

async function commit() {
  if (!ready.value || busy.value || !props.canImport) return;
  const current = generation, id = preview.value.id;
  busy.value = true; error.value = ''; notice.value = '';
  try {
    const result = await props.api(`/api/bank-statements/${encodeURIComponent(id)}/commit`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({confirm_account: true, confirm_currency: true}),
    });
    if (current !== generation) return;
    preview.value = null; confirmAccount.value = false; confirmCurrency.value = false;
    notice.value = tx('Выписка принята. Автоматически оплачено: {paid}; связано с оплатой: {linked}; требуется проверка: {review}.',
      {paid: result.auto_paid, linked: result.linked_paid, review: result.review});
    emit('changed');
    await loadReviews();
  } catch (e) {
    if (current === generation) {
      // A lost response may hide a successful commit. Do not post it twice.
      if (e.status == null || e.status >= 500) {
        uncertain.value = true;
        error.value = tx('Ответ о загрузке не получен. Обновите данные и проверьте результат перед повторной загрузкой.');
      } else error.value = tx(e.message);
    }
  } finally {if (current === generation) busy.value = false;}
}
</script>

<template>
  <section class="card bank-statements" aria-labelledby="bank-statements-title">
    <div class="card-h"><h2 id="bank-statements-title">{{tx('Банковские выписки')}}</h2></div>
    <div class="card-b">
      <p>{{tx('Загрузите выписку и проверьте счёт. После подтверждения совпадения обрабатываются с учётом прав и проверок; спорные операции попадут бухгалтеру.')}}</p>
      <p class="sub">{{tx('Автоматически сопоставляется только единственная заявка с теми же счётом, суммой, ИНН и плановой датой оплаты.')}}</p>
      <div v-if="canImport" class="bank-fields">
        <label>{{tx('Банковский счёт')}}<select v-model="accountId" :disabled="busy"><option value="">{{tx('Выберите счёт')}}</option><option v-for="a in bankAccounts" :key="a.id" :value="String(a.id)">{{a.name}} · {{a.currency}}</option></select></label>
        <label>{{tx('Валюта выписки')}}<select v-model="currency" :disabled="busy"><option value="">{{tx('Выберите валюту')}}</option><option v-for="c in ['UZS','USD','EUR']" :key="c" :value="c">{{c}}</option></select></label>
        <label class="button secondary file-label" :class="{disabled: busy || !validSelection}">{{tx('Загрузить выписку')}}<input ref="fileInput" type="file" accept=".xls,.html,.htm" :disabled="busy || !validSelection" @change="upload"></label>
      </div>
      <p v-if="account && currency && !validSelection" class="error" role="alert">{{tx('Валюта выписки должна совпадать с валютой выбранного счёта.')}}</p>
      <p v-if="busy" role="status">{{tx('Обрабатываем выписку…')}}</p>
      <p v-if="error" class="error" role="alert">{{error}}</p><p v-if="notice" role="status">{{notice}}</p>

      <div v-if="preview" class="bank-preview">
        <h3>{{tx('Проверка выписки')}}</h3>
        <p>{{tx('Счёт в файле')}}: <b>{{preview.statement.account_number}}</b> · {{tx('Счёт на сайте')}}: <b>{{preview.account.name}} · {{preview.account.currency}}</b></p>
        <p>{{tx('Владелец счёта')}}: {{preview.statement.owner_name || '—'}} · {{tx('ИНН')}}: {{preview.statement.owner_inn || '—'}}</p>
        <p>{{tx('Период')}}: {{formatShortDate(preview.statement.period_from)}} – {{formatShortDate(preview.statement.period_to)}}</p>
        <div class="bank-summary">
          <div><span>{{tx('Остаток на начало')}}</span><b>{{money(preview.statement.opening_balance)}} {{currency}}</b></div>
          <div><span>{{tx('Поступления по выписке')}}</span><b>{{money(preview.statement.credit_total)}} {{currency}}</b></div>
          <div><span>{{tx('Списания по выписке')}}</span><b>{{money(preview.statement.debit_total)}} {{currency}}</b></div>
          <div><span>{{tx('Остаток на конец')}}</span><b>{{money(preview.statement.closing_balance)}} {{currency}}</b></div>
        </div>
        <p v-if="preview.statement.ready === true && preview.statement.balance_matches === true && preview.statement.declared_totals_match !== false" class="good-text">{{tx('Баланс выписки сходится.')}}</p>
        <p v-else class="error" role="alert">{{tx('Остатки или обороты выписки не подтверждены. Исправьте файл до загрузки.')}}</p>
        <p>{{tx('Точных совпадений с заявками: {n}',{n:count('auto_pay_candidate')})}} · {{tx('Уже оплачено на сайте: {n}',{n:count('paid_match')})}} · {{tx('На проверку: {n}',{n:count('review')+count('ambiguous_duplicate')})}}</p>
        <div class="table-scroll"><table><thead><tr><th>{{tx('Дата')}}</th><th>{{tx('Контрагент / документ')}}</th><th>{{tx('Назначение')}}</th><th>{{tx('Движение')}}</th><th>{{tx('Результат сверки')}}</th></tr></thead><tbody>
          <tr v-for="row in rows" :key="row.id"><td>{{formatShortDate(row.date)}}</td><td>{{row.correspondent_name}}<small>{{row.correspondent_inn || '—'}} · {{row.document_number}}</small></td><td>{{row.purpose}}</td><td>{{directionLabel(row.direction)}}<small>{{money(row.amount)}} {{currency}}</small></td><td>{{label(row.status)}}<small v-if="row.reason">{{tx(row.reason)}}</small><small v-if="row.request_id">{{tx('Заявка №{id}',{id:row.request_id})}}</small></td></tr>
        </tbody></table></div>
        <label class="confirm"><input v-model="confirmAccount" type="checkbox" :disabled="busy"> {{tx('Подтверждаю: номер счёта в выписке относится к выбранному счёту на сайте.')}}</label>
        <label class="confirm"><input v-model="confirmCurrency" type="checkbox" :disabled="busy"> {{tx('Подтверждаю валюту выписки: {currency}',{currency})}}</label>
        <p class="sub">{{tx('После подтверждения заявка оплачивается автоматически только при наличии права оплаты и успешном прохождении проверок. В остальных случаях строка останется бухгалтеру на проверку.')}}</p>
        <div class="form-actions"><button type="button" class="secondary" :disabled="busy" @click="invalidate">{{tx('Отмена')}}</button><button type="button" :disabled="busy || !ready" @click="commit">{{tx('Подтвердить выписку')}}</button></div>
      </div>

      <div class="review-list">
        <h3>{{tx('Операции для проверки бухгалтера')}}</h3>
        <p class="sub">{{tx('Классификация помогает проверить выписку. Она не создаёт операцию, не изменяет остаток и Cash Flow; поступление или комиссию нужно провести отдельно.')}}</p>
        <p v-if="reviewError" class="error" role="alert">{{tx('Не удалось загрузить операции для проверки.')}}</p>
        <p v-else-if="reviewsLoaded && !reviews.length" class="sub">{{tx('Нет операций, ожидающих проверки.')}}</p>
        <div v-else-if="reviews.length" class="table-scroll"><table><thead><tr><th>{{tx('Дата')}}</th><th>{{tx('Контрагент / документ')}}</th><th>{{tx('Назначение')}}</th><th>{{tx('Движение')}}</th><th>{{tx('Результат сверки')}}</th></tr></thead><tbody>
          <tr v-for="row in reviews" :key="row.id"><td>{{formatShortDate(row.date)}}</td><td>{{row.correspondent_name}}<small>{{row.correspondent_inn || '—'}} · {{row.document_number}}</small></td><td>{{row.purpose}}</td><td>{{directionLabel(row.direction)}}<small>{{money(row.amount,row.currency)}} {{row.currency}}</small></td><td>{{label(row.status)}}<small v-if="row.reason">{{tx(row.reason)}}</small><small v-if="row.status === 'ambiguous_duplicate'">{{tx('Классификация не устраняет неоднозначность повтора: дневной итог останется с предупреждением.')}}</small>
            <div v-if="canReview && row.status === 'review' && row.direction === 'out'" class="review-actions">
              <button type="button" class="secondary" :disabled="reviewBusy !== null" @click="findCandidates(row)">{{tx('Найти заявку для сопоставления')}}</button>
              <template v-if="matchActions[row.id]">
                <p>{{tx('Дата по выписке')}}: {{formatShortDate(row.date)}}</p>
                <p v-if="!matchActions[row.id].items.length">{{tx('Подходящие заявки не найдены.')}}</p>
                <template v-else>
                  <label>{{tx('Выберите заявку')}}<select v-model="matchActions[row.id].selected" :disabled="reviewBusy !== null"><option value="">{{tx('Выберите заявку')}}</option><option v-for="candidate in matchActions[row.id].items" :key="candidate.id" :value="String(candidate.id)">{{candidate.number}} · {{candidate.counterparty}} · {{candidate.counterparty_inn}} · {{money(candidate.amount,row.currency)}} {{row.currency}} · {{tx('Плановая дата')}}: {{formatShortDate(candidate.date)}} · {{candidateStatus(candidate.status)}}</option></select></label>
                  <label>{{tx('Причина сопоставления (не менее 10 символов)')}}<input v-model="matchActions[row.id].note" :disabled="reviewBusy !== null"></label>
                  <label><input v-model="matchActions[row.id].confirm" type="checkbox" :disabled="reviewBusy !== null"> {{tx('Подтверждаю сопоставление и оплату выбранной заявки.')}}</label>
                  <button type="button" :disabled="reviewBusy !== null || !matchActions[row.id].selected || !matchActions[row.id].confirm || matchActions[row.id].note.trim().length < 10" @click="matchRequest(row)">{{tx('Подтвердить сопоставление')}}</button>
                </template>
              </template>
            </div>
            <div v-if="canReview" class="review-actions">
              <label>{{tx('Классификация')}}<select :value="reviewActions[row.id]?.classification || ''" :disabled="reviewBusy !== null" @change="reviewActions[row.id] = {...reviewActions[row.id], classification: $event.target.value}"><option value="">{{tx('Выберите классификацию')}}</option><option value="bank_fee">{{tx('Банковская услуга')}}</option><option value="internal_transfer">{{tx('Перевод между своими счетами')}}</option><option value="receipt">{{tx('Поступление')}}</option><option value="other">{{tx('Другое')}}</option><option value="unresolved">{{tx('Оставить на проверке')}}</option></select></label>
              <label>{{tx('Примечание')}}<input :value="reviewActions[row.id]?.note || ''" :disabled="reviewBusy !== null" @input="reviewActions[row.id] = {...reviewActions[row.id], note: $event.target.value}"></label>
              <button type="button" class="secondary" :disabled="reviewBusy !== null || !reviewActions[row.id]?.classification" @click="saveReview(row)">{{tx('Сохранить классификацию')}}</button>
            </div></td></tr>
        </tbody></table></div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.bank-statements{margin:16px 0}.bank-fields{display:flex;gap:12px;flex-wrap:wrap;align-items:end}.bank-fields label:not(.file-label){display:grid;gap:4px;min-width:170px}.file-label input{display:none}.file-label.disabled{opacity:.5;pointer-events:none}.bank-preview,.review-list{margin-top:20px}.bank-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.bank-summary div{display:grid;gap:4px;padding:10px;background:#f5f6fb;border-radius:8px}.bank-summary b{font-variant-numeric:tabular-nums}.good-text{color:#167145}.confirm{display:block;margin:10px 0}.table-scroll td{max-width:340px;white-space:normal;overflow-wrap:anywhere}.table-scroll small{display:block}.form-actions{margin-top:12px}.review-actions{display:grid;gap:7px;margin-top:8px;min-width:200px}.review-actions label{display:grid;gap:3px}
</style>
