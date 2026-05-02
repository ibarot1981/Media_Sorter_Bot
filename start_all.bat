@echo off
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m src.main --mode all
    goto :eof
)

python -m src.main --mode all
