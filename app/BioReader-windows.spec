# Build on Windows x64. The one-directory bundle keeps all runtime DLLs together.
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

if sys.platform != 'win32':
    raise SystemExit('Build BioReader-windows.spec on Windows.')

pdf_data, pdf_binaries, pdf_imports = collect_all('pypdfium2')
a = Analysis(
    ['desktop.py'], pathex=['backend'],
    binaries=pdf_binaries, datas=[('frontend', 'frontend')] + pdf_data,
    hiddenimports=collect_submodules('uvicorn') + pdf_imports + [
        'webview.platforms.winforms', 'webview.platforms.edgechromium', 'clr',
    ],
    hookspath=[], runtime_hooks=[], excludes=[], noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='BioReader',
          debug=False, strip=False, upx=False, console=False,
          disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='BioReader')
