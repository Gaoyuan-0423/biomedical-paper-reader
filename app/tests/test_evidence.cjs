// Exercise the delivered evidence panel with delayed APIs and DOM event delegation.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../frontend/evidence.js'), 'utf8');
const A = 'a'.repeat(32), B = 'b'.repeat(32), E = 'e'.repeat(32), S = 'f'.repeat(32);
const paper = id => ({ id, has_file: true, pages: 12, supplements: [{ id: S, filename: 'Supplement.pdf', pages: 5 }] });
const evidence = (extra = {}) => ({ id: E, claim: 'Functional claim', quote: 'Original passage', document_id: S, page: 3, figure_id: '', status: 'pending', source_available: true, ...extra });
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function harness() {
  const handlers = {}, calls = [], messages = [], changed = [], jumps = [];
  const panel = { innerHTML: '', addEventListener(name, fn) { handlers[name] = fn; }, querySelector: () => ({ focus() {} }) };
  const hooks = {
    request: async (method, url, body) => { calls.push({ method, url, body }); return method === 'GET' ? [] : evidence(body); },
    toast: (...args) => messages.push(args), onChanged: id => changed.push(id),
    jump: (...args) => jumps.push(args),
  };
  const context = vm.createContext({ window: { confirm: () => true }, document: { getElementById: () => panel }, console });
  vm.runInContext(source, context);
  const ui = context.window.BioEvidence;
  ui.init(hooks);
  return {
    ui, hooks, panel, calls, messages, changed, jumps,
    click(action, id) { return handlers.click({ target: { closest: () => ({ dataset: { evidenceAction: action, evidenceId: id } }) } }); },
    input(field, value) { return handlers.input({ target: { dataset: { evidenceField: field }, value } }); },
    field(field, value) { return handlers.change({ target: { dataset: { evidenceField: field }, value } }); },
    status(id, value) { return handlers.change({ target: { dataset: { evidenceStatus: id }, value } }); },
    submit() { return handlers.submit({ target: { id: 'evidence-form' }, preventDefault() {} }); },
  };
}

test('late evidence fetch cannot render a previously selected paper', async () => {
  const h = harness(), old = deferred();
  h.hooks.request = async (method, url) => url.includes(A) ? old.promise : [evidence({ claim: 'Current paper' })];
  const first = h.ui.open(paper(A));
  await h.ui.open(paper(B));
  old.resolve([evidence({ claim: 'Obsolete paper' })]); await first;
  assert.match(h.panel.innerHTML, /Current paper/);
  assert.doesNotMatch(h.panel.innerHTML, /Obsolete paper/);
});

test('capture during initial loading survives the response and repeated open', async () => {
  const h = harness(), read = deferred();
  h.hooks.request = () => read.promise;
  const loading = h.ui.open(paper(A));
  h.ui.start({ claim: 'Selected report text', document_id: S, page: 3 });
  assert.equal(h.ui.canLeave(), false);
  read.resolve([]); await loading;
  assert.match(h.panel.innerHTML, /Selected report text/);
  h.input('quote', 'Evidence still being typed');
  await h.ui.open(paper(A));
  // Rendering a source change must still retain both fields in the active draft.
  await h.field('document_id', 'main');
  assert.match(h.panel.innerHTML, /Selected report text/);
  assert.match(h.panel.innerHTML, /Evidence still being typed/);
});

test('save captures supplementary file and exact figure page, never auto-verifies', async () => {
  const h = harness();
  await h.ui.open(paper(A), [{ id: 'f001', label: 'Fig. S1', document_id: S, page: 3 }]);
  h.ui.start({ claim: '  Finding  ' });
  h.input('quote', 'Quoted observation');
  await h.field('document_id', S);
  await h.field('figure_id', 'f001');
  await h.submit();
  const posted = h.calls.find(c => c.method === 'POST');
  assert.equal(posted.url, '/api/papers/' + A + '/evidence');
  assert.deepEqual(JSON.parse(JSON.stringify(posted.body)), {
    claim: 'Finding', quote: 'Quoted observation', document_id: S, page: 3, figure_id: 'f001', status: 'pending',
  });
  assert.equal(h.ui.canLeave(), true);
  assert.deepEqual(h.changed, [A]);
  assert.match(h.panel.innerHTML, /Fig\. S1/);
});

test('failed writes retain draft, block switching, prevent double submission and can retry', async () => {
  const h = harness(), pending = deferred();
  await h.ui.open(paper(A)); h.ui.start({ claim: 'Keep this draft' });
  let writes = 0;
  h.hooks.request = async () => { writes++; return pending.promise; };
  const saving = h.submit();
  assert.equal(h.ui.canLeave(), false);
  await h.submit(); assert.equal(writes, 1);
  pending.reject(new Error('offline')); await saving;
  assert.match(h.panel.innerHTML, /Keep this draft/);
  assert.match(h.panel.innerHTML, /offline/);
  assert.equal(h.ui.canLeave(), false);
  h.hooks.request = async (_, __, body) => evidence(body);
  await h.submit();
  assert.equal(h.ui.canLeave(), true);
  assert.match(h.panel.innerHTML, /evidence-card/);
});

test('missing evidence sources cannot jump or become verified', async () => {
  const h = harness();
  h.hooks.request = async (method) => { assert.equal(method, 'GET'); return [evidence({ source_available: false })]; };
  await h.ui.open(paper(A));
  assert.match(h.panel.innerHTML, /来源已移除或页码已失效/);
  await h.click('jump', E);
  await h.status(E, 'verified');
  assert.equal(h.jumps.length, 0);
  assert.match(h.panel.innerHTML, /value="verified" disabled/);
});

test('changed source shows pending warning and jumps into current source before reverification', async () => {
  const h = harness();
  h.hooks.request = async method => method === 'GET' ? [evidence({ source_changed: true, status: 'verified' })] : evidence({ status: 'verified', source_changed: false });
  await h.ui.open(paper(A));
  assert.match(h.panel.innerHTML, /来源文件已更新/);
  assert.match(h.panel.innerHTML, /value="pending" selected/);
  await h.click('jump', E);
  assert.deepEqual(h.jumps, [[S, 3]]);
  await h.status(E, 'verified');
  assert.doesNotMatch(h.panel.innerHTML, /来源文件已更新/);
  assert.match(h.panel.innerHTML, /value="verified" selected/);
});

test('failed status updates keep authoritative status and report failure', async () => {
  const h = harness();
  h.hooks.request = async method => { if (method === 'GET') return [evidence()]; throw new Error('offline'); };
  await h.ui.open(paper(A));
  await h.status(E, 'verified');
  assert.match(h.panel.innerHTML, /value="pending" selected/);
  assert.match(h.panel.innerHTML, /保存失败/);
});

test('late mutation result updates counts for its own paper without contaminating a new paper', async () => {
  const h = harness(), write = deferred();
  await h.ui.open(paper(A)); h.ui.start({ claim: 'Old capture' });
  h.hooks.request = async method => method === 'GET' ? [evidence({ claim: 'New paper card' })] : write.promise;
  const saving = h.submit();
  h.ui.clear(); await h.ui.open(paper(B));
  write.resolve(evidence({ claim: 'Old capture' })); await saving;
  assert.match(h.panel.innerHTML, /New paper card/);
  assert.doesNotMatch(h.panel.innerHTML, /Old capture/);
  assert.deepEqual(h.changed, [A]);
});

test('empty form permits switching; typed draft cannot be overwritten by a second capture', async () => {
  const h = harness(); await h.ui.open(paper(A)); h.ui.start();
  assert.equal(h.ui.canLeave(), true);
  h.input('claim', 'First claim');
  h.ui.start({ claim: 'Second claim' });
  assert.equal(h.ui.canLeave(), false);
  await h.field('document_id', S);
  assert.match(h.panel.innerHTML, /First claim/);
  assert.doesNotMatch(h.panel.innerHTML, /Second claim/);
  await h.click('cancel');
  assert.equal(h.ui.canLeave(), true);
});

test('invalid pages and figure-page mismatch are rejected before writing', async () => {
  const h = harness(); await h.ui.open(paper(A), [{ id: 'f001', page: 3, document_id: S }]);
  h.ui.start({ claim: 'Finding', document_id: S, page: 6 });
  await h.submit(); assert.equal(h.calls.length, 1);
  await h.field('figure_id', 'f001');
  h.input('page', '2'); await h.submit();
  assert.equal(h.calls.length, 1);
  assert.match(h.panel.innerHTML, /关联图表与文件或页码不一致/);
});

test('evidence text and filenames render as text instead of markup', async () => {
  const h = harness();
  h.hooks.request = async () => [evidence({ claim: '<script>alert(1)</script>', quote: '<img src=x onerror=alert(1)>', source_name: '" onclick="boom' })];
  await h.ui.open(paper(A));
  assert.doesNotMatch(h.panel.innerHTML, /<script>|<img src=x|" onclick="boom/);
  assert.match(h.panel.innerHTML, /&lt;script&gt;/);
  assert.match(h.panel.innerHTML, /&lt;img/);
});
