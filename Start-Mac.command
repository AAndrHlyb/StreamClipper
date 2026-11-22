#!/bin/bash
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 HF_HUB_DISABLE_SYMLINKS_WARNING=1
xattr -dr com.apple.quarantine . 2>/dev/null
pause_exit() { echo; read -n 1 -s -r -p "Натисни будь-яку клавішу, щоб закрити..."; exit 1; }
if [ ! -x .venv/bin/python ]; then
  PY=""
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then PY="$c"; break; fi
  done
  if [ -z "$PY" ]; then
    echo "Потрібен Python 3. Відкриваю сторінку завантаження: встанови його і запусти Start-Mac.command знову."
    open "https://www.python.org/downloads/macos/"; pause_exit
  fi
  echo "Перший запуск: готую програму (3-5 хвилин, потрібен інтернет)..."
  "$PY" -m venv .venv || { echo "Не вдалося створити середовище Python"; pause_exit; }
fi
if [ ! -f .venv/deps_ok.txt ]; then
  .venv/bin/python -m pip install --disable-pip-version-check -q -U pip
  .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt || { echo "Не вдалося встановити бібліотеки. Перевір інтернет і запусти ще раз."; pause_exit; }
  echo ok > .venv/deps_ok.txt
else
  .venv/bin/python -m pip install --disable-pip-version-check -q -U yt-dlp
fi
.venv/bin/python app.py || pause_exit
