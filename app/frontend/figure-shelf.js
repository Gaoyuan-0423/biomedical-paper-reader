/* Project figures are resolved from the current paper stores, never from a second copy of an image. */
(function () {
  'use strict';
  let hooks;
  const sessions = new WeakMap();
  const t = (key, params = {}) => window.BioI18n?.t(key, params) ?? key.replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
  function node(tag, className, value) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (value !== undefined) el.textContent = value;
    return el;
  }
  function ui(tag, key, params = {}, cls = '') {
    const el = node(tag, cls, t(key, params));
    el.dataset.i18n = key;
    if (Object.keys(params).length) el.dataset.i18nParams = JSON.stringify(params);
    return el;
  }
  async function open(container, paperIds, openFigure) {
    const ids = [...new Set(paperIds)];
    if (!hooks || !container) return;
    const session = { count: 0 };
    sessions.set(container, session);
    const form = node('form', 'figure-shelf-search');
    const search = node('input'); search.type = 'search'; search.maxLength = 300;
    const label = ui('span', '搜索收藏图表'); label.className = 'sr-only';
    const labelWrap = node('label', 'figure-shelf-input'); labelWrap.append(label, search);
    search.placeholder = t('论文、图号、图注或图表标签'); search.dataset.i18nPlaceholder = '论文、图号、图注或图表标签';
    const submit = ui('button', '搜索', {}, 'btn'); submit.type = 'submit';
    form.append(labelWrap, submit);
    const status = node('p', 'hint'); status.setAttribute('role', 'status');
    const grid = node('div', 'figure-shelf-grid');
    const more = ui('button', '加载更多', {}, 'btn'); more.type = 'button'; more.hidden = true;
    container.replaceChildren(form, status, grid, more);
    let query = '', offset = 0;
    const live = version => sessions.get(container) === session && session.count === version;
    function card(figure) {
      const article = node('article', 'figure-shelf-card');
      if (figure.has_image) {
        const image = node('img'); image.loading = 'lazy'; image.alt = figure.label;
        image.src = hooks.image(figure.paper_id, figure.id); article.append(image);
      }
      article.append(node('strong', '', figure.label), node('p', '', figure.paper_title));
      const source = node('small');
      source.append(figure.document_id === 'main' ? ui('span', '正文') : node('span', '', figure.document_name), ' · ', ui('span', '第 {page} 页', { page: figure.page }));
      article.append(source);
      if (figure.caption) article.append(node('p', 'fig-caption', figure.caption));
      if (figure.tags?.length) article.append(node('p', 'figure-tags', figure.tags.join(' · ')));
      if (figure.source_available) {
        const button = ui('button', '查看原文', {}, 'btn'); button.type = 'button';
        button.addEventListener('click', async () => {
          button.disabled = true;
          try { await (openFigure || hooks.jump)(figure.paper_id, figure); }
          catch (error) { hooks.toast(error.message, 'error'); }
          finally { button.disabled = false; }
        });
        article.append(button);
      } else article.append(ui('small', '收藏图表的来源已变更或移除。'));
      return article;
    }
    async function load(append) {
      const version = ++session.count;
      if (!append) { query = search.value.trim(); offset = 0; grid.replaceChildren(); }
      more.disabled = true;
      status.replaceChildren(ui('span', '正在读取收藏图表…'));
      try {
        const data = await hooks.query({ paper_ids: ids, query, offset });
        if (!live(version)) return;
        if (!Array.isArray(data.figures) || !Number.isInteger(data.total)) throw new Error(t('读图结果格式无效，请重试。'));
        grid.append(...data.figures.map(card));
        offset += data.figures.length;
        more.hidden = offset >= data.total || !data.figures.length;
        status.replaceChildren(data.total ? ui('span', '找到 {count} 张收藏图表', { count: data.total }) :
          ui('span', query ? '没有匹配的收藏图表。' : '在论文的图库中点击「收藏这张图」，即可在这里查看。'));
        if (data.skipped) status.append(' ', ui('span', '{count} 篇论文已移除或无法读取。', { count: data.skipped }));
      } catch (error) {
        if (!live(version)) return;
        if (!append) more.hidden = true;
        status.textContent = t(error.message);
      } finally { if (live(version)) more.disabled = false; }
    }
    form.addEventListener('submit', event => { event.preventDefault(); void load(false); });
    more.addEventListener('click', () => void load(query === search.value.trim()));
    await load(false);
  }
  window.BioFigureShelf = { init: options => { hooks = options; }, open, jump: (paperId, figure) => hooks.jump(paperId, figure) };
})();
