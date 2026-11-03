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
  categories: {type: Array, default: () => []},
  canImport: {type: Boolean, default: false},
  canReview: {type: Boolean, default: false},
  canPost: {type: Boolean, default: false},
  canManageFee: {type: Boolean, default: false},
  canAllocate: {type: Boolean, default: false},
  canDeleteStatements: {type: Boolean, default: false},
});
const emit = defineEmits(['changed']);
const accountId = ref(''), currency = ref(''), preview = ref(null), busy = ref(false), error = ref(''), notice = ref('');
const confirmAccount = ref(false), confirmCurrency = ref(false), reviews = ref([]), reviewError = ref('');
const reviewActions = ref({}), matchActions = ref({}), duplicateActions = ref({}), transferActions = ref({}), reviewBusy = ref(null);
const feeCategoryId = ref(''), unallocated = ref([]), allocationActions = ref({}), allocationBusy = ref(null), allocationError = ref(''), allocationLoaded = ref(false);
const outgoingUnallocated = ref([]), outgoingActions = ref({}), outgoingBusy = ref(null), outgoingError = ref(''), outgoingLoaded = ref(false);
const oldBatches = ref([]), oldBatchesLoaded = ref(false), selectedOldBatch = ref(''), reprocessPreview = ref(null), reprocessBusy = ref(false), reprocessError = ref(''), reprocessNotice = ref('');
const adminBatches = ref([]), adminLoaded = ref(false), adminBusy = ref(false), adminError = ref(''), adminNotice = ref('');
const deleteBatchId = ref(null), deletePreview = ref(null), deleteReason = ref(''), deleteConfirmed = ref(false), deleteUncertain = ref(false);
const feeSettingsError = ref('');
const reviewsLoaded = ref(false), uncertain = ref(false), fileInput = ref(null);
const bankAccounts = computed(() => props.accounts.filter(a => a.kind === 'bank' && !a.archived));
const unreconciledAccounts = computed(() => bankAccounts.value.filter(a => Number(a.unreconciled_statements || 0) > 0));
const account = computed(() => bankAccounts.value.find(a => String(a.id) === String(accountId.value)));
const validSelection = computed(() => !!account.value && !!currency.value && account.value.currency === currency.value);
const rows = computed(() => Array.isArray(preview.value?.rows) ? preview.value.rows : []);
const incomeCategories = computed(() => props.categories.filter(c => c.type === 'income' && c.activity !== 'unclassified' && !c.retired));
const feeCategories = computed(() => props.categories.filter(c => c.type === 'outcome' && c.activity !== 'unclassified' && !c.retired));
const oldBatch = computed(() => oldBatches.value.find(item => String(item.id) === String(selectedOldBatch.value)));
const oldCurrency = computed(() => oldBatch.value?.currency || 'UZS');
const feeId = () => feeCategoryId.value ? Number(feeCategoryId.value) : null;
const count = status => rows.value.filter(row => row.status === status).length;
const ready = computed(() => !!preview.value && preview.value.status === 'preview' && !!preview.value.id && !!preview.value.preview_token && typeof preview.value.full_reconciliation === 'boolean' && !!preview.value.statement?.account_number && validSelection.value && confirmAccount.value && confirmCurrency.value
  && preview.value.statement?.ready === true && preview.value.statement?.balance_matches === true && preview.value.statement?.declared_totals_match !== false && !uncertain.value);
const money = (value, cur = currency.value) => formatCents(toCents(value), cur || 'UZS');
const labels = Object.freeze({
  auto_pay_candidate: N_('Точное совпадение с заявкой'),
  auto_paid: N_('Заявка оплачена'),
  paid_match: N_('Оплата уже внесена на сайте'),
  manual_paid: N_('Заявка оплачена после проверки'),
  manual_match: N_('Связано с оплатой после проверки'),
  manual_transfer_match: N_('Связано с переводом'),
  other_matched_candidate: N_('Найдена существующая операция'),
  other_matched: N_('Связано с существующей операцией'),
  income_candidate: N_('Поступление будет проведено'),
  income_matched_candidate: N_('Существующее поступление будет связано'),
  auto_income_posted: N_('Поступление проведено'),
  income_matched: N_('Поступление связано с операцией'),
  outgoing_candidate: N_('Списание будет проведено'),
  auto_outgoing_posted: N_('Списание проведено'),
  fee_candidate: N_('Комиссия будет проведена'),
  fee_existing: N_('Существующая комиссия будет связана'),
  auto_fee_posted: N_('Комиссия проведена'),
  fee_matched: N_('Комиссия связана с операцией'),
  reviewed: N_('Проверено бухгалтером'),
  review: N_('Нужна проверка бухгалтера'),
  duplicate: N_('Повтор связан с ранее проведённой операцией'),
  ambiguous_duplicate: N_('Возможный повтор — нужна проверка'),
});
const label = value => tx(labels[value] || N_('Нужна проверка бухгалтера'));
const pendingFullReconciliation = new Set(['income_candidate','income_matched_candidate','outgoing_candidate','fee_candidate','fee_existing','other_matched_candidate']);
const rowLabel = (row, fullReconciliation) => fullReconciliation !== true && pendingFullReconciliation.has(row.status) ? tx('Ожидает полной сверки') : label(row.status);
const reviewCount = (items, fullReconciliation) => items.filter(row => ['review', 'ambiguous_duplicate'].includes(row.status) || (fullReconciliation !== true && pendingFullReconciliation.has(row.status))).length;
const selectedTransfer = row => {const action = transferActions.value[row.id]; return action?.items.find(item => String(item.ledger_id) === String(action.selected));};
const resetSelectionConfirmation = (actions, row) => {const action = actions[row.id]; if (action) {action.confirm = false; action.note = '';}};
const candidateStatus = value => tx(value === 'approved' ? N_('Утверждена') : value === 'paid' ? N_('Оплачена') : N_('Не определено'));
const directionLabel = value => tx(value === 'in' ? N_('Поступление') : value === 'out' ? N_('Списание') : N_('Не определено'));
const unallocatedNames = new Set([N_('Не распределено — поступления из банка'), N_('Не распределено — списания из банка')]);
const categoryName = row => {const name = row.category_name || props.categories.find(c => c.id === row.category_id)?.name || ''; return unallocatedNames.has(name) ? tx(name) : name || (['unclassified','unclassified_out'].includes(row.category_source) ? tx('Не распределено') : '');};
const reviewErrorMessage = e => e?.status && e.message ? e.message : N_('Не удалось выполнить проверку. Обновите список и повторите.');
let generation = 0, reviewGeneration = 0, reviewOperation = 0, allocationGeneration = 0, allocationOperation = 0, outgoingGeneration = 0, outgoingOperation = 0, batchGeneration = 0, settingsGeneration = 0, reprocessGeneration = 0, feeTouched = false;
let adminGeneration = 0;

function invalidate() {
  generation += 1;
  preview.value = null; confirmAccount.value = false; confirmCurrency.value = false;
  error.value = ''; notice.value = ''; uncertain.value = false; busy.value = false;
}
watch(() => [accountId.value, currency.value, feeCategoryId.value], () => { invalidate(); reprocessGeneration += 1; reprocessBusy.value = false; reprocessPreview.value = null; reprocessError.value = ''; reprocessNotice.value = ''; });
watch(() => props.companyId, () => { invalidate(); reviewGeneration += 1; reviewOperation += 1; allocationGeneration += 1; allocationOperation += 1; outgoingGeneration += 1; outgoingOperation += 1; batchGeneration += 1; settingsGeneration += 1; reprocessGeneration += 1; feeTouched = false; feeSettingsError.value = ''; reviewBusy.value = allocationBusy.value = outgoingBusy.value = null; reprocessBusy.value = false; reviewActions.value = {}; matchActions.value = {}; duplicateActions.value = {}; transferActions.value = {}; allocationActions.value = {}; outgoingActions.value = {}; accountId.value = currency.value = feeCategoryId.value = selectedOldBatch.value = ''; reviews.value = []; unallocated.value = []; outgoingUnallocated.value = []; oldBatches.value = []; reprocessPreview.value = null; reviewsLoaded.value = allocationLoaded.value = outgoingLoaded.value = oldBatchesLoaded.value = false; loadReviews(); loadUnallocated(); loadUnallocatedOutgoing(); loadOldBatches(); loadFeeSettings(); });
// Permissions may change without remounting the component. Discard any signed
// preview as soon as access changes, and reload eligible batches when restored.
watch(() => props.canPost, allowed => {
  batchGeneration += 1; reprocessGeneration += 1; reprocessBusy.value = false;
  oldBatches.value = []; oldBatchesLoaded.value = false; selectedOldBatch.value = '';
  reprocessPreview.value = null; reprocessError.value = ''; reprocessNotice.value = '';
  if (allowed) loadOldBatches();
});

watch(() => [props.companyId, props.canDeleteStatements], () => {
  adminGeneration += 1; adminBusy.value = false; adminBatches.value = []; adminLoaded.value = false;
  adminError.value = ''; adminNotice.value = ''; deleteBatchId.value = null; deletePreview.value = null;
  deleteReason.value = ''; deleteConfirmed.value = false; deleteUncertain.value = false;
  if (props.canDeleteStatements && props.companyId) loadAdminBatches();
});

async function loadAdminBatches() {
  if (!props.canDeleteStatements || !props.companyId || adminBusy.value) return;
  const current = ++adminGeneration;
  try {
    const result = await props.api('/api/bank-statements/admin/batches');
    if (current !== adminGeneration) return;
    adminBatches.value = Array.isArray(result.items) ? result.items : [];
    adminLoaded.value = true; adminError.value = ''; deleteUncertain.value = false;
    deleteBatchId.value = null; deletePreview.value = null; deleteReason.value = ''; deleteConfirmed.value = false;
  } catch (e) {if (current === adminGeneration) {adminLoaded.value = false; adminError.value = e.message || tx('Не удалось загрузить список выписок.');}}
}

async function selectDeleteBatch(batch) {
  if (!props.canDeleteStatements || adminBusy.value || deleteUncertain.value || !adminBatches.value.some(item => item.id === batch.id)) return;
  const current = adminGeneration, company = props.companyId;
  deleteBatchId.value = null; deletePreview.value = null; deleteReason.value = ''; deleteConfirmed.value = false; adminError.value = ''; adminNotice.value = ''; adminBusy.value = true;
  try {
    const result = await props.api(`/api/bank-statements/${encodeURIComponent(batch.id)}/delete-preview`);
    if (current !== adminGeneration || props.companyId !== company || !props.canDeleteStatements) return;
    if (result?.id !== batch.id || !result.delete_token) throw new Error(tx('Выписка изменилась. Обновите список и повторите.'));
    deletePreview.value = result; deleteBatchId.value = batch.id;
  } catch (e) {if (current === adminGeneration && props.companyId === company) adminError.value = e.message || tx('Не удалось проверить выписку перед удалением.');}
  finally {if (current === adminGeneration) adminBusy.value = false;}
}

async function deleteStatement() {
  const batch = deletePreview.value;
  if (batch?.id !== deleteBatchId.value) return;
  if (!props.canDeleteStatements || adminBusy.value || deleteUncertain.value || !batch?.delete_token || !deleteConfirmed.value || deleteReason.value.trim().length < 10) return;
  const current = adminGeneration, company = props.companyId;
  adminBusy.value = true; adminError.value = ''; adminNotice.value = '';
  try {
    await props.api(`/api/bank-statements/${encodeURIComponent(batch.id)}/delete`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({confirm: true, reason: deleteReason.value.trim(), expected: batch.delete_token}),
    });
    if (current !== adminGeneration || props.companyId !== company) return;
    adminBusy.value = false; adminNotice.value = tx('Выписка удалена, её операции отменены.');
    await Promise.all([loadAdminBatches(), loadReviews(), loadUnallocated(), loadUnallocatedOutgoing(), loadOldBatches()]);
    if (props.companyId === company && props.canDeleteStatements) emit('changed');
  } catch (e) {
    if (current !== adminGeneration || props.companyId !== company) return;
    if (!e?.status || e.status >= 500) {deleteUncertain.value = true; adminError.value = tx('Ответ об удалении не получен. Обновите список, прежде чем повторить действие.');}
    else adminError.value = e.message || tx('Не удалось удалить выписку.');
  } finally {if (current === adminGeneration) adminBusy.value = false;}
}

async function loadFeeSettings() {
  if (!props.canImport && !props.canPost) return;
  const current = ++settingsGeneration;
  try {
    const result = await props.api('/api/bank-statements/settings');
    if (current !== settingsGeneration || feeTouched) return;
    const id = result.fee_category_id;
    if (id == null) {feeCategoryId.value = ''; feeSettingsError.value = ''; return;}
    if (!feeCategories.value.some(c => String(c.id) === String(id))) {
      feeCategoryId.value = ''; feeSettingsError.value = tx('Сохранённая статья комиссии недоступна. Выберите действующую статью.'); return;
    }
    feeCategoryId.value = String(id); feeSettingsError.value = '';
  } catch (e) {if (current === settingsGeneration && !feeTouched) feeSettingsError.value = tx('Не удалось загрузить сохранённую статью комиссии.');}
}

async function loadUnallocated() {
  if (!props.canAllocate) return;
  const current = ++allocationGeneration;
  try {
    const result = await props.api('/api/bank-statements/income-unallocated');
    if (current !== allocationGeneration) return;
    unallocated.value = Array.isArray(result.items) ? result.items : [];
    allocationLoaded.value = true; allocationError.value = '';
  } catch (e) {if (current === allocationGeneration) {allocationLoaded.value = false; allocationError.value = e.message || 'error';}}
}
async function assignIncome(row) {
  const action = allocationActions.value[row.id] || {};
  if (!props.canAllocate || allocationBusy.value !== null || !row.edit_token || String(row.category_id) === String(action.categoryId) || !incomeCategories.value.some(c => String(c.id) === String(action.categoryId)) || (action.reason || '').trim().length < 10) return;
  const current = allocationGeneration, company = props.companyId, operation = ++allocationOperation;
  allocationBusy.value = row.id; allocationError.value = '';
  try {
    await props.api(`/api/bank-statements/lines/${encodeURIComponent(row.id)}/income-category`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({category_id: Number(action.categoryId), edit_token: row.edit_token, reason: action.reason.trim()}),
    });
    if (current !== allocationGeneration) return;
    delete allocationActions.value[row.id]; await loadUnallocated(); if (props.companyId === company) emit('changed');
  } catch (e) {if (current === allocationGeneration) allocationError.value = e.message || 'error';}
  finally {if (operation === allocationOperation) allocationBusy.value = null;}
}

async function loadUnallocatedOutgoing() {
  if (!props.canAllocate) return;
  const current = ++outgoingGeneration;
  try {
    const result = await props.api('/api/bank-statements/outgoing-unallocated');
    if (current !== outgoingGeneration) return;
    outgoingUnallocated.value = Array.isArray(result.items) ? result.items : [];
    outgoingLoaded.value = true; outgoingError.value = '';
  } catch (e) {if (current === outgoingGeneration) {outgoingLoaded.value = false; outgoingError.value = e.message || 'error';}}
}
async function assignOutgoing(row) {
  const action = outgoingActions.value[row.id] || {};
  if (!props.canAllocate || outgoingBusy.value !== null || !row.edit_token || String(row.category_id) === String(action.categoryId) || !feeCategories.value.some(c => String(c.id) === String(action.categoryId)) || (action.reason || '').trim().length < 10) return;
  const current = outgoingGeneration, company = props.companyId, operation = ++outgoingOperation;
  outgoingBusy.value = row.id; outgoingError.value = '';
  try {
    await props.api(`/api/bank-statements/lines/${encodeURIComponent(row.id)}/outgoing-category`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({category_id: Number(action.categoryId), edit_token: row.edit_token, reason: action.reason.trim()}),
    });
    if (current !== outgoingGeneration) return;
    delete outgoingActions.value[row.id]; await loadUnallocatedOutgoing(); if (props.companyId === company) emit('changed');
  } catch (e) {if (current === outgoingGeneration) outgoingError.value = e.message || 'error';}
  finally {if (operation === outgoingOperation) outgoingBusy.value = null;}
}

async function loadOldBatches() {
  if (!props.canPost) return;
  const current = ++batchGeneration;
  try {
    const result = await props.api('/api/bank-statements/reprocess/batches');
    if (current !== batchGeneration) return;
    oldBatches.value = Array.isArray(result.items) ? result.items : [];
    oldBatchesLoaded.value = true;
  } catch (e) {if (current === batchGeneration) {oldBatchesLoaded.value = false; reprocessError.value = e.message || 'error';}}
}
async function previewOldBatch() {
  const batch = oldBatches.value.find(item => String(item.id) === String(selectedOldBatch.value));
  if (!props.canPost || busy.value || reprocessBusy.value || !batch) return;
  const current = ++reprocessGeneration, chosenFee = feeId(); feeTouched = true;
  reprocessPreview.value = null; reprocessError.value = ''; reprocessNotice.value = ''; reprocessBusy.value = true;
  try {
    const result = await props.api(`/api/bank-statements/${encodeURIComponent(batch.id)}/reprocess/preview`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({fee_category_id: chosenFee}),
    });
    if (current !== reprocessGeneration) return;
    if (String(result.batch_id) !== String(batch.id) || typeof result.full_reconciliation !== 'boolean') throw new Error(N_('Выбранная выписка изменилась. Повторите проверку.'));
    reprocessPreview.value = result;
  } catch (e) {if (current === reprocessGeneration) reprocessError.value = e.message || 'error';}
  finally {if (current === reprocessGeneration) reprocessBusy.value = false;}
}
async function commitOldBatch() {
  const data = reprocessPreview.value, batch = oldBatches.value.find(item => String(item.id) === String(selectedOldBatch.value));
  if (!props.canPost || busy.value || reprocessBusy.value || !batch || !data?.preview_token || typeof data.full_reconciliation !== 'boolean' || String(data.batch_id) !== String(batch.id)) return;
  const current = reprocessGeneration, company = props.companyId;
  reprocessBusy.value = true; reprocessError.value = ''; reprocessNotice.value = '';
  try {
    const result = await props.api(`/api/bank-statements/${encodeURIComponent(batch.id)}/reprocess/commit`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({fee_category_id: feeId(), preview_token: data.preview_token, confirm: true}),
    });
    if (current !== reprocessGeneration) return;
    reprocessPreview.value = null;
    reprocessNotice.value = tx('Повторная проверка завершена. Проведено: {posted}; связано: {linked}; на проверке: {review}.', {posted: result.posted ?? 0, linked: result.linked ?? 0, review: result.review ?? 0});
    if (result.full_reconciliation === false) reprocessNotice.value += ' ' + tx('Полная сверка не завершена; поступления, комиссии и прочие списания не проводились.');
    else if (result.full_reconciliation !== true) reprocessNotice.value += ' ' + tx('Результат полной сверки не подтверждён. Обновите данные перед повторной обработкой.');
    await Promise.all([loadReviews(), loadUnallocated(), loadUnallocatedOutgoing(), loadOldBatches()]); if (props.companyId === company) emit('changed');
  } catch (e) {if (current === reprocessGeneration) {reprocessPreview.value = null; reprocessError.value = (e.status == null || e.status >= 500) ? tx('Ответ о загрузке не получен. Обновите данные и проверьте результат перед повторной загрузкой.') : e.message || 'error';}}
  finally {if (current === reprocessGeneration) reprocessBusy.value = false;}
}

async function loadReviews() {
  const current = ++reviewGeneration;
  try {
    const result = await props.api('/api/bank-statements/reviews');
    if (current !== reviewGeneration) return;
    reviews.value = Array.isArray(result.items) ? result.items : [];
    reviewsLoaded.value = true; reviewError.value = '';
  } catch (e) {
    if (current === reviewGeneration) {reviewsLoaded.value = false; reviewError.value = reviewErrorMessage(e);}
  }
}
async function findCandidates(row) {
  if (!props.canReview || reviewBusy.value !== null || row.status !== 'review' || row.direction !== 'out') return;
  const current = reviewGeneration, operation = ++reviewOperation;
  reviewBusy.value = row.id; reviewError.value = '';
  try {
    const result = await props.api(`/api/bank-statements/lines/${encodeURIComponent(row.id)}/candidates`);
    if (current !== reviewGeneration) return;
    matchActions.value[row.id] = {items: Array.isArray(result.items) ? result.items : [], selected: '', note: '', confirm: false};
  } catch (e) {if (current === reviewGeneration) reviewError.value = reviewErrorMessage(e);}
  finally {if (operation === reviewOperation) reviewBusy.value = null;}
}
async function matchRequest(row) {
  const action = matchActions.value[row.id];
  const candidate = action?.items.find(item => String(item.id) === String(action.selected));
  if (!props.canReview || reviewBusy.value !== null || !candidate || !action.confirm || (action.note || '').trim().length < 10) return;
  const current = reviewGeneration, company = props.companyId, operation = ++reviewOperation;
  reviewBusy.value = row.id; reviewError.value = '';
  try {
    await props.api(`/api/bank-statements/lines/${encodeURIComponent(row.id)}/match`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({request_id: candidate.id, request_version: candidate.version, confirm: true, note: action.note.trim()}),
    });
    if (current !== reviewGeneration) return;
    delete matchActions.value[row.id];
    await loadReviews();
    if (props.companyId === company) emit('changed');
  } catch (e) {if (current === reviewGeneration) reviewError.value = reviewErrorMessage(e);}
  finally {if (operation === reviewOperation) reviewBusy.value = null;}
}
async function findDuplicateCandidates(row) {
  if (!props.canReview || reviewBusy.value !== null || row.status !== 'ambiguous_duplicate') return;
  const current = reviewGeneration, operation = ++reviewOperation;
  reviewBusy.value = row.id; reviewError.value = '';
  try {
    const result = await props.api(`/api/bank-statements/lines/${encodeURIComponent(row.id)}/duplicate-candidates`);
    if (current !== reviewGeneration) return;
    duplicateActions.value[row.id] = {items: Array.isArray(result.items) ? result.items : [], selected: '', note: '', confirm: false};
  } catch (e) {if (current === reviewGeneration) reviewError.value = reviewErrorMessage(e);}
  finally {if (operation === reviewOperation) reviewBusy.value = null;}
}
async function resolveDuplicate(row) {
  const action = duplicateActions.value[row.id];
  const candidate = action?.items.find(item => String(item.line_id) === String(action.selected));
  if (!props.canReview || row.status !== 'ambiguous_duplicate' || reviewBusy.value !== null || !candidate || !candidate.token || !candidate.ledger_id || !action.confirm || (action.note || '').trim().length < 10) return;
  const current = reviewGeneration, company = props.companyId, operation = ++reviewOperation;
  reviewBusy.value = row.id; reviewError.value = '';
  try {
    await props.api(`/api/bank-statements/lines/${encodeURIComponent(row.id)}/duplicate-resolve`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prior_line_id: candidate.line_id, token: candidate.token, reason: action.note.trim(), confirm: true}),
    });
    if (current !== reviewGeneration) return;
    delete duplicateActions.value[row.id]; await loadReviews(); if (props.companyId === company) emit('changed');
  } catch (e) {if (current === reviewGeneration) {delete duplicateActions.value[row.id]; reviewError.value = reviewErrorMessage(e);}}
  finally {if (operation === reviewOperation) reviewBusy.value = null;}
}
async function findTransferCandidates(row) {
  if (!props.canReview || reviewBusy.value !== null || !['review', 'reviewed'].includes(row.status)) return;
  const current = reviewGeneration, operation = ++reviewOperation;
  reviewBusy.value = row.id; reviewError.value = '';
  try {
    const result = await props.api(`/api/bank-statements/lines/${encodeURIComponent(row.id)}/transfer-candidates`);
    if (current !== reviewGeneration) return;
    transferActions.value[row.id] = {items: Array.isArray(result.items) ? result.items : [], selected: '', note: '', confirm: false};
  } catch (e) {if (current === reviewGeneration) reviewError.value = reviewErrorMessage(e);}
  finally {if (operation === reviewOperation) reviewBusy.value = null;}
}
async function linkTransfer(row) {
  const action = transferActions.value[row.id];
  const candidate = selectedTransfer(row);
  if (!props.canReview || !['review', 'reviewed'].includes(row.status) || reviewBusy.value !== null || !candidate?.ledger_id || !candidate.token || !action.confirm || (action.note || '').trim().length < 10) return;
  const current = reviewGeneration, company = props.companyId, operation = ++reviewOperation;
  reviewBusy.value = row.id; reviewError.value = '';
  try {
    await props.api(`/api/bank-statements/lines/${encodeURIComponent(row.id)}/transfer-link`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ledger_id: candidate.ledger_id, token: candidate.token, reason: action.note.trim(), confirm: true}),
    });
    if (current !== reviewGeneration) return;
    delete transferActions.value[row.id]; await loadReviews(); if (props.companyId === company) emit('changed');
  } catch (e) {if (current === reviewGeneration) {delete transferActions.value[row.id]; reviewError.value = reviewErrorMessage(e);}}
  finally {if (operation === reviewOperation) reviewBusy.value = null;}
}
async function saveReview(row) {
  if (!props.canReview || reviewBusy.value !== null) return;
  const action = reviewActions.value[row.id] || {};
  if (!action.classification) return;
  const current = reviewGeneration, company = props.companyId, operation = ++reviewOperation;
  reviewBusy.value = row.id; reviewError.value = '';
  try {
    await props.api(`/api/bank-statements/lines/${encodeURIComponent(row.id)}/review`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({classification: action.classification, note: action.note || ''}),
    });
    if (current !== reviewGeneration) return;
    await loadReviews();
    if (props.companyId === company) emit('changed');
  } catch (e) { if (current === reviewGeneration) reviewError.value = reviewErrorMessage(e); }
  finally { if (operation === reviewOperation) reviewBusy.value = null; }
}
onMounted(() => {loadReviews(); loadUnallocated(); loadUnallocatedOutgoing(); loadOldBatches(); loadFeeSettings(); if (props.canDeleteStatements) loadAdminBatches();});
onUnmounted(() => {generation += 1; reviewGeneration += 1; reviewOperation += 1; allocationGeneration += 1; allocationOperation += 1; outgoingGeneration += 1; outgoingOperation += 1; batchGeneration += 1; settingsGeneration += 1; reprocessGeneration += 1; adminGeneration += 1;});
watch(selectedOldBatch, () => {reprocessGeneration += 1; reprocessBusy.value = false; reprocessPreview.value = null; reprocessError.value = ''; reprocessNotice.value = '';});

async function upload(event) {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (!file || busy.value || reprocessBusy.value || !props.canImport || !validSelection.value) return;
  feeTouched = true;
  invalidate();
  const current = generation;
  if (!/\.(xls|html|htm)$/i.test(file.name) || !file.size) {
    error.value = tx('Выберите непустую банковскую выписку в формате XLS или HTML.');
    return;
  }
  busy.value = true;
  try {
    const params = new URLSearchParams({account_id: String(accountId.value), currency: currency.value});
    if (feeId() !== null) params.set('fee_category_id', String(feeId()));
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
      body: JSON.stringify({confirm_account: true, confirm_currency: true, fee_category_id: feeId(), preview_token: preview.value.preview_token}),
    });
    if (current !== generation) return;
    preview.value = null; confirmAccount.value = false; confirmCurrency.value = false;
    notice.value = tx('Выписка принята. Автоматически оплачено: {paid}; связано с оплатой: {linked}; требуется проверка: {review}.',
      {paid: result.auto_paid, linked: result.linked_paid, review: result.review});
    notice.value += ' ' + tx('Поступлений проведено: {income}, связано: {incomeLinked}; комиссий проведено: {fee}, связано: {feeLinked}.',
      {income: result.auto_income ?? 0, incomeLinked: result.income_linked ?? 0, fee: result.auto_fee ?? 0, feeLinked: result.fee_linked ?? 0});
    notice.value += ' ' + tx('Внешних списаний проведено: {outgoing}.', {outgoing: result.auto_outgoing ?? 0});
    notice.value += ' ' + tx('Других существующих операций связано: {other}.', {other: result.other_linked ?? 0});
    if (result.full_reconciliation === false) notice.value += ' ' + tx('Выписка сохранена. Поступления, комиссии и прочие списания отложены; точные заявки могли быть отмечены оплаченными. Спорные строки нужно проверить бухгалтеру.');
    else if (result.full_reconciliation !== true) notice.value += ' ' + tx('Результат полной сверки не подтверждён. Обновите данные перед повторной обработкой.');
    emit('changed');
    await Promise.all([loadReviews(), loadUnallocated(), loadUnallocatedOutgoing(), loadOldBatches()]);
  } catch (e) {
    if (current === generation) {
      // A lost response may hide a successful commit. Do not post it twice.
      if (e.status == null || e.status >= 500) {
        uncertain.value = true;
        error.value = tx('Ответ о загрузке не получен. Обновите данные и проверьте результат перед повторной загрузкой.');
      } else if (e.status === 409) {preview.value = null; confirmAccount.value = false; confirmCurrency.value = false; error.value = tx('Данные изменились. Загрузите выписку и проверьте её заново.');}
      else error.value = tx(e.message);
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
      <p class="sub">{{tx('Внешние поступления и списания без определённой статьи относятся к «Не распределено». Возможные собственные переводы, неясные комиссии, заявки и повторы остаются на проверке.')}}</p>
      <p class="sub">{{tx('Автоматически распознаются только явно отмеченные комиссии SWIFT в формате этого банка. Проценты и прочие удержания остаются на проверке.')}}</p>
      <details v-if="canDeleteStatements" class="review-list">
        <summary>{{tx('Удаление выписки администратором')}}</summary>
        <p class="sub">{{tx('Удаление отменяет операции, созданные этой выпиской, и их влияние на остатки и отчёты. Ранее существовавшие связанные операции сохраняются.')}}</p>
        <p class="sub">{{tx('Оплаченные заявки и подтверждённые поступления вернутся к предыдущему состоянию, если их оплата была создана этой выпиской. История действий сохраняется.')}}</p>
        <button type="button" class="secondary tiny" :disabled="adminBusy" @click="loadAdminBatches">{{tx('Обновить список выписок')}}</button>
        <p v-if="adminError" class="error" role="alert">{{tx(adminError)}}</p><p v-if="adminNotice" role="status">{{adminNotice}}</p>
        <p v-if="adminLoaded && !adminBatches.length" class="sub">{{tx('Загруженных выписок нет.')}}</p>
        <div v-if="adminBatches.length" class="table-scroll"><table><thead><tr><th>{{tx('Выписка')}}</th><th>{{tx('Счёт')}}</th><th>{{tx('Период')}}</th><th>{{tx('Операции')}}</th><th></th></tr></thead><tbody>
          <tr v-for="batch in adminBatches" :key="batch.id"><td>{{batch.filename}}<small v-if="batch.status === 'preview'">{{tx('Предпросмотр')}}</small><small v-else-if="batch.status === 'committed'">{{tx('Подтверждена')}}</small></td><td>{{batch.account_name}} · {{batch.currency}}</td><td>{{formatShortDate(batch.period_from)}} – {{formatShortDate(batch.period_to)}}</td><td>{{tx('Создано выпиской: {n}',{n:batch.operation_count})}}<small>{{tx('Ранее существовало: {n}',{n:batch.linked_count})}}</small></td><td><button type="button" class="secondary tiny" :disabled="adminBusy || deleteUncertain" @click="selectDeleteBatch(batch)">{{tx('Удалить выписку')}}</button></td></tr>
        </tbody></table></div>
        <div v-if="deleteBatchId !== null" class="review-actions">
          <p><b>{{tx('Подтвердите удаление выписки')}}</b>: {{deletePreview?.filename}} · {{deletePreview?.account_name}} · {{deletePreview?.currency}} · {{formatShortDate(deletePreview?.period_from)}} – {{formatShortDate(deletePreview?.period_to)}}</p>
          <p>{{tx('Создано выпиской: {n}',{n:deletePreview?.operation_count})}} · {{tx('Ранее существовало: {n}',{n:deletePreview?.linked_count})}}</p>
          <p class="sub">{{tx('Операции, созданные этой выпиской, будут отменены. Действие может быть отклонено, если нарушатся остатки или есть зависимые операции.')}}</p>
          <label>{{tx('Причина удаления (не менее 10 символов)')}}<input v-model="deleteReason" :disabled="adminBusy || deleteUncertain"></label>
          <label><input v-model="deleteConfirmed" type="checkbox" :disabled="adminBusy || deleteUncertain"> {{tx('Подтверждаю удаление выписки и отмену созданных ею операций.')}}</label>
          <div class="form-actions"><button type="button" class="secondary" :disabled="adminBusy" @click="deleteBatchId = null">{{tx('Отмена')}}</button><button type="button" class="danger" :disabled="adminBusy || deleteUncertain || !deleteConfirmed || deleteReason.trim().length < 10" @click="deleteStatement">{{tx('Удалить выписку и отменить операции')}}</button></div>
        </div>
      </details>
      <div v-if="(canReview || canPost || canImport) && unreconciledAccounts.length" class="error" role="alert">
        <p>{{tx('Ручная оплата заявок с этих счетов временно недоступна до сверки выписок. Исправления операций и точные оплаты по выписке остаются доступны при наличии прав.')}}</p>
        <ul><li v-for="a in unreconciledAccounts" :key="a.id">{{a.name}} · {{a.currency}} — {{tx('Несверенных выписок: {n}',{n:a.unreconciled_statements})}}</li></ul>
      </div>
      <div v-if="canImport" class="bank-fields">
        <label>{{tx('Банковский счёт')}}<select v-model="accountId" :disabled="busy || reprocessBusy"><option value="">{{tx('Выберите счёт')}}</option><option v-for="a in bankAccounts" :key="a.id" :value="String(a.id)">{{a.name}} · {{a.currency}}</option></select></label>
        <label>{{tx('Валюта выписки')}}<select v-model="currency" :disabled="busy || reprocessBusy"><option value="">{{tx('Выберите валюту')}}</option><option v-for="c in ['UZS','USD','EUR']" :key="c" :value="c">{{c}}</option></select></label>
        <label class="button secondary file-label" :class="{disabled: busy || reprocessBusy || !validSelection}">{{tx('Загрузить выписку')}}<input ref="fileInput" type="file" accept=".xls,.html,.htm" :disabled="busy || reprocessBusy || !validSelection" @change="upload"></label>
      </div>
      <label v-if="canImport || canPost" class="bank-category">{{tx('Статья банковских комиссий')}}<select v-model="feeCategoryId" :disabled="busy || reprocessBusy" @change="feeTouched = true; feeSettingsError = ''"><option value="">{{tx('Не выбрана — комиссии на проверку')}}</option><option v-for="c in feeCategories" :key="c.id" :value="String(c.id)">{{c.name}}</option></select></label>
      <p v-if="canPost && !canManageFee" class="sub">{{tx('Выбор статьи комиссии не меняет общую настройку компании.')}}</p>
      <p v-if="feeSettingsError" class="error" role="alert">{{feeSettingsError}}</p>
      <p v-if="canImport && !canPost" class="sub">{{tx('Для проведения поступлений и комиссий требуется право записи операций.')}}</p>
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
        <p v-if="preview.opening_matches === true" class="good-text">{{tx('Начальный остаток выписки совпадает с остатком на сайте: {amount} {currency}.',{amount:money(preview.site_opening),currency})}}</p>
        <p v-else-if="preview.opening_matches === false" class="error" role="alert">{{tx('Начальный остаток не совпадает: на сайте {amount} {currency}. Автоматическое проведение поступлений, комиссий и прочих списаний остановлено.',{amount:preview.site_opening == null ? '—' : money(preview.site_opening),currency})}}</p>
        <p v-if="preview.statement.ready === true && preview.statement.balance_matches === true && preview.statement.declared_totals_match !== false" class="good-text">{{tx('Баланс выписки сходится.')}}</p>
        <p v-else class="error" role="alert">{{tx('Остатки или обороты выписки не подтверждены. Исправьте файл до загрузки.')}}</p>
        <p v-if="preview.full_reconciliation === true" class="good-text">{{tx('Все строки готовы к полной сверке. Результат будет перепроверен при подтверждении.')}}</p>
        <p v-else-if="preview.full_reconciliation === false" class="error" role="alert">{{tx('Не все строки выписки сверены. После подтверждения поступления, комиссии и прочие списания будут отложены, но точно сопоставленные заявки могут быть отмечены оплаченными. Спорные строки нужно проверить бухгалтеру.')}}</p>
        <p v-else class="error" role="alert">{{tx('Сервер не подтвердил полную сверку. Загрузите выписку заново.')}}</p>
        <p>{{tx('Точных совпадений с заявками: {n}',{n:count('auto_pay_candidate')})}} · {{tx('Уже оплачено на сайте: {n}',{n:count('paid_match')})}} · {{tx('На проверку: {n}',{n:reviewCount(rows,preview.full_reconciliation)})}}</p>
        <div class="table-scroll"><table><thead><tr><th>{{tx('Дата')}}</th><th>{{tx('Контрагент / документ')}}</th><th>{{tx('Назначение')}}</th><th>{{tx('Движение')}}</th><th>{{tx('Результат сверки')}}</th></tr></thead><tbody>
          <tr v-for="row in rows" :key="row.id"><td>{{formatShortDate(row.date)}}</td><td>{{row.correspondent_name}}<small>{{row.correspondent_inn || '—'}} · {{row.document_number}}</small></td><td>{{row.purpose}}</td><td>{{directionLabel(row.direction)}}<small>{{money(row.amount)}} {{currency}}</small></td><td>{{rowLabel(row,preview.full_reconciliation)}}<small v-if="categoryName(row)">{{tx('Статья')}}: {{categoryName(row)}}</small><small v-if="row.reason">{{tx(row.reason)}}</small><small v-if="row.request_id">{{tx('Заявка №{id}',{id:row.request_id})}}</small></td></tr>
        </tbody></table></div>
        <label class="confirm"><input v-model="confirmAccount" type="checkbox" :disabled="busy"> {{tx('Подтверждаю: номер счёта в выписке относится к выбранному счёту на сайте.')}}</label>
        <label class="confirm"><input v-model="confirmCurrency" type="checkbox" :disabled="busy"> {{tx('Подтверждаю валюту выписки: {currency}',{currency})}}</label>
        <p class="sub">{{tx('После подтверждения заявка оплачивается автоматически только при наличии права оплаты и успешном прохождении проверок. В остальных случаях строка останется бухгалтеру на проверку.')}}</p>
        <p class="sub">{{tx('Поступления, комиссии и прочие списания проводятся только при необходимых правах, совпадении начального остатка и полной сверке всех строк выписки.')}}</p>
        <div class="form-actions"><button type="button" class="secondary" :disabled="busy" @click="invalidate">{{tx('Отмена')}}</button><button type="button" :disabled="busy || !ready" @click="commit">{{tx('Подтвердить выписку')}}</button></div>
      </div>

      <div class="review-list">
        <h3>{{tx('Операции для проверки бухгалтера')}}</h3>
        <p class="sub">{{tx('Классификация помогает проверить выписку и сама по себе не создаёт операцию. Проведённые суммы учитываются в остатке и Cash Flow.')}}</p>
        <button v-if="canReview" type="button" class="secondary tiny" :disabled="reviewBusy !== null" @click="loadReviews">{{tx('Обновить список')}}</button>
        <p v-if="reviewError" class="error" role="alert">{{tx(reviewError)}}</p>
        <p v-else-if="reviewsLoaded && !reviews.length" class="sub">{{tx('Нет операций, ожидающих проверки.')}}</p>
        <div v-else-if="reviews.length" class="table-scroll"><table><thead><tr><th>{{tx('Дата')}}</th><th>{{tx('Контрагент / документ')}}</th><th>{{tx('Назначение')}}</th><th>{{tx('Движение')}}</th><th>{{tx('Результат сверки')}}</th></tr></thead><tbody>
          <tr v-for="row in reviews" :key="row.id"><td>{{formatShortDate(row.date)}}</td><td>{{row.correspondent_name}}<small>{{row.correspondent_inn || '—'}} · {{row.document_number}}</small></td><td>{{row.purpose}}</td><td>{{directionLabel(row.direction)}}<small>{{money(row.amount,row.currency)}} {{row.currency}}</small></td><td>{{label(row.status)}}<small v-if="row.reason">{{tx(row.reason)}}</small><small v-if="row.status === 'ambiguous_duplicate'">{{tx('Классификация сама не устраняет возможный повтор. Сопоставьте строку с ранее проведённой операцией.')}}</small>
            <div v-if="canReview && row.status === 'ambiguous_duplicate'" class="review-actions">
              <button type="button" class="secondary" :disabled="reviewBusy !== null" @click="findDuplicateCandidates(row)">{{tx('Найти ранее проведённую операцию')}}</button>
              <template v-if="duplicateActions[row.id]">
                <p v-if="!duplicateActions[row.id].items.length">{{tx('Подходящие проведённые строки не найдены. Повтор остаётся на проверке.')}}</p>
                <template v-else>
                  <label>{{tx('Выберите ранее проведённую строку')}}<select v-model="duplicateActions[row.id].selected" :disabled="reviewBusy !== null" @change="resetSelectionConfirmation(duplicateActions,row)"><option value="">{{tx('Выберите строку')}}</option><option v-for="candidate in duplicateActions[row.id].items" :key="candidate.line_id" :value="String(candidate.line_id)">{{formatShortDate(candidate.date)}} · {{candidate.document_number}} · {{candidate.account_name}} · {{money(candidate.amount,candidate.currency)}} {{candidate.currency}}</option></select></label>
                  <label>{{tx('Причина сопоставления (не менее 10 символов)')}}<input v-model="duplicateActions[row.id].note" :disabled="reviewBusy !== null"></label>
                  <label><input v-model="duplicateActions[row.id].confirm" type="checkbox" :disabled="reviewBusy !== null"> {{tx('Подтверждаю, что это повтор ранее проведённой операции.')}}</label>
                  <p class="sub">{{tx('Новая операция не создаётся; повтор исключается из дневного итога.')}}</p>
                  <button type="button" :disabled="reviewBusy !== null || !duplicateActions[row.id].selected || !duplicateActions[row.id].confirm || duplicateActions[row.id].note.trim().length < 10" @click="resolveDuplicate(row)">{{tx('Связать с проведённой операцией')}}</button>
                </template>
              </template>
            </div>
            <div v-if="canReview && ['review','reviewed'].includes(row.status)" class="review-actions">
              <button type="button" class="secondary" :disabled="reviewBusy !== null" @click="findTransferCandidates(row)">{{tx('Найти оформленный перевод')}}</button>
              <template v-if="transferActions[row.id]">
                <p v-if="!transferActions[row.id].items.length">{{tx('Подходящий оформленный перевод не найден. Строка остаётся на проверке.')}}</p>
                <template v-else>
                  <label>{{tx('Выберите оформленный перевод')}}<select v-model="transferActions[row.id].selected" :disabled="reviewBusy !== null" @change="resetSelectionConfirmation(transferActions,row)"><option value="">{{tx('Выберите перевод')}}</option><option v-for="candidate in transferActions[row.id].items" :key="candidate.ledger_id" :value="String(candidate.ledger_id)">{{formatShortDate(candidate.date)}} · {{candidate.from_account}} → {{candidate.to_account}} · {{candidate.reference}} · {{money(candidate.amount,candidate.currency)}} {{candidate.currency}}</option></select></label>
                  <template v-if="selectedTransfer(row)">
                    <p v-if="selectedTransfer(row).counterparty_account_confirmed === true" class="good-text">{{tx('Счёт другой стороны подтверждён выпиской.')}}</p>
                    <p v-else class="error" role="alert">{{tx('Счёт другой стороны не подтверждён выпиской. Проверьте реквизиты вручную перед сопоставлением.')}}</p>
                    <p class="sub">{{tx('Новая операция не создаётся. Выберите уже оформленный перевод между своими счетами.')}}</p>
                    <label>{{tx('Причина сопоставления (не менее 10 символов)')}}<input v-model="transferActions[row.id].note" :disabled="reviewBusy !== null"></label>
                    <label><input v-model="transferActions[row.id].confirm" type="checkbox" :disabled="reviewBusy !== null"> {{tx('Я проверил реквизиты и подтверждаю связь с оформленным переводом.')}}</label>
                    <button type="button" :disabled="reviewBusy !== null || !transferActions[row.id].confirm || transferActions[row.id].note.trim().length < 10" @click="linkTransfer(row)">{{tx('Связать с переводом')}}</button>
                  </template>
                </template>
              </template>
            </div>
            <div v-if="canReview && row.status === 'review' && row.direction === 'out'" class="review-actions">
              <button type="button" class="secondary" :disabled="reviewBusy !== null" @click="findCandidates(row)">{{tx('Найти заявку для сопоставления')}}</button>
              <template v-if="matchActions[row.id]">
                <p>{{tx('Дата по выписке')}}: {{formatShortDate(row.date)}}</p>
                <p v-if="!matchActions[row.id].items.length">{{tx('Подходящие заявки не найдены.')}}</p>
                <template v-else>
                  <label>{{tx('Выберите заявку')}}<select v-model="matchActions[row.id].selected" :disabled="reviewBusy !== null" @change="resetSelectionConfirmation(matchActions,row)"><option value="">{{tx('Выберите заявку')}}</option><option v-for="candidate in matchActions[row.id].items" :key="candidate.id" :value="String(candidate.id)">{{candidate.number}} · {{candidate.counterparty}} · {{candidate.counterparty_inn}} · {{money(candidate.amount,row.currency)}} {{row.currency}} · {{tx('Плановая дата')}}: {{formatShortDate(candidate.date)}} · {{candidateStatus(candidate.status)}}</option></select></label>
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

      <div v-if="canAllocate" class="review-list">
        <h3>{{tx('Поступления без статьи')}}</h3>
        <p class="sub">{{tx('Поступления уже проведены. Выберите статью и укажите основание, чтобы распределить их в Cash Flow без повторной операции.')}}</p>
        <button type="button" class="secondary tiny" :disabled="allocationBusy !== null" @click="loadUnallocated">{{tx('Обновить список')}}</button>
        <p v-if="allocationError" class="error" role="alert">{{tx(allocationError)}}</p>
        <p v-else-if="allocationLoaded && !unallocated.length" class="sub">{{tx('Нераспределённых поступлений нет.')}}</p>
        <div v-else-if="unallocated.length" class="table-scroll"><table><thead><tr><th>{{tx('Дата')}}</th><th>{{tx('Контрагент / документ')}}</th><th>{{tx('Счёт')}}</th><th>{{tx('Сумма')}}</th><th>{{tx('Статья и основание')}}</th></tr></thead><tbody>
          <tr v-for="row in unallocated" :key="row.id"><td>{{formatShortDate(row.date)}}</td><td>{{row.correspondent_name}}<small>{{row.correspondent_inn || '—'}} · {{row.document_number}}</small></td><td>{{row.account_name}}</td><td>{{money(row.amount,row.currency)}} {{row.currency}}</td><td><div class="review-actions">
            <label>{{tx('Статья поступления')}}<select :value="allocationActions[row.id]?.categoryId || ''" :disabled="allocationBusy !== null" @change="allocationActions[row.id] = {...allocationActions[row.id], categoryId: $event.target.value}"><option value="">{{tx('Выберите статью')}}</option><option v-for="c in incomeCategories" :key="c.id" :value="String(c.id)" :disabled="String(c.id) === String(row.category_id)">{{c.name}}</option></select></label>
            <label>{{tx('Основание (не менее 10 символов)')}}<input :value="allocationActions[row.id]?.reason || ''" :disabled="allocationBusy !== null" @input="allocationActions[row.id] = {...allocationActions[row.id], reason: $event.target.value}"></label>
            <button type="button" :disabled="allocationBusy !== null || !row.edit_token || !allocationActions[row.id]?.categoryId || String(allocationActions[row.id]?.categoryId) === String(row.category_id) || (allocationActions[row.id]?.reason || '').trim().length < 10" @click="assignIncome(row)">{{tx('Назначить статью')}}</button>
          </div></td></tr>
        </tbody></table></div>
      </div>

      <div v-if="canAllocate" class="review-list">
        <h3>{{tx('Списания без статьи')}}</h3>
        <p class="sub">{{tx('Списания уже проведены. До назначения статьи они не учитываются в её бюджете. После назначения изменится фактическое использование бюджета. При превышении жёсткого лимита сначала пересмотрите бюджет; для мягкого лимита укажите обоснование. Повторной операции не будет.')}}</p>
        <button type="button" class="secondary tiny" :disabled="outgoingBusy !== null" @click="loadUnallocatedOutgoing">{{tx('Обновить список')}}</button>
        <p v-if="outgoingError" class="error" role="alert">{{tx(outgoingError)}}</p>
        <p v-else-if="outgoingLoaded && !outgoingUnallocated.length" class="sub">{{tx('Нераспределённых списаний нет.')}}</p>
        <div v-else-if="outgoingUnallocated.length" class="table-scroll"><table><thead><tr><th>{{tx('Дата')}}</th><th>{{tx('Контрагент / документ')}}</th><th>{{tx('Счёт')}}</th><th>{{tx('Сумма')}}</th><th>{{tx('Статья и основание')}}</th></tr></thead><tbody>
          <tr v-for="row in outgoingUnallocated" :key="row.id"><td>{{formatShortDate(row.date)}}</td><td>{{row.correspondent_name}}<small>{{row.correspondent_inn || '—'}} · {{row.document_number}}</small></td><td>{{row.account_name}}</td><td>{{money(row.amount,row.currency)}} {{row.currency}}</td><td><div class="review-actions">
            <label>{{tx('Статья списания')}}<select :value="outgoingActions[row.id]?.categoryId || ''" :disabled="outgoingBusy !== null" @change="outgoingActions[row.id] = {...outgoingActions[row.id], categoryId: $event.target.value}"><option value="">{{tx('Выберите статью')}}</option><option v-for="c in feeCategories" :key="c.id" :value="String(c.id)" :disabled="String(c.id) === String(row.category_id)">{{c.name}}</option></select></label>
            <label>{{tx('Основание (не менее 10 символов)')}}<input :value="outgoingActions[row.id]?.reason || ''" :disabled="outgoingBusy !== null" @input="outgoingActions[row.id] = {...outgoingActions[row.id], reason: $event.target.value}"></label>
            <button type="button" :disabled="outgoingBusy !== null || !row.edit_token || !outgoingActions[row.id]?.categoryId || String(outgoingActions[row.id]?.categoryId) === String(row.category_id) || (outgoingActions[row.id]?.reason || '').trim().length < 10" @click="assignOutgoing(row)">{{tx('Назначить статью')}}</button>
          </div></td></tr>
        </tbody></table></div>
      </div>

      <div v-if="canPost" class="review-list">
        <h3>{{tx('Повторная проверка загруженной выписки')}}</h3>
        <p class="sub">{{tx('Проверьте ранее загруженную выписку заново. До подтверждения операции и остатки не изменятся.')}}</p>
        <p v-if="oldBatchesLoaded && !oldBatches.length" class="sub">{{tx('Выписок со строками для повторной проверки нет.')}}</p>
        <div class="bank-fields"><label>{{tx('Выберите выписку')}}<select v-model="selectedOldBatch" :disabled="busy || reprocessBusy"><option value="">{{tx('Выберите выписку')}}</option><option v-for="batch in oldBatches" :key="batch.id" :value="String(batch.id)">{{batch.account_name}} · {{batch.currency}} · {{formatShortDate(batch.period_from)}} – {{formatShortDate(batch.period_to)}} · {{batch.filename}}</option></select></label><button type="button" class="secondary" :disabled="!selectedOldBatch || busy || reprocessBusy" @click="previewOldBatch">{{tx('Проверить выписку')}}</button></div>
        <p v-if="reprocessError" class="error" role="alert">{{tx(reprocessError)}}</p><p v-if="reprocessNotice" role="status">{{reprocessNotice}}</p>
        <div v-if="reprocessPreview" class="bank-preview">
          <p>{{tx('Остаток на начало по выписке: {amount} {currency}; на сайте: {site} {currency}.',{amount:reprocessPreview.statement_opening == null ? '—' : money(reprocessPreview.statement_opening,oldCurrency),site:reprocessPreview.site_opening == null ? '—' : money(reprocessPreview.site_opening,oldCurrency),currency:oldCurrency})}}</p>
          <p v-if="reprocessPreview.opening_matches === false" class="error" role="alert">{{tx('Начальный остаток не совпадает. Поступления, комиссии и прочие списания останутся на проверке.')}}</p>
          <p v-else-if="reprocessPreview.opening_matches === true" class="good-text">{{tx('Начальный остаток выписки совпадает с остатком на сайте.')}}</p>
          <p v-if="reprocessPreview.full_reconciliation === true" class="good-text">{{tx('Все строки готовы к полной сверке. Результат будет перепроверен при подтверждении.')}}</p>
          <p v-else class="error" role="alert">{{tx('Не все строки выписки сверены. После подтверждения поступления, комиссии и прочие списания будут отложены, но точно сопоставленные заявки могут быть отмечены оплаченными. Спорные строки нужно проверить бухгалтеру.')}}</p>
          <div class="table-scroll"><table><thead><tr><th>{{tx('Дата')}}</th><th>{{tx('Контрагент / документ')}}</th><th>{{tx('Движение')}}</th><th>{{tx('Результат сверки')}}</th></tr></thead><tbody><tr v-for="row in reprocessPreview.rows || []" :key="row.id"><td>{{formatShortDate(row.date)}}</td><td>{{row.correspondent_name}}<small>{{row.correspondent_inn || '—'}} · {{row.document_number}}</small></td><td>{{directionLabel(row.direction)}}<small>{{money(row.amount,row.currency || oldCurrency)}} {{row.currency || oldCurrency}}</small></td><td>{{rowLabel(row,reprocessPreview.full_reconciliation)}}<small v-if="categoryName(row)">{{tx('Статья')}}: {{categoryName(row)}}</small><small v-if="row.reason">{{tx(row.reason)}}</small></td></tr></tbody></table></div>
          <p class="sub">{{tx('Поступления, комиссии и прочие списания проводятся только при необходимых правах, совпадении начального остатка и полной сверке всех строк выписки.')}}</p>
          <button type="button" :disabled="busy || reprocessBusy || !reprocessPreview.preview_token" @click="commitOldBatch">{{tx('Подтвердить повторную обработку')}}</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.bank-statements{margin:16px 0}.bank-fields{display:flex;gap:12px;flex-wrap:wrap;align-items:end}.bank-fields label:not(.file-label),.bank-category{display:grid;gap:4px;min-width:170px}.bank-category{max-width:420px;margin-top:12px}.file-label input{display:none}.file-label.disabled{opacity:.5;pointer-events:none}.bank-preview,.review-list{margin-top:20px}.bank-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.bank-summary div{display:grid;gap:4px;padding:10px;background:#f5f6fb;border-radius:8px}.bank-summary b{font-variant-numeric:tabular-nums}.good-text{color:#167145}.confirm{display:block;margin:10px 0}.table-scroll td{max-width:340px;white-space:normal;overflow-wrap:anywhere}.table-scroll small{display:block}.form-actions{margin-top:12px}.review-actions{display:grid;gap:7px;margin-top:8px;min-width:200px}.review-actions label{display:grid;gap:3px}
</style>
