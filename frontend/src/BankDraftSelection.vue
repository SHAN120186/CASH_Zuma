<script setup>
import {computed, onUnmounted, ref} from 'vue';
import {tx} from './i18n/index.js';
import {formatCents, formatShortDate, toCents} from './overview/format.js';

const props = defineProps({api: {type: Function, required: true}, batchId: {type: Number, required: true}, disabled: Boolean});
const open = ref(false), busy = ref(false), problem = ref(''), message = ref('');
const rows = ref([]), currency = ref(''), saved = ref(null), numbers = ref(''), onlySelected = ref(false), page = ref(1);
const pageSize = 40;
let generation = 0;
onUnmounted(() => { generation++; });

const selected = computed(() => {
  const tokens = numbers.value.trim().split(/[\s,;]+/).filter(Boolean);
  if (tokens.some(t => !/^\d+$/.test(t))) return null;
  const indices = new Set(tokens.map(Number));
  const matches = rows.value.filter(row => indices.has(row.line));
  if (indices.size !== tokens.length || matches.length !== indices.size ||
      new Set(matches.map(row => row.line)).size !== indices.size) return null;
  return matches;
});
const ids = computed(() => new Set((selected.value || []).map(row => row.id)));
const changed = computed(() => saved.value && selected.value && (saved.value.line_ids.length !== ids.value.size ||
  saved.value.line_ids.some(id => !ids.value.has(id))));
const totals = computed(() => {
  const result = {in:0, out:0};
  for (const row of selected.value || []) result[row.direction] += toCents(row.amount);
  return result;
});
const filtered = computed(() => onlySelected.value ? (selected.value || []) : rows.value);
const pages = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize)));
const shown = computed(() => filtered.value.slice((Math.min(page.value, pages.value)-1)*pageSize, Math.min(page.value, pages.value)*pageSize));

function useSaved(selection) {
  const keys = new Set(selection.line_ids);
  saved.value = selection;
  numbers.value = rows.value.filter(row => keys.has(row.id)).map(row => row.line).join(', ');
}
async function load() {
  const epoch = ++generation;
  busy.value = true; problem.value = ''; message.value = '';
  try {
    const draft = await props.api('/api/bank-statements/' + props.batchId);
    if (epoch !== generation) return;
    if (draft.id !== props.batchId || draft.status !== 'preview' || !Array.isArray(draft.draft_selection?.line_ids))
      throw new Error(tx('Выбор доступен только в черновике.'));
    rows.value = draft.rows; currency.value = draft.account.currency; page.value = 1;
    useSaved(draft.draft_selection);
  } catch (e) { if (epoch === generation) {saved.value = null; problem.value = tx(e.message);} }
  finally { if (epoch === generation) busy.value = false; }
}
function toggle() {
  open.value = !open.value;
  if (open.value) load();
}
function mark(row) {
  const indices = new Set(selected.value.map(item => item.line));
  if (indices.has(row.line)) indices.delete(row.line); else indices.add(row.line);
  numbers.value = [...indices].sort((a,b) => a-b).join(', '); message.value = '';
}
async function save() {
  if (busy.value || props.disabled || !changed.value) return;
  const epoch = ++generation;
  busy.value = true; problem.value = ''; message.value = '';
  try {
    const result = await props.api('/api/bank-statements/' + props.batchId + '/draft-selection', {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({version:saved.value.version, line_ids:[...ids.value]})
    });
    if (epoch !== generation) return;
    useSaved(result); message.value = tx('Подборка сохранена без проведения.');
  } catch (e) { if (epoch === generation) problem.value = tx(e.message); }
  finally { if (epoch === generation) busy.value = false; }
}
</script>

<template>
  <button type="button" class="secondary tiny" :disabled="busy || disabled" :aria-expanded="open" @click="toggle">{{tx('Подборка строк · № {n}',{n:batchId})}}</button>
  <section v-if="open" class="draft-picks">
    <p class="sub">{{tx('Подборка сохраняет отметки для проверки. Проведение проверенных строк всей выписки подтверждается отдельно.')}}</p>
    <p v-if="problem" role="alert" class="error">{{problem}}</p>
    <button v-if="problem" type="button" class="secondary tiny" :disabled="busy || disabled" @click="load">{{tx('Обновить подборку')}}</button>
    <p v-if="busy" role="status">{{tx('Обрабатываем выписку…')}}</p>
    <fieldset v-if="saved" :disabled="busy || disabled">
      <label>{{tx('Номера строк выписки через запятую')}}<textarea v-model="numbers" rows="2" @input="message='';page=1"></textarea></label>
      <p v-if="!selected" role="alert" class="error">{{tx('Введите номера существующих строк без повторов.')}}</p>
      <p v-else>{{tx('Выбрано строк: {n}',{n:ids.size})}} · {{tx('Поступления')}} {{formatCents(totals.in,currency)}} · {{tx('Списания')}} {{formatCents(totals.out,currency)}} {{currency}}</p>
      <button type="button" :disabled="!changed" @click="save">{{tx('Сохранить подборку')}}</button>
      <p v-if="message" role="status">{{message}}</p>
      <label><input type="checkbox" v-model="onlySelected" @change="page=1"> {{tx('Только выбранные строки')}}</label>
      <div class="table-scroll"><table><thead><tr><th>{{tx('Строка')}}</th><th>{{tx('Дата')}}</th><th>{{tx('Документ')}}</th><th>{{tx('Контрагент')}}</th><th>{{tx('Сумма')}}</th></tr></thead><tbody>
        <tr v-for="row in shown" :key="row.id"><td><label><input type="checkbox" :checked="ids.has(row.id)" :disabled="!selected" @change="mark(row)"> {{row.line}}</label></td><td>{{formatShortDate(row.date)}}</td><td>{{row.document_number}}</td><td>{{row.correspondent_name}}</td><td>{{row.direction==='in'?'+':'−'}}{{formatCents(toCents(row.amount),currency)}}</td></tr>
      </tbody></table></div>
      <div v-if="pages>1" class="form-actions"><button type="button" class="secondary tiny" :disabled="page<=1" @click="page=Math.min(page,pages)-1">{{tx('Назад')}}</button>{{Math.min(page,pages)}} / {{pages}}<button type="button" class="secondary tiny" :disabled="page>=pages" @click="page++">{{tx('Далее')}}</button></div>
    </fieldset>
  </section>
</template>

<style scoped>
.draft-picks fieldset{border:0;margin:0;padding:0;min-width:0}.draft-picks textarea{display:block;width:100%;min-width:12rem}.draft-picks .table-scroll{max-height:26rem;overflow:auto}.draft-picks{margin:.5rem 0}.draft-picks label{display:block;margin:.5rem 0}
</style>
