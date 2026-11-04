"""Explicit isolated executable diagnostic; never reads the user's paper library."""
import io
import json
import platform
import sys
import traceback
from pathlib import Path


def windows_webview_runtime():
    # Load bundled pythonnet/WinForms/WebView2 assemblies before opening a window.
    # Calling the official API also detects a missing Evergreen runtime instead
    # of silently falling back to the obsolete MSHTML renderer.
    import webview.platforms.edgechromium  # noqa: F401
    from Microsoft.Web.WebView2.Core import CoreWebView2Environment
    version = CoreWebView2Environment.GetAvailableBrowserVersionString(None)
    if not version:
        raise RuntimeError('Microsoft Edge WebView2 Runtime is not installed.')
    import webview.platforms.winforms as winforms
    if winforms.renderer != 'edgechromium':
        raise RuntimeError('A usable Microsoft Edge WebView2 Runtime is required.')
    return str(version)


def synthetic_pdf():
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 60 740 Td (BioReader synthetic bundle diagnostic.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def run_self_test(app, protect_local_service, report_path, data_dir):
    result = {'status': 'failed', 'platform': sys.platform, 'machine': platform.machine(),
              'frozen': bool(getattr(sys, 'frozen', False)), 'gui_tested': False, 'checks': []}
    try:
        from fastapi.testclient import TestClient
        import main
        assert main.DATA_DIR.resolve() == Path(data_dir).resolve()
        if sys.platform == 'win32':
            result['webview2_runtime'] = windows_webview_runtime()
            result['checks'].append('Bundled pythonnet, WinForms and WebView2 assemblies load; runtime is present')
        protect_local_service(app, 18090)
        with TestClient(app, base_url='http://127.0.0.1:18090') as client:
            assert client.get('/api/papers').status_code == 403
            assert client.get('/').status_code == 200
            for route in ('/app.js', '/styles.css', '/chat.js', '/resources.js', '/library-search.js', '/zotero.js', '/vendor/pdf.min.js', '/vendor/pdf.worker.min.js', '/vendor/marked.min.js', '/vendor/docx.umd.js'):
                response = client.get(route)
                assert response.status_code == 200 and response.content, route
            assert client.get('/api/papers').json() == []
            result['checks'].append('Session-protected backend and bundled frontend assets')
            payload = synthetic_pdf()
            response = client.post('/api/papers', files={'file': ('diagnostic.pdf', payload, 'application/pdf')})
            assert response.status_code == 200, response.text
            paper_id = response.json()['id']
            url = '/api/papers/' + paper_id
            assert client.get(url + '/file').content == payload
            assert client.patch(url, json={'notes': '# Diagnostic\n\nSynthetic note.'}).status_code == 200
            assert client.get(url).json()['notes'].startswith('# Diagnostic')
            assert client.get('/api/library/search', params={'q': 'synthetic', 'scope': 'pdf'}).json()['items']
            assert client.get(url + '/chat').status_code == 200
            assert client.get(url + '/resources').status_code == 200
            assert client.delete(url).status_code == 200
            assert client.get('/api/papers').json() == []
            result['checks'].append('Synthetic PDF import, native parsing, file retrieval, note persistence, search, chat/resource reads and deletion')
        result['status'] = 'passed'
    except Exception:
        result['error'] = traceback.format_exc()
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return 0 if result['status'] == 'passed' else 1
