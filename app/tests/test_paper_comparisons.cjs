// Independent regression for the actual view handlers and scoped draft / source navigation.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {webcrypto} = require('node:crypto');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname,'../frontend/comparisons.js'),'utf8');
const A = 'a'.repeat(32), B = 'b'.repeat(32), L = 'c'.repeat(32);
const docs = [{id:'main',name:'Main.pdf',revision:'r1',metadata_revision:'r1',pages:3}];
const src = {key:'figure:f001:hash',kind:'figure',document_id:'main',document_name:'Main.pdf',
  page:2,label:'Fig 1',text:'Figure caption',figure_id:'f001',status:'',available:true};
const papers = [{id:A,title:'First <paper>'},{id:B,title:'Other paper'}];

function elements() {
  const nodes = new Map();
  function child(key) {
    if (!nodes.has(key)) nodes.set(key,{value:'',textContent:'',innerHTML:'',classList:{toggle() {}},querySelector:child});
    return nodes.get(key);
  }
  const root = {dataset:{},innerHTML:'',querySelector:child};
  return {root,child};
}

function harness() {
  const {root,child} = elements();
  const calls = [], jumps = [], saved = new Map();
  let invalidSource = false;
  const window = {crypto:webcrypto,confirm:()=>true,fetch:async(url,opts)=>{
    calls.push([url,opts]); let result, status = 200;
    const parts = url.split('/');
    if (parts[4] === 'material') {
      if (parts[6]) {
        status = invalidSource ? 409 : 200;
        result = invalidSource ? {detail:'Source changed'} : {...src,metadata_revision:'r1'};
      } else result = {documents:docs,sources:[src],accessions:['GSE111'],warnings:[],samples:[]};
    } else if (opts.method === 'PUT') {
      const body = JSON.parse(opts.body);
      result = {...body,id:parts[4],revision:body.revision+1,rows:body.rows.map(r=>({...r,title:'Saved title',
        sources:r.references.map(()=>src)}))};
      saved.set(url,result);
    } else if (parts[4]) result = saved.get(url);
    else result = [...saved.entries()].filter(([key])=>key.startsWith(url+'/')).map(([,v])=>v);
    return {ok:status===200,status,json:async()=>result};
  }};
  vm.runInNewContext(code,{window});
  const ui = window.BioCompare;
  const open = id => ui.open(root,{libraryId:id,papers,openSource:(...args)=>jumps.push(args)});
  function el(attr,{value='',id=A,checked=false,index='0'}={}) {
    const tr = {dataset:{row:id},querySelector:selector=>selector==='[data-candidate]'?{value:src.key}:child(selector)};
    return {value,checked,dataset:{paper:id,drop:index,jump:index},hasAttribute:name=>name===attr,
      closest(selector){if(selector==='button') return this;if(selector==='details') return {open:true};return tr;}};
  }
  const input = (attr,params) => root.oninput({target:el(attr,params)});
  const change = (attr,params) => root.onchange({target:el(attr,params)});
  const click = (attr,params) => root.onclick({target:el(attr,params)});
  return {ui,root,child,calls,jumps,saved,open,input,change,click,markStale:()=>{invalidSource=true;}};
}

test('comparison keeps unsaved scoped drafts and checks the exact source before any jump',async()=>{
  const h = harness();
  await h.open('all');
  h.input('data-question',{value:'<script>Reader question</script>'});
  for(const id of [A,B]) await h.change('data-paper',{id,checked:true});
  assert.ok(h.root.innerHTML.includes('&lt;script&gt;Reader question&lt;/script&gt;'));
  h.input('data-population',{value:'Patients'});h.input('data-contrast',{value:'Matched control'});
  h.input('data-row-note',{value:'Donor-level comparison'});h.input('data-relation',{value:'qualifies'});
  await h.click('data-attach',{id:A});
  await h.click('data-jump',{id:A});
  assert.deepEqual(h.jumps,[[A,'main',2,'f001','r1']]);
  assert.ok(h.ui.hasUnsaved('all'));assert.ok(h.ui.canLeave());
  await h.open(L);
  assert.ok(!h.root.innerHTML.includes('Donor-level comparison'));assert.equal(h.ui.hasUnsaved(L),false);
  await h.open('all');
  assert.ok(h.root.innerHTML.includes('Donor-level comparison'));
  await h.click('data-save');
  const saved = [...h.saved.values()][0];
  assert.equal(saved.rows[0].population,'Patients');
  assert.equal(saved.rows[0].contrast,'Matched control');
  assert.equal(saved.rows[0].relation,'qualifies');
  assert.deepEqual(saved.rows[0].references,[src.key]);assert.equal(h.ui.hasUnsaved(),false);
  await h.click('data-new');
  await h.change('data-comparisons',{value:saved.id});
  assert.ok(h.root.innerHTML.includes('Donor-level comparison'));
  h.markStale();await h.click('data-jump',{id:A});
  assert.equal(h.jumps.length,1);assert.equal(h.child('[data-cmp-status]').textContent,'Source changed');
});
