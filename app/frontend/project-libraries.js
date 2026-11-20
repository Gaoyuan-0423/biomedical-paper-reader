(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const t = key => window.BioI18n?.t(key) ?? key;
  window.BioI18n?.register({
    '来源已变化，请重新打开比较。': 'The source changed. Reopen the comparison.',
    '库名称': 'Library name', '课题说明': 'Research topic', '选择库内论文': 'Choose papers',
    '按题名、作者或 DOI 查找': 'Find by title, author or DOI', '保存课题库': 'Save library',
    '归档课题库': 'Archive library', '恢复课题库': 'Restore library', '打开课题库': 'Open library',
    '放弃尚未保存的课题库更改？': 'Discard unsaved library edits?', '已归档的课题库': 'Archived libraries',
    '新建课题库后，可把已有论文加入一个或多个课题。': 'Create a project library to group papers. A paper can belong to several libraries.',
    '归档后可在全部文献中恢复该课题库。继续归档？': 'Archive this library? You can restore it from All papers.',
    '课题库已被修改，请重新打开后保存': 'The library has changed. Reopen it before saving.',
    '课题库已归档': 'This library is archived.', '未找到课题库': 'Library not found.',
    '课题库记录损坏或版本不兼容，请检查本地文件': 'Unreadable or incompatible project library. Check your local files.',
    '论文已导入全部文献，但未加入课题库': 'Paper imported into All papers. Adding it to the project library failed.',
    '正在读取课题库…': 'Loading libraries…', '库中暂时没有论文': 'This library has no papers yet',
    '可导入 PDF，或在「课题库」中添加已有论文。': 'Import a PDF or add existing papers in Projects.',
    '请先保存跨篇比较，或撤销修改。': 'Save or discard the comparison before closing.',
    '请先保存或取消课题库更改。': 'Save or cancel the library edits before closing.',
    '没有匹配的论文': 'No matching papers', '部分课题库记录无法读取，请检查本地文件。': 'Some library records could not be read. Check the local files.',
    '在当前课题库的正文、补充 PDF 和已保存笔记中检索。': 'Search full text, supplementary PDFs and saved notes in the selected library.',
  });

  let hooks, libraries = [], active = 'all', section = 'papers', draft = null, busy = false, visible = false;
  let refreshSequence = 0, opening = 0, unreadableLibraries = 0;
  const current = () => libraries.find(item => item.id === active);
  function scoped(papers) {
    if (active === 'all') return papers;
    const members = new Set(current()?.paper_ids || []);
    return papers.filter(paper => members.has(paper.id));
  }
  function node(tag, value) {
    const el = document.createElement(tag);
    if (value !== undefined) el.textContent = value;
    return el;
  }
  function label(tag, key) {
    const el = node(tag, t(key)); el.dataset.i18n = key; return el;
  }
  function button(key, fn, primary = false) {
    const el = label('button', key); el.type = 'button'; el.className = primary ? 'btn btn-primary' : 'btn';
    el.addEventListener('click', fn); return el;
  }
  function status(message = '') {
    const el = $('project-status');
    el.textContent = message || (unreadableLibraries ? t('部分课题库记录无法读取，请检查本地文件。') : '');
    el.hidden = !el.textContent;
  }
  function setActive(id) {
    active = id;
    try { localStorage.setItem('bioreader.project-library', id); } catch (_) {}
    updateSwitches();
    hooks.scopeChanged();
  }
  function updateSwitches() {
    for (const select of [$('project-scope'), $('project-switch')]) {
      const first = label('option', '全部文献');
      first.value = 'all';
      const options = libraries.filter(lib => !lib.archived).map(lib => {
        const option = node('option', lib.name); option.value = lib.id; return option;
      });
      select.replaceChildren(first, ...options); select.value = active;
    }
  }
  function keep(record) {
    const old = libraries.find(item => item.id === record.id);
    if (old && old.revision > record.revision) return;
    libraries = libraries.filter(item => item.id !== record.id).concat(record);
    libraries.sort((a, b) => a.name.localeCompare(b.name));
    refreshSequence++;
  }
  async function refresh(initial = false) {
    const token = ++refreshSequence;
    const result = await hooks.request('GET', '/api/project-libraries');
    if (token !== refreshSequence) return;
    libraries = result.libraries; unreadableLibraries = result.unavailable;
    let wanted = active;
    if (initial) {
      try { wanted = localStorage.getItem('bioreader.project-library') || 'all'; } catch (_) {}
    }
    const next = libraries.some(item => item.id === wanted && !item.archived) ? wanted : 'all';
    if (active !== next) setActive(next);
    else { updateSwitches(); hooks.updateList(); }
  }
  function canNavigate() {
    if (busy || (section === 'compare' && window.BioCompare?.canLeave?.() === false)) return false;
    if (draft?.dirty && !window.confirm(t('放弃尚未保存的课题库更改？'))) return false;
    return true;
  }
  function canExit() {
    if (busy || draft?.dirty) { hooks.toast(t('请先保存或取消课题库更改。'), 'error'); return false; }
    if (window.BioCompare?.hasUnsaved?.() || window.BioCompare?.canLeave?.() === false) {
      hooks.toast(t('请先保存跨篇比较，或撤销修改。'), 'error'); return false;
    }
    return true;
  }
  function hasUnsaved() { return busy || !!draft?.dirty || !!window.BioCompare?.hasUnsaved?.(); }

  function switchScope(id) {
    if (id !== 'all' && !libraries.some(item => item.id === id && !item.archived)) return;
    if (!canNavigate()) { updateSwitches(); return; }
    draft = null; setActive(id);
    if (visible) renderSection();
  }
  async function open() {
    if (visible) return;
    const ticket = ++opening;
    visible = true; draft = null;
    hooks.showModal('project-modal', 'project-switch');
    const body = $('project-papers').parentElement;
    body.hidden = true;
    for (const el of $('project-modal').querySelectorAll('.project-controls, .project-sections')) el.inert = true;
    status(t('正在读取课题库…'));
    let error;
    try { await refresh(); } catch (err) { error = err; }
    if (!visible || ticket !== opening) return;
    for (const el of $('project-modal').querySelectorAll('.project-controls, .project-sections')) el.inert = false;
    body.hidden = false;
    renderSection();
    if (error) status(t(error.message));
  }
  function close() {
    if (!visible || !canNavigate()) return false;
    draft = null; visible = false; hooks.hideModal('project-modal'); window.BioEvidence?.refreshComparison?.(); return true;
  }
  function activePapers() { return scoped(hooks.papers()).filter(p => !p.schema_incompatible); }
  function renderSection() {
    status();
    for (const el of document.querySelectorAll('[data-project-tab]')) {
      const on = el.dataset.projectTab === section;
      el.setAttribute('aria-pressed', String(on));
      $('project-' + el.dataset.projectTab).hidden = !on;
    }
    if (section === 'papers') { renderPapers(); return; }
    const papers = activePapers();
    if (section === 'pubmed') {
      window.BioPubMed.open($('project-pubmed'), active, hooks.request, pmids => {
        if (close()) window.BioIdentifierImport.open(pmids.join('\n'), active);
      }); return;
    }
    if (section === 'downloads') { window.BioFulltext.project($('project-downloads'), active, papers); return; }
    if (section === 'compare') {
      window.BioCompare.open($('project-compare'), { libraryId: active, papers, openSource: async (...source) => {
        if (await hooks.openSource(...source)) close();
      } });
    } else {
      window.BioFigureShelf.open($('project-figures'), papers.map(p => p.id), async (id, figure) => {
        if (await window.BioFigureShelf.jump(id, figure) !== false) close();
      }, active);
    }
  }
  async function archive(record, archived) {
    if (archived && window.BioCompare?.hasUnsaved?.(record.id)) {
      status(t('请先保存跨篇比较，或撤销修改。')); return;
    }
    if (busy || (archived && !window.confirm(t('归档后可在全部文献中恢复该课题库。继续归档？')))) return;
    if (!canNavigate()) return;
    busy = true;
    try {
      const result = await hooks.request('PUT', '/api/project-libraries/' + record.id, {
        revision: record.revision, name: record.name, topic: record.topic, archived,
      });
      keep(result); draft = null; setActive(archived ? 'all' : record.id); renderSection();
    } catch (err) { status(t(err.message)); }
    finally { busy = false; }
  }
  function libraryRow(lib) {
    const row = node('div'); row.className = 'project-library-row';
    const info = node('div'); info.append(node('strong', lib.name));
    if (lib.topic) info.append(node('p', lib.topic));
    row.append(info, lib.archived ? button('恢复课题库', () => archive(lib, false)) :
      button('打开课题库', () => switchScope(lib.id)));
    return row;
  }
  function renderPapers() {
    const root = $('project-papers');
    if (current() && !draft) beginDraft(current());
    if (draft) { renderForm(root); return; }
    const archived = libraries.filter(lib => lib.archived);
    const openLibraries = libraries.filter(lib => !lib.archived);
    root.replaceChildren(...openLibraries.map(libraryRow));
    if (!openLibraries.length) root.append(label('p', '新建课题库后，可把已有论文加入一个或多个课题。'));
    if (archived.length) {
      const details = node('details'); details.append(label('summary', '已归档的课题库'), ...archived.map(libraryRow));
      root.append(details);
    }
  }
  function beginDraft(record) {
    draft = { record, requestId: record ? null : crypto.randomUUID().replaceAll('-', ''), name: record?.name || '', topic: record?.topic || '',
      memberIds: new Set(record?.paper_ids || []), query: '', dirty: false };
  }
  function formField(form, key, el) {
    const field = node('label'); field.className = 'project-field';
    field.append(label('span', key), el); form.append(field);
  }
  function renderForm(root) {
    const form = node('form'); form.className = 'project-form';
    if (!draft.record) form.append(label('h4', '新建课题库'));
    const name = node('input'); name.type = 'text'; name.required = true; name.maxLength = 100; name.value = draft.name;
    formField(form, '库名称', name);
    const topic = node('textarea'); topic.rows = 2; topic.maxLength = 2000; topic.value = draft.topic;
    formField(form, '课题说明', topic);
    const search = node('input'); search.type = 'search'; search.placeholder = t('按题名、作者或 DOI 查找');
    search.dataset.i18nPlaceholder = '按题名、作者或 DOI 查找'; search.value = draft.query;
    formField(form, '选择库内论文', search);
    const list = node('div'); list.className = 'project-checklist'; form.append(list);
    function renderChecks() {
      const query = search.value.toLocaleLowerCase().trim();
      const papers = hooks.papers().filter(p => !p.schema_incompatible &&
        [p.title, p.authors, p.doi].join(' ').toLocaleLowerCase().includes(query));
      list.replaceChildren(...papers.map(p => {
        const row = node('label'), box = node('input'); box.type = 'checkbox'; box.checked = draft.memberIds.has(p.id);
        box.addEventListener('change', () => {
          if (box.checked) draft.memberIds.add(p.id); else draft.memberIds.delete(p.id);
          draft.dirty = true;
        });
        row.append(box, node('span', p.title || p.filename || p.id)); return row;
      }));
      if (!papers.length) list.append(label('p', '没有匹配的论文'));
    }
    renderChecks();
    search.oninput = () => { draft.query = search.value; renderChecks(); };
    name.oninput = () => { draft.name = name.value; draft.dirty = true; };
    topic.oninput = () => { draft.topic = topic.value; draft.dirty = true; };
    const actions = node('div'); actions.className = 'project-form-actions';
    const save = label('button', '保存课题库'); save.type = 'submit'; save.className = 'btn btn-primary';
    actions.append(save, button('取消', async () => {
      if (!canNavigate()) return;
      busy = true;
      for (const control of form.elements) control.disabled = true;
      let error;
      try { await refresh(); } catch (err) { error = err; }
      finally { busy = false; }
      draft = null; section = 'papers'; renderSection();
      if (error) status(t(error.message));
    }));
    if (draft.record) actions.append(button('归档课题库', () => archive(draft.record, true)));
    form.append(actions); root.replaceChildren(form);
    form.onsubmit = async event => {
      event.preventDefault();
      const edit = draft, title = name.value.trim();
      if (!title || busy) { if (!title) name.focus(); return; }
      busy = true;
      for (const control of form.elements) control.disabled = true;
      const body = { name: title, topic: topic.value.trim() };
      try {
        let result;
        if (edit.record) {
          const previous = new Set(edit.record.paper_ids);
          body.add = [...edit.memberIds].filter(pid => !previous.has(pid));
          body.remove = [...previous].filter(pid => !edit.memberIds.has(pid));
          body.revision = edit.record.revision;
          result = await hooks.request('PUT', '/api/project-libraries/' + edit.record.id, body);
        } else {
          body.paper_ids = [...edit.memberIds];
          result = await hooks.request('POST', '/api/project-libraries?request_id=' + edit.requestId, body);
        }
        keep(result); draft = null; setActive(result.id); renderSection(); status(t('已保存'));
      } catch (err) { status(t(err.message)); }
      finally { busy = false; for (const control of form.elements) control.disabled = false; }
    };
  }
  async function attachImported(scopeId, paperId) {
    if (!scopeId || scopeId === 'all') return '';
    try {
      const result = await hooks.request('PUT', '/api/project-libraries/' + scopeId + '/papers/' + paperId);
      keep(result); updateSwitches(); hooks.updateList();
      return '';
    } catch (err) { return t('论文已导入全部文献，但未加入课题库') + ' · ' + t(err.message); }
  }
  function init(options) {
    hooks = options;
    $('open-projects').onclick = open; $('project-close').onclick = close;
    $('project-modal').onclick = e => { if (e.target === e.currentTarget) close(); };
    for (const el of [$('project-scope'), $('project-switch')]) el.onchange = () => switchScope(el.value);
    $('project-new').onclick = () => {
      if (!canNavigate()) return;
      section = 'papers'; beginDraft(null); renderSection(); $('project-papers').querySelector('input')?.focus();
    };
    for (const el of document.querySelectorAll('[data-project-tab]')) el.onclick = () => {
      if (el.dataset.projectTab === section || !canNavigate()) return;
      draft = null; section = el.dataset.projectTab; renderSection();
    };
    return refresh(true).catch(err => hooks.toast(t(err.message), 'error'));
  }
  window.BioProjects = { init, open, close, activeId: () => active, scoped, attachImported, canExit, hasUnsaved, refresh };
})();
