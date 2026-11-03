(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const PDF_WORKER = 'vendor/pdf.worker.min.js';
  const DOCX_SCRIPT = 'vendor/docx.umd.js';
  const MODE_NAMES = { deep: '精读', quick: '速读' };
  const READING_NAMES = { unread: '未读', skimmed: '已速读', reading: '精读中', completed: '已完成' };
  const DECISION_NAMES = { pending: '待判断', priority: '优先精读', reference: '留作参考', irrelevant: '暂不相关' };
  const TASK_NAMES = { decision: '判断阅读价值', fulltext: '补充全文', resume: '继续精读', notes_incomplete: '补完笔记', notes_stale: '核对新材料', evidence: '核查证据', resources: '核对数据与代码', storage: '检查本地记录' };

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
        const error = new Error(msg); error.status = resp.status; throw error;
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
    saveReading: (id, doc, position) => api.json(paperPath(id, '/reading-position?document_id=' + encodeURIComponent(doc)), {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(position), keepalive: true,
    }),
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
    supplement(id, file) {
      const fd = new FormData(); fd.append('file', file);
      return api.json(paperPath(id, '/supplements'), { method: 'POST', body: fd });
    },
    removeSupplement: (id, doc) => {
      if (!ID_RE.test(doc)) throw new Error('无效的补充文件 ID');
      return api.json(paperPath(id, '/supplements/' + encodeURIComponent(doc)), { method: 'DELETE' });
    },
    illustrate: (id, notes, all) => api.jsonBody('POST', paperPath(id, '/notes/illustrate' + (all ? '?all_figures=1' : '')), { notes }),
    prepareExport: (id, notes) => api.jsonBody('POST', paperPath(id, '/notes/export-preview'), { notes }),
    importDoi: (doi) => api.jsonBody('POST', '/api/import/doi', { doi }),
    enrich: (id, body) => api.jsonBody('POST', paperPath(id, '/enrich'), body),
    related: (id, refresh) => api.json(paperPath(id, '/related') + (refresh ? '?refresh=1' : '')),
    patch: (id, body) => api.jsonBody('PATCH', paperPath(id), body),
    autoTags: (id) => api.json(paperPath(id, '/tags/auto'), { method: 'POST' }),
    extractResources: (id) => api.json(paperPath(id, '/resources/extract'), { method: 'POST' }),
    remove: (id) => api.json(paperPath(id), { method: 'DELETE' }),
    figures: (id) => api.json(paperPath(id, '/figures')),
    reextract: (id, doc) => api.json(paperPath(id, '/figures/extract?document_id=' + encodeURIComponent(doc || 'main')), { method: 'POST' }),
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
    documentId: 'main',
    materialBusy: false,
    favOnly: false,
    activeTag: '',
    tagWrites: new Map(),
    autoTagging: new Map(),
    autoTagRefresh: new Set(),
    extractingResources: new Map(),
    resourceExtractionRefresh: new Set(),
    query: '',
    readingFilter: '',
    decisionFilter: '',
    taskFilter: '',
    workflowSaving: false,
    workflowVersion: 0,
    tab: 'preview',
    pdf: null,
    pageWrappers: [],
    scale: 1,
    fitWidth: true,
    renderToken: 0,
    generating: null,
    saveTimer: null,
    pendingSave: null,
    saveInFlight: null,
    openToken: 0,
    pdfRenderTasks: new Set(),
    pdfView: null,
    pdfContext: null,
    pdfFetch: null,
    pdfDeferredPage: 1,
    readingCache: new Map(),
    readingPending: new Map(),
    readingSaving: null,
    readingTimer: null,
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
    const wasHidden = !$('pdf-scroll').clientWidth;
    const token = state.renderToken;
    document.body.dataset.view = view;
    document.querySelectorAll('#workspace-views [data-view]').forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    if (view === 'pdf' && state.pdf) requestAnimationFrame(() => {
      if (token !== state.renderToken) return;
      if (wasHidden || !state.pdfView) rerenderPdf(); else paintVisiblePages();
    });
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
      s.src = DOCX_SCRIPT;
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
      if (state.readingFilter && (p.reading_status || 'unread') !== state.readingFilter) return false;
      if (state.decisionFilter && (p.reading_decision || 'pending') !== state.decisionFilter) return false;
      if (state.taskFilter && !(p.tasks || []).some(t => state.taskFilter === 'any' || t.id === state.taskFilter)) return false;
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
        '<span class="badge">' + (READING_NAMES[p.reading_status] || '未读') + '</span>',
        '<span class="badge">' + (DECISION_NAMES[p.reading_decision] || '待判断') + '</span>',
      ].filter(Boolean).map((m) => '<span>' + m + '</span>').join('');
      const tagRow = (p.tags || []).length
        ? '<div class="paper-item-tags">' + p.tags.slice(0, 4).map((t) => '<span>#' + escapeHtml(t) + '</span>').join('') + '</div>' : '';
      return '<li class="paper-item' + active + '" data-id="' + p.id + '">' +
        '<button class="star' + (p.favorite ? ' on' : '') + '" data-star="' + p.id + '" title="' + (p.favorite ? '取消收藏' : '收藏文献') + '" aria-pressed="' + !!p.favorite + '">' + icon('star') + '</button>' +
        '<div class="paper-item-text"><button class="paper-open paper-item-title" data-id="' + p.id + '" title="' + escapeHtml(p.title) + '"' + (active ? ' aria-current="true"' : '') + '>' + escapeHtml(p.title) + '</button>' +
        '<div class="paper-item-meta">' + meta + '</div>' + tagRow +
        '<div class="list-tasks">' + (p.tasks || []).filter(t => TASK_NAMES[t.id]).map(t => '<button class="task-link" data-task="' + t.id + '">' + TASK_NAMES[t.id] + '</button>').join('') + '</div></div></li>';
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
    $('task-total').textContent = state.papers.filter(p => (p.tasks || []).length).length + ' 篇';
    const advancedCount = [state.readingFilter, state.decisionFilter, state.taskFilter, state.activeTag].filter(Boolean).length;
    $('active-filter-count').textContent = advancedCount ? advancedCount + ' 项筛选' : state.papers.filter(p => (p.tasks || []).length).length + ' 篇待办';
    $('clear-library-filters').hidden = ![state.query, state.favOnly, state.activeTag, state.readingFilter, state.decisionFilter, state.taskFilter].some(Boolean);
    if (!items.length) {
      list.innerHTML = '<li class="library-empty">' + (state.papers.length
        ? emptyState('search', '没有符合条件的文献', '试试其他关键词，或展开下方「筛选与待办」并点击「清除筛选」。')
        : emptyState('book', '从第一篇文献开始', '导入 PDF，或通过 DOI 添加文献。')) + '</li>';
    }
    renderTagChips();
  }

  function mergeWorkflow(target, updated) {
    for (const key of ['reading_status', 'reading_decision', 'tasks', 'notes_incomplete', 'notes_stale', 'evidence_pending', 'evidence_count', 'resources_count', 'resources_pending', 'storage_warnings', 'material_revision', 'notes_material_revision']) {
      if (Object.prototype.hasOwnProperty.call(updated, key)) target[key] = updated[key];
    }
  }

  function renderWorkflow() {
    const p = state.current;
    $('paper-workflow').hidden = !p;
    if (!p) return;
    $('paper-reading').value = p.reading_status || 'unread';
    $('paper-decision').value = p.reading_decision || 'pending';
    for (const id of ['paper-reading', 'paper-decision', 'notes-complete', 'notes-reviewed']) $(id).disabled = state.workflowSaving || !!state.generating || state.materialBusy;
    $('workflow-save').textContent = state.workflowSaving ? '正在保存…' : '由你标记阅读进度';
    $('storage-warning').hidden = !(p.storage_warnings || []).length;
    $('storage-warning').textContent = (p.storage_warnings || []).map(w => w.message).join(' ');
    $('paper-tasks').innerHTML = (p.tasks || []).filter(t => TASK_NAMES[t.id]).map(t => '<button class="task-link" data-task="' + t.id + '">' + TASK_NAMES[t.id] + '</button>').join('');
    $('notes-complete').hidden = !p.notes_incomplete;
    $('notes-reviewed').hidden = !p.notes_stale;
  }

  async function saveWorkflow(patch) {
    const paper = state.current;
    if (!paper || state.workflowSaving || state.generating || state.materialBusy) { renderWorkflow(); return false; }
    state.workflowSaving = true;
    state.workflowVersion++;
    renderWorkflow();
    try {
      if (!await flushSave() || state.current !== paper) return false;
      const updated = await api.patch(paper.id, patch);
      updateListItem(updated);
      if (state.current === paper) mergeWorkflow(paper, updated);
      toast('阅读记录已保存', 'ok');
      return true;
    } catch (e) { toast('阅读记录保存失败：' + e.message, 'error'); return false; }
    finally { state.workflowSaving = false; renderWorkflow(); }
  }

  async function refreshWorkflow(id) {
    const version = ++state.workflowVersion;
    const updated = await api.paper(id);
    if (state.workflowVersion !== version) return;
    const summary = state.papers.find(p => p.id === id);
    if (summary) mergeWorkflow(summary, updated);
    if (state.current && state.current.id === id) { mergeWorkflow(state.current, updated); renderWorkflow(); updateNoteActions(); }
    renderList();
  }

  async function openTask(id, task) {
    const paper = state.papers.find(p => p.id === id);
    if (!paper || !TASK_NAMES[task]) return;
    state.pendingPaper = paper;
    await openPaper();
    if (!state.current || state.current.id !== id) return;
    if (task === 'storage') { switchTab('preview'); toast((state.current.storage_warnings || []).map(w => w.message).join(' '), 'error'); return; }
    if (task === 'fulltext') { chooseAttachPdf(); return; }
    if (task === 'resume') { setWorkspaceView('pdf'); return; }
    if (task === 'evidence') { switchTab('evidence'); return; }
    if (task === 'resources') { switchTab('resources'); return; }
    if (task === 'notes_incomplete') { switchTab('edit'); return; }
    switchTab('preview');
    if (task === 'decision') openDecision();
    if (task === 'notes_stale') toast('材料已变化，请核对笔记后点击「已核对新材料」，或重新生成笔记');
  }

  function openDecision() {
    const paper = state.current;
    if (!paper) return;
    $('decision-modal').dataset.paperId = paper.id;
    $('decision-paper-title').textContent = paper.title;
    $('decision-value').value = paper.reading_decision || 'pending';
    $('decision-status').textContent = '';
    $('decision-save').disabled = false;
    $('decision-value').disabled = false;
    openModal('decision-modal', 'decision-value');
  }

  async function submitDecision() {
    const modal = $('decision-modal');
    const id = modal.dataset.paperId;
    const decision = $('decision-value').value;
    if (!state.current || state.current.id !== id) {
      closeModal('decision-modal');
      toast('当前文献已变化，请重新打开阅读价值判断', 'error');
      return false;
    }
    if (!Object.prototype.hasOwnProperty.call(DECISION_NAMES, decision)) return false;
    if (state.workflowSaving || state.generating || state.materialBusy) {
      $('decision-status').textContent = '请等待当前操作完成后再保存判断。';
      return false;
    }
    $('decision-save').disabled = true;
    $('decision-value').disabled = true;
    $('decision-status').textContent = '正在保存…';
    const saved = await saveWorkflow({ reading_decision: decision });
    if (modal.dataset.paperId === id) {
      $('decision-save').disabled = false;
      $('decision-value').disabled = false;
      $('decision-status').textContent = saved ? '' : '尚未保存。选择已保留，请重试。';
      if (saved) closeModal('decision-modal');
    }
    return saved;
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
    state.workflowVersion++;
    const idx = state.papers.findIndex((p) => p.id === paper.id);
    const summary = Object.assign({}, paper);
    preserveNewerTags(summary);
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
    if (!paper || state.materialBusy) return;
    if (!canLeaveResearch()) return;
    if (state.generating) { toast('请先停止生成再删除文献', 'error'); return; }
    if (!await flushSave()) return;
    if (!confirm('确定删除「' + paper.title + '」及其笔记与图片？此操作不可恢复。')) return;
    try {
      await api.remove(paper.id);
      state.papers = state.papers.filter((p) => p.id !== paper.id);
      state.openToken++;
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
    if (!paper || !file || state.generating || state.materialBusy) return;
    if (!canLeaveResearch()) return;
    if (!await flushSave() || state.current !== paper) return;
    state.materialBusy = true;
    state.openToken++;
    $('editor').readOnly = true;
    renderDocuments(); renderWorkflow();
    toast('正在解析 ' + file.name + ' …');
    try {
      const meta = await api.attach(paper.id, file);
      if (state.current !== paper) { updateListItem(meta); return; }
      preserveNewerTags(meta);
      state.current = meta;
      state.documentId = 'main';
      updateListItem(meta);
      renderHead();
      renderDocuments();
      loadPdf();
      await fetchFigures();
      $('btn-generate').disabled = false;
      toast('PDF 已附加，提取到 ' + (meta.figures_count || 0) + ' 张图', 'ok');
    } catch (e) {
      toast('附加失败：' + e.message, 'error');
    } finally {
      state.materialBusy = false;
      $('editor').readOnly = false;
      renderDocuments(); renderWorkflow();
      if (window.BioEvidence) window.BioEvidence.clear();
      if (window.BioResources) window.BioResources.clear();
      if (state.tab === 'evidence') openEvidence();
      if (state.tab === 'resources') openResources();
    }
  }

  async function enrichCurrent() {
    const paper = state.current;
    if (!paper) return;
    toast('正在查询 CrossRef …');
    try {
      const meta = await api.enrich(paper.id, { overwrite: false });
      preserveNewerTags(meta);
      if (state.current !== paper) { updateListItem(meta); return; }
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
    renderWorkflow();
    $('btn-capture-evidence').disabled = !state.current;
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
    renderHeadTags();
    updateFigCount(paper.figures_count || 0);
  }

  function renderHeadTags(preserveInput = true) {
    const paper = state.current;
    if (!paper) return;
    const box = $('head-tags');
    const draft = preserveInput && box.dataset.paperId === paper.id && $('tag-input') ? $('tag-input').value : '';
    const busy = state.autoTagging.has(paper.id);
    box.dataset.paperId = paper.id;
    box.innerHTML = '<div class="head-tag-list">' + (paper.tags || []).map((t) =>
      '<span class="chip on" data-tag="' + escapeHtml(t) + '">' + escapeHtml(t) + '<button class="x" data-x="' + escapeHtml(t) + '" aria-label="移除标签 ' + escapeHtml(t) + '">' + icon('close') + '</button></span>').join('') + '</div>' +
      '<input id="tag-input" placeholder="+ 标签" aria-label="添加文献标签" maxlength="40">' +
      '<button id="btn-auto-tags" class="btn btn-ghost btn-auto-tags" type="button" title="使用当前文本模型，根据已保存的论文内容识别标签，保留已有标签"' +
      (busy || state.generating ? ' disabled' : '') + ' aria-busy="' + busy + '">' + (busy ? '正在识别…' : '识别标签') + '</button>';
    if ($('tag-input')) $('tag-input').value = draft;
  }

  function preserveNewerTags(updated) {
    const current = state.current && state.current.id === updated.id ? state.current : null;
    const listed = state.papers.find(p => p.id === updated.id);
    for (const known of [current, listed]) {
      if (known && Number.isInteger(known.tags_revision) && known.tags_revision > (updated.tags_revision || 0)) {
        updated.tags = (known.tags || []).slice();
        updated.tags_revision = known.tags_revision;
      }
    }
  }

  function mergeTags(paper, updated) {
    if (!updated || !Array.isArray(updated.tags) || (updated.id && updated.id !== paper.id)) throw new Error('标签响应无效');
    const current = state.current && state.current.id === paper.id ? state.current : null;
    const listed = state.papers.find(p => p.id === paper.id);
    const targets = [paper, listed, current].filter(Boolean);
    const revision = updated.tags_revision;
    if (Number.isInteger(revision) && targets.some(p => Number.isInteger(p.tags_revision) && p.tags_revision > revision)) return;
    for (const target of targets) {
      target.tags = updated.tags.slice();
      if (Number.isInteger(revision)) target.tags_revision = revision;
    }
    if (current) renderHeadTags(true);
    renderList();
  }

  function changeTag(tag, remove) {
    const paper = state.current;
    tag = (tag || '').trim();
    if (!paper || !tag) return Promise.resolve(false);
    const id = paper.id;
    // Each operation starts from the last acknowledged tag list. Requests for
    // another paper are independent, and tag responses never replace its notes.
    const previous = state.tagWrites.get(id) || Promise.resolve();
    const writing = previous.then(async () => {
      const current = state.current && state.current.id === id ? state.current : null;
      const summary = state.papers.find(p => p.id === id);
      const tags = ((current || summary || paper).tags || []).slice();
      if (remove ? !tags.includes(tag) : tags.includes(tag)) return true;
      const next = remove ? tags.filter(t => t !== tag) : [...tags, tag];
      try {
        const updated = await api.patch(id, { tags: next });
        mergeTags(paper, updated);
        if (remove && state.activeTag === tag) state.activeTag = '';
        renderList();
        return true;
      } catch (e) {
        if (!remove && state.current && state.current.id === id && $('tag-input') && !$('tag-input').value) $('tag-input').value = tag;
        toast((remove ? '删除' : '添加') + '标签失败：' + e.message, 'error');
        return false;
      }
    });
    state.tagWrites.set(id, writing);
    writing.then(() => { if (state.tagWrites.get(id) === writing) state.tagWrites.delete(id); });
    return writing;
  }

  function addTag(tag) { return changeTag(tag, false); }
  function removeTag(tag) { return changeTag(tag, true); }

  function autoTags(paper = state.current, afterGeneration = false) {
    if (!paper || (!afterGeneration && state.generating)) return Promise.resolve(false);
    if (state.autoTagging.has(paper.id)) {
      // A completed newer note invalidates the request already in flight. Keep
      // one queued refresh for the latest saved note without delaying reading.
      if (afterGeneration) state.autoTagRefresh.add(paper.id);
      return state.autoTagging.get(paper.id);
    }
    if (!state.settings || !state.settings.has_api_key) {
      toast(afterGeneration ? '笔记已保存；请设置 API Key 后点击「识别标签」重试' : '请先在「设置」中填写 API Key，再识别标签', 'error');
      if (!afterGeneration) openSettings();
      return Promise.resolve(false);
    }
    const id = paper.id;
    // Share the per-paper queue with manual edits so a later manual addition
    // includes new automatic tags, and recognition sees earlier manual edits.
    const previous = state.tagWrites.get(id) || Promise.resolve();
    const identifying = previous.then(async () => {
      let generated = afterGeneration;
      for (;;) {
        let succeeded = false;
        try {
          if (!generated && state.current && state.current.id === id && (state.pendingSave || state.saveInFlight)) {
            if (!await flushSave()) {
              toast('笔记尚未保存，已暂停识别标签；请先重试保存，草稿仍保留在编辑器中', 'error');
              return false;
            }
          }
          const result = await api.autoTags(id);
          if (!state.autoTagRefresh.has(id)) {
            mergeTags(paper, result.paper);
            if (!generated) toast(result.message || ((result.added_tags || []).length ? '标签已识别' : '没有发现新的标签'), 'ok');
          }
          succeeded = true;
        } catch (e) {
          if (!state.autoTagRefresh.has(id)) toast((generated ? '笔记已保存；自动识别标签失败：' : '识别标签失败：') + e.message + '。可点击「识别标签」重试', 'error');
        }
        if (!state.autoTagRefresh.delete(id)) return succeeded;
        generated = true;
      }
    });
    state.autoTagging.set(id, identifying);
    state.tagWrites.set(id, identifying);
    if (state.current && state.current.id === id) renderHeadTags(true);
    identifying.then(() => {
      if (state.autoTagging.get(id) === identifying) state.autoTagging.delete(id);
      state.autoTagRefresh.delete(id);
      if (state.tagWrites.get(id) === identifying) state.tagWrites.delete(id);
      if (state.current && state.current.id === id) renderHeadTags(true);
    });
    return identifying;
  }

  function updateFigCount(n) {
    const badge = $('fig-count');
    badge.textContent = n || '';
    badge.hidden = !n;
  }

  function clearWorkspace() {
    if (window.BioEvidence) window.BioEvidence.clear();
    if (window.BioResources) window.BioResources.clear();
    setSaveStatus('');
    $('document-toolbar').hidden = true;
    $('btn-illustrate').disabled = true;
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
    if (state.current && state.current.id === target.id) { state.openToken++; return; }
    if (!canLeaveResearch()) return;
    if (state.materialBusy) { toast('正在处理补充材料，请稍候'); return; }
    if (state.generating) { toast('正在生成笔记，请先停止再切换文献', 'error'); return; }
    const token = ++state.openToken;
    if (!await flushSave() || token !== state.openToken) return;
    try {
      const full = await api.paper(target.id);
      if (token !== state.openToken) return;
      // The reader may have continued typing while the new paper loaded.
      if (!await flushSave() || token !== state.openToken) return;
      rememberReadingPosition();
      preserveNewerTags(full);
      state.current = full;
      if (window.BioEvidence) window.BioEvidence.clear();
      if (window.BioResources) window.BioResources.clear();
      if (!state.readingCache.has(full.id)) state.readingCache.set(full.id, full.reading_position || { documents: {} });
      const active = state.readingCache.get(full.id).active_document;
      state.documentId = (full.supplements || []).some(d => d.id === active) ? active : 'main';
    } catch (e) {
      if (token === state.openToken) toast('打开失败：' + e.message, 'error');
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
    state.figures = [];
    state.figDataUrls = {};
    renderFigures();
    renderDocuments();
    updateNoteActions();
    if (window.matchMedia('(max-width: 900px)').matches) setWorkspaceView('pdf');
    if (hasFile || state.documentId !== 'main') loadPdf(); else clearPdf();
    fetchFigures().then(() => {
      if (state.tab === 'evidence') openEvidence();
      if (state.tab === 'resources') openResources();
    });
  }

  function renderDocuments() {
    const paper = state.current;
    $('document-toolbar').hidden = !paper;
    if (!paper) return;
    const docs = [{ id: 'main', filename: '正文 · ' + (paper.filename || '尚未附加'), pages: paper.pages },
      ...(paper.supplements || []).map(d => ({ ...d, filename: '补充 · ' + d.filename }))];
    $('document-select').innerHTML = docs.map(d => '<option value="' + d.id + '">' + escapeHtml(d.filename) + '（' + d.pages + ' 页）</option>').join('');
    $('document-select').value = state.documentId;
    $('document-select').disabled = state.materialBusy;
    $('btn-remove-supplement').hidden = state.documentId === 'main';
    $('btn-supplement').disabled = state.materialBusy || !!state.generating;
    $('btn-remove-supplement').disabled = state.materialBusy || !!state.generating;
  }

  async function changeSupplements(files, remove) {
    const paper = state.current;
    if (!paper || state.materialBusy || state.generating) return;
    if (!canLeaveResearch()) return;
    if (!await flushSave() || state.current !== paper) return;
    if (remove && !confirm('移除当前补充 PDF？已有笔记和其中的图片会保留；重新生成笔记时将不再使用这份补充材料。')) return;
    state.materialBusy = true;
    state.openToken++;
    $('editor').readOnly = true;
    renderDocuments();
    try {
      if (remove) {
        const meta = await api.removeSupplement(paper.id, state.documentId);
        preserveNewerTags(meta);
        state.current = meta;
        state.documentId = 'main';
      } else {
        for (const file of files) {
          toast('正在解析补充材料 ' + file.name + ' …');
          const meta = await api.supplement(paper.id, file);
          preserveNewerTags(meta);
          state.current = meta;
          state.documentId = state.current.supplements.at(-1).id;
        }
      }
      toast('材料已更新；重新生成笔记即可结合最新材料分析', 'ok');
    } catch (e) { toast('补充材料处理失败：' + e.message, 'error'); }
    finally {
      state.materialBusy = false;
      $('editor').readOnly = false;
      renderDocuments(); renderHead(); updateListItem(state.current);
      await fetchFigures(); renderFigures(); loadPdf();
      if (window.BioEvidence) window.BioEvidence.clear();
      if (window.BioResources) window.BioResources.clear();
      if (state.tab === 'evidence') openEvidence();
      if (state.tab === 'resources') openResources();
    }
  }

  async function illustrateNotes() {
    const paper = state.current;
    if (!paper || state.generating || state.materialBusy) return;
    const original = $('editor').value;
    try {
      const result = await api.illustrate(paper.id, original, true);
      if (state.current !== paper || $('editor').value !== original) { toast('笔记已变化，请再次点击自动配图'); return; }
      $('editor').value = result.notes;
      scheduleSave(); renderPreview();
      if (!await flushSave()) return;
      toast(result.notes === original ? '没有需要补入的图片；可先检查图库' : '已自动补入原文图片', 'ok');
    } catch (e) { toast('自动配图失败：' + e.message, 'error'); }
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
      if (fig) {
        const jump = document.createElement('button');
        jump.className = 'btn btn-ghost note-source-link';
        jump.dataset.noteSourceFig = fig.id;
        jump.textContent = (fig.document_name || '正文') + ' · 第 ' + fig.page + ' 页 · 查看原文';
        real.after(jump);
      }
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
    state.pendingSave = { id, notes, ...(state.current.notes_incomplete ? { notes_incomplete: true } : {}),
      ...(Number.isInteger(state.current.notesDraftRevision) ? { notes_source_revision: state.current.notesDraftRevision } : {}) };
    setSaveStatus('待保存', 'pending');
    updateNoteActions();
    state.saveTimer = setTimeout(flushSave, 800);
  }

  async function flushSave() {
    clearTimeout(state.saveTimer);
    // All callers await the same drain: older writes cannot finish after newer ones.
    if (state.saveInFlight) return state.saveInFlight;
    state.saveInFlight = drainSaves();
    try { return await state.saveInFlight; }
    finally { state.saveInFlight = null; }
  }

  async function drainSaves() {
    while (state.pendingSave) {
      const pending = state.pendingSave;
      state.pendingSave = null;
      setSaveStatus('正在保存…', 'pending');
      try {
        const body = { notes: pending.notes };
        if (pending.notes_incomplete !== undefined) body.notes_incomplete = pending.notes_incomplete;
        if (pending.notes_source_revision !== undefined) body.notes_source_revision = pending.notes_source_revision;
        const updated = await api.patch(pending.id, body);
        updateListItem(updated);
        if (state.current && state.current.id === pending.id) {
          state.current.notes_updated_at = updated.notes_updated_at;
          mergeWorkflow(state.current, updated);
          if (pending.notes_source_revision !== undefined) delete state.current.notesDraftRevision;
          renderHead();
          updateNoteActions();
          if (!state.pendingSave && $('editor').value === pending.notes) setSaveStatus('已保存', 'saved');
        }
      } catch (e) {
        // Retain the newest draft for Cmd/Ctrl+S or the next autosave attempt.
        if (!state.pendingSave) state.pendingSave = pending;
        if (state.current && state.current.id === pending.id) setSaveStatus('保存失败 · 按 ⌘/Ctrl+S 重试', 'error');
        toast('保存笔记失败，内容仍保留在编辑器中：' + e.message, 'error');
        return false;
      }
    }
    return true;
  }

  function updateNoteActions() {
    const empty = !$('editor').value.trim();
    const noExport = empty && !hasResearchRecords(state.current);
    $('btn-export-md').disabled = noExport;
    $('btn-export-word').disabled = noExport;
    $('btn-copy-wechat').disabled = noExport;
    $('btn-illustrate').disabled = empty || !!state.generating;
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
    renderWorkflow();
    renderHeadTags(true);
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
    if (!paper || state.generating || state.materialBusy) return;
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
    if (!await flushSave() || state.current !== paper || state.generating) return;
    state.openToken++; // Invalidate any earlier paper-open request.
    const previousNotes = $('editor').value;

    const mode = $('read-mode').value;
    const ctrl = new AbortController();
    state.generating = ctrl;
    renderDocuments();
    setSaveStatus('正在生成…', 'pending');
    setGenerating(true, '正在连接 ' + state.settings.model + ' …');
    switchTab('preview');
    const editor = $('editor');
    editor.value = '';
    renderPreview('');

    let acc = '';
    let buffer = '';
    let lastRender = 0;
    let completed = false;
    let saved = false;
    let donePaper = null;
    let sourceRevision = paper.material_revision;
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
              if (Number.isInteger(event.material_revision)) sourceRevision = event.material_revision;
              setGenerating(true, event.model + ' 正在' + (event.mode_name || mode) + '，笔记会实时出现…');
            } else if (event.type === 'stage') {
              setStage(event.message || event.stage, event.total ? { done: event.done, total: event.total } : null);
              if (event.stage === 'figures' && event.done) fetchFigures();
            } else if (event.type === 'error') {
              throw new Error(event.message);
            } else if (event.type === 'done') {
              if (typeof event.notes !== 'string') throw new Error('笔记完成响应无效');
              acc = event.notes;
              donePaper = event.paper || null;
              completed = true;
            }
          }
        }
      }
      if (!completed) throw new Error('连接提前结束，未收到保存确认');
      paper.notes = acc;
      paper.notes_mode = mode;
      paper.notes_updated_at = new Date().toISOString();
      paper.notes_incomplete = false;
      delete paper.notesDraftRevision;
      paper.notes_stale = false;
      paper.tasks = (paper.tasks || []).filter(t => !['notes_incomplete', 'notes_stale'].includes(t.id));
      if (donePaper) mergeWorkflow(paper, donePaper);
      if (donePaper && Array.isArray(donePaper.tags)) mergeTags(paper, donePaper);
      editor.value = acc;
      renderPreview(acc);
      $('btn-export-md').disabled = !acc;
      $('btn-export-word').disabled = !acc;
      $('btn-copy-wechat').disabled = !acc;
      updateListItem(paper);
      renderHead();
      setSaveStatus('已保存', 'saved');
      toast('笔记生成完成', 'ok');
      saved = true;
    } catch (e) {
      const stopped = e.name === 'AbortError';
      // A failed request must not erase the previous note. Partial output is
      // saved through the same queue as manual edits, before switching is allowed.
      editor.value = acc || previousNotes;
      paper.notes = editor.value;
      renderPreview();
      updateNoteActions();
      if (acc) {
        paper.notes_incomplete = true;
        paper.notesDraftRevision = sourceRevision;
        state.pendingSave = { id: paper.id, notes: acc, notes_incomplete: true,
          ...(Number.isInteger(sourceRevision) ? { notes_source_revision: sourceRevision } : {}) };
        const saved = await flushSave();
        if (saved) setSaveStatus('部分内容已保存 · 未完成', 'saved');
      } else {
        setSaveStatus(previousNotes ? '已保留原笔记' : (stopped ? '已停止' : '生成失败'), previousNotes ? 'saved' : '');
      }
      toast(stopped ? '已停止生成' + (acc ? '，已保留部分内容' : '') : '生成未完成：' + e.message, stopped ? '' : 'error');
    } finally {
      state.generating = null;
      renderDocuments(); updateNoteActions(); renderWorkflow();
      setGenerating(false);
    }
    if (completed && saved) {
      void autoTags(paper, true);
      if (mode === 'deep') void extractResources(paper, { automatic: true });
    }
  }

  function stopGenerating() {
    if (state.generating) state.generating.abort();
  }

  async function exportMarkdown() {
    if (!canLeaveResearch()) return;
    const paper = state.current;
    if (!paper) return;
    const notes = $('editor').value;
    if (!notes.trim() && !hasResearchRecords(paper)) { toast('还没有笔记或研究记录可导出'); return; }
    try {
      const resp = await fetch(paperPath(paper.id, '/export.zip'), { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notes }) });
      if (!resp.ok) throw new Error((await resp.json()).detail || '导出失败');
      downloadRaw(safeFilename(paper.title) + '.zip', await resp.blob());
      toast('已导出 Markdown 和图片；解压后打开 report.md', 'ok');
    } catch (e) { toast('导出失败：' + e.message, 'error'); }
  }

  async function figureArrayBuffer(fid, paperId) {
    const resp = await fetch(api.figUrl(paperId, fid));
    if (!resp.ok) throw new Error('图片 ' + fid + ' 加载失败');
    return await resp.arrayBuffer();
  }

  function wordInlineRuns(text, D, options = {}) {
    const decode = value => String(value).replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (raw, entity) => {
      const named = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };
      if (named[entity.toLowerCase()]) return named[entity.toLowerCase()];
      const code = entity[1].toLowerCase() === 'x' ? parseInt(entity.slice(2), 16) : parseInt(entity.slice(1), 10);
      return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : raw;
    });
    if (!window.marked || !marked.Lexer) return [new D.TextRun({ text, ...options })];
    const visit = (tokens, style) => tokens.flatMap(token => {
      if (token.type === 'strong' || token.type === 'em' || token.type === 'del') {
        return visit(token.tokens || [], { ...style, [token.type === 'strong' ? 'bold' : token.type === 'em' ? 'italics' : 'strike']: true });
      }
      if (token.type === 'link') {
        let url;
        try { url = new URL(token.href); } catch (e) { /* plain label below */ }
        const children = visit(token.tokens || [{ type: 'text', text: token.text }], style);
        if (url && ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && D.ExternalHyperlink) {
          return [new D.ExternalHyperlink({ link: url.href, children })];
        }
        return children;
      }
      if (token.tokens) return visit(token.tokens, style);
      if (token.type === 'br') return [new D.TextRun({ break: 1, ...style })];
      return [new D.TextRun({ text: decode(token.text || token.raw || ''), ...style,
        ...(token.type === 'codespan' ? { font: 'Menlo', size: 22 } : {}) })];
    });
    return visit(marked.Lexer.lexInline(text), options);
  }

  function wordTable(token, D) {
    return new D.Table({
      width: { size: 100, type: D.WidthType.PERCENTAGE },
      rows: [token.header, ...token.rows].map((cells, index) => new D.TableRow({
        tableHeader: index === 0,
        children: cells.map((cell) => new D.TableCell({
          children: [new D.Paragraph({
            children: wordInlineRuns(cell.text, D, { bold: index === 0, size: 22 }),
          })],
        })),
      })),
    });
  }

  async function exportWord() {
    if (!canLeaveResearch()) return;
    const paper = state.current;
    if (!paper) return;
    let md = $('editor').value;
    const figures = state.figures.slice();
    await flushSave();
    if (!md.trim() && !hasResearchRecords(paper)) { toast('还没有笔记或研究记录可导出'); return; }
    toast('正在生成 Word …');
    const ok = await ensureDocx();
    if (!ok || !window.docx) { toast('Word 导出组件加载失败，请重新启动应用', 'error'); return; }
    const D = window.docx;
    try {
      md = (await api.prepareExport(paper.id, md)).notes;
      const figRefs = figureMarkdownRefs(md);
      const buffers = {};
      const figMeta = {};
      await Promise.all(figRefs.map(async (m) => {
        try {
          buffers[m[2]] = await figureArrayBuffer(m[2], paper.id);
          const fig = figures.find((f) => f.id === m[2]);
          figMeta[m[2]] = fig || { label: m[1], caption: '', page: 0 };
        } catch (e) { throw new Error('无法导出图片：' + e.message); }
      }));

      const children = [];
      const pushText = (text, opts) => {
        if (!text.trim()) return;
        const runs = wordInlineRuns(text, D, opts);
        children.push(new D.Paragraph({ children: runs, spacing: { after: 120 } }));
      };

      const lines = md.split('\n');
      for (let lineIndex = 0; lineIndex < lines.length; lineIndex++) {
        const line = lines[lineIndex];
        const t = line.trim();
        // Use the same GFM parser as the preview, including escaped pipes and
        // tables without outer pipes. Preserve real editable Word cells.
        if (window.marked && t.includes('|') && /^[\s|:-]+$/.test(lines[lineIndex + 1] || '')) {
          const token = marked.lexer(lines.slice(lineIndex).join('\n'), { gfm: true })[0];
          if (token && token.type === 'table') {
            children.push(wordTable(token, D));
            lineIndex += token.raw.replace(/\n$/, '').split('\n').length - 1;
            continue;
          }
        }
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
        if (!t || /^<!-- (?:bioreader-research-appendix:v1 sha256=[0-9a-f]{64}|\/bioreader-research-appendix:v1) -->$/.test(t)) continue;
        let hm;
        if ((hm = /^#{1,6}\s+(.*)$/.exec(t))) {
          const level = Math.min(4, t.match(/^#+/)[0].length);
          children.push(new D.Paragraph({ children: wordInlineRuns(hm[1], D), heading: [D.HeadingLevel.HEADING_1, D.HeadingLevel.HEADING_2, D.HeadingLevel.HEADING_3, D.HeadingLevel.HEADING_4][level - 1], spacing: { before: 240, after: 120 } }));
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
    if (!['preview', 'edit', 'figures', 'evidence', 'resources'].includes(name)) return;
    state.tab = name;
    document.body.dataset.noteTab = name;
    document.querySelectorAll('#tabs .tab').forEach((b) => {
      const active = b.dataset.tab === name;
      b.classList.toggle('active', active);
      b.setAttribute('aria-selected', String(active));
      b.tabIndex = active ? 0 : -1;
    });
    ['preview', 'edit', 'figures', 'evidence', 'resources'].forEach((t) => { $('tab-' + t).hidden = t !== name; });
    if (window.matchMedia('(max-width: 900px)').matches) setWorkspaceView('notes');
    if (name === 'figures') renderFigures();
    if (name === 'preview') renderPreview();
    if (name === 'edit') $('editor').focus();
    if (name === 'evidence') openEvidence();
    if (name === 'resources') openResources();
  }

  function canLeaveResearch() {
    return (!window.BioEvidence || window.BioEvidence.canLeave()) && (!window.BioResources || window.BioResources.canLeave());
  }

  function openResources() {
    if (window.BioResources) window.BioResources.open(state.current);
  }

  function mergeResourceSummary(paper, updated) {
    if (!updated || updated.id !== paper.id) throw new Error('资源整理响应无效');
    const current = state.current && state.current.id === paper.id ? state.current : null;
    const listed = state.papers.find(p => p.id === paper.id);
    for (const target of [paper, current, listed].filter(Boolean)) {
      for (const key of ['resources_count', 'resources_pending']) {
        if (Object.prototype.hasOwnProperty.call(updated, key)) target[key] = updated[key];
      }
      if (Array.isArray(updated.tasks)) target.tasks = (target.tasks || []).filter(t => t.id !== 'resources')
        .concat(updated.tasks.filter(t => t.id === 'resources'));
    }
    state.workflowVersion++;
    renderList();
    if (current) { renderWorkflow(); updateNoteActions(); }
  }

  function extractResources(paper = state.current, { automatic = false } = {}) {
    if (!paper) return Promise.resolve(false);
    if (automatic && (paper.notes_mode !== 'deep' || paper.notes_incomplete || paper.notes_stale || !(paper.notes || '').trim())) return Promise.resolve(false);
    const id = paper.id;
    if (state.extractingResources.has(id)) {
      if (automatic) state.resourceExtractionRefresh.add(id);
      return state.extractingResources.get(id);
    }
    if (!automatic && (state.generating || state.materialBusy)) {
      toast('请等待当前操作完成后再整理数据与代码', 'error');
      return Promise.resolve(false);
    }
    const extracting = Promise.resolve().then(async () => {
      let generated = automatic;
      for (;;) {
        let succeeded = false;
        try {
          if (!generated && state.current && state.current.id === id && (state.pendingSave || state.saveInFlight)) {
            if (!await flushSave()) {
              toast('笔记尚未保存，已暂停整理资源；请先重试保存，草稿仍保留在编辑器中', 'error');
              return false;
            }
          }
          const version = state.workflowVersion;
          const result = await api.extractResources(id);
          if (!state.resourceExtractionRefresh.has(id)) {
            // A later workflow mutation wins over the extraction snapshot.
            if (version === state.workflowVersion) mergeResourceSummary(paper, result.paper);
            else await refreshWorkflow(id);
            if (state.tab === 'resources' && window.BioResources?.refreshAfterExtraction) await window.BioResources.refreshAfterExtraction(id);
            if (!generated) toast(result.message || '已从精读笔记整理数据与代码，请核对来源和用途', 'ok');
          }
          succeeded = true;
        } catch (e) {
          if (!state.resourceExtractionRefresh.has(id)) toast((generated ? '笔记已保存；自动整理数据与代码失败：' : '整理数据与代码失败：') + e.message + '。可在「数据与代码」中重试', 'error');
        }
        if (!state.resourceExtractionRefresh.delete(id)) return succeeded;
        generated = true;
      }
    });
    state.extractingResources.set(id, extracting);
    extracting.then(() => {
      if (state.extractingResources.get(id) === extracting) state.extractingResources.delete(id);
      state.resourceExtractionRefresh.delete(id);
    });
    return extracting;
  }

  function hasResearchRecords(paper) {
    return !!(paper && (paper.evidence_count || paper.resources_count));
  }

  function openEvidence() {
    if (window.BioEvidence) window.BioEvidence.open(state.current, state.figures);
  }

  async function captureEvidence() {
    if (!state.current || !window.BioEvidence) return;
    const editor = $('editor');
    const selection = window.getSelection();
    const text = state.tab === 'edit' ? editor.value.slice(editor.selectionStart, editor.selectionEnd)
      : (selection && $('preview').contains(selection.anchorNode) ? selection.toString() : '');
    switchTab('evidence');
    window.BioEvidence.start({ claim: text.slice(0, 4000), document_id: state.documentId, page: currentPage() });
  }

  async function figureDataUrl(fid, paperId) {
    const cacheKey = paperId + ":" + fid;
    if (state.figDataUrls[cacheKey]) return state.figDataUrls[cacheKey];
    const resp = await fetch(api.figUrl(paperId, fid));
    if (!resp.ok) throw new Error('figure ' + fid + ' unavailable');
    const blob = await resp.blob();
    return await new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => { state.figDataUrls[cacheKey] = fr.result; resolve(fr.result); };
      fr.onerror = () => reject(new Error('read failed'));
      fr.readAsDataURL(blob);
    });
  }

  async function buildWechatHtml() {
    // Embed figure images as data URLs so the copied HTML survives outside the app.
    const paper = state.current;
    const original = $('editor').value;
    if (!paper || (!original.trim() && !hasResearchRecords(paper))) return '';
    const theme = $('wechat-theme').value;
    const md = (await api.prepareExport(paper.id, original)).notes;
    let prepared = md;
    for (const m of figureMarkdownRefs(md)) {
      try {
        const url = await figureDataUrl(m[2], paper.id);
        prepared = prepared.split('(figure:' + m[2] + ')').join('(' + url + ')');
      } catch (e) { throw new Error('图片无法加载：' + e.message); }
    }
    return WechatExport.render(prepared, theme);
  }

  async function copyWechat() {
    if (!canLeaveResearch()) return;
    if (!state.current) return;
    const notes = $('editor').value;
    if (!notes.trim() && !hasResearchRecords(state.current)) { toast('还没有笔记或研究记录可复制'); return; }
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
    const paper = state.current;
    if (!paper) return [];
    try {
      const figures = await api.figures(paper.id);
      if (state.current !== paper) return [];
      state.figures = figures;
    } catch (e) {
      if (state.current !== paper) return [];
      state.figures = [];
    }
    if (state.tab === 'figures') renderFigures();
    if (state.tab === 'preview' && !state.generating) renderPreview();
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
        '<div class="fig-label">' + escapeHtml(f.label) + '<span class="badge">' + escapeHtml(f.document_name || '正文') + '</span><span class="badge">' + (f.source === 'manual' ? '手动' : '自动') + '</span><span class="badge">p.' + f.page + '</span></div>' +
        '<div class="fig-caption" data-caption="' + f.id + '" title="点击展开 / 收起" role="button" tabindex="0" aria-expanded="false">' + escapeHtml(f.caption || '（无图注）') + '</div>' + obs +
        '<div class="fig-actions">' +
        '<button class="btn" data-source-fig="' + f.id + '" title="打开这张图所在的 PDF 页面">' + icon('file') + ' 查看原文</button>' +
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
    const paper = state.current;
    if (!paper || state.materialBusy || state.generating) return;
    toast('正在重新提取图片 …');
    try {
      const res = await api.reextract(paper.id, state.documentId);
      if (!state.current || state.current.id !== paper.id) return;
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

  function rememberReadingPosition() {
    const context = state.pdfContext;
    if (!context || !state.pdfView || !$('pdf-scroll').clientHeight || !$('pdf-scroll').clientWidth) return;
    const position = { page: currentPage(), scale: state.scale, fit_width: state.fitWidth };
    const cached = state.readingCache.get(context.paperId) || { documents: {} };
    const changed = cached.active_document !== context.documentId || JSON.stringify(cached.documents[context.documentId]) !== JSON.stringify(position);
    cached.active_document = context.documentId;
    cached.documents[context.documentId] = position;
    state.readingCache.set(context.paperId, cached);
    if (changed) {
      const key = context.paperId + ':' + context.documentId;
      state.readingPending.delete(key);
      state.readingPending.set(key, { ...context, position });
    }
    if (!state.readingPending.size) return;
    clearTimeout(state.readingTimer);
    state.readingTimer = setTimeout(flushReadingPositions, 250);
  }

  async function flushReadingPositions() {
    clearTimeout(state.readingTimer);
    if (state.readingSaving) return state.readingSaving;
    state.readingSaving = (async () => {
      while (state.readingPending.size) {
        const [key, item] = state.readingPending.entries().next().value;
        try { await api.saveReading(item.paperId, item.documentId, item.position); }
        catch (e) {
          if (e.status !== 404) return; // Keep failures for the next reading action to retry.
          // Deleted papers/supplements must not block other documents' positions.
        }
        if (state.readingPending.get(key) === item) state.readingPending.delete(key);
      }
    })();
    try { await state.readingSaving; } finally { state.readingSaving = null; }
  }

  async function switchDocument(id, page) {
    const paper = state.current;
    if (!paper || state.materialBusy) return;
    if (id !== 'main' && !(paper.supplements || []).some(d => d.id === id)) return;
    if (id === state.documentId && state.pdfView) {
      if (page) goToPage(page, false);
      return;
    }
    state.documentId = id;
    renderDocuments();
    return loadPdf(page);
  }

  async function showFigureSource(fid) {
    const figure = state.figures.find(f => f.id === fid);
    if (!figure || state.materialBusy) return;
    setWorkspaceView('pdf');
    return switchDocument(figure.document_id || 'main', figure.page);
  }

  function clearPdf() {
    rememberReadingPosition();
    flushReadingPositions();
    state.renderToken++;
    if (state.pdfFetch) state.pdfFetch.abort();
    state.pdfFetch = null;
    cancelPdfRenders();
    state.pdfView = null;
    state.pdfContext = null;
    state.pdfDeferredPage = 1;
    if (state.pdf) { try { state.pdf.destroy(); } catch (e) { /* ignore */ } }
    state.pdf = null;
    state.pageWrappers = [];
    $('pdf-pages').innerHTML = '';
    $('pdf-total').textContent = '0';
    $('pdf-page').value = 1;
    $('pdf-scroll').scrollTop = 0;
    showPdfEmpty(!state.current ? 'welcome' : state.current.has_file ? 'loading' : 'missing');
    $('pdf-crop').disabled = true;
    setCropping(false);
  }

  async function loadPdf(targetPage) {
    const paper = state.current;
    if (!paper) return;
    const documentId = state.documentId;
    clearPdf();
    const token = state.renderToken;
    const position = state.readingCache.get(paper.id)?.documents[documentId];
    state.fitWidth = position ? position.fit_width : true;
    state.scale = position ? position.scale : 1;
    if (documentId === 'main' && !paper.has_file) { showPdfEmpty('missing'); return; }
    if (!window.pdfjsLib) {
      showPdfEmpty('error', 'PDF 阅读组件未能加载。请重新启动应用；若仍失败，请重新安装完整应用包。');
      toast('PDF 阅读组件未能加载，请重新启动应用', 'error');
      return;
    }
    pdfjsLib.GlobalWorkerOptions.workerSrc = PDF_WORKER;
    const controller = new AbortController();
    state.pdfFetch = controller;
    try {
      // Fetch the bytes through the validated path helper, then hand pdf.js a
      // buffer — no dynamically built URL is ever passed to getDocument().
      const resp = await fetch(paperPath(paper.id, '/file?document_id=' + encodeURIComponent(documentId)), { signal: controller.signal });
      if (!resp.ok) throw new Error('PDF 下载失败 HTTP ' + resp.status);
      const data = await resp.arrayBuffer();
      if (token !== state.renderToken) return;
      const doc = await pdfjsLib.getDocument({ data, cMapUrl: 'vendor/cmaps/', cMapPacked: true, standardFontDataUrl: 'vendor/standard_fonts/' }).promise;
      if (token !== state.renderToken) { doc.destroy(); return; }
      state.pdf = doc;
      state.pdfContext = { paperId: paper.id, documentId };
      $('pdf-empty').hidden = true;
      $('pdf-empty').setAttribute('aria-busy', 'false');
      $('pdf-total').textContent = doc.numPages;
      $('pdf-page').max = doc.numPages;
      $('pdf-crop').disabled = false;
      await renderPages(token, targetPage || position?.page || 1);
    } catch (e) {
      if (token === state.renderToken) {
        clearPdf();
        showPdfEmpty('error');
        toast('PDF 加载失败：' + e.message, 'error');
      }
    } finally { if (state.pdfFetch === controller) state.pdfFetch = null; }
  }

  async function renderPages(token, keepPage) {
    const doc = state.pdf;
    if (!doc) return;
    state.pdfDeferredPage = keepPage || 1;
    if (!$('pdf-scroll').clientWidth || !$('pdf-scroll').clientHeight) return;
    const container = $('pdf-pages');
    container.innerHTML = '';
    state.pageWrappers = [];
    state.pdfView = null;
    const first = await doc.getPage(1);
    if (token !== state.renderToken) return;
    const sizes = [];
    // Read geometry only, in bounded batches. Mixed-size pages then have stable
    // offsets before restoring the position; no full-document canvas allocation.
    for (let start = 1; start <= doc.numPages; start += 20) {
      const numbers = Array.from({ length: Math.min(20, doc.numPages - start + 1) }, (_, i) => start + i);
      const pages = await Promise.all(numbers.map(n => n === 1 ? first : doc.getPage(n)));
      if (token !== state.renderToken) return;
      sizes.push(...pages.map(page => page.getViewport({ scale: 1 })));
    }
    if (state.fitWidth) {
      const avail = $('pdf-scroll').clientWidth - 40;
      state.scale = Math.max(0.3, Math.min(3, avail / Math.max(...sizes.map(size => size.width))));
    }
    const scale = state.scale;
    $('pdf-zoom').textContent = Math.round(scale * 100) + '%';
    for (let i = 0; i < sizes.length; i++) {
      const w = document.createElement('div');
      w.className = 'pdf-page';
      w.dataset.page = i + 1;
      w.style.width = Math.floor(sizes[i].width * scale) + 'px';
      w.style.height = Math.floor(sizes[i].height * scale) + 'px';
      w.setAttribute('aria-label', '第 ' + (i + 1) + ' 页');
      container.appendChild(w);
      state.pageWrappers.push(w);
    }
    state.pdfView = { token, doc, scale, wanted: new Set(), canvases: new Map(), worker: null, task: null, failed: new Set() };
    goToPage(Math.max(1, Math.min(doc.numPages, keepPage || 1)), false);
    await paintVisiblePages();
  }

  function paintVisiblePages() {
    const view = state.pdfView, scroller = $('pdf-scroll');
    if (!view || !scroller.clientWidth || !scroller.clientHeight) return Promise.resolve();
    const current = currentPage();
    let last = current;
    while (last < state.pageWrappers.length && state.pageWrappers[last].offsetTop < scroller.scrollTop + scroller.clientHeight) last++;
    const wanted = [];
    for (let i = Math.max(1, current - 1); i <= Math.min(state.pageWrappers.length, last + 1); i++) wanted.push(i);
    wanted.sort((a, b) => Math.abs(a - current) - Math.abs(b - current));
    view.wanted = new Set(wanted);
    for (const [n, entry] of view.canvases) {
      if (view.wanted.has(n)) continue;
      entry.canvas.width = entry.canvas.height = 0;
      entry.canvas.remove();
      entry.page.cleanup();
      view.canvases.delete(n);
    }
    if (view.task && !view.wanted.has(view.task.number)) view.task.render.cancel();
    if (view.worker) return view.worker;
    view.worker = (async () => {
      while (view === state.pdfView) {
        const number = [...view.wanted].find(n => !view.canvases.has(n) && !view.failed.has(n));
        if (number === undefined) break;
        let page, canvas;
        try {
          page = await view.doc.getPage(number);
          if (view !== state.pdfView) return;
          if (!view.wanted.has(number)) { page.cleanup(); continue; }
          const vp = page.getViewport({ scale: view.scale });
          // Bound a single high-DPI canvas as well as the number of cached pages.
          const dpr = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(8_000_000 / (vp.width * vp.height)));
          canvas = document.createElement('canvas');
          canvas.width = Math.max(1, Math.floor(vp.width * dpr));
          canvas.height = Math.max(1, Math.floor(vp.height * dpr));
          canvas.style.width = Math.floor(vp.width) + 'px';
          canvas.style.height = Math.floor(vp.height) + 'px';
          const task = page.render({ canvasContext: canvas.getContext('2d'), viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null });
          view.task = { number, render: task };
          state.pdfRenderTasks.add(task);
          try { await task.promise; }
          finally { state.pdfRenderTasks.delete(task); view.task = null; }
          if (view !== state.pdfView || !view.wanted.has(number)) { canvas.width = canvas.height = 0; page.cleanup(); continue; }
          state.pageWrappers[number - 1].appendChild(canvas);
          view.canvases.set(number, { canvas, page });
        } catch (e) {
          if (canvas) canvas.width = canvas.height = 0;
          if (page && view === state.pdfView) page.cleanup();
          if (view !== state.pdfView) return;
          if (e.name === 'RenderingCancelledException') continue;
          view.failed.add(number);
          const retry = document.createElement('button');
          retry.className = 'btn pdf-retry'; retry.textContent = '第 ' + number + ' 页加载失败，点击重试';
          retry.addEventListener('click', () => { retry.remove(); view.failed.delete(number); paintVisiblePages(); });
          state.pageWrappers[number - 1].appendChild(retry);
        }
      }
    })().finally(() => { view.worker = null; });
    return view.worker;
  }

  function releasePdfCanvases() {
    if (!state.pdfView) return;
    for (const { canvas, page } of state.pdfView.canvases.values()) {
      canvas.width = canvas.height = 0;
      page.cleanup();
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
    paintVisiblePages();
    rememberReadingPosition();
  }

  function cancelPdfRenders() {
    state.pdfRenderTasks.forEach((task) => task.cancel());
    state.pdfRenderTasks.clear();
    releasePdfCanvases();
  }

  function rerenderPdf() {
    // A hidden narrow-screen view has no measurable width or page offsets.
    if (!state.pdf || !$('pdf-scroll').clientWidth || !$('pdf-scroll').clientHeight) return;
    const page = state.pdfView ? currentPage() : state.pdfDeferredPage;
    rememberReadingPosition();
    state.renderToken++;
    cancelPdfRenders();
    const token = state.renderToken;
    renderPages(token, page).catch((e) => {
      if (token === state.renderToken) toast('PDF 渲染失败，请重新打开文献：' + e.message, 'error');
    });
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
    const paper = state.current;
    if (!paper || state.materialBusy) return;
    toast('正在截取第 ' + page + ' 页的选区 …');
    try {
      const fig = await api.cropFigure(state.current.id, { page, bbox, label: '', caption: '', document_id: state.documentId });
      if (!state.current || state.current.id !== paper.id) return;
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
    for (const [id, key] of [['filter-reading', 'readingFilter'], ['filter-decision', 'decisionFilter'], ['filter-task', 'taskFilter']]) {
      $(id).addEventListener('change', e => { state[key] = e.target.value; renderList(); });
    }
    $('clear-library-filters').addEventListener('click', () => {
      state.query = state.activeTag = state.readingFilter = state.decisionFilter = state.taskFilter = '';
      state.favOnly = false;
      for (const id of ['search', 'filter-reading', 'filter-decision', 'filter-task']) $(id).value = '';
      renderList();
    });
    $('paper-reading').addEventListener('change', e => saveWorkflow({ reading_status: e.target.value }));
    $('paper-decision').addEventListener('change', e => saveWorkflow({ reading_decision: e.target.value }));
    $('decision-save').addEventListener('click', submitDecision);
    $('decision-close').addEventListener('click', () => closeModal('decision-modal'));
    $('decision-cancel').addEventListener('click', () => closeModal('decision-modal'));
    $('decision-modal').addEventListener('click', e => { if (e.target === e.currentTarget) closeModal('decision-modal'); });
    $('decision-value').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); void submitDecision(); } });
    $('notes-complete').addEventListener('click', () => saveWorkflow({ notes_incomplete: false }));
    $('btn-library-resources').addEventListener('click', async () => {
      if (window.BioResources && !window.BioResources.canLeave()) return;
      $('library-refine').open = false;
      switchTab('resources');
      if (window.BioResources) await window.BioResources.showAll();
    });
    $('notes-reviewed').addEventListener('click', () => saveWorkflow({ notes_reviewed: true }));
    $('btn-capture-evidence').addEventListener('click', captureEvidence);
    $('preview').addEventListener('click', e => {
      const button = e.target.closest('[data-note-source-fig]');
      if (button) showFigureSource(button.dataset.noteSourceFig);
    });
    $('paper-tasks').addEventListener('click', e => {
      const task = e.target.closest('[data-task]');
      if (task && state.current) openTask(state.current.id, task.dataset.task);
    });
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
        if (!modal && $('library-refine').open) { $('library-refine').open = false; $('library-refine').querySelector('summary').focus(); }
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
        const task = e.target.closest('[data-task]');
        if (task) { openTask(item.dataset.id, task.dataset.task); return; }
        const paper = state.papers.find((p) => p.id === item.dataset.id);
        if (paper) { state.pendingPaper = paper; openPaper(); }
      }
    });
    $('tag-chips').addEventListener('click', (e) => {
      const chip = e.target.closest('[data-tag]');
      if (chip) { state.activeTag = state.activeTag === chip.dataset.tag ? '' : chip.dataset.tag; renderList(); }
    });
    $('head-tags').addEventListener('click', (e) => {
      if (e.target.closest('#btn-auto-tags')) { void autoTags(); return; }
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
    $('btn-supplement').addEventListener('click', () => $('supplement-input').click());
    $('supplement-input').addEventListener('change', (e) => { const files = [...e.target.files]; e.target.value = ''; if (files.length) changeSupplements(files, false); });
    $('btn-remove-supplement').addEventListener('click', () => changeSupplements([], true));
    $('document-select').addEventListener('change', (e) => {
      switchDocument(e.target.value);
    });
    $('btn-illustrate').addEventListener('click', illustrateNotes);
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
      const source = e.target.closest('[data-source-fig]');
      if (source) { showFigureSource(source.dataset.sourceFig); return; }
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
      scrollRaf = requestAnimationFrame(() => {
        scrollRaf = null;
        if (!state.pdfView) return;
        $('pdf-page').value = currentPage();
        paintVisiblePages();
        rememberReadingPosition();
      });
    });

    const sidebar = $('sidebar');
    ['dragenter', 'dragover'].forEach((ev) => document.addEventListener(ev, (e) => {
      if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files')) { e.preventDefault(); sidebar.classList.add('dragover'); }
    }));
    ['dragleave', 'drop'].forEach((ev) => document.addEventListener(ev, (e) => {
      if (ev === 'drop') { e.preventDefault(); if (e.dataTransfer && e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files); }
      if (ev === 'drop' || e.target === document.documentElement || e.relatedTarget === null) sidebar.classList.remove('dragover');
    }));

    window.addEventListener('beforeunload', (e) => {
      rememberReadingPosition();
      flushReadingPositions();
      if (state.pendingSave || state.saveInFlight || state.generating || !canLeaveResearch()) {
        flushSave();
        e.preventDefault();
        e.returnValue = '';
      }
    });
    window.addEventListener('pagehide', () => { rememberReadingPosition(); flushReadingPositions(); });
  }

  async function init() {
    if (window.BioResources) window.BioResources.init({
      request: (method, path, body) => body === undefined ? api.json(path, { method }) : api.jsonBody(method, path, body),
      extract: paper => extractResources(paper),
      jump: async (documentId, page) => { setWorkspaceView('pdf'); await switchDocument(documentId, page); },
      openPaper: async (id) => {
        const paper = state.papers.find(p => p.id === id);
        if (!paper) { toast('文献不在当前文献库，请刷新后重试', 'error'); return false; }
        state.pendingPaper = paper; await openPaper();
        if (!state.current || state.current.id !== id) return false;
        switchTab('resources'); return true;
      },
      onChanged: refreshWorkflow, toast,
    });
    if (window.BioEvidence) window.BioEvidence.init({
      request: (method, path, body) => body === undefined ? api.json(path, { method }) : api.jsonBody(method, path, body),
      jump: async (documentId, page) => { setWorkspaceView('pdf'); await switchDocument(documentId, page); },
      onChanged: refreshWorkflow,
      toast,
    });
    if (!window.marked) toast('Markdown 渲染组件未能加载，请重新启动应用', 'error');
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
