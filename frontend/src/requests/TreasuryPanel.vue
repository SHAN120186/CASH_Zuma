<script setup>
// Казначейство в карточке заявки: загружает состояние с сервера и показывает панель
// корпоративной карты или цепочку валютной заявки. Действия доступны по полям can_* ответа сервера.
import {ref, watch, onUnmounted} from 'vue';
import {tx} from '../i18n/index.js';
import CardPaymentPanel from './CardPaymentPanel.vue';
import CurrencyChainPanel from './CurrencyChainPanel.vue';

const props = defineProps({api: {type: Function, required: true}, request: {type: Object, required: true}, money: {type: Function, required: true}});
const emit = defineEmits(['busy', 'changed', 'open-request']);
const data = ref(null), error = ref(''), loading = ref(false);
let alive = true, generation = 0;

async function load() {
  const current = ++generation, id = props.request.id;
  loading.value = true; error.value = '';
  try {
    const result = await props.api(`/api/treasury/requests/${id}`);
    if (alive && current === generation && props.request.id === id) data.value = result;
  } catch (e) { if (alive && current === generation) error.value = e.message; }
  finally { if (alive && current === generation) loading.value = false; }
}
watch(() => [props.request.id, props.request.version, props.request.status], load, {immediate: true});
onUnmounted(() => { alive = false; generation++; });

function changed(result) {
  if (result?.request_id === props.request.id) data.value = result;
  emit('changed', result);
}
</script>

<template>
  <p v-if="loading && !data" class="sub" role="status">{{ tx('Загружаем данные казначейства…') }}</p>
  <p v-if="error" class="error" role="alert">{{ tx(error) }}</p>
  <CardPaymentPanel v-if="data?.kind === 'card' && data.card" :api="api" :request="request" :card="data.card" :money="money"
    @busy="emit('busy', $event)" @changed="changed" />
  <CurrencyChainPanel v-if="data?.kind === 'fx' && data.fx" :api="api" :request="request" :fx="data.fx" :money="money"
    @busy="emit('busy', $event)" @changed="changed" @open-request="emit('open-request', $event)" />
</template>
