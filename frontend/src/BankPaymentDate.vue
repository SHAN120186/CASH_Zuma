<script setup>
import {computed, ref, watch} from 'vue';
import {tx} from './i18n/index.js';
import {formatCents, formatShortDate, toCents} from './overview/format.js';

const props = defineProps({api: {type: Function, required: true}, batchId: {type: Number, required: true}});
const emit = defineEmits(['corrected']);
const doc = ref(''), number = ref(''), pending = ref(false), proof = ref(null), error = ref('');
const requestId = computed(() => Number(/^(?:CF-)?(\d+)$/i.exec(number.value.trim())?.[1] || 0));
watch([doc, number], () => {proof.value = null; error.value = '';});
async function submit(confirm) {
  if (pending.value || !requestId.value || !doc.value.trim() || (confirm && !proof.value)) return;
  pending.value = true; error.value = '';
  const params = {request_id: requestId.value, document_number: doc.value.trim()};
  if (confirm) params.expected = proof.value.expected;
  try {
    const result = await props.api('/api/bank-statements/' + props.batchId + '/payment-date/' + (confirm ? 'confirm' : 'preview'),
      {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(params)});
    if (confirm) emit('corrected', result);
    else proof.value = result;
  } catch (e) {error.value = e.message; proof.value = null;}
  finally {pending.value = false;}
}
</script>

<template>
  <details class="bank-help">
    <summary>{{tx('Исправить дату учтённой оплаты')}}</summary>
    <fieldset :disabled="pending">
      <label>{{tx('Номер платёжки банка')}}<input v-model="doc" type="text"></label>
      <label>{{tx('Номер заявки')}}<input v-model="number" type="text" placeholder="CF-…"></label>
      <button type="button" class="secondary" :disabled="!requestId || !doc.trim()" @click="submit(false)">{{tx('Проверить дату оплаты')}}</button>
      <p v-if="error" class="error" role="alert">{{tx(error)}}</p>
      <template v-if="proof">
        <p>{{proof.payee}} · {{formatCents(toCents(proof.amount),proof.currency)}} {{proof.currency}} · {{tx('ИНН')}} {{proof.bank_inn}}</p>
        <p v-if="proof.document_name">{{proof.document_name}}</p>
        <p>{{tx('Дата в журнале')}} {{formatShortDate(proof.old_date)}} → {{tx('Дата банка')}} {{formatShortDate(proof.bank_date)}}.
          {{tx('Дата согласования')}} {{formatShortDate(proof.approval.slice(0,10))}}.</p>
        <p>{{tx('Сумма останется в одной операции. Фактический расход перейдёт на дату банка.')}}</p>
        <button type="button" @click="submit(true)">{{tx('Применить дату банка')}}</button>
      </template>
    </fieldset>
  </details>
</template>
