# tools/flash_readback.tcl -- READ-ONLY dump of the QSPI flash content.
# Uses readback_hw_cfgmem. Prints the help text so the exact option names are
# captured as evidence, then reads the first FLASH_RB_LEN bytes.
# E-02: derive the repo root from this script's own location (tools/ -> ..).
set root [file normalize [file join [file dirname [info script]] ..]]
set FLASH_PART   "mt25ql128-spi-x1_x2_x4"
set FLASH_RB_LEN "4096"
set FLASH_RB_ADDR "0"
set FLASH_RB_FILE "$root/tools/flash_logs/readback.bin"
foreach ev {FLASH_PART FLASH_RB_LEN FLASH_RB_ADDR FLASH_RB_FILE} {
  if {[info exists ::env($ev)] && $::env($ev) ne ""} { set $ev $::env($ev) }
}
set LOG "$root/tools/flash_logs/readback.log"
set fh [open $LOG w]
proc L {m} { global fh; puts $fh $m; flush $fh; puts $m }

L "### help readback_hw_cfgmem"
if {[catch {puts $fh [help readback_hw_cfgmem]} e]} { L "### HELPFAIL $e" }
L "### list_property of a hw_cfgmem:"
open_hw
if {[info exists ::env(HW_SERVER_URL)] && $::env(HW_SERVER_URL) ne ""} {
  connect_hw_server -url $::env(HW_SERVER_URL)
} else {
  connect_hw_server
}
open_hw_target
set dev [lindex [get_hw_devices] 0]
current_hw_device $dev
refresh_hw_device -update_hw_probes false $dev
if {[info exists ::env(FLASH_PRELOAD_ACCESSOR)] && $::env(FLASH_PRELOAD_ACCESSOR) ne ""} {
  L "### preload accessor $::env(FLASH_PRELOAD_ACCESSOR)"
  set_property PROGRAM.FILE $::env(FLASH_PRELOAD_ACCESSOR) $dev
  catch { program_hw_devices $dev }
  refresh_hw_device -update_hw_probes false $dev
}
catch { set_property PROGRAM.HW_CFGMEM "" $dev }
catch { delete_hw_cfgmem [get_hw_cfgmems -quiet] }
set pobj [lindex [get_cfgmem_parts -of [current_hw_device] $FLASH_PART] 0]
create_hw_cfgmem -hw_device $dev $pobj
set cfg [get_property PROGRAM.HW_CFGMEM $dev]
L "### cfg=$cfg  part=$pobj  -> [list_property $cfg]"
foreach pr [lsort [list_property $cfg]] {
  if {[catch {set v [get_property $pr $cfg]} e]} { set v "<$e>" }
  L "###   $pr = $v"
}
set rc 1
if {[catch {
  # try the documented-ish options; if the option names are wrong the help text
  # captured above is the evidence of the real syntax.
  readback_hw_cfgmem -force -file $FLASH_RB_FILE $cfg
  set rc 0
} e]} { L "### readback attempt1 ERROR: $e" }
if {$rc != 0} {
  if {[catch { readback_hw_cfgmem $cfg ; set rc 0 } e]} { L "### readback attempt2 ERROR: $e" }
}
L "### rc=$rc exists=[file exists $FLASH_RB_FILE] size=[expr {[file exists $FLASH_RB_FILE]?[file size $FLASH_RB_FILE]:-1}]"
close $fh
puts "READBACK_EXIT=$rc"
exit 0
