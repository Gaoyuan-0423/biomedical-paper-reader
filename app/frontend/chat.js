(function () {
  'use strict';
  const PAPER_ID = /^[0-9a-f]{32}$/, MESSAGE_ID = /^[a-zA-Z0-9_-]{1,100}$/, SOURCE_ID = /^S[1-9][0-9]*$/;
  const sessions = new Map(), renderedAnswers = new WeakMap();
  let hooks = {}, panel = null, active = null;
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const t = (key, params = {}) => window.BioI18n?.t(key, params) ?? key.replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? '{' + name + '}'));
  const ui = (key, params = {}) => '<span data-i18n="' + esc(key) + '" data-i18n-params="' + esc(JSON.stringify(params)) + '">' + esc(t(key, params)) + '</span>';
  const attr = (name, key) => ' data-i18n-' + name + '="' + esc(key) + '" ' + name + '="' + esc(t(key)) + '"';
  function documentLabel(context, fallback = '补充材料') {
    const name = context?.document_name;
    if (context?.document_id === 'main' && (!name || name === '正文' || name === 'Main text')) return ui('正文');
    return name ? esc(name) : ui(fallback);
  }
  function statusText(value) {
    // Only our UI prefixes are localized; opaque server/provider errors remain intact.
    for (const prefix of ['追加失败：', '复制失败：', '对话读取失败：', '无法定位原文：']) {
      if (value.startsWith(prefix)) return t(prefix + '{error}', { error: t(value.slice(prefix.length)) });
    }
    return t(value);
  }
  const path = id => '/api/papers/' + id + '/chat';
  function restoreDraft(id) { try { return window.sessionStorage?.getItem('bioreader.chat.draft.' + id) || ''; } catch (_) { return ''; } }
  function saveDraft(state) {
    try {
      const store = window.sessionStorage;
      store?.setItem('bioreader.chat.draft.' + state.paper.id, state.draft);
      const context = state.draft.trim() && (state.pinned || state.contextRequired) ? { draft: state.draft, materialRevision: state.materialRevision, context: state.pinned, needsContext: !!state.contextRequired } : null;
      store?.setItem('bioreader.chat.draft-context.' + state.paper.id, JSON.stringify(context));
    } catch (_) { /* In-memory draft remains available. */ }
  }
  function restoreContext(paper, draft) {
    if (!draft.trim()) return {};
    try {
      const saved = JSON.parse(window.sessionStorage?.getItem('bioreader.chat.draft-context.' + paper.id) || 'null');
      if (!saved || saved.draft !== draft) return {};
      const ctx = saved.context;
      const documentExists = ctx?.document_id === 'main' ? paper.has_file : (paper.supplements || []).some(item => item.id === ctx?.document_id);
      if (!saved.needsContext && saved.materialRevision === paper.material_revision && documentExists && validContext(ctx) && ctx.page <= 600 && (!ctx.selection || typeof ctx.selection === 'string' && ctx.selection.length <= 12000)) {
        const name = ctx.document_id === 'main' ? '正文' : paper.supplements.find(item => item.id === ctx.document_id)?.filename || '补充材料';
        return { pinned: { paper_id: paper.id, document_id: ctx.document_id, document_name: name, page: ctx.page, selection: ctx.selection || '' } };
      }
      return { contextRequired: true, notice: '上次提问所用材料或位置已失效。请确认 PDF 位置后点击“使用当前页”，或重新选择原文。' };
    } catch (_) { return {}; }
  }
  function contextFor(state) { return state.pinned || state.live; }
  function validContext(ctx) { return ctx && (ctx.document_id === 'main' || PAPER_ID.test(ctx.document_id || '')) && Number.isInteger(ctx.page) && ctx.page >= 1; }
  function sourceMap(message) { return new Map((Array.isArray(message.sources) ? message.sources : []).filter(s => SOURCE_ID.test(s.id || '')).map(s => [s.id, s])); }
  function sourceButton(message, source, label, labelHTML) {
    if (!MESSAGE_ID.test(message.id || '')) return esc(label);
    return '<button type="button" class="chat-source" data-chat-action="source" data-chat-message="' + esc(message.id) + '" data-chat-source="' + esc(source.id) + '">' + (labelHTML ?? esc(label)) + '</button>';
  }
  // Render a small, explicit Markdown vocabulary. Model HTML is always text;
  // citations are introduced only in prose, never inside links or code.
  function markdownText(value) {
    const named = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", '#39': "'" };
    return String(value ?? '').replace(/&(#x[\da-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (entity, name) => {
      if (name[0] !== '#') return named[name.toLowerCase()] || entity;
      const number = name[1].toLowerCase() === 'x' ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
      return number > 0 && number <= 0x10ffff && !(number >= 0xd800 && number <= 0xdfff) ? String.fromCodePoint(number) : entity;
    });
  }
  function answerHTML(message, sources) {
    if (message.status !== 'completed' || !window.marked?.lexer) return esc(message.content);
    const previous = renderedAnswers.get(message);
    if (previous && previous.content === message.content && previous.sources === message.sources && previous.locale === window.BioI18n?.getLocale()) return previous.html;
    function prose(text, citations) {
      const safe = esc(markdownText(text));
      return citations ? safe.replace(/\[(S[1-9][0-9]*)\]/g, (label, id) => sources.has(id) ? sourceButton(message, sources.get(id), label) : label) : safe;
    }
    function render(tokens, citations = true, depth = 0) {
      return (tokens || []).map(token => {
        if (depth > 32) return esc(token.raw || token.text || '');
        const children = (allow = citations) => render(token.tokens, allow, depth + 1);
        switch (token.type) {
          case 'space': return '';
          case 'text': return token.tokens ? children() : prose(token.text, citations);
          case 'escape': return esc(token.text);
          case 'html': return esc(token.raw || token.text);
          case 'paragraph': return '<p>' + children() + '</p>';
          case 'heading': { const level = Math.max(2, Math.min(6, Number(token.depth) || 2)); return '<h' + level + '>' + children() + '</h' + level + '>'; }
          case 'strong': return '<strong>' + children() + '</strong>';
          case 'em': return '<em>' + children() + '</em>';
          case 'del': return '<del>' + children() + '</del>';
          case 'br': return '<br>';
          case 'hr': return '<hr>';
          case 'code': return '<pre><code>' + esc(token.text) + '</code></pre>';
          case 'codespan': return '<code>' + esc(markdownText(token.text)) + '</code>';
          case 'blockquote': return '<blockquote>' + children() + '</blockquote>';
          case 'list': {
            const tag = token.ordered ? 'ol' : 'ul';
            const start = Number.isSafeInteger(token.start) && token.start > 0 ? ' start="' + token.start + '"' : '';
            return '<' + tag + (token.ordered ? start : '') + '>' + (token.items || []).map(item => '<li>' + (item.task ? (item.checked ? '☑ ' : '☐ ') : '') + render(item.tokens, citations, depth + 1) + '</li>').join('') + '</' + tag + '>';
          }
          case 'table': {
            const cells = (row, tag) => '<tr>' + row.map(cell => '<' + tag + '>' + render(cell.tokens, citations, depth + 1) + '</' + tag + '>').join('') + '</tr>';
            return '<div class="chat-table-scroll" tabindex="0" role="region" ' + attr('aria-label', '回答中的表格') + '><table><thead>' + cells(token.header || [], 'th') + '</thead><tbody>' + (token.rows || []).map(row => cells(row, 'td')).join('') + '</tbody></table></div>';
          }
          case 'link': {
            const label = children(false) || prose(token.text, false);
            try {
              const url = new URL(markdownText(token.href));
              if (['https:', 'http:'].includes(url.protocol) && !url.username && !url.password) return '<a href="' + esc(url.href) + '" target="_blank" rel="noopener noreferrer">' + label + '</a>';
            } catch (_) { /* Invalid and local URLs remain plain labels. */ }
            return label;
          }
          case 'image': return prose(token.text, false);
          default: return esc(token.raw || token.text || '');
        }
      }).join('');
    }
    let html;
    try { html = render(window.marked.lexer(String(message.content || ''), { gfm: true, breaks: false })); }
    catch (_) { html = '<pre>' + esc(message.content) + '</pre>'; }
    renderedAnswers.set(message, { content: message.content, sources: message.sources, locale: window.BioI18n?.getLocale(), html });
    return html;
  }
  function answerText(message) {
    const sources = [...sourceMap(message).values()];
    return String(message.content || '') + (sources.length ? '\n\n原文来源\n' + sources.map(source => '[' + source.id + '] ' + (source.document_name || '原文') + ' · PDF 第 ' + source.page + ' 页\n' + String(source.quote || '')).join('\n\n') : '');
  }
  function messageActions(message) {
    if (message.role !== 'assistant' || message.status !== 'completed' || !String(message.content || '').trim() || !MESSAGE_ID.test(message.id || '')) return '';
    const busy = active?.stream || active?.loading || active?.loadingEarlier;
    const id = esc(message.id), appended = active?.appended?.has(message.id), appending = active?.appending?.has(message.id);
    return '<div class="chat-message-actions">' +
      (hooks.copyText ? '<button class="btn btn-ghost" type="button" data-chat-action="copy" data-chat-message="' + id + '"' + (busy || active?.copying?.has(message.id) ? ' disabled' : '') + '>' + ui('复制回答与来源') + '</button>' : '') +
      (hooks.appendNote ? '<button class="btn btn-ghost" type="button" data-chat-action="append" data-chat-message="' + id + '"' + (busy || appended || appending ? ' disabled' : '') + '>' + ui(appended ? '已追加到笔记' : appending ? '正在保存…' : '追加到笔记') + '</button>' : '') + '</div>';
  }
  function prepareQuestion(action) {
    const state = active, ctx = state && contextFor(state);
    if (!state || state.loading || state.stream || !validContext(ctx)) return;
    if (state.draft.trim()) {
      state.notice = '输入框已有问题，请先发送或清空，再使用快捷提问。';
      updateStatus(); panel.querySelector('#chat-input')?.focus(); return;
    }
    const scope = ctx.selection ? '当前选中的原文' : (ctx.document_name || '当前 PDF') + '第 ' + ctx.page + ' 页';
    const prompts = {
      'quick-page': '请解释' + scope + '的主要意思，列出关键概念，专业术语保留英文。',
      'quick-methods': '请梳理' + scope + '中明确描述的方法：样品、分组、处理步骤及关键参数；未在所提供原文中出现的内容请标为未报告。',
      'quick-limits': '请检查' + scope + '中结论的证据与局限，区分原文明示的局限和你的推断，不把当前片段未提及的内容当作整篇论文没有报告。',
    };
    if (!prompts[action]) return;
    state.draft = prompts[action] + ' 仅根据本次提供的原文片段回答并标注来源，材料不足时请说明。';
    state.pinned = { ...ctx }; state.contextRequired = false; state.notice = '问题已填入，可修改后发送。';
    saveDraft(state);
    const input = panel.querySelector('#chat-input'); if (input) { input.value = state.draft; input.focus(); }
    updateStatus();
  }
  async function useAnswer(action, id) {
    const state = active;
    if (!state || state.stream || state.loading || state.loadingEarlier) return;
    const message = state.messages.find(item => item.id === id);
    if (!message || message.role !== 'assistant' || message.status !== 'completed' || !String(message.content || '').trim() || !MESSAGE_ID.test(message.id || '')) return;
    const appending = action === 'append', hook = appending ? hooks.appendNote : hooks.copyText;
    if (!hook || (appending && state.appended?.has(id))) return;
    const busy = appending ? (state.appending ||= new Set()) : (state.copying ||= new Set());
    if (busy.has(id)) return;
    const version = state.version;
    busy.add(id); state.notice = ''; updateHistory(); updateStatus();
    try {
      const result = await (appending ? hook({ paperId: state.paper.id, message }) : hook(answerText(message)));
      if (appending && result !== true) throw new Error('笔记尚未保存，请重试。');
      if (appending) (state.appended ||= new Set()).add(id);
      if (active === state && state.version === version) state.notice = appending ? '回答及原文来源已追加到笔记。' : '已复制回答及原文来源。';
    } catch (err) {
      if (active === state && state.version === version) state.notice = (appending ? '追加失败：' : '复制失败：') + (err.message || String(err));
    } finally {
      busy.delete(id);
      if (active === state && state.version === version) { updateHistory(); updateStatus(); }
    }
  }
  function messageHTML(message) {
    const user = message.role === 'user', sources = sourceMap(message);
    const content = user ? esc(message.content) : answerHTML(message, sources);
    const status = { pending: active?.stream?.assistantId === message.id ? '正在回答…' : '上一轮回复仍在结束或保存，可刷新对话查看状态。', interrupted: '回复已中断，已收到的内容保留。', error: '本次回复未完成，已收到的内容保留。' }[message.status] || '';
    const questionDocument = message.document_name || (message.document_id === 'main' ? '正文' : active?.paper.supplements?.find(d => d.id === message.document_id)?.filename || '');
    return '<article class="chat-message chat-message-' + (user ? 'user' : 'assistant') + '" data-chat-message-id="' + esc(message.id) + '"><div class="chat-role">' + ui(user ? '你' : '阅读助手') + '</div><div class="chat-content' + (!user && message.status === 'completed' && window.marked?.lexer ? ' chat-markdown' : '') + '">' + content + '</div>' +
      (user && message.selection ? '<details class="chat-sources chat-selected-question"><summary>' + ui('当时选中的原文') + ' · ' + documentLabel({ ...message, document_name: questionDocument }) + ' · ' + ui('第 {page} 页', { page: message.page }) + '</summary><blockquote>' + esc(message.selection) + '</blockquote></details>' : '') +
      (!user && status ? '<p class="chat-message-status">' + ui(status) + '</p>' : '') +
      (!user && sources.size ? '<details class="chat-sources"><summary>' + ui('原文来源 · {count}', { count: sources.size }) + '</summary>' + [...sources.values()].map(s => '<div>' + sourceButton(message, s, '', documentLabel(s, '原文') + ' · ' + ui('第 {page} 页', { page: s.page }) + ' · [' + esc(s.id) + ']') + '<blockquote>' + esc(s.quote) + '</blockquote></div>').join('') + '</details>' : '') + messageActions(message) + '</article>';
  }
  function updateContext() {
    const target = panel?.querySelector('#chat-context');
    if (!target || !active) return;
    const ctx = contextFor(active), valid = validContext(ctx);
    target.innerHTML = '<span class="chat-context-chip">' + (valid ? documentLabel(ctx) + ' · ' + ui('PDF 第 {page} 页', { page: ctx.page }) : ui('请选择可阅读的 PDF')) + '</span>' +
      (ctx?.selection ? '<div class="chat-selection"><span title="' + esc(ctx.selection) + '">' + ui('选区：') + esc(ctx.selection.slice(0, 180)) + (ctx.selection.length > 180 ? '…' : '') + '</span><button type="button" class="btn btn-ghost" data-chat-action="clear-selection">' + ui('取消选区') + '</button></div>' : '') +
      (active.contextRequired ? '<div class="chat-context-pinned">' + ui('请重新确认提问位置') + '<button type="button" class="btn" data-chat-action="use-current-context"' + (!valid ? ' disabled' : '') + '>' + ui('使用当前页') + '</button></div>' : '') +
      (active.pinned && !ctx?.selection ? '<div class="chat-context-pinned">' + ui('提问位置已固定') + '<button type="button" class="btn btn-ghost" data-chat-action="clear-selection">' + ui('跟随当前页') + '</button></div>' : '') +
      (ctx?.selection ? '<div class="chat-quick"><button type="button" class="btn" data-chat-action="explain"' + (active.stream || active.loading ? ' disabled' : '') + '>' + ui('解释选区') + '</button><button type="button" class="btn" data-chat-action="translate"' + (active.stream || active.loading ? ' disabled' : '') + '>' + ui('翻译选区') + '</button></div>' : '') +
      '<div class="chat-quick chat-reading-prompts" ' + attr('aria-label', '快捷提问') + '>' + [['quick-page', ctx?.selection ? '解释选区内容' : '解释本页'], ['quick-methods', ctx?.selection ? '梳理选区方法' : '梳理本页方法'], ['quick-limits', ctx?.selection ? '检查选区局限' : '检查本页局限']].filter(([action]) => !ctx?.selection || action !== 'quick-page').map(([action, label]) => '<button class="btn" type="button" data-chat-action="' + action + '"' + (!valid || active.stream || active.loading ? ' disabled' : '') + '>' + ui(label) + '</button>').join('') + '</div>';
  }
  function updateStatus() {
    if (!active || !panel) return;
    const state = active, box = panel.querySelector('#chat-feedback');
    if (box) box.innerHTML = (state.error ? '<p class="chat-error" role="alert">' + esc(statusText(state.error)) + '</p>' : '') + (state.notice ? '<p class="chat-notice" role="status">' + esc(statusText(state.notice)) + '</p>' : '') +
      (state.stream ? '<div class="chat-stream-status" role="status">' + ui('正在回答，收到内容后会即时显示。') + '<button class="btn" type="button" data-chat-action="stop">' + ui('停止') + '</button></div>' : state.error && state.lastAttempt ? '<button type="button" class="btn" data-chat-action="retry">' + ui('重试上一问题') + '</button>' : '');
    const send = panel.querySelector('#chat-send');
    if (send) send.disabled = !!state.stream || state.loading || state.contextRequired || !validContext(contextFor(state)) || !state.draft.trim();
    updateContext();
  }
  function updateHistory(prepend = false) {
    const box = panel?.querySelector('#chat-history');
    if (!box || !active) return;
    const bottom = box.scrollHeight - box.scrollTop - box.clientHeight < 70, before = box.scrollHeight, top = box.scrollTop;
    const state = active;
    box.innerHTML = (state.hasMore ? '<button type="button" class="btn chat-earlier" data-chat-action="earlier"' + (state.loadingEarlier || state.stream ? ' disabled' : '') + '>' + ui(state.loadingEarlier ? '正在读取…' : '加载更早对话') + '</button>' : '') +
      (state.loading ? '<p class="chat-empty" role="status">' + ui('正在恢复对话…') + '</p>' : state.messages.length ? state.messages.map(messageHTML).join('') : '<p class="chat-empty">' + ui('可直接提问，或在 PDF 中选中文字后解释、翻译。问答单独保存，不会覆盖精读笔记。') + '</p>');
    if (prepend) box.scrollTop = top + box.scrollHeight - before;
    else if (bottom || state.followBottom) { box.scrollTop = box.scrollHeight; state.followBottom = false; }
  }
  function render() {
    if (!panel) return;
    if (!active) { panel.innerHTML = '<p class="chat-empty">' + ui('打开一篇论文后开始问答。') + '</p>'; return; }
    panel.innerHTML = '<div class="chat-heading"><strong>' + ui('论文问答') + '</strong><button class="btn btn-ghost" type="button" data-chat-action="reload"' + (active.loading || active.stream ? ' disabled' : '') + '>' + ui('刷新对话') + '</button></div><div id="chat-context" class="chat-context"></div><div id="chat-history" class="chat-history" tabindex="0" ' + attr('aria-label', '论文问答历史') + '></div><div id="chat-feedback" class="chat-feedback"></div><form id="chat-form" class="chat-composer"><label for="chat-input">' + ui('关于当前论文的问题') + '</label><textarea id="chat-input" maxlength="6000" rows="3" ' + attr('placeholder', '例如：这项实验用了什么样品？') + (active.loading ? ' disabled' : '') + '>' + esc(active.draft) + '</textarea><div><span class="hint">' + ui('⌘ / Ctrl + Enter 发送') + '</span><button class="btn btn-primary" id="chat-send" type="submit">' + ui('发送') + '</button></div></form>';
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
      state.draft = running.body.message; state.pinned = { ...running.context }; saveDraft(state);
      if (active === state) { const input = panel.querySelector('#chat-input'); if (input) input.value = state.draft; }
    }
    state.notice = message;
    if (active === state) { updateHistory(); updateStatus(); }
  }
  async function open(paper) {
    if (!paper || !PAPER_ID.test(paper.id || '')) { clear(); return; }
    const cached = sessions.get(paper.id);
    if (cached && cached.materialRevision !== paper.material_revision) {
      stop(cached); cached.contextRequired = !!cached.draft.trim() && (!!cached.pinned || !!cached.contextRequired); cached.pinned = null; cached.lastAttempt = null; cached.materialRevision = paper.material_revision; cached.sourceVersion = (cached.sourceVersion || 0) + 1;
      cached.notice = '原文材料已更新，请重新选择要提问的段落。'; saveDraft(cached);
    }
    if (active?.paper.id === paper.id) { active.paper = paper; updateStatus(); return active.loadPromise; }
    if (active) { saveDraft(active); stop(active); ++active.version; active.loading = active.loadingEarlier = false; }
    const draft = restoreDraft(paper.id);
    if (!sessions.has(paper.id)) sessions.set(paper.id, { paper, materialRevision: paper.material_revision, messages: [], draft, live: { document_id: paper.has_file ? 'main' : '', document_name: '正文', page: 1, selection: '' }, pinned: null, version: 0, loading: false, loadingEarlier: false, stream: null, loaded: false, hasMore: false, error: '', notice: '', followBottom: true, ...restoreContext(paper, draft) });
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
    if (context?.selection && validContext(context)) { state.pinned = { ...context }; state.contextRequired = false; saveDraft(state); }
    updateContext(); updateStatus();
    if (state.loadPromise) await state.loadPromise;
    if (active !== state) return;
    if (action === 'ask') { panel.querySelector('#chat-input')?.focus(); return; }
    if (!contextFor(state)?.selection) { hooks.toast?.(t('请先在 PDF 中选择要解释或翻译的文字。'), 'error'); return; }
    await send(action);
  }
  async function send(action = 'ask', retryBody = null) {
    const state = active;
    if (!state || state.stream || state.loading || state.error?.startsWith('对话读取失败')) return;
    const ctx = contextFor(state);
    if (state.contextRequired && !retryBody) { state.notice = '请先重新确认提问位置，再发送保留的问题。'; updateStatus(); return; }
    if (!validContext(ctx) && !retryBody) { hooks.toast?.(t('请先打开有效的 PDF 页。'), 'error'); return; }
    const message = action === 'explain' ? '请解释这段选中的原文。' : action === 'translate' ? '请将这段选中的原文翻译成中文，并保留专业术语。' : state.draft.trim();
    const body = retryBody || { message, document_id: ctx.document_id, page: ctx.page, selection: ctx.selection || '', action };
    if (!body.message.trim()) return;
    const running = { context: { ...ctx }, controller: new AbortController(), assistantId: null, terminal: false, body, sentDraft: !retryBody && action === 'ask' };
    state.stream = running; state.error = ''; state.notice = ''; state.lastAttempt = body; state.followBottom = true;
    if (action === 'ask' && (!retryBody || state.draft.trim() === body.message)) { state.draft = ''; saveDraft(state); const input = panel.querySelector('#chat-input'); if (input) input.value = ''; }
    if (state.pinned && !state.pinned.selection) state.pinned = null;
    updateHistory(); updateStatus();
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
        if (!running.assistantId && !state.draft && action === 'ask') { state.draft = body.message; state.pinned = { ...ctx }; saveDraft(state); const input = panel.querySelector('#chat-input'); if (input) input.value = state.draft; }
      }
    } finally {
      if (state.stream === running) { state.stream = null; if (active === state) { updateHistory(); updateStatus(); } }
    }
  }
  async function click(event) {
    const button = event.target.closest('[data-chat-action]');
    if (!button || button.disabled || !active) return;
    const state = active, action = button.dataset.chatAction;
    if (['quick-page', 'quick-methods', 'quick-limits'].includes(action)) { prepareQuestion(action); return; }
    if (action === 'copy' || action === 'append') { await useAnswer(action, button.dataset.chatMessage); return; }
    if (action === 'stop') { stop(); return; }
    if (action === 'reload') { await load(state); return; }
    if (action === 'earlier') { await load(state, true); return; }
    if (action === 'use-current-context' && validContext(state.live)) { state.pinned = { ...state.live }; state.contextRequired = false; state.notice = '已使用当前 PDF 位置，请检查问题内容后发送。'; saveDraft(state); updateStatus(); return; }
    if (action === 'clear-selection') { state.pinned = null; saveDraft(state); updateStatus(); return; }
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
    panel.addEventListener('input', event => { if (event.target.id === 'chat-input' && active) { active.draft = event.target.value; if (!active.draft.trim()) { active.contextRequired = false; if (active.pinned && !active.pinned.selection) active.pinned = null; } saveDraft(active); updateStatus(); } });
    panel.addEventListener('submit', event => { if (event.target.id === 'chat-form') { event.preventDefault(); return send(); } });
    panel.addEventListener('keydown', event => { if (event.target.id === 'chat-input' && event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); return send(); } });
    render();
  }
  // Do not rebuild history or the composer on a locale change: keep drafts, selection,
  // expanded sources and the live stream. Marked labels are updated by BioI18n.
  window.BioI18n?.onChange(() => { if (active && panel) updateStatus(); });
  window.BioChat = { init, open, clear, setContext, askSelection, canLeave: () => true };
})();
