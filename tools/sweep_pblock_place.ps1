# sweep_pb3.ps1 -- Pblock x place-directive re-sweep on the CURRENT netlist.
# Rationale: build.tcl's table (lines 277-290) was measured before the R-01/R48/R-02/W-04
# fixes (+69 LUT / +17 FF), so the optimum may have moved. Each pairing gets its own
# synth cache fingerprint (range + directive are both in the key), so repeats SKIP.
# Every point is recorded with --status measure (honest: measured, no decision yet).
$ErrorActionPreference = 'Continue'
Set-Location 'D:\USER\Desktop\FPGA\FPGA'
$log = "$env:TEMP\sweep_pb3.log"
Remove-Item $log -Force -ErrorAction SilentlyContinue

$groups = @(
  @{ tag='pb3-ctl';  range='CLOCKREGION_X0Y1:CLOCKREGION_X2Y4'; place='ExtraTimingOpt' },
  @{ tag='pb3-b';    range='CLOCKREGION_X0Y1:CLOCKREGION_X3Y4'; place='ExtraTimingOpt' },
  @{ tag='pb3-c';    range='CLOCKREGION_X0Y2:CLOCKREGION_X4Y4'; place='ExtraTimingOpt' },
  @{ tag='pb3-d';    range='CLOCKREGION_X0Y1:CLOCKREGION_X2Y5'; place='ExtraTimingOpt' },
  @{ tag='pb3-e';    range='CLOCKREGION_X0Y2:CLOCKREGION_X3Y5'; place='ExtraTimingOpt' },
  @{ tag='pb3-f';    range='CLOCKREGION_X0Y1:CLOCKREGION_X3Y5'; place='ExtraTimingOpt' },
  @{ tag='pb3-g';    range='CLOCKREGION_X0Y3:CLOCKREGION_X3Y6'; place='ExtraTimingOpt' },
  @{ tag='pb3-h';    range='CLOCKREGION_X0Y2:CLOCKREGION_X2Y4'; place='WLDrivenBlockPlacement' },
  @{ tag='pb3-i';    range='CLOCKREGION_X0Y1:CLOCKREGION_X2Y4'; place='WLDrivenBlockPlacement' },
  @{ tag='pb3-j';    range='CLOCKREGION_X0Y2:CLOCKREGION_X3Y5'; place='WLDrivenBlockPlacement' },
  @{ tag='pb3-off';  range='off';                               place='ExtraTimingOpt' }
)

"=== Pblock x place re-sweep on the NEW netlist ($($groups.Count) points) ===" | Tee-Object -FilePath $log -Append
$results = @()
foreach ($g in $groups) {
  $t0 = Get-Date
  $out = & python tools\fpga_workflow.py synth --pblock-range $g.range --place-directive $g.place `
                --tag $g.tag --record --status measure 2>&1
  $dt = [int]((Get-Date) - $t0).TotalSeconds
  $fmax = ($out | Select-String -Pattern 'fmax\s+([\d.]+) MHz' | Select-Object -First 1).Matches.Groups[1].Value
  $wns  = ($out | Select-String -Pattern 'WNS\s+\+?(-?[\d.]+)ns'  | Select-Object -First 1).Matches.Groups[1].Value
  $lut  = ($out | Select-String -Pattern 'LUT (\d+) FF'            | Select-Object -First 1).Matches.Groups[1].Value
  $ff   = ($out | Select-String -Pattern 'FF (\d+) BRAM'           | Select-Object -First 1).Matches.Groups[1].Value
  $skip = if ($out | Select-String -Pattern '\[SKIP\]') { 'SKIP' } else { 'RUN' }
  $line = "{0,-9} {1,-42} {2,-24} fmax={3,6}  WNS={4,6}  LUT={5,5} FF={6,5}  [{7}, {8}s]" -f `
          $g.tag, $g.range, $g.place, $fmax, $wns, $lut, $ff, $skip, $dt
  $line | Tee-Object -FilePath $log -Append
  $results += [pscustomobject]@{ tag=$g.tag; range=$g.range; place=$g.place;
                                 fmax=[double]$fmax; wns=[double]$wns; lut=$lut; ff=$ff }
}

"" | Tee-Object -FilePath $log -Append
"=== sorted by fmax (desc) ===" | Tee-Object -FilePath $log -Append
$results | Sort-Object fmax -Descending | ForEach-Object {
  "{0,-9} fmax={1,6}  WNS={2,6}  LUT={3,5} FF={4,5}  {5} / {6}" -f `
     $_.tag, $_.fmax, $_.wns, $_.lut, $_.ff, $_.range, $_.place | Tee-Object -FilePath $log -Append
}
$best = $results | Sort-Object fmax -Descending | Select-Object -First 1
"BEST=$($best.tag) fmax=$($best.fmax) WNS=$($best.wns) LUT=$($best.lut)" | Tee-Object -FilePath $log -Append
"SWEEP_DONE" | Tee-Object -FilePath $log -Append
