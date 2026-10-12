<script setup>
import {groupAmount,amountInput} from './amountInput.js';
const props=defineProps({modelValue:[String,Number]});
const emit=defineEmits(['update:modelValue']);
function input(event){emit('update:modelValue',amountInput(event,groupAmount(props.modelValue)));}
</script>
<template><input type="text" inputmode="decimal" autocomplete="off" :value="groupAmount(modelValue)" pattern="[0-9 ]+([.][0-9]{1,2})?" @input="input"></template>
