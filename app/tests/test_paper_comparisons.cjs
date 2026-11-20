// Independent regression for the actual view handlers and scoped draft / source navigation.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {webcrypto} = require('node:crypto');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const code = ['i18n.js','comparison-draft.js','comparisons.js'].map(file => fs.readFileSync(path.join(__dirname,'../frontend',file),'utf8')).join('\n');
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

function harness({preview,additionalMaterials = [],locale='zh-CN'} = {}) {
  const {root,child} = elements();
  const calls = [], jumps = [], saved = new Map();
  let invalidSource = false;
  const window = {crypto:webcrypto,localStorage:{getItem:()=>locale},confirm:()=>true,fetch:async(url,opts)=>{
    calls.push([url,opts]); let result, status = 200;
    const parts = url.split('/');
    if (parts[4] === 'material') {
      if (parts[6]) {
        status = invalidSource ? 409 : 200;
        const key = decodeURIComponent(url.split('?key=')[1] || '');
        const item = [src,...additionalMaterials].find(s => s.key === key);
        result = invalidSource ? {detail:'Source changed'} : {...item,metadata_revision:'r1'};
      } else result = {documents:docs,sources:[src,...additionalMaterials],accessions:['GSE111'],warnings:[],samples:[]};
    } else if (parts[4] === 'draft' && opts.method === 'POST') result = preview;
    else if (opts.method === 'PUT') {
      const body = JSON.parse(opts.body);
      result = {...body,id:parts[4],revision:body.revision+1,rows:body.rows.map(r=>({...r,title:'Saved title',
        cells:r.cells.map(c=>({...c,source:[src,...additionalMaterials].find(m=>m.key===c.source_key)})),
        sources:r.references.map(k=>[src,...additionalMaterials].find(m=>m.key===k))}))};
      saved.set(url,result);
    } else if (parts[4]) result = saved.get(url);
    else result = [...saved.entries()].filter(([key])=>key.startsWith(url+'/')).map(([,v])=>v);
    return {ok:status===200,status,json:async()=>result};
  }};
  vm.runInNewContext(code,{window});
  const ui = window.BioCompare;
  const open = id => ui.open(root,{libraryId:id,papers,openSource:(...args)=>jumps.push(args)});
  function el(attr,{value='',id=A,checked=false,index='0',review='',col='paired',cohort='',sourceKey=src.key}={}) {
    const tr = {dataset:{row:id,draftPaper:id,draftCol:col,draftCohort:cohort},querySelector:selector=>selector==='[data-candidate]'?{value:sourceKey}:child(selector)};
    return {value,checked,dataset:{paper:id,drop:index,jump:index,draftColumn:col,draftValue:index,review,index},hasAttribute:name=>name===attr,
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
  assert.equal(Object.hasOwn(saved,'columns'),false);
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

test('draft actions keep cohorts separate, persist only accepted edits, and revalidate citations',async()=>{
  const pair = {key:'evidence:pair:hash',kind:'evidence',text:'Seven <matched> pairs.',page:1,
    document_name:'Supplement.pdf',document_id:'supp',available:true,bbox:[0.2,0.1,0.8,0.2]};
  const reason = {...src,text:'See Supplementary Table S2'};
  const attached = {...reason,key:'evidence:other-table:hash',text:'Single cell donor counts are in Supplementary Table 4.'};
  const discovered = {status:'candidates',candidates:[{value:'7',source:pair}]};
  const preview = {scope:'saved_sources',papers:[
    {paper_id:A,title:'First',cohorts:[{cohort:'Discovery',cells:{paired:discovered,sites:{status:'not_found',candidates:[]}}}]},
    {paper_id:B,title:'Second',cohorts:[{cohort:'Validation',cells:{paired:{status:'missing_supplement',candidates:[],reason_source:reason,supplement_label:'Supplementary Table S2'},
      single_cell:{status:'check_supplement',candidates:[],reason_source:attached,supplement_label:'Supplementary Table 4'}}}]}
  ]};
  const h = harness({preview,additionalMaterials:[pair,attached]});
  await h.open('all');
  await h.click('data-build-draft');
  assert.ok(!h.calls.some(([url])=>url.endsWith('/draft')));
  h.input('data-question',{value:'Which cohorts used paired sampling?'});
  for(const id of [A,B]) await h.change('data-paper',{id,checked:true});
  h.input('data-population',{value:'Independent manual note'});
  await h.change('data-draft-column',{col:'enrolled',checked:false});
  await h.click('data-build-draft');
  const [,req] = h.calls.find(([url])=>url.endsWith('/draft'));
  assert.equal(req.method,'POST');
  assert.deepEqual(JSON.parse(req.body).columns,['paired','single_cell','sites']);
  assert.match(h.root.innerHTML,/Discovery/);
  assert.match(h.root.innerHTML,/Validation/);
  assert.match(h.root.innerHTML,/Seven &lt;matched&gt; pairs/);
  assert.match(h.root.innerHTML,/See Supplementary Table S2/);
  assert.match(h.root.innerHTML,/缺补表，待核/);
  assert.match(h.root.innerHTML,/已保存的材料中未找到/);
  assert.match(h.root.innerHTML,/已有附件，需核对相应补表<\/span> · Supplementary Table 4/);
  assert.ok(h.root.innerHTML.includes(attached.text));
  const cell = {id:A,col:'paired',cohort:'Discovery'};
  const click = (review,opts={}) => h.click('data-review',{...cell,review,...opts});
  await click('source'); assert.deepEqual(h.jumps,[[A,'supp',1,undefined,'r1',pair.bbox]]);
  await h.click('data-review',{id:B,col:'single_cell',cohort:'Validation',review:'source',index:'reason'});
  assert.ok(h.calls.some(([url])=>url.endsWith(encodeURIComponent(attached.key))));
  await click('skip'); assert.match(h.child('[data-draft-review]').innerHTML,/已跳过/);
  await click('restore');
  h.input('data-draft-value',{...cell,value:'7 patients'});
  await click('accept');
  await h.change('data-draft-column',{col:'paired',checked:false});
  assert.match(h.child('[data-draft-review]').innerHTML,/7 patients/);
  await h.click('data-save');
  const saved = [...h.saved.values()][0];
  assert.deepEqual(saved.columns,['single_cell','sites']);
  assert.deepEqual(saved.rows[0].cells,[{column:'paired',cohort:'Discovery',value:'7 patients',source_key:pair.key,source:pair}]);
  assert.deepEqual(saved.rows[1].cells,[]);
  assert.equal(saved.rows[0].population,'Independent manual note');
  await h.click('data-new'); await h.change('data-comparisons',{value:saved.id});
  assert.match(h.root.innerHTML,/7 patients/);
  h.input('data-accepted-value',{...cell,value:'7 paired participants'});
  await h.click('data-save');
  assert.equal([...h.saved.values()][0].rows[0].cells[0].value,'7 paired participants');
  h.markStale();
  const previous = h.jumps.length;
  await click('source',{index:'accepted'});
  assert.equal(h.jumps.length,previous);
  assert.equal(h.child('[data-cmp-status]').textContent,'Source changed');
});

test('an evidence card can join only the active comparison for its library and paper',async()=>{
  const id = 'e'.repeat(32), card = {id,claim:'Participant counts',quote:'12 patients',page:1,document_id:'main',status:'pending'};
  const material = {...src,key:'evidence:'+id+':revision',kind:'evidence',label:card.claim,text:card.quote,status:'pending',page:1};
  const h = harness({additionalMaterials:[material]}); await h.open('all');
  assert.equal(h.ui.evidenceTarget('all',A),null);
  h.input('data-question',{value:'Paired data'});
  const target = h.ui.evidenceTarget('all',A);
  assert.equal(h.ui.evidenceTarget(L,A),null);
  await assert.rejects(h.ui.attachEvidence('all',A,{...card,quote:'changed'},target,()=>true));
  await assert.rejects(h.ui.attachEvidence('all',A,card,target,()=>false));
  await h.ui.attachEvidence('all',A,card,target,()=>true);
  await h.ui.attachEvidence('all',A,card,target,()=>true);
  await h.change('data-paper',{id:B,checked:true});
  await h.click('data-save');
  assert.deepEqual([...h.saved.values()][0].rows[0].references,[material.key]);
});

test('the field-source picker and accepted snapshot render in Chinese and English',async()=>{
  const excerpt = {...src,key:'field:verified-source',kind:'field',text:'Saved quote',label:'Tissue',status:'located'};
  for (const [locale,kind,status,button] of [
    ['en','Field excerpt','Located on cited page','Draft a table for selected papers'],
    ['zh-CN','字段出处','已定位','为勾选的论文起草比较表']
  ]) {
    const h = harness({locale,additionalMaterials:[excerpt]});
    await h.open('all');
    await h.change('data-paper',{id:A,checked:true});
    assert.ok(h.root.innerHTML.includes(button));
    assert.ok(h.child('[data-material]').innerHTML.includes(kind + ' · Tissue'));
    await h.click('data-attach',{id:A,sourceKey:excerpt.key});
    const html = h.child('[data-material]').innerHTML;
    assert.ok(html.includes(status)); assert.ok(html.includes('Saved quote'));
    assert.ok(!html.includes('undefined'));
  }
});

test('explicit PDF fallback displays the examined range, scanned pages and distinct source',async()=>{
  const item = {...src,kind:'pdf',key:'pdf:main:p2-b0:0:digest',source_revision:'r1',text:'We enrolled 12 patients.'};
  const preview = {scope:'saved_and_pdf', papers:[{paper_id:A,title:'Paper',warnings:[],
    pdf_coverage:[{document_name:'Main.pdf',examined:2,total:4,no_text:[1],error:''}],
    cohorts:[{cohort:'',cells:{enrolled:{status:'candidates',candidates:[{value:'12 patients',source:item}]},
      paired:{status:'not_found',candidates:[]}}}]}]};
  const h=harness({preview,additionalMaterials:[item]});
  await h.open('all');
  h.input('data-question',{value:'How many were enrolled?'});
  for(const id of [A,B]) await h.change('data-paper',{id,checked:true});
  await h.click('data-pdf-draft');
  const req=h.calls.find(([url])=>url.endsWith('/draft'))[1];
  assert.equal(JSON.parse(req.body).include_pdf,true);
  assert.match(h.root.innerHTML,/已查页数／总页数/);
  assert.match(h.root.innerHTML,/需 OCR 后再核对/);
  assert.match(h.root.innerHTML,/已查的 PDF 页面及已保存材料中未找到/);
  assert.match(h.root.innerHTML,/PDF 补查原句/);
  await h.click('data-review',{id:A,col:'enrolled',review:'source'});
  assert.equal(h.jumps.length,1);
});
