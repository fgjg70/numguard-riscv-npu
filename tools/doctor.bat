@echo off
rem ==========================================================================
rem doctor.bat - environment self-check, and R1 drift detection.
rem
rem Why a .bat: the DSH session strips process env, so Vivado/ModelSim fail
rem unless PROCESSOR_ARCHITECTURE / LM_LICENSE_FILE are set explicitly.
rem Keep this file pure ASCII (cmd parses .bat in the OEM codepage).
rem
rem Usage:  cmd /c tools\doctor.bat
rem         cmd /c tools\doctor.bat --no-check      (env only)
rem ==========================================================================
setlocal
cd /d "%~dp0.."

set "PY="
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if not defined PY set "PY=C:\Users\19716\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PY%" (
  echo [ERROR] python not found. Expected: C:\Users\19716\AppData\Local\Programs\Python\Python313\python.exe
  exit /b 1
)

set "PYTHONIOENCODING=utf-8"
rem Console codepage must match the UTF-8 that Python emits, otherwise Chinese
rem output turns into mojibake. 65001 = UTF-8.
chcp 65001 >nul 2>&1

"%PY%" tools\fpga_workflow.py doctor
set "RC=%ERRORLEVEL%"

if /i "%~1"=="--no-check" ( exit /b %RC% )

echo.
"%PY%" tools\fpga_workflow.py check
if errorlevel 1 set "RC=1"

exit /b %RC%
