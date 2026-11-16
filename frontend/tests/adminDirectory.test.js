import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,find,all,text} from './helpers/mountVue.js';
import {lang,setLang} from '../src/i18n/index.js';

const component=await compileComponent(new URL('../src/admin/AdminCenter.vue',import.meta.url));
const companies=[{id:2,code:'UZGERMED',name:'UZGERMED'},{id:3,code:'ZUMA',name:'ZUMA'},
  {id:4,code:'SAMO',name:'SAMO'},{id:5,code:'FAZO',name:'FAZO'}];
const users=[
  {id:99,name:'Synthetic admin',username:'holding.admin',active:true,holding_role:'admin',holding_label:'Администратор холдинга',state:'ok',state_label:'Действует',assignments:[]},
  {id:7,name:'Samo accountant',username:'samo.accountant',active:true,holding_role:null,state:'ok',state_label:'Действует',company_id:4,company_role:'accountant',assignments:[{company_id:4,role:'accountant',role_label:'Расчётный бухгалтер',valid:true,company:'SAMO'}]},
  {id:8,name:'Fazo employee',username:'fazo.employee',active:true,holding_role:null,state:'ok',state_label:'Действует',company_id:5,company_role:'employee',assignments:[{company_id:5,role:'employee',role_label:'Заявитель',valid:true,company:'FAZO'}]},
];
const meta={companies,company_roles:[['accountant','Расчётный бухгалтер'],['employee','Заявитель']],
  holding_roles:[['admin','Администратор холдинга']],states:{ok:'Действует'}};

async function fixture(t,{failReset=false,cardReply=null}={}) {
  const previous=Object.getOwnPropertyDescriptor(globalThis,'document');
  globalThis.document={activeElement:null,addEventListener(){},removeEventListener(){}};
  const calls=[];
  const view=mount(component,{currentUserId:99,api:async(url,options)=>{
    calls.push([url,options]);
    if(url==='/api/admin/meta')return meta;
    if(url==='/api/admin/users')return users;
    if(url==='/api/admin/users/7')return typeof cardReply==='function' ? cardReply() : cardReply || {...users[1],sessions:0,history:[]};
    if(url==='/api/admin/users/7/password'&&options?.method==='POST') {
      if(failReset)throw new Error('Synthetic reset error');
      return {user:users[1],temporary_password:'Synthetic-New-Secret-123!'};
    }
    throw new Error('Unexpected request: '+url);
  }});
  t.after(()=>{view.unmount();if(previous)Object.defineProperty(globalThis,'document',previous);else delete globalThis.document;});
  await settle();
  return {...view,calls};
}
const directory=view=>find(view.container,n=>n.props?.id==='company-access-directory');

test('directory uses loaded company choices and changes company without fetching or resetting',async t=>{
  const view=await fixture(t);
  assert.equal(directory(view),undefined);
  const toggle=find(view.container,n=>n.props?.['aria-controls']==='company-access-directory');
  toggle.props.onClick();await settle();
  const tabs=find(directory(view),n=>n.props?.class==='admin-company-tabs');
  assert.deepEqual(all(tabs,n=>n.tag==='button').map(text),companies.map(c=>c.name));
  find(tabs,n=>n.tag==='button'&&text(n)==='SAMO').props.onClick();await settle();
  assert.match(text(directory(view)),/samo.accountant/);
  assert.doesNotMatch(text(directory(view)),/fazo.employee/);
  assert.match(text(directory(view)),/holding.admin/);
  view.state.directoryFilters.q='missing';await settle();
  assert.match(text(directory(view)),/Никого не найдено/);
  assert.equal(view.calls.length,2,'opening, switching and filtering use the existing registry');
  assert.equal(view.calls.filter(([,options])=>options).length,0);
});

test('directory password action requires explicit confirmation and closes only after success',async t=>{
  const view=await fixture(t);
  view.state.showDirectory=true;view.state.directoryCompanyId=4;await settle();
  const row=find(directory(view),n=>n.tag==='tr'&&text(n).includes('samo.accountant'));
  await find(row,n=>n.tag==='button'&&text(n)==='Сбросить пароль').props.onClick();await settle();
  assert.equal(view.state.form.kind,'password');
  assert.equal(view.calls.filter(([,options])=>options).length,0,'viewing the reset form does not reset the password');
  const reason=find(view.container,n=>n.tag==='textarea');assert.ok(Object.hasOwn(reason.props,'required'));
  view.state.form.reason='Explicit synthetic administrator confirmation';
  await view.state.submit();await settle();
  const writes=view.calls.filter(([,options])=>options);
  assert.equal(writes.length,1);assert.equal(writes[0][0],'/api/admin/users/7/password');
  assert.equal(JSON.parse(writes[0][1].body).reason,'Explicit synthetic administrator confirmation');
  assert.equal(view.state.card,null);assert.equal(view.state.form,null);
  assert.equal(view.state.secret.password,'Synthetic-New-Secret-123!');
  assert.match(text(view.container),/Показывается один раз/);
});

test('failed directory password reset keeps the confirmation form and reason',async t=>{
  const view=await fixture(t,{failReset:true});
  await view.state.resetFromDirectory(users[1]);
  view.state.form.reason='Keep this reason after a failed reset';
  await view.state.submit();await settle();
  assert.equal(view.state.card.id,7);assert.equal(view.state.form.kind,'password');
  assert.equal(view.state.form.reason,'Keep this reason after a failed reset');
  assert.equal(view.state.secret,null);assert.match(text(view.container),/Synthetic reset error/);
});

test('Uzbek directory explains explicit audited password recovery',async t=>{
  const previous=lang.value;t.after(()=>setLang(previous));setLang('uz');
  const view=await fixture(t);view.state.showDirectory=true;await settle();
  assert.match(text(directory(view)),/Kompaniyalar bo‘yicha kirishlar/);
  assert.match(text(directory(view)),/parolni/);
  assert.match(text(directory(view)),/administrator/);
});

test('changing directory company discards a pending earlier user card and one-time secret',async t=>{
  let resolveCard;
  const cardReply=new Promise(resolve=>{resolveCard=resolve;});
  const view=await fixture(t,{cardReply});
  view.state.directoryCompanyId=4;await settle();
  const pending=view.state.openCard(users[1]);await settle();
  view.state.secret={password:'Synthetic-Secret-Not-For-Next-Company'};
  view.state.directoryCompanyId=5;await settle();
  assert.equal(view.state.secret,null);
  resolveCard({...users[1],sessions:0,history:[]});await pending;await settle();
  assert.equal(view.state.card,null);
  assert.doesNotMatch(text(view.container),/Synthetic-Secret-Not-For-Next-Company/);
});

test('custom reset submits exact entered password only after confirmation',async t=>{
  const view=await fixture(t);
  await view.state.resetFromDirectory(users[1]);
  view.state.form.password=' Synthetic-Manual-Secret_2026! ';
  view.state.form.reason='Explicit synthetic password change';
  assert.equal(view.calls.filter(([,options])=>options).length,0);
  await view.state.submit();await settle();
  const reset=view.calls.find(([url,options])=>url.endsWith('/password')&&options?.method==='POST');
  assert.equal(JSON.parse(reset[1].body).password,' Synthetic-Manual-Secret_2026! ');
});

test('a late registry refresh cannot reopen a user card after it was closed',async t=>{
  let resolveRefresh,cardCalls=0;
  const view=await fixture(t,{cardReply:()=>++cardCalls===1 ? {...users[1],sessions:0,history:[]} : new Promise(resolve=>{resolveRefresh=resolve;})});
  await view.state.openCard(users[1]);await settle();
  const refreshing=view.state.load();await settle();
  view.state.closeCard();await settle();
  resolveRefresh({...users[1],sessions:0,history:[]});await refreshing;await settle();
  assert.equal(view.state.card,null);
});
