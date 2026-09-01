@echo off
setlocal
PowerShell -NoProfile -ExecutionPolicy Bypass -File '%~dp0scripts\start_syn.ps1' %1
endlocal
