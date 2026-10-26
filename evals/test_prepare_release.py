"""Release metadata regressions: never publish a mismatched or undocumented tag."""
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/prepare_release.py'
SPEC = importlib.util.spec_from_file_location('prepare_release', SCRIPT)
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


class PrepareReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write('CHANGELOG.md', '# Changelog\n\n## Unreleased\n\nFuture work.\n\n## v0.3.3 — 2026-10-17\n\nChanges.\n\n## v0.2.0\n\nOld changes.\n')
        self.write('README.md', '# Project\n\n版本：v0.3.3。中文说明。\n\nVersion: v0.3.3. English description.\n')
        self.write('SKILL.md', '# Skill\n\n技能说明。版本 v0.3.3（更新记录）。\n')
        self.notes = '# v0.3.3\n\n## 中文\n\n变化与验证边界。\n\n## English\n\nChanges and validation limits.\n'
        self.write('releases/v0.3.3.md', self.notes)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def test_current_version_with_unreleased_section_preserves_notes(self):
        self.assertEqual(RELEASE.prepare_release(self.root, 'v0.3.3'), self.notes)

    def test_untrusted_or_nonstable_version_is_rejected_before_file_access(self):
        missing_root = self.root / 'absent'
        for version in ('../v0.3.3', 'v0.3.3; echo bad', 'v0.3.3\n', 'v0.3.3-rc.1',
                        'v0.3.3+build', '0.3.3', 'v00.3.3', 'v0.03.3', 'v0.3.03', 'v０.3.3'):
            with self.subTest(version=version):
                with self.assertRaises(RELEASE.ReleaseValidationError):
                    RELEASE.prepare_release(missing_root, version)

    def test_old_changelog_entry_does_not_authorize_old_version(self):
        self.write('CHANGELOG.md', '# Changelog\n\n## v0.4.0\n\nNew.\n\n## v0.3.3\n\nOld.\n')
        with self.assertRaisesRegex(RELEASE.ReleaseValidationError, 'CHANGELOG'):
            RELEASE.prepare_release(self.root, 'v0.3.3')

    def test_each_readme_language_must_match(self):
        good = (self.root / 'README.md').read_text(encoding='utf-8')
        for declaration in ('版本：v0.3.3', 'Version: v0.3.3'):
            with self.subTest(declaration=declaration):
                self.write('README.md', good.replace(declaration, declaration.replace('v0.3.3', 'v0.2.0')))
                with self.assertRaisesRegex(RELEASE.ReleaseValidationError, 'README'):
                    RELEASE.prepare_release(self.root, 'v0.3.3')

    def test_missing_readme_language_is_rejected(self):
        self.write('README.md', '版本：v0.3.3。中文说明。\n')
        with self.assertRaisesRegex(RELEASE.ReleaseValidationError, 'English'):
            RELEASE.prepare_release(self.root, 'v0.3.3')

    def test_prerelease_readme_declaration_cannot_pass_as_stable(self):
        self.write('README.md', '版本：v0.3.3。\nVersion: v0.3.3-rc.1. English.\n')
        with self.assertRaisesRegex(RELEASE.ReleaseValidationError, 'English'):
            RELEASE.prepare_release(self.root, 'v0.3.3')

    def test_skill_version_must_match(self):
        self.write('SKILL.md', '# Skill\n\n版本 v0.2.0（更新记录）。\n')
        with self.assertRaisesRegex(RELEASE.ReleaseValidationError, 'SKILL'):
            RELEASE.prepare_release(self.root, 'v0.3.3')

    def test_missing_release_notes_are_rejected(self):
        (self.root / 'releases/v0.3.3.md').unlink()
        with self.assertRaises(FileNotFoundError):
            RELEASE.prepare_release(self.root, 'v0.3.3')

    def test_empty_wrong_version_or_incomplete_notes_are_rejected(self):
        for notes in ('', self.notes.replace('# v0.3.3', '# v0.2.0'),
                      '# v0.3.3\n\n## 中文\n\n仅中文。\n',
                      '# v0.3.3\n\n## 中文\n\n## English\n\nEnglish only.\n'):
            with self.subTest(notes=notes):
                self.write('releases/v0.3.3.md', notes)
                with self.assertRaises(RELEASE.ReleaseValidationError):
                    RELEASE.prepare_release(self.root, 'v0.3.3')

    def test_cli_writes_only_after_successful_validation(self):
        script = self.root / 'scripts/prepare_release.py'
        script.parent.mkdir()
        shutil.copyfile(SCRIPT, script)
        output = self.root / 'prepared.md'
        command = [sys.executable, str(script), 'v0.3.3', '--output', str(output)]
        completed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(output.read_text(encoding='utf-8'), self.notes)
        output.write_text('Keep an existing file on validation failure.\n', encoding='utf-8')
        self.write('SKILL.md', '# Skill with no version\n')
        completed = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('Release validation failed:', completed.stderr)
        self.assertEqual(output.read_text(encoding='utf-8'), 'Keep an existing file on validation failure.\n')


if __name__ == '__main__':
    unittest.main()
