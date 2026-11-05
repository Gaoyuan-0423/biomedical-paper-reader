param([string]$Python = 'python', [switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'Build this application on Windows x64.' }
Push-Location $PSScriptRoot
try {
    & $Python -c "import sys,struct; assert sys.version_info >= (3,10) and struct.calcsize('P') == 8, '64-bit Python 3.10+ is required'"
    if ($LASTEXITCODE -ne 0) { throw 'Unsupported Python.' }
    if (-not $SkipInstall) {
        & $Python -m venv .desktop-venv
        if ($LASTEXITCODE -ne 0) { throw 'Unable to create build environment.' }
        $Python = Join-Path $PSScriptRoot '.desktop-venv/Scripts/python.exe'
        & $Python -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed.' }
        & $Python -m pip install -r backend/requirements.txt 'pyinstaller>=6,<7'
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    }
    $match = [regex]::Match((Get-Content ../SKILL.md -Raw), '版本 (v\d+\.\d+\.\d+)')
    if (-not $match.Success) { throw 'Missing release version.' }
    & $Python ../scripts/build_mygo.py --version $match.Groups[1].Value
    if ($LASTEXITCODE -ne 0) { throw 'Windows MyGo build failed.' }
    Write-Host "Built: $PSScriptRoot/mygo/build/windows-amd64"
    Write-Host 'Use the setup executable, or keep BioReader.exe and the complete BioReaderService folder together.'
} finally { Pop-Location }
