@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ============================================
echo   eMAG Crawler V2.0.2 - Installation
echo ============================================
echo.

:: Step 1: Find Python
echo [1/7] Finding Python...
set "PYTHON_CMD="

py -3.12 --version >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    set "PYTHON_CMD=py -3.12"
    goto :python_found
)

py -3 --version >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    set "PYTHON_CMD=py -3"
    goto :python_found
)

python --version >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    set "PYTHON_CMD=python"
    goto :python_found
)

echo [ERROR] Python was not found.
echo Please install Python 3.11 or 3.12 from https://www.python.org/downloads/
pause
exit /b 1

:python_found
%PYTHON_CMD% --version
echo   Python OK
echo.

:: Step 2: Create venv
echo [2/7] Creating virtual environment .venv...
if exist ".venv\Scripts\python.exe" (
    echo   .venv already exists, skipping
) else (
    %PYTHON_CMD% -m venv .venv
    if !ERRORLEVEL! NEQ 0 (
        echo [ERROR] Failed to create .venv
        pause
        exit /b 1
    )
    echo   .venv created
)
echo.

:: Step 3: Upgrade pip
echo [3/7] Upgrading pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
if !ERRORLEVEL! NEQ 0 (
    echo [WARNING] pip upgrade failed, continuing...
)
echo.

:: Step 4: Install dependencies
echo [4/7] Installing project dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if !ERRORLEVEL! NEQ 0 (
    echo [ERROR] Failed to install dependencies. Check your network connection.
    pause
    exit /b 1
)
echo   Dependencies installed
echo.

:: Step 5: Remove patchright if installed (not needed for HTTP-only mode)
echo [5/7] Removing patchright if present...
".venv\Scripts\python.exe" -m pip uninstall -y patchright >nul 2>&1
echo   Done
echo.

:: Step 6: Verify installation
echo [6/7] Verifying installation...
".venv\Scripts\python.exe" -m pip check >nul 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo [WARNING] pip check reported issues (may be non-critical)
) else (
    echo   pip check: OK
)

".venv\Scripts\python.exe" -c "import scrapling; print('  Scrapling:', scrapling.__version__)" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo [ERROR] Scrapling import failed
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "from scrapling.fetchers import Fetcher, FetcherSession; print('  Fetcher: OK')" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo [ERROR] Scrapling Fetcher import failed
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "import bs4, lxml, openpyxl, httpx, pytest; print('  Other deps: OK')" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo [ERROR] Dependency check failed
    pause
    exit /b 1
)
echo.

:: Step 7: Verify project modules
echo [7/7] Verifying project modules...
".venv\Scripts\python.exe" -c "import utils, models, parser, exporters, image_downloader, crawler; print('  Project modules: OK')" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo [ERROR] Project module import failed
    pause
    exit /b 1
)
echo.

echo ============================================
echo   Installation completed!
echo ============================================
echo.
echo Next steps:
echo   Test: .venv\Scripts\python.exe main.py --pages 1 --no-images
echo   Menu: 02_run.bat
echo.
pause
exit /b 0
