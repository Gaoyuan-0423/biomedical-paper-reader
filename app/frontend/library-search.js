(function () {
  'use strict';
  let hooks, panel, footer, modal;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const state = { visible: false, query: '', scope: 'all', searched: '', searchedScope: 'all', items: [], warnings: [], warningCount: 0, more: false, offset: 0, loading: false, opening: false, error: '', version: 0 };
  const dirty = () => state.query.trim() !== state.searched || state.scope !== state.searchedScope;
  function render() {
    if (!panel || !state.visible) return;
    panel.innerHTML = '<p class="hint">检索全库论文正文、补充 PDF 和已保存笔记，不受侧栏标签或星标筛选影响。首次读取旧 PDF 可能需要稍等；不调用 AI。</p>' +
      '<form id="library-search-form" class="library-search-form"><label class="sr-only" for="library-search-query">搜索正文与笔记</label><input id="library-search-query" type="search" minlength="2" maxlength="200" placeholder="输入关键词或一句原文" value="' + esc(state.query) + '"' + (state.opening ? ' disabled' : '') + '><label class="sr-only" for="library-search-scope">搜索范围</label><select id="library-search-scope"' + (state.opening ? ' disabled' : '') + '>' + [['all', '正文与笔记'], ['pdf', '仅正文与补充'], ['notes', '仅已保存笔记']].map(([value, name]) => '<option value="' + value + '"' + (state.scope === value ? ' selected' : '') + '>' + name + '</option>').join('') + '</select><button class="btn btn-primary" type="submit"' + (state.opening ? ' disabled' : '') + '>' + (state.loading ? '重新搜索' : '搜索') + '</button></form>' +
      (state.error ? '<p class="library-search-error" role="alert">' + esc(state.error) + '</p>' : '') +
      (state.loading ? '<p class="library-search-status" role="status">正在检索本机材料…</p>' : '') +
      '<p class="hint library-search-dirty"' + (dirty() && state.searched ? '' : ' hidden') + '>搜索条件已修改，请点击搜索更新结果。</p>' +
      (state.warnings.length ? '<details class="library-search-warnings"><summary>部分材料未能检索 · ' + state.warningCount + ' 项</summary>' + state.warnings.map(w => '<p><strong>' + esc(w.paper_title) + '</strong>：' + esc(w.message) + '</p>').join('') + (state.warningCount > state.warnings.length ? '<p>其余 ' + (state.warningCount - state.warnings.length) + ' 项未展开；可检查材料后重新搜索。</p>' : '') + '</details>' : '') +
      '<section class="library-search-results" aria-label="正文与笔记搜索结果">' + (state.items.length ? state.items.map((item, index) => '<article class="library-search-result"><div class="library-search-result-meta">' + esc(item.kind === 'notes' ? '已保存笔记' : (item.source_name || 'PDF') + ' · 第 ' + item.page + ' 页') + '</div><button type="button" class="library-search-title" data-library-search-action="open" data-library-search-index="' + index + '"' + (state.loading || state.opening || dirty() ? ' disabled' : '') + '>' + esc(item.paper_title) + '</button><p class="library-search-snippet">' + esc(item.snippet) + '</p></article>').join('') : !state.loading ? '<p class="library-search-empty">' + (state.searched ? '没有找到匹配内容。请换一个短语，或检查搜索范围。' : '输入至少两个字符后搜索。结果只来自已保存的内容。') + '</p>' : '') + '</section>';
    footer.innerHTML = '<span class="library-search-summary" role="status">' + (state.opening ? '正在打开来源…' : state.items.length ? '已显示 ' + state.items.length + ' 条结果' : '') + '</span>' + (state.more ? '<button class="btn" type="button" data-library-search-action="more"' + (state.loading || state.opening || dirty() ? ' disabled' : '') + '>加载更多</button>' : '') + '<button class="btn" type="button" data-library-search-action="close">关闭</button>';
  }
  async function search(more = false) {
    if (state.opening || more && (state.loading || !state.more || dirty())) return;
    const query = state.query.trim(), scope = state.scope;
    if (query.replace(/\s/g, '').length < 2 || query.length > 200) { state.error = '请输入 2–200 个字符的搜索词。'; render(); return; }
    const version = ++state.version, offset = more ? state.offset : 0;
    if (!more) { state.items = []; state.more = false; state.offset = 0; state.warnings = []; state.warningCount = 0; }
    state.loading = true; state.error = ''; render();
    try {
      const result = await hooks.request('GET', '/api/library/search?q=' + encodeURIComponent(query) + '&scope=' + scope + '&offset=' + offset + '&limit=20');
      if (!state.visible || version !== state.version || state.query.trim() !== query || state.scope !== scope) return;
      if (!Array.isArray(result.items) || !Number.isInteger(result.next_offset) || result.next_offset < offset + result.items.length) throw Error('搜索结果不完整，请重试。');
      state.items = state.items.concat(result.items); state.more = !!result.has_more; state.offset = result.next_offset;
      state.warnings = Array.isArray(result.warnings) ? result.warnings : []; state.warningCount = Number.isInteger(result.warning_count) ? result.warning_count : state.warnings.length;
      state.searched = query; state.searchedScope = scope;
    } catch (error) { if (state.visible && version === state.version) state.error = '搜索失败：' + error.message; }
    finally { if (state.visible && version === state.version) { state.loading = false; render(); } }
  }
  function close() { state.visible = false; state.version++; state.loading = false; state.opening = false; hooks.hideModal('library-search-modal'); }
  async function openResult(index) {
    const item = state.items[index];
    if (!item || state.loading || state.opening || dirty()) return;
    const version = state.version; state.opening = true; state.error = ''; render();
    try {
      const opened = await hooks.onOpenResult(item, { isCurrent: () => state.visible && version === state.version });
      if (!state.visible || version !== state.version) return;
      if (opened === true) close();
    } catch (error) { if (state.visible && version === state.version) state.error = '无法打开来源：' + error.message; }
    finally { if (state.visible && version === state.version) { state.opening = false; render(); } }
  }
  function open(initialQuery = '') {
    state.visible = true; state.version++; state.loading = false; state.opening = false; state.error = '';
    if (initialQuery && initialQuery !== state.query) { state.query = String(initialQuery).slice(0, 200); state.items = []; state.searched = ''; state.more = false; state.offset = 0; state.warnings = []; }
    hooks.showModal('library-search-modal'); render(); panel.querySelector('#library-search-query')?.focus();
  }
  function init(options) {
    hooks = options; panel = options.panel; footer = options.footer; modal = options.modal;
    panel.addEventListener('input', event => {
      if (event.target.id !== 'library-search-query' || state.opening) return;
      state.query = event.target.value; state.version++; state.loading = false; state.error = '';
      // Keep the input node and caret while editing; actions check dirty state.
      panel.querySelectorAll?.('[data-library-search-action="open"]').forEach(button => { button.disabled = dirty(); });
      const more = footer.querySelector('[data-library-search-action="more"]'); if (more) more.disabled = dirty();
      panel.querySelector('.library-search-status')?.remove?.();
      panel.querySelector('.library-search-error')?.remove?.();
      const hint = panel.querySelector('.library-search-dirty'); if (hint) hint.hidden = !(dirty() && state.searched);
      const submit = panel.querySelector('#library-search-form button[type="submit"]'); if (submit) submit.textContent = '搜索';
    });
    panel.addEventListener('change', event => { if (event.target.id === 'library-search-scope' && !state.opening && ['all', 'pdf', 'notes'].includes(event.target.value)) { state.scope = event.target.value; state.version++; state.loading = false; state.error = ''; render(); } });
    panel.addEventListener('submit', event => { if (event.target.id === 'library-search-form') { event.preventDefault(); return search(); } });
    modal.addEventListener('click', event => { const button = event.target.closest('[data-library-search-action]'); if (!button || button.disabled) return; const action = button.dataset.librarySearchAction; if (action === 'close') close(); else if (action === 'more') return search(true); else if (action === 'open') return openResult(Number(button.dataset.librarySearchIndex)); });
  }
  window.BioLibrarySearch = { init, open, close, isOpen: () => state.visible };
})();
