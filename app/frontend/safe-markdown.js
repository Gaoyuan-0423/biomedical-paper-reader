/* A local renderer for untrusted notes. Never enable raw HTML or remote images. */
(function (global) {
  'use strict';

  const SOURCE = /^note-source:([0-9a-f]{32}):(N[1-9][0-9]{0,4})$/;
  const FIGURE = /^figure:([a-z]\d+)$/;
  const ENTITIES = /&(?!(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]+);)/g;

  function escapeLiteral(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }

  // Marked has already escaped ordinary inline text. Keep its entities, while
  // also escaping text produced inside an HTML/raw-text lexer context.
  function escapeText(value) {
    return String(value ?? '').replace(ENTITIES, '&amp;').replace(/[<>"']/g,
      ch => ({ '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }

  function externalUrl(value) {
    const href = String(value ?? '');
    // Accept an explicit HTTP(S) scheme only. Encoded/relative/control-character
    // variants never become links, regardless of how a browser normalizes them.
    if (!/^https?:\/\//i.test(href) || /[\u0000-\u0020\u007f]/.test(href)) return null;
    try {
      const url = new URL(href);
      return /^https?:$/.test(url.protocol) && url.hostname ? url.href : null;
    } catch (_) { return null; }
  }

  function createParser(marked, observation = false) {
    // The bundled Marked v12 renderer uses positional (href, title, text)
    // arguments. A private instance avoids changing chat/global parser options.
    const renderer = new marked.Renderer();
    // Internal appendix/chat markers are HTML comments. They stay invisible;
    // executable HTML and other tags remain readable, escaped examples.
    renderer.html = value => /^<!--[\s\S]*?-->\s*$/.test(value) ? '' : escapeLiteral(value);
    renderer.text = value => escapeText(value);
    renderer.codespan = value => '<code>' + escapeText(value) + '</code>';
    renderer.code = (value, info) => {
      const language = String(info || '').split(/\s/, 1)[0];
      const attr = /^[A-Za-z0-9_+-]{1,40}$/.test(language) ? ' class="language-' + language + '"' : '';
      return '<pre><code' + attr + '>' + escapeLiteral(String(value).replace(/\n$/, '') + '\n') + '</code></pre>\n';
    };
    renderer.link = (href, title, text) => {
      const source = SOURCE.exec(String(href || ''));
      if (source) {
        if (observation) return text;
        // Match the canonical backend citation, including the visible ID.
        return text === source[2]
          ? '<a href="' + href + '" data-bio-note-source="true">' + text + '</a>' : text;
      }
      const url = externalUrl(href);
      if (!url) return text;
      return '<a href="' + escapeText(url) + '" target="_blank" rel="noopener noreferrer"' +
        (title ? ' title="' + escapeText(title) + '"' : '') + '>' + text + '</a>';
    };
    renderer.image = (href, title, text) => {
      // Observations have no authenticated figure/citation map. Keep the model's
      // image label as text instead of fetching or resolving an invented image.
      if (observation) return escapeText(text || '');
      const figure = FIGURE.exec(String(href || ''));
      if (!figure) return escapeText(text || '图片');
      return '<img src="figure:' + figure[1] + '" data-bio-figure="' + figure[1] + '" alt="' + escapeText(text) + '"' +
        (title ? ' title="' + escapeText(title) + '"' : '') + '>';
    };
    return new marked.Marked({ renderer, gfm: true, breaks: false, async: false });
  }

  let parser = null, observationParser = null, parserLibrary = null;
  function parse(markdown, options = {}) {
    const text = String(markdown ?? '');
    const library = global.marked;
    if (!library || typeof library.Marked !== 'function' || typeof library.Renderer !== 'function') {
      return '<pre>' + escapeLiteral(text) + '</pre>';
    }
    try {
      if (parserLibrary !== library) {
        parser = createParser(library);
        observationParser = createParser(library, true);
        parserLibrary = library;
      }
      const result = (options?.profile === 'observation' ? observationParser : parser).parse(text);
      return typeof result === 'string' ? result : '<pre>' + escapeLiteral(text) + '</pre>';
    } catch (_) {
      return '<pre>' + escapeLiteral(text) + '</pre>';
    }
  }

  global.BioMarkdown = { parse, parseObservation: markdown => parse(markdown, { profile: 'observation' }) };
})(window);
