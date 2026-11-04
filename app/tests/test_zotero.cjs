const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../frontend/zotero.js'), 'utf8');
const config = { endpoint: 'http://127.0.0.1:8000/mcp', storage_root: '/synthetic/storage', has_token: true, configuration_id: 'config-one' };
const item = (key = 'ABCD1234', title = 'Synthetic study') => ({ key, title, authors: ['Researcher'], year: '2025', doi: '10.1000/example', item_type: 'journalArticle' });
const attachment = { key: 'PDF12345', filename: 'study.pdf', content_type: 'application/pdf', available: true };
const preview = (key = 'ABCD1234', title) => ({ item: item(key, title), attachments: [attachment, { ...attachment, key: 'NOFILE00', filename: 'linked.pdf', available: false, unavailable_reason: '链接附件暂不支持' }], preview_id: 'preview-' + key, configuration_id: config.configuration_id });
const paper = { id: 'a'.repeat(32), title: 'Imported study', has_file: false };
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function harness() {
  const calls = [], changed = [], opened = [], toasts = [], shown = [], hidden = [], elements = {};
  function element() { return { innerHTML: '', value: '', disabled: false, handlers: {}, addEventListener(name, fn) { this.handlers[name] = fn; }, querySelector(selector) { return elements[selector] ||= { innerHTML: '', value: '', focus() {} }; } }; }
  const panel = element(), footer = element(), settingsPanel = element(), modal = element();
  const hooks = { panel, footer, settingsPanel, modal,
    request: async (method, url, body) => { calls.push({ method, url, body }); if (url === '/api/zotero/settings') return config; if (url === '/api/zotero/search') return { items: [item()], has_more: false, configuration_id: config.configuration_id }; if (url.includes('/items/')) return preview(); if (url === '/api/zotero/import') return { paper, existing: false, message: '已导入' }; if (url === '/api/zotero/check') return { server_name: 'Synthetic MCP', message: '连接成功', capabilities: { search: true, metadata: true, attachments: true } }; throw Error('Unexpected request'); },
    toast: (...args) => toasts.push(args), showModal: id => shown.push(id), hideModal: id => hidden.push(id), onImported: value => changed.push(value), openPaper: async value => { opened.push(value); return true; }, openSettings() {},
  };
  const context = vm.createContext({ window: {}, document: {}, console }); vm.runInContext(code, context); const ui = context.window.BioZotero; ui.init(hooks);
  return { ui, hooks, panel, footer, settingsPanel, modal, calls, changed, opened, toasts, shown, hidden,
    html: () => panel.innerHTML + footer.innerHTML + settingsPanel.innerHTML + Object.values(elements).map(e => e.innerHTML).join(''),
    query: value => panel.handlers.input({ target: { id: 'zotero-query', value } }),
    search: () => panel.handlers.submit({ target: { id: 'zotero-search' }, preventDefault() {} }),
    click: (action, key) => modal.handlers.click({ target: { closest: () => ({ dataset: { zoteroAction: action, zoteroKey: key } }) } }),
    choose: key => panel.handlers.change({ target: { name: 'zotero-attachment', value: key } }),
    setting: (field, value) => settingsPanel.handlers.input({ target: { dataset: { zoteroSetting: field }, value, type: typeof value === 'boolean' ? 'checkbox' : 'text', checked: value } }),
    settingAction: action => settingsPanel.handlers.click({ target: { closest: () => ({ dataset: { zoteroSettingAction: action } }) } }),
  };
}
async function searched(h) { await h.ui.open(); h.query('tumor'); await h.search(); }
async function selected(h) { await searched(h); await h.click('select', 'ABCD1234'); }

test('opening reads local settings only; search requires a deliberate nonempty query', async () => {
  const h = harness(); await h.ui.open(); assert.deepEqual(h.calls.map(c => c.url), ['/api/zotero/settings']);
  await h.search(); assert.equal(h.calls.length, 1); assert.match(h.html(), /请输入关键词/);
  h.query('tumor'); await h.search(); assert.deepEqual(JSON.parse(JSON.stringify(h.calls.at(-1).body)), { query: 'tumor', limit: 20, start: 0 }); assert.match(h.html(), /Synthetic study/);
});

test('preview defaults to metadata only, importing never opens or modifies the reading paper', async () => {
  const h = harness(); await selected(h); assert.match(h.html(), /value="" checked/); assert.match(h.html(), /linked.pdf/);
  await h.click('import'); const request = h.calls.at(-1); assert.equal(request.body.preview_id, 'preview-ABCD1234'); assert.equal(request.body.attachment_key, '');
  assert.equal(h.changed[0].id, paper.id); assert.equal(h.opened.length, 0); assert.match(h.html(), /仅文献信息 · 尚无 PDF/);
  await h.click('open-paper'); assert.equal(h.opened.length, 1); assert.equal(h.hidden.length, 1);
});

test('only an explicitly chosen available PDF is included in an import', async () => {
  const h = harness(); await selected(h); h.choose('NOFILE00'); await h.click('import'); assert.equal(h.calls.at(-1).body.attachment_key, '');
  await h.click('select', 'ABCD1234'); h.choose(attachment.key); await h.click('import'); assert.equal(h.calls.at(-1).body.attachment_key, attachment.key);
});

test('late searches cannot overwrite a newer query or a closed window', async () => {
  const h = harness(), first = deferred(); await h.ui.open();
  h.hooks.request = async (_, url, body) => body.query === 'old' ? first.promise : { items: [item('LATEST00', 'Latest result')], configuration_id: config.configuration_id };
  h.query('old'); const old = h.search(); h.query('new'); await h.search(); first.resolve({ items: [item('OLD00000', 'Old result')], configuration_id: config.configuration_id }); await old;
  assert.match(h.html(), /Latest result/); assert.doesNotMatch(h.html(), /Old result/);
  const closed = deferred(); h.hooks.request = () => closed.promise; h.query('closed'); const request = h.search(); h.ui.close(); closed.resolve({ items: [item('CLOSED00', 'Closed result')] }); await request; await h.ui.open(); assert.doesNotMatch(h.html(), /Closed result/);
});

test('a delayed attachment preview never replaces the newly selected item', async () => {
  const h = harness(), old = deferred(); await h.ui.open(); h.hooks.request = async () => ({ items: [item(), item('SECOND00')], configuration_id: config.configuration_id }); h.query('two'); await h.search();
  h.hooks.request = async (_, url) => url.includes('ABCD1234') ? old.promise : preview('SECOND00', 'New selection');
  const first = h.click('select', 'ABCD1234'); await h.click('select', 'SECOND00'); old.resolve(preview('ABCD1234', 'Obsolete preview')); await first;
  assert.match(h.html(), /New selection/); assert.doesNotMatch(h.html(), /Obsolete preview/);
});

test('changed configuration or malformed preview prevents an import', async () => {
  const h = harness(); await searched(h); h.hooks.request = async () => ({ ...preview(), configuration_id: 'changed' }); await h.click('select', 'ABCD1234'); assert.match(h.html(), /条目预览已变化/);
  let writes = 0; h.hooks.request = async () => { writes++; }; await h.click('import'); assert.equal(writes, 0);
});

test('closing during import still updates the library once and reopening restores the result', async () => {
  const h = harness(), pending = deferred(); await selected(h); let writes = 0; h.hooks.request = async () => { writes++; return pending.promise; };
  const importing = h.click('import'); await h.click('import'); assert.equal(writes, 1); assert.equal(h.ui.isImporting(), true); h.ui.close();
  pending.resolve({ paper: { ...paper, has_file: true }, existing: false, message: 'PDF 已导入' }); await importing;
  assert.equal(h.changed.length, 1); assert.equal(h.opened.length, 0); assert.equal(h.ui.isImporting(), false); assert.match(h.toasts[0][0], /PDF 已导入/);
  await h.ui.open(); assert.match(h.html(), /此条目已有 PDF/); assert.match(h.html(), /打开文献/);
});

test('import failure keeps the selection and allows retry; expired preview must be read again', async () => {
  const h = harness(); await selected(h); h.choose(attachment.key); h.hooks.request = async () => { throw Error('unavailable'); }; await h.click('import'); assert.match(h.html(), /导入失败：unavailable/); assert.match(h.html(), /value="PDF12345" checked/);
  h.hooks.request = async () => { const error = Error('预览过期'); error.status = 409; throw error; }; await h.click('import'); assert.match(h.html(), /重新读取/);
  let count = 0; h.hooks.request = async () => { count++; }; await h.click('import'); assert.equal(count, 0);
});

test('pagination deduplicates results and changed-query typing does not display a stale response', async () => {
  const h = harness(); await h.ui.open(); h.hooks.request = async (_, url, body) => { h.calls.push({ body }); return body.start === 0 ? { items: [item()], has_more: true, configuration_id: config.configuration_id } : { items: [item(), item('SECOND00')], has_more: false, configuration_id: config.configuration_id }; };
  h.query('page'); await h.search(); await h.click('more'); assert.equal(h.calls.at(-1).body.start, 20); assert.equal((h.panel.innerHTML.match(/data-zotero-action="select"/g) || []).length, 2);
  const waiting = deferred(); h.hooks.request = () => waiting.promise; h.query('waiting'); const request = h.search(); h.query('still typing'); waiting.resolve({ items: [item('LATE0000', 'Stale response')] }); await request; assert.doesNotMatch(h.html(), /Stale response/);
});

test('connection tests use current draft without saving and ignore old results after edits', async () => {
  const h = harness(); await h.ui.loadSettings(); h.setting('endpoint', 'http://127.0.0.1:1234/mcp'); h.setting('token', 'synthetic-token'); await h.settingAction('check');
  const tested = h.calls.at(-1); assert.equal(tested.url, '/api/zotero/check'); assert.equal(tested.body.token, 'synthetic-token'); assert.equal(tested.body.endpoint, 'http://127.0.0.1:1234/mcp'); assert.equal(h.calls.filter(c => c.method === 'PUT').length, 0);
  const old = deferred(); h.hooks.request = () => old.promise; const testConnection = h.settingAction('check'); h.setting('endpoint', 'http://127.0.0.1:4321/mcp'); old.resolve({ message: 'Obsolete connection success', capabilities: {} }); await testConnection; assert.doesNotMatch(h.html(), /Obsolete connection success/);
});

test('saving Zotero is independent, preserves blank token, invalidates previews, and clears the input', async () => {
  const h = harness(); await selected(h); h.setting('storage_root', '/new/synthetic/storage'); h.setting('token', ''); await h.settingAction('save');
  const saved = h.calls.at(-1); assert.equal(saved.method, 'PUT'); assert.equal(saved.url, '/api/zotero/settings'); assert.equal(Object.hasOwn(saved.body, 'token'), false); assert.match(h.html(), /Zotero 设置已单独保存/);
  let imports = 0; h.hooks.request = async () => { imports++; }; await h.click('import'); assert.equal(imports, 0);
  h.setting('clear_token', true); h.hooks.request = async (_, __, body) => { assert.equal(body.clear_token, true); return { ...config, has_token: false }; }; await h.settingAction('save'); assert.match(h.html(), /未设置 Token/);
});

test('untrusted metadata, attachment names and errors are rendered as text', async () => {
  const h = harness(); await h.ui.open(); h.hooks.request = async () => ({ items: [item('ABCD1234', '<script>bad()</script>')], configuration_id: config.configuration_id }); h.query('safe'); await h.search();
  h.hooks.request = async () => ({ ...preview(), item: { ...item(), abstract: '<img src=x onerror=bad()>' }, attachments: [{ ...attachment, filename: '<svg onload=bad()>' }] }); await h.click('select', 'ABCD1234');
  assert.doesNotMatch(h.html(), /<script>|<img|<svg/); assert.match(h.html(), /&lt;script&gt;/); assert.match(h.html(), /&lt;img/); assert.match(h.html(), /&lt;svg/);
});

test('open-paper failure leaves the successful import available without retrying the write', async () => {
  const h = harness(); await selected(h); await h.click('import'); h.hooks.openPaper = async () => false; await h.click('open-paper'); assert.equal(h.hidden.length, 0); assert.match(h.html(), /打开文献/); assert.equal(h.calls.filter(c => c.url === '/api/zotero/import').length, 1);
});

test('settings reopened during a save cannot restore an older connection or remain disabled', async () => {
  const h = harness(), write = deferred(), read = deferred(); await h.ui.loadSettings(); h.setting('endpoint', 'http://127.0.0.1:9999/mcp');
  h.hooks.request = (method) => method === 'PUT' ? write.promise : read.promise;
  const saving = h.settingAction('save'); h.ui.closeSettings(); const reopening = h.ui.loadSettings();
  write.resolve({ ...config, endpoint: 'http://127.0.0.1:9999/mcp', configuration_id: 'new' }); await saving;
  read.resolve(config); await reopening; assert.match(h.settingsPanel.innerHTML, /9999\/mcp/); assert.doesNotMatch(h.settingsPanel.innerHTML, /正在保存|data-zotero-setting="endpoint"[^>]+disabled/);
});

test('a page whose raw rows were all filtered still advances the search window', async () => {
  const h = harness(); await h.ui.open(); const starts = [];
  h.hooks.request = async (_, __, body) => { starts.push(body.start); return { items: body.start ? [item()] : [], has_more: body.start === 0, configuration_id: config.configuration_id }; };
  h.query('filtered'); await h.search(); assert.match(h.html(), /加载更多/); await h.click('more');
  assert.deepEqual(starts, [0, 20]); assert.match(h.html(), /Synthetic study/);
});
