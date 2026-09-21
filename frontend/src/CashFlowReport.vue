<script setup>
import {ref,computed,watch,nextTick} from 'vue';
import AppIcon from './AppIcon.vue';
const props=defineProps({report:Object,currency:String,year:[String,Number],api:Function,canPlan:Boolean,companies:Array,companyId:Number,scenario:String,asOf:String});
const emit=defineEmits(['refresh','year','company','scenario','as-of']);
const months=['Янв','Фев','Мар','Апр','Май','Июн','Июл','Авг','Сен','Окт','Ноя','Дек'];
const monthNames=['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
const mode=ref('actual'),start=ref(1),end=ref(12),per=ref('year'),unit=ref(props.currency==='UZS'?'m':'k'),collapsed=ref([]),editor=ref(false),editMonth=ref(1),draft=ref([]),opening=ref(''),reason=ref(''),error=ref(''),saving=ref(false),saved=ref(false);
const note=ref(''),noteMonth=ref(1),groupOpen=ref(false),groupMonth=ref(String(props.asOf).slice(0,7)),groupDay=ref(props.asOf),groupText=ref('');
const comparison=ref([]);
const units={m:{label:'Млн',div:1e8,digits:1},k:{label:'Тыс.',div:1e5,digits:1},x:{label:'Точно',div:100,digits:2}};
const indices=computed(()=>Array.from({length:Number(end.value)-Number(start.value)+1},(_,i)=>i+Number(start.value)-1));
const years=computed(()=>{const now=new Date().getFullYear(),y=Number(props.year)||now,set=new Set([y]);for(let k=now-3;k<=now+2;k++)set.add(k);return [...set].sort((a,b)=>a-b)});
watch(start,()=>{if(Number(start.value)>Number(end.value))end.value=start.value});
watch(end,()=>{if(Number(end.value)<Number(start.value))start.value=end.value});
watch(()=>props.currency,c=>{unit.value=c==='UZS'?'m':'k'});
watch(()=>[props.currency,props.year],()=>{editor.value=false;saved.value=false});
function setPer(p){per.value=p;if(p==='year'){start.value=1;end.value=12}else if(p!=='custom'){const q=Number(p.slice(1));start.value=(q-1)*3+1;end.value=q*3}}
const cents=v=>{if(v==null)return null;const [a,b='']=String(v).split('.');return BigInt(a)*100n+BigInt(b.padEnd(2,'0'))*(a.startsWith('-')?-1n:1n)};
const amount=n=>n==null?null:(n<0?'-':'')+(n<0?-n:n)/100n+'.'+String((n<0?-n:n)%100n).padStart(2,'0');
const difference=(a,b)=>a==null||b==null?null:amount(cents(a)-cents(b));
const sum=values=>values.some(v=>v==null)?null:amount(values.reduce((n,v)=>n+cents(v),0n));
function display(v,plus=false){
 if(v==null)return '—';
 const u=units[unit.value],n=Number(cents(v))/u.div;
 const text=Math.abs(n).toLocaleString('ru-RU',{minimumFractionDigits:u.digits,maximumFractionDigits:u.digits});
 return n<0?'−'+text:(plus&&n>0?'+'+text:text);
}
const unitCaption=computed(()=>({m:'млн ',k:'тыс. ',x:''}[unit.value])+props.currency);
const columns=computed(()=>mode.value==='plan'?['plan','values','delta']:['values']);
const label={plan:'План',values:'Факт',delta:'Откл.'};
const groups=computed(()=>props.report.groups.map(g=>({...g,rows:props.report.rows.filter(r=>r.activity===g.activity)})));
const summary=computed(()=>[
 {key:'opening',category:'Остаток на начало периода',values:props.report.opening,plan:props.report.plan_opening},
 {key:'net',category:'Чистое изменение денег',values:props.report.totals,plan:props.report.plan_totals},
 ...(props.report.opening_adjustments.some(v=>v!=='0.00')?[{key:'adjustment',category:'Ввод начальных остатков',values:props.report.opening_adjustments,plan:Array(12).fill(null)}]:[]),
 {key:'closing',category:'Остаток на конец периода',values:props.report.closing,plan:props.report.plan_closing}
]);
const body=computed(()=>{
 const out=[{t:'bal',key:'opening',label:summary.value[0].category,row:summary.value[0]}];
 for(const g of groups.value){
  out.push({t:'sec',key:'sec-'+g.activity,label:g.name,row:g,activity:g.activity});
  if(collapsed.value.includes(g.activity))continue;
  const ins=g.rows.filter(r=>r.kind==='in'),outs=g.rows.filter(r=>r.kind!=='in');
  if(ins.length){out.push({t:'sub',key:'in-'+g.activity,label:'Поступления'});ins.forEach(r=>out.push({t:'line',key:r.key,label:r.category,row:r}))}
  if(outs.length){out.push({t:'sub',key:'out-'+g.activity,label:'Выплаты'});outs.forEach(r=>out.push({t:'line',key:r.key,label:r.category,row:r}))}
 }
 for(const r of summary.value.slice(1))out.push({t:r.key==='net'?'net':r.key==='closing'?'close':'adj',key:r.key,label:r.category,row:r});
 return out;
});
const hasTotal=computed(()=>indices.value.length>1);
const blanks=computed(()=>(indices.value.length+(hasTotal.value?1:0))*columns.value.length);
function value(row,m,column){return column==='delta'?difference(row.values[m],row.plan[m]):row[column][m]}
function aggregate(row,column){const calc=key=>row.key==='opening'?row[key][indices.value[0]]:row.key==='closing'?row[key][indices.value.at(-1)]:sum(indices.value.map(m=>row[key][m]));return column==='delta'?difference(calc('values'),calc('plan')):calc(column)}
const devClass=v=>v==null||Number(v)===0?'':Number(v)>0?'pos':'neg';
function toggle(key){collapsed.value=collapsed.value.includes(key)?collapsed.value.filter(k=>k!==key):[...collapsed.value,key]}
const tiles=computed(()=>{
 const i0=indices.value[0],iN=indices.value.at(-1),op=props.report.groups.find(g=>g.activity==='operating'),pick=m=>indices.value.map(i=>m[i]);
 const adj=sum(pick(props.report.opening_adjustments));
 return [
  {k:'На начало периода',v:props.report.opening[i0],p:props.report.plan_opening[i0]},
  {k:'Операционная деятельность',v:op?sum(pick(op.values)):null,p:op?sum(pick(op.plan)):null},
  {k:'Чистое изменение',v:sum(pick(props.report.totals)),p:sum(pick(props.report.plan_totals)),note:adj&&adj!=='0.00'?'Ввод остатков: '+display(adj):''},
  {k:'На конец периода',v:props.report.closing[iN],p:props.report.plan_closing[iN]}
 ];
});
const periodLabel=computed(()=>per.value==='year'?`${props.year} год`:per.value==='custom'?`${months[Number(start.value)-1]}–${months[Number(end.value)-1]} ${props.year}`:`${per.value.slice(1)} квартал ${props.year}`);
const exportUrl=ext=>`/api/export/report.${ext}?year=${props.year}&currency=${props.currency}&mode=${mode.value}&start_month=${start.value}&end_month=${end.value}&company_id=${props.companyId}&scenario=${props.scenario}&as_of=${props.asOf}`;
async function edit(){editMonth.value=Number(start.value);editor.value=true;saved.value=false;resetDraft();await nextTick();document.querySelector('.plan-editor')?.scrollIntoView({block:'start'})}
function resetDraft(){const m=Number(editMonth.value)-1;draft.value=props.report.rows.map(r=>({category:r.category,category_id:r.category_id,kind:r.kind,amount:r.plan[m]==null?'':r.plan[m].replace('-','')}));opening.value=props.report.plan_opening_input[m]??'';reason.value='';error.value=''}
async function save(){saving.value=true;error.value='';try{await props.api('/api/cash-plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({company_id:props.companyId,scenario:props.scenario,month:`${props.year}-${String(editMonth.value).padStart(2,'0')}`,currency:props.currency,version:props.report.plan_versions[Number(editMonth.value)-1],opening:opening.value===''?null:String(opening.value),reason:reason.value,items:draft.value.map(({category,...r})=>({...r,amount:r.amount===''?null:String(r.amount)}))})});editor.value=false;saved.value=true;emit('refresh')}catch(e){error.value=e.message}finally{saving.value=false}}
function loadNote(){note.value=props.report.notes?.[`${props.year}-${String(noteMonth.value).padStart(2,'0')}:net`]||''}
async function saveNote(){saving.value=true;error.value='';try{await props.api('/api/plan-note',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({company_id:props.companyId,scenario:props.scenario,month:`${props.year}-${String(noteMonth.value).padStart(2,'0')}`,currency:props.currency,indicator:'net',note:note.value})});saved.value=true;emit('refresh')}catch(e){error.value=e.message}finally{saving.value=false}}
async function makeGroup(){error.value='';try{const q=new URLSearchParams({company_id:props.companyId,currency:props.currency,scenario:props.scenario,month:groupMonth.value,day:groupDay.value});groupText.value=(await props.api('/api/group-report?'+q)).text;groupOpen.value=true}catch(e){error.value=e.message}}
async function compare(){error.value='';try{const month=String(props.asOf).slice(0,7),base={company_id:props.companyId,currency:props.currency,month,day:props.asOf};comparison.value=await Promise.all(['A','B','V'].map(async scenario=>({...await props.api('/api/group-report?'+new URLSearchParams({...base,scenario})),scenario})))}catch(e){error.value=e.message}}
async function copyGroup(){await navigator.clipboard.writeText(groupText.value);saved.value=true}
</script>
<template>
 <div class="card tools">
  <div class="tl-row">
   <label class="fld">Компания <select :value="companyId" @change="emit('company',Number($event.target.value))"><option v-for="c in companies" :key="c.id" :value="c.id">{{c.name}}</option></select></label>
   <div class="seg" role="group" aria-label="Сценарий плана"><button v-for="s in ['A','B','V']" :key="s" :class="{on:scenario===s}" @click="emit('scenario',s)">Сценарий {{s}}</button></div>
   <label class="fld">Год <select aria-label="Год отчёта" :value="year" @change="emit('year',$event.target.value)"><option v-for="y in years" :key="y" :value="y">{{y}}</option></select></label>
   <label class="fld">Факт по дату <input type="date" :value="asOf" @change="emit('as-of',$event.target.value)"></label>
   <div class="seg" role="group" aria-label="Вид отчёта"><button :aria-pressed="mode==='actual'" :class="{on:mode==='actual'}" @click="mode='actual'">Факт</button><button :aria-pressed="mode==='plan'" :class="{on:mode==='plan'}" @click="mode='plan'">План-факт</button></div>
   <div class="seg" role="group" aria-label="Единицы измерения"><button v-for="(u,k) in units" :key="k" :aria-pressed="unit===k" :class="{on:unit===k}" @click="unit=k">{{u.label}}</button></div>
   <div class="grow"><a class="button secondary tiny" :href="exportUrl('xlsx')"><AppIcon name="download"/> Excel</a><a class="button secondary tiny" :href="exportUrl('pdf')"><AppIcon name="file"/> PDF</a><button class="secondary tiny" @click="compare">Сравнить А / Б / В</button><button class="secondary tiny" @click="makeGroup">Для группы</button><button v-if="canPlan" class="tiny" @click="edit">Задать план</button></div>
  </div>
  <div class="tl-row">
   <div class="chips" role="group" aria-label="Период"><button v-for="p in [['year','Год'],['q1','I кв.'],['q2','II кв.'],['q3','III кв.'],['q4','IV кв.'],['custom','Свой период']]" :key="p[0]" class="chip" :class="{on:per===p[0]}" :aria-pressed="per===p[0]" @click="setPer(p[0])">{{p[1]}}</button></div>
   <template v-if="per==='custom'"><label class="fld">с <select v-model="start"><option v-for="(m,i) in monthNames" :key="i" :value="i+1">{{m}}</option></select></label><label class="fld">по <select v-model="end"><option v-for="(m,i) in monthNames" :key="i" :value="i+1">{{m}}</option></select></label></template>
   <div class="grow"><button class="secondary tiny" @click="collapsed=groups.map(g=>g.activity)">Свернуть разделы</button><button class="secondary tiny" @click="collapsed=[]">Развернуть</button></div>
  </div>
 </div>
 <p v-if="saved" class="notice" role="status">План сохранён</p>
 <section v-if="comparison.length" class="card plan-editor"><div class="section-head"><div><h2>Сравнение сценариев на {{asOf}}</h2><p class="sub">Выбор ручной. Фактические банковские поступления одинаковы для А, Б и В и не меняются при переключении.</p></div><button class="ghost" @click="comparison=[]">Закрыть</button></div><div class="sumrow"><button v-for="r in comparison" :key="r.scenario" class="card tile" :class="{selected:r.scenario===scenario}" @click="emit('scenario',r.scenario)"><div class="k">Сценарий {{r.scenario}}</div><div class="v num">{{display(r.expense_plan)}}<small>{{unitCaption}}</small></div><div class="d">Поступления с начала месяца {{display(r.bank_income_mtd)}} {{unitCaption}} · доступно {{display(r.available)}} {{unitCaption}}</div></button></div></section>
 <section v-if="groupOpen" class="card plan-editor"><div class="section-head"><h2>Сообщение для группы</h2><button class="ghost" @click="groupOpen=false">Закрыть</button></div><div class="filter-bar"><label>Месяц<input type="month" v-model="groupMonth"></label><label>День<input type="date" v-model="groupDay"></label><button type="button" class="secondary" @click="makeGroup">Обновить</button></div><textarea readonly rows="5" :value="groupText"></textarea><button type="button" @click="copyGroup">Копировать</button><p class="sub">Текст только копируется. Программа ничего не отправляет автоматически.</p></section>
 <div class="sumrow"><div v-for="t in tiles" :key="t.k" class="card tile"><div class="k"><span>{{t.k}}</span><span class="pill" :class="mode==='plan'?'plan':'fact'">{{mode==='plan'?'План-факт':'Факт'}}</span></div><div class="v num">{{t.v==null?'Не задан':display(t.v)}}<small v-if="t.v!=null">{{unitCaption}}</small></div><div class="d"><template v-if="mode==='plan'">План {{t.p==null?'не задан':display(t.p)}}<template v-if="t.p!=null&&t.v!=null"> · <span :class="devClass(difference(t.v,t.p))">{{display(difference(t.v,t.p),true)}}</span></template></template><template v-else>{{t.note||'Фактические операции'}}</template></div></div></div>
 <div class="cf-cap"><h3>Отчёт о движении денежных средств · {{periodLabel}}</h3><span class="sub">Прямой метод · {{unitCaption}}<template v-if="mode==='plan'"> · План / Факт / Отклонение</template></span></div>
 <div class="cf-wrap" role="region" aria-label="Cash Flow по месяцам" tabindex="0"><table class="cf"><thead><tr><th class="lab" :rowspan="mode==='plan'?2:1">Статья · {{unitCaption}}</th><th v-for="m in indices" :key="m" :colspan="columns.length" class="mh">{{months[m]}} {{year}}</th><th v-if="hasTotal" :colspan="columns.length" class="mh tt">За период</th></tr><tr v-if="mode==='plan'"><template v-for="m in (hasTotal?[...indices,'total']:indices)" :key="m"><th v-for="(col,ci) in columns" :key="col" :class="[col==='plan'?'cp gs':col==='values'?'cf2':'',m==='total'?'tt':'']">{{label[col]}}</th></template></tr></thead><tbody>
  <tr v-for="r in body" :key="r.key" :class="'r-'+r.t">
   <template v-if="r.t==='sub'"><td class="lab"><span>{{r.label}}</span></td><td v-for="n in blanks" :key="n"></td></template>
   <template v-else>
    <td class="lab"><button v-if="r.t==='sec'" class="sec-btn" :class="{col:collapsed.includes(r.activity)}" :aria-expanded="!collapsed.includes(r.activity)" @click="toggle(r.activity)"><AppIcon name="chev"/>{{r.label}}</button><span v-else :title="r.t==='bal'||r.t==='close'?'Остатки не суммируются: в колонке «За период» показан остаток на границе периода':undefined">{{r.label}}</span></td>
    <template v-for="m in indices" :key="m"><td v-for="col in columns" :key="col" :class="[col==='plan'?'cp gs':'',col==='delta'?'cd '+devClass(value(r.row,m,col)):'']"><span v-if="col==='plan'&&value(r.row,m,col)==null" class="unset">Не задан</span><template v-else>{{col==='delta'?(value(r.row,m,col)==null?'—':Number(value(r.row,m,col))===0?'0':display(value(r.row,m,col),true)):display(value(r.row,m,col))}}</template><small v-if="col==='delta'&&r.key==='net'&&report.notes?.[`${year}-${String(m+1).padStart(2,'0')}:net`]" :title="report.notes[`${year}-${String(m+1).padStart(2,'0')}:net`]"> · Изоҳ</small></td></template>
    <template v-if="hasTotal"><td v-for="col in columns" :key="col" :class="['tt',col==='plan'?'cp gs':'',col==='delta'?'cd '+devClass(aggregate(r.row,col)):'']"><span v-if="col==='plan'&&aggregate(r.row,col)==null" class="unset">Не задан</span><template v-else>{{col==='delta'?(aggregate(r.row,col)==null?'—':Number(aggregate(r.row,col))===0?'0':display(aggregate(r.row,col),true)):display(aggregate(r.row,col))}}</template></td></template>
   </template>
  </tr>
 </tbody></table></div>
 <p class="cf-note"><AppIcon name="help"/><span>Остатки в колонке «За период» не складываются, а берутся на границе периода. Правила расчёта — в справке.</span></p>
 <section v-if="mode==='plan'&&canPlan" class="card plan-editor"><div class="section-head"><h2>Примечание / Изоҳ к отклонению</h2></div><div class="filter-bar"><label>Месяц<select v-model="noteMonth" @change="loadNote"><option v-for="(m,i) in months" :key="i" :value="i+1">{{m}}</option></select></label></div><textarea v-model="note" maxlength="2000" rows="3" placeholder="Причина отклонения по чистому денежному потоку"></textarea><button type="button" :disabled="saving" @click="saveNote">Сохранить примечание</button></section>
 <section v-if="editor" class="card plan-editor" aria-label="План денежных потоков"><div class="section-head"><h2>План · {{year}} · {{currency}}</h2><button class="ghost" @click="editor=false" :disabled="saving">Закрыть</button></div><form @submit.prevent="save"><div class="filter-bar"><label>Месяц<select v-model="editMonth" @change="resetDraft" :disabled="saving"><option v-for="(m,i) in months" :key="i" :value="i+1">{{m}}</option></select></label><label>Плановый остаток на начало<input v-model="opening" inputmode="decimal" pattern="[0-9]+([.][0-9]{1,2})?" placeholder="Из предыдущего плана"></label><button type="button" class="secondary" :disabled="saving" @click="draft.forEach(r=>{if(r.amount==='')r.amount='0'})">Пустые суммы → 0</button></div><p class="sub">Пустая сумма — план не задан. Укажите 0, если движения не планируются.</p><div class="plan-rows"><label v-for="r in draft" :key="r.category_id+r.kind"><span>{{r.category}}<small>{{r.kind==='in'?'Поступление':'Выплата'}}</small></span><input v-model="r.amount" :aria-label="r.category+' '+r.kind" inputmode="decimal" pattern="[0-9]+([.][0-9]{1,2})?" placeholder="Не задан" :disabled="saving"></label></div><label class="plan-reason">Основание изменения<textarea v-model="reason" required minlength="10" maxlength="1000" :disabled="saving"></textarea></label><p v-if="error" class="error" role="alert">{{error}}</p><button :disabled="saving">{{saving?'Сохраняем…':'Сохранить план'}}</button></form></section>
</template>
