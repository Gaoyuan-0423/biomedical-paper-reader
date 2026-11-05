"""Exercise the draft uploader's release guards without network or publication."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/desktop-preview.yml'
# Run the exact embedded uploader, not a second implementation of its checks.
UPLOADER = WORKFLOW.read_text('utf-8').split("python3 - <<'PY'\n")[-1].split('\n          PY', 1)[0]
UPLOADER = '\n'.join(line[10:] for line in UPLOADER.splitlines())


class PreviewUploadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.commit = 'a' * 40
        self.version = 'v0.11.0'
        self.release = {'id': 42, 'draft': True, 'tag_name': self.version, 'assets': []}
        self.tag_commit = self.commit
        self.frontend_count = 2
        self.uploads = []
        self.fail_upload = False
        self.prepare()

    def prepare(self, windows=False):
        for path in self.folder.iterdir():
            path.unlink()
        self.windows = windows
        platform, architecture = ('windows', 'amd64') if windows else ('darwin', 'arm64')
        self.report = {'status': 'passed', 'source_commit': self.commit, 'build_dirty': False,
                       'backend_frozen': True, 'gui_tested': True, 'locale_tested': True, 'brand_icon_tested': True, 'app_version': self.version[1:],
                       'desktop': 'egoist/mygo', 'mygo_version': 'v0.7.4',
                       'platform': platform, 'architecture': architecture}
        names = ([f'BioReader-{self.version}-windows-x64.zip', f'BioReader-{self.version}-windows-x64-setup.exe']
                 if windows else [f'BioReader-{self.version}-macos-arm64.dmg'])
        artifacts = []
        for name in names:
            data = ('Synthetic test package: ' + name).encode()
            (self.folder / name).write_bytes(data)
            artifacts.append({'name': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        self.info = {'version': self.version, 'commit': self.commit, 'desktop': 'egoist/mygo',
                     'mygo_version': 'v0.7.4', 'frontend_files': 2, 'platform': f'{platform}-{architecture}',
                     'self_test': self.report, 'artifacts': artifacts,
                     'installer_tested': windows, 'uninstaller_tested': windows}
        (self.folder / 'SHA256SUMS.txt').write_text(''.join(f"{item['sha256']}  {item['name']}\n" for item in artifacts), encoding='utf-8')
        self.save()

    def save(self):
        (self.folder / 'build-info.json').write_text(json.dumps(self.info), encoding='utf-8')
        (self.folder / 'self-test.json').write_text(json.dumps(self.report), encoding='utf-8')
        if self.windows:
            (self.folder / 'installer-self-test.json').write_text(json.dumps(self.report), encoding='utf-8')

    def server_asset(self, item):
        return {'id': 100 + len(self.release['assets']), 'name': item['name'], 'size': item['bytes'],
                'state': 'uploaded', 'digest': 'sha256:' + item['sha256']}

    def api(self, command, **kwargs):
        self.assertEqual(command[:2], ['gh', 'api'])
        route = command[2].removeprefix('repos/Gaoyuan-0423/biomedical-paper-reader/')
        if route == 'releases/42':
            result = self.release
        elif route == f'commits/{self.version}':
            result = {'sha': self.tag_commit}
        elif route == f'git/commits/{self.commit}':
            result = {'tree': {'sha': 'b' * 40}}
        elif route == f'git/trees/{"b" * 40}?recursive=1':
            result = {'truncated': False, 'tree': [{'type': 'blob', 'path': f'app/frontend/{i}.js'} for i in range(self.frontend_count)]}
        elif route == f'contents/app/mygo/go.mod?ref={self.commit}':
            result = {'encoding': 'base64', 'content': base64.b64encode(b'require github.com/egoist/mygo v0.7.4').decode()}
        else:
            self.fail(f'Unexpected API route: {route}')
        return json.dumps(result).encode()

    def upload(self, command, **kwargs):
        self.assertEqual(command[:4], ['gh', 'release', 'upload', self.version])
        self.assertEqual(command[5:], ['--repo', 'Gaoyuan-0423/biomedical-paper-reader'])
        self.assertNotIn('--clobber', command)
        if self.fail_upload:
            raise subprocess.CalledProcessError(1, command)
        item = next(item for item in self.info['artifacts'] if item['name'] == Path(command[4]).name)
        self.uploads.append(item['name'])
        self.release['assets'].append(self.server_asset(item))

    def run_uploader(self, **extra_env):
        env = {'PREVIEW_ASSETS': str(self.folder), 'RELEASE_VERSION': self.version, 'SOURCE_COMMIT': self.commit,
               'RELEASE_ID': '42', 'PREVIEW_PLATFORM': 'windows-x64' if self.windows else 'macos-arm64', **extra_env}
        with patch.dict(os.environ, env), patch('subprocess.check_output', side_effect=self.api), patch('subprocess.run', side_effect=self.upload), patch('builtins.print'):
            exec(compile(UPLOADER, str(WORKFLOW), 'exec'), {})

    def test_native_mac_and_windows_metadata_upload_without_publishing(self):
        for windows in (False, True):
            with self.subTest(windows=windows):
                self.prepare(windows)
                self.release['assets'] = []
                self.uploads.clear()
                self.run_uploader()
                result = json.loads((self.folder / 'draft-upload-verification.json').read_text('utf-8'))
                self.assertTrue(self.release['draft'])
                self.assertTrue(result['draft'])
                self.assertEqual(result['commit'], self.commit)
                self.assertEqual(result['frontend_files'], 2)
                self.assertEqual(len(self.uploads), 2 if windows else 1)

    def test_existing_identical_assets_are_not_replaced(self):
        self.release['assets'] = [self.server_asset(item) for item in self.info['artifacts']]
        self.run_uploader()
        self.assertEqual(self.uploads, [])

    def test_wrong_release_state_tag_or_commit_prevents_upload(self):
        for field, value in [('draft', False), ('tag_name', 'v9.9.9'), ('id', 99)]:
            with self.subTest(field=field):
                old = self.release[field]
                self.release[field] = value
                with self.assertRaises(RuntimeError):
                    self.run_uploader()
                self.release[field] = old
        self.tag_commit = 'c' * 40
        with self.assertRaises(RuntimeError):
            self.run_uploader()
        self.assertEqual(self.uploads, [])

    def test_malformed_dispatch_input_is_rejected_before_mutation(self):
        for env in [{'RELEASE_VERSION': '--clobber'}, {'SOURCE_COMMIT': 'main'}, {'RELEASE_ID': '../latest'}]:
            with self.subTest(env=env), self.assertRaises(RuntimeError):
                self.run_uploader(**env)
        self.assertEqual(self.uploads, [])

    def test_dirty_wrong_source_and_failed_native_diagnostics_prevent_upload(self):
        for field, value in [('build_dirty', True), ('source_commit', 'c' * 40), ('gui_tested', False), ('backend_frozen', False), ('locale_tested', False), ('brand_icon_tested', False)]:
            with self.subTest(field=field):
                old = self.report[field]
                self.report[field] = value
                self.save()
                with self.assertRaises(RuntimeError):
                    self.run_uploader()
                self.report[field] = old
        self.assertEqual(self.uploads, [])

    def test_source_file_count_and_package_checksum_are_verified(self):
        self.frontend_count = 3
        with self.assertRaises(RuntimeError):
            self.run_uploader()
        self.frontend_count = 2
        (self.folder / self.info['artifacts'][0]['name']).write_bytes(b'damaged package')
        with self.assertRaises(RuntimeError):
            self.run_uploader()
        self.assertEqual(self.uploads, [])

    def test_windows_requires_actual_installer_and_uninstaller_checks(self):
        self.prepare(True)
        for field in ('installer_tested', 'uninstaller_tested'):
            with self.subTest(field=field):
                self.info[field] = False
                self.save()
                with self.assertRaises(RuntimeError):
                    self.run_uploader()
                self.info[field] = True
        self.assertEqual(self.uploads, [])

    def test_existing_different_asset_is_never_overwritten(self):
        asset = self.server_asset(self.info['artifacts'][0])
        asset['digest'] = 'sha256:' + '0' * 64
        self.release['assets'] = [asset]
        with self.assertRaises(RuntimeError):
            self.run_uploader()
        self.assertEqual(self.uploads, [])

    def test_upload_failure_keeps_independent_verification(self):
        self.fail_upload = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_uploader()
        self.assertTrue((self.folder / 'asset-verification.json').is_file())
        self.assertTrue((self.folder / 'self-test.json').is_file())
        self.assertTrue(self.release['draft'])


if __name__ == '__main__':
    unittest.main()
