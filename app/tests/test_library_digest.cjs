const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname,'../frontend/digest.js'),'utf8');

function harness() {
  const root = {replaceChildren(...nodes) { this.nodes = nodes; }};
  const document = {createElement:() => ({setAttribute() {}})};
  const window = {};
  vm.runInNewContext(code,{document,window});
  return {api:window.BioDigest,root};
}
function deferred() {
  let reject;
  const promise = new Promise((_, fail) => { reject = fail; });
  return {promise,reject};
}

test('a late response from library A cannot replace library B, and B can be retried', async () => {
  const {api,root} = harness();
  const old = deferred();
  const a = api.open(root,'a',{request:() => old.promise});
  const calls = [];
  const b = api.open(root,'b',{request:async (method,url) => { calls.push([method,url]); throw Error('Library B unavailable'); }});
  await b;
  old.reject(Error('Library A unavailable'));
  await a;
  assert.equal(root.nodes[0].textContent,'Library B unavailable');
  await root.nodes[1].onclick();
  assert.deepEqual(calls,[['GET','/api/library/b/digest'],['GET','/api/library/b/digest']]);
});

test('closing an overview detaches a pending request from its old panel', async () => {
  const {api,root} = harness();
  const response = deferred();
  const opened = api.open(root,'all',{request:() => response.promise});
  api.close();
  root.replaceChildren({textContent:'Different project tab'});
  response.reject(Error('Old error'));
  await opened;
  assert.equal(root.nodes[0].textContent,'Different project tab');
});
