<script setup>
import {ref,shallowRef,computed,onMounted,onUnmounted,nextTick,watch} from 'vue';
import CashFlowReport from './CashFlowReport.vue';
import ReportArchive from './ReportArchive.vue';
import BusinessProjects from './BusinessProjects.vue';
import AuditHistory from './AuditHistory.vue';
import PeriodFilter from './PeriodFilter.vue';
import AppIcon from './AppIcon.vue';
import LanguageSwitch from './LanguageSwitch.vue';
import {tx,N_,lang,formatDate} from './i18n/index.js';
import OperationProgress from './OperationProgress.vue';
import {createOperationProgress} from './operationProgress.js';
import {invalidFormFields,focusInvalidField} from './formFeedback.js';
import {isAppFileLink,fetchDownload} from './fileDownload.js';
import CompanyCard from './CompanyCard.vue';
import OverviewPage from './overview/OverviewPage.vue';
import AdminCenter from './admin/AdminCenter.vue';
import {groupCompanyInfo} from './admin/groupRegistration.js';
import {loadRecentLogins,rememberLogin,matchingLogins,offerPasswordSave} from './loginPreferences.js';
import {canPayRequest as canPayFor} from './overview/rights.js';
import {reportDateForYear,validReportDate} from './cashflow/planView.js';
import {toCents,formatAmount,amountTitle} from './overview/format.js';
import RequestEditor from './requests/RequestEditor.vue';
import RequestCard from './requests/RequestCard.vue';
import PolicyPage from './requests/PolicyPage.vue';
import Delegations from './requests/Delegations.vue';
import {steps as requestSteps,stageLabel as requestStage,canDecide as canDecideRequest} from './requests/workflow.js';
import release from '../../release.json';
const availableRelease=ref(null),showRelease=ref(false);
let releaseTimer;
async function checkRelease(){
 if(document.hidden)return;
 try{const response=await fetch('/version.json',{cache:'no-store'});if(!response.ok)return;const next=await response.json();if(typeof next.version==='string')availableRelease.value=next.version!==release.version?next:null}catch{}
}
function refreshVersion(){if(modal.value||editor.value||cardId.value){flash(N_('Сохраните или закройте текущую форму перед обновлением.'));return}location.reload()}
onMounted(()=>{checkRelease();releaseTimer=setInterval(checkRelease,60000)});
onUnmounted(()=>clearInterval(releaseTimer));
const vModalFocus={mounted(el){el._previousFocus=document.activeElement;el._trap=e=>{if(e.key!=='Tab')return;const controls=[...el.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]')].filter(n=>n.getClientRects().length);const first=controls[0],last=controls.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}};el.addEventListener('keydown',el._trap);el.querySelector('input:not(:disabled),select:not(:disabled),button:not(:disabled)')?.focus()},unmounted(el){el.removeEventListener('keydown',el._trap);el._previousFocus?.focus()}};
const user=ref(null),csrf=ref(''),login=ref({username:'',password:''}),recentLogins=ref(loadRecentLogins()),passwordInput=ref(null),error=ref(''),busy=ref(false),notice=ref(''),noticeParams=ref(null);
const loginSuggestions=computed(()=>matchingLogins(recentLogins.value,login.value.username));
const page=ref(location.hash.slice(1)||'home'),currency=ref('UZS'),days=ref(30),year=ref(new Date().getFullYear()),month=ref(new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Tashkent'}).slice(0,7));
const companyId=ref(null),companyReady=ref(false),reportEditing=ref(false),reportScenario=ref('A');
const company=computed(()=>boot.value.companies.find(c=>c.id===companyId.value));
let companyEpoch=0;const pendingCalls=new Set();
const globalProgress=ref({active:false,label:'',completed:0,total:0,error:''});
let progressTimer;
const requestProgress=createOperationProgress(value=>{
 clearTimeout(progressTimer);globalProgress.value=value;
 if(!value.active&&value.completed>0&&!value.error)progressTimer=setTimeout(()=>requestProgress.reset(),1800);
});
const validationFields=shallowRef([]);let feedbackForm=null;
function clearFieldFeedback(){for(const {control} of validationFields.value){control.classList?.remove('form-field-invalid');control.removeAttribute?.('aria-invalid');control.removeAttribute?.('aria-errormessage')}validationFields.value=[];feedbackForm=null}
function updateFieldFeedback(form){
 const fields=invalidFormFields(form);clearFieldFeedback();feedbackForm=fields.length?form:null;validationFields.value=fields;
 for(const {control} of fields){control.classList?.add('form-field-invalid');control.setAttribute?.('aria-invalid','true');control.setAttribute?.('aria-errormessage','form-validation-feedback')}
}
function showInvalidFields(event){if(!event.target?.form)return;event.preventDefault();updateFieldFeedback(event.target.form)}
function updateInvalidFields(event){if(feedbackForm===event.target?.form)updateFieldFeedback(feedbackForm)}
// Field names and messages are read from the rendered form, so they are rebuilt after a language switch.
watch(lang,()=>{if(feedbackForm)nextTick(()=>{if(feedbackForm)updateFieldFeedback(feedbackForm)})});
function cancelPending(){companyEpoch++;for(const call of pendingCalls)call.abort();pendingCalls.clear();requestProgress.reset();clearFieldFeedback()}
const localProgressUrl=url=>/^\/api\/(?:business-projects|report-archives)(?:\/|\?|$)/.test(url);
function requestLabel(url,method='GET'){
 if(url==='/api/login')return N_('Входим в систему…');
 if(/\/import|\/plan-import/.test(url))return /\/commit/.test(url)?N_('Сохраняем импорт…'):N_('Читаем и проверяем файл…');
 if(method==='DELETE')return N_('Удаляем запись…');
 if(method!=='GET')return N_('Сохраняем изменения…');
 return N_('Загружаем данные…');
}
async function downloadFile(event){
 if(event.defaultPrevented||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
 const link=event.target?.closest?.('a[href]');if(!link||link.hasAttribute('data-browser-download')||!isAppFileLink(link.href,location.origin))return;
 event.preventDefault();
 const epoch=companyEpoch,controller=new AbortController(),ticket=requestProgress.begin(N_('Запрашиваем файл…'),3);pendingCalls.add(controller);
 try{
  const result=await fetchDownload(link.href,{signal:controller.signal,headers:companyId.value?{'X-Company-ID':String(companyId.value)}:{},
   onResponse:()=>requestProgress.advance(ticket,1,N_('Получаем файл…')),onBody:()=>requestProgress.advance(ticket,1,N_('Передаём файл браузеру…'))});
  if(epoch!==companyEpoch)throw new DOMException(N_('Компания изменена'),'AbortError');
  const blobUrl=URL.createObjectURL(result.blob),anchor=document.createElement('a');anchor.href=blobUrl;anchor.download=result.filename;anchor.dataset.browserDownload='true';anchor.hidden=true;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(blobUrl),60000);
  requestProgress.complete(ticket,N_('Файл передан браузеру'));
 }catch(e){if(epoch===companyEpoch)requestProgress.fail(ticket,e.name==='AbortError'?N_('Скачивание отменено'):e)}
 finally{pendingCalls.delete(controller)}
}
onMounted(()=>{document.addEventListener('invalid',showInvalidFields,true);document.addEventListener('input',updateInvalidFields,true);document.addEventListener('change',updateInvalidFields,true);document.addEventListener('click',downloadFile)});
onUnmounted(()=>{document.removeEventListener('invalid',showInvalidFields,true);document.removeEventListener('input',updateInvalidFields,true);document.removeEventListener('change',updateInvalidFields,true);document.removeEventListener('click',downloadFile);clearTimeout(progressTimer);cancelPending()});
const companyUrl=url=>url+(url.includes('?')?'&':'?')+'company_id='+companyId.value;
const reportAsOf=ref(new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Tashkent'}));
const boot=ref({accounts:[],companies:[],categories:[],roles:{}}),rows=ref([]),dash=ref(null),report=ref(null),preview=ref(null),search=ref(''),modal=ref(null),form=ref({}),formError=ref(''),saving=ref(false),documents=ref(null);
const selectableCompanies=computed(()=>boot.value.companies.filter(c=>c.code!=='UNASSIGNED'));
const importMode=ref('operations'),planMonthFrom=ref(1),planMonthTo=ref(12),planMappings=ref({}),planReason=ref('');
// The default import reason follows the interface language until the user edits it.
const defaultPlanReason=()=>tx('Загрузка планов расходов из проверенной книги Cash Flow');
let planReasonDefault=defaultPlanReason();planReason.value=planReasonDefault;
watch(lang,()=>{const next=defaultPlanReason();if(planReason.value===planReasonDefault)planReason.value=next;planReasonDefault=next});
const allNav=[['home','◈',N_('Обзор'),'view'],['report','▥','Cash Flow','export'],['business','▤',N_('Бизнес-планы'),'export'],['reports','▤',N_('Архив отчётов'),'export'],['accounts','▣',N_('Банк и касса'),'view'],['ledger','⇄',N_('Операции'),'ledger'],['requests','✓',N_('Заявки'),'requests-view'],['calendar','▦',N_('Календарь'),'view'],['budgets','◷',N_('Бюджеты'),'view'],['import','↥',N_('Импорт'),'import'],['categories','≡',N_('Справочники'),'catalog'],['approval','✓',N_('Согласование'),'approval_policy'],['users','♙',N_('Пользователи'),'users'],['audit','⊞',N_('Журнал'),'audit'],['profile','⚙',N_('Профиль'),'']];
const has=p=>p==='requests-view'?['request','view','pay_bank','pay_cash','request_check'].some(x=>user.value?.permissions.includes(x)):user.value?.permissions.includes(p), nav=computed(()=>allNav.filter(n=>!n[3]||has(n[3]))), title=computed(()=>tx(allNav.find(n=>n[0]===page.value)?.[2]||N_('Казначейство')));
watch(()=>[company.value?.name,title.value,lang.value],([name,section])=>{document.title=name?`${name} · ${section}`:tx('Cash Flow · Выбор компании')},{immediate:true});
const status={pending:N_('На согласовании'),approved:N_('Утверждена'),paid:N_('Оплачена'),draft:N_('Черновик'),rejected:N_('Отклонена'),returned:N_('На доработке'),cancelled:N_('Закрыта без оплаты'),expected:N_('Ожидается'),received:N_('Получено')};
const money=v=>{if(v==null)return '—';let s=String(v);if(currency.value==='UZS')s=s.replace(/\.00$/,'');const [a,b]=s.split('.');return a.replace(/\B(?=(\d{3})+(?!\d))/g,' ')+(b!==undefined?'.'+b:'')};
// Headline balances: "5,32 млрд"; the exact figure goes into the tooltip (moneyTitle).
const moneyShort=(v,cur=currency.value)=>formatAmount(toCents(v),cur), moneyTitle=(v,cur=currency.value)=>amountTitle(toCents(v),cur);
const costGroups={fixed:N_('Постоянные затраты'),variable:N_('Переменные затраты'),other:N_('Прочие затраты')};
const auditRefresh=ref(0),homeRefresh=ref(0),reportArchiveRefresh=ref(0),businessRefresh=ref(0);
const menuOpen=ref(false),helpOpen=ref(false);
const initials=n=>String(n||'').split(/\s+/).filter(Boolean).map(x=>x[0]).join('').slice(0,2).toUpperCase();
const showCurrency=computed(()=>!['approval','users','audit','categories','import','profile','reports','business'].includes(page.value));
// «Отчёты» is a root menu item; Cash Flow, business plans and the archive are its tabs.
const reportPages=['report','business','reports'];
const navGroups=[[N_('Работа'),['home','accounts','ledger','requests','calendar']],[N_('Отчёты'),reportPages,'tree'],[N_('Планирование'),['budgets','import','approval']],[N_('Администрирование'),['categories','users','audit']]];
const navSections=computed(()=>navGroups.map(([label,ids,kind])=>({label,tree:kind==='tree',items:nav.value.filter(n=>ids.includes(n[0]))})).filter(g=>g.items.length));
const reportTabs=computed(()=>nav.value.filter(n=>reportPages.includes(n[0])));
const isReportPage=computed(()=>reportPages.includes(page.value));
const reportsOpen=ref(true);
function openReports(){if(isReportPage.value){reportsOpen.value=!reportsOpen.value;return}reportsOpen.value=true;if(reportTabs.value.length)go(reportTabs.value[0][0])}
async function setCurrency(c){if(currency.value===c)return;currency.value=c;await load()}
const statusClass=r=>({pending:'warn',approved:'good',paid:'fact',draft:'gray',rejected:'bad',returned:'warn',cancelled:'gray'}[r.status]||'gray');
const MK={done:'✓',now:'●',bad:'✕',skip:'–','':''};
// Этапы заявки: заявитель → проверка → фин. директор → директор по политике статьи → оплата.
function trkSteps(r){return requestSteps(r)}
const PLAN_SHEET='план на год'; // i18n-ignore: the sheet name app/plan_import.py matches in the workbook; quoted verbatim, never translated
const MS=[N_('янв'),N_('фев'),N_('мар'),N_('апр'),N_('май'),N_('июн'),N_('июл'),N_('авг'),N_('сен'),N_('окт'),N_('ноя'),N_('дек')];
const calTiles=computed(()=>{
 const f=forecastRows.value;if(!f.length)return null;
 const n=v=>Number(v||0);
 return {inc:f.reduce((a,d)=>a+n(d.incoming),0),out:f.reduce((a,d)=>a+n(d.outgoing),0),low:f.reduce((a,d)=>n(d.balance)<n(a.balance)?d:a,f[0]),end:f.at(-1)};
});
const budgetCounts=computed(()=>({'':rows.value.length,...Object.fromEntries(Object.keys(costGroups).map(k=>[k,rows.value.filter(r=>r.cost_group===k).length]))}));
const usage=r=>r.limit==null||Number(r.limit)===0?0:(Number(r.spent)+Number(r.reserved))/Number(r.limit);
const importStep=computed(()=>preview.value?2:1);
const helpSections=computed(()=>({
 business:[[tx('Как получить файл'),tx('Нажмите «Новый проект», заполните данные и на шаге «3. Отчёт» нажмите «Рассчитать бизнес-план». Готовые «Бизнес-план · PDF» и «Бизнес-план · Excel» появляются прямо в списке проектов и в «Архиве отчётов».')],[tx('Пустые поля'),tx('Под пустым полем написано, что в него вносить, а в самом поле показан пример. Пример не сохраняется. Ноль указывайте явно: пустое поле не равно нулю.')],[tx('Черновик'),tx('Черновик можно сохранить с неполными данными и вернуться к нему позже. Обязательные поля перечислены над формой; нажатие на название открывает нужное поле.')],[tx('Папка проекта'),tx('Создайте проект и выберите папку исходных документов. После загрузки сайт извлечёт поддерживаемые данные и создаст бизнес-план и ТЭО в PDF и Excel. Недостающие поля и противоречия уточняются в форме проекта.')],[tx('Загрузка'),tx('Пользователь с правом импорта добавляет исходники до 20 МБ каждый, 100 файлов и 100 МБ на папку. В Telegram появляются готовые пары; исходные документы доступны только автору проекта.')]],
 reports:[[tx('Версии отчётов'),tx('Готовые PDF и Excel сохраняются парой с названием и периодом. Новая загрузка исходников требует нового расчёта; прежние файлы остаются в истории. Также можно загрузить уже подготовленную пару.')],[tx('Период и дата загрузки'),tx('Календарь архива ищет отчёты, чей период пересекается с выбранными датами. Для расчёта другого прогноза задайте начало и горизонт в проекте. Дата загрузки выбирает версии, добавленные в этот день по времени Ташкента.')],[tx('Загрузка готового отчёта'),tx('PDF и Excel одного отчёта загружаются парой, до 20 МБ на файл. Готовая пара сразу доступна на сайте и в Telegram-боте; удалённый отчёт можно восстановить через «Показать удалённые».')]],
 home:[[tx('Факт и план'),tx('«Факт» — подтверждённые операции и остатки на счетах: доступный остаток, график за 30 дней, доходы и расходы месяца, блок «Где находятся деньги». «План» — утверждённые платежи и ожидаемые поступления: прогноз остатка, ближайшие выплаты, план на 7 дней. Периоды разные, поэтому факт и план не вычитаются друг из друга.')],[tx('Доступный остаток'),tx('Остаток на счетах минус утверждённые и ещё не оплаченные заявки со сроком по сегодня. Процент — изменение к тому же дню прошлого месяца. График показывает остаток на счетах на конец каждого из последних 30 дней: наведите курсор или выберите день стрелками, чтобы увидеть приход и расход.')],[tx('Прогноз и минимальный резерв'),tx('«Прогноз остатка» строится на 7, 30 или 90 дней; горизонт общий с разделом «Календарь». Красная пунктирная линия — минимальный резерв; он указан внизу блока «Риски», изменить его может роль с правом на бюджеты. Если прогнозный остаток опускается ниже резерва или счёт уходит в минус, в «Рисках» появляется предупреждение с датой.')],[tx('Как считается прогноз'),tx('Прогноз на конец дня: текущие остатки + ожидаемые поступления − утверждённые неоплаченные заявки. Проверяется также нехватка на каждом счёте; переводы между счетами автоматически не предполагаются. Отрицательный остаток требует проверки даже при разрешённом овердрафте: его лимит не задан. Просрочка отнесена на сегодня. Порядок платежей внутри дня не учитывается. Модель и бюджеты повторно не добавляются.')],[tx('Заявки на согласовании'),tx('Согласующий видит блок «Ждут вашего решения» — заявки выбранной валюты, которые можно решить сейчас, и общую сумму на согласовании. Остальные роли видят сводку и ближайшие заявки. У каждой заявки показаны этапы: заявитель → проверка бухгалтера → финансовый директор → директор по политике статьи.')]],
 report:[[tx('Форма отчёта'),tx('Управленческая форма по прямому методу и структуре IAS 7: остаток на начало, операционная, инвестиционная и финансовая деятельность, чистое изменение, остаток на конец. Это управленческий отчёт, а не заявление о полном соответствии МСФО.')],[tx('Режимы'),tx('«Факт» — только фактические операции. «План-факт» — для каждого месяца План, Факт и Отклонение. Отклонение = Факт − План: зелёное улучшает денежный поток, красное ухудшает.')],[tx('Остатки не суммируются'),tx('В колонке «За период» остаток на начало берётся на начало периода, остаток на конец — на его конец. Потоки суммируются.')],[tx('Единицы и выгрузка'),tx('Суммы можно показывать в миллионах, тысячах или точно. Excel и PDF формирует сервер по выбранным году, периоду и режиму.')],[tx('«Не задан»'),tx('План не введён. Пока не заполнены все статьи, итог плана не определён. Введите ноль для статей без планируемых движений.')],[tx('План платежей и бюджеты'),tx('План платежей задаётся отдельно от лимитов бюджета и не равен автоматически лимиту расходов. Виды деятельности Cash Flow не совпадают с группами затрат бюджета (постоянные, переменные, прочие).')],[tx('Что не входит'),tx('Счета учитываются в своей валюте, внутренние переводы исключены. Ввод начальных остатков после начала года показан отдельно от денежного потока.')]],
 accounts:[[tx('Начальный остаток'),tx('Остаток на начало дня. Исправления вносятся с указанием причины и попадают в журнал. Валюта счёта с историей защищена от изменения.')],[tx('Валюты'),tx('Каждый счёт ведётся в своей валюте. Суммы разных валют не складываются.')],[tx('Архив'),tx('В архив можно убрать счёт с нулевым остатком и без незавершённых заявок. История сохраняется, счёт можно восстановить.')]],
 ledger:[[tx('Что вносить'),tx('Записывайте только фактически совершённые операции. Для расхода без заявки основание — от 10 символов.')],[tx('Период'),tx('Выберите месяц или диапазон дат и нажмите «Применить». Панель раскрывается в потоке страницы и сдвигает таблицу, ничего не перекрывая. Esc закрывает панель.')],[tx('Сторно'),tx('Ошибка исправляется сторно: оно отменяет влияние операции, история остаётся в журнале.')],['CSV',tx('Выгрузка CSV содержит полный реестр во всех валютах.')]],
 requests:[[tx('Отображение'),tx('Если заявок до четырёх — карточки, с пятой — таблица со страницами.')],[tx('Этапы согласования'),tx('Заявитель → расчётный бухгалтер (реквизиты и комплектность) → финансовый директор (бюджет и дата) → директор по политике статьи → оплата. Политика фиксируется при отправке заявки.')],[tx('Форма заявки'),tx('Одно окно: компания, канал оплаты, валюта, счёт, статья, сумма, контрагент, назначение, приоритет и дата, ниже — три блока вложений. «Сохранить черновик» сохраняет заявку без отправки, «Отправить на согласование» сохраняет, загружает файлы и отправляет. Карточка «Бюджет статьи» показывает лимит, использовано, резерв, доступно и остаток после заявки по выбранной статье.')],[tx('Документы'),tx('Черновик сохраняется без файлов. Для отправки нужны внутренняя заявка (индент) и договор или счёт на оплату; прочие документы — по желанию. PDF, PNG, JPEG, DOCX или XLSX до 5 МБ. После отправки файлы меняются в карточке заявки: новая версия обязательного документа или удаление прочего с причиной начинают согласование заново; прежние версии остаются в истории.')],[tx('Если файл не загрузился'),tx('Черновик и уже загруженные файлы сохраняются. Исправьте форму и нажмите кнопку ещё раз: сохранятся текущие поля, загрузятся только недостающие файлы, вторая заявка не создаётся. Если заявка уже отправлена, дальнейшие изменения — в её карточке с указанием причины.')],[tx('Срок оплаты'),tx('Обычная заявка — не раньше 7 рабочих дней, высокий приоритет — 3, срочная — 1. Сегодняшняя дата недоступна; праздники берутся из календаря холдинга.')],[tx('Возврат и закрытие'),tx('«Вернуть на доработку» — автор исправляет ту же заявку и отправляет снова. «Закрыть без оплаты» завершает заявку окончательно. Оба действия требуют комментария. Автор, редактор и согласующие заявку не согласуют и не оплачивают.')]],
 calendar:[[tx('Сценарии'),tx(dash.value?.scenario_note||N_('Сценарий «Все заявки» добавляет к утверждённым оплатам заявки на согласовании.'))],[tx('Как считается прогноз'),tx(dash.value?.note||N_('Прогноз на конец дня: текущие остатки + ожидаемые поступления − утверждённые неоплаченные заявки.'))],[tx('Сортировка'),tx('Порядок по дате переключается кнопкой в заголовке столбца «Дата» или списком в панели.')]],
 budgets:[[tx('Лимит и резерв'),tx('«Оплачено» — фактические платежи месяца. «Резерв» — утверждённые, но ещё не оплаченные заявки. Остаток = лимит − оплачено − резерв.')],[tx('Контроль превышения'),tx('«Разрешать превышение с обоснованием» — заявку сверх лимита можно согласовать с обоснованием. «Запрещать превышение» — сервер блокирует заявку сверх лимита.')],[tx('Группы затрат'),tx('Группа (постоянные, переменные, прочие) относится к статье во всех месяцах и валютах. Она не связана с видами деятельности Cash Flow.')]],
 import:[[tx('Как заполнить файл'),tx('В шаблоне указывайте названия счетов и статей точно как в справочниках. Тип: Поступление, Выплата или Перевод. Дата: ГГГГ-ММ-ДД. Сумма: с точкой. Документ: уникальный номер на счёте. Для выплаты укажите основание в комментарии (от 10 символов). В Excel данные должны быть на первом листе.')],[tx('Проверка'),tx('Файл сначала проверяется. Если есть ошибки, операции не записываются, а строки с проблемами показываются. Импорт применяет те же проверки, что и ручной ввод.')]],
 categories:[[tx('Статьи'),tx('Название, направление (поступление или выплата) и вид деятельности. Вид деятельности определяет раздел отчёта Cash Flow.')]],
 approval:[[tx('Маршрут заявки'),tx('Расчётный бухгалтер проверяет реквизиты и комплектность → финансовый директор проверяет бюджет и дату → директор, если этого требует политика статьи → оплата: банк — расчётный бухгалтер, касса — кассир. Один сотрудник не проходит два этапа одной заявки; автор и последний редактор её не согласуют и не оплачивают.')],[tx('Политика директора'),tx('Для каждой расходной статьи: директор утверждает всегда, только выше порога валюты заявки или не участвует. Без директора — только статьи из утверждённого перечня регулярных платежей, который ведёт администратор холдинга. Нет политики или порога валюты — директор утверждает любую сумму.')],[tx('Снимок при отправке'),tx('Политика и порог сохраняются в заявке при отправке. Изменение справочника не меняет маршрут уже отправленных заявок; каждое изменение записывается в журнал.')],[tx('Календарь и сроки'),tx('Суббота и воскресенье — выходные; праздники и рабочие выходные задаёт администратор холдинга. Минимальный срок оплаты: обычная заявка — от 7 рабочих дней, высокий приоритет — от 3, срочная — от 1; сегодняшняя дата недоступна.')],[tx('Возврат и закрытие'),tx('«Вернуть на доработку» — автор исправляет ту же заявку и отправляет снова. «Закрыть без оплаты» завершает заявку окончательно, она остаётся в истории со статусом «Закрыта без оплаты». Оба действия требуют комментария.')]],
 users:[[tx('Одна компания'),tx('Сотрудник работает ровно в одной компании и только по назначенной роли. Новое назначение переносит его и отзывает прежний доступ; связь без роли доступа не даёт. Если в старых данных сотрудник назначен в нескольких компаниях, доступ приостановлен, пока администратор не выберет одну.')],[tx('Центр администрирования'),tx('Поиск по имени и логину, фильтры по компании, роли и состоянию. Карточка сотрудника: имя, назначение, статус холдинга, сброс пароля, завершение сеансов, архив и восстановление. Каждое действие требует причину и записывается в журнал с прежним и новым назначением.')],[tx('Временный пароль'),tx('При создании, сбросе и восстановлении пароль создаётся случайным, показывается один раз и меняется сотрудником при первом входе: до смены пароля остальные разделы недоступны. Прежние сеансы и Telegram отключаются.')],[tx('Холдинг'),tx('Администратор холдинга видит все компании, управляет пользователями и доступами; финансовые действия — только по отдельному назначению роли в компании, которое выдаёт другой администратор. Учредитель видит все компании только для чтения.')],[tx('Согласование и оплата'),tx('Расчётный бухгалтер проверяет реквизиты и комплектность, финансовый директор — бюджет и дату, директор участвует по политике статьи. Банковскую заявку оплачивает расчётный бухгалтер, кассовую — кассир; проверявший бухгалтер, автор, редактор и согласующие её не оплачивают.')],[tx('ВрИО'),tx('На время отсутствия администратор холдинга назначает временно исполняющего из сотрудников этой же компании (или администратора холдинга). ВрИО работает под своим логином с ролью отсутствующего только в этой компании и только до конца срока. Сотрудника другой компании сначала переводят в центре администрирования. Собственную заявку ВрИО не согласует; журнал хранит обоих сотрудников. Смена назначения или архив завершают замещения, потерявшие основание.')],[tx('Пароли'),tx('Сохранённые пароли не отображаются и не восстанавливаются: доступ возвращается только новым временным паролем.')],[tx('Telegram-группы сводки'),tx('Каждая группа получает сводку ровно одной компании. В форме подключения укажите ID группы и выберите её компанию, затем отправьте полученную команду /register в эту группу. Код подходит только выбранной группе и компании. Неизвестная или неподключённая группа не получает финансовую сводку. Чтобы сменить компанию, отключите группу и подключите заново. Группа отключается сама, если подключивший её администратор отключён или больше не администратор холдинга.')]],
 audit:[[tx('Как искать'),tx('Выберите месяц или даты, сотрудника и действие, затем нажмите «Показать». Записи показываются простым списком; технические сведения — по кнопке «Подробности». Время указано по Ташкенту.')]],
 profile:[[tx('Сессия и пароль'),tx('Сессия действует 60 минут. Пароль не короче 12 символов. После смены пароля все сессии отзываются. Временный пароль от администратора меняется при первом входе.')],[tx('ВрИО'),tx('Если вы временно исполняете обязанности отсутствующего сотрудника, его роль показана рядом с вашей и действует только в этой компании до конца срока замещения.')],['Telegram',tx('Чтобы бот присылал напоминания по заявкам, которые ждут вашего действия, нажмите «Привязать Telegram» и отправьте боту одноразовый код — он действует 10 минут.')]]
}[page.value]||[]));

const budgetGroup=ref(''),calendarOrder=ref('asc'),receiptsOpen=ref(false),receiptsLoading=ref(false);
const budgetRows=computed(()=>rows.value.filter(r=>!budgetGroup.value||r.cost_group===budgetGroup.value));
const sortedForecast=computed(()=>calendarOrder.value==='desc'?[...forecastRows.value].reverse():forecastRows.value);
const amountFields=['amount','opening','limit','spent','reserved','remaining','balance'];
const dateFrom=ref(''),dateTo=ref(''),listPage=ref(1),listTotal=ref(0),requestStatus=ref(''),showArchive=ref(false),expandedRequest=ref(null);
const visibleRows=computed(()=>['ledger','requests'].includes(page.value)?rows.value:rows.value.filter(r=>(!r.currency||r.currency===currency.value)&&JSON.stringify(r).toLowerCase().includes(search.value.toLowerCase())));
const listPages=computed(()=>Math.max(1,Math.ceil(listTotal.value/10)));
const requestStatuses=computed(()=>Object.fromEntries(Object.entries(status).filter(([key])=>!['expected','received'].includes(key))));
watch(currency,()=>{listPage.value=1},{flush:'sync'});
async function applyPeriod(v){dateFrom.value=v.from;dateTo.value=v.to;listPage.value=1;await load()}
async function changeListPage(n){listPage.value=n;expandedRequest.value=null;await load()}
let searchTimer;watch(search,()=>{if(['ledger','requests'].includes(page.value)){clearTimeout(searchTimer);searchTimer=setTimeout(()=>{listPage.value=1;load()},300)}});
onUnmounted(()=>clearTimeout(searchTimer));
function archiveAccount(a){open(a.archived?N_('Восстановить счёт'):N_('Убрать счёт в архив'),[field('reason',N_('Причина'),'textarea',{minlength:10})],{reason:''},d=>post(`/api/accounts/${a.id}/archive`,{...d,archived:!a.archived}),N_('История сохраняется. В архив можно убрать счёт с нулевым остатком и без незавершённых заявок.'))}

let listRequestId=0,reportRequestId=0;
const stageLabel=r=>requestStage(r);
const otherCurrencies=computed(()=>[...new Set(boot.value.accounts.map(a=>a.currency))].filter(c=>c!==currency.value));
const currentAccounts=computed(()=>boot.value.accounts.filter(a=>a.currency===currency.value));
const modalFields=computed(()=>{const fields=modal.value?.fields||[];if(fields.some(f=>f.key==='action'))return fields.filter(f=>f.key!=='date'||form.value.action==='reschedule');if(!fields.some(f=>f.key==='to_account_id'))return fields;return fields.filter(f=>f.key==='to_account_id'?form.value.kind==='transfer':f.key==='category_id'?form.value.kind!=='transfer':true)});
const categoryHint=computed(()=>{if(!modal.value?.fields.some(f=>f.key==='to_account_id')||form.value.kind==='transfer')return '';const c=boot.value.categories.find(c=>c.id===Number(form.value.category_id));return c&&c.type!==(form.value.kind==='in'?'income':'outcome')?tx('Проверьте статью: её тип не совпадает с направлением операции. Если это возврат, укажите пояснение в комментарии.'):''});
async function switchCurrency(value){currency.value=value;search.value='';listPage.value=1;await load()}
const subtitles={home:N_('Факт, план и прогноз денежных средств в одном месте.'),report:N_('Управленческая форма по прямому методу · структура IAS 7.'),accounts:N_('Счета и касса по валютам. Суммы разных валют не складываются.'),ledger:N_('Фактические поступления, расходы и переводы.'),requests:N_('Заявки на оплату и этапы согласования.'),calendar:N_('Поступления, выплаты и прогноз остатка по дням.'),budgets:N_('Лимиты расходов по статьям и контроль превышения.'),import:N_('Загрузка операций из CSV или Excel.'),categories:N_('Статьи движения денег и виды деятельности.'),approval:N_('Политика директора по статьям и календарь рабочих дней.'),users:N_('Учётные записи холдинга, назначения в компаниях, ВрИО, архив и журнал доступа.'),audit:N_('Кто, когда и что делал в системе.'),profile:N_('Ваши данные, пароль и Telegram.')};
subtitles.business=N_('Проект → Данные → Отчёт. Готовые бизнес-план PDF и расчётный Excel скачиваются прямо из списка проектов.');
subtitles.reports=N_('Все готовые отчёты компании: рассчитанные бизнес-планы и загруженные PDF и Excel с периодами.');
const priorities={normal:N_('Обычный'),high:N_('Высокий'),urgent:N_('Срочный')};
const scenario=ref('approved');
const forecastRows=computed(()=>(dash.value?.forecast||[]).map(d=>scenario.value==='all'?{...d,opening:d.requested_opening,outgoing:d.requested_outgoing,balance:d.requested_balance,risk:d.requested_risk,account_shortfalls:d.requested_account_shortfalls,events:[...d.events,...d.pending_events]}:d));
// Доступные действия по заявке вычисляет сервер (r.actions); интерфейс только показывает их.
// Оплату проводит плательщик канала (банк — pay_bank, касса — pay_cash), не участвовавший в заявке; сервер проверяет то же.
const canPayRequest=r=>(r.actions||[]).includes('pay')&&canPayFor(r,user.value?.id,has);
const editor=ref(null),cardId=ref(null);
watch(()=>[page.value,modal.value,editor.value,documents.value,companyId.value,!!user.value],clearFieldFeedback);
// Выход, истёкшая сессия или смена компании закрывают форму и карточку заявки.
watch(()=>!!user.value&&companyReady.value,on=>{if(!on){editor.value=null;cardId.value=null}});
function openRequest(r){cardId.value=r.id}
async function editorSaved(r){const back=editor.value?.request;editor.value=null;if(back)cardId.value=r.id;flash(r.resent?N_('Изменения сохранены: заявка {number} снова на согласовании.'):r.submitted?N_('Заявка {number} отправлена на согласование.'):N_('Черновик {number} сохранён.'),{number:r.number});await load()}
function editorClosed(){const back=editor.value?.request;editor.value=null;if(back)cardId.value=back.id;load()}
function editorOpenCard(id){editor.value=null;cardId.value=id;load()}
function cardEdit(r){cardId.value=null;editor.value={request:r}}
function cardPay(r){cardId.value=null;transaction(r)}
async function api(url,opts={}){
 const epoch=companyEpoch,controller=new AbortController();pendingCalls.add(controller);
 const ticket=localProgressUrl(url)?null:requestProgress.begin(requestLabel(url,opts.method||'GET'));
 try{
  const r=await fetch(url,{credentials:'same-origin',...opts,signal:controller.signal,headers:{'X-CSRF-Token':csrf.value,...(companyId.value?{'X-Company-ID':String(companyId.value)}:{}),...opts.headers}});
  let data;try{data=await r.json()}catch{throw Object.assign(Error(N_('Некорректный ответ сервера')),{status:r.status})}
  if(epoch!==companyEpoch)throw new DOMException(N_('Компания изменена'),'AbortError');
  if(!r.ok){if(r.status===401)user.value=null;throw Object.assign(Error(Array.isArray(data.detail)?data.detail.map(x=>`${x.loc?.slice(1).join('.')}: ${x.msg}`).join('; '):data.detail||N_('Ошибка запроса')),{status:r.status})}
  if(ticket)requestProgress.complete(ticket,N_('Действие завершено'));return data;
 }catch(e){if(ticket&&epoch===companyEpoch){if(url==='/api/me'&&e.status===401)requestProgress.reset();else requestProgress.fail(ticket,e.name==='AbortError'?N_('Действие отменено'):e)}throw e}
 finally{pendingCalls.delete(controller)}
}
const post=(url,data={})=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function flash(text,params=null){notice.value=text;noticeParams.value=params;setTimeout(()=>notice.value='',5500)}
async function bootstrap(){boot.value=await api('/api/bootstrap');user.value=boot.value.user;csrf.value=boot.value.csrf;month.value=month.value.match(/^\d{4}-\d{2}$/)?month.value:boot.value.today.slice(0,7);if(!validReportDate(reportAsOf.value,year.value,boot.value.today)){const cutOff=reportDateForYear(year.value,boot.value.today);if(!cutOff)year.value=Number(boot.value.today.slice(0,4));reportAsOf.value=cutOff||boot.value.today}}
async function changeReportYear(value){const cutOff=reportDateForYear(value,boot.value.today);if(!cutOff){flash(N_('Будущий год недоступен: дата факта не может быть позже сегодня.'));return}year.value=Number(value);reportAsOf.value=cutOff;await load()}
async function changeReportDate(value){if(!validReportDate(value,year.value,boot.value.today)){flash(N_('Дата отчёта должна быть в выбранном году и не позже сегодня.'));return}reportAsOf.value=value;await load()}
async function enter(){companyId.value=null;companyReady.value=false;if(user.value?.must_change_password)return;const data=await api('/api/companies');boot.value={accounts:[],categories:[],...data};user.value=data.user;csrf.value=data.csrf;}
async function selectCompany(id,targetPage=null){
 if(saving.value||modal.value||documents.value||editor.value||cardId.value){flash(N_('Закройте текущую форму перед сменой компании.'));return}
 cancelPending();clearTimeout(searchTimer);
 const selectionEpoch=companyEpoch;
 companyReady.value=false;companyId.value=Number(id);busy.value=true;error.value='';notice.value='';
 rows.value=[];dash.value=null;report.value=null;preview.value=null;receipts.value=[];expandedRequest.value=null;
 search.value='';dateFrom.value='';dateTo.value='';requestStatus.value='';listPage.value=1;listTotal.value=0;showArchive.value=false;receiptsOpen.value=false;planMappings.value={};helpOpen.value=false;menuOpen.value=false;
 boot.value={...boot.value,accounts:[],categories:[],counterparties:[]};
 try{
  await bootstrap();if(selectionEpoch!==companyEpoch||companyId.value!==Number(id)||!user.value)return;
  if(['business','reports'].includes(targetPage)){
   if(has('export')&&nav.value.some(n=>n[0]===targetPage)){page.value=targetPage;location.hash=targetPage;}
   else flash(N_('У вашей роли нет прав на это действие.'));
  }
  if(!nav.value.some(n=>n[0]===page.value))page.value=nav.value[0][0];
  companyReady.value=true;await load();
 }catch(e){if(selectionEpoch===companyEpoch&&e.name!=='AbortError')error.value=e.message}finally{if(selectionEpoch===companyEpoch)busy.value=false}
}
async function openCompanyReport({companyId:id,page:targetPage}={}){
 if(busy.value||!has('export')||!['business','reports'].includes(targetPage)||!selectableCompanies.value.some(c=>c.id===id))return;
 if(saving.value||modal.value||documents.value||editor.value||cardId.value||reportEditing.value){flash(N_('Закройте текущую форму перед сменой компании.'));return;}
 await selectCompany(id,targetPage);
}
function chooseCompany(){if(busy.value)return;if(modal.value||documents.value||editor.value||cardId.value||saving.value||reportEditing.value){flash(N_('Закройте текущую форму перед сменой компании.'));return}cancelPending();busy.value=true;companyReady.value=false;companyId.value=null;enter().catch(e=>{if(e.name!=='AbortError')error.value=e.message}).finally(()=>busy.value=false)}
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
async function logout(){cancelPending();busy.value=true;try{await post('/api/logout')}finally{cancelPending();user.value=null;csrf.value='';companyReady.value=false;companyId.value=null;busy.value=false}}
async function goWith(name,opts={}){menuOpen.value=false;helpOpen.value=false;reportEditing.value=false;page.value=name;location.hash=name;search.value='';dateFrom.value=opts.from||'';dateTo.value=opts.to||'';requestStatus.value=opts.status||'';listPage.value=1;expandedRequest.value=null;receiptsOpen.value=false;rows.value=[];await load()}
const canDecide=r=>canDecideRequest(r);
async function go(name){menuOpen.value=false;helpOpen.value=false;reportEditing.value=false;page.value=name;location.hash=name;search.value='';dateFrom.value='';dateTo.value='';requestStatus.value='';listPage.value=1;expandedRequest.value=null;receiptsOpen.value=false;rows.value=[];await load()}
async function load(){if(!companyReady.value)return;const epoch=companyEpoch;busy.value=true;error.value='';try{
 if(page.value==='home')homeRefresh.value++;
 if(page.value==='reports')reportArchiveRefresh.value++;
 if(page.value==='business')businessRefresh.value++;
 if(page.value==='calendar')dash.value=await api(`/api/dashboard?currency=${currency.value}&days=${days.value}`);
 if(page.value==='accounts')rows.value=await api('/api/accounts?archived='+showArchive.value);
 if(['ledger','requests'].includes(page.value)){const requestId=++listRequestId;const requestedPage=page.value;const params=new URLSearchParams({paginated:'true',page:String(listPage.value),currency:currency.value,q:search.value});if(dateFrom.value)params.set('date_from',dateFrom.value);if(dateTo.value)params.set('date_to',dateTo.value);if(page.value==='requests'&&requestStatus.value)params.set('state',requestStatus.value);const data=await api(`/api/${page.value}?${params}`);if(requestId===listRequestId&&page.value===requestedPage){rows.value=data.items;listTotal.value=data.total;}}
 if(page.value==='calendar'&&receiptsOpen.value)await loadReceipts();
 if(page.value==='budgets')rows.value=await api(`/api/budgets?currency=${currency.value}&month=${month.value}`);
 if(page.value==='report'){const requestId=++reportRequestId;const c=currency.value,y=year.value,co=companyId.value,sc=reportScenario.value,ao=reportAsOf.value;if(report.value&&(report.value.currency!==c||report.value.year!==Number(y)||report.value.company_id!==co||report.value.scenario!==sc||report.value.as_of!==ao))report.value=null;const result=await api(`/api/report?currency=${c}&year=${y}&company_id=${co}&scenario=${sc}&as_of=${ao}`);if(requestId===reportRequestId&&page.value==='report'&&currency.value===c&&year.value===y&&companyId.value===co&&reportScenario.value===sc&&reportAsOf.value===ao)report.value=result;}
 if(page.value==='categories')rows.value=boot.value.categories;
 if(page.value==='audit')auditRefresh.value++;
 if(page.value==='users'&&user.value?.holding_role==='admin')tgGroups.value=(await api('/api/telegram-groups')).items;
 if(page.value==='profile')await loadTelegram();


 }catch(e){if(epoch===companyEpoch&&e.name!=='AbortError')error.value=e.message}finally{if(epoch===companyEpoch)busy.value=false}}
const field=(key,label,type='text',extra={})=>({key,label,type,required:true,...extra});
const accounts=()=>boot.value.accounts.map(a=>[a.id,`${a.name} · ${a.currency}`]);
const categories=()=>boot.value.categories.map(c=>[c.id,`${c.name} · ${c.type==='income'?tx('приход'):tx('расход')}`]);
// Titles, labels and notes are Russian source text (N_) translated in the template; option labels are translated here because they mix in user data.
function open(title,fields,values,submit,note='',titleParams=null){form.value={...values};formError.value='';modal.value={title,titleParams,fields,submit,note}}
async function save(){saving.value=true;formError.value='';try{await modal.value.submit({...form.value});modal.value=null;await bootstrap();await load();flash(N_('Сохранено'))}catch(e){formError.value=e.message}finally{saving.value=false}}
function account(a=null){open(a?N_('Редактировать счёт'):N_('Добавить счёт'),[
 field('name',N_('Название')),field('kind',N_('Тип'),'select',{options:[['bank',tx('Банковский счёт')],['cash',tx('Касса')]]}),field('currency',N_('Валюта'),'select',{options:['UZS','USD','EUR'].map(x=>[x,x])}),field('opening',N_('Начальный остаток'),'number',{min:0}),field('opening_date',N_('Дата начала учёта'),'date'),field('allow_overdraft',N_('Разрешён овердрафт'),'checkbox',{required:false}),...(a?[field('reason',N_('Причина исправления'),'textarea',{minlength:10})]:[])],a||{name:'',company_id:companyId.value,kind:'bank',currency:currency.value,opening:'0',opening_date:boot.value.today,allow_overdraft:false},d=>post(a?`/api/accounts/${a.id}`:'/api/accounts',Object.fromEntries(['name','kind','currency','opening','opening_date','allow_overdraft',...(a?['reason']:[])].map(k=>[k,k==='opening'?String(d[k]):d[k]]))),N_('Остаток на начало дня. Изменения сохраняются в журнале. Валюта счёта с историей защищена от изменения.'))}
function transaction(link=null,receipt=false){if(!link&&!currentAccounts.value.length){go('accounts');return}open(link?N_('Подтвердить факт'):N_('Новая операция'),[
 field('kind',N_('Тип'),'select',{options:[['in',tx('Поступление')],['out',tx('Расход')],['transfer',tx('Внутренний перевод')]],disabled:!!link}),field('account_id',N_('Счёт'),'select',{options:accounts(),disabled:!!link}),field('to_account_id',N_('Счёт-получатель (для перевода)'),'select',{options:[[null,'—'],...accounts()],required:false}),field('category_id',N_('Статья'),'select',{options:categories(),disabled:!!link}),field('amount',N_('Сумма'),'number',{min:.01,disabled:!!link}),field('date',N_('Дата факта'),'date',{min:link&&!receipt?link.approved_on||undefined:undefined}),field('reference',N_('Номер документа')),field('counterparty',N_('Контрагент'),'text',{required:false,disabled:!!link&&!receipt}),field('note',link&&!receipt?N_('Комментарий к оплате'):N_('Основание / комментарий'),'textarea',{required:false})],{kind:link?(receipt?'in':'out'):'in',account_id:link?.account_id||currentAccounts.value[0]?.id,to_account_id:null,category_id:link?.category_id||boot.value.categories[0]?.id,amount:link?.amount||'',date:boot.value.today,reference:'',counterparty:link?.counterparty||'',note:link?.purpose||''},d=>post('/api/ledger',{...d,amount:String(d.amount),category_id:d.kind==='transfer'?null:Number(d.category_id),account_id:Number(d.account_id),to_account_id:d.kind==='transfer'?Number(d.to_account_id):null,request_id:link&&!receipt?link.id:null,request_version:link&&!receipt?link.version:null,receipt_id:link&&receipt?link.id:null}),link&&!receipt?N_('Сумма, счёт, статья и получатель берутся из утверждённой заявки. Если денег или бюджета не хватает, оплата не пройдёт: верните заявку финансовому директору через «Действия».'):N_('Записывайте только фактически совершённые операции. Для расхода без заявки основание — от 10 символов.'))}
function requestForm(r=null){if(!boot.value.accounts.length){if(has('write'))go('accounts');else flash(N_('В компании нет активных счетов для заявки. Обратитесь к финансовому директору.'));return}editor.value={request:r}}
function budget(r){open(N_('Бюджет · {name}'),[field('amount',N_('Лимит'),'number',{min:0,required:false,placeholder:N_('Не задан')}),field('cost_group',N_('Группа затрат'),'select',{options:Object.entries(costGroups).map(([key,label])=>[key,tx(label)])}),field('mode',N_('Контроль'),'select',{options:[['soft',tx('Разрешать превышение с обоснованием')],['hard',tx('Запрещать превышение')]]}),field('reason',N_('Причина / основание'),'textarea',{minlength:10})],{amount:r.limit??'',mode:r.mode,cost_group:r.cost_group||'other',reason:''},d=>post('/api/budgets',{...d,amount:d.amount===''?null:String(d.amount),category_id:r.category_id,month:month.value,currency:currency.value}),N_('Группа относится к статье во всех месяцах и валютах. Пустой лимит не меняет ограничение бюджета.'),{name:r.category})}
function category(c=null){open(c?N_('Редактировать статью'):N_('Новая статья'),[field('name',N_('Название')),field('type',N_('Направление'),'select',{options:[['income',tx('Поступление')],['outcome',tx('Выплата')]]}),field('activity',N_('Деятельность'),'select',{options:[['operating',tx('Операционная')],['investing',tx('Инвестиционная')],['financing',tx('Финансовая')]]})],c?{name:c.name,type:c.type,activity:c.activity}:{name:'',type:'outcome',activity:'operating'},d=>c?api(`/api/categories/${c.id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}):post('/api/categories',d))}
function reverse(r){open(N_('Сторно операции {reference}'),[field('reason',N_('Причина сторно'),'textarea',{minlength:10})],{reason:''},d=>post(`/api/ledger/${r.id}/reverse`,d),N_('Исправление отменит влияние ошибочной операции. История останется в журнале.'),{reference:r.reference})}
const tempPassword=ref({old_password:'',new_password:'',repeat:''}),tempError=ref('');
// Временный пароль (создание, сброс, восстановление) меняется до входа в разделы; затем нужен новый вход.
async function replaceTemporaryPassword(){
 tempError.value='';const f=tempPassword.value;
 if(f.new_password!==f.repeat){tempError.value=N_('Новый пароль и повтор не совпадают.');return}
 busy.value=true;try{await post('/api/password',{old_password:f.old_password,new_password:f.new_password});tempPassword.value={old_password:'',new_password:'',repeat:''};user.value=null;csrf.value='';error.value='';flash(N_('Пароль изменён. Войдите с новым паролем.'))}catch(e){tempError.value=e.message}finally{busy.value=false}
}
function changePassword(){open(N_('Смена пароля'),[field('old_password',N_('Текущий пароль'),'password'),field('new_password',N_('Новый пароль'),'password',{minlength:12})],{old_password:'',new_password:''},async d=>{await post('/api/password',d);user.value=null;location.reload()})}
// Telegram: the one-time code lives only in memory and is dropped when the profile page is left.
const tg=ref(null),tgCode=ref(null),tgNote=ref(''),tgBusy=ref(false);
const tgBotLink=computed(()=>/^[A-Za-z0-9_]{5,32}$/.test(tg.value?.bot_username||'')?'https://t.me/'+tg.value.bot_username:null);
const tgDate=s=>{const d=new Date(s);return s&&!isNaN(d)?formatDate(d,{timeZone:'Asia/Tashkent',day:'2-digit',month:'2-digit',year:'numeric'}):''};
const tgState=computed(()=>{const t=tg.value;if(!t)return busy.value?'':tx('Статус Telegram не загружен.');if(t.linked){const d=tgDate(t.linked_at);return (d?tx('Telegram привязан с {date}',{date:d}):tx('Telegram привязан'))+(t.telegram_id_tail?' · ID …'+t.telegram_id_tail:'')}return tgNote.value?tx(tgNote.value):tgCode.value?tx('Код создан. Отправьте его боту и проверьте привязку.'):tx('Telegram не привязан.')});
watch(()=>!!user.value&&companyReady.value&&page.value==='profile',on=>{if(!on){tg.value=null;tgCode.value=null;tgNote.value=''}});
async function loadTelegram(){const r=await api('/api/telegram');if(page.value!=='profile')return;tg.value=r;if(r.linked){tgCode.value=null;tgNote.value=''}}
async function telegramCode(){tgBusy.value=true;error.value='';tgNote.value='';try{const r=await post('/api/telegram/code');if(page.value==='profile')tgCode.value={code:String(r.code),minutes:Math.round(Number(r.expires_in)/60)||10,link:/^https:\/\//.test(r.deep_link||'')?r.deep_link:null}}catch(e){if(e.name!=='AbortError')error.value=e.message}finally{tgBusy.value=false}}
async function checkTelegram(){tgBusy.value=true;error.value='';try{await loadTelegram();if(tg.value&&!tg.value.linked&&tgCode.value)tgNote.value=N_('Привязка ещё не выполнена. Отправьте боту команду и проверьте ещё раз.')}catch(e){if(e.name!=='AbortError')error.value=e.message}finally{tgBusy.value=false}}
async function copyTelegramCommand(){try{await navigator.clipboard.writeText('/start '+tgCode.value.code);flash(N_('Команда скопирована'))}catch{flash(N_('Не удалось скопировать. Выделите код и скопируйте вручную.'))}}
function unlinkTelegram(){open(N_('Отвязать Telegram'),[],{},()=>api('/api/telegram',{method:'DELETE'}),N_('Напоминания в Telegram прекратятся сразу. Привязать снова можно новым кодом.'));modal.value.saveLabel=N_('Отвязать')}
// Telegram-группы утренней сводки: видит и отключает только администратор холдинга.
const tgGroups=ref(null);
const tgGroupRows=computed(()=>tgGroups.value?.map(g=>({...g,companyInfo:groupCompanyInfo(g)})));
const tgGroupName=g=>g.title||tx('Группа {id}',{id:g.chat_id});
function removeTelegramGroup(g){open(N_('Отключить группу · {name}'),[],{},()=>api(`/api/telegram-groups/${g.chat_id}`,{method:'DELETE'}),N_('Бот перестанет присылать утреннюю сводку в эту группу. Подключить её снова можно новым кодом в форме подключения Telegram-группы на этой странице.'),{name:tgGroupName(g)});modal.value.saveLabel=N_('Отключить')}
function receipt(){open(N_('Ожидаемое поступление'),[field('account_id',N_('Счёт'),'select',{options:accounts()}),field('category_id',N_('Статья'),'select',{options:categories()}),field('amount',N_('Сумма'),'number',{min:.01}),field('date',N_('Ожидаемая дата'),'date'),field('counterparty',N_('Контрагент'))],{account_id:boot.value.accounts[0]?.id,category_id:boot.value.categories.find(c=>c.type==='income')?.id,amount:'',date:boot.value.today,counterparty:''},d=>post('/api/receipts',{...d,amount:String(d.amount)}))}
const receipts=ref([]);
async function loadReceipts(){receiptsLoading.value=true;try{receipts.value=(await api('/api/receipts')).filter(r=>r.currency===currency.value&&r.status==='expected')}catch(e){error.value=e.message}finally{receiptsLoading.value=false}}
async function showReceipts(){receiptsOpen.value=!receiptsOpen.value;if(receiptsOpen.value)await loadReceipts()}

async function upload(event){const file=event.target.files[0];if(!file)return;busy.value=true;error.value='';preview.value=null;try{preview.value=await api('/api/import/preview',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(file.name)},body:file})}catch(e){error.value=e.message}finally{busy.value=false;event.target.value=''}}

async function uploadPlan(event){const file=event.target.files[0];if(!file)return;busy.value=true;error.value='';preview.value=null;try{const q=new URLSearchParams({company_id:String(companyId.value),year:String(year.value),month_from:String(planMonthFrom.value),month_to:String(planMonthTo.value)});const r=await api('/api/plan-import/preview?'+q,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(file.name)},body:file});preview.value={...r,type:'plan'};const map={};for(const row of r.rows)if(!(row.source_key in map))map[row.source_key]=String(r.suggestions[row.source_key]||'new');planMappings.value=map}catch(e){error.value=e.message}finally{busy.value=false;event.target.value=''}}

async function commit(){busy.value=true;try{const r=await post(`/api/import/${preview.value.id}/commit`);preview.value=null;flash(N_('Импортировано операций: {count}'),{count:r.count});await bootstrap()}catch(e){error.value=e.message}finally{busy.value=false}}
async function commitPlan(){busy.value=true;error.value='';try{const r=await post(`/api/plan-import/${preview.value.id}/commit`,{mappings:planMappings.value,reason:planReason.value});preview.value=null;flash(N_('Обновлено плановых значений: {count}'),{count:r.updated});await bootstrap()}catch(e){error.value=e.message}finally{busy.value=false}}
async function docs(r){try{documents.value={ledger:r,rows:await api(`/api/documents?ledger_id=${r.id}`)}}catch(e){error.value=e.message}}
async function attach(event){const file=event.target.files[0];if(!file)return;saving.value=true;try{await api(`/api/ledger/${documents.value.ledger.id}/document`,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(file.name)},body:file});await docs(documents.value.ledger)}catch(e){error.value=e.message}finally{saving.value=false;event.target.value=''}}
async function reserve(amount){open(N_('Минимальный резерв'),[field('amount',N_('Сумма резерва'),'number',{min:0})],{amount:typeof amount==='string'?amount:dash.value?.reserve||'0'},d=>post('/api/reserve',{amount:String(d.amount),currency:currency.value}))}
window.addEventListener('hashchange',()=>{const target=location.hash.slice(1);if(user.value&&companyReady.value&&target!==page.value&&nav.value.some(n=>n[0]===target)){page.value=target;load()}});
onMounted(async()=>{try{const r=await api('/api/me');user.value=r.user;csrf.value=r.csrf;await enter()}catch{user.value=null}});
</script>

<template>
 <aside v-if="globalProgress.active||globalProgress.completed||globalProgress.error" class="global-operation" :aria-label="tx('Ход выполнения действия')"><OperationProgress v-bind="globalProgress"/><button v-if="!globalProgress.active" type="button" class="ghost tiny" :aria-label="tx('Скрыть индикатор выполнения')" @click="requestProgress.reset()">{{tx('Закрыть')}}</button></aside>
 <aside v-if="validationFields.length" id="form-validation-feedback" class="global-form-feedback" role="alert" aria-labelledby="form-validation-title"><strong id="form-validation-title">{{tx('Заполните или исправьте поля')}}</strong><p>{{tx('Нажмите название, чтобы перейти к полю.')}}</p><ul><li v-for="(field,index) in validationFields" :key="index"><button type="button" class="ghost" @click="focusInvalidField(field.control)">{{field.label}}</button><small>{{field.message}}</small></li></ul><button type="button" class="secondary tiny" @click="clearFieldFeedback">{{tx('Закрыть подсказку')}}</button></aside>
 <div v-if="availableRelease" class="release-notice" role="status"><span>{{tx('Доступна версия {version}. Сохраните текущие изменения перед обновлением.',{version:availableRelease.version})}}</span><button class="secondary tiny" @click="refreshVersion">{{tx('Обновить страницу')}}</button></div>
 <div v-if="showRelease" class="modal-backdrop" @click.self="showRelease=false" @keydown.esc="showRelease=false"><section class="modal release-dialog" role="dialog" aria-modal="true" aria-labelledby="release-title" v-modal-focus><div class="section-head"><h2 id="release-title">{{tx('Что нового · {version}',{version:release.version})}}</h2><button class="ghost icon-btn" :aria-label="tx('Закрыть список изменений')" @click="showRelease=false"><AppIcon name="x"/></button></div><p class="sub">{{release.date}}</p><ul><li v-for="change in (lang==='uz'&&release.changes_uz?.length?release.changes_uz:release.changes)" :key="change">{{change}}</li></ul></section></div>

 <!-- Вход -->
 <div v-if="!user" class="login-layout">
  <section class="login-art"><div class="brand"><span class="mark">U</span><span class="bname">UZGERMED</span></div><div><h1>{{tx('Деньги под контролем.')}}<br><em>{{tx('Решения — вовремя.')}}</em></h1><p>{{tx('Счета, платежи и бюджеты.')}}<br>{{tx('Одно пространство для финансовой команды.')}}</p><div class="login-lines"><span>{{tx('БАНК')}}</span><span>{{tx('КАССА')}}</span><span>CASH FLOW</span><svg viewBox="0 0 500 130" aria-hidden="true"><polyline points="0,120 60,100 110,108 170,62 220,80 280,40 340,55 400,10 450,28 500,4" fill="none" stroke="var(--eml)" stroke-width="3"/></svg></div></div><small>{{tx('Закрытая корпоративная система')}}</small></section>
  <form id="login-form" class="login-form" method="post" action="/api/login" autocomplete="on" @submit.prevent="signIn">
   <div class="login-lang"><LanguageSwitch compact/></div><p class="eyebrow">UZGERMED TREASURY</p><h2>{{tx('Добро пожаловать')}}</h2><p>{{tx('Войдите в финансовое пространство компании.')}}</p>
   <label for="username">{{tx('Логин')}}
    <input id="username" v-model="login.username" name="username" type="text" list="recent-logins" autocomplete="username" autocapitalize="none" :spellcheck="false" aria-describedby="login-hint" :placeholder="tx('Введите логин')" required :readonly="busy">
    <datalist id="recent-logins"><option v-for="name in recentLogins" :key="name" :value="name" /></datalist>
   </label>
   <div v-if="loginSuggestions.length" class="recent-logins" role="group" :aria-label="tx('Последние логины')">
    <span>{{tx('Последние логины')}}</span>
    <button v-for="name in loginSuggestions" :key="name" type="button" class="recent-login" :disabled="busy" @click="chooseRecentLogin(name)">{{name}}</button>
   </div>
   <small id="login-hint" class="login-hint">{{tx('Здесь появятся 3 последних успешных логина из этого браузера.')}}</small>
   <label for="current-password">{{tx('Пароль')}}<input id="current-password" ref="passwordInput" v-model="login.password" name="password" type="password" autocomplete="current-password" aria-describedby="password-hint" :placeholder="tx('Введите пароль')" required :readonly="busy"></label>
   <p v-if="error" class="error" role="alert">{{tx(error)}}</p>
   <p v-if="notice" class="notice" role="status">{{tx(notice,noticeParams)}}</p>
   <button type="submit" :disabled="busy">{{busy?tx('Входим…'):tx('Войти в систему')}} <AppIcon name="arrow"/></button>
   <small id="password-hint">{{tx('Для автозаполнения пароля подтвердите «Сохранить» в предложении браузера. Доступность зависит от браузера и его настроек.')}}</small>
  </form>
 </div>

 <!-- Смена временного пароля -->
 <section v-else-if="user.must_change_password" class="company-choice">
  <header><div class="brand"><span class="mark" aria-hidden="true">CF</span><h1 class="bname">Cash Flow</h1></div><div class="head-actions"><LanguageSwitch compact/><button class="secondary" @click="logout">{{tx('Выйти')}}</button></div></header>
  <main :aria-label="tx('Смена временного пароля')"><form class="card temp-password" @submit.prevent="replaceTemporaryPassword"><div class="card-b">
   <h2>{{tx('Задайте свой пароль')}}</h2><p class="sub">{{tx('Вы вошли с временным паролем. Придумайте новый пароль не короче 12 символов: после смены войдите снова.')}}</p>
   <div class="form-grid"><label class="wide">{{tx('Временный пароль')}}<input v-model="tempPassword.old_password" type="password" autocomplete="current-password" required></label>
   <label>{{tx('Новый пароль')}}<input v-model="tempPassword.new_password" type="password" autocomplete="new-password" minlength="12" maxlength="128" required></label>
   <label>{{tx('Повтор нового пароля')}}<input v-model="tempPassword.repeat" type="password" autocomplete="new-password" minlength="12" maxlength="128" required></label></div>
   <p v-if="tempError" class="error" role="alert">{{tx(tempError)}}</p>
   <div class="form-actions"><button :disabled="busy">{{busy?tx('Сохраняем…'):tx('Сменить пароль')}}</button></div></div></form></main>
 </section>
 <!-- Рабочее пространство -->
 <section v-else-if="!companyReady" class="company-choice">
  <header><div class="brand"><span class="mark" aria-hidden="true">CF</span><h1 class="bname">Cash Flow</h1></div><div class="head-actions"><LanguageSwitch compact/><button class="secondary" @click="logout">{{tx('Выйти')}}</button></div></header>
  <main :aria-label="tx('Выбор компании')">
   <p v-if="error" class="error" role="alert">{{tx(error)}}</p><p v-if="busy" role="status">{{tx('Открываем компанию…')}}</p>
   <div class="company-grid"><CompanyCard v-for="c in selectableCompanies" :key="c.id" :company="c" :disabled="busy" :can-reports="has('export')" @select="selectCompany" @report="openCompanyReport"/></div>
   <p v-if="!busy&&!selectableCompanies.length" class="sub">{{tx('Нет доступных компаний. Обратитесь к администратору.')}}</p>
  </main>
  <footer class="company-credit">{{tx('Разработка и собственность компании')}} <strong>Sh.A.</strong></footer>
 </section>
 <div v-else class="app" :class="{'menu-open':menuOpen}">
  <div v-if="menuOpen" class="scrim" @click="menuOpen=false"></div>
  <aside class="side" :aria-label="tx('Основное меню')">
   <a class="brand" :href="'#'+(nav[0]?.[0]||'home')" @click.prevent="go(nav[0]?.[0]||'home')"><span class="mark">{{company?.code.charAt(0)}}</span><span class="bname">{{company?.code==='ZUMA'?'ZUMA':company?.name}}</span></a>
   <button class="company-switch secondary" :disabled="busy||saving" @click="chooseCompany"><AppIcon name="accounts"/> {{tx('Сменить компанию')}}</button>
   <nav :aria-label="tx('Разделы')">
    <template v-for="g in navSections" :key="g.label">
     <template v-if="g.tree">
      <button class="nav-i nav-root" :class="{on:isReportPage&&!reportsOpen}" :aria-expanded="reportsOpen" aria-controls="nav-reports" @click="openReports"><AppIcon name="reports"/><span>{{tx(g.label)}}</span><AppIcon name="chev" class="nav-chev" :class="{open:reportsOpen}"/></button>
      <div v-if="reportsOpen" id="nav-reports" class="nav-tree" role="group" :aria-label="tx(g.label)">
       <button v-for="n in g.items" :key="n[0]" class="nav-i nav-sub" :class="{on:page===n[0]}" :aria-current="page===n[0]?'page':undefined" @click="go(n[0])"><AppIcon :name="n[0]"/><span>{{tx(n[2])}}</span><span v-if="n[0]==='report'" class="tag">{{tx('Главный')}}</span></button>
      </div>
     </template>
     <template v-else>
      <p class="nav-l">{{tx(g.label)}}</p>
      <button v-for="n in g.items" :key="n[0]" class="nav-i" :class="{on:page===n[0]}" :aria-current="page===n[0]?'page':undefined" @click="go(n[0])"><AppIcon :name="n[0]"/><span>{{tx(n[2])}}</span></button>
     </template>
    </template>
   </nav>
   <div class="side-foot">
    <button class="nav-i" :class="{on:page==='profile'}" :aria-current="page==='profile'?'page':undefined" @click="go('profile')"><span class="ava">{{initials(user.name)}}</span><span class="who"><b>{{user.name}}</b><small>{{tx(user.role_label)}}</small></span></button>
    <button class="ghost logout" @click="logout">{{tx('Выйти')}}</button>
   </div>
  </aside>

  <div class="workspace">
   <header class="topbar">
    <button class="icon-btn burger" :aria-label="tx('Открыть меню')" @click="menuOpen=true"><AppIcon name="menu"/></button>
    <nav class="crumb" :aria-label="tx('Путь')"><button class="co-switch" :disabled="busy||saving" :aria-label="tx('Компания {name}. Сменить компанию',{name:company?.name})" @click="chooseCompany"><span class="co-mark" aria-hidden="true">{{company?.code.charAt(0)}}</span><span class="co-name">{{company?.name}}</span><AppIcon name="chev"/></button><i aria-hidden="true">/</i><b>{{title}}</b><span v-if="user.holding_role==='founder'" class="pill plan ro-badge">{{tx('Только просмотр')}}</span></nav>
    <div class="topbar-r"><LanguageSwitch compact/><button class="icon-btn" :aria-label="tx('Справка')" @click="helpOpen=true"><AppIcon name="help"/></button></div>
   </header>
   <main class="page" :key="page">
    <div class="pg-head">
     <div><h1>{{title}}</h1><p v-if="subtitles[page]" class="sub">{{tx(subtitles[page])}}</p></div>
     <div class="pg-act">
      <div v-if="showCurrency" class="seg" role="group" :aria-label="tx('Валюта учёта')"><button v-for="c in ['UZS','USD','EUR']" :key="c" :class="{on:currency===c}" :aria-pressed="currency===c" @click="setCurrency(c)">{{c}}</button></div>
      <button v-if="page!=='profile'" class="icon-btn" :aria-label="tx('Обновить данные')" :title="tx('Обновить данные')" @click="load"><AppIcon name="refresh"/></button>
      <button class="secondary" @click="helpOpen=true"><AppIcon name="help"/> {{tx('Справка')}}</button>
     </div>
    </div>
    <p v-if="notice" class="notice" role="status">{{tx(notice,noticeParams)}}</p>
    <p v-if="error" class="error" role="alert">{{tx(error)}}</p>
    <div v-if="busy" class="loading">{{tx('Загрузка данных…')}}</div>

    <!-- Обзор -->
    <OverviewPage v-if="page==='home'&&has('view')" :key="companyId" :api="api" :company-id="companyId" :company="company" :currency="currency" :today="boot.today" :refresh="homeRefresh" :has="has" :can-decide="canDecide" :days="days" :track="trkSteps" :marks="MK" @update:days="d=>days=d" @go="goWith" @edit-reserve="v=>reserve(v)"/>

    <nav v-if="isReportPage&&reportTabs.length>1" class="report-tabs" :aria-label="tx('Отчёты')"><button v-for="n in reportTabs" :key="n[0]" type="button" :class="{on:page===n[0]}" :aria-current="page===n[0]?'page':undefined" @click="go(n[0])"><AppIcon :name="n[0]"/> {{tx(n[2])}}</button></nav>
    <BusinessProjects v-if="page==='business'&&has('export')" :key="companyId" :api="api" :company="company" :refresh="businessRefresh" @editing="reportEditing=$event" @generated="reportArchiveRefresh++"/>
    <ReportArchive v-if="page==='reports'&&has('export')" :key="companyId" :api="api" :company="company" :refresh="reportArchiveRefresh" @editing="reportEditing=$event"/>

    <!-- Банк и касса -->
    <template v-if="page==='accounts'">
     <div class="tb"><div class="seg" role="group" :aria-label="tx('Показать счета')"><button :class="{on:!showArchive}" @click="showArchive=false;load()">{{tx('Активные')}}</button><button :class="{on:showArchive}" @click="showArchive=true;load()">{{tx('Архив')}}</button></div><span class="sub"><b>{{visibleRows.length}}</b> · {{currency}}</span><div class="grow"><button v-if="has('write')" @click="account()"><AppIcon name="plus"/> {{tx('Добавить счёт')}}</button></div></div>
     <div class="acc-grid"><section v-for="a in visibleRows" :key="a.id" class="card ac-card" :class="{arch:a.archived}">
      <div class="ac-top"><span class="ic"><AppIcon :name="a.kind==='bank'?'accounts':'cash'"/></span><div><b>{{a.name}}</b><small>{{a.kind==='bank'?tx('Банковский счёт'):tx('Касса')}}{{a.allow_overdraft?' · '+tx('овердрафт разрешён'):''}}</small></div><span class="pill" :class="a.archived?'warn':'fact'">{{a.archived?tx('Архив')+' · ':''}}{{a.currency}}</span></div>
      <div class="ac-l">{{tx('Текущий остаток')}}</div><div class="ac-v num" :title="moneyTitle(a.balance,a.currency)">{{moneyShort(a.balance,a.currency)}}<small>{{a.currency}}</small></div><div class="ac-x num">{{money(a.balance)}} {{a.currency}}</div>
      <div class="ac-o"><span>{{tx('Начальный остаток на {date}',{date:a.opening_date})}}</span><b class="num">{{money(a.opening)}} {{a.currency}}</b></div>
      <div class="ac-b"><button v-if="has('write')&&!a.archived" class="secondary tiny" @click="account(a)"><AppIcon name="edit"/> {{tx('Изменить')}}</button><button v-if="has('users')&&has('write')" class="secondary tiny" @click="archiveAccount(a)"><AppIcon :name="a.archived?'restore':'archive'"/> {{a.archived?tx('Восстановить'):tx('В архив')}}</button></div>
     </section></div>
     <section v-if="!visibleRows.length&&!busy&&!showArchive" class="empty-state card"><div class="empty-icon" aria-hidden="true"><AppIcon name="accounts"/></div><h2>{{tx('Добавьте первый счёт в {currency}',{currency})}}</h2><p>{{tx('Укажите банк или кассу и начальный остаток. Дальше баланс будет рассчитываться по операциям.')}}</p><div class="actions"><button v-if="has('write')" @click="account()"><AppIcon name="plus"/> {{tx('Добавить счёт')}}</button><button v-for="c in otherCurrencies" :key="c" class="secondary" @click="switchCurrency(c)">{{tx('Показать {currency}',{currency:c})}}</button></div></section>
     <section v-if="!visibleRows.length&&!busy&&showArchive" class="empty card"><b>{{tx('В архиве пусто')}}</b><br>{{tx('Архивные счета можно восстановить в любой момент.')}}</section>
    </template>

    <!-- Операции -->
    <template v-if="page==='ledger'">
     <PeriodFilter :key="page" :date-from="dateFrom" :date-to="dateTo" @apply="applyPeriod"><label class="search"><AppIcon name="search"/><input v-model="search" :placeholder="tx('Поиск: контрагент, статья, документ')" :aria-label="tx('Поиск операции')"></label><div class="grow actions"><a v-if="has('export')" class="button secondary" :href="companyUrl('/api/export/ledger.csv')"><AppIcon name="download"/> CSV</a><button v-if="has('write')&&user.role!=='cashier'" @click="transaction()"><AppIcon name="plus"/> {{tx('Новая операция')}}</button></div></PeriodFilter>
     <div v-if="visibleRows.length" class="table-card rsp-wrap"><table class="rsp"><thead><tr><th>{{tx('Дата / документ')}}</th><th>{{tx('Операция')}}</th><th>{{tx('Контрагент / статья')}}</th><th>{{tx('Счёт')}}</th><th class="num">{{tx('Сумма')}}</th><th>{{tx('Документы')}}</th><th></th></tr></thead><tbody>
      <tr v-for="r in visibleRows" :key="r.id"><td :data-l="tx('Дата')">{{r.date}}<small>{{r.reference}}</small></td><td :data-l="tx('Операция')"><span class="pill" :class="{in:'good',out:'bad',transfer:'plan'}[r.kind]">{{{in:tx('Поступление'),out:tx('Расход'),transfer:tx('Перевод')}[r.kind]}}</span><small v-if="r.reversed||r.reversal_of">{{tx('Сторно')}}</small></td><td :data-l="tx('Контрагент')">{{r.counterparty||tx('Между своими счетами')}}<small>{{r.category}}</small></td><td :data-l="tx('Счёт')">{{r.account}}<template v-if="r.to_account"> → {{r.to_account}}</template></td><td class="num" :data-l="tx('Сумма')"><b :class="{pos:r.kind==='in'}">{{r.kind==='in'?'+':r.kind==='out'?'−':''}}{{money(r.amount)}}</b> {{r.currency}}</td><td :data-l="tx('Документы')"><button class="secondary tiny" @click="docs(r)"><AppIcon name="file"/> {{tx('Документы')}}</button></td><td data-l=""><button v-if="has('budget')&&!r.reversed&&!r.reversal_of" class="ghost tiny" @click="reverse(r)">{{tx('Сторно')}}</button></td></tr>
     </tbody></table></div>
     <section v-else-if="!busy" class="empty-state card"><div class="empty-icon" aria-hidden="true"><AppIcon name="ledger"/></div><h2>{{search?tx('Ничего не найдено'):tx('Пока нет операций в {currency}',{currency})}}</h2><p>{{search?tx('Попробуйте другой запрос или сбросьте поиск.'):!has('write')?tx('Операции появятся после первых поступлений, расходов или переводов по вашим счетам.'):currentAccounts.length?tx('Добавьте поступление, расход или перевод между счетами.'):tx('Сначала добавьте счёт в этой валюте и укажите начальный остаток.')}}</p><div class="actions"><button v-if="search" class="secondary" @click="search=''">{{tx('Сбросить поиск')}}</button><button v-else-if="has('write')" @click="currentAccounts.length?transaction():go('accounts')">{{currentAccounts.length?tx('Новая операция'):tx('Перейти к счетам')}}</button><button v-if="!search" v-for="c in otherCurrencies" :key="c" class="secondary" @click="switchCurrency(c)">{{tx('Показать {currency}',{currency:c})}}</button></div></section>
    </template>

    <!-- Заявки -->
    <template v-if="page==='requests'">
     <PeriodFilter :key="page" :date-from="dateFrom" :date-to="dateTo" @apply="applyPeriod"><select :aria-label="tx('Статус заявки')" v-model="requestStatus" @change="listPage=1;load()"><option value="">{{tx('Статус: все')}}</option><option v-for="(label,key) in requestStatuses" :key="key" :value="key">{{tx(label)}}</option></select><label class="search"><AppIcon name="search"/><input v-model="search" :placeholder="tx('Поиск заявки')" :aria-label="tx('Поиск заявки')"></label><div class="grow actions"><button v-if="has('request')" @click="requestForm()"><AppIcon name="plus"/> {{tx('Создать заявку')}}</button></div></PeriodFilter>
     <p class="sumline"><span>{{tx('Найдено')}} <b>{{listTotal}}</b></span><span>{{listTotal<=4?tx('До 4 заявок — карточки'):tx('С 5 заявок — таблица со страницами')}}</span></p>
     <div v-if="listTotal<=4&&visibleRows.length" class="rq-grid"><section v-for="r in visibleRows" :key="r.id" class="card rq-card">
      <div class="rq-top"><span>{{r.number}}</span><span class="pill" :class="statusClass(r)">{{stageLabel(r)}}</span></div>
      <h3>{{r.counterparty}}</h3><p class="cp">{{r.purpose}}</p>
      <div class="amt num">{{money(r.amount)}}<small>{{r.currency}}</small></div>
      <div class="meta"><span>{{tx('до {date}',{date:r.date})}}</span><span>{{r.account}}</span><span>{{tx(priorities[r.priority])}}</span></div>
      <div class="trk"><template v-for="(s,i) in trkSteps(r)" :key="i"><span v-if="i" class="ln"></span><span class="st" :class="s.c==='skip'?'':s.c"><u>{{MK[s.c]}}</u>{{s.l}}</span></template></div>
      <p v-if="r.decision_note" class="note">{{r.decision_note}}</p><p v-if="r.budget_card?.budget_set" class="note">{{tx('Доступно по бюджету статьи: {amount} {currency}',{amount:money(r.budget_card.available),currency:r.currency})}}</p>
      <div class="actions"><button class="secondary tiny" @click="openRequest(r)">{{tx('Открыть заявку')}}</button><button v-if="canPayRequest(r)" class="tiny" @click="transaction(r)">{{tx('Факт оплаты')}}</button></div>
     </section></div>
     <div v-if="listTotal>4" class="table-card rsp-wrap"><table class="rsp"><thead><tr><th>{{tx('Заявка')}}</th><th class="num">{{tx('Сумма')}}</th><th>{{tx('Оплатить до')}}</th><th>{{tx('Этапы')}}</th><th>{{tx('Статус')}}</th><th></th></tr></thead><tbody><template v-for="r in visibleRows" :key="r.id">
      <tr><td :data-l="tx('Заявка')"><b class="nowrap">{{r.number}}</b>&nbsp;· {{r.counterparty}}<small>{{r.account}}</small></td><td class="num" :data-l="tx('Сумма')"><b>{{money(r.amount)}}</b> {{r.currency}}</td><td :data-l="tx('Оплатить до')">{{r.date}}</td><td :data-l="tx('Этапы')"><span class="mini"><template v-for="(s,i) in trkSteps(r)" :key="i"><i v-if="i"></i><u :class="s.c" :title="s.l">{{[tx('З'),tx('П'),tx('Ф'),tx('Д'),tx('О')][i]}}</u></template></span></td><td :data-l="tx('Статус')"><span class="pill" :class="statusClass(r)">{{stageLabel(r)}}</span></td><td data-l=""><div class="actions"><button class="secondary tiny" @click="openRequest(r)">{{tx('Открыть')}}</button><button v-if="canPayRequest(r)" class="tiny" @click="transaction(r)">{{tx('Факт оплаты')}}</button></div></td></tr>
     </template></tbody></table></div>
     <section v-if="!visibleRows.length&&!busy" class="empty card"><b>{{tx('Заявок не найдено')}}</b><br>{{tx('Измените период, статус или поисковый запрос.')}}</section>
    </template>
    <div v-if="['ledger','requests'].includes(page)&&listPages>1" class="pagination"><span>{{tx('Найдено: {total} · Страница {page} из {pages}',{total:listTotal,page:listPage,pages:listPages})}}</span><div class="actions"><button class="secondary tiny" :disabled="listPage<=1||busy" @click="changeListPage(listPage-1)">← {{tx('Назад')}}</button><button class="secondary tiny" :disabled="listPage>=listPages||busy" @click="changeListPage(listPage+1)">{{tx('Далее')}} →</button></div></div>

    <!-- Календарь -->
    <template v-if="page==='calendar'&&dash">
     <div class="tb"><select v-model="scenario" :aria-label="tx('Сценарий')"><option value="approved">{{tx('Сценарий: утверждённые оплаты')}}</option><option value="all">{{tx('Сценарий: с заявками на согласовании')}}</option></select><div class="seg" role="group" :aria-label="tx('Период')"><button v-for="d in [7,30,90]" :key="d" :class="{on:days===d}" :aria-pressed="days===d" @click="days=d;load()">{{tx('{n} дн.',{n:d})}}</button></div><select v-model="calendarOrder" :aria-label="tx('Сортировка по дате')"><option value="asc">{{tx('Сначала ближайшие')}}</option><option value="desc">{{tx('Сначала поздние')}}</option></select><div class="grow actions"><button class="secondary" :aria-expanded="receiptsOpen" @click="showReceipts">{{tx('Ожидаемые поступления')}}</button><button v-if="has('schedule')" @click="receipt"><AppIcon name="plus"/> {{tx('План поступления')}}</button></div></div>
     <div v-if="calTiles" class="sumrow">
      <div class="card tile"><div class="k">{{tx('Поступления')}}</div><div class="v num" :class="{pos:calTiles.inc>0}" :title="(calTiles.inc>0?'+':'')+moneyTitle(calTiles.inc.toFixed(2))">{{calTiles.inc>0?'+':''}}{{moneyShort(calTiles.inc.toFixed(2))}}<small>{{currency}}</small></div><div class="d"><span class="num">{{calTiles.inc>0?'+':''}}{{money(calTiles.inc.toFixed(2))}} {{currency}}</span> · {{tx('за выбранный период')}}</div></div>
      <div class="card tile"><div class="k">{{tx('Платежи')}}</div><div class="v num" :title="(calTiles.out>0?'−':'')+moneyTitle(calTiles.out.toFixed(2))">{{calTiles.out>0?'−':''}}{{moneyShort(calTiles.out.toFixed(2))}}<small>{{currency}}</small></div><div class="d"><span class="num">{{calTiles.out>0?'−':''}}{{money(calTiles.out.toFixed(2))}} {{currency}}</span> · {{scenario==='all'?tx('с заявками на согласовании'):tx('утверждённые')}}</div></div>
      <div class="card tile"><div class="k">{{tx('Минимальный остаток')}}</div><div class="v num" :class="{neg:calTiles.low.risk}" :title="moneyTitle(calTiles.low.balance)">{{moneyShort(calTiles.low.balance)}}<small>{{currency}}</small></div><div class="d"><span class="num">{{money(calTiles.low.balance)}} {{currency}}</span> · {{calTiles.low.date}} · {{tx('резерв {amount}',{amount:money(dash.reserve)})}}</div></div>
      <div class="card tile"><div class="k">{{tx('Остаток на конец периода')}}</div><div class="v num" :title="moneyTitle(calTiles.end.balance)">{{moneyShort(calTiles.end.balance)}}<small>{{currency}}</small></div><div class="d"><span class="num">{{money(calTiles.end.balance)}} {{currency}}</span> · {{calTiles.end.date}}</div></div>
     </div>
     <section v-if="receiptsOpen" class="card" style="margin-bottom:16px"><div class="card-h"><h3>{{tx('Ожидаемые поступления · {currency}',{currency})}}</h3><span class="pill plan">{{tx('План')}}</span></div><div class="card-b"><p v-if="receiptsLoading" role="status">{{tx('Загрузка…')}}</p><div v-else-if="!receipts.length" class="empty" style="padding:22px 10px"><b style="display:block;color:var(--ink);font-size:16px;margin-bottom:4px">{{tx('Ожидаемых поступлений нет')}}</b>{{tx('Добавьте план поступления, чтобы он попал в прогноз.')}}<div v-if="has('schedule')" style="margin-top:14px"><button @click="receipt"><AppIcon name="plus"/> {{tx('План поступления')}}</button></div></div><div v-for="r in receipts" :key="r.id" class="receipt-row"><span>{{r.counterparty}}<small>{{r.date}}</small></span><strong class="num">{{money(r.amount)}} {{r.currency}}</strong><button v-if="has('write')" class="tiny" @click="transaction(r,true)">{{tx('Подтвердить приход')}}</button></div></div></section>
     <div class="table-card rsp-wrap"><table class="rsp"><thead><tr><th><button class="ghost tiny" style="font-weight:800;color:var(--em)" @click="calendarOrder=calendarOrder==='asc'?'desc':'asc'" :aria-label="tx('Сменить порядок по дате')">{{tx('Дата')}} {{calendarOrder==='asc'?'↑':'↓'}}</button></th><th>{{tx('События')}}</th><th class="num">{{tx('На начало дня')}}</th><th class="num">{{tx('Поступления')}}</th><th class="num">{{tx('Платежи')}}</th><th class="num">{{tx('Остаток')}}</th></tr></thead><tbody><tr v-for="d in sortedForecast" :key="d.date" :class="{risk:d.risk}"><td :data-l="tx('Дата')">{{d.date}}</td><td :data-l="tx('События')"><div v-for="(e,i) in d.events" :key="i">{{e.kind==='in'?'↓':e.kind==='pending'?'◌':'↑'}} {{e.name}} {{e.kind==='pending'?'· '+tx('на согласовании'):''}} {{e.priority&&e.priority!=='normal'?'· '+tx(priorities[e.priority]):''}} {{e.overdue?'· '+tx('просрочка'):''}}</div><span v-if="!d.events.length" class="muted">{{tx('Событий нет')}}</span></td><td class="num" :data-l="tx('На начало дня')">{{money(d.opening)}}</td><td class="num pos" :data-l="tx('Поступления')">{{Number(d.incoming)?'+'+money(d.incoming):'—'}}</td><td class="num" :data-l="tx('Платежи')">{{Number(d.outgoing)?'−'+money(d.outgoing):'—'}}</td><td class="num" :data-l="tx('Остаток')"><b :class="{neg:d.risk}">{{money(d.balance)}}</b><small v-for="a in d.account_shortfalls" :key="a.account_id">{{a.account}}: {{money(a.balance)}}</small><span v-if="d.risk" class="pill warn" style="margin-left:6px">{{tx('ниже резерва')}}</span></td></tr></tbody></table></div>
    </template>

    <!-- Бюджеты -->
    <template v-if="page==='budgets'">
     <div class="tb"><label class="fld">{{tx('Месяц')}} <input v-model="month" type="month" @change="load" :aria-label="tx('Месяц бюджета')"></label><div class="chips" role="group" :aria-label="tx('Группа затрат')"><button class="chip" :class="{on:budgetGroup===''}" @click="budgetGroup=''">{{tx('Все')}} · {{budgetCounts['']}}</button><button v-for="(name,key) in costGroups" :key="key" class="chip" :class="{on:budgetGroup===key}" @click="budgetGroup=key">{{tx(name)}} · {{budgetCounts[key]}}</button></div></div>
     <div v-if="budgetRows.length" class="table-card rsp-wrap"><table class="rsp"><thead><tr><th>{{tx('Статья')}}</th><th class="num">{{tx('Лимит')}}</th><th class="num">{{tx('Оплачено')}}</th><th class="num">{{tx('Резерв')}}</th><th class="num">{{tx('Остаток')}}</th><th>{{tx('Использовано')}}</th><th>{{tx('Контроль')}}</th><th></th></tr></thead><tbody><tr v-for="r in budgetRows" :key="r.category_id"><td :data-l="tx('Статья')"><b>{{r.category}}</b><small>{{tx(costGroups[r.cost_group])}}</small></td><td class="num" :data-l="tx('Лимит')">{{r.limit==null?tx('Не задан'):money(r.limit)}}</td><td class="num" :data-l="tx('Оплачено')">{{money(r.spent)}}</td><td class="num" :data-l="tx('Резерв')">{{Number(r.reserved)?money(r.reserved):'—'}}</td><td class="num" :data-l="tx('Остаток')"><b :class="Number(r.remaining)<0?'neg':'pos'">{{r.limit==null?'—':money(r.remaining)}}</b><span v-if="r.limit!=null&&Number(r.remaining)<0" class="pill bad" style="margin-left:6px">{{tx('превышение')}}</span></td><td :data-l="tx('Использовано')"><div v-if="r.limit!=null" class="ub" :title="Math.round(usage(r)*100)+'%'"><i :class="usage(r)>1?'b':usage(r)>.85?'w':''" :style="{width:Math.min(100,usage(r)*100)+'%'}"></i></div><span v-else class="muted">—</span></td><td :data-l="tx('Контроль')" style="white-space:normal;max-width:210px;font-size:12.5px;font-weight:700">{{r.limit==null?tx('Лимит не задан'):r.mode==='hard'?tx('Запрещать превышение'):tx('Разрешать превышение с обоснованием')}}</td><td data-l=""><button v-if="has('budget')" class="secondary tiny" @click="budget(r)"><AppIcon name="edit"/> {{tx('Изменить')}}</button></td></tr></tbody></table></div>
     <section v-else-if="!busy" class="empty card"><b>{{tx('Статей не найдено')}}</b><br>{{tx('В выбранной группе нет статей.')}}</section>
    </template>

    <!-- Импорт -->
    <template v-if="page==='import'">
     <div class="seg" role="group" :aria-label="tx('Тип импорта')" style="margin-bottom:14px"><button :class="{on:importMode==='operations'}" @click="importMode='operations';preview=null">{{tx('Фактические операции')}}</button><button :class="{on:importMode==='plans'}" @click="importMode='plans';preview=null">{{tx('Планы расходов А / Б / В')}}</button></div>
     <ol class="steps" :aria-label="tx('Шаги импорта')"><li class="step" :class="importStep>1?'done':'on'"><u>{{importStep>1?'✓':'1'}}</u>{{tx('Файл')}}</li><li class="step" :class="importStep===2?'on':''"><u>2</u>{{tx('Проверка')}}</li><li class="step"><u>3</u>{{tx('Подтверждение')}}</li></ol>
     <section v-if="!preview&&importMode==='operations'" class="drop"><div class="ic"><AppIcon name="import"/></div><b>{{tx('Загрузите CSV или Excel')}}</b><span class="sub">{{tx('До 5 МБ и 1000 фактических операций. Плановые суммы сюда не загружаются.')}}</span><div class="actions" style="justify-content:center;margin-top:16px"><a class="button secondary" :href="companyUrl('/api/import/template.csv')"><AppIcon name="download"/> {{tx('Шаблон CSV')}}</a><label class="button"><AppIcon name="file"/> {{tx('Выбрать файл')}}<input type="file" accept=".csv,.xlsx" @change="upload" :disabled="busy" hidden></label></div></section>
     <section v-if="!preview&&importMode==='plans'" class="card"><div class="card-h"><div><h3>{{tx('Книга Cash Flow · планы расходов')}}</h3><p class="sub">{{tx('Читается лист «{sheet}». Заголовки А, Б, В определяются по тексту, пустые ячейки не стирают текущий план.',{sheet:PLAN_SHEET})}}</p></div><span class="pill plan">{{tx('Только UZS')}}</span></div><div class="card-b"><div class="filter-bar"><span class="pill good">{{company?.name}}</span><label>{{tx('Год')}}<input v-model="year" type="number" min="2000" max="2100"></label><label>{{tx('С месяца')}}<select v-model="planMonthFrom"><option v-for="n in 12" :key="n" :value="n">{{tx(MS[n-1])}}</option></select></label><label>{{tx('По месяц')}}<select v-model="planMonthTo"><option v-for="n in 12" :key="n" :value="n">{{tx(MS[n-1])}}</option></select></label><label class="button"><AppIcon name="file"/> {{tx('Проверить XLSX')}}<input type="file" accept=".xlsx" @change="uploadPlan" :disabled="busy" hidden></label></div><p class="sub">{{tx('Предпросмотр ничего не записывает. До подтверждения можно сопоставить каждую статью или пропустить её.')}}</p></div></section>
     <section v-else-if="preview&&preview.type!=='plan'" class="card"><div class="card-h"><div><h3>{{tx('Проверено строк: {n}',{n:preview.count})}}</h3><p class="sub" style="margin-top:2px">{{preview.errors.length?tx('Есть ошибки — исправьте файл и загрузите его снова'):tx('Ошибок не найдено, можно подтверждать')}}</p></div><span class="pill" :class="preview.errors.length?'bad':'good'">{{preview.errors.length?tx('Ошибок: {n}',{n:preview.errors.length}):tx('Готово к импорту')}}</span></div><div class="card-b"><p v-for="(e,i) in preview.errors" :key="i" class="error">{{tx('Строка {line}',{line:e.line})}}: {{tx(e.error)}}</p><div class="table-scroll"><table><thead><tr><th>{{tx('Дата')}}</th><th>{{tx('Документ')}}</th><th>{{tx('Контрагент')}}</th><th class="num">{{tx('Сумма')}}</th></tr></thead><tbody><tr v-for="(r,i) in preview.rows" :key="i"><td>{{r.date}}</td><td>{{r.reference}}</td><td>{{r.counterparty}}</td><td class="num">{{money(r.amount)}}</td></tr></tbody></table></div><div class="form-actions"><button class="secondary" @click="preview=null">{{tx('Загрузить другой файл')}}</button><button v-if="preview.can_commit" @click="commit" :disabled="busy">{{tx('Подтвердить импорт {n} операций',{n:preview.rows.length})}}</button></div><p v-if="!preview.can_commit" class="sub">{{tx('Операции не записаны.')}}</p></div></section>
     <section v-else-if="preview?.type==='plan'" class="card"><div class="card-h"><div><h3>{{tx('Проверено строк планов: {n}',{n:preview.rows.length})}}</h3><p class="sub">{{tx('Месяцы: {months}. Это предпросмотр — фактические операции не создаются.',{months:preview.months.join(', ')})}}</p></div><span class="pill" :class="preview.issues.length?'bad':'good'">{{preview.issues.length?tx('Замечаний: {n}',{n:preview.issues.length}):tx('Готово к подтверждению')}}</span></div><div class="card-b"><p v-for="(e,i) in preview.issues" :key="i" class="error">{{tx('Строка {line}',{line:e.line})}}<template v-if="e.scenario"> · {{tx(e.scenario)}}</template>: {{tx(e.error)}}</p><div class="table-scroll"><table><thead><tr><th>{{tx('Месяц')}}</th><th>{{tx('Код / статья')}}</th><th class="num">{{tx('А')}}</th><th class="num">{{tx('Б')}}</th><th class="num">{{tx('В')}}</th><th>{{tx('Изменение')}}</th><th>{{tx('Сопоставление')}}</th></tr></thead><tbody><tr v-for="r in preview.rows" :key="r.month+r.source_key"><td>{{tx(MS[r.month-1])}}</td><td><b>{{r.source_code}}</b><small>{{r.source_name}}</small></td><td class="num">{{r.amounts.A==null?tx('пусто'):money((r.amounts.A/100).toFixed(2))}}</td><td class="num">{{r.amounts.B==null?tx('пусто'):money((r.amounts.B/100).toFixed(2))}}</td><td class="num">{{r.amounts.V==null?tx('пусто'):money((r.amounts.V/100).toFixed(2))}}</td><td><small>{{tx('А')}}: {{r.effects.A==='keep'?tx('без изменения'):r.effects.A==='same'?tx('совпадает'):r.effects.A==='change'?tx('обновится'):tx('новое')}}<br>{{tx('Б')}}: {{r.effects.B==='keep'?tx('без изменения'):r.effects.B==='same'?tx('совпадает'):r.effects.B==='change'?tx('обновится'):tx('новое')}}<br>{{tx('В')}}: {{r.effects.V==='keep'?tx('без изменения'):r.effects.V==='same'?tx('совпадает'):r.effects.V==='change'?tx('обновится'):tx('новое')}}</small></td><td><select v-model="planMappings[r.source_key]"><option value="new">{{tx('Создать статью')}}</option><option value="skip">{{tx('Пропустить')}}</option><option v-for="c in preview.categories" :key="c.id" :value="String(c.id)">{{c.name}}</option></select></td></tr></tbody></table></div><label class="plan-reason">{{tx('Основание импорта')}}<textarea v-model="planReason" minlength="10" maxlength="1000"></textarea></label><div class="form-actions"><button class="secondary" @click="preview=null">{{tx('Загрузить другой файл')}}</button><button v-if="preview.can_commit" @click="commitPlan" :disabled="busy||planReason.length<10">{{tx('Подтвердить планы')}}</button></div><p class="sub">{{tx('Явный ноль обновляет план до нуля. Пустая ячейка оставляет прежнее значение без изменения.')}}</p></div></section>
     <section class="card future-module"><h3>{{tx('Финансовая модель')}}</h3><span class="pill gray">{{tx('Позже')}}</span></section>
    </template>

    <!-- Cash Flow -->
    <CashFlowReport :key="companyId" v-if="page==='report'&&report" :report="report" :currency="currency" :year="year" :api="api" :can-plan="has('plan')" @editing="reportEditing=$event" :companies="boot.companies.filter(c=>c.id===companyId)" :company-id="companyId" :scenario="reportScenario" :as-of="reportAsOf" :today="boot.today" @company="selectCompany" @scenario="v=>{reportScenario=v;load()}" @year="changeReportYear" @as-of="changeReportDate" @refresh="load" />

    <!-- Справочники -->
    <template v-if="page==='categories'">
     <div class="tb"><span class="sub">{{tx('Статьи движения денег:')}} <b>{{rows.length}}</b></span><div class="grow"><button @click="category()"><AppIcon name="plus"/> {{tx('Добавить статью')}}</button></div></div>
     <div class="table-card rsp-wrap"><table class="rsp"><thead><tr><th>{{tx('Название статьи')}}</th><th>{{tx('Направление')}}</th><th>{{tx('Вид деятельности')}}</th><th></th></tr></thead><tbody><tr v-for="r in rows" :key="r.id"><td :data-l="tx('Статья')"><b>{{r.name}}</b></td><td :data-l="tx('Направление')"><span class="pill" :class="r.type==='income'?'good':'plan'">{{r.type==='income'?tx('Поступление'):tx('Выплата')}}</span></td><td :data-l="tx('Вид деятельности')">{{{operating:tx('Операционная'),investing:tx('Инвестиционная'),financing:tx('Финансовая')}[r.activity]}}</td><td data-l=""><button class="secondary tiny" @click="category(r)"><AppIcon name="edit"/> {{tx('Изменить')}}</button></td></tr></tbody></table></div>
    </template>

    <!-- Согласование и пользователи -->
    <PolicyPage v-if="page==='approval'&&has('approval_policy')" :key="companyId" :api="api" :money="money" :can-edit-calendar="has('users')"/>
    <AdminCenter v-if="page==='users'&&user.holding_role==='admin'" :api="api" :current-user-id="user.id"/>
    <Delegations v-if="page==='users'&&has('users')" :key="companyId" :api="api" :roles="boot.roles" :company="company" :today="boot.today" :current-user-id="user.id"/>
     <section v-if="page==='users'&&user.holding_role==='admin'" class="card tg-groups" aria-labelledby="tg-groups-title"><div class="card-h"><h3 id="tg-groups-title">{{tx('Telegram-группы сводки')}}</h3></div><div class="card-b">
      <p class="sub">{{tx('Каждая группа получает сводку только одной компании. Сводку видит каждый участник группы — отключите группу, если в ней есть посторонние. Чтобы сменить компанию, отключите группу и подключите заново.')}}</p>
      <p v-if="tgGroups&&!tgGroups.length" class="tg-empty">{{tx('Групп нет. Укажите ID группы и её компанию в форме подключения выше, затем отправьте полученную команду /register в эту группу.')}}</p>
      <div v-else-if="tgGroupRows" class="table-scroll rsp-wrap"><table class="rsp"><thead><tr><th>{{tx('Группа')}}</th><th>{{tx('Компания')}}</th><th>{{tx('Подключил')}}</th><th></th></tr></thead><tbody><tr v-for="g in tgGroupRows" :key="g.chat_id"><td :data-l="tx('Группа')"><span><b>{{g.title||tx('Без названия')}}</b><small>ID {{g.chat_id}}</small></span></td><td :data-l="tx('Компания')"><span>{{g.companyInfo.label}}<small v-if="!g.companyInfo.summaryEnabled">{{tx('Сводка не отправляется.')}}</small></span></td><td :data-l="tx('Подключил')"><span v-if="g.source==='config'" class="pill plan">{{tx('Настройки сервера')}}</span><span v-else>{{g.added_by||'—'}}<small v-if="g.added_at">{{tgDate(g.added_at)}}</small></span></td><td data-l=""><button v-if="g.can_remove" class="secondary tiny" @click="removeTelegramGroup(g)">{{tx('Отключить')}}</button></td></tr></tbody></table></div>
     </div></section>
    <template v-if="page==='users'&&has('approval_policy')">
     <section class="card route-steps"><div class="card-b"><h3>{{tx('Порядок согласования и оплаты')}}</h3><ol class="steps-list" style="margin-top:12px"><li><span class="n">1</span><div><b>{{tx('Расчётный бухгалтер проверяет реквизиты и комплектность')}}</b><small>{{tx('Внутренняя заявка (индент) и договор или счёт обязательны до отправки. Срок оплаты — не раньше 7 рабочих дней, при высоком приоритете — 3, срочная — 1.')}}</small></div></li><li><span class="n">2</span><div><b>{{tx('Финансовый директор проверяет бюджет и дату')}}</b><small>{{tx('Автор и последний редактор заявку не согласуют; один сотрудник не проходит два этапа. Вернуть на доработку или закрыть без оплаты можно только с комментарием.')}}</small></div></li><li><span class="n">3</span><div><b>{{tx('Директор — по политике статьи')}}</b><small>{{tx('Всегда, выше порога валюты или не участвует (только регулярные статьи из утверждённого перечня). Настройка — в разделе «Согласование».')}}</small></div></li><li><span class="n">4</span><div><b>{{tx('Оплата')}}</b><small>{{tx('Банк — расчётный бухгалтер, касса — кассир. Проверявший бухгалтер, автор, редактор и согласующие заявку не оплачивают.')}}</small></div></li></ol></div></section>
    </template>

    <!-- Журнал -->
    <AuditHistory :key="companyId" v-if="page==='audit'" :api="api" :refresh="auditRefresh" />

    <!-- Профиль -->
    <div v-if="page==='profile'" class="prof">
     <section class="card"><div class="card-b"><div class="who-l"><span class="ava">{{initials(user.name)}}</span><div><b style="font-size:17px">{{user.name}}</b><small>{{user.username}}</small></div></div><div class="kv"><div><small>{{tx('Имя')}}</small><b>{{user.name}}</b></div><div><small>{{tx('Логин')}}</small><b>{{user.username}}</b></div><div><small>{{tx('Роль')}}</small><b>{{tx(user.role_label)}}</b></div><div><small>{{tx('Сессия')}}</small><b>{{tx('60 минут')}}</b></div></div><div class="actions"><button class="secondary" @click="logout">{{tx('Выйти из системы')}}</button><button class="ghost" @click="showRelease=true">{{tx('Версия {version} · Что нового',{version:release.version})}}</button></div></div></section>
     <section class="card"><div class="card-h"><h3>{{tx('Пароль')}}</h3></div><div class="card-b"><p class="sub" style="margin-bottom:14px">{{tx('Пароль не короче 12 символов. После смены пароля все сессии отзываются.')}}</p><button @click="changePassword"><AppIcon name="lock"/> {{tx('Изменить пароль')}}</button></div></section>
     <section class="card tg-card" aria-labelledby="tg-title"><div class="card-h"><h3 id="tg-title">Telegram</h3></div><div class="card-b">
      <p class="tg-state" role="status">{{tgState}}</p>
      <div v-if="!tg&&!busy" class="actions"><button class="secondary" :disabled="tgBusy" @click="checkTelegram"><AppIcon name="refresh"/> {{tx('Повторить')}}</button></div>
      <template v-else-if="tg?.linked">
       <p class="sub">{{tx('В боте нажмите «📋 Мои заявки» — там заявки, которые ждут вашего действия.')}}</p>
       <div class="actions"><a v-if="tgBotLink" class="button" :href="tgBotLink" target="_blank" rel="noopener noreferrer"><AppIcon name="arrow"/> {{tx('Открыть бота')}}</a><button class="secondary" :disabled="tgBusy||busy" @click="unlinkTelegram">{{tx('Отвязать')}}</button></div>
      </template>
      <template v-else-if="tg">
       <p class="sub">{{tx('Бот присылает напоминания по заявкам, которые ждут вашего действия. Привязка делается одноразовым кодом.')}}</p>
       <div v-if="tgCode" class="tg-link"><input class="tg-code" :value="tgCode.code" readonly :aria-label="tx('Одноразовый код привязки Telegram')" @focus="$event.target.select()"><p>{{tg.bot_username?tx('Отправьте боту @{bot} команду',{bot:tg.bot_username}):tx('Отправьте боту команду')}} <code>/start {{tgCode.code}}</code></p><p class="sub">{{tx('Код действует {n} мин и только один раз. Новый код отменяет прежний.',{n:tgCode.minutes})}}</p></div>
       <div class="actions"><template v-if="tgCode"><a v-if="tgCode.link" class="button" :href="tgCode.link" target="_blank" rel="noopener noreferrer"><AppIcon name="arrow"/> {{tx('Открыть бота')}}</a><button :class="{secondary:!!tgCode.link}" :disabled="tgBusy||busy" @click="checkTelegram"><AppIcon name="check"/> {{tx('Проверить привязку')}}</button><button class="secondary" @click="copyTelegramCommand">{{tx('Скопировать команду')}}</button><button class="ghost" :disabled="tgBusy||busy" @click="telegramCode">{{tx('Новый код')}}</button></template><button v-else :disabled="tgBusy||busy" @click="telegramCode"><AppIcon name="key"/> {{tx('Привязать Telegram')}}</button></div>
      </template>
     </div></section>
    </div>
   </main>
  </div>
 </div>

 <!-- Справка -->
 <template v-if="user&&helpOpen">
  <div class="scrim" @click="helpOpen=false"></div>
  <section class="drawer" role="dialog" aria-modal="true" aria-labelledby="help-title" v-modal-focus @keydown.esc="helpOpen=false"><header><h2 id="help-title">{{tx('Справка · {title}',{title})}}</h2><button class="icon-btn" :aria-label="tx('Закрыть справку')" @click="helpOpen=false"><AppIcon name="x"/></button></header><div class="bd"><template v-for="s in helpSections" :key="s[0]"><h4>{{s[0]}}</h4><p>{{s[1]}}</p></template></div><div class="ft"><button class="ghost tiny" @click="helpOpen=false;showRelease=true">{{tx('Версия {version} · Что нового',{version:release.version})}}</button></div></section>
 </template>

 <!-- Заявка: форма и карточка -->
 <RequestEditor v-if="editor" :api="api" :boot="boot" :company="company" :request="editor.request" :currency="currency" :money="money" :company-url="companyUrl" @close="editorClosed" @saved="editorSaved" @open-card="editorOpenCard"/>
 <RequestCard v-if="cardId" :id="cardId" :api="api" :company-url="companyUrl" :money="money" :dues="boot.due_minimums" @close="cardId=null" @changed="load" @edit="cardEdit" @record-payment="cardPay"/>

 <!-- Формы -->
 <div v-if="modal" class="modal-backdrop"><form v-modal-focus class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title" @submit.prevent="save" @keydown.esc="!saving&&(modal=null)"><div class="section-head"><h2 id="modal-title">{{tx(modal.title,modal.titleParams)}}</h2><button type="button" class="ghost icon-btn" :aria-label="tx('Закрыть')" @click="modal=null" :disabled="saving"><AppIcon name="x"/></button></div><p v-if="modal.note" class="form-note">{{tx(modal.note)}}</p><div class="form-grid"><label v-for="f in modalFields" :key="f.key" :class="{wide:f.type==='textarea'}">{{tx(f.label)}}<select v-if="f.type==='select'" v-model="form[f.key]" :required="f.required" :disabled="f.disabled"><option v-for="o in f.options" :key="o[0]" :value="o[0]">{{o[1]}}</option></select><textarea v-else-if="f.type==='textarea'" v-model="form[f.key]" :required="f.required" :minlength="f.minlength" maxlength="3000"></textarea><input v-else v-model="form[f.key]" :type="amountFields.includes(f.key)?'text':f.type" :inputmode="amountFields.includes(f.key)?'decimal':undefined" :pattern="amountFields.includes(f.key)?'[0-9]+([.][0-9]{1,2})?':undefined" :required="f.required" :disabled="f.disabled" :min="f.min" :max="f.max" :minlength="f.minlength" :step="f.step||'0.01'"></label></div><p v-if="categoryHint" class="warning category-hint" role="status">{{categoryHint}}</p><p v-if="formError" class="error">{{tx(formError)}}</p><div class="form-actions"><button type="button" class="secondary" @click="modal=null" :disabled="saving">{{tx('Отмена')}}</button><button :disabled="saving">{{saving?tx('Сохраняем…'):modal.saveLabel?tx(modal.saveLabel):tx('Сохранить')}}</button></div></form></div>
 <div v-if="documents" class="modal-backdrop"><section class="modal"><div class="section-head"><h2>{{tx('Документы · {reference}',{reference:documents.ledger.reference})}}</h2><button class="ghost icon-btn" :aria-label="tx('Закрыть')" @click="documents=null"><AppIcon name="x"/></button></div><p v-for="d in documents.rows" :key="d.url"><a :href="companyUrl(d.url)">{{d.filename}} ↓</a></p><p v-if="!documents.rows.length" class="sub">{{tx('Документов пока нет.')}}</p><label v-if="documents.ledger.can_attach_document" class="button" style="margin-top:12px">{{tx('Прикрепить PDF / изображение')}}<input type="file" accept=".pdf,.png,.jpg,.jpeg" @change="attach" :disabled="saving" hidden></label><p class="sub" style="margin-top:12px">{{tx('До 5 МБ. Документы доступны только авторизованным пользователям с правом просмотра реестра.')}}</p></section></div>
</template>
<style>
.global-operation{position:fixed;right:18px;bottom:18px;width:min(390px,calc(100vw - 36px));z-index:120;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:0 12px 8px;box-shadow:0 8px 32px rgba(var(--shade-rgb),.18)}.global-operation>.operation-progress{margin:10px 0 5px}.global-form-feedback{position:fixed;left:18px;bottom:18px;width:min(390px,calc(100vw - 36px));max-height:50vh;overflow:auto;z-index:120;background:var(--surface);border:1px solid var(--bad);border-radius:14px;padding:16px;box-shadow:0 8px 32px rgba(var(--shade-rgb),.18)}.global-form-feedback>strong{color:var(--bad)}.global-form-feedback p,.global-form-feedback small{font-size:12px;color:var(--muted)}.global-form-feedback ul{padding-left:18px;margin:8px 0}.global-form-feedback li{margin:6px 0}.global-form-feedback li button{padding:4px 0;white-space:normal;text-align:left}.global-form-feedback small{display:block}.form-field-invalid{border-color:var(--bad)!important;outline-color:var(--bad)!important;background:var(--bad-soft)!important}@media(max-width:820px){.global-form-feedback{bottom:14px;left:14px;width:calc(100vw - 28px);max-height:38vh}.global-operation{right:14px;bottom:14px;width:calc(100vw - 28px)}}
</style>
