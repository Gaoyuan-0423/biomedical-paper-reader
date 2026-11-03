"""Small, independent reading positions; never rewrite note metadata."""
import json
import math
import os
import tempfile
import time


def read_positions(directory, meta):
    documents = {'main': meta.get('pages', 0)}
    documents.update({d['id']: d['pages'] for d in meta.get('supplements', [])})
    positions = {}
    latest = -1
    active = 'main'
    for doc, pages in documents.items():
        if not pages:
            continue
        try:
            data = json.loads((directory / 'reading' / f'{doc}.json').read_text('utf-8'))
            page, scale, updated = int(data['page']), float(data['scale']), int(data['updated'])
            if not math.isfinite(scale) or not isinstance(data['fit_width'], bool):
                continue
            positions[doc] = {'page': max(1, min(page, pages)),
                              'scale': max(0.3, min(scale, 3)), 'fit_width': data['fit_width']}
            if updated > latest:
                active, latest = doc, updated
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            continue
    return {'active_document': active, 'documents': positions}


def save_position(directory, document_id, position):
    folder = directory / 'reading'
    folder.mkdir(exist_ok=True)
    # One file per source avoids read-modify-write races between different PDFs.
    fd, temp = tempfile.mkstemp(prefix='.position-', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump({**position, 'updated': time.time_ns()}, output)
        os.replace(temp, folder / f'{document_id}.json')
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
