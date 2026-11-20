/* A preview pins its project so an import never moves into a newly selected library. */
(function (global) {
  'use strict';
  const words = {
    '加入位置': 'Destination', '准备新增': 'New record', '已有条目': 'Already in library',
    '已在此课题库': 'Already in this project', '已在回收站': 'In trash', '重复编号': 'Duplicate input',
    '无法识别编号': 'Unrecognized identifier', '查询未成功': 'Lookup failed', '需升级后读取': 'Newer version required',
    '编号指向不同条目，请核对': 'Identifiers refer to different records', '已加入': 'Added',
    '已加入课题库': 'Added to project', '已有文献，归集失败': 'Paper saved, project membership failed',
    '正在查询题录…': 'Looking up records…', '正在加入文献…': 'Adding papers…',
    '重新预览': 'Refresh preview', '重试查询失败项': 'Retry failed lookups', '打开文献': 'Open paper',
    '未找到该 PMID 的 PubMed 记录': 'No PubMed record found for this PMID', '查询超时，可重新预览': 'Lookup timed out. Preview again to retry.',
    '返回的编号或题名与请求不匹配': 'Returned identifier or title did not match the request',
    '每次请输入 1–50 个 DOI 或 PMID，每项最多 1024 个字符': 'Enter 1–50 DOI or PMID items, up to 1,024 characters each',
    '预览已过期，请重新预览': 'Preview expired. Run the preview again.', '请选择可加入的预览条目': 'Select eligible preview records',
    '原条目已变更，请重新预览': 'Original record changed. Refresh the preview.', '写入未成功': 'Could not save', '条目已变化': 'Record changed',
    '写入本地文献库失败': 'Could not write to the local library', '无法读取所选论文': 'Could not open this paper',
    '条目数': 'Records', '已选': 'Selected', '本页结果已更新': 'Import results updated',
  };
  global.BioI18n?.register(words);
  const tr = text => global.BioI18n?.t(text) ?? text;
  const $ = id => document.getElementById(id);
  let hooks, scope, previewId, rows = [], selected = new Set(), serial = 0, phase = '', visible = false;
  let feedback = '';
  const labels = {new:'准备新增', existing:'已有条目', invalid:'无法识别编号', trashed:'已在回收站', duplicate:'重复编号',
    failed:'查询未成功', save_failed:'写入未成功', changed:'条目已变化', incompatible:'需升级后读取', conflict:'编号指向不同条目，请核对', added:'已加入', joined:'已加入课题库', join_failed:'已有文献，归集失败'};
  function eligible(row) {
    return ['existing','new'].includes(row.status) && (!row.result || ['join_failed','save_failed'].includes(row.result));
  }
  function render() {
    if (!visible) return;
    $('identifier-target').textContent = tr('加入位置') + ' · ' + (scope === 'all' ? tr('全部文献') : hooks.scopeName(scope) || scope);
    $('doi-input').disabled = phase === 'apply';
    const preview = $('doi-preview');
    const caption = rows.some(r => r.status === 'failed') ? '重试查询失败项' : previewId ? '重新预览' : '预览条目';
    preview.dataset.i18n = caption; preview.textContent = tr(caption);
    preview.disabled = !!phase || !$('doi-input').value.trim();
    $('doi-submit').disabled = !!phase || !selected.size;
    $('identifier-status').textContent = phase ? tr(phase === 'apply' ? '正在加入文献…' : '正在查询题录…') :
      [feedback ? tr(feedback) : '', rows.length ? tr('条目数') + ' ' + rows.length + ' · ' + tr('已选') + ' ' + selected.size : ''].filter(Boolean).join(' · ');
    const root = $('identifier-rows');
    root.replaceChildren(...rows.map(row => {
      const wrap = document.createElement('div'); wrap.className = 'identifier-row';
      const box = document.createElement('input'); box.type = 'checkbox'; box.id = 'identifier-pick-' + row.key;
      box.disabled = !!phase || !eligible(row); box.checked = selected.has(row.key);
      box.onchange = () => {
        if (box.checked) selected.add(row.key); else selected.delete(row.key);
        render(); $(box.id)?.focus();
      };
      const label = document.createElement('label'); label.htmlFor = box.id;
      label.append(document.createTextNode(row.title || row.input));
      const info = document.createElement('small');
      const status = row.result || row.status;
      const short = status === 'existing' && row.in_project ? '已在此课题库' : labels[status];
      info.textContent = [row.title ? row.input : '', tr(short), row.reason ? tr(row.reason) : ''].filter(Boolean).join(' · ');
      label.append(info); wrap.append(box,label);
      if (row.paper_id && !['trashed','incompatible','conflict'].includes(status)) {
        const link = document.createElement('button'); link.type = 'button'; link.className = 'btn btn-ghost'; link.textContent = tr('打开文献'); link.disabled = !!phase;
        link.onclick = async () => { if (await hooks.openPaper(row.paper_id)) close(); else { feedback = '无法读取所选论文'; render(); } };
        wrap.append(link);
      }
      return wrap;
    }));
  }
  function open(text = '', explicitScope) {
    if (phase === 'apply') return;
    const target = explicitScope || hooks.currentScope();
    if (text || target !== scope) {
      rows = []; selected = new Set(); previewId = null; $('doi-input').value = text;
    }
    serial++; phase = ''; visible = true; feedback = ''; scope = target;
    hooks.show('doi-modal', 'doi-input'); render();
  }
  function close() {
    if (phase === 'apply') return;
    serial++; visible = false; phase = ''; hooks.hide('doi-modal');
  }
  async function lookup() {
    const text = $('doi-input').value.trim();
    if (!text || phase) return;
    const token = ++serial;
    const retry = previewId && rows.some(r => r.status === 'failed');
    phase = 'preview'; feedback = ''; render();
    try {
      const data = await hooks.request('/api/import/identifiers/preview', {text, library_id:scope, ...(retry ? {retry_preview_id: previewId} : {})});
      if (token !== serial) return;
      const previous = rows, checks = selected;
      previewId = data.preview_id; rows = data.rows;
      selected = new Set(rows.filter(row => {
        if (!eligible(row)) return false;
        if (retry && previous[row.key]?.status !== 'failed') return checks.has(row.key);
        return row.status === 'new' || (scope !== 'all' && !row.in_project);
      }).map(row => row.key));
    } catch (error) { if (token === serial) feedback = error.message; }
    finally { if (token === serial) { phase = ''; render(); } }
  }
  async function apply() {
    if (phase || !selected.size || !previewId) return;
    phase = 'apply'; feedback = ''; render();
    try {
      const data = await hooks.request('/api/import/identifiers/apply', {preview_id:previewId, keys:[...selected]});
      for (const item of data.rows) {
        const row = rows.find(r => r.key === item.key);
        row.result = item.status === 'failed' ? 'save_failed' : item.status === 'existing' && scope !== 'all' ? 'joined' : item.status;
        row.reason = item.reason || ''; row.paper_id = item.paper_id;
        if (!eligible(row)) selected.delete(item.key);
      }
      feedback = '本页结果已更新';
      await hooks.refresh();
    } catch (error) { feedback = error.message; }
    finally { phase = ''; render(); }
  }
  function init(options) {
    hooks = options;
    $('doi-close').onclick = close; $('doi-cancel').onclick = close;
    $('doi-modal').onclick = event => { if (event.target === event.currentTarget) close(); };
    $('doi-preview').onclick = lookup; $('doi-submit').onclick = apply;
    $('doi-input').oninput = () => {
      serial++; phase = ''; previewId = null; rows = []; selected.clear(); feedback = ''; render();
    };
    $('doi-input').onkeydown = event => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') { event.preventDefault(); lookup(); } };
    global.BioI18n?.onChange(render);
  }
  global.BioIdentifierImport = {init, open, close, isImporting:() => phase === 'apply'};
})(window);
