@echo off
setlocal

cd /d "%~dp0"

set "PYTHON=.venv\Scripts\python.exe"
set "SPEC=akagi.spec"
set "BUILD_ROOT=release"
set "DIST_DIR=%BUILD_ROOT%\dist"
set "WORK_DIR=%BUILD_ROOT%\build"

if not exist "%PYTHON%" (
  echo [ERROR] Missing Python: "%PYTHON%"
  echo [HINT] Create venv and install deps first.
  echo [HINT] py -3.12 -m venv .venv
  echo [HINT] .venv\Scripts\python.exe -m pip install -r requirements.txt
  exit /b 1
)

if not exist "%SPEC%" (
  echo [ERROR] Missing spec file: "%SPEC%"
  exit /b 1
)

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%I"
set "ZIP_PATH=%BUILD_ROOT%\Akagi-win64-py312-%TS%.zip"

echo [1/3] Building with PyInstaller...
"%PYTHON%" -m PyInstaller --noconfirm --clean "%SPEC%" --distpath "%DIST_DIR%" --workpath "%WORK_DIR%"
if errorlevel 1 (
  echo [ERROR] PyInstaller build failed.
  exit /b 1
)

if not exist "%DIST_DIR%\Akagi\Akagi.exe" (
  echo [ERROR] Build output missing: "%DIST_DIR%\Akagi\Akagi.exe"
  exit /b 1
)

echo [2/3] Creating zip package...
powershell -NoProfile -Command "Compress-Archive -Path '%CD%\%DIST_DIR%\Akagi\*' -DestinationPath '%CD%\%ZIP_PATH%' -Force"
if errorlevel 1 (
  echo [ERROR] Zip package creation failed.
  exit /b 1
)

echo [3/3] Done.
echo EXE: %CD%\%DIST_DIR%\Akagi\Akagi.exe
echo ZIP: %CD%\%ZIP_PATH%

exit /b 0
