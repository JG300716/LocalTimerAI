'use strict';
// Isolated UI state/event tests. No browser, real HTTP requests or user data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const original = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const source = original.replace(/\}\)\(\);\s*$/, `
  globalThis.__test = {state,render,navigate,refresh,setRange,saveConfig,exportData,
    chartSeries,chartDayValues,donutGroups,collectConfig};
})();`);

function fixture(label='Test project') {
  return {range:{start:'2026-10-20',end:'2026-10-26'},
    summary:{total_seconds:3600,today_seconds:3600,daily_average_seconds:3600,
      active_days:1,best_day:{date:'2026-10-26',seconds:3600},projects_count:1},
    daily:[{date:'2026-10-26',seconds:3600,projects:{[label]:3600}}],
    projects:[{name:label,seconds:3600}],available_projects:[label],
    languages:[{name:'C++',seconds:3600}],editors:[{name:'Rider',seconds:3600}],
    categories:[],applications:[],sessions:[],sessions_total:0,
    ai:{tokens_input:0,tokens_output:0,tokens_cached:0,total_seconds:0,events_count:0,models:[],daily:[],sessions:[]},legacy:[]};
}
function emptyFixture() {
  const data=fixture();
  data.summary={total_seconds:0,today_seconds:0,daily_average_seconds:0,active_days:0,best_day:null,projects_count:0};
  for(const key of ['daily','projects','available_projects','languages','editors'])data[key]=[];
  return data;
}
function config() {
  return {server:{port:48763},tracking:{enabled:true,idle_seconds:120,poll_seconds:2},
    ai:{enabled:true},applications:[{exe:'test.exe',label:'Test',project:'Project',mode:'running',category:'testing'}]};
}
const clone = value => JSON.parse(JSON.stringify(value));
const settle = async () => { for(let n=0;n<20;n++) await Promise.resolve(); };

async function environment(url='http://127.0.0.1:48763/#overview',initial={}) {
  const handlers = {}, nodes = {}, requests = [], downloads = [], blobs = [];
  const classes = () => ({add(){},remove(){},contains(){return false;}});
  nodes['#app']={innerHTML:'',addEventListener(kind,fn){handlers[kind]=fn;}};
  nodes['#toast']={textContent:'',classList:classes()};
  nodes['#view-content']={innerHTML:''};
  const storage = new Map(Object.entries(initial.storage||{}));
  const location = new URL(url);
  let fetcher = async (target, options={}) => {
    const route=new URL(target,location.href);
    if(route.pathname==='/api/status')return {name:'LocalFlow',token:'test-token',tracking:{running:true,enabled:true,paused:false}};
    if(route.pathname==='/api/dashboard')return fixture();
    if(route.pathname==='/api/config')return options.method==='POST'?{ok:true}:config();
    if(route.pathname==='/api/import/legacy')return {imported:1};
    return {ok:true};
  };
  if(initial.fetcher)fetcher=initial.fetcher;
  class TestURL extends URL {
    static createObjectURL(blob){blobs.push(blob);return 'blob:test-'+blobs.length;}
    static revokeObjectURL(){}
  }
  const document = {hidden:false,activeElement:null,documentElement:{dataset:{}},
    querySelector(selector){return nodes[selector]||null;},querySelectorAll(){return [];},
    createElement(tag){return {tag,click(){downloads.push({href:this.href,name:this.download});},remove(){},classList:classes()};},
    body:{appendChild(){}}};
  const context=vm.createContext({console,Intl,Date,Blob,URL:TestURL,URLSearchParams,
    location,document,localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)},
    history:{replaceState(_a,_b,target){location.href=new URL(target,location.href).href;}},
    window:{scrollTo(){},addEventListener(kind,fn){handlers['window:'+kind]=fn;}},
    setTimeout(){return 1;},clearTimeout(){},setInterval(){return 1;},clearInterval(){},
    fetch:async (target,options={})=>{
      requests.push({target,options});
      const value=await fetcher(target,options);
      if(value?.__response)return value.__response;
      return {ok:true,status:200,json:async()=>clone(value),blob:async()=>new Blob(['export'],{type:'text/csv'})};
    }});
  vm.runInContext(source,context,{filename:'app.js'});
  await settle();
  const app=context.__test;
  async function click(dataset,extra={}){
    const element={dataset,classList:classes(),disabled:false,...extra};
    await handlers.click({target:{closest(){return element;}}});
    await settle();
    return element;
  }
  return {app,context,nodes,document,handlers,requests,downloads,blobs,location,storage,click,
    fetchWith(fn){fetcher=fn;}};
}

test('new and legacy links always show API data and remove remembered demo mode',async()=>{
  for(const mode of ['', 'live','demo']){
    const env=await environment(`http://127.0.0.1:48763/?${mode?'mode='+mode+'&':''}keep=1#projects`,{
      storage:{'localflow-demo':'true','localflow-theme':'paper'}});
    assert.equal(env.app.state.view,'projects');
    assert.equal(env.app.state.data.summary.total_seconds,3600);
    assert.equal(env.app.state.data.projects[0].name,'Test project');
    assert.equal(Object.hasOwn(env.app.state,'demo'),false);
    assert.equal(env.storage.has('localflow-demo'),false);
    assert.equal(env.app.state.theme,'paper');
    assert.equal(env.location.hash,'#projects');
    assert.doesNotMatch(env.nodes['#app'].innerHTML,/demo-banner|demo-badge|data-action="demo"|UIBuilder|NosTale Tools/);
    await env.app.navigate('settings');
    assert.equal(env.location.searchParams.get('mode'),null);
    assert.equal(env.location.searchParams.get('keep'),'1');
    assert.equal(env.location.hash,'#settings');
    await env.app.navigate('constructor');
    assert.equal(env.app.state.view,'settings');
  }
});

test('project-card navigation updates hash while retaining other query parameters',async()=>{
  const env=await environment('http://127.0.0.1:48763/?mode=demo&keep=1#overview');
  await env.click({projectChoice:'Test project'});
  assert.equal(env.location.hash,'#timeline');
  assert.equal(env.location.searchParams.get('mode'),null);
  assert.equal(env.location.searchParams.get('keep'),'1');
  assert.equal(env.app.state.project,'Test project');
});

test('empty API data stays empty in all six views and four styles',async()=>{
  const env=await environment('http://127.0.0.1:48763/?mode=demo#overview',{
    storage:{'localflow-demo':'true'},
    fetcher:async target=>target.startsWith('/api/status')?{token:'test-token',tracking:{running:true}}:emptyFixture()});
  env.app.state.config=config();
  for(const theme of ['midnight','paper','terminal','focus']){
    env.app.state.theme=theme;
    for(const view of ['overview','projects','applications','ai','timeline','settings']){
      env.app.state.view=view;env.app.render();
      assert.equal(env.app.state.data.summary.total_seconds,0);
      assert.equal(env.app.state.data.projects.length,0);
      assert.equal(env.app.state.data.ai.tokens_input,0);
      assert.equal(env.app.state.data.ai.models.length,0);
      assert.doesNotMatch(env.nodes['#app'].innerHTML,/demo-banner|demo-badge|data-action="demo"|UIBuilder|NosTale Tools|Praca magisterska|NaN|Infinity/);
    }
  }
});

test('failed initial API request cannot fall back to fake data from a legacy preference',async()=>{
  const env=await environment('http://127.0.0.1:48763/?mode=demo#overview',{
    storage:{'localflow-demo':'true'},fetcher:async()=>{throw new Error('offline');}});
  assert.equal(env.app.state.data,null);
  assert.equal(env.app.state.live,null);
  assert.match(env.app.state.error,/offline/);
  assert.match(env.nodes['#app'].innerHTML,/offline/);
  assert.doesNotMatch(env.nodes['#app'].innerHTML,/demo-banner|demo-badge|data-action="demo"|UIBuilder|NosTale Tools|Praca magisterska/);
});

test('rejected JSON cannot poison last usable config; draft survives theme change',async()=>{
  const env=await environment();
  await env.app.navigate('settings');
  const previous=env.app.state.config;
  env.nodes['#json-config']={id:'json-config',value:'null'};
  await env.handlers.input({target:env.nodes['#json-config']});
  env.fetchWith(async()=>({__response:{ok:false,status:400,json:async()=>({error:'Config must be an object'})}}));
  assert.equal(await env.app.saveConfig(true),false);
  assert.equal(env.app.state.config,previous);
  assert.equal(env.app.state.busy,false);
  assert.match(env.nodes['#toast'].textContent,/Nie zapisano/);
  await env.click({themeChoice:'paper'});
  assert.match(env.nodes['#app'].innerHTML,/>null<\/textarea>/);
  assert.doesNotThrow(()=>env.app.render());
});

test('successful config save clears draft and reports required port restart',async()=>{
  const env=await environment();
  await env.app.navigate('settings');
  env.nodes['#json-config']={id:'json-config',value:JSON.stringify(config())};
  env.app.state.jsonDraft=env.nodes['#json-config'].value;
  env.fetchWith(async(_url,options)=>options.method==='POST'?{restart_required:true}:config());
  assert.equal(await env.app.saveConfig(true),true);
  assert.equal(env.app.state.jsonDraft,null);
  assert.match(env.nodes['#toast'].textContent,/uruchom ponownie/);
});

test('failed manual refresh reports offline state and never success',async()=>{
  const env=await environment();
  env.fetchWith(async()=>{throw new Error('offline');});
  await env.click({action:'refresh'});
  assert.equal(env.app.state.status,null);
  assert.match(env.app.state.error,/offline/);
  assert.notEqual(env.nodes['#toast'].textContent,'Dane odświeżone.');
  assert.match(env.nodes['#app'].innerHTML,/Brak połączenia z trackerem/);
});

test('failed refresh after pause cannot claim tracking resumed',async()=>{
  const env=await environment();
  env.fetchWith(async(target,options)=>{
    if(target==='/api/tracking')return {ok:true};
    throw new Error('offline');
  });
  await env.click({action:'pause'});
  const request=env.requests.find(x=>x.target==='/api/tracking');
  assert.equal(JSON.parse(request.options.body).paused,true);
  assert.doesNotMatch(env.nodes['#toast'].textContent,/wznowione|wstrzymane/);
});

test('late configuration request cannot restore a route user already left',async()=>{
  const env=await environment();
  let finish;
  env.fetchWith(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=env.app.navigate('settings');
  await settle();
  await env.app.navigate('projects');
  finish(config());
  await pending;
  assert.equal(env.app.state.view,'projects');
  assert.equal(env.location.hash,'#projects');
  assert.equal(env.location.searchParams.get('mode'),null);
});

test('failed changed filter cannot display previous-range totals',async()=>{
  const env=await environment();
  env.fetchWith(async()=>{throw new Error('offline');});
  await env.handlers.change({target:{id:'project-filter',value:'Another project'}});
  assert.equal(env.app.state.live,null);
  assert.equal(env.app.state.data,null);
  assert.equal(env.app.state.project,'Another project');
});

test('silent refresh updates idle timeline but preserves an active search input',async()=>{
  const env=await environment('http://127.0.0.1:48763/?mode=live#timeline');
  env.app.state.search='New';
  env.fetchWith(async target=>target.startsWith('/api/status')?{token:'test-token',tracking:{running:true}}:fixture('New project'));
  env.nodes['#app'].innerHTML='before idle refresh';
  assert.equal(await env.app.refresh(true),true);
  assert.notEqual(env.nodes['#app'].innerHTML,'before idle refresh');
  env.document.activeElement={id:'session-search'};
  env.nodes['#app'].innerHTML='preserve focused input';
  assert.equal(await env.app.refresh(true),true);
  assert.equal(env.nodes['#app'].innerHTML,'preserve focused input');
  assert.equal(env.app.state.data.projects[0].name,'New project');
});

test('date validation counts calendar days across daylight-saving transitions',async()=>{
  const env=await environment();
  env.nodes['#start-date']={value:'2025-10-25'};
  env.nodes['#end-date']={value:'2026-10-25'};
  await env.handlers.change({target:{id:'end-date'}});
  assert.equal(env.app.state.start,'2025-10-25');
  assert.equal(env.app.state.end,'2026-10-25');
  env.nodes['#start-date'].value='2026-10-26';
  await env.handlers.change({target:{id:'start-date'}});
  assert.match(env.nodes['#toast'].textContent,/poprawny zakres/);
  assert.equal(env.app.state.start,'2025-10-25');
});

test('WakaTime actions send intended mode and token only to local API',async()=>{
  const env=await environment();
  for(const [action,mode] of [['setup-wakatime','mirror'],['setup-wakatime-local','local-only']]){
    const button=await env.click({action});
    const request=env.requests.findLast(x=>x.target==='/api/integrations/wakatime/setup');
    assert.equal(JSON.parse(request.options.body).mode,mode);
    assert.equal(request.options.headers['X-LocalFlow-Token'],'test-token');
    assert.equal(button.disabled,false);
  }
});

test('legacy import passes file text, clears picker and reports import count',async()=>{
  const env=await environment();
  const text='2026 Week: 10.19-10.25 Duration: 1h:00m:00s';
  const input={id:'legacy-file',value:'test.txt',files:[{size:text.length,text:async()=>text}]};
  await env.handlers.change({target:input});
  await settle();
  const request=env.requests.find(x=>x.target==='/api/import/legacy');
  assert.equal(JSON.parse(request.options.body).text,text);
  assert.equal(input.value,'');
  assert.match(env.nodes['#toast'].textContent,/Zaimportowano 1 tygodni/);
});

test('CSV and JSON exports always download the local API response with current filters',async()=>{
  const env=await environment('http://127.0.0.1:48763/?mode=demo#overview');
  env.app.state.project='Actual project';
  env.fetchWith(async target=>({__response:{ok:true,blob:async()=>new Blob([
    new URL(target,env.location.href).searchParams.get('format')==='json'?'[{"real":true}]':'project,seconds\nActual project,3600'
  ],{type:target.endsWith('json')?'application/json':'text/csv'})}}));
  const before=env.requests.length;
  await env.app.exportData('csv');
  await env.app.exportData('json');
  assert.equal(env.requests.length,before+2);
  for(const request of env.requests.slice(before)){
    const url=new URL(request.target,env.location.href);
    assert.equal(url.pathname,'/api/export');
    assert.equal(url.searchParams.get('project'),'Actual project');
    assert.equal(url.searchParams.get('start'),env.app.state.start);
    assert.equal(url.searchParams.get('end'),env.app.state.end);
    assert.equal(request.options.method,undefined);
  }
  assert.deepEqual(env.downloads.map(x=>x.name),['csv','json'].map(format=>`localflow-${env.app.state.start}-${env.app.state.end}.${format}`));
  assert.equal(await env.blobs[0].text(),'project,seconds\nActual project,3600');
  assert.equal(await env.blobs[1].text(),'[{"real":true}]');
});

test('failed export reports failure without generating an alternative download',async()=>{
  const env=await environment('http://127.0.0.1:48763/?mode=demo#overview');
  env.fetchWith(async()=>({__response:{ok:false}}));
  await env.app.exportData('csv');
  assert.equal(env.downloads.length,0);
  assert.match(env.nodes['#toast'].textContent,/Nie udało się wyeksportować/);
});

test('all views and themes render with escaped user metadata and full chart sums',async()=>{
  const env=await environment();
  env.app.state.config=config();
  env.app.state.live=fixture('<img src=x onerror=alert(1)>');
  for(const theme of ['midnight','paper','terminal','focus']){
    env.app.state.theme=theme;
    for(const view of ['overview','projects','applications','ai','timeline','settings']){
      env.app.state.view=view;env.app.render();
      assert.doesNotMatch(env.nodes['#app'].innerHTML,/<img src=x|NaN|Infinity/);
      assert.match(env.nodes['#app'].innerHTML,/id="view-content"/);
    }
  }
  const items=Array.from({length:8},(_,i)=>({name:'P'+i,seconds:100}));
  const day={date:'2026-10-26',seconds:800,projects:Object.fromEntries(items.map(x=>[x.name,x.seconds]))};
  const series=env.app.chartSeries([day],items);
  assert.equal(series.length,6);
  assert.equal(env.app.chartDayValues(day,series).reduce((a,b)=>a+b,0),800);
  assert.equal(env.app.donutGroups(items).reduce((a,b)=>a+b.seconds,0),800);
});
