const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../frontend/chat.js'), 'utf8');
const A='a'.repeat(32), B='b'.repeat(32), U='c'.repeat(32), M='d'.repeat(32), S='e'.repeat(32);
const paper=id=>({id,has_file:true,notes:'Keep deep notes'});
const source={id:'S1',document_id:S,document_name:'Supplement.pdf',page:2,quote:'Original sample statement',bbox:[.1,.2,.7,.4],source_revision:'v1'};
const user={id:U,role:'user',content:'Question',status:'completed',sources:[]};
const answer=(extra={})=>({id:M,role:'assistant',content:'Response [S1]',status:'completed',sources:[source],...extra});
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};}
function response(events){const bytes=new TextEncoder().encode(events.map(e=>'data: '+JSON.stringify(e)+'\r\n\r\n').join(''));let sent=false;return{ok:true,body:{getReader:()=>({read:async()=>sent?{done:true}:(sent=true,{done:false,value:bytes})})}};}
function harness(){
 const handlers={},els={},calls=[],posts=[],jumps=[],toasts=[],storage=new Map();
 const element=()=>({innerHTML:'',value:'',disabled:false,scrollTop:0,scrollHeight:100,clientHeight:80,focus(){this.focused=true;}});
 const panel={_html:'',get innerHTML(){return this._html;},set innerHTML(v){this._html=v;const m=v.match(/<textarea[^>]*>([\s\S]*?)<\/textarea>/);if(m)el('chat-input').value=m[1];},querySelector:s=>el(s.slice(1)),addEventListener:(n,fn)=>handlers[n]=fn};
 const el=id=>els[id]||(els[id]=element());
 const hooks={panel,request:async(method,url)=>{calls.push({method,url});return{messages:[],has_more:false};},toast:(...a)=>toasts.push(a),jump:async s=>{jumps.push(s);return true;},fetch:async(url,opts)=>{posts.push({url,body:JSON.parse(opts.body),signal:opts.signal});return response([{type:'start',message:user,assistant:answer({content:'',status:'pending'})},{type:'chunk',message_id:M,text:'Response [S1]'},{type:'done',message:answer()}]);}};
 const context=vm.createContext({window:{sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)}},document:{getElementById:()=>panel},AbortController,TextDecoder,console,fetch:()=>{throw Error('unexpected fetch');}});
 vm.runInContext(code,context);const ui=context.window.BioChat;ui.init(hooks);
 return{ui,hooks,panel,calls,posts,jumps,toasts,storage,el,
 input(value){el('chat-input').value=value;handlers.input({target:{id:'chat-input',value}});},
 submit(){return handlers.submit({target:{id:'chat-form'},preventDefault(){}});},
 click(action,data={}){return handlers.click({target:{closest:()=>({dataset:{chatAction:action,...data},disabled:false})}});},
 html(){return panel.innerHTML+Object.values(els).map(e=>e.innerHTML).join('');}};
}

test('history restores per paper while repeated opens and switches preserve drafts',async()=>{
 const h=harness();h.hooks.request=async(_,url)=>({messages:[answer({content:url.includes(A)?'History A':'History B'})]});
 await h.ui.open(paper(A));h.input('Unsaved A');await h.ui.open(paper(A));assert.equal(h.el('chat-input').value,'Unsaved A');
 await h.ui.open(paper(B));h.input('Unsaved B');await h.ui.open(paper(A));assert.match(h.html(),/History A/);assert.equal(h.el('chat-input').value,'Unsaved A');assert.equal(h.ui.canLeave(),true);
 assert.equal(h.storage.get('bioreader.chat.draft.'+A),'Unsaved A');
});

test('late history cannot replace another paper and a failed history read keeps the draft',async()=>{
 const h=harness(),old=deferred();h.hooks.request=async(_,url)=>url.includes(A)?old.promise:{messages:[answer({content:'Current B'})]};
 const opening=h.ui.open(paper(A));await h.ui.open(paper(B));old.resolve({messages:[answer({content:'Old A'})]});await opening;
 assert.match(h.html(),/Current B/);assert.doesNotMatch(h.html(),/Old A/);h.input('B draft');h.hooks.request=async()=>{throw Error('offline');};await h.click('reload');assert.match(h.html(),/对话读取失败：offline/);assert.equal(h.el('chat-input').value,'B draft');
});

test('selected context remains pinned across page changes and a shortcut preserves an existing question draft',async()=>{
 const h=harness();await h.ui.open(paper(A));h.input('My next question');await h.ui.askSelection('ask',{paper_id:A,document_id:S,document_name:'Supplement.pdf',page:2,selection:'Selected passage'});
 h.ui.setContext({paper_id:A,document_id:'main',document_name:'正文',page:7,selection:''});
 assert.match(h.el('chat-context').innerHTML,/Supplement.pdf.*PDF 第 2 页/);assert.equal(h.el('chat-input').focused,true);
 await h.click('explain');assert.equal(h.posts[0].body.document_id,S);assert.equal(h.posts[0].body.page,2);assert.equal(h.posts[0].body.selection,'Selected passage');assert.equal(h.posts[0].body.action,'explain');assert.equal(h.el('chat-input').value,'My next question');
 await h.click('clear-selection');assert.match(h.el('chat-context').innerHTML,/正文.*第 7 页/);
});

test('selection action during history loading waits and does not duplicate requests',async()=>{
 const h=harness(),history=deferred();h.hooks.request=()=>history.promise;const opening=h.ui.open(paper(A));const asking=h.ui.askSelection('translate',{paper_id:A,document_id:'main',page:3,selection:'Translate this'});
 assert.equal(h.posts.length,0);history.resolve({messages:[]});await opening;await asking;assert.equal(h.posts.length,1);assert.equal(h.posts[0].body.action,'translate');
});

test('streaming accumulates completed replies, preserves deep notes and links only actual message sources',async()=>{
 const h=harness(),p=paper(A);await h.ui.open(p);h.input('Explain samples');await h.submit();assert.equal(h.posts.length,1);assert.equal(h.posts[0].body.message,'Explain samples');assert.equal(h.el('chat-input').value,'');assert.equal(p.notes,'Keep deep notes');
 assert.match(h.el('chat-history').innerHTML,/data-chat-source="S1"/);assert.doesNotMatch(h.el('chat-history').innerHTML,/正在回答/);
 await h.click('source',{chatMessage:M,chatSource:'S999'});assert.equal(h.jumps.length,0);
 h.hooks.request=async(method,url)=>{h.calls.push({method,url});return{source};};await h.click('source',{chatMessage:M,chatSource:'S1'});
 assert.ok(h.calls.at(-1).url.endsWith('/sources/'+M+'/S1'));assert.equal(h.jumps[0].document_id,S);assert.equal(h.jumps[0].page,2);
});

test('HTML and unknown citations stay harmless plain text; source IDs are scoped to one answer',async()=>{
 const h=harness();h.hooks.request=async()=>({messages:[answer({content:'<img src=x onerror=bad()> [S999] [S1]'}),answer({id:B,content:'[S1]',sources:[]})]});await h.ui.open(paper(A));
 assert.doesNotMatch(h.html(),/<img src|onerror="|data-chat-source="S999"/);assert.match(h.html(),/&lt;img/);assert.match(h.html(),/\[S999\]/);assert.equal((h.el('chat-history').innerHTML.match(/data-chat-source="S1"/g)||[]).length,2);
});

test('source verification failure, missing jump target and late source responses never jump incorrectly',async()=>{
 const h=harness();h.hooks.request=async()=>({messages:[answer()]});await h.ui.open(paper(A));h.hooks.request=async()=>{throw Error('文件已更换');};await h.click('source',{chatMessage:M,chatSource:'S1'});assert.equal(h.jumps.length,0);assert.match(h.html(),/文件已更换/);
 h.hooks.request=async()=>({source});h.hooks.jump=async()=>false;await h.click('source',{chatMessage:M,chatSource:'S1'});assert.match(h.html(),/来源文件或页码已不可用/);
 const old=deferred();h.hooks.request=()=>old.promise;const jump=h.click('source',{chatMessage:M,chatSource:'S1'});h.ui.clear();h.hooks.request=async()=>({messages:[]});await h.ui.open(paper(B));old.resolve({source});await jump;assert.equal(h.jumps.length,0);
});

test('HTTP failures retain submitted draft and explicit retry reuses original source context',async()=>{
 const h=harness();await h.ui.open(paper(A));h.input('Keep this question');h.hooks.fetch=async()=>({ok:false,statusText:'Bad',json:async()=>({detail:'模型不可用'})});await h.submit();assert.equal(h.el('chat-input').value,'Keep this question');assert.match(h.html(),/模型不可用/);
 const bodies=[];h.hooks.fetch=async(_,o)=>{bodies.push(JSON.parse(o.body));return response([{type:'start',message:user,assistant:answer({status:'pending'})},{type:'done',message:answer()}]);};h.ui.setContext({document_id:S,page:4,selection:''});await h.click('retry');assert.equal(bodies[0].document_id,'main');assert.equal(bodies[0].page,1);
});

test('stop before first event restores the input and late response cannot render into another paper',async()=>{
 const h=harness(),pending=deferred();await h.ui.open(paper(A));h.hooks.fetch=()=>pending.promise;h.input('Stopped question');const sending=h.submit();await h.click('stop');assert.equal(h.el('chat-input').value,'Stopped question');assert.match(h.html(),/已停止生成/);
 await h.ui.open(paper(B));pending.resolve(response([{type:'start',message:user,assistant:answer()},{type:'done',message:answer({content:'Late A response'})}]));await sending;assert.doesNotMatch(h.html(),/Late A response/);await h.ui.open(paper(A));assert.equal(h.el('chat-input').value,'Stopped question');
});

test('truncated stream and SSE errors preserve received partial content with retry guidance',async()=>{
 const h=harness();await h.ui.open(paper(A));h.hooks.fetch=async()=>response([{type:'start',message:user,assistant:answer({content:'',status:'pending'})},{type:'chunk',message_id:M,text:'Partial result'}]);h.input('Question');await h.submit();assert.match(h.html(),/Partial result/);assert.match(h.html(),/连接中断/);assert.match(h.html(),/回复已中断/);
 h.hooks.fetch=async()=>response([{type:'start',message:user,assistant:answer({content:'',status:'pending'})},{type:'error',message:'上游失败',assistant:answer({content:'Saved partial',status:'error'})}]);h.input('Retry question');await h.submit();assert.match(h.html(),/Saved partial/);assert.match(h.html(),/上游失败/);assert.match(h.html(),/重试上一问题/);
});

test('older history paginates before first ID and deduplicates overlap',async()=>{
 const h=harness();h.hooks.request=async(_,url)=>{h.calls.push({url});return url.includes('?before=')?{messages:[{...user,id:B},user],has_more:false}:{messages:[user,answer()],has_more:true,total:3};};await h.ui.open(paper(A));assert.match(h.html(),/加载更早对话/);await h.click('earlier');assert.ok(h.calls.at(-1).url.endsWith('?before='+U));assert.equal((h.el('chat-history').innerHTML.match(/data-chat-message-id=/g)||[]).length,3);assert.doesNotMatch(h.el('chat-history').innerHTML,/加载更早对话/);
});

test('replaced material clears stale selected context while retaining the question draft',async()=>{
 const h=harness();await h.ui.open({...paper(A),material_revision:'old'});h.input('Still my question');await h.ui.askSelection('ask',{document_id:'main',page:2,selection:'Old PDF quote'});
 h.ui.clear();await h.ui.open({...paper(A),material_revision:'new'});h.ui.setContext({document_id:'main',page:1,selection:''});
 assert.doesNotMatch(h.el('chat-context').innerHTML,/Old PDF quote/);assert.match(h.html(),/原文材料已更新/);assert.equal(h.el('chat-input').value,'Still my question');
 await h.submit();assert.equal(h.posts[0].body.selection,'');assert.equal(h.posts[0].body.page,1);
});

test('latest source click wins over a delayed earlier source and incomplete metadata is reported',async()=>{
 const h=harness(),first=deferred();h.hooks.request=async()=>({messages:[answer({sources:[source,{...source,id:'S2',page:3}]})]});await h.ui.open(paper(A));
 h.hooks.request=async(_,url)=>url.endsWith('/S1')?first.promise:{source:{...source,id:'S2',page:3}};
 const slow=h.click('source',{chatMessage:M,chatSource:'S1'});await h.click('source',{chatMessage:M,chatSource:'S2'});first.resolve({source});await slow;
 assert.equal(h.jumps.length,1);assert.equal(h.jumps[0].page,3);
 h.hooks.request=async()=>({});await h.click('source',{chatMessage:M,chatSource:'S1'});assert.match(h.html(),/来源信息不完整/);
});

test('a restored pending response without a local stream gives recovery guidance instead of an active spinner',async()=>{
 const h=harness();h.hooks.request=async()=>({messages:[answer({status:'pending',content:'Saved partial'})]});await h.ui.open(paper(A));
 assert.match(h.html(),/上一轮回复仍在结束或保存/);assert.doesNotMatch(h.html(),/正在回答…|data-chat-action="stop"/);
});

test('restored selection questions retain their exact selected text and source page safely',async()=>{
 const h=harness();h.hooks.request=async()=>({messages:[{...user,content:'请解释这段选中的原文。',document_id:S,document_name:'Supplement.pdf',page:3,selection:'<script>Original selected passage</script>'}]});await h.ui.open(paper(A));
 assert.match(h.el('chat-history').innerHTML,/当时选中的原文 · Supplement.pdf · 第 3 页/);assert.match(h.el('chat-history').innerHTML,/&lt;script&gt;Original selected passage/);assert.doesNotMatch(h.el('chat-history').innerHTML,/<script>/);
});
