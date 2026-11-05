"""Protect release provenance when Windows checkout line endings change."""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('package_mygo', ROOT / 'scripts/package_mygo.py')
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)


class MyGoPackagingTests(unittest.TestCase):
    def test_frontend_must_match_committed_bytes_even_when_checkout_matches_bundle(self):
        with tempfile.TemporaryDirectory(prefix='bioreader-package-bytes-') as directory:
            root = Path(directory)
            source = root / 'app/frontend/example.js'
            source.parent.mkdir(parents=True)
            bundled = root / 'bundle'
            bundled.mkdir()
            original = b'const example = 1;\nconsole.log(example);\n'
            source.write_bytes(original)
            (bundled / source.name).write_bytes(original)
            for arguments in (
                ['init', '-q'], ['config', 'core.autocrlf', 'false'],
                ['config', 'core.hooksPath', str(root / 'no-hooks')], ['add', 'app/frontend'],
                ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic fixture'],
            ):
                subprocess.run(['git', *arguments], cwd=root, check=True, capture_output=True)
            self.assertEqual(packaging.frontend_matches(root, bundled), 1)
            crlf = original.replace(b'\n', b'\r\n')
            source.write_bytes(crlf)
            (bundled / source.name).write_bytes(crlf)
            with self.assertRaisesRegex(RuntimeError, 'Checkout differs from Git HEAD'):
                packaging.frontend_matches(root, bundled)
            source.write_bytes(original)
            with self.assertRaisesRegex(RuntimeError, 'Bundled frontend differs from Git HEAD'):
                packaging.frontend_matches(root, bundled)


if __name__ == '__main__':
    unittest.main()
