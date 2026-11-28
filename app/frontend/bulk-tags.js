(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const t = (key, params) => window.BioI18n.t(key, params);
  window.BioI18n.register({
    '已选中 {count} 篇文献':'{count} papers selected',
    '撤销「{tag}」':'Undo “{tag}”', '新增 {added} 篇，已有 {existing} 篇，跳过 {skipped} 篇。':'Added to {added}, already tagged {existing}, skipped {skipped}.',
    '已撤销上次批量添加。':'The last bulk addition was undone.',
    '标签已在另一项操作中更新，请刷新后重试':'Tags changed in another operation. Refresh and try again.',
    '批量标签记录无法读取':'The bulk tag receipt could not be read.',
    '批量标签记录已变更，请重新打开操作':'The bulk tag receipt has changed. Reopen the action.',
    '结果中的文献已移出课题或不可编辑，请刷新文献列表':'A paper has left the project or cannot be edited. Refresh the library.',
    '请输入 1–40 个字符的单行标签':'Enter a single-line tag of 1–40 characters.',
    '有 {count} 项材料无法检索，已排除。':'{count} materials could not be searched and were excluded.',
  });
  let hooks, session;
  function controls(s) {
    if (s !== session) return;
    $('bulk-tag-apply').disabled = s.busy || !s.ready || !s.ids.length || !$('bulk-tag-value').value.trim();
    $('bulk-tag-undo').hidden = !s.receipt?.undo;
    $('bulk-tag-undo').disabled = s.busy || !s.ready;
    if (s.receipt?.undo) $('bulk-tag-undo').textContent = t('撤销「{tag}」', {tag:s.receipt.tag});
    $('bulk-tag-count').textContent = t('已选中 {count} 篇文献', {count:s.ids.length});
    $('bulk-tag-warning').textContent = s.warning ? t('有 {count} 项材料无法检索，已排除。', {count:s.warning}) : '';
    $('bulk-tag-status').textContent = s.status ? t(s.status, s.counts) : '';
    $('bulk-tag-value').disabled = s.busy || !s.ready || !s.ids.length;
    $('bulk-tag-close').disabled = s.busy;
  }
  async function open(ids, scope, warnings = 0, onClose) {
    const s = session = {ids: [...new Set(ids)], path:'/api/library/' + encodeURIComponent(scope) + '/tag-batch',
      ready:false, busy:false, warning:warnings, status:'', counts:{}, onClose};
    $('bulk-tag-value').value = '';
    controls(s);
    hooks.show('bulk-tag-modal', 'bulk-tag-close');
    try {
      s.receipt = await hooks.request('GET', s.path);
      if (session !== s) return;
      s.ready = true; controls(s);
      if (s.ids.length) $('bulk-tag-value').focus();
    } catch (e) { s.status = e.message; controls(s); }
  }
  function close() {
    if (session?.busy) return;
    const onClose = session?.onClose;
    session = null;
    hooks.hide('bulk-tag-modal');
    onClose?.();
  }
  async function submit(undo) {
    const s = session, tag = $('bulk-tag-value').value.trim();
    if (!s || s.busy || !s.ready || (undo ? !s.receipt?.undo : !s.ids.length || !tag)) return;
    s.busy = true; controls(s);
    try {
      if (undo) {
        s.receipt = await hooks.request('POST', s.path + '/' + s.receipt.id + '/undo');
        s.status = '已撤销上次批量添加。';
      } else {
        const result = await hooks.request('POST', s.path, {request_id:crypto.randomUUID().replace(/-/g, ''),
          previous_id:s.receipt.id, paper_ids:s.ids, tag});
        s.receipt = result.receipt;
        s.counts = result; s.status = '新增 {added} 篇，已有 {existing} 篇，跳过 {skipped} 篇。';
        $('bulk-tag-value').value = '';
      }
      await hooks.refresh();
    } catch (e) {
      s.status = e.message;
      // A connection can drop after the write. Recover the server's undo receipt.
      try { s.receipt = await hooks.request('GET', s.path); await hooks.refresh(); }
      catch (_) { s.ready = false; }
    } finally { s.busy = false; controls(s); }
  }
  function init(options) {
    hooks = options;
    $('bulk-tag-form').addEventListener('submit', e => { e.preventDefault(); void submit(false); });
    $('bulk-tag-value').addEventListener('input', () => controls(session));
    $('bulk-tag-close').onclick = close;
    $('bulk-tag-undo').onclick = () => submit(true);
    window.BioI18n.onChange(() => { if (session) controls(session); });
  }
  window.BioBulkTags = {init,open,close};
})();
