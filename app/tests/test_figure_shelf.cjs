const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../frontend/figure-shelf.js'), 'utf8');

function element(tag) {
  return {
    tag, dataset: {}, children: [], value: '', listeners: {},
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    setAttribute(key, value) { this[key] = value; },
    addEventListener(name, fn) { this.listeners[name] = fn; },
  };
}
function setup(query) {
  const win = {}, panel = element('div'), opened = [];
  vm.runInNewContext(script, { window: win, document: { createElement: element } });
  win.BioFigureShelf.init({ query, jump: (pid, f) => opened.push([pid, f.id]), image: () => '/figure.png' });
  return { shelf: win.BioFigureShelf, panel, opened };
}
const item = (paper, id, available = true) => ({
  paper_id: paper, id, label: '<script>Fig S1</script>', has_image: available, paper_title: 'Article', tags: ['TAM'],
  source_available: available, document_id: 'supplement', document_name: 'supplement.pdf', page: 4,
});

test('shelf searches explicit project ids, paginates, and opens only the selected available figure', async () => {
  const queries = [];
  const { shelf, panel, opened } = setup(async body => {
    queries.push({...body, paper_ids: Array.from(body.paper_ids)});
    return { figures: [item('paper-A', body.offset ? 's002' : 's001', !body.offset)], total: 2, skipped: 0 };
  });
  await shelf.open(panel, ['paper-A', 'paper-A']);
  const [form,, grid, more] = panel.children;
  assert.deepEqual(queries[0], { paper_ids: ['paper-A'], query: '', offset: 0 });
  assert.equal(grid.children[0].children.find(child => child.tag === 'strong').textContent, '<script>Fig S1</script>');
  assert.equal(grid.children[0].children.some(child => child.tag === 'script'), false);
  await grid.children[0].children.at(-1).listeners.click();
  assert.deepEqual(opened, [['paper-A','s001']]);
  more.listeners.click(); await new Promise(setImmediate);
  assert.equal(queries[1].offset, 1);
  assert.equal(grid.children.length, 2); assert.equal(more.hidden, true);
  assert.equal(grid.children[1].children.some(el => ['img', 'button'].includes(el.tag)), false);
  form.children[0].children[1].value = 'TAM';
  form.listeners.submit({ preventDefault() {} }); await new Promise(setImmediate);
  assert.deepEqual(queries[2], { paper_ids: ['paper-A'], query: 'TAM', offset: 0 });
  assert.equal(grid.children.length, 1);
});

test('switching projects while figures load cannot display a late prior project result', async () => {
  let resolve;
  const pending = new Promise(yes => { resolve = yes; });
  const { shelf, panel } = setup(async body => body.paper_ids[0] === 'A' ? pending : { figures: [], total: 0, skipped: 0 });
  const first = shelf.open(panel, ['A']);
  await shelf.open(panel, []);
  resolve({ figures: [item('A','s001')], total: 1, skipped: 0 }); await first;
  assert.equal(panel.children[2].children.length, 0);
});
