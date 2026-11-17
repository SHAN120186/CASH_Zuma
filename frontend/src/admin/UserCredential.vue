<script setup>
import {computed, onBeforeUnmount, onMounted, ref, watch} from 'vue';
import {tx, N_} from '../i18n/index.js';

const props = defineProps({
  api: {type: Function, required: true},
  user: {type: Object, required: true},
  enabled: {type: Boolean, default: false},
});
const emit = defineEmits(['updated']);
const password = ref(''), knownPassword = ref(''), reason = ref('');
const importing = ref(false), busy = ref(false), error = ref(''), notice = ref('');
const active = computed(() => props.user?.active !== false && props.user?.credential_state !== 'archived' && props.user?.state !== 'archived');
const allowed = computed(() => props.enabled && active.value && !!props.user?.id);
const canReveal = computed(() => allowed.value && props.user.credential_state === 'stored');
const canImport = computed(() => allowed.value && ['missing', 'stale'].includes(props.user.credential_state));
const canSave = computed(() => canImport.value && !busy.value && knownPassword.value.length >= 1 && knownPassword.value.length <= 128 && reason.value.trim().length >= 10);
const status = computed(() => {
  if (!active.value) return N_('Учётная запись в архиве. Просмотр и сохранение пароля недоступны.');
  if (!props.enabled) return N_('Просмотр и сохранение паролей сейчас недоступны.');
  if (props.user.credential_state === 'stored') return N_('Действующий пароль сохранён. Показывается только по нажатию кнопки.');
  if (props.user.credential_state === 'missing') return N_('Действующий пароль не сохранён. Если он известен, его можно сохранить для восстановления доступа.');
  if (props.user.credential_state === 'stale') return N_('Сохранённый пароль больше не соответствует учётной записи.');
  if (props.user.credential_state === 'locked') return N_('Сохранённый пароль сейчас недоступен.');
  return N_('Состояние пароля неизвестно. Обновите карточку пользователя.');
});

let epoch = 0, revealTimer = null, inputTimer = null, disposed = false, pageDocument;
const hidden = () => pageDocument?.hidden || pageDocument?.visibilityState === 'hidden';
function clearRevealed() {
  clearTimeout(revealTimer); revealTimer = null;
  password.value = '';
}
function clearInput() {
  clearTimeout(inputTimer); inputTimer = null;
  knownPassword.value = '';
}
function clear() {
  epoch++;
  clearRevealed(); clearInput();
  reason.value = ''; importing.value = false; busy.value = false; error.value = ''; notice.value = '';
}
function hide() {
  epoch++;
  clearRevealed(); busy.value = false; notice.value = ''; error.value = '';
}
const current = (turn, id, username) => !disposed && turn === epoch && allowed.value && !hidden() && props.user.id === id && props.user.username === username;
const post = (id, action, body) => props.api(`/api/admin/users/${id}/credential/${action}`, {
  method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
});

async function reveal() {
  if (!canReveal.value || busy.value || hidden() || disposed) return;
  const turn = ++epoch, {id, username} = props.user;
  clearRevealed(); error.value = ''; notice.value = ''; busy.value = true;
  try {
    const value = await post(id, 'reveal', {reason: N_('Восстановление доступа сотрудника')});
    if (!current(turn, id, username)) return;
    if (value?.username !== username || typeof value.password !== 'string' || !value.password.length) {
      throw new Error(N_('Сайт вернул неполные данные пароля. Обновите карточку пользователя.'));
    }
    password.value = value.password;
    revealTimer = setTimeout(() => {
      clearRevealed(); notice.value = N_('Пароль скрыт через 60 секунд.');
    }, 60000);
  } catch (e) {
    if (current(turn, id, username)) error.value = e.message;
  } finally {
    if (current(turn, id, username)) busy.value = false;
  }
}

async function copy() {
  if (!password.value || !canReveal.value || hidden() || disposed) return;
  const turn = epoch, {id, username} = props.user;
  notice.value = '';
  try {
    await navigator.clipboard.writeText(password.value);
    if (current(turn, id, username) && password.value) notice.value = N_('Пароль скопирован.');
  } catch {
    if (current(turn, id, username) && password.value) notice.value = N_('Скопируйте пароль вручную.');
  }
}

function openImport() {
  if (!canImport.value || busy.value || hidden() || disposed) return;
  clearRevealed(); clearInput(); reason.value = ''; error.value = ''; notice.value = ''; importing.value = true;
}
function cancelImport() {
  if (busy.value) return;
  clearInput(); reason.value = ''; importing.value = false; error.value = ''; notice.value = '';
}
async function save() {
  if (!canSave.value || hidden() || disposed) return;
  const turn = ++epoch, {id, username} = props.user;
  busy.value = true; error.value = ''; notice.value = '';
  try {
    await post(id, 'import', {password: knownPassword.value, reason: reason.value.trim()});
    if (!current(turn, id, username)) return;
    clearInput(); reason.value = ''; importing.value = false;
    notice.value = N_('Пароль сохранён для восстановления доступа. Пароль сотрудника не изменён.');
    emit('updated');
  } catch (e) {
    if (current(turn, id, username)) error.value = e.message;
  } finally {
    if (current(turn, id, username)) busy.value = false;
  }
}

// Neither credentials nor imported passwords leave component memory for storage.
// Invalidate pending replies immediately when the card or permission changes.
watch(() => [props.user?.id, props.user?.username, props.user?.active, props.user?.state, props.user?.credential_state, props.enabled], clear, {flush: 'sync'});
watch(knownPassword, value => {
  if (!value) {clearTimeout(inputTimer); inputTimer = null;}
  else if (inputTimer === null) inputTimer = setTimeout(() => {
    clearInput(); notice.value = N_('Введённый пароль очищен через 60 секунд.');
  }, 60000);
}, {flush: 'sync'});
function visibilityChanged() {if (hidden()) clear();}
onMounted(() => {
  pageDocument = globalThis.document;
  pageDocument?.addEventListener?.('visibilitychange', visibilityChanged);
  visibilityChanged();
});
onBeforeUnmount(() => {
  disposed = true; clear();
  pageDocument?.removeEventListener?.('visibilitychange', visibilityChanged);
});
</script>

<template>
  <section class="user-credential" :aria-label="tx('Пароль сотрудника')">
    <h3>{{tx('Пароль сотрудника')}}</h3>
    <p class="sub">{{tx(status)}}</p>
    <label>{{tx('Пароль')}}<input :type="password ? 'text' : 'password'" :value="password" :aria-label="tx('Пароль сотрудника')" placeholder="••••••••••••" readonly autocomplete="off" spellcheck="false"></label>
    <div class="credential-actions">
      <button v-if="!password" type="button" class="secondary" :disabled="!canReveal || busy" @click="reveal">{{busy && !importing ? tx('Показываем…') : tx('Показать пароль')}}</button>
      <template v-else>
        <button type="button" class="secondary" @click="hide">{{tx('Скрыть')}}</button>
        <button type="button" class="secondary" @click="copy">{{tx('Скопировать')}}</button>
      </template>
      <button v-if="canImport && !importing" type="button" class="secondary" :disabled="busy" @click="openImport">{{tx('Сохранить известный пароль')}}</button>
    </div>
    <p v-if="password" class="sub">{{tx('Пароль скроется через 60 секунд или при закрытии карточки.')}}</p>
    <form v-if="canImport && importing" @submit.prevent="save">
      <p class="sub">{{tx('Введите действующий пароль сотрудника. Сохранение не меняет пароль и не завершает его сеансы.')}}</p>
      <label>{{tx('Известный действующий пароль')}}<input v-model="knownPassword" type="password" autocomplete="off" minlength="1" maxlength="128" :disabled="busy" required spellcheck="false"></label>
      <label>{{tx('Причина (не менее 10 символов)')}}<textarea v-model="reason" minlength="10" maxlength="1000" :disabled="busy" required></textarea></label>
      <div class="credential-actions">
        <button :disabled="!canSave">{{busy ? tx('Сохраняем…') : tx('Сохранить пароль')}}</button>
        <button type="button" class="secondary" :disabled="busy" @click="cancelImport">{{tx('Отмена')}}</button>
      </div>
    </form>
    <p v-if="error" role="alert" class="error">{{tx(error)}}</p>
    <p v-if="notice" role="status" class="sub">{{tx(notice)}}</p>
  </section>
</template>

<style scoped>
.user-credential{border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0}.user-credential label{display:grid;gap:6px;margin:12px 0}.user-credential input{font-family:ui-monospace,monospace;min-width:0;width:100%;box-sizing:border-box}.user-credential textarea{min-height:64px}.user-credential .sub{font-size:13px;line-height:1.5}.credential-actions{display:flex;gap:8px;flex-wrap:wrap}.user-credential form{margin-top:16px;padding-top:8px;border-top:1px solid var(--line)}
@media(max-width:600px){.user-credential{padding:10px}.credential-actions button{flex:1}}
</style>
