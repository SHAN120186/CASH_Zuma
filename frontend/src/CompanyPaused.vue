<script setup>
import {ref,onMounted} from 'vue';
import AppIcon from './AppIcon.vue';
import LanguageSwitch from './LanguageSwitch.vue';
import {tx} from './i18n/index.js';

defineProps({
  company:{type:Object,required:true},
  checking:Boolean,
  feedback:String,
  error:String,
});
const emit=defineEmits(['retry','logout']);
const title=ref(null);
onMounted(()=>title.value?.focus?.({preventScroll:true}));
</script>

<template>
 <section class="company-paused">
  <header class="company-paused__header">
   <div class="company-paused__brand"><span aria-hidden="true">CF</span> Cash Flow</div>
   <LanguageSwitch compact/>
  </header>
  <main class="company-paused__main" aria-labelledby="company-paused-title">
   <div class="company-paused__card">
    <div class="company-paused__icon" aria-hidden="true"><AppIcon name="lock"/></div>
    <p class="company-paused__name">{{company.code==='ZUMA'?'ZUMA':company.name}}</p>
    <h1 id="company-paused-title" ref="title" tabindex="-1">{{tx('Работа компании временно приостановлена.')}}</h1>
    <p class="company-paused__contact">{{tx('Обратитесь к администратору.')}}</p>
    <div class="company-paused__actions">
     <button type="button" class="company-paused__retry" :disabled="checking" @click="emit('retry')">
      <AppIcon name="refresh"/>{{checking?tx('Проверяем доступ…'):tx('Проверить доступ')}}
     </button>
     <button type="button" class="secondary" @click="emit('logout')">{{tx('Выйти')}}</button>
    </div>
    <p v-if="feedback" class="company-paused__feedback" role="status">{{tx(feedback)}}</p>
    <p v-if="error" class="company-paused__error" role="alert">{{tx(error)}}</p>
   </div>
  </main>
 </section>
</template>

<style scoped>
.company-paused{
 min-height:100vh;min-height:100dvh;display:flex;flex-direction:column;
 padding:clamp(18px,4vw,48px);color:var(--ink);
 background:radial-gradient(ellipse at 10% 0%,var(--primary-soft),transparent 55%),
            radial-gradient(ellipse at 96% 95%,var(--bad-soft),transparent 57%),var(--bg);
}
.company-paused__header{display:flex;align-items:center;justify-content:space-between;gap:16px}
.company-paused__brand{display:flex;align-items:center;gap:10px;font-size:clamp(18px,3vw,24px);font-weight:800;letter-spacing:-.035em}
.company-paused__brand span{display:grid;place-items:center;width:38px;height:38px;border-radius:12px;background:var(--primary);color:#fff;font-size:16px;letter-spacing:0}
.company-paused__main{flex:1;display:grid;place-items:center;padding:32px 0;min-width:0}
.company-paused__card{
 width:min(100%,560px);padding:clamp(22px,6vw,48px);text-align:center;border:1px solid var(--line);border-radius:26px;min-width:0;
 background:var(--surface);box-shadow:0 24px 80px -44px rgba(var(--shade-rgb),.35);
}
.company-paused__icon{
 display:grid;place-items:center;width:68px;height:68px;margin:0 auto 18px;
 border-radius:23px;background:var(--bad-soft);color:var(--bad);
 box-shadow:0 0 0 7px color-mix(in srgb,var(--bad-soft) 42%,transparent);
}
.company-paused__icon .app-icon{width:33px;height:33px}
.company-paused__name{
 display:inline-block;margin:0 0 13px;border-radius:100px;background:var(--primary-soft);color:var(--primary-hover);
 padding:4px 11px;font-size:12px;font-weight:800;letter-spacing:.04em;overflow-wrap:anywhere;max-width:100%;
}
.company-paused h1{margin:0;font-size:clamp(23px,4.4vw,36px);line-height:1.16;letter-spacing:-.03em;text-wrap:balance}
.company-paused h1:focus{outline:none}
.company-paused__contact{margin:14px 0 0;color:var(--muted);font-size:16px;line-height:1.55}
.company-paused__actions{margin-top:25px;display:flex;justify-content:center;gap:9px;flex-wrap:wrap}
.company-paused__actions button{min-height:44px;max-width:100%;white-space:normal}
.company-paused__retry{display:inline-flex;align-items:center;justify-content:center;gap:7px}
.company-paused__retry .app-icon{width:16px;height:16px;flex:none}
.company-paused__feedback,.company-paused__error{font-size:13px;margin:14px 0 0;line-height:1.5}
.company-paused__feedback{color:var(--muted)}.company-paused__error{color:var(--bad)}
@media(max-width:390px){.company-paused__card{border-radius:20px}.company-paused__icon{width:57px;height:57px;border-radius:19px}}
</style>
