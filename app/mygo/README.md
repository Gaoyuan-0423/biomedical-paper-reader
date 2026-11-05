# BioReader MyGo desktop

The desktop shell uses **egoist/mygo v0.7.4**, pinned in `go.mod`, with Go
1.27.3. It runs the existing reading backend as a bundled PyInstaller sidecar;
end users do not install Python. macOS uses WKWebView and Windows uses WebView2.

From the repository root, `./app/build-macos.sh` builds an app and a real DMG.
On Windows x64, `./app/build-windows.ps1` builds a portable application folder
and an NSIS setup executable. Both scripts read the version from `SKILL.md`.
Build dependencies are Go 1.27.3+, native 64-bit Python 3.10+, and network access.
The scripts install Python dependencies into `app/.desktop-venv`.

For development with dependencies already installed:

```sh
python scripts/build_mygo.py --version v0.10.0
```

Generated configuration, staging resources, and outputs are ignored by Git.
`mygo.json` is generated for the requested version. MyGo packages the entire
`BioReaderService` directory alongside the shell. Keep this directory with the
portable Windows executable. The default user library stays in
`~/Library/Application Support/BioReader` on macOS and
`%LOCALAPPDATA%\BioReader` on Windows; `BIOREADER_DATA_DIR` still overrides it.

The shell sends a random bootstrap token to the child through a private stdin
pipe. The service binds a random `127.0.0.1` socket and reports readiness on
stdout. The initial window bootstrap establishes an HttpOnly SameSite session
cookie and redirects to a clean URL. Host, origin, and fetch-site checks protect
all frontend and API routes. Closing the owner's pipe stops the child, including
when the shell crashes. Native close waits for `BioDesktop.prepareClose()` so
pending notes and reading positions can save first.

For an actual packaged runtime diagnostic, invoke the built shell:

```sh
BioReader --self-test /absolute/path/report.json --gui-test
```

This uses temporary storage, starts the real bundled backend, exercises session
protection and PDF/note persistence, opens a hidden native MyGo WebView, renders
a synthetic PDF using PDF.js, and verifies child shutdown. It records the app
version, source commit, dirty-build flag, and exact checks. It does not call an
AI provider. The native save dialog, external browser, and clipboard still need
interactive acceptance checks.

`scripts/package_mygo.py --version v0.10.0 --commit <full-HEAD-SHA>` validates
source bytes, the actual extracted portable ZIP or mounted DMG, and the native
diagnostic before writing release assets and SHA-256 metadata to
`app/dist/mygo-release`. On disposable Windows CI, add `--test-installer` to also
test a silent install, the installed executable, and the uninstaller. Final
release packaging requires a clean committed build.
