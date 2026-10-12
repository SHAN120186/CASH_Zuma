<script setup>
import {ref,onMounted} from 'vue';
import {tx} from './i18n/index.js';
const props=defineProps({api:Function});
const emit=defineEmits(['changed']);
const rows=ref([]),busy=ref(false),error=ref(''),notice=ref(''),preview=ref(null),editing=ref(false),id=ref(null);
const form=ref({name:'',inn:'',note:''});
async function load(){rows.value=await props.api('/api/counterparties')}
function edit(c=null){id.value=c?.id??null;form.value={name:c?.name||'',inn:c?.inn||'',note:c?.note||''};editing.value=true;error.value=''}
async function save(){if(busy.value)return;busy.value=true;error.value='';try{await props.api('/api/counterparties'+(id.value?'/'+id.value:''),{method:id.value?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(form.value)});editing.value=false;await load();emit('changed');notice.value=tx('Контрагент сохранён.')}catch(e){error.value=tx(e.message)}finally{busy.value=false}}
async function upload(e){const file=e.target.files?.[0];e.target.value='';if(!file||busy.value)return;busy.value=true;error.value='';preview.value=null;try{preview.value=await props.api('/api/counterparties/import/preview',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(file.name)},body:file})}catch(e){error.value=tx(e.message)}finally{busy.value=false}}
async function commit(){if(busy.value||!preview.value?.can_commit)return;busy.value=true;error.value='';try{const result=await props.api('/api/counterparties/import/commit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rows:preview.value.rows})});preview.value=null;await load();emit('changed');notice.value=tx('Добавлено: {added}. Уже были в справочнике: {skipped}.',result)}catch(e){error.value=tx(e.message)}finally{busy.value=false}}
onMounted(async()=>{busy.value=true;try{await load()}catch(e){error.value=tx(e.message)}finally{busy.value=false}});
</script>
<template>
 <section class="card"><div class="card-h"><h2>{{tx('Контрагенты')}}</h2><div class="actions"><button :disabled="busy" @click="edit()">{{tx('Добавить контрагента')}}</button><label class="button secondary">{{tx('Загрузить список Excel')}}<input type="file" accept=".xlsx" :disabled="busy" @change="upload" hidden></label></div></div><div class="card-b">
  <p>{{tx('Excel: столбцы «Контрагент», «ИНН», необязательно «Примечание». ИНН лучше хранить как текст. До 1000 строк и 5 МБ.')}}</p>
  <p v-if="busy" role="status">{{tx('Загрузка…')}}</p><p v-if="error" class="error" role="alert">{{error}}</p><p v-if="notice" role="status">{{notice}}</p>
  <form v-if="editing" @submit.prevent="save"><div class="cp-grid"><label>{{tx('Контрагент')}}<input v-model="form.name" minlength="2" maxlength="160" required></label><label>{{tx('ИНН')}}<input v-model="form.inn" inputmode="numeric" pattern="[0-9]*" maxlength="20"></label><label>{{tx('Примечание')}}<textarea v-model="form.note" maxlength="240"></textarea></label></div><div class="form-actions"><button type="button" class="secondary" :disabled="busy" @click="editing=false">{{tx('Отмена')}}</button><button :disabled="busy">{{tx('Сохранить')}}</button></div></form>
  <div v-if="preview"><h3>{{tx('Проверка списка контрагентов')}}</h3><p v-for="e in preview.errors" :key="e.line" class="error">{{tx('Строка {line}',{line:e.line})}}: {{tx(e.error)}}</p><p>{{tx('Уже есть в справочнике: {n}',{n:preview.skipped})}}</p><div class="table-scroll"><table><thead><tr><th>{{tx('Контрагент')}}</th><th>{{tx('ИНН')}}</th><th>{{tx('Примечание')}}</th></tr></thead><tbody><tr v-for="(r,i) in preview.rows" :key="i"><td>{{r.name}}</td><td>{{r.inn||'—'}}</td><td>{{r.note||'—'}}</td></tr></tbody></table></div><div class="form-actions"><button class="secondary" :disabled="busy" @click="preview=null">{{tx('Отмена')}}</button><button :disabled="busy||!preview.can_commit" @click="commit">{{tx('Сохранить список контрагентов')}}</button></div></div>
  <div v-else class="table-scroll"><table><thead><tr><th>{{tx('Контрагент')}}</th><th>{{tx('ИНН')}}</th><th>{{tx('Примечание')}}</th><th></th></tr></thead><tbody><tr v-for="r in rows" :key="r.id"><td>{{r.name}}</td><td>{{r.inn||'—'}}</td><td>{{r.note||'—'}}</td><td><button class="secondary tiny" :disabled="busy" @click="edit(r)">{{tx('Редактировать')}}</button></td></tr></tbody></table></div>
 </div></section>
</template>
<style scoped>.cp-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin:20px 0}.cp-grid label{display:flex;flex-direction:column;gap:6px}.button{cursor:pointer}td{overflow-wrap:anywhere}</style>
