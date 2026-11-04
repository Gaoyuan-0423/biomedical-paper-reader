"""Package a Windows build and exercise the extracted executable before upload."""
import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def verify_frontend(root, bundle):
    """Require the checkout and bundle to preserve the committed frontend bytes."""
    count = 0
    for source in (root / 'app/frontend').rglob('*'):
        if source.is_file():
            relative = source.relative_to(root).as_posix()
            committed = subprocess.check_output(['git', 'show', f'HEAD:{relative}'], cwd=root)
            content = source.read_bytes()
            if content != committed:
                raise RuntimeError(f'Checkout frontend differs from Git HEAD: {relative}; check core.autocrlf.')
            target = bundle / '_internal/frontend' / source.relative_to(root / 'app/frontend')
            if target.read_bytes() != content:
                raise RuntimeError(f'Bundled frontend mismatch: {relative}')
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        parser.error('Package on Windows x64.')
    if not re.fullmatch(r'v\d+\.\d+\.\d+', args.version) or not re.fullmatch(r'[a-f0-9]{40}', args.commit):
        parser.error('Invalid version or source commit.')
    root = Path(__file__).resolve().parents[1]
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    if args.commit != head:
        raise RuntimeError('Requested source commit differs from Git HEAD.')
    bundle = root / 'app/dist/BioReader'
    exe = bundle / 'BioReader.exe'
    if not exe.is_file():
        raise RuntimeError('Build BioReader.exe first.')
    count = verify_frontend(root, bundle)
    output = root / 'app/dist/windows-release'
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'BioReader-{args.version}-windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
        for source in sorted(bundle.rglob('*')):
            if source.is_file():
                zipped.write(source, Path('BioReader') / source.relative_to(bundle))
    report = output / 'self-test.json'
    report.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix='bioreader-windows-package-') as tmp:
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(tmp)
        process = subprocess.run([str(Path(tmp) / 'BioReader/BioReader.exe'), '--self-test', str(report)], timeout=120)
        if process.returncode or not report.is_file():
            if report.is_file():
                print(report.read_text(encoding='utf-8'), flush=True)
            raise RuntimeError('Extracted executable self-test failed; see self-test.json if present.')
        result = json.loads(report.read_text(encoding='utf-8'))
        if result.get('status') != 'passed' or result.get('frozen') is not True:
            raise RuntimeError('Executable did not pass its bundled-runtime diagnostic.')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    (output / 'build-info.json').write_text(json.dumps({
        'version': args.version, 'commit': args.commit, 'platform': 'windows-x64',
        'frontend_files': count, 'zip_bytes': archive.stat().st_size, 'sha256': digest,
        'gui_tested': False, 'self_test': result,
    }, indent=2) + '\n', encoding='utf-8')
    print(f'Checked {archive.name}: {digest}')


if __name__ == '__main__':
    main()
