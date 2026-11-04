# ===========================================================================
# tools/flash_program.tcl -- drive the QSPI cfgmem flow on the DaVinci Pro.
#
# WHY THIS SCRIPT EXISTS
# ----------------------
# Two independent things had to be fixed before the QSPI flash could be written:
#
# 1. THE ACCESSOR. hw_server.exe owns the XicomFlash service, which is provided
#    by a small "flash accessor" design that has to be IN THE FPGA. In the pure
#    Tcl flow `program_hw_cfgmem` never loaded it, so hw_server logged
#       Command: C 77 XicomFlash override ...   ->  "Invalid context"
#    and Vivado reported
#       ERROR: [Labtools 27-3347] Flash Programming Unsuccessful:
#              Failure to set flash parameters.
#    This script therefore preloads
#       <Vivado>/data/xicom/cfgmem/bitfile/spi_<device>_pull<none|up|down>.bit
#    with program_hw_devices (env FLASH_PRELOAD_ACCESSOR). After that the device
#    reports "programmed with a design that has 1 SPI core(s)" and the whole
#    XicomFlash flow (override / getProperties / selectQSPI / erase /
#    programVerify / read) works.
#
# 2. PROGRAM.CFG_PROGRAM MUST BE 1. With CFG_PROGRAM=0 the tool erases and then
#    issues ONE programVerify that dies in ~4 ms ("Byte N does not match") and
#    writes nothing at all -- while still printing
#       INFO: [Labtoolstcl 44-377] Flash programming completed successfully
#    when VERIFY is also 0. With ERASE=1 BLANK_CHECK=1 CFG_PROGRAM=1 VERIFY=1
#    (the property set from the Vivado help example, and what the ALIENTEK guide
#    does in the GUI) it emits a long burst of programVerify calls and the image
#    really lands: full 16 MB readback == the .bin, byte for byte.
#
# The device-filtered lookup below is still the correct API --
# `get_cfgmem_parts <name>` returns ONE OBJECT PER COMPATIBLE DEVICE (89 for
# mt25ql128-spi-x1_x2_x4), and for the x8/dual variants [lindex ... 0] belongs to
# another device and create_hw_cfgmem then errors with
#   [Labtoolstcl 44-655] Cfgmem part <name> is not supported for device xc7a100t.
# (For mt25ql128-spi-x1_x2_x4 specifically the unfiltered object[0] happens to be
# accepted too, so the earlier 27-3347 was NOT caused by the lookup -- it was
# cause 1 above. Measured with tools/flash_partcheck.tcl.)
#
# ENV INPUTS
#   FLASH_PART   cfgmem part name (default mt25ql128-spi-x1_x2_x4)
#   FLASH_MCS    image to program (default fpga/cpu_a100t.mcs)
#   FLASH_ACTION probe | propdump | blankcheck | program   (default probe)
#   FLASH_LOGDIR where the per-part logs go
#
# ACTIONS
#   probe      : bind the part, dump every hw_cfgmem property, do NOT touch flash
#   blankcheck : ERASE=0 PROGRAM=0 VERIFY=0 BLANK_CHECK=1  -> READ ONLY
#   program    : full erase/blank-check/program/verify
# ===========================================================================
# E-02: derive the repo root from this script's own location (tools/ -> ..).
set root [file normalize [file join [file dirname [info script]] ..]]

set FLASH_PART   "mt25ql128-spi-x1_x2_x4"
set FLASH_MCS    "$root/fpga/cpu_a100t.mcs"
set FLASH_PRM    ""
set FLASH_ACTION "probe"
set FLASH_LOGDIR "$root/tools/flash_logs"
foreach ev {FLASH_PART FLASH_MCS FLASH_PRM FLASH_ACTION FLASH_LOGDIR} {
  if {[info exists ::env($ev)] && $::env($ev) ne ""} { set $ev $::env($ev) }
}
file mkdir $FLASH_LOGDIR
set TAG "${FLASH_PART}__${FLASH_ACTION}"
set LOG "$FLASH_LOGDIR/${TAG}.log"
set fh [open $LOG w]
proc L {msg} { global fh; puts $fh $msg; flush $fh; puts $msg }

L "### TAG=$TAG"
L "### FLASH_PART=$FLASH_PART"
L "### FLASH_MCS=$FLASH_MCS"
L "### FLASH_ACTION=$FLASH_ACTION"

# Make every Labtools message visible (no message limits) so that any flash-ID
# / parameter diagnostic the tool emits actually reaches the log.
catch { set_msg_config -id {Labtools 27-3347} -new_severity {ERROR} }
catch { set_msg_config -severity {INFO} -limit 100000 }
catch { set_msg_config -severity {WARNING} -limit 100000 }
catch { set_param labtools.maxThreads 1 }

set rc 1
if {[catch {

L "### ---- vivado params matching labtools/flash ----"
foreach patt {*labtools* *flash* *cfgmem*} {
  if {[catch {set pl [list_param -quiet -regexp $patt]} e]} { set pl "" }
  foreach p $pl { L "###   param $p = [get_param -quiet $p]" }
}

open_hw
if {[info exists ::env(HW_SERVER_URL)] && $::env(HW_SERVER_URL) ne ""} {
  L "### connect_hw_server -url $::env(HW_SERVER_URL)"
  connect_hw_server -url $::env(HW_SERVER_URL)
} else {
  connect_hw_server
}
open_hw_target
set dev [lindex [get_hw_devices] 0]
current_hw_device $dev
L "### hw_device = $dev"
foreach pr {PART PART_NAME DEVICE IDCODE REGISTER.CONFIG_STATUS} {
  if {[catch {set v [get_property $pr $dev]} e]} { set v "<n/a>" }
  L "### dev.$pr = $v"
}
refresh_hw_device $dev

# ---- optional: preload the hw_server SPI "flash accessor" design -----------
# hw_server.exe owns the XicomFlash service and pulls its accessor bitstream
# out of <install>/data/xicom/cfgmem/bitfile.zip (names: bitfile/spi_<dev>_pull*.bit).
# The failure mode we are chasing is:
#     hw_server: "Command: C 77 XicomFlash override" -> "Invalid context"
# i.e. no XicomFlash context existed, because no accessor design was in the FPGA.
# Setting FLASH_PRELOAD_ACCESSOR=1 loads that accessor by hand first.
if {[info exists ::env(FLASH_PRELOAD_ACCESSOR)] && $::env(FLASH_PRELOAD_ACCESSOR) ne ""} {
  set acc $::env(FLASH_PRELOAD_ACCESSOR)
  L "### PRELOAD accessor bitfile = $acc (exists=[file exists $acc])"
  set_property PROGRAM.FILE $acc $dev
  if {[catch {program_hw_devices -verbose $dev} e]} {
    L "### accessor program_hw_devices ERROR: $e"
  } else {
    L "### accessor program_hw_devices OK"
  }
  L "### dev.PROGRAM.FILE now = [get_property PROGRAM.FILE $dev]"
  refresh_hw_device -update_hw_probes false $dev
  L "### after refresh: devices=[get_hw_devices]"
}

# ---- device-filtered candidate list -------------------------------------
set allparts [get_cfgmem_parts -of [current_hw_device]]
L "### get_cfgmem_parts -of \[current_hw_device\] -> [llength $allparts] objects"
set sel [get_cfgmem_parts -of [current_hw_device] $FLASH_PART]
L "### filtered by '$FLASH_PART' -> [llength $sel] object(s)"
if {[llength $sel] == 0} {
  # Not compatible with this device at all: report and stop (this is 44-655).
  L "RESULT=NOT_COMPATIBLE_WITH_DEVICE"
  L "### (name exists in DB? [llength [get_cfgmem_parts -quiet $FLASH_PART]])"
  set rc 3
} else {
  set pobj [lindex $sel 0]
  L "### selected cfgmem_part = $pobj"
  # reset any stale binding from a previous run
  catch { set_property PROGRAM.HW_CFGMEM "" $dev }
  catch { delete_hw_cfgmem [get_hw_cfgmems -quiet] }

  create_hw_cfgmem -hw_device $dev $pobj
  set cfg [get_property PROGRAM.HW_CFGMEM $dev]
  L "### hw_cfgmem = $cfg"

  L "### ---- cfgmem_part properties ----"
  foreach pr [lsort [list_property $pobj]] {
    if {[catch {set v [get_property $pr $pobj]} e]} { set v "<$e>" }
    L "###   part.$pr = $v"
  }
  L "### ---- hw_cfgmem properties (before set) ----"
  foreach pr [lsort [list_property $cfg]] {
    if {[catch {set v [get_property $pr $cfg]} e]} { set v "<$e>" }
    L "###   cfg.$pr = $v"
  }

  set_property PROGRAM.FILE  $FLASH_MCS $cfg
  # PROGRAM.FILES is PLURAL because the GUI's "Program Configuration Memory
  # Device" dialog takes BOTH an .mcs and a .prm file -- the ALIENTEK guide
  # (Da Vinci Pro FPGA development guide V2.2, p.245) says exactly that: "add the
  # matching mcs file and .prm file, then click OK". The .prm carries the
  # interface/address-range/start
  # address that write_cfgmem used; without it the tool falls back to defaults
  # and the program step silently writes nothing (observed).
  set files [list $FLASH_MCS]
  if {$FLASH_PRM ne "" && [file exists $FLASH_PRM]} { lappend files $FLASH_PRM }
  L "### PROGRAM.FILES = $files"
  set_property PROGRAM.FILES $files $cfg

  switch -- $FLASH_ACTION {
    probe {
      L "### ACTION=probe : no flash operation performed"
      set rc 0
    }
    blankcheck {
      # READ-ONLY intent: no erase, no verify. There is NO PROGRAM.PROGRAM
      # property in 2018.3 (verified: [Common 17-142] Invalid property name);
      # the only program-related switches are ERASE / BLANK_CHECK / VERIFY /
      # CFG_PROGRAM. In the failure mode we are chasing the tool dies at
      # "set flash parameters", which is BEFORE any erase/write.
      foreach {p v} {PROGRAM.ERASE 0 PROGRAM.VERIFY 0 \
                     PROGRAM.BLANK_CHECK 1 PROGRAM.CFG_PROGRAM 0} {
        if {[catch {set_property $p $v $cfg} e]} { L "### setprop $p=$v -> $e" }
      }
      L "### ACTION=blankcheck (ERASE=0 VERIFY=0 BLANK_CHECK=1) starting"
      if {[catch {program_hw_cfgmem -verbose $cfg} e]} {
        L "### program_hw_cfgmem ERROR: $e"
        set rc 4
      } else {
        L "RESULT=BLANKCHECK_OK"
        set rc 0
      }
    }
    program {
      # THIS IS THE WORKING CONFIGURATION (measured, verified by readback).
      # CFG_PROGRAM=1 is essential: with it at 0 the tool erases, issues one
      # bogus programVerify that dies in ~4 ms, and writes NOTHING (and with
      # VERIFY=0 it still prints "Flash programming completed successfully").
      # This exactly matches the property set in the Vivado 2018.3 help example
      # for program_hw_cfgmem (tools/flash_logs/help.txt lines 247-255).
      foreach {p v} {PROGRAM.ERASE 1 PROGRAM.BLANK_CHECK 1 \
                     PROGRAM.CFG_PROGRAM 1 PROGRAM.VERIFY 1} {
        if {[catch {set_property $p $v $cfg} e]} { L "### setprop $p=$v -> $e" }
      }
      catch { set_property PROGRAM.ADDRESS_RANGE use_file $cfg }
      L "### ACTION=program starting (ERASE/BLANK_CHECK/CFG_PROGRAM/VERIFY = 1)"
      if {[catch {program_hw_cfgmem -verbose $cfg} e]} {
        L "### program_hw_cfgmem ERROR: $e"
        set rc 5
      } else {
        L "RESULT=PROGRAM_OK"
        set rc 0
      }
    }
    program_noverify {
      # Same as 'program' but with the verify step switched OFF. Separates
      # "the write silently does nothing" from "the write works and only the
      # verify/read path is broken" -- check with readback_hw_cfgmem afterwards.
      foreach {p v} {PROGRAM.ERASE 1 PROGRAM.BLANK_CHECK 0 \
                     PROGRAM.VERIFY 0 PROGRAM.CFG_PROGRAM 0} {
        if {[catch {set_property $p $v $cfg} e]} { L "### setprop $p=$v -> $e" }
      }
      L "### ACTION=program_noverify starting (ERASE=1 VERIFY=0)"
      if {[catch {program_hw_cfgmem -verbose $cfg} e]} {
        L "### program_hw_cfgmem ERROR: $e"
        set rc 5
      } else {
        L "RESULT=PROGRAM_NOVERIFY_OK"
        set rc 0
      }
    }
    program_doc {
      # EXACTLY the property set from the Vivado 2018.3 help example for
      # program_hw_cfgmem (see tools/flash_logs/help.txt lines 247-255):
      #   PROGRAM.ADDRESS_RANGE {use_file}
      #   PROGRAM.BLANK_CHECK 1 / ERASE 1 / CFG_PROGRAM 1 / VERIFY 1
      # Earlier runs had CFG_PROGRAM 0 -- if CFG_PROGRAM is the switch that
      # actually enables writing the configuration memory, that alone explains
      # "erase works, nothing is ever programmed".
      foreach {p v} {PROGRAM.ERASE 1 PROGRAM.BLANK_CHECK 1 \
                     PROGRAM.CFG_PROGRAM 1 PROGRAM.VERIFY 1} {
        if {[catch {set_property $p $v $cfg} e]} { L "### setprop $p=$v -> $e" }
      }
      foreach p {PROGRAM.ADDRESS_RANGE} { catch { set_property $p use_file $cfg } }
      L "### ACTION=program_doc starting (doc-recommended property set)"
      if {[catch {program_hw_cfgmem -verbose $cfg} e]} {
        L "### program_hw_cfgmem ERROR: $e"
        set rc 5
      } else {
        L "RESULT=PROGRAM_DOC_OK"
        set rc 0
      }
    }
    noerase_verify {
      # No erase: program (if the tool does so) + verify, over an already blank
      # device. Saves the ~50 s bulk erase and shows whether ERASE=1 matters.
      foreach {p v} {PROGRAM.ERASE 0 PROGRAM.BLANK_CHECK 0 \
                     PROGRAM.VERIFY 1 PROGRAM.CFG_PROGRAM 0} {
        if {[catch {set_property $p $v $cfg} e]} { L "### setprop $p=$v -> $e" }
      }
      L "### ACTION=noerase_verify starting (ERASE=0 VERIFY=1)"
      if {[catch {program_hw_cfgmem -verbose $cfg} e]} {
        L "### program_hw_cfgmem ERROR: $e"
        set rc 5
      } else {
        L "RESULT=NOERASE_VERIFY_OK"
        set rc 0
      }
    }
    default { L "### unknown FLASH_ACTION"; set rc 2 }
  }
}
} e] } {
  L "### TOP-LEVEL EXCEPTION: $e"
  L "### errorInfo: $::errorInfo"
  set rc 9
}
L "### EXITCODE=$rc"
close $fh
puts "FLASH_SCRIPT_EXIT=$rc LOG=$LOG"
exit $rc
