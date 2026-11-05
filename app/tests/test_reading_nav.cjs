const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../frontend/reading-nav.js'), 'utf8');
const A = 'a'.repeat(32), B = 'b'.repeat(32), S = 'c'.repeat(32);
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
function harness() {
  const nodes = [];
  function match(node, selector) {
    const m = /^\[data-([a-z-]+)(?:="([^"]*)")?\]$/.exec(selector);
    if (!m) return false;
    const key = m[1].replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    return Object.hasOwn(node.dataset, key) && (m[2] === undefined || node.dataset[key] === m[2]);
  }
  function element(tag) {
    const node = {tagName:tag.toUpperCase(), children:[], dataset:{}, attributes:{}, handlers:{}, style:{setProperty(k,v){this[k]=v;}}, classList:{add(){}}, disabled:false, hidden:false, _text:'',
      appendChild(child){this.children.push(child);child.parent=this;return child;},
      replaceChildren(...children){this.children=[];this._text='';children.forEach(c=>this.appendChild(c));},
      setAttribute(k,v){this.attributes[k]=v;}, getAttribute(k){return this.attributes[k];},
      set textContent(v){this._text=String(v);this.children=[];}, get textContent(){return this._text+this.children.map(c=>c.textContent).join('');},
      set innerHTML(_){throw Error('Navigation must never insert HTML');},
      addEventListener(name, fn){this.handlers[name]=fn;}, contains(target){return this===target || this.children.some(c=>c.contains(target));},
      closest(selector){return match(this,selector)?this:this.parent?.closest(selector);},
      querySelectorAll(selector){return this.children.flatMap(c=>[...(match(c,selector)?[c]:[]),...c.querySelectorAll(selector)]);},
      querySelector(selector){return this.querySelectorAll(selector)[0] || null;}, focus(){this.focused=true;}
    };nodes.push(node);return node;
  }
  const container = element('div'), calls = [], toggles = [];
  const hooks = {onPage:async(target,guard)=>{calls.push({kind:'page',target,guard});return true;},onFigure:async(target,guard)=>{calls.push({kind:'figure',target,guard});return true;},onHeading:async(target,guard)=>{calls.push({kind:'heading',target,guard});return true;},onToggle:value=>toggles.push(value)};
  const context = vm.createContext({window:{},document:{createElement:element},console});vm.runInContext(code,context);
  const ui = context.window.BioReadingNav;ui.init(container,hooks);
  const find = (action,index) => container.querySelectorAll('[data-reading-nav-action="'+action+'"]').find(n=>index===undefined || n.dataset.readingNavIndex===String(index));
  return {ui,container,hooks,calls,toggles,nodes,find,text:()=>container.textContent,
    click(action,index){const target=find(action,index);assert.ok(target,'Missing '+action);return container.handlers.click({target});},
    tab(id){const target=container.querySelector('[data-reading-nav-tab="'+id+'"]');return container.handlers.click({target});},
    setPaper(id=A,revision='one'){ui.setPaper({id,material_revision:revision});ui.setOpen(true);},
    pdf(outline=[],extra={}){return {numPages:8,getOutline:async()=>outline,getDestination:async()=>[1,{name:'Fit'}],getPageIndex:async()=>2,...extra};},
    document(pdf,documentId='main',paperId=A){return ui.setDocument({paperId,documentId,documentName:documentId==='main'?'正文':'Supplement.pdf',pdf});}
  };
}

test('navigation starts collapsed, hides without a paper and keeps literal text titles',async()=>{
 const h=harness();assert.equal(h.container.hidden,true);h.ui.setPaper({id:A});assert.equal(h.ui.isOpen(),false);assert.equal(h.container.hidden,false);await h.click('toggle');
 await h.document(h.pdf([{title:'<img src=x onerror=bad()>',dest:[0,{name:'Fit'}]}]));assert.match(h.text(),/<img src=x onerror=bad\(\)>/);assert.equal(h.nodes.some(n=>['IMG','SCRIPT'].includes(n.tagName)),false);await h.click('toggle');assert.equal(h.ui.isOpen(),false);assert.equal(h.find('toggle').getAttribute('aria-expanded'),'false');h.ui.setPaper(null);assert.equal(h.container.hidden,true);
});

test('native numeric, named and indirect destinations resolve actual PDF pages with hierarchy',async()=>{
 const h=harness();h.setPaper();const names=[],refs=[];
 await h.document(h.pdf([{title:'Introduction',dest:[0,{name:'XYZ'},null,200,null],items:[{title:'Methods',dest:'named'},{title:'Results',dest:[{num:4,gen:0},{name:'FitH'},300]}]}],{getDestination:async name=>{names.push(name);return [4,{name:'Fit'}];},getPageIndex:async ref=>{refs.push(ref);return 6;}}));
 assert.deepEqual(names,['named']);assert.equal(refs[0].num,4);assert.match(h.text(),/Introduction第 1 页Methods第 5 页Results第 7 页/);
 assert.equal(h.find('page',1).style['--reading-nav-depth'],1);await h.click('page',2);assert.equal(h.calls[0].target.page,7);assert.equal(h.calls[0].target.documentId,'main');assert.equal(h.calls[0].target.destination[0],6);assert.equal(h.calls[0].guard.isCurrent(),true);
});

test('missing outlines show an honest empty state without guessing headings',async()=>{
 const h=harness();h.setPaper();await h.document(h.pdf(null));assert.match(h.text(),/没有内置章节书签/);assert.equal(h.find('page'),undefined);assert.equal(h.calls.length,0);
});

test('malformed and external destinations remain disabled and never call navigation hooks',async()=>{
 const h=harness();h.setPaper();
 const targets=[null,'missing',[-1,{name:'Fit'}],[8,{name:'Fit'}],[0,{name:'Launch'}],[0,{name:'XYZ'},0],[0,{name:'FitH'},'bad'],[0,{name:'FitR'},null,0,1,2],[{}, {name:'Fit'}]];
 const items=targets.map((dest,i)=>({title:'Invalid '+i,dest}));items.push({title:'External',url:'https://example.org',dest:[1,{name:'Fit'}]});
 await h.document(h.pdf(items,{getDestination:async()=>null}));
 for(let i=0;i<items.length;i++){assert.equal(h.find('page',i).disabled,true);await h.click('page',i);}assert.equal(h.calls.length,0);assert.match(h.text(),/无可用页码/);
});

test('a bad bookmark resolution does not discard usable siblings',async()=>{
 const h=harness();h.setPaper();await h.document(h.pdf([{title:'Broken',dest:'bad'},{title:'Valid',dest:[2,{name:'Fit'}]}],{getDestination:async()=>{throw Error('bad ref');}}));
 assert.equal(h.find('page',0).disabled,true);assert.equal(h.find('page',1).disabled,false);await h.click('page',1);assert.equal(h.calls[0].target.page,3);
});

test('chapter retrieval failures allow retry and repeated same-document updates reuse one load',async()=>{
 const h=harness();h.setPaper();let reads=0;const pdf=h.pdf([],{getOutline:async()=>{reads++;if(reads===1)throw Error('offline');return [{title:'Recovered',dest:[0,{name:'Fit'}]}];}});
 await h.document(pdf);assert.match(h.text(),/无法读取 PDF 自带章节/);await h.click('retry');assert.match(h.text(),/Recovered/);assert.equal(reads,2);await h.document(pdf);assert.equal(reads,2);
});

test('late outline loads and destination resolution cannot replace another paper or PDF',async()=>{
 const h=harness();h.setPaper();const old=deferred();const loading=h.document(h.pdf([],{getOutline:()=>old.promise}));h.setPaper(B);await h.document(h.pdf([{title:'Current B',dest:[1,{name:'Fit'}]}]),'main',B);old.resolve([{title:'Old A',dest:[0,{name:'Fit'}]}]);await loading;assert.match(h.text(),/Current B/);assert.doesNotMatch(h.text(),/Old A/);
 h.setPaper();const ref=deferred();const resolving=h.document(h.pdf([{title:'Old main',dest:'later'}],{getDestination:()=>ref.promise}));await new Promise(setImmediate);await h.document(h.pdf([{title:'Current supplement',dest:[2,{name:'Fit'}]}]),S);ref.resolve([0,{name:'Fit'}]);await resolving;assert.match(h.text(),/Current supplement/);assert.doesNotMatch(h.text(),/Old main/);
});

test('material revision changes invalidate even when the caller mutates its paper object',async()=>{
 const h=harness(),p={id:A,material_revision:'old'};h.ui.setPaper(p);h.ui.setOpen(true);await h.document(h.pdf([{title:'Old revision',dest:[1,{name:'Fit'}]}]));p.material_revision='new';h.ui.setPaper(p);assert.doesNotMatch(h.text(),/Old revision/);assert.match(h.text(),/打开 PDF/);
});

test('figures are filtered by current PDF and retain their actual source file/page',async()=>{
 const h=harness();h.setPaper();await h.document(h.pdf());h.ui.setFigures({paperId:A,figures:[{id:'f1',label:'Main 1',page:2},{id:'s2',label:'Supplement 2',page:3,document_id:S,caption:'<script>literal caption</script>'},{id:'bad',label:'Invalid',page:1},{id:'f3',label:'Wrong page',page:0}]});await h.tab('figures');assert.match(h.text(),/Main 1/);assert.doesNotMatch(h.text(),/Supplement 2|Invalid|Wrong page/);await h.click('figure',0);assert.equal(h.calls[0].target.document_id,'main');assert.equal(h.calls[0].target.page,2);
 await h.document(h.pdf(),S);assert.match(h.text(),/Supplement 2/);assert.doesNotMatch(h.text(),/Main 1/);await h.click('figure',0);assert.equal(h.calls[1].target.document_id,S);assert.equal(h.calls[1].target.id,'s2');assert.match(h.text(),/<script>literal caption/);
});

test('empty figure lists explain scope and stale paper data is ignored',async()=>{
 const h=harness();h.setPaper(B);h.ui.setFigures({paperId:A,figures:[{id:'f1',label:'Old figure',page:1}]});h.ui.setNoteHeadings({paperId:A,headings:[{id:'old',title:'Old heading',level:1}]});await h.tab('figures');assert.match(h.text(),/当前文件还没有已提取图表/);assert.doesNotMatch(h.text(),/Old figure/);await h.tab('notes');assert.match(h.text(),/笔记还没有标题/);assert.doesNotMatch(h.text(),/Old heading/);
});

test('duplicate heading titles keep distinct stable targets and invalid duplicate IDs are omitted',async()=>{
 const h=harness();h.setPaper();h.ui.setNoteHeadings({paperId:A,headings:[{id:'note-0',title:'Methods',level:1},{id:'note-1',title:'Methods',level:3},{id:'note-1',title:'Duplicate ID',level:2},{id:'note-2',title:'<b>literal</b>',level:90},{id:'',title:'Missing ID'}]});await h.tab('notes');assert.equal((h.text().match(/Methods/g)||[]).length,2);assert.doesNotMatch(h.text(),/Duplicate ID|Missing ID/);await h.click('heading',1);assert.equal(h.calls[0].target.id,'note-1');assert.equal(h.calls[0].target.level,3);
 h.ui.setNoteHeadings({paperId:A,headings:[{id:'note-0',title:'Methods',level:1},{id:'note-1',title:'Methods',level:3}]});await h.click('heading',1);assert.equal(h.calls[1].target.id,'note-1');
});

test('navigation hooks receive cancellation guards, suppress duplicate clicks and keep failures retryable',async()=>{
 const h=harness();h.setPaper();await h.document(h.pdf([{title:'One',dest:[0,{name:'Fit'}]}]));const pending=deferred();let guard,calls=0;h.hooks.onPage=async(_,g)=>{calls++;guard=g;return pending.promise;};const opening=h.click('page',0);await h.click('page',0);assert.equal(calls,1);assert.equal(guard.isCurrent(),true);h.ui.setOpen(false);assert.equal(guard.isCurrent(),false);pending.reject(Error('Old error'));await opening;h.ui.setOpen(true);assert.doesNotMatch(h.text(),/Old error/);
 h.hooks.onPage=async()=>false;await h.click('page',0);assert.match(h.text(),/暂时无法跳转/);h.hooks.onPage=async()=>{throw Error('<img literal error>');};await h.click('page',0);assert.match(h.text(),/无法跳转：<img literal error>/);h.hooks.onPage=async()=>true;await h.click('page',0);assert.doesNotMatch(h.text(),/无法跳转/);
});

test('keyboard tabs work without opening external destinations or losing the selected tab',async()=>{
 const h=harness();h.setPaper();const current=h.container.querySelector('[data-reading-nav-tab="outline"]'),tabbar=current.parent;let prevented=false;tabbar.handlers.keydown({target:current,key:'ArrowRight',preventDefault(){prevented=true;}});assert.equal(prevented,true);assert.equal(h.container.querySelector('[data-reading-nav-tab="figures"]').getAttribute('aria-selected'),'true');assert.equal(h.container.querySelector('[data-reading-nav-tab="figures"]').focused,true);assert.match(h.text(),/当前文件还没有已提取图表/);
});

test('cyclic and excessive native outlines are bounded without invented destinations',async()=>{
 const h=harness();h.setPaper();const cyclic={title:'Cycle',dest:[0,{name:'Fit'}]};cyclic.items=[cyclic];await h.document(h.pdf([cyclic]));assert.equal((h.text().match(/Cycle/g)||[]).length,1);
 await h.document(h.pdf(Array.from({length:510},(_,i)=>({title:'Chapter '+i,dest:[0,{name:'Fit'}]}))));assert.equal(h.container.querySelectorAll('[data-reading-nav-action="page"]').length,500);assert.match(h.text(),/仅显示前 500 项/);
});
