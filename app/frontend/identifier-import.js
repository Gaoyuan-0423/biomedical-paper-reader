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
    '确认后获取正文': 'Find the PDF on confirmation',
    '正在获取正文…': 'Retrieving PDF…', '重试获取正文': 'Retry PDF download', '已处理': 'Processed',
    '停止后续导入': 'Stop after current request', '已停止后续导入': 'Remaining imports stopped',
    '未识别的编号或链接': 'Unrecognized ID or link',
    '条目数': 'Records', '已选': 'Selected', '本页结果已更新': 'Import results updated',
  };
  global.BioI18n?.register(words);
  const tr = text => global.BioI18n?.t(text) ?? text;
  const $ = id => document.getElementById(id);
  let hooks, scope, previewId, rows = [], selected = new Set(), serial = 0, phase = '', visible = false;
  let feedback = '', progress = null;
  const labels = {new:'准备新增', existing:'已有条目', invalid:'未识别的编号或链接', trashed:'已在回收站', duplicate:'重复编号',
    failed:'查询未成功', save_failed:'写入未成功', changed:'条目已变化', incompatible:'需升级后读取', conflict:'编号指向不同条目，请核对', added:'已加入', joined:'已加入课题库', join_failed:'已有文献，归集失败'};
  function eligible(row) {
    return ['existing','new'].includes(row.status) && (!row.result || ['join_failed','save_failed'].includes(row.result));
  }
  const downloadLabels = {downloaded:'正文已下载', already_present:'已有正文', not_found:'没有找到公开文件',
    failed:'获取失败', needs_review:'需人工核对'};
  function canRetryDownload(row) {
    return row.paper_id && row.is_link && !row.has_file && ['failed','not_found','needs_review'].includes(row.fulltext?.outcome)
      && ['added','existing','joined'].includes(row.result);
  }
  function sourceLink(result) {
    try {
      const url = new URL(result.source?.url);
      if (url.protocol !== 'https:' || url.username || url.password) return null;
      const link = document.createElement('a');
      link.textContent = tr('查看来源'); link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
      return link;
    } catch (_) { return null; }
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
    const cancel = $('doi-cancel');
    const cancelLabel = progress && progress.done + progress.active.size < progress.total ? '停止后续导入' : '关闭';
    cancel.textContent = tr(cancelLabel); cancel.dataset.i18n = cancelLabel;
    cancel.disabled = phase === 'apply' && (cancelLabel === '关闭' || progress.stop);
    $('doi-close').disabled = phase === 'apply';
    $('identifier-status').textContent = progress ? tr('已处理') + ' ' + progress.done + ' / ' + progress.total :
      phase ? tr('正在查询题录…') :
      [feedback ? tr(feedback) : '', rows.length ? tr('条目数') + ' ' + rows.length + ' · ' + tr('已选') + ' ' + selected.size : ''].filter(Boolean).join(' · ');
    const batch = $('doi-batch-pdf');
    if (batch) {
      const ids = rows.filter(row => ['added','joined','existing'].includes(row.result) && row.paper_id && !row.has_file).map(row => row.paper_id);
      batch.hidden = !ids.length || !!phase;
      batch.onclick = () => { if (!phase) { const target = scope; close(); hooks.batchPdf?.(ids,target); } };
    }
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
      const material = row.is_link && eligible(row) && !row.fulltext
        ? tr(row.has_file ? '已有正文' : '确认后获取正文') : '';
      info.textContent = [row.title ? row.input : '', tr(short), material, row.reason ? tr(row.reason) : ''].filter(Boolean).join(' · ');
      label.append(info);
      if (progress?.active.has(row.key) || row.fulltext) {
        const detail = document.createElement('small');
        if (progress?.active.has(row.key)) detail.textContent = tr(row.is_link && !row.has_file ? '正在获取正文…' : '正在加入文献…');
        else {
          const result = row.fulltext;
          detail.textContent = tr(downloadLabels[result.outcome] || '获取失败');
          if (result.message) detail.append(' · ', tr(result.message));
        }
        label.append(detail);
      }
      wrap.append(box,label);
      const actions = document.createElement('div'); actions.className = 'identifier-actions';
      if (row.fulltext) {
        const link = sourceLink(row.fulltext);
        if (link) actions.append(link);
      }
      if (canRetryDownload(row)) {
        const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'btn btn-ghost';
        retry.textContent = tr('重试获取正文'); retry.disabled = !!phase;
        retry.onclick = () => apply([row]);
        actions.append(retry);
      }
      if (row.paper_id && !['trashed','incompatible','conflict'].includes(status)) {
        const link = document.createElement('button'); link.type = 'button'; link.className = 'btn btn-ghost'; link.textContent = tr('打开文献'); link.disabled = !!phase;
        link.onclick = async () => { if (await hooks.openPaper(row.paper_id)) close(); else { feedback = '无法读取所选论文'; render(); } };
        actions.append(link);
      }
      if (actions.children.length) wrap.append(actions);
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
        return row.status === 'new' || (scope !== 'all' && !row.in_project) || (row.is_link && !row.has_file);
      }).map(row => row.key));
    } catch (error) { if (token === serial) feedback = error.message; }
    finally { if (token === serial) { phase = ''; render(); } }
  }
  async function apply(chosen) {
    if (phase || !chosen.length || !previewId) return;
    if (chosen.some(row => row.is_link && !row.has_file) && hooks.readyForDownload && !hooks.readyForDownload()) {
      feedback = '继续下载前请先保存当前编辑。'; render(); return;
    }
    // Plain identifiers share a request. Each link gets its own result before the next download begins.
    const groups = [];
    for (const row of chosen) {
      const last = groups[groups.length - 1];
      if (!row.is_link && last && !last[0].is_link) last.push(row);
      else groups.push([row]);
    }
    phase = 'apply'; feedback = '';
    progress = {total: chosen.length, done: 0, active: new Set(), stop: false};
    try {
      for (const group of groups) {
        if (progress.stop) break;
        const keys = group.map(row => row.key);
        progress.active = new Set(keys); render();
        const data = await hooks.request('/api/import/identifiers/apply', {preview_id: previewId, keys});
        for (const item of data.rows) {
          const row = group.find(r => r.key === item.key);
          if (!row) continue;
          // A PDF retry finds the record created or joined on the first attempt.
          if (item.status !== 'existing' || !['added','joined'].includes(row.result)) {
            row.result = item.status === 'failed' ? 'save_failed' :
              item.status === 'existing' && scope !== 'all' && !row.in_project ? 'joined' : item.status;
          }
          row.reason = item.reason || '';
          row.paper_id = item.paper_id;
          row.fulltext = item.fulltext;
          if (!eligible(row)) selected.delete(row.key);
          if (['downloaded','already_present'].includes(item.fulltext?.outcome)) {
            row.has_file = true;
            await hooks.onAttached?.(row.paper_id, item.fulltext.paper);
          }
        }
        progress.done += keys.length;
      }
      feedback = progress.stop ? '已停止后续导入' : '本页结果已更新';
    } catch (error) { feedback = error.message; }
    finally {
      try { await hooks.refresh(); } catch (error) { feedback = error.message; }
      progress = null; phase = ''; render();
    }
  }
  function init(options) {
    hooks = options;
    $('doi-close').onclick = close;
    $('doi-cancel').onclick = () => { if (progress) { progress.stop = true; render(); } else close(); };
    $('doi-modal').onclick = event => { if (event.target === event.currentTarget) close(); };
    $('doi-preview').onclick = lookup; $('doi-submit').onclick = () => apply(rows.filter(row => selected.has(row.key)));
    $('doi-input').oninput = () => {
      if (phase === 'apply') return;
      serial++; phase = ''; previewId = null; rows = []; selected.clear(); feedback = ''; render();
    };
    $('doi-input').onkeydown = event => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') { event.preventDefault(); lookup(); } };
    global.BioI18n?.onChange(render);
  }
  global.BioIdentifierImport = {init, open, close, isImporting:() => phase === 'apply'};
})(window);
