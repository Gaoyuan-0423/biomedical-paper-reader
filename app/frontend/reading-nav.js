(function () {
  'use strict';
  const t = (key, params = {}) => window.BioI18n?.t(key, params) ?? key.replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? '{' + name + '}'));
  function ui(node, key, params = {}) { node.dataset.i18n = key; node.dataset.i18nParams = JSON.stringify(params); node.textContent = t(key, params); return node; }
  function uiElement(tag, className, key, params) { return ui(element(tag, className), key, params); }
  const documentLabel = () => state.documentId === 'main' && ['正文', 'Main text', ''].includes(state.documentName) ? t('正文') : state.documentName || t('补充材料');
  const PAPER_ID = /^[0-9a-f]{32}$/, FIGURE_ID = /^[a-z]\d+$/;
  const documentId = value => value === 'main' || PAPER_ID.test(value || '');
  const tabs = [['outline', '章节'], ['figures', '图表'], ['notes', '笔记']];
  let container, hooks = {}, shell, toggleButton, content, tabbar, list, feedback;
  const state = { paper: null, documentId: 'main', documentName: '正文', pdf: null, open: false, tab: 'outline', outline: [], figures: [], headings: [], loading: false, outlineError: '', actionError: '', truncated: false, epoch: 0, action: 0, busy: false, loadPromise: null };
  const label = (value, fallback = '') => typeof value === 'string' && value.trim() ? value.trim().slice(0, 2000) : fallback;
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function button(action, text, index) {
    const node = element('button', 'reading-nav-item', text);
    node.type = 'button'; node.dataset.readingNavAction = action;
    if (index !== undefined) node.dataset.readingNavIndex = String(index);
    return node;
  }
  function empty(key) { list.appendChild(uiElement('p', 'reading-nav-empty', key)); }
  function currentFigures() { return state.figures.filter(figure => figure.document_id === state.documentId); }
  function render() {
    if (!container) return;
    container.hidden = !state.paper;
    toggleButton.setAttribute('aria-expanded', String(state.open));
    ui(toggleButton, state.open ? '收起阅读导航' : '阅读导航 · 章节 / 图表 / 笔记');
    content.hidden = !state.open;
    tabbar.querySelectorAll('[data-reading-nav-action="tab"]').forEach(node => {
      const selected = node.dataset.readingNavTab === state.tab;
      node.setAttribute('aria-selected', String(selected)); node.tabIndex = selected ? 0 : -1;
    });
    list.setAttribute('aria-labelledby', 'reading-nav-tab-' + state.tab);
    list.setAttribute('aria-busy', String(state.loading && state.tab === 'outline'));
    list.replaceChildren();
    feedback.textContent = state.actionError.startsWith('无法跳转：') ? t('无法跳转：{error}', { error: t(state.actionError.slice(5)) }) : t(state.actionError);
    feedback.hidden = !state.actionError;
    if (!state.paper || !state.open) return;
    if (state.tab === 'outline') {
      list.appendChild(uiElement('p', 'reading-nav-caption', '{document} · PDF 自带书签', { document: documentLabel() }));
      if (state.loading) { empty('正在读取 PDF 章节…'); return; }
      if (state.outlineError) {
        empty(state.outlineError); list.appendChild(ui(button('retry'), '重新读取章节')); return;
      }
      if (!state.pdf) { empty('打开 PDF 后可查看它自带的章节。'); return; }
      if (!state.outline.length) { empty('这个 PDF 没有内置章节书签，可按页码阅读或切换到图表导航。'); return; }
      state.outline.forEach((entry, index) => {
        const node = button('page', '', index); node.style.setProperty('--reading-nav-depth', Math.min(entry.depth, 6));
        node.appendChild(entry.untitled ? uiElement('span', 'reading-nav-title', '未命名书签') : element('span', 'reading-nav-title', entry.title));
        node.appendChild(uiElement('span', 'reading-nav-meta', entry.page ? '第 {page} 页' : '无可用页码', { page: entry.page }));
        node.disabled = !entry.page || state.busy;
        if (!entry.page) { node.dataset.i18nTitle = '此书签没有可解析的 PDF 内部页码'; node.title = t(node.dataset.i18nTitle); }
        list.appendChild(node);
      });
      if (state.truncated) empty('章节较多，仅显示前 500 项。');
    } else if (state.tab === 'figures') {
      const figures = currentFigures();
      list.appendChild(uiElement('p', 'reading-nav-caption', '{document} · 已提取图表', { document: documentLabel() }));
      if (!figures.length) { empty('当前文件还没有已提取图表。可在原文中使用“取图”，或切换阅读文件查看补充图。'); return; }
      figures.forEach((figure, index) => {
        const node = button('figure', '', index);
        node.appendChild(element('span', 'reading-nav-title', figure.label || figure.id));
        node.appendChild(uiElement('span', 'reading-nav-meta', '第 {page} 页', { page: figure.page }));
        if (figure.caption) { node.title = figure.caption; node.appendChild(element('span', 'reading-nav-figure-caption', figure.caption)); }
        node.disabled = state.busy || !!state.pdf && figure.page > state.pdf.numPages;
        list.appendChild(node);
      });
    } else {
      list.appendChild(uiElement('p', 'reading-nav-caption', '当前论文 · 笔记标题'));
      if (!state.headings.length) { empty('笔记还没有标题。生成精读笔记，或用 Markdown 标题整理后可在这里跳转。'); return; }
      state.headings.forEach((heading, index) => {
        const node = button('heading', heading.title, index);
        node.style.setProperty('--reading-nav-depth', heading.level - 1); node.disabled = state.busy;
        list.appendChild(node);
      });
    }
  }
  // A destination must point inside this PDF. URI/Launch actions are never run.
  async function resolveDestination(pdf, target) {
    let dest = target;
    if (typeof dest === 'string' && dest.length && dest.length <= 2000) dest = await pdf.getDestination(dest);
    if (!Array.isArray(dest) || dest.length < 2 || !dest[1] || typeof dest[1] !== 'object') return null;
    const lengths = { XYZ: 5, Fit: 2, FitH: 3, FitV: 3, FitR: 6, FitB: 2, FitBH: 3, FitBV: 3 };
    const mode = dest[1].name;
    if (!Object.prototype.hasOwnProperty.call(lengths, mode) || dest.length !== lengths[mode]) return null;
    if (!dest.slice(2).every(value => value === null || typeof value === 'number' && Number.isFinite(value))) return null;
    if (mode === 'FitR' && dest.slice(2).some(value => value === null)) return null;
    const ref = dest[0];
    let index;
    if (Number.isInteger(ref) && ref >= 0) index = ref;
    else if (ref && typeof ref === 'object' && Number.isInteger(ref.num) && ref.num >= 0 && Number.isInteger(ref.gen) && ref.gen >= 0) index = await pdf.getPageIndex({ num: ref.num, gen: ref.gen });
    else return null;
    if (!Number.isInteger(index) || index < 0 || index >= pdf.numPages) return null;
    return { page: index + 1, destination: [index, { name: mode }, ...dest.slice(2)] };
  }
  function flattenOutline(outline) {
    const result = [], seen = new Set(); let truncated = false;
    function visit(items, depth) {
      if (!Array.isArray(items) || depth > 20) { if (depth > 20) truncated = true; return; }
      for (const item of items) {
        if (result.length >= 500) { truncated = true; return; }
        if (!item || typeof item !== 'object' || seen.has(item)) continue;
        seen.add(item);
        result.push({ title: label(item.title, '未命名书签'), untitled: !label(item.title), depth, dest: item.url || item.action ? null : item.dest, page: null, destination: null });
        visit(item.items, depth + 1);
      }
    }
    visit(outline, 0); return { entries: result, truncated };
  }
  async function loadOutline() {
    const pdf = state.pdf, epoch = state.epoch;
    if (!pdf) { render(); return; }
    state.loading = true; state.outlineError = ''; state.outline = []; state.truncated = false; render();
    const live = () => state.pdf === pdf && state.epoch === epoch;
    const job = (async () => {
      try {
        const outline = await pdf.getOutline();
        if (!live()) return;
        const flattened = flattenOutline(outline);
        // Bounded concurrency keeps large native outlines responsive.
        for (let start = 0; start < flattened.entries.length; start += 8) {
          await Promise.all(flattened.entries.slice(start, start + 8).map(async entry => {
            try { Object.assign(entry, await resolveDestination(pdf, entry.dest) || {}); }
            catch (_) { /* Keep this native title visible, without guessing a page. */ }
          }));
          if (!live()) return;
        }
        state.outline = flattened.entries; state.truncated = flattened.truncated;
      } catch (error) { if (live()) state.outlineError = '无法读取 PDF 自带章节，可重试或使用页码导航。'; }
      finally { if (live()) { state.loading = false; render(); } }
    })();
    state.loadPromise = job; await job;
    if (state.loadPromise === job) state.loadPromise = null;
  }
  function setPaper(paper) {
    const next = paper && PAPER_ID.test(paper.id || '') ? paper : null;
    if (next?.id === state.paper?.id && next?.material_revision === state.paper?.material_revision) { render(); return; }
    state.paper = next ? { id: next.id, material_revision: next.material_revision } : null; state.epoch++; state.action++; state.busy = false; state.pdf = null;
    state.documentId = 'main'; state.documentName = '正文'; state.outline = []; state.figures = []; state.headings = [];
    state.loading = false; state.outlineError = ''; state.actionError = ''; state.loadPromise = null; render();
  }
  async function setDocument(context) {
    if (!state.paper || context?.paperId !== state.paper.id || !documentId(context.documentId)) return;
    const pdf = context.pdf || null;
    if (state.documentId === context.documentId && state.pdf === pdf) { state.documentName = label(context.documentName, context.documentId === 'main' ? '正文' : '补充材料'); render(); return state.loadPromise; }
    state.epoch++; state.action++; state.busy = false;
    state.documentId = context.documentId; state.documentName = label(context.documentName, context.documentId === 'main' ? '正文' : '补充材料');
    state.pdf = pdf; state.outline = []; state.loading = false; state.outlineError = ''; state.actionError = ''; state.loadPromise = null;
    render(); return loadOutline();
  }
  function setFigures(payload) {
    if (!state.paper || payload?.paperId !== state.paper.id) return;
    const seen = new Set();
    state.figures = (Array.isArray(payload.figures) ? payload.figures : []).filter(item => {
      const doc = item?.document_id || 'main';
      if (!item || !FIGURE_ID.test(item.id || '') || !documentId(doc) || !Number.isInteger(item.page) || item.page < 1 || item.page > 600 || seen.has(item.id)) return false;
      seen.add(item.id); return true;
    }).map(item => ({ id: item.id, document_id: item.document_id || 'main', page: item.page, label: label(item.label, item.id), caption: label(item.caption), document_name: label(item.document_name) }));
    render();
  }
  function setNoteHeadings(payload) {
    if (!state.paper || payload?.paperId !== state.paper.id) return;
    const seen = new Set();
    state.headings = (Array.isArray(payload.headings) ? payload.headings : []).filter(item => {
      if (!item || typeof item.id !== 'string' || !item.id || item.id.length > 200 || seen.has(item.id) || !label(item.title)) return false;
      seen.add(item.id); return true;
    }).map(item => ({ id: item.id, title: label(item.title), level: Number.isInteger(item.level) ? Math.min(6, Math.max(1, item.level)) : 1 }));
    render();
  }
  function setOpen(open) {
    state.open = !!open;
    if (!state.open) { state.action++; state.busy = false; state.actionError = ''; }
    render(); hooks.onToggle?.(state.open);
  }
  async function navigate(kind, index) {
    if (!state.paper || !state.open || state.busy || !Number.isInteger(index) || index < 0) return;
    const paperId = state.paper.id, document = state.documentId;
    let target, hook;
    if (kind === 'page') {
      const entry = state.outline[index];
      if (!entry?.page || state.loading) return;
      target = { paperId, documentId: document, page: entry.page, destination: entry.destination }; hook = hooks.onPage;
    } else if (kind === 'figure') {
      const figure = currentFigures()[index];
      if (!figure || state.pdf && figure.page > state.pdf.numPages) return;
      target = { ...figure, paperId }; hook = hooks.onFigure;
    } else if (kind === 'heading') {
      const heading = state.headings[index]; if (!heading) return;
      target = { ...heading, paperId }; hook = hooks.onHeading;
    }
    if (!hook) return;
    const action = ++state.action, epoch = state.epoch;
    const isCurrent = () => !!state.paper && state.paper.id === paperId && state.documentId === document && state.epoch === epoch && state.action === action && state.open;
    state.busy = true; state.actionError = ''; render();
    try {
      const result = await hook(target, { isCurrent });
      if (isCurrent() && result === false) state.actionError = '暂时无法跳转，请确认文件和笔记已加载后重试。';
    } catch (error) { if (isCurrent()) state.actionError = '无法跳转：' + label(error?.message, '请稍后重试'); }
    finally { if (isCurrent()) { state.busy = false; render(); } }
  }
  function init(next, options = {}) {
    hooks = options;
    if (!next || next === container) return;
    container = next; container.classList.add('reading-nav');
    container.replaceChildren();
    shell = element('section', 'reading-nav-shell');
    toggleButton = ui(button('toggle'), '阅读导航'); toggleButton.className = 'reading-nav-toggle'; toggleButton.setAttribute('aria-controls', 'reading-nav-content');
    content = element('div', 'reading-nav-content'); content.id = 'reading-nav-content';
    tabbar = element('div', 'reading-nav-tabs'); tabbar.setAttribute('role', 'tablist'); tabbar.dataset.i18nAriaLabel = '阅读导航范围'; tabbar.setAttribute('aria-label', t('阅读导航范围'));
    tabs.forEach(([id, title]) => {
      const tab = ui(button('tab'), title); tab.className = 'reading-nav-tab'; tab.id = 'reading-nav-tab-' + id; tab.dataset.readingNavTab = id;
      tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', 'reading-nav-list'); tabbar.appendChild(tab);
    });
    list = element('div', 'reading-nav-list'); list.id = 'reading-nav-list'; list.setAttribute('role', 'tabpanel'); list.tabIndex = 0;
    feedback = element('p', 'reading-nav-feedback'); feedback.setAttribute('role', 'status');
    content.appendChild(tabbar); content.appendChild(list); content.appendChild(feedback); shell.appendChild(toggleButton); shell.appendChild(content); container.appendChild(shell);
    container.addEventListener('click', event => {
      const node = event.target.closest('[data-reading-nav-action]');
      if (!node || !container.contains(node) || node.disabled) return;
      const action = node.dataset.readingNavAction;
      if (action === 'toggle') { setOpen(!state.open); return; }
      if (action === 'tab' && tabs.some(([id]) => id === node.dataset.readingNavTab)) { state.tab = node.dataset.readingNavTab; state.actionError = ''; render(); return; }
      if (action === 'retry' && !state.loading) return loadOutline();
      if (/^(?:0|[1-9]\d*)$/.test(node.dataset.readingNavIndex || '')) return navigate(action, Number(node.dataset.readingNavIndex));
    });
    tabbar.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const current = event.target.closest('[data-reading-nav-action="tab"]');
      if (!current) return;
      event.preventDefault();
      const index = tabs.findIndex(([id]) => id === current.dataset.readingNavTab);
      state.tab = tabs[event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length][0];
      render(); tabbar.querySelector('[data-reading-nav-tab="' + state.tab + '"]')?.focus();
    });
    render();
  }
  window.BioI18n?.onChange(() => {
    // Only labels change: keep the outline nodes, focus, scroll and active navigation task.
    const caption = list?.querySelector('.reading-nav-caption');
    if (caption && state.tab !== 'notes') ui(caption, state.tab === 'outline' ? '{document} · PDF 自带书签' : '{document} · 已提取图表', { document: documentLabel() });
    if (feedback) feedback.textContent = state.actionError.startsWith('无法跳转：') ? t('无法跳转：{error}', { error: t(state.actionError.slice(5)) }) : t(state.actionError);
  });
  window.BioReadingNav = { init, setPaper, setDocument, setFigures, setNoteHeadings, setOpen, toggle: () => setOpen(!state.open), isOpen: () => state.open };
})();
