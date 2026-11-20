const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../frontend/identifier-import.js'), 'utf8');
const next = () => new Promise(resolve => setImmediate(resolve));
function element(tag) {
  return { tag, value: '', textContent: '', dataset: {}, children: [],
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    focus() {},
  };
}
function content(node) {
  if (typeof node === 'string') return node;
  return node.textContent + node.children.map(content).join('');
}
function harness(rows, library = 'all') {
  const nodes = new Map(), writes = [], attachments = [];
  const node = key => {
    if (!nodes.has(key)) nodes.set(key, element('div'));
    return nodes.get(key);
  };
  const window = {};
  const document = { getElementById: node, createElement: element, createTextNode: s => s };
  vm.runInNewContext(script, {window, document, URL});
  const ui = window.BioIdentifierImport;
  const hooks = { currentScope: () => library, scopeName: s => s, refresh: async () => {}, hide() {}, show() {},
    request: async (url, body) => {
      if (url.endsWith('/preview')) return {preview_id: 'pinned-preview', rows};
      return new Promise((resolve, reject) => writes.push({body: JSON.parse(JSON.stringify(body)), resolve, reject}));
    },
    onAttached: async id => attachments.push(id),
  };
  ui.init(hooks);
  ui.open('fixture');
  const row = index => node('identifier-rows').children[index];
  const retry = index => row(index).children.at(-1).children.find(el => el.textContent === '重试获取正文');
  return {ui, hooks, node, row, retry, writes, attachments, preview: () => node('doi-preview').onclick(),
    apply: () => node('doi-submit').onclick()};
}
const previewRow = (key, changes = {}) => ({key, status: 'new', title: 'Paper ' + key, input: 'input ' + key,
  is_link: false, has_file: false, ...changes});
const saved = (key, outcome, status = 'added') => ({key, paper_id: 'paper-' + key, status,
  ...(outcome ? {fulltext:{outcome, message:'Result for paper ' + key}} : {})});

test('a pasted link defaults to selected for a missing PDF, including an existing paper in the pinned scope', async () => {
  for (const scope of ['all', 'in-project']) {
    const h = harness([
      previewRow(0, {status:'existing', is_link:true, in_project:true}),
      previewRow(1, {status:'existing', is_link:true, in_project:true, has_file:true}),
      previewRow(2, {status:'existing', in_project:true}),
      previewRow(3, {status:'trashed', is_link:true}),
    ],scope);
    await h.preview();
    assert.deepEqual([0,1,2,3].map(i => h.row(i).children[0].checked), [true,false,false,false]);
    assert.equal(h.row(3).children[0].disabled,true);
    const run = h.apply();
    assert.deepEqual(h.writes[0].body,{preview_id:'pinned-preview',keys:[0]});
    h.writes[0].resolve({rows:[saved(0,'already_present','existing')]});
    await run;
    assert.match(content(h.row(0)),/已有正文/);
    assert.deepEqual(h.attachments,['paper-0']);
    assert.equal(h.retry(0),undefined);
  }
});

test('mixed input applies IDs together and URLs sequentially; retry touches only the saved link', async () => {
  const h = harness([previewRow(0),previewRow(1),previewRow(2,{is_link:true}),previewRow(3,{is_link:true})]);
  await h.preview();
  const run = h.apply();
  assert.deepEqual(h.writes[0].body.keys,[0,1]);
  h.writes[0].resolve({rows:[saved(0),saved(1)]});
  await next();
  assert.deepEqual(h.writes[1].body.keys,[2]);
  h.writes[1].resolve({rows:[saved(2,'not_found')]});
  await next();
  assert.match(content(h.row(2)),/已加入.*没有找到公开文件/);
  assert.match(content(h.node('identifier-status')),/3 \/ 4/);
  assert.deepEqual(h.writes[2].body.keys,[3]);
  h.writes[2].resolve({rows:[saved(3,'downloaded')]});
  await run;
  assert.deepEqual(h.attachments,['paper-3']);
  assert.equal(h.node('doi-submit').disabled,true);
  const retry = h.retry(2).onclick();
  assert.deepEqual(h.writes[3].body,{preview_id:'pinned-preview',keys:[2]});
  const needsReview = saved(2,'needs_review','existing');
  needsReview.fulltext.source = {url:'javascript:alert(1)'};
  h.writes[3].resolve({rows:[needsReview]});
  await retry;
  assert.match(content(h.row(2)),/已加入.*需人工核对/);
  assert.doesNotMatch(content(h.row(2)),/查看来源/);
  const again = h.retry(2).onclick();
  h.writes[4].resolve({rows:[saved(2,'downloaded','existing')]});
  await again;
  assert.equal(h.retry(2),undefined);
  assert.deepEqual(h.attachments,['paper-3','paper-2']);
});

test('stopping after the active link keeps unprocessed links selected', async () => {
  const h = harness([previewRow(0,{is_link:true}),previewRow(1,{is_link:true})]);
  await h.preview();
  const run = h.apply();
  assert.equal(h.ui.isImporting(),true);
  assert.equal(h.node('doi-cancel').textContent,'停止后续导入');
  h.node('doi-cancel').onclick();
  h.writes[0].resolve({rows:[saved(0,'downloaded')]});
  await run;
  assert.equal(h.writes.length,1);
  assert.equal(h.ui.isImporting(),false);
  assert.match(content(h.node('identifier-status')),/已停止后续导入/);
  assert.equal(h.row(0).children[0].checked,false);
  assert.equal(h.row(1).children[0].checked,true);
});

test('successful import offers only this batch of missing PDFs in its captured project', async () => {
  const h=harness([previewRow(0),previewRow(1,{is_link:true}),previewRow(2,{status:'trashed'})],'project-a');
  let request;
  h.hooks.batchPdf=(ids,scope)=>{request={ids:Array.from(ids),scope};};
  await h.preview();
  assert.equal(h.node('doi-batch-pdf').hidden,true);
  const pending=h.apply();
  h.writes[0].resolve({rows:[saved(0,null)]}); await next();
  h.writes[1].resolve({rows:[saved(1,'downloaded')]});
  await pending;
  assert.equal(h.node('doi-batch-pdf').hidden,false);
  h.node('doi-batch-pdf').onclick();
  assert.deepEqual(request,{ids:['paper-0'],scope:'project-a'});
});
