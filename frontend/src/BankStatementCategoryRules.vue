<script setup>
import {computed,ref,watch,onUnmounted} from 'vue';
import {tx,N_} from './i18n/index.js';

const props=defineProps({api:{type:Function,required:true},companyId:{type:Number,required:true},categories:{type:Array,default:()=>[]},canView:{type:Boolean,default:false},canManage:{type:Boolean,default:false},disabled:{type:Boolean,default:false}});
const emit=defineEmits(['changed']);
const open=ref(false),loaded=ref(false),loading=ref(false),saving=ref(false),revision=ref(''),serverCanManage=ref(false),items=ref([]),originalItems=ref([]),editor=ref(null),needsRefresh=ref(false),error=ref(''),notice=ref(''),warning=ref('');
let generation=0,localSequence=0;
const endpoint='/api/bank-statements/category-rules';
const canEdit=computed(()=>props.canView&&props.canManage&&serverCanManage.value&&loaded.value&&!loading.value&&!saving.value&&!needsRefresh.value&&!props.disabled);
const currencies=['UZS','USD','EUR'];
const dirty=computed(()=>JSON.stringify(items.value.map(bodyItem))!==JSON.stringify(originalItems.value.map(bodyItem)));
const categoryOptions=computed(()=>props.categories.filter(item=>!item.retired&&item.activity!=='unclassified'&&item.type===(editor.value?.direction==='in'?'income':'outcome')));
const meaningful=(value,contract=false)=>/\p{L}/u.test(value)||contract&&(value.match(/\d/g)||[]).length>=3&&/[-/№]/.test(value);
const validEditor=computed(()=>{
 const item=editor.value;if(!item)return false;
 return ['in','out'].includes(item.direction)&&(!item.currency||currencies.includes(item.currency))&&[item.purpose_contains,item.contract_contains].every(value=>!value.trim()||value.trim().length>=5)&&(item.inn.trim()||item.counterparty.trim()||meaningful(item.purpose_contains)||meaningful(item.contract_contains,true))&&(!item.inn.trim()||/^\d{5,20}$/.test(item.inn.trim()))&&(!item.counterparty.trim()||item.counterparty.trim().length>=2&&/[\p{L}\p{N}]/u.test(item.counterparty))&&categoryOptions.value.some(category=>String(category.id)===item.category_id);
});
const direction=value=>tx(value==='in'?N_('Поступления'):N_('Списания'));
function criteria(item){
 const parts=[];
 if(item.inn)parts.push(tx('ИНН')+': '+item.inn);
 if(item.counterparty)parts.push(tx('Точное название')+': '+item.counterparty);
 if(item.purpose_contains)parts.push(tx('Назначение содержит')+': '+item.purpose_contains);
 if(item.contract_contains)parts.push(tx('Договор / счёт содержит')+': '+item.contract_contains);
 return parts.join(' · ');
}
function reset(){generation++;localSequence=0;open.value=false;loaded.value=false;loading.value=false;saving.value=false;revision.value='';serverCanManage.value=false;items.value=[];originalItems.value=[];editor.value=null;needsRefresh.value=false;error.value='';notice.value='';warning.value='';}
watch(()=>[props.companyId,props.canView,props.canManage],reset);
watch(()=>editor.value?.direction,()=>{
 if(editor.value?.category_id&&!categoryOptions.value.some(item=>String(item.id)===editor.value.category_id))editor.value.category_id='';
});
onUnmounted(()=>{generation++;});
function scope(current,company){return current===generation&&props.companyId===company&&props.canView;}
function snapshot(result){
 if(!result||typeof result.revision!=='string'||!/^[0-9a-f]{64}$/.test(result.revision)||typeof result.can_manage!=='boolean'||!Array.isArray(result.items))throw new Error('invalid rule snapshot');
 const ids=new Set();
 const clean=result.items.map(item=>{
  if(typeof item.id!=='string'||!item.id||ids.has(item.id)||!['in','out'].includes(item.direction)||item.currency!==null&&!currencies.includes(item.currency)||!['inn','counterparty','purpose_contains','contract_contains','category_name'].every(key=>typeof item[key]==='string')||!Number.isInteger(item.category_id)||item.category_id<=0||typeof item.enabled!=='boolean')throw new Error('invalid rule');
  ids.add(item.id);return {...item};
 });
 return {revision:result.revision,can_manage:result.can_manage,items:clean,warning:typeof result.warning==='string'?result.warning:''};
}
function accept(result){revision.value=result.revision;serverCanManage.value=result.can_manage;items.value=result.items.map(item=>({...item}));originalItems.value=result.items.map(item=>({...item}));warning.value=result.warning;loaded.value=true;needsRefresh.value=false;editor.value=null;}
async function toggle(){
 if(!props.canView||!props.companyId||saving.value)return;
 if(open.value){open.value=false;if(loading.value){generation++;loading.value=false;}return;}
 open.value=true;if(!loaded.value||needsRefresh.value)await load();
}
async function load(){
 if(!props.canView||!props.companyId||!open.value||saving.value||props.disabled)return;
 const current=++generation,company=props.companyId,previous=revision.value;
 loading.value=true;loaded.value=false;items.value=[];serverCanManage.value=false;editor.value=null;error.value='';notice.value='';warning.value='';
 try{
  const result=snapshot(await props.api(endpoint));
  if(!scope(current,company)||!open.value)return;
  accept(result);if(previous&&previous!==result.revision)emit('changed',result);
 }catch(e){if(scope(current,company)&&open.value)error.value=e?.status&&e.message?tx(e.message):tx('Не удалось загрузить правила статей. Обновите список.');}
 finally{if(current===generation)loading.value=false;}
}
function edit(item=null){
 if(!canEdit.value||editor.value||item&&!items.value.some(rule=>rule.id===item.id))return;
 editor.value=item?{...item,currency:item.currency||'',category_id:String(item.category_id)}:{id:'',direction:'out',currency:'',inn:'',counterparty:'',purpose_contains:'',contract_contains:'',category_id:'',enabled:true};error.value='';notice.value='';
}
function cancel(){if(!saving.value)editor.value=null;}
function discard(){if(!saving.value){items.value=originalItems.value.map(item=>({...item}));editor.value=null;error.value='';notice.value='';}}
function bodyItem(item){
 return {...(item.id&&!item._new?{id:item.id}:{}),direction:item.direction,currency:item.currency||null,inn:item.inn.trim(),counterparty:item.counterparty.trim(),purpose_contains:item.purpose_contains.trim(),contract_contains:item.contract_contains.trim(),category_id:Number(item.category_id),enabled:item.enabled};
}
async function save(next){
 if(!canEdit.value||editor.value||!dirty.value)return;
 const current=++generation,company=props.companyId,expected=revision.value;
 saving.value=true;error.value='';notice.value='';
 try{
  const result=snapshot(await props.api(endpoint,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:expected,items:next.map(bodyItem)})}));
  if(!scope(current,company)||!props.canManage)return;
  accept(result);notice.value=tx('Правила статей сохранены. Откройте проверку выписки заново, чтобы увидеть результат.');emit('changed',result);
 }catch(e){
  if(!scope(current,company))return;
  if(!e?.status||e.status>=500){needsRefresh.value=true;error.value=tx('Ответ о сохранении правил не получен. Обновите список, прежде чем сохранять снова.');}
  else if([403,409].includes(e.status)){needsRefresh.value=true;error.value=e.message?tx(e.message):tx('Правила изменились или доступ ограничен. Обновите список перед сохранением.');}
  else error.value=e.message?tx(e.message):tx('Не удалось сохранить правила статей. Проверьте поля и повторите.');
 }finally{if(current===generation)saving.value=false;}
}
function saveEditor(){
 if(!canEdit.value||!validEditor.value)return;
 const value={...editor.value,category_name:categoryOptions.value.find(item=>String(item.id)===editor.value.category_id).name,category_valid:true};
 if(value.id)items.value=items.value.map(item=>item.id===value.id?value:item);
 else items.value=[...items.value,{...value,id:`draft-${++localSequence}`,_new:true}];
 editor.value=null;error.value='';notice.value='';
}
function toggleEnabled(item){
 if(!canEdit.value||editor.value||!items.value.some(rule=>rule.id===item.id))return;
 items.value=items.value.map(rule=>rule.id===item.id?{...rule,enabled:!rule.enabled}:rule);notice.value='';
}
function remove(item){
 if(!canEdit.value||editor.value||!items.value.some(rule=>rule.id===item.id))return;
 items.value=items.value.filter(rule=>rule.id!==item.id);notice.value='';
}
</script>

<template>
 <section v-if="canView" class="category-rules">
  <button type="button" class="secondary tiny" :aria-expanded="open" :disabled="saving" @click="toggle">{{tx('Правила статей')}}</button>
  <div v-if="open" class="rules-content">
   <div class="rules-toolbar"><button type="button" class="secondary tiny" :disabled="loading||saving||disabled||((dirty||editor)&&!needsRefresh)" @click="load">{{tx('Обновить правила')}}</button><button v-if="canEdit" type="button" class="secondary tiny" :disabled="!!editor" @click="edit()">{{tx('Добавить правило')}}</button></div>
   <p v-if="loading" role="status">{{tx('Загружаем данные…')}}</p><p v-if="saving" role="status">{{tx('Сохраняем правила…')}}</p><p v-if="error" class="error" role="alert">{{error}}</p><p v-if="warning" class="error" role="alert">{{tx(warning)}}</p><p v-if="notice" role="status">{{notice}}</p>
   <p v-if="loaded&&(!serverCanManage||!canManage)" class="sub">{{tx('Правила доступны только для просмотра.')}}</p>
   <p v-if="loaded&&!items.length" class="sub">{{tx('В выбранной компании правил статей пока нет.')}}</p>
   <div v-if="items.length" class="table-scroll"><table><thead><tr><th>{{tx('Движение / валюта')}}</th><th>{{tx('Условия правила')}}</th><th>{{tx('Статья')}}</th><th>{{tx('Состояние')}}</th><th v-if="canEdit"></th></tr></thead><tbody>
    <tr v-for="item in items" :key="item.id" :class="{inactive:!item.enabled}"><td>{{direction(item.direction)}} · {{item.currency||tx('Любая валюта')}}</td><td>{{criteria(item)}}</td><td>{{item.category_name}}<small v-if="item.category_valid===false" class="error">{{tx('Статья недоступна')}}</small></td><td>{{tx(item.enabled?N_('Включено'):N_('Отключено'))}}</td><td v-if="canEdit" class="rules-actions"><button type="button" class="secondary tiny" :disabled="!!editor" @click="edit(item)">{{tx('Изменить')}}</button><button type="button" class="secondary tiny" :disabled="!!editor" @click="toggleEnabled(item)">{{tx(item.enabled?N_('Отключить'):N_('Включить'))}}</button><button type="button" class="secondary tiny" :disabled="!!editor" @click="remove(item)">{{tx('Удалить')}}</button></td></tr>
   </tbody></table></div>
   <form v-if="editor" class="rule-editor" @submit.prevent="saveEditor">
    <h4>{{tx(editor.id?N_('Изменение правила'):N_('Новое правило'))}}</h4>
    <div class="rule-fields">
     <label>{{tx('Движение')}}<select v-model="editor.direction" :disabled="!canEdit"><option value="in">{{tx('Поступления')}}</option><option value="out">{{tx('Списания')}}</option></select></label>
     <label>{{tx('Валюта')}}<select v-model="editor.currency" :disabled="!canEdit"><option value="">{{tx('Любая валюта')}}</option><option v-for="value in currencies" :key="value" :value="value">{{value}}</option></select></label>
     <label>{{tx('ИНН контрагента')}}<input v-model="editor.inn" inputmode="numeric" :disabled="!canEdit"></label>
     <label>{{tx('Точное название контрагента')}}<input v-model="editor.counterparty" :disabled="!canEdit"></label>
     <label>{{tx('Назначение содержит')}}<input v-model="editor.purpose_contains" :disabled="!canEdit"></label>
     <label>{{tx('Договор / счёт содержит')}}<input v-model="editor.contract_contains" :disabled="!canEdit"></label>
     <label>{{tx('Статья')}}<select v-model="editor.category_id" :disabled="!canEdit"><option value="">{{tx('Выберите статью')}}</option><option v-for="item in categoryOptions" :key="item.id" :value="String(item.id)">{{item.name}}</option></select></label>
    </div>
    <p class="sub">{{tx('Укажите хотя бы одно условие. Фрагмент — от 5 символов с буквами; для договора допустим номер с разделителем.')}}</p>
    <label><input v-model="editor.enabled" type="checkbox" :disabled="!canEdit"> {{tx('Правило включено')}}</label>
    <p class="sub">{{tx('Изменения будут сохранены кнопкой «Сохранить правила».')}}</p>
    <div class="form-actions"><button type="button" class="secondary tiny" :disabled="saving" @click="cancel">{{tx('Отмена')}}</button><button type="submit" :disabled="!canEdit||!validEditor">{{tx('Применить к списку')}}</button></div>
   </form>
   <p v-if="dirty" class="sub" role="status">{{tx('Есть несохранённые изменения.')}}</p>
   <div v-if="loaded&&canManage&&serverCanManage" class="form-actions"><button type="button" class="secondary tiny" :disabled="saving||(!dirty&&!editor)" @click="discard">{{tx('Отменить изменения')}}</button><button type="button" :disabled="!canEdit||!dirty||!!editor" @click="save(items)">{{tx('Сохранить правила')}}</button></div>
   <details class="rules-help"><summary>{{tx('Как применяются правила')}}</summary><p class="sub">{{tx('Все заполненные условия одного правила должны совпасть: движение, валюта, ИНН, точное название, назначение и договор. Пустое поле не ограничивает поиск.')}}</p><p class="sub">{{tx('Если совпавшие правила указывают разные статьи, строка остаётся «Не распределено» для проверки. Правила не заменяют проверки переводов, повторов, заявок и остатков.')}}</p><p class="sub">{{tx('Недоступную статью нужно заменить или удалить правило перед сохранением.')}}</p><p class="sub">{{tx('Сохранение правил не проводит операции и не меняет уже учтённые суммы. Результат виден после новой проверки выписки.')}}</p></details>
  </div>
 </section>
</template>

<style scoped>
.rule-fields label,.rule-fields input,.rule-fields select{min-width:0}.rule-fields input,.rule-fields select{width:100%;max-width:100%}
.category-rules{margin:14px 0}.rules-content{margin-top:12px;padding:14px;border:1px solid var(--line,#dce0e8);border-radius:10px}.rules-toolbar,.rules-actions{display:flex;gap:8px;flex-wrap:wrap}.rule-editor{margin-top:16px;padding:12px;background:#f8fafc;border-radius:8px}.rule-editor h4{margin:0 0 12px}.rule-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.rule-fields label{display:grid;gap:4px}.rules-help{margin-top:12px}.rules-help summary{cursor:pointer}.table-scroll td{white-space:normal;max-width:420px;overflow-wrap:anywhere}.inactive{opacity:.65}.form-actions{display:flex;gap:8px;margin-top:12px}
</style>
