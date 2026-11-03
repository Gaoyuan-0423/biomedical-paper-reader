const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../frontend/resources.js'), 'utf8');
const A = 'a'.repeat(32), B = 'b'.repeat(32), R = 'c'.repeat(32), S = 'd'.repeat(32);
const paper = (id = A) => ({ id, has_file: true, pages: 10, supplements: [{ id: S, filename: 'Supplement.pdf', pages: 4 }] });
const record = (extra = {}) => ({ id: R, title: 'Public atlas', kind: 'dataset', role: 'reused', repository: 'GEO', accession: 'GSE12345', url: 'https://example.org/dataset', organism: '', tissue: '', modality: '', notes: '', document_id: '', page: null, quote: '', status: 'pending', source_available: true, ...extra });
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function harness() {
  const handlers = {}, calls = [], messages = [], changes = [], navigation = [];
  const panel = { innerHTML: '', addEventListener(name, fn) { handlers[name] = fn; }, querySelector: () => ({ focus() {} }) };
  const hooks = {
    request: async (method, url, body) => { calls.push({ method, url, body }); return method === 'GET' ? [] : record(body); },
    toast: (...args) => messages.push(args), onChanged: id => changes.push(id),
    openPaper: async id => { navigation.push(['paper', id]); },
    jump: async (...args) => { navigation.push(['jump', ...args]); },
  };
  const context = vm.createContext({ window: { confirm: () => true }, document: { getElementById: () => panel }, URL, console });
  vm.runInContext(source, context); const ui = context.window.BioResources; ui.init(hooks);
  return { ui, hooks, panel, calls, messages, changes, navigation,
    click(action, data = {}) { return handlers.click({ target: { closest: selector => selector === '[data-resource-action]' ? { dataset: { resourceAction: action, ...data } } : null } }); },
    mode(value) { return handlers.click({ target: { closest: selector => selector === '[data-resource-mode]' ? { dataset: { resourceMode: value } } : null } }); },
    input(field, value) { return handlers.input({ target: { dataset: { resourceField: field }, value } }); },
    field(field, value) { return handlers.change({ target: { dataset: { resourceField: field }, value } }); },
    status(value, resourceKey = A + ':' + R) { return handlers.change({ target: { dataset: { resourceStatus: resourceKey }, value } }); },
    query(value) { return handlers.input({ target: { dataset: {}, value, matches: selector => selector === '#resource-search input' } }); },
    submit(id = 'resource-form') { return handlers.submit({ target: { id }, preventDefault() {} }); },
  };
}

test('full library works without selecting a paper and opens linked papers', async () => {
  const h = harness();
  h.hooks.request = async (method, url) => { h.calls.push({ method, url }); return [record({ paper_id: B, paper_title: 'Second paper', related_papers: [{ id: A, title: 'First paper' }] })]; };
  await h.ui.open(null);
  assert.equal(h.calls[0].url, '/api/resources?q=');
  assert.match(h.panel.innerHTML, /Second paper/); assert.match(h.panel.innerHTML, /其他关联论文/);
  assert.doesNotMatch(h.panel.innerHTML, /data-resource-action="edit"/);
  await h.click('paper', { resourcePaper: A });
  assert.deepEqual(h.navigation, [['paper', A]]);
});

test('a metadata-only paper can save a resource with no PDF source', async () => {
  const h = harness(); await h.ui.open({ id: A, has_file: false, supplements: [] });
  await h.click('add'); h.input('title', 'Code repository');
  await h.field('kind', 'code'); await h.field('role', 'implementation');
  h.input('url', 'https://example.org/code');
  await h.submit();
  const posted = h.calls.find(c => c.method === 'POST');
  assert.equal(posted.body.document_id, ''); assert.equal(posted.body.page, null);
  assert.equal(posted.body.kind, 'code'); assert.equal(posted.body.role, 'implementation');
  assert.equal(posted.body.status, 'pending'); assert.equal(h.ui.canLeave(), true);
  assert.deepEqual(h.changes, [A]);
});

test('recording a supplementary source keeps its page and quote through persistence', async () => {
  const h = harness(); await h.ui.open(paper()); await h.click('add');
  h.input('title', 'Atlas dataset'); await h.field('document_id', S); h.input('page', '3');
  h.input('quote', 'Data availability statement'); h.input('accession', 'GSE12345');
  await h.submit();
  const body = h.calls.find(c => c.method === 'POST').body;
  assert.equal(body.document_id, S); assert.equal(body.page, 3); assert.equal(body.quote, 'Data availability statement');
  await h.click('jump', { resourceKey: A + ':' + R });
  assert.deepEqual(h.navigation, [['jump', S, 3]]);
});

test('failure retains draft, prevents duplicate writes, and retries successfully', async () => {
  const h = harness(), pending = deferred(); await h.ui.open(paper()); await h.click('add'); h.input('title', 'Do not lose this');
  let writes = 0; h.hooks.request = () => { writes++; return pending.promise; };
  const saving = h.submit(); await h.submit(); assert.equal(writes, 1); assert.equal(h.ui.canLeave(), false);
  assert.equal(await h.ui.showAll(), false);
  pending.reject(new Error('offline')); await saving;
  assert.match(h.panel.innerHTML, /Do not lose this/); assert.match(h.panel.innerHTML, /offline/);
  h.hooks.request = async (_, __, body) => record(body); await h.submit();
  assert.equal(h.ui.canLeave(), true); assert.match(h.panel.innerHTML, /resource-card/);
});

test('late reads cannot replace results from the currently opened paper', async () => {
  const h = harness(), first = deferred();
  h.hooks.request = async (_, url) => url.includes(A) ? first.promise : [record({ title: 'Current resource' })];
  const reading = h.ui.open(paper(A)); await h.ui.open(paper(B));
  first.resolve([record({ title: 'Previous resource' })]); await reading;
  assert.match(h.panel.innerHTML, /Current resource/); assert.doesNotMatch(h.panel.innerHTML, /Previous resource/);
});

test('search responses are ordered by latest query and encode query text', async () => {
  const h = harness(), first = deferred(); await h.ui.open(null);
  h.hooks.request = async (_, url) => { h.calls.push({ url }); return url.includes('first') ? first.promise : [record({ title: 'Latest match' })]; };
  h.query('first'); const reading = h.submit('resource-search');
  h.query('human / tumor'); await h.submit('resource-search');
  first.resolve([record({ title: 'Old match' })]); await reading;
  assert.equal(h.calls.at(-1).url, '/api/resources?q=human%20%2F%20tumor');
  assert.match(h.panel.innerHTML, /Latest match/); assert.doesNotMatch(h.panel.innerHTML, /Old match/);
});

test('reopening the same paper preserves all-library mode, query and a draft', async () => {
  const h = harness(); await h.ui.open(paper()); await h.ui.showAll(); h.query('GSE12345'); await h.submit('resource-search');
  await h.ui.open(paper()); assert.equal(h.calls.at(-1).url, '/api/resources?q=GSE12345');
  await h.mode('current'); await h.click('add'); h.input('title', 'Draft');
  const count = h.calls.length; await h.ui.open(paper());
  assert.equal(h.calls.length, count); assert.equal(h.ui.canLeave(), false);
  await h.field('document_id', S); assert.match(h.panel.innerHTML, /Draft/);
});

test('late mutation cannot render into another paper and reports the original paper change', async () => {
  const h = harness(), write = deferred(); await h.ui.open(paper()); await h.click('add'); h.input('title', 'Old write');
  h.hooks.request = async method => method === 'GET' ? [record({ title: 'Current resource' })] : write.promise;
  const saving = h.submit(); h.ui.clear(); await h.ui.open(paper(B));
  write.resolve(record({ title: 'Old write' })); await saving;
  assert.match(h.panel.innerHTML, /Current resource/); assert.doesNotMatch(h.panel.innerHTML, /Old write/);
  assert.deepEqual(h.changes, [A]);
});

test('unsafe links and markup are not rendered as executable content', async () => {
  const h = harness(); h.hooks.request = async () => [record({ title: '<script>bad()</script>', notes: '<img onerror=bad()>', url: 'javascript:alert(1)', paper_id: A, paper_title: '" onclick="bad', related_papers: [{ id: B, title: '<svg onload=bad()>' }] })];
  await h.ui.open(null);
  assert.doesNotMatch(h.panel.innerHTML, /href="javascript:|<script>|<img|<svg onload|" onclick="/);
  assert.match(h.panel.innerHTML, /&lt;script&gt;/); assert.match(h.panel.innerHTML, /&lt;svg/);
  h.hooks.request = async () => [record()]; await h.ui.open(null);
  assert.match(h.panel.innerHTML, /target="_blank" rel="noopener noreferrer"/);
});

test('invalid URLs and source pages fail before any write', async () => {
  const h = harness(); await h.ui.open(paper()); await h.click('add'); h.input('title', 'Resource');
  h.input('url', 'file:///etc/passwd'); await h.submit(); assert.equal(h.calls.length, 1);
  assert.match(h.panel.innerHTML, /http 或 https/);
  h.input('url', 'https://example.org'); await h.field('document_id', S); h.input('page', '5'); await h.submit();
  assert.equal(h.calls.length, 1); assert.match(h.panel.innerHTML, /有效的原文文件和 PDF 页码/);
});

test('missing sources cannot jump or verify; edits can detach an unavailable source', async () => {
  const h = harness(); h.hooks.request = async method => { assert.equal(method, 'GET'); return [record({ document_id: B, page: 1, source_available: false })]; };
  await h.ui.open(paper()); await h.status('verified'); await h.click('jump', { resourceKey: A + ':' + R });
  assert.equal(h.navigation.length, 0); assert.match(h.panel.innerHTML, /value="verified" disabled/);
  await h.click('edit', { resourceKey: A + ':' + R });
  h.hooks.request = async (method, url, body) => { h.calls.push({ method, url, body }); return record(body); };
  await h.submit(); assert.equal(h.calls[0].body.document_id, ''); assert.equal(h.calls[0].body.page, null);
});

test('global source jump opens its paper first and respects failed navigation', async () => {
  const h = harness(); h.hooks.request = async () => [record({ paper_id: B, paper_title: 'Other paper', document_id: S, page: 3 })];
  await h.ui.open(null); await h.click('jump', { resourceKey: B + ':' + R });
  assert.deepEqual(h.navigation, [['paper', B], ['jump', S, 3]]);
  h.navigation.length = 0; h.hooks.openPaper = async () => false;
  await h.click('jump', { resourceKey: B + ':' + R }); assert.equal(h.navigation.length, 0);
});

test('status failure reverts to authoritative state and changed sources require reverification', async () => {
  const h = harness(); h.hooks.request = async method => { if (method === 'GET') return [record({ source_changed: true, status: 'verified' })]; throw new Error('offline'); };
  await h.ui.open(paper()); assert.match(h.panel.innerHTML, /原文文件已更新/); assert.match(h.panel.innerHTML, /value="pending" selected/);
  await h.status('verified'); assert.match(h.panel.innerHTML, /value="pending" selected/); assert.match(h.panel.innerHTML, /保存失败/);
});

test('empty draft can be left while meaningful unsaved changes block navigation', async () => {
  const h = harness(); await h.ui.open(paper()); await h.click('add'); assert.equal(h.ui.canLeave(), true);
  await h.field('role', 'reused'); assert.equal(h.ui.canLeave(), false);
  await h.click('paper', { resourcePaper: B }); assert.equal(h.navigation.length, 0);
  await h.click('cancel'); assert.equal(h.ui.canLeave(), true);
});
