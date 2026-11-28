const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../frontend/pdf-find.js'), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));

function harness() {
  const elements = new Map(), listeners = {}, timers = new Map(), jumps = [];
  let timerId = 0, modal = false;
  function element(id) {
    if (!elements.has(id)) elements.set(id, {hidden: false, value: '', attrs: {},
      addEventListener(event, listener) { this[event] = listener; },
      setAttribute(key, value) { this.attrs[key] = value; },
      focus() { this.focused = true; }, select() {}, closest(selector) { return selector === '#pdf-pane'; },
    });
    return elements.get(id);
  }
  const document = {body: {}, getElementById: element, addEventListener(event, fn) { listeners[event] = fn; },
    querySelector() { return modal ? {} : null; }};
  const window = {};
  vm.runInNewContext(code, {document, window,
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; }, clearTimeout(id) { timers.delete(id); }});
  const find = window.BioPdfFind;
  find.init({showPage: page => jumps.push(page), layer: () => null});
  const input = element('pdf-find-query');
  function key(target, name, options = {}) {
    const event = {target, key: name, ...options, preventDefault() { this.defaultPrevented = true; }};
    if (target === input) input.keydown(event);
    listeners.keydown(event);
    return !!event.defaultPrevented;
  }
  async function type(query) {
    input.value = query; input.input();
    for (const fn of [...timers.values()]) fn();
    await settle();
  }
  return {find, element, key, input, type, jumps, document, listeners,
    setModal(value) { modal = value; }, count: () => element('pdf-find-count').textContent};
}
function pdf(strings) {
  const calls = [];
  return {numPages: strings.length, calls, async getPage(number) {
    calls.push(number);
    return {async getTextContent() {
      const raw = await strings[number - 1];
      return {items: raw.split('\n').map((str, i, items) => ({str, hasEOL: i + 1 < items.length}))};
    }};
  }};
}

test('literal matches on all pages count repeated occurrences, wrap and reuse extracted text', async () => {
  const h = harness(), doc = pdf(['IL-6 (A)+, il-6 (a)+','note', '', 'IL-6 (A)+ 微环境','两组\n不同']);
  h.find.setDocument(doc);
  h.element('pdf-find-toggle').onclick();
  await h.type('Il-6 (A)+');
  assert.equal(h.count(), '1 / 3');
  h.key(h.input, 'Enter'); assert.equal(h.count(), '2 / 3');
  h.element('pdf-find-next').onclick(); assert.equal(h.count(), '3 / 3');
  h.key(h.input, 'Enter'); h.key(h.input, 'Enter', {shiftKey: true});
  assert.deepEqual(h.jumps, [1, 1, 4, 1, 4]);
  await h.type('微环境'); assert.equal(h.count(), '1 / 1'); assert.equal(h.jumps.at(-1), 4);
  await h.type('两组 不同'); assert.equal(h.count(), '1 / 1'); assert.equal(h.jumps.at(-1), 5);
  await h.type('[missing]'); assert.equal(h.count(), '未找到匹配项');
  assert.deepEqual(doc.calls, [1,2,3,4,5]);
});

test('switching or closing a PDF invalidates unfinished text extraction and navigation', async () => {
  const h = harness();
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  h.find.setDocument(pdf([pending])); h.element('pdf-find-toggle').onclick();
  await h.type('gene');
  assert.match(h.count(), /正在查找/);
  h.find.setDocument(pdf(['blank', 'new gene']));
  assert.equal(h.element('pdf-find-bar').hidden, true);
  assert.equal(h.input.value, '');
  h.element('pdf-find-toggle').onclick(); await h.type('gene');
  finish('gene gene'); await settle();
  assert.deepEqual(h.jumps, [2]); assert.equal(h.count(), '1 / 1');
  h.key(h.input, 'Escape');
  assert.equal(h.count(), ''); assert.equal(h.element('pdf-find-toggle').focused, true);
  h.find.setDocument(null); assert.equal(h.element('pdf-find-toggle').disabled, true);
});

test('reader shortcut follows focus and canvas clicks, leaves editors and modal searches alone', () => {
  const h = harness();
  h.find.setDocument(pdf(['x']));
  const editor = {closest() { return null; }};
  assert.equal(h.key(editor, 'f', {ctrlKey:true}), false);
  assert.equal(h.key(h.element('pdf-pane'), 'f', {metaKey:true}), true);
  assert.equal(h.element('pdf-find-bar').hidden, false);
  h.key(h.input, 'Escape');
  h.listeners.pointerdown({target: h.element('pdf-page')});
  assert.equal(h.key(h.document.body, 'f', {ctrlKey:true}), true);
  h.listeners.focusin({target:editor});
  assert.equal(h.key(h.document.body, 'f', {metaKey:true}), false);
  h.setModal(true);
  assert.equal(h.key(h.input, 'f', {ctrlKey:true}), false);
});

test('highlight targets the selected occurrence and spans separate PDF text runs', async () => {
  const h = harness(), doc = pdf(['FOO foo']);
  h.find.setDocument(doc); h.element('pdf-find-toggle').onclick(); await h.type('foo');
  const nodes = ['FOO ', 'f', 'oo'].map(data => ({nodeType: 3, data, length:data.length}));
  const children = [], ranges = [];
  const wrapper = {getBoundingClientRect: () => ({left:0, top:0, width:100, height:100}), appendChild:mark => children.push(mark)};
  const layer = {querySelectorAll: () => nodes.map(node => ({tagName:'SPAN',firstChild:node})), closest:() => wrapper};
  h.document.createRange = () => {
    const pair = {};
    ranges.push(pair);
    return {setStart(node, offset) { pair.start = [node.data, offset]; },
      setEnd(node, offset) { pair.end = [node.data, offset]; },
      getClientRects() { return [{left:3,top:4,width:10,height:5}]; }};
  };
  h.document.createElement = () => ({style:{}, setAttribute() {}, scrollIntoView() {}, remove() { this.removed = true; }});
  h.find.paint(doc, 1, layer);
  assert.deepEqual(ranges[0], {start:['FOO ',0], end:['FOO ',3]});
  h.key(h.input, 'Enter');
  h.find.paint(doc, 1, layer);
  assert.deepEqual(ranges.slice(1), [{start:['f',0],end:['f',1]}, {start:['oo',0],end:['oo',2]}]);
  assert.equal(children[0].removed, true);
  h.key(h.input, 'Escape');
  assert.ok(children.every(mark => mark.removed));
});
