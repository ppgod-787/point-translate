@echo off
rem PointTranslate launcher
rem
rem Kept pure ASCII on purpose: cmd.exe reads .bat files using the system
rem OEM codepage (GBK on Chinese Windows), so non-ASCII content here turns
rem into garbage commands and fails.
rem The exe name contains CJK characters, which are unsafe to type literally
rem in a batch file, so locate it by wildcard instead.
cd /d "%~dp0"

set "EXE="
for %%F in (*PointTranslate.exe) do set "EXE=%%~fF"

if not defined EXE (
    echo Cannot find *PointTranslate.exe in this folder.
    echo Make sure you extracted the whole folder, not just the exe.
    pause
    exit /b 1
)

start "" "%EXE%"
