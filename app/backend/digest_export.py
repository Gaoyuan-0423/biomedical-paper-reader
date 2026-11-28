"""Build an offline library overview using only material already stored in BioReader."""
import base64
import io
import json
import secrets
import tempfile
from html import escape

from fastapi import HTTPException
from markdown_it import MarkdownIt
from PIL import Image

import library_digest
from note_sources import portable_notes
from reading_workflow import storage_lock


def text(value):
    return escape(str(value or ''), quote=True)


def portable_note_html(directory, meta):
    notes = meta.get('notes', '')
    if not notes.strip():
        return ''
    try:
        note = portable_notes(directory, meta, notes)
    except HTTPException:
        note = portable_notes(directory, {**meta, 'notes_source_snapshot': ''}, notes)
    # Retain link text, tables and headings. The gallery embeds the validated figures.
    renderer = MarkdownIt('commonmark', {'html': False}).enable('table')
    renderer.renderer.rules['image'] = lambda tokens, i, *_: text(tokens[i].content)
    renderer.renderer.rules['link_open'] = lambda *_: '<span>'
    renderer.renderer.rules['link_close'] = lambda *_: '</span>'
    return renderer.render(note)


def thumbnail(open_image):
    try:
        with open_image() as source, Image.open(source) as image:
            image.thumbnail((1280, 1000), Image.Resampling.LANCZOS)
            rgb = Image.new('RGB', image.size, 'white')
            if 'A' in image.getbands() or 'transparency' in image.info:
                rgba = image.convert('RGBA')
                rgb.paste(rgba, mask=rgba.getchannel('A'))
            else:
                rgb.paste(image)
            buf = io.BytesIO()
            rgb.save(buf, format='JPEG', quality=85, optimize=True)
        return 'data:image/jpeg;base64,' + base64.b64encode(buf.getbuffer()).decode('ascii')
    except (HTTPException, OSError, ValueError, Image.DecompressionBombError):
        return ''


def topics_attribute(topics):
    return text(json.dumps(topics, ensure_ascii=False))


def paper_html(paper, topics, memberships, note_html, status):
    pid, title = text(paper['id']), text(paper['title'])
    yield f'<article class="paper" data-entry="papers" data-topics="{topics_attribute(topics)}" id="paper-{pid}" tabindex="-1"><h3>{title}</h3>'
    bibliographic = ' · '.join(v for v in (', '.join(paper['authors']), paper['year'], paper['journal'], paper['doi']) if v)
    yield f'<p class="meta">{text(bibliographic)}</p>'
    if memberships:
        yield '<p class="meta">所属文献库 · ' + text('、'.join(memberships)) + '</p>'
    if topics:
        yield '<p class="topics">' + text(' · '.join(topics)) + '</p>'
    if paper['note_title'] and paper['note_title'] != paper['title']:
        yield '<p>笔记题名 · ' + text(paper['note_title']) + '</p>'
    if status:
        yield '<p class="status">' + text(status) + '</p>'
    if paper['conclusion']:
        label = '速读笔记 · 暂定发现' if paper['conclusion_source'] == 'quick_notes' else '已保存笔记 · 一句话结论'
        yield '<div class="takeaway"><b>' + label + '</b><p>' + text(paper['conclusion']) + '</p></div>'
    elif paper['note_excerpt'] or paper['abstract_excerpt']:
        label, value = ('摘要摘录', paper['abstract_excerpt']) if paper['abstract_excerpt'] else ('笔记摘录', paper['note_excerpt'])
        yield '<div class="takeaway"><b>' + label + '</b><p>' + text(value) + '</p></div>'
    if paper['sections']:
        yield '<details><summary>展开结构化卡片</summary><div class="modules">'
        for section in paper['sections']:
            yield '<section class="module-block"><h4>' + text(section['label']) + '</h4><div class="module">' + text(section['text'] or '暂无对应摘录') + '</div></section>'
        yield '</div></details>'
    if note_html:
        yield '<details><summary>完整笔记</summary><div class="note">' + note_html + '</div></details>'
    if paper['figure_count']:
        yield f'<div class="foot"><button type="button" data-paper-figures="{pid}">查看本篇插图（{int(paper["figure_count"])}）</button></div>'
    yield '</article>'


def figure_html(figure, paper, preview):
    topics = paper['tags']
    title = figure['paper_title']
    label = figure['label']
    location = ' · '.join(str(x) for x in (figure.get('document_name'), f"第 {figure['page']} 页" if figure.get('page') else '') if x)
    yield f'<figure class="figure" data-entry="figures" data-origin="{text(paper["id"])}" data-topics="{topics_attribute(topics)}">'
    if preview:
        alt = text(title + ' · ' + label)
        yield f'<button class="image-button" type="button" aria-label="放大 {alt}"><img loading="lazy" src="{preview}" alt="{alt}"></button>'
    else:
        yield '<p class="status">此图没有可嵌入的图像。</p>'
    yield '<figcaption class="figure-body"><h3>' + text(label) + '</h3><p class="meta">' + text(location) + '</p>'
    if paper['authors']:
        yield '<p class="meta">' + text(', '.join(paper['authors'])) + '</p>'
    labels = list(dict.fromkeys([*topics, *figure['tags'], figure['kind']]))
    if any(labels):
        yield '<p class="topics">' + text(' · '.join(filter(None, labels))) + '</p>'
    if figure.get('caption'):
        yield '<p>' + text(figure['caption']) + '</p>'
    if figure.get('observation'):
        yield '<details><summary>已保存的读图记录</summary><div class="module">' + text(figure['observation']) + '</div></details>'
    pid = text(figure['paper_id'])
    yield f'<div class="foot"><a href="#paper-{pid}" data-paper-link="paper-{pid}">' + text(title) + '</a></div></figcaption></figure>'


def page_header(name, description, stats, topics):
    yield '<header><div class="wrap"><p class="meta">BioReader · 离线文献库</p><h1>' + text(name) + '</h1>'
    if description:
        yield '<p>' + text(description) + '</p>'
    yield '<div class="stats">' + ''.join(f'<span><b>{int(value)}</b>{text(label)}</span>' for label, value in stats) + '</div></div></header>'
    yield '<main class="wrap"><nav aria-label="浏览内容"><button type="button" data-view="papers">论文卡片</button><button type="button" data-view="figures">插图画廊</button></nav>'
    yield '<div class="tools"><input id="search" type="search" placeholder="搜索论文、笔记或图注" aria-label="搜索"><select id="topics" aria-label="按主题筛选"><option value="">全部主题</option>'
    for topic in topics:
        yield '<option value="' + text(topic) + '">' + text(topic) + '</option>'
    yield '</select><span class="status" role="status" id="result-count"></span></div>'


def export_stream(template, title, body):
    """Spool a complete document before download without collecting all picture data in RAM."""
    layout = template.read_text('utf-8').replace('LOCAL_NONCE', secrets.token_urlsafe(20))
    before, after = layout.split('<!--DIGEST_BODY-->')
    stream = tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024, mode='w+b')
    try:
        stream.write(before.replace('<!--DIGEST_TITLE-->', text(title)).encode())
        for fragment in body:
            stream.write(fragment.encode())
        stream.write(after.encode())
        stream.seek(0)
        return stream
    except BaseException:
        stream.close()
        raise


def build_export(root, papers_dir, scope, template):
    captured = {}
    def capture(directory):
        meta = library_digest.read_digest_meta(directory)
        captured[directory.name] = meta
        return meta

    with storage_lock:
        digest = library_digest.build_digest(root, papers_dir, scope, capture)
    library, counts = digest['library'], digest['counts']
    papers = {paper['id']: paper for paper in digest['papers']}
    def open_image(figure):
        with storage_lock:
            path = library_digest.figure_image_path(papers_dir, figure)
            if path is None:
                raise FileNotFoundError()
            return path.open('rb')

    def body():
        stats = [('篇论文', counts['papers']), ('个标签主题', len(digest['topics'])), ('篇有笔记', counts['with_notes']),
                 ('张插图', counts['figures']), ('篇有正文', counts['with_pdf'])]
        yield from page_header(library['name'], library['topic'], stats, [t['name'] for t in digest['topics']])
        warnings = [('篇论文记录无法读取', 'unavailable'), ('篇论文的插图目录无法读取', 'damaged_figure_records'),
                    ('份文献库记录无法读取', 'unavailable_libraries')]
        for message, key in warnings:
            if counts[key]:
                yield '<p class="status">' + str(counts[key]) + message + '</p>'
        yield '<section id="papers"><h2>论文卡片</h2>'
        for pid, paper in papers.items():
            status = []
            if not paper['has_notes']:
                status.append('尚无已保存笔记')
            if paper['incomplete']:
                status.append('笔记尚未完成')
            if paper['stale']:
                status.append('原文材料已更新，笔记待复核')
            saved = portable_note_html(papers_dir / pid, captured[pid])
            yield from paper_html(paper, paper['tags'], [group['name'] for group in paper['libraries']], saved, ' · '.join(status))
        yield '</section><section id="figures"><h2>插图画廊</h2><div class="gallery">'
        for figure in digest['figures']:
            preview = thumbnail(lambda: open_image(figure)) if figure['image_url'] else ''
            yield from figure_html(figure, papers[figure['paper_id']], preview)
        yield '</div></section></main>'

    return library['name'], export_stream(template, library['name'], body())
