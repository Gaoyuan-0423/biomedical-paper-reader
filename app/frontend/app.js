(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const PDF_WORKER = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.worker.min.js';
  const DOCX_CDN = 'https://cdn.jsdelivr.net/npm/docx@8.5.0/build/index.umd.js';
  const MODE_NAMES = { deep: '精读', quick: '速读' };

  // ---------- API ----------
  // Paper/figure ids reach fetch URLs (including from the ?paper= query param),
  // so every id-built path goes through this validated builder: anything that is
  // not a backend-issued hex id / figure id is rejected before hitting the network.
  const ID_RE = /^[0-9a-f]{32}$/;
  const FIG_RE = /^[a-z]\d{3,6}$/;
  function paperPath(id, suffix) {
    if (!ID_RE.test(String(id || ''))) throw new Error('无效的文献 ID');
    return '/api/papers/' + encodeURIComponent(id) + (suffix || '');
  }
  function figPath(id, fid, suffix) {
    if (!FIG_RE.test(String(fid || ''))) throw new Error('无效的图片 ID');
    return paperPath(id, '/figures/' + encodeURIComponent(fid) + (suffix || ''));
  }
  const api = {
    async json(url, opts = {}) {
      const resp = await fetch(url, opts);
      if (!resp.ok) {
        let msg = resp.statusText;
        try { const body = await resp.json(); msg = body.detail || JSON.stringify(body); } catch (e) { /* keep statusText */ }
        throw new Error(msg);
      }
      return resp.json();
    },
    jsonBody(method, url, body) {
      return api.json(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    },
    settings: () => api.json('/api/settings'),
    saveSettings: (body) => api.jsonBody('PUT', '/api/settings', body),
    clearKey: () => api.json('/api/settings/api-key', { method: 'DELETE' }),
    papers: () => api.json('/api/papers'),
    paper: (id) => api.json(paperPath(id)),
    upload(file) {
      const fd = new FormData();
      fd.append('file', file);
      return api.json('/api/papers', { method: 'POST', body: fd });
    },
    attach(id, file) {
      const fd = new FormData();
      fd.append('file', file);
      return api.json(paperPath(id, '/file'), { method: 'POST', body: fd });
    },
    importDoi: (doi) => api.jsonBody('POST', '/api/import/doi', { doi }),
    enrich: (id, body) => api.jsonBody('POST', paperPath(id, '/enrich'), body),
    related: (id, refresh) => api.json(paperPath(id, '/related') + (refresh ? '?refresh=1' : '')),
    patch: (id, body) => api.jsonBody('PATCH', paperPath(id), body),
    remove: (id) => api.json(paperPath(id), { method: 'DELETE' }),
    figures: (id) => api.json(paperPath(id, '/figures')),
    reextract: (id) => api.json(paperPath(id, '/figures/extract'), { method: 'POST' }),
    cropFigure: (id, body) => api.jsonBody('POST', paperPath(id, '/figures/crop'), body),
    patchFigure: (id, fid, body) => api.jsonBody('PATCH', figPath(id, fid), body),
    removeFigure: (id, fid) => api.json(figPath(id, fid), { method: 'DELETE' }),
    readFigure: (id, fid, body) => api.jsonBody('POST', figPath(id, fid, '/read'), body),
    figUrl: (id, fid) => figPath(id, fid, '.png'),
  };

  // ---------- state ----------
  const state = {
    settings: null,
    papers: [],
    current: null,
    favOnly: false,
    activeTag: '',
    query: '',
    tab: 'preview',
    pdf: null,
    pageWrappers: [],
    scale: 1,
    fitWidth: true,
    renderToken: 0,
    generating: null,
    saveTimer: null,
    pendingSave: null,
    figures: [],
    figDataUrls: {},
    figReading: false,
    cropping: false,
    cropDrag: null,
    pendingPaper: null,
    pendingFigure: '',
    relatedData: null,
  };

  // ---------- helpers ----------
  let toastTimer = null;
  function toast(message, kind) {
    const el = $('toast');
    el.textContent = message;
    el.className = 'toast' + (kind ? ' ' + kind : '');
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, kind === 'error' ? 4500 : 2500);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function icon(name) {
    return '<svg class="icon" aria-hidden="true"><use href="#i-' + name + '"></use></svg>';
  }

  function emptyState(symbol, title, copy) {
    return '<div class="empty"><div class="empty-symbol">' + icon(symbol) + '</div>' +
      '<div class="empty-title">' + escapeHtml(title) + '</div>' +
      '<div class="empty-copy">' + escapeHtml(copy) + '</div></div>';
  }

  function setWorkspaceView(view) {
    document.body.dataset.view = view;
    document.querySelectorAll('#workspace-views [data-view]').forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    if (view === 'pdf' && state.fitWidth && state.pdf) requestAnimationFrame(rerenderPdf);
  }

  const modalFocus = new Map();
  let inputResolve = null;
  function openModal(id, focusId) {
    const modal = $(id);
    if (modal.hidden) modalFocus.set(id, document.activeElement);
    modal.hidden = false;
    const focus = focusId ? $(focusId) : modal.querySelector('button, input, textarea, [tabindex]');
    if (focus) focus.focus();
  }

  function closeModal(id) {
    $(id).hidden = true;
    const previous = modalFocus.get(id);
    modalFocus.delete(id);
    if (previous && previous.isConnected) previous.focus();
  }

  function finishInput(value) {
    closeModal('input-modal');
    const resolve = inputResolve;
    inputResolve = null;
    if (resolve) resolve(value);
  }

  function requestText(title, description, value, submitLabel) {
    if (inputResolve) finishInput(null);
    $('input-title').textContent = title;
    $('input-description').textContent = description;
    $('input-value').value = value || '';
    $('input-submit').textContent = submitLabel;
    openModal('input-modal', 'input-value');
    return new Promise((resolve) => { inputResolve = resolve; });
  }

  function setSaveStatus(message, kind) {
    const el = $('save-status');
    if (!el) return;
    el.textContent = message;
    el.dataset.state = kind || '';
  }

  function formatDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return isNaN(d) ? '' : d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
  }

  function downloadBlob(filename, content, type) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([content], { type }));
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  function downloadRaw(filename, blob) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  function safeFilename(name) {
    return (name || 'notes').replace(/[\\/:*?"<>|]+/g, '_').slice(0, 80);
  }

  function ensureDocx() {
    if (window.docx) return Promise.resolve(true);
    return new Promise((resolve) => {
      const s = document.createElement('script');
      s.src = DOCX_CDN;
      s.onload = () => resolve(!!window.docx);
      s.onerror = () => resolve(false);
      document.head.appendChild(s);
    });
  }

  // ---------- settings ----------
  function renderStatusPill() {
    const pill = $('status-pill');
    if (state.settings && state.settings.has_api_key) {
      pill.textContent = 'API · ' + (state.settings.model || '未指定模型') + (state.settings.vision_model ? ' +读图' : '');
      pill.className = 'pill pill-ok';
    } else {
      pill.textContent = '未设置 API Key';
      pill.className = 'pill pill-warn';
    }
  }

  async function loadSettings() {
    try {
      state.settings = await api.settings();
    } catch (e) {
      state.settings = { has_api_key: false };
      toast('读取设置失败：' + e.message, 'error');
    }
    renderStatusPill();
  }

  function openSettings() {
    const s = state.settings || {};
    $('set-base-url').value = s.base_url || '';
    $('set-model').value = s.model || '';
    $('set-vision-model').value = s.vision_model || '';
    $('set-max-chars').value = s.max_chars || 90000;
    $('set-max-tokens').value = s.max_tokens || 0;
    $('set-max-figures').value = s.max_figures == null ? 8 : s.max_figures;
    $('set-direction').value = s.research_direction || '';
    $('set-s2-key').value = '';
    $('set-api-key').value = '';
    $('set-key-hint').textContent = s.has_api_key ? '当前已保存 Key：' + (s.api_key_masked || '已设置') : '尚未设置 Key';
    $('set-s2-hint').textContent = s.has_s2_api_key ? '已保存 Semantic Scholar Key（留空保持不变）' : '未设置；匿名额度较慢，免费申请 semanticscholar.org/product/api';
    openModal('settings-modal', 'set-base-url');
  }

  function closeSettings() { closeModal('settings-modal'); }

  async function saveSettings() {
    const body = {
      base_url: $('set-base-url').value.trim(),
      model: $('set-model').value.trim(),
      vision_model: $('set-vision-model').value.trim(),
      max_chars: parseInt($('set-max-chars').value, 10) || 90000,
      max_tokens: parseInt($('set-max-tokens').value, 10) || 0,
      max_figures: parseInt($('set-max-figures').value, 10) || 0,
      research_direction: $('set-direction').value.trim(),
    };
    const key = $('set-api-key').value.trim();
    if (key) body.api_key = key;
    const s2 = $('set-s2-key').value.trim();
    if (s2) body.s2_api_key = s2;
    try {
      state.settings = await api.saveSettings(body);
      renderStatusPill();
      closeSettings();
      toast('设置已保存', 'ok');
    } catch (e) {
      toast('保存失败：' + e.message, 'error');
    }
  }

  async function clearApiKey() {
    try {
      state.settings = await api.clearKey();
      renderStatusPill();
      $('set-key-hint').textContent = '尚未设置 Key';
      toast('已清除 API Key');
    } catch (e) {
      toast('操作失败：' + e.message, 'error');
    }
  }

  // ---------- library ----------
  function visiblePapers() {
    const q = state.query.trim().toLowerCase();
    return state.papers.filter((p) => {
      if (state.favOnly && !p.favorite) return false;
      if (state.activeTag && !(p.tags || []).includes(state.activeTag)) return false;
      if (!q) return true;
      return [p.title, p.authors, p.doi, p.filename, p.journal, (p.tags || []).join(' ')].join(' ').toLowerCase().includes(q);
    });
  }

  function renderTagChips() {
    const tags = [];
    for (const p of state.papers) for (const t of p.tags || []) if (!tags.includes(t)) tags.push(t);
    const box = $('tag-chips');
    if (!tags.length) { box.hidden = true; box.innerHTML = ''; return; }
    box.hidden = false;
    box.innerHTML = tags.slice(0, 30).map((t) =>
      '<button class="chip' + (state.activeTag === t ? ' on' : '') + '" data-tag="' + escapeHtml(t) + '" aria-pressed="' + (state.activeTag === t) + '">' + escapeHtml(t) +
      (state.activeTag === t ? '<span class="x">' + icon('close') + '</span>' : '') + '</button>').join('');
  }

  function renderList() {
    const list = $('paper-list');
    const items = visiblePapers();
    list.innerHTML = items.map((p) => {
      const active = state.current && state.current.id === p.id ? ' active' : '';
      const meta = [
        p.year ? escapeHtml(p.year) : '',
        p.journal ? escapeHtml(p.journal) : '',
        p.pages ? p.pages + ' 页' : '',
        formatDate(p.added_at),
        p.notes_updated_at ? '<span class="badge badge-notes">有笔记</span>' : '',
        p.figures_count ? '<span class="badge badge-fig">图 ' + p.figures_count + '</span>' : '',
        p.source === 'doi' && !p.has_file ? '<span class="badge badge-src">缺 PDF</span>' : '',
      ].filter(Boolean).map((m) => '<span>' + m + '</span>').join('');
      const tagRow = (p.tags || []).length
        ? '<div class="paper-item-tags">' + p.tags.slice(0, 4).map((t) => '<span>#' + escapeHtml(t) + '</span>').join('') + '</div>' : '';
      return '<li class="paper-item' + active + '" data-id="' + p.id + '">' +
        '<button class="star' + (p.favorite ? ' on' : '') + '" data-star="' + p.id + '" title="' + (p.favorite ? '取消收藏' : '收藏文献') + '" aria-pressed="' + !!p.favorite + '">' + icon('star') + '</button>' +
        '<div class="paper-item-text"><button class="paper-open paper-item-title" data-id="' + p.id + '" title="' + escapeHtml(p.title) + '"' + (active ? ' aria-current="true"' : '') + '>' + escapeHtml(p.title) + '</button>' +
        '<div class="paper-item-meta">' + meta + '</div>' + tagRow + '</div></li>';
    }).join('');
    const favCount = state.papers.filter((p) => p.favorite).length;
    if ($('library-total')) $('library-total').textContent = state.papers.length;
    if ($('favorite-total')) $('favorite-total').textContent = favCount;
    $('btn-fav-filter').classList.toggle('btn-fav-active', state.favOnly);
    $('btn-fav-filter').classList.toggle('active', state.favOnly);
    $('btn-fav-filter').setAttribute('aria-pressed', String(state.favOnly));
    if ($('btn-all-filter')) {
      $('btn-all-filter').classList.toggle('active', !state.favOnly);
      $('btn-all-filter').setAttribute('aria-pressed', String(!state.favOnly));
    }
    $('library-count').textContent = state.papers.length + ' 篇文献 · ' + favCount + ' 篇收藏' + (items.length !== state.papers.length ? ' · 显示 ' + items.length + ' 篇' : '');
    if (!items.length) {
      list.innerHTML = '<li class="library-empty">' + (state.papers.length
        ? emptyState('search', '没有找到文献', '试试其他关键词，或调整收藏与标签筛选。')
        : emptyState('book', '从第一篇文献开始', '导入 PDF，或通过 DOI 添加文献。')) + '</li>';
    }
    renderTagChips();
  }

  async function loadPapers() {
    try {
      state.papers = await api.papers();
    } catch (e) {
      toast('读取文献库失败：' + e.message, 'error');
      state.papers = [];
    }
    renderList();
  }

  function updateListItem(paper) {
    const idx = state.papers.findIndex((p) => p.id === paper.id);
    const summary = Object.assign({}, paper);
    delete summary.notes;
    delete summary.abstract;
    if (idx >= 0) state.papers[idx] = summary; else state.papers.unshift(summary);
    renderList();
  }

  async function toggleFavorite(id) {
    const paper = state.papers.find((p) => p.id === id);
    if (!paper) return;
    try {
      const updated = await api.patch(id, { favorite: !paper.favorite });
      updateListItem(updated);
      if (state.current && state.current.id === id) {
        state.current.favorite = updated.favorite;
        renderHead();
      }
      toast(updated.favorite ? '已收藏' : '已取消收藏');
    } catch (e) {
      toast('操作失败：' + e.message, 'error');
    }
  }

  async function uploadFiles(files) {
    const pdfs = Array.from(files).filter((f) => /\.pdf$/i.test(f.name) || f.type === 'application/pdf');
    if (!pdfs.length) { toast('只支持 PDF 文件', 'error'); return; }
    let last = null;
    for (const file of pdfs) {
      toast('正在导入 ' + file.name + ' …');
      try {
        last = await api.upload(file);
        updateListItem(last);
      } catch (e) {
        toast(file.name + ' 导入失败：' + e.message, 'error');
      }
    }
    if (last) {
      toast('导入完成：' + last.title, 'ok');
      state.pendingPaper = last;
      openPaper();
    }
  }

  async function deleteCurrent() {
    const paper = state.current;
    if (!paper) return;
    if (!confirm('确定删除「' + paper.title + '」及其笔记与图片？此操作不可恢复。')) return;
    try {
      await api.remove(paper.id);
      state.papers = state.papers.filter((p) => p.id !== paper.id);
      state.current = null;
      clearWorkspace();
      renderList();
      toast('已删除');
    } catch (e) {
      toast('删除失败：' + e.message, 'error');
    }
  }

  // ---------- DOI import / attach / enrich ----------
  function openDoiModal() {
    $('doi-input').value = '';
    openModal('doi-modal', 'doi-input');
  }

  async function submitDoi() {
    const doi = $('doi-input').value.trim();
    if (!doi) { toast('请输入 DOI', 'error'); return; }
    $('doi-submit').disabled = true;
    try {
      const meta = await api.importDoi(doi);
      updateListItem(meta);
      closeModal('doi-modal');
      toast('已导入：' + meta.title, 'ok');
      state.pendingPaper = meta;
      openPaper();
    } catch (e) {
      toast('导入失败：' + e.message, 'error');
    } finally {
      $('doi-submit').disabled = false;
    }
  }

  function chooseAttachPdf() {
    if (!state.current) return;
    $('attach-input').value = '';
    $('attach-input').click();
  }

  async function attachPdfFile(file) {
    const paper = state.current;
    if (!paper || !file) return;
    toast('正在解析 ' + file.name + ' …');
    try {
      const meta = await api.attach(paper.id, file);
      state.current = meta;
      updateListItem(meta);
      renderHead();
      loadPdf();
      await fetchFigures();
      $('btn-generate').disabled = false;
      toast('PDF 已附加，提取到 ' + (meta.figures_count || 0) + ' 张图', 'ok');
    } catch (e) {
      toast('附加失败：' + e.message, 'error');
    }
  }

  async function enrichCurrent() {
    const paper = state.current;
    if (!paper) return;
    toast('正在查询 CrossRef …');
    try {
      const meta = await api.enrich(paper.id, { overwrite: false });
      state.current = meta;
      updateListItem(meta);
      renderHead();
      toast('元数据已补全', 'ok');
    } catch (e) {
      toast('补全失败：' + e.message, 'error');
    }
  }

  // ---------- related (Semantic Scholar) ----------
  function relatedItemHtml(item) {
    const meta = [item.year, item.venue, item.authors, item.citations != null ? '被引 ' + item.citations : ''].filter(Boolean).join(' · ');
    const link = item.url ? '<a href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener">' + escapeHtml(item.title) + '</a>' : escapeHtml(item.title);
    const action = item.in_library
      ? '<button class="btn" disabled>已入库</button>'
      : (item.doi ? '<button class="btn" data-related-doi="' + escapeHtml(item.doi) + '">导入</button>' : '');
    return '<div class="related-item"><div class="related-text"><div class="related-title">' + link + '</div>' +
      '<div class="related-meta">' + escapeHtml(meta) + '</div></div>' + action + '</div>';
  }

  function renderRelated() {
    const data = state.relatedData;
    const body = $('related-body');
    if (!data) { body.innerHTML = '<div class="empty">暂无数据</div>'; return; }
    const focus = data.paper ? '<b>' + escapeHtml(data.paper.title) + '</b>（被引 ' + (data.paper.citations || 0) + '）' : '';
    body.innerHTML =
      '<div class="related-focus">锚定文献：' + focus + '。列表来自 Semantic Scholar，元数据以出版社为准。</div>' +
      '<div class="related-group"><h4>参考文献 · ' + data.references.length + '</h4>' +
      (data.references.map(relatedItemHtml).join('') || '<div class="hint">无</div>') + '</div>' +
      '<div class="related-group"><h4>引用本文 · ' + data.citations.length + '</h4>' +
      (data.citations.map(relatedItemHtml).join('') || '<div class="hint">无</div>') + '</div>';
    $('related-sub').textContent = '';
  }

  async function loadRelated(refresh) {
    const paper = state.current;
    if (!paper) return;
    openModal('related-modal', 'related-close');
    $('related-body').innerHTML = emptyState('network', refresh ? '正在更新相关文献' : '正在检索相关文献', '正在查询参考文献与引用本文的研究。');
    try {
      state.relatedData = await api.related(paper.id, refresh);
      renderRelated();
    } catch (e) {
      state.relatedData = null;
      $('related-body').innerHTML = emptyState('alert', '暂时无法获取相关文献', e.message);
    }
  }

  async function relatedImport(doi) {
    try {
      const meta = await api.importDoi(doi);
      updateListItem(meta);
      if (state.relatedData) {
        for (const group of ['references', 'citations']) {
          for (const item of state.relatedData[group]) {
            if (item.doi && item.doi.toLowerCase() === meta.doi.toLowerCase()) item.in_library = meta.id;
          }
        }
        renderRelated();
      }
      toast('已导入：' + meta.title, 'ok');
    } catch (e) {
      toast('导入失败：' + e.message, 'error');
    }
  }

  // ---------- current paper ----------
  function renderHead() {
    const paper = state.current;
    const head = $('paper-head');
    if (!paper) { head.hidden = true; return; }
    head.hidden = false;
    $('head-title').textContent = paper.title;
    const star = $('head-star');
    star.innerHTML = icon('star');
    star.className = 'star' + (paper.favorite ? ' on' : '');
    star.setAttribute('aria-pressed', String(!!paper.favorite));
    star.title = paper.favorite ? '取消收藏' : '收藏文献';
    const bits = [paper.authors, paper.year, paper.journal, paper.pages ? paper.pages + ' 页' : '',
      paper.doi ? 'DOI ' + paper.doi : '', '导入于 ' + formatDate(paper.added_at)];
    if (paper.notes_updated_at) bits.push('笔记 ' + formatDate(paper.notes_updated_at) + (paper.notes_mode ? '（' + (MODE_NAMES[paper.notes_mode] || paper.notes_mode) + '）' : ''));
    $('head-meta').textContent = bits.filter(Boolean).join(' · ');
    $('head-summary').textContent = [paper.journal, paper.year, paper.pages ? paper.pages + ' 页' : ''].filter(Boolean).join(' · ') || '文献信息';
    $('head-tags').innerHTML = (paper.tags || []).map((t) =>
      '<span class="chip on" data-tag="' + escapeHtml(t) + '">' + escapeHtml(t) + '<button class="x" data-x="' + escapeHtml(t) + '" aria-label="移除标签 ' + escapeHtml(t) + '">' + icon('close') + '</button></span>').join('') +
      '<input id="tag-input" placeholder="+ 标签" aria-label="添加文献标签" maxlength="40">';
    updateFigCount(paper.figures_count || 0);
  }

  async function addTag(tag) {
    const paper = state.current;
    tag = (tag || '').trim();
    if (!paper || !tag) return;
    const tags = (paper.tags || []).slice();
    if (tags.includes(tag)) return;
    tags.push(tag);
    try {
      const updated = await api.patch(paper.id, { tags });
      state.current.tags = updated.tags;
      updateListItem(updated);
      renderHead();
    } catch (e) {
      toast('添加标签失败：' + e.message, 'error');
    }
  }

  async function removeTag(tag) {
    const paper = state.current;
    if (!paper) return;
    const tags = (paper.tags || []).filter((t) => t !== tag);
    try {
      const updated = await api.patch(paper.id, { tags });
      state.current.tags = updated.tags;
      updateListItem(updated);
      renderHead();
      if (state.activeTag === tag) { state.activeTag = ''; renderList(); }
    } catch (e) {
      toast('删除标签失败：' + e.message, 'error');
    }
  }

  function updateFigCount(n) {
    const badge = $('fig-count');
    badge.textContent = n || '';
    badge.hidden = !n;
  }

  function clearWorkspace() {
    setSaveStatus('');
    $('editor').value = '';
    $('editor').disabled = true;
    renderPreview('');
    renderHead();
    $('btn-generate').disabled = true;
    $('btn-export-md').disabled = true;
    $('btn-export-word').disabled = true;
    $('btn-copy-wechat').disabled = true;
    state.figures = [];
    state.figDataUrls = {};
    renderFigures();
    history.replaceState(null, '', location.pathname);
    clearPdf();
    setWorkspaceView('library');
  }

  /** Entry points in this file are parameterless on purpose: callers stash the
   *  target (a server-issued record) on `state` first, then invoke. Ids for
   *  request paths are therefore only ever read from state, never handed in
   *  from DOM/URL values. */
  async function openPaper() {
    const target = state.pendingPaper;
    state.pendingPaper = null;
    if (!target) return;
    if (state.generating) { toast('正在生成笔记，请先停止再切换文献', 'error'); return; }
    await flushSave();
    try {
      const full = await api.paper(target.id);
      state.current = full;
    } catch (e) {
      toast('打开失败：' + e.message, 'error');
      return;
    }
    renderList();
    renderHead();
    $('editor').value = state.current.notes || '';
    $('editor').disabled = false;
    setSaveStatus(state.current.notes ? '已保存' : '自动保存', state.current.notes ? 'saved' : '');
    renderPreview();
    const hasFile = !!state.current.has_file;
    $('btn-generate').disabled = false;
    $('btn-generate').title = hasFile ? '' : '该条目还没有 PDF';
    $('btn-export-md').disabled = !state.current.notes;
    $('btn-export-word').disabled = !state.current.notes;
    $('btn-copy-wechat').disabled = !state.current.notes;
    history.replaceState(null, '', '?paper=' + encodeURIComponent(state.current.id));
    state.relatedData = null;
    if (window.matchMedia('(max-width: 900px)').matches) setWorkspaceView('pdf');
    if (hasFile) loadPdf(); else clearPdf();
    fetchFigures();
  }

  // ---------- notes ----------
  function figureMarkdownRefs(md) {
    return [...md.matchAll(/!\[([^\]]*)\]\(figure:([a-z]\d+)\)/g)];
  }

  function hydrateFigureImages(root) {
    if (!state.current) return;
    root.querySelectorAll('img').forEach((img) => {
      const m = /^figure:([a-z]\d+)$/.exec(img.getAttribute('src') || '');
      if (!m) return;
      const fig = state.figures.find((f) => f.id === m[1]);
      const real = document.createElement('img');
      real.src = api.figUrl(state.current.id, m[1]);
      real.alt = (fig && fig.label) || img.alt;
      real.style.maxWidth = '100%';
      img.replaceWith(real);
    });
  }

  function renderPreview(md, streaming) {
    const source = md !== undefined ? md : $('editor').value;
    const preview = $('preview');
    if (!source.trim()) {
      preview.innerHTML = emptyState('edit', state.current ? '把阅读变成自己的理解' : '你的阅读笔记，从这里开始',
        state.current ? '选择精读或速读，生成结构化笔记；也可以切换到「编辑」，记录自己的思考。' : '打开一篇文献，即可生成阅读笔记、核查图表，或记录灵感。');
      return;
    }
    if (!window.marked) { preview.innerHTML = '<pre>' + escapeHtml(source) + '</pre>'; return; }
    preview.innerHTML = marked.parse(source, { gfm: true, breaks: false });
    hydrateFigureImages(preview);
    if (window.hljs) preview.querySelectorAll('pre code').forEach((el) => { try { hljs.highlightElement(el); } catch (e) { /* ignore */ } });
    if (streaming) {
      const box = $('tab-preview');
      box.scrollTop = box.scrollHeight;
    }
  }

  function scheduleSave() {
    if (!state.current || state.generating) return;
    const id = state.current.id;
    const notes = $('editor').value;
    state.current.notes = notes;
    clearTimeout(state.saveTimer);
    state.pendingSave = { id, notes };
    setSaveStatus('待保存', 'pending');
    state.saveTimer = setTimeout(flushSave, 800);
  }

  async function flushSave() {
    clearTimeout(state.saveTimer);
    const pending = state.pendingSave;
    if (!pending) return true;
    state.pendingSave = null;
    setSaveStatus('正在保存…', 'pending');
    try {
      const updated = await api.patch(pending.id, { notes: pending.notes });
      updateListItem(updated);
      if (state.current && state.current.id === pending.id) {
        state.current.notes_updated_at = updated.notes_updated_at;
        renderHead();
        $('btn-export-md').disabled = !pending.notes;
        $('btn-export-word').disabled = !pending.notes;
        $('btn-copy-wechat').disabled = !pending.notes;
        if (!state.pendingSave && $('editor').value === pending.notes) setSaveStatus('已保存', 'saved');
      }
      return true;
    } catch (e) {
      if (state.current && state.current.id === pending.id) setSaveStatus('保存失败', 'error');
      toast('保存笔记失败：' + e.message, 'error');
      return false;
    }
  }

  function setGenerating(on, message, progress) {
    const status = $('gen-status');
    status.hidden = !on;
    if (on) {
      status.innerHTML = '<span class="dot"></span><span class="msg">' + escapeHtml(message || '生成中…') + '</span>' +
        (progress ? '<span class="progress"><div style="width:' + Math.round(100 * progress.done / progress.total) + '%"></div></span>' : '');
    }
    $('btn-generate').hidden = on;
    $('btn-stop').hidden = !on;
    $('read-mode').disabled = on;
    $('editor').readOnly = on;
  }

  function setStage(message, progress) {
    const status = $('gen-status');
    if (status.hidden) return;
    status.innerHTML = '<span class="dot"></span><span class="msg">' + escapeHtml(message) + '</span>' +
      (progress ? '<span class="progress"><div style="width:' + Math.round(100 * progress.done / progress.total) + '%"></div></span>' : '');
  }

  async function generateNotes() {
    const paper = state.current;
    if (!paper || state.generating) return;
    if (!state.settings || !state.settings.has_api_key) {
      toast('请先在「设置」中填写 API Key', 'error');
      openSettings();
      return;
    }
    if (!paper.has_file) {
      toast('该条目还没有 PDF，请先附加文件', 'error');
      chooseAttachPdf();
      return;
    }
    if ((paper.notes || '').trim() && !confirm('这篇文献已有笔记，重新生成会覆盖当前内容。继续？')) return;
    await flushSave();

    const mode = $('read-mode').value;
    const ctrl = new AbortController();
    state.generating = ctrl;
    setSaveStatus('正在生成…', 'pending');
    setGenerating(true, '正在连接 ' + state.settings.model + ' …');
    switchTab('preview');
    const editor = $('editor');
    editor.value = '';
    renderPreview('');

    let acc = '';
    let buffer = '';
    let lastRender = 0;
    try {
      const resp = await fetch(paperPath(paper.id, '/read?mode=' + encodeURIComponent(mode)), { method: 'POST', signal: ctrl.signal });
      if (!resp.ok) {
        let msg = resp.statusText;
        try { msg = (await resp.json()).detail || msg; } catch (e) { /* ignore */ }
        throw new Error(msg);
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          for (const line of frame.split('\n')) {
            if (!line.startsWith('data:')) continue;
            let event;
            try { event = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
            if (event.type === 'chunk') {
              acc += event.text;
              editor.value = acc;
              const now = performance.now();
              if (now - lastRender > 250) { renderPreview(acc, true); lastRender = now; }
            } else if (event.type === 'start') {
              setGenerating(true, event.model + ' 正在' + (event.mode_name || mode) + '，笔记会实时出现…');
            } else if (event.type === 'stage') {
              setStage(event.message || event.stage, event.total ? { done: event.done, total: event.total } : null);
              if (event.stage === 'figures' && event.done) fetchFigures();
            } else if (event.type === 'error') {
              throw new Error(event.message);
            } else if (event.type === 'done') {
              acc = event.notes;
            }
          }
        }
      }
      paper.notes = acc;
      paper.notes_mode = mode;
      paper.notes_updated_at = new Date().toISOString();
      editor.value = acc;
      renderPreview(acc);
      $('btn-export-md').disabled = !acc;
      $('btn-export-word').disabled = !acc;
      $('btn-copy-wechat').disabled = !acc;
      updateListItem(paper);
      renderHead();
      setSaveStatus('已保存', 'saved');
      toast('笔记生成完成', 'ok');
    } catch (e) {
      editor.value = acc;
      renderPreview(acc);
      if (e.name === 'AbortError') {
        setSaveStatus(acc ? '正在保存…' : '已停止', acc ? 'pending' : '');
        toast('已停止生成' + (acc ? '，已保留部分内容' : ''));
        if (acc) {
          paper.notes = acc;
          api.patch(paper.id, { notes: acc }).then((updated) => {
            updateListItem(updated);
            if (state.current && state.current.id === paper.id) setSaveStatus('已保存', 'saved');
          }).catch(() => { if (state.current && state.current.id === paper.id) setSaveStatus('保存失败', 'error'); });
        }
      } else {
        setSaveStatus(acc ? '内容尚未保存' : '生成失败', 'error');
        toast('生成失败：' + e.message, 'error', 6000);
      }
    } finally {
      state.generating = null;
      setGenerating(false);
    }
  }

  function stopGenerating() {
    if (state.generating) state.generating.abort();
  }

  async function exportMarkdown() {
    const paper = state.current;
    if (!paper) return;
    await flushSave();
    const notes = $('editor').value;
    if (!notes.trim()) { toast('还没有笔记可导出'); return; }
    downloadBlob(safeFilename(paper.title) + '.md', notes, 'text/markdown;charset=utf-8');
  }

  async function figureArrayBuffer(fid) {
    const resp = await fetch(api.figUrl(state.current.id, fid));
    if (!resp.ok) throw new Error('图片 ' + fid + ' 加载失败');
    return await resp.arrayBuffer();
  }

  async function exportWord() {
    const paper = state.current;
    if (!paper) return;
    await flushSave();
    const md = $('editor').value;
    if (!md.trim()) { toast('还没有笔记可导出'); return; }
    toast('正在生成 Word …');
    const ok = await ensureDocx();
    if (!ok || !window.docx) { toast('Word 导出组件加载失败（检查网络）', 'error'); return; }
    const D = window.docx;
    try {
      const figRefs = figureMarkdownRefs(md);
      const buffers = {};
      const figMeta = {};
      await Promise.all(figRefs.map(async (m) => {
        try {
          buffers[m[2]] = await figureArrayBuffer(m[2]);
          const fig = state.figures.find((f) => f.id === m[2]);
          figMeta[m[2]] = fig || { label: m[1], caption: '', page: 0 };
        } catch (e) { /* broken image is skipped below */ }
      }));

      const children = [];
      const pushText = (text, opts) => {
        if (!text.trim()) return;
        // Minimal inline rendering: **bold** and `code` within a paragraph.
        const runs = [];
        const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
        let last = 0, m;
        while ((m = re.exec(text))) {
          if (m.index > last) runs.push(new D.TextRun({ text: text.slice(last, m.index), ...opts }));
          if (m[0].startsWith('**')) runs.push(new D.TextRun({ text: m[0].slice(2, -2), bold: true, ...opts }));
          else runs.push(new D.TextRun({ text: m[0].slice(1, -1), font: 'Menlo', size: 22, ...opts }));
          last = m.index + m[0].length;
        }
        if (last < text.length) runs.push(new D.TextRun({ text: text.slice(last), ...opts }));
        children.push(new D.Paragraph({ children: runs, spacing: { after: 120 } }));
      };

      for (const line of md.split('\n')) {
        const t = line.trim();
        const fm = /^!\[([^\]]*)\]\(figure:([a-z]\d+)\)$/.exec(t);
        if (fm && buffers[fm[2]]) {
          const fig = figMeta[fm[2]];
          const maxW = 560;
          const w = Math.min(maxW, fig.width || maxW);
          const h = fig.height ? Math.round(fig.height * (w / fig.width)) : 340;
          children.push(new D.Paragraph({ text: fig.label || fm[1], heading: D.HeadingLevel.HEADING_3, spacing: { before: 200 } }));
          children.push(new D.Paragraph({ children: [new D.ImageRun({ data: buffers[fm[2]], transformation: { width: w, height: h } })] }));
          if (fig.caption) children.push(new D.Paragraph({ children: [new D.TextRun({ text: fig.caption, italics: true, color: '6b7280', size: 21 })], spacing: { after: 160 } }));
          continue;
        }
        if (!t) continue;
        let hm;
        if ((hm = /^#{1,6}\s+(.*)$/.exec(t))) {
          const level = Math.min(4, t.match(/^#+/)[0].length);
          children.push(new D.Paragraph({ text: hm[1], heading: [D.HeadingLevel.HEADING_1, D.HeadingLevel.HEADING_2, D.HeadingLevel.HEADING_3, D.HeadingLevel.HEADING_4][level - 1], spacing: { before: 240, after: 120 } }));
        } else if (/^(\||[-:| ]+\|)/.test(t)) {
          if (/^\|[-: |]+\|$/.test(t)) continue;
          const cells = t.replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
          children.push(new D.Paragraph({
            children: cells.flatMap((c) => [new D.TextRun({ text: c + '    ', bold: true, size: 22 }), new D.TextRun({ text: '│ ', color: '9ca3af' })]),
            spacing: { after: 40 },
          }));
        } else if (/^[-*]\s+/.test(t)) {
          pushText('• ' + t.replace(/^[-*]\s+/, ''));
        } else if (/^\d+[.)]\s+/.test(t)) {
          pushText(t);
        } else if (/^>\s?/.test(t)) {
          pushText(t.replace(/^>\s?/, ''), { italics: true, color: '4b5563' });
        } else {
          pushText(t);
        }
      }
      const doc = new D.Document({
        styles: { default: { document: { run: { font: 'PingFang SC', size: 24 }, paragraph: { spacing: { line: 340 } } } } },
        sections: [{ properties: {}, children }],
      });
      const blob = await D.Packer.toBlob(doc);
      downloadRaw(safeFilename(paper.title) + '.docx', blob);
      toast('Word 已导出（含 ' + Object.keys(buffers).length + ' 张图）', 'ok');
    } catch (e) {
      toast('Word 导出失败：' + e.message, 'error');
    }
  }

  // ---------- tabs ----------
  function switchTab(name) {
    if (!['preview', 'edit', 'figures'].includes(name)) return;
    state.tab = name;
    document.querySelectorAll('#tabs .tab').forEach((b) => {
      const active = b.dataset.tab === name;
      b.classList.toggle('active', active);
      b.setAttribute('aria-selected', String(active));
      b.tabIndex = active ? 0 : -1;
    });
    ['preview', 'edit', 'figures'].forEach((t) => { $('tab-' + t).hidden = t !== name; });
    if (window.matchMedia('(max-width: 900px)').matches) setWorkspaceView('notes');
    if (name === 'figures') renderFigures();
    if (name === 'edit') $('editor').focus();
  }

  async function figureDataUrl(fid) {
    if (state.figDataUrls[fid]) return state.figDataUrls[fid];
    const resp = await fetch(api.figUrl(state.current.id, fid));
    if (!resp.ok) throw new Error('figure ' + fid + ' unavailable');
    const blob = await resp.blob();
    return await new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => { state.figDataUrls[fid] = fr.result; resolve(fr.result); };
      fr.onerror = () => reject(new Error('read failed'));
      fr.readAsDataURL(blob);
    });
  }

  async function buildWechatHtml() {
    // Embed figure images as data URLs so the copied HTML survives outside the app.
    const md = $('editor').value;
    if (!md.trim()) return '';
    let prepared = md;
    for (const m of figureMarkdownRefs(md)) {
      try {
        const url = await figureDataUrl(m[2]);
        prepared = prepared.split('(figure:' + m[2] + ')').join('(' + url + ')');
      } catch (e) { /* leave the placeholder */ }
    }
    return WechatExport.render(prepared, $('wechat-theme').value);
  }

  async function copyWechat() {
    if (!state.current) return;
    const notes = $('editor').value;
    if (!notes.trim()) { toast('还没有笔记可复制'); return; }
    const btn = $('btn-copy-wechat');
    btn.disabled = true;
    try {
      const html = await buildWechatHtml();
      if (!html) { toast('没有可复制的内容'); return; }
      const ok = await WechatExport.copy(html);
      toast(ok ? '已复制（主题：' + $('wechat-theme').selectedOptions[0].textContent + '），去公众号编辑器 ⌘/Ctrl+V 粘贴即可' : '复制失败，请检查浏览器剪贴板权限', ok ? 'ok' : 'error');
    } catch (e) {
      toast('渲染失败：' + e.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  // ---------- figures ----------
  async function fetchFigures() {
    if (!state.current) return [];
    try {
      state.figures = await api.figures(state.current.id);
    } catch (e) {
      state.figures = [];
    }
    if (state.tab === 'figures') renderFigures();
    return state.figures;
  }

  function renderFigures() {
    const grid = $('fig-grid');
    if (!state.current) { grid.innerHTML = emptyState('image', '让证据回到图表', '打开一篇文献，查看提取的图表与读图观察。'); return; }
    const figures = state.figures;
    if (!figures.length) {
      grid.innerHTML = emptyState('image', '还没有提取的图表', '附加 PDF 后会自动提取图表，也可以在原文中使用「取图」手动框选。');
      return;
    }
    grid.innerHTML = figures.map((f) => {
      const child = f.parent ? ' child' : '';
      const obs = f.observation ? '<div class="fig-observation" title="读图模型观察（供笔记生成参考）">' + escapeHtml(f.observation) + '</div>' : '';
      return '<div class="fig-card' + child + '" data-fid="' + f.id + '">' +
        '<button class="fig-thumb" data-zoom="' + f.id + '" aria-label="放大 ' + escapeHtml(f.label) + '"><img loading="lazy" src="' + api.figUrl(state.current.id, f.id) + '" alt="' + escapeHtml(f.label) + '"></button>' +
        '<div class="fig-body">' +
        '<div class="fig-label">' + escapeHtml(f.label) + '<span class="badge">' + (f.source === 'manual' ? '手动' : '自动') + '</span><span class="badge">p.' + f.page + '</span></div>' +
        '<div class="fig-caption" data-caption="' + f.id + '" title="点击展开 / 收起" role="button" tabindex="0" aria-expanded="false">' + escapeHtml(f.caption || '（无图注）') + '</div>' + obs +
        '<div class="fig-actions">' +
        '<button class="btn btn-read" data-read-fig="' + f.id + '" title="让读图模型观察这张图，可带追问">' + icon('eye') + ' 读图</button>' +
        '<button class="btn" data-insert="' + f.id + '">' + icon('plus') + ' 插入笔记</button>' +
        '<button class="btn" data-edit-caption="' + f.id + '">' + icon('edit') + ' 图注</button>' +
        '<button class="btn btn-icon btn-ghost" data-del-fig="' + f.id + '" title="删除图片" aria-label="删除 ' + escapeHtml(f.label) + '">' + icon('trash') + '</button>' +
        '</div></div></div>';
    }).join('');
    updateFigCount(figures.length);
    if (state.current) { state.current.figures_count = figures.length; }
  }

  async function readSingleFigure() {
    if (!state.current || state.figReading) return;
    if (!state.settings || !state.settings.has_api_key) { toast('请先设置 API Key', 'error'); openSettings(); return; }
    if (!state.settings.vision_model) { toast('请先在设置里填写「读图模型」', 'error'); openSettings(); return; }
    const fid = state.pendingFigure;
    state.pendingFigure = '';
    const fig = state.figures.find((f) => f.id === fid);
    if (!fig) return;
    const paperId = state.current.id;
    const question = await requestText('读图 · ' + fig.label, '想重点核查什么？例如：F 面板两组的差别和显著性。留空则进行整体观察。', '', '开始读图');
    if (question === null || !state.current || state.current.id !== paperId) return;
    state.figReading = true;
    const btn = document.querySelector('[data-read-fig="' + fid + '"]');
    if (btn) { btn.disabled = true; btn.innerHTML = icon('eye') + ' 观察中…'; }
    try {
      const updated = await api.readFigure(state.current.id, fid, { question });
      Object.assign(fig, updated);
      renderFigures();
      toast('观察已更新到卡片（生成笔记时会一并使用）', 'ok');
    } catch (e) {
      if (btn) { btn.disabled = false; btn.innerHTML = icon('eye') + ' 读图'; }
      toast('读图失败：' + e.message, 'error');
    } finally {
      state.figReading = false;
    }
  }

  function openLightbox(fid) {
    const fig = state.figures.find((f) => f.id === fid);
    if (!fig || !state.current) return;
    const box = document.createElement('div');
    box.className = 'fig-lightbox';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', fig.label);
    box.tabIndex = -1;
    const previous = document.activeElement;
    const close = () => { box.remove(); if (previous && previous.isConnected) previous.focus(); };
    box.innerHTML = '<button class="btn btn-icon lightbox-close" aria-label="关闭图片">' + icon('close') + '</button><img src="' + api.figUrl(state.current.id, fid) + '" alt="' + escapeHtml(fig.label) + '">';
    box.addEventListener('click', close);
    box.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
      if (e.key === 'Tab') { e.preventDefault(); box.querySelector('button').focus(); }
    });
    document.body.appendChild(box);
    box.querySelector('button').focus();
  }

  function insertFigureRef(fid) {
    const fig = state.figures.find((f) => f.id === fid);
    if (!fig) return;
    const editor = $('editor');
    if ($('tab-edit').hidden) switchTab('edit');
    const snippet = '\n\n![' + fig.label + '](figure:' + fid + ')\n\n';
    const pos = editor.selectionStart || editor.value.length;
    editor.value = editor.value.slice(0, pos) + snippet + editor.value.slice(pos);
    editor.focus();
    editor.selectionStart = editor.selectionEnd = pos + snippet.length;
    scheduleSave();
    renderPreview();
    toast('已插入 ' + fig.label + '（预览 / 公众号 / Word 会渲染为图片）');
  }

  async function editFigureCaption(fid) {
    const fig = state.figures.find((f) => f.id === fid);
    if (!fig) return;
    const paperId = state.current.id;
    const caption = await requestText('编辑图注 · ' + fig.label, '补充实验条件、分组与统计信息，帮助后续读图和笔记生成。', fig.caption || '', '保存图注');
    if (caption === null || !state.current || state.current.id !== paperId) return;
    try {
      const updated = await api.patchFigure(state.current.id, fid, { caption });
      Object.assign(fig, updated);
      renderFigures();
    } catch (e) {
      toast('保存失败：' + e.message, 'error');
    }
  }

  async function deleteFigure(fid) {
    const fig = state.figures.find((f) => f.id === fid);
    if (!fig || !confirm('删除 ' + fig.label + '？')) return;
    try {
      await api.removeFigure(state.current.id, fid);
      await fetchFigures();
      updateListItem(Object.assign({}, state.current));
      toast('已删除');
    } catch (e) {
      toast('删除失败：' + e.message, 'error');
    }
  }

  async function reextractFigures() {
    if (!state.current) return;
    toast('正在重新提取图片 …');
    try {
      const res = await api.reextract(state.current.id);
      state.figures = res.figures;
      state.figDataUrls = {};
      renderFigures();
      updateListItem(Object.assign({}, state.current, { figures_count: res.figures.length }));
      if (res.warnings && res.warnings.length) toast(res.warnings[0]);
      else toast('提取到 ' + res.figures.length + ' 张图', 'ok');
    } catch (e) {
      toast('提取失败：' + e.message, 'error');
    }
  }

  async function readFigures(force) {
    if (!state.current || state.figReading) return;
    if (!state.settings || !state.settings.has_api_key) { toast('请先设置 API Key', 'error'); openSettings(); return; }
    if (!state.settings.vision_model) { toast('请先在设置里填写「读图模型」', 'error'); openSettings(); return; }
    state.figReading = true;
    const btn = $('btn-fig-read');
    btn.disabled = true;
    setGenerating(true, '读图模型正在观察图片 …');
    try {
      const resp = await fetch(paperPath(state.current.id, '/figures/read') + (force ? '?force=1' : ''), { method: 'POST' });
      if (!resp.ok) {
        let msg = resp.statusText;
        try { msg = (await resp.json()).detail || msg; } catch (e) { /* ignore */ }
        throw new Error(msg);
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          for (const line of frame.split('\n')) {
            if (!line.startsWith('data:')) continue;
            let event;
            try { event = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
            if (event.type === 'stage') setStage('读图 ' + event.done + '/' + event.total, { done: event.done, total: event.total });
            else if (event.type === 'done') {
              state.figures = event.figures;
              renderFigures();
              toast('读图完成，观察已写入图片卡片', 'ok');
            } else if (event.type === 'error') throw new Error(event.message);
          }
        }
      }
    } catch (e) {
      toast('读图失败：' + e.message, 'error', 6000);
    } finally {
      state.figReading = false;
      btn.disabled = false;
      setGenerating(false);
    }
  }

  // ---------- PDF ----------
  function showPdfEmpty(mode, message) {
    const empty = $('pdf-empty');
    const content = {
      welcome: ['你的下一次发现，从这里开始', '留一点时间，读懂一篇好论文', '从文献库选择一篇论文，或导入新的 PDF。原文、图表与思考，在这里并排展开。'],
      missing: ['文献信息已就绪', '为这篇文献附加原文', '已保存标题与文献信息。附加 PDF 后，即可对照原文、提取图表并生成阅读笔记。'],
      loading: ['正在准备原文', '正在打开论文', '正在加载 PDF，较大的文件可能需要一点时间。'],
      error: ['原文暂时无法显示', 'PDF 加载失败', '请重试，并确认本地服务仍在运行。如果文件已损坏，请重新导入完整的 PDF。'],
    }[mode];
    empty.hidden = false;
    empty.dataset.state = mode;
    empty.setAttribute('aria-busy', String(mode === 'loading'));
    empty.querySelector('.empty-eyebrow').textContent = content[0];
    empty.querySelector('.empty-title').textContent = content[1];
    $('pdf-empty-text').textContent = message || content[2];
    empty.querySelector('.empty-actions').hidden = mode !== 'welcome';
    empty.querySelector('.welcome-footnote').hidden = mode !== 'welcome';
    empty.querySelector('.paper-illustration').hidden = mode === 'loading' || mode === 'error';
    $('btn-attach-pdf').hidden = mode !== 'missing';
    let retry = $('btn-retry-pdf');
    if (!retry) {
      retry = document.createElement('button');
      retry.id = 'btn-retry-pdf';
      retry.className = 'btn btn-primary';
      retry.innerHTML = icon('refresh') + ' 重新加载 PDF';
      retry.addEventListener('click', loadPdf);
      $('btn-attach-pdf').before(retry);
    }
    retry.hidden = mode !== 'error' || !window.pdfjsLib;
  }

  function clearPdf() {
    state.renderToken++;
    if (state.pdf) { try { state.pdf.destroy(); } catch (e) { /* ignore */ } }
    state.pdf = null;
    state.pageWrappers = [];
    $('pdf-pages').innerHTML = '';
    $('pdf-total').textContent = '0';
    $('pdf-page').value = 1;
    showPdfEmpty(!state.current ? 'welcome' : state.current.has_file ? 'loading' : 'missing');
    $('pdf-crop').disabled = true;
    setCropping(false);
  }

  async function loadPdf() {
    const paper = state.current;
    if (!paper) return;
    clearPdf();
    const token = state.renderToken;
    if (!window.pdfjsLib) {
      showPdfEmpty('error', 'PDF 阅读组件未能加载。请检查网络连接，刷新页面后再打开这篇文献。');
      toast('PDF 阅读组件未能加载，请检查网络后刷新页面', 'error');
      return;
    }
    pdfjsLib.GlobalWorkerOptions.workerSrc = PDF_WORKER;
    try {
      // Fetch the bytes through the validated path helper, then hand pdf.js a
      // buffer — no dynamically built URL is ever passed to getDocument().
      const resp = await fetch(paperPath(paper.id, '/file'));
      if (!resp.ok) throw new Error('PDF 下载失败 HTTP ' + resp.status);
      const data = await resp.arrayBuffer();
      const doc = await pdfjsLib.getDocument({ data }).promise;
      if (token !== state.renderToken) { doc.destroy(); return; }
      state.pdf = doc;
      $('pdf-empty').hidden = true;
      $('pdf-empty').setAttribute('aria-busy', 'false');
      $('pdf-total').textContent = doc.numPages;
      $('pdf-page').max = doc.numPages;
      $('pdf-crop').disabled = false;
      await renderPages(token, 1);
    } catch (e) {
      if (token === state.renderToken) {
        clearPdf();
        showPdfEmpty('error');
        toast('PDF 加载失败：' + e.message, 'error');
      }
    }
  }

  async function renderPages(token, keepPage) {
    const doc = state.pdf;
    if (!doc) return;
    const container = $('pdf-pages');
    container.innerHTML = '';
    state.pageWrappers = [];
    const first = await doc.getPage(1);
    const base = first.getViewport({ scale: 1 });
    if (state.fitWidth) {
      const avail = $('pdf-scroll').clientWidth - 40;
      state.scale = Math.max(0.3, Math.min(3, avail / base.width));
    }
    $('pdf-zoom').textContent = Math.round(state.scale * 100) + '%';
    for (let i = 1; i <= doc.numPages; i++) {
      const w = document.createElement('div');
      w.className = 'pdf-page';
      w.dataset.page = i;
      w.style.width = Math.floor(base.width * state.scale) + 'px';
      w.style.height = Math.floor(base.height * state.scale) + 'px';
      container.appendChild(w);
      state.pageWrappers.push(w);
    }
    if (keepPage && keepPage > 1) goToPage(keepPage, false);

    const dpr = window.devicePixelRatio || 1;
    for (let i = 1; i <= doc.numPages; i++) {
      if (token !== state.renderToken) return;
      const page = i === 1 ? first : await doc.getPage(i);
      if (token !== state.renderToken) return;
      const vp = page.getViewport({ scale: state.scale });
      const canvas = document.createElement('canvas');
      canvas.width = Math.floor(vp.width * dpr);
      canvas.height = Math.floor(vp.height * dpr);
      canvas.style.width = Math.floor(vp.width) + 'px';
      canvas.style.height = Math.floor(vp.height) + 'px';
      const ctx = canvas.getContext('2d');
      await page.render({ canvasContext: ctx, viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
      if (token !== state.renderToken) return;
      const w = state.pageWrappers[i - 1];
      w.style.width = canvas.style.width;
      w.style.height = canvas.style.height;
      w.innerHTML = '';
      w.appendChild(canvas);
      const num = document.createElement('span');
      num.className = 'page-num';
      num.textContent = i;
      w.appendChild(num);
    }
  }

  function currentPage() {
    const scroller = $('pdf-scroll');
    const top = scroller.scrollTop + 40;
    let best = 1;
    for (const w of state.pageWrappers) {
      if (w.offsetTop <= top) best = parseInt(w.dataset.page, 10);
      else break;
    }
    return best;
  }

  function goToPage(n, smooth) {
    const w = state.pageWrappers[n - 1];
    if (!w) return;
    $('pdf-scroll').scrollTo({ top: w.offsetTop - 12, behavior: smooth ? 'smooth' : 'auto' });
    $('pdf-page').value = n;
  }

  function rerenderPdf() {
    // A hidden narrow-screen view has no measurable width or page offsets.
    if (!state.pdf || !$('pdf-scroll').clientWidth || !$('pdf-scroll').clientHeight) return;
    const page = currentPage();
    state.renderToken++;
    renderPages(state.renderToken, page);
  }

  function zoom(factor) {
    if (!state.pdf) return;
    state.fitWidth = false;
    state.scale = Math.max(0.3, Math.min(3, state.scale * factor));
    rerenderPdf();
  }

  // ---------- manual crop ----------
  function setCropping(on) {
    state.cropping = on && !!state.pdf;
    $('pdf-scroll').classList.toggle('cropping', state.cropping);
    $('crop-hint').hidden = !state.cropping;
    $('pdf-crop').classList.toggle('active', state.cropping);
    if (!state.cropping && state.cropDrag && state.cropDrag.el) state.cropDrag.el.remove();
    state.cropDrag = null;
  }

  function initCrop() {
    const scroll = $('pdf-scroll');
    scroll.addEventListener('mousedown', (e) => {
      if (!state.cropping || e.button !== 0) return;
      const pageEl = e.target.closest('.pdf-page');
      if (!pageEl) return;
      e.preventDefault();
      const rect = pageEl.getBoundingClientRect();
      const el = document.createElement('div');
      el.className = 'crop-rect';
      pageEl.appendChild(el);
      state.cropDrag = {
        el, pageEl,
        x0: e.clientX - rect.left, y0: e.clientY - rect.top,
        x1: e.clientX - rect.left, y1: e.clientY - rect.top,
      };
    });
    window.addEventListener('mousemove', (e) => {
      const drag = state.cropDrag;
      if (!drag) return;
      const rect = drag.pageEl.getBoundingClientRect();
      drag.x1 = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
      drag.y1 = Math.max(0, Math.min(rect.height, e.clientY - rect.top));
      const x = Math.min(drag.x0, drag.x1), y = Math.min(drag.y0, drag.y1);
      drag.el.style.left = x + 'px';
      drag.el.style.top = y + 'px';
      drag.el.style.width = Math.abs(drag.x1 - drag.x0) + 'px';
      drag.el.style.height = Math.abs(drag.y1 - drag.y0) + 'px';
    });
    window.addEventListener('mouseup', () => {
      const drag = state.cropDrag;
      if (!drag) return;
      state.cropDrag = null;
      const w = Math.abs(drag.x1 - drag.x0), h = Math.abs(drag.y1 - drag.y0);
      drag.el.remove();
      if (w < 15 || h < 15) return;
      const canvas = drag.pageEl.querySelector('canvas');
      if (!canvas) return;
      const cssW = parseFloat(canvas.style.width) || canvas.width;
      const cssH = parseFloat(canvas.style.height) || canvas.height;
      const scale = state.scale || 1;
      const x0 = Math.min(drag.x0, drag.x1) / scale;
      const y0 = Math.min(drag.y0, drag.y1) / scale;
      submitCrop(parseInt(drag.pageEl.dataset.page, 10), [x0, y0, x0 + w / scale, y0 + h / scale], cssW, cssH);
    });
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && state.cropping) setCropping(false);
    });
  }

  async function submitCrop(page, bbox, cssW, cssH) {
    if (!state.current) return;
    toast('正在截取第 ' + page + ' 页的选区 …');
    try {
      const fig = await api.cropFigure(state.current.id, { page, bbox, label: '', caption: '' });
      state.figures.push(fig);
      if (state.tab === 'figures') renderFigures();
      else renderFigures();
      updateFigCount(state.figures.length);
      state.current.figures_count = state.figures.length;
      updateListItem(Object.assign({}, state.current));
      toast('已截取', 'ok');
      if (state.settings && state.settings.has_api_key && state.settings.vision_model) {
        state.pendingFigure = fig.id;
        readSingleFigure();
      } else {
        toast('已截取；在设置里填读图模型后，可继续追问这张图');
      }
    } catch (e) {
      toast('截取失败：' + e.message, 'error');
    }
  }

  // ---------- splitter ----------
  function initSplitter() {
    const splitter = $('splitter');
    const main = $('main');
    let dragging = false;
    let resizeTimer = null;
    const updateWidth = (pct) => {
      const width = Math.max(22, Math.min(78, pct));
      document.documentElement.style.setProperty('--pdf-width', width + '%');
      splitter.setAttribute('aria-valuenow', String(Math.round(width)));
    };
    splitter.setAttribute('aria-valuemin', '22');
    splitter.setAttribute('aria-valuemax', '78');
    splitter.setAttribute('aria-valuenow', String(parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--pdf-width')) || 52));
    splitter.addEventListener('keydown', (e) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
      e.preventDefault();
      const current = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--pdf-width')) || 52;
      updateWidth(e.key === 'Home' ? 22 : e.key === 'End' ? 78 : current + (e.key === 'ArrowLeft' ? -3 : 3));
      if (state.fitWidth) { clearTimeout(resizeTimer); resizeTimer = setTimeout(rerenderPdf, 150); }
    });
    splitter.addEventListener('mousedown', (e) => {
      dragging = true;
      splitter.classList.add('active');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });
    window.addEventListener('mousemove', (e) => {
      if (!dragging) return;
      const rect = main.getBoundingClientRect();
      updateWidth(((e.clientX - rect.left) / rect.width) * 100);
    });
    window.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      splitter.classList.remove('active');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      if (state.fitWidth) { clearTimeout(resizeTimer); resizeTimer = setTimeout(rerenderPdf, 150); }
    });
    window.addEventListener('resize', () => {
      if (!state.fitWidth) return;
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(rerenderPdf, 250);
    });
  }

  // ---------- wiring ----------
  function initEvents() {
    document.querySelector('.upload-btn').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('file-input').click(); }
    });
    document.querySelectorAll('#workspace-views [data-view]').forEach((button) => {
      button.addEventListener('click', () => setWorkspaceView(button.dataset.view));
    });
    $('btn-toggle-library').addEventListener('click', () => {
      const collapsed = document.body.classList.toggle('library-collapsed');
      $('btn-toggle-library').setAttribute('aria-expanded', String(!collapsed));
      $('btn-toggle-library').title = collapsed ? '展开文献库' : '收起文献库';
      if (state.fitWidth) requestAnimationFrame(rerenderPdf);
    });
    $('btn-empty-import').addEventListener('click', () => $('file-input').click());
    if ($('btn-empty-doi')) $('btn-empty-doi').addEventListener('click', openDoiModal);
    if ($('btn-all-filter')) $('btn-all-filter').addEventListener('click', () => { state.favOnly = false; renderList(); });
    const exportMenu = $('export-menu');
    document.addEventListener('click', (e) => { if (!exportMenu.contains(e.target)) exportMenu.open = false; });

    $('input-close').addEventListener('click', () => finishInput(null));
    $('input-cancel').addEventListener('click', () => finishInput(null));
    $('input-submit').addEventListener('click', () => finishInput($('input-value').value));
    $('input-modal').addEventListener('click', (e) => { if (e.target === e.currentTarget) finishInput(null); });
    $('input-value').addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); finishInput($('input-value').value); }
    });
    document.addEventListener('keydown', (e) => {
      const modal = document.querySelector('.modal-backdrop:not([hidden])');
      if (!modal && (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        document.body.classList.remove('library-collapsed');
        $('btn-toggle-library').setAttribute('aria-expanded', 'true');
        setWorkspaceView('library');
        $('search').focus();
        if (state.fitWidth && !window.matchMedia('(max-width: 900px)').matches) requestAnimationFrame(rerenderPdf);
      }
      if (e.key === 'Escape') {
        if (modal) {
          e.preventDefault();
          if (modal.id === 'input-modal') finishInput(null); else closeModal(modal.id);
        }
        if (exportMenu.open) { exportMenu.open = false; exportMenu.querySelector('summary').focus(); }
      }
      if (modal && e.key === 'Tab') {
        const focusable = [...modal.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], summary, [tabindex="0"]')].filter((el) => el.getClientRects().length);
        if (!focusable.length) return;
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (e.shiftKey && (document.activeElement === first || !modal.contains(document.activeElement))) {
          e.preventDefault(); last.focus();
        } else if (!e.shiftKey && (document.activeElement === last || !modal.contains(document.activeElement))) {
          e.preventDefault(); first.focus();
        }
      }
    });
    $('btn-settings').addEventListener('click', openSettings);
    $('status-pill').addEventListener('click', openSettings);
    $('settings-close').addEventListener('click', closeSettings);
    $('settings-cancel').addEventListener('click', closeSettings);
    $('settings-save').addEventListener('click', saveSettings);
    $('settings-clear-key').addEventListener('click', clearApiKey);
    $('settings-modal').addEventListener('click', (e) => { if (e.target === e.currentTarget) closeSettings(); });

    $('file-input').addEventListener('change', (e) => { uploadFiles(e.target.files); e.target.value = ''; });
    $('attach-input').addEventListener('change', (e) => { const f = e.target.files[0]; e.target.value = ''; if (f) attachPdfFile(f); });
    $('btn-attach-pdf').addEventListener('click', chooseAttachPdf);
    $('search').addEventListener('input', (e) => { state.query = e.target.value; renderList(); });
    $('btn-fav-filter').addEventListener('click', () => {
      state.favOnly = !state.favOnly;
      renderList();
    });
    $('paper-list').addEventListener('click', (e) => {
      const star = e.target.closest('[data-star]');
      if (star) { e.stopPropagation(); toggleFavorite(star.dataset.star); return; }
      const item = e.target.closest('.paper-item');
      if (item && item.dataset.id) {
        const paper = state.papers.find((p) => p.id === item.dataset.id);
        if (paper) { state.pendingPaper = paper; openPaper(); }
      }
    });
    $('tag-chips').addEventListener('click', (e) => {
      const chip = e.target.closest('[data-tag]');
      if (chip) { state.activeTag = state.activeTag === chip.dataset.tag ? '' : chip.dataset.tag; renderList(); }
    });
    $('head-tags').addEventListener('click', (e) => {
      const x = e.target.closest('[data-x]');
      if (x) removeTag(x.dataset.x);
    });
    $('head-tags').addEventListener('keydown', (e) => {
      if (e.target.id !== 'tag-input') return;
      if (e.key === 'Enter' || e.key === ',') {
        e.preventDefault();
        const v = e.target.value.trim();
        if (v) { addTag(v); e.target.value = ''; }
      }
    });

    // DOI modal
    $('btn-doi-import').addEventListener('click', openDoiModal);
    $('doi-close').addEventListener('click', () => closeModal('doi-modal'));
    $('doi-cancel').addEventListener('click', () => closeModal('doi-modal'));
    $('doi-submit').addEventListener('click', submitDoi);
    $('doi-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') submitDoi(); });
    $('doi-modal').addEventListener('click', (e) => { if (e.target === e.currentTarget) closeModal('doi-modal'); });

    $('head-star').addEventListener('click', () => state.current && toggleFavorite(state.current.id));
    $('btn-delete').addEventListener('click', deleteCurrent);
    $('btn-enrich').addEventListener('click', enrichCurrent);
    $('btn-generate').addEventListener('click', generateNotes);
    $('btn-stop').addEventListener('click', stopGenerating);
    $('btn-export-md').addEventListener('click', exportMarkdown);
    $('btn-export-word').addEventListener('click', exportWord);

    // related modal
    $('btn-related').addEventListener('click', () => loadRelated(false));
    $('related-close').addEventListener('click', () => closeModal('related-modal'));
    $('related-refresh').addEventListener('click', () => loadRelated(true));
    $('related-modal').addEventListener('click', (e) => { if (e.target === e.currentTarget) closeModal('related-modal'); });
    $('related-body').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-related-doi]');
      if (btn) { btn.disabled = true; relatedImport(btn.dataset.relatedDoi); }
    });

    const tabs = [...document.querySelectorAll('#tabs .tab')];
    tabs.forEach((button, index) => {
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-controls', 'tab-' + button.dataset.tab);
      button.setAttribute('aria-selected', String(button.dataset.tab === state.tab));
      button.tabIndex = button.dataset.tab === state.tab ? 0 : -1;
      button.addEventListener('click', () => switchTab(button.dataset.tab));
      button.addEventListener('keydown', (e) => {
        let next = index;
        if (e.key === 'ArrowRight') next = (index + 1) % tabs.length;
        else if (e.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
        else if (e.key === 'Home') next = 0;
        else if (e.key === 'End') next = tabs.length - 1;
        else return;
        e.preventDefault();
        switchTab(tabs[next].dataset.tab);
        tabs[next].focus();
      });
    });
    const editor = $('editor');
    editor.addEventListener('input', () => { scheduleSave(); renderPreview(); });
    editor.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); flushSave(); }
    });

    // figures tab
    $('btn-fig-extract').addEventListener('click', reextractFigures);
    $('btn-fig-read').addEventListener('click', () => readFigures(false));
    $('fig-grid').addEventListener('click', (e) => {
      const zoom = e.target.closest('[data-zoom]');
      if (zoom) { openLightbox(zoom.dataset.zoom); return; }
      const cap = e.target.closest('[data-caption]');
      if (cap) { cap.setAttribute('aria-expanded', String(cap.classList.toggle('open'))); return; }
      const rf = e.target.closest('[data-read-fig]');
      if (rf) { state.pendingFigure = rf.dataset.readFig; readSingleFigure(); return; }
      const ins = e.target.closest('[data-insert]');
      if (ins) { insertFigureRef(ins.dataset.insert); return; }
      const ec = e.target.closest('[data-edit-caption]');
      if (ec) { editFigureCaption(ec.dataset.editCaption); return; }
      const del = e.target.closest('[data-del-fig]');
      if (del) deleteFigure(del.dataset.delFig);
    });
    $('fig-grid').addEventListener('keydown', (e) => {
      const caption = e.target.closest('[data-caption]');
      if (caption && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); caption.click(); }
    });

    const themeSelect = $('wechat-theme');
    WechatExport.themes.forEach((t) => {
      const opt = document.createElement('option');
      opt.value = t.key;
      opt.textContent = t.name;
      themeSelect.appendChild(opt);
    });
    themeSelect.addEventListener('change', () => toast('主题已切换：' + themeSelect.selectedOptions[0].textContent));
    $('btn-copy-wechat').addEventListener('click', copyWechat);

    $('pdf-prev').addEventListener('click', () => goToPage(Math.max(1, currentPage() - 1), true));
    $('pdf-next').addEventListener('click', () => goToPage(Math.min(state.pageWrappers.length, currentPage() + 1), true));
    $('pdf-page').addEventListener('change', (e) => goToPage(Math.max(1, Math.min(state.pageWrappers.length, parseInt(e.target.value, 10) || 1)), true));
    $('pdf-zoom-in').addEventListener('click', () => zoom(1.2));
    $('pdf-zoom-out').addEventListener('click', () => zoom(1 / 1.2));
    $('pdf-fit').addEventListener('click', () => { state.fitWidth = true; rerenderPdf(); });
    $('pdf-crop').addEventListener('click', () => setCropping(!state.cropping));
    let scrollRaf = null;
    $('pdf-scroll').addEventListener('scroll', () => {
      if (scrollRaf) return;
      scrollRaf = requestAnimationFrame(() => { scrollRaf = null; if (state.pageWrappers.length) $('pdf-page').value = currentPage(); });
    });

    const sidebar = $('sidebar');
    ['dragenter', 'dragover'].forEach((ev) => document.addEventListener(ev, (e) => {
      if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files')) { e.preventDefault(); sidebar.classList.add('dragover'); }
    }));
    ['dragleave', 'drop'].forEach((ev) => document.addEventListener(ev, (e) => {
      if (ev === 'drop') { e.preventDefault(); if (e.dataTransfer && e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files); }
      if (ev === 'drop' || e.target === document.documentElement || e.relatedTarget === null) sidebar.classList.remove('dragover');
    }));

    window.addEventListener('beforeunload', () => { if (state.pendingSave) flushSave(); });
  }

  async function init() {
    if (!window.marked) toast('Markdown 渲染库未能加载，请检查网络（CDN）', 'error');
    initEvents();
    setWorkspaceView('library');
    $('editor').disabled = true;
    renderPreview('');
    renderFigures();
    showPdfEmpty('welcome');
    initSplitter();
    initCrop();
    await Promise.all([loadSettings(), loadPapers()]);
    // Deep link: ?paper=<id> only scrolls to and flashes the matching sidebar
    // entry; opening a paper always requires a real user click, so request ids
    // never derive from the URL query parameter.
    const wanted = new URLSearchParams(location.search).get('paper');
    if (wanted) {
      const item = [...document.querySelectorAll('.paper-item')].find((li) => li.dataset.id === wanted);
      if (item) {
        item.scrollIntoView({ block: 'center' });
        item.classList.add('flash');
        setTimeout(() => item.classList.remove('flash'), 1800);
      } else {
        history.replaceState(null, '', location.pathname);
      }
    }
    if (!state.settings || !state.settings.has_api_key) {
      toast('先在右上角「设置」里填写 API Key，再生成笔记');
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
