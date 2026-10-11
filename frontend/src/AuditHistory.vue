<script setup>
import {ref,onMounted,watch} from 'vue';
import {tx,N_} from './i18n/index.js';
const props=defineProps({api:Function,refresh:Number});
const kind=ref('month'),month=ref(''),from=ref(''),to=ref(''),person=ref(''),action=ref(''),technical=ref(false),users=ref([]),actions=ref([]),items=ref([]),total=ref(0),page=ref(1),shown=ref(false),busy=ref(false),error=ref(''),opened=ref({});
const types={account:N_('Счёт'),ledger:N_('Операция'),request:N_('Заявка'),category:N_('Статья'),budget:N_('Бюджет'),cash_plan:N_('План Cash Flow'),user:N_('Пользователь'),receipt:N_('Поступление'),setting:N_('Настройка'),import:N_('Импорт'),delegation:N_('ВрИО'),calendar:N_('Календарь'),document:N_('Документ')};
// Employee names are user data; only the server's own name for automatic events is translated.
const userName=n=>n==='Система'?tx('Система'):n; // i18n-ignore: the comparison matches the server's fixed name
const initials=n=>String(n||'').split(/\s+/).filter(Boolean).map(x=>x[0]).join('').slice(0,2).toUpperCase();
function chooseMonth(){if(!month.value)return;from.value=month.value+'-01';const [y,m]=month.value.split('-').map(Number);to.value=month.value+'-'+new Date(y,m,0).getDate();shown.value=false}
function invalidate(){shown.value=false}
watch(()=>props.refresh,()=>{if(shown.value)load(page.value)});
async function load(p=1){if(from.value&&to.value&&from.value>to.value){error.value=N_('Начало периода позже окончания');return}busy.value=true;error.value='';try{const q=new URLSearchParams({page:String(p),technical:String(technical.value)});if(from.value)q.set('date_from',from.value);if(to.value)q.set('date_to',to.value);if(person.value)q.set('user_id',person.value);if(action.value)q.set('action',action.value);const d=await props.api('/api/audit/history?'+q);items.value=d.items;total.value=d.total;users.value=d.users;actions.value=d.actions;page.value=p;opened.value={};shown.value=true}catch(e){error.value=e.message}finally{busy.value=false}}
onMounted(async()=>{try{const d=await props.api('/api/audit/history?page_size=1');users.value=d.users;actions.value=d.actions}catch(e){error.value=e.message}});
</script>
<template>
<section class="card" style="margin-bottom:16px"><form class="lg-f" @submit.prevent="load(1)">
 <div class="seg" role="group" :aria-label="tx('Тип периода')"><button type="button" :class="{on:kind==='month'}" :aria-pressed="kind==='month'" @click="kind='month'">{{tx('Месяц')}}</button><button type="button" :class="{on:kind==='range'}" :aria-pressed="kind==='range'" @click="kind='range'">{{tx('Даты с / по')}}</button></div>
 <label v-if="kind==='month'">{{tx('Месяц')}}<input type="month" v-model="month" @change="chooseMonth"></label>
 <template v-else><label>{{tx('С даты')}}<input type="date" v-model="from" @change="month='';invalidate()"></label><label>{{tx('По дату')}}<input type="date" v-model="to" @change="month='';invalidate()"></label></template>
 <label>{{tx('Сотрудник')}}<select v-model="person" @change="invalidate"><option value="">{{tx('Все сотрудники')}}</option><option v-for="u in users" :key="u.id" :value="u.id">{{u.name}}</option></select></label>
 <label>{{tx('Действие')}}<select v-model="action" @change="invalidate"><option value="">{{tx('Все действия')}}</option><option v-for="a in actions" :key="a" :value="a">{{tx(a)}}</option></select></label>
 <button :disabled="busy">{{busy?tx('Поиск…'):tx('Показать')}}</button>
</form>
<details style="padding:0 16px 14px"><summary style="cursor:pointer;font-weight:700;color:var(--mut);font-size:13px">{{tx('Дополнительно')}}</summary><label class="check-inline" style="margin-top:10px"><input type="checkbox" v-model="technical" @change="invalidate">{{tx('Технические события')}}</label></details></section>
<p v-if="error" class="error" role="alert">{{tx(error)}}</p>
<section v-if="!shown" class="empty-state card"><div class="empty-icon" aria-hidden="true">⊞</div><h2>{{tx('Выберите период и нажмите «Показать»')}}</h2><p>{{tx('Затем здесь появится список действий сотрудников.')}}</p></section>
<template v-else>
 <p class="sumline"><span>{{tx('Найдено событий')}} <b>{{total}}</b></span><span>{{tx('Время Ташкента')}}</span></p>
 <div v-if="items.length" class="card"><ul class="lg-list"><li v-for="r in items" :key="r.id" class="lg-e"><span class="ava">{{initials(userName(r.user))}}</span><div class="w"><b>{{userName(r.user)}}</b> · {{tx(r.action)}}<em v-if="types[r.entity]"> {{tx(types[r.entity])}} {{r.entity_id}}</em><small>{{r.date}}<template v-if="r.role"> · {{tx(r.role)}}</template><template v-if="r.acting_for"> · {{tx('ВрИО за {name}',{name:r.acting_for})}}</template><template v-if="r.ip"> · IP {{r.ip}}</template></small><div v-if="opened[r.id]" class="det">{{r.detail}}</div></div><button v-if="r.detail" class="secondary tiny" :aria-expanded="!!opened[r.id]" @click="opened[r.id]=!opened[r.id]">{{opened[r.id]?tx('Скрыть'):tx('Подробности')}}</button></li></ul></div>
 <p v-else class="empty card">{{tx('За выбранный период событий нет.')}}</p>
 <div v-if="total>20" class="pagination"><span>{{tx('Страница {page} из {pages}',{page,pages:Math.max(1,Math.ceil(total/20))})}}</span><div class="actions"><button class="secondary tiny" :disabled="page<=1||busy" @click="load(page-1)">← {{tx('Назад')}}</button><button class="secondary tiny" :disabled="page*20>=total||busy" @click="load(page+1)">{{tx('Далее')}} →</button></div></div>
</template>
</template>
