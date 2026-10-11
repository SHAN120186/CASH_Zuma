<script setup>
import {ref, computed, watch, nextTick} from 'vue';
import {numericText, decimalShift} from './businessProjects.js';
import {tx, N_} from './i18n/index.js';

const props=defineProps({model:Object,parameters:Object,disabled:Boolean,stage:{type:String,default:'data'}});
const emit=defineEmits(['change','parameter','data']);
const decisions=ref({}), values=ref({}), showAll=ref(false), panel=ref(null), browseGroup=ref(''), search=ref(''),limit=ref(25),basisChecked=ref(false);
let restoredSelection=null;
const items=computed(()=>props.model?.input_review?.items||[]);
const groups=computed(()=>[...new Set(items.value.map(item=>item.group).filter(Boolean))]);
const scalarKeys=computed(()=>new Set((props.model?.parameters||[]).filter(item=>item.editable!==false).map(item=>item.key)));
const fraction=item=>item.unit==='доля'; // i18n-ignore
const display=(item,value)=>value==null?'':fraction(item)?decimalShift(value,2):String(value);
const baseline=item=>Object.hasOwn(item,'original_value')?item.original_value:item.value;
function initialize(){
  decisions.value=Object.fromEntries(items.value.filter(item=>item.decision||props.model?.review_decisions?.[item.key]||props.model?.input_review?.decisions?.[item.key]).map(item=>[item.key,{...(item.decision||props.model.review_decisions?.[item.key]||props.model.input_review.decisions?.[item.key])}]));
  values.value=Object.fromEntries(items.value.map(item=>[item.key,display(item,props.model?.data_updates?.[item.key]??props.model?.overrides?.[item.key]??item.value)]));
  restoredSelection=selectionFingerprint();basisChecked.value=!!props.model?.confirm_source_basis;
}
watch(()=>props.model,initialize,{immediate:true});
const currentValue=item=>scalarKeys.value.has(item.key)?props.parameters?.[item.key]??values.value[item.key]:values.value[item.key];
const selectedProposal=item=>(item.proposals||[]).find(proposal=>proposal.id===decisions.value[item.key]?.proposal_id);
function selectionFingerprint(){return JSON.stringify(items.value.filter(item=>decisions.value[item.key]?.choice==='source').map(item=>{const proposal=(item.proposals||[]).find(proposal=>proposal.id===decisions.value[item.key]?.proposal_id);return[item.key,proposal?.id||'',proposal?.source_sha256||''];}).sort((a,b)=>a[0].localeCompare(b[0])));}
watch(selectionFingerprint,value=>{if(value===restoredSelection){restoredSelection=null;return;}restoredSelection=null;basisChecked.value=false;});
const sourceBasisItems=computed(()=>items.value.filter(item=>decisions.value[item.key]?.choice==='source'&&selectedProposal(item)?.basis_confirmation));
function raw(item,value){const text=numericText(value);if(!text)return NaN;const converted=fraction(item)?decimalShift(text,-2):text,number=Number(converted);return number===0&&/[1-9]/.test(converted.replace(/[eE][+-]?\d+$/,''))?NaN:number;}
const submission=computed(()=>{
  const data_updates={},review_decisions={},scalars={},unresolved=[];
  for(const item of items.value){
    const decision=decisions.value[item.key];let value;
    if(!decision?.choice){if(item.requires_decision||numericText(item.value)!==numericText(baseline(item)))unresolved.push({key:item.key,label:item.label,message:N_('Выберите решение для этого поля.')});continue;}
    if(decision.choice==='keep')value=numericText(baseline(item))?Number(numericText(baseline(item))):NaN;
    else if(decision.choice==='source')value=numericText(selectedProposal(item)?.value)?Number(numericText(selectedProposal(item)?.value)):NaN;
    else if(decision.choice==='manual')value=raw(item,currentValue(item));
    else {unresolved.push({key:item.key,label:item.label,message:N_('Выберите решение для этого поля.')});continue;}
    if(decision.choice==='keep'&&!item.required&&baseline(item)==null){review_decisions[item.key]={choice:'keep'};continue;}
    if(!Number.isFinite(value)||decision.choice==='source'&&!selectedProposal(item)){unresolved.push({key:item.key,label:item.label,message:decision.choice==='source'?N_('Выберите значение из нового документа.'):N_('Введите числовое значение.')});continue;}
    if(item.min!=null&&value<item.min||item.max!=null&&value>item.max){unresolved.push({key:item.key,label:item.label,message:N_('Значение вне допустимого диапазона.')});continue;}
    if(item.integer&&!Number.isInteger(value)){unresolved.push({key:item.key,label:item.label,message:N_('Введите целое число.')});continue;}
    review_decisions[item.key]={choice:decision.choice,...(decision.choice==='source'?{proposal_id:decision.proposal_id}:{})};
    if(scalarKeys.value.has(item.key))scalars[item.key]=value;
    else if(decision.choice!=='keep')data_updates[item.key]=value;
  }
  for(const item of items.value){
    if(!review_decisions[item.key]||review_decisions[item.key].choice==='keep'||item.sheet!=='Труд')continue; // i18n-ignore
    const address=/^([BC])(\d+)$/.exec(item.cell||'');if(!address)continue;
    const paired=items.value.find(other=>other.sheet===item.sheet&&other.cell===(address[1]==='B'?'C':'B')+address[2]);if(!paired)continue;
    const value=data_updates[paired.key]??scalars[paired.key]??baseline(paired);
    if((!numericText(value)||!Number.isFinite(Number(numericText(value))))&&!unresolved.some(problem=>problem.key===paired.key))unresolved.push({key:paired.key,label:paired.label,message:N_('Заполните численность и зарплату выбранной должности. Пустое поле не означает ноль.')});
  }
  if(sourceBasisItems.value.length&&!basisChecked.value)for(const item of sourceBasisItems.value)if(!unresolved.some(problem=>problem.key===item.key))unresolved.push({key:item.key,label:item.label,message:N_('Проверьте валюту, единицы, период и базу цен выбранных документов.')});
  const savedDecisions=props.model?.review_decisions||props.model?.input_review?.decisions||{};
  const keys=new Set(items.value.map(item=>item.key));
  const changed=Object.entries(review_decisions).some(([key,value])=>JSON.stringify(value)!==JSON.stringify(savedDecisions[key]))||Object.keys(savedDecisions).some(key=>keys.has(key)&&!review_decisions[key])||Object.entries(data_updates).some(([key,value])=>numericText(value)!==numericText(props.model?.data_updates?.[key]));
  return {data_updates,review_decisions,scalars,unresolved,changed,confirm_source_basis:!!sourceBasisItems.value.length&&basisChecked.value};
});
watch(()=>JSON.stringify(submission.value),()=>emit('change',submission.value),{immediate:true});
const visibleItems=computed(()=>items.value.filter(item=>{
  const query=search.value.trim().toLocaleLowerCase();
  if(browseGroup.value&&browseGroup.value!=='*'&&browseGroup.value!==item.group)return false;
  if(query&&!`${tx(item.label)} ${tx(item.group)} ${item.label}`.toLocaleLowerCase().includes(query))return false;
  return showAll.value||browseGroup.value||query||item.requires_decision||submission.value.unresolved.some(problem=>problem.key===item.key)||decisions.value[item.key]?.choice&&decisions.value[item.key].choice!=='keep'||numericText(item.value)!==numericText(baseline(item));
}));
const displayedItems=computed(()=>visibleItems.value.slice(0,limit.value));
watch([browseGroup,search],()=>{limit.value=25;showAll.value=false;});
const choice=item=>decisions.value[item.key]?.choice||'';
function setChoice(item,event){
  if(props.disabled)return;
  const value=event.target.value;
  if(item.editable===false&&value!=='keep')return;
  decisions.value={...decisions.value,[item.key]:{choice:value,...(value==='source'&&(item.proposals||[]).length===1?{proposal_id:item.proposals[0].id}:{})}};
  if(value==='keep'&&scalarKeys.value.has(item.key))emit('parameter',{key:item.key,value:display(item,baseline(item))});
  if(value==='source'&&(item.proposals||[]).length===1&&scalarKeys.value.has(item.key))emit('parameter',{key:item.key,value:display(item,item.proposals[0].value)});
}
function selectProposal(item,event){if(props.disabled||item.editable===false)return;decisions.value[item.key]={choice:'source',proposal_id:event.target.value};const proposal=selectedProposal(item);if(proposal&&scalarKeys.value.has(item.key))emit('parameter',{key:item.key,value:display(item,proposal.value)});}
function setManual(item,event){if(props.disabled||item.editable===false)return;values.value[item.key]=event.target.value;if(scalarKeys.value.has(item.key))emit('parameter',{key:item.key,value:event.target.value});}
function setParameter(key,value){const item=items.value.find(item=>item.key===key);if(!item||props.disabled||item.editable===false)return;decisions.value[key]={choice:'manual'};values.value[key]=value;}
async function goToField(key){emit('data');browseGroup.value='';search.value='';await nextTick();showAll.value=true;limit.value=Math.max(25,items.value.findIndex(item=>item.key===key)+1);await nextTick();const target=Array.from(panel.value?.querySelectorAll?.('[data-review-key]')||[]).find(node=>node.getAttribute('data-review-key')===key);target?.querySelector?.('select')?.focus?.({preventScroll:true});target?.scrollIntoView?.({behavior:'smooth',block:'center'});}
defineExpose({goToField,setParameter});
</script>
<template>
  <section v-if="items.length" ref="panel" class="native-input-review" :hidden="stage==='report'">
    <h3>{{tx('Проверьте только изменения')}}</h3>
    <p class="sub">{{tx('Исходные значения уже заполнены. Для каждого расхождения оставьте шаблон, выберите значение из нового документа или введите своё.')}}</p>
    <p v-if="!visibleItems.length&&!browseGroup&&!search&&!showAll" class="notice">{{tx('Спорных значений нет. Можно перейти к отчёту.')}}</p>
    <div class="review-filters"><label>{{tx('Какие данные изменить')}}<select v-model="browseGroup" :disabled="disabled"><option value="">{{tx('Только изменения и спорные поля')}}</option><option value="*">{{tx('Все исходные значения')}}</option><option v-for="group in groups" :key="group" :value="group">{{tx(group)}}</option></select></label><label>{{tx('Найти параметр')}}<input v-model="search" type="search" :disabled="disabled" :placeholder="tx('Название продукции, расходы или сотрудник')"></label></div>
    <label v-if="sourceBasisItems.length" class="review-basis-check"><input v-model="basisChecked" type="checkbox" :disabled="disabled">{{tx('Я проверил(а) валюту, единицы, период и базу цен выбранных документов')}}</label>
    <p v-if="!visibleItems.length&&(browseGroup||search)" class="sub">{{tx('Подходящих параметров нет. Измените группу или название.')}}</p>
    <article v-for="item in displayedItems" :key="item.key" :data-review-key="item.key" class="review-item">
      <div class="section-head"><h4>{{tx(item.label)}}</h4><span v-if="item.unit" class="pill">{{fraction(item)?'%':tx(item.unit)}}</span></div>
      <p class="review-original"><small>{{tx('В оригинальном шаблоне')}}</small><b>{{item.editable===false&&baseline(item)==null?tx('Рассчитывается по формуле Excel'):display(item,baseline(item))}}</b><span v-if="numericText(item.value)!==numericText(baseline(item))" class="sub">{{tx('Сохранённое значение: {value}',{value:display(item,item.value)})}}</span></p>
      <p v-if="item.help" class="sub">{{tx(item.help)}}</p>
      <label v-if="item.editable!==false||item.requires_decision">{{tx('Как использовать это поле')}}<select :value="choice(item)" :disabled="disabled" @change="setChoice(item,$event)"><option value="">{{tx('Выберите решение')}}</option><option value="keep">{{tx('Оставить значение шаблона')}}</option><option v-if="item.proposals?.length&&item.editable!==false" value="source">{{tx('Взять из нового документа')}}</option><option v-if="item.editable!==false" value="manual">{{tx('Ввести своё значение')}}</option></select></label>
      <label v-if="choice(item)==='source'">{{tx('Значение из документа')}}<select :value="decisions[item.key]?.proposal_id||''" :disabled="disabled" @change="selectProposal(item,$event)"><option value="">{{tx('Выберите документ и значение')}}</option><option v-for="proposal in item.proposals||[]" :key="proposal.id" :value="proposal.id">{{display(item,proposal.value)}} {{fraction(item)?'%':tx(proposal.unit||item.unit)}} · {{proposal.source?.filename||proposal.source||tx('Новый документ')}}</option></select></label>
      <p v-if="choice(item)==='source'&&selectedProposal(item)?.basis_confirmation" class="warning" role="alert">{{tx('В выбранном документе не подтверждены валюта, единицы, период или база цены. Сверьте выбранное значение с оригиналом документа.')}}</p>
      <label v-if="choice(item)==='manual'">{{tx('Новое значение')}}<input :value="currentValue(item)" type="text" inputmode="decimal" :disabled="disabled" @input="setManual(item,$event)"></label>
      <p v-for="problem in submission.unresolved.filter(problem=>problem.key===item.key)" :key="problem.key" class="review-required">{{tx(problem.message)}}</p>
      <details><summary>{{tx('Показать источник и адрес ячейки')}}</summary><p>{{item.sheet}}!{{item.cell}}</p><p v-for="proposal in item.proposals||[]" :key="proposal.id">{{proposal.source?.filename||proposal.source}}<template v-if="proposal.sheet||proposal.cell"> · {{proposal.sheet}}!{{proposal.cell}}</template></p></details>
    </article>
    <button v-if="visibleItems.length>limit" type="button" class="secondary" :disabled="disabled" @click="limit+=25">{{tx('Показать ещё {n} параметров',{n:Math.min(25,visibleItems.length-limit)})}}</button>
    <details v-if="model.input_review?.warnings?.length"><summary>{{tx('Замечания к данным')}}</summary><ul><li v-for="(warning,index) in model.input_review.warnings" :key="index">{{tx(warning.message||warning)}}</li></ul></details>
  </section>
</template>
<style scoped>
.review-basis-check{display:flex;gap:10px;align-items:flex-start;margin:16px 0;line-height:1.6;font-weight:600}.review-basis-check input{flex:none;width:auto;margin-top:5px}
.native-input-review{margin:18px 0}.native-input-review .sub{line-height:1.6}.review-filters{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin:16px 0}.review-filters label{display:grid;gap:6px}.review-item{padding:16px;border:1px solid var(--line);border-radius:14px;margin:14px 0}.review-item h4{margin:0}.review-item>label{display:grid;gap:6px;margin:12px 0}.review-original{display:flex;flex-wrap:wrap;gap:10px;align-items:center}.review-original small{width:100%}.review-required{color:var(--bad);margin:10px 0;font-weight:600}.review-item details{margin-top:14px;color:var(--mut)}.review-item details p{overflow-wrap:anywhere;margin:8px 0}.review-item select{white-space:normal;width:100%}@media(max-width:560px){.review-filters{grid-template-columns:1fr}}
</style>
