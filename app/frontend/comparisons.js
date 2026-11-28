/* Compare a selected set of papers against one research question. The saved judgments belong to the reader. */
(function (global) {
  'use strict';
  const words = {
    '加入当前比较':'Add to current comparison', '已加入当前比较，保存比较后生效。':'Added to the current comparison. Save the comparison to keep it.',
    '当前比较或所选课题已变更。':'The selected comparison or library has changed.', '字段出处':'Field excerpt', '已定位':'Located on cited page', '已保存的比较': 'Saved comparisons', '新建比较': 'New comparison', '研究问题': 'Research question',
    '参与比较的论文': 'Papers to compare', '选择至少两篇论文': 'Select at least two papers',
    '论文与数据编号': 'Paper and data accessions', '原文与图表依据': 'Sources and figures',
    '此论文对问题的证据': 'How this paper bears on the question', '未判断': 'Unassessed',
    '支持': 'Supports', '部分支持或需限定': 'Supports with qualifications', '相反证据': 'Contrary evidence',
    '未报告相关结果': 'Relevant result not reported', '你的判断依据': 'Reason for your judgment',
    '跨论文结论与分歧': 'Synthesis and disagreements', '保存比较': 'Save comparison', '另存为新比较': 'Save as new comparison',
    '保留修改并另存为新比较，或重新载入已保存版本。': 'Keep your changes as a new comparison, or reload the saved version.',
    '尚未保存': 'Unsaved changes', '比较已保存': 'Comparison saved', '刷新材料': 'Refresh materials',
    '读取材料中…': 'Loading materials…', '论文已移出本库或不可用，保留先前记录。': 'Paper is outside this library or unavailable. Earlier records are retained.',
    '选取证据卡、图表或字段出处': 'Select an evidence card, figure or field excerpt', '添加依据': 'Add source', '添加原文页': 'Add PDF page',
    '原文文件': 'Source PDF', 'PDF 页码': 'PDF page', '页码超出所选文件范围。': 'Page is outside this PDF.',
    '证据卡': 'Evidence card', '图表': 'Figure', '原文页': 'PDF page', '已核查': 'Verified', '待核查': 'Pending',
    '不支持该判断': 'Rejected', '移除此依据': 'Remove source', '查看原文': 'Open source',
    '来源或记录已变化，保留保存时的内容。': 'The source or record changed. Showing the saved content.',
    '所选论文没有可引用的 PDF 或证据。': 'No citable PDF or evidence is available for this paper.',
    '本次比较中有多篇论文记录了相同数据编号，请核对样本是否重叠。': 'Some papers list the same accession. Check whether their samples overlap.',
    '选择比较记录': 'Choose a saved comparison', '换到其他比较会丢弃当前未保存的修改，继续吗？': 'Discard unsaved changes and switch comparisons?',
    '重新载入已保存版本': 'Reload saved version', '重新载入': 'Reload', '请先填写研究问题并选择至少两篇不同的论文。': 'Enter a question and select at least two different papers.',
    '正在保存…': 'Saving…', '数据编号': 'Accessions', '请先完成或取消当前比较的保存。': 'Wait until the comparison is saved.',
    '该论文有未保留到其他位置的判断和依据，确定从本次比较移除吗？': 'Remove this paper and its judgments and sources from the comparison?',
    '请求失败': 'Request failed', '证据核查': 'Evidence review', '请检查问题、所选文献和文本长度。': 'Check the question, paper selection and text lengths.',
    '手工对照': 'Manual comparison', '本库没有可选的论文。': 'There are no papers available in this library.',
    '未添加': 'None added', '添加': 'Add', '比较记录已在其他窗口更新，请重新打开后合并修改。': 'This comparison was changed in another window. Reopen it to merge your changes.',
    '所选证据或原文已变更，请刷新材料后重新选择。': 'The evidence or source has changed. Refresh the materials and select the source again.',
    'PDF 内容与所选来源版本不符。': 'The PDF no longer matches the cited version.',
    '研究对象': 'Study population', '关键对照': 'Key comparison or control',
    '资源表中的样本记录': 'Sample information from resource records', '物种': 'Organism', '组织': 'Tissue',
    '分组': 'Groups', '样本量': 'Sample size', '证据强度与适用范围': 'Strength of evidence and limits',
    '实验组和参照组应不同。':'Use different test and reference groups.', '同列含相反的对比方向':'Opposite contrasts in this column',
    '同列含不同的指标尺度':'Different scales in this column', '统一查看方向':'View in a common direction',
    '仅显示原值':'Original values only', '相对于':'relative to', '统一方向值':'Value in selected direction',
    '分组待核对':'Groups unverified', '尺度待核对':'Scale unverified', '分组与所选方向不匹配':'Groups differ from the selected contrast',
    '此尺度只显示原值':'Original value only for this scale', '此值无法换算':'Cannot convert this value',
    '核对分组与尺度':'Review groups and scale', '应用分组与尺度':'Apply groups and scale', '取消核对':'Cancel review',
    '原表隐藏数值':'Value hidden in source table',
  };
  global.BioI18n?.register(words);
  const t = key => global.BioI18n?.t(key) ?? key;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[ch]));
  const ui = key => '<span data-i18n="' + escape(key) + '">' + escape(t(key)) + '</span>';
  const field = key => ' aria-label="' + escape(t(key)) + '" data-i18n-aria-label="' + escape(key) + '"';
  const option = (value, label) => '<option value="' + escape(value) + '">' + escape(label) + '</option>';
  const translatedOption = (value, label, selected = false) => '<option value="' + escape(value) + '" data-i18n="' + escape(label) + '"' + (selected ? ' selected' : '') + '>' + escape(t(label)) + '</option>';
  const relations = { unassessed:'未判断', supports:'支持', qualifies:'部分支持或需限定', contradicts:'相反证据', unreported:'未报告相关结果' };
  const kinds = { field:'字段出处', page:'原文页', figure:'图表', evidence:'证据卡' };
  const statuses = { located:'已定位', unavailable:'来源不可用', verified:'已核查', pending:'待核查', rejected:'不支持该判断' };
  const states = new Map();
  let active;
  const uid = () => Array.from(global.crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
  const fresh = () => ({id:uid(), revision:0, question:'', synthesis:'', rows:[]});
  const base = s => '/api/comparisons/' + encodeURIComponent(s.libraryId);
  async function request(url, data, method = 'PUT') {
    const response = await global.fetch(url, data ? {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)} : {});
    const result = await response.json();
    if (!response.ok) {
      const err = new Error(typeof result.detail === 'string' ? t(result.detail) : t('请检查问题、所选文献和文本长度。'));
      err.status = response.status; throw err;
    }
    return result;
  }
  const isActive = s => active === s && s.root?.dataset.comparisonScope === s.libraryId;
  const findPaper = (s, id) => s.papers.find(p => p.id === id);
  const newRow = p => ({paper_id:p.id, title:p.title, population:'', contrast:'', relation:'unassessed', note:'', sources:[],cells:[]});
  const findRow = (s, id) => s.draft.rows.find(r => r.paper_id === id);
  function status(s, message, error = false) {
    if (!isActive(s)) return;
    const el = s.root.querySelector('[data-cmp-status]');
    if (el) { el.textContent = message; el.classList.toggle('cmp-error', error); }
  }
  function dirty(s) {
    s.dirty = true;
    status(s, t('尚未保存'));
  }
  function isAvailable(src, m) {
    if (!m) return false;
    if (src.kind === 'pdf') return m.documents.some(d => d.id === src.document_id && src.page <= d.pages && d.revision === src.source_revision);
    if (src.kind === 'page') return m.documents.some(d => d.id === src.document_id && src.page <= d.pages &&
      src.key === 'page:' + d.id + ':' + src.page + ':' + d.revision);
    return m.sources.some(item => item.key === src.key && item.available);
  }
  const drafts = global.BioComparisonDraft.create({escape,ui,t,request,base,status,dirty,render,jump,isAvailable});
  const locked = s => s.busy || s.drafting;
  function pageRef(doc, page) {
    return {key:'page:' + doc.id + ':' + page + ':' + doc.revision, kind:'page', document_id:doc.id,
      document_name:doc.name, page, figure_id:'', label:'', text:'', status:''};
  }
  function sourceCard(src, m, index) {
    const valid = isAvailable(src, m), location = [src.document_name, 'p. ' + src.page, src.figure_label].filter(Boolean).join(' · ');
    return '<div class="cmp-ref"><div>' + ui(kinds[src.kind]) + (src.status ? ' · ' + ui(statuses[src.status]) : '') +
      (src.label ? ' · <strong>' + (src.kind === 'field' ? ui(src.label) : escape(src.label)) + '</strong>' : '') + '</div>' +
      (src.text ? '<blockquote>' + escape(src.text) + '</blockquote>' : '') +
      '<div class="cmp-ref-actions"><button type="button" class="btn btn-ghost" data-jump="' + index + '"' +
      (valid ? '' : ' disabled') + field('查看原文') + '>' + escape(location) + '</button>' +
      '<button type="button" class="btn btn-ghost" data-drop="' + index + '">' + ui('移除此依据') + '</button></div>' +
      (m && !valid ? '<small class="cmp-warning">' + ui('来源或记录已变化，保留保存时的内容。') + '</small>' : '') + '</div>';
  }
  function evidence(s, row) {
    const id = row.paper_id, paper = findPaper(s, id), m = s.materials.get(id);
    const cards = row.sources.map((src, i) => sourceCard(src, m, i)).join('');
    if (!paper) return cards + '<p class="cmp-warning">' + ui('论文已移出本库或不可用，保留先前记录。') + '</p>';
    const retry = '<button type="button" class="btn btn-ghost" data-retry>' + ui('刷新材料') + '</button>';
    if (s.errors.has(id)) return cards + '<p class="cmp-error">' + escape(s.errors.get(id)) + '</p>' + retry;
    if (!m) return cards + '<p class="hint">' + ui('读取材料中…') + '</p>';
    const candidates = m.sources.filter(src => src.available && !row.sources.some(item => item.key === src.key));
    const picker = candidates.length ? '<div class="cmp-line"><select data-candidate' + field('选取证据卡、图表或字段出处') + '>' +
      candidates.map(src => option(src.key, t(kinds[src.kind]) + ' · ' + (src.kind === 'field' ? t(src.label) : src.label) + ' · p. ' + src.page)).join('') +
      '</select><button type="button" class="btn" data-attach>' + ui('添加依据') + '</button></div>' : '';
    const pages = m.documents.length ? '<div class="cmp-line"><select data-document' + field('原文文件') + '>' +
      m.documents.map(d => option(d.id, d.name)).join('') + '</select><input data-page type="number" min="1" max="600" value="1"' +
      field('PDF 页码') + '><button type="button" class="btn" data-attach-page>' + ui('添加原文页') + '</button></div>' : '';
    return cards + picker + pages + (!cards && !picker && !pages ? '<p class="hint">' + ui('所选论文没有可引用的 PDF 或证据。') + '</p>' : '') +
      m.warnings.map(w => '<p class="cmp-warning">' + escape(w) + '</p>').join('');
  }
  function rowHTML(s, row) {
    const m = s.materials.get(row.paper_id);
    return '<tr data-row="' + escape(row.paper_id) + '"><th scope="row"><strong>' + escape(findPaper(s, row.paper_id)?.title || row.title) +
      '</strong><span class="cmp-accessions">' + escape(m?.accessions.join(' · ') || '') + '</span></th><td class="cmp-design">' +
      '<label>' + ui('研究对象') + '<textarea maxlength="4000" rows="2" data-population>' + escape(row.population) + '</textarea></label>' +
      '<label>' + ui('关键对照') + '<textarea maxlength="4000" rows="2" data-contrast>' + escape(row.contrast) + '</textarea></label>' +
      '<div data-samples>' + sampleHTML(m) + '</div></td><td data-material>' + evidence(s, row) +
      '</td><td><select data-relation' + field('此论文对问题的证据') + '>' + Object.entries(relations).map(([v,l]) => translatedOption(v,l,row.relation === v)).join('') +
      '</select><textarea rows="3" maxlength="16000" data-row-note' + field('证据强度与适用范围') + ' placeholder="' + escape(t('证据强度与适用范围')) +
      '" data-i18n-placeholder="证据强度与适用范围">' + escape(row.note) + '</textarea></td></tr>';
  }
  function sampleHTML(m) {
    if (!m?.samples?.length) return '';
    const labels = {organism:'物种', tissue:'组织', sample_groups:'分组', sample_count:'样本量'};
    return '<details class="cmp-samples"><summary>' + ui('资源表中的样本记录') + '</summary>' +
      m.samples.map(item => '<p><strong>' + escape([item.title,item.identifiers].filter(Boolean).join(' · ')) + '</strong><br>' +
        Object.entries(labels).filter(([k]) => item[k]).map(([k,l]) => ui(l) + ' · ' + escape(item[k])).join('<br>') + '</p>').join('') + '</details>';
  }
  function overlap(s) {
    const counts = new Map();
    for (const row of s.draft.rows) for (const acc of s.materials.get(row.paper_id)?.accessions || [])
      counts.set(acc, (counts.get(acc) || 0) + 1);
    return [...counts].filter(([, n]) => n > 1).map(([acc]) => acc).sort();
  }
  function refreshOverlap(s) {
    if (!isActive(s)) return;
    const el = s.root.querySelector('[data-overlap]'), common = overlap(s);
    if (el) { el.hidden = !common.length; el.innerHTML = common.length ? ui('本次比较中有多篇论文记录了相同数据编号，请核对样本是否重叠。') + ' ' + escape(common.join(' · ')) : ''; }
  }
  async function loadMaterial(s, id) {
    if (!findPaper(s,id)) return;
    const token = {}, epoch = s.epoch;
    s.loads.set(id, token);
    try {
      const result = await request(base(s) + '/material/' + id);
      if (epoch !== s.epoch || s.loads.get(id) !== token) return;
      s.materials.set(id, result); s.errors.delete(id);
    } catch (err) {
      if (epoch !== s.epoch || s.loads.get(id) !== token) return;
      s.materials.delete(id); s.errors.set(id, err.message);
    }
    refreshMaterial(s,id);
  }
  function refreshMaterial(s,id) {
    if (!isActive(s)) return;
    const row = findRow(s,id), tr = s.root.querySelector('[data-row="' + id + '"]');
    if (row && tr) {
      tr.querySelector('[data-material]').innerHTML = evidence(s,row);
      tr.querySelector('.cmp-accessions').textContent = s.materials.get(id)?.accessions.join(' · ') || '';
      tr.querySelector('[data-samples]').innerHTML = sampleHTML(s.materials.get(id));
    }
    if (row?.cells?.length && !s.root.querySelector('[data-draft-review] textarea:focus')) drafts.refresh(s);
    refreshOverlap(s);
  }
  function versions(s) {
    const el = s.root.querySelector('[data-comparisons]');
    if (!el) return;
    el.innerHTML = translatedOption('', '新建比较') + s.list.map(c => option(c.id, c.question)).join('');
    el.value = s.draft.revision ? s.draft.id : '';
  }
  const scales = {unknown:'待核对', log2fc:'log2FC', fc:'FC', percentage:'百分比', other:'其他尺度'};
  function directionLabel(test, reference) {
    return test + ' ' + t('相对于') + ' ' + reference;
  }
  function columnDirections(items) {
    const pairs = new Map(), eligible = new Map(), scalesSeen = new Set();
    let reversed = false;
    for (const m of items) {
      const {test_group:test, reference_group:ref, scale} = m.contrast_definition || {};
      if (scale && scale !== 'unknown') scalesSeen.add(scale);
      if (!test || !ref) continue;
      const key = JSON.stringify([test,ref]), opposite = JSON.stringify([ref,test]);
      if (pairs.has(opposite)) reversed = true;
      pairs.set(key,true);
      if (scale === 'log2fc') {
        eligible.set(key,[test,ref]); eligible.set(opposite,[ref,test]);
      }
    }
    return {eligible, reversed, mixed:scalesSeen.size > 1};
  }
  // Negating a decimal string preserves tiny exponents and digits beyond JS number precision.
  function directedValue(m, target) {
    if (!target) return '';
    const def = m.contrast_definition || {};
    let issue;
    if (!def.scale || def.scale === 'unknown') issue = '尺度待核对';
    else if (def.scale !== 'log2fc') issue = '此尺度只显示原值';
    else if (!def.test_group || !def.reference_group) issue = '分组待核对';
    else if (!(target.includes(def.test_group) && target.includes(def.reference_group))) issue = '分组与所选方向不匹配';
    const raw = String(m.source.value).trim().replace(/^−/, '-');
    const number = /^([+-]?)((?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)$/.exec(raw);
    if (!issue && !number) issue = '此值无法换算';
    if (issue) return '<small>' + ui(issue) + '</small>';
    const magnitude = number[2], zero = !/[1-9]/.test(magnitude.split(/[eE]/)[0]);
    const negative = (number[1] === '-') !== (target[0] !== def.test_group);
    const value = (zero ? '' : negative ? '-' : '+') + magnitude;
    return '<div class="cmp-directed">' + ui('统一方向值') + ' <b data-directed-value>' + escape(value) + '</b></div>';
  }
  function contrastEditor(m) {
    const {test_group,reference_group,scale} = m.contrast_definition || {};
    return '<div class="cmp-contrast-editor" data-edit-definition="' + escape(m.id) + '"><label>' + ui('实验组（分子）') +
      '<input data-test-group maxlength="128" value="' + escape(test_group) + '"></label><label>' + ui('参照组（分母）') +
      '<input data-reference-group maxlength="128" value="' + escape(reference_group) + '"></label><label>' + ui('已核对的指标尺度') +
      '<select data-scale>' + Object.entries(scales).map(([key,label]) => translatedOption(key,label,key === (scale || 'unknown'))).join('') +
      '</select></label><button type="button" class="btn" data-apply-definition>' + ui('应用分组与尺度') +
      '</button><button type="button" class="btn btn-ghost" data-cancel-definition>' + ui('取消核对') + '</button></div>';
  }
  function measurements(s) {
    const rows = s.draft.rows;
    const all = rows.flatMap(row => row.table_measurements || []);
    const columns = [...new Set(all.map(m => m.field_label))];
    if (!columns.length) return '';
    const directions = new Map(columns.map(label => [label,columnDirections(all.filter(m => m.field_label === label))]));
    function heading(label) {
      const {eligible,reversed,mixed} = directions.get(label);
      const key = s.viewDirections?.get(label) || '';
      return '<th scope="col">' + escape(label) +
        (reversed ? '<small class="cmp-warning">' + ui('同列含相反的对比方向') + '</small>' : '') +
        (mixed ? '<small class="cmp-warning">' + ui('同列含不同的指标尺度') + '</small>' : '') +
        (eligible.size ? '<label>' + ui('统一查看方向') + '<select data-view-direction="' + escape(label) + '">' +
          translatedOption('','仅显示原值',!eligible.has(key)) + [...eligible].map(([value,pair]) =>
            '<option value="' + escape(value) + '"' + (value === key ? ' selected' : '') + '>' + escape(directionLabel(...pair)) + '</option>').join('') +
          '</select></label>' : '') + '</th>';
    }
    const cell = (row, label) => (row.table_measurements || []).filter(m => m.field_label === label).map(m => {
      const src = m.source;
      const def = m.contrast_definition || {}, test = def.test_group, ref = def.reference_group;
      const groups = test && ref ? directionLabel(test,ref) : [m.direction || test || ref,t('分组待核对')].filter(Boolean).join(' · ');
      const scale = def.scale && def.scale !== 'unknown' ? t(scales[def.scale]) : t('尺度待核对');
      return '<div><strong data-original-value>' + escape((src.display_value ?? src.value) || t('原表隐藏数值')) + '</strong>' +
        '<small>' + escape([src.identifier,src.column_name,...(src.conditions || []).map(c => c.name + '=' + c.value),groups,scale].filter(Boolean).join(' · ')) + '</small>' +
        directedValue(m, directions.get(label).eligible.get(s.viewDirections?.get(label))) +
        '<button class="btn btn-ghost" type="button" data-measurement-source="' + escape(m.id) + '">' +
        escape(src.document_name + ' · ' + src.sheet + ' · ') + (global.BioI18n?.t('第 {row} 行',{row:src.row}) ?? String(src.row)) + '</button>' +
        '<button type="button" class="btn btn-ghost" data-review-definition="' + escape(m.id) + '">' + ui('核对分组与尺度') + '</button>' +
        '<button type="button" class="btn btn-ghost" data-measurement-remove="' + escape(m.id) + '"' + field('从比较中移除此数值') + '>' + ui('移除') + '</button>' +
        (s.editingMeasurement === m.id ? contrastEditor(m) : '') + '</div>';
    }).join('') || '—';
    return '<section><h4>' + ui('原表数值') + '</h4><div class="cmp-table-wrap"><table class="cmp-measurements"><thead><tr><th scope="col">' +
      ui('论文') + '</th>' + columns.map(heading).join('') + '</tr></thead><tbody>' +
      rows.map(row => '<tr data-measurement-paper="' + escape(row.paper_id) + '"><th scope="row">' + escape(findPaper(s,row.paper_id)?.title || row.title) +
        '</th>' + columns.map(label => '<td>' + cell(row,label) + '</td>').join('') + '</tr>').join('') + '</tbody></table></div></section>';
  }
  async function clickMeasurement(s,el) {
    const row = findRow(s,el.closest('[data-measurement-paper]')?.dataset.measurementPaper);
    const id = el.dataset.measurementSource || el.dataset.measurementRemove || el.dataset.reviewDefinition || el.closest('[data-edit-definition]')?.dataset.editDefinition;
    const m = row?.table_measurements?.find(item => item.id === id);
    if (!m) return;
    if (el.hasAttribute('data-review-definition') || el.hasAttribute('data-cancel-definition')) {
      s.editingMeasurement = el.dataset.reviewDefinition; render(s);
      s.root.querySelector('[data-edit-definition] input')?.focus(); return;
    }
    if (el.hasAttribute('data-apply-definition')) {
      const form = el.closest('[data-edit-definition]');
      const test_group = form.querySelector('[data-test-group]').value.trim();
      const reference_group = form.querySelector('[data-reference-group]').value.trim();
      if (test_group && test_group === reference_group) { status(s,t('实验组和参照组应不同。'),true); return; }
      m.contrast_definition = {test_group,reference_group,scale:form.querySelector('[data-scale]').value};
      m.definitionEdited = true; s.editingMeasurement = null; dirty(s); render(s); return;
    }
    if (el.hasAttribute('data-measurement-remove')) {
      row.table_measurements = row.table_measurements.filter(item => item !== m); dirty(s); render(s); return;
    }
    const ticket = {}, epoch = s.epoch, view = s.view;
    s.jumpToken = ticket;
    const current = () => s.jumpToken === ticket && isActive(s) && epoch === s.epoch && view === s.view &&
      !s.root.closest?.('[hidden]') && row.table_measurements.includes(m);
    try {
      const comparison = s.draft.copyFrom || s.draft.id;
      const result = await request(base(s) + '/' + comparison + '/table-measurements/' + row.paper_id + '/' + m.id + '/source');
      if (!current()) return;
      const p = findPaper(s,row.paper_id);
      if (!p || !s.openTableSource(p,result.source)) throw new Error(t('来源不可用'));
    } catch (err) { if (current()) status(s,err.message,true); }
  }
  function render(s) {
    if (!isActive(s)) return;
    const rows = s.draft.rows, extra = rows.filter(r => !findPaper(s,r.paper_id)).map(r => ({id:r.paper_id, title:r.title}));
    s.root.innerHTML = '<div class="cmp-board"><div class="cmp-toolbar"><label>' + ui('已保存的比较') +
      '<select data-comparisons' + field('选择比较记录') + '></select></label><button type="button" class="btn" data-new>' +
      ui('新建比较') + '</button><button type="button" class="btn btn-ghost" data-reload>' + ui('重新载入') + '</button></div>' +
      '<fieldset class="cmp-fields"' + (locked(s) ? ' disabled' : '') + '><label>' + ui('研究问题') +
      '<textarea rows="2" maxlength="2000" data-question>' + escape(s.draft.question) + '</textarea></label><details class="cmp-selection"' +
      ((s.selectionOpen ?? rows.length < 2) ? ' open' : '') + '><summary>' + ui('参与比较的论文') + ' (' + rows.length + ')</summary><div class="cmp-papers">' +
      (s.papers.length || extra.length ? [...s.papers,...extra].map(p => '<label><input type="checkbox" data-paper="' + escape(p.id) + '"' +
        (findRow(s,p.id) ? ' checked' : '') + '><span>' + escape(p.title || p.id) + '</span></label>').join('') : ui('本库没有可选的论文。')) + '</div></details>' +
      measurements(s) + drafts.controls(s) + '<p class="cmp-warning" data-overlap hidden></p>' +
      (rows.length ? '<div class="cmp-table-wrap"><table class="cmp-table"><thead><tr><th>' + ui('论文与数据编号') + '</th><th>' + ui('研究对象') + ' / ' + ui('关键对照') + '</th><th>' + ui('原文与图表依据') +
        '</th><th>' + ui('此论文对问题的证据') + '</th></tr></thead><tbody>' + rows.map(r => rowHTML(s,r)).join('') + '</tbody></table></div>' :
        '<p class="hint">' + ui('选择至少两篇论文') + '</p>') +
      '<label>' + ui('跨论文结论与分歧') + '<textarea rows="3" maxlength="32000" data-synthesis>' + escape(s.draft.synthesis) +
      '</textarea></label><div class="cmp-footer"><button type="button" class="btn btn-primary" data-save>' +
      ui(s.busy ? '正在保存…' : '保存比较') + '</button><button type="button" class="btn" data-copy' + (s.conflict ? '' : ' hidden') + '>' +
      ui('另存为新比较') + '</button><span role="status" data-cmp-status></span></div></fieldset></div>';
    versions(s); refreshOverlap(s);
    status(s, s.drafting ? t('正在起草…') : s.busy ? t('正在保存…') : s.dirty ? t('尚未保存') : s.draft.revision ? t('比较已保存') : '');
  }
  function switchAllowed(s) { return !locked(s) && (!s.dirty || global.confirm(t('换到其他比较会丢弃当前未保存的修改，继续吗？'))); }
  async function choose(s, id) {
    if (!switchAllowed(s)) { versions(s); return; }
    const ticket = ++s.view;
    s.busy = true; render(s);
    try {
      const next = id ? await request(base(s) + '/' + id) : fresh();
      if (ticket !== s.view) return;
      drafts.clearPreview(s); s.draft = next; s.selectionOpen = null; s.viewDirections = new Map(); s.editingMeasurement = null;
      s.dirty = false; s.conflict = false; s.busy = false; render(s);
      await Promise.all(next.rows.map(r => loadMaterial(s,r.paper_id)));
    } catch (err) { if (ticket === s.view) { s.busy = false; render(s); status(s, err.message, true); } }
  }
  async function reloadList(s) {
    const epoch = s.epoch;
    try {
      const list = await request(base(s)); if (epoch !== s.epoch) return;
      s.list = list;
      if (isActive(s)) versions(s);
    } catch (err) { if (epoch === s.epoch) status(s, err.message, true); }
  }
  async function save(s, asNew = false) {
    if (locked(s)) return;
    if (!s.draft.question.trim() || s.draft.rows.length < 2) { status(s,t('请先填写研究问题并选择至少两篇不同的论文。'),true); return; }
    const d = s.draft;
    if (d.rows.some(r => r.cells?.some(cell => !cell.value.trim()))) { status(s,t('采纳的字段值不能为空。'),true); return; }
    // Keep the same ID on network retry so a successful write cannot create a duplicate.
    if (asNew && d.revision > 0) {
      if (d.rows.some(r => r.table_measurements?.length)) d.copyFrom = d.id;
      d.id = uid(); d.revision = 0; s.conflict = false;
    }
    const body = {revision:d.revision, question:d.question, synthesis:d.synthesis, ...(d.copyFrom ? {copy_measurements_from:d.copyFrom} : {}),
      ...(d.columns || d.rows.some(r => r.cells?.length) ? {columns:drafts.selected(s)} : {}), rows:d.rows.map(r => ({
      paper_id:r.paper_id, ...(r.table_measurements ? {table_measurement_refs:r.table_measurements.map(({id,snapshot,definitionEdited,contrast_definition}) =>
        ({id,snapshot,...(definitionEdited ? {contrast_definition} : {})}))} : {}), population:r.population || '', contrast:r.contrast || '',
      relation:r.relation, note:r.note, cells:(r.cells || []).map(({cohort,column,value,source_key}) => ({cohort,column,value,source_key})), references:r.sources.map(src => src.key)
    }))};
    s.busy = true; render(s);
    try {
      const result = await request(base(s) + '/' + d.id, body);
      s.list = [result, ...s.list.filter(item => item.id !== result.id)];
      s.draft = result; s.dirty = false; s.conflict = false; s.busy = false; render(s);
    } catch (err) {
      s.busy = false; s.conflict = err.status === 409; render(s); status(s,err.message,true);
    }
  }
  function edit(s,e) {
    if (locked(s)) return;
    const el = e.target, row = findRow(s,el.closest('[data-row]')?.dataset.row);
    if (el.hasAttribute('data-draft-value') || el.hasAttribute('data-accepted-value')) return drafts.input(s,el);
    if (el.hasAttribute('data-question')) {
      s.draft.question = el.value;
      if (s.preview) { drafts.clearPreview(s); drafts.refresh(s); }
    }
    else if (el.hasAttribute('data-synthesis')) s.draft.synthesis = el.value;
    else if (row && el.hasAttribute('data-population')) row.population = el.value;
    else if (row && el.hasAttribute('data-contrast')) row.contrast = el.value;
    else if (row && el.hasAttribute('data-row-note')) row.note = el.value;
    else if (row && el.hasAttribute('data-relation')) row.relation = el.value;
    else return;
    dirty(s);
  }
  function change(s,e) {
    const el = e.target;
    if (el.hasAttribute('data-comparisons')) return choose(s, el.value);
    if (locked(s)) return;
    if (el.hasAttribute('data-view-direction')) {
      (s.viewDirections ||= new Map()).set(el.dataset.viewDirection,el.value); render(s); return;
    }
    if (el.hasAttribute('data-draft-column')) return drafts.toggle(s,el);
    if (!el.hasAttribute('data-paper')) return;
    s.selectionOpen = !!el.closest('details')?.open;
    const id = el.dataset.paper, old = findRow(s,id);
    if (el.checked && !old) {
      const paper = findPaper(s,id); if (!paper) return;
      s.draft.rows.push(newRow(paper));
    } else if (!el.checked && old) {
      if (old.table_measurements?.length && !global.confirm(t('移除论文及其补充表数值？'))) { el.checked = true; return; }
      if (!old.table_measurements?.length && (old.note.trim() || old.population?.trim() || old.contrast?.trim() || old.relation !== 'unassessed' || old.sources.length || old.cells?.length) &&
          !global.confirm(t('该论文有未保留到其他位置的判断和依据，确定从本次比较移除吗？'))) { el.checked = true; return; }
      s.draft.rows = s.draft.rows.filter(r => r.paper_id !== id);
    }
    drafts.clearPreview(s); dirty(s); render(s);
    if (el.checked) return loadMaterial(s,id);
  }
  async function click(s,e) {
    const el = e.target.closest('button'); if (!el || locked(s)) return;
    if (['data-measurement-source','data-measurement-remove','data-review-definition','data-apply-definition','data-cancel-definition'].some(attr => el.hasAttribute(attr))) return clickMeasurement(s,el);
    if (el.hasAttribute('data-build-draft')) return drafts.build(s);
    if (el.hasAttribute('data-pdf-draft')) return drafts.build(s,true);
    if (el.hasAttribute('data-fold-paper')) return drafts.fold(s,el.dataset.foldPaper);
    if (el.hasAttribute('data-review')) return drafts.action(s,el);
    if (el.hasAttribute('data-new')) return choose(s,'');
    if (el.hasAttribute('data-save')) return save(s);
    if (el.hasAttribute('data-copy')) return save(s,true);
    if (el.hasAttribute('data-reload')) { await reloadList(s); return choose(s,s.draft.revision ? s.draft.id : ''); }
    const tr = el.closest('[data-row]'), id = tr?.dataset.row, row = findRow(s,id);
    if (!row) return;
    const m = s.materials.get(id);
    if (el.hasAttribute('data-retry')) return loadMaterial(s,id);
    if (el.hasAttribute('data-jump')) {
      const src = row.sources[Number(el.dataset.jump)];
      if (src) return jump(s,id,src,() => row.sources.includes(src));
      return;
    }
    if (el.hasAttribute('data-drop')) row.sources.splice(Number(el.dataset.drop),1);
    else if (m && el.hasAttribute('data-attach')) {
      const src = m.sources.find(item => item.key === tr.querySelector('[data-candidate]').value);
      if (!src || row.sources.some(item => item.key === src.key)) return;
      row.sources.push(src);
    } else if (m && el.hasAttribute('data-attach-page')) {
      const doc = m.documents.find(d => d.id === tr.querySelector('[data-document]').value);
      const page = Number(tr.querySelector('[data-page]').value);
      if (!doc || !Number.isInteger(page) || page < 1 || page > doc.pages) { status(s,t('页码超出所选文件范围。'),true); return; }
      const src = pageRef(doc,page); if (row.sources.some(item => item.key === src.key)) return;
      row.sources.push(src);
    } else return;
    dirty(s); refreshMaterial(s,id);
  }
  async function jump(s,id,src,stillSelected) {
    const epoch = s.epoch, view = s.view, token = {};
    s.jumpToken = token;
    try {
      const live = await request(base(s) + '/material/' + id + '/source?key=' + encodeURIComponent(src.key));
      if (s.jumpToken !== token || locked(s) || !isActive(s) || s.root.closest?.('[hidden]') || epoch !== s.epoch || view !== s.view || !stillSelected()) return;
      return await s.openSource(id,live.document_id,live.page,live.figure_id,live.metadata_revision,...(live.bbox ? [live.bbox] : []));
    } catch (err) {
      if (s.jumpToken !== token || !isActive(s) || epoch !== s.epoch || view !== s.view) return;
      await loadMaterial(s,id); status(s,err.message,true);
    }
  }
  async function open(root, {libraryId,papers,openSource,openTableSource}) {
    let s = states.get(libraryId);
    if (!s) {
      s = {libraryId, draft:fresh(), list:[], materials:new Map(), errors:new Map(), loads:new Map(), epoch:0, view:0};
      states.set(libraryId,s);
    }
    active = s; Object.assign(s,{root,papers:papers.filter(p => !p.schema_incompatible),openSource,openTableSource});
    if (!s.edits) drafts.clearPreview(s);
    ++s.epoch; s.materials.clear(); s.errors.clear();
    root.dataset.comparisonScope = libraryId;
    root.oninput = e => edit(s,e); root.onchange = e => change(s,e); root.onclick = e => click(s,e);
    render(s);
    await Promise.all([reloadList(s), ...s.draft.rows.map(r => loadMaterial(s,r.paper_id))]);
  }
  function evidenceTarget(scope,id) {
    const s = states.get(scope);
    return s && !locked(s) && findPaper(s,id) && s.draft.question.trim() ? s.draft : null;
  }
  async function attachEvidence(scope,id,record,target,stillCurrent) {
    if (!target || evidenceTarget(scope,id) !== target) throw new Error(t('当前比较或所选课题已变更。'));
    const s = states.get(scope);
    const material = await request(base(s) + '/material/' + id);
    if (!stillCurrent() || evidenceTarget(scope,id) !== target) throw new Error(t('当前比较或所选课题已变更。'));
    const src = material.sources.find(src => src.kind === 'evidence' && src.key.startsWith('evidence:' + record.id + ':') &&
      src.available && src.text === (record.quote || '') && src.label === record.claim &&
      src.page === record.page && src.document_id === record.document_id && src.status === record.status);
    if (!src) throw new Error(t('所选证据或原文已变更，请刷新材料后重新选择。'));
    let row = findRow(s,id);
    if (row?.sources.some(item => item.key === src.key)) return;
    if (!row) { row = newRow(findPaper(s,id)); target.rows.push(row); drafts.clearPreview(s); }
    row.sources.push(src);
    s.materials.set(id,material); s.errors.delete(id); dirty(s); render(s);
  }
  function canAttachTable(scope,id) {
    const s = states.get(scope);
    return !s || (!locked(s) && (!s.dirty || s.draft.id !== id));
  }
  function receivedTable(scope,record) {
    const s = states.get(scope);
    if (!s) return;
    s.list = [record,...s.list.filter(c => c.id !== record.id)];
    if (s.draft.id === record.id && !s.dirty && !locked(s)) { s.draft = record; render(s); }
    else if (isActive(s)) versions(s);
  }
  function selectedId(scope) {
    const s = states.get(scope);
    return s?.draft.revision ? s.draft.id : s?.dirty ? 'unsaved' : null;
  }
  const chooseSaved = (scope,id) => states.has(scope) && choose(states.get(scope),id);
  global.BioCompare = {open,evidenceTarget,attachEvidence,canAttachTable,receivedTable,chooseSaved,selectedId, canLeave:() => !active || !locked(active), hasUnsaved:(id) => [...states.values()].some(s => (!id || s.libraryId === id) && (s.dirty || s.busy))};
})(window);
