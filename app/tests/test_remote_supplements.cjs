const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../frontend/remote-supplements.js'), 'utf8');

function element(tag) {
  return { tag, dataset: {}, children: [],
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
  };
}
function setup(hooks) {
  const nodes = {};
  function get(id) { return nodes[id] ||= element('div'); }
  const win = {};
  vm.runInNewContext(script, {window: win, document: {createElement: element, getElementById: get}, URL});
  const ui = win.BioSupplementDownloads;
  ui.init({show() {}, hide() {}, ...hooks});
  const body = get('remote-supplement-body');
  const findButton = text => body.children.find(n => n.textContent === text);
  const boxes = () => body.children.flatMap(row => row.children.flatMap(child =>
    child.tag === 'label' ? child.children.filter(n => n.tag === 'input') : []));
  return {ui, nodes, body, findButton, boxes};
}

test('download starts only after selection and retries only the failed file', async () => {
  const paper = {id:'one', doi:'10.1234/test', supplements: [], supplement_files: []};
  const candidates = ['A','B','C'].map(id => ({id, filename: id + '.csv', article_version:'PMC123.1'}));
  const calls = [];
  let requests = 0, fail = true;
  const h = setup({
    async find() { requests++; return {candidates}; },
    async save(id, key) {
      assert.equal(id, paper.id);
      calls.push(key);
      if (key === 'B' && fail) throw Error('network failure');
      paper.supplement_files.push({candidate_id:key, filename:key + '.csv', mime:'text/csv'});
      return paper;
    },
  });
  h.ui.open(paper);
  assert.equal(requests, 0);
  await h.findButton('查找公开补充材料').onclick();
  assert.equal(requests, 1);
  assert.equal(h.nodes['remote-supplement-save'].disabled, true);
  for (const box of h.boxes().slice(0, 2)) { box.checked = true; box.onchange(); }
  await h.nodes['remote-supplement-save'].onclick();
  assert.deepEqual(calls, ['A', 'B']);
  assert.equal(h.boxes()[0].disabled, true);
  assert.equal(h.boxes()[1].checked, true);
  assert.equal(h.boxes()[2].checked, false);
  assert.equal(h.nodes['remote-supplement-status'].textContent, '下载遇到问题');
  fail = false;
  await h.nodes['remote-supplement-save'].onclick();
  assert.deepEqual(calls, ['A', 'B', 'B']);
  assert.equal(h.nodes['remote-supplement-save'].disabled, true);
});

test('dismissed lookup cannot alter the next paper and saved attachments work offline', async () => {
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  const exported = [];
  const h = setup({find: () => pending, exportFile: (pid, item) => exported.push([pid, item.id])});
  h.ui.open({id:'first', pmid:'123'});
  const old = h.findButton('查找公开补充材料').onclick();
  assert.equal(h.ui.close(), true);
  h.ui.open({id:'second', supplement_files:[{id:'zip', filename:'data.zip', mime:'application/zip'}]});
  finish({candidates:[{id:'stale', filename:'stale.pdf'}]}); await old;
  assert.equal(h.boxes().length, 0);
  assert.equal(h.findButton('查找公开补充材料').disabled, true);
  const download = h.body.children.flatMap(n => n.children).find(n => n.textContent === '导出附件');
  await download.onclick();
  assert.deepEqual(exported, [['second','zip']]);
});
