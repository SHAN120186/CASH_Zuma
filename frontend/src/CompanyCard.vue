<script setup>
import {computed} from 'vue';
import AppIcon from './AppIcon.vue';
import {tx} from './i18n/index.js';

const props=defineProps({company:{type:Object,required:true},disabled:Boolean});
const emit=defineEmits(['select']);
const suspended=computed(()=>props.company.active===false);
const tones=['violet','blue','teal','amber','rose'];
const brandTones={UZGERMED:'violet',ZUMA:'blue',SAMO:'teal',FAZO:'amber',UZYARN:'rose'};
const tone=computed(()=>brandTones[props.company.code]||tones[[...String(props.company.code||'')].reduce((n,c)=>n+c.charCodeAt(0),0)%tones.length]);
function select(){if(!props.disabled&&!suspended.value)emit('select',props.company.id);}
</script>

<template>
 <div class="company-card-shell" :class="['company-tone-'+tone,{'company-suspended':suspended}]">
  <button class="company-card" :aria-label="suspended?tx('Компания {name} отключена',{name:company.name}):tx('Открыть компанию {name}',{name:company.name})" :disabled="disabled||suspended" :aria-describedby="suspended?'company-unavailable-'+company.id:undefined" @click="select">
   <span class="company-symbol" aria-hidden="true">{{company.code.charAt(0)}}</span>
   <span class="company-copy"><b>{{company.code==='ZUMA'?'ZUMA':company.name}}</b><small>{{suspended?tx('Отключена'):tx('Рабочее пространство')}}</small></span>
   <span class="company-enter" aria-hidden="true"><AppIcon :name="suspended?'archive':'arrow'"/></span>
  </button>
  <p v-if="suspended" :id="'company-unavailable-'+company.id" class="company-disabled-note" role="status">{{tx('Компания отключена. Обратитесь к администратору.')}}</p>
 </div>
</template>
