<script setup>
import {ref, watch, onUnmounted} from 'vue';
import {tx} from '../i18n/index.js';
const props=defineProps({disabled:Boolean,error:String,loaded:Number,total:Number,hasMore:Boolean});
const emit=defineEmits(['more']);
const target=ref(null);
let observer;
watch(()=>[target.value,props.disabled,props.error,props.loaded,props.hasMore],()=>{
  observer?.disconnect();
  if(!target.value||props.disabled||props.error||!props.hasMore||typeof IntersectionObserver==='undefined')return;
  observer=new IntersectionObserver(entries=>{
    if(entries.some(e=>e.isIntersecting)&&!props.disabled&&!props.error&&props.hasMore){observer.disconnect();emit('more');}
  },{rootMargin:'160px'});
  observer.observe(target.value);
},{flush:'post'});
onUnmounted(()=>observer?.disconnect());
</script>
<template>
 <div ref="target" class="pagination" aria-live="polite">
  <span>{{tx('Показано {loaded} из {total}',{loaded,total})}}</span>
  <span v-if="error" role="alert">{{error}}</span>
  <button v-if="hasMore" class="secondary tiny" :disabled="disabled" @click="emit('more')">{{disabled?tx('Загрузка…'):error?tx('Повторить загрузку'):tx('Показать ещё')}}</button>
 </div>
</template>
