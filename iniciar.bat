@echo off
rem Doble clic para levantar el sistema (PC + celular). "iniciar.bat solo" = solo desde esta PC.
cd /d "%~dp0"
if /i "%1"=="solo" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0levantar_con_celular.ps1" -SoloPC
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0levantar_con_celular.ps1"
)
pause
