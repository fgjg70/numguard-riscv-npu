# tools/flash_partcheck.tcl -- for each named cfgmem part, record BOTH lookups:
#   (a) the DEVICE-FILTERED lookup  get_cfgmem_parts -of [current_hw_device] <n>
#   (b) the UNFILTERED lookup       get_cfgmem_parts <n>
# and then call create_hw_cfgmem with an UNFILTERED object to capture the literal
# [Labtoolstcl 44-655] error. That contrast is the whole story of the earlier
# failed attempts: (b) returns ONE OBJECT PER COMPATIBLE DEVICE (e.g. 89 objects
# for mt25ql128-spi-x1_x2_x4), so [lindex ... 0] binds a cfgmem_part belonging to
# some OTHER Xilinx device and the flash parameter set never matches xc7a100t.
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
puts "PARTS: get_cfgmem_parts -of \[current_hw_device\] -> [llength [get_cfgmem_parts -of [current_hw_device]]] entries"

foreach name {mt25ql128-spi-x1_x2_x4 mt25ql128-spi-x1_x2_x4_x8 w25q128fw-qspi-x1-single w25q128fv-qspi-x1-single} {
  puts "================ $name ================"
  set filt [get_cfgmem_parts -of [current_hw_device] $name]
  set unfilt [get_cfgmem_parts $name]
  puts "  device-filtered -> [llength $filt] object(s)"
  puts "  UNFILTERED      -> [llength $unfilt] object(s)"
  if {[llength $unfilt]} {
    set first [lindex $unfilt 0]
    puts "  unfiltered\[0\] = $first"
    puts "  unfiltered\[0\].COMPATIBLE_PARTS = [get_property COMPATIBLE_PARTS $first]"
    catch { set_property PROGRAM.HW_CFGMEM "" $dev }
    catch { delete_hw_cfgmem [get_hw_cfgmems -quiet] }
    if {[catch { create_hw_cfgmem -hw_device $dev $first ; puts "  create_hw_cfgmem(unfiltered) -> OK: [get_property PROGRAM.HW_CFGMEM $dev]" } e]} {
      puts "  create_hw_cfgmem(unfiltered) ERROR: $e"
    }
  } else {
    puts "  (name absent from the installed part table under this spelling)"
  }
}
puts "PARTCHECK_DONE"
