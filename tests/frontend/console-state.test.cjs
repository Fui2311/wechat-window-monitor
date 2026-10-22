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
function harness(storage = new Map()) {
  const elements = new Map();
  function element() {
    const classes = new Set();
    return {value:'', textContent:'', disabled:false, checked:false, options:[], style:{},
      querySelector:()=>({className:''}),
      classList:{add:x=>classes.add(x), remove:x=>classes.delete(x), contains:x=>classes.has(x),
        toggle:(x, flag)=>flag ? classes.add(x) : classes.delete(x)},
      setAttribute(){}, appendChild(child){this.options.push(child);}, replaceChildren(){},
      set innerHTML(value){this.options=[];}, get innerHTML(){return '';}};
  }
  const get = id => {if (!elements.has(id)) elements.set(id, element()); return elements.get(id);};
  const ctx = vm.createContext({console:{error(){}, warn(){}}, URL, URLSearchParams, Date,
    localStorage:{getItem:k=>storage.get(k) ?? null, setItem:(k,v)=>storage.set(k,v)},
    document:{getElementById:get, addEventListener(){}, createElement:element,
      querySelectorAll:()=>['llmBaseUrl','llmModel','llmTimeout','llmKeyEnv','llmApiKey','llmClearKey','llmDisableThinking'].map(get)},
    request:()=>{throw new Error('unexpected request');}, post:()=>{throw new Error('unexpected post');}});
  vm.runInContext(source + `\napiGet = url => request(url); apiPost = (url, body) => post(url, body);
    renderEvents = () => {}; renderOutbox = () => {}; showToast = () => {};
    globalThis.app = {state, selectDatabase, loadDatabases, loadEvents, loadOutbox, openEmailPreview,
      renderProcessing, saveProcessing, confirmSendService, refreshStatus};`, ctx);
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
