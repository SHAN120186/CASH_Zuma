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

test('company shortcuts are sibling buttons, name the company accessibly and preserve ordinary selection',async t=>{
  const selected=[],reports=[];const view=await fixture(t,{company,canReports:true,onSelect:id=>selected.push(id),onReport:event=>reports.push(event)});
  const primary=find(view.container,node=>node.props?.class==='company-card');primary.props.onClick();assert.deepEqual(selected,[2]);
  assert.equal(all(primary,node=>node.tag==='button').length,1);
  const actions=find(view.container,node=>node.props?.class==='company-report-actions');const buttons=all(actions,node=>node.tag==='button');assert.equal(buttons.length,2);
  assert.match(buttons[0].props['aria-label'],/Бизнес-планы.*Synthetic Zuma/);assert.match(buttons[1].props['aria-label'],/Архив отчётов.*Synthetic Zuma/);
  buttons.forEach(button=>button.props.onClick());assert.deepEqual(reports,[{companyId:2,page:'business'},{companyId:2,page:'reports'}]);assert.deepEqual(selected,[2]);
});

test('no export permission keeps ordinary company selection; disabled report actions cannot emit navigation',async t=>{
  for(const props of [{canReports:false},{canReports:true,disabled:true}]){
    const reports=[];const view=await fixture(t,{company,...props,onReport:event=>reports.push(event)});
    const actions=find(view.container,node=>node.props?.class==='company-report-actions');
    if(!props.canReports)assert.equal(actions,undefined);else {assert.ok(all(actions,node=>node.tag==='button').every(button=>button.props.disabled));all(actions,node=>node.tag==='button').forEach(button=>button.props.onClick());}
    view.state.openReport('business');assert.deepEqual(reports,[]);assert.ok(find(view.container,node=>node.props?.class==='company-card'));
  }
});

test('report shortcuts and company-specific accessible labels follow Uzbek',async t=>{
  const previous=lang.value;t.after(()=>setLang(previous));setLang('uz');
  const view=await fixture(t,{company,canReports:true});const actions=find(view.container,node=>node.props?.class==='company-report-actions');
  assert.match(text(actions),/Biznes-rejalar/);assert.match(text(actions),/Hisobotlar arxivi/);assert.ok(all(actions,node=>node.tag==='button').every(button=>button.props['aria-label'].includes('Synthetic Zuma')));
});
