import {N_} from '../i18n/index.js';

export const OSV_SCALES = [
  {value:1,label:N_('Единицы валюты')},
  {value:1000,label:N_('Тысячи валюты')},
  {value:1000000,label:N_('Миллионы валюты')},
];
export const OSV_MONTHS = [N_('Январь'),N_('Февраль'),N_('Март'),N_('Апрель'),N_('Май'),N_('Июнь'),N_('Июль'),N_('Август'),N_('Сентябрь'),N_('Октябрь'),N_('Ноябрь'),N_('Декабрь')];
export const OSV_UNITS = {x:{divisor:1,label:N_('Единицы'),digits:2},k:{divisor:1000,label:N_('Тыс.'),digits:1},m:{divisor:1000000,label:N_('Млн'),digits:1}};

export async function fileBase64(file) {
  const bytes=new Uint8Array(await file.arrayBuffer());
  let binary='';
  for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
  return btoa(binary);
}

export function sourceYears(source) {
  const first=Number(source?.year_from||String(source?.first_date||'').slice(0,4));
  const last=Number(source?.year_to||String(source?.last_date||'').slice(0,4));
  if(!Number.isInteger(first)||!Number.isInteger(last)||first<2000||last>2100||first>last)return [];
  return Array.from({length:last-first+1},(_,index)=>first+index);
}

export function periodMonths(start,end) {
  const from=Number(start),to=Number(end);
  if(!Number.isInteger(from)||!Number.isInteger(to)||from<1||to>12||from>to)return [];
  return Array.from({length:to-from+1},(_,index)=>from+index);
}

// Amounts here are confirmed currency units, unlike the operational report's
// integer minor-unit helpers. Preserve null: no source coverage is not zero.
export function sumAmounts(values) {
  if(!values.length||values.some(value=>value==null||value===''||!Number.isFinite(Number(value))))return null;
  let total=0n;
  for(const value of values){
    const match=String(value).match(/^(-?)(\d+)(?:\.(\d{1,2}))?$/);
    if(!match)return null;
    const cents=BigInt(match[2])*100n+BigInt((match[3]||'').padEnd(2,'0'));
    total+=match[1]?-cents:cents;
  }
  const absolute=total<0n?-total:total;
  return `${total<0n?'-':''}${absolute/100n}.${String(absolute%100n).padStart(2,'0')}`;
}

export function osvExportUrl(sourceId,year,scenario,companyId,start,end) {
  const query=new URLSearchParams({year:String(year),scenario,company_id:String(companyId),start_month:String(start),end_month:String(end)});
  return `/api/cashflow-osv/sources/${Number(sourceId)}/export.xlsx?${query}`;
}
