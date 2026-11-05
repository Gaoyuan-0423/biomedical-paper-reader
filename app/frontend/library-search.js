(function () {
  'use strict';
  const t = (key, params = {}) => window.BioI18n?.t(key, params) ?? key.replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
  const message = (key, params = {}) => ({ key, params, toString: () => window.BioResearchI18n?.messageText(key, params) ?? t(key, params) });
  const messageHTML = value => value?.key ? (window.BioResearchI18n?.messageHTML(value.key, value.params) ?? ui(value.key, value.params)) : esc(value);
  const ui = (key, params = {}) => '<span data-i18n="' + esc(key) + '" data-i18n-params="' + esc(JSON.stringify(params)) + '">' + esc(t(key, params)) + '</span>';
  let hooks, panel, footer, modal;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const state = { visible: false, query: '', scope: 'all', searched: '', searchedScope: 'all', items: [], warnings: [], warningCount: 0, more: false, offset: 0, loading: false, opening: false, error: '', version: 0 };
  const dirty = () => state.query.trim() !== state.searched || state.scope !== state.searchedScope;
  function render() {
    if (!panel || !state.visible) return;
    panel.innerHTML = "<p class=\"hint\">" + ui("检索全库论文正文、补充 PDF 和已保存笔记，不受侧栏标签或星标筛选影响。首次读取旧 PDF 可能需要稍等；不调用 AI。") + "</p>" +
      "<form id=\"library-search-form\" class=\"library-search-form\"><label class=\"sr-only\" for=\"library-search-query\">" + ui("搜索正文与笔记") + "</label><input id=\"library-search-query\" type=\"search\" minlength=\"2\" maxlength=\"200\" placeholder=\"输入关键词或一句原文\" data-i18n-placeholder=\"输入关键词或一句原文\" value=\"" + esc(state.query) + '"' + (state.opening ? ' disabled' : '') + "><label class=\"sr-only\" for=\"library-search-scope\">" + ui("搜索范围") + "</label><select id=\"library-search-scope\"" + (state.opening ? ' disabled' : '') + '>' + [['all', '正文与笔记'], ['pdf', '仅正文与补充'], ['notes', '仅已保存笔记']].map(([value, name]) => '<option value="' + value + '"' + (state.scope === value ? ' selected' : '') + ' data-i18n="' + esc(name) + '">' + esc(t(name)) + '</option>').join('') + '</select><button class="btn btn-primary" type="submit"' + (state.opening ? ' disabled' : '') + '>' + (state.loading ? ui("重新搜索") : ui("搜索")) + '</button></form>' +
      (state.error ? '<p class="library-search-error" role="alert">' + messageHTML(state.error) + '</p>' : '') +
      (state.loading ? "<p class=\"library-search-status\" role=\"status\">" + ui("正在检索本机材料…") + "</p>" : '') +
      '<p class="hint library-search-dirty"' + (dirty() && state.searched ? '' : ' hidden') + ">" + ui("搜索条件已修改，请点击搜索更新结果。") + "</p>" +
      (state.warnings.length ? "<details class=\"library-search-warnings\"><summary>" + ui('部分材料未能检索 · {count} 项', { count: state.warningCount }) + "</summary>" + state.warnings.map(w => '<p><strong>' + esc(w.paper_title) + '</strong>：' + ui(w.message) + '</p>').join('') + (state.warningCount > state.warnings.length ? "<p>" + ui('其余 {count} 项未展开；可检查材料后重新搜索。', { count: state.warningCount - state.warnings.length }) + "</p>" : '') + '</details>' : '') +
      "<section class=\"library-search-results\" aria-label=\"正文与笔记搜索结果\" data-i18n-aria-label=\"正文与笔记搜索结果\">" + (state.items.length ? state.items.map((item, index) => '<article class="library-search-result"><div class="library-search-result-meta">' + (item.kind === 'notes' ? ui('已保存笔记') : (item.document_id === 'main' ? ui('正文') : esc(item.source_name || 'PDF')) + ui(' · 第 {page} 页', { page: item.page })) + '</div><button type="button" class="library-search-title" data-library-search-action="open" data-library-search-index="' + index + '"' + (state.loading || state.opening || dirty() ? ' disabled' : '') + '>' + esc(item.paper_title) + '</button><p class="library-search-snippet">' + esc(item.snippet) + '</p></article>').join('') : !state.loading ? '<p class="library-search-empty">' + (state.searched ? ui("没有找到匹配内容。请换一个短语，或检查搜索范围。") : ui("输入至少两个字符后搜索。结果只来自已保存的内容。")) + '</p>' : '') + '</section>';
    footer.innerHTML = '<span class="library-search-summary" role="status">' + (state.opening ? ui("正在打开来源…") : state.items.length ? ui('已显示 {count} 条结果', { count: state.items.length }) : '') + '</span>' + (state.more ? '<button class="btn" type="button" data-library-search-action="more"' + (state.loading || state.opening || dirty() ? ' disabled' : '') + ">" + ui("加载更多") + "</button>" : '') + "<button class=\"btn\" type=\"button\" data-library-search-action=\"close\">" + ui("关闭") + "</button>";
  }
  async function search(more = false) {
    if (state.opening || more && (state.loading || !state.more || dirty())) return;
    const query = state.query.trim(), scope = state.scope;
    if (query.replace(/\s/g, '').length < 2 || query.length > 200) { state.error = message("请输入 2–200 个字符的搜索词。"); render(); return; }
    const version = ++state.version, offset = more ? state.offset : 0;
    if (!more) { state.items = []; state.more = false; state.offset = 0; state.warnings = []; state.warningCount = 0; }
    state.loading = true; state.error = ''; render();
    try {
      const result = await hooks.request('GET', '/api/library/search?q=' + encodeURIComponent(query) + '&scope=' + scope + '&offset=' + offset + '&limit=20');
      if (!state.visible || version !== state.version || state.query.trim() !== query || state.scope !== scope) return;
      if (!Array.isArray(result.items) || !Number.isInteger(result.next_offset) || result.next_offset < offset + result.items.length) throw Error(t("搜索结果不完整，请重试。"));
      state.items = state.items.concat(result.items); state.more = !!result.has_more; state.offset = result.next_offset;
      state.warnings = Array.isArray(result.warnings) ? result.warnings : []; state.warningCount = Number.isInteger(result.warning_count) ? result.warning_count : state.warnings.length;
      state.searched = query; state.searchedScope = scope;
    } catch (error) { if (state.visible && version === state.version) state.error = message("搜索失败：" + "{detail}", { detail: error.message }); }
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
    } catch (error) { if (state.visible && version === state.version) state.error = message("无法打开来源：" + "{detail}", { detail: error.message }); }
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
      const submit = panel.querySelector('#library-search-form button[type="submit"]'); if (submit) { submit.textContent = t('搜索'); submit.setAttribute?.('data-i18n', '搜索'); }
    });
    panel.addEventListener('change', event => { if (event.target.id === 'library-search-scope' && !state.opening && ['all', 'pdf', 'notes'].includes(event.target.value)) { state.scope = event.target.value; state.version++; state.loading = false; state.error = ''; render(); } });
    panel.addEventListener('submit', event => { if (event.target.id === 'library-search-form') { event.preventDefault(); return search(); } });
    modal.addEventListener('click', event => { const button = event.target.closest('[data-library-search-action]'); if (!button || button.disabled) return; const action = button.dataset.librarySearchAction; if (action === 'close') close(); else if (action === 'more') return search(true); else if (action === 'open') return openResult(Number(button.dataset.librarySearchIndex)); });
  }
  window.BioLibrarySearch = { init, open, close, isOpen: () => state.visible };
})();
