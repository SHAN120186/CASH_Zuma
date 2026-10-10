<script setup>
import {ref,computed,watch,nextTick,onMounted,onBeforeUnmount} from 'vue';
import AppIcon from './AppIcon.vue';
import {editorMonth,planCompleteness,reportDateForYear,validReportDate} from './cashflow/planView.js';
import {toCents,formatAmount,amountTitle} from './overview/format.js';
import {tx,N_} from './i18n/index.js';
const props=defineProps({report:Object,currency:String,year:[String,Number],api:Function,canPlan:Boolean,companies:Array,companyId:Number,scenario:String,asOf:String,today:String});
const emit=defineEmits(['refresh','year','company','scenario','as-of','editing']);
const months=[N_('Янв'),N_('Фев'),N_('Мар'),N_('Апр'),N_('Май'),N_('Июн'),N_('Июл'),N_('Авг'),N_('Сен'),N_('Окт'),N_('Ноя'),N_('Дек')];
const monthNames=[N_('Январь'),N_('Февраль'),N_('Март'),N_('Апрель'),N_('Май'),N_('Июнь'),N_('Июль'),N_('Август'),N_('Сентябрь'),N_('Октябрь'),N_('Ноябрь'),N_('Декабрь')];
// Scenario letters are Cyrillic in Russian (А, Б, В) and Latin in Uzbek (A, B, V).
const scenarioLetters={A:N_('А'),B:N_('Б'),V:N_('В')};
const scenarioLetter=s=>tx(scenarioLetters[s]);
const mode=ref('actual'),start=ref(1),end=ref(12),per=ref('year'),unit=ref(props.currency==='UZS'?'m':'k'),collapsed=ref([]),editor=ref(false),editMonth=ref(1),draft=ref([]),opening=ref(''),reason=ref(''),error=ref(''),saving=ref(false),saved=ref(false);
const savedMonth=ref(null),dateError=ref('');
watch(editor,value=>emit('editing',value));
const note=ref(''),noteMonth=ref(1),groupOpen=ref(false),groupMonth=ref(String(props.asOf).slice(0,7)),groupDay=ref(props.asOf),groupText=ref('');
const comparison=ref([]);
const units={m:{label:N_('Млн'),div:1e8,digits:1},k:{label:N_('Тыс.'),div:1e5,digits:1},x:{label:N_('Точно'),div:100,digits:2}};
const indices=computed(()=>Array.from({length:Number(end.value)-Number(start.value)+1},(_,i)=>i+Number(start.value)-1));
const years=computed(()=>{const now=Number(String(props.today||props.asOf).slice(0,4)),y=Number(props.year),set=new Set();if(y>=2000&&y<=now)set.add(y);for(let k=Math.max(2000,now-3);k<=now;k++)set.add(k);return [...set].sort((a,b)=>a-b)});
const latestDate=computed(()=>reportDateForYear(props.year,props.today||props.asOf));
function changeAsOf(event){const value=event.target.value;dateError.value='';if(!validReportDate(value,props.year,props.today||props.asOf)){event.target.value=props.asOf;dateError.value=N_('Дата отчёта должна быть в выбранном году и не позже сегодня.');return}emit('as-of',value)}
watch(()=>[props.year,props.asOf],()=>{dateError.value=''});
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
// KPI tiles: compact "52 млн" / "350 тыс." whatever the table unit, every digit in the tooltip.
const short=(v,sign=false)=>formatAmount(toCents(v),props.currency,{sign});
const exact=(v,sign=false)=>v==null?undefined:amountTitle(toCents(v),props.currency,{sign});
const unitCaption=computed(()=>({m:tx('млн')+' ',k:tx('тыс.')+' ',x:''}[unit.value])+props.currency);
const columns=computed(()=>mode.value==='plan'?['plan','values','delta']:['values']);
const planStatus=computed(()=>planCompleteness(props.report.rows,indices.value));
const label={plan:N_('План'),values:N_('Факт'),delta:N_('Откл.')};
const groups=computed(()=>props.report.groups.map(g=>({...g,rows:props.report.rows.filter(r=>r.activity===g.activity)})));
const summary=computed(()=>[
 {key:'opening',category:tx('Остаток на начало периода'),values:props.report.opening,plan:props.report.plan_opening},
 {key:'net',category:tx('Чистое изменение денег'),values:props.report.totals,plan:props.report.plan_totals},
 ...(props.report.opening_adjustments.some(v=>v!=='0.00')?[{key:'adjustment',category:tx('Ввод начальных остатков'),values:props.report.opening_adjustments,plan:Array(12).fill(null)}]:[]),
 {key:'closing',category:tx('Остаток на конец периода'),values:props.report.closing,plan:props.report.plan_closing}
]);
const body=computed(()=>{
 const out=[{t:'bal',key:'opening',label:summary.value[0].category,row:summary.value[0]}];
 for(const g of groups.value){
  out.push({t:'sec',key:'sec-'+g.activity,label:tx(g.name),row:g,activity:g.activity});
  if(collapsed.value.includes(g.activity))continue;
  const ins=g.rows.filter(r=>r.kind==='in'),outs=g.rows.filter(r=>r.kind!=='in');
  if(ins.length){out.push({t:'sub',key:'in-'+g.activity,label:tx('Поступления')});ins.forEach(r=>out.push({t:'line',key:r.key,label:r.category,row:r}))}
  if(outs.length){out.push({t:'sub',key:'out-'+g.activity,label:tx('Выплаты')});outs.forEach(r=>out.push({t:'line',key:r.key,label:r.category,row:r}))}
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
  {k:tx('На начало периода'),v:props.report.opening[i0],p:props.report.plan_opening[i0]},
  {k:tx('Операционная деятельность'),v:op?sum(pick(op.values)):null,p:op?sum(pick(op.plan)):null},
  {k:tx('Чистое изменение'),v:sum(pick(props.report.totals)),p:sum(pick(props.report.plan_totals)),note:adj&&adj!=='0.00'?tx('Ввод остатков: {amount} {currency}',{amount:short(adj),currency:props.currency}):''},
  {k:tx('На конец периода'),v:props.report.closing[iN],p:props.report.plan_closing[iN]}
 ];
});
const periodLabel=computed(()=>per.value==='year'?tx('{year} год',{year:props.year}):per.value==='custom'?(Number(start.value)===Number(end.value)?`${tx(monthNames[Number(start.value)-1])} ${props.year}`:`${tx(months[Number(start.value)-1])}–${tx(months[Number(end.value)-1])} ${props.year}`):tx('{quarter} квартал {year}',{quarter:per.value.slice(1),year:props.year}));
// Plan completeness: a known subtotal with its units, or a plain "not set".
const planAmount=v=>v==null?tx('не заданы'):display(v)+' '+unitCaption.value;
const exportUrl=ext=>`/api/export/report.${ext}?year=${props.year}&currency=${props.currency}&mode=${mode.value}&start_month=${start.value}&end_month=${end.value}&company_id=${props.companyId}&scenario=${props.scenario}&as_of=${props.asOf}`;
async function edit(){editMonth.value=editorMonth(props.asOf,props.year,start.value,end.value);editor.value=true;saved.value=false;resetDraft();await nextTick();document.querySelector('.plan-editor')?.scrollIntoView({block:'start'})}
function resetDraft(){const m=Number(editMonth.value)-1;draft.value=props.report.rows.map(r=>({category:r.category,category_id:r.category_id,kind:r.kind,amount:r.plan[m]==null?'':r.plan[m].replace('-','')}));opening.value=props.report.plan_opening_input[m]??'';reason.value='';error.value=''}
async function save(){saving.value=true;error.value='';const month=Number(editMonth.value);try{await props.api('/api/cash-plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({company_id:props.companyId,scenario:props.scenario,month:`${props.year}-${String(month).padStart(2,'0')}`,currency:props.currency,version:props.report.plan_versions[month-1],opening:opening.value===''?null:String(opening.value),reason:reason.value,items:draft.value.map(({category,...r})=>({...r,amount:r.amount===''?null:String(r.amount)}))})});editor.value=false;saved.value=true;savedMonth.value=month;mode.value='plan';per.value='custom';start.value=month;end.value=month;emit('refresh');await nextTick();wrap.value?.scrollIntoView?.({block:'nearest'})}catch(e){error.value=e.message}finally{saving.value=false}}
function loadNote(){note.value=props.report.notes?.[`${props.year}-${String(noteMonth.value).padStart(2,'0')}:net`]||''}
async function saveNote(){saving.value=true;error.value='';try{await props.api('/api/plan-note',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({company_id:props.companyId,scenario:props.scenario,month:`${props.year}-${String(noteMonth.value).padStart(2,'0')}`,currency:props.currency,indicator:'net',note:note.value})});saved.value=true;emit('refresh')}catch(e){error.value=e.message}finally{saving.value=false}}
async function makeGroup(){error.value='';try{const q=new URLSearchParams({company_id:props.companyId,currency:props.currency,scenario:props.scenario,month:groupMonth.value,day:groupDay.value});groupText.value=(await props.api('/api/group-report?'+q)).text;groupOpen.value=true}catch(e){error.value=e.message}}
async function compare(){error.value='';try{const month=String(props.asOf).slice(0,7),base={company_id:props.companyId,currency:props.currency,month,day:props.asOf};comparison.value=await Promise.all(['A','B','V'].map(async scenario=>({...await props.api('/api/group-report?'+new URLSearchParams({...base,scenario})),scenario})))}catch(e){error.value=e.message}}
async function copyGroup(){await navigator.clipboard.writeText(groupText.value);saved.value=true}
// On a laptop the table is wider than the screen, and the months with data sit on the right.
// After loading (and when the period, view or units change) the month of "Факт по дату" is
// scrolled into view; a shadow on the right edge shows that more columns are hidden there.
const wrap=ref(null),moreRight=ref(false),bars=ref({x:0,y:0});
const isElement=el=>!!el&&typeof el.querySelector==='function'&&typeof el.getBoundingClientRect==='function';
function edges(){const el=wrap.value;if(!isElement(el))return;moreRight.value=el.scrollLeft+el.clientWidth<el.scrollWidth-1;bars.value={x:Math.max(0,el.offsetWidth-el.clientWidth-2*el.clientLeft),y:Math.max(0,el.offsetHeight-el.clientHeight-2*el.clientTop)}}
function showAsOfMonth(){
 const el=wrap.value;if(!isElement(el))return;
 const list=indices.value,y=Number(String(props.asOf).slice(0,4)),m=Number(String(props.asOf).slice(5,7))-1;
 const target=y===Number(props.year)?Math.min(Math.max(m,list[0]),list.at(-1)):y>Number(props.year)?list.at(-1):list[0];
 const th=el.querySelector(`th[data-m="${target}"]`);
 // 32px of the next column stay visible, so the edge shadow lies over it and not over the month.
 if(th){const visibleRight=el.getBoundingClientRect().left+el.clientLeft+el.clientWidth;el.scrollLeft=Math.max(0,el.scrollLeft+th.getBoundingClientRect().right-visibleRight+32)}
 autoLeft=el.scrollLeft;edges();
}
// Column widths can still change after the first scroll (web font, window size): the month
// is aligned again unless the user has scrolled the table themselves since.
let resize,autoLeft=null;
function resized(){const el=wrap.value;if(isElement(el)&&autoLeft!=null&&Math.abs(el.scrollLeft-autoLeft)<2)showAsOfMonth();else edges()}
onMounted(()=>{nextTick(showAsOfMonth);if(typeof ResizeObserver==='function'&&wrap.value){resize=new ResizeObserver(resized);resize.observe(wrap.value);if(wrap.value.firstElementChild)resize.observe(wrap.value.firstElementChild)}});
onBeforeUnmount(()=>resize?.disconnect());
watch(()=>[props.asOf,props.year,props.currency,props.scenario,mode.value,unit.value,start.value,end.value].join('|'),()=>nextTick(showAsOfMonth));
</script>
<template>
 <div class="card tools">
  <div class="tl-row">
   <span class="pill good">{{companies?.find(c=>c.id===companyId)?.name}}</span>
   <div class="seg" role="group" :aria-label="tx('Сценарий плана')"><button v-for="s in ['A','B','V']" :key="s" :class="{on:scenario===s}" :aria-pressed="scenario===s" @click="emit('scenario',s)">{{tx('Сценарий {letter}',{letter:scenarioLetter(s)})}}</button></div>
   <label class="fld">{{tx('Год')}} <select :aria-label="tx('Год отчёта')" :value="year" @change="emit('year',$event.target.value)"><option v-for="y in years" :key="y" :value="y">{{y}}</option></select></label>
   <label class="fld">{{tx('Факт по дату')}} <input type="date" :value="asOf" :min="`${year}-01-01`" :max="latestDate" @change="changeAsOf"></label>
   <div class="seg" role="group" :aria-label="tx('Вид отчёта')"><button :aria-pressed="mode==='actual'" :class="{on:mode==='actual'}" @click="mode='actual'">{{tx('Факт')}}</button><button :aria-pressed="mode==='plan'" :class="{on:mode==='plan'}" @click="mode='plan'">{{tx('План-факт')}}</button></div>
   <div class="seg" role="group" :aria-label="tx('Единицы измерения')"><button v-for="(u,k) in units" :key="k" :aria-pressed="unit===k" :class="{on:unit===k}" @click="unit=k">{{tx(u.label)}}</button></div>
   <div class="grow"><a class="button secondary tiny" :href="exportUrl('xlsx')"><AppIcon name="download"/> Excel</a><a class="button secondary tiny" :href="exportUrl('pdf')"><AppIcon name="file"/> PDF</a><button class="secondary tiny" @click="compare">{{tx('Сравнить А / Б / В')}}</button><button class="secondary tiny" @click="makeGroup">{{tx('Для группы')}}</button><button v-if="canPlan" class="tiny" @click="edit">{{tx('Задать план')}}</button></div>
  </div>
  <div class="tl-row">
   <div class="chips" role="group" :aria-label="tx('Период')"><button v-for="p in [['year',tx('Год')],['q1',tx('I кв.')],['q2',tx('II кв.')],['q3',tx('III кв.')],['q4',tx('IV кв.')],['custom',tx('Свой период')]]" :key="p[0]" class="chip" :class="{on:per===p[0]}" :aria-pressed="per===p[0]" @click="setPer(p[0])">{{p[1]}}</button></div>
   <template v-if="per==='custom'"><label class="fld">{{tx('с')}} <select v-model="start"><option v-for="(m,i) in monthNames" :key="i" :value="i+1">{{tx(m)}}</option></select></label><label class="fld">{{tx('по')}} <select v-model="end"><option v-for="(m,i) in monthNames" :key="i" :value="i+1">{{tx(m)}}</option></select></label></template>
   <div class="grow"><button class="secondary tiny" @click="collapsed=groups.map(g=>g.activity)">{{tx('Свернуть разделы')}}</button><button class="secondary tiny" @click="collapsed=[]">{{tx('Развернуть')}}</button></div>
  </div>
 </div>
 <p v-if="dateError" class="error" role="alert">{{tx(dateError)}}</p>
 <p v-if="saved" class="notice" role="status">{{tx('План сохранён')}}<template v-if="savedMonth"> · {{tx(monthNames[savedMonth-1])}} {{year}} · {{currency}} · {{tx('сценарий {letter}',{letter:scenarioLetter(scenario)})}}</template></p>
 <section v-if="comparison.length" class="card plan-editor"><div class="section-head"><div><h2>{{tx('Сравнение сценариев на {date}',{date:asOf})}}</h2><p class="sub">{{tx('Выбор ручной. Фактические банковские поступления одинаковы для А, Б и В и не меняются при переключении.')}}</p></div><button class="ghost" @click="comparison=[]">{{tx('Закрыть')}}</button></div><div class="sumrow"><button v-for="r in comparison" :key="r.scenario" class="card tile" :class="{selected:r.scenario===scenario}" @click="emit('scenario',r.scenario)"><div class="k">{{tx('Сценарий {letter}',{letter:scenarioLetter(r.scenario)})}}</div><div class="v num">{{r.expense_plan_complete||r.expense_plan_defined_rows>0?display(r.expense_plan):tx('Не задан')}}<small v-if="r.expense_plan_complete||r.expense_plan_defined_rows>0">{{unitCaption}}</small></div><div class="d" role="status">{{r.expense_plan_complete?tx('Полный план выплат'):r.expense_plan_defined_rows>0?tx('Частичный план выплат'):tx('План выплат не задан')}}<template v-if="r.expense_plan_complete===false"> · {{tx('незаполненных строк: {count}',{count:r.expense_plan_missing_rows})}}</template></div><div class="d">{{tx('Поступления с начала месяца {income} {unit} · доступно {available} {unit}',{income:display(r.bank_income_mtd),available:display(r.available),unit:unitCaption})}}</div></button></div></section>
 <section v-if="groupOpen" class="card plan-editor"><div class="section-head"><h2>{{tx('Сообщение для группы')}}</h2><button class="ghost" @click="groupOpen=false">{{tx('Закрыть')}}</button></div><div class="filter-bar"><label>{{tx('Месяц')}}<input type="month" v-model="groupMonth"></label><label>{{tx('День')}}<input type="date" v-model="groupDay"></label><button type="button" class="secondary" @click="makeGroup">{{tx('Обновить')}}</button></div><textarea readonly rows="5" :value="groupText"></textarea><button type="button" @click="copyGroup">{{tx('Копировать')}}</button><p class="sub">{{tx('Текст только копируется. Программа ничего не отправляет автоматически.')}}</p></section>
 <div class="sumrow"><div v-for="t in tiles" :key="t.k" class="card tile"><div class="k"><span>{{t.k}}</span><span class="pill" :class="mode==='plan'?'plan':'fact'">{{mode==='plan'?tx('План-факт'):tx('Факт')}}</span></div><div class="v num" :title="exact(t.v)">{{t.v==null?tx('Не задан'):short(t.v)}}<small v-if="t.v!=null">{{currency}}</small></div><div class="d"><template v-if="mode==='plan'">{{tx('План')}} <span :title="exact(t.p)">{{t.p==null?tx('не задан'):short(t.p)+' '+currency}}</span><template v-if="t.p!=null&&t.v!=null"> · <span :class="devClass(difference(t.v,t.p))" :title="exact(difference(t.v,t.p),true)">{{short(difference(t.v,t.p),true)}}</span></template></template><template v-else>{{t.note||tx('Фактические операции')}}</template></div></div></div>
 <div class="cf-cap"><h3>{{tx('Отчёт о движении денежных средств')}} · {{periodLabel}}</h3><span class="sub">{{tx('Прямой метод')}} · {{unitCaption}}<template v-if="mode==='plan'"> · {{tx('План / Факт / Отклонение')}}</template></span></div>
 <section v-if="mode==='plan'&&planStatus.missingRows" class="card plan-editor" :aria-label="tx('Полнота плана')"><h3>{{planStatus.knownCells?tx('Частичный план'):tx('План не задан')}}</h3><p>{{tx('Заданные суммы: поступления {income} · выплаты {expense}.',{income:planAmount(planStatus.income),expense:planAmount(planStatus.expense)})}}</p><p class="sub">{{tx('Незаполненные строки: {count} за выбранный период. Полный итог и плановый остаток на конец не определены. Пустые суммы не равны нулю.',{count:planStatus.missingRows})}}</p></section>
 <div class="cf-frame" :class="{more:moreRight,'v-bar':bars.x>0}" :style="{'--cf-bar-x':bars.x+'px','--cf-bar-y':bars.y+'px'}"><div ref="wrap" class="cf-wrap" role="region" :aria-label="tx('Cash Flow по месяцам')" tabindex="0" @scroll.passive="edges"><table class="cf"><thead><tr><th class="lab" :rowspan="mode==='plan'?2:1">{{tx('Статья')}} · {{unitCaption}}</th><th v-for="m in indices" :key="m" :colspan="columns.length" class="mh" :data-m="m">{{tx(months[m])}} {{year}}</th><th v-if="hasTotal" :colspan="columns.length" class="mh tt">{{tx('За период')}}</th></tr><tr v-if="mode==='plan'"><template v-for="m in (hasTotal?[...indices,'total']:indices)" :key="m"><th v-for="(col,ci) in columns" :key="col" :class="[col==='plan'?'cp gs':col==='values'?'cf2':'',m==='total'?'tt':'']">{{tx(label[col])}}</th></template></tr></thead><tbody>
  <tr v-for="r in body" :key="r.key" :class="'r-'+r.t">
   <template v-if="r.t==='sub'"><td class="lab"><span>{{r.label}}</span></td><td v-for="n in blanks" :key="n"></td></template>
   <template v-else>
    <td class="lab"><button v-if="r.t==='sec'" class="sec-btn" :class="{col:collapsed.includes(r.activity)}" :aria-expanded="!collapsed.includes(r.activity)" @click="toggle(r.activity)"><AppIcon name="chev"/>{{r.label}}</button><span v-else :title="r.t==='bal'||r.t==='close'?tx('Остатки не суммируются: в колонке «За период» показан остаток на границе периода'):undefined">{{r.label}}</span></td>
    <template v-for="m in indices" :key="m"><td v-for="col in columns" :key="col" :class="[col==='plan'?'cp gs':'',col==='delta'?'cd '+devClass(value(r.row,m,col)):'']"><span v-if="col==='plan'&&value(r.row,m,col)==null" class="unset">{{tx('Не задан')}}</span><template v-else>{{col==='delta'?(value(r.row,m,col)==null?'—':Number(value(r.row,m,col))===0?'0':display(value(r.row,m,col),true)):display(value(r.row,m,col))}}</template><small v-if="col==='delta'&&r.key==='net'&&report.notes?.[`${year}-${String(m+1).padStart(2,'0')}:net`]" :title="report.notes[`${year}-${String(m+1).padStart(2,'0')}:net`]"> · {{tx('Изоҳ')}}</small></td></template>
    <template v-if="hasTotal"><td v-for="col in columns" :key="col" :class="['tt',col==='plan'?'cp gs':'',col==='delta'?'cd '+devClass(aggregate(r.row,col)):'']"><span v-if="col==='plan'&&aggregate(r.row,col)==null" class="unset">{{tx('Не задан')}}</span><template v-else>{{col==='delta'?(aggregate(r.row,col)==null?'—':Number(aggregate(r.row,col))===0?'0':display(aggregate(r.row,col),true)):display(aggregate(r.row,col))}}</template></td></template>
   </template>
  </tr>
 </tbody></table></div></div>
 <p class="cf-note"><AppIcon name="help"/><span>{{tx('Остатки в колонке «За период» не складываются, а берутся на границе периода. Правила расчёта — в справке.')}}</span></p>
 <section v-if="mode==='plan'&&canPlan" class="card plan-editor"><div class="section-head"><h2>{{tx('Примечание / Изоҳ к отклонению')}}</h2></div><div class="filter-bar"><label>{{tx('Месяц')}}<select v-model="noteMonth" @change="loadNote"><option v-for="(m,i) in months" :key="i" :value="i+1">{{tx(m)}}</option></select></label></div><textarea v-model="note" maxlength="2000" rows="3" :placeholder="tx('Причина отклонения по чистому денежному потоку')"></textarea><button type="button" :disabled="saving" @click="saveNote">{{tx('Сохранить примечание')}}</button></section>
 <section v-if="editor" class="card plan-editor" :aria-label="tx('План денежных потоков')"><div class="section-head"><h2>{{tx('План')}} · {{year}} · {{currency}}</h2><button class="ghost" @click="editor=false" :disabled="saving">{{tx('Закрыть')}}</button></div><form @submit.prevent="save"><div class="filter-bar"><label>{{tx('Месяц')}}<select v-model="editMonth" @change="resetDraft" :disabled="saving"><option v-for="(m,i) in months" :key="i" :value="i+1">{{tx(m)}}</option></select></label><label>{{tx('Плановый остаток на начало')}}<input v-model="opening" inputmode="decimal" pattern="[0-9]+([.][0-9]{1,2})?" :placeholder="tx('Из предыдущего плана')"></label><button type="button" class="secondary" :disabled="saving" @click="draft.forEach(r=>{if(r.amount==='')r.amount='0'})">{{tx('Пустые суммы → 0')}}</button></div><p class="sub">{{tx('Пустая сумма — план не задан. Укажите 0, если движения не планируются.')}}</p><div class="plan-rows"><label v-for="r in draft" :key="r.category_id+r.kind"><span>{{r.category}}<small>{{r.kind==='in'?tx('Поступление'):tx('Выплата')}}</small></span><input v-model="r.amount" :aria-label="r.category+' '+r.kind" inputmode="decimal" pattern="[0-9]+([.][0-9]{1,2})?" :placeholder="tx('Не задан')" :disabled="saving"></label></div><label class="plan-reason">{{tx('Основание изменения')}}<textarea v-model="reason" required minlength="10" maxlength="1000" :disabled="saving"></textarea></label><p v-if="error" class="error" role="alert">{{tx(error)}}</p><button :disabled="saving">{{saving?tx('Сохраняем…'):tx('Сохранить план')}}</button></form></section>
</template>
