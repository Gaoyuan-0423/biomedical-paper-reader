const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../frontend/chat.js'), 'utf8');
const marked = require('../frontend/vendor/marked.min.js');
const A='a'.repeat(32), B='b'.repeat(32), U='c'.repeat(32), M='d'.repeat(32), S='e'.repeat(32);
const paper=id=>({id,has_file:true,notes:'Keep deep notes'});
const source={id:'S1',document_id:S,document_name:'Supplement.pdf',page:2,quote:'Original sample statement',bbox:[.1,.2,.7,.4],source_revision:'v1'};
const user={id:U,role:'user',content:'Question',status:'completed',sources:[]};
const answer=(extra={})=>({id:M,role:'assistant',content:'Response [S1]',status:'completed',sources:[source],...extra});
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};}
function response(events){const bytes=new TextEncoder().encode(events.map(e=>'data: '+JSON.stringify(e)+'\r\n\r\n').join(''));let sent=false;return{ok:true,body:{getReader:()=>({read:async()=>sent?{done:true}:(sent=true,{done:false,value:bytes})})}};}
function harness(savedStorage){
 const handlers={},els={},calls=[],posts=[],jumps=[],toasts=[],storage=savedStorage || new Map();
 const element=()=>({innerHTML:'',value:'',disabled:false,scrollTop:0,scrollHeight:100,clientHeight:80,focus(){this.focused=true;}});
 const panel={_html:'',get innerHTML(){return this._html;},set innerHTML(v){this._html=v;const m=v.match(/<textarea[^>]*>([\s\S]*?)<\/textarea>/);if(m)el('chat-input').value=m[1];},querySelector:s=>el(s.slice(1)),addEventListener:(n,fn)=>handlers[n]=fn};
 const el=id=>els[id]||(els[id]=element());
 const hooks={panel,request:async(method,url)=>{calls.push({method,url});return{messages:[],has_more:false};},toast:(...a)=>toasts.push(a),jump:async s=>{jumps.push(s);return true;},fetch:async(url,opts)=>{posts.push({url,body:JSON.parse(opts.body),signal:opts.signal});return response([{type:'start',message:user,assistant:answer({content:'',status:'pending'})},{type:'chunk',message_id:M,text:'Response [S1]'},{type:'done',message:answer()}]);}};
 const context=vm.createContext({window:{marked,sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)}},document:{getElementById:()=>panel},AbortController,TextDecoder,URL,console,fetch:()=>{throw Error('unexpected fetch');}});
 vm.runInContext(code,context);const ui=context.window.BioChat;ui.init(hooks);
 return{ui,hooks,panel,calls,posts,jumps,toasts,storage,el,context,
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
 await h.submit();assert.equal(h.posts.length,0);assert.match(h.el('chat-context').innerHTML,/使用当前页/);await h.click('use-current-context');await h.submit();assert.equal(h.posts[0].body.selection,'');assert.equal(h.posts[0].body.page,1);
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

test('completed answers render structured Markdown with prose citations but code stays literal',async()=>{
 const h=harness();h.hooks.request=async()=>({messages:[answer({content:'# Results\n\n**Key** and _caveat_ [S1]\n\n- sample one\n- sample two\n\n> Quoted conclusion\n\n| Sample | Result |\n| --- | --- |\n| A | [S1] |\n\n`literal <b> [S1]`\n\n```html\n<script>alert(1)</script> [S1]\n```'})]});await h.ui.open(paper(A));
 const html=h.el('chat-history').innerHTML;
 assert.match(html,/<h2>Results<\/h2>/);assert.match(html,/<strong>Key<\/strong>/);assert.match(html,/<em>caveat<\/em>/);assert.match(html,/<ul><li>sample one/);assert.match(html,/<blockquote><p>Quoted conclusion/);assert.match(html,/<thead><tr><th>Sample/);assert.match(html,/<code>literal &lt;b&gt; \[S1\]<\/code>/);assert.match(html,/<pre><code>&lt;script&gt;/);
 assert.equal((html.match(/data-chat-source="S1"/g)||[]).length,3);assert.doesNotMatch(html,/<script>|<b>/);
});

test('Markdown links, images and raw HTML cannot smuggle source controls or executable markup',async()=>{
 const h=harness();h.hooks.request=async()=>({messages:[answer({content:'[safe [S1]](https://example.org/paper)\n\n[bad](javascript:alert%281%29) [file](file:///etc/passwd) [credential](https://user:secret@example.org/)\n\n![tracking [S1]](https://example.org/track.png)\n\n<img src=x onerror=bad()>\n\n<iframe src="https://example.org"></iframe>\n\n`[S1]` and [S999] and [S1]'})]});await h.ui.open(paper(A));
 const html=h.el('chat-history').innerHTML;
 assert.match(html,/<a href="https:\/\/example.org\/paper" target="_blank" rel="noopener noreferrer">safe \[S1\]<\/a>/);
 assert.doesNotMatch(html,/<img|<iframe|href="(?:javascript|file|data):|user:secret|track\.png/);
 assert.match(html,/&lt;img/);assert.match(html,/&lt;iframe/);assert.match(html,/tracking \[S1\]/);assert.equal((html.match(/data-chat-source="S1"/g)||[]).length,2);
});

test('streaming and incomplete answers stay literal and never enable completed-answer actions',async()=>{
 const h=harness();let copies=0,saves=0;h.hooks.copyText=async()=>copies++;h.hooks.appendNote=async()=>{saves++;return true;};h.hooks.request=async()=>({messages:[answer({status:'pending',content:'## Partial [S1]'}),answer({id:B,status:'interrupted',content:'**Incomplete**'})]});await h.ui.open(paper(A));
 const html=h.el('chat-history').innerHTML;assert.match(html,/## Partial \[S1\]/);assert.doesNotMatch(html,/<h2>|<strong>|data-chat-action="(?:copy|append)"/);
 await h.click('copy',{chatMessage:M});await h.click('append',{chatMessage:B});assert.equal(copies,0);assert.equal(saves,0);
});

test('completed-answer Markdown is cached while another answer streams and missing marked is harmless',async()=>{
 const h=harness();let parses=0;h.context.window.marked={lexer(...args){parses++;return marked.lexer(...args);}};
 h.hooks.request=async()=>({messages:[answer()]});await h.ui.open(paper(A));const baseline=parses;
 const pending=deferred();h.hooks.fetch=()=>pending.promise;h.input('Next question');const sending=h.submit();assert.equal(parses,baseline);
 pending.resolve(response([{type:'start',message:user,assistant:answer({id:B,content:'',status:'pending'})},{type:'chunk',message_id:B,text:'## Live'},{type:'done',message:answer({id:B,content:'## Finished'})}]));await sending;assert.equal(parses,baseline+1);
 h.context.window.marked=null;await h.click('reload');assert.match(h.el('chat-history').innerHTML,/Response \[S1\]/);
});

test('page starters fill a draft without sending, pin its page, then follow scrolling after send',async()=>{
 const h=harness();await h.ui.open(paper(A));h.ui.setContext({paper_id:A,document_id:S,document_name:'Supplement.pdf',page:2,selection:''});await h.click('quick-methods');
 assert.equal(h.posts.length,0);assert.match(h.el('chat-input').value,/Supplement\.pdf第 2 页.*样品、分组/);assert.match(h.el('chat-input').value,/原文片段/);
 h.ui.setContext({paper_id:A,document_id:'main',page:3,selection:''});assert.match(h.el('chat-context').innerHTML,/Supplement.pdf.*第 2 页/);assert.match(h.el('chat-context').innerHTML,/跟随当前页/);
 await h.submit();assert.equal(h.posts[0].body.document_id,S);assert.equal(h.posts[0].body.page,2);
 h.ui.setContext({paper_id:A,document_id:'main',page:4,selection:''});h.input('Next custom question');await h.submit();assert.equal(h.posts[1].body.document_id,'main');assert.equal(h.posts[1].body.page,4);
});

test('starters preserve existing drafts and selected source contexts, with explicit unknown limitations',async()=>{
 const h=harness();await h.ui.open(paper(A));h.input('My unfinished thought');await h.click('quick-page');assert.equal(h.el('chat-input').value,'My unfinished thought');assert.match(h.html(),/输入框已有问题/);assert.equal(h.posts.length,0);
 h.input('');await h.ui.askSelection('ask',{paper_id:A,document_id:S,document_name:'Selected supplement',page:3,selection:'Exact selection'});await h.click('quick-limits');assert.match(h.el('chat-input').value,/当前选中的原文/);assert.match(h.el('chat-input').value,/不把当前片段未提及的内容当作整篇论文没有报告/);
 assert.doesNotMatch(h.el('chat-context').innerHTML,/data-chat-action="quick-page"/);await h.submit();assert.equal(h.posts[0].body.selection,'Exact selection');assert.equal(h.posts[0].body.document_id,S);assert.equal(h.posts[0].body.page,3);
});

test('clearing a page starter releases its pin and an invalid PDF context cannot create a starter',async()=>{
 const h=harness();await h.ui.open(paper(A));h.ui.setContext({document_id:'main',page:2});await h.click('quick-page');h.ui.setContext({document_id:'main',page:5});h.input('');assert.match(h.el('chat-context').innerHTML,/第 5 页/);
 h.ui.setContext({document_id:'',page:0});await h.click('quick-limits');assert.equal(h.el('chat-input').value,'');assert.equal(h.posts.length,0);
});

test('copy includes full source descriptors and append reports success only after persistence',async()=>{
 const h=harness(),saving=deferred(),copied=[],saved=[];h.hooks.copyText=async text=>copied.push(text);h.hooks.appendNote=async payload=>{saved.push(payload);return saving.promise;};h.hooks.request=async()=>({messages:[answer()]});await h.ui.open(paper(A));
 await h.click('copy',{chatMessage:M});assert.match(copied[0],/Response \[S1\]/);assert.match(copied[0],/Supplement\.pdf · PDF 第 2 页\nOriginal sample statement/);assert.match(h.html(),/已复制回答及原文来源/);
 const appending=h.click('append',{chatMessage:M});assert.match(h.html(),/正在保存/);assert.doesNotMatch(h.html(),/已追加到笔记/);await h.click('append',{chatMessage:M});assert.equal(saved.length,1);assert.equal(saved[0].paperId,A);assert.equal(saved[0].message.id,M);
 saving.resolve(true);await appending;assert.match(h.html(),/已追加到笔记/);await h.click('append',{chatMessage:M});assert.equal(saved.length,1);
});

test('failed append and clipboard actions remain retryable and do not claim saved or copied',async()=>{
 const h=harness();h.hooks.copyText=async()=>{throw Error('Clipboard denied');};h.hooks.appendNote=async()=>false;h.hooks.request=async()=>({messages:[answer()]});await h.ui.open(paper(A));
 await h.click('copy',{chatMessage:M});assert.match(h.html(),/复制失败：Clipboard denied/);assert.doesNotMatch(h.html(),/已复制回答/);
 await h.click('append',{chatMessage:M});assert.match(h.html(),/追加失败：笔记尚未保存/);assert.doesNotMatch(h.html(),/已追加到笔记/);h.hooks.appendNote=async()=>{throw Error('Disk full');};await h.click('append',{chatMessage:M});assert.match(h.html(),/追加失败：Disk full/);
 h.hooks.appendNote=async()=>true;await h.click('append',{chatMessage:M});assert.match(h.html(),/已追加到笔记/);
});

test('late append completion stays tied to its original paper and cannot show success on another paper',async()=>{
 const h=harness(),saving=deferred(),saved=[];h.hooks.appendNote=async payload=>{saved.push(payload);return saving.promise;};h.hooks.request=async()=>({messages:[answer()]});await h.ui.open(paper(A));const appending=h.click('append',{chatMessage:M});
 await h.ui.open(paper(B));saving.resolve(true);await appending;assert.equal(saved[0].paperId,A);assert.doesNotMatch(h.html(),/已追加到笔记|回答及原文来源已追加/);
 await h.ui.open(paper(A));assert.match(h.html(),/已追加到笔记/);await h.click('append',{chatMessage:M});assert.equal(saved.length,1);
});

test('reloading a page starter restores its original PDF context along with the draft',async()=>{
 const h=harness(),p={...paper(A),material_revision:'same',supplements:[{id:S,filename:'Supplement.pdf'}]};await h.ui.open(p);h.ui.setContext({document_id:S,document_name:'Supplement.pdf',page:2});await h.click('quick-page');h.ui.setContext({document_id:'main',page:7});
 const restored=harness(h.storage);await restored.ui.open(p);restored.ui.setContext({document_id:'main',page:7});assert.match(restored.el('chat-context').innerHTML,/Supplement.pdf.*第 2 页/);assert.match(restored.el('chat-input').value,/第 2 页/);await restored.submit();assert.equal(restored.posts[0].body.document_id,S);assert.equal(restored.posts[0].body.page,2);
});

test('reloaded draft with replaced material or removed supplement requires explicit new context',async()=>{
 for(const change of ['revision','removed']){
  const h=harness(),p={...paper(A),material_revision:'old',supplements:[{id:S,filename:'Supplement.pdf'}]};await h.ui.open(p);h.ui.setContext({document_id:S,document_name:'Supplement.pdf',page:2});await h.click('quick-methods');
  const restored=harness(h.storage);await restored.ui.open({...p,material_revision:change==='revision'?'new':'old',supplements:change==='removed'?[]:p.supplements});restored.ui.setContext({document_id:'main',page:4});assert.match(restored.el('chat-input').value,/第 2 页/);assert.match(restored.el('chat-context').innerHTML,/请重新确认提问位置/);assert.equal(restored.el('chat-send').disabled,true);await restored.submit();assert.equal(restored.posts.length,0);
  const again=harness(h.storage);await again.ui.open({...p,material_revision:'new'});again.ui.setContext({document_id:'main',page:4});await again.click('use-current-context');await again.submit();assert.equal(again.posts[0].body.document_id,'main');assert.equal(again.posts[0].body.page,4);
 }
});

test('a failed request restores a page starter and its original source for direct resubmission',async()=>{
 const h=harness(),pending=deferred();await h.ui.open({...paper(A),material_revision:'same'});h.ui.setContext({document_id:'main',page:2});await h.click('quick-page');h.hooks.fetch=()=>pending.promise;const sending=h.submit();h.ui.setContext({document_id:'main',page:7});pending.resolve({ok:false,statusText:'offline',json:async()=>({detail:'offline'})});await sending;
 assert.match(h.el('chat-input').value,/第 2 页/);assert.match(h.el('chat-context').innerHTML,/第 2 页/);
 const restored=harness(h.storage);await restored.ui.open({...paper(A),material_revision:'same'});restored.ui.setContext({document_id:'main',page:7});await restored.submit();assert.equal(restored.posts[0].body.page,2);
});
