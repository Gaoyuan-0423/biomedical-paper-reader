"""Verify the same underlying XLSX number with two formats through the real desktop UI."""
import argparse
import tempfile
from pathlib import Path

from openpyxl import Workbook
from playwright.sync_api import expect, sync_playwright

from browser_table_citations import desktop_service, import_paper, make_fixtures


def run(page, bootstrap, directory, screenshots):
    page.goto(bootstrap)
    peer_pdf, peer_book = make_fixtures(directory / 'B', 'B', 5, 2, 1.25)
    peer, _ = import_paper(page, peer_pdf, peer_book)
    pdf, workbook_path = make_fixtures(directory / 'A', 'A', 8, 2, 1.42)
    book = Workbook()
    sheet = book.active
    sheet.title = 'Rates'
    sheet.append(['gene', 'rate'])
    sheet.append(['PCT', 0.125])
    sheet.append(['FRAC', 0.125])
    sheet['B2'].number_format = '0.0%'
    book.save(workbook_path)
    book.close()
    paper, _ = import_paper(page, pdf, workbook_path)
    page.locator('#view-chat').click()
    comparison = None
    for row_number, gene, displayed in ((2, 'PCT', '12.5%'), (3, 'FRAC', '0.125')):
        page.locator('#chat-input').fill(f'Table S2 中 {gene} 的 rate 是多少？')
        with page.expect_response(lambda response: response.url.endswith(f'/{paper}/chat')
                                  and response.request.method == 'POST') as asked:
            page.locator('#chat-send').click()
        assert asked.value.ok, asked.value.text()
        answer = page.locator('.chat-message-assistant').last
        expect(answer.locator('.chat-content')).to_contain_text(displayed)
        message = answer.get_attribute('data-chat-message-id')
        with page.expect_response(lambda r: r.url.endswith(f'/chat/sources/{message}/S1')) as opened:
            answer.locator('.chat-content [data-chat-source="S1"]').click()
        assert opened.value.ok, opened.value.text()
        source = opened.value.json()['source']
        assert source['value'] == '0.125' and source.get('display_value', source['value']) == displayed
        hit = page.locator(f'#table-source-grid tr[data-source-row="{row_number}"] td[aria-current="true"]')
        expect(hit).to_have_text(displayed)
        if row_number == 2:
            expect(page.locator('#table-source-modal')).to_contain_text('0.125')
        page.locator('#table-source-close').click()
        answer.locator('[data-chat-action="compare-table"]').click()
        if comparison is None:
            page.locator('#table-comparison-question').fill('Rates from the original worksheet')
            page.locator('#table-comparison-peer').select_option(peer)
        else:
            page.locator('#table-comparison-target').select_option(comparison)
        with page.expect_response(lambda r: r.url.endswith('/table-measurements')
                                  and r.request.method == 'POST') as saved:
            page.locator('#table-comparison-save').click()
        assert saved.value.ok, saved.value.text()
        comparison = saved.value.json()['id']
        page.locator('#table-comparison-close').click()

    page.reload()
    page.locator('#open-projects').click()
    page.locator('[data-project-tab="compare"]').click()
    page.locator('[data-comparisons]').select_option(comparison)
    grid = page.locator('.cmp-measurements')
    for displayed in ('12.5%', '0.125'):
        expect(grid.get_by_text(displayed, exact=True)).to_be_visible()
    if screenshots:
        expect(page.locator('#toast')).to_be_hidden(timeout=10000)
        page.screenshot(path=screenshots / 'percent-and-decimal.png', animations='disabled')
    for row, displayed in ((2, '12.5%'), (3, '0.125')):
        grid.locator('[data-measurement-source]').filter(has_text=f'第 {row} 行').click()
        expect(page.locator(f'#table-source-grid tr[data-source-row="{row}"] td[aria-current="true"]')).to_have_text(displayed)
        page.locator('#table-source-close').click()
    print('Rates!B2 = 12.5%, Rates!B3 = 0.125. Both raw values equal 0.125. '
          'Checked chat, highlights, saved comparison, reload and both original-cell links.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service-executable', type=Path)
    parser.add_argument('--screenshots', type=Path)
    parser.add_argument('--channel', default='chrome')
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bioreader-table-formats-') as temp, sync_playwright() as pw:
        directory = Path(temp)
        with desktop_service(directory / 'library', args.service_executable) as url:
            browser = pw.chromium.launch(channel=args.channel)
            try:
                run(browser.new_page(viewport={'width': 1440, 'height': 960}, locale='zh-CN'),
                    url, directory, args.screenshots)
            finally:
                browser.close()
