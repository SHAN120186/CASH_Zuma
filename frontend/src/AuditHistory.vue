<script setup>
import {ref,onMounted,watch} from 'vue';
const props=defineProps({api:Function,refresh:Number});
const kind=ref('month'),month=ref(''),from=ref(''),to=ref(''),person=ref(''),action=ref(''),technical=ref(false),users=ref([]),actions=ref([]),items=ref([]),total=ref(0),page=ref(1),shown=ref(false),busy=ref(false),error=ref(''),opened=ref({});
const types={account:'Счёт',ledger:'Операция',request:'Заявка',category:'Статья',budget:'Бюджет',cash_plan:'План Cash Flow',user:'Пользователь',receipt:'Поступление',setting:'Настройка',import:'Импорт'};
const initials=n=>String(n||'').split(/\s+/).filter(Boolean).map(x=>x[0]).join('').slice(0,2).toUpperCase();
function chooseMonth(){if(!month.value)return;from.value=month.value+'-01';const [y,m]=month.value.split('-').map(Number);to.value=month.value+'-'+new Date(y,m,0).getDate();shown.value=false}
function invalidate(){shown.value=false}
watch(()=>props.refresh,()=>{if(shown.value)load(page.value)});
async function load(p=1){if(from.value&&to.value&&from.value>to.value){error.value='Начало периода позже окончания';return}busy.value=true;error.value='';try{const q=new URLSearchParams({page:String(p),technical:String(technical.value)});if(from.value)q.set('date_from',from.value);if(to.value)q.set('date_to',to.value);if(person.value)q.set('user_id',person.value);if(action.value)q.set('action',action.value);const d=await props.api('/api/audit/history?'+q);items.value=d.items;total.value=d.total;users.value=d.users;actions.value=d.actions;page.value=p;opened.value={};shown.value=true}catch(e){error.value=e.message}finally{busy.value=false}}
onMounted(async()=>{try{const d=await props.api('/api/audit/history?page_size=1');users.value=d.users;actions.value=d.actions}catch(e){error.value=e.message}});
</script>
<template>
<section class="card" style="margin-bottom:16px"><form class="lg-f" @submit.prevent="load(1)">
 <div class="seg" role="group" aria-label="Тип периода"><button type="button" :class="{on:kind==='month'}" :aria-pressed="kind==='month'" @click="kind='month'">Месяц</button><button type="button" :class="{on:kind==='range'}" :aria-pressed="kind==='range'" @click="kind='range'">Даты с / по</button></div>
 <label v-if="kind==='month'">Месяц<input type="month" v-model="month" @change="chooseMonth"></label>
 <template v-else><label>С даты<input type="date" v-model="from" @change="month='';invalidate()"></label><label>По дату<input type="date" v-model="to" @change="month='';invalidate()"></label></template>
 <label>Сотрудник<select v-model="person" @change="invalidate"><option value="">Все сотрудники</option><option v-for="u in users" :key="u.id" :value="u.id">{{u.name}}</option></select></label>
 <label>Действие<select v-model="action" @change="invalidate"><option value="">Все действия</option><option v-for="a in actions" :key="a">{{a}}</option></select></label>
 <button :disabled="busy">{{busy?'Поиск…':'Показать'}}</button>
</form>
<details style="padding:0 16px 14px"><summary style="cursor:pointer;font-weight:700;color:var(--mut);font-size:13px">Дополнительно</summary><label class="check-inline" style="margin-top:10px"><input type="checkbox" v-model="technical" @change="invalidate">Технические события</label></details></section>
<p v-if="error" class="error" role="alert">{{error}}</p>
<section v-if="!shown" class="empty-state card"><div class="empty-icon" aria-hidden="true">⊞</div><h2>Выберите период и нажмите «Показать»</h2><p>Затем здесь появится список действий сотрудников.</p></section>
<template v-else>
 <p class="sumline"><span>Найдено событий <b>{{total}}</b></span><span>Время Ташкента</span></p>
 <div v-if="items.length" class="card"><ul class="lg-list"><li v-for="r in items" :key="r.id" class="lg-e"><span class="ava">{{initials(r.user)}}</span><div class="w"><b>{{r.user}}</b> · {{r.action}}<em v-if="types[r.entity]"> {{types[r.entity]}} {{r.entity_id}}</em><small>{{r.date}}</small><div v-if="opened[r.id]" class="det">{{r.detail}}</div></div><button v-if="r.detail" class="secondary tiny" :aria-expanded="!!opened[r.id]" @click="opened[r.id]=!opened[r.id]">{{opened[r.id]?'Скрыть':'Подробности'}}</button></li></ul></div>
 <p v-else class="empty card">За выбранный период событий нет.</p>
 <div v-if="total>20" class="pagination"><span>Страница {{page}} из {{Math.max(1,Math.ceil(total/20))}}</span><div class="actions"><button class="secondary tiny" :disabled="page<=1||busy" @click="load(page-1)">← Назад</button><button class="secondary tiny" :disabled="page*20>=total||busy" @click="load(page+1)">Далее →</button></div></div>
</template>
</template>
