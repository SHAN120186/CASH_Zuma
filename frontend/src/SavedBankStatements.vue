<script setup>
import {ref, watch, onUnmounted} from 'vue';
import {tx} from './i18n/index.js';
import {toCents, formatCents, formatShortDate} from './overview/format.js';

const props = defineProps({api:{type:Function,required:true},companyId:{type:Number,required:true},
  canResume:{type:Boolean,default:false},busy:{type:Boolean,default:false},refreshKey:{type:Number,default:0}});
const emit = defineEmits(['resume']);
const open = ref(false), page = ref(1), hasMore = ref(false), items = ref([]), loaded = ref(false), loading = ref(false), error = ref('');
let generation = 0;
const money = (value, currency) => formatCents(toCents(value), currency);
function invalidate() {generation++; items.value=[]; loaded.value=false; loading.value=false; error.value=''; hasMore.value=false; page.value=1;}
watch(() => [props.companyId, props.canResume], () => {invalidate(); open.value=false;});
watch(() => props.refreshKey, () => {if (open.value) loadPage(1); else invalidate();});
onUnmounted(invalidate);
async function loadPage(number) {
  if (!open.value || !props.canResume || !props.companyId || !Number.isInteger(number) || number<1) return;
  const current=++generation, company=props.companyId;
  items.value=[]; loaded.value=false; error.value=''; loading.value=true;
  try {
    const result=await props.api(`/api/bank-statements/previews?page=${number}&page_size=30`);
    if (current!==generation || company!==props.companyId || !open.value || !props.canResume) return;
    if (result?.page!==number || !Array.isArray(result.items) || typeof result.has_more!=='boolean' || result.items.some(item=>!item?.id || item.status!=='preview' || !item.account?.id || !item.account?.currency)) throw new Error(tx('Не удалось проверить список сохранённых проверок.'));
    page.value=number; hasMore.value=result.has_more; items.value=result.items; loaded.value=true;
  } catch(e) {if (current===generation && open.value) error.value=e.message ? tx(e.message) : tx('Не удалось загрузить сохранённые проверки.');}
  finally {if (current===generation) loading.value=false;}
}
async function toggle() {
  if (!props.canResume) return;
  if (open.value) {open.value=false; invalidate(); return;}
  open.value=true; await loadPage(1);
}
function resume(item) {
  if (props.canResume && !props.busy && !loading.value && open.value && items.value.some(row=>row.id===item.id)) emit('resume',item);
}
</script>

<template>
  <section v-if="canResume" class="saved-statements" :aria-label="tx('Сохранённые проверки')">
    <h3>{{tx('Сохранённые проверки')}}</h3>
    <button type="button" class="secondary tiny" :aria-expanded="open" @click="toggle"><template v-if="open">{{tx('Скрыть сохранённые проверки')}}</template><template v-else>{{tx('Показать сохранённые проверки')}}</template></button>
    <template v-if="open">
      <p class="sub">{{tx('Продолжите проверку сохранённой выписки без повторной загрузки файла. Операции создаются только после отдельного подтверждения.')}}</p>
      <button type="button" class="secondary tiny" :disabled="loading || busy" @click="loadPage(1)">{{tx('Обновить список')}}</button>
      <p v-if="loading" role="status">{{tx('Загружаем данные…')}}</p><p v-if="error" class="error" role="alert">{{error}}</p>
      <p v-if="loaded && !items.length" class="sub">{{tx('Сохранённых проверок нет. Подтверждённые выписки доступны в истории.')}}</p>
      <div v-if="items.length" class="table-scroll"><table><thead><tr><th>{{tx('Выписка')}}</th><th>{{tx('Счёт')}}</th><th>{{tx('Период')}}</th><th>{{tx('Поступления по выписке')}}</th><th>{{tx('Списания по выписке')}}</th><th></th></tr></thead><tbody>
        <tr v-for="item in items" :key="item.id"><td>{{item.filename}}</td><td>{{item.account.name}} · {{item.account.currency}}</td><td>{{formatShortDate(item.period_from)}} – {{formatShortDate(item.period_to)}}</td><td>{{money(item.credit_total,item.account.currency)}} {{item.account.currency}}</td><td>{{money(item.debit_total,item.account.currency)}} {{item.account.currency}}</td><td><button type="button" class="secondary tiny" :disabled="loading || busy" @click="resume(item)">{{tx('Открыть проверку')}}</button></td></tr>
      </tbody></table></div>
      <div v-if="loaded && (page>1 || hasMore)" class="form-actions"><button type="button" class="secondary tiny" :disabled="loading || busy || page<=1" @click="loadPage(page-1)">{{tx('Назад')}}</button><span>{{tx('Страница {n}',{n:page})}}</span><button type="button" class="secondary tiny" :disabled="loading || busy || !hasMore" @click="loadPage(page+1)">{{tx('Далее')}}</button></div>
    </template>
  </section>
</template>

<style scoped>
.saved-statements{margin:20px 0}.table-scroll td{max-width:340px;white-space:normal;overflow-wrap:anywhere}.form-actions{display:flex;gap:10px;align-items:center;margin-top:10px}
</style>
