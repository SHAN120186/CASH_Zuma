<script setup>
import {computed} from 'vue';
import AppIcon from './AppIcon.vue';
import {tx} from './i18n/index.js';

const props=defineProps({company:{type:Object,required:true},disabled:Boolean});
const emit=defineEmits(['select']);
const suspended=computed(()=>props.company.active===false);
function select(){if(!props.disabled&&!suspended.value)emit('select',props.company.id);}
</script>

<template>
 <div class="company-card-shell" :class="{'company-suspended':suspended}">
  <button class="company-card" :aria-label="suspended?tx('Компания {name} отключена',{name:company.name}):tx('Открыть компанию {name}',{name:company.name})" :disabled="disabled||suspended" :aria-describedby="suspended?'company-unavailable-'+company.id:undefined" @click="select">
   <span class="company-symbol" aria-hidden="true">{{company.code.charAt(0)}}</span>
   <span class="company-copy"><b>{{company.code==='ZUMA'?'ZUMA':company.name}}</b><small v-if="!suspended">{{tx('Рабочее пространство')}}</small></span>
   <span v-if="suspended" class="company-off-badge"><AppIcon name="lock" aria-hidden="true"/>{{tx('Отключена')}}</span>
   <span v-else class="company-enter" aria-hidden="true"><AppIcon name="arrow"/></span>
  </button>
  <p v-if="suspended" :id="'company-unavailable-'+company.id" class="company-disabled-note" role="status">{{tx('Компания отключена. Обратитесь к администратору.')}}</p>
 </div>
</template>
