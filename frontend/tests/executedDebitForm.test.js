import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileComponent, mount, settle, all, text} from './helpers/mountVue.js';

const component = await compileComponent(new URL('../src/requests/PaymentBankEvidence.vue', import.meta.url));

test('the bank-date button submits one JSON fact with the statement date and request version', async t => {
  const row = {batch_id:3, line:5, line_id:17, status:'preview', line_status:'review',
    reference:'SYN-8', date:'2026-09-14', amount:'100.00', currency:'UZS'};
  const calls = [], paid = [];
  const api = async (url, options) => {
    if (!options) return {request_id:4, request_version:3, request_status:'approved',
      approved_on:'2026-09-15', rows:[row], book:'0.00', shortfall:'100.00'};
    calls.push({url, options});
    const request = new Request('https://example.test' + url, options);
    assert.equal(request.headers.get('Content-Type'), 'application/json');
    assert.equal(request.method, 'POST');
    const body = await request.json();
    assert.deepEqual([body.request_id, body.request_version, body.confirm, body.payment_date],
      [4, 3, true, row.date]);
    return {request_id:4, ledger_id:9, payment_date:row.date};
  };
  const view = mount(component, {api, companyId:1, requestId:4, account:{id:2,currency:'UZS'},
    reference:'SYN-8', day:'2026-09-16', canOpen:true, onAlreadyPaid:id=>paid.push(id)});
  t.after(()=>view.unmount());
  await new Promise(resolve=>setTimeout(resolve, 400));
  await settle();
  const [button] = all(view.container, n=>n.tag==='button' && text(n)==='Учесть списание датой банка');
  assert.ok(button);
  await Promise.all([button.props.onClick(), button.props.onClick()]);
  assert.deepEqual(calls.map(c=>c.url), ['/api/bank-statements/lines/17/recognize-debit']);
  assert.deepEqual(paid, [4]);
});
