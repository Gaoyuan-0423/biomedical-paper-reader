#!/usr/bin/env bash
# Build the MyGo app and DMG on macOS, using Python 3.10+ and Go 1.27.3+.
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
.desktop-venv/bin/python -m pip install -r backend/requirements.txt 'pyinstaller>=6,<7'
BIOREADER_VERSION="${1:-$(.desktop-venv/bin/python -c 'from pathlib import Path; import re; print(re.search(r"版本 (v\d+\.\d+\.\d+)", Path("../SKILL.md").read_text("utf-8"))[1])')}"
.desktop-venv/bin/python ../scripts/build_mygo.py --version "$BIOREADER_VERSION"
