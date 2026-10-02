@echo off
setlocal
cd /d "%~dp0"
title Lisan AI - LOCAL TEST COPY

rem ---- 1. Python 3.12 -------------------------------------------------------
set "PY="
py -3.12 --version >nul 2>nul && set "PY=py -3.12"
if not defined PY (
  python --version 2>nul | findstr /b /c:"Python 3.12" >nul && set "PY=python"
)
if not defined PY goto nopython

rem ---- 2. ffmpeg ------------------------------------------------------------
where ffmpeg >nul 2>nul
if errorlevel 1 goto noffmpeg

rem ---- 3. settings file (first run only) --------------------------------------
if not exist ".env.local" (
  echo First run: creating .env.local from your existing .env ...
  %PY% setup_local.py
  if errorlevel 1 goto fail
  echo.
  echo Open .env.local in a text editor and check it, then run this file again.
  pause
  exit /b 0
)

rem ---- 4. Python environment (first run, or when requirements.txt changed) --------
if exist ".venv\Scripts\python.exe" (
  fc /b requirements.txt ".venv\requirements.installed.txt" >nul 2>nul
  if not errorlevel 1 goto run
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating the Python environment. First run only - this can take 10 to 20 minutes.
  %PY% -m venv .venv
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
copy /y requirements.txt ".venv\requirements.installed.txt" >nul

:run
set LOCAL_DEV=1
echo.
echo ================================================================
echo   Lisan AI LOCAL TEST COPY  -  http://localhost:8000
echo   It uses the LIVE database. Close this window to stop it.
echo ================================================================
echo.
start "" /min cmd /c "timeout /t 10 /nobreak >nul & start http://localhost:8000"
".venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
pause
exit /b 0

:nopython
echo Python 3.12 was not found. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run this file again.
pause
exit /b 1

:noffmpeg
echo ffmpeg was not found. Open a new PowerShell window, run:   winget install Gyan.FFmpeg
echo Then close and reopen this window and run this file again.
pause
exit /b 1

:fail
echo.
echo Something failed - scroll up to read the message above.
pause
exit /b 1
