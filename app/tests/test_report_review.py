"""Provenance and concurrency of reader edits; no external model or private library."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_documents import fixture_pdf, main, TestClient


class ReportReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for key, value in [
            ("DATA_DIR", root),
            ("PAPERS_DIR", root / "papers"),
            ("SETTINGS_FILE", root / "settings.json"),
        ]:
            stub = patch.object(main, key, value)
            stub.start()
            self.addCleanup(stub.stop)
        main.save_settings({**main.DEFAULT_SETTINGS, "api_key": "synthetic-test"})
        self.c = TestClient(main.app)
        self.addCleanup(self.c.close)
        record = self.c.post(
            "/api/papers",
            files={
                "file": ("paper.pdf", fixture_pdf(marker="Methods. Blockade was tested in a separate cohort."))
            },
        ).json()
        self.u = "/api/papers/" + record["id"]
        self.d = main.paper_dir(record["id"])

    def test_sentence_selection_revision_and_saved_audit(self):
        claim = "A drives B."
        meta = self.c.patch(self.u, json={"notes": claim + "\n\n" + claim}).json()
        base = {"original": claim, "notes_revision": meta["notes_revision"]}
        self.assertEqual(self.c.post(self.u + "/report-review/preview", json=base).status_code, 409)
        base["occurrence"] = 1
        view = self.c.post(self.u + "/report-review/preview", json=base).raise_for_status().json()
        methods = next(s for s in view["sources"] if "intervention" in s["groups"])
        self.assertTrue("Blockade" in methods["quote"])
        self.assertEqual(methods["page"], 1)
        body = {
            **base,
            "replacement": "A correlates with B.",
            "reason": "The perturbation used a separate cohort.",
            "support": "association",
            "intervention": "present",
            "keys": [methods["key"]],
            "request_id": "a" * 32,
        }
        contradiction = {**body, "support": "intervention", "intervention": "unconfirmed"}
        self.assertEqual(self.c.post(self.u + "/report-review/apply", json=contradiction).status_code, 422)
        updated = self.c.post(self.u + "/report-review/apply", json=body).raise_for_status().json()
        self.assertEqual(updated["notes"], claim + "\n\nA correlates with B.")
        self.assertEqual(
            len(self.c.post(self.u + "/report-review/apply", json=body).json()["claim_reviews"]), 1
        )
        self.assertEqual(
            self.c.post(self.u + "/report-review/apply", json={**body, "request_id": "c" * 32}).status_code, 409
        )
        self.assertEqual(
            self.c.get(self.u + "/report-review").json()["reviews"][0]["sources"][0]["quote"], methods["quote"]
        )
        (self.d / "paper.pdf").write_bytes(fixture_pdf(marker="Replaced"))
        self.assertEqual(self.c.post(self.u + "/report-review/locate", json=methods).status_code, 409)

    def test_generation_records_actual_prompt_and_remains_historical(self):
        sup = self.c.post(
            self.u + "/supplements", files={"file": ("appendix.pdf", fixture_pdf(marker="Supp"))}
        ).json()["supplements"][0]["id"]
        figures = self.c.get(self.u + "/figures").json()
        main_fig = next(f for f in figures if f.get("document_id", "main") == "main" and not f.get("parent"))
        other = next(f for f in figures if f.get("document_id") == sup and not f.get("parent"))
        for f in figures:
            if f["id"] == main_fig["id"]:
                f["observation"] = "Blue marks are concentrated."
        main.save_figures(self.d, figures)
        sent = []

        async def model(*args, **kwargs):
            sent.append(args[3][-1]["content"])
            yield "Reader report"

        main.save_settings({**main.load_settings(), "vision_model": "local-fixture", "max_figures": 4})

        async def fail(*args, **kwargs):
            raise main.UpstreamError("Synthetic unreadable image")

        with patch.object(main, "stream_chat", model), patch.object(main, "_read_one_figure", fail):
            r = self.c.post(self.u + "/read?mode=deep")
        self.assertIn('"type": "done"', r.text)
        self.assertIn("Blue marks are concentrated.", sent[0])
        overview = self.c.get(self.u + "/report-review").raise_for_status().json()["coverage"]
        rows = {f["id"]: f for f in overview["figures"]}
        self.assertEqual(rows[main_fig["id"]]["used"], "visual")
        self.assertEqual((rows[other["id"]]["used"], rows[other["id"]]["failed"]), ("caption", True))
        for f in figures:
            f["observation"] = "A later reading"
        main.save_figures(self.d, figures)
        self.assertEqual(self.c.get(self.u + "/report-review").json()["coverage"], overview)
        self.assertNotIn("notes_figure_coverage", main.summary_of(main.read_meta(self.d)))
        # Disabling figure context leaves every whole figure visible as unprocessed.
        with patch.object(main, "stream_chat", model):
            self.c.post(self.u + "/read?mode=quick&use_figures=0")
        cov = self.c.get(self.u + "/report-review").json()["coverage"]
        self.assertEqual({f["used"] for f in cov["figures"]}, {"none"})
