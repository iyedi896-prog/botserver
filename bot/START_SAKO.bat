@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Sae Uploader - Telegram Login Service

echo.
echo ==========================================
echo       Sae Uploader - Telegram Service
echo ==========================================
echo Bot: https://t.me/sakoportal_bot  ^|  Local API: http://127.0.0.1:8765
echo Keep this window open while users sign in.
echo.

if not exist "%~dp0wexo_telegram_bot.py" (
  echo [!] wexo_telegram_bot.py was not found.
  echo     Extract the entire ZIP before running this file.
  echo.
  pause
  exit /b 1
)

set "PYTHON_CMD="
call :findpython
if defined PYTHON_CMD goto :havepython

echo [!] Python 3.8 or newer was not found.
where winget >nul 2>nul
if errorlevel 1 goto :nopython
choice /C YN /M "Install Python 3.12 automatically"
if errorlevel 2 goto :nopython
winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
call :findpython
if defined PYTHON_CMD goto :havepython
echo Python was installed. Run START_SAKO.bat again.
pause
exit /b 1

:nopython
echo Install Python 3.8+ from https://www.python.org/downloads/windows/
echo Enable the Python PATH option, then run this file again.
pause
exit /b 1

:havepython
echo Using: %PYTHON_CMD%
echo Bot token is requested once and saved in wexo_token.txt.
echo.

:loop
%PYTHON_CMD% "wexo_telegram_bot.py"
set "CODE=%ERRORLEVEL%"
if "%CODE%"=="0" goto :end
if "%CODE%"=="3" (
  echo.
  echo Fix the configuration problem above, then run START_SAKO.bat again.
  pause
  exit /b 3
)
echo.
echo Service stopped with code %CODE%. Restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto :loop

:end
endlocal
exit /b 0

:findpython
for %%C in ("py -3" "python" "python3") do (
  if not defined PYTHON_CMD (
    %%~C -c "import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=%%~C"
  )
)
exit /b 0
