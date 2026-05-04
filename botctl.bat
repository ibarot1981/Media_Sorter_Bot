@echo off
setlocal

cd /d "%~dp0"

powershell -ExecutionPolicy Bypass -File ".\scripts\botctl.ps1" %*
