<script setup>
import {computed,onUnmounted,ref,watch} from 'vue';
import {tx,N_,intlLocale,formatDate} from './i18n/index.js';

const props=defineProps({api:{type:Function,required:true},company:{type:Object,required:true},refresh:{type:Number,default:0}});
const payload=ref(null),loading=ref(false),syncing=ref(false),error=ref(''),endMonth=ref(''),tab=ref('scorecard');
let generation=0,active=true;
const report=computed(()=>payload.value?.report);
const metrics=computed(()=>Object.fromEntries((report.value?.scorecard||[]).map(row=>[row.key,row])));
const numeric=v=>v==null?null:Number(v);
const fmt=(v,digits=1)=>v==null?'—':Number(v).toLocaleString(intlLocale.value,{maximumFractionDigits:digits,minimumFractionDigits:digits});
const money=v=>fmt(v==null?null:Number(v)/1e6,1);
const metric=row=>!row||row.value==null?'—':row.unit==='%'?fmt(Number(row.value)*100,Number(row.value)!==0&&Math.abs(Number(row.value))<0.001?3:1)+'%':row.unit==='flag'?(Number(row.value)===1?tx('Совпадает'):tx('Расхождение')):fmt(row.value,row.unit==='x'?3:1)+(row.unit==='x'?'×':row.unit==='days'?' '+tx('дн.'):'');
const label=status=>tx(({good:N_('Устойчиво'),watch:N_('Внимание'),critical:N_('Риск'),unavailable:N_('Нет данных')})[status]);
const tabs=[['scorecard',N_('Показатели и баллы')],['monthly',N_('Динамика по месяцам')],['data',N_('Полнота данных')]];
const columns=[['plan',N_('План выручки')],['revenue',N_('Чистая выручка')],['gross_profit',N_('Валовая прибыль')],['ebitda','EBITDA'],['net_income',N_('Чистая прибыль')],['ocf',N_('Поток модели')],['cash',N_('Деньги')],['ar',N_('Дебиторская задолженность')],['inventory',N_('Запасы')],['ap',N_('Кредиторская задолженность')],['st_debt',N_('Краткосрочные кредиты')],['lt_debt',N_('Долгосрочные кредиты')]];
const chartMax=computed(()=>Math.max(1,...(report.value?.monthly||[]).flatMap(row=>['revenue','net_income','ocf'].map(key=>Math.abs(numeric(row[key])||0)))));
const chartKeys=['revenue','net_income','ocf'];
const chartHeight=value=>Math.abs(numeric(value)||0)/chartMax.value*120;
const monthName=m=>formatDate(new Date(report.value.year,m-1,1),{month:'short'});
const rule=row=>{
 if(row.direction==='linear')return tx('Балл = значение × 10, от 0 до 10.');
 if(row.direction==='checks')return tx('Балл = доля OK × 8. Флаги модели не заменяют независимую проверку.');
 if(row.direction==='mapping')return tx('Расхождение ≤ 1 исходной единицы: 10 баллов; иначе 2.');
 return row.bands.map(([limit,score])=>(row.direction==='higher'?'≥ ':'≤ ')+(row.unit==='%'?fmt(limit*100,0)+'%':fmt(limit,2))+' → '+score).join('; ')+'; '+tx('иначе 0');
};
async function load(sync=false){
 const id=++generation,cid=props.company.id;
 error.value='';loading.value=true;syncing.value=sync;
 if(!sync)payload.value=null;
 try{
  if(sync)await props.api('/api/ifrs-rating/sync?company_id='+cid,{method:'POST'});
  if(!active||generation!==id||props.company.id!==cid)return;
  const query=new URLSearchParams({company_id:String(cid)});
  if(endMonth.value)query.set('end_month',endMonth.value);
  const data=await props.api('/api/ifrs-rating?'+query);
  if(!active||generation!==id||props.company.id!==cid)return;
  if(data.company_id!==cid)throw Error(N_('Источник относится к другой компании. Обновите страницу.'));
  payload.value=data;
 }catch(e){if(active&&generation===id)error.value=e.message;}
 finally{if(active&&generation===id){loading.value=false;syncing.value=false;}}
}
watch(()=>[props.company.id,props.refresh],()=>{endMonth.value='';load();},{immediate:true});
onUnmounted(()=>{active=false;generation++;});
</script>

<template>
 <section class="ifrs-rating" :aria-busy="loading">
  <header class="rating-heading">
   <div><p class="rating-eyebrow">{{company.name}}</p><h2>{{tx('Финансовый профиль')}}</h2><p class="muted">{{tx('Управленческая оценка финансов и качества данных')}}</p></div>
   <div class="rating-actions">
    <label v-if="report">{{tx('Период с начала года')}}<select v-model="endMonth" :disabled="loading" @change="load()"><option value="">{{tx('Последний доступный месяц')}}</option><option v-for="m in report.available_months" :key="m" :value="String(m)">{{monthName(m)}} {{report.year}}</option></select></label>
    <button v-if="payload?.can_sync" class="secondary" :disabled="loading" @click="load(true)">{{tx(syncing?N_('Обновляем из Google Drive…'):N_('Обновить из Google Drive'))}}</button>
   </div>
  </header>
  <p v-if="error" class="error" role="alert">{{tx(error)}} <button type="button" class="secondary" :disabled="loading" @click="load()">{{tx('Повторить')}}</button></p>
  <p v-if="loading" class="rating-loading" role="status">{{tx(syncing?N_('Обновляем из Google Drive…'):N_('Рассчитываем рейтинг…'))}}</p>
  <section v-if="payload&&!report&&!loading" class="card rating-empty"><h3>{{tx('Подключите финансовую модель')}}</h3><p>{{tx('Обновите данные из Google Drive, чтобы рассчитать отчёт.')}}</p><a v-if="payload.source_url" :href="payload.source_url" target="_blank" rel="noopener noreferrer">{{tx('Открыть источник')}}</a></section>
  <template v-if="report">
   <p v-if="payload.stale" class="error" role="status">{{tx('Google Drive недоступен. Показана последняя сохранённая версия; данные могут быть устаревшими.')}}</p>
   <div class="rating-source"><span>{{report.period.start}} — {{report.period.end}} · {{report.period.months}} {{tx('мес.')}}</span><span>{{tx('Синхронизация')}}: {{formatDate(payload.binding.synced_at,{dateStyle:'short',timeStyle:'short'})}}</span><a v-if="payload.source_url" :href="payload.source_url" target="_blank" rel="noopener noreferrer">Google Drive ↗</a></div>
   <div class="rating-overview">
    <section class="rating-verdict">
     <div class="rating-grade-line"><span class="rating-grade">{{report.grade||'—'}}</span><div><p>{{tx('Предварительный рейтинг')}}</p><strong>{{fmt(report.score)}} <small>/ 100</small></strong><p v-if="report.capped">{{tx('Рейтинг ограничен уровнем C из-за критических рисков.')}}</p></div></div>
     <div class="rating-meter" role="img" :aria-label="tx('Итоговый балл')+': '+fmt(report.score)"><span :style="{width:Math.min(100,Number(report.score)||0)+'%'}"></span></div>
     <p v-if="report.status==='incomplete'">{{tx('Итог не рассчитан: часть показателей недоступна.')}} {{tx('Покрытие весов')}}: {{report.coverage_weight}}%.</p>
     <p v-else>{{tx('Оценка по сохранённым показателям модели. Соответствие МСФО не подтверждено.')}}</p>
    </section>
    <section class="card rating-categories"><h3>{{tx('Вклад в оценку')}}</h3><div v-for="category in report.categories" :key="category.title" class="rating-category"><span>{{tx(category.title)}}</span><div class="category-track"><i :style="{width:Math.max(0,Math.min(100,Number(category.points)/category.weight*100))+'%'}"></i></div><strong>{{fmt(category.points)}} <small>/ {{category.weight}}</small><sup v-if="!category.complete">*</sup></strong></div></section>
   </div>
   <div class="rating-kpis">
    <article v-for="key in ['plan_execution','ebitda_margin','cash_ratio','net_debt_ebitda']" :key="key" class="card"><p>{{tx(metrics[key].title)}}</p><strong>{{metric(metrics[key])}}</strong><span :class="['rating-status',metrics[key].status]">{{label(metrics[key].status)}}</span></article>
   </div>
   <section class="card rating-risks"><h3>{{tx('Ключевые риски')}}</h3><ul><li v-for="issue in report.issues" :key="issue.code" :class="issue.severity"><span class="risk-dot"></span>{{tx(issue.title)}}</li></ul></section>
   <nav class="rating-tabs" :aria-label="tx('Разделы рейтинга')"><button v-for="[key,title] in tabs" :key="key" :class="{on:tab===key}" :aria-current="tab===key?'page':undefined" @click="tab=key">{{tx(title)}}</button></nav>
   <section v-if="tab==='scorecard'" class="card rating-detail">
    <h3>{{tx('Показатели и баллы')}}</h3><p class="muted">{{tx('Баллы от 0 до 10. Вклад = балл × вес / 10.')}}</p>
    <div class="rating-scroll" tabindex="0" :aria-label="tx('Таблица показателей')"><table><thead><tr><th>{{tx('Показатель')}}</th><th>{{tx('Значение')}}</th><th>{{tx('Вес')}}</th><th>{{tx('Балл')}}</th><th>{{tx('Вклад')}}</th><th>{{tx('Оценка')}}</th></tr></thead><tbody><template v-for="category in report.categories" :key="category.title"><tr class="rating-group"><th colspan="6">{{tx(category.title)}} · {{category.weight}}%</th></tr><tr v-for="row in report.scorecard.filter(item=>item.category===category.title)" :key="row.key"><th scope="row">{{tx(row.title)}}<small>{{row.formula}}</small></th><td>{{metric(row)}}</td><td>{{row.weight}}%</td><td>{{fmt(row.score)}}</td><td>{{fmt(row.points)}}</td><td><span :class="['rating-status',row.status]">{{label(row.status)}}</span></td></tr></template></tbody></table></div>
   </section>
   <section v-if="tab==='monthly'" class="card rating-detail">
    <h3>{{tx('Динамика по месяцам')}}</h3><p class="muted">{{tx('Млн исходных единиц. Остатки — на конец месяца.')}}</p>
    <div class="rating-chart-legend"><span v-for="(key,n) in chartKeys" :key="key" :class="'series-'+n">{{tx(columns.find(c=>c[0]===key)[1])}}</span></div>
    <svg class="rating-chart" viewBox="0 0 760 290" role="img" :aria-label="tx('Выручка, прибыль и денежный поток по месяцам')"><line x1="30" y1="155" x2="750" y2="155" stroke="#9daeb2"/><text x="12" y="159" font-size="12">0</text><g v-for="(row,index) in report.monthly" :key="row.month" :transform="`translate(${40+index*700/report.monthly.length},0)`"><rect v-for="(key,n) in chartKeys" :key="key" :x="n*18" :y="numeric(row[key])>=0?155-chartHeight(row[key]):155" width="13" :height="chartHeight(row[key])" :fill="['#246b66','#7cab99','#d99654'][n]"><title>{{row.period}} · {{tx(columns.find(c=>c[0]===key)[1])}}: {{money(row[key])}}</title></rect><text y="285" font-size="12" fill="currentColor">{{monthName(row.month)}}</text></g></svg>
    <div class="rating-scroll" tabindex="0" :aria-label="tx('Данные по месяцам')"><table><thead><tr><th>{{tx('Показатель')}}</th><th v-for="row in report.monthly" :key="row.month">{{row.period}}</th></tr></thead><tbody><tr v-for="[key,title] in columns" :key="key"><th scope="row">{{tx(title)}}</th><td v-for="row in report.monthly" :key="row.month" :title="row.sources[key]">{{money(row[key])}}</td></tr></tbody></table></div>
   </section>
   <section v-if="tab==='data'" class="card rating-detail"><h3>{{tx('Полнота данных')}}</h3><p class="muted">{{tx('Внешние подтверждения и производственные KPI не представлены в финансовой модели.')}}</p><div class="rating-data-grid"><article v-for="block in report.data_blocks.filter(b=>b.title)" :key="block.key"><h4>{{tx(block.title)}}</h4><p>{{tx(block.requirement)}}</p><span class="rating-status unavailable">{{tx('Требуется источник')}}</span></article></div>
    <h3 class="pharma-heading">{{tx('Производственные KPI из образца')}}</h3><p class="muted">{{tx('Цели — ориентиры образца. До получения факта производственные KPI не входят в общий балл.')}}</p>
    <div class="rating-scroll" tabindex="0" :aria-label="tx('Производственные KPI из образца')"><table><thead><tr><th>{{tx('Показатель')}}</th><th>{{tx('Ориентир')}}</th><th>{{tx('Факт')}}</th></tr></thead><tbody><tr v-for="kpi in report.pharma_kpis" :key="kpi.title"><th>{{tx(kpi.title)}}<small>{{kpi.module}}</small></th><td>{{tx(kpi.target)}}</td><td>{{tx('Нет данных')}}</td></tr></tbody></table></div>
   </section>
   <details class="card rating-method"><summary>{{tx('Методика и источники расчёта')}} · {{report.method_version}}</summary>
    <p>{{tx('Веса взяты из образца IFRS Rating. Полная шкала баллов задана в коде; пороги являются управленческими, а не нормативами МСФО.')}}</p>
    <p>A+ ≥ 90 · A ≥ 80 · B+ ≥ 70 · B ≥ 60 · C ≥ 50 · D &lt; 50</p>
    <p>{{tx('При расхождении обязательств или отрицательном потоке при положительной прибыли рейтинг не выше C. Отсутствующие показатели не заменяются нулём; веса не перераспределяются.')}}</p>
    <p>{{tx('Используются сохранённые месячные показатели FinModel, а не исправленный баланс сайта. Формулы Excel не исполняются. EBITDA и EBIT сохраняют трактовку модели; капитал берётся из строки 36.')}}</p>
    <p>{{tx('Год определяется по Mapping. Поток модели: строка 8 минус строка 16 листа 09_CASH_Flow, по номерам месяцев в строке 3; календарный год строки 4 устарел. Это proxy OCF, требующий разделения видов деятельности.')}}</p>
    <p>{{tx('ROA и EBITDA годовые: × 12 / число месяцев. DSO, DIO и DPO используют остатки на конец периода и календарные дни. Быстрая ликвидность исключает запасы и авансы.')}}</p>
    <p>{{tx('Неположительный капитал или EBITDA при наличии долга дают 0 баллов долговой метрике. Нулевой знаменатель других коэффициентов означает отсутствие оценки.')}}</p>
    <div class="rating-scroll" tabindex="0" :aria-label="tx('Шкала баллов')"><table><thead><tr><th>{{tx('Показатель')}}</th><th>{{tx('Шкала баллов')}}</th></tr></thead><tbody><tr v-for="row in report.scorecard" :key="row.key"><th>{{tx(row.title)}}</th><td class="rule-cell">{{rule(row)}}</td></tr></tbody></table></div>
    <details><summary>{{tx('Ячейки исходной модели')}}</summary><p class="muted">{{payload.source.filename}} · SHA-256 {{payload.source.sha256}}</p><div class="rating-scroll"><table><tbody><tr v-for="(sources,key) in report.provenance" :key="key"><th>{{key}}</th><td class="rule-cell">{{sources.join('; ')}}</td></tr></tbody></table></div></details>
   </details>
  </template>
 </section>
</template>

<style scoped>
.ifrs-rating .card{padding:22px;min-width:0}.rating-grade-line small{display:inline!important;color:#d4e5e1!important;opacity:1!important}.rating-category strong small{display:inline!important}.rating-category strong{white-space:nowrap}.pharma-heading{margin-top:26px!important}

.ifrs-rating{display:grid;gap:18px;min-width:0;color:#213a3d}.rating-heading{display:flex;justify-content:space-between;align-items:flex-end;gap:18px;flex-wrap:wrap}.rating-heading h2{font-size:32px;letter-spacing:-1px;margin:3px 0}.rating-heading p{margin:0}.rating-eyebrow{text-transform:uppercase;letter-spacing:1.5px;font-size:11px;font-weight:700}.rating-actions{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap}.rating-actions label{display:grid;gap:5px;font-size:12px}.rating-source{display:flex;gap:10px 24px;flex-wrap:wrap;font-size:12px;color:#65777b}.rating-source a{margin-left:auto;color:#246b66}.rating-overview{display:grid;grid-template-columns:minmax(280px,1fr) minmax(360px,1.45fr);gap:18px}.rating-verdict{background:#123e3b;border-radius:16px;color:#fff;padding:26px;display:flex;flex-direction:column;justify-content:center}.rating-grade-line{display:flex;align-items:center;gap:24px}.rating-grade{font-size:90px;line-height:1;font-weight:700;letter-spacing:-5px}.rating-grade-line strong{font-size:34px}.rating-grade-line small{font-size:15px;font-weight:400;opacity:.65}.rating-verdict p{font-size:12px;color:#d4e5e1;line-height:1.65}.rating-meter{height:5px;background:#39625f;border-radius:10px;overflow:hidden;margin:22px 0 5px}.rating-meter span{height:100%;background:#cae9ad;display:block}.rating-categories h3{margin-top:0}.rating-category{display:grid;grid-template-columns:145px minmax(0,1fr) 72px;align-items:center;gap:12px;margin:11px 0;font-size:12px}.rating-category strong{text-align:right;font-variant-numeric:tabular-nums}.rating-category small{font-weight:400;color:#61777a}.category-track{height:6px;background:#edf2f0;border-radius:5px;overflow:hidden}.category-track i{display:block;background:#579383;height:100%}.rating-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.rating-kpis article{display:flex;flex-direction:column;align-items:flex-start;gap:12px}.rating-kpis p{margin:0;font-size:12px;min-height:34px}.rating-kpis strong{font-size:28px;font-variant-numeric:tabular-nums}.rating-status{display:inline-block;border-radius:5px;padding:3px 7px;font-size:11px;white-space:nowrap;background:#f0f2f4;color:#59676e}.rating-status.good{background:#e6f2eb;color:#24714e}.rating-status.watch{background:#fbf1db;color:#865d17}.rating-status.critical{background:#fbe9e5;color:#a44633}.rating-risks h3{margin-top:0}.rating-risks ul{padding:0;margin:0;list-style:none;display:grid;gap:9px}.rating-risks li{display:flex;align-items:baseline;gap:10px;font-size:13px;line-height:1.6}.risk-dot{width:6px;height:6px;flex:0 0 6px;border-radius:50%;background:#c39758}.critical .risk-dot{background:#bf624e}.rating-tabs{display:flex;gap:6px;flex-wrap:wrap}.rating-tabs button{background:transparent;color:#52686b;border:1px solid #dce4e1}.rating-tabs .on{background:#246b66;border-color:#246b66;color:#fff}.rating-detail h3{margin:0 0 8px}.rating-scroll{overflow:auto;max-width:100%;overscroll-behavior-inline:contain}.rating-scroll table{width:100%;border-collapse:collapse;font-size:12px}.rating-scroll th,.rating-scroll td{padding:12px 14px;border-bottom:1px solid #e7edea;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}.rating-scroll th:first-child{text-align:left;white-space:normal;min-width:210px;max-width:340px;font-weight:500}.rating-scroll thead th{background:#f6f8f7;color:#627578;font-size:11px}.rating-scroll small{display:block;font-weight:400;font-size:10px;color:#73878a;line-height:1.6;margin-top:4px;overflow-wrap:anywhere}.rating-group th{background:#edf4f0!important;font-weight:700!important;color:#2d6054}.rating-scroll td.rule-cell{text-align:left;white-space:normal;min-width:250px;line-height:1.7}.rating-method{font-size:12px;line-height:1.8}.rating-method summary{cursor:pointer;font-weight:700}.rating-method>p{max-width:1050px}.rating-method details{margin-top:18px;overflow-wrap:anywhere}.rating-chart{width:100%;height:290px;margin:12px 0}.rating-chart-legend{display:flex;gap:20px;flex-wrap:wrap;font-size:12px}.rating-chart-legend span:before{content:'';display:inline-block;width:8px;height:8px;margin-right:7px;background:#246b66}.rating-chart-legend .series-1:before{background:#7cab99}.rating-chart-legend .series-2:before{background:#d99654}.rating-data-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.rating-data-grid article{border:1px solid #e0e8e3;padding:16px;border-radius:10px}.rating-data-grid h4{margin:0}.rating-data-grid p{font-size:12px;line-height:1.7;color:#647779}.rating-loading{padding:18px;background:#eef4f1;border-radius:10px}.rating-empty{padding:30px}
@media(max-width:1050px){.rating-overview{grid-template-columns:1fr}.rating-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.rating-data-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:600px){.rating-heading h2{font-size:26px}.rating-grade{font-size:70px}.rating-verdict{padding:22px}.rating-category{grid-template-columns:120px minmax(0,1fr) 65px;gap:8px}.rating-kpis strong{font-size:23px}.rating-kpis{gap:9px}.ifrs-rating .rating-kpis article{padding:14px}.rating-source a{margin-left:0}.rating-data-grid{grid-template-columns:1fr}.rating-scroll th,.rating-scroll td{padding:10px}.rating-actions{width:100%}.rating-actions label{flex:1}.rating-chart{height:230px}}
</style>
