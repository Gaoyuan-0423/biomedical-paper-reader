// Run the delivered modules with the actual translation engine and synthetic research data.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const A = 'a'.repeat(32), S = 'b'.repeat(32), R = 'c'.repeat(32);
const paper = { id: A, has_file: true, pages: 4, supplements: [{ id: S, filename: '正文.pdf', pages: 2 }] };
function element() {
  return { innerHTML: '', handlers: {}, nodes: {}, addEventListener(event, handler) { this.handlers[event] = handler; },
    querySelector(selector) { return this.nodes[selector] ||= { innerHTML: '', focus() {}, setAttribute() {} }; }, querySelectorAll() { return []; } };
}
function harness(name) {
  const panel = element(), footer = element(), modal = element(), settingsPanel = element(), calls = [];
  const context = vm.createContext({ window: { confirm: () => true }, document: { getElementById: () => panel }, URL, console });
  for (const file of ['i18n.js', 'i18n-research.js', name + '.js']) vm.runInContext(fs.readFileSync(path.join(__dirname, '../frontend', file), 'utf8'), context);
  const i18n = context.window.BioI18n;
  const hooks = { panel, footer, modal, settingsPanel, request: async (method, url, body) => { calls.push({ method, url, body }); return method === 'GET' ? [] : { id: R, ...body }; }, toast() {}, onChanged() {}, showModal() {}, hideModal() {} };
  const api = context.window[{ resources: 'BioResources', evidence: 'BioEvidence', zotero: 'BioZotero', 'library-search': 'BioLibrarySearch' }[name]];
  api.init(hooks);
  return { api, hooks, panel, footer, modal, settingsPanel, i18n, calls, window: context.window,
    english: () => i18n.setLocale('en', { persist: false }),
    click(action, data = {}) { return panel.handlers.click({ target: { closest: selector => selector === '[data-resource-action]' || selector === '[data-evidence-action]' ? { dataset: { resourceAction: action, evidenceAction: action, ...data } } : null } }); },
    input(field, value) { return panel.handlers.input({ target: { value, dataset: { resourceField: field, evidenceField: field } } }); },
    submit(id) { return panel.handlers.submit({ target: { id }, preventDefault() {} }); },
  };
}
const text = html => html.replace(/<[^>]+>/g, '');

test('resource UI uses English while preserving stored research values and unsaved API payloads', async () => {
  const h = harness('resources'); await h.english();
  h.hooks.request = async (method, url, body) => { h.calls.push({ method, url, body }); return method === 'GET' ? [{ id: R, title: '数据与复现', purpose: '用途', kind: 'dataset', role: 'reused', organism: '物种', document_id: 'main', source_name: '正文', page: 2 }] : { id: R, ...body }; };
  await h.api.open(paper);
  assert.match(text(h.panel.innerHTML), /Data &amp; reproducibility/);
  assert.match(text(h.panel.innerHTML), /数据与复现/); assert.match(text(h.panel.innerHTML), /用途/);
  assert.match(text(h.panel.innerHTML), /Organism物种/); assert.match(text(h.panel.innerHTML), /Main PDF · PDF p\. 2/);
  await h.click('add'); h.input('title', '研究资源 草稿'); h.input('sample_details', '中文原文样品');
  await h.i18n.setLocale('zh-CN', { persist: false }); await h.submit('resource-form');
  const body = h.calls.find(call => call.method === 'POST').body;
  assert.equal(body.title, '研究资源 草稿'); assert.equal(body.sample_details, '中文原文样品');
});

test('evidence figure options translate page labels but preserve figure names and drafts', async () => {
  const h = harness('evidence'); await h.english();
  await h.api.open(paper, [{ id: 'f1', label: '正文', document_id: 'main', page: 2 }]);
  h.api.start({ claim: '需要核查的判断', quote: '原文摘录', document_id: 'main', page: 2, figure_id: 'f1' });
  assert.match(text(h.panel.innerHTML), /Claim to/);
  assert.match(h.panel.innerHTML, />正文 · p\. 2<\/option>/);
  assert.doesNotMatch(h.panel.innerHTML, /<option[^>]*>[^<]*<span/);
  assert.match(h.panel.innerHTML, />正文.pdf<\/option>/);
  await h.i18n.setLocale('zh-CN', { persist: false }); await h.submit('evidence-form');
  const body = h.calls.find(call => call.method === 'POST').body;
  assert.equal(body.claim, '需要核查的判断'); assert.equal(body.quote, '原文摘录'); assert.equal(body.figure_id, 'f1'); assert.equal(body.page, 2);
});

test('Zotero connection capability labels are localized without changing endpoint, token or folder drafts', async () => {
  const h = harness('zotero');
  h.hooks.request = async (method, url, body) => { h.calls.push({ method, url, body }); return url.endsWith('/check') ? { server_name: '中文服务', capabilities: { search: true, metadata: true, attachments: true } } : { endpoint: 'http://localhost:8000/mcp', storage_root: '/tmp/storage', has_token: false }; };
  await h.api.loadSettings();
  for (const [field, value] of Object.entries({ endpoint: 'http://localhost:9000/mcp', token: 'synthetic-token', storage_root: '/tmp/中文目录' })) h.settingsPanel.handlers.input({ target: { dataset: { zoteroSetting: field }, value } });
  await h.english();
  await h.settingsPanel.handlers.click({ target: { closest: () => ({ dataset: { zoteroSettingAction: 'check' } }) } });
  const body = h.calls.at(-1).body;
  assert.equal(body.endpoint, 'http://localhost:9000/mcp'); assert.equal(body.token, 'synthetic-token'); assert.equal(body.storage_root, '/tmp/中文目录');
  assert.match(text(h.settingsPanel.innerHTML), /Connected.*中文服务.*Search.*Metadata.*Attachment/);
  assert.match(h.settingsPanel.innerHTML, /data-i18n-placeholder="留空保持已保存 Token"/);
});

test('local search retains the original query, title and snippet even when they match UI source keys', async () => {
  const h = harness('library-search'); await h.english();
  h.hooks.request = async (method, url) => { h.calls.push({ method, url }); return { items: [{ paper_title: '搜索', snippet: '搜索正文与笔记', kind: 'pdf', document_id: 'main', source_name: '正文', page: 3 }], next_offset: 1 }; };
  h.api.open('正文与笔记'); await h.submit('library-search-form');
  assert.match(h.calls[0].url, new RegExp('q=' + encodeURIComponent('正文与笔记')));
  assert.match(text(h.panel.innerHTML), /Main PDF · p\. 3搜索搜索正文与笔记/);
  assert.match(text(h.footer.innerHTML), /Showing 1 result/);
  assert.match(h.panel.innerHTML, /data-i18n-placeholder="输入关键词或一句原文"/);
});

test('dynamic errors escape untrusted details in both interface languages', async () => {
  const h = harness('resources'); await h.english();
  h.hooks.request = async () => { throw Error('<img src=x onerror="bad()">'); };
  await h.api.open(paper);
  assert.match(text(h.panel.innerHTML), /Could not load resources/);
  assert.doesNotMatch(h.panel.innerHTML, /<img src=/);
  assert.match(h.panel.innerHTML, /&lt;img src=x onerror=&quot;bad\(\)&quot;&gt;/);
});

test('known backend failures render actionable English in all research modules', async () => {
  const resource = harness('resources'); await resource.english();
  resource.hooks.request = async () => { throw Error('资源依据对应的 PDF 不存在'); };
  await resource.api.open(paper);
  assert.match(text(resource.panel.innerHTML), /Could not load resources: The PDF cited by this resource no longer exists\./);
  const evidence = harness('evidence'); await evidence.english();
  evidence.hooks.request = async () => { throw Error('证据页码超出该 PDF 的范围'); };
  await evidence.api.open(paper);
  assert.match(text(evidence.panel.innerHTML), /The evidence page is outside the PDF page range\./);
  const zotero = harness('zotero'); await zotero.english();
  zotero.hooks.request = async (method) => {
    if (method === 'GET') return { endpoint: 'http://localhost:8000/mcp', storage_root: '/tmp/synthetic' };
    throw Error('无法连接 Zotero MCP，请检查服务是否启动及地址是否正确');
  };
  await zotero.api.loadSettings();
  await zotero.settingsPanel.handlers.click({ target: { closest: () => ({ dataset: { zoteroSettingAction: 'check' } }) } });
  assert.match(text(zotero.settingsPanel.innerHTML), /Connection test failed: Could not connect to Zotero MCP\./);
  const search = harness('library-search'); await search.english();
  search.hooks.request = async () => { throw Error('该文献尚未附加 PDF'); };
  search.api.open('synthetic query'); await search.submit('library-search-form');
  assert.match(text(search.panel.innerHTML), /Search failed: This paper does not have an attached PDF\./);
  const format = zotero.window.BioResearchI18n.messageText;
  for (const [detail, expected] of [
    ['请填写有效的 Zotero MCP HTTP 地址', 'Enter a valid Zotero MCP HTTP URL.'],
    ['连接令牌格式不正确', 'Invalid connection token format.'],
    ['请填写 Zotero 附件目录的绝对路径或 ~/ 开头的路径', 'Enter an absolute Zotero attachment folder path or a path starting with ~/.'],
    ['MCP 认证失败（HTTP 401），请检查 Bearer Token', 'MCP authentication failed (HTTP 401). Check the bearer token.'],
  ]) assert.equal(format('连接测试失败：{detail}', { detail }), 'Connection test failed: ' + expected);
});

test('existing backend errors switch languages in place while unknown upstream details stay literal and escaped', async () => {
  const h = harness('resources');
  await h.api.open(paper); await h.click('add'); h.input('title', '未保存研究资源');
  h.hooks.request = async () => { throw Error('资源依据对应的 PDF 不存在'); };
  await h.submit('resource-form');
  const snapshot = h.panel.innerHTML;
  const decode = value => value.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  const match = /<span data-research-error="([^"]+)">([^<]*)<\/span>/.exec(snapshot);
  assert.ok(match);
  const raw = decode(match[1]), errorNode = { textContent: decode(match[2]), getAttribute: () => raw };
  h.window.document = { documentElement: { dataset: {} }, getElementById: () => null,
    querySelectorAll: selector => selector === '[data-research-error]' ? [errorNode] : [] };
  await h.english(); assert.equal(errorNode.textContent, 'Save failed: The PDF cited by this resource no longer exists.');
  assert.equal(h.panel.innerHTML, snapshot, 'language changes must not rebuild the draft form');
  assert.equal(JSON.parse(raw).params.detail, '资源依据对应的 PDF 不存在');
  await h.i18n.setLocale('zh-CN', { persist: false }); assert.equal(errorNode.textContent, '保存失败：资源依据对应的 PDF 不存在');
  assert.match(h.panel.innerHTML, /未保存研究资源/);
  await h.english();
  const upstream = '第三方原文：搜索；无法连接 Zotero MCP，请检查服务是否启动及地址是否正确 <img onerror="bad()">';
  const formatted = h.window.BioResearchI18n.messageText('保存失败：{detail}', { detail: upstream });
  assert.equal(formatted, 'Save failed: ' + upstream);
  const html = h.window.BioResearchI18n.messageHTML('保存失败：{detail}', { detail: upstream });
  assert.doesNotMatch(html, /<img/); assert.match(html, /&lt;img onerror=&quot;bad\(\)&quot;&gt;/);
});
