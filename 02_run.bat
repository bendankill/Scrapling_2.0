@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

:: 检查虚拟环境
if not exist ".venv\Scripts\python.exe" (
    echo [错误] 虚拟环境不存在！
    echo 请先双击运行 01_install.bat 安装环境
    pause
    exit /b 1
)

:: 激活虚拟环境
call .venv\Scripts\activate.bat

:menu
cls
echo ============================================
echo   eMAG 爬虫 V2.0 — 运行菜单
echo ============================================
echo.
echo   1. 测试抓取 1 页，不下载图片
echo   2. 测试抓取 2 页，不下载图片
echo   3. 抓取指定页数，下载主图
echo   4. 抓取指定页数，不下载图片
echo   5. 抓取全部页面并下载主图（需要二次确认）
echo   6. 打开类目配置文件
echo   7. 退出
echo.
set /p choice=请输入选项 (1-7):

if "%choice%"=="1" goto test1
if "%choice%"=="2" goto test2
if "%choice%"=="3" goto pages_img
if "%choice%"=="4" goto pages_noimg
if "%choice%"=="5" goto allpages
if "%choice%"=="6" goto editconfig
if "%choice%"=="7" goto end
echo 无效选项，请重试
pause
goto menu

:test1
echo.
echo [执行] 测试抓取 1 页，不下载图片...
.venv\Scripts\python.exe main.py --pages 1 --no-images
if %ERRORLEVEL% NEQ 0 (
    echo [注意] 程序返回错误码: %ERRORLEVEL%
)
echo.
pause
goto menu

:test2
echo.
echo [执行] 测试抓取 2 页，不下载图片...
.venv\Scripts\python.exe main.py --pages 2 --no-images
if %ERRORLEVEL% NEQ 0 (
    echo [注意] 程序返回错误码: %ERRORLEVEL%
)
echo.
pause
goto menu

:pages_img
echo.
set /p num=请输入每个类目最大页数:
echo [执行] 抓取 %num% 页，下载主图...
.venv\Scripts\python.exe main.py --pages %num%
if %ERRORLEVEL% NEQ 0 (
    echo [注意] 程序返回错误码: %ERRORLEVEL%
)
echo.
pause
goto menu

:pages_noimg
echo.
set /p num=请输入每个类目最大页数:
echo [执行] 抓取 %num% 页，不下载图片...
.venv\Scripts\python.exe main.py --pages %num% --no-images
if %ERRORLEVEL% NEQ 0 (
    echo [注意] 程序返回错误码: %ERRORLEVEL%
)
echo.
pause
goto menu

:allpages
echo.
echo [警告] 此操作将抓取全部页面并下载主图，可能耗时很长！
set /p confirm=确认执行？(输入 yes 继续):
if /i not "%confirm%"=="yes" (
    echo 已取消
    pause
    goto menu
)
echo [执行] 抓取全部页面并下载主图...
.venv\Scripts\python.exe main.py --all-pages
if %ERRORLEVEL% NEQ 0 (
    echo [注意] 程序返回错误码: %ERRORLEVEL%
)
echo.
pause
goto menu

:editconfig
echo.
echo 正在打开类目配置文件...
start notepad config\categories.json
pause
goto menu

:end
echo 再见！
exit /b 0
