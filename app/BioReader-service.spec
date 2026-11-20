# The Python runtime and parsing libraries ship beside the MyGo executable.
from PyInstaller.utils.hooks import collect_all, collect_submodules

pdf_data, pdf_binaries, pdf_imports = collect_all('pypdfium2')
a = Analysis(['service.py'], pathex=['backend'], binaries=pdf_binaries,
             datas=[('frontend', 'frontend')] + pdf_data,
             hiddenimports=collect_submodules('uvicorn') + pdf_imports,
             # python-pptx resolves presenter-note XML through oxml/../templates.
             # Keep the actual package directory available to the filesystem lookup.
             module_collection_mode={'pptx.oxml': 'py'},
             hookspath=[], runtime_hooks=[], excludes=['webview'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='BioReaderService',
          debug=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='BioReaderService')
