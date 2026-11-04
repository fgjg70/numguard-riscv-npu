@echo off
rem ==========================================================================
rem run.bat - build every regression image, then run the FULL regression.
rem
rem   Authoritative entry point (11 tests, all of them):
rem       python tools\fpga_workflow.py verify
rem
rem   What changed (W-05/W-06):
rem     - the old script compiled only tb_units + tb_core (2 of 11) and then
rem       printed an unconditional green banner -> 18% coverage, all-pass look.
rem     - the old script guarded with `if not exist prog\basic.hex`, i.e. it
rem       checked that a hex EXISTS, not that it is FRESH. asm.py does not write
rem       the file when assembly fails, so a broken basic.S silently ran the
rem       PREVIOUS basic.hex (PITFALLS R1). Now every image is deleted first and
rem       a failed build aborts with a non-zero exit code.
rem
rem   Usage: sim\run.bat [verify args, e.g. --force --only tb_units]
rem   Note: keep this file ASCII-only (cmd parses .bat in OEM codepage, R2).
rem ==========================================================================
setlocal
pushd "%~dp0.."

set "PY=python"
where python >nul 2>nul || set "PY=py"

echo.
echo === [1/2] rebuilding every regression image (delete-then-build) ===
%PY% tools\build_sw.py --all
if errorlevel 1 (
  echo [ERROR] image build failed - refusing to test stale .hex files
  popd
  exit /b 1
)

echo.
echo === [2/2] full regression (11 tests, via fpga_workflow.py verify) ===
%PY% tools\fpga_workflow.py verify %*
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
