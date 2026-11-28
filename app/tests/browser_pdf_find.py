"""Exercise the PDF toolbar and keyboard shortcuts with real, paginated Chinese/English PDFs.

Run like browser_table_citations.py. All documents are generated in a temporary directory.
"""
import argparse
import re
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from browser_table_citations import desktop_service


def write_pdf(printer, destination, page_texts):
    pages = ''.join(
        f'<section><h1>{destination.stem}</h1><h2>Page {i}</h2><p>{text}</p></section>'
        for i, text in enumerate(page_texts, 1)
    )
    printer.set_content(
        f'<meta charset="utf-8"><title>{destination.stem}</title><style>@page {{size:A4;margin:18mm}} '
        'body {font:16px sans-serif} section + section {break-before:page}</style>' + pages)
    printer.pdf(path=destination, prefer_css_page_size=True)


def upload(page, selector, pdf, endpoint):
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith(endpoint)) as uploaded:
        page.locator(selector).set_input_files(pdf)
    assert uploaded.value.ok, uploaded.value.text()
    return uploaded.value.json()


def assert_hit(page, index, total, number):
    expect(page.locator('#pdf-find-count')).to_have_text(re.compile(rf'^{index}\s*/\s*{total}$'))
    expect(page.locator('#pdf-page')).to_have_value(str(number))
    mark = page.locator(f'.pdf-page[data-page="{number}"] .pdf-find-highlight').first
    expect(mark).to_be_visible()
    expect(mark).to_be_in_viewport()


def exercise(page, printer, bootstrap, root, screenshots):
    main = root / 'Main study.pdf'
    supplement = root / 'Supplement.pdf'
    main_pages = ['Control cohort and methods.'] * 19
    main_pages[1] = 'We compared paired samples. 组织部位记录见附表。'
    main_pages[-1] = 'The validation used PAIRED samples.'
    write_pdf(printer, main, main_pages)
    write_pdf(printer, supplement, ['Supplementary methods.', 'Independent analysis.', 'One paired comparison.'])
    printer.close()

    page.goto(bootstrap)
    paper = upload(page, '#file-input', main, '/api/papers')
    expect(page).to_have_url(re.compile(r'\?paper=' + paper['id'] + '$'))
    added = upload(page, '#supplement-input', supplement, '/' + paper['id'] + '/supplements')
    sid = added['supplements'][0]['id']
    docs = page.locator('#document-select')
    expect(docs).to_have_value(sid)
    docs.select_option('main')
    expect(page.locator('#pdf-page')).to_have_attribute('max', '19')
    surface = page.locator('.pdf-page[data-page="1"] .pdf-text-layer')
    surface.get_by_text('Control cohort and methods.', exact=True).click()
    shortcut = 'Meta+f' if sys.platform == 'darwin' else 'Control+f'
    page.keyboard.press(shortcut)
    query = page.locator('#pdf-find-query')
    expect(query).to_be_focused()
    query.fill('paired')
    assert_hit(page, 1, 2, 2)
    query.press('Enter')
    assert_hit(page, 2, 2, 19)
    if screenshots:
        expect(page.locator('#toast')).to_be_hidden(timeout=10000)
        page.screenshot(path=screenshots / 'find-main-page19.png', animations='disabled')
    query.press('Shift+Enter')
    assert_hit(page, 1, 2, 2)
    page.locator('#pdf-find-prev').click()
    assert_hit(page, 2, 2, 19)
    page.locator('#pdf-find-next').click()
    assert_hit(page, 1, 2, 2)

    query.fill('组织部位')
    assert_hit(page, 1, 1, 2)
    query.fill('absent-token')
    expect(page.locator('#pdf-find-count')).to_contain_text(re.compile(r'未|没有|0\s*/\s*0'))
    expect(page.locator('.pdf-find-highlight')).to_have_count(0)
    query.fill('paired')
    docs.select_option(sid)
    expect(page.locator('#pdf-find-bar')).to_be_hidden()
    expect(page.locator('#pdf-page')).to_have_attribute('max', '3')
    page.locator('#pdf-find-toggle').click()
    expect(query).to_have_value('')
    query.fill('paired')
    assert_hit(page, 1, 1, 3)
    if screenshots:
        page.screenshot(path=screenshots / 'find-supplement-page3.png', animations='disabled')
    query.press('Escape')
    expect(page.locator('#pdf-find-bar')).to_be_hidden()
    page.locator('#view-chat').click()
    page.locator('#chat-input').focus()
    page.keyboard.press(shortcut)
    expect(page.locator('#pdf-find-bar')).to_be_hidden()
    print('Main PDF: 2 hits on pages 2 and 19. Supplement: 1 hit on page 3. '
          'Chinese query, highlights, keyboard navigation, wraparound and shortcut scope passed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', default='chrome')
    parser.add_argument('--service-executable', type=Path)
    parser.add_argument('--screenshots', type=Path)
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(exist_ok=True, parents=True)
    with tempfile.TemporaryDirectory(prefix='bioreader-pdf-find-') as temp, sync_playwright() as pw:
        root = Path(temp)
        with desktop_service(root / 'library', args.service_executable) as url:
            browser = pw.chromium.launch(channel=args.channel)
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 960}, locale='zh-CN')
                exercise(page, browser.new_page(), url, root, args.screenshots)
            finally:
                browser.close()
