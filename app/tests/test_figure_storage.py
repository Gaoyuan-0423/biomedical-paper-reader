"""Real filesystem concurrency and recovery for synthetic figure records."""
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from test_documents import main
import figures


class FigureStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bioreader-figure-storage-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.primary = self.directory / 'figures.json'
        self.backup = self.directory / 'figures.json.bak'
        self.original = [{'id': 'f001', 'caption': 'Synthetic caption', 'observation': '## Saved observation'}]
        self.updated = [dict(self.original[0], observation='## New complete observation\n\nFinal paragraph.')]
        figures.save_figures(self.directory, self.original)

    def test_readers_always_see_complete_old_or_new_json_during_a_real_write(self):
        ready, release = threading.Event(), threading.Event()
        replace = figures.os.replace

        def delayed_replace(source, destination):
            if Path(destination) == self.primary:
                ready.set()
                if not release.wait(2):
                    raise AssertionError('Reader failed to reach the write barrier')
            replace(source, destination)

        with ThreadPoolExecutor(max_workers=1) as executor:
            with patch.object(figures.os, 'replace', delayed_replace):
                writing = executor.submit(figures.save_figures, self.directory, self.updated)
                try:
                    self.assertTrue(ready.wait(2))
                    for _ in range(10):
                        self.assertEqual(figures.load_figures(self.directory), self.original)
                        self.assertEqual(json.loads(self.primary.read_text('utf-8')), self.original)
                finally:
                    release.set()
                writing.result(timeout=2)
        self.assertEqual(figures.load_figures(self.directory), self.updated)
        self.assertEqual(json.loads(self.backup.read_text('utf-8')), self.original)
        self.assertEqual(list(self.directory.glob('.figures-*.tmp')), [])

    def test_failed_replace_keeps_the_previous_observation_and_cleans_temporary_files(self):
        replace = figures.os.replace

        def fail_primary(source, destination):
            if Path(destination) == self.primary:
                raise OSError('Synthetic disk failure')
            replace(source, destination)

        with patch.object(figures.os, 'replace', fail_primary):
            with self.assertRaisesRegex(OSError, 'Synthetic disk failure'):
                figures.save_figures(self.directory, self.updated)
        self.assertEqual(figures.load_figures(self.directory), self.original)
        self.assertEqual(json.loads(self.backup.read_text('utf-8')), self.original)
        self.assertEqual(list(self.directory.glob('.figures-*.tmp')), [])

    def test_corrupted_primary_reads_valid_backup_without_overwriting_damaged_evidence(self):
        figures.save_figures(self.directory, self.updated)
        damaged = b'[{"id":"f001","observation":"truncated'
        self.primary.write_bytes(damaged)
        self.assertEqual(figures.load_figures(self.directory), self.original)
        self.assertEqual(self.primary.read_bytes(), damaged)
        # The next explicit successful mutation starts with recovered records;
        # it must not replace the backup with truncated JSON.
        recovered = figures.load_figures(self.directory)
        recovered[0]['caption'] = 'Updated after recovery'
        figures.save_figures(self.directory, recovered)
        self.assertEqual(figures.load_figures(self.directory), recovered)
        self.assertEqual(json.loads(self.backup.read_text('utf-8')), self.original)

    def test_invalid_primary_and_backup_stop_reads_and_writes_instead_of_becoming_empty(self):
        for damaged in (b'[', b'{}', b'[null]', b'[{"id":"f001","observation":42}]'):
            with self.subTest(damaged=damaged):
                self.primary.write_bytes(damaged)
                self.backup.write_bytes(b'also broken')
                with self.assertRaises(figures.FigureStorageError):
                    figures.load_figures(self.directory)
                with self.assertRaises(figures.FigureStorageError):
                    figures.save_figures(self.directory, [])
                self.assertEqual(self.primary.read_bytes(), damaged)
                self.assertEqual(self.backup.read_bytes(), b'also broken')

    def test_legitimate_empty_library_does_not_resurrect_deleted_figures_from_backup(self):
        figures.save_figures(self.directory, [])
        self.assertEqual(json.loads(self.backup.read_text('utf-8')), self.original)
        self.assertEqual(figures.load_figures(self.directory), [])


if __name__ == '__main__':
    unittest.main()
