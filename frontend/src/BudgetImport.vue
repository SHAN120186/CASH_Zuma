<script setup>
import {ref,computed} from 'vue';
import {tx} from './i18n/index.js';
const props=defineProps({api:Function,month:String,currency:String});
const emit=defineEmits(['changed']);
const fileInput=ref(null),preview=ref(null),busy=ref(false),error=ref(''),notice=ref(''),scale=ref(1),choices=ref({});
const ready=computed(()=>preview.value&&!preview.value.issues.length&&preview.value.rows.every(r=>r.amount===null||choices.value[r.code])&&preview.value.rows.some(r=>r.amount!==null&&choices.value[r.code]&&choices.value[r.code]!=='skip'));
function previous(r){return preview.value.categories.find(c=>String(c.id)===choices.value[r.code])?.old??'—'}
async function upload(e){const file=e.target.files?.[0];e.target.value='';if(!file||busy.value)return;busy.value=true;error.value='';notice.value='';preview.value=null;try{const p=await props.api(`/api/budgets/import/preview?month=${props.month}&currency=${props.currency}&scale=${scale.value}`,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(file.name)},body:file});choices.value=Object.fromEntries(p.rows.map(r=>[r.code,r.income?'skip':r.suggestion]));preview.value=p}catch(e){error.value=tx(e.message)}finally{busy.value=false}}
async function commit(){if(!ready.value||busy.value)return;busy.value=true;error.value='';try{const result=await props.api(`/api/budgets/import/${preview.value.id}/commit`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mappings:choices.value})});preview.value=null;notice.value=tx('Загружено статей бюджета: {n}',{n:result.updated});emit('changed')}catch(e){error.value=tx(e.message)}finally{busy.value=false}}
</script>
<template>
<section class="card budget-import"><div class="card-b">
 <div class="tb"><label class="fld">{{tx('Суммы в файле')}}<select v-model="scale" :disabled="busy||!!preview"><option :value="1">{{tx('Единицы')}}</option><option :value="1000">{{tx('Тысячи')}}</option><option :value="1000000">{{tx('Миллионы')}}</option></select></label><button class="secondary" :disabled="busy" @click="fileInput?.click()">{{tx('Загрузить бюджет Excel')}}</button><input ref="fileInput" hidden type="file" accept=".xlsx" @change="upload"></div>
 <p>{{tx('Excel: код статьи, название, сумма. До 1000 статей и 15 МБ. Пустые суммы сохраняют текущий лимит, ноль обнуляет его.')}}</p>
 <p v-if="busy" role="status">{{tx('Обработка бюджета…')}}</p><p v-if="error" class="error" role="alert">{{error}}</p><p v-if="notice" role="status">{{notice}}</p>
 <div v-if="preview"><h3>{{tx('Предпросмотр бюджета')}} · {{preview.month}} · {{preview.currency}}</h3><p>{{tx('После подтверждения выбранные суммы заменят лимиты. Оплаты, остатки и планы А/Б/В не изменятся. Новые статьи создаются как операционные расходы.')}}</p>
 <p v-for="e in preview.issues" :key="e.line" class="error">{{tx('Строка {line}',{line:e.line})}}: {{tx(e.error)}}</p>
 <div class="table-scroll"><table><thead><tr><th>{{tx('Код статьи')}}</th><th>{{tx('Название статьи')}}</th><th>{{tx('Сумма')}}</th><th>{{tx('Статья на сайте')}}</th><th>{{tx('Текущий лимит')}}</th></tr></thead><tbody>
 <tr v-for="r in preview.rows" :key="r.line"><td>{{r.code}}</td><td>{{r.name}}<small v-if="r.income">{{tx('Статья поступлений — пропущена')}}</small></td><td>{{r.amount===null?'—':Number(r.amount).toLocaleString('ru-RU',{maximumFractionDigits:2})}}</td><td><span v-if="r.amount===null">{{tx('Без изменения')}}</span><select v-else v-model="choices[r.code]" :disabled="busy" :aria-label="tx('Статья на сайте')+' '+r.code"><option value="">{{tx('Выберите статью')}}</option><option value="skip">{{tx('Пропустить строку')}}</option><option v-if="preview.can_create&&!r.income" value="new">{{tx('Создать статью из Excel')}}</option><option v-for="c in preview.categories" :key="c.id" :value="String(c.id)">{{c.code?c.code+' · ':''}}{{c.name}}</option></select></td><td>{{previous(r)}}</td></tr>
 </tbody></table></div><div class="form-actions"><button class="secondary" :disabled="busy" @click="preview=null">{{tx('Отмена')}}</button><button :disabled="busy||!ready" @click="commit">{{tx('Подтвердить загрузку бюджета')}}</button></div></div>
</div></section>
</template>
<style scoped>.budget-import{margin:16px 0}td{max-width:350px;white-space:normal;overflow-wrap:anywhere}select{max-width:310px}small{display:block}.form-actions{margin-top:16px}</style>
