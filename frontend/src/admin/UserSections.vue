<script setup>
import {ref,watch,computed} from 'vue';
import {tx,N_} from '../i18n/index.js';
const props=defineProps({api:Function,user:Object,companies:Array});
const company=ref(props.user.company_id||props.companies[0]?.id),policy=ref(null),choices=ref({}),reason=ref(''),busy=ref(false),error=ref(''),notice=ref('');
const modes=[[ 'default',N_('По роли')],['hidden',N_('Скрыт')],['view',N_('Просмотр')],['edit',N_('Редактирование')]];
const reportKeys=['report','pnl','balance','rating','business','reports'];
const reportMode=computed({get:()=>{const values=reportKeys.filter(k=>k in choices.value).map(k=>choices.value[k]);return values.length&&values.every(v=>v===values[0])?values[0]:'custom';},set:mode=>{if(mode!=='custom')for(const key of reportKeys)if(key in choices.value)choices.value[key]=mode;}});
let epoch=0;
async function load(){const turn=++epoch,uid=props.user.id,cid=company.value;busy.value=true;error.value='';policy.value=null;try{const value=await props.api(`/api/admin/users/${uid}/sections?company_id=${cid}`);if(turn!==epoch||uid!==props.user.id||cid!==company.value)return;policy.value=value;choices.value=Object.fromEntries(value.modules.map(m=>[m.key,value.sections[m.key]||'default']));}catch(e){if(turn===epoch)error.value=e.message;}finally{if(turn===epoch)busy.value=false;}}
async function save(){busy.value=true;error.value='';notice.value='';try{await props.api(`/api/admin/users/${props.user.id}/sections`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({company_id:Number(company.value),version:policy.value.version,sections:choices.value,reason:reason.value})});reason.value='';await load();notice.value=N_('Доступ сохранён. Пользователь войдёт заново; пароли не меняются.');}catch(e){error.value=e.message;}finally{busy.value=false;}}
watch(()=>[props.user.id,company.value],load,{immediate:true});
</script>
<template>
 <section class="section-access">
  <h3>{{tx('Доступ к разделам')}}</h3>
  <p>{{tx('Права действуют только в выбранной компании. Скрытые разделы закрыты и по прямой ссылке.')}}</p>
  <label>{{tx('Компания')}}<select v-model="company" :disabled="busy"><option v-for="c in companies.filter(c=>user.holding_role||c.id===user.company_id)" :key="c.id" :value="c.id">{{c.name}}</option></select></label>
  <p v-if="error" role="alert" class="error">{{tx(error)}}</p><p v-if="notice" role="status">{{tx(notice)}}</p>
  <form v-if="policy" @submit.prevent="save">
   <label>{{tx('Все отчёты')}}<select v-model="reportMode" :disabled="busy||policy.locked"><option value="custom">{{tx('Настроить отдельно')}}</option><option v-for="[key,label] in modes.filter(x=>x[0]!=='edit'||user.holding_role!=='founder')" :key="key" :value="key">{{tx(label)}}</option></select></label>
   <p v-if="policy.locked">{{tx('Администратор холдинга всегда имеет полный доступ.')}}</p>
   <table><thead><tr><th>{{tx('Раздел')}}</th><th>{{tx('Режим доступа')}}</th><th>{{tx('По роли')}}</th></tr></thead><tbody>
    <tr v-for="m in policy.modules" :key="m.key"><td>{{tx(m.label)}}</td><td><select v-model="choices[m.key]" :aria-label="tx('Доступ: {section}',{section:tx(m.label)})" :disabled="busy||policy.locked"><option v-for="[key,label] in modes.filter(x=>x[0]!=='edit'||(!['users','audit'].includes(m.key)&&user.holding_role!=='founder'))" :key="key" :value="key">{{tx(label)}}</option></select></td><td>{{tx(modes.find(x=>x[0]===m.default)?.[1]||N_('Скрыт'))}}</td></tr>
   </tbody></table>
   <label v-if="!policy.locked">{{tx('Причина')}}<textarea v-model="reason" minlength="10" maxlength="1000" required :disabled="busy"></textarea></label>
   <button v-if="!policy.locked" :disabled="busy">{{busy?tx('Сохраняем…'):tx('Сохранить доступ')}}</button>
  </form>
 </section>
</template>
<style scoped>.section-access{border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0}.section-access table{width:100%;margin:12px 0}.section-access td,.section-access th{padding:8px;text-align:left}.section-access select{max-width:100%;min-width:140px}.section-access label{display:grid;gap:6px;margin:10px 0}.section-access p{font-size:13px;line-height:1.5}</style>
