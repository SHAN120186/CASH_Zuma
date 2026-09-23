<script setup>
defineProps({name:String});
const paths={
 home:['M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z'],
 accounts:['M3 9 12 4l9 5H3Z','M5 10v8m5-8v8m4-8v8m5-8v8M3 21h18'],
 ledger:['M7 7h13l-3-3M17 17H4l3 3'],
 requests:['M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z','M14 3v5h5M9 14l2 2 4-4'],
 calendar:['M4 5h16v16H4z','M8 3v4m8-4v4M4 10h16'],
 budgets:['M12 3v9h9','M20.5 15A9 9 0 1 1 12 3'],
 import:['M12 16V4m-5 5 5-5 5 5','M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3'],
 report:['M3 17l6-6 4 4 8-8','M15 7h6v6'],
 categories:['M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z','M4 19V5'],
 users:['M15 21v-3a5 5 0 0 0-10 0v3','M10 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8','M17 4a4 4 0 0 1 0 7m1 3a5 5 0 0 1 3 5v2'],
 approval:['M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z','m8 12 3 3 5-6'],
 audit:['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z','M12 7v5l3 2'],
 schedules:['M3 5h18v14H3z','m3 6 9 7 9-7'],
 profile:['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18','M12 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6','M6 19a6 6 0 0 1 12 0'],
 cash:['M4 4h16v4H4z','M5 8v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8M10 12h4'],
 help:['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z','M9.5 9.5a2.5 2.5 0 1 1 4 2c-.9.6-1.5 1.1-1.5 2.2M12 17.2v.1'],
 menu:['M4 7h16M4 12h16M4 17h16'],
 x:['M6 6l12 12M18 6 6 18'],
 edit:['M4 20h4L19 9l-4-4L4 16z','m13.5 6.5 4 4'],
 plus:['M12 5v14M5 12h14'],
 search:['M11 4.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Z','m16 16 4.5 4.5'],
 chev:['m6 9 6 6 6-6'],
 download:['M12 4v11m-5-5 5 5 5-5','M4 19h16'],
 arrow:['M5 12h14m-6-6 6 6-6 6'],
 check:['m5 12.5 4.5 4.5L19 7'],
 restore:['M4 12a8 8 0 1 0 8-8','M4 4v4h4'],
 archive:['M3 4h18v5H3z','M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9M10 13h4'],
 key:['M8 11a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z','m11 12 9-9m-4 4 3 3'],
 refresh:['M20 12a8 8 0 1 1-2.3-5.7','M20 4v5h-5'],
 up:['M12 19V5m-6 6 6-6 6 6'],
 down:['M12 5v14m-6-6 6 6 6-6'],
 file:['M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z','M14 3v5h5'],
 lock:['M5 11h14v9H5z','M8 11V8a4 4 0 0 1 8 0v3']
};
</script>
<template><svg class="app-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path v-for="(d,i) in paths[name]||paths.accounts" :key="i" :d="d"/></svg></template>
