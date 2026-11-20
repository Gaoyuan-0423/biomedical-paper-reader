/* Saved queries send only a user-entered PubMed query when refreshed. */
(function () {
  'use strict';
  window.BioI18n.register({
    '保存检索式':'Save search', 'PubMed 检索式':'PubMed query', '检查新增文献':'Check for new papers',
    '预览并选择导入':'Preview and select papers', '标记本次结果为已看':'Mark these results as seen',
    '共 {total} 条，最近 {window} 条中有 {n} 篇未看。':'{total} matches, {n} unseen among the latest {window}.',
    '检索记录无法读取':'Search record could not be read', 'PubMed 查询暂未成功，请重试':'PubMed search failed. Try again.',
    'PubMed 无法按该检索式查询，请核对字段或关键词':'PubMed could not run this query. Check the fields and terms.',
    '检索结果已更新，请刷新后操作':'Results have changed. Refresh the view first.',
  });
  const sessions = new WeakMap();
  const t = (key, args) => window.BioI18n.t(key,args);
  function el(tag, key, params) {
    const node = document.createElement(tag);
    if (key) { node.dataset.i18n = key; if (params) node.dataset.i18nParams = JSON.stringify(params); node.textContent = t(key, params); }
    return node;
  }
  function action(key, fn) {
    const node = el('button',key); node.type = 'button'; node.className = 'btn'; node.onclick = fn; return node;
  }
  async function open(root, scope, request, select) {
    const token = {}; sessions.set(root,token);
    const active = () => sessions.get(root) === token;
    const base = '/api/pubmed-searches/' + scope;
    const form = el('form'), label = el('label','PubMed 检索式'), input = el('textarea');
    input.maxLength = 1000; input.required = true; input.rows = 2; input.id = 'pubmed-query'; label.htmlFor = input.id;
    const save = el('button','保存检索式'); save.className = 'btn'; save.type = 'submit'; form.className = 'project-form';
    const status = el('p'), list = el('div'); status.setAttribute('role','status');
    form.append(label,input,save); root.replaceChildren(form,status,list);
    let ticket = 0;
    async function load() {
      const revision = ++ticket, records = await request('GET',base);
      if (!active() || revision !== ticket) return;
      list.replaceChildren(...records.map(row));
    }
    function row(record) {
      const section = el('section'), title = el('h4'), result = el('p'), actions = el('div'); title.textContent = record.query;
      actions.className = 'project-form-actions';
      const edit = async (endpoint, body) => {
        for (const child of actions.children) child.disabled = true;
        try {
          const updated = await request('POST',base+'/'+record.id+'/'+endpoint,body);
          if (active()) { status.textContent=''; section.replaceWith(row(updated)); }
        } catch(err) { if (active()) {status.textContent=t(err.message); for (const b of actions.children) b.disabled=false;} }
      };
      actions.append(action('检查新增文献', () => edit('refresh')));
      if (record.total !== null) result.append(el('span','共 {total} 条，最近 {window} 条中有 {n} 篇未看。',
        {total:record.total,window:Math.min(record.total,record.window),n:record.unseen.length}));
      if (record.unseen.length) actions.append(action('预览并选择导入',()=>select(record.unseen)),
        action('标记本次结果为已看',()=>edit('seen',{result_id:record.result_id})));
      section.append(title,actions,result); return section;
    }
    form.onsubmit = async event => {
      event.preventDefault(); if (!input.value.trim()) return; save.disabled=true;
      try { await request('POST',base,{query:input.value.trim()}); if (active()) { input.value=''; status.textContent=''; await load(); } }
      catch(err) { if (active()) status.textContent=t(err.message); } finally {save.disabled=false;}
    };
    try { await load(); } catch(err) { if (active()) status.textContent=t(err.message); }
  }
  window.BioPubMed = {open};
})();
