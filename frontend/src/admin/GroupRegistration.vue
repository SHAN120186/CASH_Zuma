<script setup>
import {ref, computed, watch} from 'vue';
import {groupCodePayload, issueGroupCode} from './groupRegistration.js';
import {tx, N_, formatDate} from '../i18n/index.js';

const props = defineProps({api: {type: Function, required: true}, companies: {type: Array, required: true}});
const chatId = ref(''), companyCode = ref(''), code = ref(null);
const busy = ref(false), error = ref(''), copied = ref('');
const choices = computed(() => props.companies.filter(c => c.code !== 'UNASSIGNED'));
watch([chatId, companyCode], () => {code.value = null; error.value = ''; copied.value = '';});
const expires = value => formatDate(value, {timeZone: 'Asia/Tashkent', hour: '2-digit', minute: '2-digit'});

async function create() {
  if (busy.value) return;
  error.value = ''; copied.value = ''; code.value = null;
  let payload;
  try {payload = groupCodePayload(chatId.value, companyCode.value, choices.value);}
  catch (e) {error.value = e.message; return;}
  busy.value = true;
  try {code.value = await issueGroupCode(props.api, payload);}
  catch (e) {error.value = e.message;}
  finally {busy.value = false;}
}
async function copy() {
  try {await navigator.clipboard.writeText(code.value.command); copied.value = N_('Команда скопирована.');}
  catch {copied.value = N_('Выделите и скопируйте команду вручную.');}
}
</script>

<template>
  <section class="card admin-group-registration">
    <div class="card-h"><h3>{{ tx('Telegram: подключить группу к компании') }}</h3></div>
    <div class="card-b">
      <p class="sub">{{ tx('Каждая группа привязана ровно к одной компании. Добавьте бота в группу. Команда /id в группе покажет её ID. Выберите компанию, получите код и отправьте готовую команду в эту группу — личная привязка администратора к боту не нужна.') }}</p>
      <form class="form-grid admin-form" @submit.prevent="create">
        <label>{{ tx('ID Telegram-группы') }}<input v-model="chatId" type="text" inputmode="text" autocomplete="off" :placeholder="tx('Например, -1001234567890')" :disabled="busy" required></label>
        <label>{{ tx('Компания группы') }}<select v-model="companyCode" :disabled="busy" required>
          <option disabled value="">{{ tx('Выберите компанию') }}</option>
          <option v-for="c in choices" :key="c.id" :value="c.code">{{ c.name }}</option>
        </select></label>
        <p class="form-note wide">{{ tx('Группа получает сводку только выбранной компании; её увидят все участники. Код действует 10 минут и один раз, только для выбранной группы и компании. Новый код отменяет предыдущий. Неизвестная или неподключённая группа не получает финансовую сводку. Чтобы сменить компанию подключённой группы, сначала отключите группу.') }}</p>
        <div class="form-actions wide"><button :disabled="busy || !choices.length">{{ busy ? tx('Готовим код…') : tx('Получить код') }}</button></div>
      </form>
      <p v-if="error" class="error" role="alert">{{ tx(error) }}</p>
      <section v-if="code" class="admin-secret" role="status">
        <b>{{ tx('{company} · группа {chat}', {company: code.company_name, chat: code.chat_id}) }}</b>
        <p>{{ tx('Администратор Telegram-группы отправляет эту команду в указанную группу:') }}</p>
        <code>{{ code.command }}</code>
        <p class="sub">{{ tx('Код действует до {time} по Ташкенту. После подтверждения подключения сводка приходит по будням в 08:00. Проверка: «Сводка сейчас» в группе.', {time: expires(code.expires_at)}) }}</p>
        <div class="actions"><button type="button" class="secondary tiny" @click="copy">{{ tx('Скопировать команду') }}</button><button type="button" class="ghost tiny" @click="code = null">{{ tx('Скрыть') }}</button></div>
        <p v-if="copied" class="sub">{{ tx(copied) }}</p>
      </section>
    </div>
  </section>
</template>
