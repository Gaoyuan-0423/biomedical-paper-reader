#!/usr/bin/env bash
# Run on macOS, using Python 3.10+ and an isolated environment.
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'Build this app on macOS.' >&2
  exit 1
fi
PYTHON="${BIOREADER_PYTHON:-python3}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10 or newer is required; set BIOREADER_PYTHON to its path"'
"$PYTHON" -m venv .desktop-venv
.desktop-venv/bin/python -m pip install --upgrade pip
.desktop-venv/bin/python -m pip install -r backend/requirements.txt 'pywebview>=6.2.1,<7' 'pyinstaller>=6,<7'
.desktop-venv/bin/python -m PyInstaller --noconfirm --clean --windowed \
  --name BioReader --icon BioReader.icns --osx-bundle-identifier org.biomedicalpaperreader.bioreader \
  --paths backend --add-data 'frontend:frontend' \
  --collect-submodules uvicorn --collect-all pypdfium2 \
  desktop.py
echo "Built: $(pwd)/dist/BioReader.app"
