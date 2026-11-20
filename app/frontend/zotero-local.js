// One-off migration from a read-only snapshot. All user-provided strings enter as text.
(function (global) {
  'use strict';
  const labels = {
    '从本机 Zotero 文库迁移':'Import a local Zotero library', 'Zotero 资料目录或 zotero.sqlite 的路径':'Zotero data directory or zotero.sqlite path',
    '选择资料目录':'Choose data directory', '选择数据库文件':'Choose database file', '预览本地文库':'Preview local library',
    '导入所选条目':'Import selected items', 'Zotero 原笔记':'Original Zotero notes', '导入到':'Import into',
    '在回收站中，请先恢复':'In Trash. Restore the paper first.', '已有题录':'Existing record', '新条目':'New record',
    '子笔记与附件':'Child notes and attachments', '本地文库没有可导入的条目。':'This library has no importable records.',
    '已选 PDF 的用途':'Use of selected PDFs', '第一份所选 PDF 作为正文':'Use the first selected PDF as the main paper',
    '全部作为补充 PDF':'Import all as supplementary PDFs', '已保留当前正文，所选 PDF 将作为补充材料。':'The main PDF will be kept. Selected PDFs are added as supplements.',
    '已导入':'Imported', '已写入全部文献，课题归集失败，可重试。':'Saved to All papers. Retry to add it to the project.',
    '请勾选指定的正文 PDF。':'Select the PDF designated as the main paper.',
    '正在预览本机文库…':'Opening local library…', '本地来源':'Local source', '在 Zotero 中打开条目':'Open item in Zotero', '请先填写资料路径。':'Enter the library path first.',
    '本次已导入 {count} 篇。':'Imported {count} papers.', '刷新论文列表失败':'Could not refresh the paper list',
  };
  global.BioI18n?.register(labels);
  const t = (key, params={}) => global.BioI18n?.t(key,params) || key.replace('{count}',params.count);
  const $ = id => document.getElementById(id);
  const el = (tag,text) => { const node=document.createElement(tag); if (text != null) node.textContent=text; return node; };
  let hooks, token='', scope='', running=false, items=[], more=false, visible=false;
  const cards = new Map();

  function status(message) { $('zlocal-status').textContent=message; }
  function lock(value) {
    running=value;
    for (const id of ['zlocal-path','zlocal-preview','zlocal-more','zlocal-file','zlocal-folder','zlocal-close']) $(id).disabled=value;
    for (const input of $('zlocal-list').querySelectorAll('input,select')) input.disabled=value || input.dataset.locked === 'yes';
    $('zlocal-import').disabled=value || !Array.from(cards.values()).some(c=>c.paper.checked);
  }
  function checkbox(parent,text,disabled=false) {
    const label=el('label'), box=el('input');
    box.type='checkbox'; box.disabled=disabled; box.dataset.locked=disabled?'yes':'no';
    label.append(box,document.createTextNode(text)); parent.append(label);
    return box;
  }
  function addCard(item) {
    const row=el('article');
    const paper=checkbox(row,item.title,item.trashed);
    row.append(el('small',[item.authors,item.year,item.doi,t(item.trashed?'在回收站中，请先恢复':item.existing_id?'已有题录':'新条目')].filter(Boolean).join(' · ')));
    const details=el('details'), summary=el('summary',t('子笔记与附件')+' ('+item.notes.length+' / '+item.attachments.length+')');
    details.append(summary);
    const notes=[], files=[];
    for (const note of item.notes) {
      const node=el('details'), title=el('summary',note.text.split('\n')[0].slice(0,90) || note.key);
      const box=checkbox(details,note.key,item.trashed);
      node.append(title,el('pre',note.text)); details.append(node);
      notes.push([note.key,box]);
    }
    for (const a of item.attachments) {
      const box=checkbox(details,(a.filename || a.key) + (a.available?'':' · '+a.reason),item.trashed || !a.available);
      files.push([a.key,box]);
    }
    let main;
    if (files.length && item.has_main) details.append(el('small',t('已保留当前正文，所选 PDF 将作为补充材料。')));
    if (files.length && !item.has_main) {
      const label=el('label',t('已选 PDF 的用途')); main=el('select');
      for (const [value,text] of [['auto',t('第一份所选 PDF 作为正文')],['',t('全部作为补充 PDF')],...item.attachments.filter(a=>a.available).map(a=>[a.key,a.filename])]) {
        const option=el('option',text); option.value=value; main.append(option);
      }
      label.append(main); details.append(label);
    }
    if (notes.length || files.length) row.append(details);
    row.onchange=e=>{
      if (e.target !== paper && e.target.type === 'checkbox' && e.target.checked) paper.checked=true;
      lock(false);
    };
    $('zlocal-list').append(row);
    cards.set(item.id,{paper,notes,files,main,row});
  }
  async function discard() {
    const old=token; token=''; cards.clear(); items=[]; more=false;
    $('zlocal-list').replaceChildren(); $('zlocal-more').hidden=true;
    if (old) await hooks.request('DELETE','/api/zotero-local/'+old).catch(()=>{});
  }
  async function preview(append) {
    if (running || !visible) return;
    if (!append && !$('zlocal-path').value.trim()) return status(t('请先填写资料路径。'));
    lock(true); status(t('正在预览本机文库…'));
    try {
      if (!append) await discard();
      const response=append ? await hooks.request('GET','/api/zotero-local/'+token+'?offset='+items.length) :
        await hooks.request('POST','/api/zotero-local/preview',{path:$('zlocal-path').value.trim(),library_id:scope});
      token=response.preview; more=response.has_more; items.push(...response.items); response.items.forEach(addCard);
      $('zlocal-more').hidden=!more; status(items.length?'':t('本地文库没有可导入的条目。'));
    } catch (error) { status(error.message); }
    finally { lock(false); }
  }
  async function importItems() {
    if (running || !token) return;
    const queue=[];
    for (const item of items) {
      const card=cards.get(item.id); if (!card.paper.checked) continue;
      const keys=list=>list.filter(([,box])=>box.checked).map(([key])=>key);
      const attachments=keys(card.files), main=card.main?.value || '';
      const primary=main==='auto'?(attachments[0]||''):main;
      if (primary && !attachments.includes(primary)) return status(t('请勾选指定的正文 PDF。')+' '+item.title);
      queue.push({item,card,body:{item_id:item.id,notes:keys(card.notes),attachments,main_attachment:primary}});
    }
    lock(true);
    const errors=[], importedIds=[]; let ok=0;
    try {
      for (const {item,card,body} of queue) {
        status('('+ (ok+errors.length+1)+' / '+queue.length+') '+item.title);
        try {
          const reply=await hooks.request('POST','/api/zotero-local/'+token+'/import',body);
          importedIds.push(reply.paper.id);
          if (!reply.in_library) throw new Error(t('已写入全部文献，课题归集失败，可重试。'));
          ok++; card.paper.checked=false;
          card.row.append(el('small',t('已导入')));
        } catch (err) { errors.push(item.title+' · '+err.message); }
      }
      try { await hooks.refresh(importedIds); } catch (err) { errors.push(t('刷新论文列表失败')+' · '+err.message); }
      status([t('本次已导入 {count} 篇。',{count:ok}),...errors].join('\n'));
    } finally { lock(false); }
  }
  function open() {
    if (running) return;
    global.BioZotero?.close();
    scope=hooks.scope(); visible=true;
    $('zlocal-scope').textContent=t('导入到')+' '+hooks.scopeName();
    $('zlocal-file').hidden=$('zlocal-folder').hidden=typeof global.mygo?.call !== 'function';
    hooks.show('zlocal-modal','zlocal-path'); status(''); lock(false);
  }
  function close() {
    if (running) return;
    visible=false; hooks.hide('zlocal-modal'); void discard();
  }
  async function pick(folder) {
    if (running) return;
    try {
      const value=await global.mygo.call('ZoteroPicker.Choose',folder);
      if (value) { $('zlocal-path').value=value; await preview(false); }
    } catch (err) { status(err.message); }
  }
  function renderNotes(paper) {
    const box=$('zlocal-notes'); if (!box) return;
    const list=paper?.zotero_local_notes || [];
    const origins=paper?.zotero_local_origins || [];
    box.hidden=!list.length && !origins.length; box.open=false;
    const body=box.querySelector('div'); body.replaceChildren();
    for (const origin of origins) {
      const uri=origin.select_uri;
      const line=el('p');
      if (/^zotero:\/\/select\/(?:library|groups\/[1-9][0-9]*)\/items\/[A-Z0-9]{8}$/.test(uri)) {
        const link=el('a',t('在 Zotero 中打开条目')+' · '+origin.item_key);
        link.href=uri;
        if (global.mygo?.call) link.onclick=async event=>{
          event.preventDefault(); try { await global.mygo.call('ZoteroPicker.ShowItem',uri); }
          catch (error) { line.append(document.createTextNode(' · '+error.message)); }
        };
        line.append(link);
      } else line.textContent=t('本地来源')+' · '+origin.library_id+' / '+origin.item_key;
      body.append(line);
    }
    for (const n of list) {
      body.append(el('small',t('本地来源')+' · '+n.library_id+' / '+n.item_key+' / '+n.key+(n.version>1?' · v'+n.version:'')),el('pre',n.text));
    }
  }
  function init(config) {
    hooks=config; $('zlocal-open').onclick=open; $('zlocal-close').onclick=close;
    $('zlocal-preview').onclick=()=>preview(false); $('zlocal-more').onclick=()=>preview(true); $('zlocal-import').onclick=importItems;
    $('zlocal-folder').onclick=()=>pick(true); $('zlocal-file').onclick=()=>pick(false);
    $('zlocal-path').oninput=()=>{if (token) { void discard(); lock(false); }};
  }
  global.BioLocalZotero={init,close,busy:()=>running,renderNotes};
})(window);
