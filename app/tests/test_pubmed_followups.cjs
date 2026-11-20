const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function element(tag) {
  return {tag, dataset:{}, children:[],
    append(...nodes) { for (const n of nodes) { n.parent = this; this.children.push(n); } },
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); },
    replaceWith(node) { this.parent.children[this.parent.children.indexOf(this)] = node; node.parent = this.parent; },
    setAttribute(key,value) { this[key] = value; },
  };
}
function descendants(node) { return [node,...node.children.flatMap(descendants)]; }
function button(node,label) { return descendants(node).find(n => n.tag === 'button' && n.dataset.i18n === label); }
function harness() {
  const window = {BioI18n:{register() {}, t: (key, values={}) => key.replace(/\{(\w+)\}/g,(_,k) => values[k])}};
  const source = fs.readFileSync(path.join(__dirname,'../frontend/saved-pubmed.js'),'utf8');
  vm.runInNewContext(source,{window,document:{createElement:element}});
  return {ui:window.BioPubMed,root:element('div')};
}

test('startup scans saved searches once and continues past an unavailable query', async () => {
  const {ui} = harness(), calls = [];
  const request = async (method,url) => {
    calls.push([method,url]);
    if (method === 'GET') return [{scope:'all',id:'a'},{scope:'b',id:'c'}];
    if (url.includes('/all/')) throw new Error('Temporarily unavailable');
    return {};
  };
  await Promise.all([ui.startup(request), ui.startup(request)]);
  assert.deepEqual(calls,[['GET','/api/pubmed-followups'],['POST','/api/pubmed-searches/all/a/refresh?due_only=true'],
    ['POST','/api/pubmed-searches/b/c/refresh?due_only=true']]);
});

test('pagination, preview and mark-seen use the displayed page and its exact snapshot', async () => {
  const {ui,root} = harness(), calls = [], previews = [];
  const first = {id:'a',query:'local',tracking:true,unseen:['11'],unseen_total:51,page:1,pages:2,result_id:'first'};
  const second = {...first,unseen:['12'],page:2,result_id:'second'};
  const request = async (method,url,body) => {
    calls.push([method,url,body]);
    if (url === '/api/pubmed-searches/x') return [first];
    if (url.endsWith('?page=2')) return second;
    if (url.endsWith('/seen')) return {...first,pages:1,unseen_total:50};
    if (url.endsWith('/tracking')) return {...first,tracking:false};
    throw new Error('Unexpected endpoint');
  };
  await ui.open(root,'x',request,ids => previews.push(Array.from(ids)));
  await button(root,'下一页未看文献').onclick();
  assert.ok(button(root,'上一页未看文献'));
  button(root,'预览本页并选择导入').onclick();
  assert.deepEqual(previews,[['12']]);
  await button(root,'标记本页为已看').onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1))), ['POST','/api/pubmed-searches/x/a/seen',{page:2,result_id:'second'}]);
  assert.equal(button(root,'下一页未看文献'),undefined);
  await button(root,'暂停启动检查').onclick();
  assert.ok(button(root,'恢复启动检查'));
});
