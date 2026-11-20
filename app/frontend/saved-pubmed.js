/* Saved PubMed queries and page-scoped review of their local PMID backlog. */
(function () {
  'use strict';
  window.BioI18n.register({
    '保存检索式':'Save search', 'PubMed 检索式':'PubMed query', '检查新增文献':'Check for new papers',
    '预览本页并选择导入':'Preview this page and select papers', '标记本页为已看':'Mark this page as seen',
    '共 {n} 篇未看 · 第 {page}/{pages} 页':'{n} unseen · Page {page} of {pages}',
    '最近成功检查于 {time}':'Last successful check: {time}',
    '首次检查覆盖最近 7 天。':'The first check covers the past seven days.',
    '启动时自动检查':'Check automatically at startup', '已暂停启动检查':'Startup checks paused',
    '暂停启动检查':'Pause startup checks', '恢复启动检查':'Resume startup checks',
    '已有检查正在进行':'A check is already running', '本页 PMID':'PMIDs on this page',
    '检查失败，已保留未看文献和上次进度。':'Check failed. The unread papers and last successful checkpoint were kept.',
    '检索记录无法读取':'Search record could not be read', 'PubMed 查询暂未成功，请重试':'PubMed search failed. Try again.',
    'PubMed 无法按该检索式查询，请核对字段或关键词':'PubMed could not run this query. Check the fields and terms.',
    '检索结果已更新，请刷新后操作':'Results have changed. Refresh the view first.',
    '上一页未看文献':'Previous page of unread papers', '下一页未看文献':'Next page of unread papers',
  });
  const sessions = new WeakMap();
  const t = (key, args) => window.BioI18n.t(key,args);
  let observer = null, startupRun = null;
  function el(tag, key, params) {
    const node = document.createElement(tag);
    if (key) { node.dataset.i18n = key; if (params) node.dataset.i18nParams = JSON.stringify(params); node.textContent = t(key, params); }
    return node;
  }
  function action(key, fn) {
    const node = el('button',key); node.type = 'button'; node.className = 'btn'; node.onclick = fn; return node;
  }
  function startup(request) {
    if (startupRun) return startupRun;
    startupRun = (async () => {
      const due = await request('GET','/api/pubmed-followups');
      for (const {scope,id} of due) {
        try {
          await request('POST','/api/pubmed-searches/'+scope+'/'+id+'/refresh?due_only=true');
        } catch (_) { /* A failed search retains its checkpoint and can be retried independently. */ }
        if (observer?.scope === scope) await observer.refresh(id);
      }
    })().catch(() => { /* The library opens offline; manual refresh remains available. */ });
    return startupRun;
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
    const entries = new Map();
    let ticket = 0;
    async function load() {
      const revision = ++ticket, records = await request('GET',base);
      if (!active() || revision !== ticket) return;
      entries.clear();
      list.replaceChildren(...records.map(row));
    }
    function row(record) {
      const section = el('section'), title = el('h4'), result = el('p'), actions = el('div'); title.textContent = record.query;
      actions.className = 'project-form-actions';
      let busy = false;
      const operate = async (method, suffix, body) => {
        if (busy) return;
        busy = true;
        for (const child of actions.children) child.disabled = true;
        try {
          const updated = await request(method,base+'/'+record.id+suffix,body);
          if (active() && entries.get(record.id)?.section === section) {
            status.textContent = updated.refreshing ? t('已有检查正在进行') : '';
            section.replaceWith(row(updated));
          }
        } catch(err) {
          if (active()) status.textContent = t(err.message);
        } finally {
          busy = false;
          for (const button of actions.children) button.disabled = false;
        }
      };
      const page = number => operate('GET','?page='+number);
      entries.set(record.id,{section,refresh: () => page(record.page)});
      result.append(el('span','共 {n} 篇未看 · 第 {page}/{pages} 页',{n:record.unseen_total,page:record.page,pages:record.pages}));
      const checked = record.last_checked
        ? el('p','最近成功检查于 {time}',{time:new Date(record.last_checked).toLocaleString()})
        : el('p','首次检查覆盖最近 7 天。');
      actions.append(action('检查新增文献', () => operate('POST','/refresh')),
        action(record.tracking ? '暂停启动检查' : '恢复启动检查', () => operate('POST','/tracking',{enabled:!record.tracking})));
      section.append(title,el('p',record.tracking ? '启动时自动检查' : '已暂停启动检查'),checked,result);
      if (record.last_error) section.append(el('p','检查失败，已保留未看文献和上次进度。'),el('p',record.last_error));
      if (record.page > 1) actions.append(action('上一页未看文献',() => page(record.page-1)));
      if (record.page < record.pages) actions.append(action('下一页未看文献',() => page(record.page+1)));
      if (record.unseen.length) {
        const details = el('details'), ids = el('p'); ids.textContent = record.unseen.join(', ');
        details.append(el('summary','本页 PMID'),ids); section.append(details);
        actions.append(action('预览本页并选择导入',()=>select(record.unseen)),
          action('标记本页为已看',()=>operate('POST','/seen',{page:record.page,result_id:record.result_id})));
      }
      section.append(actions); return section;
    }
    observer = {scope,refresh: id => entries.get(id)?.refresh()};
    form.onsubmit = async event => {
      event.preventDefault(); if (!input.value.trim()) return; save.disabled=true;
      try { await request('POST',base,{query:input.value.trim()}); if (active()) { input.value=''; status.textContent=''; await load(); } }
      catch(err) { if (active()) status.textContent=t(err.message); } finally {save.disabled=false;}
    };
    try { await load(); } catch(err) { if (active()) status.textContent=t(err.message); }
  }
  window.BioPubMed = {open,startup};
})();
