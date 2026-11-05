/* Explicit interface translations only. Research content is never inspected. */
(function (global) {
  'use strict';
  const catalog = new Map(), listeners = new Set(), templates = [];
  const attributes = ['title', 'placeholder', 'aria-label', 'aria-description'];
  const selector = ['[data-i18n]', ...attributes.map(name => '[data-i18n-' + name + ']')].join(',');
  let locale = 'zh-CN', revision = 0, savedLocale = null, pendingLocale = null, saving = null;
  let initialized = false, observing = false, feedback = '';
  try { if (global.localStorage?.getItem('bioreader.ui-locale') === 'en') locale = 'en'; } catch (_) {}

  function interpolate(value, params) {
    return String(value).replace(/\{([A-Za-z][\w]*)\}/g, (match, key) =>
      Object.prototype.hasOwnProperty.call(params, key) ? String(params[key] ?? '') : match);
  }
  function register(values) {
    for (const [source, value] of Object.entries(values)) {
      catalog.set(source, value);
      if (/\{[A-Za-z][\w]*\}/.test(source)) {
        const keys = [];
        const escaped = source.split(/(\{[A-Za-z][\w]*\})/g).map(part => {
          if (/^\{[A-Za-z][\w]*\}$/.test(part)) { keys.push(part.slice(1, -1)); return '([\\s\\S]*?)'; }
          return part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        }).join('');
        templates.push({ source, regex: new RegExp('^' + escaped + '$'), keys });
      }
    }
  }
  function t(source, params = {}) {
    source = String(source ?? '');
    if (locale === 'zh-CN') return interpolate(source, params);
    let value = catalog.get(source);
    if (value === undefined && !Object.keys(params).length) {
      for (const template of templates) {
        const match = template.regex.exec(source);
        if (!match) continue;
        params = Object.fromEntries(template.keys.map((key, index) => [key, match[index + 1]]));
        value = catalog.get(template.source); break;
      }
    }
    return interpolate(typeof value === 'function' ? value(params) : value ?? source, params);
  }
  function paramsFor(element) {
    try {
      const value = JSON.parse(element.getAttribute('data-i18n-params') || '{}');
      return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    } catch (_) { return {}; }
  }
  function translateElement(element) {
    const params = paramsFor(element), key = element.getAttribute('data-i18n');
    if (key !== null) {
      const value = t(key, params);
      if (element.textContent !== value) element.textContent = value;
    }
    for (const name of attributes) {
      const source = element.getAttribute('data-i18n-' + name);
      if (source !== null) {
        const value = t(source, params);
        if (element.getAttribute(name) !== value) element.setAttribute(name, value);
      }
    }
  }
  function applyDom(root = global.document) {
    if (!root) return;
    if (root.matches?.(selector)) translateElement(root);
    root.querySelectorAll?.(selector).forEach(translateElement);
  }
  function updateControl() {
    const document = global.document;
    if (!document) return;
    document.documentElement.lang = locale;
    document.documentElement.dataset.locale = locale;
    document.querySelectorAll('[data-ui-locale]').forEach(control => { control.value = locale; });
    const retry = document.getElementById('locale-retry');
    if (retry) { retry.hidden = !feedback; retry.textContent = t(feedback); retry.title = t('点击重试保存界面语言'); }
  }
  function changeLocale(next) {
    if (!['zh-CN', 'en'].includes(next)) throw new Error('Unsupported interface language');
    const changed = next !== locale;
    locale = next;
    try { global.localStorage?.setItem('bioreader.ui-locale', locale); } catch (_) {}
    updateControl(); applyDom();
    if (changed) for (const listener of listeners) {
      try { listener(locale); } catch (error) { global.console?.error('Interface language update failed', error); }
    }
  }
  async function request(method, body) {
    const controller = global.AbortController ? new global.AbortController() : null;
    const timer = controller ? global.setTimeout(() => controller.abort(), 8000) : null;
    try {
      const response = await global.fetch('/api/ui-preferences', { method,
        ...(controller ? { signal: controller.signal } : {}),
        ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
      if (!response.ok) throw new Error('Interface preference request failed');
      const value = await response.json();
      if (!['zh-CN', 'en'].includes(value.locale)) throw new Error('Invalid saved interface language');
      return value;
    } finally { if (timer !== null) global.clearTimeout(timer); }
  }
  async function drain() {
    let success = true;
    while (pendingLocale !== null) {
      const requested = pendingLocale; pendingLocale = null;
      try {
        const result = await request('PUT', { locale: requested });
        if (result.locale !== requested) throw new Error('Language was not saved');
        savedLocale = requested; feedback = '';
      } catch (_) {
        success = false;
        if (pendingLocale === null) feedback = '语言未保存 · 重试';
      }
      updateControl();
    }
    return success && savedLocale === locale;
  }
  function persist() {
    pendingLocale = locale;
    if (!saving) saving = drain().finally(() => { saving = null; });
    return saving;
  }
  function setLocale(next, options = {}) {
    revision++; feedback = ''; changeLocale(next);
    return options.persist === false ? Promise.resolve(true) : persist();
  }
  async function flush() {
    if (saving) await saving;
    return !feedback;
  }
  function observe() {
    if (observing || !global.MutationObserver || !global.document?.body) return;
    observing = true;
    const observer = new global.MutationObserver(records => {
      const roots = new Set();
      for (const record of records) {
        if (record.type === 'attributes') roots.add(record.target);
        else {
          if (record.target.matches?.(selector)) roots.add(record.target);
          for (const node of record.addedNodes) if (node.nodeType === 1) roots.add(node);
        }
      }
      for (const root of roots) if (root.isConnected) applyDom(root);
    });
    observer.observe(global.document.body, { subtree: true, childList: true, attributes: true,
      attributeFilter: ['data-i18n', 'data-i18n-params', ...attributes.map(name => 'data-i18n-' + name)] });
  }
  async function init() {
    if (initialized) return;
    initialized = true;
    global.document?.querySelectorAll('[data-ui-locale]').forEach(control => {
      control.addEventListener('change', () => { void setLocale(control.value); });
    });
    global.document?.getElementById('locale-retry')?.addEventListener('click', () => {
      void (feedback === '语言偏好未载入 · 重试' ? loadPreferences() : persist());
    });
    changeLocale(locale); observe();
    await loadPreferences();
  }
  async function loadPreferences() {
    const initialRevision = revision;
    try {
      const stored = await request('GET');
      if (revision === initialRevision) { savedLocale = stored.locale; feedback = ''; changeLocale(stored.locale); }
    } catch (_) {
      if (revision === initialRevision) { feedback = '语言偏好未载入 · 重试'; updateControl(); }
    }
  }
  global.BioI18n = { t, register, getLocale: () => locale, onChange: callback => { listeners.add(callback); return () => listeners.delete(callback); }, applyDom, setLocale, init, flush };
  if (global.document?.readyState === 'loading') global.document.addEventListener('DOMContentLoaded', init, { once: true });
  else if (global.document) void init();
})(window);
