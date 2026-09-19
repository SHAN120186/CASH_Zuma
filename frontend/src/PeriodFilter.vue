<script setup>
import {ref,onUnmounted} from 'vue';
const emit=defineEmits(['apply']);
const props=defineProps({dateFrom:String,dateTo:String});
const expanded=ref(false),pinned=ref(false),from=ref(props.dateFrom||''),to=ref(props.dateTo||''),error=ref(''),month=ref('');
function chooseMonth(){if(!month.value)return;const [y,m]=month.value.split('-').map(Number);from.value=month.value+'-01';to.value=month.value+'-'+new Date(y,m,0).getDate()}
let timer;
function enterArea(){clearTimeout(timer)}
onUnmounted(()=>clearTimeout(timer));
function leave(event){const area=event.currentTarget;clearTimeout(timer);timer=setTimeout(()=>{if(!pinned.value&&!area.contains(document.activeElement))expanded.value=false},250)}
function apply(clear=false){if(!clear&&from.value&&to.value&&from.value>to.value){error.value='Начало периода позже окончания';return}error.value='';if(clear){from.value='';to.value=''}emit('apply',{from:from.value,to:to.value});expanded.value=false;pinned.value=false}
</script>
<template><div class="period-filter" @pointerenter="enterArea" @pointerleave="leave" @focusout="leave" @keydown.esc="expanded=false;pinned=false">
<div class="period-row"><button class="secondary" type="button" :aria-expanded="expanded" @pointerenter="e=>{if(e.pointerType==='mouse')expanded=true}" @click="pinned=!pinned;expanded=pinned">Период <span aria-hidden="true">▾</span></button>
<slot></slot></div>
<span class="period-caption">{{!dateFrom&&!dateTo?'Все даты':(dateFrom||'Начало истории')+' — '+(dateTo||'Без ограничения')}}</span>
<form v-if="expanded" class="period-panel" @submit.prevent="apply()"><label>Месяц<input type="month" v-model="month" @change="chooseMonth"></label><label>С даты<input type="date" v-model="from" @change="month=''"></label><label>По дату включительно<input type="date" v-model="to" @change="month=''"></label><button>Применить</button><button type="button" class="secondary" @click="month='';apply(true)">Все даты</button><p v-if="error" role="alert">{{error}}</p></form>
</div></template>
