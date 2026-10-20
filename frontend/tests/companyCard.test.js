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
  const view=await fixture(t,{company,disabled:true});
  const buttons=all(view.container,node=>node.tag==='button');
  assert.equal(buttons.length,1);assert.equal(buttons[0].props.disabled,true);
});

test('the sole company card retains its Uzbek accessible label',async t=>{
  const previous=lang.value;t.after(()=>setLang(previous));setLang('uz');
  const view=await fixture(t,{company});const primary=find(view.container,node=>node.props?.class==='company-card');
  assert.equal(primary.props['aria-label'],'Synthetic Zuma kompaniyasini ochish');
  assert.equal(all(view.container,node=>node.tag==='button').length,1);assert.equal(text(find(primary,node=>node.tag==='b')),'ZUMA');
});
