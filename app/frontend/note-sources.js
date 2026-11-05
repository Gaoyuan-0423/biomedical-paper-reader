/* Saved note citations are resolved by the backend, never by model page numbers. */
(function (global) {
  'use strict';
  const SOURCE = /^note-source:([0-9a-f]{32}):(N[1-9][0-9]{0,4})$/;
  let hooks = {}, currentKey = '', requestToken = 0, renderToken = 0;
  let sources = null, pending = null, currentRoot = null, currentStreaming = false;

  function parseReference(value) {
    const match = SOURCE.exec(String(value || ''));
    return match ? { snapshotId: match[1], sourceId: match[2] } : null;
  }

  function init(options) { hooks = options; clear(); }
  function clear() {
    currentKey = ''; requestToken++; renderToken++; sources = null; pending = null; currentRoot = null;
  }

  function decorate(root, paperId, token, streaming) {
    if (root !== currentRoot || token !== renderToken) return;
    root.querySelectorAll('a[href^="note-source:"]').forEach(anchor => {
      // Code examples must remain code, even if user HTML contains an anchor.
      if (anchor.closest('pre, code')) return;
      const reference = parseReference(anchor.getAttribute('href'));
      if (!reference) {
        anchor.replaceWith(document.createTextNode(anchor.textContent || '来源不可用'));
        return;
      }
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'note-citation';
      button.dataset.snapshotId = reference.snapshotId;
      button.dataset.sourceId = reference.sourceId;
      button.addEventListener('click', async () => {
        if (currentRoot !== root || token !== renderToken || button.disabled) return;
        button.disabled = true;
        try { await hooks.jump({ paperId, ...reference }); }
        catch (error) { hooks.toast?.('无法打开笔记来源：' + error.message, 'error'); }
        finally { if (currentRoot === root && token === renderToken) update(button, streaming); }
      });
      anchor.replaceWith(button);
    });
    root.querySelectorAll('button.note-citation').forEach(button => update(button, streaming));
  }

  function update(button, streaming) {
    const key = button.dataset.snapshotId + ':' + button.dataset.sourceId;
    const source = sources?.get(key);
    const missing = sources !== null && !source;
    const removed = source?.source_available === false, changed = source?.source_changed === true;
    button.disabled = !!streaming || !!pending || missing || removed || changed;
    button.textContent = removed ? '来源已移除' : changed ? '来源已变化' : missing ? '来源不可用' : source ? '原文 · ' + source.page + ' 页' : '原文 ' + button.dataset.sourceId;
    button.title = streaming ? '笔记保存完成后可查看来源' : pending ? '正在读取笔记来源' : removed || changed ? '笔记保留了生成时的来源记录；当前文件已变化，无法定位旧原文。' : missing ? '当前笔记没有可用的来源记录' : source
      ? (source.document_name || (source.document_id === 'main' ? '正文' : '补充材料')) + ' · PDF 第 ' + source.page + ' 页\n' + String(source.quote || '').slice(0, 240)
      : '点击重新核验并打开原文';
    button.setAttribute('aria-label', button.title);
  }

  function render(paper, root, options = {}) {
    if (!paper || !root) { clear(); return; }
    const key = [paper.id, paper.notes_revision || 0, paper.notes_source_snapshot || '', paper.material_revision || 0].join(':');
    const changed = currentKey !== key;
    if (changed) { currentKey = key; requestToken++; sources = null; pending = null; }
    currentRoot = root;
    currentStreaming = !!options.streaming;
    const token = ++renderToken;
    const anchors = root.querySelectorAll('a[href^="note-source:"]');
    if (!anchors.length) return;
    if (!options.streaming && sources === null && !pending) {
      const request = ++requestToken;
      pending = Promise.resolve().then(() => hooks.request(paper.id)).then(result => {
        if (request !== requestToken || currentKey !== key) return;
        sources = new Map();
        for (const source of result.sources || []) {
          const snapshot = source.snapshot_id || result.snapshot_id;
          if (!parseReference('note-source:' + snapshot + ':' + source.id) || !Number.isInteger(source.page) || source.page < 1) continue;
          sources.set(snapshot + ':' + source.id, source);
        }
      }).catch(() => {
        // Leave a retry action available; resolving a click still checks disk.
      }).finally(() => {
        if (request !== requestToken || currentKey !== key) return;
        pending = null;
        if (currentRoot) decorate(currentRoot, paper.id, renderToken, currentStreaming);
      });
    }
    decorate(root, paper.id, token, !!options.streaming);
  }

  global.BioNoteSources = { init, clear, render, parseReference };
})(window);
