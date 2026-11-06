/* Display preferences affect app chrome and reading text, never research data. */
(function (global) {
  'use strict';
  const defaults = Object.freeze({ scheme: 'system', accent: 'blue', ui_font_size: 13, note_font_size: 14 });
  const keys = Object.keys(defaults), cacheKey = 'bioreader.appearance';
  let current = { ...defaults }, pending = {}, saving = null, timer = null, initialized = false;
  let loadError = false, saveError = false, loading = true, confirmedRevision = 0;
  const revisions = Object.fromEntries(keys.map(key => [key, 0]));
  const system = global.matchMedia?.('(prefers-color-scheme: dark)');
  const tr = key => global.BioI18n?.t(key) ?? key;
  function valid(key, value) {
    if (key === 'scheme') return ['system', 'light', 'dark'].includes(value);
    if (key === 'accent') return ['blue', 'teal', 'violet'].includes(value);
    return Number.isInteger(value) && value >= 12 && value <= (key === 'ui_font_size' ? 18 : 24);
  }
  function normalize(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('Invalid appearance preferences');
    const result = {};
    for (const key of keys) {
      const item = Object.hasOwn(value, key) ? value[key] : defaults[key];
      if (!valid(key, item)) throw Error('Invalid appearance preference: ' + key);
      result[key] = item;
    }
    return result;
  }
  try { current = normalize(JSON.parse(global.localStorage?.getItem(cacheKey) || 'null')); } catch (_) {}
  function resolvedScheme() { return current.scheme === 'system' ? (system?.matches ? 'dark' : 'light') : current.scheme; }
  function updateControls() {
    const document = global.document;
    if (!document) return;
    const root = document.documentElement;
    root.dataset.appearanceState = loadError || saveError ? 'error' : loading ? 'loading' : saving || Object.keys(pending).length ? 'saving' : 'ready';
    const controls = {scheme:'appearance-scheme',accent:'appearance-accent',ui_font_size:'appearance-ui-font',note_font_size:'appearance-note-font'};
    for (const [key, id] of Object.entries(controls)) {
      const control = document.getElementById(id);
      if (control && control.value !== String(current[key])) control.value = String(current[key]);
    }
    for (const [id,key] of [['appearance-ui-value','ui_font_size'],['appearance-note-value','note_font_size']]) {
      const output = document.getElementById(id);
      if (output) output.textContent = current[key] + ' px';
    }
    const status = document.getElementById('appearance-status'), retry = document.getElementById('appearance-retry');
    if (status) {
      status.textContent = tr(loadError ? '外观偏好未载入，当前预览尚未确认保存。' : saveError ? '外观未保存，当前预览仍保留。' : loading ? '正在载入外观…' : root.dataset.appearanceState === 'saving' ? '正在保存外观…' : '外观已保存');
      status.dataset.state = root.dataset.appearanceState;
    }
    if (retry) { retry.hidden = !loadError && !saveError; retry.textContent = tr('重试'); }
  }
  function apply() {
    const root = global.document?.documentElement;
    if (root) {
      root.dataset.colorScheme = resolvedScheme(); root.dataset.appearance = current.scheme; root.dataset.accent = current.accent;
      root.style.setProperty('--ui-font-size', current.ui_font_size + 'px');
      root.style.setProperty('--note-font-size', current.note_font_size + 'px');
      global.document.getElementById('app-theme-color')?.setAttribute('content', resolvedScheme() === 'dark' ? '#101722' : '#f3f6fb');
    }
    try { global.localStorage?.setItem(cacheKey, JSON.stringify(current)); } catch (_) {}
    updateControls();
  }
  async function request(method, body) {
    const controller = global.AbortController ? new global.AbortController() : null;
    const timeout = controller ? global.setTimeout(() => controller.abort(), 8000) : null;
    try {
      const response = await global.fetch('/api/ui-preferences', { method,
        ...(controller ? { signal: controller.signal } : {}),
        ...(body ? { headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) } : {}) });
      if (!response.ok) throw Error('Appearance preference request failed');
      return normalize(await response.json());
    } finally { if (timeout !== null) global.clearTimeout(timeout); }
  }
  async function load() {
    const snapshot = { ...revisions }, confirmation = confirmedRevision;
    loading = true; updateControls();
    try {
      const stored = await request('GET');
      for (const key of keys) if (revisions[key] === snapshot[key] && !Object.hasOwn(pending,key)) current[key] = stored[key];
      loadError = false;
    } catch (_) { if (confirmedRevision === confirmation) loadError = true; }
    finally { loading = false; apply(); }
    return !loadError;
  }
  async function drain() {
    while (Object.keys(pending).length) {
      const patch = pending, snapshot = { ...revisions }; pending = {};
      try {
        const stored = await request('PATCH', patch);
        if (!Object.entries(patch).every(([key,value]) => stored[key] === value)) throw Error('Appearance was not saved');
        // A full PATCH response also recovers unchanged fields after a failed
        // initial GET, without replacing any input made while saving.
        for (const key of keys) if (revisions[key] === snapshot[key] && !Object.hasOwn(pending,key)) { current[key] = stored[key]; revisions[key]++; }
        confirmedRevision++; saveError = false; loadError = false; apply();
      } catch (_) {
        pending = { ...patch, ...pending }; saveError = true;
        return false;
      }
      updateControls();
    }
    return !loadError && !saveError;
  }
  function persist() {
    if (!saving) {
      // Enter the saving state before a request can resolve synchronously.
      saving = Promise.resolve().then(drain).finally(() => { saving = null; updateControls(); });
      updateControls();
    }
    return saving;
  }
  function update(patch) {
    if (!patch || typeof patch !== 'object' || Array.isArray(patch) || !Object.keys(patch).length) throw Error('Invalid appearance update');
    for (const [key,value] of Object.entries(patch)) if (!keys.includes(key) || !valid(key,value)) throw Error('Invalid appearance update: ' + key);
    for (const [key,value] of Object.entries(patch)) { current[key] = value; pending[key] = value; revisions[key]++; }
    saveError = false; apply();
    global.clearTimeout(timer);
    timer = global.setTimeout(() => { timer = null; void persist(); }, 220);
  }
  async function flush() {
    global.clearTimeout(timer); timer = null;
    if (saving) await saving;
    if (Object.keys(pending).length && !saveError) return persist();
    return !loadError && !saveError;
  }
  async function retry() {
    global.clearTimeout(timer); timer = null;
    if (saving) await saving;
    if (loadError && !await load()) return false;
    saveError = false;
    return Object.keys(pending).length ? persist() : !loadError;
  }
  function bindControls() {
    const document = global.document;
    if (!document) return;
    for (const [id,key] of [['appearance-scheme','scheme'],['appearance-accent','accent'],['appearance-ui-font','ui_font_size'],['appearance-note-font','note_font_size']]) {
      const control = document.getElementById(id);
      control?.addEventListener(key.endsWith('_size') ? 'input' : 'change', () => update({[key]:key.endsWith('_size') ? Number(control.value) : control.value}));
    }
    document.getElementById('appearance-retry')?.addEventListener('click', () => { void retry(); });
    global.BioI18n?.onChange(updateControls);
  }
  async function init() {
    if (initialized) return;
    initialized = true; bindControls(); apply(); await load();
  }
  if (system?.addEventListener) system.addEventListener('change', () => { if (current.scheme === 'system') apply(); });
  else system?.addListener?.(() => { if (current.scheme === 'system') apply(); });
  apply();
  global.BioAppearance = { init, update, flush, retry, getPreferences: () => ({ ...current }), getResolvedScheme: resolvedScheme };
  if (global.document?.readyState === 'loading') global.document.addEventListener('DOMContentLoaded', init, { once:true });
  else if (global.document) void init();
})(window);
