@echo off
setlocal enabledelayedexpansion

REM Fix UNC path issue (e.g. \\wsl.localhost\... or network shares)
pushd "%~dp0"
if errorlevel 1 (
    echo [ERROR] Failed to enter directory: %~dp0
    pause
    exit /b 1
)

echo ========================================================
echo   Opener - Single-File EXE Builder (Windows 10/11)
echo   Working Directory: %CD%
echo ========================================================

set "PY_CMD="
where python >nul 2>nul && set "PY_CMD=python"
if not defined PY_CMD (
    where py >nul 2>nul && set "PY_CMD=py"
)

if not defined PY_CMD (
    echo [ERROR] Python launcher not found in PATH.
    echo Please install Python 3.10+ from python.org and check "Add Python to PATH".
    pause
    popd
    exit /b 1
)

echo [1/4] Upgrading pip...
%PY_CMD% -m pip install --upgrade pip
if errorlevel 1 (
    echo [WARN] Pip upgrade warning, continuing...
)

echo [2/4] Installing / updating dependencies...
%PY_CMD% -m pip install --upgrade selenium pyinstaller webdriver-manager
if errorlevel 1 (
    echo [ERROR] Failed to install required packages.
    pause
    popd
    exit /b 1
)

echo [3/4] Cleaning previous build artifacts and old files...
if exist "build" rmdir /s /q "build" 2>nul
if exist "dist\open.exe" del /f /q "dist\open.exe" 2>nul
if exist "open.exe" del /f /q "open.exe" 2>nul

echo [4/4] Compiling standalone single-file EXE (opener.exe)...
%PY_CMD% -m PyInstaller --noconfirm --clean opener.spec
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed.
    pause
    popd
    exit /b 1
)

REM Clean up any leftover duplicate open.exe
if exist "dist\open.exe" del /f /q "dist\open.exe" 2>nul

echo.
echo ========================================================
echo   BUILD COMPLETED SUCCESSFULLY!
echo   Single executable: %~dp0dist\opener.exe
echo ========================================================
echo.
pause
popd
exit /b 0
