const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'../frontend/appearance.js'),'utf8');
const defaults = {locale:'zh-CN',scheme:'system',accent:'blue',ui_font_size:13,note_font_size:14};
const tick = () => new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function harness(options={}) {
  const nodes=new Map(),timers=new Map(),requests=[],listeners=[],memory=new Map(),catalog={};let nextTimer=0,locale='zh-CN',stored={...defaults,...options.stored};
  if(options.cached!==undefined)memory.set('bioreader.appearance',JSON.stringify(options.cached));
  const element=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',hidden:false,dataset:{},attrs:{},handlers:{},setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,fn){this.handlers[k]=fn;}});return nodes.get(id);};
  const root={dataset:{},style:{setProperty(k,v){this[k]=v;}}};const media={matches:!!options.dark,addEventListener(k,fn){this.changed=fn;}};
  const document={readyState:'loading',documentElement:root,getElementById:element,addEventListener(){}};
  const fetch=async(url,request)=>{requests.push({url,method:request.method,body:request.body?JSON.parse(request.body):null});if(options.fetch)return options.fetch(url,request);if(request.method==='PATCH')stored={...stored,...JSON.parse(request.body)};return {ok:true,json:async()=>({...stored})};};
  const window={document,fetch,matchMedia:()=>media,localStorage:{getItem:k=>memory.get(k),setItem:(k,v)=>memory.set(k,v)},
    setTimeout(fn,ms){const id=++nextTimer;timers.set(id,{fn,ms});return id;},clearTimeout(id){timers.delete(id);},
    BioI18n:{register(v){Object.assign(catalog,v);},t(key){return locale==='en'?catalog[key]||key:key;},onChange(fn){listeners.push(fn);}},};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../frontend/i18n-shell.js'),'utf8'),{window});
  vm.runInNewContext(source,{window});const api=window.BioAppearance;
  return {api,root,media,nodes,element,requests,timers,window,memory,getStored:()=>stored,
    locale(value){locale=value;listeners.forEach(fn=>fn());},system(value){media.matches=value;media.changed?.({matches:value});}};
}

test('defaults and validated cached preview apply before loading without allowing injected CSS',async()=>{
 const h=harness({dark:true});assert.equal(h.root.dataset.colorScheme,'dark');assert.equal(h.root.style['--ui-font-size'],'13px');assert.equal(h.element('app-theme-color').attrs.content,'#101722');await h.api.init();assert.equal(h.root.dataset.appearanceState,'ready');
 const cached=harness({cached:{scheme:'light',accent:'violet',ui_font_size:18,note_font_size:24}});assert.equal(cached.root.dataset.colorScheme,'light');assert.equal(cached.root.style['--note-font-size'],'24px');
 const bad=harness({cached:{scheme:'dark',accent:'url(https://evil.test)',ui_font_size:'20px',note_font_size:24}});assert.equal(bad.root.dataset.accent,'blue');assert.equal(bad.root.style['--ui-font-size'],'13px');
 for(const patch of [{locale:'en'},{ui_font_size:19},{note_font_size:11},{ui_font_size:14.5},{scheme:'auto'},{accent:'red'},{ui_font_size:'18'},{}])assert.throws(()=>h.api.update(patch),/Invalid appearance/);
});

test('system changes apply only in follow-system mode and do not write preferences',async()=>{
 const h=harness();await h.api.init();h.system(true);assert.equal(h.root.dataset.colorScheme,'dark');assert.equal(h.requests.length,1);
 h.api.update({scheme:'light'});await h.api.flush();h.system(false);h.system(true);assert.equal(h.root.dataset.colorScheme,'light');assert.equal(h.requests.length,2);
 h.api.update({scheme:'system'});await h.api.flush();assert.equal(h.root.dataset.colorScheme,'dark');h.system(false);assert.equal(h.root.dataset.colorScheme,'light');assert.equal(h.element('app-theme-color').attrs.content,'#f3f6fb');
});

test('late initial preferences merge untouched fields but never overwrite a new slider choice',async()=>{
 const pending=deferred(),h=harness({fetch:async(url,request)=>request.method==='GET'?pending.promise:{ok:true,json:async()=>({...defaults,scheme:'dark',accent:'teal',ui_font_size:18,note_font_size:22})}});
 const loading=h.api.init();assert.match(h.element('appearance-status').textContent,/载入/);h.api.update({ui_font_size:18});pending.resolve({ok:true,json:async()=>({...defaults,scheme:'dark',accent:'teal',ui_font_size:12,note_font_size:22})});await loading;
 assert.equal(h.api.getPreferences().ui_font_size,18);assert.equal(h.api.getPreferences().note_font_size,22);assert.equal(h.root.dataset.accent,'teal');await h.api.flush();assert.deepEqual(h.requests[1].body,{ui_font_size:18});
});

test('rapid slider updates serialize partial writes while stale responses leave newer previews intact',async()=>{
 const pending=deferred();let writes=0;const h=harness({fetch:async(url,request)=>{if(request.method==='GET')return {ok:true,json:async()=>({...defaults})};if(++writes===1)return pending.promise;return {ok:true,json:async()=>({...defaults,ui_font_size:18,note_font_size:23})};}});await h.api.init();
 h.api.update({ui_font_size:15});const saving=h.api.flush();await tick();h.api.update({ui_font_size:18,note_font_size:23});pending.resolve({ok:true,json:async()=>({...defaults,ui_font_size:15})});await saving;
 assert.equal(h.root.style['--ui-font-size'],'18px');assert.equal(h.root.style['--note-font-size'],'23px');assert.deepEqual(h.requests.slice(1).map(r=>r.body),[{ui_font_size:15},{ui_font_size:18,note_font_size:23}]);assert.equal(h.root.dataset.appearanceState,'ready');
});

test('failed saves retain preview and pending fields for an explicit retry without changing locale',async()=>{
 let fail=true;const h=harness({fetch:async(url,request)=>({ok:request.method==='GET'||!fail,json:async()=>({...defaults,locale:'en',...(request.body?JSON.parse(request.body):{})})})});await h.api.init();h.api.update({accent:'violet',note_font_size:20});assert.equal(await h.api.flush(),false);assert.equal(h.root.dataset.accent,'violet');assert.equal(h.element('appearance-retry').hidden,false);
 h.locale('en');assert.match(h.element('appearance-status').textContent,/not saved/);assert.equal(h.root.style['--note-font-size'],'20px');fail=false;assert.equal(await h.api.retry(),true);assert.equal(h.element('appearance-retry').hidden,true);assert(h.requests.filter(r=>r.method==='PATCH').every(r=>!Object.hasOwn(r.body,'locale')));
});

test('a successful partial save after failed initial load recovers other persisted fields',async()=>{
 const h=harness({fetch:async(url,request)=>({ok:request.method!=='GET',json:async()=>({...defaults,scheme:'dark',accent:'teal',note_font_size:24,...(request.body?JSON.parse(request.body):{})})})});await h.api.init();assert.equal(h.root.dataset.appearanceState,'error');h.api.update({ui_font_size:17});assert.equal(await h.api.flush(),true);
 assert.equal(h.root.dataset.colorScheme,'dark');assert.equal(h.root.dataset.accent,'teal');assert.equal(h.root.style['--note-font-size'],'24px');assert.deepEqual(h.requests.at(-1).body,{ui_font_size:17});
});

test('retrying failed initial load reads disk preferences without writing cached defaults',async()=>{
 let fail=true;const h=harness({fetch:async()=>({ok:!fail,json:async()=>({...defaults,scheme:'dark',accent:'violet',ui_font_size:16,note_font_size:21})})});await h.api.init();assert.equal(await h.api.flush(),false);fail=false;assert.equal(await h.api.retry(),true);assert.equal(h.root.style['--note-font-size'],'21px');assert.deepEqual(h.requests.map(r=>r.method),['GET','GET']);
});

test('a successful save wins over an older initial GET failure and response',async()=>{
 for(const failed of [false,true]){
  const pending=deferred(),h=harness({fetch:async(url,request)=>request.method==='GET'?pending.promise:{ok:true,json:async()=>({...defaults,scheme:'dark',accent:'teal',ui_font_size:18,note_font_size:22})}});const loading=h.api.init();h.api.update({ui_font_size:18});await h.api.flush();pending.resolve({ok:!failed,json:async()=>({...defaults})});await loading;assert.equal(h.root.dataset.appearanceState,'ready');assert.equal(h.root.dataset.colorScheme,'dark');assert.equal(h.root.style['--note-font-size'],'22px');
 }
});

test('unresponsive preference writes time out and leave an actionable retry state',async()=>{
 const h=harness();await h.api.init();h.window.AbortController=AbortController;h.window.fetch=(url,request)=>new Promise((resolve,reject)=>request.signal.addEventListener('abort',()=>reject(Error('timeout'))));h.api.update({accent:'teal'});const saving=h.api.flush();await tick();const timeout=[...h.timers.values()].find(item=>item.ms===8000);assert.ok(timeout);timeout.fn();assert.equal(await saving,false);assert.equal(h.element('appearance-retry').hidden,false);assert.equal(h.root.dataset.accent,'teal');
});
