const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../frontend/note-sources.js'), 'utf8');
const A = 'a'.repeat(32), B = 'b'.repeat(32), G = 'c'.repeat(32), H = 'd'.repeat(32);
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function harness(request = async () => ({ snapshot_id: G, sources: [{ id: 'N1', page: 2, document_name: 'supplement.pdf', quote: 'Evidence.' }] }), i18n) {
  const root = { items: [], querySelectorAll(selector) {
    return this.items.filter(node => selector.startsWith('a[') ? node.tag === 'a' && node.href.startsWith('note-source:') : node.tag === 'button' && node.className === 'note-citation');
  } };
  function node(tag) {
    return { tag, dataset: {}, attributes: {}, textContent: '', disabled: false,
      setAttribute(key, value) { this.attributes[key] = value; },
      getAttribute(key) { return key === 'href' ? this.href : this.attributes[key]; },
      closest() { return this.inCode ? {} : null; },
      addEventListener(name, callback) { this['on' + name] = callback; },
      replaceWith(next) { root.items.splice(root.items.indexOf(this), 1, next); },
    };
  }
  const context = vm.createContext({ window: { BioI18n: i18n }, document: { createElement: node, createTextNode: text => ({ tag: 'text', textContent: text }) } });
  vm.runInContext(source, context);
  const calls = [], messages = [];
  const api = context.window.BioNoteSources;
  const hooks = { request, jump: async reference => { calls.push(reference); }, toast: message => messages.push(message) };
  api.init(hooks);
  const paper = { id: A, notes_revision: 1, notes_source_snapshot: G, material_revision: 0 };
  function anchor(href = 'note-source:' + G + ':N1', inCode = false) { return { ...node('a'), href, inCode, textContent: 'N1' }; }
  function render(next = paper, entries = [anchor()], options = {}) { root.items = entries; api.render(next, root, options); }
  return { api, root, paper, anchor, render, calls, messages, hooks };
}

test('only immutable-generation note references parse', () => {
  const h = harness();
  assert.equal(h.api.parseReference('note-source:' + G + ':N1').sourceId, 'N1');
  for (const value of ['[1]', 'https://example.test', 'note-source:../../secret:N1', 'note-source:' + G + ':N0', 'note-source:' + G + ':N1000000', 'note-source:' + G + ':N1?x=1']) assert.equal(h.api.parseReference(value), null);
});
test('valid citation displays server page and safe original quotation, then resolves by id', async () => {
  const h = harness(async () => ({ snapshot_id: G, sources: [{ id: 'N1', page: 3, document_name: '<img onerror=bad()>', quote: '<script>bad()</script>' }] }));
  h.render(); assert.equal(h.root.items[0].disabled, true); await tick();
  const button = h.root.items[0];
  assert.equal(button.tag, 'button'); assert.equal(button.textContent, '原文 · 3 页'); assert.match(button.title, /<img onerror=bad\(\)>/);
  await button.onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls)), [{ paperId: A, snapshotId: G, sourceId: 'N1' }]);
});
test('unknown source is disabled and malformed source never remains a navigable link', async () => {
  const h = harness(); h.render(h.paper, [h.anchor('note-source:' + G + ':N99'), h.anchor('note-source:bad:N1')]); await tick();
  assert.equal(h.root.items[0].disabled, true); assert.equal(h.root.items[0].textContent, '来源不可用'); assert.equal(h.root.items[1].tag, 'text');
});
test('code examples and ordinary citation links are left unchanged', async () => {
  const h = harness(); const code = h.anchor(undefined, true), external = h.anchor('https://example.test/reference/1');
  h.render(h.paper, [code, external]); await tick(); assert.equal(h.root.items[0], code); assert.equal(h.root.items[1], external);
});
test('late source response cannot alter a newly opened paper', async () => {
  const first = deferred(); const h = harness(id => id === A ? first.promise : Promise.resolve({snapshot_id: H, sources: [{id: 'N1', page: 7}]}));
  h.render(); await tick(); h.render({...h.paper, id: B, notes_source_snapshot: H}, [h.anchor('note-source:' + H + ':N1')]); await tick();
  first.resolve({snapshot_id: G, sources: [{id: 'N1', page: 99}]}); await tick();
  assert.equal(h.root.items[0].textContent, '原文 · 7 页'); await h.root.items[0].onclick(); assert.equal(h.calls[0].paperId, B);
});
test('new note revision invalidates an in-flight source list', async () => {
  const first = deferred(); let count = 0;
  const h = harness(() => ++count === 1 ? first.promise : Promise.resolve({snapshot_id: G, sources: [{id:'N1',page:4}]}));
  h.render(); await tick(); h.render({...h.paper, notes_revision: 2}); await tick(); first.resolve({snapshot_id:G,sources:[{id:'N1',page:8}]}); await tick();
  assert.equal(h.root.items[0].textContent, '原文 · 4 页');
});
test('transient list failure leaves a click available for a fresh backend resolve', async () => {
  const h = harness(async () => {throw new Error('Temporary disconnect');}); h.render(); await tick();
  assert.equal(h.root.items[0].disabled, false); await h.root.items[0].onclick(); assert.equal(h.calls.length, 1);
});
test('file replacement and removal preserve text but disable old destinations', async () => {
  const h = harness(async () => ({snapshot_id:G,sources:[{id:'N1',page:2,source_changed:true},{id:'N2',page:3,source_available:false}]}));
  h.render(h.paper, [h.anchor(),h.anchor('note-source:'+G+':N2')]); await tick();
  assert.equal(h.root.items[0].textContent,'来源已变化'); assert.equal(h.root.items[1].textContent,'来源已移除'); assert.ok(h.root.items.every(node => node.disabled));
});
test('a delayed list cannot activate sources while generation is in progress', async () => {
  const pending = deferred(); const h = harness(() => pending.promise); h.render(); await tick(); h.render(h.paper,[h.anchor()],{streaming:true});
  pending.resolve({snapshot_id:G,sources:[{id:'N1',page:2}]}); await tick(); assert.equal(h.root.items[0].disabled,true);
});
test('clearing the workspace invalidates pending requests and detached citation actions', async () => {
  const h = harness(); h.render(); await tick(); const old=h.root.items[0]; h.api.clear(); await old.onclick(); assert.equal(h.calls.length,0);
});

test('language changes preserve source quotations and in-flight navigation guards',async()=>{
 const i18n=require('./i18n_reading_harness.cjs')(),h=harness(async()=>({snapshot_id:G,sources:[{id:'N1',page:4,document_id:'main',document_name:'正文',quote:'来源不可用 is quoted paper text.'}]}),i18n);
 h.render();await tick();const button=h.root.items[0];i18n.setLocale('en');
 assert.equal(h.root.items[0],button);assert.equal(button.textContent,'Source · Page 4');assert.match(button.title,/Main text · PDF page 4\n来源不可用 is quoted paper text/);
 const pending=deferred();h.hooks.jump=()=>pending.promise;const opening=button.onclick();assert.equal(button.disabled,true);
 i18n.setLocale('zh-CN');assert.equal(button.disabled,true);assert.equal(button.textContent,'原文 · 4 页');pending.resolve();await opening;assert.equal(button.disabled,false);
});
