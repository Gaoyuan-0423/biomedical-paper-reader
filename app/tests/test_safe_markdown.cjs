const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const marked = require('../frontend/vendor/marked.min.js');
const renderer = fs.readFileSync(path.join(__dirname, '../frontend/safe-markdown.js'), 'utf8');
const exportCode = fs.readFileSync(path.join(__dirname, '../frontend/export.js'), 'utf8');
const snapshot = 'a'.repeat(32);
const reference = 'note-source:' + snapshot + ':N1';

function harness(library = marked) {
  const window = { marked: library };
  const context = vm.createContext({ window, URL });
  vm.runInContext(renderer, context);
  return { context, window, parse: md => window.BioMarkdown.parse(md) };
}

test('real bundled Marked retains headings, lists, tables, emphasis and code', () => {
  const { parse } = harness();
  const html = parse('# H1\n\n## H2\n\n### H3\n\n#### H4\n\n##### H5\n\n###### H6\n\n' +
    '- **strong** and *emphasis*\n- ~~removed~~\n\n1. first\n2. second\n\n' +
    '| A | B |\n| --- | ---: |\n| 1 | 2 |\n\n> quotation\n\n`<tag>`\n\n```python\na < b\n```');
  for (let level = 1; level <= 6; level++) assert.ok(html.includes('<h' + level + '>H' + level + '</h' + level + '>'));
  for (const tag of ['ul', 'ol', 'li', 'table', 'thead', 'tbody', 'th', 'td', 'strong', 'em', 'del', 'blockquote', 'pre']) {
    assert.match(html, new RegExp('<' + tag + '(?:>| )'));
  }
  assert.match(html, /<code>&lt;tag&gt;<\/code>/);
  assert.match(html, /class="language-python">a &lt; b/);
});

test('raw HTML and forged citation anchors are text, never active elements', () => {
  const { parse } = harness();
  const html = parse('<img src=x onerror="alert(1)">\n<script>alert(2)</script>\n' +
    '<iframe srcdoc="<script>alert(3)</script>"></iframe>\n' +
    '<a href="' + reference + '" data-bio-note-source="true">N1</a>');
  assert.doesNotMatch(html, /<(?:img|script|iframe|a)\b/i);
  assert.match(html, /&lt;img/);
  assert.match(html, /&lt;script/);
  assert.match(html, /&lt;a href=/);
});

test('internal HTML comment markers remain hidden while code examples remain visible', () => {
  const { parse } = harness();
  const marker = '<!-- bioreader-chat:message_1 -->';
  const html = parse(marker + '\n\nVisible answer\n\n`' + marker + '`');
  assert.match(html, /<p>Visible answer<\/p>/);
  assert.equal((html.match(/bioreader-chat/g) || []).length, 1);
  assert.match(html, /<code>&lt;!-- bioreader-chat:message_1 --&gt;<\/code>/);
});

test('dangerous, encoded, relative and control-character link targets stay inert', () => {
  const { parse } = harness();
  const targets = ['javascript:alert(1)', 'jav&#x61;script:alert(1)', 'javascript&#58;alert(1)',
    'java\tscript:alert(1)', 'data:text/html;base64,PHNjcmlwdD4=', 'vbscript:alert(1)',
    'file:///tmp/private', '//example.org/path', '/api/settings', 'https://example.org/\u0001bad'];
  for (const target of targets) assert.doesNotMatch(parse('[click](' + target + ')'), /<a\b/, target);
});

test('HTTP(S) links preserve text and use safe external-link attributes', () => {
  const { parse } = harness();
  const html = parse('[**PubMed**](https://pubmed.ncbi.nlm.nih.gov/123/?x=1&y=2 "paper")\n\n<http://example.org/>');
  assert.match(html, /href="https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/123\/\?x=1&amp;y=2"/);
  assert.match(html, /target="_blank" rel="noopener noreferrer"/);
  assert.match(html, /<strong>PubMed<\/strong>/);
  assert.match(html, /href="http:\/\/example\.org\/"/);
});

test('only validated local figure images can create img elements', () => {
  const { parse } = harness();
  const html = parse('![Figure 1](figure:f001 "caption")\n\n![supplement](figure:s20)');
  assert.match(html, /<img src="figure:f001" data-bio-figure="f001" alt="Figure 1" title="caption">/);
  assert.match(html, /data-bio-figure="s20"/);
  for (const target of ['https://tracker.example/pixel', '//tracker.example/pixel', 'data:image/png;base64,AA==',
    'data:image/svg+xml,x', 'blob:https://example.org/a', '/api/private', 'figure:../f001', 'figure:f001?x=1']) {
    const blocked = parse('![image label](' + target + ')');
    assert.doesNotMatch(blocked, /<img\b/, target);
    assert.match(blocked, /image label/);
  }
});

test('citation links require a canonical generation, ID and matching label', () => {
  const { parse } = harness();
  assert.equal(parse('[N1](' + reference + ')').trim(),
    '<p><a href="' + reference + '" data-bio-note-source="true">N1</a></p>');
  for (const markdown of ['[N2](' + reference + ')', '[custom](' + reference + ')',
    '[N0](note-source:' + snapshot + ':N0)', '[N100000](note-source:' + snapshot + ':N100000)',
    '[N1](note-source:abc:N1)', '[N1](' + reference + '?x=1)', '[N1](' + reference + '#x)']) {
    assert.doesNotMatch(parse(markdown), /<a\b/, markdown);
  }
});

test('code and HTML examples cannot acquire figure or citation DOM privileges', () => {
  const { parse } = harness();
  const sample = '[N1](' + reference + ') ![Figure](figure:f001)';
  const html = parse('`' + sample + '`\n\n```html\n' + sample + '\n<img src=x onerror=alert(1)>\n```\n\n' +
    '    ' + sample + '\n');
  assert.doesNotMatch(html, /<(?:a|img)\b/);
  assert.match(html, /<code>/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test('attribute quotes, entities and HTML raw-text contexts cannot introduce handlers', () => {
  const { parse } = harness();
  const html = parse('![&quot; onerror=&quot;alert(1)](figure:f001 "&#34; onload=&#34;alert(2)")\n\n' +
    '[label](https://example.org/ "&#34; onclick=&#34;alert(3)")\n\n' +
    '<span><style><img src=x onerror=alert(4)></style></span>');
  assert.doesNotMatch(html, /<(?:style|span)\b/);
  assert.doesNotMatch(html, /<(?:img|a)[^>]*"\s+on(?:error|load|click)\s*=/i);
  assert.equal((html.match(/<img\b/g) || []).length, 1);
  assert.equal((html.match(/<a\b/g) || []).length, 1);
});

test('missing parser fails closed and the shared Marked instance stays unchanged', () => {
  const raw = '<img src=x onerror=alert(1)>';
  const before = marked.parse(raw);
  harness().parse(raw);
  assert.equal(marked.parse(raw), before);
  assert.equal(harness(null).parse(raw), '<pre>&lt;img src=x onerror=alert(1)&gt;</pre>');
});

test('WeChat export inserts only safe Markdown output and also fails closed without it', () => {
  for (const available of [true, false]) {
    const h = harness();
    if (!available) delete h.window.BioMarkdown;
    const created = [];
    h.context.document = {
      head: { appendChild() {} },
      createElement(tag) {
        const element = { innerHTML: '', sheet: { cssRules: [] }, querySelectorAll: () => [],
          appendChild() {}, setAttribute() {}, remove() {}, get outerHTML() { return this.innerHTML; } };
        created.push({ tag, element });
        return element;
      },
    };
    vm.runInContext(exportCode, h.context);
    const result = h.window.WechatExport.render('<img src=x onerror="alert(1)">\n\n# Heading', 'classic-blue');
    assert.doesNotMatch(result, /<img\b/);
    assert.match(result, /&lt;img/);
    const inserted = created.find(item => item.tag === 'section').element.innerHTML;
    assert.equal(inserted, result);
    if (available) assert.match(result, /<h1>Heading<\/h1>/);
  }
});

test('observation profile keeps Markdown but never resolves model-authored images or internal citations', () => {
  const h = harness();
  const html = h.window.BioMarkdown.parseObservation('## Full observation\n\n- **Panel A**\n\n|Group|Value|\n|---|---|\n|A|2|\n\n' +
    '![forged local](figure:f001)\n![remote](https://tracker.invalid/pixel)\n![data](data:image/png;base64,AA)\n' +
    '[N1](' + reference + ') [figure](figure:f001) [unsafe](javascript:alert(1)) ' +
    '[PubMed](https://pubmed.ncbi.nlm.nih.gov/123/)\n<script>alert(1)</script>');
  assert.match(html, /<h2>Full observation<\/h2>/); assert.match(html, /<strong>Panel A<\/strong>/); assert.match(html, /<table>/);
  assert.doesNotMatch(html, /<img\b|<script\b|data-bio-note-source|href="(?:figure:|note-source:|javascript:)/);
  assert.match(html, /forged local/); assert.match(html, /href="https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/123\/"/);
  assert.equal(html, h.window.BioMarkdown.parse('## Full observation\n\n- **Panel A**\n\n|Group|Value|\n|---|---|\n|A|2|\n\n' +
    '![forged local](figure:f001)\n![remote](https://tracker.invalid/pixel)\n![data](data:image/png;base64,AA)\n' +
    '[N1](' + reference + ') [figure](figure:f001) [unsafe](javascript:alert(1)) ' +
    '[PubMed](https://pubmed.ncbi.nlm.nih.gov/123/)\n<script>alert(1)</script>', {profile: 'observation'}));
});

test('observation rendering does not change default note and export figure/citation behavior', () => {
  const h = harness(), md = '![Actual figure](figure:f001)\n\n[N1](' + reference + ')';
  const before = h.parse(md);
  assert.match(before, /<img /); assert.match(before, /data-bio-note-source/);
  h.window.BioMarkdown.parseObservation(md);
  assert.equal(h.parse(md), before);
  assert.match(harness(null).window.BioMarkdown.parseObservation('<img src=x onerror=bad()>'), /&lt;img/);
});
