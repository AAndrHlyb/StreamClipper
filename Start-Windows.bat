@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title StreamClipper
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
if exist ".venv\Scripts\python.exe" goto deps
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
  echo Python не знайдено. Встановлюю Python 3.12 ^(потрібно один раз^)...
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  echo.
  echo Готово. Закрий це вікно і запусти Start-Windows.bat ще раз.
  echo Якщо winget не спрацював: завантаж Python з https://www.python.org/downloads/ ^(галочка "Add python.exe to PATH"^)
  pause
  exit /b
)
echo Перший запуск: готую програму ^(3-5 хвилин, потрібен інтернет^)...
%PY% -m venv .venv
if errorlevel 1 (echo Не вдалося створити середовище Python. & pause & exit /b)
:deps
if not exist ".venv\deps_ok.txt" goto install
fc /b requirements.txt ".venv\deps_ok.txt" >nul 2>&1 || goto install
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-warn-script-location -q -U yt-dlp
goto run
:install
echo Встановлюю бібліотеки ^(кілька хвилин^)...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-warn-script-location -q -U pip
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-warn-script-location -q -r requirements.txt
if errorlevel 1 (echo Не вдалося встановити бібліотеки. Перевір інтернет і запусти ще раз. & pause & exit /b)
copy /y requirements.txt ".venv\deps_ok.txt" >nul
:run
".venv\Scripts\python.exe" app.py
if errorlevel 1 pause
