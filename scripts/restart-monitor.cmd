@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0monitor.ps1" -Action restart
if errorlevel 1 pause
