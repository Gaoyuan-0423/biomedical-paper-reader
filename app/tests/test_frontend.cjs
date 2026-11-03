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
      setAttribute() {}, focus() {}, querySelector: () => element(), querySelectorAll: () => [],
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
    '  globalThis.app = { state, api, addTag, removeTag, autoTags, extractResources, renderHeadTags, attachPdfFile, enrichCurrent, updateNoteActions, visiblePapers, renderList, openTask, saveWorkflow, refreshWorkflow, renderWorkflow, scheduleSave, flushSave, openPaper, fetchFigures, generateNotes, renderPages, clearPdf, rerenderPdf, exportWord, illustrateNotes, changeSupplements, exportMarkdown, loadPdf, goToPage, paintVisiblePages, currentPage, rememberReadingPosition, flushReadingPositions, switchDocument, showFigureSource, reextractFigures, submitCrop };'), context);
  const app = context.app;
  app.state.current = { id: A, title: 'Synthetic paper', notes: '', tags: [] };
  $('head-tags').dataset.paperId = A;
  app.state.settings = { has_api_key: true, model: 'test' };
  app.api.figures = async () => [];
  app.api.illustrate = async (id, notes) => ({ notes });
  app.api.prepareExport = (id, notes) => app.api.illustrate(id, notes);
  app.api.saveReading = async () => ({});
  app.api.autoTags = async id => ({ paper: { id, tags: app.state.current?.id === id ? app.state.current.tags || [] : [] }, added_tags: [] });
  app.api.extractResources = async id => ({ paper: { id, resources_count: 0, resources_pending: 0, tasks: [] }, resources: [], added_count: 0, skipped_count: 0 });
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

test('rapid tag additions and removals serialize from the latest saved tags', async () => {
  const h = harness(), first = deferred(), writes = [];
  h.state.current.tags = ['original'];
  h.api.patch = async (id, body) => {
    writes.push([...body.tags]);
    if (writes.length === 1) await first.promise;
    return { id, tags: body.tags };
  };
  const one = h.addTag('first'), two = h.addTag('second'), three = h.removeTag('original');
  await new Promise(setImmediate);
  assert.deepEqual(writes, [['original', 'first']]);
  first.resolve();
  await Promise.all([one, two, three]);
  assert.deepEqual(writes, [['original', 'first'], ['original', 'first', 'second'], ['first', 'second']]);
  assert.deepEqual([...h.state.current.tags], ['first', 'second']);
  assert.equal(h.state.tagWrites.size, 0);
});

test('a delayed tag response updates its paper without changing the newly opened paper', async () => {
  const h = harness(), response = deferred();
  const paperA = h.state.current;
  h.state.papers = [{ ...paperA }, { id: B, title: 'Paper B', tags: ['B only'] }];
  h.api.patch = () => response.promise;
  const adding = h.addTag('A tag');
  await new Promise(setImmediate);
  h.state.current = { id: B, title: 'Paper B', notes: 'B note', tags: ['B only'] };
  h.$('head-tags').innerHTML = 'B header';
  response.resolve({ id: A, tags: ['A tag'] });
  await adding;
  assert.deepEqual([...h.state.current.tags], ['B only']);
  assert.equal(h.$('head-tags').innerHTML, 'B header');
  assert.deepEqual([...paperA.tags], ['A tag']);
  assert.deepEqual([...h.state.papers.find(p => p.id === A).tags], ['A tag']);
});

test('tag responses merge only tags and preserve newer metadata and input', async () => {
  const h = harness(), response = deferred();
  h.state.papers = [{ ...h.state.current, favorite: true, notes_updated_at: 'latest' }];
  h.api.patch = () => response.promise;
  const adding = h.addTag('topic');
  await new Promise(setImmediate);
  h.state.current.notes = 'newer note';
  h.state.current.favorite = true;
  h.$('tag-input').value = 'next draft';
  response.resolve({ id: A, tags: ['topic'], notes: 'old note', favorite: false, notes_updated_at: 'old' });
  await adding;
  assert.equal(h.state.current.notes, 'newer note');
  assert.equal(h.state.current.favorite, true);
  assert.equal(h.state.papers[0].favorite, true);
  assert.equal(h.state.papers[0].notes_updated_at, 'latest');
  assert.equal(h.$('tag-input').value, 'next draft');
});

test('a failed tag save keeps the draft and does not block the next queued operation', async () => {
  const h = harness(); let count = 0;
  h.api.patch = async (id, body) => { if (++count === 1) throw new Error('offline'); return { id, tags: body.tags }; };
  const failed = h.addTag('retry me'), next = h.addTag('saved');
  assert.equal(await failed, false);
  assert.equal(await next, true);
  assert.deepEqual([...h.state.current.tags], ['saved']);
  assert.equal(h.$('tag-input').value, 'retry me');
  assert.equal(h.state.tagWrites.size, 0);
});

test('completed deep and quick notes start automatic tags without waiting for recognition', async () => {
  for (const mode of ['deep', 'quick']) {
    const h = harness(), response = deferred(), calls = [];
    h.$('read-mode').value = mode;
    h.api.autoTags = id => { calls.push(id); return response.promise; };
    stream(h, [{ type: 'done', notes: 'saved ' + mode, paper: { id: A, tags: ['manual'], tags_revision: 1 } }]);
    await h.generateNotes();
    assert.equal(h.state.generating, null);
    assert.equal(h.$('save-status').textContent, '已保存');
    assert.equal(h.$('editor').value, 'saved ' + mode);
    assert.deepEqual(calls, [A]);
    assert.match(h.$('head-tags').innerHTML, /正在识别/);
    const identifying = h.state.autoTagging.get(A);
    response.resolve({ paper: { id: A, tags: ['manual', 'RNA-seq'], tags_revision: 2 }, added_tags: ['RNA-seq'] });
    assert.equal(await identifying, true);
    assert.deepEqual([...h.state.current.tags], ['manual', 'RNA-seq']);
    assert.equal(h.state.current.tags_revision, 2);
    assert.equal(h.$('editor').value, 'saved ' + mode);
    assert.equal(h.state.autoTagging.size, 0);
  }
});

test('automatic tag failures leave successful notes saved and offer a separate retry', async () => {
  const h = harness(), response = deferred();
  h.api.autoTags = () => response.promise;
  stream(h, [{ type: 'done', notes: 'saved note' }]);
  await h.generateNotes();
  const identifying = h.state.autoTagging.get(A);
  response.reject(Object.assign(new Error('记录已变化，请重试'), { status: 409 }));
  assert.equal(await identifying, false);
  assert.equal(h.$('save-status').textContent, '已保存');
  assert.equal(h.state.current.notes, 'saved note');
  assert.equal(h.$('editor').value, 'saved note');
  assert.equal(h.state.pendingSave, null);
  assert.match(h.$('toast').textContent, /笔记已保存.*识别标签.*重试/);
  assert.doesNotMatch(h.$('head-tags').innerHTML, /正在识别/);
});

test('partial and stopped generations never request automatic tags', async () => {
  for (const ending of [undefined, Object.assign(new Error('stopped'), { name: 'AbortError' })]) {
    const h = harness(); let calls = 0;
    h.api.autoTags = async () => { calls++; throw new Error('unexpected recognition'); };
    h.api.patch = async (id, body) => ({ id, ...body });
    stream(h, [{ type: 'chunk', text: 'partial note' }], ending);
    await h.generateNotes();
    assert.equal(calls, 0);
    assert.equal(h.state.current.notes_incomplete, true);
  }
});

test('automatic tags deduplicate requests and update only their paper after switching', async () => {
  const h = harness(), response = deferred(); let calls = 0;
  const paperA = h.state.current;
  h.state.papers = [{ ...paperA }, { id: B, tags: ['B only'] }];
  h.api.autoTags = () => { calls++; return response.promise; };
  const identifying = h.autoTags();
  assert.equal(h.autoTags(), identifying);
  await new Promise(setImmediate);
  h.state.current = { id: B, tags: ['B only'], notes: 'B note' };
  h.renderHeadTags();
  assert.doesNotMatch(h.$('head-tags').innerHTML, /正在识别/);
  h.state.current = paperA; h.renderHeadTags();
  assert.match(h.$('head-tags').innerHTML, /正在识别/);
  h.state.current = { id: B, tags: ['B only'], notes: 'B note' }; h.renderHeadTags();
  response.resolve({ paper: { id: A, tags: ['cell atlas'], tags_revision: 1, notes: 'old A', favorite: false }, added_tags: ['cell atlas'] });
  await identifying;
  assert.equal(calls, 1);
  assert.deepEqual([...h.state.current.tags], ['B only']);
  assert.equal(h.state.current.notes, 'B note');
  assert.deepEqual([...h.state.papers[0].tags], ['cell atlas']);
  assert.doesNotMatch(h.$('head-tags').innerHTML, /cell atlas/);
});

test('automatic and manual tag changes share a queue and retain both additions', async () => {
  const h = harness(), first = deferred(), automatic = deferred(), writes = [];
  h.api.patch = async (id, body) => {
    writes.push([...body.tags]);
    if (writes.length === 1) await first.promise;
    return { id, tags: body.tags, tags_revision: writes.length === 1 ? 1 : 3 };
  };
  let autoCalls = 0;
  h.api.autoTags = () => { autoCalls++; return automatic.promise; };
  const manualFirst = h.addTag('before'), identifying = h.autoTags(), manualLast = h.addTag('after');
  await new Promise(setImmediate);
  assert.equal(autoCalls, 0);
  first.resolve(); await manualFirst; await new Promise(setImmediate);
  assert.equal(autoCalls, 1);
  assert.deepEqual(writes, [['before']]);
  automatic.resolve({ paper: { id: A, tags: ['before', 'inferred'], tags_revision: 2 }, added_tags: ['inferred'] });
  await identifying; await manualLast;
  assert.deepEqual(writes, [['before'], ['before', 'inferred', 'after']]);
  assert.deepEqual([...h.state.current.tags], ['before', 'inferred', 'after']);
});

test('manual tag recognition without a key opens settings without changing notes', async () => {
  const h = harness(); let calls = 0;
  h.state.settings = { has_api_key: false };
  h.state.current.notes = 'existing note';
  h.$('settings-modal').hidden = true;
  h.api.autoTags = async () => { calls++; };
  assert.equal(await h.autoTags(), false);
  assert.equal(calls, 0);
  assert.equal(h.state.current.notes, 'existing note');
  assert.equal(h.$('settings-modal').hidden, false);
  assert.match(h.$('toast').textContent, /API Key/);
});

test('an older generation metadata revision cannot undo newer manual tags', async () => {
  const h = harness();
  h.state.current.tags = ['new manual']; h.state.current.tags_revision = 3;
  stream(h, [{ type: 'done', notes: 'saved note', paper: { id: A, tags: ['old tag'], tags_revision: 2 } }]);
  await h.generateNotes();
  await h.state.autoTagging.get(A);
  assert.deepEqual([...h.state.current.tags], ['new manual']);
  assert.equal(h.state.current.tags_revision, 3);
});

test('manual recognition waits for note saving and skips recognition if saving fails', async () => {
  const h = harness(), saving = deferred(), calls = [];
  h.api.patch = () => saving.promise;
  h.api.autoTags = async id => { calls.push(id); return { paper: { id, tags: ['saved text'] }, added_tags: ['saved text'] }; };
  edit(h, 'latest note');
  const identifying = h.autoTags();
  await new Promise(setImmediate);
  assert.deepEqual(calls, []);
  saving.resolve({ id: A, notes_updated_at: 'now' });
  assert.equal(await identifying, true);
  assert.deepEqual(calls, [A]);
  assert.equal(h.state.pendingSave, null);

  h.api.patch = async () => { throw new Error('offline'); };
  edit(h, 'keep this unsaved draft');
  assert.equal(await h.autoTags(), false);
  assert.deepEqual(calls, [A]);
  assert.equal(h.$('editor').value, 'keep this unsaved draft');
  assert.equal(h.state.pendingSave.notes, 'keep this unsaved draft');
  assert.match(h.$('toast').textContent, /笔记尚未保存.*暂停识别/);
});

test('a delayed note-save summary cannot replace newer recognized tags in the library', async () => {
  const h = harness(), saving = deferred();
  h.state.papers = [{ ...h.state.current, tags: [], tags_revision: 0 }];
  h.api.patch = () => saving.promise;
  edit(h, 'new note'); const flushing = h.flushSave();
  h.api.autoTags = async id => ({ paper: { id, tags: ['new topic'], tags_revision: 1 }, added_tags: ['new topic'] });
  await h.autoTags(h.state.current, true);
  saving.resolve({ id: A, notes_updated_at: 'now', tags: [], tags_revision: 0 });
  await flushing;
  assert.deepEqual([...h.state.papers[0].tags], ['new topic']);
  assert.equal(h.state.papers[0].tags_revision, 1);
});

test('a delayed paper-open response cannot undo tags recognized while it was loading', async () => {
  const h = harness(), opening = deferred(), recognition = deferred();
  const paperA = h.state.current;
  h.state.papers = [{ ...paperA, tags_revision: 0 }];
  h.api.autoTags = () => recognition.promise;
  const identifying = h.autoTags();
  await new Promise(setImmediate);
  h.state.current = { id: B, notes: '', tags: ['B'] };
  h.api.paper = () => opening.promise;
  h.state.pendingPaper = { id: A }; const switching = h.openPaper();
  await new Promise(setImmediate);
  recognition.resolve({ paper: { id: A, tags: ['latest topic'], tags_revision: 1 }, added_tags: ['latest topic'] });
  await identifying;
  opening.resolve({ id: A, notes: '', has_file: false, tags: [], tags_revision: 0 });
  await switching;
  assert.equal(h.state.current.id, A);
  assert.deepEqual([...h.state.current.tags], ['latest topic']);
  assert.equal(h.state.current.tags_revision, 1);
});

test('newly completed notes refresh an older recognition once and coalesce repeated completions', async () => {
  const h = harness(), old = deferred(), fresh = deferred(); let calls = 0;
  h.api.autoTags = () => ++calls === 1 ? old.promise : fresh.promise;
  const identifying = h.autoTags();
  await new Promise(setImmediate);
  for (const note of ['first completed note', 'latest completed note']) {
    stream(h, [{ type: 'done', notes: note }]);
    await h.generateNotes();
    assert.equal(h.$('save-status').textContent, '已保存');
    assert.equal(h.state.generating, null);
  }
  assert.equal(calls, 1);
  old.reject(Object.assign(new Error('记录已变化'), { status: 409 }));
  await new Promise(setImmediate);
  assert.equal(calls, 2);
  assert.match(h.$('head-tags').innerHTML, /正在识别/);
  assert.doesNotMatch(h.$('toast').textContent, /失败/);
  fresh.resolve({ paper: { id: A, tags: ['latest topic'], tags_revision: 1 }, added_tags: ['latest topic'] });
  assert.equal(await identifying, true);
  assert.deepEqual([...h.state.current.tags], ['latest topic']);
  assert.equal(h.state.current.notes, 'latest completed note');
  assert.equal(h.state.autoTagging.size, 0);
  assert.equal(h.state.autoTagRefresh.size, 0);
});

test('late material and metadata responses preserve newer recognized tags', async () => {
  for (const kind of ['attach', 'enrich', 'supplement', 'removeSupplement']) {
    const h = harness(), response = deferred();
    h.state.current.supplements = [{ id: C, filename: 'supplement.pdf', pages: 0 }];
    h.state.papers = [{ ...h.state.current, tags_revision: 0 }];
    h.api[kind] = () => response.promise;
    let changing;
    if (kind === 'attach') changing = h.attachPdfFile({ name: 'new.pdf' });
    else if (kind === 'enrich') changing = h.enrichCurrent();
    else {
      if (kind === 'removeSupplement') h.state.documentId = C;
      changing = h.changeSupplements(kind === 'supplement' ? [{ name: 'supp.pdf' }] : [], kind === 'removeSupplement');
    }
    await new Promise(setImmediate);
    h.api.autoTags = async id => ({ paper: { id, tags: ['latest topic'], tags_revision: 1 }, added_tags: ['latest topic'] });
    await h.autoTags(h.state.current, true);
    response.resolve({ id: A, title: 'Updated metadata', notes: '', tags: [], tags_revision: 0, has_file: false,
      supplements: kind === 'removeSupplement' ? [] : [{ id: C, filename: 'supplement.pdf', pages: 0 }] });
    await changing;
    assert.deepEqual([...h.state.current.tags], ['latest topic'], kind);
    assert.equal(h.state.current.tags_revision, 1, kind);
    assert.deepEqual([...h.state.papers[0].tags], ['latest topic'], kind);
  }
});

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

test('library filters intersect favorites, tags, actionable tasks and search', () => {
  const h = harness();
  h.state.papers = [
    { id: A, title: 'Cancer atlas', favorite: true, tags: ['RNA-seq'], tasks: [{ id: 'evidence' }] },
    { id: B, title: 'Legacy paper', reading_status: 'reading', reading_decision: 'pending', tasks: [{ id: 'decision' }, { id: 'resume' }] },
    { id: C, title: 'Cancer methods', favorite: false, tags: ['RNA-seq'], tasks: [{ id: 'fulltext' }] },
  ];
  h.state.taskFilter = 'any';
  assert.deepEqual(Array.from(h.visiblePapers(), p => p.id), [A, C]);
  h.state.favOnly = true; h.state.activeTag = 'RNA-seq'; h.state.taskFilter = 'evidence'; h.state.query = 'cancer';
  assert.deepEqual(Array.from(h.visiblePapers(), p => p.id), [A]);
  h.state.query = 'absent'; assert.equal(h.visiblePapers().length, 0);
});

test('legacy reading fields produce no visible badges or empty task strip', () => {
  const h = harness();
  Object.assign(h.state.current, { reading_status: 'reading', reading_decision: 'pending', tasks: [{ id: 'decision' }, { id: 'resume' }] });
  h.state.papers = [h.state.current];
  h.renderList(); h.renderWorkflow();
  assert.doesNotMatch(h.$('paper-list').innerHTML, /精读中|待判断|判断阅读价值|继续精读/);
  assert.equal(h.$('task-total').textContent, '0 篇');
  assert.equal(h.$('paper-workflow').hidden, true);
  h.state.current.notes_incomplete = true;
  h.state.current.tasks.push({ id: 'notes_incomplete' });
  h.renderWorkflow();
  assert.equal(h.$('paper-workflow').hidden, false);
  assert.equal(h.$('notes-complete').hidden, false);
  assert.match(h.$('paper-tasks').innerHTML, /补完笔记/);
});

test('note review save waits for persistence and does not replace a new draft', async () => {
  const h = harness(), request = deferred(), writes = [];
  h.api.patch = async (id, body) => {
    writes.push(body);
    if (body.notes) await request.promise;
    return { id, ...body, notes: 'server snapshot', tasks: [] };
  };
  edit(h, 'keep draft');
  const save = h.saveWorkflow({ notes_incomplete: false });
  assert.equal(writes.length, 1); assert.equal(writes[0].notes, 'keep draft');
  request.resolve(); await save;
  assert.equal(h.state.current.notes_incomplete, false);
  assert.equal(h.$('editor').value, 'keep draft');
  assert.equal(h.state.current.notes, 'keep draft');
  assert.equal(writes[1].notes_incomplete, false);
});

test('failed note review retains its reminder and a late response cannot modify another paper', async () => {
  const h = harness(); h.state.current.notes_incomplete = true;
  h.api.patch = async () => { throw new Error('offline'); };
  await h.saveWorkflow({ notes_incomplete: false });
  assert.equal(h.state.current.notes_incomplete, true);
  assert.equal(h.$('notes-complete').hidden, false);
  const request = deferred(); h.api.patch = () => request.promise;
  const save = h.saveWorkflow({ notes_incomplete: false }); await new Promise(setImmediate);
  h.state.current = { id: B, notes_incomplete: true };
  request.resolve({ id: A, notes_incomplete: false, tasks: [] }); await save;
  assert.equal(h.state.current.notes_incomplete, true);
});

test('late workflow refresh cannot restore a resolved note reminder', async () => {
  const h = harness(), request = deferred();
  h.api.paper = () => request.promise;
  const refresh = h.refreshWorkflow(A);
  h.api.patch = async (id, body) => ({ id, ...body, tasks: [] });
  await h.saveWorkflow({ notes_incomplete: false });
  request.resolve({ id: A, notes_incomplete: true }); await refresh;
  assert.equal(h.state.current.notes_incomplete, false);
});

test('saved deep notes extract resources independently of automatic tag failures', async () => {
  const h = harness(), response = deferred(); let calls = 0;
  h.api.autoTags = async () => { throw new Error('tag timeout'); };
  h.api.extractResources = () => { calls++; return response.promise; };
  stream(h, [{ type: 'done', notes: 'Data availability: GSE12345', paper: { id: A, notes_stale: false } }]);
  await h.generateNotes();
  assert.equal(calls, 1);
  assert.equal(h.$('save-status').textContent, '已保存');
  assert.equal(h.state.generating, null);
  const extracting = h.state.extractingResources.get(A);
  response.resolve({ paper: { id: A, resources_count: 1, resources_pending: 1, tasks: [{ id: 'resources' }] }, resources: [{ accession: 'GSE12345' }], added_count: 1 });
  assert.equal(await extracting, true);
  assert.equal(h.state.current.resources_count, 1);
  assert.equal(h.state.current.notes, 'Data availability: GSE12345');
  assert.equal(h.$('save-status').textContent, '已保存');
});

test('quick, incomplete, stopped and stale notes never automatically extract resources', async () => {
  for (const scenario of ['quick', 'partial', 'stopped', 'stale']) {
    const h = harness(); let calls = 0;
    h.api.extractResources = async () => { calls++; throw new Error('unexpected extraction'); };
    h.api.patch = async (id, body) => ({ id, ...body });
    if (scenario === 'quick') h.$('read-mode').value = 'quick';
    const events = scenario === 'partial' || scenario === 'stopped' ? [{ type: 'chunk', text: 'Partial note' }]
      : [{ type: 'done', notes: 'Saved note', paper: { notes_stale: scenario === 'stale' } }];
    stream(h, events, scenario === 'stopped' ? Object.assign(new Error('stop'), { name: 'AbortError' }) : undefined);
    await h.generateNotes();
    assert.equal(calls, 0, scenario);
  }
});

test('resource extraction merges only its fields and never replaces another paper or clears a draft', async () => {
  const h = harness(), response = deferred(), refreshed = [];
  const paperA = h.state.current;
  paperA.notes = 'new note'; paperA.tags = ['manual']; paperA.favorite = true; paperA.tasks = [{ id: 'evidence' }];
  h.state.papers = [{ ...paperA }]; h.state.tab = 'resources';
  h.context.window.BioResources = { refreshAfterExtraction: async id => refreshed.push(id), clear: () => { throw new Error('draft must not be cleared'); } };
  h.api.extractResources = () => response.promise;
  const extracting = h.extractResources();
  await new Promise(setImmediate);
  h.state.current = { id: B, notes: 'B note', tags: ['B'], favorite: true, resources_count: 9 };
  response.resolve({ paper: { id: A, notes: 'old', tags: ['old'], favorite: false, resources_count: 2, resources_pending: 2, tasks: [{ id: 'resources' }, { id: 'notes_stale' }] } });
  assert.equal(await extracting, true);
  assert.equal(h.state.current.notes, 'B note');
  assert.equal(h.state.current.resources_count, 9);
  assert.equal(paperA.notes, 'new note');
  assert.deepEqual(paperA.tags, ['manual']);
  assert.equal(paperA.favorite, true);
  assert.equal(paperA.resources_count, 2);
  assert.deepEqual([...paperA.tasks].map(t => t.id), ['evidence', 'resources']);
  assert.deepEqual(refreshed, [A]);
});

test('resource extraction failure leaves notes saved and permits an independent manual retry', async () => {
  const h = harness(), response = deferred();
  h.api.extractResources = () => response.promise;
  stream(h, [{ type: 'done', notes: 'Saved deep note' }]);
  await h.generateNotes();
  const extracting = h.state.extractingResources.get(A);
  response.reject(new Error('storage unavailable'));
  assert.equal(await extracting, false);
  assert.equal(h.state.current.notes, 'Saved deep note');
  assert.equal(h.$('save-status').textContent, '已保存');
  assert.equal(h.state.pendingSave, null);
  assert.match(h.$('toast').textContent, /笔记已保存.*数据与复现.*重试/);
  h.api.extractResources = async id => ({ paper: { id, resources_count: 1, resources_pending: 1, tasks: [{ id: 'resources' }] } });
  assert.equal(await h.extractResources(), true);
  assert.equal(h.state.current.resources_count, 1);
});

test('automatic tag and resource success cannot hide the other operation failure', async () => {
  for (const failed of ['tags', 'resources']) {
    const h = harness(), tags = deferred(), resources = deferred();
    h.api.autoTags = () => tags.promise;
    h.api.extractResources = () => resources.promise;
    stream(h, [{ type: 'done', notes: 'Saved complete deep note' }]);
    await h.generateNotes();
    const tagging = h.state.autoTagging.get(A), extracting = h.state.extractingResources.get(A);
    if (failed === 'tags') {
      tags.reject(new Error('tag service unavailable'));
      await tagging;
    } else {
      resources.reject(new Error('resource storage unavailable'));
      await extracting;
    }
    const failure = h.$('toast').textContent;
    assert.match(failure, /笔记已保存.*失败/);
    if (failed === 'tags') {
      resources.resolve({ paper: { id: A, resources_count: 1, resources_pending: 1, tasks: [{ id: 'resources' }] } });
      await extracting;
    } else {
      tags.resolve({ paper: { id: A, tags: ['topic'], tags_revision: 1 }, added_tags: ['topic'] });
      await tagging;
    }
    assert.equal(h.$('toast').textContent, failure, failed);
    assert.equal(h.$('save-status').textContent, '已保存');
    assert.equal(h.state.current.notes, 'Saved complete deep note');
  }
});

test('resource extraction coalesces repeated completions and refreshes changed workflow authoritatively', async () => {
  const h = harness(), old = deferred(), latest = deferred(); let calls = 0, reads = 0;
  Object.assign(h.state.current, { notes_mode: 'deep', notes: 'latest deep note', notes_incomplete: false });
  h.api.extractResources = () => ++calls === 1 ? old.promise : latest.promise;
  const extracting = h.extractResources();
  assert.equal(h.extractResources(h.state.current, { automatic: true }), extracting);
  assert.equal(h.extractResources(h.state.current, { automatic: true }), extracting);
  await new Promise(setImmediate);
  old.reject(Object.assign(new Error('notes changed'), { status: 409 }));
  await new Promise(setImmediate);
  assert.equal(calls, 2);
  h.state.workflowVersion++;
  h.api.paper = async id => { reads++; return { id, notes_incomplete: false, resources_count: 3, resources_pending: 3, tasks: [{ id: 'resources' }] }; };
  latest.resolve({ paper: { id: A, notes_incomplete: true, resources_count: 1, resources_pending: 1, tasks: [] } });
  assert.equal(await extracting, true);
  assert.equal(reads, 1);
  assert.equal(h.state.current.notes_incomplete, false);
  assert.equal(h.state.current.resources_count, 3);
  assert.equal(h.state.extractingResources.size, 0);
});

test('manual resource extraction waits for note saving and keeps a failed-save draft', async () => {
  const h = harness(), saved = deferred(); let calls = 0;
  h.api.patch = () => saved.promise;
  h.api.extractResources = async id => { calls++; return { paper: { id, resources_count: 0, tasks: [] } }; };
  edit(h, 'latest editable note');
  const extracting = h.extractResources();
  await new Promise(setImmediate);
  assert.equal(calls, 0);
  saved.resolve({ id: A });
  assert.equal(await extracting, true);
  assert.equal(calls, 1);
  h.api.patch = async () => { throw new Error('save offline'); };
  edit(h, 'keep draft');
  assert.equal(await h.extractResources(), false);
  assert.equal(calls, 1);
  assert.equal(h.$('editor').value, 'keep draft');
  assert.equal(h.state.pendingSave.notes, 'keep draft');
});

test('partial-generation marker survives retry edits without changing favorites', async () => {
  const h = harness(); let body;
  h.state.current.favorite = true;
  h.api.patch = async (id, value) => { body = value; return { id, ...value, tasks: [{ id: 'notes_incomplete' }] }; };
  stream(h, [{ type: 'chunk', text: 'Partial text' }]);
  await h.generateNotes();
  assert.equal(body.notes_incomplete, true);
  edit(h, 'Partial text plus manual edit'); await h.flushSave();
  assert.equal(body.notes_incomplete, true);
  assert.equal(h.state.current.favorite, true);
});

test('generation uses authoritative material freshness from done event', async () => {
  const h = harness(); h.state.current.favorite = true;
  stream(h, [{ type: 'done', notes: 'Finished report', paper: { notes_incomplete: false, notes_stale: true, tasks: [{ id: 'notes_stale' }] } }]);
  await h.generateNotes();
  assert.equal(h.state.current.notes_stale, true);
  assert.equal(h.state.current.tasks[0].id, 'notes_stale');
  assert.equal(h.state.current.favorite, true);
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
    return { notes: '<!-- bioreader-research-appendix:v1 sha256=' + 'a'.repeat(64) + ' -->\n## 数据与复现\n\n- 与本文关系：复用已有数据\n- 名称：a\\_b &amp; c\n- 链接：[打开资源](https://example.org/data)\n<!-- /bioreader-research-appendix:v1 -->\n' };
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
