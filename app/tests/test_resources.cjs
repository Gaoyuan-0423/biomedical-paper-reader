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
  assert.equal(h.ui.canLeave(), true); assert.match(h.panel.innerHTML, /resource-row/);
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

test('manual extraction shares the app helper, shows busy state, and reloads saved records once complete', async () => {
  const h = harness(), pending = deferred();
  const p = { ...paper(), notes: '# 精读\n\n数据编号 GSE12345', notes_mode: 'deep', notes_incomplete: false, notes_stale: false };
  await h.ui.open(p); let calls = 0;
  h.hooks.extract = async selected => { calls++; assert.equal(selected.id, A); await pending.promise; await h.ui.refreshAfterExtraction(A); return true; };
  h.hooks.request = async () => [record({ notes: '从精读笔记自动整理，待核对。' })];
  const extracting = h.click('extract'); await h.click('extract');
  assert.equal(calls, 1); assert.match(h.panel.innerHTML, /正在整理…/); assert.equal(h.ui.canLeave(), false);
  pending.resolve(); await extracting;
  assert.match(h.panel.innerHTML, /GSE12345/); assert.match(h.panel.innerHTML, /从精读笔记自动整理/);
  assert.equal(h.ui.canLeave(), true); assert.equal(p.notes, '# 精读\n\n数据编号 GSE12345');
});

test('failed extraction preserves records and can retry without overwriting them', async () => {
  const h = harness(); h.hooks.request = async () => [record({ title: 'Manually verified record', status: 'verified' })];
  await h.ui.open({ ...paper(), notes: '# 精读', notes_mode: 'deep' });
  h.hooks.extract = async () => false; await h.click('extract');
  assert.match(h.panel.innerHTML, /Manually verified record/); assert.match(h.panel.innerHTML, /原有资源仍保留/);
  assert.equal(h.ui.canLeave(), true);
  h.hooks.extract = async () => true; await h.click('extract');
  assert.doesNotMatch(h.panel.innerHTML, /本次未完成整理/); assert.match(h.panel.innerHTML, /Manually verified record/);
});

test('quick, incomplete, stale or missing notes do not invoke extraction', async () => {
  const h = harness(); let calls = 0; h.hooks.extract = async () => { calls++; return true; };
  for (const extra of [{ notes: '' }, { notes_mode: 'quick' }, { notes_incomplete: true }, { notes_stale: true }]) {
    await h.ui.open({ ...paper(), notes: '# 精读', notes_mode: 'deep', ...extra }); await h.click('extract');
    assert.match(h.panel.innerHTML, /data-resource-action="extract"[^>]+ disabled/);
  }
  assert.equal(calls, 0);
});

test('automatic extraction refresh waits for an unsaved resource draft to be resolved', async () => {
  const h = harness(); await h.ui.open(paper()); await h.click('add'); h.input('title', 'Unsaved draft');
  const before = h.calls.length; await h.ui.refreshAfterExtraction(A);
  assert.equal(h.calls.length, before); assert.equal(h.ui.canLeave(), false);
  h.hooks.request = async () => [record({ title: 'Fresh extracted record' })];
  await h.click('cancel');
  assert.match(h.panel.innerHTML, /Fresh extracted record/); assert.equal(h.ui.canLeave(), true);
});

test('late extraction completion cannot render resources into a different paper', async () => {
  const h = harness(), pending = deferred(); await h.ui.open({ ...paper(), notes: '# 精读', notes_mode: 'deep' });
  h.hooks.extract = () => pending.promise; const extracting = h.click('extract');
  h.ui.clear(); h.hooks.request = async () => [record({ title: 'Current paper resource' })]; await h.ui.open(paper(B));
  await h.ui.refreshAfterExtraction(A); pending.resolve(true); await extracting;
  assert.match(h.panel.innerHTML, /Current paper resource/); assert.doesNotMatch(h.panel.innerHTML, /正在整理…/);
});

test('the table preserves grouped identifiers, readable safe links and non-GEO resources as one row each', async () => {
  const h = harness();
  h.hooks.request = async (_, url) => url.endsWith('/geo') ? { records: {} } : [record({ title: 'Validation cohorts', purpose: '比较外部队列', identifiers: '**GSE12345**、GSE67890；[GDC](https://portal.gdc.cancer.gov/)\nTCGA / IMvigor210', conditions: '仅使用已获授权的临床字段', sample_details: '论文使用 12 位患者的肿瘤组织' }), record({ id: S, title: 'Cell line', accession: '', url: '', identifiers: 'MCF-7；培养条件待核对' })];
  await h.ui.open(paper());
  assert.match(h.panel.innerHTML, /<table class="resource-table">/);
  assert.equal((h.panel.innerHTML.match(/class="resource-row"/g) || []).length, 2);
  for (const label of ['用途', '编号／链接', '影响使用的条件', '样品／材料', '比较外部队列', 'TCGA / IMvigor210', '论文使用 12 位患者']) assert.ok(h.panel.innerHTML.includes(label));
  assert.match(h.panel.innerHTML, /href="https:\/\/portal.gdc.cancer.gov\/"/);
  assert.doesNotMatch(h.panel.innerHTML, /\*\*GSE|\[GDC\]|resource-card/);
  assert.equal((h.panel.innerHTML.match(/data-resource-accession="GSE/g) || []).length, 2);
});

test('new table fields survive editing and persist without requiring a URL or accession', async () => {
  const h = harness(); await h.ui.open(paper()); await h.click('add');
  const fields = { title: 'IMvigor210', purpose: '外部验证', identifiers: 'IMvigor210；申请编号未报告', conditions: '需确认队列使用许可', sample_details: '论文筛选后的患者，数量待核对' };
  for (const [name, value] of Object.entries(fields)) h.input(name, value);
  assert.equal(h.ui.canLeave(), false); await h.submit();
  const posted = h.calls.find(c => c.method === 'POST').body;
  for (const [name, value] of Object.entries(fields)) assert.equal(posted[name], value);
  assert.equal(posted.url, ''); assert.equal(posted.accession, '');
  await h.click('edit', { resourceKey: A + ':' + R });
  for (const [name, value] of Object.entries(fields)) assert.ok(h.panel.innerHTML.includes(value), name);
});

test('legacy automatic raw note snippets are hidden in table and edit while manual notes remain available', async () => {
  const h = harness();
  h.hooks.request = async () => [record({ notes: '来自精读笔记自动整理，尚未核对原文。笔记片段：\n| raw-private-marker | GSE12345 |\n## 其他长篇段落' }), record({ id: S, auto_extraction: { version: 2 }, notes: '人工补充：需确认数据授权' })];
  await h.ui.open(null);
  assert.doesNotMatch(h.panel.innerHTML, /raw-private-marker|笔记片段|其他长篇段落/);
  assert.match(h.panel.innerHTML, /人工补充：需确认数据授权/);
  await h.ui.open(paper()); await h.mode('current'); await h.click('edit', { resourceKey: A + ':' + R });
  assert.doesNotMatch(h.panel.innerHTML, /raw-private-marker|笔记片段/);
});

const geo = (extra = {}) => ({ accession: 'GSE12345', title: 'Synthetic GEO collection', source_url: 'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE12345', fetched_at: '2026-11-03', geo_sample_count: 60, samples_total: 60, samples_shown: 1, sample_scope: 'examples', experiment_types: ['Expression profiling'], platforms: ['GPL111'], summary: 'Public collection', overall_design: 'Tumor and normal samples; paper subset unknown', samples: [{ accession: 'GSM123', title: 'Example <script>sample</script>', source_url: 'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM123', organism: ['Homo sapiens'], source: ['tumor tissue'], characteristics: ['donor: example only'], molecule: ['total RNA'], library_strategy: ['RNA-Seq'] }], warnings: [], ...extra });

test('opening resources reads only the GEO cache and clearly distinguishes repository totals and sample examples', async () => {
  const h = harness();
  h.hooks.request = async (method, url) => { h.calls.push({ method, url }); return url.endsWith('/geo') ? { records: { GSE12345: geo() } } : [record({ sample_details: '论文实际使用 12 个样本' })]; };
  await h.ui.open(paper());
  assert.ok(h.calls.every(c => c.method === 'GET'));
  assert.equal(h.calls[1].url, '/api/papers/' + A + '/resources/geo');
  for (const text of ['论文实际使用 12 个样本', 'GEO 收录 60 个样本，非论文实际使用数量', '样品示例（1/60）', '不能代表全部样品或论文分组', '研究设计原文', '材料来源', 'tumor tissue']) assert.ok(h.panel.innerHTML.includes(text), text);
  assert.doesNotMatch(h.panel.innerHTML, /<script>/); assert.match(h.panel.innerHTML, /&lt;script&gt;/);
});

test('GEO enrichment is explicit, prevents duplicate requests, caches separately and preserves manually entered samples', async () => {
  const h = harness(), response = deferred();
  h.hooks.request = async (method, url) => { h.calls.push({ method, url }); return method === 'POST' ? response.promise : url.endsWith('/geo') ? { records: {} } : [record({ sample_details: '论文样品描述保留' })]; };
  await h.ui.open(paper());
  const data = { resourceKey: A + ':' + R, resourceAccession: 'GSE12345' };
  const fetching = h.click('geo', data); await h.click('geo', data);
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1); assert.match(h.panel.innerHTML, /正在读取 GEO/);
  response.resolve({ metadata: geo(), cached: false }); await fetching;
  assert.match(h.panel.innerHTML, /GEO 样品信息/); assert.match(h.panel.innerHTML, /论文样品描述保留/);
  assert.deepEqual(h.changes, []);
  await h.click('geo', data); assert.ok(h.calls.at(-1).url.endsWith('/geo/GSE12345?refresh=true'));
});

test('failed GEO reads retain resource data and late responses never render into another paper', async () => {
  const h = harness();
  h.hooks.request = async (method, url) => { if (method === 'POST') throw new Error('network unavailable'); return url.endsWith('/geo') ? { records: {} } : [record()]; };
  await h.ui.open(paper()); const data = { resourceKey: A + ':' + R, resourceAccession: 'GSE12345' };
  await h.click('geo', data); assert.match(h.panel.innerHTML, /GEO 信息读取失败：network unavailable/); assert.match(h.panel.innerHTML, /Public atlas/);
  const response = deferred(); h.hooks.request = async (method, url) => method === 'POST' ? response.promise : url.endsWith('/geo') ? { records: {} } : [record({ title: 'Current paper only', accession: 'GSE98765' })];
  const fetching = h.click('geo', data); h.ui.clear(); await h.ui.open(paper(B));
  response.resolve({ metadata: geo() }); await fetching;
  assert.match(h.panel.innerHTML, /Current paper only/); assert.doesNotMatch(h.panel.innerHTML, /Synthetic GEO collection|GSM123|network unavailable/);
});

test('a GEO response does not replace an active resource draft', async () => {
  const h = harness(), response = deferred();
  h.hooks.request = async (method, url) => method === 'POST' ? response.promise : url.endsWith('/geo') ? { records: {} } : [record()];
  await h.ui.open(paper()); const fetching = h.click('geo', { resourceKey: A + ':' + R, resourceAccession: 'GSE12345' });
  await h.click('add'); h.input('sample_details', '尚未保存的样品说明'); const before = h.panel.innerHTML;
  response.resolve({ metadata: geo() }); await fetching;
  assert.equal(h.panel.innerHTML, before); assert.equal(h.ui.canLeave(), false);
  await h.field('document_id', S); assert.match(h.panel.innerHTML, /尚未保存的样品说明/);
});

test('GEO actions match explicit accession membership including official legacy URLs but excluding search and unrelated links', async () => {
  const h = harness();
  h.hooks.request = async (_, url) => url.endsWith('/geo') ? { records: {} } : [
    record({ accession: '', identifiers: '', url: 'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE12345' }),
    record({ id: S, accession: '', url: '', identifiers: 'https://pubmed.ncbi.nlm.nih.gov/?term=GSE99999 https://example.org/GSE77777 [dataset](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE67890)；GSE24680；GSE13579-suffix；GSE12345.version' }),
  ];
  await h.ui.open(paper());
  for (const accession of ['GSE12345', 'GSE67890', 'GSE24680']) assert.match(h.panel.innerHTML, new RegExp('data-resource-accession="' + accession + '"'));
  for (const accession of ['GSE99999', 'GSE77777', 'GSE13579']) assert.doesNotMatch(h.panel.innerHTML, new RegExp('data-resource-accession="' + accession + '"'));
  assert.equal((h.panel.innerHTML.match(/data-resource-accession="GSE12345"/g) || []).length, 1);
});
