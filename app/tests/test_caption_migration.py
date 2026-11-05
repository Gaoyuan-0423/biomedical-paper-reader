"""Legacy caption upgrades use synthetic PDFs and isolated record directories."""
import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import caption_migration as migration
from figures import extract_captions, load_figures, save_figures
from test_figure_captions import pdf_bytes, text


def fixture(body='Synthetic samples include three independent groups.'):
    return pdf_bytes([[('rect', 60, 100, 500, 450),
        text('Supplementary Figure 1', 470, size=13, bold=True),
        text(body, 490, size=10),
        text('Error bars represent the synthetic standard deviation.', 505, size=10)]])


class CaptionMigrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='bioreader-caption-migration-')
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.source = self.directory / 'paper.pdf'
        self.source.write_bytes(fixture())
        self.full = extract_captions(io.BytesIO(self.source.read_bytes()))[0]
        self.record = dict(self.full, id='f001', source='auto', parent=None,
            document_id='main', caption='Supplementary Figure 1',
            observation='My saved observation.', width=640, height=480)
        save_figures(self.directory, [self.record])
        (self.directory / 'figures').mkdir()
        (self.directory / 'figures/f001.png').write_bytes(b'unchanged existing crop')
        (self.directory / 'meta.json').write_text('{"notes":"My separate paper note."}')
        self.lock = threading.RLock()

    def upgrade(self):
        return migration.upgrade_captions(self.directory, {'main': self.source}, self.lock)

    def test_legacy_caption_expands_without_regenerating_images_or_changing_research(self):
        before_files = {name: (self.directory / name).read_bytes()
                        for name in ('paper.pdf', 'figures/f001.png', 'meta.json')}
        with patch('figures.render_page', side_effect=AssertionError('migration must not render')):
            upgraded = self.upgrade()[0]
        self.assertEqual(upgraded, dict(self.record, caption=self.full['caption']))
        for name, data in before_files.items():
            self.assertEqual((self.directory / name).read_bytes(), data, name)
        marker = json.loads((self.directory / migration._MARKER).read_text())['documents']['main']
        self.assertEqual(marker['source_sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_current_marker_skips_pdf_parse_even_after_new_observation(self):
        self.upgrade()
        records = load_figures(self.directory)
        records[0]['observation'] = 'A newer model result.'
        save_figures(self.directory, records)
        with patch.object(migration, 'extract_captions', side_effect=AssertionError('unnecessary PDF rescan')) as extract:
            self.assertEqual(self.upgrade(), records)
            extract.assert_not_called()

    def test_manual_edited_empty_and_unmarked_custom_captions_are_preserved(self):
        for values in ({'caption': 'My manual caption', 'caption_edited': True},
                       {'caption': '', 'caption_edited': True},
                       {'caption': 'Supplementary Figure 1', 'source': 'manual'},
                       {'caption': 'My older unmarked manual caption.'}):
            with self.subTest(values=values):
                record = dict(self.record, **values)
                save_figures(self.directory, [record])
                self.assertEqual(self.upgrade(), [record])

    def test_child_caption_can_expand_when_its_parent_was_already_updated(self):
        parent = dict(self.record, caption=self.full['caption'])
        child = dict(self.record, id='f002', label='Figure 1A', parent='f001', bbox=[65, 100, 250, 300])
        save_figures(self.directory, [parent, child])
        result = self.upgrade()
        self.assertEqual(result, [parent, dict(child, caption=self.full['caption'])])

    def test_pdf_or_algorithm_change_invalidates_marker(self):
        self.upgrade()
        real = migration.extract_captions
        with patch.object(migration, 'extract_captions', wraps=real) as extract:
            self.source.write_bytes(fixture('Different synthetic sample conditions are recorded.'))
            self.upgrade()
            self.assertEqual(extract.call_count, 1)
            with patch.object(migration, 'CAPTION_EXTRACTION_VERSION', migration.CAPTION_EXTRACTION_VERSION + 1):
                self.upgrade()
            self.assertEqual(extract.call_count, 2)

    def test_delayed_parse_keeps_new_observation_and_new_manual_caption(self):
        real = migration.extract_captions
        for edit in ({'observation': 'Concurrent model result.'},
                     {'caption': '', 'caption_edited': True}):
            with self.subTest(edit=edit):
                save_figures(self.directory, [self.record])
                def parse(snapshot):
                    # Prove expensive parsing runs outside the storage lock.
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        def write():
                            with self.lock:
                                fresh = load_figures(self.directory)
                                fresh[0].update(edit)
                                save_figures(self.directory, fresh)
                        pool.submit(write).result(timeout=2)
                    return real(snapshot)
                with patch.object(migration, 'extract_captions', side_effect=parse):
                    result = self.upgrade()[0]
                expected = dict(self.record, **edit)
                if 'caption' not in edit:
                    expected['caption'] = self.full['caption']
                self.assertEqual(result, expected)

    def test_removed_figure_or_changed_pdf_during_parse_is_not_restored(self):
        real = migration.extract_captions
        def remove(snapshot):
            save_figures(self.directory, [])
            return real(snapshot)
        with patch.object(migration, 'extract_captions', side_effect=remove):
            self.assertEqual(self.upgrade(), [])
        save_figures(self.directory, [self.record])
        def replace(snapshot):
            self.source.write_bytes(fixture('Changed while the snapshot was being parsed.'))
            return real(snapshot)
        with patch.object(migration, 'extract_captions', side_effect=replace):
            self.assertEqual(self.upgrade(), [self.record])
        self.assertFalse((self.directory / migration._MARKER).exists())

    def test_failed_parse_preserves_records_and_can_retry_after_cooldown(self):
        with patch.object(migration.time, 'monotonic', return_value=100), patch.object(
                migration, 'extract_captions', side_effect=ValueError('temporary failure')) as extract:
            self.assertEqual(self.upgrade(), [self.record])
            self.assertEqual(self.upgrade(), [self.record])
            self.assertEqual(extract.call_count, 1)
        self.assertFalse((self.directory / migration._MARKER).exists())
        with patch.object(migration.time, 'monotonic', return_value=200):
            self.assertEqual(self.upgrade()[0]['caption'], self.full['caption'])

    def test_backup_read_does_not_migrate_or_replace_a_damaged_primary(self):
        saved = dict(self.record, observation='Observation retained in backup.')
        save_figures(self.directory, [saved])
        save_figures(self.directory, [saved])
        damaged = b'[{"id":"f001","caption":'
        (self.directory / 'figures.json').write_bytes(damaged)
        backup = (self.directory / 'figures.json.bak').read_bytes()
        with patch.object(migration, 'extract_captions') as extract:
            self.assertEqual(self.upgrade(), [saved])
            extract.assert_not_called()
        self.assertEqual((self.directory / 'figures.json').read_bytes(), damaged)
        self.assertEqual((self.directory / 'figures.json.bak').read_bytes(), backup)
        self.assertFalse((self.directory / migration._MARKER).exists())

    def test_other_document_and_ambiguous_or_changed_geometry_are_not_reassigned(self):
        other = dict(self.record, id='s002', document_id='a' * 32)
        save_figures(self.directory, [self.record, other])
        with patch.object(migration, 'extract_captions', return_value=[self.full, self.full]):
            self.assertEqual(self.upgrade(), [self.record, other])
        moved = dict(self.record, bbox=[60, 120, 400, 400])
        save_figures(self.directory, [moved, other])
        self.assertEqual(self.upgrade(), [moved, other])

    def test_concurrent_gallery_requests_parse_same_document_only_once(self):
        real = migration.extract_captions
        started, release = threading.Event(), threading.Event()
        def parse(snapshot):
            started.set()
            self.assertTrue(release.wait(3))
            return real(snapshot)
        with patch.object(migration, 'extract_captions', side_effect=parse) as extract, ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.upgrade)
            self.assertTrue(started.wait(3))
            second = pool.submit(self.upgrade)
            release.set()
            self.assertEqual(first.result(timeout=3), second.result(timeout=3))
            self.assertEqual(extract.call_count, 1)


if __name__ == '__main__':
    unittest.main()
