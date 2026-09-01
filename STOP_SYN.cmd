@echo off
setlocal
PowerShell -NoProfile -ExecutionPolicy Bypass -File '%~dp0scripts\stop_syn.ps1'
endlocal
