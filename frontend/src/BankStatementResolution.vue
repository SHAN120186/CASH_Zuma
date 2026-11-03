<script setup>
import {computed,ref,watch,onUnmounted} from 'vue';
import {tx,N_} from './i18n/index.js';
import {toCents,formatCents,formatShortDate} from './overview/format.js';
import {statementCategoryName} from './cashflow/statementCategory.js';

const props=defineProps({api:{type:Function,required:true},companyId:{type:Number,required:true},row:{type:Object,required:true},canResolve:{type:Boolean,default:false},disabled:{type:Boolean,default:false}});
const emit=defineEmits(['changed','refresh','working']);
const open=ref(false),kind=ref(''),options=ref(null),target=ref(''),reason=ref(''),attest=ref(false),attestCounterparty=ref(false),confirm=ref(false),preview=ref(null),busy=ref(false),error=ref(''),notice=ref(''),uncertain=ref(false);
let generation=0,updating=false;
const definitions={
 'historical-transfer':{title:N_('Учесть уже выполненный перевод'),attestation:N_('Подтверждаю по банковским документам: перевод уже выполнен, оба счёта принадлежат этой компании.'),action:N_('Учесть выполненный перевод'),status:'historical_transfer_posted'},
 'historical-request':{title:N_('Учесть историческую оплату закрытой заявки'),attestation:N_('Подтверждаю по банковским документам: выбранная закрытая заявка фактически оплачена.'),action:N_('Учесть историческую оплату'),status:'historical_request_paid'},
 'distinct-income':{title:N_('Учесть отдельное поступление с тем же номером'),attestation:N_('Подтверждаю по банковским документам: это отдельное фактически полученное поступление, а не повтор.'),action:N_('Учесть отдельное поступление'),status:'manual_distinct_income'},
};
const eligible=computed(()=>['review','reviewed','ambiguous_duplicate'].includes(props.row.status)&&['in','out'].includes(props.row.direction));
const kinds=computed(()=>props.row.direction==='out'?['historical-transfer','historical-request']:['distinct-income']);
const definition=computed(()=>definitions[kind.value]);
const items=computed(()=>kind.value==='historical-transfer'?options.value?.accounts||[]:options.value?.items||[]);
const itemId=item=>kind.value==='historical-transfer'?item.id:kind.value==='historical-request'?item.request_id:item.line_id;
const selected=computed(()=>items.value.find(item=>String(itemId(item))===target.value));
const needsCounterpartyConfirmation=computed(()=>kind.value==='historical-request'&&selected.value?.requires_counterparty_confirmation===true);
const available=computed(()=>!!options.value&&options.value.available!==false&&items.value.length>0);
const canPreview=computed(()=>props.canResolve&&eligible.value&&open.value&&!props.disabled&&!busy.value&&!uncertain.value&&available.value&&!!selected.value&&reason.value.trim().length>=10&&attest.value&&(!needsCounterpartyConfirmation.value||attestCounterparty.value));
const money=(value,currency=props.row.currency)=>formatCents(toCents(value),currency||'UZS');
const systemCategories=new Set([N_('Не распределено — поступления из банка'),N_('Не распределено — списания из банка')]);
const categoryLabel=name=>systemCategories.has(name)?tx(name):statementCategoryName(name);
const endpoint=()=>`/api/bank-statements/lines/${encodeURIComponent(props.row.id)}/${kind.value}`;
const token=()=>kind.value==='historical-transfer'?preview.value?.preview_token:preview.value?.token;
function clearPreview(){preview.value=null;confirm.value=false;error.value='';notice.value='';}
function invalidate(){generation++;if(busy.value)finish();open.value=false;kind.value='';options.value=null;target.value='';reason.value='';attest.value=false;attestCounterparty.value=false;confirm.value=false;preview.value=null;error.value='';notice.value='';uncertain.value=false;}
watch(()=>[props.companyId,props.canResolve,JSON.stringify(props.row)],invalidate);
watch([target,reason,attest,attestCounterparty],()=>{if(updating)return;generation++;if(busy.value)finish();clearPreview();},{flush:'sync'});
watch(target,()=>{if(!updating)attestCounterparty.value=false;},{flush:'sync'});
watch(kind,()=>{generation++;if(busy.value)finish();options.value=null;target.value='';reason.value='';attest.value=false;attestCounterparty.value=false;clearPreview();if(open.value&&kind.value)loadOptions();},{flush:'sync'});
onUnmounted(()=>{generation++;if(busy.value)finish();});
function inScope(current,company,line){return current===generation&&props.companyId===company&&String(props.row.id)===String(line)&&props.canResolve&&eligible.value&&open.value;}
function start(){busy.value=true;error.value='';notice.value='';emit('working',true);}
function finish(){busy.value=false;emit('working',false);}
function message(e,fallback){return e?.status&&e.message?tx(e.message):tx(fallback);}
async function toggle(){
 if(!props.canResolve||!eligible.value||props.disabled||busy.value)return;
 if(open.value){if(uncertain.value)return;invalidate();return;}
 open.value=true;if(kinds.value.length===1)kind.value=kinds.value[0];
}
async function loadOptions(){
 if(!props.canResolve||!eligible.value||!open.value||!kinds.value.includes(kind.value)||busy.value||props.disabled||uncertain.value)return;
 clearPreview();options.value=null;target.value='';attest.value=false;attestCounterparty.value=false;
 const current=++generation,company=props.companyId,line=props.row.id,scenario=kind.value;start();
 try{
  const result=await props.api(`${endpoint()}/options`);
  if(!inScope(current,company,line)||scenario!==kind.value)return;
  const entries=scenario==='historical-transfer'?result?.accounts:result?.items;
  const matchingSource=scenario!=='historical-transfer'||String(result.line?.id)===String(line)&&result.source_account?.currency===props.row.currency&&(!props.row.account_id||String(result.source_account.id)===String(props.row.account_id));
  if(!matchingSource||!Array.isArray(entries)||entries.some(item=>!Number.isInteger(itemId(item))||itemId(item)<=0||item.currency!==props.row.currency||scenario==='distinct-income'&&String(item.line_id)===String(line)||scenario==='historical-transfer'&&String(item.id)===String(result.source_account?.id)||scenario==='historical-request'&&item.requires_counterparty_confirmation===true&&(!item.bank_inn?.trim()||!item.bank_counterparty?.trim())))throw new Error('invalid options');
  options.value=result;
  if(result.available===false)error.value=result.reason?tx(result.reason):tx('Для этой строки нет доступного решения.');
 }catch(e){if(inScope(current,company,line))error.value=message(e,N_('Не удалось получить варианты решения. Обновите строку и повторите.'));}
 finally{if(current===generation)finish();}
}
function input(){
 const common={reason:reason.value.trim()};
 if(kind.value==='historical-transfer')return {...common,to_account_id:Number(target.value)};
 if(kind.value==='historical-request')return {...common,request_id:Number(target.value),attest_bank_fact:true,...(needsCounterpartyConfirmation.value?{attest_counterparty:attestCounterparty.value}:{})};
 return {...common,distinct_from_line_id:Number(target.value),attest_bank_fact:true};
}
function sameFinancialSource(result){
 const operation=kind.value==='historical-transfer'?result.operation:result;
 const amount=toCents(props.row.amount);
 return amount!==null&&amount>0&&String(result.line_id)===String(props.row.id)&&operation?.currency===props.row.currency&&operation.date===props.row.date&&toCents(operation.amount)===amount;
}
async function check(){
 if(!canPreview.value)return;
 const current=++generation,company=props.companyId,line=props.row.id,body=input();clearPreview();start();
 try{
  const result=await props.api(`${endpoint()}/preview`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!inScope(current,company,line))return;
  const signature=kind.value==='historical-transfer'?result?.preview_token:result?.token;
  const matchingTarget=kind.value==='historical-transfer'?String(result.destination_account?.id)===target.value:kind.value==='historical-request'?String(result.request_id)===target.value:String(result.distinct_from_line_id)===target.value;
  const matchingEffect=kind.value==='historical-transfer'?result.operation?.kind==='transfer'&&result.destination_account?.currency===props.row.currency&&String(result.source_account?.id)===String(options.value.source_account?.id):result.effect===(kind.value==='historical-request'?'historical_request_payment':'distinct_income')&&(kind.value!=='historical-request'||result.previous_status==='cancelled');
  const matchingCounterparty=kind.value!=='historical-request'||(result.requires_counterparty_confirmation===true)===needsCounterpartyConfirmation.value&&(!needsCounterpartyConfirmation.value||result.bank_inn===selected.value.bank_inn&&result.bank_counterparty===selected.value.bank_counterparty);
  if(typeof signature!=='string'||!signature.trim()||!matchingTarget||!matchingEffect||!matchingCounterparty||!sameFinancialSource(result))throw new Error('invalid preview');
  preview.value=result;confirm.value=false;
 }catch(e){if(inScope(current,company,line))error.value=message(e,N_('Не удалось проверить выбранное решение. Обновите строку и проверьте заново.'));}
 finally{if(current===generation)finish();}
}
async function commit(){
 if(!props.canResolve||!eligible.value||props.disabled||busy.value||uncertain.value||!preview.value||!token()||!confirm.value||!attest.value||!selected.value||(needsCounterpartyConfirmation.value&&!attestCounterparty.value)||reason.value.trim().length<10)return;
 const current=++generation,company=props.companyId,line=props.row.id,scenario=kind.value;
 const body=scenario==='historical-transfer'?{...input(),preview_token:token(),confirm_bank_fact:true,confirm_ownership:true}:{...input(),token:token(),confirm:true};
 start();
 try{
  const result=await props.api(`${endpoint()}/commit`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!inScope(current,company,line))return;
  if(String(result?.line_id)!==String(line)||result.status!==definitions[scenario].status||!Number.isInteger(result.ledger_id)||result.ledger_id<=0)throw new Error('unverified result');
  preview.value=null;confirm.value=false;updating=true;attest.value=false;attestCounterparty.value=false;updating=false;notice.value=tx('Учёт банковского факта сохранён. Данные выписки и отчётов обновляются.');emit('changed',result);
 }catch(e){
  if(!inScope(current,company,line))return;
  if(!e?.status||e.status>=500){uncertain.value=true;confirm.value=false;error.value=tx('Ответ об учёте не получен. Проверьте результат по сохранённой строке; повторно подтверждать сейчас нельзя.');}
  else{clearPreview();error.value=message(e,N_('Не удалось учесть банковский факт. Обновите строку и проверьте решение заново.'));}
 }finally{if(current===generation)finish();}
}
async function refreshResult(){
 if(!props.canResolve||!open.value||busy.value||props.disabled||!uncertain.value||!props.row.batch_id)return;
 const current=++generation,company=props.companyId,line=props.row.id,scenario=kind.value;start();
 try{
  const result=await props.api(`/api/bank-statements/${encodeURIComponent(props.row.batch_id)}`);
  if(!inScope(current,company,line))return;
  const row=Array.isArray(result?.rows)?result.rows.find(item=>String(item.id)===String(line)):null;
  if(String(result?.id)!==String(props.row.batch_id)||!row)throw new Error('invalid saved row');
  clearPreview();updating=true;attest.value=false;attestCounterparty.value=false;updating=false;uncertain.value=false;
  if(row.ledger_id&&row.status===definitions[scenario].status){notice.value=tx('Учёт банковского факта подтверждён сохранённой строкой.');emit('changed',row);}
  else if(row.ledger_id){notice.value=tx('Строка уже учтена другим способом. Обновите список и проверьте историю.');emit('changed',row);}
  else{notice.value=tx('Строка остаётся на проверке. Проверьте решение заново перед подтверждением.');emit('refresh');}
 }catch(e){if(inScope(current,company,line))error.value=message(e,N_('Не удалось проверить результат. Повторное подтверждение остаётся заблокированным.'));}
 finally{if(current===generation)finish();}
}
</script>

<template>
 <section v-if="canResolve&&eligible" class="bank-resolution" :aria-label="tx('Решение по спорной строке')">
  <button type="button" class="secondary tiny" :disabled="disabled||busy||uncertain" :aria-expanded="open" @click="toggle">{{tx(open?N_('Скрыть решение по строке'):N_('Разобрать спорную строку'))}}</button>
  <div v-if="open" class="resolution-fields">
   <p class="sub">{{tx('Администратор учитывает уже совершённый банковский факт. Новая платёжная команда в банк не отправляется.')}}</p>
   <p><b>{{formatShortDate(row.date)}} · {{money(row.amount)}} {{row.currency}}</b><br>{{row.correspondent_name || '—'}} · {{row.document_number || '—'}}</p>
   <p class="sub">{{tx('Счёт на сайте')}}: {{options?.source_account?.name||row.account_name||'—'}} · {{row.currency}}</p>
   <p v-if="row.correspondent_account||options?.line?.correspondent_account" class="sub">{{tx('Счёт корреспондента в выписке')}}: {{row.correspondent_account||options.line.correspondent_account}}</p>
   <p v-if="row.purpose" class="sub">{{row.purpose}}</p>
   <label>{{tx('Какое событие подтверждают банковские документы?')}}<select v-model="kind" :disabled="disabled||busy||uncertain"><option value="">{{tx('Выберите решение')}}</option><option v-for="scenario in kinds" :key="scenario" :value="scenario">{{tx(definitions[scenario].title)}}</option></select></label>
   <template v-if="kind&&options">
    <label v-if="items.length">{{tx(kind==='historical-transfer'?N_('Свой счёт получателя'):kind==='historical-request'?N_('Закрытая заявка'):N_('Другое поступление с тем же номером'))}}<select v-model="target" :disabled="disabled||busy||uncertain"><option value="">{{tx('Выберите запись')}}</option><option v-for="item in items" :key="itemId(item)" :value="String(itemId(item))"><template v-if="kind==='historical-transfer'">{{item.name}} · {{item.currency}}</template><template v-else>{{item.number||item.document_number}} · {{item.counterparty}} · {{formatShortDate(item.date)}} · {{money(item.amount,item.currency)}} {{item.currency}}</template></option></select></label>
    <p v-if="!items.length" class="sub">{{tx(kind==='historical-transfer'?N_('Добавьте свой банковский счёт получателя в разделе «Банк и касса»: та же компания и валюта, дата начала учёта не позже даты перевода. Затем обновите варианты решения.'):N_('Подходящих записей нет. Строка остаётся на проверке.'))}}</p>
    <button v-if="!items.length" type="button" class="secondary tiny" :disabled="disabled||busy||uncertain" @click="loadOptions">{{tx('Обновить варианты решения')}}</button>
    <p v-if="kind==='historical-transfer'&&selected" class="sub">{{tx(selected.correspondence_confirmed?N_('Счёт получателя подтверждён реквизитами выписки.'):N_('Реквизиты счёта получателя не подтверждены автоматически. Проверьте принадлежность компании по банковским документам.'))}}</p>
    <p v-if="kind==='distinct-income'&&selected" class="sub">{{selected.purpose}}</p>
    <label>{{tx('Основание по банковским документам (не менее 10 символов)')}}<textarea v-model="reason" :disabled="disabled||busy||uncertain" minlength="10" maxlength="1000"></textarea></label>
    <label class="resolution-check"><input v-model="attest" type="checkbox" :disabled="disabled||busy||uncertain"> {{tx(definition.attestation)}}</label>
    <p v-if="needsCounterpartyConfirmation" class="sub">{{selected.bank_counterparty}} · {{tx('ИНН')}} {{selected.bank_inn}}</p>
    <label v-if="needsCounterpartyConfirmation" class="resolution-check"><input v-model="attestCounterparty" type="checkbox" :disabled="disabled||busy||uncertain"> {{tx('Подтверждаю по договору или счёту: получатель в закрытой заявке — этот контрагент из выписки (ИНН {inn}).',{inn:selected.bank_inn})}}</label>
    <button type="button" class="secondary" :disabled="!canPreview" @click="check">{{tx('Проверить решение')}}</button>
   </template>
   <p v-if="busy" role="status">{{tx('Проверяем данные…')}}</p><p v-if="error" class="error" role="alert">{{error}}</p><p v-if="notice" role="status">{{notice}}</p>
   <div v-if="preview" class="resolution-preview">
    <h4>{{tx('Проверьте результат перед учётом')}}</h4>
    <p v-if="kind==='historical-transfer'">{{preview.source_account?.name}} → {{preview.destination_account?.name}}</p>
    <p v-else>{{preview.counterparty}} · {{categoryLabel(preview.category_name)}}</p>
    <p v-if="kind==='historical-request'">{{selected.number}} · {{tx('Закрыта без оплаты')}}</p>
    <p>{{formatShortDate(row.date)}} · <b>{{money(row.amount)}} {{row.currency}}</b></p>
    <p v-if="kind==='distinct-income'" class="sub">{{tx('В учёт попадёт только выбранное отдельное поступление. Другое поступление не будет проведено повторно.')}}</p>
    <p v-else-if="kind==='historical-request'" class="sub">{{tx('Будет учтена историческая оплата по выписке. Это не новая команда на оплату закрытой заявки.')}}</p>
    <p v-else class="sub">{{tx('Будет учтён выполненный перевод между своими счетами. Перевод не является внешним расходом Cash Flow.')}}</p>
    <p v-if="preview.reconciliation" class="sub">{{tx('Остаток банка: {bank} {currency}; после учёта: {site} {currency}; непроведённые списания: {pending} {currency}.',{bank:money(preview.reconciliation.bank_closing),site:money(preview.reconciliation.projected_closing),pending:money(preview.reconciliation.projected_pending_debit_total),currency:row.currency})}}</p>
    <p v-if="kind==='historical-transfer'&&preview.reconciliation?.destination_balance_after!=null" class="sub">{{tx('Остаток счёта получателя: {before} {currency}; после учёта: {after} {currency}.',{before:money(preview.reconciliation.destination_balance_before),after:money(preview.reconciliation.destination_balance_after),currency:row.currency})}}</p>
    <label class="resolution-check"><input v-model="confirm" type="checkbox" :disabled="disabled||busy||uncertain"> {{tx('Я проверил результат и подтверждаю учёт этого банковского факта.')}}</label>
    <button type="button" :disabled="disabled||busy||uncertain||!confirm" @click="commit">{{tx(definition.action)}}</button>
   </div>
   <button v-if="uncertain" type="button" class="secondary" :disabled="disabled||busy" @click="refreshResult">{{tx('Проверить результат учёта')}}</button>
  </div>
 </section>
</template>

<style scoped>
.bank-resolution{margin-top:12px;padding-top:10px;border-top:1px solid var(--line,#ddd)}.resolution-fields{display:grid;gap:10px;margin-top:10px;min-width:240px;max-width:520px}.resolution-fields label:not(.resolution-check){display:grid;gap:5px}.resolution-check{display:block}.resolution-preview{display:grid;gap:8px;padding:12px;background:#f6f8fc;border:1px solid var(--line,#ddd);border-radius:8px}.resolution-preview h4,.resolution-preview p{margin:0}.resolution-fields textarea{min-height:72px}
</style>
