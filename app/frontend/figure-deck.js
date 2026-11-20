/* Editable presentation drafts are scoped to the open project. Source images stay in the paper store. */
(function (global) {
  'use strict';
  const sessions = new Map();
  const labels = {
    title: '英文标题',
    question: '研究问题',
    control: '关键对照',
    observation: '主要观察',
    limitation: '局限',
    notes: '中文讲者备注'
  };
  const english = {
    question: 'Research question',
    control: 'Key control',
    observation: 'Main observation',
    limitation: 'Limitation'
  };
  const limits = { title: 140, question: 220, control: 220, observation: 220, limitation: 220, notes: 12000 };
  const han = /[\u3400-\u9fff\u{20000}-\u{2fa1f}]/u;
  const englishText = (text) => /[A-Za-z]/.test(text) && !han.test(text);
  const t = (key, params = {}) =>
    global.BioI18n?.t(key, params) ?? key.replace(/\{(\w+)\}/g, (_, k) => String(params[k]));
  const sourceLabels = {
    paper_title: '论文题名',
    caption: '图注',
    observation: '读图笔记',
    reader: '读者补充资料'
  };
  global.BioI18n?.register({
    从读图笔记起草英文页和中文讲稿: 'Draft English slide and Chinese notes',
    '正在起草当前页…': 'Drafting this slide…',
    读者补充资料: 'Reader-supplied material',
    论文题名: 'Paper title',
    '读者补充资料（可选）': 'Additional material for the model (optional)',
    '点起草后，将当前图、题名、图注、已保存的读图笔记和补充资料发给配置的读图模型。':
      'Drafting sends only this figure, its title, caption, saved notes, and additional material to the configured vision model.',
    '点起草后，将当前图的题名、图注、已保存的读图笔记和补充资料发给配置的文本模型。':
      'Drafting sends only the title, caption, saved notes, and additional material for this figure to the configured text model.',
    '请先配置模型，或手工填写幻灯片。': 'Configure a model to draft a slide, or fill it in manually.',
    起草所引原句: 'Sources quoted for this draft',
    '补充资料已改变，请重新起草。': 'The additional material changed. Draft the slide again.',
    '可起草的字段均已手工填写。': 'All draft fields have been edited by the reader.',
    '采纳前请逐项核对原句。': 'Check each quoted passage before using the draft.',
    '当前材料没有可核对的新候选值。': 'No new values with matching source text were found.',
    '模型回复格式无效，请重试或手工填写。':
      'The model returned an invalid format. Retry or fill the slide manually.',
    '幻灯片起草超时，请重试或手工填写。': 'Drafting timed out. Retry or fill the slide manually.',
    '无法连接模型，请检查网络或服务地址。':
      'Could not reach the model. Check the network and service address.',
    组会幻灯片: 'Journal club slides',
    加入组会幻灯片: 'Add to slide deck',
    已选入幻灯片: 'Selected for slide deck',
    '选择收藏图后可编排英文幻灯片和中文讲稿。':
      'Select saved figures to prepare English slides with Chinese speaker notes.',
    幻灯片顺序: 'Slide order',
    上移: 'Move up',
    下移: 'Move down',
    从幻灯片移除: 'Remove from deck',
    英文标题: 'English title',
    中文讲者备注: 'Chinese speaker notes',
    主要观察: 'Main observation',
    局限: 'Limitation',
    预览幻灯片: 'Preview slide',
    编辑幻灯片: 'Edit slide',
    英文幻灯片内容: 'English slide text',
    图注和已保存的读图笔记: 'Caption and saved figure notes',
    '选取一段原文后加入字段。': 'Select source text, then add it to a field.',
    加入字段: 'Add to field',
    '没有已保存的读图笔记。': 'No saved figure notes.',
    '请填写英文内容，中文讲者备注另填。':
      'Enter English slide text and Chinese speaker notes in their respective fields.',
    需要补写: 'Text needed',
    '导出可编辑 PPTX': 'Export editable PPTX',
    '正在导出…': 'Exporting…',
    '第 {page} 张还需填写：{field}': 'Complete slide {page}: {field}',
    '请选择要填入的原句。': 'Select a passage to insert.',
    '所选文字超出该字段长度，请缩短选段。':
      'The passage is too long for this field. Select a shorter passage.',
    '每次最多选择 50 张收藏图。': 'Select up to 50 figures per deck.',
    '放弃这张幻灯片的编辑并移除？': 'Discard edits and remove this slide?',
    '收藏图已变更，请移除后重新选图。': 'The saved figure changed. Remove it and select it again.',
    '图片、图注或读图笔记已更新，请重新选图核对。':
      'The image, caption, or saved notes changed. Select the figure again to review.',
    '收藏图片无法读取，请重新选图。': 'The saved image cannot be read. Select the figure again.',
    '所选图片合计超过 96 MB，请减少幻灯片后导出。': 'The selected images exceed 96 MB. Export fewer slides.',
    'PPTX 导出失败': 'Could not export the presentation',
    '等待图片…': 'Loading figure…'
  });
  function node(tag, text, cls) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }
  function ui(tag, key, cls) {
    const el = node(tag, t(key), cls);
    el.dataset.i18n = key;
    return el;
  }
  function button(key, fn, cls = 'btn') {
    const el = ui('button', key, cls);
    el.type = 'button';
    el.addEventListener('click', fn);
    return el;
  }
  function defaults(source) {
    const fields = Object.fromEntries(Object.keys(labels).map((k) => [k, '']));
    if (/^(?:figure|fig\.?|table)\s*S?\d+[a-z]?$/i.test(source.label)) fields.title = source.label;
    const notes = source.observation.trim();
    if (source.observation_language === 'en' && englishText(notes) && notes.length <= limits.observation)
      fields.observation = notes;
    if (han.test(notes) && source.observation_language !== 'en' && notes.length <= limits.notes)
      fields.notes = notes;
    return fields;
  }
  function incomplete(fields) {
    return Object.keys(labels).find(
      (k) =>
        !fields[k]?.trim() ||
        fields[k].length > limits[k] ||
        !(k === 'notes' ? han.test(fields[k]) : englishText(fields[k]))
    );
  }
  function open(container, scope, hooks) {
    let state = sessions.get(scope);
    if (!state) {
      state = { slides: [], active: '', preview: false, busy: false };
      sessions.set(scope, state);
    }
    const panel = node('section', undefined, 'deck-panel'),
      report = node('p', '', 'hint');
    report.setAttribute('role', 'status');
    const workspace = node('div');
    panel.append(ui('h3', '组会幻灯片'), workspace, report);
    const find = (key) => state.slides.find((slide) => slide.key === key);
    const refKey = (fig) => fig.paper_id + '/' + fig.id;
    state.refresh = render;
    function toggle(fig) {
      if (state.busy) return;
      const key = refKey(fig),
        existing = find(key);
      if (existing) return remove(existing);
      if (state.slides.length >= 50) {
        report.textContent = t('每次最多选择 50 张收藏图。');
        return;
      }
      const ref = { paper_id: fig.paper_id, id: fig.id, shelf_key: fig.shelf_key };
      const slide = {
        key,
        ref,
        name: fig.label,
        fields: null,
        source: null,
        edited: false,
        manual: new Set(),
        evidence: {},
        readerMaterial: ''
      };
      state.slides.push(slide);
      state.active = key;
      state.preview = false;
      report.textContent = '';
      render();
      return hooks
        .previewDeck(scope, ref)
        .then((source) => {
          slide.source = source;
          slide.fields = defaults(source);
        })
        .catch((err) => {
          state.slides = state.slides.filter((item) => item !== slide);
          hooks.toast(t(err.message), 'error');
        })
        .finally(() => state.refresh());
    }
    function remove(slide) {
      if (state.busy || (slide.edited && !global.confirm(t('放弃这张幻灯片的编辑并移除？')))) return;
      state.slides = state.slides.filter((item) => item !== slide);
      if (state.active === slide.key) state.active = '';
      render();
    }
    function move(slide, to) {
      const from = state.slides.indexOf(slide);
      if (state.busy || from < 0 || to < 0 || to >= state.slides.length || from === to) return;
      state.slides.splice(from, 1);
      state.slides.splice(to, 0, slide);
      render();
    }
    function updatePick(el) {
      const on = !!find(el.dataset.deckPick),
        key = on ? '已选入幻灯片' : '加入组会幻灯片';
      el.textContent = t(key);
      el.dataset.i18n = key;
      el.setAttribute('aria-pressed', String(on));
      el.disabled = state.busy;
    }
    function pick(fig) {
      const el = button('加入组会幻灯片', () => toggle(fig));
      el.dataset.deckPick = refKey(fig);
      updatePick(el);
      return el;
    }
    function order(slide, index) {
      const row = node('li');
      row.draggable = !state.busy;
      row.addEventListener('dragstart', (e) => e.dataTransfer.setData('text/plain', slide.key));
      row.addEventListener('dragover', (e) => e.preventDefault());
      row.addEventListener('drop', (e) => {
        e.preventDefault();
        move(find(e.dataTransfer.getData('text/plain')), index);
      });
      const choose = node(
        'button',
        index + 1 + '. ' + slide.name + ' · ' + (slide.source?.paper_title || t('等待图片…')),
        'btn btn-ghost'
      );
      choose.type = 'button';
      choose.setAttribute('aria-pressed', String(state.active === slide.key));
      choose.onclick = () => {
        state.active = slide.key;
        render();
      };
      const up = button('上移', () => move(slide, index - 1)),
        down = button('下移', () => move(slide, index + 1));
      up.disabled = index === 0;
      down.disabled = index === state.slides.length - 1;
      row.append(
        choose,
        up,
        down,
        button('从幻灯片移除', () => remove(slide), 'btn btn-ghost')
      );
      return row;
    }
    function reference(slide) {
      const details = node('details');
      details.append(ui('summary', '图注和已保存的读图笔记'), ui('p', '选取一段原文后加入字段。', 'hint'));
      for (const [title, value] of [
        ['图注', slide.source.caption],
        ['读图笔记', slide.source.observation]
      ]) {
        if (!value) continue;
        const label = node('label'),
          source = node('textarea');
        source.readOnly = true;
        source.rows = 4;
        source.value = value;
        const picker = node('select');
        picker.setAttribute('aria-label', t('加入字段'));
        for (const [k, key] of Object.entries(labels)) {
          const opt = ui('option', key);
          opt.value = k;
          picker.append(opt);
        }
        const add = button('加入字段', () => {
          const part = source.value.slice(source.selectionStart, source.selectionEnd).trim(),
            k = picker.value;
          if (!part) {
            report.textContent = t('请选择要填入的原句。');
            return;
          }
          const text = [slide.fields[k], part].filter(Boolean).join(k === 'notes' ? '\n' : ' ');
          if (text.length > limits[k]) {
            report.textContent = t('所选文字超出该字段长度，请缩短选段。');
            return;
          }
          if (k === 'notes' ? !han.test(text) : !englishText(text)) {
            report.textContent = t('请填写英文内容，中文讲者备注另填。');
            return;
          }
          slide.fields[k] = text;
          slide.manual.add(k);
          slide.edited = true;
          report.textContent = '';
          render();
        });
        label.append(ui('span', title), source);
        const actions = node('div', undefined, 'deck-actions');
        actions.append(picker, add);
        details.append(label, actions);
      }
      return details;
    }
    function drafting(slide) {
      const area = node('div', undefined, 'deck-draft');
      const mode = hooks.modelMode?.();
      if (!mode) {
        area.append(ui('p', '请先配置模型，或手工填写幻灯片。', 'hint'));
        return area;
      }
      const narrative =
        mode === 'vision'
          ? '点起草后，将当前图、题名、图注、已保存的读图笔记和补充资料发给配置的读图模型。'
          : '点起草后，将当前图的题名、图注、已保存的读图笔记和补充资料发给配置的文本模型。';
      const label = node('label'),
        extra = node('textarea');
      extra.maxLength = 2400;
      extra.rows = 2;
      extra.value = slide.readerMaterial;
      extra.oninput = () => {
        slide.readerMaterial = extra.value;
        slide.edited = true;
      };
      label.append(ui('span', '读者补充资料（可选）'), extra);
      const start = button(slide.suggesting ? '正在起草当前页…' : '从读图笔记起草英文页和中文讲稿', () =>
        suggest(slide)
      );
      start.disabled = !!slide.suggesting;
      area.append(label, ui('p', narrative, 'hint'), start);
      if (Object.keys(slide.evidence).length) {
        const sources = node('details');
        sources.append(ui('summary', '起草所引原句'), ui('p', '采纳前请逐项核对原句。', 'hint'));
        for (const [k, item] of Object.entries(slide.evidence)) {
          const info = node('p');
          info.append(
            ui('strong', labels[k]),
            ' · ',
            ui('span', sourceLabels[item.source]),
            ' · ' + item.model
          );
          sources.append(info, node('blockquote', item.quote));
        }
        area.append(sources);
      }
      return area;
    }
    async function suggest(slide) {
      if (slide.suggesting || state.busy) return;
      const fields = Object.keys(labels).filter((k) => !slide.manual.has(k));
      if (!fields.length) {
        report.textContent = t('可起草的字段均已手工填写。');
        return;
      }
      const extra = slide.readerMaterial;
      slide.suggesting = true;
      report.textContent = '';
      render();
      try {
        const result = await hooks.draftSlide(scope, {
          ...slide.ref,
          revision: slide.source.revision,
          fields,
          reader_material: extra
        });
        if (find(slide.key) !== slide) return;
        if (slide.readerMaterial !== extra) {
          report.textContent = t('补充资料已改变，请重新起草。');
          return;
        }
        let added = 0;
        for (const [k, item] of Object.entries(result.fields)) {
          if (!fields.includes(k) || slide.manual.has(k)) continue;
          slide.fields[k] = item.value;
          slide.evidence[k] = { ...item, model: result.model };
          added++;
        }
        if (added) slide.edited = true;
        report.textContent = t(added ? '采纳前请逐项核对原句。' : '当前材料没有可核对的新候选值。');
      } catch (err) {
        report.textContent = t(err.message);
      } finally {
        slide.suggesting = false;
        state.refresh();
      }
    }
    function field(slide, k) {
      const wrap = node('label'),
        value = slide.fields[k];
      wrap.append(ui('span', labels[k]));
      if (state.preview) {
        wrap.append(value.trim() ? node('p', value) : ui('p', '需要补写', 'hint'));
      } else {
        const input = node('textarea');
        input.rows = k === 'notes' ? 4 : k === 'title' ? 1 : 2;
        input.maxLength = limits[k];
        input.value = value;
        input.dataset.deckField = k;
        input.lang = k === 'notes' ? 'zh-CN' : 'en';
        input.oninput = () => {
          slide.fields[k] = input.value;
          slide.manual.add(k);
          slide.edited = true;
          report.textContent = '';
        };
        wrap.append(input);
      }
      return wrap;
    }
    function render() {
      workspace.replaceChildren();
      container.querySelectorAll('[data-deck-pick]').forEach(updatePick);
      if (!state.slides.length) {
        workspace.append(ui('p', '选择收藏图后可编排英文幻灯片和中文讲稿。', 'hint'));
        return;
      }
      const active = find(state.active) || state.slides[0];
      state.active = active.key;
      const deck = node('fieldset', undefined, 'deck-controls');
      deck.disabled = state.busy;
      const list = node('ol', undefined, 'deck-order');
      list.setAttribute('aria-label', t('幻灯片顺序'));
      list.append(...state.slides.map(order));
      deck.append(list);
      if (active.source) {
        const actions = node('div', undefined, 'deck-actions');
        actions.append(
          button(state.preview ? '编辑幻灯片' : '预览幻灯片', () => {
            state.preview = !state.preview;
            render();
          })
        );
        const canvas = node('div', undefined, 'deck-canvas'),
          picture = node('img');
        picture.alt = active.name;
        picture.src = hooks.image(active.ref.paper_id, active.ref.id) + '?revision=' + active.source.revision;
        const content = node('div', undefined, 'deck-copy');
        for (const k of Object.keys(english)) {
          if (state.preview) {
            content.append(node('strong', english[k]), node('p', active.fields[k] || t('需要补写')));
          } else content.append(field(active, k));
        }
        const title = state.preview
          ? node('h3', active.fields.title || t('需要补写'))
          : field(active, 'title');
        title.className = 'deck-title';
        const source = active.source,
          footer = node(
            'small',
            [source.paper_title, source.label, source.document_name, 'PDF p. ' + source.page].join(' · '),
            'deck-citation'
          );
        canvas.append(title, picture, content, footer);
        deck.append(actions, canvas, field(active, 'notes'), drafting(active));
        if (!state.preview) deck.append(reference(active));
      } else deck.append(ui('p', '等待图片…'));
      const exportButton = button(
        state.busy ? '正在导出…' : '导出可编辑 PPTX',
        exportDeck,
        'btn btn-primary'
      );
      exportButton.disabled = state.busy || state.slides.some((s) => !s.source || s.suggesting);
      deck.append(exportButton);
      workspace.append(deck);
    }
    async function exportDeck() {
      if (state.busy || state.slides.some((s) => !s.source || s.suggesting)) return;
      for (const [index, slide] of state.slides.entries()) {
        const missing = incomplete(slide.fields);
        if (missing) {
          state.active = slide.key;
          state.preview = false;
          render();
          report.textContent = t('第 {page} 张还需填写：{field}', {
            page: index + 1,
            field: t(labels[missing])
          });
          workspace.querySelector('[data-deck-field="' + missing + '"]')?.focus();
          return;
        }
      }
      state.busy = true;
      report.textContent = '';
      render();
      try {
        const slides = state.slides.map((s) => ({ ...s.ref, revision: s.source.revision, ...s.fields }));
        await hooks.exportDeck(scope, { slides });
      } catch (err) {
        report.textContent = t(err.message);
      } finally {
        state.busy = false;
        state.refresh();
      }
    }
    render();
    return { panel, pick };
  }
  global.BioFigureDeck = { open };
})(window);
