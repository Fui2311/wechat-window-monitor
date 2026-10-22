/* Deterministic request-order tests of the shipped, dependency-free console. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const {resolve} = require('node:path');
const source = readFileSync(resolve(__dirname, '../../wechat_tracker/resources/web/app.js'), 'utf8');

function deferred() {
  let resolve, reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return {promise, resolve, reject};
}
function harness(storage = new Map(), realRenderers = false) {
  const elements = new Map();
  function element() {
    const classes = new Set();
    let html = '', text = '';
    return {value:'', disabled:false, checked:false, options:[], style:{},
      querySelector:()=>({className:''}),
      classList:{add:x=>classes.add(x), remove:x=>classes.delete(x), contains:x=>classes.has(x),
        toggle:(x, flag)=>flag ? classes.add(x) : classes.delete(x)},
      setAttribute(){}, appendChild(child){this.options.push(child);}, replaceChildren(){},
      set textContent(value){text=String(value);html='';this.options=[];}, get textContent(){return text;},
      set innerHTML(value){html=value;text='';this.options=[];}, get innerHTML(){return html;}};
  }
  const get = id => {if (!elements.has(id)) elements.set(id, element()); return elements.get(id);};
  const ctx = vm.createContext({console:{error(){}, warn(){}}, URL, URLSearchParams, Date, Blob,
    localStorage:{getItem:k=>storage.get(k) ?? null, setItem:(k,v)=>storage.set(k,v)},
    document:{getElementById:get, addEventListener(){}, createElement:element,
      querySelectorAll:()=>['llmBaseUrl','llmModel','llmTimeout','llmKeyEnv','llmApiKey','llmClearKey','llmDisableThinking'].map(get)},
    request:()=>{throw new Error('unexpected request');}, post:()=>{throw new Error('unexpected post');}});
  ctx.fetch=async (url, options) => {
    const data = options?.method === 'POST' ? await ctx.post(url, JSON.parse(options.body)) : await ctx.request(url);
    return {ok:true, json:async()=>data};
  };
  for (const file of ['presentation.js','http.js']) vm.runInContext(readFileSync(resolve(__dirname, '../../wechat_tracker/resources/web',file),'utf8'),ctx);
  vm.runInContext(source + `\n
    ${realRenderers ? '' : 'renderEvents = () => {}; renderOutbox = () => {};'} showToast = () => {};
    globalThis.app = {state, selectDatabase, loadDatabases, loadEvents, loadOutbox, openEmailPreview,
      renderProcessing, saveProcessing, confirmSendService, refreshStatus, formatTime, formatClockTime, formatCaptureTime, refreshConsoleData, controlService, loadServices, renderEvents, serviceStatusView, exportCalendar, loadMessages, searchMessages};`, ctx);
  return {ctx, app:ctx.app, get, storage};
}
const db = path => ({path, label:path, group_name:path, is_active:path==='A'});

test('late events and outbox responses cannot replace the selected source or its counts', async () => {
  const {ctx, app, get} = harness();
  app.state.databases = [db('A'), db('B')];
  const pending=[];
  ctx.request = url => {const d=deferred(); pending.push({url,...d}); return d.promise;};
  app.selectDatabase('A');
  const oldEvents=app.loadEvents(), oldJobs=app.loadOutbox();
  app.selectDatabase('B');
  assert.equal(app.state.events.length, 0);
  assert.equal(get('kpiEventsCount').textContent, '--');
  const newEvents=app.loadEvents(), newJobs=app.loadOutbox();
  pending[2].resolve({events:[{id:'B-event'}]});
  pending[3].resolve({jobs:[{id:2,delivery:{state:'sent'}}],delivery_service:{group:'B'}});
  await Promise.all([newEvents,newJobs]);
  pending[0].resolve({events:[{id:'A-event'}, {id:'other-A'}]});
  pending[1].resolve({jobs:[],delivery_service:{group:'A'}});
  await Promise.all([oldEvents,oldJobs]);
  assert.equal(app.state.events[0].id,'B-event');
  assert.equal(app.state.outbox[0].id,2);
  assert.equal(app.state.databases[0].event_count,undefined);
  assert.equal(app.state.databases[1].event_count,1);
  assert.match(get('mailDeliveryStatus').textContent,/B：1/);
});

test('returning A to B to A still rejects the original A request and its error', async () => {
  const {ctx,app,get}=harness();
  const stale=deferred(); let calls=0;
  ctx.request = () => ++calls===1 ? stale.promise : Promise.resolve({events:[{id:'fresh-A'}]});
  app.selectDatabase('A'); const first=app.loadEvents();
  app.selectDatabase('B'); app.selectDatabase('A'); await app.loadEvents();
  stale.reject(new Error('old request failed')); await first;
  assert.equal(app.state.events[0].id,'fresh-A');
  assert.doesNotMatch(get('kpiEventsDetail').textContent,/读取失败/);
});

test('switching source cancels open and in-flight email previews', async () => {
  const {ctx,app,get}=harness(); const pending=deferred();
  ctx.request=()=>pending.promise;
  app.selectDatabase('A');
  get('emailPreviewModal').classList.add('active');
  const preview=app.openEmailPreview('same-id');
  app.selectDatabase('B');
  pending.resolve({subject:'from A',body:'old body'}); await preview;
  assert.equal(get('emailPreviewModal').classList.contains('active'),false);
  assert.notEqual(get('emailPreviewSubject').textContent,'from A');
});

test('managed source changes reset remembered history and remain correct after reload', async () => {
  const storage=new Map([['selectedDatabase','A'],['selectedDatabaseActive','A']]);
  const one=harness(storage);
  one.ctx.request=async()=>({databases:[{...db('B'),is_active:true},{...db('A'),is_active:false}]});
  await one.app.loadDatabases();
  assert.equal(one.app.state.selectedDb,'B');
  const two=harness(storage); two.ctx.request=one.ctx.request; await two.app.loadDatabases();
  assert.equal(two.app.state.selectedDb,'B');
  two.app.selectDatabase('A');
  const three=harness(storage); three.ctx.request=one.ctx.request; await three.app.loadDatabases();
  assert.equal(three.app.state.selectedDb,'A'); // Explicit history choice within same managed source survives.
});

test('saving locks all draft fields until completion; shared settings remain read-only', async () => {
  const {ctx,app,get}=harness();
  app.renderProcessing({processing_mode:'llm',llm_model:'saved',llm_base_url:'https://example.com/v1'});
  get('llmModel').value='draft';
  const pending=deferred();ctx.post=()=>pending.promise;
  const save=app.saveProcessing();
  assert.equal(get('llmModel').disabled,true);
  assert.equal(get('llmApiKey').disabled,true);
  pending.resolve({processing:{processing_mode:'llm',llm_model:'draft'}});await save;
  assert.equal(get('llmModel').disabled,false);
  assert.equal(get('llmModel').value,'draft');
  app.renderProcessing({settings_shared:true,settings_source:'/shared.toml',llm_model:'shared'});
  assert.equal(get('btnSaveProcessing').disabled,true);
  assert.equal(get('llmModel').disabled,true);
  assert.match(get('processingScope').textContent,/shared.toml/);
  ctx.post=()=>assert.fail('shared settings must never be posted');
  await app.saveProcessing('rules');
});

test('send confirmation reads the managed queue regardless of the viewed database', async () => {
  const {ctx,app}=harness();app.selectDatabase('history');
  let confirmation;
  ctx.request=async url=>{assert.equal(url,'/api/service/backlog');return {group:'Managed',pending:3,due:2,retrying:1,failed:1};};
  ctx.capture=(title,text,action)=>{confirmation={title,text,action};};
  vm.runInContext('showConfirm = (title,text,action) => capture(title,text,action);',ctx);
  await app.confirmSendService('start');
  assert.match(confirmation.text,/Managed/);
  assert.match(confirmation.text,/3 条待发送/);
  assert.match(confirmation.text,/过往通知/);
  ctx.request=async()=>{throw new Error('offline');};confirmation=null;
  await app.confirmSendService('start');assert.equal(confirmation,null);
});

test('switching to rules preserves other unsaved fields and explicitly labels them', async () => {
  const {ctx,app,get}=harness();
  app.renderProcessing({processing_mode:'llm',llm_model:'persisted'});
  get('llmModel').value='unsaved model';app.state.processingDirty=true;
  ctx.post=async(url,body)=>{
    assert.equal(Object.keys(body).join(','),'processing_mode');
    assert.equal(body.processing_mode,'rules');
    return {processing:{processing_mode:'rules',llm_model:'persisted'}};
  };
  await app.saveProcessing('rules');
  assert.equal(get('llmModel').value,'unsaved model');
  assert.equal(app.state.processingDirty,true);
  assert.match(get('processingFeedback').textContent,/尚未保存/);
});

test('demo outbox explicitly stays outside the real waiting count', async () => {
  const {ctx,app,get}=harness();app.selectDatabase('demo');
  ctx.request=async()=>({jobs:[{id:1,delivery:{state:'simulation'}}],delivery_service:{simulation:true}});
  await app.loadOutbox();
  assert.match(get('kpiOutboxDetail').textContent,/未发送 0/);
  assert.match(get('mailDeliveryStatus').textContent,/均不会真实发送/);
});

test('a failed selected-source read reports failure without showing another source', async () => {
  const {ctx,app,get}=harness();
  app.selectDatabase('A');app.state.events=[{id:'old-A'}];
  app.selectDatabase('B');ctx.request=async()=>{throw new Error('offline');};
  await app.loadEvents();await app.loadOutbox();
  assert.equal(app.state.events.length,0);
  assert.equal(app.state.outbox.length,0);
  assert.match(get('eventsListContainer').textContent,/读取失败/);
  assert.match(get('mailDeliveryStatus').textContent,/读取失败/);
});

test('an old database inventory cannot restore the previous managed group', async () => {
  const {ctx,app}=harness();const pending=[];
  ctx.request=()=>{const d=deferred();pending.push(d);return d.promise;};
  const old=app.loadDatabases();const fresh=app.loadDatabases();
  pending[1].resolve({databases:[{...db('B'),is_active:true}]});await fresh;
  pending[0].resolve({databases:[db('A')]});await old;
  assert.equal(app.state.selectedDb,'B');assert.equal(app.state.databases[0].path,'B');
});

test('late status errors do not mark a healthy newer status offline', async () => {
  const {ctx,app}=harness();const pending=[];
  ctx.request=()=>{const d=deferred();pending.push(d);return d.promise;};
  const old=app.refreshStatus();const fresh=app.refreshStatus();
  pending[1].resolve({service:{},config:{group:'B'},email_status:{}});await fresh;
  assert.equal(app.state.isBackendOnline,true);
  pending[0].reject(new Error('old connection failed'));await old;
  assert.equal(app.state.config.group,'B');assert.equal(app.state.isBackendOnline,true);
});

test('a status requested before saving cannot revert the saved processing indicator', async () => {
  const {ctx,app}=harness();
  app.renderProcessing({processing_mode:'rules',llm_model:'configured-model'});
  const pending=deferred();ctx.request=()=>pending.promise;
  const old=app.refreshStatus();
  ctx.post=async()=>({processing:{processing_mode:'llm',llm_model:'configured-model'}});
  await app.saveProcessing('llm');
  pending.resolve({service:{},config:{processing:{processing_mode:'rules'}}});await old;
  assert.equal(app.state.processing.processing_mode,'llm');
});

test('event, capture and status clocks follow configured timezone including DST and midnight', () => {
  const {app}=harness();
  app.state.config.timezone='Asia/Shanghai';
  assert.equal(app.formatTime('2026-10-24T07:00:09Z'),'2026-10-24 15:00');
  assert.equal(app.formatClockTime('2026-10-24T07:00:09Z'),'15:00:09');
  assert.equal(app.formatCaptureTime('2026-10-24T07:00:09Z'),'2026-10-24 15:00:09');
  assert.equal(app.formatTime('2026-10-23T16:00:00Z'),'2026-10-24 00:00');
  app.state.config.timezone='UTC';
  assert.equal(app.formatTime('2026-10-24T07:00:09Z'),'2026-10-24 07:00');
  app.state.config.timezone='America/New_York';
  assert.equal(app.formatTime('2026-10-24T07:00:09Z'),'2026-10-24 03:00');
  assert.equal(app.formatTime('2026-12-24T07:00:09Z'),'2026-12-24 02:00');
  assert.equal(app.formatTime('not a date'),'');
  assert.equal(app.formatCaptureTime(null),'未记录');
});

test('manual refresh discovers sources before loading records and preserves the current draft', async () => {
  const storage = new Map([['selectedDatabase','B'],['selectedDatabaseActive','A']]);
  const {ctx,app,get}=harness(storage);const inventory=deferred();const calls=[];
  app.state.processingDirty=true;app.state.processingInitialized=true;get('llmModel').value='unsaved draft';
  app.state.messageOffset=100;app.state.messageSnapshot=200;
  ctx.request=async url=>{
    calls.push(url);
    if(url==='/api/status')return {service:{},config:{group:'A',processing:{llm_model:'saved model'}}};
    if(url==='/api/databases')return inventory.promise;
    if(url.startsWith('/api/events'))return {events:[]};
    if(url.startsWith('/api/messages'))return {messages:[],counts:{},total:0};
    if(url.startsWith('/api/outbox'))return {jobs:[]};
    if(url==='/api/services')return {services:[]};
    throw new Error(url);
  };
  const refresh=app.refreshConsoleData();
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(calls,['/api/status','/api/databases']);
  assert.equal(get('manualRefreshBtn').disabled,true);
  assert.equal(app.state.messageOffset,0);
  assert.equal(app.state.messageSnapshot,null);
  await app.refreshConsoleData(); // Duplicate clicks do not dispatch another batch.
  assert.equal(calls.length,2);
  inventory.resolve({databases:[db('A'),db('B'),db('C')]});await refresh;
  assert.equal(app.state.databases.length,3);
  assert.equal(app.state.selectedDb,'B');
  assert.ok(calls.includes('/api/events?db=B'));
  assert.equal(get('llmModel').value,'unsaved draft');
  assert.equal(get('manualRefreshBtn').disabled,false);
});

test('manual refresh follows a new active group instead of loading the former source', async () => {
  const storage=new Map([['selectedDatabase','A'],['selectedDatabaseActive','A']]);
  const {ctx,app}=harness(storage);const calls=[];
  ctx.request=async url=>{
    calls.push(url);
    if(url==='/api/status')return {service:{},config:{group:'C'}};
    if(url==='/api/databases')return {databases:[{...db('C'),is_active:true},{...db('A'),is_active:false}]};
    if(url.startsWith('/api/events'))return {events:[]};
    if(url.startsWith('/api/messages'))return {messages:[],counts:{},total:0};
    if(url.startsWith('/api/outbox'))return {jobs:[]};
    if(url==='/api/services')return {services:[]};
    throw new Error(url);
  };
  await app.refreshConsoleData();
  assert.equal(app.state.selectedDb,'C');
  assert.ok(calls.includes('/api/events?db=C'));
  assert.ok(!calls.includes('/api/events?db=A'));
});

test('all service controls carry their displayed group and restart keeps its existing mode by default', async () => {
  const {ctx,app}=harness();const posts=[];app.state.config.group='A';
  ctx.post=async(url,body)=>{posts.push({url,body});return {ok:true};};
  vm.runInContext('loadConsoleData=async()=>{};',ctx);
  await app.controlService('start');await app.controlService('restart');await app.controlService('stop',false,'old A');
  assert.equal(posts[0].body.expected_group,'A');
  assert.equal(posts[1].body.expected_group,'A');
  assert.equal('send' in posts[1].body,false);
  assert.equal(posts[2].body.expected_group,'old A');
  app.state.config.group='';await app.controlService('stop');
  assert.equal(posts.length,3);
});

test('offline refresh keeps the selected source and draft, and allows retry', async () => {
  const {ctx,app,get}=harness();app.state.selectedDb='A';app.state.events=[{id:'saved A'}];
  get('llmModel').value='unsaved draft';const calls=[];
  ctx.request=async url=>{calls.push(url);throw new Error('backend offline');};
  await app.refreshConsoleData();
  assert.deepEqual(calls,['/api/status']);
  assert.equal(app.state.selectedDb,'A');
  assert.equal(app.state.events[0].id,'saved A');
  assert.equal(get('llmModel').value,'unsaved draft');
  assert.equal(app.state.isBackendOnline,false);
  assert.equal(get('manualRefreshBtn').disabled,false);
});

test('another page changing shared storage does not hide this page managed-source change', async () => {
  const storage=new Map([['selectedDatabase','A'],['selectedDatabaseActive','A']]);
  const {ctx,app}=harness(storage);
  storage.set('selectedDatabaseActive','B'); // Second page has already observed the new group.
  ctx.request=async()=>({databases:[{...db('B'),is_active:true},{...db('A'),is_active:false}]});
  await app.loadDatabases();
  assert.equal(app.state.selectedDb,'B');
  assert.equal(app.state.activeDbPath,'B');
});

test('status updates refresh untouched group candidates while preserving an explicit draft', async () => {
  const {ctx,app,get}=harness();
  ctx.request=async()=>({service:{},config:{group:'A'}});await app.refreshStatus();
  assert.equal(get('inputSwitchGroup').value,'A');
  ctx.request=async()=>({service:{},config:{group:'B'}});await app.refreshStatus();
  assert.equal(get('inputSwitchGroup').value,'B');
  app.state.switchGroupDirty=true;get('inputSwitchGroup').value='intended C';
  ctx.request=async()=>({service:{},config:{group:'D'}});await app.refreshStatus();
  assert.equal(get('inputSwitchGroup').value,'intended C');
});

test('optional LLM settings follow initial mode and preserve the user disclosure choice on refresh', () => {
  const {app,get}=harness();
  app.renderProcessing({processing_mode:'rules',llm_model:'saved'});
  assert.equal(get('llmSettings').open,false);
  get('llmSettings').open=true;app.state.processingDirty=true;get('llmModel').value='draft';
  app.renderProcessing({processing_mode:'rules',llm_model:'saved'});
  assert.equal(get('llmSettings').open,true);
  assert.equal(get('llmModel').value,'draft');
  app.renderProcessing({processing_mode:'llm',llm_model:'saved'});
  assert.equal(get('llmSettings').open,true);
  get('llmSettings').open=false;
  app.renderProcessing({processing_mode:'llm',llm_model:'saved'});
  assert.equal(get('llmSettings').open,false);
  const initialLlm=harness();initialLlm.app.renderProcessing({processing_mode:'llm',settings_shared:true});
  assert.equal(initialLlm.get('llmSettings').open,true);
  assert.equal(initialLlm.get('llmModel').disabled,true);
});

test('service list recovers after a transient error when successful data has not changed', async () => {
  const {ctx,app,get}=harness();
  ctx.request=async()=>({services:[]});await app.loadServices();
  assert.match(get('servicesTableBody').innerHTML,/未发现/);
  ctx.request=async()=>{throw new Error('temporary');};await app.loadServices();
  assert.match(get('servicesTableBody').innerHTML,/获取服务列表失败/);
  ctx.request=async()=>({services:[]});await app.loadServices();
  assert.doesNotMatch(get('servicesTableBody').innerHTML,/获取服务列表失败/);
  assert.match(get('servicesTableBody').innerHTML,/未发现/);
});

test('empty lectures recover from an error even when the next successful response is still empty', async () => {
  const {ctx,app,get}=harness(new Map(),true);app.selectDatabase('empty');
  ctx.request=async()=>({events:[]});await app.loadEvents();
  assert.match(get('eventsListContainer').innerHTML,/未找到讲座/);
  ctx.request=async()=>{throw new Error('temporary');};await app.loadEvents();
  assert.match(get('eventsListContainer').textContent,/读取失败/);
  ctx.request=async()=>({events:[]});await app.loadEvents();
  assert.match(get('eventsListContainer').innerHTML,/未找到讲座/);
  assert.equal(app.state.lastDataErrors['讲座数据'],undefined);
});

test('empty lecture guidance follows search, history, demo and current-source context', () => {
  const {app,get}=harness(new Map(),true);
  app.state.selectedDb='empty';app.state.config.group='Managed';
  const selected={path:'empty',is_active:false,group_name:'History'};app.state.databases=[selected];
  app.renderEvents();assert.match(get('eventsListContainer').innerHTML,/只读展示/);
  assert.doesNotMatch(get('eventsListContainer').innerHTML,/启动预览/);
  selected.is_active=true;app.renderEvents();assert.match(get('eventsListContainer').innerHTML,/启动预览/);
  selected.is_demo=true;selected.is_active=false;app.renderEvents();assert.match(get('eventsListContainer').innerHTML,/重新运行 Demo/);
  app.state.searchQuery='absent';app.renderEvents();assert.match(get('eventsListContainer').innerHTML,/清除搜索/);
});

test('only confirmed monitoring is green and header and table use the same status projection', async () => {
  const {ctx,app,get}=harness();
  for (const [status,running,label] of [
    ['starting',true,'服务启动中'],['waiting_for_weixin',true,'等待微信窗口'],
    ['monitoring',true,'监控中'],['unknown',true,'采集待确认'],
    ['database_error',false,'数据库异常'],['stopped_or_unresponsive',false,'停止或无响应']]) {
    const service={status,running,data_dir:'/fixture',group:'Fixture'};
    ctx.request=async url=>url==='/api/status'?{service,config:{group:'Fixture'}}:{services:[service]};
    await app.refreshStatus();await app.loadServices();
    assert.equal(get('serviceStatusText').textContent,label);
    assert.ok(get('servicesTableBody').options[0].innerHTML.includes(label));
    assert.equal(app.serviceStatusView(service).level==='success',status==='monitoring');
  }
});

test('service rows update visible identity and processing results even if lifecycle state is unchanged', async () => {
  const {ctx,app,get}=harness();
  let service={data_dir:'/fixture',group:'Old',pid:10,running:false,processing_last_method:'rules'};
  ctx.request=async()=>({services:[service]});await app.loadServices();
  assert.match(get('servicesTableBody').options[0].innerHTML,/Old/);
  service={...service,group:'New',pid:20,is_current:true,processing_last_method:'llm'};
  await app.loadServices();const row=get('servicesTableBody').options[0].innerHTML;
  assert.match(row,/New/);assert.match(row,/>20</);assert.match(row,/当前/);assert.match(row,/LLM 成功/);
});

function calendarHarness() {
  const h=harness();h.downloads=[];h.revoked=[];h.notices=[];
  h.ctx.URL=class extends URL {
    static createObjectURL(blob){assert.ok(blob.size>0);return 'blob:fixture';}
    static revokeObjectURL(url){h.revoked.push(url);}
  };
  h.ctx.setTimeout=callback=>callback();
  h.ctx.document.body={appendChild(){}};
  h.ctx.document.createElement=()=>({click(){h.downloads.push({href:this.href,name:this.download});},remove(){}});
  h.ctx.notice=(message,type)=>h.notices.push({message,type});
  vm.runInContext('showToast=(message,type)=>notice(message,type);',h.ctx);
  return h;
}

test('calendar export reports HTTP errors without downloading an error document', async () => {
  const h=calendarHarness();
  h.ctx.fetch=async()=>({ok:false,status:503,text:async()=>JSON.stringify({error:'数据库暂时不可读'})});
  await h.app.exportCalendar();
  assert.equal(h.downloads.length,0);
  assert.match(h.notices.at(-1).message,/数据库暂时不可读/);
  assert.equal(h.notices.at(-1).type,'error');
  assert.equal(h.get('btnExportIcs').disabled,false);
});

test('calendar export downloads the captured selected source and releases its object URL', async () => {
  const h=calendarHarness();h.app.state.selectedDb='history A';
  h.ctx.fetch=async url=>{assert.equal(url,'/api/calendar/export?db=history%20A');return {ok:true,text:async()=>'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'};};
  await h.app.exportCalendar();
  assert.equal(h.downloads.length,1);assert.equal(h.downloads[0].name,'lectures.ics');
  assert.deepEqual(h.revoked,['blob:fixture']);
  assert.match(h.notices.at(-1).message,/请求浏览器下载/);
  assert.equal(h.get('btnExportIcs').disabled,false);
});

test('late calendar responses after source change or shutdown cannot trigger a download', async () => {
  for (const action of ['change','roundtrip','shutdown']) {
    const h=calendarHarness(),pending=deferred();h.app.state.selectedDb='A';
    h.ctx.fetch=()=>pending.promise;
    const first=h.app.exportCalendar();await h.app.exportCalendar();
    assert.equal(h.get('btnExportIcs').disabled,true);
    if(action==='shutdown')h.app.state.isConsoleStopped=true;
    else {h.app.selectDatabase('B');if(action==='roundtrip')h.app.selectDatabase('A');}
    pending.resolve({ok:true,text:async()=>'BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n'});await first;
    assert.equal(h.downloads.length,0);
    assert.equal(h.app.state.calendarExportBusy,false);
  }
});

test('empty or invalid calendar responses explain why no file is downloaded', async () => {
  for (const [content,notice] of [['BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n',/没有可导出的明确活动时间/],['unexpected HTML',/未返回有效日历/]]) {
    const h=calendarHarness();h.ctx.fetch=async()=>({ok:true,text:async()=>content});
    await h.app.exportCalendar();
    assert.equal(h.downloads.length,0);
    assert.match(h.notices.at(-1).message,notice);
    assert.equal(h.get('btnExportIcs').disabled,false);
  }
});

test('older message pages reuse the capture boundary; a fresh first page includes arrivals', async () => {
  const {ctx,app,get}=harness();
  const urls=[];
  ctx.request=async url=>{urls.push(url);return {messages:[{text:'stored'}],total:250,snapshot:251,counts:{all:250},has_more:true};};
  await app.loadMessages();
  app.state.messageOffset=100;
  await app.loadMessages();
  assert.equal(new URLSearchParams(urls[1].split('?')[1]).get('snapshot'),'251');
  assert.equal(get('messagesPageInfo').textContent,'101–101 / 250 条');
  app.state.messageOffset=0;
  await app.loadMessages();
  assert.equal(new URLSearchParams(urls[2].split('?')[1]).has('snapshot'),false);
  app.selectDatabase('other');
  assert.equal(app.state.messageSnapshot,null);
});

test('typing a message search rejects the old response during the debounce window', async () => {
  const {ctx,app,get}=harness();
  const pending=deferred();
  ctx.request=()=>pending.promise;
  ctx.clearTimeout=()=>{};
  let timer;
  ctx.setTimeout=callback=>{timer=callback;return 1;};
  const old=app.loadMessages();
  app.searchMessages('new search');
  pending.resolve({messages:[{text:'obsolete'}],total:1,counts:{all:1}});
  await old;
  assert.equal(app.state.messages.length,0);
  assert.match(get('messagesLoadStatus').textContent,/等待搜索/);
  assert.equal(get('btnMessagesNext').disabled,true);
  ctx.request=async url=>{
    assert.equal(new URLSearchParams(url.split('?')[1]).get('q'),'new search');
    return {messages:[{text:'new result'}],total:1,snapshot:5,counts:{all:1}};
  };
  await timer();
  assert.equal(app.state.messages[0].text,'new result');
  assert.equal(get('messagesPageInfo').textContent,'1–1 / 1 条');
});
