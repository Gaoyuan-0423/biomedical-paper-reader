"""Real Chromium acceptance. With app requirements and playwright installed, run
python app/tests/browser_table_citations.py (optional --screenshots DIRECTORY).
Uses installed Google Chrome by default, or --channel chromium for Playwright's build.
"""
import argparse
import json
import re
import secrets
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from playwright.sync_api import expect, sync_playwright
from pypdf import PdfWriter


APP = Path(__file__).resolve().parents[1]
QUESTION = 'Table S2 里 FLC 的 log2FC 是多少？'
FILENAME = 'Table S2.xlsx'


@contextmanager
def desktop_service(directory, executable=None):
    """Bootstrap the desktop sidecar against an empty, disposable library."""
    directory.mkdir()
    token = secrets.token_hex(32)
    command = [str(executable.resolve())] if executable else [sys.executable, str(APP / 'service.py')]
    with subprocess.Popen(
        command, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, text=True, encoding='utf-8',
    ) as child:
        try:
            child.stdin.write(json.dumps({'token': token, 'diagnostic_data': str(directory)}) + '\n')
            child.stdin.flush()
            ready = json.loads(child.stdout.readline())
            yield ready['url'] + '/?desktop_token=' + token
        finally:
            child.stdin.close()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()


def make_fixtures(directory, label, row, column, value, metric='log2FC'):
    directory.mkdir()
    title = f'Citation study {label}'
    pdf = directory / (title + '.pdf')
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=300)
    writer.add_metadata({'/Title': title})
    writer.write(pdf)

    book = Workbook()
    sheet = book.active
    sheet.title = 'DEGs'
    headers = ['gene', metric, 'marker'] if column == 2 else ['gene', 'marker', metric]
    sheet.append(headers)
    for number in range(2, row + 3):
        if number == 3:
            continue  # A physical gap must not shift the cited spreadsheet row.
        data = {'gene': 'FLC' if number == row else f'GENE{number}',
                metric: value if number == row else 0, 'marker': f'{label}-{number}'}
        for col, header in enumerate(headers, start=1):
            sheet.cell(number, col, data[header])
    workbook = directory / FILENAME
    book.save(workbook)
    return pdf, workbook


def import_paper(page, pdf, workbook):
    with page.expect_response(lambda response: response.url.endswith('/api/papers')
                              and response.request.method == 'POST') as uploaded:
        page.locator('#file-input').set_input_files(pdf)
    assert uploaded.value.ok, uploaded.value.text()
    paper = uploaded.value.json()
    expect(page).to_have_url(re.compile(r'\?paper=' + paper['id'] + '$'))
    with page.expect_file_chooser() as choosing:
        page.locator('#btn-supplement').click()
    with page.expect_response(lambda response: response.url.endswith('/' + paper['id'] + '/supplements')
                              and response.request.method == 'POST') as attached:
        choosing.value.set_files(workbook)
    assert attached.value.ok, attached.value.text()
    attachment, = attached.value.json()['supplement_files']
    assert attachment['filename'] == FILENAME
    return paper['id'], attachment['id']


def ask(page, paper_id, value, question=QUESTION):
    page.locator('#view-chat').click()
    page.locator('#chat-input').fill(question)
    with page.expect_response(lambda response: response.url.endswith('/' + paper_id + '/chat')
                              and response.request.method == 'POST') as asked:
        page.locator('#chat-send').click()
    assert asked.value.ok, asked.value.text()
    answer = page.locator('.chat-message-assistant')
    expect(answer.locator('.chat-content')).to_contain_text(str(value))
    expect(answer.locator('.chat-content [data-chat-source="S1"]')).to_be_visible()
    return answer.get_attribute('data-chat-message-id')


def inspect_citation(page, label, paper_id, attachment_id, message_id, row, column, value, screenshot):
    answer = page.locator(f'.chat-message-assistant[data-chat-message-id="{message_id}"]')
    expect(answer.locator('.chat-content')).to_contain_text(str(value))
    citation = answer.locator('.chat-content [data-chat-source="S1"]')
    expect(citation).to_be_visible()
    for text in (FILENAME, 'DEGs', f'第 {row} 行', 'log2FC'):
        expect(citation).to_contain_text(text)
    with page.expect_response(lambda response: response.url.endswith(
            f'/{paper_id}/chat/sources/{message_id}/S1')) as verified:
        citation.click()
    assert verified.value.ok, verified.value.text()
    source = verified.value.json()['source']
    assert source['paper_id'] == paper_id and source['document_id'] == attachment_id
    assert (source['document_name'], source['sheet'], source['row'], source['column'],
            source['column_name'], source['value']) == (FILENAME, 'DEGs', row, column, 'log2FC', str(value))
    dialog = page.locator('#table-source-modal')
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text(FILENAME)
    location = dialog.locator('#table-source-location')
    for text in (f'Citation study {label}', 'DEGs', 'log2FC', f'{get_column_letter(column)}{row}'):
        expect(location).to_contain_text(text)
    hit = dialog.locator(f'tr.table-source-hit[data-source-row="{row}"]')
    expect(hit.get_by_role('cell', name='FLC', exact=True)).to_be_visible()
    expect(hit.locator('td').nth(column - 1)).to_have_attribute('aria-current', 'true')
    expect(hit.locator('td[aria-current="true"]')).to_have_text(str(value))
    expect(dialog.locator(f'tr[data-source-row="{row - 1}"]')).to_contain_text(f'{label}-{row - 1}')
    if screenshot:
        expect(page.locator('#toast')).to_be_hidden(timeout=10000)
        page.screenshot(path=screenshot, animations='disabled')
    page.locator('#table-source-close').click()
    print(f'{label}  {FILENAME}  DEGs!{get_column_letter(column)}{row}  FLC log2FC = {value}', flush=True)


def compare_measurements(page, papers, messages, directory, screenshots):
    # Create a named project through the library controls. Both source files are
    # already attached and both chat messages are from earlier in the session.
    page.locator('#open-projects').click()
    page.locator('#project-new').click()
    form = page.locator('#project-papers form')
    form.get_by_label('库名称').fill('FLC project')
    for label in ('A', 'B'):
        form.get_by_label(f'Citation study {label}').check()
    form.get_by_role('button', name='保存课题库').click()
    expect(page.locator('#project-switch')).not_to_have_value('all')
    scope = page.locator('#project-switch').input_value()
    assert scope != 'all'
    page.locator('#project-close').click()
    directions = {'A': ('treated','control'), 'B': ('control','treated')}
    comparison_id = ''
    for label, peer in [('A', 'B'), ('B', 'A')]:
        paper = papers[label][0]
        page.locator(f'.paper-open[data-id="{paper}"]').click()
        page.locator('#view-chat').click()
        answer = page.locator(f'.chat-message-assistant[data-chat-message-id="{messages[label]}"]')
        answer.locator('[data-chat-action="compare-table"]').click()
        expect(page.locator('#table-comparison-scope')).to_have_value(scope)
        choice = page.locator('#table-comparison-target')
        if label == 'A':
            expect(choice).to_be_enabled()
            page.locator('#table-comparison-question').fill('Compare FLC expression')
            page.locator('#table-comparison-peer').select_option(papers[peer][0])
        else:
            choice.select_option(comparison_id)
        expect(page.locator('#table-comparison-label')).to_have_value('log2FC')
        page.locator('#table-comparison-label').fill('FLC fold change')
        test, ref = directions[label]
        page.locator('#table-comparison-test-group').fill(test)
        page.locator('#table-comparison-reference-group').fill(ref)
        page.locator('#table-comparison-scale').select_option('log2fc')
        with page.expect_response(lambda response: response.url.endswith('/table-measurements') and
                                  response.request.method == 'POST') as saved:
            page.locator('#table-comparison-save').click()
        assert saved.value.ok, saved.value.text()
        record = saved.value.json()
        comparison_id = record['id']
        assert f'/api/comparisons/{scope}/{comparison_id}/' in saved.value.url
        expect(page.locator('#table-comparison-view')).to_be_visible()
        if label == 'A':
            page.locator('#table-comparison-close').click()
        else:
            page.locator('#table-comparison-view').click()

    # Reload the real desktop app and open the saved comparison via its normal entry point.
    page.reload()
    page.locator('#open-projects').click()
    page.locator('[data-project-tab="compare"]').click()
    page.locator('[data-comparisons]').select_option(comparison_id)
    grid = page.locator('.cmp-measurements')
    expect(grid.get_by_role('columnheader', name='FLC fold change')).to_be_visible()
    expect(grid).to_contain_text('同列含相反的对比方向')
    expect(grid.locator('[data-directed-value]')).to_have_count(0)
    grid.locator('[data-view-direction]').select_option(label='treated 相对于 control')
    for label, coord, row, value in [('A', 'B8', 8, '1.42'), ('B', 'C21', 21, '-0.73')]:
        paper, attachment = papers[label]
        line = grid.locator(f'tr[data-measurement-paper="{paper}"]')
        for text in (f'Citation study {label}', value, 'FLC', 'log2FC', ' 相对于 '.join(directions[label]), FILENAME, 'DEGs'):
            expect(line).to_contain_text(text)
        expect(line.locator('[data-original-value]')).to_have_text(value)
        expect(line.locator('[data-directed-value]')).to_have_text('+1.42' if label == 'A' else '+0.73')
        button = line.locator('[data-measurement-source]')
        key = button.get_attribute('data-measurement-source')
        with page.expect_response(lambda response: response.url.endswith(
                f'/{scope}/{comparison_id}/table-measurements/{paper}/{key}/source')) as original:
            button.click()
        assert original.value.ok and original.value.json()['source']['document_id'] == attachment
        modal = page.locator('#table-source-modal')
        expect(modal.locator('#table-source-location')).to_contain_text(f'Citation study {label}')
        expect(modal.locator('#table-source-location')).to_contain_text(coord)
        expect(modal.locator(f'tr[data-source-row="{row}"] td[aria-current="true"]')).to_have_text(value)
        page.locator('#table-source-close').click()
    if screenshots:
        expect(page.locator('#toast')).to_be_hidden(timeout=10000)
        page.screenshot(path=screenshots / 'supplement-table-comparison.png', animations='disabled')

    # Removing one adopted cell must preserve the other paper and its source.
    grid.locator(f'tr[data-measurement-paper="{papers["A"][0]}"] [data-measurement-remove]').click()
    with page.expect_response(lambda response: response.url.endswith('/' + comparison_id) and
                              response.request.method == 'PUT') as edited:
        page.locator('[data-save]').click()
    assert edited.value.ok, edited.value.text()
    rows = {item['paper_id']: item for item in edited.value.json()['rows']}
    assert not rows[papers['A'][0]].get('table_measurements')
    assert rows[papers['B'][0]]['table_measurements'][0]['source']['value'] == '-0.73'
    print('Saved comparison restored with two original cells, directions and working citations. Single-value removal verified.', flush=True)

    page.locator('#project-close').click()
    pdf, workbook = make_fixtures(directory / 'C', 'C', 5, 2, 0.25, metric='FC')
    paper_c, attachment_c = import_paper(page, pdf, workbook)
    reply_c = ask(page, paper_c, 0.25, 'Table S2 里 FLC 的 FC 是多少？')
    page.locator(f'[data-chat-message-id="{reply_c}"] [data-chat-action="compare-table"]').click()
    expect(page.locator('#table-comparison-target')).to_have_value(comparison_id)
    expect(page.locator('#table-comparison-save')).to_have_text('加入本篇及数值')
    page.locator('#table-comparison-label').fill('FLC fold change')
    with page.expect_response(lambda response: response.url.endswith('/table-measurements') and
                              response.request.method == 'POST') as joined:
        page.locator('#table-comparison-save').click()
    assert joined.value.ok, joined.value.text()
    added, = (r for r in joined.value.json()['rows'] if r['paper_id'] == paper_c)
    assert added['table_measurements'][0]['source']['document_id'] == attachment_c
    assert added['table_measurements'][0]['source']['value'] == '0.25'
    page.locator('#table-comparison-view').click()
    grid.locator('[data-view-direction]').select_option(label='treated 相对于 control')
    c = grid.locator(f'tr[data-measurement-paper="{paper_c}"]')
    expect(c).to_contain_text('尺度待核对')
    expect(c.locator('[data-directed-value]')).to_have_count(0)
    c.locator('[data-review-definition]').click()
    c.locator('[data-test-group]').fill('control')
    c.locator('[data-reference-group]').fill('treated')
    c.locator('[data-scale]').select_option('fc')
    c.locator('[data-apply-definition]').click()
    expect(grid).to_contain_text('同列含不同的指标尺度')
    expect(c).to_contain_text('此尺度只显示原值')
    expect(c.locator('[data-directed-value]')).to_have_count(0)
    with page.expect_response(lambda response: response.url.endswith('/' + comparison_id) and
                              response.request.method == 'PUT') as checked:
        page.locator('[data-save]').click()
    assert checked.value.ok
    current = next(r for r in checked.value.json()['rows'] if r['paper_id'] == paper_c)['table_measurements'][0]
    assert current['contrast_definition']['scale'] == 'fc' and current['source']['value'] == '0.25'
    print('Opposite log2FC normalized only on selection. Third paper retained its raw FC and was never converted.', flush=True)


def run(page, url, directory, screenshots):
    page.goto(url)
    cases = [('A', 8, 2, 1.42), ('B', 21, 3, -0.73)]
    papers = {}
    for label, row, col, value in cases:
        pdf, workbook = make_fixtures(directory / label, label, row, col, value)
        papers[label] = import_paper(page, pdf, workbook)
    assert papers['A'][0] != papers['B'][0] and papers['A'][1] != papers['B'][1]

    messages = {}
    for label, row, col, value in cases:
        paper, attachment = papers[label]
        page.locator(f'.paper-open[data-id="{paper}"]').click()
        expect(page).to_have_url(re.compile(r'\?paper=' + paper + '$'))
        messages[label] = ask(page, paper, value)
        inspect_citation(page, label, paper, attachment, messages[label], row, col, value,
                         screenshots / f'supplement-table-{label.lower()}.png' if screenshots else None)

    page.reload()  # Drop in-memory chat sessions before returning to A's saved answer.
    paper, attachment = papers['A']
    message = messages['A']
    page.locator(f'.paper-open[data-id="{paper}"]').click()
    expect(page).to_have_url(re.compile(r'\?paper=' + paper + '$'))
    page.locator('#view-chat').click()
    expect(page.locator('.chat-message-assistant')).to_have_count(1)
    inspect_citation(page, 'A', paper, attachment, message, 8, 2, 1.42,
                     screenshots / 'supplement-table-history-a.png' if screenshots else None)
    print('A history re-opened after B using the original saved message and attachment.', flush=True)
    compare_measurements(page, papers, messages, directory, screenshots)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', default='chrome')
    parser.add_argument('--screenshots', type=Path)
    parser.add_argument('--service-executable', type=Path, help='Run the packaged desktop sidecar instead of service.py')
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(exist_ok=True, parents=True)
    with tempfile.TemporaryDirectory(prefix='bioreader-table-browser-') as temporary:
        root = Path(temporary)
        with desktop_service(root / 'library', args.service_executable) as bootstrap_url, sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=args.channel)
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 960}, locale='zh-CN')
                run(page, bootstrap_url, root, args.screenshots)
            finally:
                browser.close()
