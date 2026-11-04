"""Platform paths and explicit bundled-runtime checks use isolated data only."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))
from desktop_paths import desktop_data_dir


class DesktopPathsTests(unittest.TestCase):
    def test_macos_keeps_existing_data_directory(self):
        self.assertEqual(desktop_data_dir('darwin', {}, '/example-user'), Path('/example-user/Library/Application Support/BioReader'))

    def test_windows_uses_local_app_data_and_has_home_fallback(self):
        self.assertEqual(desktop_data_dir('win32', {'LOCALAPPDATA': '/local-profile'}, '/other'), Path('/local-profile/BioReader'))
        self.assertEqual(desktop_data_dir('win32', {}, '/example-user'), Path('/example-user/AppData/Local/BioReader'))

    def test_explicit_data_directory_wins_on_all_platforms(self):
        for platform in ('darwin', 'win32', 'linux'):
            self.assertEqual(desktop_data_dir(platform, {'BIOREADER_DATA_DIR': '/chosen/data', 'LOCALAPPDATA': '/ignored'}, '/home'), Path('/chosen/data'))

    def test_diagnostic_overrides_data_directory_without_touching_existing_data(self):
        with tempfile.TemporaryDirectory(prefix='bioreader-diagnostic-test-') as tmp:
            directory = Path(tmp)
            untouched = directory / 'existing'; untouched.mkdir()
            (untouched / 'marker.txt').write_text('Do not change.', encoding='utf-8')
            report = directory / 'report.json'
            result = subprocess.run([sys.executable, str(APP_DIR / 'desktop.py'), '--self-test', str(report)],
                                    env={**os.environ, 'BIOREADER_DATA_DIR': str(untouched)}, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr + (report.read_text(encoding='utf-8') if report.exists() else ''))
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(data['status'], 'passed')
            self.assertFalse(data['gui_tested'])
            self.assertFalse(data['frozen'])
            self.assertEqual([p.name for p in untouched.iterdir()], ['marker.txt'])


if __name__ == '__main__':
    unittest.main()
