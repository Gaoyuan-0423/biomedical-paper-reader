/* WeChat (公众号) export engine.
 * Pipeline (mirrors what mdnice does): markdown -> semantic HTML -> theme CSS scoped under
 * `.nice` -> every rule inlined as a `style` attribute (pseudo-elements become real spans)
 * -> rich-text clipboard. WeChat's editor strips <style>, classes, ids and most attributes,
 * but keeps inline styles, so this is the only shape that survives a paste. */
(function (global) {
  'use strict';

  const THEMES = {
    'classic-blue': { name: '经典蓝', primary: '#2b5fd9', soft: '#edf2ff', text: '#3a3f4b', heading: 'underline' },
    'forest-green': { name: '墨绿', primary: '#2f855a', soft: '#eef7f1', text: '#333a36', heading: 'bar' },
    'warm-orange': { name: '暖橙', primary: '#c05621', soft: '#fff4ec', text: '#3f3a37', heading: 'block' },
    'ink-gray': { name: '素雅灰', primary: '#4a5568', soft: '#f4f5f7', text: '#2d3748', heading: 'bar' },
  };

  // Atom One Dark token palette; scoped so it only touches fenced code blocks.
  const CODE_CSS = `
.nice pre code .hljs-comment, .nice pre code .hljs-quote { color: #7f848e; font-style: italic; }
.nice pre code .hljs-doctag, .nice pre code .hljs-keyword, .nice pre code .hljs-formula { color: #c678dd; }
.nice pre code .hljs-section, .nice pre code .hljs-name, .nice pre code .hljs-selector-tag, .nice pre code .hljs-deletion, .nice pre code .hljs-subst { color: #e06c75; }
.nice pre code .hljs-literal { color: #56b6c2; }
.nice pre code .hljs-string, .nice pre code .hljs-regexp, .nice pre code .hljs-addition, .nice pre code .hljs-attribute, .nice pre code .hljs-meta .hljs-string { color: #98c379; }
.nice pre code .hljs-attr, .nice pre code .hljs-variable, .nice pre code .hljs-template-variable, .nice pre code .hljs-type, .nice pre code .hljs-selector-class, .nice pre code .hljs-selector-attr, .nice pre code .hljs-selector-pseudo, .nice pre code .hljs-number { color: #d19a66; }
.nice pre code .hljs-symbol, .nice pre code .hljs-bullet, .nice pre code .hljs-link, .nice pre code .hljs-meta, .nice pre code .hljs-selector-id, .nice pre code .hljs-title { color: #61aeee; }
.nice pre code .hljs-built_in, .nice pre code .hljs-title.class_, .nice pre code .hljs-class .hljs-title { color: #e6c07b; }
.nice pre code .hljs-emphasis { font-style: italic; }
.nice pre code .hljs-strong { font-weight: bold; }
`;

  function headingCss(t) {
    switch (t.heading) {
      case 'bar':
        return `
.nice h2 .content { display: inline-block; padding: 2px 0 2px 12px; border-left: 4px solid ${t.primary}; line-height: 1.3; }
.nice h3 .prefix { display: inline-block; width: 8px; height: 8px; background: ${t.primary}; margin-right: 8px; vertical-align: middle; transform: rotate(45deg); }`;
      case 'block':
        return `
.nice h2 .content { display: inline-block; padding: 4px 14px; background: ${t.primary}; color: #ffffff; border-radius: 4px; line-height: 1.4; font-size: 17px; }
.nice h3 .prefix { display: inline-block; width: 4px; height: 16px; background: ${t.primary}; margin-right: 8px; vertical-align: -2px; border-radius: 2px; }`;
      default:
        return `
.nice h2 .content { display: inline-block; padding-bottom: 4px; border-bottom: 2px solid ${t.primary}; line-height: 1.4; }
.nice h3 .prefix { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: ${t.primary}; margin-right: 8px; vertical-align: middle; }`;
    }
  }

  function themeCss(t) {
    return `
.nice { font-size: 16px; color: ${t.text}; line-height: 1.8; letter-spacing: 0.3px; word-break: break-word; padding: 0 6px; text-align: left; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; }
.nice p { margin: 0 0 18px; text-align: justify; }
.nice h1, .nice h2, .nice h3, .nice h4, .nice h5, .nice h6 { color: ${t.primary}; font-weight: bold; line-height: 1.4; }
.nice h1 { font-size: 22px; text-align: center; margin: 36px 0 24px; }
.nice h1 .content { display: inline-block; padding: 0 10px 8px; border-bottom: 3px solid ${t.primary}; }
.nice h2 { font-size: 19px; margin: 34px 0 18px; }
.nice h3 { font-size: 17px; margin: 28px 0 14px; }
.nice h4 { font-size: 16px; margin: 22px 0 12px; }
.nice h5, .nice h6 { font-size: 15px; margin: 18px 0 10px; }
.nice strong { color: ${t.primary}; font-weight: bold; }
.nice em { font-style: italic; color: ${t.text}; }
.nice del { color: #999999; }
.nice a { color: ${t.primary}; text-decoration: none; border-bottom: 1px solid ${t.primary}; }
.nice .footnote-word { color: ${t.primary}; border-bottom: 1px dashed ${t.primary}; }
.nice .footnote-ref { color: ${t.primary}; font-size: 12px; margin-left: 1px; }
.nice blockquote { margin: 22px 0; padding: 14px 18px 10px; border-left: 4px solid ${t.primary}; background: ${t.soft}; color: #555555; border-radius: 0 8px 8px 0; }
.nice blockquote::before { content: "“"; display: block; font-size: 30px; line-height: 0.6; color: ${t.primary}; margin-bottom: 8px; font-family: Georgia, serif; }
.nice blockquote p { margin: 0 0 8px; text-align: left; }
.nice ul, .nice ol { margin: 0 0 18px; padding-left: 26px; }
.nice li { margin: 6px 0; }
.nice li section { display: inline; }
.nice li p { display: inline; margin: 0; }
.nice code { font-family: Menlo, Consolas, "Courier New", monospace; font-size: 14px; background: ${t.soft}; color: ${t.primary}; padding: 2px 6px; border-radius: 4px; margin: 0 2px; }
.nice pre { margin: 18px 0; padding: 14px 16px; background: #282c34; border-radius: 8px; overflow-x: auto; box-shadow: 0 2px 8px rgba(0,0,0,0.18); }
.nice pre code { display: block; background: transparent; color: #abb2bf; padding: 0; margin: 0; font-size: 13px; line-height: 1.7; white-space: nowrap; border-radius: 0; }
.nice .table-container { overflow-x: auto; margin: 18px 0; }
.nice table { border-collapse: collapse; width: 100%; font-size: 14px; }
.nice th, .nice td { border: 1px solid #dde2ea; padding: 8px 10px; text-align: left; vertical-align: top; line-height: 1.6; }
.nice th { background: ${t.primary}; color: #ffffff; font-weight: bold; }
.nice tr:nth-child(even) td { background: ${t.soft}; }
.nice hr { border: none; height: 1px; margin: 30px 0; background: linear-gradient(to right, rgba(0,0,0,0), ${t.primary}, rgba(0,0,0,0)); }
.nice figure { margin: 22px 0; text-align: center; }
.nice img { max-width: 100%; border-radius: 6px; display: block; margin: 0 auto; }
.nice figcaption { font-size: 13px; color: #888888; margin-top: 8px; text-align: center; }
.nice .footnotes { margin-top: 36px; padding-top: 14px; border-top: 1px solid #e5e7eb; font-size: 13px; color: #666666; }
.nice .footnotes .footnote-item { margin: 0 0 6px; text-align: left; line-height: 1.6; }
.nice .footnote-num { color: ${t.primary}; margin-right: 4px; }
.nice .footnote-url { color: #888888; word-break: break-all; }
${headingCss(t)}`;
  }

  // ---------- DOM shaping ----------

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function markdownToHtml(md) {
    if (global.marked && typeof global.marked.parse === 'function') {
      return global.marked.parse(md || '', { gfm: true, breaks: false });
    }
    return '<pre>' + escapeHtml(md || '') + '</pre>';
  }

  function wrapHeadings(root) {
    root.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach((h) => {
      const inner = h.innerHTML;
      h.removeAttribute('id');
      h.innerHTML = '<span class="prefix"></span><span class="content">' + inner + '</span><span class="suffix"></span>';
    });
  }

  function wrapTables(root) {
    root.querySelectorAll('table').forEach((table) => {
      const box = document.createElement('section');
      box.className = 'table-container';
      table.parentNode.insertBefore(box, table);
      box.appendChild(table);
    });
  }

  function figuresFromImages(root) {
    root.querySelectorAll('p > img:only-child').forEach((img) => {
      const p = img.parentNode;
      if (p.textContent.trim()) return;
      const figure = document.createElement('figure');
      figure.appendChild(img);
      if (img.alt) {
        const cap = document.createElement('figcaption');
        cap.textContent = img.alt;
        figure.appendChild(cap);
      }
      p.parentNode.replaceChild(figure, p);
    });
  }

  function sectionizeListItems(root) {
    root.querySelectorAll('li').forEach((li) => {
      const section = document.createElement('section');
      while (li.firstChild) section.appendChild(li.firstChild);
      li.appendChild(section);
    });
    root.querySelectorAll('li input[type="checkbox"]').forEach((box) => {
      box.parentNode.replaceChild(document.createTextNode(box.checked ? '☑ ' : '☐ '), box);
    });
  }

  function highlightAndHardenCode(root) {
    root.querySelectorAll('pre code').forEach((code) => {
      const source = code.textContent.replace(/\n$/, '');
      const lang = (code.className.match(/language-([\w+-]+)/) || [])[1];
      let html = escapeHtml(source);
      if (global.hljs) {
        try {
          html = lang && global.hljs.getLanguage(lang)
            ? global.hljs.highlight(source, { language: lang }).value
            : global.hljs.highlightAuto(source).value;
        } catch (e) { /* fall back to escaped text */ }
      }
      code.innerHTML = html;
      hardenWhitespace(code);
    });
  }

  // WeChat drops `white-space: pre`, so newlines become <br> and spaces become NBSP.
  function hardenWhitespace(code) {
    const walker = document.createTreeWalker(code, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach((node) => {
      const parts = node.nodeValue.split('\n');
      if (parts.length === 1 && !/[ \t]/.test(node.nodeValue)) return;
      const frag = document.createDocumentFragment();
      parts.forEach((part, i) => {
        if (i > 0) frag.appendChild(document.createElement('br'));
        if (part) frag.appendChild(document.createTextNode(part.replace(/\t/g, '    ').replace(/ /g, '\u00a0')));
      });
      node.parentNode.replaceChild(frag, node);
    });
  }

  // 公众号 strips external links; keep the text and move the URL into a footnote list.
  function linksToFootnotes(root) {
    const entries = [];
    const indexByHref = new Map();
    root.querySelectorAll('a[href]').forEach((a) => {
      const href = a.getAttribute('href') || '';
      const text = a.textContent.trim();
      if (/^https?:\/\/mp\.weixin\.qq\.com\//i.test(href)) return;
      if (!/^https?:\/\//i.test(href)) {
        a.replaceWith(document.createTextNode(a.textContent));
        return;
      }
      let n = indexByHref.get(href);
      if (!n) {
        n = entries.length + 1;
        indexByHref.set(href, n);
        entries.push({ n, text: text || href, href });
      }
      const word = document.createElement('span');
      word.className = 'footnote-word';
      word.innerHTML = a.innerHTML;
      const sup = document.createElement('sup');
      sup.className = 'footnote-ref';
      sup.textContent = '[' + n + ']';
      a.replaceWith(word, sup);
    });
    if (!entries.length) return;
    const box = document.createElement('section');
    box.className = 'footnotes';
    entries.forEach((e) => {
      const p = document.createElement('p');
      p.className = 'footnote-item';
      const num = document.createElement('span');
      num.className = 'footnote-num';
      num.textContent = '[' + e.n + ']';
      const label = document.createElement('span');
      label.className = 'footnote-text';
      label.textContent = e.text + (e.text === e.href ? '' : ': ');
      const url = document.createElement('span');
      url.className = 'footnote-url';
      url.textContent = e.text === e.href ? '' : e.href;
      p.append(num, label, url);
      box.appendChild(p);
    });
    root.appendChild(box);
  }

  // ---------- CSS inlining ----------

  function inlineStyles(host, cssText) {
    const style = document.createElement('style');
    style.textContent = cssText;
    document.head.appendChild(style);
    try {
      Array.from(style.sheet.cssRules).forEach((rule) => {
        if (!(rule instanceof CSSStyleRule)) return;
        rule.selectorText.split(',').forEach((raw) => {
          const selector = raw.trim();
          const pseudo = selector.match(/^(.*?)::?(before|after)$/);
          const base = pseudo ? pseudo[1].trim() : selector;
          let targets;
          try { targets = host.querySelectorAll(base); } catch (e) { return; }
          targets.forEach((el) => (pseudo ? applyPseudo(el, pseudo[2], rule.style) : applyDeclarations(el, rule.style)));
        });
      });
    } finally {
      style.remove();
    }
  }

  function applyDeclarations(el, style, skip) {
    for (let i = 0; i < style.length; i++) {
      const prop = style[i];
      if (prop === skip) continue;
      el.style.setProperty(prop, style.getPropertyValue(prop), style.getPropertyPriority(prop));
    }
  }

  function applyPseudo(el, which, style) {
    let content = style.getPropertyValue('content').trim();
    if (content === 'none' || content === 'normal') content = '';
    content = content.replace(/^["']|["']$/g, '');
    const span = document.createElement('span');
    span.className = 'pseudo-' + which;
    span.textContent = content;
    applyDeclarations(span, style, 'content');
    if (which === 'before') el.insertBefore(span, el.firstChild);
    else el.appendChild(span);
  }

  // ---------- public API ----------

  function render(markdown, themeKey) {
    const theme = THEMES[themeKey] || THEMES['classic-blue'];
    const host = document.createElement('div');
    const root = document.createElement('section');
    root.className = 'nice';
    root.setAttribute('data-tool', 'biomedical-paper-reader');
    root.innerHTML = markdownToHtml(markdown);
    host.appendChild(root);

    wrapHeadings(root);
    wrapTables(root);
    figuresFromImages(root);
    sectionizeListItems(root);
    highlightAndHardenCode(root);
    linksToFootnotes(root);
    inlineStyles(host, themeCss(theme) + CODE_CSS);

    return root.outerHTML;
  }

  function plainTextOf(html) {
    const div = document.createElement('div');
    div.innerHTML = html;
    return div.innerText || div.textContent || '';
  }

  async function copy(html) {
    const plain = plainTextOf(html);
    if (navigator.clipboard && global.ClipboardItem) {
      try {
        await navigator.clipboard.write([
          new ClipboardItem({
            'text/html': new Blob([html], { type: 'text/html' }),
            'text/plain': new Blob([plain], { type: 'text/plain' }),
          }),
        ]);
        return true;
      } catch (e) { /* fall through to the execCommand path */ }
    }
    return legacyCopy(html, plain);
  }

  function legacyCopy(html, plain) {
    let ok = false;
    const handler = (e) => {
      e.preventDefault();
      e.clipboardData.setData('text/html', html);
      e.clipboardData.setData('text/plain', plain);
      ok = true;
    };
    const ta = document.createElement('textarea');
    ta.value = ' ';
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    document.addEventListener('copy', handler);
    try { document.execCommand('copy'); } finally {
      document.removeEventListener('copy', handler);
      ta.remove();
    }
    return ok;
  }

  function wrapDocument(html, title) {
    return '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><title>' + escapeHtml(title || '笔记') +
      '</title></head><body style="margin:0;padding:24px;background:#ffffff;">' + html + '</body></html>';
  }

  global.WechatExport = {
    themes: Object.entries(THEMES).map(([key, t]) => ({ key, name: t.name })),
    render,
    copy,
    wrapDocument,
  };
})(window);
