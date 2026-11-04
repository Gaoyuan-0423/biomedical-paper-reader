"""Desktop entry point. The HTTP service listens only on a random loopback port."""
from __future__ import annotations

import os
import secrets
import socket
import sys
import threading
import time
import tempfile
from pathlib import Path
from desktop_paths import desktop_data_dir

# The repository version continues to use app/data. Desktop data survives app updates.
os.environ['BIOREADER_DATA_DIR'] = str(desktop_data_dir())
# Windowed Windows executables have no standard streams; logging must not probe
# None.isatty(). Diagnostics write a JSON report instead of depending on a console.
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')
_self_test = None
_self_test_data = None
if __name__ == '__main__' and '--self-test' in sys.argv[1:]:
    import argparse
    parser = argparse.ArgumentParser(description='BioReader isolated bundle diagnostic (no GUI)')
    parser.add_argument('--self-test', type=Path, required=True, metavar='REPORT_JSON')
    _self_test = parser.parse_args().self_test.resolve()
    _self_test_data = tempfile.TemporaryDirectory(prefix='bioreader-bundle-check-')
    os.environ['BIOREADER_DATA_DIR'] = _self_test_data.name
sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

import uvicorn
import webview
from starlette.responses import PlainTextResponse
from main import app

# Markdown and Word exports use download links; pywebview disables downloads by default.
webview.settings["ALLOW_DOWNLOADS"] = True


def protect_local_service(app, port):
    """Restrict browser requests to this window's origin and session."""
    authority = f"127.0.0.1:{port}"
    origin = f"http://{authority}"
    token = secrets.token_urlsafe(32)
    # Cookies are scoped by host rather than port. Isolate concurrent windows.
    cookie_name = f"bioreader_session_{port}"

    @app.middleware("http")
    async def local_session(request, call_next):
        if request.headers.get("host") != authority:
            return PlainTextResponse("Forbidden", status_code=403)
        supplied_origin = request.headers.get("origin")
        fetch_site = request.headers.get("sec-fetch-site", "").lower()
        if (supplied_origin and supplied_origin != origin) or fetch_site in {"cross-site", "same-site"}:
            return PlainTextResponse("Forbidden", status_code=403)
        cookie = request.cookies.get(cookie_name, "")
        if not secrets.compare_digest(cookie.encode("utf-8"), token.encode("ascii")):
            # Only a top-level entry can establish a session. API and file requests
            # require the SameSite cookie even when a browser omits fetch metadata.
            if request.url.path != "/" or request.method != "GET":
                return PlainTextResponse("Forbidden", status_code=403)
            response = await call_next(request)
            response.set_cookie(cookie_name, token, httponly=True, samesite="strict", path="/")
            return response
        return await call_next(request)


def main():
    if sys.platform == 'win32':
        from desktop_diagnostics import windows_webview_runtime
        windows_webview_runtime()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    protect_local_service(app, port)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", use_colors=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("BioReader 后台服务无法启动。")
            time.sleep(0.05)
        webview.create_window("BioReader", f"http://127.0.0.1:{port}/", width=1440, height=900,
                              min_size=(800, 600))
        webview.start(gui='edgechromium' if sys.platform == 'win32' else None)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()


if __name__ == "__main__":
    if _self_test is not None:
        from desktop_diagnostics import run_self_test
        try:
            sys.exit(run_self_test(app, protect_local_service, _self_test, _self_test_data.name))
        finally:
            _self_test_data.cleanup()
    else:
        try:
            main()
        except Exception as exc:
            if sys.platform != 'win32':
                raise
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                'BioReader 无法启动。请保留完整的 BioReader 文件夹，并确认已安装 Microsoft Edge WebView2 Runtime。\n'
                '官方下载：https://developer.microsoft.com/en-us/microsoft-edge/webview2/\n\n' + str(exc),
                'BioReader', 0x10)
            sys.exit(1)
