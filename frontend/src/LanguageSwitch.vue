<script setup>
import {LANGUAGES, lang, setLang, tx} from './i18n/index.js';
defineProps({compact: {type: Boolean, default: false}});
</script>

<template>
  <div class="lang-switch" :class="{compact}" role="group" :aria-label="tx('Язык интерфейса')">
    <button v-for="item in LANGUAGES" :key="item.code" type="button" :class="{on: lang===item.code}" :aria-pressed="lang===item.code" :lang="item.code" :title="item.name" @click="setLang(item.code)">{{item.short}}</button>
  </div>
</template>

<style scoped>
.lang-switch{display:inline-flex;gap:2px;padding:3px;border:1px solid var(--line);border-radius:999px;background:var(--surface)}
.lang-switch button{min-height:30px;padding:0 11px;border:0;border-radius:999px;background:transparent;color:var(--muted);font-size:12px;font-weight:700;letter-spacing:.04em;box-shadow:none}
.lang-switch button:hover{color:var(--text);background:var(--surface-muted)}
.lang-switch button.on{background:var(--primary);color:#fff}
.lang-switch.compact button{min-height:26px;padding:0 9px}
</style>
