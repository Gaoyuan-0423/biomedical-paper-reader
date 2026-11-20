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

    def test_adjacent_citation_and_source_offset_scope(self):
        meta = main.read_meta(self.d)
        _, sources, _ = main.ns.prepare_sources(self.d, meta)
        snapshot = main.ns.save_snapshot(self.d, 'a' * 32, meta, sources[:1])
        link = '[N1](note-source:' + snapshot['snapshot_id'] + ':N1)'
        text = '🔬 A **drives** B.\n\nA drives B. Claim B was measured ' + link + '.'
        meta.update(notes=text, notes_source_snapshot=snapshot['snapshot_id'], notes_revision=2)
        main.write_meta(self.d, meta)
        for claim, expected in [('A drives B.', False), ('Claim B was measured', True)]:
            result = self.c.post(self.u + '/report-review/preview', json={
                'original':claim, 'start':text.index(claim), 'notes_revision':2}).raise_for_status().json()
            self.assertEqual(any('citation' in item['groups'] for item in result['sources']), expected)
        body = {'original':'A drives B.', 'notes_revision':2, 'start':2,
                'replacement':'A correlates with B.', 'reason':'Observed only.', 'support':'insufficient',
                'intervention':'unconfirmed', 'keys':[], 'request_id':'b'*32}
        self.assertEqual(self.c.post(self.u+'/report-review/apply', json=body).status_code,409)
        body['start'] = text.index(body['original'])
        result = self.c.post(self.u+'/report-review/apply', json=body).raise_for_status().json()
        self.assertEqual(result['notes'], text.replace('\n\nA drives B.', '\n\nA correlates with B.'))

    def test_changed_pdf_and_unversioned_figures_are_never_rebound_to_the_new_bytes(self):
        from unittest.mock import AsyncMock
        pdf = self.d / 'paper.pdf'
        original_bytes = pdf.read_bytes()
        fig = next(f for f in main.load_figures(self.d) if not f.get('parent'))
        self.c.patch(self.u+'/figures/'+fig['id'],json={'observation':'Historical visual finding'})
        sent = []
        async def model(*args, **kwargs):
            sent.append(args[3][-1]['content'])
            yield 'Review claim.'
        with patch.object(main,'stream_chat',model):
            self.assertIn('"type": "done"', self.c.post(self.u+'/read?mode=quick').text)
        before = self.c.get(self.u+'/report-review').json()['coverage']['figures'][0]
        self.assertEqual((before['used'],before['available']), ('visual',True))
        base = {'notes_revision':main.read_meta(self.d)['notes_revision'],'original':'Review claim.'}
        old = next(i for i in self.c.post(self.u+'/report-review/preview',json=base).json()['sources'] if i['kind']=='figure')
        pdf.write_bytes(fixture_pdf(label='Figure 9',marker='Other document'))
        historic = self.c.get(self.u+'/report-review').json()['coverage']['figures'][0]
        self.assertEqual((historic['used'],historic['available']),('visual',False))
        self.assertEqual(self.c.post(self.u+'/report-review/locate',json=old).status_code,409)
        self.assertFalse(any(i['kind']=='figure' for i in self.c.post(self.u+'/report-review/preview',json=base).json()['sources']))
        decision = {**base,'replacement':'Changed.','reason':'External replacement','support':'association',
                    'intervention':'unconfirmed','keys':[old['key']],'request_id':'f'*32}
        self.assertEqual(self.c.post(self.u+'/report-review/apply',json=decision).status_code,409)
        main.save_settings({**main.load_settings(),'vision_model':'mock-vision'})
        with patch.object(main,'stream_chat',model), patch.object(main,'_read_one_figure',AsyncMock()) as vision:
            self.assertIn('"type": "done"',self.c.post(self.u+'/read?mode=deep').text)
            vision.assert_not_awaited()
        self.assertNotIn('Historical visual finding',sent[-1])
        self.assertEqual(self.c.get(self.u+'/report-review').json()['coverage']['figures'][0]['used'],'none')
        pdf.write_bytes(original_bytes)
        legacy = main.read_meta(self.d)
        legacy['document_revisions'].pop('main')
        main.write_meta(self.d,legacy)
        self.assertFalse(any(i['kind']=='figure' for i in self.c.post(self.u+'/report-review/preview',
            json={**base,'notes_revision':legacy['notes_revision']}).json()['sources']))
        self.assertEqual(main.load_figures(self.d)[0]['observation'],'Historical visual finding')

    def test_official_replacement_retains_but_does_not_reuse_old_figure_notes(self):
        fig = next(f for f in main.load_figures(self.d) if not f.get('parent'))
        self.c.patch(self.u+'/figures/'+fig['id'],json={'observation':'Old read','caption':'Old manual caption'})
        new = self.c.post(self.u+'/file',files={'file':('new.pdf',fixture_pdf(marker='New experiment'))}).raise_for_status().json()
        live = next(f for f in main.load_figures(self.d) if f['id']==fig['id'])
        self.assertEqual((live['observation'],live['caption']), ('Old read','Old manual caption'))
        locator = {'figure_id':fig['id'],'document_id':'main','page':fig['page'],'bbox':fig['bbox'],
                   'source_revision':new['document_revisions']['main']}
        checked = self.c.post(self.u+'/report-review/locate',json=locator).raise_for_status().json()
        self.assertEqual((checked['observation'],checked['caption']),('',''))
        self.c.patch(self.u+'/figures/'+fig['id'],json={'observation':'New read','caption':'New caption'})
        checked = self.c.post(self.u+'/report-review/locate',json=locator).raise_for_status().json()
        self.assertEqual((checked['observation'],checked['caption']),('New read','New caption'))

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
