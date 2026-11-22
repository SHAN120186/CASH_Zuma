<script setup>
import {computed, onUnmounted, ref, watch} from 'vue';
import {N_, tx} from '../i18n/index.js';
import {formatCents, formatLongDate, toCents} from './format.js';
import UncoveredBankPayments from './UncoveredBankPayments.vue';

const props = defineProps({api:Function, companyId:Number, accounts:Array, currency:String, today:String, refresh:Number});
const emit = defineEmits(['open-request']);
const opened = ref(false), accountId = ref(''), source = ref(''), file = ref(null);
const saved = ref([]), sourcesLoading = ref(false), sourcesError = ref('');
const data = ref(null), loading = ref(false), error = ref('');
const tab = ref('bank'), filter = ref('all'), limit = ref(30);
const bankAccounts = computed(() => (props.accounts || []).filter(a => a.kind === 'bank' && !a.archived && a.currency === props.currency));
const account = computed(() => bankAccounts.value.find(a => String(a.id) === accountId.value));
const labels = {
  matched:N_('Подтверждено по выписке'), candidate:N_('Возможное совпадение'), bank_only:N_('Связь списания с заявкой не найдена'),
  fee:N_('Комиссия банка'), incoming:N_('Поступление'), other:N_('Другая операция'),
};
const counts = computed(() => {
  const grouped = {};
  for (const row of data.value?.rows || []) grouped[row.status] = (grouped[row.status] || 0) + 1;
  return grouped;
});
const records = computed(() => {
  if (!data.value) return [];
  if (tab.value === 'site') return data.value.unmatched_payments;
  return filter.value === 'all' ? data.value.rows : data.value.rows.filter(row => row.status === filter.value);
});
const ready = computed(() => !!account.value && (source.value === 'file' ? !!file.value : saved.value.some(s => String(s.id) === source.value)));
const money = val => formatCents(toCents(val), props.currency);
const number = id => 'CF-' + String(id).padStart(5, '0');
function difference(bank, site) {
  const a = toCents(bank), b = toCents(site);
  return a === null || b === null ? '—' : formatCents(a - b, props.currency, {sign:true});
}
const query = () => new URLSearchParams({account_id:accountId.value, currency:props.currency});
let sourceEpoch = 0, reportEpoch = 0;
function clearReport() {
  reportEpoch++;
  data.value = null;
  loading.value = false;
  error.value = '';
  tab.value = 'bank';
  filter.value = 'all';
  limit.value = 30;
}
watch([() => props.companyId, bankAccounts], ([company, accounts], previous) => {
  if (!previous || previous[0] !== company || !accounts.some(a => String(a.id) === accountId.value)) {
    accountId.value = accounts.length === 1 ? String(accounts[0].id) : '';
  }
}, {immediate:true});
async function listSources() {
  const epoch = ++sourceEpoch;
  clearReport();
  saved.value = [];
  source.value = '';
  file.value = null;
  sourcesError.value = '';
  sourcesLoading.value = false;
  if (!opened.value || !props.companyId || !account.value) return;
  sourcesLoading.value = true;
  try {
    const result = await props.api('/api/bank-statements/period-sources/list?' + query());
    if (epoch !== sourceEpoch) return;
    saved.value = result.items;
    source.value = saved.value.length ? String(saved.value[0].id) : 'file';
  } catch (e) {
    if (epoch !== sourceEpoch) return;
    sourcesError.value = e.message;
    source.value = 'file';
  } finally {
    if (epoch === sourceEpoch) sourcesLoading.value = false;
  }
}
watch([opened, accountId, () => props.companyId, () => props.currency], listSources);
watch(source, () => {file.value = null; clearReport();}, {flush:'sync'});
watch(() => props.refresh, () => {if (opened.value) listSources();});
watch([tab, filter], () => {limit.value = 30;});
onUnmounted(() => {sourceEpoch++; reportEpoch++;});
function chooseFile(event) {
  clearReport();
  file.value = event.target.files?.[0] || null;
}
async function compare(confirmAccount = false) {
  if (!opened.value || !ready.value || loading.value) return;
  const params = query();
  if (confirmAccount === true && source.value === 'file' && data.value?.requires_confirmation === true) {
    params.set('confirm_account', 'true');
  }
  clearReport();
  const epoch = reportEpoch;
  loading.value = true;
  try {
    const result = source.value === 'file'
      ? await props.api('/api/bank-statements/compare-period?' + params, {
        method:'POST', headers:{'Content-Type':'application/octet-stream', 'X-Filename':encodeURIComponent(file.value.name)}, body:file.value,
      })
      : await props.api('/api/bank-statements/period-sources/' + encodeURIComponent(source.value) + '?' + params);
    if (epoch !== reportEpoch) return;
    if (String(result.account?.id) !== accountId.value || result.account.currency !== props.currency ||
        !result.statement || !Array.isArray(result.rows) || !Array.isArray(result.unmatched_payments)) throw Error(tx('Некорректный ответ сервера'));
    data.value = result;
  } catch (e) {
    if (epoch === reportEpoch) error.value = e.message || tx('Ошибка загрузки');
  } finally {
    if (epoch === reportEpoch) loading.value = false;
  }
}
</script>
<template>
 <section class="card period">
  <div class="card-h"><h2>{{tx('Сверка с банком')}}</h2>
   <button type="button" class="secondary" :aria-expanded="opened" @click="opened=!opened">{{opened ? tx('Скрыть') : tx('Открыть сверку')}}</button>
  </div>
  <div v-if="opened" class="card-b">
   <div class="controls">
    <label>{{tx('Банковский счёт')}}
     <select v-model="accountId"><option value="">{{tx('Выберите счёт')}}</option><option v-for="a in bankAccounts" :value="String(a.id)" :key="a.id">{{a.name}} · {{a.currency}}</option></select>
    </label>
    <template v-if="account && !sourcesLoading">
     <label v-if="saved.length">{{tx('Выписка для сверки')}}
      <select v-model="source"><option v-for="s in saved" :key="s.id" :value="String(s.id)">{{s.period_from}} — {{s.period_to}} · {{s.filename}}</option><option value="file">{{tx('Выбрать файл выписки')}}</option></select>
     </label>
     <label v-if="source==='file'">{{tx('Файл выписки')}}<input type="file" accept=".xlsx,.xls,.html,.htm" @change="chooseFile"></label>
     <button v-if="!data?.requires_confirmation" type="button" :disabled="!ready || loading" @click="compare()">{{tx('Сверить с заявками')}}</button>
    </template>
   </div>
   <p v-if="!bankAccounts.length" class="sub">{{tx('В этой валюте у компании нет банковских счетов.')}}</p>
   <p v-if="sourcesError" role="alert" class="error">{{tx(sourcesError)}}</p>
   <p v-if="sourcesLoading || loading" role="status">{{tx('Загружаем данные…')}}</p>
   <p v-else-if="error" role="alert" class="error">{{tx(error)}}</p>
   <template v-if="data">
    <h3 class="period-title">{{formatLongDate(data.statement.period_from)}} — {{formatLongDate(data.statement.period_to)}}</h3>
    <p class="sub source">{{data.statement.owner_name}} · {{data.statement.account_number}} · {{data.account.currency}}</p>
    <p v-if="!data.statement.balance_matches || data.statement.declared_totals_match===false || !data.statement.ready" role="alert" class="error">{{tx('Проверьте итоги выписки и полноту её строк.')}}</p>
    <dl class="totals">
     <div><dt>{{tx('Остаток на начало')}}</dt><dd>{{money(data.statement.opening_balance)}}</dd></div>
     <div><dt>{{tx('Поступления')}}</dt><dd>{{money(data.statement.credit_total)}}</dd></div>
     <div><dt>{{tx('Списания')}}</dt><dd>{{money(data.statement.debit_total)}}</dd></div>
     <div><dt>{{tx('Остаток на конец')}}</dt><dd>{{money(data.statement.closing_balance)}}</dd></div>
    </dl>
    <div v-if="data.requires_confirmation" class="check-account">
     <p>{{tx('Проверьте владельца и номер счёта в выписке выше. Подтвердите выбранный счёт и валюту для сравнения с оплатами сайта.')}}</p>
     <p><b>{{data.account.name}} · {{data.account.currency}}</b></p>
     <button type="button" @click="compare(true)">{{tx('Подтвердить счёт и валюту для сверки')}}</button>
    </div>
    <template v-else>
    <div v-if="data.site" class="site-totals"><span>{{tx('На начало в Cash Zuma')}} <b>{{money(data.site.opening_balance)}}</b></span>
     <span>{{tx('На конец в Cash Zuma')}} <b>{{money(data.site.closing_balance)}}</b></span>
     <span>{{tx('Разница на конец (банк − сайт)')}} <b>{{difference(data.statement.closing_balance,data.site.closing_balance)}}</b></span></div>
    <div class="views" role="group" :aria-label="tx('Показать операции')">
     <button type="button" :aria-pressed="tab==='bank'" :class="{secondary:tab!=='bank'}" @click="tab='bank'">{{tx('По выписке')}} · {{data.rows.length}}</button>
     <button type="button" :aria-pressed="tab==='site'" :class="{secondary:tab!=='site'}" @click="tab='site'">{{tx('Оплаты сайта без подтверждённой связи')}} · {{data.unmatched_payments.length}}</button>
    </div>
    <label v-if="tab==='bank'" class="filter">{{tx('Результат сверки')}}
     <select v-model="filter"><option value="all">{{tx('Все строки')}}</option><option v-for="(label,status) in labels" :value="status" :key="status">{{tx(label)}} · {{counts[status]||0}}</option></select>
    </label>
    <p v-else class="sub">{{tx('Оплаты по этому счёту за период выписки. Возможные совпадения требуют проверки бухгалтером.')}}</p>
    <p v-if="!records.length" class="sub">{{tx('Нет строк для показа.')}}</p>
    <div v-else class="table-scroll" tabindex="0">
     <table><thead><tr><th>{{tx('Дата / документ')}}</th><th>{{tx('Контрагент')}}</th><th class="num">{{tx('Сумма')}}</th><th>{{tx('Результат сверки')}}</th><th>{{tx('Заявка')}}</th></tr></thead><tbody>
      <tr v-for="row in records.slice(0,limit)" :key="tab==='bank'?row.line:row.request_id">
       <td class="row-date" :data-label="tx('Дата / документ')">{{row.date}}<small v-if="row.document_number">№ {{row.document_number}}</small></td>
       <td class="party">{{tab==='bank'?row.correspondent_name:row.counterparty}}<details v-if="row.purpose"><summary>{{tx('Назначение платежа')}}</summary><small>{{row.purpose}}</small></details></td>
       <td class="num" :data-label="tx('Сумма')">{{tab==='bank'?(row.direction==='in'?'+':'−'):''}}{{money(row.amount)}}</td>
       <td><span :class="['pill',{matched:'good',candidate:'warn',bank_only:'warn',app_only:'warn'}[row.status] || 'gray']">{{row.status==='app_only'?tx('Нет связи с выпиской'):labels[row.status]?tx(labels[row.status]):tx('Требует проверки')}}</span>
        <small v-if="row.reason">{{tx(row.reason)}}</small></td>
       <td><template v-if="tab==='bank' && row.request_details?.length">
        <div v-for="item in row.request_details" :key="item.request_id" class="request-match">
         <button type="button" class="secondary tiny" @click="emit('open-request',{id:item.request_id})">{{number(item.request_id)}}</button>
         <small v-if="item.counterparty">{{item.counterparty}}</small><small v-if="item.amount!=null">{{money(item.amount)}} {{data.account.currency}}</small>
         <small v-if="item.date">{{tx('Оплачено на сайте {date}',{date:item.date})}}</small>
         <small v-if="item.document_number">{{tx('Документ сайта №{number}',{number:item.document_number})}}</small>
        </div>
       </template><template v-else><button v-for="id in (tab==='bank'?row.request_ids:[row.request_id])" :key="id" type="button" class="secondary tiny" @click="emit('open-request',{id})">{{number(id)}}</button></template></td>
      </tr>
     </tbody></table>
    </div>
    <button v-if="records.length>limit" type="button" class="secondary more" @click="limit+=30">{{tx('Показать ещё')}}</button>
    </template>
   </template>
   <UncoveredBankPayments v-if="account" :key="`${companyId}-${account.id}-${currency}`" :api="api" :account="account" :today="today" :refresh="refresh" @open-request="emit('open-request',$event)"/>
  </div>
 </section>
</template>
<style scoped>
.check-account{padding:12px 16px;border-left:3px solid var(--warn);background:var(--warnl);border-radius:5px}.check-account p{margin:0 0 10px}.request-match+.request-match{margin-top:8px}
.period{margin-bottom:16px}.period>.card-h{padding-bottom:16px}.period .card-b{padding-top:0}.controls,.views,.site-totals{display:flex;align-items:end;flex-wrap:wrap;gap:12px}.controls label{min-width:0;max-width:100%}.controls input,.controls select{max-width:100%}.period-title{margin:24px 0 4px}.source{overflow-wrap:anywhere}.totals{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(180px,100%),1fr));gap:10px;margin:14px 0}.totals>div{padding:12px 14px;background:var(--surface-muted);border-radius:10px}.totals dt,.site-totals{font-size:.85rem;color:var(--mut)}.totals dd{margin:6px 0 0;font-size:1.1rem;font-weight:800;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.site-totals b{display:block}.views{margin:24px 0 12px}.views button{white-space:normal}.filter{max-width:320px}.caution{color:var(--warn)}.table-scroll{overflow-x:auto;max-width:100%;margin-top:12px}.period table{border-collapse:collapse;width:100%}.period th,.period td{text-align:left;padding:9px;vertical-align:top;border-bottom:1px solid var(--line)}.period .num{white-space:nowrap;text-align:right;font-variant-numeric:tabular-nums}.period td small{display:block}.period td .pill{white-space:normal}.period .party{min-width:165px;overflow-wrap:anywhere}.period details{font-size:.8rem;margin-top:4px}.period summary{cursor:pointer}.more{margin-top:12px}
@media(max-width:600px){.controls{display:grid;grid-template-columns:minmax(0,1fr)}.controls select,.controls input{width:100%}.totals{grid-template-columns:repeat(2,minmax(0,1fr))}.views button{flex:1 1 140px}.site-totals{display:grid;gap:5px}.site-totals b{display:inline}}
@media(max-width:600px){
 .period table,.period tbody{display:block}
 .period thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
 .period tr{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:8px;padding:12px}
 .period td{display:block;grid-column:1 / -1;border:0;padding:0}
 .period td.row-date{grid-column:1;grid-row:1}
 .period td.num{grid-column:2;grid-row:1;align-self:start;font-weight:800}
 .period td[data-label]::before{content:attr(data-label);display:block;color:var(--mut);font-weight:500;font-size:.72rem}
 .period .party{min-width:0}
 .period td:empty{display:none}
}
</style>
