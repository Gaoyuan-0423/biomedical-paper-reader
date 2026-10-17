#!/usr/bin/env python3
"""OPTIONAL helper: render one PDF page to PNG. Not a runtime dependency.

Prefer whatever PDF/image tools the host already provides; use this script
only when the host lacks page rendering. Backends (first available wins):
PyMuPDF (fitz), then pypdfium2 + Pillow. Missing both is not an error of the
reading workflow — the script exits with guidance.

Exit codes: 0 rendered, 2 no backend available, 1 other error.
"""
import argparse
import sys

USAGE_BACKENDS = (
    'No rendering backend found. This script is optional; host PDF tools are '
    'preferred. To use it, install one of:  pip install pymupdf  |  '
    'pip install pypdfium2 pillow'
)


def render_with_fitz(pdf_path, page_number, out_path, zoom, crop):
    import fitz

    document = fitz.open(pdf_path)
    if not 1 <= page_number <= document.page_count:
        raise SystemExit(f'error: page {page_number} outside 1..{document.page_count}')
    page = document[page_number - 1]
    matrix = fitz.Matrix(zoom, zoom)
    if crop:
        x0, y0, x1, y1 = crop
        clip = fitz.Rect(x0, y0, x1, y1)
        if clip.is_empty or not page.rect.intersects(clip):
            raise SystemExit(f'error: crop {crop} does not intersect the page')
        pixmap = page.get_pixmap(matrix=matrix, clip=clip)
    else:
        pixmap = page.get_pixmap(matrix=matrix)
    pixmap.save(out_path)


def render_with_pypdfium2(pdf_path, page_number, out_path, zoom, crop):
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_path)
    if not 1 <= page_number <= len(document):
        raise SystemExit(f'error: page {page_number} outside 1..{len(document)}')
    bitmap = document[page_number - 1].render(scale=zoom)
    image = bitmap.to_pil()
    if crop:
        width, height = image.size
        page_width = width / zoom
        page_height = height / zoom
        x0, y0, x1, y1 = crop
        image = image.crop((x0 * zoom, y0 * zoom, x1 * zoom, y1 * zoom))
        if not (0 <= x0 < x1 <= page_width and 0 <= y0 < y1 <= page_height):
            raise SystemExit(f'error: crop {crop} outside page {page_width:.0f}x{page_height:.0f}')
    image.save(out_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('pdf', help='input PDF path')
    parser.add_argument('page', type=int, help='1-based page number (file order, not printed page)')
    parser.add_argument('out', help='output PNG path')
    parser.add_argument('--zoom', type=float, default=2.0, help='render scale factor (default 2.0)')
    parser.add_argument('--crop', type=float, nargs=4, metavar=('X0', 'Y0', 'X1', 'Y1'),
                        help='optional crop rectangle in PDF points from the top-left corner')
    args = parser.parse_args()

    for loader, render in ((lambda: __import__('fitz'), render_with_fitz),
                           (lambda: __import__('pypdfium2'), render_with_pypdfium2)):
        try:
            loader()
        except ImportError:
            continue
        render(args.pdf, args.page, args.out, args.zoom, args.crop)
        print(f'rendered {args.pdf} page {args.page} -> {args.out}')
        return 0
    print(USAGE_BACKENDS, file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
