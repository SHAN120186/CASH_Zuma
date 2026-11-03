// Cash Flow displays one calendar year at a time, in whole-month columns.
// Preserve the statement end day as the fact cutoff; do not imply a partial
// starting month or a cross-year statement can be represented exactly.
export function statementCashFlowContext(data, today) {
 const {currency,period_from:from,period_to:to}=data||{};
 const valid=s=>typeof s==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(s)&&!Number.isNaN(Date.parse(`${s}T00:00:00Z`))&&new Date(`${s}T00:00:00Z`).toISOString().slice(0,10)===s;
 if(!['UZS','USD','EUR'].includes(currency)||!valid(from)||!valid(to)||!valid(today)||from>to||to>today)return null;
 const year=Number(to.slice(0,4)),crossYear=from.slice(0,4)!==to.slice(0,4);
 if(year<2000||year>2100)return null;
 return {currency,year,asOf:to,start:crossYear?1:Number(from.slice(5,7)),end:Number(to.slice(5,7)),crossYear};
}
