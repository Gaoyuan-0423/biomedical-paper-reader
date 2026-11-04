(function () {
  'use strict';
  let hooks, panel, footer, settingsPanel, modal;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const authors = value => Array.isArray(value) ? value.map(a => typeof a === 'string' ? a : a.name || [a.firstName, a.lastName].filter(Boolean).join(' ')).join('、') : String(value || '');
  const state = { visible: false, query: '', searched: '', items: [], hasMore: false, start: 0, configurationId: '', selected: '', preview: null, attachment: '', searching: false, loading: false, importing: false, opening: false, error: '', success: null, searchVersion: 0, previewVersion: 0 };
  const settings = { active: false, config: null, draft: {}, loading: false, saving: false, checking: false, error: '', message: '', version: 0, session: 0, writeVersion: 0 };
  const endpoint = () => settings.config?.endpoint || '';
  const disabled = yes => yes ? ' disabled' : '';

  function feedbackHTML(error, message) {
    return (error ? '<p class="zotero-error" role="alert">' + esc(error) + '</p>' : '') + (message ? '<p class="zotero-message" role="status">' + esc(message) + '</p>' : '');
  }
  function renderSettings() {
    if (!settingsPanel) return;
    if (settings.loading) { settingsPanel.innerHTML = '<p class="hint" role="status">正在读取 Zotero 连接设置…</p>'; return; }
    const d = settings.draft, locked = settings.saving;
    settingsPanel.innerHTML = '<p class="hint">通过 Zotero MCP 搜索你选择的文献，再按条目导入。此处连接独立于模型 API，不会自动同步整库。</p>' +
      '<div class="field-grid"><label class="field zotero-endpoint-field"><span>MCP 地址</span><input id="zotero-endpoint" data-zotero-setting="endpoint" type="url" placeholder="http://127.0.0.1:8000/mcp" value="' + esc(d.endpoint) + '"' + disabled(locked) + '><small>localhost / 127.0.0.1 指使用者自己的电脑。请填 MCP 服务地址；Zotero 的 localhost:23119/api/ 是本地 API，不是 MCP 地址。</small></label>' +
      '<label class="field"><span>访问 Token（可选）</span><input id="zotero-token" data-zotero-setting="token" type="password" autocomplete="off" placeholder="留空保持已保存 Token" value="' + esc(d.token) + '"' + disabled(locked) + '><small>' + (settings.config?.has_token ? '已保存 Token；更换 MCP 地址后需重新填写。' : '未设置 Token；没有鉴权的本机服务可留空。') + '</small></label>' +
      '<label class="field zotero-storage-field"><span>本机 Zotero 附件目录</span><input id="zotero-storage-root" data-zotero-setting="storage_root" type="text" value="' + esc(d.storage_root || '~/Zotero/storage') + '"' + disabled(locked) + '><small>读取本机已经下载的 PDF。链接附件暂请用「导入 PDF」手动上传。</small></label></div>' +
      '<label class="zotero-clear-token"><input type="checkbox" id="zotero-clear-token" data-zotero-setting="clear_token"' + (d.clear_token ? ' checked' : '') + disabled(locked) + '>清除已保存 Token</label>' +
      '<div class="zotero-settings-actions"><button type="button" class="btn" data-zotero-setting-action="check"' + disabled(locked || settings.checking) + '>' + (settings.checking ? '正在测试…' : '测试连接') + '</button><button type="button" class="btn btn-primary" data-zotero-setting-action="save"' + disabled(locked) + '>' + (locked ? '正在保存…' : '保存 Zotero 设置') + '</button></div><div id="zotero-settings-feedback">' + feedbackHTML(settings.error, settings.message) + '</div>';
  }
  function settingsFeedback() {
    const box = settingsPanel?.querySelector('#zotero-settings-feedback');
    if (box) box.innerHTML = feedbackHTML(settings.error, settings.message);
  }
  async function loadSettings() {
    settings.active = true;
    const session = ++settings.session, writeVersion = settings.writeVersion;
    settings.version++; settings.loading = true; settings.checking = false; settings.error = ''; settings.message = ''; settings.draft.token = '';
    renderSettings();
    try {
      const received = await hooks.request('GET', '/api/zotero/settings');
      if (session !== settings.session) return;
      const config = writeVersion === settings.writeVersion ? received : settings.config || received;
      settings.config = config;
      settings.draft = { endpoint: config.endpoint || '', storage_root: config.storage_root || '~/Zotero/storage', token: '', clear_token: false };
    } catch (error) { if (session === settings.session) settings.error = '读取 Zotero 设置失败：' + error.message; }
    finally { if (session === settings.session) { settings.loading = false; renderSettings(); if (state.visible) render(); } }
  }
  function closeSettings() {
    settings.active = false; settings.session++; settings.version++; settings.loading = false; settings.checking = false;
    settings.draft.token = ''; settings.draft.clear_token = false;
    const token = settingsPanel?.querySelector('#zotero-token'); if (token) token.value = '';
  }
  function configurationBody() {
    const body = { endpoint: (settings.draft.endpoint || '').trim(), storage_root: (settings.draft.storage_root || '').trim() || '~/Zotero/storage' };
    if (settings.draft.clear_token) body.clear_token = true;
    else if ((settings.draft.token || '').trim()) body.token = settings.draft.token.trim();
    return body;
  }
  function resetResults() {
    state.searchVersion++; state.previewVersion++; state.searching = false; state.loading = false;
    state.items = []; state.hasMore = false; state.start = 0; state.searched = ''; state.selected = ''; state.preview = null; state.attachment = ''; state.configurationId = ''; state.error = ''; state.success = null;
  }
  async function settingsAction(action) {
    if (settings.loading || settings.saving || !['check', 'save'].includes(action) || (action === 'check' && settings.checking)) return;
    const body = configurationBody(), session = settings.session, version = ++settings.version;
    if (!body.endpoint && action === 'check') { settings.error = '请先填写 Zotero MCP 地址。'; settings.message = ''; settingsFeedback(); return; }
    settings.error = ''; settings.message = ''; settings.checking = action === 'check'; settings.saving = action === 'save';
    if (action === 'save') settings.writeVersion++;
    let saved = false;
    renderSettings();
    try {
      const result = await hooks.request(action === 'save' ? 'PUT' : 'POST', '/api/zotero/' + (action === 'save' ? 'settings' : 'check'), body);
      if (action === 'save') {
        saved = true; settings.writeVersion++; settings.config = result; resetResults();
        if (state.visible) render();
      }
      if (session !== settings.session || version !== settings.version) return;
      if (action === 'save') {
        settings.draft = { endpoint: result.endpoint || '', storage_root: result.storage_root || '~/Zotero/storage', token: '', clear_token: false };
        settings.message = 'Zotero 设置已单独保存。';
      } else {
        const capabilities = result.capabilities || {};
        settings.message = (result.message || '连接成功') + (result.server_name ? ' · ' + result.server_name : '') + '；可用能力：' + [['search', '搜索'], ['metadata', '文献信息'], ['attachments', '附件']].filter(([key]) => capabilities[key]).map(([, label]) => label).join('、');
      }
    } catch (error) { if (session === settings.session && version === settings.version) settings.error = (action === 'save' ? '保存失败：' : '连接测试失败：') + error.message; }
    finally {
      if (action === 'save') settings.saving = false;
      if (session === settings.session && version === settings.version) { settings.checking = false; renderSettings(); }
      else if (action === 'save' && settings.active) {
        if (saved) { settings.draft = { endpoint: settings.config.endpoint || '', storage_root: settings.config.storage_root || '~/Zotero/storage', token: '', clear_token: false }; settings.message = 'Zotero 设置已单独保存。'; }
        renderSettings();
      }
    }
  }

  function itemHTML(item) {
    return '<button type="button" class="zotero-result' + (state.selected === item.key ? ' selected' : '') + '" data-zotero-action="select" data-zotero-key="' + esc(item.key) + '" aria-pressed="' + (state.selected === item.key) + '"' + disabled(state.importing) + '><strong>' + esc(item.title || '未命名文献') + '</strong><span>' + esc([authors(item.authors), item.year].filter(Boolean).join(' · ') || item.item_type || '文献条目') + '</span>' + (item.doi ? '<small>DOI: ' + esc(item.doi) + '</small>' : '') + '</button>';
  }
  function previewHTML() {
    if (state.loading) return '<p class="hint" role="status">正在读取文献信息与附件…</p>';
    const preview = state.preview;
    if (!preview) return '<p class="zotero-empty">' + (state.selected ? '条目读取未完成。<button type="button" class="btn btn-ghost" data-zotero-action="preview-retry">重新读取</button>' : '选择一条搜索结果，查看文献信息和可导入的 PDF。') + '</p>';
    const item = preview.item, attachments = Array.isArray(preview.attachments) ? preview.attachments : [];
    return '<h4>' + esc(item.title || '未命名文献') + '</h4><p class="zotero-meta">' + esc([authors(item.authors), item.year, item.journal].filter(Boolean).join(' · ')) + '</p>' +
      (item.doi ? '<p class="zotero-meta">DOI: ' + esc(item.doi) + '</p>' : '') +
      (item.abstract ? '<details class="zotero-abstract"><summary>摘要</summary><p>' + esc(item.abstract) + '</p></details>' : '') +
      '<fieldset class="zotero-attachments"' + disabled(state.importing) + '><legend>选择导入内容</legend><label class="zotero-attachment"><input type="radio" name="zotero-attachment" value=""' + (!state.attachment ? ' checked' : '') + '><span><strong>仅导入文献信息</strong><small>保存标题、作者、摘要等；不读取 PDF，可稍后手动附加。</small></span></label>' +
      attachments.map(file => { const available = file.available && file.content_type === 'application/pdf'; return '<label class="zotero-attachment' + (!available ? ' unavailable' : '') + '"><input type="radio" name="zotero-attachment" value="' + esc(file.key) + '"' + (state.attachment === file.key ? ' checked' : '') + disabled(!available) + '><span><strong>' + esc(file.filename || file.title || '附件') + '</strong><small>' + esc(available ? '文献信息 + 本机 PDF' : file.unavailable_reason || '没有可读取的本机 PDF') + '</small></span></label>'; }).join('') + '</fieldset>' +
      (!attachments.length ? '<p class="hint">此条目没有可导入的 PDF 附件，仍可导入文献信息。</p>' : '');
  }
  function render() {
    if (!panel || !state.visible) return;
    panel.innerHTML = '<p class="hint zotero-intro">搜索 Zotero 中的文献，再选择需要的条目和 PDF。导入到 BioReader 后独立保存，不修改 Zotero。</p>' +
      (!endpoint() && !settings.loading ? '<div class="zotero-connect-prompt">请先配置 Zotero MCP 连接。<button class="btn" type="button" data-zotero-action="settings">连接设置</button></div>' : '') +
      '<form id="zotero-search" class="zotero-search"><label class="sr-only" for="zotero-query">搜索 Zotero 文献</label><input id="zotero-query" type="search" maxlength="300" placeholder="输入标题、作者或关键词" value="' + esc(state.query) + '"' + disabled(state.importing) + '><button class="btn btn-primary" type="submit"' + disabled(state.importing || !endpoint()) + '>' + (state.searching ? '搜索中…' : '搜索') + '</button></form>' +
      feedbackHTML(state.error, '') +
      '<div class="zotero-browser"><section class="zotero-results" aria-label="搜索结果">' + (state.items.length ? state.items.map(itemHTML).join('') : '<p class="zotero-empty">' + (state.searching ? '正在搜索…' : state.searched ? '没有找到匹配文献，请换一个关键词。' : '输入关键词开始搜索，不会自动读取整库。') + '</p>') +
      (state.hasMore ? '<button class="btn zotero-more" type="button" data-zotero-action="more"' + disabled(state.searching || state.importing) + '>加载更多</button>' : '') + '</section><section class="zotero-preview" aria-label="文献信息与附件">' + previewHTML() + '</section></div>';
    renderFooter();
  }
  function renderFooter() {
    if (!footer || !state.visible) return;
    const success = state.success;
    footer.innerHTML = '<div class="zotero-import-status" role="status">' + (state.importing ? '正在导入所选内容…关闭窗口后仍会继续。' : success ? esc(success.message || (success.existing ? '文献已在 BioReader 中。' : '已导入 BioReader。')) + '<small>' + (success.paper.has_file ? '此条目已有 PDF' : '仅文献信息 · 尚无 PDF') + '</small>' : state.preview ? state.attachment ? '将导入文献信息和选中的 PDF。' : '将仅导入文献信息，不读取 PDF。' : '一次只导入你选择的条目。') + '</div><button type="button" class="btn" data-zotero-action="close">关闭</button>' +
      (success ? '<button class="btn btn-primary" type="button" data-zotero-action="open-paper"' + disabled(state.opening) + '>' + (state.opening ? '正在打开…' : '打开文献') + '</button>' : '<button class="btn btn-primary" type="button" id="zotero-import" data-zotero-action="import"' + disabled(!state.preview || state.loading || state.importing || state.searching) + '>' + (state.importing ? '导入中…' : '导入所选内容') + '</button>');
  }
  async function open() {
    state.visible = true; hooks.showModal('zotero-modal', 'zotero-close'); render();
    if (!settings.config && !settings.loading) await loadSettings();
    if (state.visible) panel.querySelector('#zotero-query')?.focus();
  }
  function close() {
    state.visible = false; state.searchVersion++; state.previewVersion++; state.searching = false; state.loading = false;
    hooks.hideModal('zotero-modal');
  }
  async function search(more = false) {
    const query = state.query.trim();
    if (state.importing || !endpoint()) return;
    if (!query) { state.error = '请输入关键词后再搜索。'; render(); return; }
    if (more && (state.searching || query !== state.searched)) return;
    const version = ++state.searchVersion;
    state.previewVersion++; state.loading = false; state.selected = ''; state.preview = null; state.attachment = ''; state.error = ''; state.success = null;
    if (!more) { state.items = []; state.start = 0; state.hasMore = false; }
    state.searching = true; render();
    try {
      const result = await hooks.request('POST', '/api/zotero/search', { query, limit: 20, start: state.start });
      if (!state.visible || version !== state.searchVersion || state.query.trim() !== query) return;
      if (more && state.configurationId !== result.configuration_id) throw new Error('连接配置已变化，请重新搜索。');
      const items = Array.isArray(result.items) ? result.items : [];
      const known = new Set(state.items.map(i => i.key)); state.items = state.items.concat(items.filter(i => i.key && !known.has(i.key)));
      state.start += 20; state.hasMore = !!result.has_more; state.configurationId = result.configuration_id; state.searched = query;
    } catch (error) { if (state.visible && version === state.searchVersion) state.error = '搜索失败：' + error.message; }
    finally { if (state.visible && version === state.searchVersion) { state.searching = false; render(); } }
  }
  async function select(key) {
    if (state.importing || !state.items.some(item => item.key === key)) return;
    const version = ++state.previewVersion, configuration = state.configurationId;
    state.selected = key; state.preview = null; state.attachment = ''; state.loading = true; state.error = ''; state.success = null; render();
    try {
      const result = await hooks.request('GET', '/api/zotero/items/' + encodeURIComponent(key) + '?configuration_id=' + encodeURIComponent(configuration));
      if (!state.visible || version !== state.previewVersion || configuration !== state.configurationId) return;
      if (result.configuration_id !== configuration || result.item?.key !== key || !result.preview_id) throw new Error('条目预览已变化，请重新搜索。');
      state.preview = result;
    } catch (error) { if (state.visible && version === state.previewVersion) state.error = '读取条目失败：' + error.message; }
    finally { if (state.visible && version === state.previewVersion) { state.loading = false; render(); } }
  }
  async function importItem() {
    if (state.importing || state.searching || state.loading || !state.preview || state.success) return;
    const body = { preview_id: state.preview.preview_id, attachment_key: state.attachment };
    state.importing = true; state.error = ''; render();
    try {
      const result = await hooks.request('POST', '/api/zotero/import', body);
      if (!result.paper?.id) throw new Error('导入结果缺少文献，请刷新文献库检查。');
      state.success = result;
      hooks.onImported(result.paper);
      if (!state.visible) hooks.toast(result.message || 'Zotero 文献已导入，可在文献库打开。', 'ok');
    } catch (error) {
      state.error = '导入失败：' + error.message;
      if (error.status === 409) state.preview = null;
      if (!state.visible) hooks.toast(state.error, 'error');
    } finally { state.importing = false; render(); }
  }
  async function click(event) {
    const button = event.target.closest('[data-zotero-action]');
    if (!button || button.disabled) return;
    const action = button.dataset.zoteroAction;
    if (action === 'close') return close();
    if (action === 'settings') { close(); hooks.openSettings(); return; }
    if (action === 'select') return select(button.dataset.zoteroKey);
    if (action === 'preview-retry') return select(state.selected);
    if (action === 'more') return search(true);
    if (action === 'import') return importItem();
    if (action === 'open-paper' && state.success && !state.opening) {
      state.opening = true; renderFooter();
      try { if (await hooks.openPaper(state.success.paper)) close(); }
      catch (error) { state.error = '打开失败：' + error.message; render(); }
      finally { state.opening = false; renderFooter(); }
    }
  }
  function init(options) {
    hooks = options; panel = options.panel || document.getElementById('zotero-panel'); footer = options.footer || document.getElementById('zotero-footer');
    settingsPanel = options.settingsPanel || document.getElementById('zotero-settings'); modal = options.modal || document.getElementById('zotero-modal');
    modal.addEventListener('click', event => { if (event.target === modal) return close(); return click(event); });
    panel.addEventListener('submit', event => { if (event.target.id === 'zotero-search') { event.preventDefault(); return search(); } });
    panel.addEventListener('input', event => { if (event.target.id === 'zotero-query') state.query = event.target.value; });
    panel.addEventListener('change', event => {
      if (event.target.name !== 'zotero-attachment' || state.importing || !state.preview) return;
      const key = event.target.value;
      if (key && !state.preview.attachments.some(file => file.key === key && file.available && file.content_type === 'application/pdf')) return;
      state.attachment = key; state.success = null; renderFooter();
    });
    settingsPanel.addEventListener('input', event => {
      const field = event.target.dataset.zoteroSetting; if (!field || settings.saving) return;
      settings.draft[field] = event.target.type === 'checkbox' ? event.target.checked : event.target.value;
      settings.version++; settings.error = ''; settings.message = ''; settingsFeedback();
      if (settings.checking) { settings.checking = false; const button = settingsPanel.querySelector('[data-zotero-setting-action="check"]'); if (button) { button.disabled = false; button.textContent = '测试连接'; } }
    });
    settingsPanel.addEventListener('click', event => { const button = event.target.closest('[data-zotero-setting-action]'); if (button && !button.disabled) return settingsAction(button.dataset.zoteroSettingAction); });
  }
  window.BioZotero = { init, open, close, loadSettings, closeSettings, isImporting: () => state.importing };
})();
