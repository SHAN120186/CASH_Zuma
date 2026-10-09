<script setup>
import AppIcon from './AppIcon.vue';
import {tx} from './i18n/index.js';

const props=defineProps({company:{type:Object,required:true},disabled:Boolean,canReports:Boolean});
const emit=defineEmits(['select','report']);
const finePointer=window.matchMedia('(hover: hover) and (pointer: fine)');
const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
function openReport(page){if(props.disabled||!props.canReports)return;emit('report',{companyId:props.company.id,page});}

function reset(event){
 const style=event.currentTarget.style;
 for(const name of ['--spot-x','--spot-y','--tilt-x','--tilt-y'])style.removeProperty(name);
}
function followPointer(event){
 if(props.disabled||event.pointerType!=='mouse'||!finePointer.matches||reducedMotion.matches){reset(event);return}
 // Measure the stationary shell so tilting the button cannot move its own target.
 const card=event.currentTarget,box=card.parentElement.getBoundingClientRect();
 if(!box.width||!box.height)return;
 const x=Math.max(0,Math.min(1,(event.clientX-box.left)/box.width));
 const y=Math.max(0,Math.min(1,(event.clientY-box.top)/box.height));
 card.style.setProperty('--spot-x',`${(x*100).toFixed(1)}%`);
 card.style.setProperty('--spot-y',`${(y*100).toFixed(1)}%`);
 card.style.setProperty('--tilt-x',`${((.5-y)*5).toFixed(2)}deg`);
 card.style.setProperty('--tilt-y',`${((x-.5)*5).toFixed(2)}deg`);
}
</script>

<template>
 <div class="company-card-shell">
  <button class="company-card" :aria-label="tx('Открыть компанию {name}',{name:company.name})" :disabled="disabled"
   @pointermove="followPointer" @pointerleave="reset" @pointercancel="reset" @blur="reset" @click="$emit('select',company.id)">
   <span class="company-symbol" aria-hidden="true">{{company.code.charAt(0)}}</span>
   <b>{{company.code==='ZUMA'?'ZUMA':company.name}}</b>
   <span class="company-enter" aria-hidden="true"><AppIcon name="arrow"/></span>
  </button>
  <div v-if="canReports" class="company-report-actions">
   <button type="button" class="secondary" :disabled="disabled" :aria-label="tx('Открыть {section} компании {name}',{section:tx('Бизнес-планы'),name:company.name})" @click="openReport('business')"><AppIcon name="business"/> {{tx('Бизнес-планы')}}</button>
   <button type="button" class="secondary" :disabled="disabled" :aria-label="tx('Открыть {section} компании {name}',{section:tx('Архив отчётов'),name:company.name})" @click="openReport('reports')"><AppIcon name="reports"/> {{tx('Архив отчётов')}}</button>
  </div>
 </div>
</template>
<style scoped>
.company-report-actions{display:grid;gap:8px;margin-top:12px;min-width:0}.company-report-actions button{width:100%;max-width:100%;min-width:0;white-space:normal;overflow-wrap:anywhere;padding:8px 12px;font-size:13px;line-height:1.4}.company-report-actions .app-icon{flex-shrink:0}
</style>
