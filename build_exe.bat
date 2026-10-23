@echo off
REM ---------------------------------------------------------------------------
REM Builds "Basic-Home-Library.exe" - a single standalone file that runs on
REM Windows 10 and 11 with no Python installation required.
REM
REM Run it from a prompt where "python" is the interpreter you installed the
REM requirements into. The finished .exe is left in the "dist" folder.
REM
REM The build goes through Basic-Home-Library.spec rather than plain command
REM line flags. That matters on Anaconda/Miniconda, where Tcl/Tk and a few
REM other DLLs sit in <env>\Library\bin, which PyInstaller does not scan: build
REM without the spec there and the .exe dies on start-up with
REM "ImportError: DLL load failed while importing _tkinter".
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

echo.
echo === Installing the requirements ===========================================
python -m pip install --upgrade -r requirements.txt pyinstaller || goto :failed

echo.
echo === Building Basic-Home-Library.exe =======================================
python -m PyInstaller --noconfirm --clean "Basic-Home-Library.spec" || goto :failed

echo.
echo === Done ==================================================================
echo The app is here:  "%~dp0dist\Basic-Home-Library.exe"
echo Copy that one file wherever you like and double-click it to run.
echo.
pause
exit /b 0

:failed
echo.
echo *** The build failed. Scroll up to see the error. ***
echo.
pause
exit /b 1
