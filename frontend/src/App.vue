<script setup>
import {ref,computed,onMounted,onUnmounted,nextTick,watch} from 'vue';
import CashFlowReport from './CashFlowReport.vue';
import AuditHistory from './AuditHistory.vue';
import PeriodFilter from './PeriodFilter.vue';
import AppIcon from './AppIcon.vue';
import {loadRecentLogins,rememberLogin,matchingLogins,offerPasswordSave} from './loginPreferences.js';
import release from '../../release.json';
const availableRelease=ref(null),showRelease=ref(false);
let releaseTimer;
async function checkRelease(){
 if(document.hidden)return;
 try{const response=await fetch('/version.json',{cache:'no-store'});if(!response.ok)return;const next=await response.json();if(typeof next.version==='string')availableRelease.value=next.version!==release.version?next:null}catch{}
}
function refreshVersion(){if(modal.value){flash('Сохраните или закройте текущую форму перед обновлением.');return}location.reload()}
onMounted(()=>{checkRelease();releaseTimer=setInterval(checkRelease,60000)});
onUnmounted(()=>clearInterval(releaseTimer));
const vModalFocus={mounted(el){el._previousFocus=document.activeElement;el._trap=e=>{if(e.key!=='Tab')return;const controls=[...el.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]')].filter(n=>n.getClientRects().length);const first=controls[0],last=controls.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}};el.addEventListener('keydown',el._trap);el.querySelector('input:not(:disabled),select:not(:disabled),button:not(:disabled)')?.focus()},unmounted(el){el.removeEventListener('keydown',el._trap);el._previousFocus?.focus()}};
const user=ref(null),csrf=ref(''),login=ref({username:'',password:''}),recentLogins=ref(loadRecentLogins()),passwordInput=ref(null),error=ref(''),busy=ref(false),notice=ref('');
const loginSuggestions=computed(()=>matchingLogins(recentLogins.value,login.value.username));
const page=ref(location.hash.slice(1)||'home'),currency=ref('UZS'),days=ref(30),year=ref(new Date().getFullYear()),month=ref(new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Tashkent'}).slice(0,7));
const companyId=ref(null),companyReady=ref(false),reportEditing=ref(false),reportScenario=ref('A');
const company=computed(()=>boot.value.companies.find(c=>c.id===companyId.value));
let companyEpoch=0;const pendingCalls=new Set();
const companyUrl=url=>url+(url.includes('?')?'&':'?')+'company_id='+companyId.value;
const reportAsOf=ref(new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Tashkent'}));
const boot=ref({accounts:[],companies:[],categories:[],roles:{}}),rows=ref([]),dash=ref(null),report=ref(null),preview=ref(null),search=ref(''),modal=ref(null),form=ref({}),formError=ref(''),saving=ref(false),documents=ref(null);
const importMode=ref('operations'),planMonthFrom=ref(1),planMonthTo=ref(12),planMappings=ref({}),planReason=ref('Загрузка планов расходов из проверенной книги Cash Flow');
const allNav=[['home','◈','Обзор','view'],['report','▥','Cash Flow','export'],['accounts','▣','Банк и касса','view'],['ledger','⇄','Операции','ledger'],['requests','✓','Заявки','requests-view'],['calendar','▦','Календарь','view'],['budgets','◷','Бюджеты','view'],['import','↥','Импорт','import'],['categories','≡','Справочники','catalog'],['approval','✓','Согласование','approval_policy'],['users','♙','Пользователи','users'],['audit','⊞','Журнал','audit'],['profile','⚙','Профиль','']];
const has=p=>p==='requests-view'?(user.value?.permissions.includes('request')||user.value?.permissions.includes('view')):user.value?.permissions.includes(p), nav=computed(()=>allNav.filter(n=>!n[3]||has(n[3]))), title=computed(()=>allNav.find(n=>n[0]===page.value)?.[2]||'Казначейство');
watch(()=>[company.value?.name,title.value],([name,section])=>{document.title=name?`${name} · ${section}`:'Cash Flow · Выбор компании'},{immediate:true});
const status={pending:'На согласовании',approved:'Утверждена',paid:'Оплачена',draft:'Черновик',rejected:'Отклонена',returned:'На доработке',cancelled:'Отменена',expected:'Ожидается',received:'Получено'};
const money=v=>{if(v==null)return '—';let s=String(v);if(currency.value==='UZS')s=s.replace(/\.00$/,'');const [a,b]=s.split('.');return a.replace(/\B(?=(\d{3})+(?!\d))/g,' ')+(b!==undefined?'.'+b:'')};
const costGroups={fixed:'Постоянные затраты',variable:'Переменные затраты',other:'Прочие затраты'};
const auditRefresh=ref(0);
const menuOpen=ref(false),helpOpen=ref(false),homeAccounts=ref([]),homeRequests=ref([]),hover=ref(null);
const initials=n=>String(n||'').split(/\s+/).filter(Boolean).map(x=>x[0]).join('').slice(0,2).toUpperCase();
const showCurrency=computed(()=>!['approval','users','audit','categories','import','profile'].includes(page.value));
const navGroups=[['Работа',['home','report','accounts','ledger','requests','calendar']],['Планирование',['budgets','import','approval']],['Администрирование',['categories','users','audit']]];
const navSections=computed(()=>navGroups.map(([label,ids])=>({label,items:nav.value.filter(n=>ids.includes(n[0]))})).filter(g=>g.items.length));
async function setCurrency(c){if(currency.value===c)return;currency.value=c;await load()}
const statusClass=r=>({pending:'warn',approved:'good',paid:'fact',draft:'gray',rejected:'bad',returned:'warn',cancelled:'gray'}[r.status]||'gray');
const MK={done:'✓',now:'●',bad:'✕',skip:'–','':''};
function trkSteps(r){
 let a='done',f='',d='',dl='Директор';
 if(r.status==='draft'||r.status==='returned')a='now';
 else if(r.status==='pending'){f=r.approval_stage==='finance'?'now':'done';d=r.approval_stage==='director'?'now':'';if(r.approval_stage==='finance')dl='Директор · по порогу'}
 else if(r.status==='approved'||r.status==='paid'){f='done';const dir=r.approved_by!=null&&r.approved_by!==r.finance_approved_by;d=dir?'done':'skip';if(!dir)dl='Директор не требовался'}
 else if(r.status==='rejected')f='bad';
 else if(r.status==='cancelled')a='';
 return [{c:a,l:'Заявитель'},{c:f,l:'Финансист'},{c:d,l:dl}];
}
const MS=['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек'];
const dayOf=s=>Number(String(s).slice(8,10)),monOf=s=>MS[Number(String(s).slice(5,7))-1];
const shortDate=s=>`${dayOf(s)} ${monOf(s)}`;
const compact=v=>{const a=Math.abs(v),s=v<0?'−':'';if(a>=1e9)return s+(a/1e9).toFixed(1).replace('.0','')+' млрд';if(a>=1e6)return s+(a/1e6).toFixed(1).replace('.0','')+' млн';if(a>=1e3)return s+(a/1e3).toFixed(1).replace('.0','')+' тыс.';return s+Math.round(a)};
const chartBox={w:820,h:260,l:64,r:16,t:16,b:30};
const chart=computed(()=>{
 const f=dash.value?.forecast||[];if(!f.length||dash.value?.balance==null)return null;
 const vals=f.map(d=>Number(d.balance)),res=Number(dash.value.reserve||0);
 let hi=Math.max(...vals,res),lo=Math.min(...vals,res);if(hi===lo){hi+=1;lo-=1}
 const floor=Math.min(...vals,res)>=0,pad=(hi-lo)*.08;hi+=pad;lo=floor?Math.max(0,lo-pad):lo-pad;
 const {w,h,l,r,t,b}=chartBox,x=i=>f.length===1?l:l+i*(w-l-r)/(f.length-1),y=v=>t+(hi-v)*(h-t-b)/(hi-lo);
 const idx=[0,Math.floor((f.length-1)/2),f.length-1].filter((v,i,a)=>a.indexOf(v)===i);
 return {pts:vals.map((v,i)=>[x(i),y(v)]),path:vals.map((v,i)=>(i?'L':'M')+x(i).toFixed(1)+' '+y(v).toFixed(1)).join(''),reserveY:res?y(res):null,
  ticks:[0,1,2,3,4].map(k=>{const v=lo+(hi-lo)*k/4;return {y:y(v),label:compact(v)}}),
  labels:idx.map((i,k)=>({x:x(i),text:shortDate(f[i].date),anchor:k===0?'start':k===idx.length-1?'end':'middle'}))};
});
function chartMove(e){
 const c=chart.value;if(!c)return;
 const box=e.currentTarget.getBoundingClientRect(),px=(e.clientX-box.left)/box.width*chartBox.w;
 let best=0;c.pts.forEach((p,i)=>{if(Math.abs(p[0]-px)<Math.abs(c.pts[best][0]-px))best=i});
 hover.value={i:best,left:c.pts[best][0]/chartBox.w*box.width+8,top:c.pts[best][1]/chartBox.h*box.height+6};
}
const forecastSummary=computed(()=>{
 const f=dash.value?.forecast||[],bad=f.find(d=>d.risk);
 if(bad)return `Проверьте ликвидность: ${bad.date}`;
 if(!dash.value?.forecast_has_events)return 'Будущие события пока не зарегистрированы';
 return `Минимум ${money(Math.min(...f.map(d=>Number(d.balance))).toFixed(2))} ${currency.value} — выше резерва`;
});
const upcoming=computed(()=>{const out=[];for(const d of dash.value?.forecast||[]){for(const e of d.events||[])if(e.kind==='out')out.push({date:d.date,name:e.name,amount:e.amount,overdue:e.overdue});if(out.length>=5)break}return out.slice(0,5)});
async function loadHome(){
 const epoch=companyEpoch;
 try{homeAccounts.value=await api('/api/accounts')}catch{if(epoch===companyEpoch)homeAccounts.value=[]}
 try{const q=new URLSearchParams({paginated:'true',page:'1',currency:currency.value,q:'',state:'pending'});homeRequests.value=(await api('/api/requests?'+q)).items.slice(0,4)}catch{if(epoch===companyEpoch)homeRequests.value=[]}
}
const calTiles=computed(()=>{
 const f=forecastRows.value;if(!f.length)return null;
 const n=v=>Number(v||0);
 return {inc:f.reduce((a,d)=>a+n(d.incoming),0),out:f.reduce((a,d)=>a+n(d.outgoing),0),low:f.reduce((a,d)=>n(d.balance)<n(a.balance)?d:a,f[0]),end:f.at(-1)};
});
const budgetCounts=computed(()=>({'':rows.value.length,...Object.fromEntries(Object.keys(costGroups).map(k=>[k,rows.value.filter(r=>r.cost_group===k).length]))}));
const usage=r=>r.limit==null||Number(r.limit)===0?0:(Number(r.spent)+Number(r.reserved))/Number(r.limit);
const importStep=computed(()=>preview.value?2:1);
const helpSections=computed(()=>({
 home:[['Факт и план','Метка «Факт» — подтверждённые операции по счетам. Метка «План» — утверждённые платежи и ожидаемые поступления ближайших дней. Значения «План · 7 дней» и «Факт · месяц» относятся к разным периодам и не вычитаются друг из друга.'],['Минимальный резерв','Красная пунктирная линия на графике — резерв ликвидности. Если прогнозный остаток опускается ниже него или счёт уходит в минус, показывается предупреждение с датой.'],['Как считается прогноз',dash.value?.note||'Прогноз на конец дня: текущие остатки + ожидаемые поступления − утверждённые неоплаченные заявки.'],['Заявки на согласовании','Для каждой заявки видны этапы: заявитель → финансист → директор. Директор подключается, если сумма выше его порога для валюты заявки.']],
 report:[['Форма отчёта','Управленческая форма по прямому методу и структуре IAS 7: остаток на начало, операционная, инвестиционная и финансовая деятельность, чистое изменение, остаток на конец. Это управленческий отчёт, а не заявление о полном соответствии МСФО.'],['Режимы','«Факт» — только фактические операции. «План-факт» — для каждого месяца План, Факт и Отклонение. Отклонение = Факт − План: зелёное улучшает денежный поток, красное ухудшает.'],['Остатки не суммируются','В колонке «За период» остаток на начало берётся на начало периода, остаток на конец — на его конец. Потоки суммируются.'],['Единицы и выгрузка','Суммы можно показывать в миллионах, тысячах или точно. Excel и PDF формирует сервер по выбранным году, периоду и режиму.'],['«Не задан»','План не введён. Пока не заполнены все статьи, итог плана не определён. Введите ноль для статей без планируемых движений.'],['План платежей и бюджеты','План платежей задаётся отдельно от лимитов бюджета и не равен автоматически лимиту расходов. Виды деятельности Cash Flow не совпадают с группами затрат бюджета (постоянные, переменные, прочие).'],['Что не входит','Счета учитываются в своей валюте, внутренние переводы исключены. Ввод начальных остатков после начала года показан отдельно от денежного потока.']],
 accounts:[['Начальный остаток','Остаток на начало дня. Исправления вносятся с указанием причины и попадают в журнал. Валюта счёта с историей защищена от изменения.'],['Валюты','Каждый счёт ведётся в своей валюте. Суммы разных валют не складываются.'],['Архив','В архив можно убрать счёт с нулевым остатком и без незавершённых заявок. История сохраняется, счёт можно восстановить.']],
 ledger:[['Что вносить','Записывайте только фактически совершённые операции. Для расхода без заявки основание — от 10 символов.'],['Период','Выберите месяц или диапазон дат и нажмите «Применить». Панель раскрывается в потоке страницы и сдвигает таблицу, ничего не перекрывая. Esc закрывает панель.'],['Сторно','Ошибка исправляется сторно: оно отменяет влияние операции, история остаётся в журнале.'],['CSV','Выгрузка CSV содержит полный реестр во всех валютах.']],
 requests:[['Отображение','Если заявок до четырёх — карточки, с пятой — таблица со страницами.'],['Этапы согласования','Заявитель → финансист → директор. До порога включительно достаточно финансиста; выше порога требуется отдельное подтверждение директора. Без настроенного порога все суммы требуют директора.'],['Кто может согласовать','Автор и последний редактор не могут согласовать заявку. Возврат, перенос и отмена освобождают резерв бюджета; при переносе требуется повторное согласование.']],
 calendar:[['Сценарии',dash.value?.scenario_note||'Сценарий «Все заявки» добавляет к утверждённым оплатам заявки на согласовании.'],['Как считается прогноз',dash.value?.note||'Прогноз на конец дня: текущие остатки + ожидаемые поступления − утверждённые неоплаченные заявки.'],['Сортировка','Порядок по дате переключается кнопкой в заголовке столбца «Дата» или списком в панели.']],
 budgets:[['Лимит и резерв','«Оплачено» — фактические платежи месяца. «Резерв» — утверждённые, но ещё не оплаченные заявки. Остаток = лимит − оплачено − резерв.'],['Контроль превышения','«Разрешать превышение с обоснованием» — заявку сверх лимита можно согласовать с обоснованием. «Запрещать превышение» — сервер блокирует заявку сверх лимита.'],['Группы затрат','Группа (постоянные, переменные, прочие) относится к статье во всех месяцах и валютах. Она не связана с видами деятельности Cash Flow.']],
 import:[['Как заполнить файл','В шаблоне указывайте названия счетов и статей точно как в справочниках. Тип: Поступление, Выплата или Перевод. Дата: ГГГГ-ММ-ДД. Сумма: с точкой. Документ: уникальный номер на счёте. Для выплаты укажите основание в комментарии (от 10 символов). В Excel данные должны быть на первом листе.'],['Проверка','Файл сначала проверяется. Если есть ошибки, операции не записываются, а строки с проблемами показываются. Импорт применяет те же проверки, что и ручной ввод.']],
 categories:[['Статьи','Название, направление (поступление или выплата) и вид деятельности. Вид деятельности определяет раздел отчёта Cash Flow.']],
 approval:[['Порядок согласования','Сначала заявку проверяет финансист. Если сумма выше порога выбранной валюты, её дополнительно утверждает директор. Автор и последний редактор не могут согласовать собственную заявку.'],['Если порог не задан','Для всех сумм этой валюты требуется директор. Укажите 0, если нужен директор для каждой положительной суммы.']],
 users:[['Пороги согласования','Кассир создаёт заявку, финансист проверяет. До порога включительно достаточно финансиста; выше требуется отдельное подтверждение директора. Без настроенного порога все суммы требуют директора. Порог задаётся отдельно для UZS, USD и EUR.'],['Кто подтверждает','Автор, последний редактор и финансист не могут подтвердить за директора ту же заявку.'],['Пароли','Пароли не отображаются. При сбросе все сеансы пользователя завершаются.']],
 audit:[['Как искать','Выберите месяц или даты, сотрудника и действие, затем нажмите «Показать». Записи показываются простым списком; технические сведения — по кнопке «Подробности». Время указано по Ташкенту.']],
 profile:[['Сессия и пароль','Сессия действует 60 минут. Пароль не короче 12 символов. После смены пароля все сессии отзываются.']]
}[page.value]||[]));

const budgetGroup=ref(''),calendarOrder=ref('asc'),receiptsOpen=ref(false),receiptsLoading=ref(false);
const budgetRows=computed(()=>rows.value.filter(r=>!budgetGroup.value||r.cost_group===budgetGroup.value));
const sortedForecast=computed(()=>calendarOrder.value==='desc'?[...forecastRows.value].reverse():forecastRows.value);
const amountFields=['amount','opening','limit','spent','reserved','remaining','balance'];
const dateFrom=ref(''),dateTo=ref(''),listPage=ref(1),listTotal=ref(0),requestStatus=ref(''),showArchive=ref(false),policy=ref(null),expandedRequest=ref(null);
const visibleRows=computed(()=>['ledger','requests'].includes(page.value)?rows.value:rows.value.filter(r=>(!r.currency||r.currency===currency.value)&&JSON.stringify(r).toLowerCase().includes(search.value.toLowerCase())));
const listPages=computed(()=>Math.max(1,Math.ceil(listTotal.value/10)));
const requestStatuses=computed(()=>Object.fromEntries(Object.entries(status).filter(([key])=>!['expected','received'].includes(key))));
watch(currency,()=>{listPage.value=1},{flush:'sync'});
async function applyPeriod(v){dateFrom.value=v.from;dateTo.value=v.to;listPage.value=1;await load()}
async function changeListPage(n){listPage.value=n;expandedRequest.value=null;await load()}
let searchTimer;watch(search,()=>{if(['ledger','requests'].includes(page.value)){clearTimeout(searchTimer);searchTimer=setTimeout(()=>{listPage.value=1;load()},300)}});
onUnmounted(()=>clearTimeout(searchTimer));
function archiveAccount(a){open(a.archived?'Восстановить счёт':'Убрать счёт в архив',[field('reason','Причина','textarea',{minlength:10})],{reason:''},d=>post(`/api/accounts/${a.id}/archive`,{...d,archived:!a.archived}),'История сохраняется. В архив можно убрать счёт с нулевым остатком и без незавершённых заявок.')}
function resetPassword(u){open('Сбросить пароль · '+u.name,[field('password','Новый пароль','password',{minlength:12})],{password:''},d=>post(`/api/users/${u.id}/password`,d),'Минимум 12 символов. Все сеансы пользователя завершатся. Старый пароль не отображается.')}
function approvalPolicy(c){open('Порог согласования · '+c,[field('amount','Сумма в '+c,'text',{required:false,inputmode:'decimal'})],{amount:policy.value?.limits?.[c]??''},d=>post('/api/approval-policy',{currency:c,amount:d.amount===''?null:String(d.amount)}),'Пустое поле — все суммы требуют директора. До порога включительно согласует финансист, выше — дополнительно директор. Уже утверждённые заявки не пересматриваются.')}

let listRequestId=0,reportRequestId=0;
const stageLabel=r=>r.status==='pending'?(r.approval_stage==='director'?'Ожидает директора':'Ожидает финансиста'):status[r.status];
const otherCurrencies=computed(()=>[...new Set(boot.value.accounts.map(a=>a.currency))].filter(c=>c!==currency.value));
const currentAccounts=computed(()=>boot.value.accounts.filter(a=>a.currency===currency.value));
const modalFields=computed(()=>{const fields=modal.value?.fields||[];if(fields.some(f=>f.key==='action'))return fields.filter(f=>f.key!=='date'||form.value.action==='reschedule');if(!fields.some(f=>f.key==='to_account_id'))return fields;return fields.filter(f=>f.key==='to_account_id'?form.value.kind==='transfer':f.key==='category_id'?form.value.kind!=='transfer':true)});
const categoryHint=computed(()=>{if(!modal.value?.fields.some(f=>f.key==='to_account_id')||form.value.kind==='transfer')return '';const c=boot.value.categories.find(c=>c.id===Number(form.value.category_id));return c&&c.type!==(form.value.kind==='in'?'income':'outcome')?'Проверьте статью: её тип не совпадает с направлением операции. Если это возврат, укажите пояснение в комментарии.':''});
async function switchCurrency(value){currency.value=value;search.value='';listPage.value=1;await load()}
const subtitles={home:'Факт, план и прогноз денежных средств в одном месте.',report:'Управленческая форма по прямому методу · структура IAS 7.',accounts:'Счета и касса по валютам. Суммы разных валют не складываются.',ledger:'Фактические поступления, расходы и переводы.',requests:'Заявки на оплату и этапы согласования.',calendar:'Поступления, выплаты и прогноз остатка по дням.',budgets:'Лимиты расходов по статьям и контроль превышения.',import:'Загрузка операций из CSV или Excel.',categories:'Статьи движения денег и виды деятельности.',approval:'Пороги директора по валютам и порядок проверки платежей.',users:'Доступ, роли и пороги согласования директором.',audit:'Кто, когда и что делал в системе.',profile:'Ваши данные и пароль.'};
const priorities={normal:'Обычный',high:'Высокий',urgent:'Срочный'};
const scenario=ref('approved');
const forecastRows=computed(()=>(dash.value?.forecast||[]).map(d=>scenario.value==='all'?{...d,opening:d.requested_opening,outgoing:d.requested_outgoing,balance:d.requested_balance,risk:d.requested_risk,account_shortfalls:d.requested_account_shortfalls,events:[...d.events,...d.pending_events]}:d));
const canEditRequest=r=>has('request')&&['draft','pending','returned','rejected'].includes(r.status)&&(r.creator_id===user.value.id||has('request_edit'));
const risk=computed(()=>dash.value?.forecast.find(d=>d.risk));
const chartPoints=computed(()=>{let a=dash.value?.forecast.map(d=>Number(d.balance))||[];let lo=Math.min(...a,0),hi=Math.max(...a,1);return a.map((v,i)=>`${20+i*860/(a.length-1||1)},${190-(v-lo)*160/(hi-lo||1)}`).join(' ')});
async function api(url,opts={}){const epoch=companyEpoch,controller=new AbortController();pendingCalls.add(controller);try{const r=await fetch(url,{credentials:'same-origin',...opts,signal:controller.signal,headers:{'X-CSRF-Token':csrf.value,...(companyId.value?{'X-Company-ID':String(companyId.value)}:{}),...opts.headers}});let data;try{data=await r.json()}catch{throw Error('Некорректный ответ сервера')}if(epoch!==companyEpoch)throw new DOMException('Компания изменена','AbortError');if(!r.ok){if(r.status===401)user.value=null;throw Error(Array.isArray(data.detail)?data.detail.map(x=>`${x.loc?.slice(1).join('.')}: ${x.msg}`).join('; '):data.detail||'Ошибка запроса')}return data}finally{pendingCalls.delete(controller)}}
const post=(url,data={})=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function flash(text){notice.value=text;setTimeout(()=>notice.value='',5500)}
async function bootstrap(){boot.value=await api('/api/bootstrap');user.value=boot.value.user;csrf.value=boot.value.csrf;month.value=month.value.match(/^\d{4}-\d{2}$/)?month.value:boot.value.today.slice(0,7)}
async function enter(){companyId.value=null;companyReady.value=false;const data=await api('/api/companies');boot.value={accounts:[],categories:[],...data};user.value=data.user;csrf.value=data.csrf;}
async function selectCompany(id){
 if(saving.value||modal.value||documents.value){flash('Закройте текущую форму перед сменой компании.');return}
 companyEpoch++;for(const call of pendingCalls)call.abort();pendingCalls.clear();clearTimeout(searchTimer);
 companyReady.value=false;companyId.value=Number(id);busy.value=true;error.value='';notice.value='';
 rows.value=[];dash.value=null;report.value=null;preview.value=null;homeAccounts.value=[];homeRequests.value=[];receipts.value=[];policy.value=null;expandedRequest.value=null;
 search.value='';dateFrom.value='';dateTo.value='';requestStatus.value='';listPage.value=1;listTotal.value=0;showArchive.value=false;receiptsOpen.value=false;planMappings.value={};helpOpen.value=false;menuOpen.value=false;
 boot.value={...boot.value,accounts:[],categories:[],counterparties:[]};
 try{await bootstrap();companyReady.value=true;if(!nav.value.some(n=>n[0]===page.value))page.value=nav.value[0][0];await load()}catch(e){error.value=e.message}finally{busy.value=false}
}
function chooseCompany(){if(busy.value)return;if(modal.value||documents.value||saving.value||reportEditing.value){flash('Закройте текущую форму перед сменой компании.');return}busy.value=true;companyReady.value=false;companyId.value=null;enter().catch(e=>{if(e.name!=='AbortError')error.value=e.message}).finally(()=>busy.value=false)}
async function chooseRecentLogin(username){login.value.username=username;login.value.password='';error.value='';await nextTick();passwordInput.value?.focus()}
async function signIn(event){
 if(busy.value)return;
 // Read the submitted fields directly, including browser autofill without input events.
 const fields=new FormData(event.currentTarget);
 const credentials={username:String(fields.get('username')||'').trim(),password:String(fields.get('password')||'')};
 busy.value=true;error.value='';
 try{
  const r=await post('/api/login',credentials);
  recentLogins.value=rememberLogin(r.user.username||credentials.username,recentLogins.value);
  offerPasswordSave(credentials);
  csrf.value=r.csrf;user.value=r.user;
  // Removing the submitted form lets native password managers detect success.
  await nextTick();login.value.password='';
  await enter();
 }catch(e){error.value=e.message}finally{busy.value=false}
}
async function logout(){try{await post('/api/logout')}finally{user.value=null;csrf.value=''}}
async function go(name){menuOpen.value=false;helpOpen.value=false;reportEditing.value=false;page.value=name;location.hash=name;search.value='';dateFrom.value='';dateTo.value='';requestStatus.value='';listPage.value=1;expandedRequest.value=null;receiptsOpen.value=false;rows.value=[];await load()}
async function load(){if(!companyReady.value)return;const epoch=companyEpoch;busy.value=true;error.value='';try{
 if(['home','calendar'].includes(page.value))dash.value=await api(`/api/dashboard?currency=${currency.value}&days=${days.value}`);
 if(page.value==='home')await loadHome();
 if(page.value==='accounts')rows.value=await api('/api/accounts?archived='+showArchive.value);
 if(['ledger','requests'].includes(page.value)){const requestId=++listRequestId;const requestedPage=page.value;const params=new URLSearchParams({paginated:'true',page:String(listPage.value),currency:currency.value,q:search.value});if(dateFrom.value)params.set('date_from',dateFrom.value);if(dateTo.value)params.set('date_to',dateTo.value);if(page.value==='requests'&&requestStatus.value)params.set('state',requestStatus.value);const data=await api(`/api/${page.value}?${params}`);if(requestId===listRequestId&&page.value===requestedPage){rows.value=data.items;listTotal.value=data.total;}}
 if(page.value==='calendar'&&receiptsOpen.value)await loadReceipts();
 if(page.value==='budgets')rows.value=await api(`/api/budgets?currency=${currency.value}&month=${month.value}`);
 if(page.value==='report'){const requestId=++reportRequestId;const c=currency.value,y=year.value,co=companyId.value,sc=reportScenario.value,ao=reportAsOf.value;if(report.value&&(report.value.currency!==c||report.value.year!==Number(y)||report.value.company_id!==co||report.value.scenario!==sc||report.value.as_of!==ao))report.value=null;const result=await api(`/api/report?currency=${c}&year=${y}&company_id=${co}&scenario=${sc}&as_of=${ao}`);if(requestId===reportRequestId&&page.value==='report'&&currency.value===c&&year.value===y&&companyId.value===co&&reportScenario.value===sc&&reportAsOf.value===ao)report.value=result;}
 if(page.value==='categories')rows.value=boot.value.categories;
 if(page.value==='audit')auditRefresh.value++;
 if(page.value==='users')rows.value=await api('/api/users');
 if(['users','approval'].includes(page.value)&&has('approval_policy'))policy.value=await api('/api/approval-policy');


 }catch(e){if(epoch===companyEpoch&&e.name!=='AbortError')error.value=e.message}finally{if(epoch===companyEpoch)busy.value=false}}
const field=(key,label,type='text',extra={})=>({key,label,type,required:true,...extra});
const accounts=()=>boot.value.accounts.map(a=>[a.id,`${a.name} · ${a.currency}`]);
const categories=()=>boot.value.categories.map(c=>[c.id,`${c.name} · ${c.type==='income'?'приход':'расход'}`]);
function open(title,fields,values,submit,note=''){form.value={...values};formError.value='';modal.value={title,fields,submit,note}}
async function save(){saving.value=true;formError.value='';try{await modal.value.submit({...form.value});modal.value=null;await bootstrap();await load();flash('Сохранено')}catch(e){formError.value=e.message}finally{saving.value=false}}
function account(a=null){open(a?'Редактировать счёт':'Добавить счёт',[
 field('name','Название'),field('kind','Тип','select',{options:[['bank','Банковский счёт'],['cash','Касса']]}),field('currency','Валюта','select',{options:['UZS','USD','EUR'].map(x=>[x,x])}),field('opening','Начальный остаток','number',{min:0}),field('opening_date','Дата начала учёта','date'),field('allow_overdraft','Разрешён овердрафт','checkbox',{required:false}),...(a?[field('reason','Причина исправления','textarea',{minlength:10})]:[])],a||{name:'',company_id:companyId.value,kind:'bank',currency:currency.value,opening:'0',opening_date:boot.value.today,allow_overdraft:false},d=>post(a?`/api/accounts/${a.id}`:'/api/accounts',Object.fromEntries(['name','kind','currency','opening','opening_date','allow_overdraft',...(a?['reason']:[])].map(k=>[k,k==='opening'?String(d[k]):d[k]]))),'Остаток на начало дня. Изменения сохраняются в журнале. Валюта счёта с историей защищена от изменения.')}
function transaction(link=null,receipt=false){if(!link&&!currentAccounts.value.length){go('accounts');return}open(link?'Подтвердить факт':'Новая операция',[
 field('kind','Тип','select',{options:[['in','Поступление'],['out','Расход'],['transfer','Внутренний перевод']],disabled:!!link}),field('account_id','Счёт','select',{options:accounts(),disabled:!!link}),field('to_account_id','Счёт-получатель (для перевода)','select',{options:[[null,'—'],...accounts()],required:false}),field('category_id','Статья','select',{options:categories(),disabled:!!link}),field('amount','Сумма','number',{min:.01,disabled:!!link}),field('date','Дата факта','date'),field('reference','Номер документа'),field('counterparty','Контрагент','text',{required:false}),field('note','Основание / комментарий','textarea',{required:false})],{kind:link?(receipt?'in':'out'):'in',account_id:link?.account_id||currentAccounts.value[0]?.id,to_account_id:null,category_id:link?.category_id||boot.value.categories[0]?.id,amount:link?.amount||'',date:boot.value.today,reference:'',counterparty:link?.counterparty||'',note:link?.purpose||''},d=>post('/api/ledger',{...d,amount:String(d.amount),category_id:d.kind==='transfer'?null:Number(d.category_id),account_id:Number(d.account_id),to_account_id:d.kind==='transfer'?Number(d.to_account_id):null,request_id:link&&!receipt?link.id:null,request_version:link&&!receipt?link.version:null,receipt_id:link&&receipt?link.id:null}),'Записывайте только фактически совершённые операции. Для расхода без заявки основание — от 10 символов.')}
function requestForm(r=null){
 const fields=[field('account_id','Счёт списания','select',{options:accounts()}),field('category_id','Статья','select',{options:categories()}),field('amount','Сумма','number',{min:.01}),field('date','Плановая дата','date'),field('counterparty','Контрагент'),field('purpose','Назначение','textarea',{minlength:5}),field('priority','Приоритет','select',{options:Object.entries(priorities)}),field('status','После сохранения','select',{options:[['pending','Отправить на согласование'],['draft','Сохранить черновик']]}),...(r?[field('reason','Причина изменения','textarea',{minlength:10})]:[])];
 const values=r?{account_id:r.account_id,category_id:r.category_id,amount:r.amount,date:r.date,counterparty:r.counterparty,purpose:r.purpose,priority:r.priority,project:r.project,status:'draft',reason:''}:{account_id:currentAccounts.value[0]?.id,category_id:boot.value.categories.find(c=>c.type==='outcome')?.id,amount:'',date:boot.value.today,counterparty:'',purpose:'',priority:'normal',status:'pending'};
 open(r?'Изменить '+r.number:'Новая заявка',fields,values,d=>r?api(`/api/requests/${r.id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({...d,amount:String(d.amount),version:r.version})}):post('/api/requests',{...d,amount:String(d.amount)}),'Автор и последний редактор не могут утвердить заявку. Согласование выполняет другой пользователь.');
}
function requestActions(r){const options=[];
 if(has('approve')&&r.status==='pending'&&![r.creator_id,r.last_editor_id].includes(user.value.id)){if((r.approval_stage==='finance'&&['admin','finance'].includes(user.value.role))||(r.approval_stage==='director'&&['admin','director'].includes(user.value.role)&&r.finance_approved_by!==user.value.id))options.push(['approve',r.approval_stage==='finance'?'Подтвердить проверку':'Утвердить оплату']);options.push(['reject','Отклонить'])}
 if(has('approve')&&['pending','approved'].includes(r.status))options.push(['return','Вернуть на доработку'],['reschedule','Перенести дату']);
 if(has('request')&&(r.creator_id===user.value.id||has('request_edit'))&&['draft','pending','approved','returned','rejected'].includes(r.status))options.push(['cancel','Отменить заявку']);
 return options;
}
function decision(r){const options=requestActions(r);open('Действие · '+r.number,[field('action','Действие','select',{options}),field('date','Новая дата (при переносе)','date',{required:false}),field('note','Обоснование','textarea',{minlength:10})],{action:options[0][0],date:r.date,note:''},d=>post(`/api/requests/${r.id}/decision`,{...d,version:r.version}),'Возврат, перенос и отмена освобождают резерв бюджета. При переносе требуется повторное согласование.')}
async function submitRequest(r){try{await post(`/api/requests/${r.id}/decision`,{action:'submit',version:r.version});await load()}catch(e){error.value=e.message}}
function budget(r){open('Бюджет · '+r.category,[field('amount','Лимит','number',{min:0,required:false,placeholder:'Не задан'}),field('cost_group','Группа затрат','select',{options:Object.entries(costGroups)}),field('mode','Контроль','select',{options:[['soft','Разрешать превышение с обоснованием'],['hard','Запрещать превышение']]}),field('reason','Причина / основание','textarea',{minlength:10})],{amount:r.limit??'',mode:r.mode,cost_group:r.cost_group||'other',reason:''},d=>post('/api/budgets',{...d,amount:d.amount===''?null:String(d.amount),category_id:r.category_id,month:month.value,currency:currency.value}),'Группа относится к статье во всех месяцах и валютах. Пустой лимит не меняет ограничение бюджета.')}
function category(c=null){open(c?'Редактировать статью':'Новая статья',[field('name','Название'),field('type','Направление','select',{options:[['income','Поступление'],['outcome','Выплата']]}),field('activity','Деятельность','select',{options:[['operating','Операционная'],['investing','Инвестиционная'],['financing','Финансовая']]})],c?{name:c.name,type:c.type,activity:c.activity}:{name:'',type:'outcome',activity:'operating'},d=>c?api(`/api/categories/${c.id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}):post('/api/categories',d))}
function editUser(u=null){open(u?'Доступ · '+u.name:'Новый пользователь',[...(!u?[field('username','Логин'),field('name','Имя')]:[]),field('role','Роль','select',{options:Object.entries(boot.value.roles)}),field('password',u?'Новый пароль (необязательно)':'Пароль','password',{required:!u,minlength:12}),...(u?[field('active','Активен','checkbox',{required:false})]:[])],u?{role:u.role,active:u.active,password:''}:{username:'',name:'',role:'operator',password:''},d=>post(u?`/api/users/${u.id}`:'/api/users',d),'Новый сотрудник получает доступ только к выбранной компании. Логин, пароль, роль и отключение учётной записи действуют во всех доступных ему компаниях. Администраторы управляют всеми компаниями.')}
function linkUser(){open('Доступ к '+company.value.name,[field('username','Существующий логин')],{username:''},d=>post('/api/company-users',d),'Добавить сотрудника, у которого уже есть учётная запись. Его роль и пароль сохранятся.')}
function unlinkUser(u){open('Убрать доступ · '+u.name,[],{},()=>api(`/api/company-users/${u.id}`,{method:'DELETE'}),'Доступ к '+company.value.name+' будет отозван. Доступ к другим компаниям и история действий сохранятся.')}
function reverse(r){open('Сторно операции '+r.reference,[field('reason','Причина сторно','textarea',{minlength:10})],{reason:''},d=>post(`/api/ledger/${r.id}/reverse`,d),'Исправление отменит влияние ошибочной операции. История останется в журнале.')}
function changePassword(){open('Смена пароля',[field('old_password','Текущий пароль','password'),field('new_password','Новый пароль','password',{minlength:12})],{old_password:'',new_password:''},async d=>{await post('/api/password',d);user.value=null;location.reload()})}
function receipt(){open('Ожидаемое поступление',[field('account_id','Счёт','select',{options:accounts()}),field('category_id','Статья','select',{options:categories()}),field('amount','Сумма','number',{min:.01}),field('date','Ожидаемая дата','date'),field('counterparty','Контрагент')],{account_id:boot.value.accounts[0]?.id,category_id:boot.value.categories.find(c=>c.type==='income')?.id,amount:'',date:boot.value.today,counterparty:''},d=>post('/api/receipts',{...d,amount:String(d.amount)}))}
const receipts=ref([]);
async function loadReceipts(){receiptsLoading.value=true;try{receipts.value=(await api('/api/receipts')).filter(r=>r.currency===currency.value&&r.status==='expected')}catch(e){error.value=e.message}finally{receiptsLoading.value=false}}
async function showReceipts(){receiptsOpen.value=!receiptsOpen.value;if(receiptsOpen.value)await loadReceipts()}

async function upload(event){const file=event.target.files[0];if(!file)return;busy.value=true;error.value='';preview.value=null;try{preview.value=await api('/api/import/preview',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(file.name)},body:file})}catch(e){error.value=e.message}finally{busy.value=false;event.target.value=''}}

async function uploadPlan(event){const file=event.target.files[0];if(!file)return;busy.value=true;error.value='';preview.value=null;try{const q=new URLSearchParams({company_id:String(companyId.value),year:String(year.value),month_from:String(planMonthFrom.value),month_to:String(planMonthTo.value)});const r=await api('/api/plan-import/preview?'+q,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(file.name)},body:file});preview.value={...r,type:'plan'};const map={};for(const row of r.rows)if(!(row.source_key in map))map[row.source_key]=String(r.suggestions[row.source_key]||'new');planMappings.value=map}catch(e){error.value=e.message}finally{busy.value=false;event.target.value=''}}

async function commit(){busy.value=true;try{const r=await post(`/api/import/${preview.value.id}/commit`);preview.value=null;flash(`Импортировано операций: ${r.count}`);await bootstrap()}catch(e){error.value=e.message}finally{busy.value=false}}
async function commitPlan(){busy.value=true;error.value='';try{const r=await post(`/api/plan-import/${preview.value.id}/commit`,{mappings:planMappings.value,reason:planReason.value});preview.value=null;flash(`Обновлено плановых значений: ${r.updated}`);await bootstrap()}catch(e){error.value=e.message}finally{busy.value=false}}
async function docs(r){try{documents.value={ledger:r,rows:await api(`/api/documents?ledger_id=${r.id}`)}}catch(e){error.value=e.message}}
async function attach(event){const file=event.target.files[0];if(!file)return;saving.value=true;try{await api(`/api/ledger/${documents.value.ledger.id}/document`,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(file.name)},body:file});await docs(documents.value.ledger)}catch(e){error.value=e.message}finally{saving.value=false;event.target.value=''}}
async function reserve(){open('Минимальный резерв',[field('amount','Сумма резерва','number',{min:0})],{amount:dash.value?.reserve||'0'},d=>post('/api/reserve',{amount:String(d.amount),currency:currency.value}))}
window.addEventListener('hashchange',()=>{const target=location.hash.slice(1);if(user.value&&companyReady.value&&target!==page.value&&nav.value.some(n=>n[0]===target)){page.value=target;load()}});
onMounted(async()=>{try{const r=await api('/api/me');user.value=r.user;csrf.value=r.csrf;await enter()}catch{user.value=null}});
</script>

<template>
 <div v-if="availableRelease" class="release-notice" role="status"><span>Доступна версия {{availableRelease.version}}. Сохраните текущие изменения перед обновлением.</span><button class="secondary tiny" @click="refreshVersion">Обновить страницу</button></div>
 <div v-if="showRelease" class="modal-backdrop" @click.self="showRelease=false" @keydown.esc="showRelease=false"><section class="modal release-dialog" role="dialog" aria-modal="true" aria-labelledby="release-title" v-modal-focus><div class="section-head"><h2 id="release-title">Что нового · {{release.version}}</h2><button class="ghost icon-btn" aria-label="Закрыть список изменений" @click="showRelease=false"><AppIcon name="x"/></button></div><p class="sub">{{release.date}}</p><ul><li v-for="change in release.changes" :key="change">{{change}}</li></ul></section></div>

 <!-- Вход -->
 <div v-if="!user" class="login-layout">
  <section class="login-art"><div class="brand"><span class="mark">U</span><span class="bname">UZGERMED</span></div><div><h1>Деньги под контролем.<br><em>Решения — вовремя.</em></h1><p>Счета, платежи и бюджеты.<br>Одно пространство для финансовой команды.</p><div class="login-lines"><span>БАНК</span><span>КАССА</span><span>CASH FLOW</span><svg viewBox="0 0 500 130" aria-hidden="true"><polyline points="0,120 60,100 110,108 170,62 220,80 280,40 340,55 400,10 450,28 500,4" fill="none" stroke="#8adbc5" stroke-width="3"/></svg></div></div><small>Закрытая корпоративная система</small></section>
  <form id="login-form" class="login-form" method="post" action="/api/login" autocomplete="on" @submit.prevent="signIn">
   <p class="eyebrow">UZGERMED TREASURY</p><h2>Добро пожаловать</h2><p>Войдите в финансовое пространство компании.</p>
   <label for="username">Логин
    <input id="username" v-model="login.username" name="username" type="text" list="recent-logins" autocomplete="username" autocapitalize="none" :spellcheck="false" aria-describedby="login-hint" placeholder="Введите логин" required :readonly="busy">
    <datalist id="recent-logins"><option v-for="name in recentLogins" :key="name" :value="name" /></datalist>
   </label>
   <div v-if="loginSuggestions.length" class="recent-logins" role="group" aria-label="Последние логины">
    <span>Последние логины</span>
    <button v-for="name in loginSuggestions" :key="name" type="button" class="recent-login" :disabled="busy" @click="chooseRecentLogin(name)">{{name}}</button>
   </div>
   <small id="login-hint" class="login-hint">Здесь появятся 3 последних успешных логина из этого браузера.</small>
   <label for="current-password">Пароль<input id="current-password" ref="passwordInput" v-model="login.password" name="password" type="password" autocomplete="current-password" aria-describedby="password-hint" placeholder="Введите пароль" required :readonly="busy"></label>
   <p v-if="error" class="error" role="alert">{{error}}</p>
   <button type="submit" :disabled="busy">{{busy?'Входим…':'Войти в систему'}} <AppIcon name="arrow"/></button>
   <small id="password-hint">Для автозаполнения пароля подтвердите «Сохранить» в предложении браузера. Доступность зависит от браузера и его настроек.</small>
  </form>
 </div>

 <!-- Рабочее пространство -->
 <section v-else-if="!companyReady" class="company-choice">
  <header><div class="brand"><span class="mark">CF</span><span class="bname">Cash Flow</span></div><button class="secondary" @click="logout">Выйти</button></header>
  <main><p class="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО</p><h1>Выберите компанию</h1><p class="sub">Счета, операции, заявки и отчёты выбранной компании.</p>
   <p v-if="error" class="error" role="alert">{{error}}</p><p v-if="busy" role="status">Открываем компанию…</p>
   <div class="company-grid"><button v-for="c in boot.companies" :key="c.id" class="company-card" :class="{'unassigned':c.code==='UNASSIGNED'}" :disabled="busy" @click="selectCompany(c.id)"><span class="company-symbol">{{c.code==='UNASSIGNED'?'?':c.code.charAt(0)}}</span><span><b>{{c.code==='ZUMA'?'ZUMA':c.name}}</b><small>{{c.code==='UNASSIGNED'?'Записи без указанной компании':'Открыть рабочее пространство'}}</small></span><AppIcon name="arrow"/></button></div>
   <p v-if="!busy&&!boot.companies.length" class="sub">Нет доступных компаний. Обратитесь к администратору.</p>
  </main>
 </section>
 <div v-else class="app" :class="{'menu-open':menuOpen}">
  <div v-if="menuOpen" class="scrim" @click="menuOpen=false"></div>
  <aside class="side" aria-label="Основное меню">
   <a class="brand" href="#home" @click.prevent="go('home')"><span class="mark">{{company?.code.charAt(0)}}</span><span class="bname">{{company?.code==='ZUMA'?'ZUMA':company?.name}}</span></a>
   <button class="company-switch secondary" :disabled="busy||saving" @click="chooseCompany"><AppIcon name="accounts"/> Сменить компанию</button>
   <nav aria-label="Разделы">
    <template v-for="g in navSections" :key="g.label">
     <p class="nav-l">{{g.label}}</p>
     <button v-for="n in g.items" :key="n[0]" class="nav-i" :class="{on:page===n[0]}" :aria-current="page===n[0]?'page':undefined" @click="go(n[0])"><AppIcon :name="n[0]"/><span>{{n[2]}}</span><span v-if="n[0]==='report'" class="tag">Главный</span></button>
    </template>
   </nav>
   <div class="side-foot">
    <button class="nav-i" :class="{on:page==='profile'}" :aria-current="page==='profile'?'page':undefined" @click="go('profile')"><span class="ava">{{initials(user.name)}}</span><span class="who"><b>{{user.name}}</b><small>{{user.role_label}}</small></span></button>
    <button class="ghost logout" @click="logout">Выйти</button>
   </div>
  </aside>

  <div class="workspace">
   <header class="topbar">
    <button class="icon-btn burger" aria-label="Открыть меню" @click="menuOpen=true"><AppIcon name="menu"/></button>
    <nav class="crumb" aria-label="Путь"><button class="ghost tiny" :disabled="busy||saving" @click="chooseCompany">{{company?.name}}</button><i>/</i><b>{{title}}</b></nav>
    <div class="topbar-r"><button class="icon-btn" aria-label="Справка" @click="helpOpen=true"><AppIcon name="help"/></button></div>
   </header>
   <main class="page">
    <div class="pg-head">
     <div><h1>{{title}}</h1><p v-if="subtitles[page]" class="sub">{{subtitles[page]}}</p></div>
     <div class="pg-act">
      <div v-if="showCurrency" class="seg" role="group" aria-label="Валюта учёта"><button v-for="c in ['UZS','USD','EUR']" :key="c" :class="{on:currency===c}" :aria-pressed="currency===c" @click="setCurrency(c)">{{c}}</button></div>
      <button v-if="page!=='profile'" class="icon-btn" aria-label="Обновить данные" title="Обновить данные" @click="load"><AppIcon name="refresh"/></button>
      <button class="secondary" @click="helpOpen=true"><AppIcon name="help"/> Справка</button>
     </div>
    </div>
    <p v-if="notice" class="notice" role="status">{{notice}}</p>
    <p v-if="error" class="error" role="alert">{{error}}</p>
    <div v-if="busy" class="loading">Загрузка данных…</div>

    <!-- Обзор -->
    <template v-if="page==='home'&&dash">
     <section v-if="risk" class="warning"><b>Проверьте ликвидность на {{risk.date}}</b><p v-if="risk.reserve_risk">Общий остаток ниже резерва: {{money(risk.balance)}} {{currency}}</p><p v-for="a in risk.account_shortfalls" :key="a.account_id">{{a.account}}: {{money(a.balance)}} {{currency}}</p></section>
     <div class="gA">
      <section class="card hero s5">
       <div class="lbl"><AppIcon name="accounts"/> Фактический остаток · {{dash.account_count}} {{dash.account_count===1?'счёт':'счетов'}}</div>
       <div class="big num"><template v-if="dash.balance===null">Счета не настроены</template><template v-else>{{money(dash.balance)}}<small>{{currency}}</small></template></div>
       <span class="dl">на {{dash.as_of}}</span>
       <div class="hero-foot"><span>Минимальный резерв: <b>{{money(dash.reserve)}} {{currency}}</b></span><button v-if="has('budget')" class="ghost tiny" @click="reserve">Изменить</button></div>
      </section>
      <section class="card kpi s3">
       <div class="card-b"><div class="k">На согласовании</div><div class="v num">{{dash.pending_count}}<small>{{dash.pending_count===1?'заявка':'заявок'}}</small></div><p class="sub">на сумму <b class="num">{{money(dash.pending_amount)}}</b> {{currency}}</p><button v-if="has('request')" class="secondary" @click="go('requests')">Открыть заявки <AppIcon name="arrow"/></button></div>
      </section>
      <section class="card s4">
       <div class="card-h"><h3>Поступления и выплаты</h3></div>
       <div class="card-b pf">
        <div class="pf-h" style="margin-top:0">Поступления</div>
        <div class="pf-row"><span class="pill plan">План · 7 дней</span><b class="num">{{money(dash.incoming7)}}</b></div>
        <div class="pf-row"><span class="pill fact">Факт · месяц</span><b class="num">{{money(dash.fact_in)}}</b></div>
        <div class="pf-h">Выплаты</div>
        <div class="pf-row"><span class="pill plan">План · 7 дней</span><b class="num">{{money(dash.outgoing7)}}</b></div>
        <div class="pf-row"><span class="pill fact">Факт · месяц</span><b class="num">{{money(dash.fact_out)}}</b></div>
       </div>
      </section>

      <section v-if="!dash.account_count" class="card s12">
       <div class="card-h"><div><h3>С чего начать</h3><p class="sub" style="margin-top:2px">Три шага, чтобы прогноз и отчёты заработали</p></div></div>
       <div class="card-b"><ol class="steps-list">
        <li><span class="n">1</span><div><b>Добавьте счёт или кассу</b><small>Укажите валюту и подтверждённый остаток на начало учёта.</small><button v-if="has('write')" class="tiny" style="margin-top:8px" @click="go('accounts')">К счетам</button></div></li>
        <li><span class="n">2</span><div><b>Задайте минимальный резерв</b><small>Ниже этого остатка прогноз покажет предупреждение.</small></div></li>
        <li><span class="n">3</span><div><b>Загрузите операции или создайте заявки</b><small>Банковскую выписку можно загрузить в разделе «Импорт».</small></div></li>
       </ol></div>
      </section>

      <section v-else class="card s8">
       <div class="card-h"><div><h3>Прогноз остатка</h3><p class="sub" style="margin-top:2px">{{forecastSummary}}</p></div><div class="seg" role="group" aria-label="Горизонт прогноза"><button v-for="d in [7,30,90]" :key="d" :class="{on:days===d}" :aria-pressed="days===d" @click="days=d;load()">{{d}} дн.</button></div></div>
       <div v-if="chart" class="chart-box" @pointerleave="hover=null">
        <svg :viewBox="`0 0 ${chartBox.w} ${chartBox.h}`" role="img" aria-label="Прогноз денежных средств" @pointermove="chartMove">
         <g v-for="t in chart.ticks" :key="t.y"><line :x1="chartBox.l" :x2="chartBox.w-chartBox.r" :y1="t.y" :y2="t.y" stroke="#e4ece9"/><text :x="chartBox.l-8" :y="t.y+4" text-anchor="end" font-size="11" fill="#84978f" font-weight="700">{{t.label}}</text></g>
         <line v-if="chart.reserveY!=null" :x1="chartBox.l" :x2="chartBox.w-chartBox.r" :y1="chart.reserveY" :y2="chart.reserveY" stroke="#b23a32" stroke-width="1.6" stroke-dasharray="2 5" stroke-linecap="round"/>
         <text v-if="chart.reserveY!=null" :x="chartBox.w-chartBox.r" :y="chart.reserveY-6" text-anchor="end" font-size="11" fill="#b23a32" font-weight="800">Мин. резерв {{compact(Number(dash.reserve))}}</text>
         <path :d="chart.path" fill="none" stroke="#3a6ea8" stroke-width="2.6" stroke-dasharray="7 6" stroke-linejoin="round" stroke-linecap="round"/>
         <circle :cx="chart.pts[0][0]" :cy="chart.pts[0][1]" r="5.5" fill="#086a56" stroke="#fff" stroke-width="2"/>
         <text v-for="l in chart.labels" :key="l.x" :x="l.x" :y="chartBox.h-8" :text-anchor="l.anchor" font-size="11" fill="#56695f" font-weight="700">{{l.text}}</text>
         <g v-if="hover"><line :x1="chart.pts[hover.i][0]" :x2="chart.pts[hover.i][0]" :y1="chartBox.t" :y2="chartBox.h-chartBox.b" stroke="#10231e" stroke-opacity=".35"/><circle :cx="chart.pts[hover.i][0]" :cy="chart.pts[hover.i][1]" r="5" fill="#fff" stroke="#10231e" stroke-width="2.5"/></g>
        </svg>
        <div v-if="hover" class="tip" :style="{left:hover.left+'px',top:hover.top+'px'}">{{dash.forecast[hover.i].date}}<br><em>{{hover.i===0?'Сегодня · факт':'План (прогноз)'}}</em> · {{money(dash.forecast[hover.i].balance)}} {{currency}}</div>
       </div>
       <div class="legend"><span><i></i>Факт на сегодня</span><span><i class="pl"></i>План / прогноз</span><span><i class="rs"></i>Минимальный резерв</span></div>
      </section>

      <section class="card s4">
       <div class="card-h"><h3>Где находятся деньги</h3><span class="pill fact">Факт</span></div>
       <div class="card-b"><template v-if="homeAccounts.length"><div v-for="a in homeAccounts.filter(x=>x.currency===currency)" :key="a.id" class="list-row"><span class="ic"><AppIcon :name="a.kind==='bank'?'accounts':'cash'"/></span><div><b>{{a.name}}</b><small>{{a.kind==='bank'?'Банковский счёт':'Касса'}}</small></div><div class="amt num">{{money(a.balance)}}<small>{{a.currency}}</small></div></div><p v-if="!homeAccounts.some(x=>x.currency===currency)" class="empty" style="padding:18px 0">Нет счетов в {{currency}}</p></template><p v-else class="empty" style="padding:18px 0">Счета пока не добавлены.</p></div>
      </section>
      <section class="card s7">
       <div class="card-h"><h3>Заявки на согласовании</h3><button v-if="has('request')" class="secondary tiny" @click="go('requests')">Все заявки <AppIcon name="arrow"/></button></div>
       <div class="card-b"><template v-if="homeRequests.length"><div v-for="r in homeRequests" :key="r.id" class="list-row" style="display:block"><div style="display:flex;justify-content:space-between;gap:12px"><div><b>{{r.number}} · {{r.counterparty}}</b><small>{{r.purpose}} · до {{r.date}}</small></div><div class="amt num" style="font-weight:800;white-space:nowrap">{{money(r.amount)}}<small>{{r.currency}}</small></div></div><div class="trk"><template v-for="(s,i) in trkSteps(r)" :key="i"><span v-if="i" class="ln"></span><span class="st" :class="s.c==='skip'?'':s.c"><u>{{MK[s.c]}}</u>{{s.l}}</span></template></div></div></template><p v-else class="empty" style="padding:18px 0">Заявок на согласовании нет.</p></div>
      </section>
      <section class="card s5">
       <div class="card-h"><h3>Ближайшие выплаты</h3><span class="pill plan">План</span></div>
       <div class="card-b"><template v-if="upcoming.length"><div v-for="(p,i) in upcoming" :key="i" class="list-row"><div class="dt"><div>{{dayOf(p.date)}}<small>{{monOf(p.date)}}</small></div></div><div><b>{{p.name}}</b><small>Утверждено{{p.overdue?' · просрочено':''}}</small></div><div class="amt num">{{money(p.amount)}}<small>{{currency}}</small></div></div></template><p v-else class="empty" style="padding:18px 0">Нет утверждённых выплат на выбранный период.</p></div>
      </section>
     </div>
    </template>

    <!-- Банк и касса -->
    <template v-if="page==='accounts'">
     <div class="tb"><div class="seg" role="group" aria-label="Показать счета"><button :class="{on:!showArchive}" @click="showArchive=false;load()">Активные</button><button :class="{on:showArchive}" @click="showArchive=true;load()">Архив</button></div><span class="sub"><b>{{visibleRows.length}}</b> · {{currency}}</span><div class="grow"><button v-if="has('write')" @click="account()"><AppIcon name="plus"/> Добавить счёт</button></div></div>
     <div class="acc-grid"><section v-for="a in visibleRows" :key="a.id" class="card ac-card" :class="{arch:a.archived}">
      <div class="ac-top"><span class="ic"><AppIcon :name="a.kind==='bank'?'accounts':'cash'"/></span><div><b>{{a.name}}</b><small>{{a.kind==='bank'?'Банковский счёт':'Касса'}}{{a.allow_overdraft?' · овердрафт разрешён':''}}</small></div><span class="pill" :class="a.archived?'warn':'fact'">{{a.archived?'Архив · ':''}}{{a.currency}}</span></div>
      <div class="ac-l">Текущий остаток</div><div class="ac-v num">{{money(a.balance)}}<small>{{a.currency}}</small></div>
      <div class="ac-o"><span>Начальный остаток на {{a.opening_date}}</span><b class="num">{{money(a.opening)}} {{a.currency}}</b></div>
      <div class="ac-b"><button v-if="has('write')&&!a.archived" class="secondary tiny" @click="account(a)"><AppIcon name="edit"/> Изменить</button><button v-if="has('users')" class="secondary tiny" @click="archiveAccount(a)"><AppIcon :name="a.archived?'restore':'archive'"/> {{a.archived?'Восстановить':'В архив'}}</button></div>
     </section></div>
     <section v-if="!visibleRows.length&&!busy&&!showArchive" class="empty-state card"><div class="empty-icon" aria-hidden="true"><AppIcon name="accounts"/></div><h2>Добавьте первый счёт в {{currency}}</h2><p>Укажите банк или кассу и начальный остаток. Дальше баланс будет рассчитываться по операциям.</p><div class="actions"><button v-if="has('write')" @click="account()"><AppIcon name="plus"/> Добавить счёт</button><button v-for="c in otherCurrencies" :key="c" class="secondary" @click="switchCurrency(c)">Показать {{c}}</button></div></section>
     <section v-if="!visibleRows.length&&!busy&&showArchive" class="empty card"><b>В архиве пусто</b><br>Архивные счета можно восстановить в любой момент.</section>
    </template>

    <!-- Операции -->
    <template v-if="page==='ledger'">
     <PeriodFilter :key="page" :date-from="dateFrom" :date-to="dateTo" @apply="applyPeriod"><label class="search"><AppIcon name="search"/><input v-model="search" placeholder="Поиск: контрагент, статья, документ" aria-label="Поиск операции"></label><div class="grow actions"><a v-if="has('export')" class="button secondary" :href="companyUrl('/api/export/ledger.csv')"><AppIcon name="download"/> CSV</a><button v-if="has('write')&&user.role!=='cashier'" @click="transaction()"><AppIcon name="plus"/> Новая операция</button></div></PeriodFilter>
     <div v-if="visibleRows.length" class="table-card rsp-wrap"><table class="rsp"><thead><tr><th>Дата / документ</th><th>Операция</th><th>Контрагент / статья</th><th>Счёт</th><th class="num">Сумма</th><th>Документы</th><th></th></tr></thead><tbody>
      <tr v-for="r in visibleRows" :key="r.id"><td data-l="Дата">{{r.date}}<small>{{r.reference}}</small></td><td data-l="Операция"><span class="pill" :class="{in:'good',out:'bad',transfer:'plan'}[r.kind]">{{{in:'Поступление',out:'Расход',transfer:'Перевод'}[r.kind]}}</span><small v-if="r.reversed||r.reversal_of">Сторно</small></td><td data-l="Контрагент">{{r.counterparty||'Между своими счетами'}}<small>{{r.category}}</small></td><td data-l="Счёт">{{r.account}}</td><td class="num" data-l="Сумма"><b :class="{pos:r.kind==='in'}">{{r.kind==='in'?'+':r.kind==='out'?'−':''}}{{money(r.amount)}}</b> {{r.currency}}</td><td data-l="Документы"><button class="secondary tiny" @click="docs(r)"><AppIcon name="file"/> Документы</button></td><td data-l=""><button v-if="has('budget')&&!r.reversed&&!r.reversal_of" class="ghost tiny" @click="reverse(r)">Сторно</button></td></tr>
     </tbody></table></div>
     <section v-else-if="!busy" class="empty-state card"><div class="empty-icon" aria-hidden="true"><AppIcon name="ledger"/></div><h2>{{search?'Ничего не найдено':'Пока нет операций в '+currency}}</h2><p>{{search?'Попробуйте другой запрос или сбросьте поиск.':currentAccounts.length?'Добавьте поступление, расход или перевод между счетами.':'Сначала добавьте счёт в этой валюте и укажите начальный остаток.'}}</p><div class="actions"><button v-if="search" class="secondary" @click="search=''">Сбросить поиск</button><button v-else-if="has('write')" @click="currentAccounts.length?transaction():go('accounts')">{{currentAccounts.length?'Новая операция':'Перейти к счетам'}}</button><button v-if="!search" v-for="c in otherCurrencies" :key="c" class="secondary" @click="switchCurrency(c)">Показать {{c}}</button></div></section>
    </template>

    <!-- Заявки -->
    <template v-if="page==='requests'">
     <PeriodFilter :key="page" :date-from="dateFrom" :date-to="dateTo" @apply="applyPeriod"><select aria-label="Статус заявки" v-model="requestStatus" @change="listPage=1;load()"><option value="">Статус: все</option><option v-for="(label,key) in requestStatuses" :key="key" :value="key">{{label}}</option></select><label class="search"><AppIcon name="search"/><input v-model="search" placeholder="Поиск заявки" aria-label="Поиск заявки"></label><div class="grow actions"><button v-if="has('request')" @click="requestForm()"><AppIcon name="plus"/> Создать заявку</button></div></PeriodFilter>
     <p class="sumline"><span>Найдено <b>{{listTotal}}</b></span><span>{{listTotal<=4?'До 4 заявок — карточки':'С 5 заявок — таблица со страницами'}}</span></p>
     <div v-if="listTotal<=4&&visibleRows.length" class="rq-grid"><section v-for="r in visibleRows" :key="r.id" class="card rq-card">
      <div class="rq-top"><span>{{r.number}}</span><span class="pill" :class="statusClass(r)">{{stageLabel(r)}}</span></div>
      <h3>{{r.counterparty}}</h3><p class="cp">{{r.purpose}}</p>
      <div class="amt num">{{money(r.amount)}}<small>{{r.currency}}</small></div>
      <div class="meta"><span>до {{r.date}}</span><span>{{r.account}}</span><span>{{priorities[r.priority]}}</span></div>
      <div class="trk"><template v-for="(s,i) in trkSteps(r)" :key="i"><span v-if="i" class="ln"></span><span class="st" :class="s.c==='skip'?'':s.c"><u>{{MK[s.c]}}</u>{{s.l}}</span></template></div>
      <p v-if="r.decision_note" class="note">{{r.decision_note}}</p><p v-if="r.budget.limit!=null" class="note">Доступно по бюджету: {{money(r.budget.remaining)}} {{r.currency}}</p>
      <div class="actions"><button v-if="canEditRequest(r)" class="secondary tiny" @click="requestForm(r)">Изменить</button><button v-if="['draft','returned'].includes(r.status)&&canEditRequest(r)" class="tiny" @click="submitRequest(r)">Отправить</button><button v-if="requestActions(r).length" class="secondary tiny" @click="decision(r)">Действия</button><button v-if="r.status==='approved'&&has('write')" class="tiny" @click="transaction(r)">Факт оплаты</button></div>
     </section></div>
     <div v-if="listTotal>4" class="table-card rsp-wrap"><table class="rsp"><thead><tr><th>Заявка</th><th class="num">Сумма</th><th>Оплатить до</th><th>Этапы</th><th>Статус</th><th></th></tr></thead><tbody><template v-for="r in visibleRows" :key="r.id">
      <tr><td data-l="Заявка"><b>{{r.number}}</b> · {{r.counterparty}}<small>{{r.account}}</small></td><td class="num" data-l="Сумма"><b>{{money(r.amount)}}</b> {{r.currency}}</td><td data-l="Оплатить до">{{r.date}}</td><td data-l="Этапы"><span class="mini"><template v-for="(s,i) in trkSteps(r)" :key="i"><i v-if="i"></i><u :class="s.c" :title="s.l">{{['З','Ф','Д'][i]}}</u></template></span></td><td data-l="Статус"><span class="pill" :class="statusClass(r)">{{stageLabel(r)}}</span></td><td data-l=""><button class="secondary tiny" :aria-expanded="expandedRequest===r.id" @click="expandedRequest=expandedRequest===r.id?null:r.id">Подробнее</button></td></tr>
      <tr v-if="expandedRequest===r.id" class="detail-row"><td colspan="6" class="detail-cell"><p>{{r.purpose}}</p><p class="sub">{{r.category}} · {{priorities[r.priority]}}</p><div class="trk"><template v-for="(s,i) in trkSteps(r)" :key="i"><span v-if="i" class="ln"></span><span class="st" :class="s.c==='skip'?'':s.c"><u>{{MK[s.c]}}</u>{{s.l}}</span></template></div><p v-if="r.decision_note" class="sub" style="margin-top:8px">{{r.decision_note}}</p><div class="actions" style="margin-top:10px"><button v-if="canEditRequest(r)" class="secondary tiny" @click="requestForm(r)">Изменить</button><button v-if="['draft','returned'].includes(r.status)&&canEditRequest(r)" class="tiny" @click="submitRequest(r)">Отправить</button><button v-if="requestActions(r).length" class="secondary tiny" @click="decision(r)">Действия</button><button v-if="r.status==='approved'&&has('write')" class="tiny" @click="transaction(r)">Факт оплаты</button></div></td></tr>
     </template></tbody></table></div>
     <section v-if="!visibleRows.length&&!busy" class="empty card"><b>Заявок не найдено</b><br>Измените период, статус или поисковый запрос.</section>
    </template>
    <div v-if="['ledger','requests'].includes(page)&&listPages>1" class="pagination"><span>Найдено: {{listTotal}} · Страница {{listPage}} из {{listPages}}</span><div class="actions"><button class="secondary tiny" :disabled="listPage<=1||busy" @click="changeListPage(listPage-1)">← Назад</button><button class="secondary tiny" :disabled="listPage>=listPages||busy" @click="changeListPage(listPage+1)">Далее →</button></div></div>

    <!-- Календарь -->
    <template v-if="page==='calendar'&&dash">
     <div class="tb"><select v-model="scenario" aria-label="Сценарий"><option value="approved">Сценарий: утверждённые оплаты</option><option value="all">Сценарий: с заявками на согласовании</option></select><div class="seg" role="group" aria-label="Период"><button v-for="d in [7,30,90]" :key="d" :class="{on:days===d}" :aria-pressed="days===d" @click="days=d;load()">{{d}} дн.</button></div><select v-model="calendarOrder" aria-label="Сортировка по дате"><option value="asc">Сначала ближайшие</option><option value="desc">Сначала поздние</option></select><div class="grow actions"><button class="secondary" :aria-expanded="receiptsOpen" @click="showReceipts">Ожидаемые поступления</button><button v-if="has('schedule')" @click="receipt"><AppIcon name="plus"/> План поступления</button></div></div>
     <div v-if="calTiles" class="sumrow">
      <div class="card tile"><div class="k">Поступления</div><div class="v num" :class="{pos:calTiles.inc>0}">{{calTiles.inc>0?'+':''}}{{money(calTiles.inc.toFixed(2))}}<small>{{currency}}</small></div><div class="d">за выбранный период</div></div>
      <div class="card tile"><div class="k">Платежи</div><div class="v num">{{calTiles.out>0?'−':''}}{{money(calTiles.out.toFixed(2))}}<small>{{currency}}</small></div><div class="d">{{scenario==='all'?'с заявками на согласовании':'утверждённые'}}</div></div>
      <div class="card tile"><div class="k">Минимальный остаток</div><div class="v num" :class="{neg:calTiles.low.risk}">{{money(calTiles.low.balance)}}<small>{{currency}}</small></div><div class="d">{{calTiles.low.date}} · резерв {{money(dash.reserve)}}</div></div>
      <div class="card tile"><div class="k">Остаток на конец периода</div><div class="v num">{{money(calTiles.end.balance)}}<small>{{currency}}</small></div><div class="d">{{calTiles.end.date}}</div></div>
     </div>
     <section v-if="receiptsOpen" class="card" style="margin-bottom:16px"><div class="card-h"><h3>Ожидаемые поступления · {{currency}}</h3><span class="pill plan">План</span></div><div class="card-b"><p v-if="receiptsLoading" role="status">Загрузка…</p><div v-else-if="!receipts.length" class="empty" style="padding:22px 10px"><b style="display:block;color:var(--ink);font-size:16px;margin-bottom:4px">Ожидаемых поступлений нет</b>Добавьте план поступления, чтобы он попал в прогноз.<div v-if="has('schedule')" style="margin-top:14px"><button @click="receipt"><AppIcon name="plus"/> План поступления</button></div></div><div v-for="r in receipts" :key="r.id" class="receipt-row"><span>{{r.counterparty}}<small>{{r.date}}</small></span><strong class="num">{{money(r.amount)}} {{r.currency}}</strong><button v-if="has('write')" class="tiny" @click="transaction(r,true)">Подтвердить приход</button></div></div></section>
     <div class="table-card rsp-wrap"><table class="rsp"><thead><tr><th><button class="ghost tiny" style="font-weight:800;color:var(--em)" @click="calendarOrder=calendarOrder==='asc'?'desc':'asc'" aria-label="Сменить порядок по дате">Дата {{calendarOrder==='asc'?'↑':'↓'}}</button></th><th>События</th><th class="num">На начало дня</th><th class="num">Поступления</th><th class="num">Платежи</th><th class="num">Остаток</th></tr></thead><tbody><tr v-for="d in sortedForecast" :key="d.date" :class="{risk:d.risk}"><td data-l="Дата">{{d.date}}</td><td data-l="События"><div v-for="(e,i) in d.events" :key="i">{{e.kind==='in'?'↓':e.kind==='pending'?'◌':'↑'}} {{e.name}} {{e.kind==='pending'?'· на согласовании':''}} {{e.priority&&e.priority!=='normal'?'· '+priorities[e.priority]:''}} {{e.overdue?'· просрочка':''}}</div><span v-if="!d.events.length" class="muted">Событий нет</span></td><td class="num" data-l="На начало дня">{{money(d.opening)}}</td><td class="num pos" data-l="Поступления">{{Number(d.incoming)?'+'+money(d.incoming):'—'}}</td><td class="num" data-l="Платежи">{{Number(d.outgoing)?'−'+money(d.outgoing):'—'}}</td><td class="num" data-l="Остаток"><b :class="{neg:d.risk}">{{money(d.balance)}}</b><small v-for="a in d.account_shortfalls" :key="a.account_id">{{a.account}}: {{money(a.balance)}}</small><span v-if="d.risk" class="pill warn" style="margin-left:6px">ниже резерва</span></td></tr></tbody></table></div>
    </template>

    <!-- Бюджеты -->
    <template v-if="page==='budgets'">
     <div class="tb"><label class="fld">Месяц <input v-model="month" type="month" @change="load" aria-label="Месяц бюджета"></label><div class="chips" role="group" aria-label="Группа затрат"><button class="chip" :class="{on:budgetGroup===''}" @click="budgetGroup=''">Все · {{budgetCounts['']}}</button><button v-for="(name,key) in costGroups" :key="key" class="chip" :class="{on:budgetGroup===key}" @click="budgetGroup=key">{{name}} · {{budgetCounts[key]}}</button></div></div>
     <div v-if="budgetRows.length" class="table-card rsp-wrap"><table class="rsp"><thead><tr><th>Статья</th><th class="num">Лимит</th><th class="num">Оплачено</th><th class="num">Резерв</th><th class="num">Остаток</th><th>Использовано</th><th>Контроль</th><th></th></tr></thead><tbody><tr v-for="r in budgetRows" :key="r.category_id"><td data-l="Статья"><b>{{r.category}}</b><small>{{costGroups[r.cost_group]}}</small></td><td class="num" data-l="Лимит">{{r.limit==null?'Не задан':money(r.limit)}}</td><td class="num" data-l="Оплачено">{{money(r.spent)}}</td><td class="num" data-l="Резерв">{{Number(r.reserved)?money(r.reserved):'—'}}</td><td class="num" data-l="Остаток"><b :class="Number(r.remaining)<0?'neg':'pos'">{{r.limit==null?'—':money(r.remaining)}}</b><span v-if="r.limit!=null&&Number(r.remaining)<0" class="pill bad" style="margin-left:6px">превышение</span></td><td data-l="Использовано"><div v-if="r.limit!=null" class="ub" :title="Math.round(usage(r)*100)+'%'"><i :class="usage(r)>1?'b':usage(r)>.85?'w':''" :style="{width:Math.min(100,usage(r)*100)+'%'}"></i></div><span v-else class="muted">—</span></td><td data-l="Контроль" style="white-space:normal;max-width:210px;font-size:12.5px;font-weight:700">{{r.limit==null?'Лимит не задан':r.mode==='hard'?'Запрещать превышение':'Разрешать превышение с обоснованием'}}</td><td data-l=""><button v-if="has('budget')" class="secondary tiny" @click="budget(r)"><AppIcon name="edit"/> Изменить</button></td></tr></tbody></table></div>
     <section v-else-if="!busy" class="empty card"><b>Статей не найдено</b><br>В выбранной группе нет статей.</section>
    </template>

    <!-- Импорт -->
    <template v-if="page==='import'">
     <div class="seg" role="group" aria-label="Тип импорта" style="margin-bottom:14px"><button :class="{on:importMode==='operations'}" @click="importMode='operations';preview=null">Фактические операции</button><button :class="{on:importMode==='plans'}" @click="importMode='plans';preview=null">Планы расходов А / Б / В</button></div>
     <ol class="steps" aria-label="Шаги импорта"><li class="step" :class="importStep>1?'done':'on'"><u>{{importStep>1?'✓':'1'}}</u>Файл</li><li class="step" :class="importStep===2?'on':''"><u>2</u>Проверка</li><li class="step"><u>3</u>Подтверждение</li></ol>
     <section v-if="!preview&&importMode==='operations'" class="drop"><div class="ic"><AppIcon name="import"/></div><b>Загрузите CSV или Excel</b><span class="sub">До 5 МБ и 1000 фактических операций. Плановые суммы сюда не загружаются.</span><div class="actions" style="justify-content:center;margin-top:16px"><a class="button secondary" :href="companyUrl('/api/import/template.csv')"><AppIcon name="download"/> Шаблон CSV</a><label class="button"><AppIcon name="file"/> Выбрать файл<input type="file" accept=".csv,.xlsx" @change="upload" :disabled="busy" hidden></label></div></section>
     <section v-if="!preview&&importMode==='plans'" class="card"><div class="card-h"><div><h3>Книга Cash Flow · планы расходов</h3><p class="sub">Читается лист «план на год». Заголовки А, Б, В определяются по тексту, пустые ячейки не стирают текущий план.</p></div><span class="pill plan">Только UZS</span></div><div class="card-b"><div class="filter-bar"><span class="pill good">{{company?.name}}</span><label>Год<input v-model="year" type="number" min="2000" max="2100"></label><label>С месяца<select v-model="planMonthFrom"><option v-for="n in 12" :key="n" :value="n">{{MS[n-1]}}</option></select></label><label>По месяц<select v-model="planMonthTo"><option v-for="n in 12" :key="n" :value="n">{{MS[n-1]}}</option></select></label><label class="button"><AppIcon name="file"/> Проверить XLSX<input type="file" accept=".xlsx" @change="uploadPlan" :disabled="busy" hidden></label></div><p class="sub">Предпросмотр ничего не записывает. До подтверждения можно сопоставить каждую статью или пропустить её.</p></div></section>
     <section v-else-if="preview&&preview.type!=='plan'" class="card"><div class="card-h"><div><h3>Проверено строк: {{preview.count}}</h3><p class="sub" style="margin-top:2px">{{preview.errors.length?'Есть ошибки — исправьте файл и загрузите его снова':'Ошибок не найдено, можно подтверждать'}}</p></div><span class="pill" :class="preview.errors.length?'bad':'good'">{{preview.errors.length?'Ошибок: '+preview.errors.length:'Готово к импорту'}}</span></div><div class="card-b"><p v-for="(e,i) in preview.errors" :key="i" class="error">Строка {{e.line}}: {{e.error}}</p><div class="table-scroll"><table><thead><tr><th>Дата</th><th>Документ</th><th>Контрагент</th><th class="num">Сумма</th></tr></thead><tbody><tr v-for="(r,i) in preview.rows" :key="i"><td>{{r.date}}</td><td>{{r.reference}}</td><td>{{r.counterparty}}</td><td class="num">{{money(r.amount)}}</td></tr></tbody></table></div><div class="form-actions"><button class="secondary" @click="preview=null">Загрузить другой файл</button><button v-if="preview.can_commit" @click="commit" :disabled="busy">Подтвердить импорт {{preview.rows.length}} операций</button></div><p v-if="!preview.can_commit" class="sub">Операции не записаны.</p></div></section>
     <section v-else-if="preview?.type==='plan'" class="card"><div class="card-h"><div><h3>Проверено строк планов: {{preview.rows.length}}</h3><p class="sub">Месяцы: {{preview.months.join(', ')}}. Это предпросмотр — фактические операции не создаются.</p></div><span class="pill" :class="preview.issues.length?'bad':'good'">{{preview.issues.length?'Замечаний: '+preview.issues.length:'Готово к подтверждению'}}</span></div><div class="card-b"><p v-for="(e,i) in preview.issues" :key="i" class="error">Строка {{e.line}}<template v-if="e.scenario"> · {{e.scenario}}</template>: {{e.error}}</p><div class="table-scroll"><table><thead><tr><th>Месяц</th><th>Код / статья</th><th class="num">А</th><th class="num">Б</th><th class="num">В</th><th>Изменение</th><th>Сопоставление</th></tr></thead><tbody><tr v-for="r in preview.rows" :key="r.month+r.source_key"><td>{{MS[r.month-1]}}</td><td><b>{{r.source_code}}</b><small>{{r.source_name}}</small></td><td class="num">{{r.amounts.A==null?'пусто':money((r.amounts.A/100).toFixed(2))}}</td><td class="num">{{r.amounts.B==null?'пусто':money((r.amounts.B/100).toFixed(2))}}</td><td class="num">{{r.amounts.V==null?'пусто':money((r.amounts.V/100).toFixed(2))}}</td><td><small>А: {{r.effects.A==='keep'?'без изменения':r.effects.A==='same'?'совпадает':r.effects.A==='change'?'обновится':'новое'}}<br>Б: {{r.effects.B==='keep'?'без изменения':r.effects.B==='same'?'совпадает':r.effects.B==='change'?'обновится':'новое'}}<br>В: {{r.effects.V==='keep'?'без изменения':r.effects.V==='same'?'совпадает':r.effects.V==='change'?'обновится':'новое'}}</small></td><td><select v-model="planMappings[r.source_key]"><option value="new">Создать статью</option><option value="skip">Пропустить</option><option v-for="c in preview.categories" :key="c.id" :value="String(c.id)">{{c.name}}</option></select></td></tr></tbody></table></div><label class="plan-reason">Основание импорта<textarea v-model="planReason" minlength="10" maxlength="1000"></textarea></label><div class="form-actions"><button class="secondary" @click="preview=null">Загрузить другой файл</button><button v-if="preview.can_commit" @click="commitPlan" :disabled="busy||planReason.length<10">Подтвердить планы</button></div><p class="sub">Явный ноль обновляет план до нуля. Пустая ячейка оставляет прежнее значение без изменения.</p></div></section>
     <section class="card future-module"><h3>Финансовая модель</h3><span class="pill gray">Позже</span></section>
    </template>

    <!-- Cash Flow -->
    <CashFlowReport :key="companyId" v-if="page==='report'&&report" :report="report" :currency="currency" :year="year" :api="api" :can-plan="has('plan')" @editing="reportEditing=$event" :companies="boot.companies.filter(c=>c.id===companyId)" :company-id="companyId" :scenario="reportScenario" :as-of="reportAsOf" @company="selectCompany" @scenario="v=>{reportScenario=v;load()}" @year="v=>{year=v;reportAsOf=`${v}-12-31`;load()}" @as-of="v=>{reportAsOf=v;load()}" @refresh="load" />

    <!-- Справочники -->
    <template v-if="page==='categories'">
     <div class="tb"><span class="sub">Статьи движения денег: <b>{{rows.length}}</b></span><div class="grow"><button @click="category()"><AppIcon name="plus"/> Добавить статью</button></div></div>
     <div class="table-card rsp-wrap"><table class="rsp"><thead><tr><th>Название статьи</th><th>Направление</th><th>Вид деятельности</th><th></th></tr></thead><tbody><tr v-for="r in rows" :key="r.id"><td data-l="Статья"><b>{{r.name}}</b></td><td data-l="Направление"><span class="pill" :class="r.type==='income'?'good':'plan'">{{r.type==='income'?'Поступление':'Выплата'}}</span></td><td data-l="Вид деятельности">{{{operating:'Операционная',investing:'Инвестиционная',financing:'Финансовая'}[r.activity]}}</td><td data-l=""><button class="secondary tiny" @click="category(r)"><AppIcon name="edit"/> Изменить</button></td></tr></tbody></table></div>
    </template>

    <!-- Пользователи -->
    <template v-if="['users','approval'].includes(page)&&has('approval_policy')">
     <div class="approval-grid"><div v-for="c in ['UZS','USD','EUR']" :key="c" class="card thr"><div class="k"><span>Порог директора · {{c}}</span><span class="pill" :class="policy?.limits?.[c]!=null?'good':'warn'">{{policy?.limits?.[c]!=null?'Задан':'Не настроен'}}</span></div><div class="v num">{{policy?.limits?.[c]!=null?money(policy.limits[c]):'—'}}<small v-if="policy?.limits?.[c]!=null">{{c}}</small></div><div class="d">{{policy?.limits?.[c]!=null?'До порога включительно — финансист, выше — дополнительно директор':'Все суммы требуют директора'}}</div><button class="secondary tiny" @click="approvalPolicy(c)"><AppIcon name="edit"/> Настроить {{c}}</button></div></div>
    </template>
    <template v-if="page==='users'&&has('users')">
     <div class="tb"><h3>Сотрудники · {{company?.name}}</h3><div class="grow actions"><button class="secondary" @click="linkUser()">Добавить по логину</button><button @click="editUser()"><AppIcon name="plus"/> Пользователь</button></div></div>
     <div class="table-card rsp-wrap"><table class="rsp"><thead><tr><th>Имя</th><th>Логин</th><th>Роль</th><th>Статус</th><th></th></tr></thead><tbody><tr v-for="r in rows" :key="r.id"><td data-l="Имя"><b>{{r.name}}</b></td><td data-l="Логин">{{r.username}}</td><td data-l="Роль">{{r.role_label}}</td><td data-l="Статус"><span class="pill" :class="r.active?'good':'gray'">{{r.active?'Активен':'Отключён'}}</span></td><td data-l=""><div class="actions"><button class="secondary tiny" @click="editUser(r)"><AppIcon name="edit"/> Управлять</button><button class="secondary tiny" @click="resetPassword(r)"><AppIcon name="key"/> Сбросить пароль</button><button v-if="r.role!=='admin'" class="ghost tiny" @click="unlinkUser(r)">Убрать доступ</button></div></td></tr></tbody></table></div>
    </template>

    <!-- Журнал -->
    <AuditHistory :key="companyId" v-if="page==='audit'" :api="api" :refresh="auditRefresh" />

    <!-- Профиль -->
    <div v-if="page==='profile'" class="prof">
     <section class="card"><div class="card-b"><div class="who-l"><span class="ava">{{initials(user.name)}}</span><div><b style="font-size:17px">{{user.name}}</b><small>{{user.username}}</small></div></div><div class="kv"><div><small>Имя</small><b>{{user.name}}</b></div><div><small>Логин</small><b>{{user.username}}</b></div><div><small>Роль</small><b>{{user.role_label}}</b></div><div><small>Сессия</small><b>60 минут</b></div></div><div class="actions"><button class="secondary" @click="logout">Выйти из системы</button><button class="ghost" @click="showRelease=true">Версия {{release.version}} · Что нового</button></div></div></section>
     <section class="card"><div class="card-h"><h3>Пароль</h3></div><div class="card-b"><p class="sub" style="margin-bottom:14px">Пароль не короче 12 символов. После смены пароля все сессии отзываются.</p><button @click="changePassword"><AppIcon name="lock"/> Изменить пароль</button></div></section>
    </div>
   </main>
  </div>
 </div>

 <!-- Справка -->
 <template v-if="user&&helpOpen">
  <div class="scrim" @click="helpOpen=false"></div>
  <section class="drawer" role="dialog" aria-modal="true" aria-labelledby="help-title" v-modal-focus @keydown.esc="helpOpen=false"><header><h2 id="help-title">Справка · {{title}}</h2><button class="icon-btn" aria-label="Закрыть справку" @click="helpOpen=false"><AppIcon name="x"/></button></header><div class="bd"><template v-for="s in helpSections" :key="s[0]"><h4>{{s[0]}}</h4><p>{{s[1]}}</p></template></div><div class="ft"><button class="ghost tiny" @click="helpOpen=false;showRelease=true">Версия {{release.version}} · Что нового</button></div></section>
 </template>

 <!-- Формы -->
 <div v-if="modal" class="modal-backdrop"><form v-modal-focus class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title" @submit.prevent="save" @keydown.esc="!saving&&(modal=null)"><div class="section-head"><h2 id="modal-title">{{modal.title}}</h2><button type="button" class="ghost icon-btn" aria-label="Закрыть" @click="modal=null" :disabled="saving"><AppIcon name="x"/></button></div><p v-if="modal.note" class="form-note">{{modal.note}}</p><div class="form-grid"><label v-for="f in modalFields" :key="f.key" :class="{wide:f.type==='textarea'}">{{f.label}}<select v-if="f.type==='select'" v-model="form[f.key]" :required="f.required" :disabled="f.disabled"><option v-for="o in f.options" :key="o[0]" :value="o[0]">{{o[1]}}</option></select><textarea v-else-if="f.type==='textarea'" v-model="form[f.key]" :required="f.required" :minlength="f.minlength" maxlength="3000"></textarea><input v-else v-model="form[f.key]" :type="amountFields.includes(f.key)?'text':f.type" :inputmode="amountFields.includes(f.key)?'decimal':undefined" :pattern="amountFields.includes(f.key)?'[0-9]+([.][0-9]{1,2})?':undefined" :required="f.required" :disabled="f.disabled" :min="f.min" :max="f.max" :minlength="f.minlength" :step="f.step||'0.01'"></label></div><p v-if="categoryHint" class="warning category-hint" role="status">{{categoryHint}}</p><p v-if="formError" class="error">{{formError}}</p><div class="form-actions"><button type="button" class="secondary" @click="modal=null" :disabled="saving">Отмена</button><button :disabled="saving">{{saving?'Сохраняем…':modal.saveLabel||'Сохранить'}}</button></div></form></div>
 <div v-if="documents" class="modal-backdrop"><section class="modal"><div class="section-head"><h2>Документы · {{documents.ledger.reference}}</h2><button class="ghost icon-btn" aria-label="Закрыть" @click="documents=null"><AppIcon name="x"/></button></div><p v-for="d in documents.rows" :key="d.url"><a :href="companyUrl(d.url)">{{d.filename}} ↓</a></p><p v-if="!documents.rows.length" class="sub">Документов пока нет.</p><label v-if="has('write')" class="button" style="margin-top:12px">Прикрепить PDF / изображение<input type="file" accept=".pdf,.png,.jpg,.jpeg" @change="attach" :disabled="saving" hidden></label><p class="sub" style="margin-top:12px">До 5 МБ. Документы доступны только авторизованным пользователям с правом просмотра реестра.</p></section></div>
</template>
