# ===========================================================================
# tools/flash_probe.tcl -- READ-ONLY diagnostics for QSPI flash programming.
# Does NOT program/erase anything. Only enumerates the cfgmem part database
# and prints the help text of the relevant commands.
# Usage: vivado -mode batch -nojournal -nolog -source tools/flash_probe.tcl
# ===========================================================================
# E-02: derive the repo root from this script's own location (tools/ -> ..).
set root [file normalize [file join [file dirname [info script]] ..]]
set out  "$root/tools/flash_logs"
file mkdir $out
set fp [open "$out/parts_all.txt" w]

puts $fp "### get_cfgmem_parts : [llength [get_cfgmem_parts]] entries"
foreach p [lsort [get_cfgmem_parts]] { puts $fp $p }
puts $fp ""
puts $fp "### get_cfgmem_parts *n25* : [llength [get_cfgmem_parts *n25*]]"
foreach p [lsort [get_cfgmem_parts *n25*]] { puts $fp $p }
puts $fp ""
puts $fp "### get_cfgmem_parts *mt25* : [llength [get_cfgmem_parts *mt25*]]"
foreach p [lsort [get_cfgmem_parts *mt25*]] { puts $fp $p }
puts $fp ""
puts $fp "### get_cfgmem_parts *128* : [llength [get_cfgmem_parts *128*]]"
foreach p [lsort [get_cfgmem_parts *128*]] { puts $fp $p }
close $fp

set fh [open "$out/help.txt" w]
foreach c {create_hw_cfgmem program_hw_cfgmem write_cfgmem get_cfgmem_parts} {
  puts $fh "================ help $c ================"
  if {[catch {puts $fh [help $c]} e]} { puts $fh "HELP_FAIL: $e" }
}
# Any command with 'cfgmem' in its name?
puts $fh "================ commands matching *cfgmem* / *flash* ================"
foreach c [lsort [info commands]] {
  if {[string match -nocase *cfgmem* $c] || [string match -nocase *flash* $c]} { puts $fh $c }
}
close $fh

# Properties of a cfgmem part object (read-only).
set fp2 [open "$out/part_props.txt" w]
foreach pn {mt25ql128-spi-x1_x2_x4 n25q128-3.3v-spi-x1_x2_x4 mt25ql128-spi-x1_x2_x4_x8} {
  puts $fp2 "==== $pn ===="
  if {[catch {
    set obj [get_cfgmem_parts $pn]
    puts $fp2 "obj=$obj"
    foreach pr [lsort [list_property $obj]] { puts $fp2 "  $pr = [get_property $pr $obj]" }
  } e]} { puts $fp2 "FAIL: $e" }
}
close $fp2
puts "PROBE_DONE"
