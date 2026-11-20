const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const frontend = path.join(__dirname,'../frontend');
const marked = require(path.join(frontend,'vendor/marked.min.js'));
function selection(raw,index,original,wordOffset=0) {
  function paragraphs(html) {
    return [...html.matchAll(/<p>(.*?)<\/p>/gs)].map(m => ({tagName:'P',textContent:m[1].replace(/<[^>]*>/g,'')}));
  }
  const window = {marked};
  const document = {createElement: () => {
    const template = {content:{querySelectorAll:() => paragraphs(template.innerHTML)}};
    return template;
  }};
  for (const file of ['safe-markdown.js','report-review.js'])
    vm.runInNewContext(fs.readFileSync(path.join(frontend,file),'utf8'),{window,document});
  const blocks = paragraphs(window.BioMarkdown.parse(raw)), block = blocks[index];
  const preview = {querySelectorAll:()=>blocks,contains:b=>blocks.includes(b)};
  const node = {nodeType:3,parentElement:{closest:()=>block}};
  const offset=block.textContent.indexOf(original,wordOffset);
  assert.ok(offset>=0);
  const range = {startContainer:node,endContainer:node,startOffset:offset,endOffset:offset+original.length,
    toString:()=>original,cloneRange() {
      return {selectNodeContents() {},setStart(n,i) {this.side=block.textContent.slice(i);},
        setEnd(n,i) {this.side=block.textContent.slice(0,i);},toString() {return this.side;}};
    }};
  return JSON.parse(JSON.stringify(window.BioReportReview.previewSelection(preview,raw,range)));
}

test('a literal match in a different paragraph cannot replace a formatted selection',()=>{
  const raw='🔬 Header.\n\nA **drives** B.\n\nA drives B.';
  assert.deepEqual(selection(raw,1,'A drives B.'),{unmapped:true});
  assert.deepEqual(selection(raw,2,'A drives B.'),{original:'A drives B.',start:Array.from(raw.slice(0,raw.lastIndexOf('A drives B.'))).length});
});
test('duplicate plain claims map to the selected occurrence and punctuation stays outside',()=>{
  const raw='Claim. Claim.\n\nClaim.';
  assert.deepEqual(selection(raw,0,'Claim',6),{original:'Claim',start:7});
  assert.deepEqual(selection(raw,1,'Claim'),{original:'Claim',start:15});
});
