# tools/flash_sweep.ps1 -- try EVERY 128Mb cfgmem part against the board and
# record a three-state verdict per part, with the raw Labtools error line.
#
# The device-compatible list is derived from the Vivado part table
#   data/xicom/xicom_cfgmem_part_table.csv   (MEM_TYPE=spi, MEM_DENSITY=128,
#   'xc7a100t' in COMPATIBLE_DEVICES) -> exactly 9 parts in 2018.3.
# Two extra non-compatible names are included so the 44-655 case is on record.
#
# Each part runs in a FRESH Vivado process, and the script re-binds
# PROGRAM.HW_CFGMEM ("" then delete_hw_cfgmem) before creating the new object,
# so no stale part can leak into the next trial.
#
# Results -> tools/flash_logs/sweep_results.csv
param(
  [string]$Mcs = 'D:/USER/Desktop/FPGA/FPGA/tools/flash_logs/probe2_ramp.mcs',
  [string]$Action = 'program'
)
$ErrorActionPreference = 'Continue'
$root = 'D:\USER\Desktop\FPGA\FPGA'
$logdir = Join-Path $root 'tools\flash_logs'
$acc = 'D:/USER/Desktop/FPGA/FPGA/tools/flash_logs/accessor/spi_xc7a100t_pullnone.bit'
$env:FLASH_PRELOAD_ACCESSOR = $acc
$env:HW_SERVER_URL = 'localhost:3121'

$parts = @(
  'mt25ql128-spi-x1_x2_x4',
  'mt25qu128-spi-x1_x2_x4',
  's25fl128sxxxxxx0-spi-x1_x2_x4',
  's25fl128sxxxxxx1-spi-x1_x2_x4',
  's25fl128l-spi-x1_x2_x4',
  'mx25l12845g-spi-x1_x2_x4',
  'mx25u12835f-spi-x1_x2_x4',
  'is25lp128-spi-x1_x2_x4@',
  'is25wp128-spi-x1_x2_x4',
  'mt25ql128-spi-x1_x2_x4_x8',
  'w25q128fw-qspi-x1-single'
)

$rows = @()
foreach ($p in $parts) {
  $name = $p -replace '@$',''
  $out = & (Join-Path $root 'tools\flash_run.ps1') -Part $name -Action $Action -Mcs $Mcs
  $verdict = ($out | Select-String '^VERDICT=').Line -replace '^VERDICT=',''
  # full, untruncated Labtools error line
  $full = Join-Path $logdir (($name -replace '[^A-Za-z0-9_\.\-]','_') + "__${Action}.full.log")
  $err = ''
  if (Test-Path $full) {
    $m = Select-String -Path $full -Pattern 'ERROR: \[(Labtools|Labtoolstcl|Xicom) [0-9-]+\]' | Select-Object -First 1
    if ($m) { $err = ($m.Line -replace '^.*?(ERROR: \[)','ERROR: [') }
  }
  $rows += [pscustomobject]@{ part = $name; action = $Action; verdict = $verdict; first_error = $err }
  "SWEEP: {0,-34} {1,-32} {2}" -f $name, $verdict, $err
}
$rows | Export-Csv -NoTypeInformation -Encoding UTF8 (Join-Path $logdir 'sweep_results.csv')
"SWEEP_DONE -> tools/flash_logs/sweep_results.csv"
