import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,find,all,text} from './helpers/mountVue.js';
import {lang,setLang} from '../src/i18n/index.js';
const component=await compileComponent(new URL('../src/CompanyCard.vue',import.meta.url));
const company={id:2,code:'ZUMA',name:'Synthetic Zuma'};
async function fixture(t,props){
  const previous=Object.getOwnPropertyDescriptor(globalThis,'window');globalThis.window={matchMedia(){return {matches:false};}};
  const view=mount(component,props);t.after(()=>{view.unmount();if(previous)Object.defineProperty(globalThis,'window',previous);else delete globalThis.window;});await settle();return view;
}

test('company chooser contains only the company card and preserves accessible selection',async t=>{
  const selected=[];const view=await fixture(t,{company,onSelect:id=>selected.push(id)});
  const primary=find(view.container,node=>node.props?.class==='company-card');primary.props.onClick();assert.deepEqual(selected,[2]);
  assert.equal(all(view.container,node=>node.tag==='button').length,1);
  assert.equal(primary.props['aria-label'],'Открыть компанию Synthetic Zuma');
  assert.equal(find(view.container,node=>node.props?.class==='company-report-actions'),undefined);
});

test('busy company selection disables the sole company card',async t=>{
  const selected=[];const view=await fixture(t,{company,disabled:true,onSelect:id=>selected.push(id)});
  const buttons=all(view.container,node=>node.tag==='button');
  assert.equal(buttons.length,1);assert.equal(buttons[0].props.disabled,true);
  buttons[0].props.onClick();assert.deepEqual(selected,[]);
});

test('a suspended company explains the restriction and cannot emit selection',async t=>{
 const selected=[];const view=await fixture(t,{company:{...company,active:false},onSelect:id=>selected.push(id)});
 const button=find(view.container,n=>n.tag==='button');
 assert.equal(button.props.disabled,true);assert.equal(button.props['aria-label'],'Компания Synthetic Zuma отключена');
 const explanation=find(view.container,n=>n.props?.id===button.props['aria-describedby']);
 assert.equal(text(explanation),'Компания отключена. Обратитесь к администратору.');
 button.props.onClick();assert.deepEqual(selected,[]);
 view.app._instance.props.company={...company,active:true};await settle();
 assert.equal(button.props.disabled,false);button.props.onClick();assert.deepEqual(selected,[2]);
});

test('all five companies retain their names and distinct visual identities',async t=>{
 const tones=[];
 for(const [index,code] of ['UZGERMED','ZUMA','SAMO','FAZO','UZYARN'].entries()){
  const selected=[];const view=await fixture(t,{company:{id:index+1,code,name:code,active:true},onSelect:id=>selected.push(id)});
  assert.equal(text(find(view.container,n=>n.tag==='b')),code);
  tones.push(String(find(view.container,n=>String(n.props?.class||'').includes('company-card-shell')).props.class).match(/company-tone-\w+/)[0]);
  find(view.container,n=>n.tag==='button').props.onClick();assert.deepEqual(selected,[index+1]);
 }
 assert.equal(new Set(tones).size,5);
});

test('the suspended company message is localized without translating its name',async t=>{
 const previous=lang.value;t.after(()=>setLang(previous));setLang('uz');
 const view=await fixture(t,{company:{id:6,code:'UZYARN',name:'UZYARN',active:false}});
 assert.match(text(view.container),/Kompaniya o‘chirilgan\. Administratorga murojaat qiling\./);
 assert.equal(find(view.container,n=>n.tag==='button').props['aria-label'],'UZYARN kompaniyasi o‘chirilgan');
});

test('the sole company card retains its Uzbek accessible label',async t=>{
  const previous=lang.value;t.after(()=>setLang(previous));setLang('uz');
  const view=await fixture(t,{company});const primary=find(view.container,node=>node.props?.class==='company-card');
  assert.equal(primary.props['aria-label'],'Synthetic Zuma kompaniyasini ochish');
  assert.equal(all(view.container,node=>node.tag==='button').length,1);assert.equal(text(find(primary,node=>node.tag==='b')),'ZUMA');
});
