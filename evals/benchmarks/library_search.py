"""Compare warm and first-use library-search requests on generated PDFs."""
import argparse
import ctypes
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'app' / 'backend'))


def baseline(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if not c.isspace())


def rust_normalizer(directory):
    ext = {'darwin': 'dylib', 'win32': 'dll'}.get(sys.platform, 'so')
    path = directory / ('normalize.' + ext)
    subprocess.run(['rustc', '-O', '--crate-type', 'cdylib', str(HERE / 'normalize.rs'), '-o', str(path)],
                   check=True, cwd=directory)
    library = ctypes.CDLL(str(path))
    normalize = library.normalize_ascii
    normalize.argtypes = (ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p)
    normalize.restype = ctypes.c_size_t

    def run(text):
        if len(text) < 32 or not text.isascii():
            return ''.join(unicodedata.normalize('NFKC', text).casefold().split())
        src = text.encode('ascii')
        dest = ctypes.create_string_buffer(len(src))
        size = normalize(src, len(src), dest)
        return dest.raw[:size].decode('ascii')

    return library, run


def generate_paper(directory, index, count, backend, sources):
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/Encoding'): NameObject('/WinAnsiEncoding'), NameObject('/BaseFont'): NameObject('/Helvetica'),
    }))
    sentences = ['Specimens were recorded at the initial consultation.',
                 'Readers reviewed each paper and documented a finding.',
                 'A second reader inspected the same figure on the next day.',
                 'A baseline subgroup analysis was reported in the appendix.']
    pages = []
    for number in range(1, 9):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        blocks, commands = [], ['BT /F1 9 Tf 46 744 Td 13 TL']
        for b in range(5):
            lines = [f'Paper {index + 1}, page {number}, paragraph {b + 1}.']
            lines.extend(sentences[(index + b + number + k) % len(sentences)] for k in range(6))
            if b == 0 and number % 2 == 0:
                lines[-1] = 'Résumé and ß are preserved in the record.'
            if index == count - 1 and number == 7 and b == 3:
                lines[-1] += ' Sparse marker QX77.'
            commands.extend('(' + line + ") '" for line in lines)
            blocks.append({'text': ' '.join(lines), 'bbox': [0.075, (b * 110 + 50) / 792, 0.85, (b * 110 + 140) / 792]})
        stream = DecodedStreamObject()
        stream.set_data('\n'.join(commands + ['ET']).encode('cp1252'))
        page[NameObject('/Contents')] = writer._add_object(stream)
        pages.append({'number': number, 'blocks': blocks})
    paper_id = f'{index + 1:032x}'
    paper = directory / paper_id
    paper.mkdir(parents=True)
    writer.write(str(paper / 'paper.pdf'))
    revision = sources.file_revision(paper / 'paper.pdf')
    sources.save_parsed_blocks(paper, 'main', revision, pages)
    meta = backend.blank_meta(paper_id) | {'title': f'Generated paper {index + 1}', 'has_file': True, 'pages': 8,
                                            'notes': '合成论文，用于测试检索。', 'document_revisions': {'main': revision}}
    backend.write_meta(paper, meta)


def measure(client, search, directory, methods, rng, cold):
    queries = {'single': ('QX77', 1)}
    if not cold:
        queries.update({'miss': ('never mentioned in the generated document', 0), 'frequent': ('second reader', 20)})
    samples = {(query, method): [] for query in queries for method in methods}
    expected = {}
    for rep in range(3 if cold else 8):
        order = list(samples)
        rng.shuffle(order)
        for query, method in order:
            if cold:
                for generated_cache in directory.glob('*/blocks/main.json'):
                    generated_cache.unlink()
            phrase, matches = queries[query]
            with patch.object(search, '_normalized', methods[method]):
                start = time.perf_counter()
                response = client.get('/api/library/search', params={'q': phrase})
                seconds = time.perf_counter() - start
            response.raise_for_status()
            data = response.json()
            assert len(data['items']) == matches and data['warning_count'] == 0
            assert expected.setdefault(query, data) == data
            if cold or rep > 0:
                samples[query, method].append(1000 * seconds)
    for (query, method), values in sorted(samples.items()):
        print(f'{directory.name:>10} {query:>9} {method:>8}  median {statistics.median(values):7.1f} ms'
              f'  range {min(values):.1f}–{max(values):.1f} ms')


def run(root, enable_rust):
    # main resolves its data path at import time. Isolate the entire app first.
    os.environ['BIOREADER_DATA_DIR'] = str(root / 'data')
    import chat_sources
    import library_search
    import main

    rng = random.Random(20261120)
    methods = {'baseline': baseline, 'current': library_search._normalized}
    library = None
    if enable_rust:
        library, methods['rust'] = rust_normalizer(root)
    try:
        corpus = [''.join(map(chr, range(128))), ''.join(map(chr, range(0x110000))), '１２ Å ﬂ Þ ß Β \u00a0\r\n \u001c中文标题']
        corpus.extend(''.join(chr(rng.randrange(128 if i % 2 else 0x110000)) for _ in range(i % 109)) for i in range(1000))
        for text in corpus:
            expected = baseline(text)
            assert all(fn(text) == expected for fn in methods.values())
        with TestClient(main.app) as client:
            for count, cold in [(5, False), (128, False), (2, True)]:
                directory = root / (('cold-' if cold else 'warm-') + str(count))
                for i in range(count):
                    generate_paper(directory, i, count, main, chat_sources)
                with patch.object(main, 'PAPERS_DIR', directory):
                    measure(client, library_search, directory, methods, rng, cold)
    finally:
        if library is not None and sys.platform == 'win32':
            import _ctypes
            _ctypes.FreeLibrary(library._handle)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rust', action='store_true', help='compile and measure the experimental Rust implementation')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='bioreader-search-') as temp:
        run(Path(temp), args.rust)
