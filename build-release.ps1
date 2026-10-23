# Build the release package: exe -> launcher -> spec-compliant zip
#
# Usage:  powershell -ExecutionPolicy Bypass -File build-release.ps1
#
# NOTE: this file is intentionally pure ASCII.
# Windows PowerShell 5.1 reads .ps1 files as ANSI (GBK on Chinese Windows)
# unless they start with a UTF-8 BOM. Non-ASCII text here becomes mojibake
# and breaks the parser. Keep it ASCII.
#
# Why a script instead of a bare pyinstaller call:
# pyinstaller --clean wipes the whole dist directory, so anything placed
# there manually (like start.bat) is lost on every rebuild.

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    throw "Virtualenv not found: $py`nRun first: uv venv .venv --python 3.11; uv pip install --python .venv\Scripts\python.exe -r requirements.txt pyinstaller"
}

Write-Host '[1/5] Stopping running instances' -ForegroundColor Cyan
Get-Process | Where-Object {
    $_.ProcessName -like '*PointTranslate*' -or $_.ProcessName -like '*zhiyi*'
} | ForEach-Object {
    Write-Host "      stop PID $($_.Id)"
    Stop-Process -Id $_.Id -Force
}
Start-Sleep -Milliseconds 1500

Write-Host '[2/5] Building exe from spec' -ForegroundColor Cyan
$pi = Join-Path $PSScriptRoot '.venv\Scripts\pyinstaller.exe'
# PyInstaller writes its INFO log to stderr. With $ErrorActionPreference='Stop'
# PowerShell turns any stderr output from a native command into a terminating
# NativeCommandError, so relax it around this call and check $LASTEXITCODE instead.
$ErrorActionPreference = 'Continue'
& $pi point_translate.spec --noconfirm --clean *>&1 | Out-Null
$piExit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($piExit -ne 0) {
    throw "PyInstaller failed with exit code $piExit"
}
if (-not (Test-Path 'dist\PointTranslate\_internal')) {
    throw 'Build failed: dist\PointTranslate\_internal is missing'
}

Write-Host '[3/5] Adding launcher script' -ForegroundColor Cyan
# Normalise to CRLF. The repo stores it as LF (see .gitattributes); cmd.exe
# tolerates LF-only batch files, but CRLF is the safe bet for something
# shipped to end users on Windows.
# NOTE: [System.IO.File] resolves relative paths against the *process* working
# directory, which is not the same as PowerShell's location -- always pass
# absolute paths to .NET APIs here.
$batSrc = Join-Path $PSScriptRoot 'start.bat'
$batDst = Join-Path $PSScriptRoot 'dist\PointTranslate\start.bat'
$bat = [System.IO.File]::ReadAllText($batSrc)
$bat = $bat -replace "`r`n", "`n" -replace "`n", "`r`n"
[System.IO.File]::WriteAllText($batDst, $bat, (New-Object System.Text.ASCIIEncoding))
Write-Host '      start.bat written as CRLF'

Write-Host '[4/5] Removing local runtime data' -ForegroundColor Cyan
foreach ($f in @('config.json', 'cache.sqlite3', 'cache.sqlite3-journal', 'selftest.txt')) {
    $p = Join-Path 'dist\PointTranslate' $f
    if (Test-Path $p) { Remove-Item $p -Force; Write-Host "      removed $f" }
}

Write-Host '[5/5] Creating spec-compliant zip' -ForegroundColor Cyan
$ErrorActionPreference = 'Continue'
& $py _make_release_zip.py
$zipExit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($zipExit -ne 0) {
    throw "zip builder failed with exit code $zipExit"
}

Write-Host ''
Write-Host 'Done. Artifacts:' -ForegroundColor Green
Get-ChildItem 'dist\*.zip' | ForEach-Object {
    $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
    Write-Host ("  {0}  ({1:N1} MB)" -f $_.Name, ($_.Length / 1MB))
    Write-Host ("  SHA256: {0}" -f $hash)
}
