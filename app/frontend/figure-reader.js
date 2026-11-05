/* Read saved figure observations without changing notes or invoking a model. */
(function (global) {
  'use strict';
  const PAPER = /^[0-9a-f]{32}$/, FIGURE = /^[a-z]\d{3,6}$/;
  const tr = (key, params = {}) => global.BioI18n?.t(key, params) ?? key.replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''));
  let hooks = {}, current = null, view = null, previousFocus = null, background = [], unsubscribe = null;
  let zoom = 1, imageWidth = 0, imageHeight = 0, sequence = 0, resizeObserver = null;
  function node(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }
  function button(action, label, symbol) {
    const el = node('button', 'btn', symbol || tr(label));
    el.type = 'button'; el.dataset.figureReaderAction = action;
    el.setAttribute('aria-label', tr(label));
    return el;
  }
  function imagePath(paperId, figureId) {
    return '/api/papers/' + paperId + '/figures/' + figureId + '.png';
  }
  function renderObservation(value) {
    if (global.BioMarkdown?.parseObservation) return global.BioMarkdown.parseObservation(value);
    return '<pre>' + String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])) + '</pre>';
  }
  function fitImage() {
    if (!view || !current) return;
    const width = imageWidth || Number(current.figure.width) || 800;
    const height = imageHeight || Number(current.figure.height) || 600;
    const fit = Math.min(Math.max(1, view.viewport.clientWidth - 32) / width, Math.max(1, view.viewport.clientHeight - 32) / height, 1);
    const scale = fit * zoom;
    view.image.style.width = Math.max(1, Math.round(width * scale)) + 'px';
    view.image.style.height = Math.max(1, Math.round(height * scale)) + 'px';
    view.percent.textContent = Math.round(scale * 100) + '%';
    view.percent.setAttribute('aria-label', tr('图像缩放 {percent}%', { percent: Math.round(scale * 100) }));
    view.zoomOut.disabled = zoom <= 0.25; view.zoomIn.disabled = zoom >= 8;
  }
  function labels() {
    if (!view || !current) return;
    const f = current.figure;
    view.title.textContent = f.label || tr('图片');
    view.source.textContent = tr('{source} · PDF 第 {page} 页', { source: f.document_name || tr('正文'), page: f.page });
    view.notesHeading.textContent = tr('读图笔记');
    view.captionHeading.textContent = tr('原图图注');
    view.emptyTitle.textContent = tr('还没有读图笔记');
    view.emptyCopy.textContent = tr('这张图还没有已保存的读图笔记。可返回图库，点击「读图」生成。');
    view.imageError.textContent = tr('图片暂时无法加载，请重试。');
    view.fit.textContent = tr('适应窗口'); view.fit.setAttribute('aria-label', tr('适应窗口'));
    view.retry.textContent = tr('重新加载图片'); view.retry.setAttribute('aria-label', tr('重新加载图片'));
    view.zoomIn.setAttribute('aria-label', tr('放大图片')); view.zoomOut.setAttribute('aria-label', tr('缩小图片'));
    view.close.setAttribute('aria-label', tr('关闭图文阅读'));
    view.viewport.setAttribute('aria-label', tr('图片区域，可滚动查看放大后的细节'));
    view.notesContent.setAttribute('aria-label', tr('读图笔记与原图图注'));
    if (!f.caption) view.caption.textContent = tr('这张图暂无图注。');
    fitImage();
  }
  function loadImage() {
    if (!view || !current) return;
    const localView = view, token = ++sequence;
    view.root.dataset.imageState = 'loading'; view.error.hidden = true; view.image.hidden = false;
    view.image.onload = () => {
      if (view !== localView || token !== sequence) return;
      imageWidth = localView.image.naturalWidth; imageHeight = localView.image.naturalHeight;
      view.root.dataset.imageState = 'ready'; fitImage();
    };
    view.image.onerror = () => {
      if (view !== localView || token !== sequence) return;
      view.root.dataset.imageState = 'error'; view.image.hidden = true; view.error.hidden = false;
    };
    // The image path comes from validated app IDs, never from Markdown or model text.
    view.image.src = imagePath(current.paperId, current.figureId) + (current.retry ? '?reader-retry=' + current.retry : '');
  }
  function updateFigure(figure) {
    if (!current || !view) return;
    current.figure = { ...figure };
    view.image.alt = typeof figure.label === 'string' ? figure.label : '';
    const observation = typeof figure.observation === 'string' ? figure.observation : '';
    if (view.observation !== observation) {
      const top = view.notesContent.scrollTop;
      view.markdown.innerHTML = observation.trim() ? renderObservation(observation) : '';
      view.observation = observation;
      view.notesContent.scrollTop = top;
    }
    view.root.dataset.notesState = observation.trim() ? 'ready' : 'empty';
    view.markdown.hidden = !observation.trim(); view.empty.hidden = !!observation.trim();
    view.caption.textContent = figure.caption || tr('这张图暂无图注。');
    labels();
  }
  function focusables() {
    return view ? [...view.dialog.querySelectorAll('button, a[href], summary, [tabindex="0"]')].filter(el => !el.disabled && el.getClientRects().length) : [];
  }
  function keepFocus(event) {
    if (view && !view.root.contains(event.target)) view.close.focus();
  }
  function keydown(event) {
    if (!view) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); return; }
    if (event.key !== 'Tab') return;
    const elements = focusables(), first = elements[0], last = elements.at(-1);
    if (!first) return;
    if (event.shiftKey && (document.activeElement === first || !view.dialog.contains(document.activeElement))) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !view.dialog.contains(document.activeElement))) {
      event.preventDefault(); first.focus();
    }
  }
  function close(restoreFocus = true) {
    if (!view) return;
    const closed = current, trigger = previousFocus;
    sequence++; resizeObserver?.disconnect(); resizeObserver = null;
    document.removeEventListener('focusin', keepFocus);
    view.root.remove(); view = null; current = null; previousFocus = null;
    for (const [el, inert] of background) el.inert = inert;
    background = []; document.body.classList.remove('figure-reader-open');
    if (restoreFocus) {
      if (trigger?.isConnected) trigger.focus();
      else hooks.returnFocus?.({ paperId: closed.paperId, figureId: closed.figureId });
    }
  }
  function open(context, trigger = document.activeElement) {
    if (!PAPER.test(context?.paperId || '') || !FIGURE.test(context?.figure?.id || '')) return false;
    close(false);
    current = { paperId: context.paperId, figureId: context.figure.id, materialRevision: context.materialRevision, figure: { ...context.figure }, retry: 0 };
    previousFocus = trigger; zoom = 1; imageWidth = imageHeight = 0;
    const root = node('div', 'figure-reader-backdrop'); root.id = 'figure-reader'; root.dataset.state = 'open';
    const dialog = node('section', 'figure-reader-dialog'); dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-modal', 'true'); dialog.setAttribute('aria-labelledby', 'figure-reader-title');
    const header = node('header', 'figure-reader-header'), heading = node('div', 'figure-reader-heading');
    const title = node('h2'); title.id = 'figure-reader-title';
    const source = node('div', 'figure-reader-source'); source.id = 'figure-reader-source';
    const closeButton = button('close', '关闭图文阅读', '×'); closeButton.classList.add('btn-icon', 'btn-ghost');
    heading.append(title, source); header.append(heading, closeButton);
    const columns = node('div', 'figure-reader-columns'), imagePane = node('section', 'figure-reader-image-pane');
    const toolbar = node('div', 'figure-reader-image-toolbar'), fit = button('fit', '适应窗口'), zoomOut = button('zoom-out', '缩小图片', '−'), zoomIn = button('zoom-in', '放大图片', '+'), percent = node('output', 'figure-reader-zoom');
    toolbar.append(fit, zoomOut, percent, zoomIn);
    const viewport = node('div', 'figure-reader-viewport'); viewport.tabIndex = 0; viewport.id = 'figure-reader-viewport';
    const canvas = node('div', 'figure-reader-canvas'), image = node('img', 'figure-reader-image'); image.id = 'figure-reader-image'; image.draggable = false;
    canvas.append(image); viewport.append(canvas);
    const error = node('div', 'figure-reader-image-error'), imageError = node('p'), retry = button('retry-image', '重新加载图片'); error.hidden = true; error.append(imageError, retry); viewport.append(error);
    imagePane.append(toolbar, viewport);
    const notesPane = node('section', 'figure-reader-notes-pane'), notesHeading = node('h3', 'figure-reader-notes-heading'); notesHeading.id = 'figure-reader-notes-title'; notesPane.setAttribute('aria-labelledby', notesHeading.id);
    const notesContent = node('div', 'figure-reader-notes-content'); notesContent.id = 'figure-reader-notes'; notesContent.tabIndex = 0;
    const markdown = node('div', 'figure-reader-markdown'), empty = node('div', 'figure-reader-empty'), emptyTitle = node('h4'), emptyCopy = node('p');
    markdown.id = 'figure-reader-markdown'; empty.append(emptyTitle, emptyCopy);
    const details = node('details', 'figure-reader-caption'), captionHeading = node('summary'), caption = node('div', 'figure-reader-caption-text'); details.append(captionHeading, caption);
    notesContent.append(markdown, empty, details); notesPane.append(notesHeading, notesContent); columns.append(imagePane, notesPane); dialog.append(header, columns); root.append(dialog);
    view = { root, dialog, title, source, close: closeButton, fit, zoomOut, zoomIn, percent, viewport, image, notesContent, notesHeading, markdown, empty, emptyTitle, emptyCopy, captionHeading, caption, error, imageError, retry, observation: null };
    root.addEventListener('keydown', keydown);
    root.addEventListener('click', event => {
      if (event.target === root) { close(); return; }
      const action = event.target.closest('[data-figure-reader-action]')?.dataset.figureReaderAction;
      if (action === 'close') close();
      else if (action === 'retry-image') { current.retry++; loadImage(); }
      else if (action === 'fit') { zoom = 1; fitImage(); viewport.scrollTop = viewport.scrollLeft = 0; }
      else if (action === 'zoom-in' || action === 'zoom-out') { zoom = Math.max(0.25, Math.min(8, zoom * (action === 'zoom-in' ? 1.25 : 0.8))); fitImage(); }
    });
    document.body.append(root); updateFigure(context.figure); loadImage();
    closeButton.focus();
    background = [...document.body.children].filter(el => el !== root).map(el => [el, el.inert]);
    for (const [el] of background) el.inert = true;
    document.body.classList.add('figure-reader-open'); document.addEventListener('focusin', keepFocus);
    if (global.ResizeObserver) { resizeObserver = new global.ResizeObserver(fitImage); resizeObserver.observe(viewport); }
    return true;
  }
  function sync(context) {
    if (!current) return;
    if (context?.paperId !== current.paperId || context.materialRevision !== current.materialRevision) { close(false); return; }
    const figure = (context.figures || []).find(item => item.id === current.figureId);
    if (!figure) { close(); return; }
    updateFigure(figure);
  }
  function init(options = {}) {
    hooks = options; unsubscribe?.(); unsubscribe = global.BioI18n?.onChange(labels);
  }
  global.BioFigureReader = { init, open, sync, close, isOpen: () => !!view };
})(window);
