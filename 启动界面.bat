@echo off
chcp 936 >nul
cd /d "%~dp0"
title 海报提案 Agent

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   没有找到 python 命令。请先安装 Python 3.11+，安装时勾选 "Add python.exe to PATH"。
    echo.
    pause
    exit /b
)

for /f %%i in ('powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8502 -State Listen -ErrorAction SilentlyContinue) { 'True' } else { 'False' }"') do set RUNNING=%%i
if /i "%RUNNING%"=="True" goto :already

echo.
echo   正在启动界面，浏览器会在几秒后自动打开：http://127.0.0.1:8502
echo.
echo   [重要] 不要关掉这个黑窗口。关掉它 = 停止服务。
echo.

rem 后台等 8 秒后打开浏览器（服务器起来需要时间）
start "" /min cmd /c "ping -n 9 127.0.0.1 >nul & start http://127.0.0.1:8502"

python -m streamlit run app.py --server.headless true --server.port 8502 --browser.gatherUsageStats false
pause
exit /b

:already
echo.
echo   界面已经在运行，直接打开浏览器：http://127.0.0.1:8502
echo.
start "" http://127.0.0.1:8502
exit /b
