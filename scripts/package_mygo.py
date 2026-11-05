"""Validate actual MyGo release artifacts against their committed source."""
import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frontend_matches(root, bundled):
    count = 0
    for source in (root / 'app/frontend').rglob('*'):
        if not source.is_file():
            continue
        relative = source.relative_to(root).as_posix()
        committed = subprocess.check_output(['git', 'show', f'HEAD:{relative}'], cwd=root)
        if source.read_bytes() != committed:
            raise RuntimeError(f'Checkout differs from Git HEAD: {relative}')
        target = bundled / source.relative_to(root / 'app/frontend')
        if target.read_bytes() != committed:
            raise RuntimeError(f'Bundled frontend differs from Git HEAD: {relative}')
        count += 1
    return count


def self_test(executable, report, commit, version):
    report.unlink(missing_ok=True)
    result = subprocess.run([str(executable), '--self-test', str(report), '--gui-test'], timeout=120)
    diagnostic = json.loads(report.read_text(encoding='utf-8')) if report.is_file() else {}
    if result.returncode or diagnostic.get('status') != 'passed' or not diagnostic.get('backend_frozen') or not diagnostic.get('gui_tested'):
        print(json.dumps(diagnostic, indent=2, ensure_ascii=False), flush=True)
        raise RuntimeError('Packaged MyGo/native WebView diagnostic failed.')
    if diagnostic.get('source_commit') != commit or diagnostic.get('build_dirty') is not False:
        raise RuntimeError('Packaged executable was not built from the requested clean commit.')
    if diagnostic.get('app_version') != version.removeprefix('v'):
        raise RuntimeError('Packaged executable has a different application version.')
    return diagnostic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--test-installer', action='store_true', help='Install into a temporary folder on a disposable Windows runner')
    args = parser.parse_args()
    if not re.fullmatch(r'v\d+\.\d+\.\d+', args.version) or not re.fullmatch(r'[a-f0-9]{40}', args.commit):
        parser.error('Invalid release version or source commit.')
    root = Path(__file__).resolve().parents[1]
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip() != args.commit:
        raise RuntimeError('Requested source commit differs from Git HEAD.')
    goos = {'darwin': 'darwin', 'win32': 'windows'}.get(sys.platform)
    arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'amd64': 'amd64', 'x86_64': 'amd64'}.get(platform.machine().lower())
    if not goos or not arch:
        parser.error('Validate on the native target platform.')
    target = root / 'app/mygo/build' / f'{goos}-{arch}'
    output = root / 'app/dist/mygo-release'
    output.mkdir(parents=True, exist_ok=True)
    release_files = []
    with tempfile.TemporaryDirectory(prefix='bioreader-mygo-package-') as temporary:
        temporary = Path(temporary)
        if goos == 'windows':
            portable = output / f'BioReader-{args.version}-windows-x64.zip'
            installer_name = f'bioreader-setup-{args.version[1:]}-{arch}.exe'
            with zipfile.ZipFile(portable, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for source in sorted(target.rglob('*')):
                    if source.is_file() and source.name != installer_name:
                        archive.write(source, Path('BioReader') / source.relative_to(target))
            with zipfile.ZipFile(portable) as archive:
                archive.extractall(temporary)
            installed = temporary / 'BioReader'
            count = frontend_matches(root, installed / 'BioReaderService/_internal/frontend')
            diagnostic = self_test(installed / 'BioReader.exe', output / 'self-test.json', args.commit, args.version)
            installer = output / f'BioReader-{args.version}-windows-x64-setup.exe'
            shutil.copy2(target / installer_name, installer)
            release_files += [portable, installer]
            if args.test_installer:
                installed = temporary / 'installed'
                try:
                    subprocess.run([str(installer), '/S', f'/D={installed}'], check=True, timeout=120)
                    frontend_matches(root, installed / 'BioReaderService/_internal/frontend')
                    self_test(installed / 'BioReader.exe', output / 'installer-self-test.json', args.commit, args.version)
                finally:
                    uninstallers = list(installed.glob('*ninstall*.exe'))
                    if uninstallers:
                        subprocess.run([str(uninstallers[0]), '/S', f'_?={installed}'], check=True, timeout=120)
        else:
            dmg = output / f'BioReader-{args.version}-macos-{arch}.dmg'
            shutil.copy2(target / f'bioreader-{args.version[1:]}-{arch}.dmg', dmg)
            subprocess.run(['hdiutil', 'verify', str(dmg)], check=True, stdout=subprocess.DEVNULL)
            mount = temporary / 'mounted'
            mount.mkdir()
            subprocess.run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount), str(dmg)], check=True, stdout=subprocess.DEVNULL)
            try:
                application = mount / 'BioReader.app'
                subprocess.run(['codesign', '--verify', '--deep', '--strict', str(application)], check=True)
                count = frontend_matches(root, application / 'Contents/Resources/BioReaderService/_internal/frontend')
                diagnostic = self_test(application / 'Contents/MacOS/BioReader', output / 'self-test.json', args.commit, args.version)
            finally:
                subprocess.run(['hdiutil', 'detach', str(mount)], check=True, stdout=subprocess.DEVNULL)
            release_files.append(dmg)
    artifacts = [{'name': path.name, 'bytes': path.stat().st_size, 'sha256': digest(path)} for path in release_files]
    (output / 'SHA256SUMS.txt').write_text(''.join(f"{item['sha256']}  {item['name']}\n" for item in artifacts), encoding='utf-8')
    (output / 'build-info.json').write_text(json.dumps({'version': args.version, 'commit': args.commit,
        'desktop': 'egoist/mygo', 'mygo_version': 'v0.7.4', 'platform': f'{goos}-{arch}',
        'frontend_files': count, 'artifacts': artifacts, 'self_test': diagnostic,
        'installer_tested': bool(args.test_installer and goos == 'windows')}, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(artifacts, indent=2))


if __name__ == '__main__':
    main()
