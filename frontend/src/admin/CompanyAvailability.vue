<script setup>
import {ref,computed,watch,nextTick} from 'vue';
import {tx,N_} from '../i18n/index.js';
const props=defineProps({api:{type:Function,required:true},companies:{type:Array,default:()=>[]}});
const emit=defineEmits(['changed','refresh']);
const selected=ref(null),reason=ref(''),confirmed=ref(false),busy=ref(false),error=ref(''),notice=ref(''),reasonInput=ref(null);
const entries=computed(()=>props.companies.filter(c=>c.code!=='UNASSIGNED'));
const canSave=computed(()=>!!selected.value&&!busy.value&&confirmed.value&&reason.value.trim().length>=10);
function cancel(){if(busy.value)return;selected.value=null;reason.value='';confirmed.value=false;error.value='';}
function choose(company){if(busy.value)return;selected.value={...company,active:company.active!==false};reason.value='';confirmed.value=false;error.value='';notice.value='';nextTick(()=>reasonInput.value?.focus?.());}
watch(()=>props.companies,()=>{if(!selected.value||busy.value)return;const current=entries.value.find(c=>c.id===selected.value.id);if(!current||(current.active!==false)!==selected.value.active)cancel();},{deep:true});
async function save(){
 if(!canSave.value)return;
 const target={...selected.value},active=!target.active;
 busy.value=true;error.value='';notice.value='';
 try{
  const result=await props.api(`/api/admin/companies/${target.id}/availability`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({active,expected_active:target.active,reason:reason.value.trim()})});
  if(result?.company?.id!==target.id||result.company.active!==active)throw new Error(N_('Не удалось подтвердить состояние компании. Обновите список перед повторной попыткой.'));
  selected.value=null;reason.value='';confirmed.value=false;
  notice.value=active?N_('Компания включена. Доступ восстановлен.'):N_('Компания отключена. Данные и назначения сохранены.');
  emit('changed',result.company);
 }catch(e){error.value=e.message;}finally{busy.value=false;}
}
</script>
<template>
 <section class="company-availability card" aria-labelledby="company-availability-title" :aria-busy="busy">
  <div class="card-h"><h3 id="company-availability-title">{{tx('Управление компаниями')}}</h3></div>
  <div class="card-b">
   <p class="sub">{{tx('Отключение приостанавливает работу компании для сотрудников. Данные, роли и назначения сохраняются. Включить компанию обратно может администратор холдинга.')}}</p>
   <p v-if="notice" class="notice" role="status">{{tx(notice)}}</p>
   <ul class="availability-list">
    <li v-for="c in entries" :key="c.id" class="availability-row">
     <span><b>{{c.name}}</b><small>{{c.active===false?tx('Отключена'):tx('Включена')}}</small></span>
     <button type="button" role="switch" class="availability-switch" :class="{'is-off':c.active===false}" :disabled="busy"
      :aria-label="tx('Доступ к компании {name}',{name:c.name})" :aria-checked="c.active!==false"
      :title="c.active===false?tx('Включить компанию {name}',{name:c.name}):tx('Отключить компанию {name}',{name:c.name})" @click="choose(c)">
      <span class="availability-track" aria-hidden="true"></span><span aria-hidden="true">{{c.active===false?tx('Выкл'):tx('Вкл')}}</span>
     </button>
    </li>
   </ul>
   <p v-if="!entries.length" class="sub">{{tx('Нет доступных компаний. Обратитесь к администратору.')}}</p>
   <form v-if="selected" class="availability-form" @submit.prevent="save">
    <h4>{{selected.active?tx('Отключить компанию {name}',{name:selected.name}):tx('Включить компанию {name}',{name:selected.name})}}</h4>
    <p>{{selected.active?tx('Сотрудники не смогут открывать компанию и выполнять действия до её включения.'):tx('Сотрудники смогут снова работать по своим сохранённым назначениям.')}}</p>
    <label>{{tx('Причина изменения (не менее 10 символов)')}}<textarea ref="reasonInput" v-model="reason" minlength="10" maxlength="1000" required :disabled="busy"/></label>
    <label class="availability-confirm"><input v-model="confirmed" type="checkbox" required :disabled="busy">{{selected.active?tx('Подтверждаю отключение компании {name}',{name:selected.name}):tx('Подтверждаю включение компании {name}',{name:selected.name})}}</label>
    <p v-if="error" class="error" role="alert">{{tx(error)}} <button type="button" class="secondary tiny" :disabled="busy" @click="cancel();$emit('refresh')">{{tx('Обновить список')}}</button></p>
    <div class="actions"><button type="button" class="secondary" :disabled="busy" @click="cancel">{{tx('Отмена')}}</button><button :disabled="!canSave">{{busy?tx('Сохраняем…'):tx('Подтвердить изменение')}}</button></div>
   </form>
  </div>
 </section>
</template>
<style scoped>
.availability-list{list-style:none;padding:0;margin:14px 0;display:grid;gap:8px}.availability-row{display:flex;align-items:center;gap:12px;padding:12px;border:1px solid var(--line);border-radius:12px;min-width:0}.availability-row>span:first-child{flex:1;min-width:0;overflow-wrap:anywhere}.availability-row small{display:block;color:var(--muted);font-size:11px}.availability-switch{flex:none;display:inline-flex;align-items:center;justify-content:space-between;gap:9px;min-width:96px;min-height:44px;padding:6px 9px;border:1px solid var(--good);border-radius:12px;background:var(--good-soft);color:var(--good);font-weight:800}
.availability-switch.is-off{border-color:var(--bad);background:var(--bad-soft);color:var(--bad)}
.availability-switch:hover:not(:disabled){background:var(--good-soft);color:var(--good);border-color:var(--good)}.availability-switch.is-off:hover:not(:disabled){background:var(--bad-soft);color:var(--bad);border-color:var(--bad)}
.availability-switch:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
.availability-track{display:block;position:relative;width:32px;height:18px;flex:none;border-radius:999px;background:currentColor}
.availability-track::after{content:'';position:absolute;left:2px;top:2px;width:14px;height:14px;border-radius:50%;background:#fff}
.availability-switch:not(.is-off) .availability-track::after{left:16px}.availability-form{margin-top:16px;border:1px solid var(--line);border-radius:12px;padding:16px;display:grid;gap:12px}.availability-form h4,.availability-form p{margin:0}.availability-form>label:not(.availability-confirm){display:grid;gap:6px}.availability-form textarea{min-height:70px}.availability-confirm{display:flex;align-items:flex-start;gap:8px;font-size:13px}.availability-confirm input{flex:none;margin-top:3px}.availability-form .actions{justify-content:flex-end}@media(max-width:520px){.availability-row{flex-wrap:wrap;gap:8px}.availability-row>span:first-child{flex-basis:calc(100% - 110px)}.availability-switch{margin-left:auto}.availability-form{padding:12px}.availability-form .actions button{flex:1}}
</style>
