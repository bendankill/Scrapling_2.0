@echo off
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
setlocal enabledelayedexpansion

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found!
    echo Please run 01_install.bat first.
    pause
    exit /b 1
)

:menu
cls
echo ============================================
echo   eMAG Crawler V2.0.1 - Run Menu
echo ============================================
echo.
echo   1. Test 1 page, no images
echo   2. Test 2 pages, no images
echo   3. Crawl N pages, with images
echo   4. Crawl N pages, no images
echo   5. Crawl all pages, with images (confirmation required)
echo   6. Open config\categories.txt
echo   7. Exit
echo.
set /p choice="Enter option (1-7): "

if "%choice%"=="1" goto test1
if "%choice%"=="2" goto test2
if "%choice%"=="3" goto pages_img
if "%choice%"=="4" goto pages_noimg
if "%choice%"=="5" goto allpages
if "%choice%"=="6" goto editconfig
if "%choice%"=="7" goto end
echo Invalid option
pause
goto menu

:test1
echo.
echo [RUN] 1 page, no images...
".venv\Scripts\python.exe" main.py --pages 1 --no-images
call :check_result
pause
goto menu

:test2
echo.
echo [RUN] 2 pages, no images...
".venv\Scripts\python.exe" main.py --pages 2 --no-images
call :check_result
pause
goto menu

:pages_img
echo.
set "num="
set /p num="Pages per category: "
:: Validate: must be a positive integer
set "valid=1"
if "%num%"=="" set "valid=0"
for /f "delims=0123456789" %%d in ("%num%") do set "valid=0"
if "%num%"=="0" set "valid=0"
if "%valid%"=="0" (
    echo [ERROR] Please enter a positive integer (1, 2, 3, ...)
    pause
    goto menu
)
echo [RUN] %num% pages, with images...
".venv\Scripts\python.exe" main.py --pages %num%
call :check_result
pause
goto menu

:pages_noimg
echo.
set "num="
set /p num="Pages per category: "
set "valid=1"
if "%num%"=="" set "valid=0"
for /f "delims=0123456789" %%d in ("%num%") do set "valid=0"
if "%num%"=="0" set "valid=0"
if "%valid%"=="0" (
    echo [ERROR] Please enter a positive integer (1, 2, 3, ...)
    pause
    goto menu
)
echo [RUN] %num% pages, no images...
".venv\Scripts\python.exe" main.py --pages %num% --no-images
call :check_result
pause
goto menu

:allpages
echo.
echo [WARNING] This will crawl ALL pages and download images. It may take a long time!
set /p confirm="Type 'yes' to confirm: "
if /i not "%confirm%"=="yes" (
    echo Cancelled
    pause
    goto menu
)
echo [RUN] All pages, with images...
".venv\Scripts\python.exe" main.py --all-pages
call :check_result
pause
goto menu

:editconfig
echo.
echo Opening config\categories.txt...
if exist "config\categories.txt" (
    start notepad "config\categories.txt"
) else (
    echo [ERROR] config\categories.txt not found
)
pause
goto menu

:check_result
set "EC=%ERRORLEVEL%"
if %EC% EQU 0 (
    echo [OK] Completed successfully (exit code 0)
) else if %EC% EQU 3 (
    echo [CAPTCHA] Website verification required - see captcha_diagnostic.json
    echo   Open the URL in a normal browser, complete the verification, then run again.
    echo   If the issue persists, pure HTTP access may not be possible right now.
) else if %EC% EQU 130 (
    echo [STOPPED] Interrupted by user (Ctrl+C)
) else (
    echo [FAILED] Exit code: %EC%
)
goto :eof

:end
echo Goodbye!
exit /b 0
