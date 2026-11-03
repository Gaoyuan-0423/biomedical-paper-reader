"""Deterministic figure placement and portable Markdown exports; no model calls."""
import io
import re
import zipfile
from pathlib import Path

FIGURE_REF = re.compile(r'!\[([^\]\n]*)\]\(figure:([a-z]\d{3,6})\)')


def figure_title(figure):
    source = figure.get('document_name') or '正文'
    return f"{figure['label']} · {source}".replace('[', '(').replace(']', ')').replace('\n', ' ')


def illustrate_notes(notes, figures, mode='deep', drop_unknown=True):
    """Honor valid placements; fill unillustrated figure sections, then a gallery.

    In quick mode only cited figures are selected. In deep mode all parent figures
    are included as source illustrations, never as a claim of visual verification.
    """
    if not notes.strip():
        return notes
    valid = {f['id']: f for f in figures}
    if drop_unknown:
        notes = FIGURE_REF.sub(lambda m: m.group(0) if m[2] in valid else m[1], notes)
    # Exporters consume standalone image blocks, even if a model wrote them inline.
    lines = []
    for line in notes.split('\n'):
        if FIGURE_REF.search(line) and not FIGURE_REF.fullmatch(line.strip()):
            line = FIGURE_REF.sub(lambda m: '\n\n' + m[0] + '\n\n', line).strip()
        lines.append(line)
    notes = '\n'.join(lines)
    used = {m[2] for m in FIGURE_REF.finditer(notes)}
    missing = []
    for f in figures:
        if f.get('parent') or f['id'] in used:
            continue
        label = re.escape(f['label'])
        # Match a complete figure number: Figure 1 must not match Figure 10.
        pattern = re.compile(label + r'(?![\w\d])', re.I)
        if mode == 'quick' and not pattern.search(notes):
            continue
        ref = f"![{figure_title(f)}](figure:{f['id']})"
        # Same-number figures from separate documents are ambiguous: use a gallery.
        same_label = sum(other['label'].casefold() == f['label'].casefold() for other in figures if not other.get('parent'))
        headings = list(re.finditer(r'^#{2,6} .+$', notes, re.M)) if same_label == 1 else []
        heading = next((m for m in headings if pattern.search(m[0])), None)
        if heading:
            notes = notes[:heading.end()] + '\n\n' + ref + notes[heading.end():]
        else:
            missing.append(f"### {figure_title(f)}\n\n{ref}")
        used.add(f['id'])
    if missing:
        notes = notes.rstrip() + '\n\n## 原文图集\n\n' + '\n\n'.join(missing)
    return notes


def markdown_bundle(notes, directory: Path):
    """Return a ZIP containing report.md and only its referenced source images."""
    images = {}
    def replace(match):
        fid = match[2]
        path = directory / 'figures' / f'{fid}.png'
        if not path.is_file():
            raise ValueError(f'图片 {fid} 已不存在，请重新提取或移除该引用后导出')
        images[fid] = path
        return f'![{match[1]}](assets/{fid}.png)'
    report = FIGURE_REF.sub(replace, notes)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('report.md', report)
        for fid, path in images.items():
            archive.write(path, f'assets/{fid}.png')
    return output.getvalue()
