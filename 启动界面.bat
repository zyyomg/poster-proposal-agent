@echo off
chcp 936 >nul
cd /d "%~dp0"
title Brief-to-Draft

for /f %%i in ('powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8502 -State Listen -ErrorAction SilentlyContinue) { 'True' } else { 'False' }"') do set RUNNING=%%i
if /i "%RUNNING%"=="True" goto :already

echo.
echo   正在启动界面，浏览器会自动打开：http://127.0.0.1:8502
echo.
echo   [重要] 不要关掉这个黑窗口。关掉它 = 停止服务。
echo.
python -m streamlit run app.py --server.port 8502 --browser.gatherUsageStats false
pause
exit /b

:already
echo.
echo   界面已经在运行，直接打开浏览器：http://127.0.0.1:8502
echo.
start "" http://127.0.0.1:8502
exit /b
