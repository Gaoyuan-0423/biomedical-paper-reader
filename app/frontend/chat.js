(function () {
  'use strict';
  const PAPER_ID = /^[0-9a-f]{32}$/, MESSAGE_ID = /^[a-zA-Z0-9_-]{1,100}$/, SOURCE_ID = /^S[1-9][0-9]*$/;
  const sessions = new Map();
  let hooks = {}, panel = null, active = null;
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const path = id => '/api/papers/' + id + '/chat';
  function restoreDraft(id) { try { return window.sessionStorage?.getItem('bioreader.chat.draft.' + id) || ''; } catch (_) { return ''; } }
  function saveDraft(state) { try { window.sessionStorage?.setItem('bioreader.chat.draft.' + state.paper.id, state.draft); } catch (_) { /* In-memory draft remains available. */ } }
  function contextFor(state) { return state.pinned || state.live; }
  function validContext(ctx) { return ctx && (ctx.document_id === 'main' || PAPER_ID.test(ctx.document_id || '')) && Number.isInteger(ctx.page) && ctx.page >= 1; }
  function sourceMap(message) { return new Map((Array.isArray(message.sources) ? message.sources : []).filter(s => SOURCE_ID.test(s.id || '')).map(s => [s.id, s])); }
  function sourceButton(message, source, label) {
    if (!MESSAGE_ID.test(message.id || '')) return esc(label);
    return '<button type="button" class="chat-source" data-chat-action="source" data-chat-message="' + esc(message.id) + '" data-chat-source="' + esc(source.id) + '">' + esc(label) + '</button>';
  }
  function messageHTML(message) {
    const user = message.role === 'user', sources = sourceMap(message);
    const content = esc(message.content).replace(/\[(S[1-9][0-9]*)\]/g, (text, id) => !user && sources.has(id) ? sourceButton(message, sources.get(id), text) : text);
    const status = { pending: active?.stream?.assistantId === message.id ? '正在回答…' : '上一轮回复仍在结束或保存，可刷新对话查看状态。', interrupted: '回复已中断，已收到的内容保留。', error: '本次回复未完成，已收到的内容保留。' }[message.status] || '';
    const questionDocument = message.document_name || (message.document_id === 'main' ? '正文' : active?.paper.supplements?.find(d => d.id === message.document_id)?.filename || '补充材料');
    return '<article class="chat-message chat-message-' + (user ? 'user' : 'assistant') + '" data-chat-message-id="' + esc(message.id) + '"><div class="chat-role">' + (user ? '你' : '阅读助手') + '</div><div class="chat-content">' + content + '</div>' +
      (user && message.selection ? '<details class="chat-sources chat-selected-question"><summary>当时选中的原文 · ' + esc(questionDocument) + ' · 第 ' + esc(message.page) + ' 页</summary><blockquote>' + esc(message.selection) + '</blockquote></details>' : '') +
      (!user && status ? '<p class="chat-message-status">' + status + '</p>' : '') +
      (!user && sources.size ? '<details class="chat-sources"><summary>原文来源 · ' + sources.size + '</summary>' + [...sources.values()].map(s => '<div>' + sourceButton(message, s, (s.document_name || '原文') + ' · 第 ' + s.page + ' 页 · [' + s.id + ']') + '<blockquote>' + esc(s.quote) + '</blockquote></div>').join('') + '</details>' : '') + '</article>';
  }
  function updateContext() {
    const target = panel?.querySelector('#chat-context');
    if (!target || !active) return;
    const ctx = contextFor(active), valid = validContext(ctx);
    target.innerHTML = '<span class="chat-context-chip">' + (valid ? esc(ctx.document_name || (ctx.document_id === 'main' ? '正文' : '补充材料')) + ' · PDF 第 ' + ctx.page + ' 页' : '请选择可阅读的 PDF') + '</span>' +
      (ctx?.selection ? '<div class="chat-selection"><span title="' + esc(ctx.selection) + '">选区：' + esc(ctx.selection.slice(0, 180)) + (ctx.selection.length > 180 ? '…' : '') + '</span><button type="button" class="btn btn-ghost" data-chat-action="clear-selection">取消选区</button></div>' : '') +
      (ctx?.selection ? '<div class="chat-quick"><button type="button" class="btn" data-chat-action="explain"' + (active.stream || active.loading ? ' disabled' : '') + '>解释选区</button><button type="button" class="btn" data-chat-action="translate"' + (active.stream || active.loading ? ' disabled' : '') + '>翻译选区</button></div>' : '');
  }
  function updateStatus() {
    if (!active || !panel) return;
    const state = active, box = panel.querySelector('#chat-feedback');
    if (box) box.innerHTML = (state.error ? '<p class="chat-error" role="alert">' + esc(state.error) + '</p>' : '') + (state.notice ? '<p class="chat-notice" role="status">' + esc(state.notice) + '</p>' : '') +
      (state.stream ? '<div class="chat-stream-status" role="status">正在回答，收到内容后会即时显示。<button class="btn" type="button" data-chat-action="stop">停止</button></div>' : state.error && state.lastAttempt ? '<button type="button" class="btn" data-chat-action="retry">重试上一问题</button>' : '');
    const send = panel.querySelector('#chat-send');
    if (send) send.disabled = !!state.stream || state.loading || !validContext(contextFor(state)) || !state.draft.trim();
    updateContext();
  }
  function updateHistory(prepend = false) {
    const box = panel?.querySelector('#chat-history');
    if (!box || !active) return;
    const bottom = box.scrollHeight - box.scrollTop - box.clientHeight < 70, before = box.scrollHeight, top = box.scrollTop;
    const state = active;
    box.innerHTML = (state.hasMore ? '<button type="button" class="btn chat-earlier" data-chat-action="earlier"' + (state.loadingEarlier || state.stream ? ' disabled' : '') + '>' + (state.loadingEarlier ? '正在读取…' : '加载更早对话') + '</button>' : '') +
      (state.loading ? '<p class="chat-empty" role="status">正在恢复对话…</p>' : state.messages.length ? state.messages.map(messageHTML).join('') : '<p class="chat-empty">可直接提问，或在 PDF 中选中文字后解释、翻译。问答单独保存，不会覆盖精读笔记。</p>');
    if (prepend) box.scrollTop = top + box.scrollHeight - before;
    else if (bottom || state.followBottom) { box.scrollTop = box.scrollHeight; state.followBottom = false; }
  }
  function render() {
    if (!panel) return;
    if (!active) { panel.innerHTML = '<p class="chat-empty">打开一篇论文后开始问答。</p>'; return; }
    panel.innerHTML = '<div class="chat-heading"><strong>论文问答</strong><button class="btn btn-ghost" type="button" data-chat-action="reload"' + (active.loading || active.stream ? ' disabled' : '') + '>刷新对话</button></div><div id="chat-context" class="chat-context"></div><div id="chat-history" class="chat-history" tabindex="0" aria-label="论文问答历史"></div><div id="chat-feedback" class="chat-feedback"></div><form id="chat-form" class="chat-composer"><label for="chat-input">关于当前论文的问题</label><textarea id="chat-input" maxlength="6000" rows="3" placeholder="例如：这项实验用了什么样品？"' + (active.loading ? ' disabled' : '') + '>' + esc(active.draft) + '</textarea><div><span class="hint">⌘ / Ctrl + Enter 发送</span><button class="btn btn-primary" id="chat-send" type="submit">发送</button></div></form>';
    updateHistory(); updateStatus();
  }
  function upsert(state, message) {
    if (!message || !MESSAGE_ID.test(message.id || '') || !['user', 'assistant'].includes(message.role)) return;
    const index = state.messages.findIndex(m => m.id === message.id);
    if (index >= 0) state.messages[index] = message; else state.messages.push(message);
  }
  async function load(state, earlier = false) {
    if (state.stream || state.loading || state.loadingEarlier) return state.loadPromise;
    const version = ++state.version;
    if (earlier) state.loadingEarlier = true; else state.loading = true;
    state.error = ''; if (active === state) { if (earlier) updateHistory(); else render(); }
    state.loadPromise = (async () => {
      try {
        const first = state.messages[0]?.id;
        const result = await hooks.request('GET', path(state.paper.id) + (earlier && MESSAGE_ID.test(first || '') ? '?before=' + encodeURIComponent(first) : ''));
        if (state.version !== version) return;
        const messages = Array.isArray(result.messages) ? result.messages : [];
        state.messages = earlier ? [...messages, ...state.messages.filter(m => !messages.some(old => old.id === m.id))] : messages;
        state.hasMore = !!result.has_more; state.loaded = true;
      } catch (err) { if (state.version === version) state.error = '对话读取失败：' + err.message; }
      finally {
        if (state.version === version) {
          state.loading = state.loadingEarlier = false;
          if (active === state) { if (earlier) { updateHistory(true); updateStatus(); } else render(); }
        }
      }
    })();
    return state.loadPromise;
  }
  function stop(state = active, message = '已停止生成，已收到的内容保留。') {
    if (!state?.stream) return;
    const running = state.stream; state.stream = null; running.controller.abort();
    const assistant = state.messages.find(m => m.id === running.assistantId);
    if (assistant?.status === 'pending') assistant.status = 'interrupted';
    if (!running.assistantId && running.sentDraft && !state.draft) {
      state.draft = running.body.message; saveDraft(state);
      if (active === state) { const input = panel.querySelector('#chat-input'); if (input) input.value = state.draft; }
    }
    state.notice = message;
    if (active === state) { updateHistory(); updateStatus(); }
  }
  async function open(paper) {
    if (!paper || !PAPER_ID.test(paper.id || '')) { clear(); return; }
    const cached = sessions.get(paper.id);
    if (cached && cached.materialRevision !== paper.material_revision) {
      stop(cached); cached.pinned = null; cached.lastAttempt = null; cached.materialRevision = paper.material_revision; cached.sourceVersion = (cached.sourceVersion || 0) + 1;
      cached.notice = '原文材料已更新，请重新选择要提问的段落。';
    }
    if (active?.paper.id === paper.id) { active.paper = paper; updateStatus(); return active.loadPromise; }
    if (active) { saveDraft(active); stop(active); ++active.version; active.loading = active.loadingEarlier = false; }
    if (!sessions.has(paper.id)) sessions.set(paper.id, { paper, materialRevision: paper.material_revision, messages: [], draft: restoreDraft(paper.id), live: { document_id: paper.has_file ? 'main' : '', document_name: '正文', page: 1, selection: '' }, pinned: null, version: 0, loading: false, loadingEarlier: false, stream: null, loaded: false, hasMore: false, error: '', notice: '', followBottom: true });
    active = sessions.get(paper.id); active.paper = paper; render(); await load(active);
  }
  function clear() {
    if (active) { saveDraft(active); stop(active); ++active.version; active.loading = active.loadingEarlier = false; }
    active = null; render();
  }
  function setContext(context) {
    if (!active || (context.paper_id && context.paper_id !== active.paper.id)) return;
    active.live = { ...context, selection: '' };
    updateContext(); updateStatus();
  }
  async function askSelection(action, context) {
    const state = active;
    if (!state || !['ask', 'explain', 'translate'].includes(action) || (context?.paper_id && context.paper_id !== state.paper.id)) return;
    if (context?.selection) state.pinned = { ...context };
    updateContext(); updateStatus();
    if (state.loadPromise) await state.loadPromise;
    if (active !== state) return;
    if (action === 'ask') { panel.querySelector('#chat-input')?.focus(); return; }
    if (!contextFor(state)?.selection) { hooks.toast?.('请先在 PDF 中选择要解释或翻译的文字。', 'error'); return; }
    await send(action);
  }
  async function send(action = 'ask', retryBody = null) {
    const state = active;
    if (!state || state.stream || state.loading || state.error?.startsWith('对话读取失败')) return;
    const ctx = contextFor(state);
    if (!validContext(ctx) && !retryBody) { hooks.toast?.('请先打开有效的 PDF 页。', 'error'); return; }
    const message = action === 'explain' ? '请解释这段选中的原文。' : action === 'translate' ? '请将这段选中的原文翻译成中文，并保留专业术语。' : state.draft.trim();
    const body = retryBody || { message, document_id: ctx.document_id, page: ctx.page, selection: ctx.selection || '', action };
    if (!body.message.trim()) return;
    const running = { controller: new AbortController(), assistantId: null, terminal: false, body, sentDraft: !retryBody && action === 'ask' };
    state.stream = running; state.error = ''; state.notice = ''; state.lastAttempt = body; state.followBottom = true;
    if (action === 'ask' && (!retryBody || state.draft.trim() === body.message)) { state.draft = ''; saveDraft(state); const input = panel.querySelector('#chat-input'); if (input) input.value = ''; }
    updateStatus();
    const isCurrent = () => active === state && state.stream === running && !running.controller.signal.aborted;
    function event(value) {
      if (!isCurrent() || running.terminal) return;
      if (value.type === 'start') { upsert(state, value.message); upsert(state, value.assistant); running.assistantId = value.assistant?.id; }
      else if (value.type === 'chunk' && value.message_id === running.assistantId) { const m = state.messages.find(m => m.id === running.assistantId); if (m) m.content += String(value.text || ''); }
      else if (value.type === 'done') { upsert(state, value.message); running.terminal = true; state.lastAttempt = null; }
      else if (value.type === 'error') { upsert(state, value.assistant); running.terminal = true; state.error = String(value.message || '本次回复未完成。'); }
      updateHistory(); updateStatus();
    }
    try {
      const response = await (hooks.fetch || fetch)(path(state.paper.id), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: running.controller.signal });
      if (!response.ok) { let detail = response.statusText; try { detail = (await response.json()).detail || detail; } catch (_) {} throw new Error(detail || '问答请求失败'); }
      if (!response.body) throw new Error('未收到回复内容。');
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let buffer = '';
      const consume = block => { const data = block.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n'); if (data) event(JSON.parse(data)); };
      while (isCurrent()) {
        const { done, value } = await reader.read();
        if (done) { buffer += decoder.decode(); if (buffer.trim()) consume(buffer); break; }
        buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, '\n');
        let end; while ((end = buffer.indexOf('\n\n')) >= 0) { consume(buffer.slice(0, end)); buffer = buffer.slice(end + 2); }
      }
      if (isCurrent() && !running.terminal) throw new Error('连接中断，已收到的回复保留。可刷新对话或重试。');
    } catch (err) {
      if (isCurrent()) {
        state.error = err.message || String(err);
        const assistant = state.messages.find(m => m.id === running.assistantId);
        if (assistant?.status === 'pending') assistant.status = 'interrupted';
        if (!running.assistantId && !state.draft && action === 'ask') { state.draft = body.message; saveDraft(state); const input = panel.querySelector('#chat-input'); if (input) input.value = state.draft; }
      }
    } finally {
      if (state.stream === running) { state.stream = null; if (active === state) { updateHistory(); updateStatus(); } }
    }
  }
  async function click(event) {
    const button = event.target.closest('[data-chat-action]');
    if (!button || button.disabled || !active) return;
    const state = active, action = button.dataset.chatAction;
    if (action === 'stop') { stop(); return; }
    if (action === 'reload') { await load(state); return; }
    if (action === 'earlier') { await load(state, true); return; }
    if (action === 'clear-selection') { state.pinned = null; updateStatus(); return; }
    if (action === 'explain' || action === 'translate') { await askSelection(action, contextFor(state)); return; }
    if (action === 'retry' && state.lastAttempt) { await send(state.lastAttempt.action, state.lastAttempt); return; }
    if (action === 'source') {
      const version = state.version;
      const message = state.messages.find(m => m.id === button.dataset.chatMessage), source = message && sourceMap(message).get(button.dataset.chatSource);
      if (!source || !MESSAGE_ID.test(message.id)) return;
      const sourceVersion = state.sourceVersion = (state.sourceVersion || 0) + 1;
      button.disabled = true;
      try {
        const result = await hooks.request('GET', path(state.paper.id) + '/sources/' + encodeURIComponent(message.id) + '/' + source.id);
        if (!result.source) throw new Error('来源信息不完整，请刷新对话后重试。');
        if (active === state && state.version === version && state.sourceVersion === sourceVersion && await hooks.jump(result.source) === false) throw new Error('来源文件或页码已不可用，请重新打开论文。');
      } catch (err) { if (active === state && state.version === version && state.sourceVersion === sourceVersion) { state.error = '无法定位原文：' + err.message; updateStatus(); } }
      finally { button.disabled = false; }
    }
  }
  function init(options) {
    hooks = options;
    const next = options.panel || document.getElementById('chat-panel');
    if (!next || panel === next) return;
    panel = next;
    panel.addEventListener('click', click);
    panel.addEventListener('input', event => { if (event.target.id === 'chat-input' && active) { active.draft = event.target.value; saveDraft(active); updateStatus(); } });
    panel.addEventListener('submit', event => { if (event.target.id === 'chat-form') { event.preventDefault(); return send(); } });
    panel.addEventListener('keydown', event => { if (event.target.id === 'chat-input' && event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); return send(); } });
    render();
  }
  window.BioChat = { init, open, clear, setContext, askSelection, canLeave: () => true };
})();
