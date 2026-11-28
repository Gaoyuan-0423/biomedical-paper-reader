"""Exercise project-scoped filtered tagging, reload-safe undo and full-text search in Chrome."""
import argparse
import io
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright, expect
from pypdf import PdfWriter

from browser_table_citations import desktop_service


def api(page, method, path, data=None):
    return page.evaluate("""async ([method, path, data]) => {
      const res = await fetch(path, {method, headers:{'Content-Type':'application/json'},
        body:data === null ? undefined : JSON.stringify(data)});
      if (!res.ok) throw Error(await res.text());
      return res.json();
    }""", [method, path, data])


def upload_paper(page, title, **fields):
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=300)
    writer.add_metadata({'/Title':title})
    output = io.BytesIO()
    writer.write(output)
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/papers')) as sent:
        page.locator('#file-input').set_input_files({'name':title+'.pdf', 'mimeType':'application/pdf','buffer':output.getvalue()})
    assert sent.value.ok, sent.value.text()
    pid = sent.value.json()['id']
    api(page, 'PATCH', '/api/papers/'+pid, fields)
    return pid


def run(page, url, screenshots):
    page.goto(url)
    tag = '待核对补充表'
    ids = {}
    # A already has the target tag. C fails the favorite filter, D is outside the project,
    # E fails the metadata query, F fails the existing tag filter.
    for key in 'ABCDEF':
        name = ('unrelated ' if key == 'E' else 'cohort ') + key
        tags = ['已读'] if key != 'F' else []
        if key == 'A':
            tags += [tag, '保留']
        ids[key] = upload_paper(page, name, tags=tags, favorite=key != 'C',
                                notes='assay notebook' if key in 'BCDF' else 'general')
    project = api(page, 'POST', '/api/project-libraries', {'name':'Tag project', 'paper_ids':[ids[k] for k in 'ABCEF']})
    page.reload()
    page.locator('#project-scope').select_option(project['id'])
    page.locator('#btn-fav-filter').click()
    page.locator('#tag-chips [data-tag="已读"]').click()
    page.locator('#search').fill('cohort')
    expect(page.locator('.paper-open')).to_have_count(2)
    page.locator('#library-bulk-tags').click()
    expect(page.locator('#bulk-tag-count')).to_have_text('已选中 2 篇文献')
    page.locator('#bulk-tag-value').fill(tag)
    page.locator('#bulk-tag-apply').click()
    expect(page.locator('#bulk-tag-status')).to_contain_text('新增 1 篇，已有 1 篇，跳过 0 篇')
    if screenshots:
        expect(page.locator('#toast')).to_be_hidden(timeout=10000)
        page.screenshot(path=screenshots/'bulk-tags-scoped.png', animations='disabled')
    page.locator('#bulk-tag-close').click()
    for key in 'ABCDEF':
        labels = api(page, 'GET', '/api/papers/' + ids[key])['tags']
        assert (tag in labels) == (key in 'AB'), (key, labels)
    page.locator('.paper-open[data-id="' + ids['B'] + '"]').click()
    page.locator('#tag-input').fill('later')
    with page.expect_response(lambda r: r.request.method == 'PATCH' and r.url.endswith('/' + ids['B'])):
        page.locator('#tag-input').press('Enter')
    page.reload()
    page.locator('#project-scope').select_option(project['id'])
    page.locator('#search').fill('no-matching-paper')
    expect(page.locator('.paper-open')).to_have_count(0)
    page.locator('#library-bulk-tags').click()
    expect(page.locator('#bulk-tag-count')).to_have_text('已选中 0 篇文献')
    page.locator('#bulk-tag-undo').click()
    expect(page.locator('#bulk-tag-status')).to_have_text('已撤销上次批量添加。')
    page.locator('#bulk-tag-close').click()
    assert api(page, 'GET', '/api/papers/' + ids['A'])['tags'] == ['已读', tag, '保留']
    assert api(page, 'GET', '/api/papers/' + ids['B'])['tags'] == ['已读', 'later']
    print('Sidebar scope + favorites + tag + query identified only A/B. Undo after reload/empty filters preserved existing and later labels.')

    page.locator('#btn-library-search').click()
    page.locator('#library-search-query').fill('assay notebook')
    page.locator('#library-search-scope').select_option('notes')
    page.locator('#library-search-form button[type=submit]').click()
    page.locator('[data-library-search-action="bulk-tags"]').click()
    expect(page.locator('#bulk-tag-count')).to_have_text('已选中 3 篇文献')
    page.locator('#bulk-tag-value').fill('notes result')
    page.locator('#bulk-tag-apply').click()
    expect(page.locator('#bulk-tag-status')).to_contain_text('新增 3 篇')
    for key in 'ABCDEF':
        labels = api(page, 'GET', '/api/papers/' + ids[key])['tags']
        assert ('notes result' in labels) == (key in 'BCF'), (key, labels)
    page.locator('#bulk-tag-undo').click()
    expect(page.locator('#bulk-tag-status')).to_have_text('已撤销上次批量添加。')
    page.locator('#bulk-tag-close').click()
    expect(page.locator('#library-search-modal')).to_be_visible()
    expect(page.locator('#library-search-query')).to_have_value('assay notebook')
    print('Full-text results independently respected the selected project, tagged B/C/F and excluded D.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service-executable', type=Path)
    parser.add_argument('--screenshots', type=Path)
    parser.add_argument('--channel', default='chrome')
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bioreader-bulk-tag-') as temp, sync_playwright() as pw:
        with desktop_service(Path(temp)/'library', args.service_executable) as url:
            browser = pw.chromium.launch(channel=args.channel)
            try:
                run(browser.new_page(viewport={'width':1440, 'height':960}, locale='zh-CN'), url, args.screenshots)
            finally:
                browser.close()
