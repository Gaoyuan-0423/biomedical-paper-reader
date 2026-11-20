/* A batch is scoped to the library selected when it starts. Individual failures are retryable. */
(function () {
  'use strict';
  let hooks, currentView = null;
  const jobs = new Map(), paperRequests = new Map();
  const t = (key, params = {}) => window.BioI18n.t(key, params);
  window.BioI18n.register({
    '仅列出缺正文的论文。': 'Only papers without a main PDF are listed.',
    '查找选中论文的全文': 'Find full text for selected papers',
    '停止后续下载': 'Stop after this paper', '等待查找': 'Queued', '正在查找': 'Searching',
    '正文已下载': 'PDF downloaded', '已有正文': 'PDF already attached', '没有找到公开文件': 'No open file found',
    '获取失败': 'Download failed', '需人工核对': 'Review candidate', '尚未处理': 'Not processed',
    '重试未完成项': 'Retry unfinished items', '查看来源': 'Open source',
    '此范围内没有缺正文的论文。': 'All papers in this scope have a main PDF.',
    '已处理 {done} / {total} 篇': '{done} of {total} papers processed',
    '本轮查找已停止': 'Search stopped', '全选缺正文论文': 'Select all missing PDFs',
    '继续下载前请先保存当前编辑。': 'Save your current edits before downloading.',
  });
  const statuses = {queued:'等待查找',working:'正在查找',downloaded:'正文已下载',already_present:'已有正文',
    not_found:'没有找到公开文件',failed:'获取失败',needs_review:'需人工核对',stopped:'尚未处理'};
  const complete = outcome => outcome === 'downloaded' || outcome === 'already_present';
  function label(tag, key, params = {}) {
    const el = document.createElement(tag); el.textContent = t(key, params);
    el.dataset.i18n = key;
    if (Object.keys(params).length) el.dataset.i18nParams = JSON.stringify(params);
    return el;
  }
  function button(key, handler, primary = false) {
    const el = label('button', key); el.type = 'button'; el.className = primary ? 'btn btn-primary' : 'btn';
    el.onclick = handler; return el;
  }
  function webLink(value) {
    try { const url = new URL(value); return url.protocol === 'https:' ? url.href : ''; } catch (_) { return ''; }
  }
  async function acquire(paper, scope) {
    // Requests under different project scopes must pass their own membership checks.
    const key = scope + '/' + paper.id;
    if (paperRequests.has(key)) return paperRequests.get(key);
    const task = hooks.fetch(paper.id, scope).then(async result => {
      if (complete(result.outcome)) await hooks.attached(paper.id, result.paper);
      return result;
    });
    paperRequests.set(key, task);
    try { return await task; } finally { paperRequests.delete(key); }
  }
  async function run(view, papers) {
    if (jobs.get(view.key)?.running || !papers.length) return;
    if (!hooks.ready()) { hooks.toast(t('继续下载前请先保存当前编辑。'), 'error'); return; }
    let job = jobs.get(view.key);
    if (!job) { job = {results:new Map()}; jobs.set(view.key,job); }
    Object.assign(job, {running:true, stopped:false, done:0, total:papers.length});
    for (const paper of papers) job.results.set(paper.id, {paper, outcome:'queued'});
    const refresh = () => { if (currentView?.key === view.key) render(currentView); };
    refresh();
    try {
      for (const paper of papers) {
        const row = job.results.get(paper.id);
        if (job.stopped) { row.outcome = 'stopped'; continue; }
        row.outcome = 'working'; refresh();
        try {
          const result = await acquire(paper, view.scope);
          row.outcome = Object.hasOwn(statuses, result.outcome) ? result.outcome : 'failed';
          row.detail = result.message || ''; row.url = webLink(result.source?.url || paper.url ||
            (paper.doi ? 'https://doi.org/' + encodeURI(paper.doi) : ''));
        } catch (error) { row.outcome = 'failed'; row.detail = error.message; }
        job.done++; refresh();
      }
    } finally { job.running = false; refresh(); }
  }
  function render(view) {
    const job = jobs.get(view.key), root = view.root;
    // Keep completed results visible only while their paper still belongs to this view.
    const papers = new Map(view.papers.filter(p => !p.schema_incompatible &&
      (!p.has_file || complete(job?.results.get(p.id)?.outcome))).map(p => [p.id,p]));
    const actions = document.createElement('div'); actions.className = 'project-form-actions';
    const checks = document.createElement('div'); checks.className = 'download-checklist';
    const selectable = [...papers.values()].filter(p => !complete(job?.results.get(p.id)?.outcome));
    const start = button('查找选中论文的全文', () => run(view,selectable.filter(p => view.selected.has(p.id))), true);
    const selectAll = document.createElement('input'); selectAll.type = 'checkbox';
    function selectionChanged() {
      const count = selectable.filter(p => view.selected.has(p.id)).length;
      start.disabled = job?.running || count === 0;
      selectAll.checked = count > 0 && count === selectable.length;
      selectAll.indeterminate = count > 0 && count < selectable.length;
    }
    if (job?.running) actions.append(button('停止后续下载', () => { job.stopped = true; render(view); }));
    else if (selectable.length) {
      actions.append(start);
      const retry = selectable.filter(p => ['failed','stopped','needs_review','not_found'].includes(job?.results.get(p.id)?.outcome));
      if (retry.length && !view.single) actions.append(button('重试未完成项', () => run(view,retry)));
    }
    if (!job?.running && !view.single && selectable.length) {
      const all = document.createElement('label');
      selectAll.onchange = () => {
        for (const p of selectable) { if (selectAll.checked) view.selected.add(p.id); else view.selected.delete(p.id); }
        render(view);
      };
      all.append(selectAll, label('span','全选缺正文论文')); actions.prepend(all);
    }
    for (const p of papers.values()) {
      const row = job?.results.get(p.id);
      const line = document.createElement('div'), name = document.createElement('label');
      line.className = 'download-item'; const title = document.createElement('strong'); title.textContent = p.title || p.doi || p.id;
      if (!view.single) {
        const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = view.selected.has(p.id);
        cb.disabled = !!job?.running || complete(row?.outcome);
        cb.onchange = () => { if (cb.checked) view.selected.add(p.id); else view.selected.delete(p.id); selectionChanged(); };
        name.append(cb);
      }
      name.append(title); line.append(name);
      if (row) {
        const info = document.createElement('small'); info.append(label('span', statuses[row.outcome]));
        if (row.detail) info.append(' · ', window.BioI18n.t(row.detail));
        if (row.url) {
          const link = label('a','查看来源'); link.href = row.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
          info.append(' · ',link);
        }
        line.append(info);
      }
      checks.append(line);
    }
    selectionChanged(); root.replaceChildren();
    if (!papers.size) { root.append(label('p','此范围内没有缺正文的论文。')); return; }
    if (!view.single) root.append(label('p','仅列出缺正文的论文。'));
    root.append(actions);
    if (job) {
      const status = label('p','已处理 {done} / {total} 篇',{done:job.done,total:job.total}); status.setAttribute('role','status');
      if (job.stopped) status.append(' · ', label('span','本轮查找已停止')); root.append(status);
    }
    root.append(checks);
  }
  function show(root, papers, scope, key, single = false) {
    const old = currentView;
    currentView = {root,scope,key,single,papers,selected: old?.key === key ? old.selected : new Set(papers.filter(p => !p.has_file).map(p => p.id))};
    render(currentView); return currentView;
  }
  function openSingle(paper) {
    if (!paper || paper.has_file) return;
    hooks.show();
    const view = show(document.getElementById('fulltext-single'), [paper], 'all', 'paper/' + paper.id, true);
    if (!jobs.has(view.key)) void run(view, [paper]);
  }
  function openImported(papers, scope) {
    const missing = papers.filter(p => !p.has_file && !p.schema_incompatible);
    hooks.show();
    return show(document.getElementById('fulltext-single'), missing, scope,
      'import/' + scope + '/' + missing.map(p => p.id).sort().join(','));
  }
  window.BioFulltext = {openImported, init: options => { hooks = options; }, openSingle,
    project: (root, scope, papers) => show(root, papers, scope, scope), busy: () => paperRequests.size > 0};
})();
