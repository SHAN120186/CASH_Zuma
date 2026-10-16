import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent,mount,settle,find,text,all} from './helpers/mountVue.js';
import {createOperationProgress,progressPercent} from '../src/operationProgress.js';
const component=await compileComponent(new URL('../src/OperationProgress.vue',import.meta.url));

test('progress percentages follow completed stages, clamp input and stay unknown without a total',()=>{
  assert.equal(progressPercent(1,3),33);assert.equal(progressPercent(2,3),67);
  assert.equal(progressPercent(200,201),99);
  assert.equal(progressPercent(5,4),100);assert.equal(progressPercent(-2,4),0);
  assert.equal(progressPercent(1,0),null);assert.equal(progressPercent(1,Infinity),null);
});

test('a single request waits at zero until the checked response and does not invent a timed percentage',async()=>{
  const snapshots=[],progress=createOperationProgress(value=>snapshots.push(value));
  const request=progress.begin('Загружаем отчёт',1);
  assert.deepEqual(progress.state,{active:true,label:'Загружаем отчёт',completed:0,total:1,error:''});
  await Promise.resolve();await Promise.resolve();assert.equal(progress.state.completed,0);
  progress.complete(request,'Отчёт загружен');
  assert.deepEqual(progress.state,{active:false,label:'Отчёт загружен',completed:1,total:1,error:''});
  assert.equal(progress.complete(request),false);assert.equal(progress.fail(request,'late failure'),false);
  assert.equal(snapshots.length,2);
});

test('concurrent independent requests aggregate completed stages and one failure never reports full completion',()=>{
  const progress=createOperationProgress();
  const first=progress.begin('Первый отчёт',3),second=progress.begin('Второй отчёт',1);
  progress.advance(first,1,'Первый файл сохранён');progress.complete(second);
  assert.equal(progress.state.active,true);assert.equal(progress.state.completed,2);assert.equal(progress.state.total,4);
  progress.fail(first,Error('Сервер отказал'));
  assert.equal(progress.state.active,false);assert.equal(progressPercent(progress.state.completed,progress.state.total),50);
  assert.equal(progress.state.error,'Сервер отказал');
  const next=progress.begin('Следующий отчёт',1);
  assert.equal(progress.state.error,'');assert.equal(progress.state.completed,0);
  assert.equal(progress.complete(first),false);progress.complete(next);assert.equal(progress.state.completed,1);
});

test('unknown totals remain indeterminate and reset invalidates late callbacks',()=>{
  const progress=createOperationProgress(),unknown=progress.begin('Проверяем модель',0);
  const known=progress.begin('Сохраняем данные',1);progress.complete(known);
  assert.equal(progress.state.active,true);assert.equal(progress.state.total,0);
  progress.progress(unknown,{completed:1,total:3,label:'Проверен первый этап'});
  assert.equal(progress.state.completed,2);assert.equal(progress.state.total,4);
  progress.reset();assert.equal(progress.advance(unknown),false);assert.equal(progress.complete(unknown),false);
  assert.deepEqual(progress.state,{active:false,label:'',completed:0,total:0,error:''});
});

test('the final stage is published only by checked completion and failure keeps its last confirmed stage',()=>{
  const progress=createOperationProgress(),ticket=progress.begin('Сохраняем PDF и Excel',4);
  progress.progress(ticket,{completed:3,total:4,label:'Файлы сохранены'});
  progress.progress(ticket,{completed:4,total:4,label:'Проверяем готовность'});
  assert.equal(progress.state.completed,3);
  progress.fail(ticket,'Версия осталась черновиком');
  assert.equal(progressPercent(progress.state.completed,progress.state.total),75);
  const retry=progress.begin('Повторяем проверку',1);progress.complete(retry);assert.equal(progressPercent(progress.state.completed,progress.state.total),100);
});

test('mounted progress shows accessible actual stage counts and percentage with an active spinner',async t=>{
  const view=mount(component,{active:true,label:'Загружаем Excel',completed:2,total:4});t.after(view.unmount);await settle();
  assert.match(text(view.container),/50%/);assert.match(text(view.container),/Прогресс этапов · 2 из 4/);
  const status=find(view.container,node=>node.props?.role==='status');assert.equal(status.props['aria-live'],'polite');
  const bar=find(view.container,node=>node.props?.role==='progressbar');assert.equal(bar.props['aria-valuenow'],50);
  assert.equal(all(view.container,node=>node.props?.class==='operation-spinner').length,1);
});

test('mounted unknown progress has no false numeric percent and completion or error remains visible',async t=>{
  const unknown=mount(component,{active:true,label:'Ожидаем сервер',total:0});t.after(unknown.unmount);await settle();
  assert.doesNotMatch(text(unknown.container),/\d+%/);
  assert.equal(find(unknown.container,node=>node.props?.role==='progressbar').props['aria-valuenow'],undefined);
  const complete=mount(component,{active:false,label:'Готово',completed:1,total:1});t.after(complete.unmount);await settle();
  assert.match(text(complete.container),/100%/);assert.equal(find(complete.container,node=>node.props?.class==='operation-spinner'),undefined);
  const failed=mount(component,{active:false,label:'Загрузка не завершена',completed:1,total:3,error:'Excel не принят'});t.after(failed.unmount);await settle();
  assert.match(text(failed.container),/33%/);assert.match(text(failed.container),/Excel не принят/);
  const idle=mount(component,{});t.after(idle.unmount);await settle();assert.equal(find(idle.container,node=>node.props?.role==='status'),undefined);
});

test('completion hides after a display delay without changing counts and restart or failure cancels that delay',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const view=mount(component,{active:false,label:'Готово',completed:1,total:1});t.after(view.unmount);await settle();
  assert.match(text(view.container),/100%/);
  t.mock.timers.tick(2199);await settle();assert.match(text(view.container),/100%/);
  t.mock.timers.tick(1);await settle();assert.equal(find(view.container,node=>node.props?.role==='status'),undefined);
  assert.equal(view.app._instance.props.completed,1);assert.equal(view.app._instance.props.total,1);
  view.app._instance.props.active=true;view.app._instance.props.completed=0;await settle();
  t.mock.timers.tick(3000);await settle();assert.match(text(view.container),/0%/);
  view.app._instance.props.active=false;view.app._instance.props.error='Сервер отказал';await settle();
  t.mock.timers.tick(10000);await settle();assert.match(text(view.container),/Сервер отказал/);
});
