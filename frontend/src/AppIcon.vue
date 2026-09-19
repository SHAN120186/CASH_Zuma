<script setup>
defineProps({name:String});
const paths={
 home:['M3 11 12 3l9 8','M5 10v11h5v-7h4v7h5V10'],
 accounts:['M3 9 12 4l9 5H3Z','M5 10v8m5-8v8m4-8v8m5-8v8M3 21h18'],
 ledger:['M4 7h15l-4-4','M20 17H5l4 4'],
 requests:['M8 4H5v17h14V4h-3','M8 3h8v4H8z','m8 14 3 3 5-6'],
 calendar:['M4 5h16v16H4z','M8 3v4m8-4v4M4 10h16'],
 budgets:['M12 3v9h9','M9 4a9 9 0 1 0 11 11'],
 import:['M12 16V3m-5 5 5-5 5 5','M4 15v6h16v-6'],
 report:['M4 3v18h17','M8 17v-5m5 5V7m5 10V4'],
 categories:['M4 5h3v3H4zM11 6h9M4 11h3v3H4zM11 12h9M4 17h3v3H4zM11 18h9'],
 users:['M15 21v-3a5 5 0 0 0-10 0v3','M10 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8','M17 4a4 4 0 0 1 0 7m1 3a5 5 0 0 1 3 5v2'],
 audit:['M5 3h14v18H5z','M9 8h6m-6 4h6m-6 4h4'],
 schedules:['M3 5h18v14H3z','m3 6 9 7 9-7'],
 profile:['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18','M12 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6','M6 19a6 6 0 0 1 12 0'],
 cash:['M3 5h18v14H3z','M3 9h18m-6 6h3']
};
</script>
<template><svg class="app-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path v-for="(d,i) in paths[name]||paths.accounts" :key="i" :d="d"/></svg></template>
