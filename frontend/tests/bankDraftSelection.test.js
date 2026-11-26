import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent, mount, settle} from './helpers/mountVue.js';

const component = await compileComponent(new URL('../src/BankDraftSelection.vue',import.meta.url));
test('saved selection uses draft row identities and reopens with no posting call', async t => {
  const rows = [
    {id:71,line:5,amount:'15.79',direction:'in'},
    {id:72,line:9,amount:'2.50',direction:'out'},
    {id:73,line:15,amount:'80.00',direction:'out'},
  ];
  let stored = {version:0,line_ids:[]}; const writes = [];
  async function api(url, options={}) {
    if (!options.method) {
      assert.equal(url, '/api/bank-statements/7');
      return {id:7,status:'preview',rows,account:{currency:'UZS'},draft_selection:stored};
    }
    assert.equal(url, '/api/bank-statements/7/draft-selection');
    assert.equal(options.method, 'PUT');
    const data=JSON.parse(options.body); writes.push(data);
    stored={version:data.version+1,line_ids:data.line_ids};return stored;
  }
  const view=mount(component,{api,batchId:7});t.after(view.unmount);
  view.state.toggle();await settle();
  view.state.numbers='5, 500';await view.state.save();assert.equal(writes.length,0);
  view.state.numbers='5, 5';await view.state.save();assert.equal(writes.length,0);
  view.state.numbers='5, 9';assert.deepEqual(view.state.totals,{in:1579,out:250});
  await view.state.save();assert.deepEqual(writes,[{version:0,line_ids:[71,72]}]);
  view.state.toggle();view.state.toggle();await settle();
  assert.deepEqual([...view.state.ids],[71,72]);assert.equal(view.state.changed,false);
  view.state.mark(rows[1]);assert.deepEqual([...view.state.ids],[71]);
});
