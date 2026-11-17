@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Field Manual Vault Archiver v5

echo ===============================================================
echo    FIELD MANUAL VAULT - SELECTIVE ARCHIVER v5
echo ===============================================================
echo.
echo Unofficial, independent utility. Your existing manuals are preserved.
echo Discovery is rate-limited and uses cached metadata when possible.
echo.
set "PYTHON=py -3"
where py >nul 2>nul
if errorlevel 1 (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python 3.10+ not found. Install from https://www.python.org/downloads/
    echo Check Add Python to PATH during installation.
    pause
    exit /b 1
  )
  set "PYTHON=python"
)
set /p "DEST=Field Manual Vault folder (blank = Documents\Field Manual Vault): "
if not defined DEST set "DEST=%USERPROFILE%\Documents\Field Manual Vault"
set "DEST=%DEST:"=%"
echo.
echo CHOOSE AN ACTION
echo.
echo  1  AUDIT EXISTING PDF/EPUB FILES (no internet)
echo  2  BUILD WHOLE-SITE CATALOG ONLY (may take time)
echo  3  SELECTIVE BULK DOWNLOAD (categories, formats, GB cap)
echo  4  LEGACY FIVE POPULAR SEARCH TOPICS
echo  5  COMPLETE ORGANIZED DOWNLOAD (potentially over 100 GB)
echo  6  REBUILD OFFLINE HTML CATALOG (no internet)
echo  7  INCREMENTAL CATALOG UPDATE (selected categories, no files downloaded)
echo.
set /p "SELECT=Choose 1-7 (default 3): "
if not defined SELECT set "SELECT=3"
set "ARGS="
if "%SELECT%"=="1" set "ARGS=--mode organized --audit-only"
if "%SELECT%"=="2" set "ARGS=--mode organized --dry-run"
if "%SELECT%"=="3" set "ARGS=--mode selective --interactive --estimate-samples 6"
if "%SELECT%"=="4" set "ARGS=--mode topics"
if "%SELECT%"=="5" set "ARGS=--mode organized"
if "%SELECT%"=="6" set "ARGS=--index-only"
if "%SELECT%"=="7" set "ARGS=--mode selective --interactive --update-only"
if not defined ARGS (
  echo Invalid choice.
  pause
  exit /b 2
)
if "%SELECT%"=="1" goto EXECUTE
if "%SELECT%"=="6" goto EXECUTE

echo.
echo DISCOVERY REQUEST PACING (all workers share the same global limit)
echo  1  Gentle:   1 worker; at least 1.25 s between requests
echo  2  Balanced: 2 workers; at least 0.55 s between requests (default)
echo  3  Faster:   3 workers; at least 0.50 s between requests
echo.
set /p "SPEED=Select 1-3 (default 2): "
if not defined SPEED set "SPEED=2"
if "%SPEED%"=="1" set "ARGS=%ARGS% --workers 1 --request-interval 1.25"
if "%SPEED%"=="2" set "ARGS=%ARGS% --workers 2 --request-interval 0.55"
if "%SPEED%"=="3" set "ARGS=%ARGS% --workers 3 --request-interval 0.5"
if not "%SPEED%"=="1" if not "%SPEED%"=="2" if not "%SPEED%"=="3" (
  echo Invalid speed choice.
  pause
  exit /b 2
)
if "%SELECT%"=="5" goto CONFIRM_FULL
if "%SELECT%"=="4" goto CONFIRM_LEGACY
goto EXECUTE

:CONFIRM_FULL
echo.
echo CAUTION: Can consume over 100 GB. Complete site discovery is not guaranteed.
set /p "CONFIRM=Type YES to attempt the full archive: "
if /i not "%CONFIRM%"=="YES" goto CANCEL
goto EXECUTE

:CONFIRM_LEGACY
echo.
echo Legacy search topics do not have a per-run storage limit by default.
set /p "CONFIRM=Type YES to start legacy topics: "
if /i not "%CONFIRM%"=="YES" goto CANCEL
goto EXECUTE

:EXECUTE
echo.
%PYTHON% "%~dp0trex_vault_archiver.py" --dest "%DEST%" %ARGS%
set "RESULT=%ERRORLEVEL%"
echo.
if "%RESULT%"=="0" (echo Completed.) else (echo Exit status: %RESULT%. Check the logs above.)
pause
exit /b %RESULT%

:CANCEL
echo Canceled. No download started.
pause
exit /b 0
