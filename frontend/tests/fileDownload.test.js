import {test} from 'node:test';
import assert from 'node:assert/strict';
import {isAppFileLink,downloadFilename,fetchDownload} from '../src/fileDownload.js';

test('only existing same-origin API file actions are intercepted',()=>{
  const origin='https://example.test';
  for(const path of ['/api/report-archives/3/files/pdf?company_id=2','/api/business-projects/1/sources/2/download?company_id=2','/api/business-projects/1/native.xlsx','/api/business-projects/template.xlsx','/api/export.xlsx','/api/documents/3?company_id=2','/api/request-documents/4?company_id=2']) assert.ok(isAppFileLink(path,origin));
  for(const path of ['https://other.test/api/export.xlsx','/api/business-projects/1','/api/profile','/help.pdf','#reports']) assert.equal(isAppFileLink(path,origin),false);
});
test('UTF8 names remain readable and cannot introduce local paths',()=>{
  assert.equal(downloadFilename("attachment; filename*=UTF-8''%D0%9E%D1%82%D1%87%D1%91%D1%82.pdf"),'Отчёт.pdf');
  assert.equal(downloadFilename('attachment; filename="../bad/file.xlsx"'),'.._bad_file.xlsx');
  assert.equal(downloadFilename(''),'отчёт');
});
test('progress advances only after actual response and body completion',async()=>{
  const stages=[];let respond,body;
  const pending=fetchDownload('/api/export.xlsx',{fetcher:()=>new Promise(resolve=>{respond=resolve;}),onResponse:()=>stages.push('response'),onBody:()=>stages.push('body')});
  assert.deepEqual(stages,[]);
  respond({ok:true,redirected:false,headers:new Headers({'content-type':'application/pdf','content-disposition':'attachment; filename="report.pdf"'}),blob:()=>new Promise(resolve=>{body=resolve;})});
  await Promise.resolve();assert.deepEqual(stages,['response']);
  body(new Blob(['synthetic-file']));const result=await pending;
  assert.deepEqual(stages,['response','body']);assert.equal(result.filename,'report.pdf');
});
test('failed authentication and invalid payload do not count as a finished file',async()=>{
  const stages=[];const opts={onResponse:()=>stages.push('response'),onBody:()=>stages.push('body')};
  await assert.rejects(fetchDownload('/api/export.xlsx',{...opts,fetcher:async()=>({ok:false,status:401,json:async()=>({detail:'Войдите в систему'})})}),/Войдите/);
  assert.deepEqual(stages,[]);
  await assert.rejects(fetchDownload('/api/export.xlsx',{...opts,fetcher:async()=>({ok:true,redirected:false,headers:new Headers({'content-type':'text/html'})})}),/страницу/);
  assert.deepEqual(stages,[]);
});
test('an original JSON attachment downloads while JSON error responses are refused',async()=>{
  const headers=new Headers({'content-type':'application/json','content-disposition':'attachment; filename="inputs.json"'});
  const result=await fetchDownload('/api/business-projects/1/sources/2/download',{fetcher:async()=>({ok:true,redirected:false,headers,blob:async()=>new Blob(['{"sample":true}'])})});
  assert.equal(result.filename,'inputs.json');
  headers.delete('content-disposition');
  await assert.rejects(fetchDownload('/api/business-projects/1/sources/2/download',{fetcher:async()=>({ok:true,redirected:false,headers})}),/страницу/);
});
