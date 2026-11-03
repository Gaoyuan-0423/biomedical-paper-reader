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
    return { value: '', textContent: '', dataset: {}, style: {}, children: [],
      get innerHTML() { return this.html || ''; }, set innerHTML(value) { this.html = value; this.children = []; },
      get offsetTop() { return this.parent ? 20 + this.parent.children.slice(0, this.parent.children.indexOf(this)).reduce((n, el) => n + parseFloat(el.style.height || 0) + 18, 0) : 0; },
      classList: { toggle() {}, add() {}, remove() {} }, hidden: false,
      setAttribute() {}, querySelector: () => element(), querySelectorAll: () => [],
      addEventListener(name, fn) { this['on' + name] = fn; }, before() {}, appendChild(child) { child.parent = this; this.children.push(child); },
      getContext: () => ({}), scrollTo({ top }) { this.scrollTop = Math.max(0, top); }, click() { this.onclick?.(); },
      remove() { if (this.parent) this.parent.children = this.parent.children.filter(el => el !== this); } };
  }
  const $ = (id) => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
  const context = vm.createContext({
    document: { getElementById: $, createElement: element, querySelectorAll: () => [], addEventListener() {}, body: element() },
    window: { matchMedia: () => ({ matches: false }), devicePixelRatio: 1 },
    location: { pathname: '/' }, history: { replaceState() {} },
    setTimeout: () => 1, clearTimeout() {}, confirm: () => true,
    requestAnimationFrame: callback => setImmediate(callback),
    performance, AbortController, TextDecoder, Blob, URL, console,
    fetch: async () => { throw new Error('Unexpected fetch'); },
  });
  vm.runInContext(source.replace("  document.addEventListener('DOMContentLoaded', init);",
    '  globalThis.app = { state, api, updateNoteActions, visiblePapers, saveWorkflow, refreshWorkflow, renderWorkflow, scheduleSave, flushSave, openPaper, fetchFigures, generateNotes, renderPages, clearPdf, rerenderPdf, exportWord, illustrateNotes, changeSupplements, exportMarkdown, loadPdf, goToPage, paintVisiblePages, currentPage, rememberReadingPosition, flushReadingPositions, switchDocument, showFigureSource, reextractFigures, submitCrop };'), context);
  const app = context.app;
  app.state.current = { id: A, title: 'Synthetic paper', notes: '', tags: [] };
  app.state.settings = { has_api_key: true, model: 'test' };
  app.api.figures = async () => [];
  app.api.illustrate = async (id, notes) => ({ notes });
  app.api.prepareExport = (id, notes) => app.api.illustrate(id, notes);
  app.api.saveReading = async () => ({});
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
  h.$('pdf-scroll').clientWidth = 640; h.$('pdf-scroll').clientHeight = 600;
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
  for (const kind of ['Table', 'TableRow', 'TableCell', 'Paragraph', 'TextRun', 'Document', 'ImageRun', 'ExternalHyperlink']) {
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

function pdfFixture(h, count = 100, render) {
  const rendered = [], cleaned = [];
  const pages = Array.from({ length: count }, (_, i) => ({
    getViewport: ({ scale }) => ({ width: 600 * scale, height: (i % 2 ? 900 : 800) * scale }),
    render: () => { rendered.push(i + 1); return render ? render(i + 1) : { promise: Promise.resolve(), cancel() {} }; },
    cleanup() { cleaned.push(i + 1); },
  }));
  h.$('pdf-scroll').clientWidth = 640; h.$('pdf-scroll').clientHeight = 600;
  h.$('pdf-scroll').scrollTop = 0;
  h.state.pdf = { numPages: count, getPage: async n => pages[n - 1], destroy() {} };
  h.state.pdfContext = { paperId: A, documentId: 'main' };
  return { rendered, cleaned, doc: h.state.pdf };
}

test('a 100-page mixed-size PDF renders only the current neighborhood and releases distant canvases', async () => {
  const h = harness(), pdf = pdfFixture(h);
  await h.renderPages(0, 1);
  assert.deepEqual(pdf.rendered, [1, 2]);
  assert.equal(h.state.pageWrappers[1].style.height, '900px');
  const oldCanvas = h.state.pdfView.canvases.get(1).canvas;
  h.goToPage(80, false); await h.paintVisiblePages();
  assert.equal(h.currentPage(), 80);
  assert.deepEqual(pdf.rendered, [1, 2, 80, 79, 81]);
  assert.equal(oldCanvas.width, 0);
  assert.ok(pdf.cleaned.includes(1));
  assert.equal(h.state.pdfView.canvases.size, 3);
  assert.equal(h.$('pdf-page').value, 80);
});

test('jumping during a canvas render cancels it and prioritizes the new page', async () => {
  const h = harness(), first = deferred(); let cancelled = 0;
  const pdf = pdfFixture(h, 100, n => n === 1 ? { promise: first.promise, cancel() {
    cancelled++; first.reject(Object.assign(new Error('cancelled'), { name: 'RenderingCancelledException' }));
  } } : { promise: Promise.resolve(), cancel() {} });
  const rendering = h.renderPages(0, 1);
  await new Promise(setImmediate);
  h.goToPage(60, false); await rendering;
  assert.ok(cancelled > 0);
  assert.equal(pdf.rendered[1], 60);
  assert.deepEqual([...h.state.pdfView.canvases.keys()], [60, 59, 61]);
});

test('a failed PDF page offers retry without blanking the document', async () => {
  const h = harness(); let failed = false;
  pdfFixture(h, 3, n => ({ promise: n === 1 && !failed ? (failed = true, Promise.reject(new Error('render failed'))) : Promise.resolve(), cancel() {} }));
  await h.renderPages(0, 1);
  const retry = h.state.pageWrappers[0].children.find(el => el.className === 'btn pdf-retry');
  assert.ok(retry); assert.ok(h.state.pdfView.canvases.has(2));
  retry.click(); await h.paintVisiblePages();
  assert.ok(h.state.pdfView.canvases.has(1));
  assert.equal(h.state.pdfView.failed.size, 0);
});

test('reading saves serialize and retain the newest position across documents', async () => {
  const h = harness(), first = deferred(), writes = [];
  pdfFixture(h); await h.renderPages(0, 1);
  h.api.saveReading = async (id, doc, position) => { writes.push([id, doc, position.page]); if (writes.length === 1) await first.promise; };
  const saving = h.flushReadingPositions();
  h.state.pdfContext = { paperId: A, documentId: B }; h.goToPage(5, false);
  h.state.pdfContext = { paperId: A, documentId: 'main' }; h.goToPage(10, false);
  first.resolve(); await saving;
  assert.deepEqual(writes, [[A, 'main', 1], [A, B, 5], [A, 'main', 10]]);
  assert.equal(h.state.readingCache.get(A).active_document, 'main');
  assert.equal(h.state.readingCache.get(A).documents[B].page, 5);
});

test('reading-position save failures retry; deleted materials do not block other saves', async () => {
  const h = harness(); pdfFixture(h); await h.renderPages(0, 1);
  h.api.saveReading = async () => { throw new Error('offline'); };
  await h.flushReadingPositions(); assert.equal(h.state.readingPending.size, 1);
  h.state.pdfContext = { paperId: B, documentId: 'main' }; h.goToPage(4, false);
  const writes = [];
  h.api.saveReading = async (id, doc, pos) => { if (id === A) throw Object.assign(new Error('gone'), { status: 404 }); writes.push(pos.page); };
  await h.flushReadingPositions();
  assert.deepEqual(writes, [4]); assert.equal(h.state.readingPending.size, 0);
});

test('PDF loading restores the selected source position and manual zoom', async () => {
  const h = harness(), pdf = pdfFixture(h, 10), urls = [];
  h.state.current.has_file = true;
  h.state.current.supplements = [{ id: B, pages: 10, filename: 'Supplement.pdf' }];
  h.state.readingCache.set(A, { active_document: B, documents: { [B]: { page: 7, scale: 1.5, fit_width: false } } });
  h.context.pdfjsLib = h.context.window.pdfjsLib = { GlobalWorkerOptions: {}, getDocument: () => ({ promise: Promise.resolve(pdf.doc) }) };
  h.context.fetch = async url => { urls.push(url); return { ok: true, arrayBuffer: async () => new ArrayBuffer(8) }; };
  await h.switchDocument(B);
  assert.match(urls[0], new RegExp('document_id=' + B));
  assert.equal(h.currentPage(), 7); assert.equal(h.state.scale, 1.5); assert.equal(h.state.fitWidth, false);
  assert.equal(pdf.rendered[0], 7);
});

test('figure source navigation switches to the correct supplement and page', async () => {
  const h = harness(), pdf = pdfFixture(h, 10);
  h.state.current.has_file = true;
  h.state.current.supplements = [{ id: B, pages: 10, filename: 'Supplement.pdf' }];
  h.state.figures = [{ id: 's002', page: 6, document_id: B }];
  h.context.pdfjsLib = h.context.window.pdfjsLib = { GlobalWorkerOptions: {}, getDocument: () => ({ promise: Promise.resolve(pdf.doc) }) };
  h.context.fetch = async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) });
  await h.showFigureSource('s002');
  assert.equal(h.state.documentId, B); assert.equal(h.currentPage(), 6);
});

test('late re-extraction and crop results cannot enter another paper', async () => {
  const h = harness(), extraction = deferred(), crop = deferred();
  h.api.reextract = () => extraction.promise; h.api.cropFigure = () => crop.promise;
  const extracting = h.reextractFigures(), cropping = h.submitCrop(1, [0, 0, 10, 10], 10, 10);
  h.state.current = { id: B }; h.state.figures = [{ id: 'f099' }];
  extraction.resolve({ figures: [{ id: 'f001' }] }); crop.resolve({ id: 'm002' });
  await Promise.all([extracting, cropping]);
  assert.equal(h.state.figures.length, 1); assert.equal(h.state.figures[0].id, 'f099');
});

test('failed illustrated-note saves keep the failure status instead of reporting success', async () => {
  const h = harness(); h.$('editor').value = 'Original';
  h.api.illustrate = async () => ({ notes: 'Original with images' });
  h.api.patch = async () => { throw new Error('offline'); };
  await h.illustrateNotes();
  assert.equal(h.state.pendingSave.notes, 'Original with images');
  assert.match(h.$('save-status').textContent, /保存失败/);
  assert.doesNotMatch(h.$('toast').textContent, /已自动补入/);
});

test('a hidden PDF view defers rendering without losing its restored page', async () => {
  const h = harness(), pdf = pdfFixture(h);
  h.$('pdf-scroll').clientWidth = 0;
  await h.renderPages(0, 70);
  assert.equal(pdf.rendered.length, 0); assert.equal(h.state.readingPending.size, 0);
  h.$('pdf-scroll').clientWidth = 640;
  h.rerenderPdf(); await new Promise(setImmediate);
  assert.equal(h.currentPage(), 70); assert.equal(pdf.rendered[0], 70);
});

test('high zoom on a retina screen bounds canvas pixels while keeping display size', async () => {
  const h = harness(); pdfFixture(h, 3);
  h.context.window.devicePixelRatio = 3;
  h.state.fitWidth = false; h.state.scale = 3;
  await h.renderPages(0, 2);
  const canvas = h.state.pdfView.canvases.get(2).canvas;
  assert.ok(canvas.width * canvas.height <= 8_000_000);
  assert.equal(canvas.style.width, '1800px'); assert.equal(canvas.style.height, '2700px');
});

test('fit width accommodates landscape pages in a mixed-orientation PDF', async () => {
  const h = harness(), pdf = pdfFixture(h, 3);
  const landscape = await pdf.doc.getPage(2);
  landscape.getViewport = ({ scale }) => ({ width: 900 * scale, height: 600 * scale });
  await h.renderPages(0, 2);
  assert.equal(h.state.scale, 600 / 900);
  assert.ok(h.state.pageWrappers.every(page => parseFloat(page.style.width) <= 600));
  assert.equal(h.currentPage(), 2);
});

test('workflow filters intersect reading, decision, tasks and search, with safe legacy defaults', () => {
  const h = harness();
  h.state.papers = [
    { id: A, title: 'Cancer atlas', reading_status: 'reading', reading_decision: 'priority', tasks: [{ id: 'resume' }] },
    { id: B, title: 'Legacy paper' },
    { id: C, title: 'Cancer methods', reading_status: 'reading', reading_decision: 'reference', tasks: [] },
  ];
  h.state.readingFilter = 'unread';
  assert.deepEqual(Array.from(h.visiblePapers(), p => p.id), [B]);
  h.state.readingFilter = 'reading'; h.state.decisionFilter = 'priority'; h.state.taskFilter = 'resume'; h.state.query = 'cancer';
  assert.deepEqual(Array.from(h.visiblePapers(), p => p.id), [A]);
  h.state.query = 'absent'; assert.equal(h.visiblePapers().length, 0);
});

test('workflow save waits for note persistence and does not replace a new draft', async () => {
  const h = harness(), request = deferred(), writes = [];
  h.api.patch = async (id, body) => {
    writes.push(body);
    if (body.notes) await request.promise;
    return { id, ...body, notes: 'server snapshot', tasks: [] };
  };
  edit(h, 'keep draft');
  const save = h.saveWorkflow({ reading_status: 'completed' });
  assert.equal(writes.length, 1); assert.equal(writes[0].notes, 'keep draft');
  request.resolve(); await save;
  assert.equal(h.state.current.reading_status, 'completed');
  assert.equal(h.$('editor').value, 'keep draft');
  assert.equal(h.state.current.notes, 'keep draft');
  assert.equal(writes[1].reading_status, 'completed');
});

test('failed status save restores persisted controls and late status response cannot modify another paper', async () => {
  const h = harness(); h.state.current.reading_status = 'unread';
  h.api.patch = async () => { throw new Error('offline'); };
  await h.saveWorkflow({ reading_status: 'completed' });
  assert.equal(h.state.current.reading_status, 'unread');
  assert.equal(h.$('paper-reading').value, 'unread');
  const request = deferred(); h.api.patch = () => request.promise;
  const save = h.saveWorkflow({ reading_status: 'reading' }); await new Promise(setImmediate);
  h.state.current = { id: B, reading_status: 'skimmed' };
  request.resolve({ id: A, reading_status: 'reading', tasks: [] }); await save;
  assert.equal(h.state.current.reading_status, 'skimmed');
});

test('late workflow refresh cannot undo a newer manual reading decision', async () => {
  const h = harness(), request = deferred();
  h.api.paper = () => request.promise;
  const refresh = h.refreshWorkflow(A);
  h.api.patch = async (id, body) => ({ id, ...body, tasks: [] });
  await h.saveWorkflow({ reading_decision: 'priority' });
  request.resolve({ id: A, reading_decision: 'pending' }); await refresh;
  assert.equal(h.state.current.reading_decision, 'priority');
});

test('partial-generation marker survives retry edits and report generation never marks reading complete', async () => {
  const h = harness(); let body;
  h.state.current.reading_status = 'unread';
  h.api.patch = async (id, value) => { body = value; return { id, ...value, tasks: [{ id: 'notes_incomplete' }] }; };
  stream(h, [{ type: 'chunk', text: 'Partial text' }]);
  await h.generateNotes();
  assert.equal(body.notes_incomplete, true);
  edit(h, 'Partial text plus manual edit'); await h.flushSave();
  assert.equal(body.notes_incomplete, true);
  assert.equal(h.state.current.reading_status, 'unread');
});

test('generation uses authoritative material freshness from done event', async () => {
  const h = harness(); h.state.current.reading_status = 'skimmed';
  stream(h, [{ type: 'done', notes: 'Finished report', paper: { notes_incomplete: false, notes_stale: true, tasks: [{ id: 'notes_stale' }] } }]);
  await h.generateNotes();
  assert.equal(h.state.current.notes_stale, true);
  assert.equal(h.state.current.tasks[0].id, 'notes_stale');
  assert.equal(h.state.current.reading_status, 'skimmed');
});

test('loading figures adds exact source links to an already saved note preview', async () => {
  const h = harness(), created = [], originalCreate = h.context.document.createElement;
  h.$('editor').value = '![Figure 1](figure:f001)';
  h.context.window.marked = h.context.marked = { parse: () => '<p>figure</p>' };
  h.context.document.createElement = () => {
    const element = originalCreate(); element.after = sibling => created.push(sibling); return element;
  };
  const source = { alt: 'Figure 1', getAttribute: () => 'figure:f001', replaceWith() {} };
  h.$('preview').querySelectorAll = selector => selector === 'img' ? [source] : [];
  h.api.figures = async () => [{ id: 'f001', label: 'Figure 1', document_id: C, document_name: 'Supplement', page: 3 }];
  await h.fetchFigures();
  assert.equal(created.length, 1);
  assert.equal(created[0].dataset.noteSourceFig, 'f001');
  assert.equal(created[0].textContent, 'Supplement · 第 3 页 · 查看原文');
});

test('saved resources enable report exports even without a note and do not enable illustration', () => {
  const h = harness(); h.state.current.resources_count = 1;
  h.updateNoteActions();
  assert.equal(h.$('btn-export-word').disabled, false);
  assert.equal(h.$('btn-export-md').disabled, false);
  assert.equal(h.$('btn-copy-wechat').disabled, false);
  assert.equal(h.$('btn-illustrate').disabled, true);
});

test('Word export uses saved research appendix without modifying notes and keeps literal text and resource hyperlinks', async () => {
  const h = harness(); mockWord(h); let requested;
  h.state.current.resources_count = 1;
  h.api.prepareExport = async (id, notes) => {
    requested = { id, notes };
    return { notes: '<!-- bioreader-research-appendix:v1 sha256=' + 'a'.repeat(64) + ' -->\n## 数据与代码\n\n- 与本文关系：复用已有数据\n- 名称：a\\_b &amp; c\n- 链接：[打开资源](https://example.org/data)\n<!-- /bioreader-research-appendix:v1 -->\n' };
  };
  await h.exportWord();
  assert.deepEqual(requested, { id: A, notes: '' });
  assert.equal(h.$('editor').value, '');
  assert.equal(h.state.current.notes, '');
  const serialized = JSON.stringify(h.exported);
  assert.ok(serialized.includes('复用已有数据'));
  const paragraphText = h.exported.sections[0].children.map(p => (p.children || []).map(r => r.text || '').join('')).join('\n');
  assert.ok(paragraphText.includes('a_b & c'));
  assert.ok(serialized.includes('ExternalHyperlink'));
  assert.ok(serialized.includes('https://example.org/data'));
  assert.ok(!serialized.includes('bioreader-research-appendix'));
});

test('unsaved resource draft blocks export instead of silently excluding it', async () => {
  const h = harness(); mockWord(h);
  h.context.window.BioResources = { canLeave: () => false };
  h.$('editor').value = 'A note';
  let requested = false;
  h.api.prepareExport = async () => { requested = true; return { notes: 'bad' }; };
  await h.exportWord();
  assert.equal(requested, false);
  assert.equal(h.exported, undefined);
});

test('failed research appendix preparation does not export a partial Word document', async () => {
  const h = harness(); mockWord(h); h.$('editor').value = 'Keep note';
  h.api.prepareExport = async () => { throw new Error('records unavailable'); };
  await h.exportWord();
  assert.equal(h.exported, undefined);
  assert.equal(h.$('editor').value, 'Keep note');
  assert.match(h.$('toast').textContent, /records unavailable/);
});
