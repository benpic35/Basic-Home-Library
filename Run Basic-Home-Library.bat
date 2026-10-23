@echo off
REM ---------------------------------------------------------------------------
REM Starts Basic-Home-Library from the source files.
REM (If you built the standalone .exe, use dist\Basic-Home-Library.exe instead.)
REM
REM Looks for a Python that can actually run the app - one with Tkinter and the
REM packages in requirements.txt - and says so plainly if it cannot find one.
REM ---------------------------------------------------------------------------
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "APP=Basic-Home-Library.py"
set "PYEXE="
set "ANYPY="

REM --- Candidates, best first -------------------------------------------------
REM Whatever is on PATH, then the py launcher, then common Anaconda/Miniconda
REM environments, then the usual python.org install locations.
for %%I in (python.exe) do call :try "%%~$PATH:I"
if defined PYEXE goto :launch

for %%I in (py.exe) do if not "%%~$PATH:I"=="" (
    for /f "usebackq delims=" %%P in (`"%%~$PATH:I" -3 -c "import sys;print(sys.executable)" 2^>nul`) do call :try "%%P"
)
if defined PYEXE goto :launch

for %%E in (miniconda3 anaconda3 miniforge3) do (
    call :try "%USERPROFILE%\%%E\envs\library\python.exe"
    if not defined PYEXE call :try "%USERPROFILE%\%%E\python.exe"
)
if defined PYEXE goto :launch

for %%V in (313 312 311 310 39) do (
    if not defined PYEXE call :try "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
    if not defined PYEXE call :try "C:\Python%%V\python.exe"
)
if defined PYEXE goto :launch

goto :nopython

REM --- Launch -----------------------------------------------------------------
:launch
REM pythonw.exe runs the window with no console behind it; fall back to python.
set "PYW=%PYEXE:python.exe=pythonw.exe%"
if exist "%PYW%" (
    start "" "%PYW%" "%APP%"
) else (
    start "" "%PYEXE%" "%APP%"
)
exit /b 0

REM --- Helpers ----------------------------------------------------------------
:try
REM %1 = candidate interpreter. Accepts it only if it exists, is not the
REM Microsoft Store placeholder, and can import what the app needs.
if defined PYEXE exit /b
if "%~1"=="" exit /b
echo %~1 | find /i "\WindowsApps\" >nul && exit /b
if not exist "%~1" exit /b
set "ANYPY=%~1"
"%~1" -c "import tkinter, openpyxl" >nul 2>&1 || exit /b
set "PYEXE=%~1"
exit /b

:nopython
echo.
if defined ANYPY (
    echo Python was found here:
    echo     %ANYPY%
    echo but it is missing something the app needs.
    echo.
    echo Install the requirements with:
    echo     "%ANYPY%" -m pip install -r requirements.txt
    echo.
    echo If that reports Tkinter missing, reinstall Python from python.org
    echo and keep the "tcl/tk and IDLE" option ticked.
) else (
    echo No usable Python installation was found.
    echo.
    echo Either install Python 3.9 or newer from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH" during setup^), then run:
    echo     pip install -r requirements.txt
    echo.
    echo ...or just use the ready-made program instead, which needs no Python:
    echo     dist\Basic-Home-Library.exe
)
echo.
pause
exit /b 1
