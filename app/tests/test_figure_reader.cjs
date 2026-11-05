const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const A = 'a'.repeat(32), B = 'b'.repeat(32);
const code = name => fs.readFileSync(path.join(__dirname, '../frontend/' + name), 'utf8');
function harness() {
  const handlers = {}, callbacks = [], catalog = {};
  let locale = 'zh-CN', context;
  const match = (el, selector) => selector.split(',').some(part => {
    part = part.trim();
    if (part[0] === '#') return el.id === part.slice(1);
    if (part[0] === '.') return el.className.split(/\s+/).includes(part.slice(1));
    const m = /^\[data-([a-z-]+)(?:="([^"]*)")?\]$/.exec(part);
    if (m) { const key = m[1].replace(/-([a-z])/g, (_, c) => c.toUpperCase()); return Object.hasOwn(el.dataset, key) && (m[2] === undefined || m[2] === el.dataset[key]); }
    if (part === '[tabindex="0"]') return el.tabIndex === 0;
    if (part === 'a[href]') return el.tagName === 'A' && !!el.getAttribute('href');
    return el.tagName === part.toUpperCase();
  });
  function element(tag) {
    const classes = new Set();
    const el = {tagName:tag.toUpperCase(), parent:null, children:[], dataset:{}, attributes:{}, handlers:{}, style:{}, className:'', hidden:false, disabled:false, inert:false, scrollTop:0, scrollLeft:0, clientWidth:600, clientHeight:500, _text:'', _html:'', writes:0,
      classList:{add(...names){names.forEach(x=>classes.add(x));},remove(...names){names.forEach(x=>classes.delete(x));},contains(name){return classes.has(name);}},
      append(...children){children.forEach(child=>{this.children.push(child);child.parent=this;});},
      set textContent(value){this._text=String(value);this._html='';this.children=[];},get textContent(){return this._text+this.children.map(c=>c.textContent).join('');},
      set innerHTML(value){this._html=String(value);this.children=[];this.writes++;},get innerHTML(){return this._html;},
      setAttribute(key,value){this.attributes[key]=String(value);},getAttribute(key){return this.attributes[key]??null;},
      addEventListener(key,fn){this.handlers[key]=fn;},removeEventListener(key){delete this.handlers[key];},
      get isConnected(){return this===document.body || !!this.parent?.isConnected;},
      remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);this.parent=null;},
      contains(target){return this===target || this.children.some(c=>c.contains(target));},
      closest(selector){return match(this,selector)?this:this.parent?.closest(selector)||null;},
      querySelectorAll(selector){return this.children.flatMap(c=>[...(match(c,selector)?[c]:[]),...c.querySelectorAll(selector)]);},
      querySelector(selector){return this.querySelectorAll(selector)[0]||null;},
      getClientRects(){for(let n=this;n;n=n.parent)if(n.hidden)return [];return [{}];},
      focus(){document.activeElement=this;handlers.focusin?.({target:this});},
    }; return el;
  }
  const document = {createElement:element,activeElement:null,addEventListener(key,fn){handlers[key]=fn;},removeEventListener(key){delete handlers[key];}};
  document.body=element('body');
  const background=element('main'),trigger=element('button'),alreadyInert=element('aside');alreadyInert.inert=true;background.append(trigger);document.body.append(background,alreadyInert);trigger.focus();
  const i18n={register(values){Object.assign(catalog,values);},t(key,params={}){return String(locale==='en'?catalog[key]||key:key).replace(/\{(\w+)\}/g,(_,name)=>String(params[name]??''));},onChange(fn){callbacks.push(fn);return()=>callbacks.splice(callbacks.indexOf(fn),1);}};
  const resize=[];
  context=vm.createContext({window:{marked:require('../frontend/vendor/marked.min.js'),BioI18n:i18n,ResizeObserver:class{constructor(fn){this.fn=fn;resize.push(this);}observe(){}disconnect(){this.disconnected=true;}}},document,console});
  for(const file of ['i18n-app.js','safe-markdown.js','figure-reader.js'])vm.runInContext(code(file),context);
  const ui=context.window.BioFigureReader, restored=[];ui.init({returnFocus(value){restored.push(value);}});
  const $=selector=>document.body.querySelector(selector);
  return {ui,$,document,trigger,background,alreadyInert,restored,resize,
    open(figure={},paperId=A,materialRevision=1){return ui.open({paperId,materialRevision,figure:{id:'s001',label:'Supplementary Figure 1',document_name:'supplement.pdf',page:4,width:1200,height:900,...figure}},trigger);},
    click(action){const root=$('#figure-reader'),target=$('[data-figure-reader-action="'+action+'"]');root.handlers.click({target});},
    key(key,shiftKey=false){let prevented=false,stopped=false;$('#figure-reader').handlers.keydown({key,shiftKey,preventDefault(){prevented=true;},stopPropagation(){stopped=true;}});return {prevented,stopped};},
    locale(value){locale=value;callbacks.slice().forEach(fn=>fn());}
  };
}

test('saved full observation renders safely with a trusted image and literal metadata',()=>{
 const h=harness(),observation='## Finding\n\n**Strong** result\n\n- Item one\n\n| A | B |\n|---|---|\n|1|2|\n\n'+('Paragraph. '.repeat(100))+'FINAL TAIL\n\n<script>bad()</script>\n![remote](https://evil.test/a)\n![forged](figure:f002)\n[forged](note-source:'+B+':N1)';
 h.open({label:'<img src=x onerror=bad()>',caption:'<b>literal caption</b>',observation});
 assert.equal(h.$('#figure-reader-image').src,'/api/papers/'+A+'/figures/s001.png');
 const html=h.$('#figure-reader-markdown').innerHTML;assert.match(html,/<h2>Finding<\/h2>/);assert.match(html,/<strong>Strong<\/strong>/);assert.match(html,/<ul>/);assert.match(html,/<table>/);assert.match(html,/FINAL TAIL/);assert.doesNotMatch(html,/<script|<img|data-bio-note-source|href="note-source/);
 assert.equal(h.$('#figure-reader-title').textContent,'<img src=x onerror=bad()>');assert.equal(h.$('.figure-reader-caption-text').textContent,'<b>literal caption</b>');assert.equal(h.$('#figure-reader').dataset.notesState,'ready');assert.match(h.$('#figure-reader-source').textContent,/supplement.pdf.*4/);
});

test('empty observations have a clear empty state without starting a read request',()=>{
 const h=harness();h.open({observation:'  '});assert.equal(h.$('#figure-reader').dataset.notesState,'empty');assert.equal(h.$('#figure-reader-markdown').hidden,true);assert.match(h.$('.figure-reader-empty').textContent,/还没有已保存/);assert.equal(h.$('.figure-reader-caption-text').textContent,'这张图暂无图注。');
});

test('repeat open, close, Escape and focus trap preserve the existing background state',()=>{
 const h=harness();h.open();assert.equal(h.background.inert,true);assert.equal(h.alreadyInert.inert,true);h.open({id:'f003'});assert.equal(h.document.body.querySelectorAll('#figure-reader').length,1);
 const root=h.$('#figure-reader'),close=h.$('[data-figure-reader-action="close"]');root.handlers.click({target:h.$('#figure-reader-title')});assert.equal(h.ui.isOpen(),true);
 h.trigger.focus();assert.equal(h.document.activeElement,close);const tab=h.key('Tab',true);assert.equal(tab.prevented,true);assert.equal(h.document.activeElement.tagName,'SUMMARY');assert.equal(h.key('Tab').prevented,true);assert.equal(h.document.activeElement,close);
 assert.deepEqual(h.key('Escape'),{prevented:true,stopped:true});assert.equal(h.ui.isOpen(),false);assert.equal(h.document.activeElement,h.trigger);assert.equal(h.background.inert,false);assert.equal(h.alreadyInert.inert,true);
 h.open();h.trigger.remove();h.$('#figure-reader').handlers.click({target:h.$('#figure-reader')});assert.equal(h.restored[0].figureId,'s001');assert.equal(h.resize.at(-1).disconnected,true);
});

test('zoom, fit and image retry operate locally and ignore late image events',()=>{
 const h=harness();h.open();const oldImage=h.$('#figure-reader-image');oldImage.naturalWidth=1200;oldImage.naturalHeight=900;oldImage.onload();assert.equal(h.$('#figure-reader').dataset.imageState,'ready');const fit=oldImage.style.width;
 h.click('zoom-in');assert.ok(parseInt(oldImage.style.width)>parseInt(fit));h.$('#figure-reader-viewport').scrollLeft=100;h.click('fit');assert.equal(oldImage.style.width,fit);assert.equal(h.$('#figure-reader-viewport').scrollLeft,0);
 oldImage.onerror();assert.equal(h.$('#figure-reader').dataset.imageState,'error');h.click('retry-image');assert.match(oldImage.src,/reader-retry=1$/);assert.equal(h.$('#figure-reader').dataset.imageState,'loading');
 const late=oldImage.onload;h.open({id:'f002'});late();assert.equal(h.$('#figure-reader').dataset.imageState,'loading');assert.match(h.$('#figure-reader-image').src,/f002.png$/);
});

test('sync updates full saved notes without resetting image zoom, note scroll or caption expansion',()=>{
 const h=harness();h.open({observation:'Old'});h.click('zoom-in');const width=h.$('#figure-reader-image').style.width;h.$('#figure-reader-notes').scrollTop=170;h.$('.figure-reader-caption').open=true;
 const figure={id:'s001',label:'Supplementary Figure 1',page:4,width:1200,height:900,caption:'Updated manual caption',observation:'## New\n\nSaved final paragraph'};
 h.ui.sync({paperId:A,materialRevision:1,figures:[figure]});assert.match(h.$('#figure-reader-markdown').innerHTML,/Saved final paragraph/);assert.equal(h.$('#figure-reader-notes').scrollTop,170);assert.equal(h.$('#figure-reader-image').style.width,width);assert.equal(h.$('.figure-reader-caption').open,true);assert.equal(h.$('.figure-reader-caption-text').textContent,figure.caption);
 const writes=h.$('#figure-reader-markdown').writes;h.ui.sync({paperId:A,materialRevision:1,figures:[figure]});assert.equal(h.$('#figure-reader-markdown').writes,writes);
});

test('paper, material and figure removal invalidate the viewer without showing another source',()=>{
 const h=harness();h.open();h.ui.sync({paperId:B,materialRevision:1,figures:[{id:'s001',observation:'Different paper'}]});assert.equal(h.ui.isOpen(),false);assert.notEqual(h.document.activeElement,h.trigger);
 h.open();h.ui.sync({paperId:A,materialRevision:2,figures:[{id:'s001'}]});assert.equal(h.ui.isOpen(),false);
 h.open();h.trigger.remove();h.ui.sync({paperId:A,materialRevision:1,figures:[]});assert.equal(h.ui.isOpen(),false);assert.equal(h.restored.length,1);
 assert.equal(h.open({id:'../../secret'}),false);assert.equal(h.open({},'../bad'),false);assert.equal(h.ui.isOpen(),false);
});

test('language switch changes controls while retaining original observations and caption text',()=>{
 const h=harness();h.open({label:'中文图名',caption:'原文图注',observation:'## 中文模型结果\n\nOriginal answer'});const content=h.$('#figure-reader-markdown'),html=content.innerHTML,writes=content.writes;h.$('#figure-reader-notes').scrollTop=130;
 h.locale('en');assert.equal(h.$('[data-figure-reader-action="fit"]').textContent,'Fit to window');assert.equal(h.$('#figure-reader-notes-title').textContent,'Figure notes');assert.equal(h.$('#figure-reader-title').textContent,'中文图名');assert.equal(h.$('.figure-reader-caption-text').textContent,'原文图注');assert.equal(content.innerHTML,html);assert.equal(content.writes,writes);assert.equal(h.$('#figure-reader-notes').scrollTop,130);
});
