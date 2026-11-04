const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../frontend/library-search.js'), 'utf8');
const item = (title = 'Synthetic paper', kind = 'pdf') => ({ paper_id: 'a'.repeat(32), paper_title: title, kind, source_name: kind === 'pdf' ? '正文' : '已保存笔记', page: kind === 'pdf' ? 2 : null, snippet: 'Original matching phrase.', source: kind === 'pdf' ? { document_id: 'main', page: 2, quote: 'Original matching phrase.', source_revision: 'b'.repeat(64), block_id: 'p2-b1' } : null });
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function harness() {
  const calls = [], opened = [], shown = [], hidden = [];
  function element() { return { innerHTML: '', handlers: {}, nodes: {}, addEventListener(name, fn) { this.handlers[name] = fn; }, querySelector(selector) { return this.nodes[selector] ||= { focus() {}, disabled: false }; }, querySelectorAll() { return []; } }; }
  const panel = element(), footer = element(), modal = element();
  const hooks = { panel, footer, modal, request: async (method, url) => { calls.push({ method, url }); return { items: [item()], has_more: false, next_offset: 1, warnings: [], warning_count: 0 }; }, onOpenResult: async result => { opened.push(result); return true; }, showModal: id => shown.push(id), hideModal: id => hidden.push(id), toast() {} };
  const context = vm.createContext({ window: {}, console, encodeURIComponent }); vm.runInContext(code, context);
  const ui = context.window.BioLibrarySearch; ui.init(hooks);
  return { ui, hooks, panel, footer, modal, calls, opened, shown, hidden, html: () => panel.innerHTML + footer.innerHTML,
    query: value => panel.handlers.input({ target: { id: 'library-search-query', value } }),
    scope: value => panel.handlers.change({ target: { id: 'library-search-scope', value } }),
    search: () => panel.handlers.submit({ target: { id: 'library-search-form' }, preventDefault() {} }),
    click: (action, index = 0) => modal.handlers.click({ target: { closest: () => ({ dataset: { librarySearchAction: action, librarySearchIndex: String(index) } }) } }),
  };
}
async function searched(h) { h.ui.open(); h.query('matching phrase'); await h.search(); }

test('opening performs no network or model request and explicit search encodes its query', async () => {
  const h = harness(); h.ui.open('ANXA5 & 胃癌'); assert.equal(h.calls.length, 0); await h.search();
  assert.equal(h.calls[0].method, 'GET'); assert.match(h.calls[0].url, /q=ANXA5%20%26%20%E8%83%83%E7%99%8C&scope=all&offset=0&limit=20/);
  assert.match(h.html(), /不受侧栏标签或星标筛选影响/);
});

test('too-short input is rejected locally and scope is applied explicitly', async () => {
  const h = harness(); h.ui.open(); h.query('a'); await h.search(); assert.equal(h.calls.length, 0); assert.match(h.html(), /2–200/);
  h.query('sample'); h.scope('notes'); await h.search(); assert.match(h.calls[0].url, /scope=notes/);
});

test('results escape titles, snippets and errors without inserting model HTML', async () => {
  const h = harness(); h.hooks.request = async () => ({ items: [{ ...item('<img onerror=bad>'), snippet: '<script>bad()</script>' }], next_offset: 1 }); await searched(h);
  assert.doesNotMatch(h.html(), /<img|<script>/); assert.match(h.html(), /&lt;script&gt;/);
  h.hooks.request = async () => { throw Error('<svg onload=bad>'); }; await h.search(); assert.doesNotMatch(h.html(), /<svg/);
});

test('notes remain distinguishable from PDF pages and opening is explicit', async () => {
  const h = harness(); h.hooks.request = async () => ({ items: [item('Notes result', 'notes')], next_offset: 1 }); await searched(h);
  assert.match(h.html(), /已保存笔记/); assert.doesNotMatch(h.html(), /第 null 页/); assert.equal(h.opened.length, 0);
  await h.click('open'); assert.equal(h.opened[0].source, null); assert.equal(h.ui.isOpen(), false);
});

test('blocked navigation retains results and a later explicit retry can open', async () => {
  const h = harness(); await searched(h); h.hooks.onOpenResult = async () => false; await h.click('open');
  assert.equal(h.ui.isOpen(), true); assert.match(h.html(), /Synthetic paper/); assert.equal(h.hidden.length, 0);
  h.hooks.onOpenResult = async () => true; await h.click('open'); assert.equal(h.hidden.length, 1);
});

test('source verification failure retains search results and retry action', async () => {
  const h = harness(); await searched(h); h.hooks.onOpenResult = async () => { throw Error('PDF 已变化'); }; await h.click('open');
  assert.equal(h.ui.isOpen(), true); assert.match(h.html(), /PDF 已变化/); assert.match(h.html(), /Synthetic paper/);
});

test('changing query or scope invalidates a pending response and prevents opening old results', async () => {
  const h = harness(); await searched(h); const waiting = deferred(); h.hooks.request = () => waiting.promise;
  h.query('pending'); const request = h.search(); h.query('changed'); waiting.resolve({ items: [item('Stale result')], next_offset: 1 }); await request;
  assert.doesNotMatch(h.html(), /Stale result/); await h.click('open'); assert.equal(h.opened.length, 0);
  const scoped = deferred(); h.hooks.request = () => scoped.promise; const oldScope = h.search(); h.scope('notes'); scoped.resolve({ items: [item('Old scope')], next_offset: 1 }); await oldScope;
  assert.doesNotMatch(h.html(), /Old scope/);
});

test('newer submitted search wins and a closed response cannot appear on reopen', async () => {
  const h = harness(); h.ui.open(); const old = deferred(); h.hooks.request = (_, url) => url.includes('q=old') ? old.promise : Promise.resolve({ items: [item('New result')], next_offset: 1 });
  h.query('old'); const pending = h.search(); h.query('new'); await h.search(); old.resolve({ items: [item('Old result')], next_offset: 1 }); await pending;
  assert.match(h.html(), /New result/); assert.doesNotMatch(h.html(), /Old result/);
  const closed = deferred(); h.hooks.request = () => closed.promise; h.query('closed'); const request = h.search(); h.ui.close(); closed.resolve({ items: [item('Closed result')], next_offset: 1 }); await request; h.ui.open(); assert.doesNotMatch(h.html(), /Closed result/);
});

test('pagination retains results and uses server next offset; unsubmitted edits disable more', async () => {
  const h = harness(); h.hooks.request = async (_, url) => { h.calls.push({ url }); return { items: [item(url.includes('offset=0') ? 'First' : 'Second')], next_offset: url.includes('offset=0') ? 20 : 40, has_more: true }; }; await searched(h);
  await h.click('more'); assert.match(h.calls[1].url, /offset=20/); assert.match(h.html(), /First/); assert.match(h.html(), /Second/);
  h.query('changed'); await h.click('more'); assert.equal(h.calls.length, 2);
});

test('failed searches can retry and partial material warnings are visible', async () => {
  const h = harness(); h.hooks.request = async () => { throw Error('unavailable'); }; await searched(h); assert.match(h.html(), /搜索失败/);
  h.hooks.request = async () => ({ items: [item()], next_offset: 1, warnings: [{ paper_title: 'Scanned PDF', message: '需要文字识别' }], warning_count: 1 }); await h.search();
  assert.doesNotMatch(h.html(), /搜索失败/); assert.match(h.html(), /部分材料未能检索 · 1 项/); assert.match(h.html(), /Scanned PDF/);
});

test('close invalidates the navigation context and double clicks only open once', async () => {
  const h = harness(); await searched(h); const navigation = deferred(); let context, count = 0;
  h.hooks.onOpenResult = async (_, value) => { context = value; count++; return navigation.promise; };
  const opening = h.click('open'); await h.click('open'); assert.equal(count, 1); assert.equal(context.isCurrent(), true);
  h.ui.close(); assert.equal(context.isCurrent(), false); navigation.resolve(true); await opening; assert.equal(h.hidden.length, 1);
  h.ui.open(); assert.match(h.html(), /Synthetic paper/);
});

test('malformed pagination reports failure instead of losing the existing page', async () => {
  const h = harness(); h.hooks.request = async () => ({ items: [item()], next_offset: 1, has_more: true }); await searched(h);
  h.hooks.request = async () => ({ items: [item('Invalid next')], next_offset: 0 }); await h.click('more');
  assert.match(h.html(), /搜索结果不完整/); assert.match(h.html(), /Synthetic paper/); assert.doesNotMatch(h.html(), /Invalid next/);
});
