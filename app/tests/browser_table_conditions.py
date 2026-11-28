"""Exercise conditioned table lookups, paper-local choices and a distant spreadsheet column."""
import argparse
import tempfile
from pathlib import Path

from openpyxl import Workbook
from playwright.sync_api import expect, sync_playwright

from browser_table_citations import desktop_service, import_paper, make_fixtures


def run(page, url, root, screenshots):
    pdf, table = make_fixtures(root / 'A', 'A', 8, 2, 1.42)
    book = Workbook()
    sheet = book.active
    sheet.title = 'DEGs'
    sheet.append(['gene', 'timepoint', 'cell_type'] + [f'attribute{i}' for i in range(12)] + ['log2FC'])
    for name, time, cell, value in [('FLC', '6h', 'T cell', 1.42), ('FLC', '24h', 'T cell', -0.73),
                                    ('ABC1', '24h', 'B cell', 0.2)]:
        sheet.append([name, time, cell] + [''] * 12 + [value])
    book.save(table)
    book.close()
    page.goto(url)
    paper, attachment = import_paper(page, pdf, table)
    page.locator('#view-chat').click()

    def ask(question, expected):
        page.locator('#chat-input').fill(question)
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/' + paper + '/chat')):
            page.locator('#chat-send').click()
        answer = page.locator('.chat-message-assistant').last
        expect(answer.locator('.chat-content')).to_contain_text(expected)
        return answer

    answer = ask('Table S2 中 FLC 的 log2FC 是多少', '多个匹配')
    message = answer.get_attribute('data-chat-message-id')
    choices = answer.locator('[data-chat-action="choose-table"]')
    expect(choices).to_have_count(2)
    expect(choices.first).to_contain_text('DEGs!P2')
    expect(choices.first).to_contain_text('timepoint=6h')
    expect(choices.last).to_contain_text('timepoint=24h')
    expect(answer.locator('[data-chat-action="source"]')).to_have_count(0)
    if screenshots:
        expect(page.locator('#toast')).to_be_hidden(timeout=10000)
        page.screenshot(path=screenshots / 'table-conditions-choices.png')
    page.reload()
    page.locator(f'.paper-open[data-id="{paper}"]').click()
    page.locator('#view-chat').click()
    saved = page.locator(f'[data-chat-message-id="{message}"]')
    with page.expect_response(lambda r: r.url.endswith('/' + message + '/table-candidates/2')) as selected:
        saved.locator('[data-chat-choice="2"]').click()
    assert selected.value.ok, selected.value.text()
    result = selected.value.json()
    assert result['source']['document_id'] == attachment
    modal = page.locator('#table-source-modal')
    expect(modal).to_be_visible()
    expect(modal.locator('#table-source-location')).to_contain_text('timepoint=24h')
    target = modal.locator('tr[data-source-row="3"]')
    expect(target.locator('td').first).to_have_text('FLC')
    expect(target.locator('td').nth(1)).to_have_text('24h')
    expect(target.locator('td').nth(2)).to_have_text('T cell')
    expect(target.locator('td[aria-current="true"]')).to_have_text('-0.73')
    if screenshots:
        page.screenshot(path=screenshots / 'table-conditions-original.png')
    page.locator('#table-source-close').click()
    expect(saved.locator('.chat-content')).to_contain_text('-0.73')
    expect(saved.locator('[data-chat-action="compare-table"]')).to_be_visible()
    absent = ask('Table S2 中 NK cell 的 FLC 的 log2FC 是多少', '未找到')
    expect(absent.locator('[data-chat-action="source"]')).to_have_count(0)
    specific = ask('Table S2 中 cell_type 为 T cell 时，6h 的 FLC 的 log2FC 是多少', '1.42')
    expect(specific.locator('.chat-content [data-chat-source="S1"]')).to_be_visible()
    page.reload()
    page.locator(f'.paper-open[data-id="{paper}"]').click()
    page.locator('#view-chat').click()
    saved.locator('.chat-content [data-chat-source="S1"]').click()
    expect(modal.locator('#table-source-location')).to_contain_text('P3')
    expect(target.locator('td[aria-current="true"]')).to_have_text('-0.73')
    print('FLC / 6h and 24h produced two stored choices. Selected 24h at DEGs!P3; '
          'history retained -0.73 after separate searches and reload. Explicit T cell / 6h returned 1.42; NK cell yielded no citation.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', default='chrome')
    parser.add_argument('--screenshots', type=Path)
    parser.add_argument('--service-executable', type=Path)
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(exist_ok=True, parents=True)
    with tempfile.TemporaryDirectory(prefix='bioreader-condition-browser-') as temporary:
        root = Path(temporary)
        with desktop_service(root / 'library', args.service_executable) as url, sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=args.channel)
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 960}, locale='zh-CN')
                run(page, url, root, args.screenshots)
            finally:
                browser.close()
