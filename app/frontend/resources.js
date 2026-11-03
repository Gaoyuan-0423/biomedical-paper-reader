(function () {
  'use strict';
  const ID = /^[0-9a-f]{32}$/;
  const KINDS = { dataset: '数据集', code: '代码', other: '其他资源' };
  const ROLES = { generated: '本文产生', reused: '复用已有资源', implementation: '代码实现', unknown: '待确认' };
  let hooks, panel, paper = null, records = [], mode = 'current', query = '', draft = null;
  let epoch = 0, busy = false, loading = false, extracting = false, opened = false, refreshPending = false, error = '';
  let geoCache = {}, geoBusy = new Set(), geoErrors = {}, geoExpanded = new Set();
  const AUTO_NOTE = '来自精读笔记自动整理，尚未核对原文。';
  const AUTO_SOURCE_NOTE = '来自精读笔记自动整理。';
  const CONTENT_FIELDS = ['title', 'purpose', 'identifiers', 'conditions', 'sample_details', 'repository', 'accession', 'url', 'organism', 'tissue', 'modality', 'notes', 'quote'];
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const current = token => token === epoch;
  const key = record => (record.paper_id || paper?.id || '') + ':' + record.id;
  const options = (items, selected) => Object.entries(items).map(([value, label]) => '<option value="' + esc(value) + '"' + (selected === value ? ' selected' : '') + '>' + esc(label) + '</option>').join('');

  function path(paperId, id) {
    if (!ID.test(paperId) || (id && !ID.test(id))) throw new Error('无效的资源记录');
    return '/api/papers/' + paperId + '/resources' + (id ? '/' + id : '');
  }
  function safeURL(value) {
    try { const url = new URL(String(value).trim()); return /^https?:$/.test(url.protocol) && !url.username && !url.password ? url.href : ''; }
    catch (_) { return ''; }
  }
  function cleanText(value) {
    return String(value || '').replace(/<br\s*\/?\s*>/gi, '\n').replace(/\*\*([^*]+)\*\*/g, '$1').replace(/`([^`]+)`/g, '$1');
  }
  function richText(value) {
    const text = cleanText(value), pattern = /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>"']+)/gi;
    let result = '', last = 0;
    for (const match of text.matchAll(pattern)) {
      result += esc(text.slice(last, match.index));
      const raw = match[2] || match[3], trimmed = match[3] ? raw.replace(/[.,;，。；）)]+$/, '') : raw;
      const href = safeURL(trimmed), label = match[1] || trimmed;
      result += href ? '<a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' + esc(label) + '</a>' : esc(match[0]);
      if (href && match[3]) result += esc(raw.slice(trimmed.length));
      last = match.index + match[0].length;
    }
    return result + esc(text.slice(last));
  }
  function cellText(value, fallback = '未记录') {
    const text = cleanText(value).trim();
    if (!text) return '<span class="resource-unrecorded">' + fallback + '</span>';
    const html = '<div class="resource-cell-text">' + richText(text) + '</div>';
    const preview = text.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/\n/g, ' ');
    return text.length > 220 ? '<details class="resource-long"><summary>' + esc(preview.slice(0, 100)) + '… <span>展开</span></summary>' + html + '</details>' : html;
  }
  function geoAccessions(record) {
    const urlPattern = /https?:\/\/[^\s<>"`\[\]{}|\u3000-\u303f\uff00-\uffef]+/gi;
    const identifiers = String(record.identifiers || ''), found = new Set();
    const collect = text => {
      for (const match of String(text).normalize('NFKC').matchAll(/(?:^|[^A-Za-z0-9_])(GSE\d+)(?![A-Za-z0-9_]|-[A-Za-z0-9]|\.[A-Za-z0-9])/gi)) found.add(match[1].toUpperCase());
    };
    collect(identifiers.replace(urlPattern, ' '));
    const accession = String(record.accession || '').normalize('NFKC');
    if (/^GSE\d+$/i.test(accession)) found.add(accession.toUpperCase());
    for (const match of (String(record.url || '') + ' ' + identifiers).matchAll(urlPattern)) {
      let raw = match[0].replace(/[.,;:!?]+$/, '');
      while (raw.endsWith(')') && (raw.match(/\)/g) || []).length > (raw.match(/\(/g) || []).length) raw = raw.slice(0, -1);
      const href = safeURL(raw);
      if (!href) continue;
      try {
        const url = new URL(href), pathname = decodeURIComponent(url.pathname);
        const allowed = (['ncbi.nlm.nih.gov', 'www.ncbi.nlm.nih.gov'].includes(url.hostname) && /^\/(geo\/|sra(\/|$)|bioproject(\/|$)|projects\/gap\/)/.test(pathname)) ||
          (['ebi.ac.uk', 'www.ebi.ac.uk'].includes(url.hostname) && /^\/(ena\/browser\/view\/|arrayexpress\/experiments\/|biostudies\/arrayexpress\/studies\/)/.test(pathname)) ||
          (url.hostname === 'ngdc.cncb.ac.cn' && /^\/(gsa\/|gsa-human\/|bioproject\/|omix\/)/.test(pathname));
        if (!allowed) continue;
        collect(pathname);
        for (const [name, value] of url.searchParams) if (['acc', 'term', 'id', 'study_id'].includes(name.toLowerCase()) && /^GSE\d+$/i.test(value)) found.add(value.toUpperCase());
      } catch (_) { /* Invalid URL encodings are not accession evidence. */ }
    }
    return [...found].sort();
  }
  function automatic(record) { return !!record.auto_extraction || String(record.notes || '').startsWith(AUTO_NOTE) || record.notes === AUTO_SOURCE_NOTE; }
  function readableNotes(record) {
    const notes = String(record.notes || '');
    return notes === AUTO_SOURCE_NOTE || notes === AUTO_NOTE || notes.startsWith(AUTO_NOTE + '笔记片段：') ? '' : notes;
  }
  function documents() {
    return paper ? (paper.has_file ? [{ id: 'main', name: '正文', pages: paper.pages }] : [])
      .concat((paper.supplements || []).map(d => ({ id: d.id, name: d.filename, pages: d.pages }))) : [];
  }
  function extractable() {
    return paper && paper.notes_mode === 'deep' && !!String(paper.notes || '').trim() && !paper.notes_incomplete && !paper.notes_stale;
  }
  function textField(name, label, value, limit = 1000, type = 'text') {
    return '<label>' + label + '<input data-resource-field="' + name + '" type="' + type + '" maxlength="' + limit + '" value="' + esc(value) + '"' + (busy ? ' disabled' : '') + (name === 'title' ? ' required' : '') + '></label>';
  }
  function textarea(name, label, value, limit) {
    return '<label>' + label + '<textarea data-resource-field="' + name + '" maxlength="' + limit + '" rows="3"' + (busy ? ' disabled' : '') + '>' + esc(value) + '</textarea></label>';
  }
  function formHTML() {
    const disabled = busy ? ' disabled' : '', docs = documents(), doc = docs.find(d => d.id === draft.document_id);
    return '<form class="resource-form" id="resource-form"><strong>' + (draft.id ? '编辑资源' : '添加数据或代码') + '</strong>' +
      textField('title', '资源名称', draft.title, 1000) +
      '<div class="resource-fields">' + textarea('purpose', '用途', draft.purpose, 12000) + textarea('identifiers', '编号／链接（同组可填多个）', draft.identifiers, 12000) + '</div>' +
      '<div class="resource-fields">' + textarea('conditions', '影响使用的条件', draft.conditions, 12000) + textarea('sample_details', '样品／材料（论文实际使用情况）', draft.sample_details, 12000) + '</div>' +
      '<details class="resource-details"' + (draft.detailsOpen ? ' open' : '') + '><summary>分类、兼容编号与原文依据</summary><div class="resource-details-body">' +
      '<div class="resource-fields"><label>资源类型<select data-resource-field="kind"' + disabled + '>' + options(KINDS, draft.kind) + '</select></label><label>与本文的关系<select data-resource-field="role"' + disabled + '>' + options(ROLES, draft.role) + '</select></label></div>' +
      '<div class="resource-fields">' + textField('repository', '仓库（如 GEO、GitHub）', draft.repository, 200) + textField('accession', '仓库编号（可选）', draft.accession, 300) + '</div>' +
      textField('url', '资源链接（可选）', draft.url, 4000, 'url') +
      textField('organism', '物种／研究对象', draft.organism, 1000) + textField('tissue', '组织／部位', draft.tissue, 1000) + textField('modality', '技术类型', draft.modality, 1000) +
      textarea('notes', '备注', draft.notes, 12000) +
      '<div class="resource-source-fields"><label>原文来源（可选）<select data-resource-field="document_id"' + disabled + '><option value="">不关联原文</option>' + docs.map(d => '<option value="' + esc(d.id) + '"' + (draft.document_id === d.id ? ' selected' : '') + '>' + esc(d.name) + '</option>').join('') + '</select></label>' +
      '<label>PDF 页码<input data-resource-field="page" type="number" min="1" max="' + (doc?.pages || 600) + '" value="' + esc(draft.page) + '"' + (!doc || busy ? ' disabled' : ' required') + '></label></div>' +
      textarea('quote', '原文摘录', draft.quote, 12000) + '</div></details>' +
      (error ? '<p class="resource-error" role="alert">' + esc(error) + '</p>' : '') +
      '<div class="resource-actions"><button class="btn btn-primary" type="submit"' + disabled + '>' + (busy ? '正在保存…' : '保存资源') + '</button><button class="btn btn-ghost" type="button" data-resource-action="cancel"' + disabled + '>取消</button></div></form>';
  }
  function paperLink(id, title) {
    return ID.test(id || '') ? '<button type="button" class="resource-paper-link" data-resource-action="paper" data-resource-paper="' + id + '" title="' + esc(title) + '">' + esc(title || '查看关联论文') + '</button>' : esc(title || '');
  }
  function geoHTML(record) {
    const paperId = record.paper_id || paper?.id, recordKey = esc(key(record));
    return geoAccessions(record).map(accession => {
      const cacheKey = paperId + ':' + accession, metadata = geoCache[cacheKey], pending = geoBusy.has(cacheKey);
      const button = '<button type="button" class="resource-geo-button" data-resource-action="geo" data-resource-key="' + recordKey + '" data-resource-accession="' + accession + '"' + (pending || busy || draft ? ' disabled' : '') + '>' + (pending ? '正在读取 GEO…' : metadata ? '刷新 GEO 信息' : '补充 GEO 信息 · ' + accession) + '</button>';
      const failure = geoErrors[cacheKey] ? '<p class="resource-error" role="alert">' + esc(geoErrors[cacheKey]) + '</p>' : '';
      if (!metadata) return '<div class="resource-geo">' + button + failure + '</div>';
      const samples = Array.isArray(metadata.samples) ? metadata.samples.slice(0, 3) : [];
      const count = Number.isFinite(metadata.geo_sample_count) ? metadata.geo_sample_count : '未报告';
      const total = Number.isFinite(metadata.samples_total) ? metadata.samples_total : count;
      const field = (label, values) => Array.isArray(values) && values.length ? '<p><b>' + label + '：</b>' + esc(values.join('；')) + '</p>' : '';
      const source = safeURL(metadata.source_url);
      return '<details class="resource-geo" data-geo-key="' + esc(cacheKey) + '"' + (geoExpanded.has(cacheKey) ? ' open' : '') + '><summary>GEO 样品信息 · ' + accession + '</summary><div class="resource-geo-body">' +
        '<p class="resource-geo-scope">GEO 收录 ' + esc(count) + ' 个样本，非论文实际使用数量。</p>' +
        '<strong>' + esc(metadata.title || accession) + '</strong>' +
        field('实验类型', metadata.experiment_types) + field('平台', metadata.platforms) +
        (metadata.overall_design ? '<details class="resource-geo-design"><summary>研究设计原文（供分组参考）</summary>' + cellText(metadata.overall_design) + '</details>' : '') +
        (metadata.summary ? '<details class="resource-geo-design"><summary>GEO 简介原文</summary>' + cellText(metadata.summary) + '</details>' : '') +
        '<p><b>样品示例（' + samples.length + '/' + esc(total) + '）</b>：仅展示公开记录中的部分样品，不能代表全部样品或论文分组。</p>' +
        samples.map(sample => '<section class="resource-geo-sample"><strong>' + esc(sample.accession) + ' · ' + esc(sample.title) + '</strong>' + field('物种', sample.organism) + field('材料来源', sample.source) + field('样品特征', sample.characteristics) + field('分子', sample.molecule) + field('文库策略', sample.library_strategy) + (safeURL(sample.source_url) ? '<a href="' + esc(safeURL(sample.source_url)) + '" target="_blank" rel="noopener noreferrer">查看样品记录</a>' : '') + '</section>').join('') +
        (Array.isArray(metadata.warnings) ? metadata.warnings : []).map(w => '<p class="resource-warning">' + esc(w) + '</p>').join('') +
        '<div class="resource-geo-footer">' + (source ? '<a href="' + esc(source) + '" target="_blank" rel="noopener noreferrer">GEO 原始记录</a>' : '') + (metadata.fetched_at ? '<span>获取于 ' + esc(metadata.fetched_at) + '</span>' : '') + button + '</div>' + failure + '</div></details>';
    }).join('');
  }
  function rowHTML(record) {
    const mutable = mode === 'current' && paper, disabled = busy || draft ? ' disabled' : '';
    const available = record.source_available !== false;
    const recordKey = esc(key(record));
    const details = [record.organism, record.tissue, record.modality].filter(Boolean);
    const related = (record.related_papers || []).filter(p => p.id !== (record.paper_id || paper?.id));
    const identifiers = record.identifiers || [record.accession, record.url].filter(Boolean).join('\n');
    return '<tr class="resource-row" data-resource-row="' + recordKey + '"><th scope="row"><div class="resource-title">' + richText(record.title) + '</div>' +
      (automatic(record) ? '<span class="resource-auto">笔记整理</span>' : '') +
      (mode === 'all' ? '<div class="resource-paper">来自 ' + paperLink(record.paper_id, record.paper_title) + '</div>' : '') +
      (related.length ? '<details class="resource-related"><summary>其他关联论文 · ' + related.length + '</summary>' + related.map(p => paperLink(p.id, p.title)).join('') + '</details>' : '') +
      '</th><td>' + cellText(record.purpose || (record.role !== 'unknown' ? ROLES[record.role] : '')) + '</td><td class="resource-identifiers">' + cellText(identifiers) + '</td><td>' + cellText(record.conditions) + '</td><td class="resource-samples">' + cellText(record.sample_details, '论文实际使用样品未记录') + geoHTML(record) + '</td>' +
      '<td class="resource-operations">' + (mutable ? '<button type="button" class="resource-edit" data-resource-action="edit" data-resource-key="' + recordKey + '"' + disabled + '>编辑</button>' : '') +
      '<details class="resource-row-details"><summary>详情</summary><div class="resource-row-details-body">' +
      '<p class="resource-meta">' + esc([KINDS[record.kind] || KINDS.other, record.repository, ...details].filter(Boolean).join(' · ')) + '</p>' +
      (readableNotes(record) ? cellText(readableNotes(record)) : '') +
      (record.quote ? '<blockquote>' + esc(record.quote) + '</blockquote>' : '') +
      (record.document_id ? '<p class="resource-source">' + esc(record.source_name || documents().find(d => d.id === record.document_id)?.name || '原文') + ' · PDF 第 ' + esc(record.page) + ' 页</p>' : '') +
      (!available ? '<p class="resource-warning">原文来源已移除或页码失效，请编辑来源。</p>' : record.source_changed ? '<p class="resource-warning">原文文件已更新，请检查资源与原文的关联。</p>' : '') +
      '<div class="resource-actions">' +
      (record.document_id ? '<button type="button" class="btn" data-resource-action="jump" data-resource-key="' + recordKey + '"' + (!available || busy ? ' disabled' : '') + '>查看原文</button>' : '') +
      (mutable ? '<button type="button" class="btn btn-ghost danger-ghost" data-resource-action="delete" data-resource-key="' + recordKey + '"' + disabled + '>删除</button>' : '') +
      '</div></div></details></td></tr>';
  }
  function render() {
    if (!panel) return;
    const visible = mode === 'current' && query ? records.filter(r => CONTENT_FIELDS.some(k => String(r[k] || '').toLowerCase().includes(query.toLowerCase()))) : records;
    panel.innerHTML = '<div class="resource-toolbar"><div><strong>数据与复现</strong><p class="hint">积累可复用资源，追踪它们来自哪些论文。</p></div>' +
      '<div class="resource-toolbar-actions"><button class="btn" type="button" data-resource-action="extract" aria-busy="' + extracting + '" title="仅整理已保存且材料未变更的完整精读笔记中的明确资源"' + (!paper || mode !== 'current' || loading || busy || draft || !extractable() ? ' disabled' : '') + '>' + (extracting ? '正在整理…' : '从精读笔记整理') + '</button>' +
      '<button class="btn btn-primary" type="button" data-resource-action="add"' + (!paper || mode !== 'current' || loading || busy || draft ? ' disabled' : '') + '>添加资源</button></div></div>' +
      (paper && mode === 'current' ? '<p class="hint resource-extract-hint">' + (extractable() ? '整理笔记中的资源、用途、使用条件与样品，可随时编辑补充。' : '完整精读笔记保存且材料未变更时，可在这里整理资源、用途、使用条件与样品。') + '</p>' : '') +
      '<div class="resource-modes" role="group" aria-label="资源范围"><button class="btn' + (mode === 'current' ? ' active' : '') + '" type="button" data-resource-mode="current" aria-pressed="' + (mode === 'current') + '"' + (!paper || busy || draft ? ' disabled' : '') + '>当前论文</button><button class="btn' + (mode === 'all' ? ' active' : '') + '" type="button" data-resource-mode="all" aria-pressed="' + (mode === 'all') + '"' + (busy || draft ? ' disabled' : '') + '>全部论文</button></div>' +
      '<form class="resource-search" id="resource-search"><input type="search" aria-label="搜索数据与复现" placeholder="搜索编号、样品、用途…" value="' + esc(query) + '" maxlength="500"' + (busy || draft ? ' disabled' : '') + '><button class="btn" type="submit"' + (busy || draft ? ' disabled' : '') + '>搜索</button></form>' +
      (!paper ? '<p class="hint">在全库中搜索资源；打开一篇论文后可添加与编辑它的资源。</p>' : '') +
      (draft ? formHTML() : '') + (!draft && error ? '<p class="resource-error" role="alert">' + esc(error) + ' <button class="btn btn-ghost" type="button" data-resource-action="reload">重试</button></p>' : '') +
      (loading ? '<p class="resource-empty" role="status">正在读取资源…</p>' : visible.length ? '<p class="resource-count">' + visible.length + ' 条资源记录 <span>宽表可横向滚动</span></p><div class="resource-table-scroll" tabindex="0" role="region" aria-label="数据与复现资源表"><table class="resource-table"><thead><tr><th scope="col">资源</th><th scope="col">用途</th><th scope="col">编号／链接</th><th scope="col">影响使用的条件</th><th scope="col">样品／材料</th><th scope="col">操作</th></tr></thead><tbody>' + visible.map(rowHTML).join('') + '</tbody></table></div>' : '<p class="resource-empty">' + (query ? '没有找到匹配资源。可尝试仓库编号或不同关键词。' : '暂无资源记录。阅读论文时，将数据仓库、代码和原文依据保存在这里。') + '</p>');
  }
  async function load() {
    const token = ++epoch;
    loading = true; error = ''; render();
    try {
      const result = await hooks.request('GET', mode === 'all' || !paper ? '/api/resources?q=' + encodeURIComponent(query) : path(paper.id));
      if (current(token)) {
        records = Array.isArray(result) ? result : result.resources || [];
        if (mode === 'current' && paper && records.some(r => geoAccessions(r).length)) {
          const paperId = paper.id;
          try {
            const cache = await hooks.request('GET', path(paperId) + '/geo');
            if (current(token) && cache?.records && !Array.isArray(cache.records)) {
              for (const [accession, metadata] of Object.entries(cache.records)) if (/^GSE\d+$/.test(accession) && metadata && typeof metadata === 'object') geoCache[paperId + ':' + accession] = metadata;
            }
          } catch (_) { /* Cached metadata is optional; the explicit GEO action can retry. */ }
        }
      }
    } catch (err) { if (current(token)) { error = '资源读取失败：' + err.message; records = []; } }
    finally { if (current(token)) { loading = false; render(); } }
  }
  async function open(nextPaper) {
    opened = true;
    const next = nextPaper && ID.test(nextPaper.id) ? nextPaper : null;
    const same = (paper?.id || '') === (next?.id || '');
    if (same && (draft || busy)) { paper = next; return; }
    if (!same) { draft = null; records = []; geoCache = {}; geoBusy = new Set(); geoErrors = {}; geoExpanded = new Set(); query = ''; extracting = false; refreshPending = false; mode = next ? 'current' : 'all'; }
    if (!next) mode = 'all';
    paper = next; busy = false;
    await load();
  }
  function clear() {
    ++epoch; paper = null; records = []; query = ''; mode = 'all'; draft = null; busy = false; loading = false; extracting = false; opened = false; refreshPending = false; error = '';
    geoCache = {}; geoBusy = new Set(); geoErrors = {}; geoExpanded = new Set();
    if (panel) panel.innerHTML = '';
  }
  async function showAll() {
    if (!canLeave()) return false;
    opened = true; draft = null; mode = 'all'; query = '';
    await load();
    return true;
  }
  async function refreshAfterExtraction(paperId) {
    if (!opened || (mode !== 'all' && paper?.id !== paperId)) return;
    if (draft || busy) { refreshPending = true; return; }
    refreshPending = false;
    await load();
  }
  async function extract() {
    if (!paper || mode !== 'current' || busy || loading || draft || !extractable()) return;
    const token = epoch, target = paper;
    let success = false;
    busy = true; extracting = true; error = ''; render();
    try {
      if (!hooks.extract) throw new Error('资源整理功能未能加载，请重新打开应用。');
      success = await hooks.extract(target);
      if (current(token) && success === false) error = '本次未完成整理，原有资源仍保留。可稍后重试。';
    } catch (err) { if (current(token)) { error = '整理失败：' + err.message; hooks.toast?.(error, 'error'); } }
    finally {
      if (current(token)) {
        busy = false; extracting = false;
        if (success) { refreshPending = false; await load(); } else render();
      }
    }
  }
  function start(record = {}) {
    if (!paper || mode !== 'current' || busy || loading || draft) return;
    const doc = documents().find(d => d.id === record.document_id);
    draft = { id: record.id || '', title: record.title || '', kind: record.kind || 'dataset', role: record.role || 'unknown',
      purpose: record.purpose || '', identifiers: record.identifiers || '', conditions: record.conditions || '', sample_details: record.sample_details || '',
      repository: record.repository || '', accession: record.accession || '', url: record.url || '', organism: record.organism || '', tissue: record.tissue || '',
      modality: record.modality || '', notes: readableNotes(record), document_id: doc ? doc.id : '', page: doc ? record.page : null, quote: record.quote || '',
      detailsOpen: !!(record.organism || record.tissue || record.modality || readableNotes(record) || record.document_id || record.quote) };
    error = ''; render(); panel.querySelector('[data-resource-field="title"]')?.focus();
  }
  function changed(id) { Promise.resolve().then(() => hooks.onChanged?.(id)).catch(() => hooks.toast?.('资源已保存，列表刷新失败，请稍后重试。', 'error')); }
  async function mutate(method, id, body) {
    if (!paper || mode !== 'current' || busy || loading) return;
    const paperId = paper.id, token = epoch;
    busy = true; error = ''; render();
    try {
      const result = await hooks.request(method, path(paperId, id), body);
      changed(paperId);
      if (!current(token)) return;
      if (method === 'DELETE') records = records.filter(r => r.id !== id);
      else if (id) records = records.map(r => r.id === id ? result : r);
      else records.unshift(result);
      if (draft && (method === 'POST' || draft.id === id)) draft = null;
      hooks.toast?.(method === 'DELETE' ? '资源已删除' : '资源已保存');
    } catch (err) { if (current(token)) { error = '保存失败：' + err.message; hooks.toast?.(error, 'error'); } }
    finally {
      if (current(token)) {
        busy = false; render();
        if (refreshPending && !draft) { refreshPending = false; await load(); }
      }
    }
  }
  function input(event) {
    if (event.target.matches?.('#resource-search input')) { query = event.target.value; return; }
    const field = event.target.dataset.resourceField;
    if (!draft || busy || ![...CONTENT_FIELDS, 'kind', 'role', 'document_id', 'page'].includes(field)) return;
    draft[field] = event.target.value;
  }
  async function submit(event) {
    if (!['resource-form', 'resource-search'].includes(event.target.id)) return;
    event.preventDefault();
    if (event.target.id === 'resource-search') { if (!busy && !draft) await load(); return; }
    if (!draft || busy) return;
    const doc = documents().find(d => d.id === draft.document_id), page = draft.document_id ? Number(draft.page) : null;
    if (!draft.title.trim()) { error = '请填写资源名称。'; render(); return; }
    if (draft.url.trim() && !safeURL(draft.url)) { error = '资源链接必须是有效的 http 或 https 地址，且不能包含账号或密码。'; render(); return; }
    if (draft.document_id && (!doc || !Number.isInteger(page) || page < 1 || page > doc.pages)) { error = '请选择有效的原文文件和 PDF 页码。'; render(); return; }
    const body = {};
    for (const name of [...CONTENT_FIELDS, 'kind', 'role', 'document_id']) body[name] = draft[name].trim();
    body.page = page;
    await mutate(draft.id ? 'PATCH' : 'POST', draft.id, body);
  }
  async function change(event) {
    const element = event.target;
    input(event);
    if (draft && !busy && element.dataset.resourceField === 'document_id') {
      draft.page = draft.document_id ? 1 : null; draft.detailsOpen = true; render();
    }
  }
  async function click(event) {
    const modeButton = event.target.closest('[data-resource-mode]');
    if (modeButton) {
      if (modeButton.disabled || !canLeave()) return;
      const next = modeButton.dataset.resourceMode;
      if (!['current', 'all'].includes(next) || (next === 'current' && !paper)) return;
      draft = null; mode = next; query = ''; await load(); return;
    }
    const button = event.target.closest('[data-resource-action]');
    if (!button || button.disabled || busy) return;
    const action = button.dataset.resourceAction;
    if (action === 'cancel') { draft = null; error = ''; render(); if (refreshPending) { refreshPending = false; await load(); } return; }
    if (action === 'add') { start(); return; }
    if (action === 'extract') { await extract(); return; }
    if (action === 'reload') { if (!draft) await load(); return; }
    if (action === 'paper') {
      if (!canLeave() || !ID.test(button.dataset.resourcePaper || '')) return;
      try { await hooks.openPaper(button.dataset.resourcePaper); } catch (err) { hooks.toast?.('无法打开论文：' + err.message, 'error'); }
      return;
    }
    const record = records.find(r => key(r) === button.dataset.resourceKey);
    if (!record) return;
    if (action === 'geo') { await fetchGeo(record, button.dataset.resourceAccession); return; }
    if (action === 'edit') { start(record); return; }
    if (action === 'delete' && mode === 'current' && !draft) {
      if (window.confirm('删除这条资源记录？仓库中的文件与其他论文记录不会删除。')) await mutate('DELETE', record.id);
    } else if (action === 'jump' && record.document_id && record.source_available !== false) {
      if (!canLeave()) return;
      try {
        if (record.paper_id && record.paper_id !== paper?.id && await hooks.openPaper(record.paper_id) === false) return;
        await hooks.jump(record.document_id, record.page);
      } catch (err) { hooks.toast?.('无法定位原文：' + err.message, 'error'); }
    }
  }
  async function fetchGeo(record, accession) {
    const paperId = record.paper_id || paper?.id, cacheKey = paperId + ':' + accession, token = epoch, pendingSet = geoBusy;
    if (draft || busy || !ID.test(paperId || '') || !geoAccessions(record).includes(accession) || geoBusy.has(cacheKey)) return;
    geoBusy.add(cacheKey); geoExpanded.add(cacheKey); delete geoErrors[cacheKey]; render();
    try {
      const result = await hooks.request('POST', path(paperId) + '/geo/' + accession + (geoCache[cacheKey] ? '?refresh=true' : ''));
      if (!result?.metadata || typeof result.metadata !== 'object') throw new Error('返回的信息不完整，请重试。');
      if (current(token)) geoCache[cacheKey] = result.metadata;
    } catch (err) {
      if (current(token)) geoErrors[cacheKey] = 'GEO 信息读取失败：' + err.message;
    } finally {
      pendingSet.delete(cacheKey);
      if (pendingSet === geoBusy && !draft) render();
    }
  }
  function canLeave() {
    if (extracting) { hooks.toast?.('正在整理资源，请稍后再切换。', 'error'); return false; }
    if (busy) { hooks.toast?.('正在保存资源，请稍后再切换。', 'error'); return false; }
    if (draft && (draft.id || [...CONTENT_FIELDS, 'document_id'].some(k => String(draft[k] || '').trim()) || draft.kind !== 'dataset' || draft.role !== 'unknown')) {
      hooks.toast?.('资源尚未保存，请先在「数据与复现」页保存或取消编辑。', 'error'); return false;
    }
    return true;
  }
  function init(options) {
    hooks = options;
    const next = document.getElementById('resources-panel');
    if (!next || next === panel) return;
    panel = next;
    panel.addEventListener('input', input); panel.addEventListener('change', change);
    panel.addEventListener('click', click); panel.addEventListener('submit', submit);
    panel.addEventListener('toggle', event => {
      if (event.target.isConnected === false) return;
      if (draft && event.target.classList.contains('resource-details')) draft.detailsOpen = event.target.open;
      const geoKey = event.target.dataset.geoKey;
      if (geoKey) { if (event.target.open) geoExpanded.add(geoKey); else geoExpanded.delete(geoKey); }
    }, true);
    render();
  }
  window.BioResources = { init, open, clear, canLeave, showAll, refreshAfterExtraction };
})();
