/* Literal find uses the open PDF.js document, including pages outside the virtual viewport. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const t = (key, params) => window.BioI18n?.t(key, params) ?? key.replace(/\{(\w+)\}/g, (_, name) => String(params?.[name] ?? ''));
  window.BioI18n?.register({
    '正在查找…': 'Searching…', '未找到匹配项': 'No matches',
    '有 {count} 页无法检索': '{count} page(s) could not be searched',
  });
  let hooks, pdf, pages = new Map(), generation = 0, timer;
  let pattern, results = [], total = 0, index = -1, searching = false, failed = 0;
  let marks = [], scrollToHit = false, pointerInReader = false;

  function removeMarks() {
    for (const mark of marks) mark.remove();
    marks = [];
  }
  function reset() {
    ++generation;
    clearTimeout(timer); timer = null;
    results = []; total = 0; index = -1; pattern = null; failed = 0; searching = false; scrollToHit = false;
    removeMarks();
    status();
  }
  function status() {
    if (!hooks) return;
    const label = total ? (index + 1) + ' / ' + total : pattern && !searching ? t('未找到匹配项') : '';
    const detail = searching ? t('正在查找…') : failed ? t('有 {count} 页无法检索', {count: failed}) : '';
    $('pdf-find-count').textContent = [label, detail].filter(Boolean).join(' · ');
    $('pdf-find-prev').disabled = $('pdf-find-next').disabled = total < 2;
  }
  function close(focus = true) {
    reset();
    $('pdf-find-bar').hidden = true;
    $('pdf-find-toggle').setAttribute('aria-expanded', 'false');
    $('pdf-find-query').value = '';
    if (focus && pdf) $('pdf-find-toggle').focus();
  }
  function open() {
    if (!pdf) return;
    $('pdf-find-bar').hidden = false;
    $('pdf-find-toggle').setAttribute('aria-expanded', 'true');
    $('pdf-find-query').focus(); $('pdf-find-query').select();
  }
  function setDocument(documentProxy) {
    if (pdf === documentProxy) return;
    pdf = documentProxy;
    pages = new Map();
    if (!hooks) return;
    close(false);
    $('pdf-find-toggle').disabled = !pdf;
  }
  function pageText(doc, number) {
    if (pages.has(number)) return pages.get(number);
    const cache = pages;
    const pending = doc.getPage(number).then(page => page.getTextContent()).then(content =>
      content.items.filter(item => typeof item.str === 'string').map(item => item.str + (item.hasEOL ? '\n' : '')).join('')
    ).catch(error => { cache.delete(number); throw error; });
    cache.set(number, pending);
    return pending;
  }
  function current() {
    return results.find(result => index < result.first + result.count);
  }
  function patternFor(text) {
    const parts = text.trim().split(/\s+/u).map(part => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    return new RegExp(parts.join('\\s+'), 'giu');
  }
  function paint(doc, number, layer) {
    const hit = current();
    if (pdf !== doc || !layer || !hit || hit.page !== number || !pattern) return;
    removeMarks();
    // PDF.js emits one span per nonempty text item and a BR for hasEOL.
    // Match the same occurrence in the rendered text and use actual glyph ranges.
    let text = '';
    const spans = [];
    for (const element of layer.querySelectorAll('span:not(.markedContent), br')) {
      if (element.tagName === 'BR') { text += '\n'; continue; }
      const node = element.firstChild;
      if (node?.nodeType !== 3) continue;
      spans.push({node, start: text.length, end: text.length + node.length});
      text += node.data;
    }
    pattern.lastIndex = 0;
    let match;
    for (let i = hit.first; i <= index; i++) {
      match = pattern.exec(text);
      if (!match) return;
    }
    const start = match.index, end = start + match[0].length;
    const wrapper = layer.closest('.pdf-page'), bounds = wrapper?.getBoundingClientRect();
    if (!bounds?.width || !bounds.height) return;
    for (const span of spans) {
      if (span.end <= start || span.start >= end) continue;
      const range = document.createRange();
      range.setStart(span.node, Math.max(0, start - span.start));
      range.setEnd(span.node, Math.min(span.node.length, end - span.start));
      for (const rect of range.getClientRects()) {
        if (!rect.width || !rect.height) continue;
        const mark = document.createElement('div');
        mark.className = 'pdf-find-highlight';
        mark.setAttribute('aria-hidden', 'true');
        mark.style.left = (rect.left - bounds.left) / bounds.width * 100 + '%';
        mark.style.top = (rect.top - bounds.top) / bounds.height * 100 + '%';
        mark.style.width = rect.width / bounds.width * 100 + '%';
        mark.style.height = rect.height / bounds.height * 100 + '%';
        wrapper.appendChild(mark); marks.push(mark);
      }
    }
    if (scrollToHit && marks.length) {
      marks[0].scrollIntoView({block: 'nearest', inline: 'nearest'});
      scrollToHit = false;
    }
  }
  function navigate(next) {
    if (!total) return;
    index = (next + total) % total;
    const page = current().page;
    removeMarks(); scrollToHit = true;
    hooks.showPage(page);
    paint(pdf, page, hooks.layer(page));
    status();
  }
  async function scan() {
    clearTimeout(timer); timer = null;
    const doc = pdf, token = generation, re = pattern;
    for (let page = 1; page <= doc.numPages; page++) {
      let text;
      try { text = await pageText(doc, page); }
      catch (_) {
        if (token !== generation) return;
        failed++; continue;
      }
      if (token !== generation) return;
      re.lastIndex = 0;
      let count = 0;
      while (re.exec(text)) count++;
      if (count) {
        results.push({page, first: total, count});
        total += count;
        if (index < 0) navigate(0);
        else status();
      }
    }
    if (token === generation) { searching = false; status(); }
  }
  function changed() {
    reset();
    const query = $('pdf-find-query').value.trim();
    if (!pdf || !query) return;
    pattern = patternFor(query);
    searching = true;
    status();
    timer = setTimeout(scan, 140);
  }
  function readerShortcut(event) {
    if (!pdf || event.defaultPrevented || event.isComposing) return;
    if (document.querySelector('.modal-backdrop:not([hidden]), dialog[open]') || window.BioFigureReader?.isOpen()) return;
    const inReader = !!event.target?.closest?.('#pdf-pane') || (event.target === document.body && pointerInReader);
    if (!inReader) return;
    const key = event.key.toLowerCase();
    if (key === 'f' && (event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey) {
      event.preventDefault(); open();
    } else if (key === 'escape' && !$('pdf-find-bar').hidden) {
      event.preventDefault(); close();
    }
  }
  function init(options) {
    hooks = options;
    $('pdf-find-toggle').onclick = () => $('pdf-find-bar').hidden ? open() : close();
    $('pdf-find-close').onclick = () => close();
    $('pdf-find-next').onclick = () => navigate(index + 1);
    $('pdf-find-prev').onclick = () => navigate(index - 1);
    const input = $('pdf-find-query');
    input.addEventListener('input', changed);
    input.addEventListener('keydown', event => {
      if (event.key !== 'Enter' || event.isComposing) return;
      event.preventDefault();
      if (timer !== null && pattern) void scan();
      else navigate(index + (event.shiftKey ? -1 : 1));
    });
    document.addEventListener('pointerdown', event => { pointerInReader = !!event.target.closest?.('#pdf-pane'); }, true);
    document.addEventListener('focusin', event => {
      if (event.target !== document.body) pointerInReader = !!event.target.closest?.('#pdf-pane');
    });
    document.addEventListener('keydown', readerShortcut);
    window.BioI18n?.onChange(status);
  }
  window.BioPdfFind = {init, setDocument, paint};
})();
