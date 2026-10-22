/* Public module contracts, independently of the DOM controller. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');
const {resolve}=require('node:path');
const presentation=require('../../wechat_tracker/resources/web/presentation.js');
const {createApi}=require('../../wechat_tracker/resources/web/http.js');

test('presentation runs without DOM, storage, application state or network globals', () => {
  const source=readFileSync(resolve(__dirname,'../../wechat_tracker/resources/web/presentation.js'),'utf8');
  const rules=vm.runInNewContext(source+'\nTrackerPresentation;', {URL});
  assert.equal(rules.formatTime('2026-10-23T16:00:00Z','Asia/Shanghai'),'2026-10-24 00:00');
  assert.equal(rules.formatTime('2026-10-24T07:00:00Z','UTC'),'2026-10-24 07:00');
  assert.equal(rules.formatCaptureTime(null),'未记录');
  assert.equal(rules.displayEventTime(null,'周五下午'),'周五下午');
  assert.equal(rules.serviceStatusView({running:true,status:'starting'}).level,'warning');
});

test('link presentation rejects executable schemes and escapes HTML independently of the UI', () => {
  assert.equal(presentation.signupLinkHtml('javascript:alert(1)'),'未提供');
  assert.equal(presentation.signupLinkHtml('data:text/html,test'),'未提供');
  assert.match(presentation.signupLinkHtml('https://example.com/?a=1&b=2'),/a=1&amp;b=2/);
  assert.match(presentation.signupLinkHtml('https://example.com/'),/rel="noopener noreferrer"/);
  assert.equal(presentation.escapeHtml('<script>"&\''),'&lt;script&gt;&quot;&amp;&#039;');
});

test('injected HTTP transport supports JSON reads, writes and text downloads without global fetch', async () => {
  const calls=[];
  const api=createApi(async (url,options)=>{
    calls.push({url,options});
    return {ok:true,json:async()=>({accepted:true}),text:async()=>'BEGIN:VCALENDAR'};
  });
  assert.equal((await api.get('/status')).accepted,true);
  assert.equal((await api.post('/settings',{mode:'rules'})).accepted,true);
  assert.equal(await api.text('/calendar'),'BEGIN:VCALENDAR');
  assert.equal(calls[1].options.method,'POST');
  assert.equal(calls[1].options.headers['Content-Type'],'application/json');
  assert.deepEqual(JSON.parse(calls[1].options.body),{mode:'rules'});
});

test('JSON, text and empty HTTP failures and network failures retain actionable errors', async () => {
  for (const [body,expected] of [['{"error":"配置尚未就绪"}',/配置尚未就绪/],['temporarily unavailable',/temporarily unavailable/],['',/HTTP 503/]]) {
    const api=createApi(async()=>({ok:false,status:503,text:async()=>body}));
    await assert.rejects(api.get('/status'),expected);
    await assert.rejects(api.post('/settings'),expected);
    await assert.rejects(api.text('/calendar'),expected);
  }
  const api=createApi(async()=>{throw new Error('connection refused');});
  await assert.rejects(api.get('/status'),/connection refused/);
});
