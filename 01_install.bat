@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ============================================
echo   eMAG 爬虫 V2.0 — 环境安装脚本
echo ============================================
echo.

:: 检查 Python
echo [1/5] 检查 Python...
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 未找到 Python！请先安装 Python 3.11 或 3.12
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version
echo.

:: 创建虚拟环境
echo [2/5] 创建虚拟环境 .venv...
if exist ".venv\Scripts\python.exe" (
    echo 虚拟环境已存在，跳过创建
) else (
    python -m venv .venv
    if %ERRORLEVEL% NEQ 0 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
    echo 虚拟环境创建成功
)
echo.

:: 激活虚拟环境
echo [3/5] 激活虚拟环境...
call .venv\Scripts\activate.bat
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 激活虚拟环境失败
    pause
    exit /b 1
)
echo.

:: 升级 pip
echo [4/5] 升级 pip...
.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
echo.

:: 安装依赖
echo [5/5] 安装项目依赖...
.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 安装依赖失败，请检查网络连接
    pause
    exit /b 1
)
echo.

:: 安装 Playwright Chromium（Scrapling StealthyFetcher 需要）
echo 安装 Playwright Chromium...
.venv\Scripts\python.exe -m playwright install chromium
if %ERRORLEVEL% NEQ 0 (
    echo [警告] Playwright Chromium 安装失败，StealthyFetcher 可能无法使用
)

:: 显示 Scrapling 版本
echo.
echo Scrapling 版本:
.venv\Scripts\python.exe -c "import scrapling; print('  ', scrapling.__version__)"

echo.
echo ============================================
echo   安装完成！
echo ============================================
echo.
echo 下一步: 双击 02_run.bat 启动爬虫
echo.
pause
