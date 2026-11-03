import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,text,find} from './helpers/mountVue.js';
import {statementCashFlowContext} from '../src/cashflow/statementContext.js';
import {lang,setLang} from '../src/i18n/index.js';
const component=await compileComponent(new URL('../src/BankStatementHistory.vue',import.meta.url));
const item={id:9,filename:'synthetic.xls',account:{id:1,name:'Synthetic bank',currency:'USD'},period_from:'2026-05-10',period_to:'2026-06-20',credit_total:'100.00',debit_total:'25.00'};
const states=['active','reversed','changed','missing','unposted'];
const detail={...item,status:'committed',statement:{period_from:item.period_from,period_to:item.period_to},rows:states.map((state,i)=>({id:i+1,date:'2026-06-01',correspondent_name:'Synthetic',correspondent_inn:'123',document_number:`D${i}`,direction:'out',purpose:'Synthetic',amount:'5.00',status:'auto_outgoing_posted',operation_state:state,request_id:null}))};

test('confirmed history loads only on request, is read-only, paginates and describes actual operation states',async t=>{
 const calls=[],links=[];const api=async(url,options={})=>{calls.push([url,options]);if(url.includes('/history?'))return {page:url.includes('page=2')?2:1,has_more:!url.includes('page=2'),items:[item]};if(url.endsWith('/history/9'))return detail;throw Error('unexpected')};
 const view=mount(component,{api,companyId:1,canView:true,canViewCashFlow:true,onCashFlow:x=>links.push(x)});t.after(view.unmount);await settle();assert.deepEqual(calls,[]);
 await view.state.toggle();await settle();assert.equal(calls[0][0],'/api/bank-statements/history?page=1&page_size=30');assert.match(text(view.container),/synthetic.xls/);
 await view.state.openDetail(item);await settle();for(const label of ['Проведена','Сторнирована','Операция изменена','Операция не найдена','Не проведена'])assert.match(text(view.container),new RegExp(label));
 view.state.openCashFlow();assert.deepEqual(links,[{currency:'USD',period_from:'2026-05-10',period_to:'2026-06-20'}]);assert.ok(calls.every(([,opts])=>!opts.method||opts.method==='GET'));
 await view.state.loadPage(2);await settle();assert.equal(view.state.page,2);assert.equal(view.state.detail,null);assert.equal(view.state.hasMore,false);
});

test('history remains inaccessible without authorization and hides cash flow without its right',async t=>{
 let calls=0;const api=async url=>{calls++;return url.includes('/history?')?{page:1,has_more:false,items:[item]}:detail};
 const view=mount(component,{api,companyId:1,canView:false,canViewCashFlow:false});t.after(view.unmount);await settle();await view.state.toggle();assert.equal(calls,0);assert.doesNotMatch(text(view.container),/История банковских выписок/);
 view.app._instance.props.canView=true;await settle();await view.state.toggle();await view.state.openDetail(item);await settle();assert.doesNotMatch(text(view.container),/Открыть Cash Flow/);
 view.app._instance.props.canView=false;await settle();await view.state.openDetail(item);assert.equal(calls,2);assert.equal(view.state.detail,null);
});

test('late list and detail responses cannot cross companies or restore a closed section',async t=>{
 let finishList,finishDetail;const api=async url=>url.includes('/history?')?new Promise(resolve=>finishList=resolve):new Promise(resolve=>finishDetail=resolve);
 const view=mount(component,{api,companyId:1,canView:true});t.after(view.unmount);await settle();let pending=view.state.toggle();await settle();view.app._instance.props.companyId=2;await settle();finishList({page:1,has_more:false,items:[item]});await pending;assert.equal(view.state.items.length,0);assert.equal(view.state.open,false);
 pending=view.state.toggle();await settle();finishList({page:1,has_more:false,items:[item]});await pending;const read=view.state.openDetail(item);await settle();await view.state.toggle();finishDetail(detail);await read;assert.equal(view.state.detail,null);assert.equal(view.state.open,false);
});

test('cash flow preserves statement currency and months, with explicit cross-year and invalid-date handling',()=>{
 assert.deepEqual(statementCashFlowContext({currency:'USD',period_from:'2026-05-10',period_to:'2026-06-20'},'2026-11-03'),{currency:'USD',year:2026,asOf:'2026-06-20',start:5,end:6,crossYear:false});
 assert.deepEqual(statementCashFlowContext({currency:'EUR',period_from:'2025-12-10',period_to:'2026-01-05'},'2026-11-03'),{currency:'EUR',year:2026,asOf:'2026-01-05',start:1,end:1,crossYear:true});
 for(const candidate of [{currency:'USD',period_from:'2026-02-31',period_to:'2026-03-01'},{currency:'UZS',period_from:'2026-01-01',period_to:'2027-01-01'},{currency:'GBP',period_from:'2026-01-01',period_to:'2026-02-01'}])assert.equal(statementCashFlowContext(candidate,'2026-11-03'),null);
});

test('statement history and state labels are available in Uzbek',async t=>{
 const prior=lang.value;setLang('uz');t.after(()=>setLang(prior));const api=async url=>url.includes('/history?')?{page:1,has_more:false,items:[item]}:detail;
 const view=mount(component,{api,companyId:1,canView:true});t.after(view.unmount);await view.state.toggle();await view.state.openDetail(item);await settle();assert.match(text(view.container),/Bank ko‘chirmalari tarixi/);assert.match(text(view.container),/Storno qilingan/);
});
