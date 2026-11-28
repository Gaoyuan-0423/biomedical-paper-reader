(function (global) {
  'use strict';
  const t = (key, vars) => global.BioI18n?.t(key, vars) ?? key;
  global.BioI18n?.register({
    '笔记题名':'Note title', '文献卡片':'Paper cards', '插图画廊':'Figure gallery', '导出离线 HTML':'Export offline HTML',
    '正在整理本地文献…':'Organizing local papers…', '正在导出…':'Exporting…',
    '文献篇数':'Papers', '已有笔记':'Saved notes', '库内插图':'Figures', '已有正文':'Full texts',
    '全部主题':'All topics', '未分类':'Untagged', '全部图类':'All figure types', '未记录图类':'Unspecified figure type',
    '搜题名、作者、主题或笔记':'Search titles, authors, topics or notes',
    '搜图注、观察、标签或论文':'Search captions, observations, tags or papers',
    '搜索库内卡片':'Search library cards', '筛选主题':'Filter by topic', '筛选图类':'Filter by figure type', '全部图类和图标签':'All figure types and tags',
    '图标签':'Figure tag', '查看本篇插图（{count}）':'View figures from this paper ({count})',
    '全部插图':'All figures', '放大插图':'Enlarge figure', '缩小插图':'Reduce figure',
    '一句话结论':'Key finding', '速读暂定发现':'Preliminary finding from quick notes',
    '摘要摘录':'Abstract excerpt', '笔记摘录':'Note excerpt', '当前没有已整理的结论或摘要。':'No saved conclusion or abstract is available.',
    '卡片详情':'Card details', '打开完整笔记':'Open full notes', '打开论文':'Open paper', '对照结论原文':'View cited passage',
    '所属文献库':'Library memberships', '笔记生成中断':'Partial notes', '笔记材料已变化':'Sources changed',
    '已有正文，尚无笔记':'Full text available, no saved notes', '尚无笔记':'No saved notes',
    '未记录':'Not recorded', '打开插图原文':'Open figure in source PDF', '原图来源暂不可用':'Figure source unavailable',
    '尚无可用预览图':'No preview available', '这张图尚未记录图注。':'No saved caption for this figure.',
    '库中尚无文献。':'This library has no papers yet.', '库中尚无已提取插图。':'No figures have been extracted for this library.',
    '没有符合筛选条件的结果。':'No results match these filters.', '清除条件':'Clear filters',
    '找到 {count} 篇文献':'{count} papers found', '找到 {count} 张插图':'{count} figures found',
    '另有 {count} 条文献记录暂不可读取。':'{count} additional paper records could not be read.',
    '已记录图像观察':'Saved figure observation', '原图来源有变化，请重新打开库总览。':'The figure source changed. Reopen the library overview.',
    '重试读取':'Retry loading', '打开正文':'Open main PDF', '研究背景与问题':'Background and question',
    '研究方法':'Methods', '研究对象与设计':'Study population and design', '关键结果与数值':'Key results and values',
    '机制与证据':'Mechanism and evidence', '研究局限':'Study limitations', '课题启示':'Project implications',
    '待核问题与下一步':'Open questions and next steps', '笔记已变化，请重新打开库总览。':'The notes changed. Reopen the library overview.',
  });

  let active;
  const node = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  };
  function button(text, action, cls = 'btn') {
    const el = node('button', text, cls);
    el.type = 'button';
    el.onclick = action;
    return el;
  }
  function select(id, label, options) {
    const el = node('select');
    el.id = id;
    el.setAttribute('aria-label', t(label));
    for (const [value, text] of options) {
      const option = node('option', text);
      option.value = value;
      el.append(option);
    }
    return el;
  }
  const norm = value => String(value ?? '').normalize('NFKC').toLocaleLowerCase();
  function searchText(parts) {
    return parts.flat(Infinity).filter(Boolean).map(norm).join('\n');
  }
  function paperSearch(paper) {
    return searchText([paper.title,paper.note_title,paper.authors,paper.doi,paper.year,paper.journal,paper.tags,
      paper.conclusion,paper.abstract_excerpt,paper.note_excerpt,paper.sections?.map(item => item.text)]);
  }
  function figureSearch(figure, paper) {
    return searchText([figure.label,figure.caption,figure.observation,figure.kind,figure.tags,
      figure.document_name,figure.paper_title,paper?.authors,paper?.tags]);
  }
  function source(s, callback, trigger) {
    const version = ++s.navigation;
    const current = () => active === s && s.navigation === version;
    trigger.disabled = true; s.message.textContent = '';
    Promise.resolve().then(() => callback(current)).then(ok => {
      if (current() && ok) s.hooks.leave();
    }).catch(err => {
      if (current()) s.message.textContent = t(err.message);
    }).finally(() => { trigger.disabled = false; });
  }
  function action(s, label, callback, attribute) {
    const el = button(t(label), () => source(s, callback, el), 'btn btn-ghost');
    if (attribute) el.setAttribute(attribute, '');
    return el;
  }
  function paperCard(s, paper) {
    const el = node('article', null, 'digest-paper');
    el.dataset.digestPaper = paper.id;
    el.append(node('h3', paper.title));
    if (paper.note_title && norm(paper.note_title.trim()) !== norm(paper.title.trim())) {
      const noteTitle = node('p',null,'digest-note-title');
      noteTitle.append(node('span',t('笔记题名')+' · '),node('strong',paper.note_title));
      el.append(noteTitle);
    }
    const byline = [paper.authors?.join(', '), paper.year, paper.journal, paper.doi].filter(Boolean);
    if (byline.length) el.append(node('p', byline.join(' · '), 'digest-byline'));
    const flags = [!paper.has_notes && (paper.has_pdf ? '已有正文，尚无笔记' : '尚无笔记'),
      paper.incomplete && '笔记生成中断',paper.stale && '笔记材料已变化'].filter(Boolean);
    if (flags.length) el.append(node('p', flags.map(key => t(key)).join(' · '), 'digest-flags'));
    const quote = node('div', null, 'digest-extract');
    const finding = paper.conclusion || paper.abstract_excerpt || paper.note_excerpt;
    if (finding) {
      const label = paper.conclusion ? (paper.conclusion_source === 'quick_notes' ? '速读暂定发现' : '一句话结论')
        : paper.abstract_excerpt ? '摘要摘录' : '笔记摘录';
      quote.append(node('span', t(label)), node('p', finding));
    } else quote.append(node('p', t('当前没有已整理的结论或摘要。'), 'digest-muted'));
    el.append(quote);
    const chips = node('div', null, 'digest-chips');
    for (const name of paper.tags || []) chips.append(node('span',name));
    if (chips.childElementCount) el.append(chips);

    const detail = node('details', null, 'digest-details');
    detail.append(node('summary', t('卡片详情')));
    const blocks = node('dl');
    for (const section of paper.sections || []) {
      blocks.append(node('dt', t(section.label)), node('dd',section.text || t('未记录'),section.text ? '' : 'digest-muted'));
    }
    if (blocks.childElementCount) detail.append(blocks);
    if (paper.libraries?.length) detail.append(node('p', t('所属文献库') + ' · ' + paper.libraries.map(l => l.name).join(' / '), 'digest-byline'));
    el.append(detail);
    const actions = node('div',null,'digest-actions');
    actions.append(action(s, paper.has_notes ? '打开完整笔记' : '打开论文',
      current => s.hooks.openPaper(paper.id,current), 'data-digest-open-paper'));
    if (paper.conclusion_citation) {
      actions.append(action(s,'对照结论原文',current => s.hooks.openCitation(paper,current),'data-digest-open-citation'));
    } else if (paper.has_pdf) {
      actions.append(action(s,'打开正文',current => s.hooks.openSource(paper.id,'main',1,null,null,null,current)));
    }
    if (paper.figure_count) actions.append(button(t('查看本篇插图（{count}）',{count:paper.figure_count}),() => {
      ++s.navigation;
      s.view = 'figures'; s.onlyPaper = paper.id; s.limit = 18;
      s.controls.search.value = s.controls.kind.value = s.controls.topic.value = '';
      renderResults(s); s.controls.search.focus();
    },'btn btn-ghost'));
    el.append(actions);
    return el;
  }

  function figureCard(s, fig) {
    const el = node('article',null,'digest-figure');
    el.dataset.digestFigure = fig.id; el.dataset.paperId = fig.paper_id;
    const preview = node('div',null,'digest-image');
    if (fig.image_url && fig.image_url.startsWith('/api/')) {
      const img = node('img'); img.alt = [fig.paper_title,fig.label].filter(Boolean).join(' · ');
      img.loading = 'lazy'; img.decoding = 'async';
      const zoom = button('',() => {
        const on = el.classList.toggle('digest-zoomed');
        zoom.setAttribute('aria-expanded', String(on));
        zoom.setAttribute('aria-label', t(on ? '缩小插图' : '放大插图') + ' · ' + img.alt);
      },'digest-zoom');
      zoom.setAttribute('aria-label',t('放大插图') + ' · ' + img.alt);
      zoom.setAttribute('aria-expanded','false');
      img.onerror = () => preview.replaceChildren(node('span',t('尚无可用预览图')));
      img.src = fig.image_url; zoom.append(img); preview.append(zoom);
    } else preview.append(node('span',t('尚无可用预览图')));
    const body = node('div',null,'digest-figure-body');
    body.append(node('p',[fig.label,fig.kind].filter(Boolean).join(' · '),'digest-figure-id'),
      node('h3',fig.paper_title),node('p',fig.caption || t('这张图尚未记录图注。'),'digest-caption'));
    if (fig.observation) {
      const observation = node('details',null,'digest-observation');
      observation.append(node('summary',t('已记录图像观察')),node('p',fig.observation));
      body.append(observation);
    }
    const tags = node('div',null,'digest-chips');
    for (const tag of fig.tags || []) tags.append(node('span',tag));
    if (tags.childElementCount) body.append(tags);
    const location = [fig.document_name, fig.page && t('第 {page} 页',{page:fig.page})].filter(Boolean);
    if (location.length) body.append(node('p',location.join(' · '),'digest-byline'));
    if (fig.source_available && fig.source_url?.startsWith('/api/')) {
      body.append(action(s,'打开插图原文',async current => {
        const verified = await s.hooks.request('GET',fig.source_url);
        if (!current()) return false;
        if (verified.paper_id !== fig.paper_id || verified.id !== fig.id ||
            verified.document_id !== fig.document_id || verified.page !== fig.page)
          throw new Error(t('原图来源有变化，请重新打开库总览。'));
        return s.hooks.openSource(verified.paper_id,verified.document_id,verified.page,verified.id,
          verified.source_revision,verified.bbox,current);
      },'data-digest-open-figure'));
    } else body.append(node('small',t('原图来源暂不可用'),'digest-muted'));
    el.append(preview,body);
    return el;
  }

  function renderResults(s, append = false) {
    const {data, controls} = s;
    const figures = s.view === 'figures';
    const query = norm(controls.search.value.trim());
    // Internal option values are indices, allowing a real topic named “unclassified”.
    const value = controls.topic.value;
    const topic = value && value !== 'untagged' ? data.topics[Number(value) - 1]?.name : '';
    const selectedKind = controls.kind.value;
    const kind = selectedKind && s.figureFilters[Number(selectedKind)-1];
    const records = (figures ? data.figures : data.papers).filter(item => {
      const paper = figures ? s.papers.get(item.paper_id) : item;
      const tags = paper?.tags || [];
      return (!topic || tags.includes(topic)) && (value !== 'untagged' || !tags.length) &&
        (!figures || !s.onlyPaper || item.paper_id === s.onlyPaper) &&
        (!figures || !kind || (kind.isTag ? item.tags.includes(kind.value) : item.kind === kind.value)) &&
        (!query || s.index.get(item).includes(query));
    });
    controls.kind.hidden = !figures;
    controls.scope.hidden = !(figures && s.onlyPaper);
    if (!controls.scope.hidden) controls.scope.firstChild.textContent = s.papers.get(s.onlyPaper)?.title || '';
    controls.search.placeholder = t(figures ? '搜图注、观察、标签或论文' : '搜题名、作者、主题或笔记');
    controls.list.className = figures ? 'digest-gallery' : 'digest-cards';
    controls.status.textContent = t(figures ? '找到 {count} 张插图' : '找到 {count} 篇文献',{count:records.length});
    controls.views.forEach(el => el.setAttribute('aria-pressed', String(s.view === el.dataset.digestView)));
    const start = append ? controls.list.childElementCount : 0;
    const entries = records.slice(start,s.limit).map(item => figures ? figureCard(s,item) : paperCard(s,item));
    if (!entries.length) {
      const filtered = !!(query || value || (figures && selectedKind));
      entries.push(node('p',t(filtered ? '没有符合筛选条件的结果。' : figures ? '库中尚无已提取插图。' : '库中尚无文献。'),'digest-empty'));
      if (filtered) entries.push(button(t('清除条件'), () => {
        controls.topic.value = controls.kind.value = controls.search.value = '';
        renderResults(s);
      }));
    }
    if (append) controls.list.append(...entries); else controls.list.replaceChildren(...entries);
    controls.more.hidden = s.limit >= records.length;
  }

  function render(s) {
    const {data,root} = s;
    const header = node('header',null,'digest-header');
    const heading = node('div');
    heading.append(node('h2',data.library.id === 'all' ? t('全部文献') : data.library.name));
    if (data.library.topic) heading.append(node('p',data.library.topic));
    const exp = button(t('导出离线 HTML'),async () => {
      if (exp.disabled) return;
      exp.disabled = true; exp.textContent = t('正在导出…'); s.message.textContent = '';
      try { await s.hooks.exportHtml(s.scope,heading.firstElementChild.textContent,() => active === s); }
      catch (err) { if (active === s) s.message.textContent = t(err.message); }
      finally { exp.disabled = false; exp.textContent = t('导出离线 HTML'); }
    });
    exp.id = 'digest-export'; header.append(heading,exp);
    const stats = node('dl',null,'digest-metrics');
    for (const [key,label] of [['papers','文献篇数'],['with_notes','已有笔记'],['figures','库内插图'],['with_pdf','已有正文']]) {
      const item = node('div'); item.dataset.digestMetric = key;
      item.append(node('dt',t(label)),node('dd',String(data.counts[key]))); stats.append(item);
    }
    const views = node('div',null,'digest-views');
    const filter = node('div',null,'digest-filters');
    const search = node('input'); search.type = 'search'; search.id = 'digest-query';
    search.setAttribute('aria-label',t('搜索库内卡片'));
    const topics = [['',t('全部主题')], ...data.topics.map((item,i) => [String(i+1),item.name+' ('+item.count+')'])];
    if (data.counts.unclassified) topics.push(['untagged',t('未分类')+' ('+data.counts.unclassified+')']);
    const topic = select('digest-topic','筛选主题',topics);
    const kind = select('digest-figure-type','筛选图类',[['',t('全部图类和图标签')],...s.figureFilters.map((item,i) =>
      [String(i+1), item.isTag ? t('图标签') + ' · ' + item.value : item.value || t('未记录图类')])]);
    const scope = node('div',null,'digest-figure-scope');
    scope.append(node('span'),button(t('全部插图'),() => { s.onlyPaper = ''; s.limit = 18; renderResults(s); },'btn btn-ghost'));
    const status = node('span',null,'digest-result-count'); status.setAttribute('role','status');
    const tabs = [['papers','文献卡片'],['figures','插图画廊']].map(([id,label]) => {
      const el = button(t(label),() => {
        if (s.view === id) return;
        ++s.navigation; s.view = id; s.onlyPaper = ''; s.limit = 18; s.message.textContent = ''; renderResults(s);
      },'btn');
      el.dataset.digestView = id; return el;
    });
    views.append(...tabs,status); filter.append(search,topic,kind);
    const list = node('div');
    const more = button(t('加载更多'),() => { s.limit += 18; renderResults(s,true); });
    for (const control of [search,topic,kind]) control.addEventListener(control === search ? 'input' : 'change', () => {
      ++s.navigation; s.limit = 18; s.message.textContent = ''; renderResults(s);
    });
    s.controls = {search,topic,kind,scope,status,list,more,views:tabs};
    const elements = [header,stats];
    if (data.counts.unavailable) elements.push(node('p',t('另有 {count} 条文献记录暂不可读取。',{count:data.counts.unavailable}),'digest-flags'));
    root.replaceChildren(...elements,views,filter,scope,s.message,list,more);
    renderResults(s);
  }

  async function open(root,scope,hooks) {
    const s = active = {root,scope,hooks,navigation:0,view:'papers',limit:18};
    s.message = node('p',null,'digest-error'); s.message.setAttribute('role','status');
    root.replaceChildren(node('p',t('正在整理本地文献…'),'digest-empty'));
    try {
      const data = await hooks.request('GET','/api/library/'+encodeURIComponent(scope)+'/digest');
      if (active !== s) return;
      s.data = data; s.papers = new Map(data.papers.map(p => [p.id,p]));
      s.index = new Map(data.papers.map(p => [p,paperSearch(p)]));
      for (const f of data.figures) s.index.set(f,figureSearch(f,s.papers.get(f.paper_id)));
      const kinds = [...new Set(data.figures.map(f => f.kind || ''))].sort();
      const tags = [...new Set(data.figures.flatMap(f => f.tags || []))].sort();
      s.figureFilters = [...kinds.map(value => ({value})),...tags.map(value => ({value,isTag:true}))];
      render(s);
    } catch (err) {
      if (active === s) root.replaceChildren(node('p',t(err.message),'digest-error'),button(t('重试读取'),() => open(root,scope,hooks)));
    }
  }

  global.BioI18n?.onChange(() => {
    const s = active;
    if (!s?.controls) return;
    const {search,topic,kind} = s.controls;
    const expanded = new Set([...s.root.querySelectorAll('article[data-digest-paper] details[open]')]
      .map(detail => detail.closest('article').dataset.digestPaper));
    render(s);
    s.controls.search.value = search.value;
    s.controls.topic.value = topic.value;
    s.controls.kind.value = kind.value;
    renderResults(s);
    for (const el of s.root.querySelectorAll('[data-digest-paper]'))
      if (expanded.has(el.dataset.digestPaper)) el.querySelector('details').open = true;
  });
  global.BioDigest = {open,close:() => { active = null; }};
})(window);
