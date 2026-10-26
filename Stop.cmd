@echo off
setlocal
call "%~dp0Start.cmd" --stop
exit /b %errorlevel%
