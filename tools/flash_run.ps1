# tools/flash_run.ps1 -- one-stop runner for the QSPI cfgmem experiments.
#
# Captures the COMPLETE Vivado stdout/stderr (not just the tail) into
# tools/flash_logs/<part>__<action>.full.log so that every Labtools message
# around [Labtools 27-3347] is preserved as evidence.
#
# Usage:
#   pwsh -File tools/flash_run.ps1 -Part mt25ql128-spi-x1_x2_x4 -Action blankcheck
#   pwsh -File tools/flash_run.ps1 -Part <name> -Action program -Mcs <path>
param(
  [Parameter(Mandatory=$true)][string]$Part,
  [ValidateSet('probe','blankcheck','program','program_noverify','program_doc','noerase_verify')][string]$Action = 'blankcheck',
  [string]$Mcs = 'D:/USER/Desktop/FPGA/FPGA/fpga/cpu_a100t.mcs',
  [string]$HwServerUrl = ''
)
$ErrorActionPreference = 'Continue'
$root = 'D:\USER\Desktop\FPGA\FPGA'
$logdir = Join-Path $root 'tools\flash_logs'
New-Item -ItemType Directory -Force -Path $logdir | Out-Null
$safe = ($Part -replace '[^A-Za-z0-9_\.\-]','_')
$full = Join-Path $logdir "${safe}__${Action}.full.log"

$env:PROCESSOR_ARCHITECTURE = 'AMD64'
$env:FLASH_PART   = $Part
$env:FLASH_ACTION = $Action
$env:FLASH_MCS    = $Mcs
if ($HwServerUrl -ne '') { $env:HW_SERVER_URL = $HwServerUrl } else { Remove-Item Env:\HW_SERVER_URL -ErrorAction SilentlyContinue }

& 'D:\software\Learn\Xilinx\Vivado\2018.3\bin\vivado.bat' -mode batch -nojournal -nolog `
    -source (Join-Path $root 'tools\flash_program.tcl') *>&1 |
  Tee-Object -FilePath $full | Out-Null

# Classify from the SCRIPT'S OWN log (tools/flash_logs/<part>__<action>.log).
# Do NOT classify from $full: Vivado batch mode echoes every sourced Tcl line as
# a '# ...' comment, so the literal strings "RESULT=PROGRAM_OK" etc. appear in
# $full even when that branch never ran. (Cost me one false positive.)
$own = Join-Path $logdir "${safe}__${Action}.log"
$txt = if (Test-Path $own) { Get-Content $own -Raw } else { '' }
# Only REAL Vivado messages count. Vivado batch mode echoes every sourced Tcl
# line as a '# ...' comment, so a raw -match on the full log gives false
# positives (e.g. this script's own "set_msg_config -id {Labtools 27-3347}").
$msgs = if (Test-Path $full) { (Select-String -Path $full -Pattern '^(INFO|WARNING|ERROR):' | ForEach-Object { $_.Line }) -join "`n" } else { '' }
$cls = 'UNKNOWN'
if     ($txt -match 'RESULT=PROGRAM_OK')         { $cls = 'PROGRAM_OK' }
elseif ($txt -match 'RESULT=PROGRAM_NOVERIFY_OK'){ $cls = 'PROGRAM_NOVERIFY_OK' }
elseif ($txt -match 'RESULT=NOERASE_VERIFY_OK')  { $cls = 'NOERASE_VERIFY_OK' }
elseif ($txt -match 'RESULT=BLANKCHECK_OK')      { $cls = 'BLANKCHECK_OK(read-only)' }
elseif ($txt -match 'RESULT=NOT_COMPATIBLE')     { $cls = '44-655_NOT_SUPPORTED_FOR_DEVICE' }
elseif ($msgs -match 'does not match')           { $cls = '27-3347_VERIFY_MISMATCH' }
elseif ($msgs -match '27-3347')                  { $cls = '27-3347_FAIL_SET_FLASH_PARAMS' }
elseif ($msgs -match '44-655')                   { $cls = '44-655_NOT_SUPPORTED_FOR_DEVICE' }
$errLine = (Select-String -Path $full -Pattern 'ERROR: \[Labtools|ERROR: \[Common' | Select-Object -First 1).Line
"PART=$Part ACTION=$Action VERDICT=$cls"
if ($errLine) { "FIRSTERROR=$errLine" }
"FULLLOG=$full"
exit 0
