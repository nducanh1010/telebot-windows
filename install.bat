@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    python "%~dp0install.py" %*
) else (
    py -3 "%~dp0install.py" %*
)
pause
