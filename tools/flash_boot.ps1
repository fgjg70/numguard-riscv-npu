# tools/flash_boot.ps1 -- ONE-SHOT: make the board boot our design from QSPI.
#
# Usage:
#   powershell -File tools\flash_boot.ps1                      # SPIx1 image from fpga\cpu_a100t.bit
#   powershell -File tools\flash_boot.ps1 -Bit fpga\cpu_a100t_spix4.bit -Iface SPIx4
#   powershell -File tools\flash_boot.ps1 -SkipProgram          # only build+verify the image
#
# It (1) extracts the hw_server SPI "flash accessor" bitstream out of the Vivado
# installation, (2) builds a bootable .bin/.mcs with write_cfgmem, (3) programs
# the QSPI flash, and (4) READS THE WHOLE FLASH BACK and compares it byte for
# byte with the image it wrote. Step 4 is the only real proof -- Labtools reports
# "Flash programming completed successfully" even in configurations where
# nothing was written at all.
param(
  [string]$Bit   = 'D:/USER/Desktop/FPGA/FPGA/fpga/cpu_a100t.bit',
  [ValidateSet('SPIx1','SPIx4','SPIx2')][string]$Iface = 'SPIx1',
  [string]$Part  = 'mt25ql128-spi-x1_x2_x4',
  [switch]$SkipProgram
)
$ErrorActionPreference = 'Stop'
$root   = 'D:\USER\Desktop\FPGA\FPGA'
$logdir = Join-Path $root 'tools\flash_logs'
$accdir = Join-Path $logdir 'accessor'
$vivado = 'D:\software\Learn\Xilinx\Vivado\2018.3\bin\vivado.bat'
$zip    = 'D:\software\Learn\Xilinx\Vivado\2018.3\data\xicom\cfgmem\bitfile.zip'
$py     = 'C:\Users\19716\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
New-Item -ItemType Directory -Force -Path $logdir, $accdir | Out-Null
$env:PROCESSOR_ARCHITECTURE = 'AMD64'

# ---------------------------------------------------------------- 1. accessor
# hw_server does NOT load this by itself in the Tcl flow; without it there is no
# XicomFlash context and the flash call dies with
#   [Labtools 27-3347] Flash programming unsuccessful: Failure to set flash parameters
# The variant must match the bitstream's BITSTREAM.CONFIG.UNUSEDPIN:
#   PULLNONE -> spi_<dev>_pullnone.bit, PULLUP -> spi_<dev>_pullup.bit, ...
$accName = if ($Bit -match 'spix4') { 'spi_xc7a100t_pullup.bit' } else { 'spi_xc7a100t_pullnone.bit' }
$acc = Join-Path $accdir $accName
if (-not (Test-Path $acc)) {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $z = [System.IO.Compression.ZipFile]::OpenRead($zip)
  $e = $z.Entries | Where-Object { $_.FullName -eq "bitfile/$accName" }
  if (-not $e) { $z.Dispose(); throw "accessor $accName not found in $zip" }
  [System.IO.Compression.ZipFileExtensions]::ExtractToFile($e, $acc, $true)
  $z.Dispose()
}
Write-Host "[1/4] accessor = $acc ($((Get-Item $acc).Length) bytes)"

# ------------------------------------------------------------- 2. build image
$env:IMG_BIT = $Bit; $env:IMG_IFACE = $Iface; $env:IMG_SIZE = '16'
$tag = $Iface.ToLower()
& $vivado -mode batch -nojournal -nolog -source (Join-Path $root 'tools\flash_mkimage.tcl') *>&1 |
  Select-String 'MKIMAGE' | ForEach-Object { Write-Host "      $($_.Line)" }
$img = Join-Path $root "fpga\cpu_a100t_$tag.bin"
if (-not (Test-Path $img)) { throw "image not produced: $img" }
Write-Host "[2/4] image = $img ($((Get-Item $img).Length) bytes, $Iface)"

if ($SkipProgram) { Write-Host 'Skipping program step.'; exit 0 }

# ------------------------------------------------------------- 3. program it
$env:FLASH_PRELOAD_ACCESSOR = $acc
$env:HW_SERVER_URL = 'localhost:3121'
# If no hw_server is listening on 3121 the script uses Vivado's own.
$listening = (Get-NetTCPConnection -LocalPort 3121 -State Listen -ErrorAction SilentlyContinue) -ne $null
if (-not $listening) { Remove-Item Env:\HW_SERVER_URL -ErrorAction SilentlyContinue }
& (Join-Path $root 'tools\flash_run.ps1') -Part $Part -Action program -Mcs $img
Write-Host '[3/4] program step done (see VERDICT above)'

# ------------------------------------------------------------- 4. verify it
$env:FLASH_RB_FILE = 'D:/USER/Desktop/FPGA/FPGA/tools/flash_logs/readback.bin'
& $vivado -mode batch -nojournal -nolog -source (Join-Path $root 'tools\flash_readback.tcl') *>&1 |
  Select-String 'Mfg ID|Readback Operation successful|ERROR' | ForEach-Object { Write-Host "      $($_.Line)" }
$rb = Join-Path $logdir 'readback.bin'
& $py -c @"
import sys
rb  = open(r'$($rb -replace '\\','/')','rb').read()
img = open(r'$($img -replace '\\','/')','rb').read()
head = rb[:len(img)]
print('readback=%d image=%d' % (len(rb), len(img)))
if head == img:
    extra = sum(1 for b in rb[len(img):] if b != 0xFF)
    print('VERIFY=PASS  (first %d bytes identical; %d stray non-FF bytes after image)' % (len(img), extra))
else:
    mis = [i for i in range(len(img)) if img[i] != rb[i]]
    print('VERIFY=FAIL  mismatches=%d first=%s' % (len(mis), mis[:8]))
    sys.exit(1)
"@
if ($LASTEXITCODE -eq 0) {
  Write-Host '[4/4] PASS - flash content == image. Now POWER-CYCLE the board to prove self-boot.'
} else {
  Write-Host '[4/4] FAIL - flash content != image.'; exit 1
}
