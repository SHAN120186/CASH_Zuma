<script setup>
import AppIcon from './AppIcon.vue';

const props=defineProps({company:{type:Object,required:true},disabled:Boolean});
defineEmits(['select']);
const finePointer=window.matchMedia('(hover: hover) and (pointer: fine)');
const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');

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
 card.style.setProperty('--tilt-x',`${((.5-y)*6).toFixed(2)}deg`);
 card.style.setProperty('--tilt-y',`${((x-.5)*6).toFixed(2)}deg`);
}
</script>

<template>
 <div class="company-card-shell">
  <button class="company-card" :aria-label="'Открыть компанию '+company.name" :disabled="disabled"
   @pointermove="followPointer" @pointerleave="reset" @pointercancel="reset" @blur="reset" @click="$emit('select',company.id)">
   <span class="company-symbol" aria-hidden="true">{{company.code.charAt(0)}}</span>
   <b>{{company.code==='ZUMA'?'ZUMA':company.name}}</b>
   <span class="company-enter" aria-hidden="true"><AppIcon name="arrow"/></span>
  </button>
 </div>
</template>
