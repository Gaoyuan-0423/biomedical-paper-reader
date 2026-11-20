const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function element(tag) {
  const el = {tag, dataset:{}, children:[], textContent:'', events:{}, value:'',
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; },
    setAttribute(attr,value) { this[attr] = value; },
    addEventListener(event,fn) { this.events[event] = fn; },
    focus() { this.focused = true; },
    click() { return this.events.click?.() ?? this.onclick?.(); },
    descendants() { return this.children.filter(child => typeof child === 'object').flatMap(child => [child,...child.descendants()]); },
    querySelector(selector) {
      const key = selector.match(/data-deck-field="(\w+)"/)?.[1];
      return this.descendants().find(child => child.dataset.deckField===key);
    },
    querySelectorAll() { return []; }
  };
  return el;
}

test('reader confirms missing fields, edits each slide and exports the reordered selection',async()=>{
  const window = {confirm:()=>true}, container = element('div'), exports = [];
  const script = fs.readFileSync(path.join(__dirname,'../frontend/figure-deck.js'),'utf8');
  vm.runInNewContext(script,{window,document:{createElement:element}});
  const make = id=>({paper_id:id,id:'f001',shelf_key:id,label:'Figure 1'});
  const deck = window.BioFigureDeck.open(container,'study',{
    previewDeck: async(scope,ref) => ({revision:ref.paper_id,label:'Figure 1',paper_title:ref.paper_id,
      caption:'Original caption.',observation:'中文观察',observation_language:'zh-CN',document_name:'Main.pdf',page:2}),
    image:()=>'/image',exportDeck:async(scope,body)=>{exports.push([scope,JSON.parse(JSON.stringify(body))]);}
  });
  container.append(deck.panel);
  const nodes=()=>deck.panel.descendants();
  const click=key=>nodes().find(el=>el.dataset.i18n===key && el.tag==='button').click();
  const entry=k=>nodes().find(el=>el.dataset.deckField===k);
  for(const id of ['A','B']) {
    await deck.pick(make(id)).click();
    assert.equal(entry('notes').value,'中文观察');
    assert.equal(entry('observation').value,'');
    await click('导出可编辑 PPTX');
    assert.equal(exports.length,0);
    for(const k of ['title','question','control','observation','limitation']) {
      entry(k).value=k+' '+id;entry(k).oninput();
    }
  }
  const rows=nodes().filter(el=>el.tag==='li');
  rows[1].children.find(el=>el.dataset.i18n==='上移').click();
  await click('预览幻灯片');
  assert.ok(nodes().some(el=>el.tag==='p' && el.textContent==='observation B'));
  await click('导出可编辑 PPTX');
  assert.equal(exports[0][0],'study');assert.deepEqual(exports[0][1].slides.map(s=>s.title),['title B','title A']);
  assert.ok(exports[0][1].slides.every(s=>s.notes==='中文观察'));
});

test('model drafts preserve fields typed while waiting and failure keeps manual and earlier text',async()=>{
  const window={confirm:()=>true}, container=element('section');
  const script=fs.readFileSync(path.join(__dirname,'../frontend/figure-deck.js'),'utf8');
  vm.runInNewContext(script,{window,document:{createElement:element}});
  let resolve,reject,request;
  const deck=window.BioFigureDeck.open(container,'study',{
    previewDeck:async()=>({revision:'r1',label:'Figure 1',paper_title:'P',document_name:'P.pdf',page:1,caption:'paired samples',observation:''}),
    modelMode:()=> 'text', image:()=>'/image',
    draftSlide:(scope,body)=>{request=body;return new Promise((yes,no)=>{resolve=yes;reject=no;});}
  });
  container.append(deck.panel);
  await deck.pick({paper_id:'A',id:'f001',shelf_key:'S',label:'Figure 1'}).click();
  const nodes=()=>deck.panel.descendants(), entry=k=>nodes().find(el=>el.dataset.deckField===k);
  const edit=(k,v)=>{const el=entry(k);el.value=v;el.oninput();};
  const start=()=>nodes().find(el=>el.dataset.i18n==='从读图笔记起草英文页和中文讲稿').click();
  edit('control','Manual control');
  const pending=start();
  assert.ok(!request.fields.includes('control'));
  edit('question','Reader question during generation');
  resolve({model:'test-model',fields:{
    observation:{value:'Matched samples',source:'caption',quote:'paired samples'},
    question:{value:'Model question',source:'caption',quote:'paired samples'},
    control:{value:'Model control',source:'caption',quote:'paired samples'}
  }});
  await pending;
  assert.equal(entry('observation').value,'Matched samples'); assert.equal(entry('control').value,'Manual control');
  assert.equal(entry('question').value,'Reader question during generation');
  assert.ok(nodes().some(el=>el.tag==='blockquote' && el.textContent==='paired samples'));
  const failed=start(); reject(Error('Model error'));await failed;
  assert.equal(entry('observation').value,'Matched samples');assert.equal(entry('control').value,'Manual control');
  assert.ok(nodes().some(el=>el.textContent==='Model error'));
});
