/* Choose a destination for a table cell cited in a paper's chat. */
(function (global) {
  'use strict';
  const t = key => global.BioI18n?.t(key) ?? key;
  const el = name => document.getElementById('table-comparison-' + name);
  const base = scope => '/api/comparisons/' + encodeURIComponent(scope);
  let hooks, current, version = 0;
  function options(select, pairs, selected) {
    select.replaceChildren(...pairs.map(([value, label]) => new Option(label, value)));
    if (pairs.some(([value]) => value === selected)) select.value = selected;
  }
  function status(key) { el('status').textContent = t(key); }
  function includesPaper(record,id) {
    return record.paper_ids?.includes(id) || record.rows?.some(row => row.paper_id === id);
  }
  function controls() {
    const s = current;
    if (!s) return;
    const creating = !el('target').value, locked = s.saving || s.done;
    const selected = s.list.find(record => record.id === el('target').value);
    const saveText = selected && !includesPaper(selected,s.paper.id) ? '加入本篇及数值' : '加入比较';
    el('save').dataset.i18n = saveText;
    el('save').textContent = t(saveText);
    el('new').hidden = !creating;
    el('question').required = creating;
    el('peer').required = creating;
    el('peer').disabled = el('question').disabled = !creating;
    el('fields').disabled = !!locked;
    el('close').disabled = !!s.saving;
    el('save').hidden = !!s.done;
    el('save').disabled = !!(locked || s.loading || s.failed || el('target').value === 'unsaved' || (creating && !s.peers.length));
    el('target').disabled = !!(s.loading || s.failed);
    el('view').hidden = !s.done;
    el('reload').hidden = !s.failed;
  }
  async function targets(preferred) {
    const s = current, ticket = ++version, scope = el('scope').value;
    s.loading = true; s.failed = false;
    const members = s.scopes.find(lib => lib.id === scope)?.paper_ids;
    s.peers = hooks.papers().filter(p => !p.schema_incompatible && p.id !== s.paper.id && (!members || members.includes(p.id)));
    options(el('peer'), [['',t('请选择另一篇论文')], ...s.peers.map(p => [p.id,p.title])], el('peer').value);
    options(el('target'), []); controls(); status('正在加载比较…');
    try {
      const list = await hooks.request('GET', base(scope));
      if (current !== s || ticket !== version) return;
      s.list = list;
      const chosen = preferred ?? global.BioCompare.selectedId(scope);
      const choices = [...list.map(item => [item.id,item.question]), ['',t('新建比较')]];
      if (chosen === 'unsaved') choices.unshift(['unsaved',t('请选择保存目标')]);
      options(el('target'), choices, chosen);
      status(chosen === 'unsaved' ? '当前比较草稿尚未保存。请另选目标，或先保存草稿。' : !list.length && !s.peers.length ? '当前课题库只有这一篇论文。请选择其他课题库，或先添加另一篇论文。' : '');
    } catch (err) {
      if (current !== s || ticket !== version) return;
      s.failed = true; status(err.message);
    } finally { if (current === s && ticket === version) { s.loading = false; controls(); } }
  }
  async function open(paper, messageId, source) {
    if (current?.saving) return;
    const s = current = {paper,messageId,source,peers:[],list:[],loading:true};
    ++version; el('form').reset();
    const original = (source.display_value ?? source.value) || t('原表隐藏数值');
    const conditions = (source.conditions || []).map(c => c.name + '=' + c.value);
    el('source').textContent = [paper.title,source.identifier,...conditions,source.column_name + ' = ' + original,source.document_name,source.sheet].filter(Boolean).join(' · ');
    el('label').value = source.column_name;
    s.scopes = global.BioProjects.measurementScopes(paper.id);
    options(el('scope'), s.scopes.map(lib => [lib.id,lib.name]), global.BioProjects.activeId());
    hooks.show('table-comparison-modal', 'table-comparison-scope');
    await targets();
  }
  function close() {
    if (current?.saving) return;
    current = null; version++; hooks.hide('table-comparison-modal');
  }
  async function save(event) {
    event.preventDefault();
    const s = current;
    if (!s || s.loading || s.failed || s.saving || s.done || el('target').value === 'unsaved') return;
    const scope = el('scope').value, fieldLabel = el('label').value.trim();
    if (!fieldLabel) { el('label').focus(); return; }
    const test_group = el('test-group').value.trim(), reference_group = el('reference-group').value.trim(), scale = el('scale').value;
    if (test_group && test_group === reference_group) { status('实验组和参照组应不同。'); el('reference-group').focus(); return; }
    let target = s.list.find(c => c.id === el('target').value);
    if (!target && (!el('question').value.trim() || !s.peers.some(p => p.id === el('peer').value))) return;
    if (target && !global.BioCompare.canAttachTable(scope,target.id)) { status('请先保存或撤销目标比较中尚未保存的编辑。'); return; }
    s.saving = true; controls(); status('正在保存…');
    try {
      if (!target) {
        const question = el('question').value.trim(), peer = el('peer').value;
        const intent = JSON.stringify([scope, question, peer]);
        if (s.intent !== intent) { s.intent = intent; s.newId = global.crypto.randomUUID().replaceAll('-',''); }
        target = await hooks.request('PUT', base(scope) + '/' + s.newId,
          {revision:0,question,rows:[{paper_id:s.paper.id},{paper_id:peer}]});
        s.list.unshift(target); el('target').add(new Option(target.question,target.id),0); el('target').value = target.id;
      }
      s.done = await hooks.request('POST', base(scope) + '/' + target.id + '/table-measurements', {
        revision:target.revision, paper_id:s.paper.id, message_id:s.messageId, source_id:s.source.id,
        field_label:fieldLabel, add_paper:!includesPaper(target,s.paper.id),
        ...(test_group || reference_group || scale !== 'unknown' ? {contrast_definition:{test_group,reference_group,scale}} : {}),
      });
      global.BioCompare.receivedTable(scope,s.done);
      status('数值已加入比较。');
    } catch (err) {
      status(err.message); s.failed = true;
    } finally { s.saving = false; controls(); }
  }
  function init(options) {
    hooks = options;
    el('scope').onchange = () => targets();
    el('target').onchange = () => { status(!el('target').value && !current.peers.length ? '当前课题库只有这一篇论文。请选择其他课题库，或先添加另一篇论文。' : ''); controls(); };
    el('reload').onclick = () => targets(el('target').value);
    el('close').onclick = close;
    el('modal').onclick = e => { if (e.target === e.currentTarget) close(); };
    el('form').onsubmit = save;
    el('view').onclick = () => {
      const scope = el('scope').value, id = current?.done?.id;
      if (id) { close(); void global.BioProjects.openComparison(scope,id); }
    };
  }
  global.BioTableComparison = {open,close,init};
})(window);
