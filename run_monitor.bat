@echo off
REM Ejecuta el monitor en modo 24/7 desde este PC (alternativa local a Oracle/GitHub).
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py --loop %*
