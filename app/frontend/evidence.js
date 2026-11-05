(function () {
  'use strict';
  const t = (key, params = {}) => window.BioI18n?.t(key, params) ?? key.replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
  const message = (key, params = {}) => ({ key, params, toString: () => window.BioResearchI18n?.messageText(key, params) ?? t(key, params) });
  const messageHTML = value => value?.key ? (window.BioResearchI18n?.messageHTML(value.key, value.params) ?? ui(value.key, value.params)) : esc(value);
  const ui = (key, params = {}) => '<span data-i18n="' + esc(key) + '" data-i18n-params="' + esc(JSON.stringify(params)) + '">' + esc(t(key, params)) + '</span>';

  const STATUS = { pending: '待核查', verified: '已核查', rejected: '不支持该判断' };
  const ID = /^[0-9a-f]{32}$/;
  let hooks, panel, paper, figures = [], records = [], draft = null, pendingStart = null;
  let epoch = 0, loading = false, busy = false, error = '';
  const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const isCurrent = (token, id) => token === epoch && paper && paper.id === id;

  function path(id, evidenceId) {
    if (!ID.test(id) || (evidenceId && !ID.test(evidenceId))) throw new Error(t("无效的证据记录"));
    return '/api/papers/' + id + '/evidence' + (evidenceId ? '/' + evidenceId : '');
  }

  function documents() {
    if (!paper) return [];
    return (paper.has_file ? [{ id: 'main', name: t("正文"), pages: paper.pages }] : [])
      .concat((paper.supplements || []).map(s => ({ id: s.id, name: s.filename, pages: s.pages })));
  }

  function sourceHTML(record) {
    return record.document_id === 'main' ? ui('正文') : esc(record.source_name || documents().find(d => d.id === record.document_id)?.name || '') || ui('来源文件已移除');
  }

  function formHTML() {
    const docs = documents(), doc = docs.find(d => d.id === draft.document_id);
    const availableFigures = figures.filter(f => (f.document_id || 'main') === draft.document_id);
    const disabled = busy ? ' disabled' : '';
    return '<form class="evidence-form" id="evidence-form">' +
      '<div class="evidence-form-heading"><strong>' + (draft.id ? ui("编辑证据") : ui("记录一条关键证据")) + '</strong></div>' +
      "<label>" + ui("需要核查的判断") + "<textarea id=\"evidence-claim\" data-evidence-field=\"claim\" required maxlength=\"4000\" rows=\"2\" placeholder=\"例如：干预后转移负荷下降，是否支持该细胞亚群的功能作用？\" data-i18n-placeholder=\"例如：干预后转移负荷下降，是否支持该细胞亚群的功能作用？\"" + disabled + '>' + esc(draft.claim) + '</textarea></label>' +
      "<label>" + ui("原文摘录或图表观察") + "<textarea data-evidence-field=\"quote\" maxlength=\"12000\" rows=\"3\" placeholder=\"粘贴原文，或记录图中实际看到的结果及面板号\" data-i18n-placeholder=\"粘贴原文，或记录图中实际看到的结果及面板号\"" + disabled + '>' + esc(draft.quote) + '</textarea></label>' +
      "<div class=\"evidence-source-fields\"><label>" + ui("来源文件") + "<select data-evidence-field=\"document_id\" required" + disabled + '>' +
      (!doc ? "<option value=\"\" data-i18n=\"请选择来源文件\">请选择来源文件</option>" : '') + docs.map(d => '<option value="' + esc(d.id) + '"' + (d.id === draft.document_id ? ' selected' : '') + (d.id === 'main' ? ' data-i18n="正文"' : '') + '>' + esc(d.name) + '</option>').join('') +
      "</select></label><label>" + ui("PDF 页码") + "<input data-evidence-field=\"page\" type=\"number\" required min=\"1\" max=\"" + esc(doc?.pages || 600) + '" value="' + esc(draft.page) + '"' + disabled + '></label></div>' +
      "<label>" + ui("关联图表（可选）") + "<select data-evidence-field=\"figure_id\"" + disabled + "><option value=\"\" data-i18n=\"仅定位到页面\">仅定位到页面</option>" +
      availableFigures.map(f => '<option value="' + esc(f.id) + '"' + (f.id === draft.figure_id ? ' selected' : '') + ' data-i18n="{label} · 第 {page} 页" data-i18n-params="' + esc(JSON.stringify({ label: f.label || f.id, page: f.page })) + '">' + esc(t('{label} · 第 {page} 页', { label: f.label || f.id, page: f.page })) + '</option>').join('') + '</select></label>' +
      "<p class=\"hint\">" + ui("页码按当前 PDF 文件从 1 开始计数。保存后为“待核查”，查看原文后再标记结果。") + "</p>" +
      (error ? '<p class="evidence-error" role="alert">' + messageHTML(error) + '</p>' : '') +
      '<div class="evidence-form-actions"><button type="submit" class="btn btn-primary"' + disabled + '>' + (busy ? ui("正在保存…") : ui("保存证据")) + '</button><button type="button" class="btn btn-ghost" data-evidence-action="cancel"' + disabled + ">" + ui("取消") + "</button></div></form>";
  }

  function cardHTML(record) {
    const available = record.source_available !== false;
    const status = ((!available || record.source_changed) && record.status === 'verified') ? 'pending' : (STATUS[record.status] ? record.status : 'pending');
    const disabled = busy || draft ? ' disabled' : '';
    return '<article class="evidence-card"><div class="evidence-card-heading"><span class="evidence-status evidence-status-' + status + '">' + ui(STATUS[status]) + '</span>' +
      '<button type="button" class="btn btn-ghost" data-evidence-action="edit" data-evidence-id="' + esc(record.id) + '"' + disabled + ">" + ui("编辑") + "</button></div>" +
      '<p class="evidence-claim">' + esc(record.claim) + '</p>' +
      (record.quote ? '<blockquote>' + esc(record.quote) + '</blockquote>' : '') +
      '<p class="evidence-source">' + sourceHTML(record) + ui(' · PDF 第 {page} 页', { page: record.page }) +
      (record.figure_id ? ' · ' + esc(figures.find(f => f.id === record.figure_id)?.label || record.figure_id) : '') + '</p>' +
      (!available ? "<p class=\"evidence-warning\">" + ui("来源已移除或页码已失效，请编辑并重新选择来源。") + "</p>" : record.source_changed ? "<p class=\"evidence-warning\">" + ui("来源文件已更新，请重新查看原文并核查这条证据。") + "</p>" : '') +
      '<div class="evidence-card-actions"><button type="button" class="btn" data-evidence-action="jump" data-evidence-id="' + esc(record.id) + '"' + (!available || busy ? ' disabled' : '') + ">" + ui("查看原文") + "</button>" +
      "<label class=\"evidence-verdict-label\"><span class=\"sr-only\">" + ui("核查结果") + "</span><select aria-label=\"核查结果\" data-i18n-aria-label=\"核查结果\" data-evidence-status=\"" + esc(record.id) + '"' + disabled + '>' +
      Object.entries(STATUS).map(([key, label]) => '<option value="' + key + '"' + (key === status ? ' selected' : '') + (key === 'verified' && !available ? ' disabled' : '') + ' data-i18n="' + esc(label) + '">' + esc(t(label)) + '</option>').join('') + '</select></label>' +
      '<button type="button" class="btn btn-ghost danger-ghost" data-evidence-action="delete" data-evidence-id="' + esc(record.id) + '"' + disabled + ">" + ui("删除") + "</button></div></article>";
  }

  function render() {
    if (!panel) return;
    if (!paper) { panel.innerHTML = "<p class=\"evidence-empty\">" + ui("选择一篇文献，记录关键判断与原文依据。") + "</p>"; return; }
    panel.innerHTML = "<div class=\"evidence-toolbar\"><div><strong>" + ui("关键证据") + "</strong><p class=\"hint\">" + ui("记录判断、定位来源，留下自己的核查结果。") + "</p></div>" +
      '<button type="button" class="btn btn-primary" data-evidence-action="add"' + (busy || loading || !documents().length ? ' disabled' : '') + ">" + ui("添加证据") + "</button></div>" +
      (!documents().length ? "<p class=\"evidence-warning\">" + ui("请先添加 PDF，再记录可定位的证据。") + "</p>" : '') +
      (draft ? formHTML() : '') +
      (!draft && error ? '<p class="evidence-error" role="alert">' + messageHTML(error) + " <button type=\"button\" class=\"btn btn-ghost\" data-evidence-action=\"reload\">" + ui("重新加载") + "</button></p>" : '') +
      (loading ? "<p class=\"evidence-empty\" role=\"status\">" + ui("正在读取证据…") + "</p>" : records.length ? '<div class="evidence-list">' + records.map(cardHTML).join('') + '</div>' : "<p class=\"evidence-empty\">" + ui("还没有证据记录。把报告中的关键结论与对应原文或图表关联起来。") + "</p>");
  }

  async function open(nextPaper, nextFigures = []) {
    if (!nextPaper || !ID.test(nextPaper.id)) { clear(); return; }
    const same = paper && paper.id === nextPaper.id;
    // A view refresh must not discard typed evidence or race a pending write.
    if (same && (draft || busy)) { paper = nextPaper; figures = nextFigures; return; }
    if (!same) pendingStart = null;
    const token = ++epoch, id = nextPaper.id;
    paper = nextPaper; figures = nextFigures; records = []; draft = null; error = ''; busy = false; loading = true; render();
    try {
      const result = await hooks.request('GET', path(id));
      if (!isCurrent(token, id)) return;
      records = Array.isArray(result) ? result : result.evidence || [];
    } catch (err) { if (isCurrent(token, id)) error = message("证据读取失败：" + "{detail}", { detail: err.message }); }
    finally {
      if (isCurrent(token, id)) {
        loading = false; render();
        if (pendingStart) { const initial = pendingStart; pendingStart = null; start(initial); }
      }
    }
  }

  function clear() {
    ++epoch; paper = null; figures = []; records = []; draft = null; pendingStart = null; loading = false; busy = false; error = ''; render();
  }

  function start(initial = {}) {
    if (!paper || busy || !documents().length) return;
    if (loading) { pendingStart = initial; return; }
    if (draft && (draft.claim.trim() || draft.quote.trim())) { hooks.toast?.(t("请先保存或取消正在编辑的证据。"), 'error'); return; }
    const doc = documents().find(d => d.id === initial.document_id) || documents()[0];
    const figure = figures.find(f => f.id === initial.figure_id && (f.document_id || 'main') === doc.id);
    draft = { id: initial.id || '', claim: initial.claim || '', quote: initial.quote || '', document_id: doc.id, page: initial.page || 1, figure_id: figure ? figure.id : '' };
    error = ''; render(); panel.querySelector('#evidence-claim')?.focus();
  }

  function notifyChanged(id) {
    Promise.resolve().then(() => hooks.onChanged?.(id)).catch(() => hooks.toast?.(t("证据已保存，文献列表刷新失败，请稍后刷新。"), 'error'));
  }

  async function mutate(method, evidenceId, body) {
    if (!paper || busy || loading) return false;
    const id = paper.id, token = epoch;
    busy = true; error = ''; render();
    try {
      const result = await hooks.request(method, path(id, evidenceId), body);
      notifyChanged(id);
      if (!isCurrent(token, id)) return true;
      if (method === 'DELETE') records = records.filter(r => r.id !== evidenceId);
      else if (evidenceId) records = records.map(r => r.id === evidenceId ? result : r);
      else records.unshift(result);
      if (draft && (method === 'POST' || draft.id === evidenceId)) draft = null;
      hooks.toast?.(method === 'DELETE' ? t("证据已删除") : t("证据已保存"));
      return true;
    } catch (err) {
      if (isCurrent(token, id)) { error = message("保存失败：" + "{detail}", { detail: err.message }); hooks.toast?.(error, 'error'); }
      return false;
    } finally { if (isCurrent(token, id)) { busy = false; render(); } }
  }

  async function submit(event) {
    if (event.target.id !== 'evidence-form') return;
    event.preventDefault();
    if (!draft || busy) return;
    const doc = documents().find(d => d.id === draft.document_id), page = Number(draft.page);
    if (!draft.claim.trim() || !doc || !Number.isInteger(page) || page < 1 || page > doc.pages) {
      error = message("请填写判断，并选择有效的来源文件和 PDF 页码。"); render(); return;
    }
    const figure = figures.find(f => f.id === draft.figure_id);
    if (draft.figure_id && (!figure || (figure.document_id || 'main') !== doc.id || figure.page !== page)) {
      error = message("关联图表与文件或页码不一致，请重新选择图表。"); render(); return;
    }
    await mutate(draft.id ? 'PATCH' : 'POST', draft.id, {
      claim: draft.claim.trim(), quote: draft.quote.trim(), document_id: doc.id, page,
      figure_id: draft.figure_id, status: 'pending',
    });
  }

  function input(event) {
    const key = event.target.dataset.evidenceField;
    if (!draft || busy || !['claim', 'quote', 'document_id', 'page', 'figure_id'].includes(key)) return;
    draft[key] = event.target.value;
  }

  async function change(event) {
    const target = event.target;
    if (target.dataset.evidenceStatus && !busy && !draft) {
      const record = records.find(r => r.id === target.dataset.evidenceStatus);
      if (!record || !STATUS[target.value] || (target.value === 'verified' && record.source_available === false)) { render(); return; }
      await mutate('PATCH', record.id, { status: target.value }); return;
    }
    input(event);
    if (!draft || busy) return;
    if (target.dataset.evidenceField === 'document_id') {
      draft.page = 1; draft.figure_id = ''; render();
    } else if (target.dataset.evidenceField === 'figure_id') {
      const figure = figures.find(f => f.id === draft.figure_id);
      if (figure) draft.page = figure.page;
      render();
    }
  }

  async function click(event) {
    const button = event.target.closest('[data-evidence-action]');
    if (!button || button.disabled || busy) return;
    const action = button.dataset.evidenceAction;
    if (draft && (action === 'delete' || action === 'edit')) return;
    if (action === 'add') { start(); return; }
    if (action === 'cancel') { draft = null; error = ''; render(); return; }
    if (action === 'reload') { await open(paper, figures); return; }
    const record = records.find(r => r.id === button.dataset.evidenceId);
    if (!record) return;
    if (action === 'edit') { start(record); return; }
    if (action === 'delete') {
      if (window.confirm(t("删除这条证据记录？原文与笔记不会删除。"))) await mutate('DELETE', record.id);
    } else if (action === 'jump' && record.source_available !== false) {
      try { await hooks.jump(record.document_id, record.page); }
      catch (err) { hooks.toast?.(t("无法定位原文：") + err.message, 'error'); }
    }
  }

  function init(options) {
    hooks = options;
    const nextPanel = document.getElementById('evidence-panel');
    if (!nextPanel || panel === nextPanel) return;
    panel = nextPanel;
    panel.addEventListener('click', click);
    panel.addEventListener('input', input);
    panel.addEventListener('change', change);
    panel.addEventListener('submit', submit);
    render();
  }

  function canLeave() {
    if (busy) { hooks.toast?.(t("正在保存证据，请稍后再切换。"), 'error'); return false; }
    const value = draft || pendingStart;
    if (value && ((value.claim || '').trim() || (value.quote || '').trim())) {
      hooks.toast?.(t("证据尚未保存，请先在「证据」页保存或取消编辑。"), 'error'); return false;
    }
    return true;
  }

  window.BioEvidence = { init, open, clear, start, canLeave };
})();
