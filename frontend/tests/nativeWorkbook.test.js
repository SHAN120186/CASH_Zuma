import {test} from 'node:test';
import assert from 'node:assert/strict';
import {reactive} from 'vue';
import {compileComponent, mount, settle, text, find, all} from './helpers/mountVue.js';
const component = await compileComponent(new URL('../src/NativeWorkbook.vue', import.meta.url));
const company = {id:2};
const original = () => ({id:7,company_id:2,revision:4,can_edit:true,native_model:{source_name:'Synthetic.xlsx',sheet_count:28,
  parameters:[{key:'Input!B2',sheet:'Input',cell:'B2',label:'Investment',value:100,min:0,max:1000}],
  metrics:[{key:'investment',label:'Investment',sheet:'Calc',cell:'B3',original:100,unit:'USD'}]}});
test('native view preserves original values and sends only explicitly entered native parameters',async t=>{
  const project=original(), calls=[];
  const view=mount(component,{company,project,api:async(url,options)=>{calls.push({url,options});return project;}}); t.after(view.unmount);
  await settle(); assert.match(text(view.container),/100 USD/); assert.match(text(view.container),/Ещё не пересчитано/);
  view.state.parameters['Input!B2']=150; await view.state.calculate();
  assert.equal(calls[0].url,'/api/business-projects/7/native/preview?company_id=2');
  assert.deepEqual(JSON.parse(calls[0].options.body),{revision:4,overrides:{'Input!B2':150}});
  assert.equal(project.native_model.metrics[0].original,100);
});
test('unresolved native formulas block publishing, show the cell and hide stale downloads after parameter edits',async t=>{
  const project=original();project.native_model.preview={blocked:true,formula_count:10,error_count:1,issues:[{sheet:'Calc',cell:'B3',error:'#REF!',message:'Восстановите ссылку.'}]};
  project.native_model.metrics[0].calculated='#REF!';
  const view=mount(component,{company,project,api:async()=>project});t.after(view.unmount);await settle();
  assert.match(text(view.container),/нельзя публиковать/);assert.match(text(view.container),/Calc!B3/);assert.match(text(view.container),/#REF!/);
  assert.equal(find(view.container,n=>n.tag==='a').props.href,'/api/business-projects/7/native.xlsx?company_id=2&revision=4');
  view.state.parameters['Input!B2']=110;await settle();assert.equal(find(view.container,n=>n.tag==='a'),undefined);
});
test('readonly native view cannot submit changes or expose private recalculated download',async t=>{
  const project=original();project.can_edit=false;project.native_model.preview={blocked:false,formula_count:10,error_count:0,issues:[]};
  const calls=[];const view=mount(component,{company,project,api:async(...args)=>calls.push(args)});t.after(view.unmount);await settle();
  await view.state.calculate();assert.equal(calls.length,0);assert.equal(find(view.container,n=>n.tag==='form'||n.tag==='a'),undefined);
});

const readyOriginal = () => {
  const project = original();
  project.native_model.preview = {blocked:false,formula_count:10,error_count:0,issues:[]};
  project.native_model.metrics[0].calculated = 125;
  project.native_model.parameters.push({key:'Input!C2',sheet:'Input',cell:'C2',label:'Read only source',value:17,editable:false});
  return project;
};

test('native preparation requires an explicit month and generates using the preview response revision and editable parameters',async t=>{
  const project=readyOriginal(),calls=[],events=[];
  const checked={...project,revision:5}, response={...checked,status:'ready'};
  const view=mount(component,{company,project,onUpdated:value=>events.push(['updated',value]),onGenerated:()=>events.push(['generated']),
    api:async(url,options)=>{calls.push({url,options});return url.includes('/preview?')?checked:response;}});t.after(view.unmount);await settle();
  assert.equal(view.state.reportStart,'');
  const calendar=find(view.container,node=>node.tag==='input'&&node.props.type==='month');
  assert.ok(Object.hasOwn(calendar.props,'required'));assert.notEqual(calendar.props.required,false);
  assert.equal(calendar.props.min,'2000-01');assert.equal(calendar.props.max,'2097-12');
  await view.state.generate();assert.equal(calls.length,0); // a calendar is never guessed
  view.state.reportStart='2028-04';await view.state.generate();await settle();
  assert.equal(calls[0].url,'/api/business-projects/7/native/preview?company_id=2');
  assert.deepEqual(JSON.parse(calls[0].options.body),{revision:4,overrides:{'Input!B2':100}});
  assert.equal(calls[1].url,'/api/business-projects/7/native/generate?company_id=2');
  assert.equal(calls[1].options.method,'POST');
  assert.deepEqual(JSON.parse(calls[1].options.body),{revision:5,start:'2028-04-01',overrides:{'Input!B2':100}});
  assert.deepEqual(events,[['updated',checked],['updated',response],['generated']]);
  assert.equal(project.native_model.metrics[0].original,100);
  assert.equal(project.native_model.metrics[0].calculated,125);
  assert.match(text(view.container),/даты договоров не меняются/);
});

test('native preparation refreshes edited parameters and stops on fresh blocking checks or readonly callers',async t=>{
  for(const scenario of ['changed','blocked','readonly']){
    const project=readyOriginal(),calls=[];
    if(scenario==='blocked')project.native_model.preview.blocked=true;
    if(scenario==='readonly')project.can_edit=false;
    const view=mount(component,{company,project,api:async(...args)=>{calls.push(args);return project;}});t.after(view.unmount);await settle();
    view.state.reportStart='2028-04';
    if(scenario==='changed')view.state.parameters['Input!B2']=101;
    await view.state.generate();assert.equal(calls.length,scenario==='readonly'?0:scenario==='blocked'?1:2,scenario);
    if(scenario==='changed'){
      assert.deepEqual(JSON.parse(calls[0][1].body).overrides,{'Input!B2':101});
      assert.deepEqual(JSON.parse(calls[1][1].body).overrides,{'Input!B2':101});
    } else if(scenario==='blocked'){
      assert.match(text(view.container),/Исправьте указанные формулы/);
    }
  }
});

test('native primary report has one PDF and one Excel with secondary TEO and closed previous generations',async t=>{
  const project=readyOriginal();project.current_generation_id=31;
  const archive=(id,company_id=2,status='ready')=>({id,company_id,status});
  project.generations=[
    {id:99,native:true,business_archive:archive(990),teo_archive:archive(991)},
    {id:31,native:true,business_archive:archive(310),teo_archive:archive(311)},
    {id:20,native:false,business_archive:archive(200),teo_archive:archive(201)},
  ];
  const view=mount(component,{company,project,api:async()=>project});t.after(view.unmount);await settle();
  const results=find(view.container,node=>node.props?.class==='native-results');
  const downloads=all(results,node=>node.tag==='a'&&String(node.props.href).startsWith('/api/report-archives/'));
  assert.deepEqual(downloads.map(node=>node.props.href),[
    '/api/report-archives/310/files/pdf?company_id=2','/api/report-archives/310/files/xlsx?company_id=2',
    '/api/report-archives/311/files/pdf?company_id=2',
  ]);
  assert.deepEqual(downloads.map(node=>text(node).trim()),['Бизнес-план · PDF','Бизнес-план · Excel','ТЭО · PDF']);
  assert.equal(all(view.container,node=>node.tag==='a'&&String(node.props.href).includes('/files/xlsx')).length,1);
  const teo=find(results,node=>node.tag==='details');assert.notEqual(teo.props.open,true);
  assert.equal(project.generations[0].business_archive.id,990);
});

test('native report links cannot expose another company or a deleted archive and ignore a generic selected generation',async t=>{
  for(const scenario of ['foreign','deleted','generic']){
    const project=readyOriginal();project.current_generation_id=31;
    project.generations=[{id:31,native:scenario!=='generic',
      business_archive:{id:310,company_id:scenario==='foreign'?3:2,status:scenario==='deleted'?'deleted':'ready'},
      teo_archive:{id:311,company_id:scenario==='foreign'?3:2,status:scenario==='deleted'?'deleted':'ready'}}];
    const view=mount(component,{company,project,api:async()=>project});t.after(view.unmount);await settle();
    assert.equal(all(view.container,node=>node.tag==='a'&&String(node.props.href).startsWith('/api/report-archives/')).length,0,scenario);
  }
});

test('native generation suppresses a duplicate in-flight submit and reports server failure without altering original figures',async t=>{
  const project=readyOriginal(),calls=[],events=[];let reject;
  const view=mount(component,{company,project,onGenerated:()=>events.push('generated'),api:async(...args)=>{
    calls.push(args);return new Promise((resolve,fail)=>{reject=fail;});}});t.after(view.unmount);await settle();
  view.state.reportStart='2028-04';
  const pending=view.state.generate();await settle();
  assert.equal(view.state.busy,true);
  await view.state.generate();assert.equal(calls.length,1);
  reject(Error('Проверьте исходный Word.'));await pending;await settle();
  assert.equal(view.state.busy,false);assert.deepEqual(events,[]);
  assert.match(text(view.container),/Проверьте исходный Word/);
  assert.equal(project.native_model.metrics[0].original,100);
  assert.equal(project.native_model.metrics[0].calculated,125);
  assert.equal(view.state.parameters['Input!B2'],100);
  assert.equal(view.state.reportStart,'2028-04');
});

test('native groups convert percentages to fractions, preserve blanks and keep locked credit captions out of editable inputs',async t=>{
  const project=readyOriginal(),calls=[];
  project.native_model.parameters=[
    {key:'Стоим_проекта!B38',sheet:'Стоим_проекта',cell:'B38',label:'FX',value:10000,min:0.000001},
    {key:'Стоим_проекта!D28',sheet:'Стоим_проекта',cell:'D28',label:'Credit',value:100,min:0},
    {key:'РКЛ!AA6',sheet:'РКЛ',cell:'AA6',label:'Rate',value:'0.12',unit:'доля',min:0,max:1},
    {key:'Производство!B19',sheet:'Производство',cell:'B19',label:'Loading',value:'0.8',unit:'доля',min:0,max:1},
    {key:'Производство!Q19',sheet:'Производство',cell:'Q19',label:'Growth',value:'0.01',unit:'доля',min:0,max:1},
    {key:'НДС!F4',sheet:'НДС',cell:'F4',label:'VAT',value:'0.12',unit:'доля',min:0,max:1},
    ...['B5','B6','B7','B10'].map(cell=>({key:'Раб_капит!'+cell,sheet:'Раб_капит',cell,label:'Days '+cell,value:30,unit:'дней',min:1})),
    ...['AA7','AA8','AA10'].map(cell=>({key:'РКЛ!'+cell,sheet:'РКЛ',cell,label:'Locked '+cell,value:6,editable:false})),
  ];
  const view=mount(component,{company,project,api:async(url,options)=>{calls.push({url,...options});return project;}});t.after(view.unmount);await settle();
  assert.equal(all(view.container,node=>Object.hasOwn(node.props||{},'data-native-group')).length,4);
  assert.equal(all(view.container,node=>node.tag==='input'&&node.props.inputmode==='decimal').length,10);
  assert.equal(view.state.parameters['РКЛ!AA6'],'12');
  assert.equal(view.state.parameters['Производство!B19'],'80');
  assert.equal(view.state.parameters['Производство!Q19'],'1');
  view.state.parameters['РКЛ!AA6']='15';
  await view.state.calculate();await settle();
  const values=JSON.parse(calls[0].body).overrides;
  assert.equal(values['РКЛ!AA6'],0.15);assert.equal(values['Производство!B19'],0.8);assert.equal(values['Производство!Q19'],0.01);
  assert.equal(Object.keys(values).length,10);assert.equal(values['РКЛ!AA7'],undefined);
  view.state.parameters['Стоим_проекта!D28']='';await view.state.calculate();await settle();
  assert.equal(calls.length,1);assert.match(text(view.container),/Пустое поле не означает ноль/);
  const technical=find(view.container,node=>node.props?.class==='native-technical');assert.notEqual(technical.props.open,true);
  assert.equal(all(view.container,node=>node.tag==='article').length,3);
});

test('native report remains available as a labelled previous calculation after edits and a repreview invalidates its generation',async t=>{
  const project=reactive(readyOriginal());project.status='ready';project.inputs={start:'2028-04-01'};project.current_generation_id=31;
  const archive=id=>({id,company_id:2,status:'ready'});
  project.generations=[{id:31,revision:4,native:true,business_archive:archive(310),teo_archive:archive(311)}];
  const view=mount(component,{company,project,api:async()=>project});t.after(view.unmount);await settle();
  assert.equal(view.state.stale,false);assert.equal(find(view.container,node=>node.props?.class==='native-results').tag,'section');
  view.state.parameters['Input!B2']=101;await settle();
  assert.equal(view.state.stale,true);assert.match(text(view.container),/Предыдущая сохранённая версия/);
  const previous=find(view.container,node=>node.props?.class==='native-results');assert.equal(previous.tag,'details');assert.notEqual(previous.props.open,true);
  project.current_generation_id=null;project.status='needs_data';project.revision=5;await settle();
  assert.equal(view.state.stale,true);assert.ok(view.state.files.some(file=>file.url.includes('/310/')));
  project.current_generation_id=32;project.status='ready';project.generations.unshift({id:32,revision:5,native:true,business_archive:archive(320),teo_archive:archive(321)});
  view.state.parameters['Input!B2']=100;await settle();
  assert.equal(view.state.stale,false);assert.equal(find(view.container,node=>node.props?.class==='native-results').tag,'section');
});

test('native preparation never generates after a foreign preview response or after the view unmounts',async t=>{
  for(const unmount of [false,true]){
    const project=readyOriginal(),calls=[];let resolve;
    const view=mount(component,{company,project,api:async(...args)=>{calls.push(args);return new Promise(done=>{resolve=done;});}});
    t.after(view.unmount);await settle();view.state.reportStart='2028-04';
    const pending=view.state.generate();await settle();
    if(unmount)view.unmount();
    resolve({...project,company_id:unmount?2:9});await pending;await settle();
    assert.equal(calls.length,1);
    if(!unmount)assert.match(text(view.container),/другой компании/);
  }
});
