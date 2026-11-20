"""Local, project-scoped lookup of figures saved by the reader."""
import re
from pathlib import Path

from pydantic import BaseModel, Field
from figures import crop_source_changed
from paper_tags import tag_key
from reading_workflow import source_info


class ShelfQuery(BaseModel):
    paper_ids: list[str]
    query: str = Field(default='', max_length=300)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=32, ge=1, le=100)


def figure_tags(values):
    if len(values) > 20:
        raise ValueError('每张图最多 20 个标签。')
    tags, seen = [], set()
    for raw in values:
        if re.search(r'[\x00-\x1f\x7f]', raw) or not 1 <= len(raw.strip()) <= 40:
            raise ValueError('每个图表标签需为 1–40 个字符。')
        tag = ' '.join(raw.split())
        key = tag_key(tag)
        if key not in seen:
            seen.add(key)
            tags.append(tag)
    return tags


def shelf_item(d: Path, meta, fig):
    doc = fig.get('document_id', 'main')
    path, pages, name = source_info(d, meta, doc)
    revisions = meta.get('document_revisions', {})
    revision = revisions.get(doc, 'legacy')
    page = fig.get('page')
    available = bool(path and path.is_file() and type(page) is int and 1 <= page <= pages
                     and revision == fig.get('shelf_source_revision', 'legacy') and not crop_source_changed(fig, revisions))
    return {
        'paper_id': d.name, 'paper_title': meta.get('title') or d.name, 'id': fig['id'],
        'document_id': doc, 'document_name': name, 'page': page, 'label': fig.get('label') or fig['id'],
        'caption': fig.get('caption') or '', 'tags': fig.get('tags', []), 'shelf_key': fig['shelf_key'],
        'source_revision': revision, 'source_available': available,
        'has_image': available and (d / 'figures' / (fig['id'] + '.png')).is_file(),
    }


def matches(item, words):
    haystack = tag_key(' '.join([item['paper_title'], item['label'], item['document_name'], item['caption'], *item['tags']]))
    return all(word in haystack for word in words)
