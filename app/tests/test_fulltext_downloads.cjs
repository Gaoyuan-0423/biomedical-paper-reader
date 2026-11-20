const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const script = fs.readFileSync(path.join(__dirname, '../frontend/fulltext-downloads.js'), 'utf8');
const start = '查找选中论文的全文', retry = '重试未完成项', stop = '停止后续下载';
const tick = () => new Promise(resolve => setImmediate(resolve));
function element(tag) {
  return {tag, dataset: {}, children: [],
    append(...items) { this.children.push(...items); },
    prepend(...items) { this.children.unshift(...items); },
    setAttribute(name, value) { this[name] = value; },
    replaceChildren(...items) { this.children = items; },
  };
}
function descendants(node) {
  return [node, ...node.children.filter(item => typeof item !== 'string').flatMap(descendants)];
}
function text(node) {
  return typeof node === 'string' ? node : [node.textContent || '', ...node.children.map(text)].join('');
}
function button(root, key) {
  return descendants(root).find(el => el.tag === 'button' && el.dataset.i18n === key);
}
function checkbox(root, name) {
  const label = descendants(root).find(el => el.tag === 'label' && text(el) === name);
  assert.ok(label, 'Expected checkbox: ' + name);
  return label.children.find(child => child.tag === 'input');
}
function check(box, selected) {
  assert.ok(!box.disabled);
  box.checked = selected;
  box.onchange();
}
const paper = id => ({id, title: 'Article ' + id, doi: '10.1234/' + id});
function harness() {
  const panel = element('div'), single = element('div'), calls = [], attached = [], toasts = [];
  const window = {BioI18n: {register() {}, t: (key, values = {}) => key.replace(/\{(\w+)\}/g, (_, k) => values[k])}};
  const document = {createElement: element, getElementById: id => id === 'fulltext-single' ? single : null};
  vm.runInNewContext(script, {window, document, URL});
  const ui = window.BioFulltext;
  const hooks = {ready: () => true, show() {}, toast: (...args) => toasts.push(args),
    fetch: (id, scope) => new Promise((resolve, reject) => calls.push({id, scope, resolve, reject})),
    attached: (id, meta) => attached.push([id, meta]),
  };
  ui.init(hooks);
  return {panel, single, calls, attached, ui, hooks, toasts,
    project(scope, papers) { return ui.project(panel, scope, papers); },
  };
}
const called = h => h.calls.map(({id, scope}) => [id, scope]);
const row = (h, id) => descendants(h.panel).find(el => el.className === 'download-item' && text(el).startsWith('Article ' + id));

test('only checked, missing, compatible papers run and the batch waits for each previous response', async () => {
  const h = harness();
  h.project('project-a', [paper('A'), paper('B'), paper('C'),
    {...paper('D'), has_file:true}, {...paper('E'), schema_incompatible:true}]);
  assert.equal(h.calls.length, 0);
  assert.ok(!row(h, 'D') && !row(h, 'E'));
  check(checkbox(h.panel, '全选缺正文论文'), false);
  assert.ok(button(h.panel, start).disabled);
  check(checkbox(h.panel, 'Article A'), true);
  check(checkbox(h.panel, 'Article C'), true);
  assert.ok(checkbox(h.panel, '全选缺正文论文').indeterminate);
  const job = button(h.panel, start).onclick();
  assert.deepEqual(called(h), [['A', 'project-a']]);
  assert.equal(h.ui.busy(), true);
  assert.ok(checkbox(h.panel, 'Article C').disabled);
  h.calls[0].resolve({outcome:'downloaded', paper:{id:'A'}, source:{url:'https://europepmc.org/articles/PMC42/pdf'}});
  await tick();
  assert.deepEqual(called(h), [['A','project-a'], ['C','project-a']]);
  h.calls[1].resolve({outcome:'already_present', paper:{id:'C'}});
  await job;
  assert.equal(h.ui.busy(), false);
  assert.deepEqual(h.attached, [['A',{id:'A'}], ['C',{id:'C'}]]);
  assert.match(text(h.panel), /已处理 2 \/ 2/);
  assert.equal(descendants(row(h,'A')).find(el => el.tag === 'a').href, 'https://europepmc.org/articles/PMC42/pdf');
  assert.equal(button(h.panel, retry), undefined);
});

test('stop finishes the active paper and retry processes only unfinished rows, including interrupted and failed ones', async () => {
  const h = harness();
  h.project('project-b', ['A','B','C'].map(paper));
  const first = button(h.panel, start).onclick();
  button(h.panel, stop).onclick();
  assert.equal(h.calls.length, 1);
  h.calls[0].resolve({outcome:'downloaded', paper:{id:'A'}});
  await first;
  assert.match(text(row(h,'B')), /尚未处理/);
  assert.match(text(h.panel), /已处理 1 \/ 3/);
  const second = button(h.panel, retry).onclick();
  assert.deepEqual(called(h).at(-1), ['B','project-b']);
  h.calls[1].reject(new Error('Index unavailable'));
  await tick();
  assert.deepEqual(called(h).at(-1), ['C','project-b']);
  h.calls[2].resolve({outcome:'needs_review', message:'Check the title'});
  await second;
  assert.match(text(row(h,'B')), /Index unavailable/);
  assert.match(text(row(h,'C')), /需人工核对/);
  const third = button(h.panel, retry).onclick();
  h.calls[3].resolve({outcome:'downloaded'});
  await tick();
  h.calls[4].resolve({outcome:'already_present'});
  await third;
  assert.deepEqual(h.calls.map(c => c.id), ['A','B','C','B','C']);
  assert.equal(button(h.panel, retry), undefined);
});

test('late project results stay scoped, and removed or manually completed papers are excluded from retry', async () => {
  const h = harness();
  h.project('alpha', ['A','B'].map(paper));
  const first = button(h.panel, start).onclick();
  h.project('beta', [paper('C')]);
  h.calls[0].resolve({outcome:'not_found'});
  await tick();
  assert.deepEqual(called(h).at(-1), ['B','alpha']);
  h.calls[1].resolve({outcome:'failed', message:'Temporary error'});
  await first;
  assert.ok(row(h,'C') && !row(h,'A') && !row(h,'B'));
  assert.equal(button(h.panel, retry), undefined);
  h.project('alpha', [paper('B'), paper('D')]);
  assert.ok(!row(h,'A'));
  const next = button(h.panel, retry).onclick();
  assert.deepEqual(called(h).at(-1), ['B','alpha']);
  h.calls[2].resolve({outcome:'failed'});
  await next;
  h.project('alpha', [{...paper('B'), has_file:true}, paper('D')]);
  assert.ok(!row(h,'B') && row(h,'D'));
  assert.equal(button(h.panel, retry), undefined);
});

test('single lookup is explicit, respects pending edits, and shares an in-flight request for the same scope', async () => {
  const h = harness(), p = paper('A');
  h.hooks.ready = () => false;
  h.ui.openSingle(p);
  assert.equal(h.calls.length, 0);
  assert.match(h.toasts[0][0], /先保存/);
  h.hooks.ready = () => true;
  h.ui.openSingle(p);
  assert.deepEqual(called(h), [['A','all']]);
  h.project('all', [p]);
  const batch = button(h.panel, start).onclick();
  assert.equal(h.calls.length, 1);
  h.calls[0].resolve({outcome:'downloaded', paper:{id:'A'}});
  await batch;
  assert.deepEqual(h.attached, [['A',{id:'A'}]]);
  h.ui.openSingle(p);
  assert.match(text(h.single), /正文已下载/);
  assert.equal(h.calls.length, 1);
});
