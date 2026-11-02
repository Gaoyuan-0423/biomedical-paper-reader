"""Desktop-only local HTTP session protections."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("BIOREADER_DATA_DIR", tempfile.mkdtemp(prefix="bioreader-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop import protect_local_service
import webview
from fastapi import FastAPI
from fastapi.testclient import TestClient


def make_client(port):
    app = FastAPI()

    @app.get("/")
    def index():
        return {"ok": True}

    @app.get("/api/papers")
    def papers():
        return []

    protect_local_service(app, port)
    return TestClient(app, base_url=f"http://127.0.0.1:{port}")


class DesktopSessionTest(unittest.TestCase):
    def test_downloads_enabled_for_exports(self):
        self.assertTrue(webview.settings["ALLOW_DOWNLOADS"])

    def test_requires_session_and_rejects_foreign_requests(self):
        with make_client(18080) as client:
            self.assertEqual(client.get("/api/papers").status_code, 403)
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/api/papers").status_code, 200)
            for headers in ({"Origin": "https://example.org"},
                            {"Sec-Fetch-Site": "cross-site"},
                            {"Host": "evil.example:18080"}):
                self.assertEqual(client.get("/api/papers", headers=headers).status_code, 403)

    def test_concurrent_instances_have_distinct_cookie_names(self):
        with make_client(18080) as first, make_client(18081) as second:
            first.get("/")
            second.get("/")
            # Simulate a shared cookie jar, since browsers scope cookies by host.
            first.cookies.update(second.cookies)
            second.cookies.update(first.cookies)
            self.assertEqual(first.get("/api/papers").status_code, 200)
            self.assertEqual(second.get("/api/papers").status_code, 200)


if __name__ == "__main__":
    unittest.main()
