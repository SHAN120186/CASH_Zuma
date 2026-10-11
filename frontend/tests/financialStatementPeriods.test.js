import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setLang,lang} from '../src/i18n/index.js';
import {aggregateStatementRow,cellNumber,statementDeviation,statementScaledNumber,statementScaledValue,statementScaledDeviation} from '../src/financialStatements.js';

const cell=(value,month,side,status='cached')=>({
  value:value == null ? null : String(value),status,
  source:`Synthetic!${side}${month}`,formula:`=SyntheticInput!${side}${month}`,
});
const months=Array.from({length:12},(_,index)=>index+1);
function row(number,plan=months,fact=plan,measure='amount') {
  return {row:number,key:'synthetic_'+number,label:'Synthetic source row',kind:measure === 'amount' ? 'amount' : 'ratio',measure,
    cells:months.map((month,index)=>({month,plan:cell(plan[index],month,'P'),fact:cell(fact[index],month,'F')}))};
}
function report(rows,sheet='09_Statements') {
  return {sheet,periods:months.map(month=>({month,period:`2026-${String(month).padStart(2,'0')}`})),rows};
}
const available=months.map(month=>({month,status:'available',record_count:1}));
const select=(startMonth,endMonth,extra={})=>({startMonth,endMonth,year:2026,coverage:available,...extra});

test('PNL money totals are exact, preserve explicit zero and every source cell, and do not mutate source',()=>{
  const amounts=row(7,['0.1','0.2','0.3',...months.slice(3)],['0','-0.2','0.3',...months.slice(3)]);
  const source=report([amounts]);const before=JSON.stringify(source);
  const total=aggregateStatementRow(source,amounts,select(1,3));
  assert.equal(total.mode,'sum');assert.equal(total.plan.value,'0.6');assert.equal(total.fact.value,'0.1');
  assert.equal(total.plan.status,'aggregated');assert.equal(cellNumber(total.plan),0.6);
  assert.deepEqual(total.fact.provenance.map(item=>item.source),['Synthetic!F1','Synthetic!F2','Synthetic!F3']);
  assert.deepEqual(total.fact.provenance.map(item=>item.period),['2026-01','2026-02','2026-03']);
  assert.ok(total.fact.provenance.every(item=>item.formula && item.row === 7));
  assert.equal(JSON.stringify(source),before);
  const annual=aggregateStatementRow(report([row(7)]),row(7),select(1,12));
  assert.equal(annual.fact.value,'78');assert.equal(annual.fact.provenance.length,12);
});

test('one unconfirmed month suppresses the full PNL total and deviation without exposing a partial sum',()=>{
  for (const status of ['missing','missing_cache','future','error','invalid']) {
    const amounts=row(7);
    amounts.cells[1].fact={...amounts.cells[1].fact,status,value:'999',source_cache:'999'};
    const total=aggregateStatementRow(report([amounts]),amounts,select(1,3));
    assert.equal(total.plan.value,'6');assert.equal(total.fact.value,null,status);
    assert.equal(total.fact.reason,'incomplete_period');assert.deepEqual(total.fact.missingMonths,[2]);
    assert.equal(total.fact.provenance.length,3);assert.equal(cellNumber(total.fact),null);
    assert.deepEqual(statementDeviation(total.plan,total.fact),{value:null,percent:null});
  }
  const amounts=row(7);amounts.cells[1].fact.value=null;
  assert.equal(aggregateStatementRow(report([amounts]),amounts,select(1,3)).fact.value,null);
});

test('fact coverage must be available even if an inconsistent cached cell is numeric',()=>{
  const amounts=row(7),source=report([amounts]);
  for (const status of ['missing','invalid','future']) {
    const coverage=available.map(item=>item.month === 2 ? {...item,status} : item);
    const total=aggregateStatementRow(source,amounts,select(1,3,{coverage}));
    assert.equal(total.plan.value,'6');assert.equal(total.fact.value,null);
    assert.deepEqual(total.fact.missingMonths,[2]);
  }
  assert.equal(aggregateStatementRow(source,amounts,select(1,3,{coverage:[]})).fact.value,null);
  // Server cell statuses already include coverage when an explicit coverage array is absent.
  assert.equal(aggregateStatementRow(source,amounts,{startMonth:1,endMonth:3}).fact.value,'6');
});

test('missing, mismatched and duplicate headers never inherit the selected year',()=>{
  for (const configure of [
    source=>source.periods[1].period=null,
    source=>source.periods[1].period='2025-02',
    source=>source.periods[1].period='2026-03',
    source=>source.periods.push({...source.periods[1]}),
  ]) {
    const amounts=row(7),source=report([amounts]);configure(source);
    const total=aggregateStatementRow(source,amounts,select(1,3));
    assert.equal(total.plan.value,null);assert.equal(total.fact.value,null);
    assert.equal(total.reason,'invalid_period');assert.deepEqual(total.plan.missingMonths,[2]);
  }
  const amounts=row(7);
  assert.equal(aggregateStatementRow(report([amounts]),amounts,select(1,3,{year:2025})).plan.value,null);
});

test('reversed and invalid custom ranges are rejected instead of silently reordering',()=>{
  const amounts=row(7),source=report([amounts]);
  for (const options of [select(7,3),select(0,3),select(1,13),select(1.5,3),select('bad',3)]) {
    const total=aggregateStatementRow(source,amounts,options);
    assert.equal(total.reason,'invalid_period');assert.equal(total.plan.value,null);
    assert.equal(total.fact.value,null);assert.deepEqual(total.months,[]);
  }
});

test('one-month margins preserve the original cache and formula but still require dates and coverage',()=>{
  const gross=row(12,[10]),revenue=row(7,[100]),margin=row(13,['0.91'],undefined,'percent');
  const source=report([gross,revenue,margin]);
  const selected=aggregateStatementRow(source,margin,select(1,1));
  assert.equal(selected.mode,'source');assert.equal(selected.plan.value,'0.91');
  assert.equal(selected.plan.status,'cached');assert.equal(selected.plan.formula,'=SyntheticInput!P1');
  assert.equal(selected.plan.provenance.length,1);
  const missingCoverage=select(1,1,{coverage:[{month:1,status:'missing'}]});
  assert.equal(aggregateStatementRow(source,margin,missingCoverage).fact.value,null);
  source.periods[0].period=null;
  assert.equal(aggregateStatementRow(source,margin,select(1,1)).plan.value,null);
});

test('period gross and net margins divide full period bases, rather than averaging source percentages',()=>{
  const revenue=row(7,[100,900]),gross=row(12,[10,180]),net=row(23,[5,45]);
  const grossMargin=row(13,['0.1','0.2'],undefined,'percent');
  const netMargin=row(24,['0.05','0.05'],undefined,'percent');
  const source=report([revenue,gross,net,grossMargin,netMargin]);
  const gm=aggregateStatementRow(source,grossMargin,select(1,2));
  assert.equal(gm.plan.value,'0.19');assert.equal(gm.plan.status,'calculated');
  assert.notEqual(gm.plan.value,'0.15');assert.equal(gm.fact.provenance.length,4);
  assert.deepEqual(gm.fact.provenance.map(item=>item.row),[12,12,7,7]);
  assert.deepEqual(gm.fact.provenance.map(item=>item.role),['numerator','numerator','denominator','denominator']);
  assert.equal(aggregateStatementRow(source,netMargin,select(1,2)).fact.value,'0.05');
  gross.cells[1].fact.status='error';
  const incomplete=aggregateStatementRow(source,grossMargin,select(1,2));
  assert.equal(incomplete.plan.value,'0.19');assert.equal(incomplete.fact.value,null);
  assert.deepEqual(incomplete.fact.missingMonths,[2]);assert.equal(incomplete.fact.provenance.length,4);
});

test('zero revenue and missing ratio bases are undefined, while zero profit with revenue is an explicit zero',()=>{
  const margin=row(13,[0,0],undefined,'percent'),gross=row(12,[0,0]),revenue=row(7,[0,0]);
  const source=report([margin,gross,revenue]);
  const noRevenue=aggregateStatementRow(source,margin,select(1,2));
  assert.equal(noRevenue.fact.value,null);assert.equal(noRevenue.fact.reason,'zero_base');
  revenue.cells[0].plan.value='100';revenue.cells[0].fact.value='100';
  assert.equal(aggregateStatementRow(source,margin,select(1,2)).fact.value,'0');
  const noBase=aggregateStatementRow(report([margin]),margin,select(1,2));
  assert.equal(noBase.fact.value,null);assert.equal(noBase.fact.reason,'ratio_base_missing');
  const unknown=row(25,[1,2],undefined,'coefficient');
  const unsupported=aggregateStatementRow(report([unknown]),unknown,select(1,2));
  assert.equal(unsupported.fact.value,null);assert.equal(unsupported.reason,'ratio_method_required');
});

test('balance and balance KPIs use only the exact endpoint, without summing or falling back to prior facts',()=>{
  for (const measure of ['amount','percent','days','coefficient']) {
    const balances=row(measure === 'amount' ? 19 : 73,[10,20,30,40],[11,22,33,44],measure);
    const source=report([balances],'09b_Balance');
    balances.cells[1].fact.status='missing';source.periods[0].period=null;
    const endpoint=aggregateStatementRow(source,balances,select(1,3));
    assert.equal(endpoint.mode,'end_month');assert.equal(endpoint.plan.value,'30');assert.equal(endpoint.fact.value,'33');
    assert.equal(endpoint.fact.provenance.length,1);assert.equal(endpoint.fact.provenance[0].month,3);
    balances.cells[3].fact.status='future';
    const missingEnd=aggregateStatementRow(source,balances,select(1,4));
    assert.equal(missingEnd.plan.value,'40');assert.equal(missingEnd.fact.value,null);
    assert.deepEqual(missingEnd.fact.missingMonths,[4]);
  }
});

test('display divisors apply only to original monetary amounts and monetary deviations',t=>{
  const previous=lang.value;t.after(()=>setLang(previous));setLang('ru');
  const amount=row(7),percent=row(13,[],[],'percent'),days=row(65,[],[],'days'),coefficient=row(73,[],[],'coefficient');
  assert.equal(statementScaledNumber(cell('1234567.891234',1,'P'),amount,1000,'en-US'),'1,234.57');
  assert.equal(statementScaledNumber(cell('1234567.891234',1,'P'),amount,1000000,'en-US'),'1.23');
  assert.equal(statementScaledNumber(cell('0.25',1,'P'),percent,1000000,'en-US'),'25.00%');
  assert.equal(statementScaledNumber(cell('42',1,'P'),days,1000000,'en-US'),'42.00 дн.');
  assert.equal(statementScaledNumber(cell('1.25',1,'P'),coefficient,1000000,'en-US'),'1.25');
  assert.equal(statementScaledValue(2500,amount,1000),2.5);
  assert.equal(statementScaledValue(5,days,1000000),5);
  assert.equal(statementScaledValue(.05,percent,1000000),.05);
  assert.equal(statementScaledValue(null,amount,1),null);
  assert.equal(statementScaledNumber(cell('50',1,'P'),amount,0,'en-US'),'—');
  const norm={value:'≥ 1.5 (author)',status:'author_guideline'};coefficient.norm=norm;
  statementScaledNumber(cell('1.25',1,'P'),coefficient,1000,'en-US');assert.deepEqual(coefficient.norm,norm);
});

test('decimal precision and integers beyond Number.MAX_SAFE_INTEGER survive totals and display',()=>{
  const amounts=row(7,['9007199254740993.1234','0.1266'],['9007199254740994.1234','0.1266']);
  const total=aggregateStatementRow(report([amounts]),amounts,select(1,2));
  assert.equal(total.plan.value,'9007199254740993.25');assert.equal(total.fact.value,'9007199254740994.25');
  assert.equal(statementScaledNumber(total.plan,amounts,1,'en-US'),'9,007,199,254,740,993.25');
  assert.equal(statementDeviation(total.plan,total.fact).value,1);
  const precise=row(7,['1.234','2.345']);
  assert.equal(aggregateStatementRow(report([precise]),precise,select(1,2)).fact.value,'3.579');
  const scientific=row(7,['1.2e2','3e-2']);
  assert.equal(aggregateStatementRow(report([scientific]),scientific,select(1,2)).fact.value,'120.03');
});

test('overflow totals, ratios and deviations never display Infinity or replace it with zero',()=>{
  const huge=row(7,['1e308','1e308']);
  const total=aggregateStatementRow(report([huge]),huge,select(1,2));
  assert.equal(total.plan.value,null);assert.equal(total.plan.status,'invalid');assert.equal(total.reason,'nonfinite_result');
  const revenue=row(7,['1e-308','1e-308']),gross=row(12,['1e308','0']),margin=row(13,[0,0],undefined,'percent');
  const ratio=aggregateStatementRow(report([revenue,gross,margin]),margin,select(1,2));
  assert.equal(ratio.fact.value,null);assert.equal(ratio.fact.reason,'nonfinite_result');
  assert.deepEqual(statementDeviation(cell('-1e308',1,'P'),cell('1e308',1,'F')),{value:null,percent:null});
  const tiny=row(7,['1e-500','1e-500']),tinyProfit=row(12,['1e-500','1e-500']);
  assert.equal(aggregateStatementRow(report([tiny,tinyProfit,margin]),margin,select(1,2)).fact.value,'1');
});

test('days and coefficients retain numeric deviations without relative percentages',()=>{
  const plan=cell('1.2',1,'P'),fact=cell('1.5',1,'F');
  assert.deepEqual(statementDeviation(plan,fact,'ratio'),{value:.3,percent:null});
  assert.deepEqual(statementDeviation(cell('36',1,'P'),cell('40',1,'F'),'ratio'),{value:4,percent:null});
});

test('monetary deviation display retains all decimal digits before raw, thousand and million formatting',()=>{
  const amount=row(7),plan=cell('0',1,'P'),fact=cell('9007199254740993.25',1,'F');
  assert.equal(statementScaledDeviation(plan,fact,amount,1,'en-US'),'9,007,199,254,740,993.25');
  assert.equal(statementScaledDeviation(plan,fact,amount,1000,'en-US'),'9,007,199,254,740.99');
  assert.equal(statementScaledDeviation(plan,fact,amount,1000000,'en-US'),'9,007,199,254.74');
  assert.equal(statementScaledDeviation(fact,plan,amount,1,'en-US'),'-9,007,199,254,740,993.25');
  assert.equal(statementScaledDeviation(cell('-0.1234',1,'P'),cell('0.1234',1,'F'),amount,1,'en-US'),'0.25');
  assert.equal(statementScaledDeviation(plan,{...fact,status:'future'},amount,1,'en-US'),'—');
  assert.equal(statementScaledDeviation(cell('-1e308',1,'P'),cell('1e308',1,'F'),amount,1,'en-US'),'—');
});

test('deviation formatter keeps percentage points, days and coefficients independent of money divisors',t=>{
  const previous=lang.value;t.after(()=>setLang(previous));setLang('ru');
  assert.equal(statementScaledDeviation(cell('0.2',1,'P'),cell('0.25',1,'F'),row(13,[],[],'percent'),1000000,'en-US'),'5.00 п. п.');
  assert.equal(statementScaledDeviation(cell('36',1,'P'),cell('40',1,'F'),row(65,[],[],'days'),1000000,'en-US'),'4.00');
  assert.equal(statementScaledDeviation(cell('1.2',1,'P'),cell('1.5',1,'F'),row(73,[],[],'coefficient'),1000000,'en-US'),'0.30');
});

test('balance range KPI flows use the full period, average its opening and closing stocks and calendar days',()=>{
  const balances=[row(9,[100,200,300]),row(13,[200,300,400]),row(18,[500,700,900]),
    row(19,[1000,1500,2000]),row(36,[400,600,800]),row(8,[50,75,100])];
  for (const stock of balances) {
    for (const side of ['plan','fact']) stock.cells[0][side].opening_balance={
      value:String(Number(stock.cells[0][side].value)/2),status:'calculated',source:'Synthetic opening',
    };
  }
  const net=row(23,[10,20,30]),sales=row(7,[100,200,300]),cogs=row(11,[40,80,120]);
  const pnlReport=report([net,sales,cogs]);
  const balance=report(balances,'09b_Balance');
  const options=select(1,3,{pnlReport});
  const ratio=number=>aggregateStatementRow(balance,row(number,[],[],'percent'),options).fact;
  assert.equal(ratio(62).value,'0.1');
  assert.equal(Number(ratio(63).value),60/((500+2000)/2));
  assert.equal(Number(ratio(64).value),60/((200+800)/2));
  assert.equal(Number(ratio(65).value),((50+300)/2)/600*90);
  assert.equal(Number(ratio(66).value),((100+400)/2)/240*90);
  assert.equal(Number(ratio(67).value),300/600);
  assert.equal(Number(ratio(68).value),100/600);
  assert.equal(Number(ratio(70).value),600/((250+900)/2));
  assert.equal(ratio(63).status,'calculated');
  assert.deepEqual(ratio(63).provenance.filter(item=>item.role==='opening_balance').map(item=>item.source),['Synthetic opening']);
  assert.equal(ratio(63).provenance.filter(item=>item.role==='flow').length,3);
  assert.notEqual(ratio(63).value,'0.02');
});

test('range balance KPI ignores saved percentages when a flow month, opening balance or denominator is unconfirmed',()=>{
  const assets=row(19,[1000,1500,2000]);
  const net=row(23,[10,20,30]),sales=row(7,[100,200,300]),ratio=row(63,[.3,.4,.9],undefined,'percent');
  const balance=report([assets,ratio],'09b_Balance'),pnlReport=report([net,sales]);
  const opts=select(1,3,{pnlReport});
  const missingOpening=aggregateStatementRow(balance,ratio,opts).fact;
  assert.equal(missingOpening.value,null);assert.equal(missingOpening.reason,'opening_balance_missing');
  assets.cells[0].fact.opening_balance={value:'500',status:'calculated'};
  net.cells[1].fact.status='missing';
  const partial=aggregateStatementRow(balance,ratio,opts).fact;
  assert.equal(partial.value,null);assert.equal(partial.reason,'incomplete_period');assert.deepEqual(partial.missingMonths,[2]);
  net.cells[1].fact.status='calculated';
  assets.cells[0].fact.opening_balance.value='-2000';
  const zero=aggregateStatementRow(balance,ratio,opts).fact;
  assert.equal(zero.value,null);assert.equal(zero.reason,'zero_denominator');
  assert.equal(aggregateStatementRow(balance,ratio,select(1,3)).fact.reason,'ratio_period_method_required');
});

test('calendar days account for a leap February and EBITDA keeps its allocation reason across ranges',()=>{
  const ar=row(9,[10,20]),sales=row(7,[100,200]),ratio=row(65,[999,999],undefined,'days');
  const balance=report([ar,ratio],'09b_Balance'),pnl=report([sales]);
  for(const source of [balance,pnl])source.periods=source.periods.map(item=>({...item,period:item.period.replace('2026','2024')}));
  ar.cells[0].fact.opening_balance={value:'0',status:'calculated'};
  const total=aggregateStatementRow(balance,ratio,{...select(1,2,{pnlReport:pnl}),year:2024});
  assert.equal(total.fact.value,'2'); // Average 10 / revenue 300 × 60 actual days.
  const ebitda=row(16,[10,20]);
  for(const period of ebitda.cells)period.fact={value:null,status:'requires_allocation',reason:'depreciation_allocation_required',source_cache:'999'};
  const blocked=aggregateStatementRow(report([ebitda]),ebitda,select(1,2)).fact;
  assert.equal(blocked.value,null);assert.equal(blocked.status,'requires_allocation');
  assert.equal(blocked.reason,'depreciation_allocation_required');
});

test('period inventory turnover does not turn a negative COGS flow into a positive denominator',()=>{
 const inventory=row(13,[100,100]),cogs=row(11,[30,-60]),ratio=row(66,[5,5],undefined,'days');
 inventory.cells[0].fact.opening_balance={value:'100',status:'calculated'};
 const value=aggregateStatementRow(report([inventory,ratio],'09b_Balance'),ratio,
   select(1,2,{pnlReport:report([cogs])})).fact;
 assert.equal(value.value,null);assert.equal(value.reason,'nonpositive_flow');
});

test('unrecognized row layout cannot use fixed-position quarter KPI or PNL margin formulas',()=>{
 const assets=row(19,[1000,1500,2000]),roe=row(64,[.1,.2,.91],undefined,'percent');
 assets.cells[0].fact.opening_balance={value:'500',status:'calculated'};
 const balance=report([assets,roe],'09b_Balance');
 const endpoint=aggregateStatementRow(balance,roe,select(1,3,{profileUnrecognized:true,pnlReport:report([row(23,[10,20,30])])}));
 assert.equal(endpoint.mode,'end_month');assert.equal(endpoint.fact.value,'0.91');assert.equal(endpoint.fact.status,'cached');
 const margin=row(13,[.1,.2,.3],undefined,'percent');
 const source=report([row(7,[100,200,300]),row(12,[10,40,90]),margin]);
 const unsafe=aggregateStatementRow(source,margin,select(1,3,{profileUnrecognized:true}));
 assert.equal(unsafe.fact.value,null);assert.equal(unsafe.fact.reason,'profile_unrecognized');
});


test('quarter PNL and sales-based KPI keep the unresolved Mapping classification reason instead of blaming missing months',()=>{
 const sales=row(7,[100,200,300]),gross=row(12,[20,40,60]),net=row(23,[10,20,30]),margin=row(24,[.1,.1,.1],undefined,'percent');
 for(const amount of [sales,gross])for(const pair of amount.cells)pair.fact={value:null,status:'unavailable',reason:'actual_revenue_classification_required',source_cache:'999'};
 const pnl=report([sales,gross,net,margin]);
 const total=aggregateStatementRow(pnl,sales,select(1,3)).fact;
 assert.equal(total.value,null);assert.equal(total.reason,'actual_revenue_classification_required');assert.equal(total.status,'unavailable');
 const netmargin=aggregateStatementRow(pnl,margin,select(1,3)).fact;
 assert.equal(netmargin.value,null);assert.equal(netmargin.reason,'actual_revenue_classification_required');
 const ar=row(9,[100,200,300]),dso=row(65,[90,60,30],undefined,'days');ar.cells[0].fact.opening_balance={value:'50',status:'calculated'};
 const metric=aggregateStatementRow(report([ar,dso],'09b_Balance'),dso,select(1,3,{pnlReport:pnl})).fact;
 assert.equal(metric.value,null);assert.equal(metric.reason,'actual_revenue_classification_required');
});
