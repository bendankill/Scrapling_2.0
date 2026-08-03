@echo off
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ============================================
echo   eMAG Crawler V2.0.1 - Installation
echo ============================================
echo.

:: Step 1: Find Python
echo [1/6] Finding Python...
set "PYTHON_CMD="

py -3.12 --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PYTHON_CMD=py -3.12"
    goto :python_found
)

py -3 --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PYTHON_CMD=py -3"
    goto :python_found
)

python --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
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

:: Check Python version >= 3.11
for /f "tokens=2 delims= " %%v in ('%PYTHON_CMD% --version') do set "PYVER=%%v"
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    set "PYMAJ=%%a"
    set "PYMIN=%%b"
)
if %PYMAJ% LSS 3 (
    echo [ERROR] Python 3.11+ required, found %PYVER%
    pause
    exit /b 1
)
if %PYMAJ% EQU 3 if %PYMIN% LSS 11 (
    echo [ERROR] Python 3.11+ required, found %PYVER%
    pause
    exit /b 1
)
echo.

:: Step 2: Create venv
echo [2/6] Creating virtual environment .venv...
if exist ".venv\Scripts\python.exe" (
    echo   .venv already exists, skipping
) else (
    %PYTHON_CMD% -m venv .venv
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to create .venv
        pause
        exit /b 1
    )
    echo   .venv created
)
echo.

:: Step 3: Upgrade pip
echo [3/6] Upgrading pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] pip upgrade failed, continuing...
)
echo.

:: Step 4: Install dependencies
echo [4/6] Installing project dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install dependencies. Check your network connection.
    pause
    exit /b 1
)
echo   Dependencies installed
echo.

:: Step 5: Verify installation
echo [5/6] Verifying installation...
".venv\Scripts\python.exe" -c "import scrapling; print('  Scrapling:', scrapling.__version__)" 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Scrapling import failed
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "import scrapling.fetchers; from scrapling.fetchers import Fetcher, FetcherSession; print('  Fetcher: OK')" 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Scrapling Fetcher import failed
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "import bs4; import lxml; import openpyxl; import httpx; print('  Other deps: OK')" 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Dependency check failed
    pause
    exit /b 1
)
echo.

:: Step 6: Verify project imports
echo [6/6] Verifying project modules...
".venv\Scripts\python.exe" -c "import utils; import models; import parser; import exporters; import image_downloader; import crawler; print('  Project modules: OK')" 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Project module import failed
    pause
    exit /b 1
)
echo.

echo ============================================
echo   Installation completed!
echo ============================================
echo.
echo Next step: run 02_run.bat to start the crawler
echo Or: .venv\Scripts\python.exe main.py --pages 1 --no-images
echo.
pause
exit /b 0
