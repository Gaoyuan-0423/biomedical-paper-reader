/* Reader decisions and the exact figure inputs of the saved report. */
(function (global) {
  'use strict';
  const words = {
    核对这句话: 'Review this claim',
    本次读图: 'Figure coverage',
    为这几篇补正文: 'Get full text for these papers',
    记为证据: 'Save as evidence',
    复核后的结论: 'Reviewed claim',
    保存修改及依据: 'Save revision and evidence',
    修改依据: 'Reason for decision',
    证据支持程度: 'Evidence strength',
    依据不足: 'Insufficient evidence',
    观察性关联: 'Observational association',
    干预证据支持: 'Supported by intervention',
    需限定适用范围: 'Qualified support',
    干预实验: 'Intervention experiments',
    尚待确认: 'Not yet confirmed',
    已找到干预实验: 'Intervention found',
    已核对片段未见干预: 'No intervention in reviewed excerpts',
    关联图与图注: 'Candidate figures and captions',
    结论所引原句: 'Excerpts cited near this claim',
    干预相关候选片段: 'Possible intervention excerpts',
    方法与设计候选片段: 'Possible methods and design excerpts',
    其他相关原句: 'Other relevant excerpts',
    查看原页: 'Open PDF page',
    未找到相应片段: 'No matching excerpts found',
    该文献没有可定位的图: 'No locatable figures in this paper',
    '材料过长或缺少可读文本，本次检索范围有限。': 'Some material exceeds the search limit or has no readable text.',
    '正在查找图表、方法和相关原句…': 'Finding figures, methods and source excerpts…',
    '选择原句或图表作为复核依据。': 'Select the excerpts or figures that support your review.',
    之前的复核: 'Previous reviews',
    原结论: 'Original claim',
    '本版报告生成时没有读图记录。': 'No generation-time coverage was recorded for this report.',
    软件已识别的整图: 'Whole figures identified by the reader',
    手动补入: 'Manually added',
    视觉观察: 'Visual observation supplied',
    仅用图注: 'Caption only',
    未处理: 'Not processed',
    读图失败: 'Visual read failed',
    补读这张图: 'Read this figure',
    补入漏提取的图: 'Capture a missing figure',
    '报告生成时送入模型的图表材料。补读后可重新生成报告。':
      'Figure material supplied when generating this report. Regenerate the report to include later readings.',
    '请在「编辑」中重新选中原句，以确定需要修改的位置。': 'Select the sentence in Edit to identify the exact source position.',
    '先在报告预览或编辑框选中一句话。': 'Select a sentence in the report preview or editor first.',
    '保存当前笔记后再复核。': 'Save the current note before reviewing it.',
    复核已保存: 'Review saved',
    当前原图已变化: 'Source figure changed',
    '报告有新的修改，请重新选句。': 'Report changed. Select the claim again.',
    关闭复核: 'Close review',
    '需要先保存或取消当前复核。': 'Save or close the current review first.',
    '报告生成后有人工编辑，以下清单属于生成时版本。':
      'This report has since been edited. The list describes the generated version.',
    正文图: 'Main figures',
    补充图: 'Supplementary figures',
    原文证据: 'Source evidence',
    '取图时先选择对应 PDF 和页面。': 'Select the PDF and page before cropping.',
  };
  global.BioI18n?.register(words);
  const t = (k) => global.BioI18n?.t(k) ?? k;
  const el = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  };
  const ui = (tag, key, cls) => {
    const n = el(tag, t(key), cls);
    n.dataset.i18n = key;
    return n;
  };
  function button(key, action, primary = false) {
    const b = ui('button', key, primary ? 'btn btn-primary' : 'btn');
    b.type = 'button';
    b.onclick = action;
    return b;
  }
  function section(key, open) {
    const d = el('details');
    d.open = !!open;
    d.append(ui('summary', key));
    return d;
  }
  function field(key, control) {
    const l = el('label', undefined, 'review-field');
    l.append(ui('span', key), control);
    return l;
  }
  function options(labels) {
    const s = el('select');
    for (const [k, v] of Object.entries(labels)) {
      const o = ui('option', v);
      o.value = k;
      s.append(o);
    }
    return s;
  }
  const support = {
    insufficient: '依据不足',
    association: '观察性关联',
    intervention: '干预证据支持',
    qualified: '需限定适用范围',
  };
  const experiment = {
    unconfirmed: '尚待确认',
    present: '已找到干预实验',
    absent_in_reviewed_sources: '已核对片段未见干预',
  };
  let hooks, dialog, body, msg, session;
  function close() {
    if (dialog.open) dialog.close();
  }
  function begin(key, paper) {
    session = {id: paper.id, rev: paper.notes_revision, busy: false};
    body.replaceChildren();
    msg.textContent = '';
    dialog.querySelector('h2').textContent = t(key);
    if (!dialog.open) dialog.showModal();
    return session;
  }
  function current(s) {
    return s === session && hooks.paper()?.id === s.id;
  }
  function issue(error) {
    msg.textContent = error.message || String(error);
  }
  function history(records) {
    if (!records?.length) return;
    const list = section('之前的复核');
    body.append(list);
    for (const r of [...records].reverse()) {
      const card = el('article', undefined, 'review-item');
      card.append(
        el('blockquote', r.original),
        el('strong', r.replacement),
        el('p', t(support[r.support]) + ' · ' + t(experiment[r.intervention])),
        el('p', r.reason),
      );
      for (const src of r.sources)
        card.append(el('small', src.document_name + ' · p. ' + src.page), el('blockquote', src.quote || src.label));
      list.append(card);
    }
  }
  function materialGroup(s, title, items, open, selected) {
    const details = section(title, open);
    body.append(details);
    if (!items.length) {
      details.append(ui('p', title === '关联图与图注' ? '该文献没有可定位的图' : '未找到相应片段', 'hint'));
      return;
    }
    for (const src of items) {
      const card = el('article', undefined, 'review-item'),
        label = el('label'),
        chk = el('input');
      chk.type = 'checkbox';
      chk.checked = selected.has(src.key);
      chk.dataset.sourceKey = src.key;
      chk.onchange = () => {
        if (chk.checked) selected.add(src.key);
        else selected.delete(src.key);
        for (const other of body.querySelectorAll('input[data-source-key]'))
          if (other.dataset.sourceKey === src.key) other.checked = chk.checked;
      };
      label.append(chk, el('strong', [src.label, src.document_name, 'p. ' + src.page].filter(Boolean).join(' · ')));
      card.append(label);
      if (src.figure_id) {
        const img = el('img');
        img.loading = 'lazy';
        img.src = hooks.image(s.id, src.figure_id);
        img.alt = src.label;
        card.append(img);
      }
      card.append(
        el('blockquote', src.quote),
        button('查看原页', async () => {
          try {
            await hooks.locate(s.id, src);
            if (!current(s)) return;
            close();
            await hooks.jump(s.id, src);
          } catch (e) {
            issue(e);
          }
        }),
      );
      details.append(card);
    }
  }
  // A visible string can occur in another paragraph, link target or hidden
  // Markdown comment. Probe the actual sanitized rendering at each literal
  // match and require both boundaries to appear exactly in the selected block.
  function matchPreviewPosition(raw, original, before, after, renderedBlock) {
    if (!original || renderedBlock(raw) !== before + original + after) return null;
    let open = '\uE000', close = '\uE001';
    while (raw.includes(open) || (before + original + after).includes(open)) open += '\uE000';
    while (raw.includes(close) || (before + original + after).includes(close)) close += '\uE001';
    const expected = before + open + original + close + after;
    let match = null, attempts = 0;
    for (let i = raw.indexOf(original); i !== -1; i = raw.indexOf(original, i + 1)) {
      if (++attempts > 12) return null;
      const probe = raw.slice(0,i) + open + original + close + raw.slice(i+original.length);
      if (renderedBlock(probe) === expected) {
        if (match !== null) return null;
        match = Array.from(raw.slice(0,i)).length;
      }
    }
    return match;
  }

  function previewSelection(preview, editorText, range) {
    const blocks = 'p,li,h1,h2,h3,h4,h5,h6,td,th,blockquote';
    const nearest = node => (node.nodeType === 1 ? node : node.parentElement)?.closest(blocks);
    const block = nearest(range.startContainer);
    if (!block || !preview.contains(block) || block !== nearest(range.endContainer) || !global.BioMarkdown)
      return {unmapped:true};
    const original = range.toString(), left = range.cloneRange(), right = range.cloneRange();
    left.selectNodeContents(block); left.setEnd(range.startContainer,range.startOffset);
    right.selectNodeContents(block); right.setStart(range.endContainer,range.endOffset);
    const index = Array.from(preview.querySelectorAll(blocks)).indexOf(block);
    const renderedBlock = markdown => {
      // Template contents are inert, including any locally referenced figures.
      const template = document.createElement('template');
      template.innerHTML = global.BioMarkdown.parse(markdown);
      const parsed = template.content.querySelectorAll(blocks)[index];
      return parsed?.tagName === block.tagName ? parsed.textContent : null;
    };
    const start = matchPreviewPosition(editorText,original,left.toString(),right.toString(),renderedBlock);
    return start === null ? {unmapped:true} : {original,start};
  }

  async function review(paper, selection) {
    if (selection?.unmapped) {
      hooks.toast(t('请在「编辑」中重新选中原句，以确定需要修改的位置。'), 'error');
      return;
    }
    if (!selection?.original) {
      if (session?.form && current(session) && session.rev === paper.notes_revision) {
        dialog.showModal();
        return;
      }
      hooks.toast(t('先在报告预览或编辑框选中一句话。'), 'error');
      return;
    }
    const s = begin('核对这句话', paper),
      query = {...selection, notes_revision: s.rev};
    s.form = true;
    msg.textContent = t('正在查找图表、方法和相关原句…');
    try {
      const [preview, overview] = await Promise.all([hooks.api(s.id, '/preview', query), hooks.api(s.id, '')]);
      if (!current(s)) return;
      msg.textContent = '';
      body.append(ui('strong', '原结论'), el('blockquote', selection.original));
      const replacement = el('textarea'),
        reason = el('textarea'),
        strength = options(support),
        tested = options(experiment);
      replacement.value = selection.original;
      replacement.maxLength = reason.maxLength = 4000;
      const selected = new Set(),
        key = Array.from(crypto.getRandomValues(new Uint8Array(16)), (x) => x.toString(16).padStart(2, '0')).join('');
      body.append(
        field('复核后的结论', replacement),
        field('证据支持程度', strength),
        field('干预实验', tested),
        field('修改依据', reason),
        ui('p', '选择原句或图表作为复核依据。', 'hint'),
      );
      for (const [group, label] of [
        ['citation', '结论所引原句'],
        ['figure', '关联图与图注'],
        ['methods', '方法与设计候选片段'],
        ['intervention', '干预相关候选片段'],
        ['excerpt', '其他相关原句'],
      ]) {
        materialGroup(
          s,
          label,
          preview.sources.filter((v) => v.groups.includes(group)),
          group === 'citation' && preview.sources.some((v) => v.groups.includes(group)),
          selected,
        );
      }
      if (preview.truncated) body.append(ui('p', '材料过长或缺少可读文本，本次检索范围有限。', 'hint'));
      const save = button(
        '保存修改及依据',
        async () => {
          if (s.busy || !current(s)) return;
          s.busy = save.disabled = true;
          try {
            const result = await hooks.apply(s.id, {
              ...query,
              replacement: replacement.value,
              reason: reason.value,
              support: strength.value,
              intervention: tested.value,
              keys: [...selected],
              request_id: key,
            });
            if (current(s)) {
              close();
              session = null;
              hooks.saved(result);
              hooks.toast(t('复核已保存'));
            }
          } catch (e) {
            issue(e);
          } finally {
            s.busy = save.disabled = false;
          }
        },
        true,
      );
      body.append(save);
      history(overview.reviews);
    } catch (e) {
      if (current(s)) issue(e);
    }
  }
  async function coverage(paper) {
    const s = begin('本次读图', paper);
    try {
      const data = await hooks.api(s.id, '');
      if (!current(s)) return;
      const cov = data.coverage;
      if (!cov) {
        body.append(ui('p', '本版报告生成时没有读图记录。'));
      } else {
        if (cov.notes_revision && cov.notes_revision !== data.notes_revision)
          body.append(ui('p', '报告生成后有人工编辑，以下清单属于生成时版本。', 'hint'));
        const rows = cov.figures;
        body.append(
          ui('p', '报告生成时送入模型的图表材料。补读后可重新生成报告。', 'hint'),
          el(
            'strong',
            t('软件已识别的整图') +
              ' ' +
              rows.filter((f) => !f.manual).length +
              ' · ' +
              t('手动补入') +
              ' ' +
              rows.filter((f) => f.manual).length,
          ),
        );
        for (const [key, main] of [
          ['正文图', true],
          ['补充图', false],
        ]) {
          const list = rows.filter((f) => (f.document_id === 'main') === main),
            group = section(key, true);
          body.append(group);
          group.firstChild.append(' (' + list.length + ')');
          for (const f of list) {
            const line = el('article', undefined, 'review-item');
            line.append(
              el('strong', f.label + ' · ' + f.document_name + ' · p. ' + f.page),
              el(
                'p',
                t({visual: '视觉观察', caption: '仅用图注', none: '未处理'}[f.used]) +
                  (f.failed ? ' · ' + t('读图失败') : ''),
              ),
            );
            if (f.available)
              line.append(
                button('补读这张图', async () => {
                  try {
                    await hooks.locate(s.id,{...f,figure_id:f.id,bbox:f.identity[3],source_revision:f.identity[5]});
                    if (!current(s)) return;
                    close();
                    hooks.read(s.id, f.id);
                  } catch (e) {
                    issue(e);
                  }
                }),
              );
            else line.append(ui('small', '当前原图已变化'));
            group.append(line);
          }
        }
      }
      body.append(
        button('补入漏提取的图', () => {
          close();
          hooks.crop(s.id);
        }),
      );
      history(data.reviews);
    } catch (e) {
      if (current(s)) issue(e);
    }
  }
  function init(h) {
    hooks = h;
    dialog = document.getElementById('report-review-dialog');
    if (!dialog) return;
    body = dialog.querySelector('.review-body');
    msg = dialog.querySelector('[role=status]');
    dialog.querySelector('[data-close-review]').onclick = close;
    dialog.addEventListener('cancel', (e) => {
      if (session?.busy) e.preventDefault();
    });
  }
  global.BioReportReview = {init, review, coverage, previewSelection};
})(window);
