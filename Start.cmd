@echo off
setlocal
cd /d "%~dp0"

py -3 --version >nul 2>&1
if errorlevel 1 goto try_python
py -3 "%~dp0app.py" %*
set "localFlowExit=%errorlevel%"
goto done

:try_python
python --version >nul 2>&1
if errorlevel 1 goto missing_python
python "%~dp0app.py" %*
set "localFlowExit=%errorlevel%"
goto done

:missing_python
echo LocalFlow requires Python 3.10 or newer. Install Python and enable the Python launcher.
set "localFlowExit=1"

:done
if "%localFlowExit%"=="0" goto finish
if /i "%~1"=="--no-browser" goto finish
echo.
echo LocalFlow exited with error %localFlowExit%.
pause

:finish
exit /b %localFlowExit%
