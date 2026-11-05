"""Build the MyGo desktop app and its bundled Python sidecar on the target OS."""
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


def desktop_config(app, version):
    config = {'name': 'BioReader', 'slug': 'bioreader',
        'identifier': 'org.biomedicalpaperreader.bioreader', 'version': version[1:],
        'publisher': 'BioReader', 'out': 'build',
        'macos': {'dmgTitle': 'BioReader ' + version},
        'windows': {'displayName': 'BioReader', 'installDirName': 'BioReader'}}
    if (app / 'BioReader.png').is_file():
        # MyGo derives the Windows executable and NSIS installer ICO from PNG.
        config['icon'] = '../BioReader.png'
    if (app / 'BioReader.icns').is_file():
        config['macos']['icon'] = '../BioReader.icns'
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--python', default=sys.executable, help='Python with backend requirements and PyInstaller installed')
    parser.add_argument('--skip-backend', action='store_true', help='Reuse an already-built sidecar for local shell development')
    parser.add_argument('--skip-installer', action='store_true', help='Only build the portable Windows folder')
    args = parser.parse_args()
    if not re.fullmatch(r'v\d+\.\d+\.\d+', args.version):
        parser.error('Version must be vMAJOR.MINOR.PATCH.')
    goos = {'darwin': 'darwin', 'win32': 'windows'}.get(sys.platform)
    arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'amd64': 'amd64', 'x86_64': 'amd64'}.get(platform.machine().lower())
    if not goos or not arch:
        parser.error('Build on macOS or Windows with a native 64-bit Python.')
    root = Path(__file__).resolve().parents[1]
    app = root / 'app'
    project = app / 'mygo'
    env = os.environ.copy()
    # macOS system proxies are not automatically read by Go. Only pass them to
    # the build subprocess, preserving explicitly configured environment values.
    for protocol, proxy in urllib.request.getproxies().items():
        if protocol in ('http', 'https'):
            env.setdefault(protocol.upper() + '_PROXY', proxy)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    changes = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all', '--',
        'app/mygo', 'app/service.py', 'app/service_security.py', 'app/BioReader-service.spec',
        'app/backend', 'app/frontend', 'app/BioReader.png', 'app/BioReader.icns', 'scripts/build_mygo.py'], cwd=root, text=True)
    dirty = bool(changes) or args.skip_backend
    env['MYGO_LDFLAGS'] = f'-X main.buildCommit={commit} -X main.buildDirty={str(dirty).lower()}'
    if not args.skip_backend:
        subprocess.run([args.python, '-m', 'PyInstaller', '--noconfirm', '--clean', 'BioReader-service.spec'], cwd=app, env=env, check=True)
    backend = app / 'dist/BioReaderService'
    if not backend.is_dir():
        raise RuntimeError('Build the sidecar before using --skip-backend.')
    resources = project / 'resources'
    resources.mkdir(exist_ok=True)
    destination = resources / 'BioReaderService'
    if destination.exists():
        shutil.rmtree(destination)
    # MyGo omits hidden resource directories such as Pillow's .dylibs. The
    # PyInstaller root symlinks must become real files so their libraries remain
    # loadable after MyGo copies/signs the sidecar into the application bundle.
    shutil.copytree(backend, destination, symlinks=False)
    notices = resources / 'THIRD-PARTY-LICENSES'
    if notices.exists():
        shutil.rmtree(notices)
    notices.mkdir()
    module_directories = subprocess.check_output(['go', 'list', '-deps', '-f',
        '{{if .Module}}{{.Module.Dir}}{{end}}', '.'], cwd=project, env=env, text=True)
    for directory in sorted(set(module_directories.splitlines())):
        if not directory or Path(directory).resolve() == project.resolve():
            continue
        module = Path(directory)
        for source in (*module.glob('LICENSE*'), *module.glob('NOTICE*'), *module.glob('COPYING*')):
            if source.is_file():
                shutil.copy2(source, notices / (module.name.replace('@', '-') + '-' + source.name))
    go_root = subprocess.check_output(['go', 'env', 'GOROOT'], cwd=project, env=env, text=True).strip()
    shutil.copy2(Path(go_root) / 'LICENSE', notices / 'Go-LICENSE')
    config = desktop_config(app, args.version)
    (project / 'mygo.json').write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    command = ['go', 'run', 'github.com/egoist/mygo/cmd/mygo@v0.7.4', 'build', '-platform', f'{goos}/{arch}']
    if args.skip_installer:
        command.append('-skip-installer')
    subprocess.run(command, cwd=project, env=env, check=True)
    print('Built:', project / 'build' / f'{goos}-{arch}')


if __name__ == '__main__':
    main()
