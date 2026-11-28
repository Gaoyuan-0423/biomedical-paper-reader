"""Exercise existing library membership, paper cards, figure sources and the offline HTML download."""
import argparse
import re
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from browser_bulk_tags import api, upload_paper
from browser_table_citations import desktop_service


def seed(page):
    ids = {}
    for name in ('Alpha', 'Shared', 'Beta'):
        fields = {'tags': ['tissue map'] if name != 'Beta' else ['expression']}
        if name != 'Shared':
            fields['notes'] = (f'# Notes for {name}\n\n**一句话发现：** Unique finding from {name}.\n\n'
                               '## 研究方法\n\nA paired design.\n\n## 关键局限\n\nSmall sample.\n\n'
                               '![external placeholder](https://not-a-real-server.invalid/track.png)')
        ids[name] = upload_paper(page, name, **fields)
        if name == 'Shared':
            continue
        # Use the actual PDF crop API and a real raster preview for both Fig 1s.
        route = '/api/papers/' + ids[name] + '/figures'
        img = api(page, 'POST', route + '/crop', {'page': 1, 'bbox': [20, 20, 160, 120],
                  'label': 'Fig 1', 'caption': name + ' signal intensities'})
        api(page, 'PATCH', route + '/' + img['id'], {'tags': [name + ' marker'], 'observation': name + ' coordinates'})
    scopes = {}
    for library, pair in [('Library A', ['Alpha', 'Shared']), ('Library B', ['Beta', 'Shared'])]:
        record = api(page, 'POST', '/api/project-libraries', {'name': library, 'topic': 'Atlas',
                     'paper_ids': [ids[key] for key in pair]})
        scopes[library] = record['id']
    page.reload()
    return ids, scopes


def offline(browser, file, screenshots):
    context = browser.new_context(offline=True, viewport={'width': 1340, 'height': 900})
    try:
        page = context.new_page()
        requests = []
        page.on('request', lambda r: requests.append(r.url) if r.url.startswith(('http://','https://')) else None)
        page.goto(file.resolve().as_uri())
        expect(page.get_by_role('heading', name='Library A', exact=True)).to_be_visible()
        expect(page.locator('.paper:visible')).to_have_count(2)
        alpha = page.locator('.paper').filter(has=page.get_by_role('heading', name='Alpha', exact=True))
        expect(alpha).to_contain_text('Unique finding from Alpha.')
        expect(page.locator('.paper').filter(has_text='Unique finding from Beta.')).to_have_count(0)
        alpha.get_by_text('完整笔记', exact=True).click()
        expect(alpha.locator('.note')).to_contain_text('Small sample.')
        page.get_by_role('button', name='插图画廊').click()
        page.locator('#search').fill('Alpha signal')
        expect(page.locator('.figure:visible')).to_have_count(1)
        expect(page.locator('.figure:visible img')).to_be_visible()
        assert page.locator('.figure:visible img').evaluate('(img) => img.complete && img.naturalWidth > 0')
        page.locator('.figure:visible .image-button').click()
        expect(page.locator('#image-view')).to_be_visible()
        page.locator('#close-image').click()
        page.locator('[data-paper-link]').first.click()
        expect(alpha).to_be_focused()
        expect(page.locator('.paper:visible')).to_have_count(2)
        if screenshots:
            page.screenshot(path=screenshots / 'digest-offline.png', animations='disabled')
        assert not requests, requests
    finally:
        context.close()


def run(page, browser, bootstrap, directory, screenshots):
    page.goto(bootstrap)
    ids, scopes = seed(page)
    page.locator('#open-digest').click()
    panel = page.locator('#project-digest')
    def metric(key, count):
        expect(panel.locator(f'[data-digest-metric="{key}"] dd')).to_have_text(str(count))
    metric('papers', 3)
    metric('with_notes', 2)
    metric('figures', 2)
    shared = panel.locator(f'[data-digest-paper="{ids["Shared"]}"]')
    expect(shared).to_contain_text('尚无笔记')
    shared.locator('summary').click()
    expect(shared).to_contain_text('Library A')
    expect(shared).to_contain_text('Library B')
    page.locator('#project-switch').select_option(scopes['Library A'])
    metric('papers', 2)
    metric('figures', 1)
    alpha = panel.locator(f'[data-digest-paper="{ids["Alpha"]}"]')
    expect(alpha).to_contain_text('Unique finding from Alpha.')
    expect(panel.locator(f'[data-digest-paper="{ids["Beta"]}"]')).to_have_count(0)
    alpha.locator('summary').click()
    expect(alpha).to_contain_text('A paired design.')
    if screenshots:
        page.screenshot(path=screenshots / 'digest-library.png', animations='disabled')
    with page.expect_download() as download:
        page.locator('#digest-export').click()
    saved = directory / download.value.suggested_filename
    download.value.save_as(saved)
    assert saved.suffix == '.html'
    offline(browser, saved, screenshots)
    page.locator('[data-digest-view="figures"]').click()
    page.locator('#digest-query').fill('Alpha marker')
    source = panel.locator(f'[data-digest-figure][data-paper-id="{ids["Alpha"]}"]')
    expect(source).to_contain_text('Fig 1')
    source.locator('[data-digest-open-figure]').click()
    expect(page.locator('#project-modal')).to_be_hidden()
    expect(page).to_have_url(re.compile(r'paper=' + ids['Alpha']))
    expect(page.locator('#pdf-page')).to_have_value('1')
    page.locator('#open-digest').click()
    page.locator('#project-switch').select_option(scopes['Library B'])
    metric('papers', 2)
    page.locator('[data-digest-view="figures"]').click()
    page.locator('#digest-query').fill('Beta signal')
    beta = panel.locator(f'[data-digest-figure][data-paper-id="{ids["Beta"]}"]')
    expect(beta).to_contain_text('Fig 1')
    beta.locator('[data-digest-open-figure]').click()
    expect(page).to_have_url(re.compile(r'paper=' + ids['Beta']))
    expect(page.locator('#pdf-page')).to_have_value('1')
    page.locator('#open-digest').click()
    page.locator('[data-digest-view="papers"]').click()
    panel.locator(f'[data-digest-paper="{ids["Beta"]}"] [data-digest-open-paper]').click()
    expect(page.locator('#preview')).to_contain_text('Unique finding from Beta.')
    print('Two original libraries each contain 2 papers, 3 unique in all. Both Fig 1 sources and full notes open correctly. '
          'Export contains only Library A and works offline, including search, embedded figures and saved notes.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service-executable', type=Path)
    parser.add_argument('--screenshots', type=Path)
    parser.add_argument('--channel', default='chrome')
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(exist_ok=True, parents=True)
    with tempfile.TemporaryDirectory(prefix='bioreader-library-digest-') as temp, sync_playwright() as pw:
        root = Path(temp)
        with desktop_service(root / 'library', args.service_executable) as url:
            browser = pw.chromium.launch(channel=args.channel)
            try:
                run(browser.new_page(viewport={'width': 1440, 'height': 960}, locale='zh-CN'), browser,
                    url, root, args.screenshots)
            finally:
                browser.close()
