import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent, mount, settle, find} from './helpers/mountVue.js';

const reportComponent=await compileComponent(new URL('../src/CashFlowReport.vue',import.meta.url));
const appComponent=await compileComponent(new URL('../src/App.vue',import.meta.url),{'./CashFlowReport.vue':reportComponent});
const zero=()=>Array(12).fill('0.00'),empty=()=>Array(12).fill(null);

function syntheticReport(query){
  return {company_id:2,currency:query.get('currency'),year:Number(query.get('year')),scenario:query.get('scenario'),
    as_of:query.get('as_of'),notes:{},rows:[],groups:[],totals:zero(),plan_totals:empty(),
    opening:zero(),closing:zero(),opening_adjustments:zero(),plan_opening:empty(),plan_closing:empty(),
    plan_versions:Array(12).fill(0),plan_opening_input:empty()};
}

test('mounted App refreshes server today after Tashkent midnight and preserves an explicit historical report date',async t=>{
  const originals={Date:globalThis.Date,fetch:globalThis.fetch,window:globalThis.window,location:globalThis.location,document:globalThis.document};
  const RealDate=originals.Date;
  let instant='2026-10-11T18:59:59.000Z';
  globalThis.Date=class extends RealDate{
    constructor(...args){super(...(args.length?args:[instant]));}
    static now(){return new RealDate(instant).getTime();}
  };
  globalThis.window={addEventListener(){},localStorage:{getItem:()=>null}};
  globalThis.location={hash:'#report'};
  globalThis.document={hidden:true,activeElement:null,addEventListener(){},removeEventListener(){},querySelector:()=>({scrollIntoView(){}})};
  const user={id:1,username:'synthetic.director',name:'Synthetic',role:'director',permissions:['view','export','plan']};
  const calls=[];
  globalThis.fetch=async url=>{
    calls.push(url);
    if(url==='/api/me')return {ok:false,status:401,json:async()=>({detail:'Synthetic unauthenticated startup'})};
    if(url==='/api/bootstrap')return {ok:true,json:async()=>({user,csrf:'synthetic-token',
      today:new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Tashkent'}),accounts:[],categories:[],
      companies:[{id:2,name:'Synthetic company',code:'SYNTHETIC'}],roles:{}})};
    assert.ok(url.startsWith('/api/report?'),'only synthetic read APIs are permitted');
    return {ok:true,json:async()=>syntheticReport(new URL(url,'http://isolated.test').searchParams)};
  };
  const view=mount(appComponent);
  t.after(()=>{view.unmount();Object.assign(globalThis,originals);});
  await settle();
  Object.assign(view.state,{user,companyId:2,companyReady:true,year:2026,currency:'UZS',reportScenario:'A',reportAsOf:'2026-07-31'});
  await view.state.bootstrap();await view.state.load();await settle();
  assert.equal(view.state.boot.today,'2026-10-11');
  assert.equal(view.state.reportAsOf,'2026-07-31');
  assert.equal(calls.filter(url=>url==='/api/bootstrap').length,1);
  const dateInput=()=>find(view.container,node=>node.tag==='input'&&node.props.type==='date');
  assert.equal(dateInput().props.max,'2026-10-11');

  // UTC stays on October 11 while the company's business day advances in Tashkent.
  instant='2026-10-11T19:00:01.000Z';
  const before=calls.length;
  await view.state.load();await settle();
  assert.equal(view.state.boot.today,'2026-10-12');
  assert.equal(view.state.reportAsOf,'2026-07-31','a valid user-selected historical cut-off is retained');
  assert.equal(dateInput().props.max,'2026-10-12');
  assert.equal(calls[before],'/api/bootstrap','refresh the business date before requesting the report');
  const request=new URL(calls.at(-1),'http://isolated.test');
  assert.equal(request.pathname,'/api/report');
  assert.equal(request.searchParams.get('as_of'),'2026-07-31');
  assert.equal(calls.filter(url=>url==='/api/bootstrap').length,2);

  await view.state.load();await settle();
  assert.equal(calls.filter(url=>url==='/api/bootstrap').length,2,'the same business day does not trigger another bootstrap');
  assert.equal(view.state.boot.today,'2026-10-12');
  assert.equal(view.state.reportAsOf,'2026-07-31');
});
