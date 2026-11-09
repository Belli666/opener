@echo off
pushd "%~dp0"

echo ========================================================
echo   Starting Opener...
echo   Working Directory: %CD%
echo ========================================================

if exist "dist\opener.exe" (
    "dist\opener.exe"
    set "EXIT_CODE=%errorlevel%"
    popd
    exit /b %EXIT_CODE%
)

if exist "opener.exe" (
    "opener.exe"
    set "EXIT_CODE=%errorlevel%"
    popd
    exit /b %EXIT_CODE%
)

REM Fallback: run via Python script directly
where python >nul 2>nul
if %errorlevel%==0 (
    python open.py
    set "EXIT_CODE=%errorlevel%"
    popd
    exit /b %EXIT_CODE%
)

where py >nul 2>nul
if %errorlevel%==0 (
    py open.py
    set "EXIT_CODE=%errorlevel%"
    popd
    exit /b %EXIT_CODE%
)

echo [ERROR] opener.exe not found.
echo Please run build_exe.bat first to build opener.exe.
pause
popd
exit /b 1
