<script setup>
import {computed,ref,watch,onUnmounted} from 'vue';
import {progressPercent} from './operationProgress.js';
import {tx} from './i18n/index.js';
const props=defineProps({active:{type:Boolean,default:false},label:{type:String,default:''},
  completed:{type:Number,default:0},total:{type:Number,default:0},error:{type:String,default:''}});
const totalCount=computed(()=>Number.isFinite(props.total)&&props.total>0?Math.floor(props.total):0);
const completedCount=computed(()=>Math.min(totalCount.value||Infinity,Math.max(0,Number.isFinite(props.completed)?Math.floor(props.completed):0)));
const percent=computed(()=>progressPercent(completedCount.value,totalCount.value));
const completionVisible=ref(false);
let completionTimer=null;
const clearCompletionTimer=()=>{if(completionTimer!==null){clearTimeout(completionTimer);completionTimer=null;}};
watch(()=>[props.active,props.completed,props.total,props.error,props.label],()=>{
  clearCompletionTimer();completionVisible.value=false;
  if(!props.active&&!props.error&&completedCount.value>0){
    completionVisible.value=true;
    completionTimer=setTimeout(()=>{completionVisible.value=false;completionTimer=null;},2200);
  }
},{immediate:true});
onUnmounted(clearCompletionTimer);
const visible=computed(()=>props.active||completionVisible.value||!!props.error);
// Labels and errors arrive as Russian source text (or server messages) and are translated here.
// Several failures are joined with " · ": each part is translated when the whole is not known.
const labelText=computed(()=>props.label?tx(props.label):tx('Выполняем действие'));
const errorText=computed(()=>{const whole=tx(props.error);return whole!==props.error?whole:props.error.split(' · ').map(part=>tx(part)).join(' · ')});
</script>
<template>
  <section v-if="visible" class="operation-progress" :class="{'has-error':!!error}" role="status" aria-live="polite" aria-atomic="true">
    <div class="operation-progress-title"><span v-if="active" class="operation-spinner" aria-hidden="true"></span><strong>{{labelText}}</strong><b v-if="percent!==null" class="operation-percent">{{percent}}%</b></div>
    <div class="operation-progress-track" role="progressbar" :aria-label="label?tx(label):tx('Прогресс действия')" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="percent===null?undefined:percent" :aria-valuetext="percent===null?tx('Ожидаем ответ сервера'):percent+'%'">
      <span v-if="percent!==null" :style="{width:percent+'%'}"></span><span v-else class="operation-indeterminate"></span>
    </div>
    <p class="operation-progress-caption">{{totalCount?tx('Прогресс этапов · {done} из {total}',{done:completedCount,total:totalCount}):tx('Ожидаем ответ сервера · количество этапов пока неизвестно')}}</p>
    <p v-if="active" class="operation-progress-caption">{{totalCount?tx('Процент показывает завершённые этапы. Ожидаем завершения текущего этапа.'):tx('Обновим прогресс после ответа сервера.')}}</p>
    <p v-if="error" class="operation-progress-error">{{errorText}}</p>
  </section>
</template>
<style scoped>
.operation-progress{border:1px solid var(--line);border-radius:12px;padding:12px 14px;background:var(--emx);margin:12px 0}.operation-progress-title{display:flex;align-items:center;gap:9px}.operation-progress-title strong{flex:1;min-width:0;overflow-wrap:anywhere;font-size:13px}.operation-percent{font-variant-numeric:tabular-nums;white-space:nowrap;color:var(--em)}.operation-progress-track{height:5px;border-radius:10px;background:var(--line);overflow:hidden;margin:10px 0 8px}.operation-progress-track>span{display:block;height:100%;background:var(--em);border-radius:10px}.operation-progress-track>.operation-indeterminate{width:35%;animation:operation-slide 1.5s ease-in-out infinite}.operation-progress-caption{font-size:11px;color:var(--muted);line-height:1.5;margin:4px 0}.operation-progress-error{font-size:12px;color:var(--bad,#b42318);margin:8px 0 0;overflow-wrap:anywhere}.operation-progress.has-error{background:var(--warnl);border-color:var(--warn,#a76500)}.operation-spinner{width:14px;height:14px;flex:none;border:2px solid var(--line);border-top-color:var(--em);border-radius:50%;animation:operation-spin .9s linear infinite}@keyframes operation-spin{to{transform:rotate(360deg)}}@keyframes operation-slide{0%{transform:translateX(-110%)}100%{transform:translateX(400%)}}@media(prefers-reduced-motion:reduce){.operation-spinner,.operation-indeterminate{animation:none}}
</style>
