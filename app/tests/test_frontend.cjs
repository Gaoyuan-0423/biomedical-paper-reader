// Exercise the shipped IIFE with deterministic network timing, without a build step.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
const A = 'a'.repeat(32), B = 'b'.repeat(32), C = 'c'.repeat(32);
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function harness() {
  const elements = new Map();
  function element() {
    return { value: '', textContent: '', innerHTML: '', dataset: {}, style: {}, children: [],
      classList: { toggle() {}, add() {}, remove() {} }, hidden: false,
      setAttribute() {}, querySelector: () => element(), querySelectorAll: () => [],
      addEventListener() {}, before() {}, appendChild(child) { this.children.push(child); },
      getContext: () => ({}), scrollTo() {}, click() {}, remove() {} };
  }
  const $ = (id) => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
  const context = vm.createContext({
    document: { getElementById: $, createElement: element, querySelectorAll: () => [], addEventListener() {}, body: element() },
    window: { matchMedia: () => ({ matches: false }), devicePixelRatio: 1 },
    location: { pathname: '/' }, history: { replaceState() {} },
    setTimeout: () => 1, clearTimeout() {}, confirm: () => true,
    performance, AbortController, TextDecoder, Blob, URL, console,
    fetch: async () => { throw new Error('Unexpected fetch'); },
  });
  vm.runInContext(source.replace("  document.addEventListener('DOMContentLoaded', init);",
    '  globalThis.app = { state, api, scheduleSave, flushSave, openPaper, fetchFigures, generateNotes, renderPages, clearPdf, rerenderPdf, exportWord, illustrateNotes, changeSupplements, exportMarkdown };'), context);
  const app = context.app;
  app.state.current = { id: A, title: 'Synthetic paper', notes: '', tags: [] };
  app.state.settings = { has_api_key: true, model: 'test' };
  app.api.figures = async () => [];
  app.api.illustrate = async (id, notes) => ({ notes });
  $('read-mode').value = 'deep';
  return { ...app, $, context };
}
function edit(h, notes) { h.$('editor').value = notes; h.scheduleSave(); }
function stream(h, events, ending) {
  h.state.current.has_file = true;
  h.context.fetch = async () => ({ ok: true, body: { getReader() {
    let read = false;
    return { async read() {
      if (read) { if (ending) throw ending; return { done: true }; }
      read = true;
      return { done: false, value: new TextEncoder().encode(events.map(e => 'data: ' + JSON.stringify(e) + '\n\n').join('')) };
    } };
  } } });
}

test('autosave serializes writes and flush waits for the newest edit', async () => {
  const h = harness(), first = deferred(), writes = [];
  h.api.patch = async (id, body) => { writes.push(body.notes); if (writes.length === 1) await first.promise; return { id, ...body }; };
  edit(h, 'old'); const flushing = h.flushSave();
  edit(h, 'new'); const secondFlush = h.flushSave();
  assert.deepEqual(writes, ['old']);
  first.resolve();
  assert.equal(await flushing, true); assert.equal(await secondFlush, true);
  assert.deepEqual(writes, ['old', 'new']);
  assert.equal(h.state.pendingSave, null);
  assert.equal(h.$('save-status').textContent, '已保存');
});

test('failed save retains draft, blocks switching, and can be retried', async () => {
  const h = harness(); let opened = false;
  h.api.patch = async () => { throw new Error('offline'); };
  h.api.paper = async () => { opened = true; };
  edit(h, 'keep this'); h.state.pendingPaper = { id: B };
  await h.openPaper();
  assert.equal(opened, false); assert.equal(h.state.current.id, A);
  assert.equal(h.state.pendingSave.notes, 'keep this');
  assert.equal(h.$('editor').value, 'keep this');
  h.api.patch = async (id, body) => ({ id, ...body });
  assert.equal(await h.flushSave(), true);
  assert.equal(h.state.pendingSave, null);
});

test('a failed in-flight save does not replace a newer draft', async () => {
  const h = harness(), request = deferred();
  h.api.patch = () => request.promise;
  edit(h, 'old'); const flushing = h.flushSave(); edit(h, 'latest');
  request.reject(new Error('offline'));
  assert.equal(await flushing, false);
  assert.equal(h.state.pendingSave.notes, 'latest');
});

test('switching waits for an already running autosave', async () => {
  const h = harness(), request = deferred(); let opened = false;
  h.api.patch = () => request.promise;
  h.api.paper = async id => { opened = true; return { id, notes: '', has_file: false }; };
  edit(h, 'keep this'); const flushing = h.flushSave();
  h.state.pendingPaper = { id: B }; const opening = h.openPaper();
  await Promise.resolve(); assert.equal(opened, false);
  request.resolve({ id: A }); await flushing; await opening;
  assert.equal(h.state.current.id, B);
});

test('rapid switching displays only the most recently selected paper', async () => {
  const h = harness(), b = deferred(), c = deferred();
  h.api.paper = id => id === B ? b.promise : c.promise;
  h.state.pendingPaper = { id: B }; const first = h.openPaper();
  await new Promise(setImmediate);
  h.state.pendingPaper = { id: C }; const last = h.openPaper();
  await new Promise(setImmediate);
  c.resolve({ id: C, notes: 'C note', has_file: false }); await last;
  b.resolve({ id: B, notes: 'B note', has_file: false }); await first;
  assert.equal(h.state.current.id, C); assert.equal(h.$('editor').value, 'C note');
});

test('edits made while a new paper loads are saved before switching', async () => {
  const h = harness(), loading = deferred(), writes = [];
  h.api.paper = () => loading.promise;
  h.api.patch = async (id, body) => { writes.push([id, body.notes]); return { id, ...body }; };
  h.state.pendingPaper = { id: B }; const opening = h.openPaper();
  await new Promise(setImmediate); edit(h, 'late edit');
  loading.resolve({ id: B, notes: '', has_file: false }); await opening;
  assert.deepEqual(writes, [[A, 'late edit']]); assert.equal(h.state.current.id, B);
});

test('stale figure responses do not enter the next paper', async () => {
  const h = harness(), response = deferred(); h.api.figures = () => response.promise;
  const reading = h.fetchFigures(); h.state.current = { id: B }; h.state.figures = [{ id: 'f002' }];
  response.resolve([{ id: 'f001' }]); await reading;
  assert.equal(h.state.figures[0].id, 'f002');
});

test('an interrupted stream is saved as partial, never reported complete', async () => {
  const h = harness(), writes = [];
  h.api.patch = async (id, body) => { writes.push(body.notes); return { id, ...body }; };
  stream(h, [{ type: 'chunk', text: 'partial note' }]);
  await h.generateNotes();
  assert.deepEqual(writes, ['partial note']);
  assert.equal(h.$('save-status').textContent, '部分内容已保存 · 未完成');
  assert.match(h.$('toast').textContent, /未收到保存确认/);
  assert.equal(h.$('btn-export-word').disabled, false);
});

test('generation failure before output restores the previous note', async () => {
  const h = harness(); h.state.current.notes = 'original'; h.$('editor').value = 'original';
  stream(h, [{ type: 'error', message: 'upstream unavailable' }]);
  await h.generateNotes();
  assert.equal(h.$('editor').value, 'original'); assert.equal(h.state.current.notes, 'original');
  assert.equal(h.$('save-status').textContent, '已保留原笔记');
});

test('stopping a generation awaits partial saving and retains failed drafts', async () => {
  const h = harness(), saving = deferred();
  h.api.patch = () => saving.promise;
  stream(h, [{ type: 'chunk', text: 'partial' }], Object.assign(new Error('cancelled'), { name: 'AbortError' }));
  const generation = h.generateNotes();
  await new Promise(setImmediate); assert.notEqual(h.state.generating, null);
  saving.reject(new Error('offline')); await generation;
  assert.equal(h.state.generating, null); assert.equal(h.state.pendingSave.notes, 'partial');
  assert.match(h.$('save-status').textContent, /保存失败/);
});

test('only the explicit done event reports a completed saved note', async () => {
  const h = harness(); stream(h, [{ type: 'chunk', text: 'draft' }, { type: 'done', notes: 'final note' }]);
  await h.generateNotes();
  assert.equal(h.$('editor').value, 'final note');
  assert.equal(h.$('save-status').textContent, '已保存');
});

test('a stale first-page load cannot append old PDF wrappers', async () => {
  const h = harness(), page = deferred();
  h.state.pdf = { getPage: () => page.promise, numPages: 2 };
  const rendering = h.renderPages(0, 1); h.state.renderToken++;
  page.resolve({ getViewport: () => ({ width: 100, height: 100 }) });
  await rendering;
  assert.equal(h.state.pageWrappers.length, 0);
  assert.equal(h.$('pdf-pages').children.length, 0);
});

test('clearing a PDF cancels pending canvas renders', () => {
  const h = harness(); let cancelled = 0, destroyed = 0;
  h.state.pdfRenderTasks.add({ cancel() { cancelled++; } });
  h.state.pdf = { destroy() { destroyed++; } };
  h.clearPdf();
  assert.equal(cancelled, 1); assert.equal(destroyed, 1);
  assert.equal(h.state.pdfRenderTasks.size, 0);
});

test('selecting the current paper cancels an earlier pending navigation', async () => {
  const h = harness(), loading = deferred();
  h.api.paper = () => loading.promise;
  h.state.pendingPaper = { id: B }; const opening = h.openPaper();
  await new Promise(setImmediate);
  h.state.pendingPaper = { id: A }; await h.openPaper();
  loading.resolve({ id: B, notes: 'stale' }); await opening;
  assert.equal(h.state.current.id, A);
});

function mockWord(h) {
  const D = { WidthType: { PERCENTAGE: 'pct' }, HeadingLevel: {}, Packer: {
    async toBlob(doc) { h.exported = doc; return new Blob(['docx']); },
  } };
  for (const kind of ['Table', 'TableRow', 'TableCell', 'Paragraph', 'TextRun', 'Document', 'ImageRun']) {
    D[kind] = class { constructor(options) { Object.assign(this, options); this.kind = kind; } };
  }
  h.context.window.docx = D;
  h.context.marked = h.context.window.marked = require('../frontend/vendor/marked.min.js');
}

test('Word export produces editable tables, escaped pipes and following prose', async () => {
  const h = harness(); mockWord(h);
  h.$('editor').value = '# Heading\n\n| Method | Result |\n| --- | --- |\n| A \\| B | pass |\n\nAfter the table';
  await h.exportWord();
  const blocks = h.exported.sections[0].children;
  const table = blocks.find(b => b.kind === 'Table');
  assert.equal(table.rows.length, 2); assert.equal(table.rows[0].tableHeader, true);
  assert.equal(table.rows[1].children[0].children[0].children[0].text, 'A | B');
  assert.equal(blocks.at(-1).children[0].text, 'After the table');
});

test('Word export recognizes tables without outer pipes', async () => {
  const h = harness(); mockWord(h);
  h.$('editor').value = 'Method | Result\n--- | ---\nA | pass';
  await h.exportWord();
  assert.equal(h.exported.sections[0].children[0].kind, 'Table');
});

test('Word export keeps the original paper and figures while switching', async () => {
  const h = harness(), saving = deferred(), urls = []; mockWord(h);
  h.state.figures = [{ id: 'f001', label: 'Original figure', width: 100, height: 80 }];
  h.api.patch = () => saving.promise;
  h.context.fetch = async url => { urls.push(url); return { ok: true, arrayBuffer: async () => new ArrayBuffer(8) }; };
  edit(h, '![Figure](figure:f001)'); const exporting = h.exportWord();
  h.state.current = { id: B, title: 'Another paper' }; h.state.figures = [];
  h.$('editor').value = 'different note'; saving.resolve({ id: A }); await exporting;
  assert.deepEqual(urls, ['/api/papers/' + A + '/figures/f001.png']);
  assert.equal(h.exported.sections[0].children[0].text, 'Original figure');
});

test('adding material prevents a paper switch until parsing completes', async () => {
  const h = harness(), upload = deferred();
  h.api.supplement = () => upload.promise;
  const importing = h.changeSupplements([{ name: 'supp.pdf' }], false);
  await new Promise(setImmediate);
  h.state.pendingPaper = { id: B }; await h.openPaper();
  assert.equal(h.state.current.id, A); assert.equal(h.state.materialBusy, true);
  upload.resolve({ id: A, supplements: [{ id: C, filename: 'supp.pdf', pages: 2 }] });
  await importing;
  assert.equal(h.state.documentId, C); assert.equal(h.state.materialBusy, false);
});

test('automatic illustrations do not overwrite an edit made during the request', async () => {
  const h = harness(), response = deferred();
  h.$('editor').value = 'original'; h.api.illustrate = () => response.promise;
  const illustrating = h.illustrateNotes(); h.$('editor').value = 'new edit';
  response.resolve({ notes: 'original with figures' }); await illustrating;
  assert.equal(h.$('editor').value, 'new edit');
});

test('Word export automatically adds available figures to a text-only note', async () => {
  const h = harness(); mockWord(h);
  h.$('editor').value = 'Text-only legacy note';
  h.api.illustrate = async (id, notes) => ({ notes: notes + '\n\n![Figure 1](figure:f001)' });
  h.context.fetch = async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) });
  await h.exportWord();
  const blocks = h.exported.sections[0].children;
  assert.ok(blocks.some(b => b.children && b.children.some(c => c.kind === 'ImageRun')));
});

test('Word export reports a missing image instead of silently exporting text only', async () => {
  const h = harness(); mockWord(h);
  h.$('editor').value = '![Figure 1](figure:f001)';
  h.context.fetch = async () => ({ ok: false });
  await h.exportWord();
  assert.equal(h.exported, undefined);
  assert.match(h.$('toast').textContent, /无法导出图片/);
});
