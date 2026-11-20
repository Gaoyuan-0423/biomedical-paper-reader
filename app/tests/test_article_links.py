"""Scholarly link previews, PDF identity checks and pinned public transport."""
import asyncio
import gzip
import hashlib
import io
import ipaddress
import json
import socket
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from pypdf import PdfReader, PdfWriter

from test_documents import fixture_pdf
import article_links as al
import public_download as net


PAGE = 'https://journals.example.org/papers/abc'
PDF = 'https://journals.example.org/reprints/article.pdf'
DOI = '10.1234/example'
TITLE = 'MAIN synthetic paper for source isolation.'


def metadata_pdf(text=TITLE, author='A. Author'):
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(fixture_pdf())))
    writer.add_metadata({'/Title': text, '/Author': author})
    dest = io.BytesIO()
    writer.write(dest)
    return dest.getvalue()


class ArticlePreviewTests(unittest.IsolatedAsyncioTestCase):
    async def preview(self, page, url=PAGE, final_url=None):
        with patch.object(al, 'fetch_public', AsyncMock(return_value=(final_url or url, 'text/html', page.encode()))):
            return await al.inspect_url(url)

    async def test_citation_preview_resolves_address_and_preserves_redirect_origin(self):
        page = f'''<head><meta name="citation_doi" content="{DOI}"/>
          <meta name="citation_title" content="A controlled study of human cognition"/>
          <meta name="citation_journal_title" content="Brain"/><meta name="citation_author" content="Chen Q."/>
          <meta name="citation_author" content="Smith J."/><meta name="citation_date" content="2020/04/15"/>
          <meta name="citation_pmid" content="1234567"/><META NAME="citation_pdf_url" CONTENT="../paper.pdf"/></head>'''
        r = await self.preview(page, final_url='https://publisher.example.org/article/11/full')
        self.assertEqual(r['doi'], DOI)
        self.assertEqual(r['authors'], 'Chen Q.; Smith J.')
        self.assertEqual(r['year'], '2020')
        self.assertEqual(r['_link'], {'article_url': PAGE, 'pdf_url':'https://publisher.example.org/article/paper.pdf', 'inspected':True})

    async def test_identifier_only_page_can_be_completed_by_metadata_provider(self):
        r = await self.preview('<meta name="citation_doi" content="10.1234/example">')
        self.assertEqual((r['doi'], r['title']), ('10.1234/example',''))

    async def test_jsonld_graph_ignores_references_and_accepts_without_identifier(self):
        record = {'@type':'ScholarlyArticle', 'headline':'Spatial patterns in a matched tissue cohort',
                  'encoding':{'encodingFormat':'application/pdf','contentUrl':PDF},
                  'author':[{'@type':'Person','name':'Paper Author'}],
                  'citation':[{'@type':'ScholarlyArticle','name':'A different paper','identifier':'10.1234/unrelated'}]}
        data = json.dumps({'@graph':[{'@type':'WebSite','name':'Publisher'},record]})
        result = await self.preview('<script type="application/ld+json">'+data+'</script>')
        self.assertEqual(result['authors'],'Paper Author')
        self.assertEqual(result['doi'],'')
        self.assertEqual(result['_link']['pdf_url'], PDF)
        with self.assertRaisesRegex(al.ArticleLinkError, '多篇论文'):
            data = json.dumps([record, {**record,'headline':'A different independent paper'}])
            await self.preview('<script type="application/ld+json">'+data+'</script>')

    async def test_arxiv_abstract_without_explicit_pdf_and_dublin_core(self):
        record = await self.preview('<meta name="DC.Title" content="Attention Is All You Need">',url='https://arxiv.org/abs/1706.03762')
        self.assertEqual(record['_link']['pdf_url'],'https://arxiv.org/pdf/1706.03762')
        record = await self.preview('<meta name="DC.Title" content="Matched tissue samples across stages">'
                                    '<meta name="DC.Creator" content="H. Wang"><meta name="DC.Type" content="Journal Article">')
        self.assertEqual(record['authors'], 'H. Wang')
        self.assertNotIn('pdf_url',record['_link'])

    async def test_refuses_generic_pages_mismatched_dois_and_supplement(self):
        for html in ('<title>Please log in</title><meta property="og:title" content="Please sign in to retrieve the article">',
                     '<meta name="citation_title" content="Supplementary materials on the study"><meta name="citation_doi" content="10.1234/abc">',
                     '<meta name="citation_title" content="Title sufficiently long"><meta name="citation_doi" content="10.1234/abc"><meta name="DC.Identifier" content="10.1234/def">'):
            with self.subTest(html=html), self.assertRaises(al.ArticleLinkError):
                await self.preview(html)

    async def test_unsafe_and_supplement_pdf_links_do_not_discard_metadata(self):
        for target in ('http://127.0.0.1/file.pdf','javascript:void(0)','/supporting-information/data.pdf'):
            r = await self.preview('<meta name="citation_title" content="Title sufficiently long"><meta name="citation_doi" content="10.1234/abc">'
                                   f'<meta name="citation_pdf_url" content="{target}">')
            self.assertNotIn('pdf_url',r['_link'])
            self.assertEqual((await al.download_pdf(r['_link'],r,10000))['outcome'],'not_found')

    async def test_direct_pdf_keeps_only_fingerprint_and_validates_redownload(self):
        pdf = metadata_pdf()
        with patch.object(al, 'fetch_public', AsyncMock(return_value=(PDF, 'application/pdf', pdf))):
            record = await al.inspect_url(PDF)
            self.assertEqual(record['title'], TITLE)
            self.assertEqual(record['doi'], '')
            self.assertEqual(record['authors'], 'A. Author')
            self.assertEqual(record['_link']['sha256'], hashlib.sha256(pdf).hexdigest())
            self.assertNotIn('content',record)
            self.assertEqual((await al.download_pdf(record['_link'],record,1000000))['content'], pdf)
            changed = {**record, 'title':'Completely unrelated research paper'}
            self.assertEqual((await al.download_pdf(record['_link'],changed,1000000))['outcome'],'needs_review')
        with patch.object(al, 'fetch_public', AsyncMock(return_value=(PDF, 'application/pdf', metadata_pdf(author='Edited author')))):
            self.assertEqual((await al.download_pdf(record['_link'],record,1000000))['outcome'],'needs_review')

    async def test_obvious_pdf_anchor_ignores_navigation_supplements_and_ambiguous_candidates(self):
        meta = '<meta name="citation_doi" content="10.1234/abc"><meta name="citation_title" content="A study of matched populations">'
        navigation = '<nav><a href="/handbook.pdf">PDF</a></nav><a href="/suppl/table.pdf" aria-label="Download PDF">Supplementary table</a>'
        result = await self.preview(meta + navigation + '<a href="/paper.pdf"><span>Download</span></a>')
        self.assertEqual(result['_link']['pdf_url'],'https://journals.example.org/paper.pdf')
        result = await self.preview(meta + '<a href="/one.pdf">PDF</a><a href="/two.pdf">PDF</a>')
        self.assertNotIn('pdf_url',result['_link'])

    async def test_simple_heading_confirmed_by_paper_or_declined(self):
        body = metadata_pdf()
        html = f'<main><h1>{TITLE}</h1><a href="{PDF}" aria-label="Download PDF"><span>Read</span></a></main>'
        fetch = AsyncMock(side_effect=[(PAGE,'text/html',html.encode()), (PDF,'application/pdf',body)])
        with patch.object(al,'fetch_public',fetch):
            record = await al.inspect_url(PAGE)
        self.assertEqual(record['title'],TITLE)
        self.assertEqual(record['_link']['sha256'],hashlib.sha256(body).hexdigest())
        self.assertEqual(fetch.await_count,2)
        with patch.object(al,'fetch_public',AsyncMock(side_effect=[
                (PAGE,'text/html',html.replace(TITLE,'Unrelated paper on a different subject').encode()),
                (PDF,'application/pdf',body)])), self.assertRaises(al.ArticleIdentityError):
            await al.inspect_url(PAGE)

    async def test_front_title_ignores_journal_header_and_joins_wrapped_block(self):
        class FirstPage:
            def extract_text(self, **kwargs):
                return ('Journal of Population Research 7 (2023) 12–21\n\n'
                        '    Patterns of cell migration\n    during adult development\n\nSmith J.    SMITH@EXAMPLE.ORG')
        page = FirstPage()
        self.assertEqual(al._front_title(page, page.extract_text()),'Patterns of cell migration during adult development')
        self.assertEqual(al._title('article12345.dvi'),'')

    async def test_same_journal_prefers_https_for_declared_http_pdf(self):
        body = metadata_pdf()
        link = {'article_url':PAGE, 'pdf_url':PDF.replace('https:', 'http:')}
        with patch.object(al,'fetch_public', AsyncMock(return_value=(PDF,'application/pdf',body))) as fetch:
            self.assertEqual((await al.download_pdf(link, {'title':TITLE},100000))['outcome'],'downloaded')
        self.assertEqual(fetch.call_args.args[0], PDF)

    async def test_rejects_nonpaper_and_supplement_pdf(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100,height=100)
        writer.add_metadata({'/Title':'Corporate quarterly financial statement'})
        file = io.BytesIO(); writer.write(file)
        for body in (file.getvalue(), fixture_pdf(marker='Supporting information')):
            with patch.object(al, 'fetch_public', AsyncMock(return_value=(PDF,'application/pdf',body))), self.assertRaises(al.ArticleLinkError):
                await al.inspect_url(PDF)


class PinnedTransportTests(unittest.IsolatedAsyncioTestCase):
    def test_invalid_address_authority_and_nonpublic_ips(self):
        for url in ('file:///file.pdf','https://127.0.0.1/a','http://[::ffff:127.0.0.1]/a',
                    'http://@paper.example.org/a','http://user:pass@paper.example.org','http://paper.example.org:8000/',
                    'http://localhost/a','http://x.localhost/a','http://224.0.0.1/','http://[64:ff9b::7f00:1]/',
                    'http://evil.example.org\\@paper.example.org/','https://example.org/a\rb'):
            with self.subTest(url=url), self.assertRaises(al.ArticleLinkError):
                net.public_url(url)
        self.assertEqual(net.public_url('https://JOURNAL.example.org:443/文章#pdf'), 'https://journal.example.org/%E6%96%87%E7%AB%A0')
        self.assertEqual(net.public_url('http://8.8.8.8/page'), 'http://8.8.8.8/page')

    async def test_mixed_dns_results_reject_whole_host(self):
        infos = [(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,80)) for ip in ('8.8.8.8','127.0.0.1')]
        with patch.object(asyncio.get_running_loop(),'getaddrinfo',AsyncMock(return_value=infos)), self.assertRaises(al.ArticleLinkError):
            await net._addresses(httpx.URL(PAGE))

    async def test_transport_targets_prevalidated_ip_and_keeps_tls_name_and_authority(self):
        client = AsyncMock()
        client.send.return_value = 'ok'
        ips = [ipaddress.ip_address('8.8.8.8')]
        with patch.object(net,'_addresses',AsyncMock(return_value=ips)) as resolutions:
            self.assertEqual(await net._request(client, httpx.URL(PAGE)), 'ok')
        resolutions.assert_awaited_once()
        req = client.send.call_args.args[0]
        self.assertEqual(req.url.host,'8.8.8.8')
        self.assertEqual(req.headers['host'],'journals.example.org')
        self.assertEqual(req.extensions['sni_hostname'],'journals.example.org')
        self.assertNotIn('cookie',req.headers)
        self.assertNotIn('authorization',req.headers)

    async def test_redirect_validated_before_next_request(self):
        response = httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
        with patch.object(net,'_request',AsyncMock(return_value=response)) as request, self.assertRaises(al.ArticleLinkError):
            await net.fetch_public(PAGE)
        request.assert_awaited_once()

    async def test_streaming_limits_check_compressed_body_and_html(self):
        class Stream(httpx.AsyncByteStream):
            def __init__(self,data):
                self.data = data
            async def __aiter__(self):
                yield self.data

        payload = b'x'*3000
        headers = {'content-type':'text/html','content-encoding':'gzip'}
        resp = httpx.Response(200,headers=headers,stream=Stream(gzip.compress(payload)))
        with self.assertRaisesRegex(al.ArticleLinkError,'超过大小'):
            await net._body(resp,1000)
        with patch.object(net,'HTML_LIMIT',1024), self.assertRaisesRegex(al.ArticleLinkError,'超过大小'):
            await net._body(httpx.Response(200,stream=Stream(payload)),4000)
        resp = httpx.Response(200,headers=headers,stream=Stream(gzip.compress(payload)))
        self.assertEqual(await net._body(resp,4000),payload)
