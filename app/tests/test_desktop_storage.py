"""Exercise desktop storage and bundled frontend without touching user data."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]

# Run in a child process because the application resolves its paths at import time.
SCENARIO = r'''
import io
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter

app_dir = Path(sys.argv[1])
data_dir = Path(sys.argv[2])
bundle_dir = Path(sys.argv[3])
if sys.argv[4] == "frozen":
    sys._MEIPASS = str(bundle_dir)
sys.path.insert(0, str(app_dir))
from desktop import app, protect_local_service
import main

assert main.DATA_DIR == data_dir
assert main.FRONTEND_DIR == (bundle_dir if sys.argv[4] == "frozen" else app_dir) / "frontend"
protect_local_service(app, 18090)
with TestClient(app, base_url="http://127.0.0.1:18090") as client:
    assert client.get("/").status_code == 200
    for path in ("/app.js", "/styles.css", "/evidence.js", "/evidence.css", "/resources.js", "/resources.css", "/vendor/pdf.min.js",
                 "/vendor/pdf.worker.min.js", "/vendor/marked.min.js",
                 "/vendor/highlight.min.js", "/vendor/github.min.css", "/vendor/docx.umd.js"):
        response = client.get(path)
        assert response.status_code == 200 and response.content, (path, response.status_code)
    assert client.get("/api/papers").json() == []
    assert client.put("/api/settings", json={"model": "test-model"}).status_code == 200
    assert (data_dir / "settings.json").is_file()
    assert client.get("/api/settings").json()["model"] == "test-model"

    pdf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(pdf)
    response = client.post("/api/papers", files={"file": ("test.pdf", pdf.getvalue(), "application/pdf")})
    assert response.status_code == 200, response.text
    paper_id = response.json()["id"]
    assert (data_dir / "papers" / paper_id / "paper.pdf").is_file()
    assert client.get(f"/api/papers/{paper_id}/file").content == pdf.getvalue()
    assert any(paper["id"] == paper_id for paper in client.get("/api/papers").json())
    assert client.patch(f"/api/papers/{paper_id}", json={"notes": "saved note"}).status_code == 200
    assert client.get(f"/api/papers/{paper_id}").json()["notes"] == "saved note"
    assert client.delete(f"/api/papers/{paper_id}").status_code == 200
    assert client.get("/api/papers").json() == []
    assert not (data_dir / "papers" / paper_id).exists()
'''


class DesktopStorageTest(unittest.TestCase):
    def test_source_and_frozen_resource_paths_and_pdf_lifecycle(self):
        for mode in ("source", "frozen"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix="bioreader-test-") as tmp:
                root = Path(tmp)
                data = root / "data"
                bundle = root / "bundle"
                bundle.mkdir()
                (bundle / "frontend").symlink_to(APP_DIR / "frontend", target_is_directory=True)
                env = os.environ.copy()
                env["BIOREADER_DATA_DIR"] = str(data)
                result = subprocess.run(
                    [sys.executable, "-c", SCENARIO, str(APP_DIR), str(data), str(bundle), mode],
                    env=env, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
