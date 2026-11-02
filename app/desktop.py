"""Mac desktop entry point. The HTTP service listens only on a random loopback port."""
from __future__ import annotations

import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

# The repository version continues to use app/data. Desktop data survives app updates.
os.environ.setdefault(
    "BIOREADER_DATA_DIR", str(Path.home() / "Library" / "Application Support" / "BioReader")
)
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
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    protect_local_service(app, port)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
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
        webview.start()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()


if __name__ == "__main__":
    main()
