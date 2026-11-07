import {test} from 'node:test';
import assert from 'node:assert/strict';
import {defineComponent,h,ref,shallowRef,onMounted} from 'vue';
import {lazyPage} from '../src/lazyPage.js';
import {mount,settle,text,find} from './helpers/mountVue.js';

const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return{promise,resolve};};

test('a heavy page is not downloaded until opened and receives its current props',async t=>{
  let loads=0;
  const Page=lazyPage(async()=>{loads++;return {props:['label'],render(){return h('p',this.label);}};});
  const view=mount(defineComponent({setup(){const opened=ref(false);return{opened};},render(){return this.opened?h(Page,{label:'Current selected company'}):h('p','Other page');}}));
  t.after(view.unmount);await settle();assert.equal(loads,0);
  view.state.opened=true;await settle();assert.equal(loads,1);assert.match(text(view.container),/Current selected company/);
});

test('a delayed code download shows a status without inventing financial data',async t=>{
  const wait=deferred(),view=mount(lazyPage(()=>wait.promise));t.after(view.unmount);
  await new Promise(done=>setTimeout(done,150));await settle();
  const status=find(view.container,node=>node.props?.role==='status');assert.ok(status);assert.match(text(status),/Загружаем раздел/);
  assert.doesNotMatch(text(view.container),/0 UZS|Оплачена/);
  wait.resolve({render:()=>h('p','Loaded')});await settle();assert.equal(text(view.container),'Loaded');
});

test('a failed download has a visible retry that loads the page once after recovery',async t=>{
  let calls=0;const errors=[];
  const Page=lazyPage(()=>++calls===1?Promise.reject(new Error('Synthetic offline')):Promise.resolve({render:()=>h('p','Recovered page')}));
  const view=mount(Page);view.app.config.errorHandler=error=>errors.push(error);t.after(view.unmount);await settle();
  assert.ok(find(view.container,node=>node.props?.role==='alert'));assert.match(text(view.container),/Не удалось загрузить раздел/);
  assert.equal(calls,1);assert.equal(errors.length,1);
  find(view.container,node=>node.tag==='button'&&text(node)==='Повторить').props.onClick();await settle();
  assert.equal(calls,2);assert.equal(text(view.container),'Recovered page');
});

test('a page closed while its code is loading cannot mount or perform its reads later',async t=>{
  const wait=deferred();let mounted=0;
  const Page=lazyPage(()=>wait.promise);
  const view=mount(defineComponent({setup(){const allowed=ref(true);return{allowed};},render(){return this.allowed?h(Page):h('p','No access');}}));
  t.after(view.unmount);await settle();view.state.allowed=false;await settle();
  wait.resolve({setup(){onMounted(()=>mounted++);return()=>h('p','Private page');}});await settle();
  assert.equal(mounted,0);assert.equal(text(view.container),'No access');
});

test('switching company during code loading mounts only the latest company context',async t=>{
  const wait=deferred(),reads=[];const Page=lazyPage(()=>wait.promise);
  const view=mount(defineComponent({setup(){const company=shallowRef({id:1});return{company};},render(){return h(Page,{key:this.company.id,company:this.company});}}));
  t.after(view.unmount);await settle();view.state.company={id:2};await settle();
  wait.resolve({props:['company'],setup(props){onMounted(()=>reads.push(props.company.id));return()=>h('p','Company '+props.company.id);}});await settle();
  assert.deepEqual(reads,[2]);assert.equal(text(view.container),'Company 2');
});
