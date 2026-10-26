<script setup>
import {ref,onMounted,onUnmounted,watch} from 'vue';
import {tx} from './i18n/index.js';
const props=defineProps({api:Function,companyId:Number,canEdit:Boolean});
const emit=defineEmits(['changed','editing']);
const data=ref({version:0,items:[],can_edit:false}),draft=ref(null),busy=ref(false),error=ref('');
let active=true;
const url=code=>`/api/departments${code?'/'+encodeURIComponent(code):''}?company_id=${props.companyId}`;
async function load(){busy.value=true;error.value='';try{const result=await props.api(url());if(active)data.value=result;}catch(e){if(active&&e.name!=='AbortError')error.value=e.message;}finally{if(active)busy.value=false;}}
function edit(row){draft.value=row?{...row}:{code:'',name_ru:'',name_uz:'',active:true};}
watch(draft,x=>emit('editing',!!x));
async function save(){if(busy.value||!props.canEdit||!data.value.can_edit)return;busy.value=true;error.value='';const row={...draft.value};try{await props.api(url(row.code),{method:row.code?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:data.value.version,name_ru:row.name_ru,name_uz:row.name_uz,active:row.active})});if(!active)return;draft.value=null;emit('changed');await load();}catch(e){if(active&&e.name!=='AbortError')error.value=e.message;}finally{if(active)busy.value=false;}}
onMounted(load);onUnmounted(()=>{active=false;emit('editing',false);});
</script>
<template>
 <section class="card">
  <div class="card-h"><h2>{{tx('Отделы компании')}}</h2><div class="actions"><button type="button" class="secondary" :disabled="busy||!!draft" @click="load">{{tx('Обновить список')}}</button><button v-if="canEdit&&data.can_edit&&!draft" type="button" @click="edit()">{{tx('Добавить отдел')}}</button></div></div>
  <div class="card-b">
   <p class="sub">{{tx('Отделы доступны при создании заявки. Для каждой компании ведётся отдельный список.')}}</p>
   <p v-if="error" class="error" role="alert">{{tx(error)}}</p><p v-if="busy" role="status">{{tx('Загружаем данные…')}}</p>
   <form v-if="draft" @submit.prevent="save" class="department-form">
    <label>{{tx('Название на русском')}}<input v-model.trim="draft.name_ru" required maxlength="120"></label>
    <label>{{tx('Название на узбекском')}}<input v-model.trim="draft.name_uz" required maxlength="120"></label>
    <label><input v-model="draft.active" type="checkbox"> {{tx('Доступен для новых заявок')}}</label>
    <p class="sub">{{tx('Отключённый отдел остаётся в ранее созданных заявках. Его можно включить снова.')}}</p>
    <div class="actions"><button type="submit" :disabled="busy">{{tx('Сохранить')}}</button><button type="button" class="secondary" :disabled="busy" @click="draft=null">{{tx('Отменить')}}</button></div>
   </form>
   <div class="table-scroll"><table><thead><tr><th>{{tx('Название на русском')}}</th><th>{{tx('Название на узбекском')}}</th><th>{{tx('Состояние')}}</th><th>{{tx('Действия')}}</th></tr></thead><tbody><tr v-for="row in data.items" :key="row.code"><td>{{row.name_ru}}</td><td>{{row.name_uz}}</td><td>{{row.active?tx('Доступен'):tx('Отключён')}}</td><td><button v-if="canEdit&&data.can_edit" type="button" class="secondary tiny" :disabled="busy||!!draft" @click="edit(row)">{{tx('Изменить')}}</button></td></tr></tbody></table></div>
  </div>
 </section>
</template>
<style scoped>.department-form{display:grid;gap:14px;padding:18px;border:1px solid var(--line);border-radius:12px;margin:16px 0}.department-form label{display:grid;gap:6px}.department-form label:has(input[type=checkbox]){display:flex;align-items:center;gap:8px}.card-h{gap:12px;flex-wrap:wrap}</style>
