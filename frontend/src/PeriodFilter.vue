<script setup>
import {ref,computed,nextTick} from 'vue';
import AppIcon from './AppIcon.vue';
const emit=defineEmits(['apply']);
const props=defineProps({dateFrom:String,dateTo:String});
const expanded=ref(false),kind=ref('month'),from=ref(props.dateFrom||''),to=ref(props.dateTo||''),error=ref(''),month=ref(''),first=ref(null),toggler=ref(null);
const label=computed(()=>!props.dateFrom&&!props.dateTo?'все даты':(props.dateFrom||'начало истории')+' — '+(props.dateTo||'без ограничения'));
function chooseMonth(){if(!month.value)return;const [y,m]=month.value.split('-').map(Number);from.value=month.value+'-01';to.value=month.value+'-'+new Date(y,m,0).getDate()}
async function toggle(){expanded.value=!expanded.value;if(expanded.value){from.value=props.dateFrom||'';to.value=props.dateTo||'';error.value='';await nextTick();first.value?.focus()}}
function close(){if(!expanded.value)return;expanded.value=false;toggler.value?.focus()}
function apply(clear=false){if(!clear&&from.value&&to.value&&from.value>to.value){error.value='Начало периода позже окончания';return}error.value='';if(clear){from.value='';to.value=''}emit('apply',{from:from.value,to:to.value});expanded.value=false}
</script>
<template><div class="period-filter" @keydown.esc="close">
<div class="period-row"><button ref="toggler" class="secondary" type="button" :aria-expanded="expanded" aria-controls="period-panel" @click="toggle"><AppIcon name="calendar"/> Период: {{label}} <AppIcon name="chev"/></button>
<slot></slot></div>
<form v-if="expanded" id="period-panel" class="period-panel" @submit.prevent="apply()">
<div class="seg" role="group" aria-label="Тип периода"><button type="button" :class="{on:kind==='month'}" :aria-pressed="kind==='month'" @click="kind='month'">Месяц</button><button type="button" :class="{on:kind==='range'}" :aria-pressed="kind==='range'" @click="kind='range'">Даты с / по</button></div>
<label v-if="kind==='month'">Месяц<input ref="first" type="month" v-model="month" @change="chooseMonth"></label>
<template v-else><label>С даты<input ref="first" type="date" v-model="from" @change="month=''"></label><label>По дату включительно<input type="date" v-model="to" @change="month=''"></label></template>
<button>Применить</button><button type="button" class="secondary" @click="month='';apply(true)">Все даты</button><p v-if="error" role="alert">{{error}}</p>
</form>
</div></template>
