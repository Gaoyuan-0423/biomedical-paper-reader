// Candidate values stay in memory until the reader accepts an individual cell.
(function (global) {
  'use strict';
  const labels = {enrolled:'入组人数', paired:'完整配对人数', single_cell:'实际进入单细胞分析的人数', sites:'取样部位'};
  const columns = Object.keys(labels);
  global.BioI18n?.register({
    '入组人数':'Participants enrolled', '完整配对人数':'Participants with complete pairs',
    '实际进入单细胞分析的人数':'Participants in single-cell analysis', '取样部位':'Sampling sites',
    '比较字段':'Comparison fields', '论文与队列':'Papers and cohorts', '为勾选的论文起草比较表':'Draft a table for selected papers',
    '正在起草…':'Drafting…', '从已保存的证据卡和字段摘录起草。逐格核对后采纳，再保存比较。':'Uses saved evidence cards and field excerpts. Review and accept each cell, then save the comparison.',
    '请至少勾选一个比较字段。':'Select at least one comparison field.',
    '请先移除已不在课题库中的论文，再起草。':'Remove papers that are no longer in this library before drafting.',
    '已保存的材料中未找到':'Not found in saved material', '已有附件，需核对相应补表':'Check existing attachments for the cited table', '缺补表，待核':'Missing supplementary table; check required',
    '未注明队列':'Cohort unspecified', '候选值':'Candidate value', '已采纳':'Accepted',
    '查看原句':'Open cited page', '采纳':'Accept', '跳过此格':'Skip cell', '已跳过':'Skipped',
    '重新查看候选值':'Show candidates again', '移除已采纳值':'Remove accepted value',
    '从本次比较移除这个已采纳值？':'Remove this accepted value from the comparison?',
    '候选摘录、来源文件或队列已变更，请重新起草并复核。':'The excerpt, source, or cohort changed. Generate the draft again and review it.',
    '采纳的出处已变更，请重新起草并复核。':'The accepted source changed. Generate the draft again and review it.',
    '所选论文已不在当前课题库。':'The selection includes a paper outside the current library.',
    '同一队列的比较列只能采纳一个非空值。':'Accept one nonempty value per cohort and field.',
    '请填写要采纳的值。':'Enter the value to accept.', '采纳的字段值不能为空。':'An accepted field value cannot be empty.',
  });

  function create({escape,ui,t,request,base,status,dirty,render,jump,isAvailable}) {
    const selected = s => s.draft.columns ?? columns;
    function clearPreview(s) {
      s.preview = null;
      s.edits = new Map();
      s.skipped = new Set();
      s.folded = new Set();
    }
    function refresh(s) {
      const container = s.root.querySelector('[data-draft-review]');
      if (container) container.innerHTML = review(s);
    }
    function controls(s) {
      return '<div class="cmp-draft-controls"><fieldset><legend>' + ui('比较字段') + '</legend><div class="cmp-draft-columns">' +
        columns.map(k => '<label><input type="checkbox" data-draft-column="' + k + '"' + (selected(s).includes(k) ? ' checked' : '') +
          '>' + ui(labels[k]) + '</label>').join('') + '</div></fieldset><div><button type="button" class="btn" data-build-draft>' +
        ui(s.drafting ? '正在起草…' : '为勾选的论文起草比较表') + '</button></div><p class="hint">' +
        ui('从已保存的证据卡和字段摘录起草。逐格核对后采纳，再保存比较。') + '</p></div><div data-draft-review>' + review(s) + '</div>';
    }
    function citation(source, index, material, accepted) {
      if (!source) return '';
      const stale = accepted && material && !isAvailable(source,material);
      return '<blockquote>' + escape(source.text) + '</blockquote><button type="button" class="btn btn-ghost" data-review="source" data-index="' +
        index + '"' + (stale ? ' disabled' : '') + '>' + ui('查看原句') + ' · ' +
        escape([source.document_name, 'p. ' + source.page, source.figure_label].filter(Boolean).join(' · ')) + '</button>' +
        (stale ? '<small class="cmp-warning">' + ui('来源或记录已变化，保留保存时的内容。') + '</small>' : '');
    }
    const cellKey = (id, cohort, column) => JSON.stringify([id,cohort,column]);
    const editKey = (key, candidate) => JSON.stringify([key,candidate.source.key,candidate.value]);
    function cellHTML(s,row,cohort,column,proposal) {
      const saved = (row.cells || []).find(c => c.column === column && c.cohort === cohort);
      const key = cellKey(row.paper_id,cohort,column), m = s.materials.get(row.paper_id);
      let content;
      if (saved) {
        content = '<div class="cmp-ref"><label>' + ui('已采纳') + '<textarea rows="2" maxlength="4000" data-accepted-value' +
          (m && !isAvailable(saved.source,m) ? ' readonly' : '') + '>' + escape(saved.value) + '</textarea></label>' +
          citation(saved.source,'accepted',m,true) + '<button type="button" class="btn btn-ghost" data-review="remove">' +
          ui('移除已采纳值') + '</button></div>';
      } else if (s.skipped?.has(key)) {
        content = '<small>' + ui('已跳过') + '</small> <button type="button" class="btn btn-ghost" data-review="restore">' + ui('重新查看候选值') + '</button>';
      } else if (proposal?.candidates?.length) {
        content = proposal.candidates.map((candidate,i) => '<div class="cmp-ref"><label>' + ui('候选值') +
          '<textarea rows="2" maxlength="4000" data-draft-value="' + i + '">' +
          escape(s.edits?.get(editKey(key,candidate)) ?? candidate.value) + '</textarea></label>' +
          citation(candidate.source,i,m,false) + '<button type="button" class="btn" data-review="accept" data-index="' + i + '">' +
          ui('采纳') + '</button></div>').join('') + '<button type="button" class="btn btn-ghost" data-review="skip">' + ui('跳过此格') + '</button>';
      } else if (proposal?.reason_source && ['missing_supplement','check_supplement'].includes(proposal.status)) {
        const message = proposal.status === 'check_supplement' ? '已有附件，需核对相应补表' : '缺补表，待核';
        content = '<div class="cmp-ref"><span class="cmp-warning">' + ui(message) +
          (proposal.supplement_label ? ' · ' + escape(proposal.supplement_label) : '') + '</span>' +
          citation(proposal.reason_source,'reason',m,false) + '</div>';
      } else if (proposal) {
        content = '<small>' + ui('已保存的材料中未找到') + '</small>';
      } else content = '—';
      return '<td data-draft-paper="' + escape(row.paper_id) + '" data-draft-cohort="' + escape(cohort) + '" data-draft-col="' + column +
        '"><strong class="cmp-mobile-col">' + ui(labels[column]) + '</strong>' + content + '</td>';
    }
    function review(s) {
      const papers = new Map((s.preview?.papers || []).map(p => [p.paper_id,p]));
      const rows = s.draft.rows.filter(r => papers.has(r.paper_id) || r.cells?.length);
      if (!rows.length) return '';
      const visible = columns.filter(col => rows.some(r => r.cells?.some(c => c.column === col)) ||
        (s.preview && selected(s).includes(col)));
      const bodies = rows.map(row => {
        const paper = papers.get(row.paper_id);
        const cohorts = new Set([...(paper?.cohorts || []).map(g => g.cohort), ...(row.cells || []).map(c => c.cohort)]);
        const folded = s.folded?.has(row.paper_id);
        const groups = [...cohorts].map(cohort => {
          const proposals = paper?.cohorts.find(g => g.cohort === cohort)?.cells;
          return '<tr' + (folded ? ' hidden' : '') + '><th scope="row">' + (cohort ? escape(cohort) : ui('未注明队列')) + '</th>' +
            visible.map(col => cellHTML(s,row,cohort,col,proposals?.[col])).join('') + '</tr>';
        }).join('');
        return '<tbody><tr><th scope="rowgroup" colspan="' + (visible.length+1) + '"><button class="btn btn-ghost" type="button" aria-expanded="' +
          !folded + '" data-fold-paper="' + escape(row.paper_id) + '"><span aria-hidden="true">' + (folded ? '▸' : '▾') + '</span> ' +
          escape(paper?.title || row.title || row.paper_id) + '</button>' +
          (paper?.warnings || []).map(w => '<p class="cmp-warning">' + escape(w) + '</p>').join('') + '</th></tr>' + groups + '</tbody>';
      }).join('');
      return '<div class="cmp-table-wrap"><table class="cmp-review-table"><thead><tr><th scope="col">' + ui('论文与队列') + '</th>' +
        visible.map(col => '<th scope="col">' + ui(labels[col]) + '</th>').join('') + '</tr></thead>' + bodies + '</table></div>';
    }
    function fold(s,id) {
      if (s.folded.has(id)) s.folded.delete(id);
      else s.folded.add(id);
      refresh(s);
      s.root.querySelector('[data-fold-paper="' + id + '"]')?.focus?.();
    }
    async function build(s) {
      if (!s.draft.question.trim() || s.draft.rows.length < 2) return status(s,t('请先填写研究问题并选择至少两篇不同的论文。'),true);
      if (!selected(s).length) return status(s,t('请至少勾选一个比较字段。'),true);
      if (s.draft.rows.some(r => !s.papers.some(p => p.id === r.paper_id))) return status(s,t('请先移除已不在课题库中的论文，再起草。'),true);
      s.drafting = true; render(s);
      try {
        const preview = await request(base(s) + '/draft', {question:s.draft.question, paper_ids:s.draft.rows.map(r => r.paper_id), columns:selected(s)}, 'POST');
        clearPreview(s); s.preview = preview;
        s.drafting = false; render(s);
      } catch (err) {
        s.drafting = false; render(s); status(s,err.message,true);
      }
    }
    function context(s,el) {
      const data = el.closest('[data-draft-paper]')?.dataset;
      if (!data) return {};
      const {draftPaper:id, draftCohort:cohort, draftCol:column} = data;
      const row = s.draft.rows.find(r => r.paper_id === id);
      const proposal = s.preview?.papers.find(p => p.paper_id === id)?.cohorts.find(g => g.cohort === cohort)?.cells[column];
      const saved = row?.cells?.find(c => c.cohort === cohort && c.column === column);
      return {row,cohort,column,proposal,saved,key:cellKey(id,cohort,column)};
    }
    function input(s,el) {
      const {proposal,key,saved} = context(s,el);
      if (el.hasAttribute('data-accepted-value')) {
        if (saved) { saved.value = el.value; dirty(s); }
        return;
      }
      const candidate = proposal?.candidates[Number(el.dataset.draftValue)];
      if (candidate) s.edits.set(editKey(key,candidate),el.value);
    }
    function toggle(s,el) {
      const picked = new Set(selected(s));
      if (el.checked) picked.add(el.dataset.draftColumn);
      else picked.delete(el.dataset.draftColumn);
      s.draft.columns = columns.filter(col => picked.has(col));
      clearPreview(s); refresh(s); dirty(s);
    }
    function action(s,el) {
      const {row,cohort,column,proposal,saved,key} = context(s,el);
      if (!row) return;
      const index = el.dataset.index, candidate = proposal?.candidates?.[Number(index)];
      switch (el.dataset.review) {
        case 'source': {
          const src = index === 'accepted' ? saved?.source : index === 'reason' ? proposal?.reason_source : candidate?.source;
          const preview = s.preview;
          if (src) return jump(s,row.paper_id,src,() => index === 'accepted' ? row.cells?.includes(saved) : s.preview === preview);
          return;
        }
        case 'accept': {
          if (!candidate || saved) return;
          const value = (s.edits.get(editKey(key,candidate)) ?? candidate.value).trim();
          if (!value) return status(s,t('请填写要采纳的值。'),true);
          (row.cells ||= []).push({cohort,column,value,source_key:candidate.source.key,source:candidate.source});
          dirty(s); break;
        }
        case 'skip': s.skipped.add(key); break;
        case 'restore': s.skipped.delete(key); break;
        case 'remove':
          if (!saved || !global.confirm(t('从本次比较移除这个已采纳值？'))) return;
          row.cells = row.cells.filter(c => c !== saved);
          dirty(s); break;
        default: return;
      }
      refresh(s);
    }
    return {controls,clearPreview,refresh,build,toggle,input,action,fold,selected};
  }
  global.BioComparisonDraft = {create};
})(window);
