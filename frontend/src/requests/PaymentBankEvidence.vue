<script setup>
import {ref, watch} from 'vue';
import {tx} from '../i18n/index.js';
import {formatCents, formatLongDate, toCents} from '../overview/format.js';

const props = defineProps({api:Function, companyId:Number, account:Object, requestId:Number, reference:String, day:String, canOpen:Boolean});
const emit = defineEmits(['open-source', 'already-paid']);
const result = ref(null), error = ref(''), loading = ref(false);
watch(() => [props.companyId, props.account?.id, props.account?.currency, props.requestId, props.reference?.trim(), props.day],
  ([company, account, currency, requestId, reference, day], _, cleanup) => {
    result.value = null;
    error.value = '';
    loading.value = false;
    if (!company || !account || !requestId || !day || !reference || reference.length < 2) return;
    let active = true;
    const timer = setTimeout(async () => {
      loading.value = true;
      try {
        const params = new URLSearchParams({account_id:account, currency, fact_date:day, reference});
        const response = await props.api('/api/bank-statements/payment-evidence/' + requestId + '?' + params);
        if (active && response.request_id === requestId) {
          if (response.request_status === 'paid') emit('already-paid', requestId);
          else result.value = response;
        }
      } catch (e) {
        if (active) error.value = e.message;
      } finally {
        if (active) loading.value = false;
      }
    }, 350);
    cleanup(() => {active = false; clearTimeout(timer);});
  }, {immediate:true, flush:'sync'});
</script>
<template>
  <div aria-live="polite">
    <p v-if="loading" class="sub">{{tx('Ищем платёжку в выписках…')}}</p>
    <p v-else-if="error" class="warning">{{tx(error)}}</p>
    <p v-if="result?.rows.length" class="sub">{{tx('По учёту на {date} есть {amount} {currency}.',{
      date:formatLongDate(day),amount:formatCents(toCents(result.book),account.currency),currency:account.currency})}}
      <span v-if="toCents(result.shortfall)>0">{{tx('До суммы заявки не хватает {amount} {currency}.',{
        amount:formatCents(toCents(result.shortfall),account.currency),currency:account.currency})}}</span>
    </p>
    <section v-for="row in result?.rows" :key="row.batch_id + '-' + row.line" class="form-note">
      <strong>{{tx('Списание в выписке')}} · № {{row.reference}} · {{formatLongDate(row.date)}}</strong>
      <p>{{formatCents(toCents(row.amount),row.currency)}} {{row.currency}} · {{tx('Выписка № {id}',{id:row.batch_id})}} · {{row.status==='preview'?tx('Черновик'):tx('Подтверждена')}}</p>
      <p v-if="row.status==='preview' && result.request_status==='approved'">{{tx('Номер и сумма найдены в черновике. Заявка ожидает учёта. Сначала сверьте строку выписки.')}}</p>
      <p v-if="result.approved_on && row.date < result.approved_on">{{tx('Дата платёжки раньше согласования ({date}). При учёте по выписке сохраняются обе даты.',{date:formatLongDate(result.approved_on)})}}</p>
      <details><summary>{{tx('Посмотреть платёжку')}}</summary>
        <p>{{row.counterparty}} · {{tx('ИНН')}} {{row.inn || '—'}}</p><p>{{row.purpose}}</p>
      </details>
      <button v-if="canOpen && (row.status==='preview' || row.line_status==='review')" type="button" class="secondary" @click="emit('open-source',row)">{{tx('Открыть строку выписки')}}</button>
    </section>
  </div>
</template>
