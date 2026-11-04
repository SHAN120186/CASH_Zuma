<script setup>
import {computed} from 'vue';
import {tx} from './i18n/index.js';
import {toCents, formatCents} from './overview/format.js';
const props=defineProps({row:{type:Object,required:true},currency:{type:String,default:'UZS'}});
const group=computed(()=>props.row.cash_funding);
const complete=computed(()=>group.value?.component==='gross'&&toCents(group.value.net_amount)!==null&&toCents(group.value.fee_amount)!==null);
const money=value=>formatCents(toCents(value),props.currency);
</script>
<template><small v-if="complete">{{tx('В кассу: {net}; комиссия: {fee} {currency}.',{net:money(group.net_amount),fee:money(group.fee_amount),currency})}}</small></template>
