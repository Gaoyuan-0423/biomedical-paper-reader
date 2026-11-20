(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const words = {
    '补充附件': 'Supplementary files', '查找公开补充材料': 'Find public supplements',
    '已保存的补充材料': 'Saved supplementary files', '待选择的附件': 'Available files',
    '下载选中附件': 'Download selected files', '尚无已保存附件': 'No saved supplementary files.',
    '没有找到支持的公开附件。': 'No supported public supplementary files were found.',
    '正在查找 PMC 附件…': 'Finding supplementary files in PMC…', '需要 DOI、PMID 或 PMCID 才能在线查找。': 'Online search requires a DOI, PMID or PMCID.',
    '打开补充 PDF': 'Open supplementary PDF', '导出附件': 'Save file as', '获取中…': 'Downloading…',
    '附件已保存': 'File saved', '请先完成当前材料操作。': 'Finish the current document operation first.',
    '下载遇到问题': 'Download problem', '来源': 'Source', 'PMC 附件已变化，请重新查找': 'The PMC file changed. Search again.',
    '补充材料清单已过期，请重新查找': 'This file list has expired. Search again.',
    'PMC 连接失败，请稍后重试': 'PMC connection failed. Try again later.',
    '论文编号已变化，请重新查找': 'The paper identifiers changed. Search again.',
    'PMC 文件超过大小上限': 'The PMC file exceeds the download limit.',
    '补充附件内容与文件类型不符': 'The file content does not match its expected format.',
    'PMC 查询超时': 'The PMC search timed out.', '补充附件不存在': 'Supplementary file not found.',
    'PMC 编号响应无效': 'Invalid PMC identifier response.', 'PMC 编号查询失败': 'PMC identifier lookup failed.',
    'PMC 文件清单格式无效': 'Invalid PMC file manifest.', 'PMC 文章 XML 格式无效': 'Invalid PMC article XML.',
    'PMC 附件地址无效': 'Invalid PMC file address.', 'PMC 附件重定向次数过多': 'Too many redirects for the PMC file.',
  };
  window.BioI18n?.register(words);
  const tr = key => window.BioI18n?.t(key) || key;
  let hooks, paper, pending = false, saving = false, sequence = 0, searched = false, rows = [], selected = new Set(), results = new Map();
  function node(tag, value = '') {
    const el = document.createElement(tag); el.textContent = value; return el;
  }
  function label(tag, key) {
    const el = node(tag, tr(key)); el.dataset.i18n = key; return el;
  }
  function button(key, fn) {
    const el = label('button', key); el.className = 'btn'; el.type = 'button'; el.onclick = fn; return el;
  }
  function feedback(key = '') {
    const el = $('remote-supplement-status'); el.textContent = tr(key);
    if (words[key]) el.dataset.i18n = key; else delete el.dataset.i18n;
  }
  function originLink(item) {
    try {
      const url = new URL(item.source_url);
      if (url.protocol !== 'https:' || url.host !== 'pmc-oa-opendata.s3.amazonaws.com') return null;
      const a = label('a', '来源'); a.href = url.href; a.rel = 'noopener noreferrer'; a.target = '_blank'; return a;
    } catch (_) { return null; }
  }
  function records() { return [...(paper.supplements || []), ...(paper.supplement_files || [])]; }
  function render() {
    const root = $('remote-supplement-body'); root.replaceChildren(label('h4', '已保存的补充材料'));
    const saved = records(), known = new Set(saved.flatMap(r => [r.candidate_id, ...(r.candidate_aliases || [])]));
    if (!saved.length) root.append(label('p', '尚无已保存附件'));
    for (const item of saved) {
      const row = node('div'); row.className = 'supp-file-row';
      row.append(node('span', [item.source_label, item.filename || item.id].filter((value, index, names) => value && names.indexOf(value) === index).join(' · ')));
      if (item.article_version) row.append(node('small', item.article_version));
      const source = originLink(item); if (source) row.append(source);
      const pdf = !item.mime || item.mime === 'application/pdf';
      const open = button(pdf ? '打开补充 PDF' : '导出附件', async () => {
        if (pending) return;
        try {
          if (pdf) { const id = paper.id; close(); await hooks.jump(id, item.id); }
          else await hooks.exportFile(paper.id, item);
        } catch (err) { hooks.toast(tr(err.message), 'error'); }
      });
      open.disabled = pending; row.append(open); root.append(row);
    }
    const search = button('查找公开补充材料', discover);
    search.disabled = pending || ![paper.doi, paper.pmid, paper.pmcid].some(Boolean); root.append(search);
    if (search.disabled && !pending) root.append(label('p', '需要 DOI、PMID 或 PMCID 才能在线查找。'));
    if (rows.length) root.append(label('h4', '待选择的附件'));
    if (searched && !rows.length) root.append(label('p', '没有找到支持的公开附件。'));
    for (const item of rows) {
      const row = node('div'); row.className = 'supp-file-row';
      const field = node('label'), box = node('input'); box.type = 'checkbox'; box.checked = selected.has(item.id);
      box.disabled = pending || known.has(item.id); box.onchange = () => {
        if (box.checked) selected.add(item.id); else selected.delete(item.id);
        $('remote-supplement-save').disabled = !selected.size || pending;
      };
      field.append(box, node('span', [item.label, item.filename].filter((v,i,a) => v && a.indexOf(v) === i).join(' · ')));
      row.append(field, node('small', item.article_version));
      const source = originLink(item); if (source) row.append(source);
      if (known.has(item.id)) row.append(label('small', '附件已保存'));
      else if (results.has(item.id)) row.append(node('small', tr(results.get(item.id))));
      root.append(row);
    }
    $('remote-supplement-save').disabled = pending || !selected.size;
    $('remote-supplement-close').disabled = saving;
  }
  async function discover() {
    if (pending || !paper) return;
    const token = sequence; pending = true; searched = false; rows = []; selected.clear(); results.clear();
    render(); feedback('正在查找 PMC 附件…');
    try {
      const data = await hooks.find(paper.id);
      if (sequence !== token) return;
      rows = data.candidates; searched = true; feedback();
    } catch (err) { if (sequence === token) feedback(err.message); }
    finally { if (sequence === token) { pending = false; render(); } }
  }
  async function save() {
    if (pending || !selected.size) return;
    saving = pending = true; render(); feedback('获取中…');
    let failure = false;
    try {
      for (const key of [...selected]) {
        try {
          paper = await hooks.save(paper.id, key);
          selected.delete(key); results.delete(key);
        } catch (err) { failure = true; results.set(key, err.message); }
        render();
      }
    } finally { saving = pending = false; feedback(failure ? '下载遇到问题' : '附件已保存'); render(); }
  }
  function close() {
    if (saving) return false;
    sequence++; pending = false; paper = null; hooks.hide('remote-supplement-modal'); return true;
  }
  function open(current) {
    if (!current || pending) return;
    ++sequence; paper = current; rows = []; selected.clear(); results.clear(); searched = false;
    feedback(); render(); hooks.show('remote-supplement-modal', 'remote-supplement-close');
  }
  function init(options) {
    hooks = options;
    $('remote-supplement-close').onclick = close; $('remote-supplement-save').onclick = save;
    $('remote-supplement-modal').onclick = e => { if (e.target === e.currentTarget) close(); };
  }
  window.BioSupplementDownloads = {init, open, close, busy: () => saving};
})();
