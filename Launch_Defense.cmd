@echo off
setlocal
cd /d "%~dp0"
title CarDiNel Defense Viewer
if defined CARDINEL_PYTHON (
  "%CARDINEL_PYTHON%" -c "import PySide6, matplotlib" >nul 2>&1
  if not errorlevel 1 goto custom
)
for %%P in ("%~dp0.venv\Scripts\python.exe" "%~dp0..\orbit-trust\.venv\Scripts\python.exe" "%USERPROFILE%\OneDrive\Desktop\orbit-trust\.venv\Scripts\python.exe" "%USERPROFILE%\Desktop\orbit-trust\.venv\Scripts\python.exe") do (
  if exist "%%~P" (
    "%%~P" -c "import PySide6, matplotlib" >nul 2>&1
    if not errorlevel 1 (
      set "CARDINEL_PYTHON=%%~P"
      goto custom
    )
  )
)
py -3 -c "import PySide6, matplotlib" >nul 2>&1
if not errorlevel 1 (
  py -3 -m src.defense %*
  goto finish
)
python -c "import PySide6, matplotlib" >nul 2>&1
if not errorlevel 1 (
  python -m src.defense %*
  goto finish
)
echo No Python environment with PySide6 and matplotlib was found.
echo Open DEFENSE_VIEWER.md for the one-time setup instructions.
pause
exit /b 1
:custom
"%CARDINEL_PYTHON%" -m src.defense %*
:finish
if errorlevel 1 (
  echo The viewer stopped with an error. Keep this window open to read it.
  pause
  exit /b 1
)
endlocal
