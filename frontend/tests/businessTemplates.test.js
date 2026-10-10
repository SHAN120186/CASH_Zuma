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

test('new project offers private originals first, needs exactly Excel and Word and asks no invented forecast month',async t=>{
  const calls=[];
  const view=mount(component,{company,api:async(url,options)=>{calls.push({url,options});return{items:[],can_upload:true};}});t.after(view.unmount);await settle();
  await button(view.container,'Новый проект').props.onClick();await settle();
  assert.equal(view.state.projectMode,'template');assert.match(text(view.container),/Сохранённых шаблонов пока нет/);
  assert.equal(find(view.container,node=>node.tag==='input'&&node.props.type==='month'),undefined);
  view.state.header.title='Synthetic project';view.state.chooseSources({target:{files:[file('Synthetic.xlsx')],value:'selection'}});
  await view.state.upload();await settle();assert.equal(calls.filter(call=>call.options?.method).length,0);assert.match(view.state.error,/один оригинальный Excel.*один Word/);
  assert.equal(button(view.container,'Загрузить оригинал и перейти').props.disabled,true);
  view.state.chooseSources({target:{files:[file('Synthetic.xlsx'),file('Business plan.docx')],value:'selection'}});await settle();
  assert.equal(view.state.uploadRequirement,'');assert.equal(button(view.container,'Загрузить оригинал и перейти').props.disabled,false);
});

test('original pair import creates files mode, uploads real revisions sequentially and analyses without a guessed date',async t=>{
  let stored=project({revision:0,native_model:undefined,files:[]});const calls=[];
  const view=mount(component,{company,api:async(url,options)=>{
    calls.push({url,options});if(!options)return{items:[],can_upload:true};
    if(url.includes('/sources?')){stored={...stored,revision:stored.revision+1,files:[...stored.files,{filename:decodeURIComponent(options.headers['X-Filename'])}]};return stored;}
    if(url.includes('/analyse?')){stored={...stored,native_model:{parameters:[],metrics:[]}};return stored;}
    return stored;
  }});t.after(view.unmount);await settle();view.state.newProject('template');await settle();view.state.header.title='Synthetic project';
  view.state.chooseSources({target:{files:[file('Synthetic.xlsx'),file('Business plan.docx')],value:'selection'}});
  await view.state.upload();await settle();const writes=calls.filter(call=>call.options?.method);
  assert.equal(JSON.parse(writes[0].options.body).mode,'files');assert.equal(writes[0].url,'/api/business-projects?company_id=2');
  assert.match(writes[1].url,/expected_revision=0$/);assert.match(writes[2].url,/expected_revision=1$/);
  assert.equal(writes[1].options.body.name,'Synthetic.xlsx');assert.equal(writes[2].options.body.name,'Business plan.docx');
  assert.deepEqual(JSON.parse(writes[3].options.body),{revision:2,overrides:{}});
  assert.equal(view.state.current.mode,'files');assert.equal(view.state.header.start,'');assert.equal(view.state.flowStage,'data');assert.equal(view.state.progress.completed,view.state.progress.total);
});

test('selected private template is copied through explicit create then analysis; unknown creation reuses the same request',async t=>{
  const calls=[];let loseFirst=true;
  const view=mount(component,{company,api:async(url,options)=>{
    calls.push({url,options});if(!options)return{items:url.includes('business-templates')?[template]:[],can_upload:true};
    if(url==='/api/business-projects?company_id=2'&&loseFirst){loseFirst=false;throw new TypeError('Failed to fetch');}
    return project();
  }});t.after(view.unmount);await settle();view.state.newProject('template');await settle();view.state.header.title='Template project';view.state.selectedTemplateId=9;
  await view.state.useTemplate();await settle();assert.equal(view.state.state.createUnknown,true);assert.equal(view.state.current,null);
  view.state.header.title='Changed UI title';await view.state.useTemplate();await settle();const writes=calls.filter(call=>call.options?.method);
  const initial=JSON.parse(writes[0].options.body);assert.equal(initial.mode,'files');assert.equal(initial.template_id,9);assert.equal(initial.title,'Template project');assert.equal(initial.company_id,2);assert.match(initial.request_key,/^[0-9a-f-]{36}$/i);
  assert.deepEqual(JSON.parse(writes[1].options.body),initial);
  assert.deepEqual(JSON.parse(writes[2].options.body),{revision:2,overrides:{}});
  assert.equal(view.state.flowStage,'data');assert.equal(view.state.state.createUnknown,false);assert.match(text(view.container),/2. Новые данные/);
});

test('template save is scoped, revision checked, idempotent across a lost response and leaves project revision unchanged',async t=>{
  const value=project(),calls=[];let loseFirst=true;
  const view=mount(component,{company,api:async(url,options)=>{
    calls.push({url,options});if(!options)return url.includes('/7?')?value:{items:url.includes('business-templates')?[template]:[value],can_upload:true};
    if(loseFirst){loseFirst=false;throw new TypeError('Failed to fetch');}return template;
  }});t.after(view.unmount);await settle();await view.state.openProject(value);await settle();
  assert.equal(view.state.canSaveTemplate,true);view.state.templateTitle='Saved original pair';await view.state.saveTemplate();await view.state.saveTemplate();await settle();
  const writes=calls.filter(call=>call.options?.method);assert.equal(writes.length,2);assert.equal(writes[0].url,'/api/business-projects/7/save-template?company_id=2');
  assert.deepEqual(JSON.parse(writes[0].options.body),JSON.parse(writes[1].options.body));assert.equal(JSON.parse(writes[0].options.body).revision,2);assert.equal(view.state.current.revision,2);
  assert.match(view.state.notice,/Шаблон сохранён/);assert.equal(view.state.templates[0].id,9);
});

test('two Word candidates and readonly projects do not offer or invoke saving a template',async t=>{
  for(const value of [project({can_edit:false}),project({files:[{filename:'Business plan.docx'},{filename:'Other business plan.docx'},{filename:'Synthetic.xlsx'}]})]){
    const calls=[];const view=mount(component,{company,api:async(url,options)=>{calls.push({url,options});return url.includes('/7?')?value:{items:[value],can_upload:true};}});t.after(view.unmount);await settle();await view.state.openProject(value);await settle();
    assert.equal(view.state.canSaveTemplate,false);assert.equal(button(view.container,'Сохранить Excel и Word как шаблон'),undefined);await view.state.saveTemplate();assert.equal(calls.filter(call=>call.options?.method).length,0);
  }
});

test('new documents retain an existing native project, use revision control and need no forecast header',async t=>{
  let value=project();const calls=[];
  const view=mount(component,{company,api:async(url,options)=>{calls.push({url,options});if(!options)return url.includes('/7?')?value:{items:[value],can_upload:true};if(url.includes('/sources?'))value={...value,revision:3};return value;}});t.after(view.unmount);await settle();await view.state.openProject(value);await settle();
  view.state.chooseSources({target:{files:[file('New prices.xlsx')],value:'selection'}});assert.equal(view.state.header.start,'');await view.state.upload();await settle();
  const writes=calls.filter(call=>call.options?.method);assert.equal(writes.length,2);assert.equal(writes[0].url,'/api/business-projects/7/sources?company_id=2&expected_revision=2');assert.deepEqual(JSON.parse(writes[1].options.body),{revision:3,overrides:{}});
  assert.equal(view.state.current.id,7);assert.equal(view.state.flowStage,'data');
});

test('company changes discard stale private templates and stop template analysis after a pending copy',async t=>{
  const list=deferred(),copy=deferred(),calls=[],props=reactive({company:{...company},api:async(url,options)=>{
    calls.push({url,options});if(options)return copy.promise;if(url.includes('business-templates'))return url.includes('company_id=2')?list.promise:{items:[],can_upload:true};return{items:[],can_upload:true};
  }});
  const view=mount(component,props);t.after(view.unmount);await settle();view.state.newProject('template');await settle();props.company.id=3;await settle();list.resolve({items:[template],can_upload:true});await settle();assert.deepEqual(view.state.templates,[]);
  props.company.id=2;await settle();view.state.newProject('template');await settle();view.state.header.title='Template project';view.state.selectedTemplateId=9;
  const pending=view.state.useTemplate();await settle();props.company.id=3;await settle();copy.resolve(project());await pending;await settle();
  assert.equal(view.state.current,null);assert.equal(calls.filter(call=>call.url.includes('/analyse?')).length,0);assert.equal(view.state.progress.label,'Проекты загружены');
});

test('identical original Word copies remain saveable while unrelated documents are ignored',async t=>{
  const value=project({files:[{filename:'Business plan.docx',sha256:'a'.repeat(64)},{filename:'Copy business plan.docx',sha256:'a'.repeat(64)},{filename:'Contract.docx',sha256:'b'.repeat(64)}]});
  const view=mount(component,{company,api:async url=>url.includes('/7?')?value:{items:[value],can_upload:true}});t.after(view.unmount);await settle();await view.state.openProject(value);await settle();
  assert.equal(view.state.canSaveTemplate,true);assert.ok(button(view.container,'Сохранить Excel и Word как шаблон'));
});
