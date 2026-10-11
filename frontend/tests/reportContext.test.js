import {test} from 'node:test';
import assert from 'node:assert/strict';
import {normalizeReportSelection as normalize} from '../src/reportContext.js';

test('quarter and month choices keep the same boundaries across all reports', () => {
  assert.deepEqual(normalize({year:2026,period:'q2',start:1,end:12,unit:'m',mode:'compare',sourceId:5}),
    {year:2026,period:'q2',start:4,end:6,unit:'m',mode:'compare',sourceId:5});
  assert.equal(normalize({period:'month',start:7,end:12}).end,7);
  assert.equal(normalize({mode:'plan'}).mode,'plan');
});
test('invalid choices are rejected while a reversed custom range remains detectable', () => {
  const value=normalize({year:'evil',period:'custom',start:9,end:2,unit:'billion',sourceId:-1});
  assert.equal(value.year,null); assert.equal(value.unit,'x'); assert.equal(value.sourceId,null);
  assert.ok(value.start>value.end);
  assert.deepEqual(normalize({}, {year:2025,unit:'k'}),
    {year:2025,period:'year',start:1,end:12,unit:'k',mode:'actual',sourceId:null});
});
