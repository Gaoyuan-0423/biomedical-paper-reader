const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../frontend/i18n.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness(fetch, cached) {
  const controls = [{ value: '', addEventListener() {} }, { value: '', addEventListener() {} }];
  const retry = { hidden: true, addEventListener(name, callback) { this[name] = callback; } }, memory = new Map(cached ? [['bioreader.ui-locale', cached]] : []);
  const document = { readyState: 'loading', documentElement: { dataset: {} },
    addEventListener() {}, querySelectorAll: selector => selector === '[data-ui-locale]' ? controls : [],
    getElementById: id => id === 'locale-retry' ? retry : null };
  const window = { document, fetch: fetch || (async () => ({ ok: true, json: async () => ({ locale: 'zh-CN' }) })),
    localStorage: { getItem: key => memory.get(key), setItem: (key, value) => memory.set(key, value) } };
  vm.runInNewContext(source, { window });
  return { api: window.BioI18n, window, document, retry, controls, memory };
}

test('explicit translations support parameters, plurals and formatted service messages', async () => {
  const { api } = harness();
  api.register({ '保存': 'Save', '第 {page} 页': 'Page {page}', '{count} 篇文献': p => `${p.count} ${Number(p.count) === 1 ? 'paper' : 'papers'}`,
    '错误（{reason}）': 'Error ({reason})' });
  assert.equal(api.t('保存'), '保存');
  await api.setLocale('en', { persist: false });
  assert.equal(api.t('保存'), 'Save');
  assert.equal(api.t('第 {page} 页', { page: 4 }), 'Page 4');
  assert.equal(api.t('第 12 页'), 'Page 12');
  assert.equal(api.t('{count} 篇文献', { count: 1 }), '1 paper');
  assert.equal(api.t('错误（原始错误）'), 'Error (原始错误)');
  assert.equal(api.t('未知原文内容'), '未知原文内容');
  assert.throws(() => api.setLocale('fr'), /Unsupported/);
});

test('translation touches explicit UI nodes only and never interprets parameter HTML', async () => {
  const { api } = harness(); api.register({ '共 {value}': 'Total {value}', '关闭': 'Close' });
  const attrs = { 'data-i18n': '共 {value}', 'data-i18n-params': JSON.stringify({ value: '<img onerror=bad>' }), 'data-i18n-title': '关闭' };
  const node = { textContent: '', getAttribute: k => attrs[k] ?? null, setAttribute: (k, v) => { attrs[k] = v; } };
  const paper = { textContent: '关闭', value: '用户笔记和草稿' };
  await api.setLocale('en', { persist: false });
  api.applyDom({ querySelectorAll: () => [node] });
  assert.equal(node.textContent, 'Total <img onerror=bad>'); assert.equal(attrs.title, 'Close');
  assert.equal(paper.textContent, '关闭'); assert.equal(paper.value, '用户笔记和草稿');
});

test('stored backend preference wins across browser origins and synchronizes both controls', async () => {
  const { api, controls, document } = harness(async () => ({ ok: true, json: async () => ({ locale: 'en' }) }));
  await api.init(); assert.equal(api.getLocale(), 'en'); assert.equal(document.documentElement.lang, 'en');
  assert.deepEqual(controls.map(c => c.value), ['en', 'en']);
});

test('late preference load cannot overwrite a user language choice', async () => {
  let answer;
  const { api } = harness((url, options) => options.method === 'GET' ? new Promise(resolve => { answer = resolve; })
    : Promise.resolve({ ok: true, json: async () => JSON.parse(options.body) }));
  const loading = api.init(); await api.setLocale('zh-CN');
  answer({ ok: true, json: async () => ({ locale: 'en' }) }); await loading;
  assert.equal(api.getLocale(), 'zh-CN');
});

test('rapid switches persist in order and close waits for the final preference', async () => {
  const requests = [], replies = [];
  const { api } = harness((url, options) => { requests.push(JSON.parse(options.body).locale); return new Promise(resolve => replies.push(resolve)); });
  const first = api.setLocale('en'); api.setLocale('zh-CN');
  let flushed = false; const closing = api.flush().then(() => { flushed = true; });
  assert.deepEqual(requests, ['en']); assert.equal(flushed, false);
  replies.shift()({ ok: true, json: async () => ({ locale: 'en' }) }); await tick();
  assert.deepEqual(requests, ['en', 'zh-CN']); assert.equal(flushed, false);
  replies.shift()({ ok: true, json: async () => ({ locale: 'zh-CN' }) });
  await first; await closing; assert.equal(api.getLocale(), 'zh-CN'); assert.equal(flushed, true);
});

test('failed language save stays visible and can be retried without changing user content', async () => {
  let fail = true;
  const { api, retry } = harness(async (url, options) => ({ ok: !fail, json: async () => JSON.parse(options.body) }));
  assert.equal(await api.setLocale('en'), false); assert.equal(api.getLocale(), 'en'); assert.equal(retry.hidden, false);
  assert.equal(await api.flush(), false);
  fail = false; assert.equal(await api.setLocale('en'), true); assert.equal(retry.hidden, true);
});

test('every static shell translation has an English catalog entry', async () => {
  const { api, window } = harness();
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../frontend/i18n-shell.js'), 'utf8'), { window });
  await api.setLocale('en', { persist: false });
  const html = fs.readFileSync(path.join(__dirname, '../frontend/index.html'), 'utf8');
  const decode = s => s.replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&#10;/g, '\n').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
  for (const match of html.matchAll(/data-i18n(?:-(?:title|placeholder|aria-label|aria-description))?="([^"]+)"/g)) {
    const key = decode(match[1]); assert.notEqual(api.t(key), key, `Missing English shell translation: ${key}`);
  }
});

test('an unresponsive preference service times out so native close can finish', async () => {
  let abortTimer, cleared = false;
  const { api, window, retry } = harness((url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('aborted')));
  }));
  window.AbortController = AbortController;
  window.setTimeout = (callback, milliseconds) => { assert.equal(milliseconds, 8000); abortTimer = callback; return 1; };
  window.clearTimeout = id => { assert.equal(id, 1); cleared = true; };
  const saving = api.setLocale('en');
  abortTimer();
  assert.equal(await saving, false);
  assert.equal(await api.flush(), false);
  assert.equal(retry.hidden, false);
  assert.equal(cleared, true);
});

test('retrying a failed initial load reads the stored language without overwriting it', async () => {
  let failed = true;
  const methods = [];
  const { api, retry } = harness(async (url, options) => {
    methods.push(options.method);
    return { ok: !failed, json: async () => ({ locale: 'en' }) };
  });
  await api.init();
  assert.equal(api.getLocale(), 'zh-CN'); assert.equal(retry.hidden, false);
  failed = false; retry.click(); await tick();
  assert.deepEqual(methods, ['GET', 'GET']);
  assert.equal(api.getLocale(), 'en'); assert.equal(retry.hidden, true);
});
