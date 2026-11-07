import {test} from 'node:test';
import assert from 'node:assert/strict';
import {reactive} from 'vue';
import {compileComponent,mount,settle,text,find,all} from './helpers/mountVue.js';
const component=await compileComponent(new URL('../src/BusinessProjects.vue',import.meta.url));
const company={id:2,name:'Synthetic company'};
const template={id:9,title:'Synthetic template',xlsx_name:'Synthetic.xlsx',docx_name:'Business plan.docx',created_at:'2026-10-10T00:00:00+00:00'};
const project=(extra={})=>({id:7,company_id:2,revision:2,can_edit:true,title:'Synthetic project',mode:'files',status:'needs_data',files:[{filename:'Synthetic.xlsx'},{filename:'Business plan.docx'}],inputs:{},extraction:{},native_model:{parameters:[],metrics:[]},generations:[],...extra});
const button=(root,phrase)=>find(root,node=>node.tag==='button'&&text(node).includes(phrase));
const file=(name)=>({name,size:12,webkitRelativePath:''});
const deferred=()=>{let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail;});return{promise,resolve,reject};};

test('new projects are manual and have no financial Excel or template controls',async t=>{
 const calls=[];const view=mount(component,{company,api:async(url,options)=>{calls.push({url,options});return{items:[],can_upload:true};}});t.after(view.unmount);await settle();view.state.newProject('template');await settle();
 assert.equal(view.state.projectMode,'manual');assert.equal(all(view.container,n=>n.tag==='input'&&n.props.type==='file').length,0);
 assert.doesNotMatch(text(view.container),/Выбрать Excel и Word|Использовать шаблон|Импорт оригинальных/);assert.equal(calls.filter(c=>c.url.includes('business-templates')).length,0);
});
test('legacy import actions cannot issue mutations or template requests',async t=>{
 const calls=[];const view=mount(component,{company,api:async(url,options)=>{calls.push({url,options});return{items:[],can_upload:true};}});t.after(view.unmount);await settle();view.state.newProject();
 for(const action of ['upload','useTemplate','saveTemplate']){await view.state[action]();assert.match(view.state.error,/Импорт финансовых моделей Excel отключён/);}
 assert.equal(calls.filter(c=>c.options?.method||c.url.includes('business-templates')).length,0);
});
test('saved native project has no edit or import controls',async t=>{
 const value=project();const view=mount(component,{company,canDownload:true,api:async url=>url.includes('/7?')?value:{items:[value],can_upload:true}});t.after(view.unmount);await settle();await view.state.openProject(value);await settle();
 assert.equal(view.state.canEdit,false);assert.equal(view.state.canSaveTemplate,false);assert.equal(all(view.container,n=>n.tag==='input'&&n.props.type==='file').length,0);assert.match(text(view.container),/Импорт финансовых моделей Excel отключён/);
});
