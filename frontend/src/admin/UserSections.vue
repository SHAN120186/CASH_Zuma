<script setup>
import {ref,watch,computed} from 'vue';
import {tx,N_} from '../i18n/index.js';
const props=defineProps({api:Function,user:Object,companies:Array});
const company=ref(props.user.company_id||props.companies[0]?.id),policy=ref(null),choices=ref({}),reason=ref(''),busy=ref(false),error=ref(''),notice=ref('');
const modes=[['hidden',N_('Нет доступа')],['view',N_('Только просмотр')],['edit',N_('Можно работать')]];
const reportKeys=['report','pnl','balance','rating','business','reports'];
const savedMode=m=>policy.value?.sections[m.key]||'default';
const effective=(m,mode)=>policy.value?.locked?'edit':mode==='default'?m.default:mode;
const modeLabel=mode=>tx(modes.find(x=>x[0]===mode)?.[1]||N_('Нет доступа'));
const standardLabel=m=>tx('Стандартный: {access}',{access:modeLabel(m.default)});
const selectionLabel=(m,mode)=>mode==='default'?standardLabel(m):modeLabel(effective(m,mode));
const changed=m=>choices.value[m.key]!==savedMode(m);
const pending=computed(()=>policy.value?.modules.filter(changed)||[]);
const canSave=computed(()=>!busy.value&&pending.value.length>0&&reason.value.trim().length>=10);
const reportMode=computed({get:()=>{const values=reportKeys.filter(k=>k in choices.value).map(k=>choices.value[k]);return values.length&&values.every(v=>v===values[0])?values[0]:'custom';},set:mode=>{if(mode!=='custom')for(const key of reportKeys)if(key in choices.value)choices.value[key]=mode;notice.value='';}});
const allowedModes=m=>modes.filter(x=>x[0]!=='edit'||(!['users','audit'].includes(m.key)&&props.user.holding_role!=='founder'));
function reset(){if(!policy.value)return;choices.value=Object.fromEntries(policy.value.modules.map(m=>[m.key,savedMode(m)]));reason.value='';notice.value='';}
let epoch=0;
async function load(){const turn=++epoch,uid=props.user.id,cid=company.value;busy.value=true;error.value='';notice.value='';reason.value='';policy.value=null;try{const value=await props.api(`/api/admin/users/${uid}/sections?company_id=${cid}`);if(turn!==epoch||uid!==props.user.id||cid!==company.value)return;policy.value=value;reset();}catch(e){if(turn===epoch)error.value=e.message;}finally{if(turn===epoch)busy.value=false;}}
async function save(){if(!canSave.value)return;busy.value=true;error.value='';notice.value='';try{await props.api(`/api/admin/users/${props.user.id}/sections`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({company_id:Number(company.value),version:policy.value.version,sections:choices.value,reason:reason.value.trim()})});await load();notice.value=N_('Изменения сохранены. Новые права действуют при следующем входе сотрудника. Пароль остаётся прежним.');}catch(e){error.value=e.message;}finally{busy.value=false;}}
watch(()=>[props.user.id,company.value],load,{immediate:true});
</script>
<template>
 <section class="section-access">
  <h3>{{tx('Доступ к разделам')}}</h3>
  <p>{{tx('Выберите, что сотрудник сможет делать в каждом разделе. Затем укажите причину и сохраните изменения.')}}</p>
  <label>{{tx('Компания')}}<select v-model="company" :disabled="busy"><option v-for="c in companies.filter(c=>user.holding_role||c.id===user.company_id)" :key="c.id" :value="c.id">{{c.name}}</option></select></label>
  <p v-if="error" role="alert" class="error">{{tx(error)}}</p><p v-if="notice" role="status" class="access-success">{{tx(notice)}}</p>
  <p v-if="busy&&!policy" role="status">{{tx('Загружаем настройки доступа…')}}</p>
  <form v-if="policy" @submit.prevent="save">
   <p v-if="policy.locked">{{tx('Администратор холдинга всегда имеет полный доступ.')}}</p>
   <template v-else>
    <details class="access-help"><summary>{{tx('Что означают режимы доступа')}}</summary>
     <p>{{tx('Нет доступа — раздел не виден в меню и не открывается по прямой ссылке.')}}</p>
     <p>{{tx('Только просмотр — сотрудник видит данные, но не может их добавлять, изменять или удалять.')}}</p>
     <p>{{tx('Можно работать — доступны рабочие действия раздела. Проверки согласования, оплаты и авторства документов сохраняются.')}}</p>
     <p>{{tx('Стандартный — доступ автоматически определяется должностью сотрудника. Рядом указано, какой доступ получится.')}}</p>
     <p>{{tx('Чтобы обновлять общую книгу P&L и баланса, выберите «Можно работать» для обоих отчётов. Права скачивания файлов настраиваются отдельно от этих режимов.')}}</p>
    </details>
    <label>{{tx('Изменить доступ ко всем отчётам')}}<select v-model="reportMode" :disabled="busy"><option value="custom">{{tx('Разные настройки')}}</option><option value="default">{{tx('Стандартный для каждого отчёта')}}</option><option v-for="[key,label] in modes.filter(x=>x[0]!=='edit'||user.holding_role!=='founder')" :key="key" :value="key">{{tx(label)}}</option></select></label>
    <small>{{tx('Этот выбор меняет только строки отчётов ниже. Применится после сохранения.')}}</small>
   </template>
   <table><thead><tr><th>{{tx('Раздел')}}</th><th>{{tx('Доступ сотрудника')}}</th></tr></thead><tbody>
    <tr v-for="m in policy.modules" :key="m.key" :class="{'access-changed':changed(m)}">
     <td>{{tx(m.label)}}</td><td>
      <strong v-if="policy.locked">{{modeLabel('edit')}}</strong>
      <select v-else v-model="choices[m.key]" :aria-label="tx('Доступ: {section}',{section:tx(m.label)})" :disabled="busy" @change="notice=''">
       <option value="default">{{standardLabel(m)}}</option><option v-for="[key,label] in allowedModes(m)" :key="key" :value="key">{{tx(label)}}</option>
      </select>
      <small v-if="changed(m)" class="access-pending">{{tx('Не сохранено: {before} → {after}',{before:selectionLabel(m,savedMode(m)),after:selectionLabel(m,choices[m.key])})}}</small>
     </td>
    </tr>
   </tbody></table>
   <div v-if="!policy.locked" class="access-save">
    <p role="status">{{pending.length?tx('Не сохранено разделов: {n}',{n:pending.length}):tx('Все настройки сохранены.')}}</p>
    <label>{{tx('Причина изменения (не менее 10 символов)')}}<textarea v-model="reason" minlength="10" maxlength="1000" required :disabled="busy||!pending.length" :placeholder="tx('Например: разрешить бухгалтеру работу с отчётами')"></textarea></label>
    <div class="access-actions"><button :disabled="!canSave">{{busy?tx('Сохраняем…'):tx('Сохранить изменения')}}</button><button type="button" class="secondary" :disabled="busy||!pending.length" @click="reset">{{tx('Отменить изменения')}}</button></div>
   </div>
  </form>
 </section>
</template>
<style scoped>
.section-access{border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0}.section-access table{width:100%;margin:12px 0;table-layout:fixed}.section-access th:first-child{width:33%}.section-access td,.section-access th{padding:10px 8px;text-align:left;vertical-align:top}.section-access select{width:100%;min-width:0;max-width:100%;white-space:normal}.section-access label{display:grid;gap:6px;margin:10px 0}.section-access p{font-size:13px;line-height:1.5}.section-access small{display:block;line-height:1.5;color:var(--muted)}.access-help{padding:10px;border:1px solid var(--line);border-radius:8px;margin:12px 0}.access-help summary{cursor:pointer;font-weight:600}.access-pending{margin-top:5px;color:var(--text)!important;font-weight:600}.access-changed{background:var(--surface-alt,#f1f1ff)}.access-save{position:sticky;bottom:0;background:var(--surface,#fff);border-top:1px solid var(--line);padding:10px 0 2px;z-index:1}.access-save p{margin:0}.access-save textarea{min-height:52px}.access-actions{display:flex;gap:8px;flex-wrap:wrap}.access-success{font-weight:600;color:var(--text)}
@media(max-width:600px){.section-access{padding:10px}.section-access th:first-child{width:31%}.section-access td,.section-access th{padding:8px 4px}.section-access select{font-size:12px}.access-actions button{flex:1}.access-save{position:static}}
</style>
